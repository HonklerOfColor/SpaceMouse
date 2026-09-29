import CoreGraphics
import CoreText
import Darwin
import Foundation
import IOKit
import IOKit.hid

/// SpacePilot LCD, 240×64, one bit per pixel.
/// Feature reports follow the protocol documented by jtsiomb/3dxdisp:
/// 0x12 keeps the firmware from redrawing its own logo, 0x0C sets the page
/// and column, 0x0D writes seven columns, 0x0E repeats a column pattern.
/// Bit 0 of a column byte is the top pixel of that 8-pixel page.
final class Display {
    private let queue = DispatchQueue(label: "sp1hid.lcd")
    private var device: IOHIDDevice?
    private var lines = ["SpacePilot", "Fusion"]
    private var refresh: DispatchWorkItem?
    private var loggedFailure = false

    func attach(_ device: IOHIDDevice) {
        queue.async {
            self.device = device
            self.loggedFailure = false
            self.draw()
            self.schedule()
            fputs("sp1hid: display on\n", stderr)
        }
    }

    func detach() {
        queue.async {
            self.refresh?.cancel()
            self.refresh = nil
            self.device = nil
        }
    }

    func show(_ lines: [String]) {
        let clean = lines.map { $0.replacingOccurrences(of: "\n", with: " ") }.filter { !$0.isEmpty }
        queue.async {
            self.lines = Array((clean.isEmpty ? ["SpacePilot"] : clean).prefix(4))
            self.draw()
            self.schedule()
        }
    }

    private func schedule() {
        refresh?.cancel()
        guard device != nil else { return }
        let item = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.draw()
            self.schedule()
        }
        refresh = item
        queue.asyncAfter(deadline: .now() + 1.2, execute: item)
    }

    private func draw() {
        guard device != nil else { return }
        let pixels = render(lines, load: sampleLoad())
        var pages = [[UInt8]](repeating: [UInt8](repeating: 0, count: 240), count: 8)
        var lit = 0
        for page in 0..<8 {
            for col in 0..<240 {
                var bits: UInt8 = 0
                for bit in 0..<8 {
                    let y = page * 8 + bit
                    if pixels[y * 240 + col] >= 80 {
                        bits |= UInt8(1 << bit)
                        lit += 1
                    }
                }
                pages[page][col] = bits
            }
        }
        if lit == 0 && !lines.isEmpty {
            fputs("sp1hid: display render was empty\n", stderr)
        }
        // Without this the firmware paints its logo back over the frame.
        guard send([0x12, 0x00, 0x00, 0x2F, 0x00, 0x00, 0x00, 0x00]) else { return }
        for page in 0..<8 {
            guard send([0x0C, UInt8(page), 0x00, 0x00]) else { return }
            sendColumns(pages[page])
        }
    }

    private func sendColumns(_ cols: [UInt8]) {
        let width = cols.count
        var start = 0
        while start < width {
            var pats = [UInt8](repeating: 0, count: 3)
            var reps = [Int](repeating: 0, count: 3)
            pats[0] = cols[start]
            reps[0] = 1
            var idx = 0
            var i = start + 1
            while i < width {
                if cols[i] == pats[idx] {
                    if reps[idx] >= 255 { break }
                    reps[idx] += 1
                } else {
                    idx += 1
                    if idx >= 3 { break }
                    pats[idx] = cols[i]
                    reps[idx] = 1
                }
                i += 1
            }
            let advance = i - start
            if advance > 7 || width - i < 7 {
                var buf: [UInt8] = [0x0E]
                for n in 0..<3 where reps[n] > 0 {
                    buf.append(UInt8(reps[n]))
                    buf.append(pats[n])
                }
                if !send(buf) { return }
                start += advance
            } else {
                var buf: [UInt8] = [0x0D]
                buf.append(contentsOf: cols[start..<(start + 7)])
                if !send(buf) { return }
                start += 7
            }
        }
    }

    private func send(_ bytes: [UInt8]) -> Bool {
        guard let device, let first = bytes.first else { return false }
        let result = bytes.withUnsafeBufferPointer { ptr in
            IOHIDDeviceSetReport(
                device,
                kIOHIDReportTypeFeature,
                CFIndex(first),
                ptr.baseAddress!,
                ptr.count
            )
        }
        if result != kIOReturnSuccess {
            if !loggedFailure {
                loggedFailure = true
                fputs(String(format: "sp1hid: display report 0x%02x failed 0x%08x\n", first, result), stderr)
            }
            return false
        }
        return true
    }
}

func renderPreview(_ lines: [String]) -> [UInt8] {
    render(lines, load: sampleLoad())
}

private struct Load {
    var cpu: Int
    var ram: Int
    var gpu: Int?
}

private var previousCPU: (UInt32, UInt32, UInt32, UInt32)?

private func sampleLoad() -> Load {
    Load(cpu: sampleCPU(), ram: sampleRAM(), gpu: sampleGPU())
}

private func sampleCPU() -> Int {
    func read() -> (UInt32, UInt32, UInt32, UInt32)? {
        var info = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer -> kern_return_t in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
                host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, rebound, &count)
            }
        }
        guard result == KERN_SUCCESS else { return nil }
        return info.cpu_ticks
    }
    guard let now = read() else { return 0 }
    if previousCPU == nil {
        previousCPU = now
        usleep(200_000)
        guard let later = read() else { return 0 }
        let value = cpuPercent(from: now, to: later)
        previousCPU = later
        return value
    }
    let value = cpuPercent(from: previousCPU!, to: now)
    previousCPU = now
    return value
}

private func cpuPercent(from earlier: (UInt32, UInt32, UInt32, UInt32), to later: (UInt32, UInt32, UInt32, UInt32)) -> Int {
    let user = later.0 &- earlier.0
    let system = later.1 &- earlier.1
    let idle = later.2 &- earlier.2
    let nice = later.3 &- earlier.3
    let busy = UInt64(user) + UInt64(system) + UInt64(nice)
    let total = busy + UInt64(idle)
    guard total > 0 else { return 0 }
    return Int(min(100, busy * 100 / total))
}

private func sampleRAM() -> Int {
    var stats = vm_statistics64()
    var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.size / MemoryLayout<integer_t>.size)
    let result = withUnsafeMutablePointer(to: &stats) { pointer -> kern_return_t in
        pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
            host_statistics64(mach_host_self(), HOST_VM_INFO64, rebound, &count)
        }
    }
    guard result == KERN_SUCCESS else { return 0 }
    var total: UInt64 = 0
    var length = MemoryLayout<UInt64>.size
    guard sysctlbyname("hw.memsize", &total, &length, nil, 0) == 0, total > 0 else { return 0 }
    var page: vm_size_t = 0
    guard host_page_size(mach_host_self(), &page) == KERN_SUCCESS, page > 0 else { return 0 }
    let purgeable = UInt64(stats.purgeable_count)
    let app = UInt64(stats.internal_page_count) > purgeable ? UInt64(stats.internal_page_count) - purgeable : 0
    let used = app + UInt64(stats.wire_count) + UInt64(stats.compressor_page_count)
    let totalPages = total / UInt64(page)
    guard totalPages > 0 else { return 0 }
    return Int(min(100, used * 100 / totalPages))
}

private func sampleGPU() -> Int? {
    var iterator: io_iterator_t = 0
    guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"), &iterator) == KERN_SUCCESS else {
        return nil
    }
    defer { IOObjectRelease(iterator) }
    var best: Int?
    var service = IOIteratorNext(iterator)
    while service != 0 {
        var properties: Unmanaged<CFMutableDictionary>?
        if IORegistryEntryCreateCFProperties(service, &properties, kCFAllocatorDefault, 0) == KERN_SUCCESS,
           let raw = properties?.takeRetainedValue() as? [String: Any],
           let stats = raw["PerformanceStatistics"] as? [String: Any],
           let value = percent(stats["Device Utilization %"]) {
            best = max(best ?? 0, min(100, value))
        }
        IOObjectRelease(service)
        service = IOIteratorNext(iterator)
    }
    return best
}

private func percent(_ value: Any?) -> Int? {
    if let number = value as? Int { return number }
    if let number = value as? Double { return Int(number.rounded()) }
    if let number = value as? NSNumber { return number.intValue }
    return nil
}

private func render(_ lines: [String], load: Load) -> [UInt8] {
    let width = 240
    let height = 64
    var pixels = [UInt8](repeating: 0, count: width * height)
    pixels.withUnsafeMutableBytes { raw in
        guard let ctx = CGContext(
            data: raw.baseAddress,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else { return }
        // Bitmap row 0 is the top of the panel. Core Text draws upward from
        // the baseline, so each line is flipped back onto that baseline.
        ctx.translateBy(x: 0, y: CGFloat(height))
        ctx.scaleBy(x: 1, y: -1)
        ctx.clip(to: CGRect(x: 0, y: 0, width: width, height: height))
        let font = CTFontCreateWithName("Helvetica" as CFString, 13, nil)
        let ascent = CTFontGetAscent(font)
        let descent = CTFontGetDescent(font)
        let lineHeight = ascent + descent + 3
        let color = CGColor(gray: 1, alpha: 1)
        let attributes: [CFString: Any] = [
            kCTFontAttributeName: font,
            kCTForegroundColorAttributeName: color,
        ]
        let statSize: CGFloat = 23
        let regular = CTFontCreateWithName("Helvetica" as CFString, statSize, nil)
        let statFont = CTFontCreateCopyWithSymbolicTraits(regular, statSize, nil, .traitBold, .traitBold) ?? regular
        let statAttributes: [CFString: Any] = [
            kCTFontAttributeName: statFont,
            kCTForegroundColorAttributeName: color,
        ]
        let statAscent = CTFontGetAscent(statFont)
        let statDescent = CTFontGetDescent(statFont)
        let labels = ["CPU", "RAM", "GPU"].compactMap {
            textLine($0, attributes: statAttributes as CFDictionary, limit: nil)
        }
        let values = [
            "\(load.cpu)%",
            "\(load.ram)%",
            load.gpu.map { "\($0)%" } ?? "--",
        ].compactMap {
            textLine($0, attributes: statAttributes as CFDictionary, limit: nil)
        }
        let labelWidth = labels.map(lineWidth).max() ?? 0
        let numberWidth = max(values.map(lineWidth).max() ?? 0, lineWidth(of: "100%", attributes: statAttributes as CFDictionary))
        let labelX = CGFloat(width) - 2 - numberWidth - 8 - labelWidth
        let leftLimit = labelX - 6
        let slot = CGFloat(height) / 3
        ctx.saveGState()
        ctx.clip(to: CGRect(x: 0, y: 0, width: max(0, leftLimit), height: CGFloat(height)))
        for (index, line) in lines.prefix(4).enumerated() {
            guard let drawn = textLine(line, attributes: attributes as CFDictionary, limit: leftLimit - 4) else { continue }
            draw(drawn, in: ctx, x: 4, baseline: ascent + 1 + CGFloat(index) * lineHeight)
        }
        ctx.restoreGState()
        for index in 0..<min(labels.count, values.count) {
            let baseline = slot * CGFloat(index) + statAscent + max(0, (slot - statAscent - statDescent) / 2)
            draw(labels[index], in: ctx, x: labelX, baseline: baseline)
            let used = lineWidth(values[index])
            draw(values[index], in: ctx, x: CGFloat(width) - 2 - used, baseline: baseline)
        }
    }
    return pixels
}

private func lineWidth(_ line: CTLine) -> CGFloat {
    CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))
}

private func lineWidth(of string: String, attributes: CFDictionary) -> CGFloat {
    guard let line = textLine(string, attributes: attributes, limit: nil) else { return 0 }
    return lineWidth(line)
}

private func textLine(_ string: String, attributes: CFDictionary, limit: CGFloat?) -> CTLine? {
    guard let text = CFAttributedStringCreate(nil, string as CFString, attributes) else { return nil }
    let line = CTLineCreateWithAttributedString(text)
    guard let limit else { return line }
    if CTLineGetTypographicBounds(line, nil, nil, nil) <= Double(limit) { return line }
    guard let dots = CFAttributedStringCreate(nil, "…" as CFString, attributes) else { return line }
    let token = CTLineCreateWithAttributedString(dots)
    return CTLineCreateTruncatedLine(line, Double(limit), .end, token) ?? line
}

private func draw(_ line: CTLine, in ctx: CGContext, x: CGFloat, baseline: CGFloat) {
    ctx.saveGState()
    ctx.translateBy(x: x, y: baseline)
    ctx.scaleBy(x: 1, y: -1)
    ctx.textPosition = .zero
    CTLineDraw(line, ctx)
    ctx.restoreGState()
}

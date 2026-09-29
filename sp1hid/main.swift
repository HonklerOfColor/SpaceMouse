import Foundation
import IOKit
import IOKit.hid
import IOKit.hidsystem

/// Reads a 3Dconnexion SpacePilot (USB 046d:c625) and writes deflection samples
/// as JSON lines. Axes are raw signed 16-bit values from the device, roughly
/// -500...500. Buttons are a 21-bit mask, bit 0 = HID button 1.

private let vendorID = 0x046D
private let productID = 0xC625

final class Driver {
    let monitor: Bool
    let display = Display()
    private let lock = NSLock()
    private var tx = 0, ty = 0, tz = 0
    private var rx = 0, ry = 0, rz = 0
    private var buttons = 0
    private var lastAxesAt = Date.distantPast
    private var wasMoving = false
    private var buttonDirty = false
    private var reportCount = 0
    private var tickCount = 0
    private var connected = false
    private var seen = Set<ObjectIdentifier>()
    private var buffers: [UnsafeMutablePointer<UInt8>] = []

    init(monitor: Bool) {
        self.monitor = monitor
    }

    func handle(reportID: UInt32, bytes: UnsafePointer<UInt8>, length: Int) {
        let id = reportID
        var start = 0
        var count = length
        if length > 1, Int(bytes[0]) == Int(id) {
            // macOS includes the report id as the first byte.
            start = 1
            count = length - 1
        }
        guard count > 0 else { return }
        let payload = bytes + start

        lock.lock()
        defer { lock.unlock() }
        reportCount += 1

        switch id {
        case 1 where count >= 6:
            tx = i16(payload, 0)
            ty = i16(payload, 2)
            tz = i16(payload, 4)
            lastAxesAt = Date()
        case 2 where count >= 6:
            rx = i16(payload, 0)
            ry = i16(payload, 2)
            rz = i16(payload, 4)
            lastAxesAt = Date()
        case 3:
            var mask = 0
            let n = min(count, 3)
            for i in 0..<n {
                mask |= Int(payload[i]) << (8 * i)
            }
            mask &= (1 << 21) - 1
            if mask != buttons {
                buttons = mask
                buttonDirty = true
            }
        default:
            break
        }
    }

    func setConnected(_ value: Bool, exclusive: Bool = true) {
        lock.lock()
        connected = value
        if !value {
            tx = 0; ty = 0; tz = 0
            rx = 0; ry = 0; rz = 0
            buttons = 0
            wasMoving = false
        }
        lock.unlock()
        if value {
            emitStatus(exclusive ? "connected" : "shared")
        } else {
            emitStatus("disconnected")
            emitSample(force: true)
        }
    }

    func isConnected() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return connected
    }

    func register(_ device: IOHIDDevice) {
        let key = ObjectIdentifier(device)
        lock.lock()
        let fresh = seen.insert(key).inserted
        lock.unlock()
        guard fresh else { return }

        let capacity = 64
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
        buffer.initialize(repeating: 0, count: capacity)
        lock.lock()
        buffers.append(buffer)
        lock.unlock()

        let context = Unmanaged.passUnretained(self).toOpaque()
        IOHIDDeviceRegisterInputReportCallback(device, buffer, capacity, { context, _, _, _, reportID, report, length in
            guard let context, length > 0 else { return }
            let driver = Unmanaged<Driver>.fromOpaque(context).takeUnretainedValue()
            driver.handle(reportID: reportID, bytes: report, length: length)
        }, context)
        IOHIDDeviceScheduleWithRunLoop(device, RunLoop.current.getCFRunLoop(), CFRunLoopMode.commonModes.rawValue)

        // The cap's X/Y axes are relative. macOS otherwise turns them into pointer motion.
        var open = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeSeizeDevice))
        var exclusive = open == kIOReturnSuccess
        if !exclusive {
            fputs(String(format: "sp1hid: exklusiv öffnen 0x%08x, versuche geteilt\n", open), stderr)
            open = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
            exclusive = false
        }
        if open != kIOReturnSuccess {
            fputs(String(format: "sp1hid: IOHIDDeviceOpen 0x%08x\n", open), stderr)
        }

        let name = (IOHIDDeviceGetProperty(device, kIOHIDProductKey as CFString) as? String) ?? "SpacePilot"
        fputs("sp1hid: \(name) offen \(exclusive ? "exklusiv" : "geteilt")\n", stderr)
        if open == kIOReturnSuccess {
            display.attach(device)
        }
        setConnected(true, exclusive: exclusive)
    }

    func forget(_ device: IOHIDDevice) {
        let key = ObjectIdentifier(device)
        lock.lock()
        seen.remove(key)
        let anyLeft = !seen.isEmpty
        lock.unlock()
        if !anyLeft {
            fputs("sp1hid: SpacePilot getrennt\n", stderr)
            display.detach()
            setConnected(false)
        }
    }

    func tick() {
        lock.lock()
        if Date().timeIntervalSince(lastAxesAt) > 0.2 {
            tx = 0; ty = 0; tz = 0
            rx = 0; ry = 0; rz = 0
        }
        let moving = tx != 0 || ty != 0 || tz != 0 || rx != 0 || ry != 0 || rz != 0
        let dirty = buttonDirty
        buttonDirty = false
        let sample = (tx, ty, tz, rx, ry, rz, buttons, reportCount, connected)
        let shouldSend = moving || wasMoving || dirty
        wasMoving = moving
        lock.unlock()

        tickCount += 1
        let heartbeat = tickCount % 15 == 0
        if shouldSend || heartbeat {
            emitSample(sample)
        }
        if monitor {
            let (tx, ty, tz, rx, ry, rz, buttons, reports, connected) = sample
            let names = buttonNames(buttons)
            fputs(String(format: "\r%@ TX %+4d TY %+4d TZ %+4d | RX %+4d RY %+4d RZ %+4d | %@ | n=%d   ",
                         connected ? "ON " : "OFF",
                         tx, ty, tz, rx, ry, rz,
                         names.isEmpty ? "—" : names,
                         reports), stderr)
            fflush(stderr)
            _ = reports
        }
    }

    private func emitSample(_ sample: (Int, Int, Int, Int, Int, Int, Int, Int, Bool)? = nil, force: Bool = false) {
        let values: (Int, Int, Int, Int, Int, Int, Int)
        let reports: Int
        if let sample {
            values = (sample.0, sample.1, sample.2, sample.3, sample.4, sample.5, sample.6)
            reports = sample.7
        } else {
            lock.lock()
            values = (tx, ty, tz, rx, ry, rz, buttons)
            reports = reportCount
            lock.unlock()
        }
        _ = force
        let line = "{\"tx\":\(values.0),\"ty\":\(values.1),\"tz\":\(values.2),\"rx\":\(values.3),\"ry\":\(values.4),\"rz\":\(values.5),\"b\":\(values.6),\"n\":\(reports)}\n"
        fputs(line, stdout)
        fflush(stdout)
    }

    private func emitStatus(_ status: String) {
        fputs("{\"status\":\"\(status)\"}\n", stdout)
        fflush(stdout)
    }
}

private func i16(_ p: UnsafePointer<UInt8>, _ offset: Int) -> Int {
    let v = Int(p[offset]) | (Int(p[offset + 1]) << 8)
    return v >= 32768 ? v - 65536 : v
}

private func buttonNames(_ mask: Int) -> String {
    var parts: [String] = []
    for i in 0..<21 where (mask & (1 << i)) != 0 {
        parts.append("B\(i)")
    }
    return parts.joined(separator: ",")
}

private func fail(_ message: String) -> Never {
    fputs("sp1hid: \(message)\n", stderr)
    fputs("{\"status\":\"error\",\"message\":\(jsonString(message))}\n", stdout)
    fflush(stdout)
    exit(1)
}

private func jsonString(_ value: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [value])
    let text = String(data: data, encoding: .utf8)!
    return String(text.dropFirst().dropLast())
}

if CommandLine.arguments.contains("--lcd-dump") {
    let pixels = renderPreview(["Fusion   1.50x", "Press a button to assign it"])
    var bytes = Array("P5\n240 64\n255\n".utf8)
    bytes.append(contentsOf: pixels)
    let url = URL(fileURLWithPath: "/tmp/sp1lcd.pgm")
    try? Data(bytes).write(to: url)
    fputs("wrote \(url.path)\n", stderr)
    exit(0)
}

let monitor = CommandLine.arguments.contains("--monitor")
let driver = Driver(monitor: monitor)

let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))

let access = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)
if access != kIOHIDAccessTypeGranted {
    let granted = IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)
    fputs("sp1hid: Eingabeüberwachung \(granted ? "erlaubt" : "nicht erlaubt")\n", stderr)
    if !granted {
        fail("macOS is blocking input. System Settings → Privacy & Security → Input Monitoring: enable Autodesk Fusion, then start again.")
    }
}

let matching: [String: Any] = [
    kIOHIDVendorIDKey: vendorID,
    kIOHIDProductIDKey: productID,
]
IOHIDManagerSetDeviceMatching(manager, matching as CFDictionary)

let context = Unmanaged.passUnretained(driver).toOpaque()
IOHIDManagerRegisterDeviceMatchingCallback(manager, { context, _, _, device in
    guard let context else { return }
    Unmanaged<Driver>.fromOpaque(context).takeUnretainedValue().register(device)
}, context)
IOHIDManagerRegisterDeviceRemovalCallback(manager, { context, _, _, device in
    guard let context else { return }
    Unmanaged<Driver>.fromOpaque(context).takeUnretainedValue().forget(device)
}, context)

let runLoop = RunLoop.current.getCFRunLoop()
IOHIDManagerScheduleWithRunLoop(manager, runLoop, CFRunLoopMode.commonModes.rawValue)

var openResult = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeSeizeDevice))
if openResult != kIOReturnSuccess {
    fputs(String(format: "sp1hid: Manager exklusiv 0x%08x\n", openResult), stderr)
    openResult = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
}
if openResult != kIOReturnSuccess {
    let hex = String(format: "0x%08x", openResult)
    if openResult == kIOReturnNotPermitted {
        fail("no access to the HID device (\(hex)). System Settings → Privacy & Security → Input Monitoring: allow Terminal or Autodesk Fusion, then start again.")
    }
    fail("IOHIDManagerOpen fehlgeschlagen (\(hex))")
}

if let devices = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice> {
    for device in devices {
        driver.register(device)
    }
}

if !driver.isConnected() {
    fputs("{\"status\":\"waiting\"}\n", stdout)
    fflush(stdout)
    fputs("sp1hid: kein SpacePilot gefunden\n", stderr)
}

Thread.detachNewThread {
    while let line = readLine(strippingNewline: true) {
        guard let data = line.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let lines = obj["lcd"] as? [String] else { continue }
        driver.display.show(lines)
    }
}

fputs("sp1hid: bereit (046d:c625)\n", stderr)

signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
terminate.setEventHandler { exit(0) }
terminate.resume()
let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
interrupt.setEventHandler { exit(0) }
interrupt.resume()
Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { _ in
    driver.tick()
}
RunLoop.current.run()

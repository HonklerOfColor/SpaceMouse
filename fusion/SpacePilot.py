"""Fusion add-in: drive the viewport from a 3Dconnexion SpacePilot (SP1)."""

import json
import math
import os
import subprocess
import sys
import threading
import time
import traceback

import adsk.core

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import motion

SUPPORT = os.path.expanduser("~/Library/Application Support/SpacePilotFusion")
CONFIG_PATH = os.path.join(SUPPORT, "config.json")
LOG_PATH = os.path.join(SUPPORT, "addin.log")
HELPER = os.path.join(_HERE, "sp1hid")
EVENT_ID = "sp1_motion"
CMD_ID = "sp1StatusCmd"
PALETTE_ID = "sp1Palette"

KEY_CODES = {
    "esc": 53,
    "shift": 56,
    "alt": 58,
    "option": 58,
    "ctrl": 59,
    "control": 59,
    "cmd": 55,
    "command": 55,
}

VIEW_BY_NAME = {
    "top": adsk.core.ViewOrientations.TopViewOrientation,
    "bottom": adsk.core.ViewOrientations.BottomViewOrientation,
    "front": adsk.core.ViewOrientations.FrontViewOrientation,
    "back": adsk.core.ViewOrientations.BackViewOrientation,
    "left": adsk.core.ViewOrientations.LeftViewOrientation,
    "right": adsk.core.ViewOrientations.RightViewOrientation,
    "iso": adsk.core.ViewOrientations.IsoTopRightViewOrientation,
}

BUTTON_CHOICES = (
    ("", "None"),
    ("fit", "Fit"),
    ("iso", "Isometric"),
    ("top", "Top"),
    ("bottom", "Bottom"),
    ("front", "Front"),
    ("back", "Back"),
    ("left", "Left"),
    ("right", "Right"),
    ("undo", "Undo"),
    ("redo", "Redo"),
    ("faster", "Faster"),
    ("slower", "Slower"),
    ("key:shift", "Hold Shift"),
    ("key:ctrl", "Hold Ctrl"),
    ("key:alt", "Hold Alt"),
    ("key:cmd", "Hold Command"),
    ("key:esc", "Esc"),
)
BUTTON_ACTIONS = {key for key, _label in BUTTON_CHOICES}

_handlers = []
_addin = None


def log(message):
    try:
        os.makedirs(SUPPORT, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S ") + message + "\n")
    except Exception:
        pass


def default_config():
    return {
        "axisRange": 500,
        "deadzone": 0.03,
        "curve": 1.2,
        "panSpeed": 1.3,
        "zoomSpeed": 1.8,
        "orbitSpeed": 2.6,
        "rollSpeed": 1.8,
        "speed": 1.0,
        "objectMode": True,
        "dominant": False,
        "map": dict(motion.DEFAULT_MAP),
        "buttons": {},
        "_help": (
            "map ties a motion to a raw axis. A leading minus reverses it. "
            "Example: \"panY\": \"-ty\". buttons maps a button index to an action, "
            "for example \"4\": \"fit\". Actions: fit, iso, top, bottom, front, back, left, right, "
            "undo, redo, faster, slower, key:shift, key:ctrl, key:alt, key:cmd, key:esc."
        ),
    }


def load_config():
    cfg = default_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            user = json.load(handle)
    except FileNotFoundError:
        os.makedirs(SUPPORT, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return cfg
    except Exception as exc:
        log("config: %s" % exc)
        return cfg
    mapping = user.pop("map", None)
    buttons = user.pop("buttons", None)
    cfg.update(user)
    if isinstance(mapping, dict):
        cfg["map"].update(mapping)
    if isinstance(buttons, dict):
        cfg["buttons"] = {str(key): value for key, value in buttons.items()}
    return cfg


def save_config(cfg):
    os.makedirs(SUPPORT, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def post_key(keycode, down):
    cg = ctypes_coregraphics()
    if cg is None:
        return False
    event = cg.CGEventCreateKeyboardEvent(None, keycode, bool(down))
    if not event:
        return False
    cg.CGEventPost(0, event)
    cf = ctypes_corefoundation()
    cf.CFRelease(event)
    return True


_cg = None
_cf = None


def ctypes_coregraphics():
    global _cg
    if _cg is False:
        return None
    if _cg:
        return _cg
    import ctypes
    try:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        _cg = cg
        return cg
    except Exception as exc:
        log("tasten: %s" % exc)
        _cg = False
        return None


def ctypes_corefoundation():
    global _cf
    if _cf:
        return _cf
    import ctypes
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    _cf = cf
    return cf


def extents_of(camera):
    try:
        extents = camera.getExtents()
    except Exception:
        return None
    if not isinstance(extents, (list, tuple)):
        return None
    if len(extents) >= 3 and isinstance(extents[0], bool):
        if not extents[0]:
            return None
        width, height = extents[1], extents[2]
    elif len(extents) >= 2:
        width, height = extents[0], extents[1]
    else:
        return None
    if not width or not height or width <= 0 or height <= 0:
        return None
    return (float(width), float(height))


def view_height(camera, eye, target):
    if camera.cameraType == adsk.core.CameraTypes.OrthographicCameraType:
        try:
            extents = camera.getExtents()
            if isinstance(extents, tuple) and len(extents) >= 2:
                height = extents[-1]
                if height and height > 0:
                    return height
        except Exception:
            pass
    distance = math.dist(eye, target)
    fov = camera.perspectiveAngle or 0.6
    if fov > 2.5:
        fov = math.radians(fov)
    return max(2.0 * distance * math.tan(max(fov, 0.05) * 0.5), 0.1)


def tuple_of(point):
    return (point.x, point.y, point.z)


class MotionHandler(adsk.core.CustomEventHandler):
    def __init__(self, addin):
        super().__init__()
        self.addin = addin

    def notify(self, args):
        try:
            self.addin.on_sample()
        except Exception:
            log(traceback.format_exc())


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, addin):
        super().__init__()
        self.addin = addin

    def notify(self, args):
        command = args.command
        handler = ExecuteHandler(self.addin)
        command.execute.add(handler)
        _handlers.append(handler)


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, addin):
        super().__init__()
        self.addin = addin

    def notify(self, args):
        try:
            self.addin.toggle_palette()
        except Exception:
            log(traceback.format_exc())


class ReadyHandler(adsk.core.HTMLEventHandler):
    def __init__(self, addin):
        super().__init__()
        self.addin = addin

    def notify(self, args):
        action = ""
        data = ""
        try:
            action = args.action or ""
            data = args.data or ""
        except Exception:
            log(traceback.format_exc())
            return
        try:
            if action == "setting":
                self.addin.apply_setting(data)
            elif action == "ready":
                self.addin._push_palette(force=True)
        except Exception:
            log(traceback.format_exc())


class AddIn:
    def __init__(self):
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface
        self.lock = threading.Lock()
        self.axes = [0, 0, 0, 0, 0, 0]
        self.buttons = 0
        self.status = "waiting for the device…"
        self.ok = False
        self.bad = False
        self.button_text = ""
        self.prev_buttons = 0
        self.edges = []
        self.held_keys = set()
        self.last_apply = None
        self.last_palette = 0.0
        self.config = load_config()
        self.config_mtime = self._mtime()
        self.stop_flag = False
        self.proc = None
        self.thread = None
        self.palette = None
        self.controls = []
        self.command = None
        self.custom_event = None
        self._shown_error = False
        self._revealed = False
        self._view = None
        self._logged_cam = False
        self._logged_noview = False
        self._buttons_dirty = False
        self.reports = 0
        self._saw_motion = False

    def start(self):
        self.event_id = EVENT_ID
        try:
            self.custom_event = self.app.registerCustomEvent(self.event_id)
        except Exception:
            self.event_id = "%s_%d" % (EVENT_ID, int(time.time()))
            self.custom_event = self.app.registerCustomEvent(self.event_id)
        handler = MotionHandler(self)
        self.custom_event.add(handler)
        _handlers.append(handler)
        try:
            self._install_command()
        except Exception:
            log(traceback.format_exc())
        try:
            self._ensure_palette(visible=False)
        except Exception:
            log(traceback.format_exc())
        import importlib
        importlib.reload(motion)
        self.thread = threading.Thread(target=self._read_loop, name="spacepilot", daemon=True)
        self.thread.start()
        log("gestartet")

    def stop(self):
        self.stop_flag = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if self.thread is not None:
            self.thread.join(timeout=1.5)
        for keycode in list(self.held_keys):
            post_key(keycode, False)
        self.held_keys.clear()
        try:
            if self.palette:
                self.palette.deleteMe()
        except Exception:
            pass
        for control in self.controls:
            try:
                control.deleteMe()
            except Exception:
                pass
        try:
            if self.command:
                self.command.deleteMe()
        except Exception:
            pass
        log("gestoppt")

    def toggle_palette(self):
        palette = self._ensure_palette(visible=True)
        if palette is None:
            self.ui.messageBox(self._status_text(), "SpacePilot")
            return
        if not palette.isVisible:
            palette.isVisible = True
        self._push_palette(force=True)

    def on_sample(self):
        self._reload_config()
        with self.lock:
            raw = list(self.axes)
            buttons = self.buttons
            status = self.status
            ok = self.ok
            bad = self.bad
            button_text = self.button_text
        self._handle_buttons()
        with self.lock:
            button_text = self.button_text
            held = self.buttons
        self._apply_motion(raw)
        now = time.monotonic()
        dirty = self._buttons_dirty
        self._buttons_dirty = False
        if dirty or now - self.last_palette > 0.1:
            self.last_palette = now
            self._push_palette(
                force=dirty, status=status, ok=ok, bad=bad, raw=raw,
                button_text=button_text, held=held,
            )
        if ok and not self._revealed:
            self._revealed = True
            self._ensure_palette(visible=True)
        if bad and not self._shown_error:
            self._shown_error = True
            self.ui.messageBox(status, "SpacePilot")

    def _read_loop(self):
        while not self.stop_flag:
            if not os.path.isfile(HELPER) or not os.access(HELPER, os.X_OK):
                self._set_status("helper missing: %s" % HELPER, ok=False, bad=True)
                self._fire()
                time.sleep(2)
                continue
            try:
                self.proc = subprocess.Popen(
                    [HELPER],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                self._set_status("failed to start: %s" % exc, ok=False, bad=True)
                self._fire()
                time.sleep(2)
                continue
            log("reader pid %s" % self.proc.pid)
            for line in self.proc.stdout:
                if self.stop_flag:
                    break
                self._ingest(line.strip())
            if self.stop_flag:
                break
            code = self.proc.poll()
            self._set_status("connection ended (code %s), retrying…" % code, ok=False, bad=False)
            self._fire()
            time.sleep(1.0)

    def _ingest(self, line):
        if not line:
            return
        try:
            msg = json.loads(line)
        except Exception:
            return
        if "status" in msg:
            status = msg["status"]
            if status == "connected":
                self._set_status("SpacePilot connected", ok=True, bad=False)
            elif status == "shared":
                self._set_status("SpacePilot connected, macOS is still moving the pointer", ok=True, bad=True)
            elif status == "disconnected":
                self._set_status("SpacePilot disconnected", ok=False, bad=False)
                self._set_axes([0, 0, 0, 0, 0, 0], 0)
            elif status == "waiting":
                self._set_status("waiting for the SpacePilot…", ok=False, bad=False)
            elif status == "error":
                self._set_status(msg.get("message", "Error"), ok=False, bad=True)
            self._fire()
            return
        if "tx" not in msg:
            return
        axes = [msg.get(name, 0) for name in ("tx", "ty", "tz", "rx", "ry", "rz")]
        reports = int(msg.get("n", 0))
        self.reports = reports
        self._set_axes(axes, int(msg.get("b", 0)))
        if reports > 0:
            self._set_status("SpacePilot connected, %d reports" % reports, ok=True, bad=False)
        else:
            self._set_status("SpacePilot connected. Move the cap.", ok=True, bad=False)
        if any(abs(value) > 15 for value in axes) and not self._saw_motion:
            self._saw_motion = True
            log("bewegung %s" % axes)
        self._fire()

    def _set_axes(self, axes, buttons):
        with self.lock:
            self.axes = axes
            if buttons != self.prev_buttons:
                for index in range(21):
                    now = bool(buttons & (1 << index))
                    was = bool(self.prev_buttons & (1 << index))
                    if now != was:
                        self.edges.append((index, now))
                self.prev_buttons = buttons
            self.buttons = buttons

    def _set_status(self, status, ok, bad):
        with self.lock:
            self.status = status
            self.ok = ok
            self.bad = bad

    def _fire(self):
        try:
            self.app.fireCustomEvent(self.event_id, "")
        except Exception:
            pass

    def _reload_config(self):
        mtime = self._mtime()
        if mtime != self.config_mtime and mtime is not None:
            self.config = load_config()
            self.config_mtime = mtime

    def _mtime(self):
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return None

    def _apply_motion(self, raw):
        cfg = self.config
        try:
            axis_range = float(cfg.get("axisRange", 500))
            deadzone = float(cfg.get("deadzone", 0.07))
            curve = float(cfg.get("curve", 2.0))
            shaped = [motion.shape(value, axis_range, deadzone, curve) for value in raw]
            mapped = motion.map_axes(shaped, cfg.get("map") or motion.DEFAULT_MAP)
        except Exception as exc:
            log("achsen: %s" % exc)
            return
        if cfg.get("dominant"):
            key = max(mapped, key=lambda name: abs(mapped[name]))
            mapped = {name: (mapped[name] if name == key else 0.0) for name in mapped}
        moving = any(abs(value) > 1e-6 for value in mapped.values())
        now = time.monotonic()
        if not moving:
            self.last_apply = None
            self._view = None
            return
        if self.last_apply is None:
            dt = 1.0 / 30.0
        else:
            dt = min(max(now - self.last_apply, 0.0), 0.05)
        self.last_apply = now
        try:
            viewport = self.app.activeViewport
            if viewport is None:
                if not self._logged_noview:
                    self._logged_noview = True
                    log("kein ansichtsfenster")
                self.button_text = "Open a design, then the cap moves the view."
                return
            if self.button_text.startswith("Open a design"):
                self.button_text = ""
            camera = viewport.camera
            if self._view is None:
                eye = tuple_of(camera.eye)
                target = tuple_of(camera.target)
                up = tuple_of(camera.upVector)
                ortho = camera.cameraType == adsk.core.CameraTypes.OrthographicCameraType
                self._view = {
                    "eye": eye,
                    "target": target,
                    "up": up,
                    "ortho": ortho,
                    "extents": extents_of(camera) if ortho else None,
                    "fov": camera.perspectiveAngle,
                }
            view = self._view
            speed = float(cfg.get("speed", 1.0))
            speeds = {
                "pan": float(cfg.get("panSpeed", 0.8)) * speed,
                "zoom": float(cfg.get("zoomSpeed", 1.15)) * speed,
                "orbit": float(cfg.get("orbitSpeed", 1.7)) * speed,
                "roll": float(cfg.get("rollSpeed", 1.8)) * speed,
            }
            object_mode = bool(cfg.get("objectMode", True))
            eye, target, up = view["eye"], view["target"], view["up"]
            if view["ortho"] and view["extents"]:
                height = view["extents"][1]
            else:
                height = view_height(camera, eye, target)
            # Orthographic zoom is the view height, not the eye distance.
            eye_axes = mapped
            if view["ortho"] and view["extents"]:
                eye_axes = dict(mapped)
                eye_axes["zoom"] = 0.0
            eye, target, up = motion.integrate(
                eye, target, up, eye_axes, dt, height, speeds, object_mode,
            )
            if view["ortho"] and view["extents"]:
                factor = motion.zoom_scale(mapped["zoom"], speeds["zoom"], dt, object_mode)
                width, extent_height = view["extents"]
                view["extents"] = (
                    min(max(width * factor, 0.01), 1.0e7),
                    min(max(extent_height * factor, 0.01), 1.0e7),
                )
            view["eye"], view["target"], view["up"] = eye, target, up
            camera.isSmoothTransition = False
            camera.isFitView = False
            camera.eye = adsk.core.Point3D.create(*eye)
            camera.target = adsk.core.Point3D.create(*target)
            camera.upVector = adsk.core.Vector3D.create(*up)
            if view["ortho"] and view["extents"]:
                camera.setExtents(view["extents"][0], view["extents"][1])
            viewport.camera = camera
            try:
                viewport.refresh()
            except Exception:
                pass
            if not self._logged_cam:
                self._logged_cam = True
                back = tuple_of(viewport.camera.eye)
                log(
                    "kamera %s abweichung %.4f"
                    % ("ortho" if view["ortho"] else "perspektive", math.dist(back, eye))
                )
        except Exception as exc:
            self._view = None
            message = "kamera: %s" % exc
            if message != getattr(self, "_last_cam_error", None):
                self._last_cam_error = message
                log(message)

    def _handle_buttons(self):
        with self.lock:
            edges = self.edges
            self.edges = []
            mapping = dict(self.config.get("buttons") or {})
        notes = []
        for index, down in edges:
            action = str(mapping.get(str(index), "") or "")
            if down:
                if action:
                    notes.append("Button %d → %s" % (index, action))
                else:
                    notes.append("Button %d, choose an action below" % index)
            self._buttons_dirty = True
            if action.startswith("key:"):
                keycode = KEY_CODES.get(action.split(":", 1)[1].lower())
                if keycode is None:
                    continue
                if down and keycode not in self.held_keys:
                    if post_key(keycode, True):
                        self.held_keys.add(keycode)
                elif not down and keycode in self.held_keys:
                    post_key(keycode, False)
                    self.held_keys.discard(keycode)
            elif down and action:
                self._run_action(action)
        if notes:
            self.button_text = "  ".join(notes)
            log(self.button_text)

    def _run_action(self, action):
        name = action.strip().lower()
        try:
            if name == "fit":
                viewport = self.app.activeViewport
                if viewport:
                    viewport.fit()
                return
            if name in VIEW_BY_NAME:
                self._set_orientation(VIEW_BY_NAME[name], fit=(name == "iso"))
                return
            if name == "undo":
                self._run_command(("UndoCommand", "FusionUndoCommand"))
                return
            if name == "redo":
                self._run_command(("RedoCommand", "FusionRedoCommand"))
                return
            if name in ("faster", "slower"):
                cfg = self.config
                speed = float(cfg.get("speed", 1.0))
                speed = speed * 1.25 if name == "faster" else speed / 1.25
                cfg["speed"] = max(0.2, min(5.0, speed))
                save_config(cfg)
                self.config_mtime = self._mtime()
                self.button_text = "Speed %.2f" % cfg["speed"]
                return
            log("unbekannte Aktion %s" % action)
        except Exception as exc:
            log("aktion %s: %s" % (name, exc))

    def _set_orientation(self, orientation, fit):
        viewport = self.app.activeViewport
        if viewport is None:
            return
        camera = viewport.camera
        camera.isSmoothTransition = True
        camera.viewOrientation = orientation
        viewport.camera = camera
        if fit:
            viewport.fit()

    def _run_command(self, candidates):
        defs = self.ui.commandDefinitions
        for name in candidates:
            command = defs.itemById(name)
            if command:
                command.execute()
                return
        log("befehl nicht gefunden: %s" % ", ".join(candidates))

    def _ensure_palette(self, visible):
        try:
            palette = self.ui.palettes.itemById(PALETTE_ID)
        except Exception:
            palette = None
        if palette is None:
            html = os.path.join(_HERE, "index.html")
            try:
                palette = self.ui.palettes.add(
                    PALETTE_ID, "SpacePilot", html, visible, True, True, 380, 720, True
                )
            except Exception as exc:
                log("palette: %s" % exc)
                return None
            try:
                palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
            except Exception:
                pass
            handler = ReadyHandler(self)
            self._html_handler = handler
            _handlers.append(handler)
            palette.incomingFromHTML.add(handler)
        elif visible:
            palette.isVisible = True
        self.palette = palette
        return palette

    def _push_palette(self, force, status=None, ok=None, bad=None, raw=None, button_text=None, held=None):
        palette = self.palette
        if palette is None:
            return
        try:
            if not force and not palette.isVisible:
                return
        except Exception:
            return
        if raw is None or held is None:
            with self.lock:
                if raw is None:
                    raw = list(self.axes)
                    status = self.status
                    ok = self.ok
                    bad = self.bad
                    button_text = self.button_text
                if held is None:
                    held = self.buttons
        cfg = self.config
        try:
            axis_range = float(cfg.get("axisRange", 500))
            shaped = [motion.shape(value, axis_range, 0.0, 1.0) for value in raw]
        except Exception:
            shaped = [0, 0, 0, 0, 0, 0]
        mapping = cfg.get("map") or motion.DEFAULT_MAP
        held_mask = int(held or 0)
        payload = json.dumps({
            "status": status,
            "ok": bool(ok),
            "bad": bool(bad),
            "axes": shaped,
            "buttonText": button_text or "",
            "settings": {
                "speed": float(cfg.get("speed", 1.0)),
                "map": mapping,
                "reversed": {
                    key: motion.is_reversed(mapping.get(key, default), default)
                    for key, default in motion.DEFAULT_MAP.items()
                },
                "buttons": cfg.get("buttons") or {},
                "held": [index for index in range(21) if held_mask & (1 << index)],
                "actions": [{"id": key, "label": label} for key, label in BUTTON_CHOICES],
            },
        })
        try:
            result = palette.sendInfoToHTML("state", payload)
            if not getattr(self, "_logged_html", False):
                self._logged_html = True
                log("html antwort: %s" % result)
        except Exception as exc:
            if not getattr(self, "_logged_html", False):
                self._logged_html = True
                log("html fehler: %s" % exc)

    def apply_setting(self, data):
        try:
            msg = json.loads(data or "{}")
        except Exception:
            log("einstellung: ungültig")
            return
        if not isinstance(msg, dict):
            return
        cfg = self.config
        changed = False
        if "speed" in msg:
            try:
                speed = float(msg["speed"])
            except (TypeError, ValueError):
                speed = None
            if speed is not None:
                cfg["speed"] = max(0.2, min(5.0, speed))
                changed = True
        flip = msg.get("flip")
        if flip in motion.DEFAULT_MAP:
            mapping = cfg.setdefault("map", {})
            current = mapping.get(flip) or motion.DEFAULT_MAP[flip]
            mapping[flip] = motion.flip_spec(current)
            changed = True
        if "button" in msg:
            try:
                index = int(msg["button"])
            except (TypeError, ValueError):
                index = -1
            action = str(msg.get("action") or "")
            if 0 <= index <= 20 and action in BUTTON_ACTIONS:
                buttons = cfg.setdefault("buttons", {})
                if action:
                    buttons[str(index)] = action
                else:
                    buttons.pop(str(index), None)
                changed = True
        if not changed:
            return
        try:
            save_config(cfg)
            self.config_mtime = self._mtime()
        except Exception as exc:
            log("einstellung: %s" % exc)
            return
        self._push_palette(force=True)

    def _status_text(self):
        with self.lock:
            return self.status + ("\n" + self.button_text if self.button_text else "")

    def _install_command(self):
        existing = self.ui.commandDefinitions.itemById(CMD_ID)
        if existing:
            existing.deleteMe()
        command = self.ui.commandDefinitions.addButtonDefinition(
            CMD_ID,
            "SpacePilot",
            "Open the SpacePilot panel. Shows axes and button numbers.",
            "",
        )
        created = CreatedHandler(self)
        command.commandCreated.add(created)
        _handlers.append(created)
        self.command = command
        try:
            product_types = list(self.app.supportedProductTypes)
        except Exception:
            product_types = ["DesignProductType"]
        for product_type in product_types:
            try:
                workspaces = self.ui.workspacesByProductType(product_type)
            except Exception:
                continue
            for index in range(workspaces.count):
                try:
                    panel = workspaces.item(index).toolbarPanels.itemById("SolidScriptsAddinsPanel")
                    if panel is None or panel.controls.itemById(CMD_ID) is not None:
                        continue
                    self.controls.append(panel.controls.addCommand(command))
                except Exception:
                    log(traceback.format_exc())


def run(_context):
    global _addin
    try:
        _addin = AddIn()
        _addin.start()
    except Exception:
        log(traceback.format_exc())
        try:
            adsk.core.Application.get().userInterface.messageBox(
                "The SpacePilot add-in could not start.\nDetails: %s" % LOG_PATH,
                "SpacePilot",
            )
        except Exception:
            pass


def stop(_context):
    global _addin
    if _addin is not None:
        try:
            _addin.stop()
        except Exception:
            log(traceback.format_exc())
        _addin = None

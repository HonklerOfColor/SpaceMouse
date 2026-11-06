"""View motion for a SpacePilot cap. No Fusion dependency, so it can be tested."""

import math

AXIS = {"tx": 0, "ty": 1, "tz": 2, "rx": 3, "ry": 4, "rz": 5}

# HID frame of the cap: +X right, +Y toward the user, +Z down.
# Object mode: push away pans up, push down zooms in.
DEFAULT_MAP = {
    "panX": "tx",
    "panY": "-ty",
    "zoom": "-tz",
    "pitch": "rx",
    "yaw": "-rz",
    "roll": "-ry",
}


def flip_spec(spec):
    """Toggle the leading minus on a map entry such as 'ty' or '-tz'."""
    text = str(spec or "").strip()
    if text.startswith("-"):
        return text[1:]
    if text.startswith("+"):
        return "-" + text[1:]
    if not text:
        return text
    return "-" + text


def is_reversed(spec, default):
    """True when spec points the opposite way from the calibrated default."""
    def negative(text):
        return str(text or "").lstrip().startswith("-")

    return negative(spec) != negative(default)


def clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def shape(raw, axis_range, deadzone, power):
    """Raw int16 deflection -> -1..1 with a deadzone and a response curve."""
    value = clamp(raw / float(axis_range), -1.5, 1.5)
    magnitude = abs(value)
    if magnitude < deadzone:
        return 0.0
    span = 1.0 - deadzone
    shaped = clamp((magnitude - deadzone) / span, 0.0, 1.0)
    if power != 1.0:
        shaped = shaped ** power
    return math.copysign(shaped, value)


def pick(spec, raw_axes):
    sign = 1.0
    name = spec
    if name.startswith("-"):
        sign = -1.0
        name = name[1:]
    if name.startswith("+"):
        name = name[1:]
    return sign * raw_axes[AXIS[name]]


def map_axes(raw_six, mapping):
    return {key: pick(mapping[key], raw_six) for key in DEFAULT_MAP}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(v):
    return math.sqrt(_dot(v, v))


def _normalize(v):
    length = _length(v)
    if length < 1e-12:
        return None
    return _scale(v, 1.0 / length)


def basis(eye, target, up):
    forward = _normalize(_sub(target, eye))
    if forward is None:
        return None
    right = _normalize(_cross(forward, up))
    if right is None:
        return None
    true_up = _cross(right, forward)
    return right, forward, true_up


def _rotate(vector, axis, angle):
    unit = _normalize(axis)
    if unit is None or angle == 0.0:
        return vector
    cos = math.cos(angle)
    sin = math.sin(angle)
    return _add(
        _add(_scale(vector, cos), _scale(_cross(unit, vector), sin)),
        _scale(unit, _dot(unit, vector) * (1.0 - cos)),
    )


def _rotate_about(point, origin, axis, angle):
    return _add(origin, _rotate(_sub(point, origin), axis, angle))


def zoom_scale(zoom_axis, zoom_speed, dt, object_mode):
    """Scale for eye distance and orthographic extents. Values below 1 zoom in."""
    axis = zoom_axis if object_mode else -zoom_axis
    return math.exp(-axis * zoom_speed * dt)


def upright_axis(up):
    """World +Y or +Z, whichever lies closer to the camera's up vector."""
    return (0.0, 0.0, 1.0) if abs(up[2]) >= abs(up[1]) else (0.0, 1.0, 0.0)


# How clearly the view must favour the other side before the twist axis turns
# over while the cap is held. About 6 degrees past the switch at 45 degrees.
FLIP_MARGIN = 0.15


def facing(axis, eye, target, up, previous=None):
    """The world axis, reversed where needed so twist turns the same way on screen.

    Seen from the side, the axis takes the side of the camera's up, so an
    upside-down model turns like an upright one. Seen from above or below,
    the axis points back at the camera, so Bottom spins the way Top does.
    previous is the axis from the last step. It is kept until the view
    clearly favours the other side, so the axis does not flicker.
    """
    if axis is None:
        return None
    given = tuple(axis)
    reverse = tuple(-c or 0.0 for c in axis)
    unit = _normalize(axis)
    frame = basis(eye, target, up)
    if unit is None or frame is None:
        return tuple(previous) if previous is not None else given
    _right, forward, true_up = frame
    along_up = _dot(unit, true_up)
    along_view = _dot(unit, forward)
    # Each side's case is the stronger of the two cues for it.
    keep = max(along_up, -along_view)
    turn = max(-along_up, along_view)
    if previous is not None and tuple(previous) == reverse:
        return given if keep > turn + FLIP_MARGIN else reverse
    if previous is not None and tuple(previous) == given:
        return reverse if turn > keep + FLIP_MARGIN else given
    return reverse if turn > keep else given


def integrate(eye, target, up, axes, dt, view_height, speeds, object_mode, world_up=None):
    """Move a look-at camera from cap axes panX, panY, zoom, pitch, yaw, roll.

    Axes are already shaped to about -1..1. Object mode moves the model with
    the hand, so the camera travels the opposite way. With world_up, twist
    turns around that axis and keeps the horizon level. Without it, twist
    turns around the vertical axis of the screen.
    """
    dt = clamp(dt, 0.0, 0.05)
    active = any(abs(axes[key]) > 1e-9 for key in ("panX", "panY", "zoom", "pitch", "yaw", "roll"))
    if dt == 0.0 or not active:
        return eye, target, up

    frame = basis(eye, target, up)
    if frame is None:
        return eye, target, up
    right, forward, true_up = frame
    direction = -1.0 if object_mode else 1.0

    pan = view_height * speeds["pan"] * dt
    shift = _add(
        _scale(right, direction * axes["panX"] * pan),
        _scale(true_up, direction * axes["panY"] * pan),
    )
    eye = _add(eye, shift)
    target = _add(target, shift)

    frame = basis(eye, target, true_up)
    if frame is None:
        return eye, target, up
    right, forward, true_up = frame
    distance = _length(_sub(target, eye))
    if distance < 1e-9:
        return eye, target, up
    # Positive zoom brings the model closer. Camera mode flies the opposite way.
    factor = zoom_scale(axes["zoom"], speeds["zoom"], dt, object_mode)
    distance = clamp(distance * factor, 0.01, 1.0e7)
    eye = _sub(target, _scale(forward, distance))

    frame = basis(eye, target, true_up)
    if frame is None:
        return eye, target, up
    right, forward, true_up = frame

    pitch = direction * axes["pitch"] * speeds["orbit"] * dt
    eye = _rotate_about(eye, target, right, pitch)
    true_up = _rotate(true_up, right, pitch)

    frame = basis(eye, target, true_up)
    if frame is None:
        return eye, target, up
    right, forward, true_up = frame

    yaw_axis = true_up
    if world_up is not None:
        yaw_axis = _normalize(world_up) or true_up
    yaw = direction * axes["yaw"] * speeds["orbit"] * dt
    eye = _rotate_about(eye, target, yaw_axis, yaw)
    true_up = _rotate(true_up, yaw_axis, yaw)

    frame = basis(eye, target, true_up)
    if frame is None:
        return eye, target, up
    right, forward, true_up = frame

    roll = direction * axes["roll"] * speeds["roll"] * dt
    true_up = _rotate(true_up, forward, roll)
    true_up = _normalize(true_up) or true_up
    return eye, target, true_up

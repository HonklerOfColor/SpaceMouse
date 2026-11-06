import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import motion


def test_deadzone_and_curve():
    assert motion.shape(0, 500, 0.08, 2.0) == 0.0
    assert motion.shape(20, 500, 0.08, 2.0) == 0.0
    full = motion.shape(500, 500, 0.08, 2.0)
    assert abs(full - 1.0) < 1e-9
    half = motion.shape(250, 500, 0.0, 2.0)
    assert 0.2 < half < 0.3
    assert motion.shape(-500, 500, 0.08, 2.0) < 0


def test_map_minus():
    raw = [10, 20, 30, 1, 2, 3]
    shaped = [motion.shape(v, 500, 0.0, 1.0) for v in raw]
    mapped = motion.map_axes(shaped, dict(motion.DEFAULT_MAP))
    assert mapped["panX"] == shaped[0]
    assert mapped["panY"] == -shaped[1]
    assert mapped["zoom"] == -shaped[2]
    assert mapped["yaw"] == -shaped[5]
    assert mapped["roll"] == -shaped[4]
    assert motion.flip_spec("ty") == "-ty"
    assert motion.flip_spec("-tz") == "tz"
    assert motion.flip_spec("+rx") == "-rx"
    assert motion.is_reversed("tx", "tx") is False
    assert motion.is_reversed("-tx", "tx") is True
    assert motion.is_reversed("-tz", "-tz") is False
    assert motion.is_reversed("tz", "-tz") is True
    assert motion.zoom_scale(1.0, 1.0, 0.05, True) < 1.0


def test_pan_moves_model_with_the_hand():
    eye = (0.0, 0.0, 10.0)
    target = (0.0, 0.0, 0.0)
    up = (0.0, 1.0, 0.0)
    axes = {"panX": 1.0, "panY": 0.0, "zoom": 0.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    speeds = {"pan": 1.0, "zoom": 1.0, "orbit": 1.0, "roll": 1.0}
    eye2, target2, _ = motion.integrate(eye, target, up, axes, 0.05, 10.0, speeds, True)
    # Object mode: model goes right, camera goes left. dt is clamped to 0.05s.
    assert eye2[0] < -0.4
    assert target2[0] < -0.4
    assert abs(eye2[2] - 10.0) < 1e-6


def test_zoom_in_reduces_distance():
    eye = (0.0, 0.0, 10.0)
    target = (0.0, 0.0, 0.0)
    up = (0.0, 1.0, 0.0)
    axes = {"panX": 0.0, "panY": 0.0, "zoom": 1.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    speeds = {"pan": 1.0, "zoom": 1.0, "orbit": 1.0, "roll": 1.0}
    eye2, target2, _ = motion.integrate(eye, target, up, axes, 0.05, 10.0, speeds, True)
    before = math.dist(eye, target)
    after = math.dist(eye2, target2)
    assert after < before * 0.97
    assert abs(target2[0]) < 1e-9


def test_idle_does_not_move():
    eye = (1.0, 2.0, 3.0)
    target = (1.0, 2.0, 0.0)
    up = (0.0, 1.0, 0.0)
    axes = {k: 0.0 for k in ("panX", "panY", "zoom", "pitch", "yaw", "roll")}
    speeds = {"pan": 1.0, "zoom": 1.0, "orbit": 1.0, "roll": 1.0}
    eye2, target2, up2 = motion.integrate(eye, target, up, axes, 0.016, 8.0, speeds, True)
    assert eye2 == eye
    assert target2 == target
    assert up2 == up


def _tilt_then_twist(world_up):
    eye = (0.0, -10.0, 0.0)
    target = (0.0, 0.0, 0.0)
    up = (0.0, 0.0, 1.0)
    still = {k: 0.0 for k in ("panX", "panY", "zoom", "pitch", "yaw", "roll")}
    speeds = {"pan": 1.0, "zoom": 1.0, "orbit": 2.6, "roll": 1.8}
    for _ in range(10):
        eye, target, up = motion.integrate(
            eye, target, up, dict(still, pitch=1.0), 0.05, 10.0, speeds, True, world_up=world_up,
        )
    for _ in range(40):
        eye, target, up = motion.integrate(
            eye, target, up, dict(still, yaw=1.0), 0.05, 10.0, speeds, True, world_up=world_up,
        )
    return eye, target, up


def test_twist_keeps_horizon_level():
    world_up = (0.0, 0.0, 1.0)
    eye, target, up = _tilt_then_twist(world_up)
    right, _forward, _up = motion.basis(eye, target, up)
    assert abs(motion._dot(right, world_up)) < 1e-9
    assert abs(math.dist(eye, target) - 10.0) < 1e-6
    # Without a world axis, twist follows the screen and the horizon tilts.
    eye, target, up = _tilt_then_twist(None)
    right, _forward, _up = motion.basis(eye, target, up)
    assert abs(motion._dot(right, world_up)) > 0.5


def test_twist_direction_matches_screen_axis_when_level():
    eye = (0.0, -10.0, 0.0)
    target = (0.0, 0.0, 0.0)
    up = (0.0, 0.0, 1.0)
    axes = {"panX": 0.0, "panY": 0.0, "zoom": 0.0, "pitch": 0.0, "yaw": 1.0, "roll": 0.0}
    speeds = {"pan": 1.0, "zoom": 1.0, "orbit": 1.0, "roll": 1.0}
    screen = motion.integrate(eye, target, up, axes, 0.05, 10.0, speeds, True)
    world = motion.integrate(eye, target, up, axes, 0.05, 10.0, speeds, True, world_up=up)
    for a, b in zip(screen, world):
        assert math.dist(a, b) < 1e-9


TWIST = {"panX": 0.0, "panY": 0.0, "zoom": 0.0, "pitch": 0.0, "yaw": 1.0, "roll": 0.0}
SLOW = {"pan": 1.0, "zoom": 1.0, "orbit": 1.0, "roll": 1.0}
ORIGIN = (0.0, 0.0, 0.0)
Z_UP = (0.0, 0.0, 1.0)


def test_twist_upside_down_matches_screen_axis():
    # Model turned over: the camera's up points down the world axis.
    eye = (0.0, -10.0, 0.0)
    up = (0.0, 0.0, -1.0)
    world_up = motion.facing(Z_UP, eye, ORIGIN, up)
    assert world_up == (0.0, 0.0, -1.0)
    screen = motion.integrate(eye, ORIGIN, up, TWIST, 0.05, 10.0, SLOW, True)
    world = motion.integrate(eye, ORIGIN, up, TWIST, 0.05, 10.0, SLOW, True, world_up=world_up)
    for a, b in zip(screen, world):
        assert math.dist(a, b) < 1e-9


def _screen_spin(eye, up, world_up):
    """Which way the picture turns: the sign of up's turn around the view."""
    _eye, _target, after = motion.integrate(eye, ORIGIN, up, TWIST, 0.05, 10.0, SLOW, True, world_up=world_up)
    _right, forward, before = motion.basis(eye, ORIGIN, up)
    return motion._dot(motion._cross(before, after), forward)


def test_bottom_spins_like_top():
    top = ((0.0, 0.0, 10.0), (0.0, 1.0, 0.0))
    bottoms = [
        ((0.0, 0.0, -10.0), (0.0, -1.0, 0.0)),  # tilted over from Top
        ((0.0, 0.0, -10.0), (0.0, 1.0, 0.0)),  # ViewCube Bottom
        ((0.3, -0.2, -10.0), (0.0, -1.0, 0.0)),  # almost Bottom
    ]
    reference = _screen_spin(top[0], top[1], motion.facing(Z_UP, top[0], ORIGIN, top[1]))
    assert abs(reference) > 1e-3
    for eye, up in bottoms:
        spin = _screen_spin(eye, up, motion.facing(Z_UP, eye, ORIGIN, up))
        assert spin * reference > 0, (eye, up, spin, reference)
        # A fixed +Z spins the other way, which is the bug this guards.
        assert _screen_spin(eye, up, Z_UP) * reference < 0


def test_facing():
    side = (0.0, -10.0, 0.0)
    assert motion.facing(Z_UP, side, ORIGIN, (0.0, 0.3, 0.9)) == Z_UP
    assert motion.facing(Z_UP, side, ORIGIN, (0.0, 0.3, -0.9)) == (0.0, 0.0, -1.0)
    # From above the axis points at the camera, also upside down.
    above = (0.0, -2.0, 10.0)
    assert motion.facing(Z_UP, above, ORIGIN, (0.0, 1.0, 0.0)) == Z_UP
    assert motion.facing(Z_UP, above, ORIGIN, (0.0, -1.0, 0.0)) == Z_UP
    # From below it points the other way, so the picture spins as from above.
    below = (0.0, -2.0, -10.0)
    assert motion.facing(Z_UP, below, ORIGIN, (0.0, 1.0, 0.0)) == (0.0, 0.0, -1.0)
    assert motion.facing(None, side, ORIGIN, (0.0, 0.0, -1.0)) is None


def test_axis_turns_over_while_the_cap_is_held():
    # One gesture: tilt from the front over to upside down, never letting go.
    eye, target, up = (0.0, -10.0, 0.0), ORIGIN, Z_UP
    tilt = dict(TWIST, yaw=0.0, pitch=1.0)
    world_up, changes = None, 0
    for _ in range(70):
        axis = motion.facing(Z_UP, eye, target, up, world_up)
        changes += world_up is not None and axis != world_up
        world_up = axis
        eye, target, up = motion.integrate(eye, target, up, tilt, 0.05, 10.0, SLOW, True, world_up=world_up)
    assert motion._dot(motion.basis(eye, target, up)[2], Z_UP) < -0.9
    world_up = motion.facing(Z_UP, eye, target, up, world_up)
    assert world_up == (0.0, 0.0, -1.0)
    assert changes == 1, changes
    # The view is not quite level, so compare which way the eye moves.
    screen = motion.integrate(eye, target, up, TWIST, 0.05, 10.0, SLOW, True)[0]
    world = motion.integrate(eye, target, up, TWIST, 0.05, 10.0, SLOW, True, world_up=world_up)[0]
    assert motion._dot(motion._sub(screen, eye), motion._sub(world, eye)) > 0.0


def test_axis_does_not_flicker_near_the_switch():
    # Upright and below the model: past 45 degrees the spin takes over.
    def below(degrees):
        e = math.radians(degrees)
        return (0.0, -10.0 * math.cos(e), -10.0 * math.sin(e)), ORIGIN, Z_UP

    down = (0.0, 0.0, -1.0)
    assert motion.facing(Z_UP, *below(40)) == Z_UP
    assert motion.facing(Z_UP, *below(50)) == down
    assert motion.facing(Z_UP, *below(50), previous=Z_UP) == Z_UP
    assert motion.facing(Z_UP, *below(40), previous=down) == down
    assert motion.facing(Z_UP, *below(56), previous=Z_UP) == down
    assert motion.facing(Z_UP, *below(34), previous=down) == Z_UP


def test_upright_axis():
    assert motion.upright_axis((0.0, 0.1, 0.99)) == (0.0, 0.0, 1.0)
    assert motion.upright_axis((0.0, -0.9, 0.2)) == (0.0, 1.0, 0.0)


if __name__ == "__main__":
    test_deadzone_and_curve()
    test_map_minus()
    test_pan_moves_model_with_the_hand()
    test_zoom_in_reduces_distance()
    test_idle_does_not_move()
    test_twist_keeps_horizon_level()
    test_twist_direction_matches_screen_axis_when_level()
    test_twist_upside_down_matches_screen_axis()
    test_bottom_spins_like_top()
    test_facing()
    test_axis_turns_over_while_the_cap_is_held()
    test_axis_does_not_flicker_near_the_switch()
    test_upright_axis()
    print("ok")

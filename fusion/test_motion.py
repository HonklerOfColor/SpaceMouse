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


def test_twist_upside_down_matches_screen_axis():
    # Model turned over: the camera's up points down the world axis.
    eye = (0.0, -10.0, 0.0)
    target = (0.0, 0.0, 0.0)
    up = (0.0, 0.0, -1.0)
    axes = {"panX": 0.0, "panY": 0.0, "zoom": 0.0, "pitch": 0.0, "yaw": 1.0, "roll": 0.0}
    speeds = {"pan": 1.0, "zoom": 1.0, "orbit": 1.0, "roll": 1.0}
    world_up = motion.facing((0.0, 0.0, 1.0), up)
    assert world_up == (0.0, 0.0, -1.0)
    screen = motion.integrate(eye, target, up, axes, 0.05, 10.0, speeds, True)
    world = motion.integrate(eye, target, up, axes, 0.05, 10.0, speeds, True, world_up=world_up)
    for a, b in zip(screen, world):
        assert math.dist(a, b) < 1e-9


def test_facing():
    z = (0.0, 0.0, 1.0)
    assert motion.facing(z, (0.0, 0.3, 0.9)) == z
    assert motion.facing(z, (0.0, 0.3, -0.9)) == (0.0, 0.0, -1.0)
    # Top view looks along the axis. It keeps the axis as given.
    assert motion.facing(z, (0.0, 1.0, 0.0)) == z
    assert motion.facing(z, (0.0, 1.0, -1e-12)) == z
    assert motion.facing(None, (0.0, 0.0, -1.0)) is None


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
    test_facing()
    test_upright_axis()
    print("ok")

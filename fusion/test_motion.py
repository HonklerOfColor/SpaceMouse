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


if __name__ == "__main__":
    test_deadzone_and_curve()
    test_map_minus()
    test_pan_moves_model_with_the_hand()
    test_zoom_in_reduces_distance()
    test_idle_does_not_move()
    print("ok")

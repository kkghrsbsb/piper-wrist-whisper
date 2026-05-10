import numpy as np

from piper_teleop.pure_functions import (
    apply_deadzone,
    apply_lead_window,
    clip_target_to_limits,
    compute_target_increment,
)


def test_apply_deadzone_below_threshold_returns_zero():
    assert apply_deadzone(0.3, 0.5) == 0.0
    assert apply_deadzone(-0.3, 0.5) == 0.0


def test_apply_deadzone_above_threshold_returns_value():
    assert apply_deadzone(0.6, 0.5) == 0.6
    assert apply_deadzone(-0.6, 0.5) == -0.6


def test_apply_deadzone_at_threshold_returns_zero():
    # 实现是 |value| > deadzone,等于阈值时返回 0
    assert apply_deadzone(0.5, 0.5) == 0.0


def test_clip_target_within_range_unchanged():
    target = np.array([0.5, 1.0, -1.0, 0.0, 0.0, 0.0])
    limits = [(-2.0, 2.0)] * 6
    out = clip_target_to_limits(target, limits)
    np.testing.assert_array_equal(out, target)


def test_clip_target_clamps_to_limits():
    target = np.array([5.0, -5.0, 5.0, -5.0, 5.0, -5.0])
    limits = [(-1.0, 1.0)] * 6
    out = clip_target_to_limits(target, limits)
    np.testing.assert_array_equal(out, [1.0, -1.0, 1.0, -1.0, 1.0, -1.0])


def test_clip_target_does_not_mutate_input():
    target = np.array([5.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    limits = [(-1.0, 1.0)] * 6
    clip_target_to_limits(target, limits)
    assert target[0] == 5.0  # 输入未被改写


def test_clip_target_per_joint_limits():
    target = np.array([0.0, -1.0, 0.5, 0.0, 0.0, 0.0])
    limits = [
        (-2.6179, 2.6179),
        (0.0, 3.14),       # J2 下界 0,-1.0 应被裁到 0
        (-2.967, 0.0),     # J3 上界 0,0.5 应被裁到 0
        (-1.745, 1.745),
        (-1.22, 1.22),
        (-2.09439, 2.09439),
    ]
    out = clip_target_to_limits(target, limits)
    assert out[1] == 0.0
    assert out[2] == 0.0


def test_apply_lead_window_within_window_unchanged():
    current = np.zeros(6)
    target = np.array([0.05, -0.05, 0.0, 0.0, 0.0, 0.0])
    out = apply_lead_window(target, current, max_lead=0.1)
    np.testing.assert_array_equal(out, target)


def test_apply_lead_window_clips_excess_lead():
    current = np.zeros(6)
    target = np.array([0.5, -0.5, 0.0, 0.0, 0.0, 0.0])
    out = apply_lead_window(target, current, max_lead=0.1)
    np.testing.assert_allclose(out, [0.1, -0.1, 0.0, 0.0, 0.0, 0.0])


def test_apply_lead_window_relative_to_current():
    current = np.array([1.0, -1.0, 0.0, 0.0, 0.0, 0.0])
    target = np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    out = apply_lead_window(target, current, max_lead=0.1)
    # 相对 current 裁剪:1.0 + 0.1 = 1.1; -1.0 + 0.1 = -0.9
    np.testing.assert_allclose(out, [1.1, -0.9, 0.0, 0.0, 0.0, 0.0])


def _zero_axes():
    return {k: 0.0 for k in ("left_x", "left_y", "right_x", "right_y", "hat_x", "hat_y")}


def test_compute_target_increment_zero_input_unchanged():
    target = np.zeros(6)
    out = compute_target_increment(target, _zero_axes(), step=0.1)
    np.testing.assert_array_equal(out, target)


def test_compute_target_increment_left_x_negates_j1():
    target = np.zeros(6)
    axes = _zero_axes()
    axes["left_x"] = 1.0
    out = compute_target_increment(target, axes, step=0.1)
    # J1: -= left_x * step
    assert out[0] == -0.1
    np.testing.assert_array_equal(out[1:], np.zeros(5))


def test_compute_target_increment_full_axis_mapping():
    target = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    axes = {
        "left_x": 1.0,
        "left_y": 1.0,
        "right_y": 1.0,
        "right_x": 1.0,
        "hat_x": 1.0,
        "hat_y": 1.0,
    }
    out = compute_target_increment(target, axes, step=0.1)
    # J1 -= left_x; J2 -= left_y; J3 += right_y; J4 += hat_x; J5 -= hat_y; J6 += right_x
    np.testing.assert_allclose(out, [0.9, 0.9, 1.1, 1.1, 0.9, 1.1])


def test_compute_target_increment_does_not_mutate_input():
    target = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    axes = _zero_axes()
    axes["left_x"] = 1.0
    compute_target_increment(target, axes, step=0.1)
    assert target[0] == 1.0

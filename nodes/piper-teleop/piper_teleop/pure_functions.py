"""可单元测试的纯函数,gamepad 控制逻辑的核心计算部分。

这些函数不依赖 pygame、dora、piper-control,可以在 CI 中单独验证。
"""

import numpy as np


def apply_deadzone(value: float, deadzone: float) -> float:
    """摇杆死区:|value| <= deadzone 视为 0。"""
    return value if abs(value) > deadzone else 0.0


def clip_target_to_limits(
    target_q: np.ndarray,
    joint_limits: list[tuple[float, float]],
) -> np.ndarray:
    """按 URDF 关节限位裁剪 target_q,返回新数组(不修改输入)。"""
    out = target_q.copy()
    for i, (lo, hi) in enumerate(joint_limits):
        out[i] = np.clip(out[i], lo, hi)
    return out


def apply_lead_window(
    target_q: np.ndarray,
    current_q: np.ndarray,
    max_lead: float,
) -> np.ndarray:
    """限制 target_q 超前当前关节角的最大幅度,防止欠账积累。"""
    return np.clip(target_q, current_q - max_lead, current_q + max_lead)


def compute_target_increment(
    target_q: np.ndarray,
    axes: dict,
    step: float,
) -> np.ndarray:
    """根据手柄轴输入计算下一帧的 target_q。

    轴到关节的映射沿用 gamepad_joint_control.py:
      J1 ← -left_x      J2 ← -left_y      J3 ← +right_y
      J4 ← +hat_x       J5 ← -hat_y       J6 ← +right_x

    axes 字典 key:left_x, left_y, right_x, right_y, hat_x, hat_y。
    """
    out = target_q.copy()
    out[0] -= axes["left_x"] * step
    out[1] -= axes["left_y"] * step
    out[2] += axes["right_y"] * step
    out[3] += axes["hat_x"] * step
    out[4] -= axes["hat_y"] * step
    out[5] += axes["right_x"] * step
    return out

"""piper-gamepad 节点:pygame 读手柄 → publish joint_action 给 piper-bringup。

行为见 docs/src/plan/2026-05-piper-teleop-debug-plan.md §3.2。
"""

import numpy as np
import pyarrow as pa
import pygame
from dora import Node

from piper_teleop.constants import (
    ARM_MODE_SPEEDS,
    AXIS_MAP,
    BUTTON_MAP,
    DEADZONE,
    DEFAULT_SPEED_INDEX,
    GRIPPER_RANGE,
    GRIPPER_STEP,
    HAT_INDEX,
    JOINT_ANGLE_STEP,
    JOINT_LIMITS,
    SPEED_FACTORS,
    TARGET_MAX_LEAD,
    ZERO_POSITION,
)
from piper_teleop.pure_functions import (
    apply_deadzone,
    apply_lead_window,
    clip_target_to_limits,
    compute_target_increment,
)


class ButtonEdge:
    """按键上升沿检测器(按下瞬间触发一次)。"""

    def __init__(self):
        self._last = False

    def update(self, pressed: bool) -> bool:
        triggered = pressed and not self._last
        self._last = pressed
        return triggered


def _get_axis(joystick, name: str) -> float:
    idx = AXIS_MAP.get(name)
    if idx is None or idx >= joystick.get_numaxes():
        return 0.0
    value = joystick.get_axis(idx)
    if name in ("left_trigger", "right_trigger"):
        return (value + 1.0) / 2.0
    return value


def _get_hat(joystick) -> tuple[int, int]:
    if HAT_INDEX >= joystick.get_numhats():
        return (0, 0)
    return joystick.get_hat(HAT_INDEX)


def _get_button(joystick, name: str) -> bool:
    idx = BUTTON_MAP.get(name)
    if idx is None or idx >= joystick.get_numbuttons():
        return False
    return bool(joystick.get_button(idx))


def main():
    node = Node()
    pygame.init()
    pygame.joystick.init()

    joystick = None

    # 来自 piper-bringup 的状态缓存
    enabled = False
    at_zero = False
    current_q = np.array(ZERO_POSITION + [0.0], dtype=np.float32)  # 7 元素
    target_q = np.array(ZERO_POSITION, dtype=np.float32)           # 6 关节
    gripper_pos = 0.0
    target_initialized = False

    speed_index = DEFAULT_SPEED_INDEX
    btn_y = ButtonEdge()
    btn_a = ButtonEdge()
    btn_lb = ButtonEdge()
    btn_rb = ButtonEdge()

    # NOTE: record_event 输出在 plan §3.2 中预留,本批次不监听 Start 键、
    # 不实际 publish,留给后续 teach-recorder plan。

    print("piper-gamepad: started, waiting for bringup ready (enabled + at_zero)...")

    for event in node:
        if event["type"] != "INPUT":
            continue

        eid = event["id"]

        if eid == "enabled":
            enabled = bool(event["value"][0].as_py())
            print(f"piper-gamepad: enabled={enabled}")

        elif eid == "at_zero":
            at_zero = bool(event["value"][0].as_py())
            print(f"piper-gamepad: at_zero={at_zero}")

        elif eid == "jointstate":
            current_q = np.asarray(event["value"], dtype=np.float32)
            # 第一次拿到 jointstate 且 bringup ready,初始化 target_q
            if enabled and at_zero and not target_initialized:
                target_q = current_q[:6].copy()
                gripper_pos = float(current_q[6])
                target_initialized = True
                print(f"piper-gamepad: target_q initialized from jointstate")

        elif eid == "tick":
            # 处理 pygame 事件(手柄热插拔)
            for pe in pygame.event.get():
                if pe.type == pygame.JOYDEVICEADDED:
                    joystick = pygame.joystick.Joystick(pe.device_index)
                    joystick.init()
                    print(f"piper-gamepad: joystick connected: {joystick.get_name()}")
                elif pe.type == pygame.JOYDEVICEREMOVED:
                    joystick = None
                    print("piper-gamepad: joystick disconnected")

            if not (enabled and at_zero and target_initialized):
                continue
            if joystick is None:
                continue

            # 边沿检测的按键
            y_pressed = btn_y.update(_get_button(joystick, "y"))
            a_pressed = btn_a.update(_get_button(joystick, "a"))
            lb_pressed = btn_lb.update(_get_button(joystick, "lb"))
            rb_pressed = btn_rb.update(_get_button(joystick, "rb"))

            if a_pressed:
                print("piper-gamepad: A pressed → disable_request")
                node.send_output("disable_request", pa.array([True]))
                continue

            if y_pressed:
                print("piper-gamepad: Y pressed → home_request")
                node.send_output("home_request", pa.array([True]))
                # 让 bringup 接管回零,本节点等下次 jointstate 自动同步 target_q
                target_initialized = False
                continue

            if lb_pressed:
                speed_index = (speed_index + 1) % len(SPEED_FACTORS)
                print(
                    f"piper-gamepad: speed x{SPEED_FACTORS[speed_index]} "
                    f"arm_speed={ARM_MODE_SPEEDS[speed_index]}"
                )
            if rb_pressed:
                speed_index = (speed_index - 1) % len(SPEED_FACTORS)
                print(
                    f"piper-gamepad: speed x{SPEED_FACTORS[speed_index]} "
                    f"arm_speed={ARM_MODE_SPEEDS[speed_index]}"
                )

            speed_factor = SPEED_FACTORS[speed_index]

            # 读摇杆 + D-pad
            axes = {
                "left_x": apply_deadzone(_get_axis(joystick, "left_x"), DEADZONE),
                "left_y": apply_deadzone(_get_axis(joystick, "left_y"), DEADZONE),
                "right_x": apply_deadzone(_get_axis(joystick, "right_x"), DEADZONE),
                "right_y": apply_deadzone(_get_axis(joystick, "right_y"), DEADZONE),
            }
            hat_x, hat_y = _get_hat(joystick)
            axes["hat_x"] = float(hat_x)
            axes["hat_y"] = float(hat_y)

            has_joint_input = any(abs(v) > 0 for v in axes.values())
            current_joints = current_q[:6]

            # 无输入时对齐 target_q 到当前位,防止欠账积累
            if not has_joint_input:
                target_q = current_joints.copy()
            else:
                step = JOINT_ANGLE_STEP * speed_factor
                target_q = compute_target_increment(target_q, axes, step)
                target_q = apply_lead_window(target_q, current_joints, TARGET_MAX_LEAD)

            target_q = clip_target_to_limits(target_q, JOINT_LIMITS)

            # 夹爪由扳机控制
            lt = _get_axis(joystick, "left_trigger")
            rt = _get_axis(joystick, "right_trigger")
            gripper_delta = (rt - lt) * GRIPPER_STEP * speed_factor
            if gripper_delta != 0.0:
                lo, hi = GRIPPER_RANGE
                gripper_pos = float(np.clip(gripper_pos + gripper_delta, lo, hi))

            # publish joint_action: 6 关节 + 1 夹爪
            joint_action = np.concatenate(
                [target_q.astype(np.float32), np.array([gripper_pos], dtype=np.float32)]
            )
            node.send_output("joint_action", pa.array(joint_action))


if __name__ == "__main__":
    main()

"""piper-bringup 节点:CAN 连接 + 使能 + 回零 + 退出时 safe_shutdown。

行为见 docs/src/plan/2026-05-piper-teleop-debug-plan.md §3.2。
本批次(B)实现启动 + 退出 + home/disable 处理;joint_action 写入留给批次 C。
"""

import time
from typing import Callable

import numpy as np
import pyarrow as pa
from dora import Node
from piper_control import (
    piper_connect,
    piper_control,
    piper_init,
    piper_interface,
)

from piper_teleop.constants import (
    ENABLE_PROBE_INITIAL_DELAY,
    ENABLE_PROBE_INTERVAL,
    ENABLE_PROBE_SAMPLES,
    GRIPPER_EFFORT,
    GRIPPER_RANGE,
    JOINT_LIMITS,
    JOINT_SAFE_SPEED,
    MOVE_THRESHOLD,
    MOVE_TIMEOUT,
    SAFE_DISABLE_POSITION,
    SAFE_SHUTDOWN_PAUSE_SEC,
    ZERO_POSITION,
)
from piper_teleop.pure_functions import clip_target_to_limits


def probe_enabled(
    check_fn: Callable[[], bool],
    samples: int = ENABLE_PROBE_SAMPLES,
    initial_delay: float = ENABLE_PROBE_INITIAL_DELAY,
    interval: float = ENABLE_PROBE_INTERVAL,
) -> bool:
    """多次采样判断使能状态,任一为 True 即视为已使能。"""
    time.sleep(initial_delay)
    results = [check_fn()]
    for _ in range(samples - 1):
        time.sleep(interval)
        results.append(check_fn())
    return any(results)


def builtin_move(
    robot,
    target: list,
    speed: int = JOINT_SAFE_SPEED,
    threshold: float = MOVE_THRESHOLD,
    timeout: float = MOVE_TIMEOUT,
) -> bool:
    """用 BuiltinJointPositionController 阻塞式 move 到 target,返回是否到位。"""
    with piper_control.BuiltinJointPositionController(
        robot, rest_position=None
    ) as ctrl:
        robot.set_arm_mode(speed=speed)
        return ctrl.move_to_position(target, threshold=threshold, timeout=timeout)


def connect_and_enable():
    """三步 CAN 连接 + 使能臂与夹爪,返回 PiperInterface。"""
    ports = piper_connect.find_ports()
    print(f"piper-bringup: found ports {ports}")
    piper_connect.activate(ports)
    active = piper_connect.active_ports()
    if not active:
        raise RuntimeError("No active CAN ports found")
    print(f"piper-bringup: active ports {active}")

    robot = piper_interface.PiperInterface(can_port=active[0])
    print(f"piper-bringup: PiperInterface created on {active[0]}")

    if not probe_enabled(robot.is_arm_enabled):
        print("piper-bringup: arm not enabled, resetting...")
        piper_init.reset_arm(
            robot,
            arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
            move_mode=piper_interface.MoveMode.JOINT,
        )

    if not probe_enabled(robot.is_gripper_enabled):
        print("piper-bringup: gripper not enabled, resetting...")
        piper_init.reset_gripper(robot)

    return robot


def safe_shutdown(robot, pause_sec: float = SAFE_SHUTDOWN_PAUSE_SEC) -> None:
    """退出流程:回安全位 → 等待 → 失能夹爪 → 失能机械臂(顺序固定)。"""
    print("piper-bringup: safe_shutdown → moving to SAFE_DISABLE_POSITION")
    builtin_move(robot, SAFE_DISABLE_POSITION)
    time.sleep(pause_sec)
    print("piper-bringup: disable_gripper")
    robot.disable_gripper()
    print("piper-bringup: disable_arm")
    robot.disable_arm()
    print("piper-bringup: shutdown complete")


def get_jointstate(robot) -> np.ndarray:
    """读取 7 元素状态:6 关节角(rad) + 1 夹爪位置(m)。"""
    joints = robot.get_joint_positions()
    gripper_pos, _ = robot.get_gripper_state()
    return np.array(list(joints) + [float(gripper_pos)], dtype=np.float32)


def apply_joint_action(robot, action: np.ndarray) -> None:
    """处理 7 元素 joint_action:6 关节角(rad) + 1 夹爪位置(m)。

    防御性裁剪 → 下发 command_joint_positions + command_gripper。
    """
    if action.shape != (7,):
        raise ValueError(f"joint_action must be shape (7,), got {action.shape}")

    joints = clip_target_to_limits(action[:6], JOINT_LIMITS)
    gripper_lo, gripper_hi = GRIPPER_RANGE
    gripper = float(np.clip(action[6], gripper_lo, gripper_hi))

    robot.command_joint_positions(joints.tolist())
    robot.command_gripper(gripper, GRIPPER_EFFORT)


def main():
    node = Node()

    # 启动:连接 → 使能 → 回零
    robot = connect_and_enable()

    print(f"piper-bringup: calibrating to ZERO_POSITION {ZERO_POSITION}")
    at_zero = builtin_move(robot, ZERO_POSITION)
    print(f"piper-bringup: at_zero={at_zero}")

    # BuiltinJointPositionController 退出后切回 command_joint_positions 模式
    robot.set_arm_mode(speed=JOINT_SAFE_SPEED)

    # 通告 ready 状态
    node.send_output("enabled", pa.array([True]))
    node.send_output("at_zero", pa.array([at_zero]))
    node.send_output("jointstate", pa.array(get_jointstate(robot)))

    print("piper-bringup: entering main loop")

    try:
        for event in node:
            if event["type"] != "INPUT":
                continue

            eid = event["id"]

            if eid == "joint_action":
                action = np.asarray(event["value"], dtype=np.float32)
                apply_joint_action(robot, action)

            elif eid == "home_request":
                print("piper-bringup: home_request → moving to ZERO_POSITION")
                ok = builtin_move(robot, ZERO_POSITION)
                # controller 退出后切回 command 模式,后续 joint_action 才能下发
                robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
                node.send_output("at_zero", pa.array([ok]))

            elif eid == "disable_request":
                print("piper-bringup: disable_request → exiting main loop")
                break

            # 每个事件后 publish 一次 jointstate(批次 D 可视情况改为 timer 驱动)
            node.send_output("jointstate", pa.array(get_jointstate(robot)))

    finally:
        safe_shutdown(robot)


if __name__ == "__main__":
    main()

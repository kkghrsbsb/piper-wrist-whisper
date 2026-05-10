"""
纯 piper_control 版本的连接测试脚本（不依赖 lerobot）。

流程：发现 CAN → 使能臂和夹爪 → 回零位 → 等待用户按 Enter → 安全关闭。
"""

import time

from piper_control import piper_connect, piper_control, piper_init, piper_interface

# ── 常量 ───────────────────────────────────────────────────
INIT_JOINT_POSITION = [-1.5708, 0.0, 0.0, 0.0, 0.0, 0.0]
SAFE_DISABLE_POSITION = [-1.5708, 0.0, 0.0, 0.02, 0.5, 0.0]
JOINT_SAFE_SPEED = 10
MOVE_TIMEOUT = 12.0
MOVE_THRESHOLD = 0.01


# ── 工具函数 ───────────────────────────────────────────────
def probe_enabled(robot, check_fn, label, sample_count=5):
    """多次采样判断使能状态，防止单次误判。"""
    time.sleep(0.3)
    samples = [check_fn() for _ in range(sample_count) if not time.sleep(0.05)]
    enabled = any(samples)
    print(f"  {label} samples={samples} -> {'enabled' if enabled else 'disabled'}")
    return enabled


def builtin_move(robot, target):
    """用 BuiltinJointPositionController 阻塞式移动到目标位置。"""
    with piper_control.BuiltinJointPositionController(
        robot, rest_position=None
    ) as ctrl:
        robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
        print(f"  moving to {target}")
        ok = ctrl.move_to_position(target, threshold=MOVE_THRESHOLD, timeout=MOVE_TIMEOUT)
        print(f"  reached: {ok}")


def safe_shutdown(robot):
    """回安全位后失能臂和夹爪。"""
    print("moving to safe position...")
    builtin_move(robot, SAFE_DISABLE_POSITION)
    time.sleep(1)
    robot.disable_gripper()
    robot.disable_arm()
    print("arm and gripper disabled.")


# ── 主流程 ─────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("正在连接机械臂（connect + calibrate 回零位）...")
    print("=" * 60)

    # 1. 发现并激活 CAN
    ports = piper_connect.find_ports()
    print(f"found ports: {ports}")
    piper_connect.activate(ports)
    active = piper_connect.active_ports()
    if not active:
        raise RuntimeError("No active CAN ports found.")
    print(f"active ports: {active}")

    robot = piper_interface.PiperInterface(can_port=active[0])
    print(f"PiperInterface created on {active[0]}")

    # 2. 使能臂
    if not probe_enabled(robot, robot.is_arm_enabled, "arm"):
        print("resetting arm...")
        piper_init.reset_arm(
            robot,
            arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
            move_mode=piper_interface.MoveMode.JOINT,
        )

    # 3. 使能夹爪
    if not probe_enabled(robot, robot.is_gripper_enabled, "gripper"):
        print("resetting gripper...")
        piper_init.reset_gripper(robot)

    print(f"joints: {robot.get_joint_positions()}")
    print(f"gripper: {robot.get_gripper_state()}")
    robot.show_status()

    # 4. 回零位（calibrate）
    print("\ncalibrating (move to zero)...")
    builtin_move(robot, INIT_JOINT_POSITION)

    print()
    print("=" * 60)
    print("arm enabled and at zero position.")
    print("press Enter to safely shutdown...")
    print("=" * 60)

    try:
        input()
    except KeyboardInterrupt:
        print("\nCtrl+C received")

    safe_shutdown(robot)
    print("done.")


if __name__ == "__main__":
    main()

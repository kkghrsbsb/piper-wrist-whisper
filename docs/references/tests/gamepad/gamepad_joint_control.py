"""
检测机械臂是否已使能且在零位，然后可以通过手柄遥操

前置条件：先在另一个终端运行 tests/connect_init.py，使机械臂进入使能+零位状态。

本脚本通过独立的 PiperInterface 连接同一个 CAN 总线，已验证：
1. 能读取到使能状态和零位关节角
2. 能通过第二个 CAN 连接发送控制命令（CAN 共存）
"""

import time

import numpy as np
import pygame
from piper_control import piper_connect, piper_control, piper_interface

# ── 阈值常量 ──────────────────────────────────────────────
ZERO_POSITION_THRESHOLD = 0.05  # rad，判定"在零位"的最大偏差

# ── 关节限位 (rad)，来自 URDF ─────────────────────────────
JOINT_LIMITS = [
    (-2.6179, 2.6179),   # J1
    (0.0, 3.14),         # J2
    (-2.967, 0.0),       # J3
    (-1.745, 1.745),     # J4
    (-1.22, 1.22),       # J5
    (-2.09439, 2.09439), # J6
]

# ── 运动常量 ──────────────────────────────────────────────
JOINT_SAFE_SPEED = 10
MOVE_TIMEOUT = 12.0
MOVE_THRESHOLD = 0.01
ZERO_POSITION = [-1.5708, 0.0, 0.0, 0.0, 0.0, 0.0] 

# ── 手柄控制参数 ──────────────────────────────────────────
JOINT_ANGLE_STEP = 0.5 * np.pi / 180.0  # rad/tick，基础步长
GRIPPER_STEP = 0.001                      # m/tick，夹爪基础步长
GRIPPER_EFFORT = 0.5                      # Nm，夹爪力度
DEADZONE = 0.5                            # 摇杆死区

SPEED_FACTORS = [0.25, 0.5, 1.0, 2.0, 3.0]
ARM_MODE_SPEEDS = [5, 8, 12, 18, 25]  # 与 SPEED_FACTORS 一一对应的 set_arm_mode speed
TARGET_MAX_LEAD = 0.1  # rad，target_q 允许超前当前关节角的最大值
DEFAULT_SPEED_INDEX = 0  # 默认最低档，保证安全

CONTROL_LOOP_WAIT_MS = 5  # ~200Hz

# ── Linux 手柄按键映射 ────────────────────────────────────
BUTTON_MAP = {
    "a": 0, "b": 1, "x": 2, "y": 3,
    "lb": 4, "rb": 5, "back": 6, "start": 7,
    "home": 8, "l3": 9, "r3": 10,
}
AXIS_MAP = {
    "left_x": 0, "left_y": 1,
    "right_x": 3, "right_y": 4,
    "left_trigger": 2, "right_trigger": 5,
}
HAT_INDEX = 0  # D-pad


def check_arm_status(robot: piper_interface.PiperInterface) -> tuple[bool, bool]:
    """检测机械臂是否已使能且在零位。

    Returns:
        (is_enabled, is_at_zero): 使能状态, 是否在零位
    """
    # 多次采样判断使能状态
    time.sleep(0.3)
    enabled_samples = []
    for _ in range(5):
        enabled_samples.append(robot.is_arm_enabled())
        time.sleep(0.05)

    is_enabled = any(enabled_samples)
    print(f"  使能状态采样: {enabled_samples} → {'已使能' if is_enabled else '未使能'}")

    # 读取关节位置判断是否在零位
    joints = robot.get_joint_positions()
    print(f"  关节位置 (rad): {[f'{j:.4f}' for j in joints]}")

    max_deviation = max(abs(j - z) for j, z in zip(joints, ZERO_POSITION))
    is_at_zero = max_deviation < ZERO_POSITION_THRESHOLD
    print(
        f"  最大偏差: {max_deviation:.4f} rad, 阈值: {ZERO_POSITION_THRESHOLD}"
        f" → {'在零位' if is_at_zero else '不在零位'}"
    )

    return is_enabled, is_at_zero


def builtin_move(robot, target):
    """用 BuiltinJointPositionController 阻塞式移动到目标位置。"""
    with piper_control.BuiltinJointPositionController(
        robot, rest_position=None
    ) as ctrl:
        robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
        print(f"  moving to {target}")
        ok = ctrl.move_to_position(
            target, threshold=MOVE_THRESHOLD, timeout=MOVE_TIMEOUT
        )
        print(f"  reached: {ok}")
        return ok


def go_home_and_verify(robot):
    """回零位并验证到达。返回是否成功。"""
    print("\n── 回零位 ──")
    builtin_move(robot, ZERO_POSITION)
    # 回零后重新设置 arm mode，因为 BuiltinJointPositionController 退出后
    # 需要恢复到 command_joint_positions 可用的状态
    robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
    _, is_at_zero = check_arm_status(robot)
    if is_at_zero:
        print("  ✓ 已确认到达零位")
    else:
        print("  ⚠ 回零后仍未在零位阈值内")
    return is_at_zero


# ── 手柄输入工具 ──────────────────────────────────────────

def apply_deadzone(value):
    return value if abs(value) > DEADZONE else 0.0


def get_axis(joystick, name):
    """读取手柄轴值，扳机值归一化到 [0, 1]。"""
    idx = AXIS_MAP.get(name)
    if idx is None or idx >= joystick.get_numaxes():
        return 0.0
    value = joystick.get_axis(idx)
    if name in ("left_trigger", "right_trigger"):
        return (value + 1.0) / 2.0
    return value


def get_hat(joystick):
    """读取 D-pad，返回 (x, y)。"""
    if HAT_INDEX >= joystick.get_numhats():
        return (0, 0)
    return joystick.get_hat(HAT_INDEX)


def get_button_pressed(joystick, name):
    """读取按键当前是否被按下。"""
    idx = BUTTON_MAP.get(name)
    if idx is None or idx >= joystick.get_numbuttons():
        return False
    return joystick.get_button(idx)


class ButtonEdge:
    """检测按键的上升沿（按下瞬间触发一次）。"""

    def __init__(self):
        self._last = False

    def update(self, pressed: bool) -> bool:
        triggered = pressed and not self._last
        self._last = pressed
        return triggered


# ── 手柄控制主函数 ────────────────────────────────────────

def gamepad_control(robot: piper_interface.PiperInterface):
    """手柄遥操关节控制循环。"""

    # ── pygame 初始化 ──
    pygame.init()
    pygame.joystick.init()

    print("\n── 手柄遥操控制 ──")
    print("等待手柄连接...")
    print("（请插入手柄或按 Ctrl+C 退出）")

    joystick = None

    # ── 状态 ──
    target_q = np.array(robot.get_joint_positions(), dtype=float)
    gripper_pos = robot.get_gripper_state()[0]
    speed_index = DEFAULT_SPEED_INDEX

    # ── 按键边沿检测 ──
    btn_y = ButtonEdge()
    btn_a = ButtonEdge()
    btn_lb = ButtonEdge()
    btn_rb = ButtonEdge()

    # 先设置 arm mode，使 command_joint_positions 可用；初始使用最低档速度
    robot.set_arm_mode(speed=ARM_MODE_SPEEDS[speed_index])
    time.sleep(0.1)

    running = True
    try:
        while running:
            # ── 处理 pygame 事件（手柄热插拔） ──
            for event in pygame.event.get():
                if event.type == pygame.JOYDEVICEADDED:
                    joystick = pygame.joystick.Joystick(event.device_index)
                    joystick.init()
                    print(f"  手柄已连接: {joystick.get_name()}")
                elif event.type == pygame.JOYDEVICEREMOVED:
                    joystick = None
                    print("  手柄已断开，等待重新连接...")

            if joystick is None:
                pygame.time.wait(100)
                continue

            # ── 读取按键 ──
            y_pressed = btn_y.update(get_button_pressed(joystick, "y"))
            a_pressed = btn_a.update(get_button_pressed(joystick, "a"))
            lb_pressed = btn_lb.update(get_button_pressed(joystick, "lb"))
            rb_pressed = btn_rb.update(get_button_pressed(joystick, "rb"))

            # ── A: 回零位并退出 ──
            if a_pressed:
                print("\nA 按下，准备退出...")
                go_home_and_verify(robot)
                running = False
                break

            # ── Y: 回零位后继续 ──
            if y_pressed:
                print("\nY 按下，回零位...")
                if go_home_and_verify(robot):
                    target_q = np.array(ZERO_POSITION, dtype=float)
                    gripper_pos = 0.0
                    # 恢复 arm mode
                    robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
                    time.sleep(0.1)
                    print("  已恢复手柄控制")
                else:
                    print("  ⚠ 回零失败，继续当前控制")
                continue

            # ── LB/RB: 速度因子切换 ──
            if lb_pressed:
                speed_index = (speed_index + 1) % len(SPEED_FACTORS)
                robot.set_arm_mode(speed=ARM_MODE_SPEEDS[speed_index])
                print(f"  速度因子: x{SPEED_FACTORS[speed_index]}  arm_speed={ARM_MODE_SPEEDS[speed_index]}")
            if rb_pressed:
                speed_index = (speed_index - 1) % len(SPEED_FACTORS)
                robot.set_arm_mode(speed=ARM_MODE_SPEEDS[speed_index])
                print(f"  速度因子: x{SPEED_FACTORS[speed_index]}  arm_speed={ARM_MODE_SPEEDS[speed_index]}")

            speed_factor = SPEED_FACTORS[speed_index]

            # ── 读取摇杆 ──
            left_x = apply_deadzone(get_axis(joystick, "left_x"))
            left_y = apply_deadzone(get_axis(joystick, "left_y"))
            right_x = apply_deadzone(get_axis(joystick, "right_x"))
            right_y = apply_deadzone(get_axis(joystick, "right_y"))
            hat_x, hat_y = get_hat(joystick)

            has_joint_input = bool(left_x or left_y or right_x or right_y or hat_x or hat_y)

            current_q = np.array(robot.get_joint_positions(), dtype=float)

            # 无输入时对齐到当前位，防止松手后继续追赶残余欠账
            if not has_joint_input:
                target_q = current_q.copy()
            else:
                # ── 关节增量 ──
                step = JOINT_ANGLE_STEP * speed_factor
                target_q[0] -= left_x * step   # J1: 左摇杆 左/右
                target_q[1] -= left_y * step   # J2: 左摇杆 上/下
                target_q[2] += right_y * step  # J3: 右摇杆 上/下
                target_q[3] += hat_x * step    # J4: D-pad 左/右
                target_q[4] -= hat_y * step    # J5: D-pad 上/下
                target_q[5] += right_x * step  # J6: 右摇杆 左/右

                # 限制 target_q 超前窗口，防止大欠账积累
                target_q = np.clip(target_q, current_q - TARGET_MAX_LEAD, current_q + TARGET_MAX_LEAD)

            # ── 关节限位裁剪 ──
            for i in range(6):
                lo, hi = JOINT_LIMITS[i]
                target_q[i] = np.clip(target_q[i], lo, hi)

            # ── 下发关节命令 ──
            robot.command_joint_positions(target_q.tolist())

            # ── 夹爪控制 ──
            lt = get_axis(joystick, "left_trigger")
            rt = get_axis(joystick, "right_trigger")
            gripper_delta = (rt - lt) * GRIPPER_STEP * speed_factor
            if gripper_delta != 0.0:
                gripper_pos = float(np.clip(gripper_pos + gripper_delta, 0.0, 0.1))
                robot.command_gripper(gripper_pos, GRIPPER_EFFORT)

            # ── 状态显示（低频，避免刷屏） ──
            # 只在有输入时打印
            has_input = has_joint_input or lt > 0.05 or rt > 0.05
            if has_input:
                joints_str = [f"{q:6.3f}" for q in target_q]
                print(
                    f"\r  joints(rad)={joints_str}"
                    f"  gripper={gripper_pos:.3f}"
                    f"  speed=x{speed_factor}",
                    end="",
                    flush=True,
                )

            pygame.time.wait(CONTROL_LOOP_WAIT_MS)

    except KeyboardInterrupt:
        print("\n\nCtrl+C，准备退出...")
        go_home_and_verify(robot)

    finally:
        pygame.quit()


def main():
    print("=" * 60)
    print("通过独立连接检测+手柄遥操控制机械臂")
    print("=" * 60)

    # 发现并激活 CAN 端口
    ports = piper_connect.find_ports()
    print(f"发现端口: {ports}")
    piper_connect.activate(ports)
    active = piper_connect.active_ports()
    print(f"已激活端口: {active}")

    if not active:
        print("错误：未发现已激活的 CAN 端口")
        return

    # 创建独立的 PiperInterface（与 connect_init.py 的连接共存）
    robot = piper_interface.PiperInterface(can_port=active[0])
    print(f"已创建独立 PiperInterface (port={active[0]})")

    print("\n── 检测机械臂状态 ──")
    is_enabled, is_at_zero = check_arm_status(robot)

    if not is_enabled:
        print("\n⚠ 机械臂未使能，请先在另一个终端运行 connect_init.py。")
        return

    if not is_at_zero:
        print("\n⚠ 机械臂不在零位。请先确认 connect_init.py 已完成 calibrate。")
        return

    print(f"\n✓ 机械臂状态正常: 使能={is_enabled}, 零位={is_at_zero}")
    print("\n── 进入手柄遥操 ──")
    gamepad_control(robot)

    print("\n── 控制结束 ──")
    print("本脚本不负责失能机械臂。请在 connect_init.py 中按 Enter 断开。")


if __name__ == "__main__":
    main()

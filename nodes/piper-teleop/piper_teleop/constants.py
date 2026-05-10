"""手柄遥操常量,沿用参考脚本 docs/references/tests/gamepad/gamepad_joint_control.py。"""

import numpy as np

# 关节限位 (rad),来自 URDF
JOINT_LIMITS = [
    (-2.6179, 2.6179),    # J1
    (0.0, 3.14),          # J2
    (-2.967, 0.0),        # J3
    (-1.745, 1.745),      # J4
    (-1.22, 1.22),        # J5
    (-2.09439, 2.09439),  # J6
]

ZERO_POSITION = [-1.5708, 0.0, 0.0, 0.0, 0.0, 0.0]
SAFE_DISABLE_POSITION = [-1.5708, 0.0, 0.0, 0.02, 0.5, 0.0]
GRIPPER_RANGE = (0.0, 0.1)

# bringup 启动与回零参数(沿用 connect_init.py)
JOINT_SAFE_SPEED = 10
MOVE_TIMEOUT = 12.0
MOVE_THRESHOLD = 0.01

# 多次采样使能状态,防止单次误判
ENABLE_PROBE_SAMPLES = 5
ENABLE_PROBE_INITIAL_DELAY = 0.3
ENABLE_PROBE_INTERVAL = 0.05

# safe_shutdown 中 move 完到失能之间的等待
SAFE_SHUTDOWN_PAUSE_SEC = 1.0

# 手柄控制参数
JOINT_ANGLE_STEP = 0.5 * np.pi / 180.0  # rad/tick,基础步长
GRIPPER_STEP = 0.001                     # m/tick,夹爪基础步长
GRIPPER_EFFORT = 0.5                     # Nm,夹爪力度
DEADZONE = 0.5                           # 摇杆死区

SPEED_FACTORS = [0.25, 0.5, 1.0, 2.0, 3.0]
ARM_MODE_SPEEDS = [5, 8, 12, 18, 25]
TARGET_MAX_LEAD = 0.1  # rad,target_q 允许超前 current_q 的最大值
DEFAULT_SPEED_INDEX = 0

# Linux 手柄按键映射
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
HAT_INDEX = 0

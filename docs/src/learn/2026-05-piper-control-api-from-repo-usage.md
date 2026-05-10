# piper-control 接口文档(基于实际调用)

> **上游库**:[https://github.com/Reimagine-Robotics/piper_control](https://github.com/Reimagine-Robotics/piper_control)
>
> 本文档仅记录以下仓库中实际调用过的接口,签名与返回值类型均从调用方代码反推,不来自上游源码阅读:
>
> - `piper_control_demo`(`scripts/`、`src/piper_control_demo/`、`src/piper_socket_bridge/`、`tests/`)
> - `lerobot` 移植(`src/lerobot/motors/piper/piper.py`)
> - dora 节点(`nodes/rerun-piper-sim/robot_state_publisher.py`)

通过 `from piper_control import ...` 使用四个子模块:`piper_connect`、`piper_interface`、`piper_init`、`piper_control`。

---

## piper_control.piper_connect

CAN 端口发现与激活的工具模块,所有仓库统一通过它完成 CAN 上线。

标准调用序列(三步固定模式):

```python
ports = piper_connect.find_ports()
piper_connect.activate(ports)
ports = piper_connect.active_ports()
```

### `piper_connect.find_ports()`

- 参数:无
- 返回值:`list[str]`,发现到的 CAN 端口名列表(例如 `["can0"]`)
- 调用来源:`piper_control_demo/config.py`、`connect_init.py`、`gamepad_joint_control.py`、`lerobot/piper.py`、`robot_state_publisher.py`

### `piper_connect.activate(ports)`

- 参数:`ports: list[str]`,`find_ports()` 返回的端口列表
- 返回值:未使用(调用方丢弃)
- 调用来源:同上,紧接 `find_ports()` 调用

### `piper_connect.active_ports()`

- 参数:无
- 返回值:`list[str]`,已激活的 CAN 端口列表;空列表表示无可用端口
- 调用来源:同上,激活后读取一次确认是否拿到可用端口

---

## piper_control.piper_interface

### `class PiperInterface`

- 构造:`PiperInterface(can_port: str)`,传入 `active_ports()` 的第一个端口
- 调用来源:所有真实机械臂入口脚本

下文 `robot` 均指 `PiperInterface` 实例。

---

#### `robot.set_installation_pos(pos)`

- 参数:`pos: piper_interface.ArmInstallationPos`,所有调用统一传 `ArmInstallationPos.UPRIGHT`
- 返回值:未使用
- 调用来源:`piper_control_demo` 所有连接真机的脚本,均在 `PiperInterface` 创建后立即调用
- ⚠ **注意**:`lerobot/piper.py` 与 dora 节点 `robot_state_publisher.py` 均**未调用**此方法,实际是否必须尚不明确,可能缺省值已是 UPRIGHT

---

#### `robot.show_status()`

- 参数:无
- 返回值:未使用(打印状态到终端)
- 调用来源:`move_debug.py`、`connect_init.py`、`robot_sender.py`、`robot_follow.py`;`lerobot/piper.py` 的 `connect()` 中也调用

---

#### `robot.is_arm_enabled()`

- 参数:无
- 返回值:`bool`
- 调用来源:`piper_control_demo/config.py` 与 `gamepad_joint_control.py` 均采用多次采样取或值(`any(samples)`)的方式判断,避免单次误判;`lerobot/piper.py` 同样做多次采样

---

#### `robot.is_gripper_enabled()`

- 参数:无
- 返回值:`bool`
- 调用来源:`config.probe_gripper_enabled_state`、`lerobot/piper.py` 的 `probe_gripper_enabled_state`,在 `reset_gripper` 后再次确认夹爪是否使能

---

#### `robot.enable_gripper()`

- 参数:无
- 返回值:未使用
- 调用来源:`ensure_arm_and_gripper_enabled`(`reset_gripper` 之后仍未使能时显式调用)

---

#### `robot.disable_arm()`

- 参数:无
- 返回值:未使用
- 调用来源:`disable_safe.py`、`connect_init.safe_shutdown`、`lerobot/piper.py` 的 `safe_shutdown`
- ⚠ **注意**:部分旧脚本(`old_move_debug.py`、`socket_old/`)使用 `piper_init.disable_arm(robot)` 替代,两者并存,语义差异尚不明确(见"待探索")

---

#### `robot.disable_gripper()`

- 参数:无
- 返回值:未使用
- 调用来源:`disable_safe.py`、`connect_init.safe_shutdown`、`control.confirm_and_shutdown`、`lerobot/piper.py` 的 `safe_shutdown`,均在失能臂之前先失能夹爪

---

#### `robot.get_joint_positions()`

- 参数:无
- 返回值:`list[float]`,6 个关节角,单位 rad
- 调用来源:
  - 控制循环采样当前位姿(`move_to_position_with_keyboard_stop`)
  - 状态流式输出(`show_status.py`、`robot_sender.py`)
  - `lerobot/piper.py` 的 `read()` 方法
  - dora 节点 `robot_state_publisher.py` 周期读取发布

---

#### `robot.command_joint_positions(positions)`

- 参数:`positions: list[float]`,6 个关节目标角,单位 rad;若来自 numpy array 需先 `.tolist()`
- 返回值:未使用
- 调用来源:
  - `gamepad_joint_control.py` 中直接下发关节目标(配合 `set_arm_mode(speed=...)` 使用,不走 `BuiltinJointPositionController`)
  - `lerobot/piper.py` 的 `write()` 方法,与 `command_gripper` 在同一控制周期内配对调用,构成一次完整的 7-DOF 控制帧(6 关节 + 夹爪)

---

#### `robot.set_arm_mode(speed=...)`

- 参数:`speed: int`,取值范围注释说明为 `[0, 100]`,已验证安全运动范围 `[5, 20]`,仓库实际使用 `5` 或 `10`
- 返回值:未使用
- 调用来源:进入 `BuiltinJointPositionController` 上下文之后立刻调用;或在直接使用 `command_joint_positions` 之前设置

---

#### `robot.get_gripper_state()`

- 参数:无
- 返回值:2 元素元组,解包为 `(gripper_pos: float, gripper_effort: float)`;所有调用均解构两元素,`gripper_effort` 通常以 `_` 丢弃
  - `gripper_pos` 单位 m,范围 `[0.0, 0.1]`
- 调用来源:状态打印、状态流采样(`show_status.py`、`robot_sender.py`)、`lerobot/piper.py` 的 `read()` 方法、dora 节点 `robot_state_publisher.py`

---

#### `robot.command_gripper(position, effort)`

- 参数:
  - `position: float`,单位 m,范围 `[0.0, 0.1]`
  - `effort: float`,范围 `[0, 2]`,常见值 `0.4`、`0.5`
  - 两个参数均支持关键字传参:`command_gripper(position=..., effort=...)`(见 `lerobot/piper.py`)
- 返回值:未使用
- 调用来源:`move_debug.py`、`gamepad_joint_control.py`、`robot_follow.py`、`lerobot/piper.py` 的 `write()` 方法

---

#### `robot.set_collision_protection(levels)`

- 参数:`levels: list[int]`,6 个等级,默认 `[5, 5, 5, 5, 5, 5]`
- 返回值:未使用
- 调用来源:`config.verify_collision_protection`(写入后读取验证)、`old_move_debug.py`(直接调用)

---

#### `robot.get_collision_protection()`

- 参数:无
- 返回值:`list[int]`,与 `set_collision_protection` 写入的形状相同
- 调用来源:`config.verify_collision_protection` 多次采样比对期望值

---

### `enum ArmInstallationPos`

- 模块路径:`piper_control.piper_interface`
- 实际使用的成员:`ArmInstallationPos.UPRIGHT`
- 用途:传入 `robot.set_installation_pos()`

### `enum ArmController`

- 模块路径:`piper_control.piper_interface`
- 实际使用的成员:`ArmController.POSITION_VELOCITY`
- 用途:作为 `piper_init.reset_arm()` 的 `arm_controller` 关键字参数

### `enum MoveMode`

- 模块路径:`piper_control.piper_interface`
- 实际使用的成员:`MoveMode.JOINT`
- 用途:作为 `piper_init.reset_arm()` 的 `move_mode` 关键字参数

---

## piper_control.piper_init

高层助手函数,调用时均以 `PiperInterface` 实例作为第一个位置参数。

### `piper_init.reset_arm(robot, arm_controller=..., move_mode=...)`

- 参数:
  - `robot: PiperInterface`
  - `arm_controller`:统一使用 `ArmController.POSITION_VELOCITY`
  - `move_mode`:统一使用 `MoveMode.JOINT`
- 返回值:未使用
- 调用来源:`config.ensure_arm_and_gripper_enabled`(检测到未使能时)、`connect_init.py`、`lerobot/piper.py` 的 `connect()`、`old_move_debug.py`、`socket_old/` 旧脚本

### `piper_init.reset_gripper(robot)`

- 参数:`robot: PiperInterface`
- 返回值:未使用
- 调用来源:`ensure_arm_and_gripper_enabled`、`connect_init.py` 无条件调用以重置夹爪

### `piper_init.disable_arm(robot)`

- 参数:`robot: PiperInterface`
- 返回值:未使用
- 调用来源:`control.confirm_and_shutdown`、`old_move_debug.py`、`socket_old/` 收尾流程
- ⚠ **注意**:与 `robot.disable_arm()` 并存,两者语义差异尚不明确(见"待探索")

---

## piper_control.piper_control

### `class BuiltinJointPositionController`

- 构造:`BuiltinJointPositionController(robot, rest_position=None)`
  - `rest_position=None`:到达目标位后不动;所有调用均使用此默认值
- 使用方式:**必须以上下文管理器形式调用**,进入后立刻 `robot.set_arm_mode(speed=...)`

```python
with piper_control.BuiltinJointPositionController(robot, rest_position=None) as controller:
    robot.set_arm_mode(speed=10)
    controller.move_to_position(target, threshold=0.01, timeout=8.0)
```

- 调用来源:`move_debug.py`、`old_move_debug.py`、`connect_init.py`、`gamepad_joint_control.py`(仅回零)、`control.confirm_and_shutdown`、`control.move_to_position_with_keyboard_stop`、`robot_follow.py`、`lerobot/piper.py` 的 `builtin_control_move`、`socket_old/` 旧脚本

#### `controller.command_joints(positions)`

- 参数:`positions: list[float]`,6 个关节角,单位 rad;若来自 numpy array 需先 `.tolist()`
- 返回值:未使用
- 调用来源:自定义控制循环中逐步逼近目标(`move_to_position_with_keyboard_stop`、`robot_follow.receive_and_follow_stream`)

#### `controller.move_to_position(target, threshold=..., timeout=...)`

- 参数:
  - `target: list[float]`,6 个目标关节角,单位 rad
  - `threshold: float`,到位判定阈值,常见值 `0.01`
  - `timeout: float`,秒,常见值 `8.0` 或 `12.0`
- 返回值:`bool`,是否在 timeout 内到位
- 调用来源:`confirm_and_shutdown` 回安全位、`connect_init.py` / `gamepad_joint_control.py` 回零、`robot_follow.py` 回零、`lerobot/piper.py` 的 `builtin_control_move`

---

## 待探索的问题

1. **`set_installation_pos` 是否必须**:`lerobot/piper.py` 与 dora 节点 `robot_state_publisher.py` 均省略了此调用,`piper_control_demo` 所有脚本均调用。需确认缺省值是否已是 UPRIGHT,省略是否安全。

2. **`robot.disable_arm()` 与 `piper_init.disable_arm(robot)` 的差异**:两者在不同脚本中并存,`lerobot/piper.py` 使用前者,`socket_old/` 使用后者。语义差异不明,建议统一。

3. **`BuiltinJointPositionController.move_to_position` 与自定义循环并存**:`piper_control_demo` 自实现了 `move_to_position_with_keyboard_stop`(支持键盘急停 + 运动守护),两者在不同场景分别使用,前者功能更丰富但不能替代后者的阻塞式简单用法。

# dora-piper 真机写入节点方案

> 上位 plan:`2026-05-system-architecture-baseline-plan.md` Phase 2 第 1 步。
> 本方案只规划实现,不修改代码。目标是新增生产链路的 Piper 真机写入节点,
> 让 `action-dispatcher → dora-piper` 能回放 `actions/wave.npz` 到真实机械臂。

---

## 1. 功能目标

新增 `dora-piper` 节点,作为真机执行端:

1. 启动后自动连接 CAN、自动 enable arm/gripper。
2. 启动 enable 成功后自动移动到 `INIT_JOINT_POSITION`
   `[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]`。
3. 周期 publish `jointstate: pa.Array<float32>[7]`,包含 6 关节角(rad) + 夹爪位置(m)。
4. publish `enabled: pa.Array<bool>`。
5. 消费 `joint_action: pa.Array<float32>[7]`,每帧都执行:
   - `robot.set_arm_mode(speed=10)`
   - `robot.command_joint_positions(joints[:6])`
   - `robot.command_gripper(position=joints[6], effort=0.5)`
6. 退出时执行安全关机:
   - 阻塞 move 到 `SAFE_DISABLE_POSITION`
   - 先 `disable_gripper()`
   - 后 `disable_arm()`
7. 第一版不加 `TEACH_MODE`。
8. 第一版不做单元测试;只提供用户手动执行的真机验证 dataflow。

第一版完成标志:

```text
action-dispatcher → dora-piper → Piper 真机
```

用户手动启动 dataflow 后,真机自动 enable、移动到初始位,随后回放 `actions/wave.npz`。

---

## 2. 当前问题或动机

当前已经完成两段仿真链路:

```text
action-dispatcher → mujoco-sim-publisher → MuJoCo viewer
```

这验证了 NPZ 轨迹文件可以被逐帧转换为 `joint_action[7]`,但无法判断真机执行效果。
要验证 `actions/wave.npz` 的真实速度、末帧、动作效果,必须实现真机写入节点:

```text
action-dispatcher → dora-piper
```

现有参考来源:

| 来源 | 用途 |
|---|---|
| `docs/references/lerobot/piper.py` | 周期 `read/write`、`command_joint_positions + command_gripper`、safe shutdown |
| `nodes/piper-teleop/piper_teleop/bringup.py` | 已真机验证的 CAN 连接、enable、move、disable 顺序 |
| `nodes/rerun-piper-sim/rerun_piper_sim/robot_state_publisher.py` | 最小读取侧 `jointstate` 发布方式 |
| `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md` | piper-control 调用约定 |
| `.venv/.../piper_control/piper_interface.py` | `set_arm_mode`、`command_joint_positions`、`command_gripper` 的实际行为 |
| `.venv/.../piper_control/piper_init.py` | `reset_arm` / `reset_gripper` 的阻塞行为与风险 |

需要注意:`mujoco-sim-publisher` 不是“真机镜像”节点,而是 `dora-piper` 的仿真替身。
真机镜像方向仍是已有 `rerun-piper-sim`:

```text
Piper 真机 → robot_state_publisher → MuJoCo/Rerun
```

---

## 3. 方案设计

### 3.1 节点边界

节点目录:

```text
nodes/dora-piper/
```

Python 包:

```text
dora_piper
```

entry point:

```text
dora-piper = "dora_piper.node:main"
```

职责:

- 持有唯一 `PiperInterface`
- 连接并 enable 真机
- 自动移动到 `INIT_JOINT_POSITION`
- 接收 `joint_action`
- 下发真机控制帧
- 周期 publish `jointstate`
- 安全退出

不包含:

- 不实现 action-dispatcher
- 不实现 arm-supervisor
- 不实现 home/zero 请求
- 不实现 teach mode / `TEACH_MODE`
- 不实现动作队列
- 不做语音/TTS
- 不运行真机命令;由用户手动执行验证 dataflow

### 3.2 常量

沿用现有已验证语义:

```python
INIT_JOINT_POSITION = [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]
SAFE_DISABLE_POSITION = [-1.5708, 0.0, 0.0, 0.02, 0.5, 0.0]
JOINT_SAFE_SPEED = 10
GRIPPER_EFFORT = 0.5
GRIPPER_RANGE = (0.0, 0.1)
MOVE_TIMEOUT = 12.0
MOVE_THRESHOLD = 0.01
```

关节限位第一版可沿用 `piper-teleop` 的 `JOINT_LIMITS`,也可依赖
`piper_control.PiperInterface.command_joint_positions` 内部裁剪。建议两层都保留:

- dora-piper 侧先裁剪,便于日志与可读性。
- piper-control 内部再裁剪,作为 SDK 防御。

### 3.3 dora 接口

输入:

| 输入 | 来源 | 类型 | 说明 |
|---|---|---|---|
| `tick` | `dora/timer/millis/20` | timer | 20ms publish jointstate |
| `joint_action` | `action-dispatcher/joint_action` | `pa.Array<float32>[7]` | 6 关节 + 夹爪 |
| `disable_request` | 手动测试节点或后续 supervisor | `pa.Array<bool>` | 第一版可选,收到 true 后退出主循环 |

输出:

| 输出 | 类型 | 说明 |
|---|---|---|
| `jointstate` | `pa.Array<float32>[7]` | 当前真机关节 + 夹爪 |
| `enabled` | `pa.Array<bool>` | enable + init move 成功后 publish true |
| `at_init_pose` | `pa.Array<bool>` | 自动移动到初始位后 publish move 结果 |

说明:

- `at_init_pose` 不是给 teach-recorder 使用,只是让测试 dataflow 可观察启动是否到位。
- 第一版不输出 `at_zero`,因为启动目标不是 zero,而是 init pose。
- 第一版不处理 `enable_request`;启动自动 enable 已经满足当前目标。

### 3.4 启动流程

启动后自动执行:

```text
Node()
→ find_ports()
→ activate(ports)
→ active_ports()
→ PiperInterface(can_port=active[0])
→ 可选 set_installation_pos(ArmInstallationPos.UPRIGHT)
→ 多次采样 is_arm_enabled()
→ 如未 enable: piper_init.reset_arm(...)
→ 多次采样 is_gripper_enabled()
→ 如未 enable: piper_init.reset_gripper(...)
→ builtin_move(INIT_JOINT_POSITION)
→ robot.set_arm_mode(speed=10)
→ publish enabled=True
→ publish at_init_pose=<move result>
→ publish jointstate
→ 进入主循环
```

`set_installation_pos` 的处理:

- piper-control 源码注释写着 “call this right after connecting”。
- 但 `lerobot/piper.py` 与现有 `robot_state_publisher.py` 未调用。
- 第一版建议加环境变量 `PIPER_SET_INSTALLATION_POS`,默认 `false`。
- 用户如需要可设置 `PIPER_SET_INSTALLATION_POS=true` 再验证。

理由:避免突然引入一个现有已验证链路没有用过的控制调用。

### 3.5 `builtin_move`

用于启动到 `INIT_JOINT_POSITION` 与退出到 `SAFE_DISABLE_POSITION`:

```python
with BuiltinJointPositionController(robot, rest_position=None) as ctrl:
    robot.set_arm_mode(speed=10)
    ok = ctrl.move_to_position(target, threshold=0.01, timeout=12.0)
```

重要约束:

- `BuiltinJointPositionController` 是阻塞调用。
- 它退出后必须再 `robot.set_arm_mode(speed=10)`,否则后续 `command_joint_positions`
  可能无效。
- 第一版接受启动/退出阻塞,因为这是人工真机验证链路,不是最终 supervisor 状态机。

### 3.6 写入流程

收到 `joint_action`:

```text
validate shape == (7,)
reject NaN/Inf
clip joints + gripper
robot.set_arm_mode(speed=10)
robot.command_joint_positions(joints[:6].tolist())
robot.command_gripper(position=gripper, effort=0.5)
```

用户明确要求:

> `joint_action` 写入每帧都 `set_arm_mode(speed=10)`。

因此第一版不做“启动后只设置一次”的优化。这样牺牲一点效率,换取从阻塞 move、
SDK 状态漂移或上游异常中恢复 command 模式的确定性。

如果 `joint_action` shape 错、NaN/Inf:

- 打印 warning
- 跳过该帧
- 不下发任何真机命令

### 3.7 读取流程

每个 `tick` publish:

```python
joints = robot.get_joint_positions()
gripper_pos, _ = robot.get_gripper_state()
jointstate = np.array(list(joints) + [gripper_pos], dtype=np.float32)
node.send_output("jointstate", pa.array(jointstate, type=pa.float32()))
```

`tick` 频率:

```yaml
tick: dora/timer/millis/20
```

这让真机状态输出与 `action-dispatcher` / `teach-recorder` 的 50Hz 约定一致。

### 3.8 退出流程

退出触发:

- `disable_request=True`
- 用户 Ctrl+C / dora stop 导致 finally 执行
- 主循环异常

退出动作:

```text
safe_shutdown:
  builtin_move(SAFE_DISABLE_POSITION)
  sleep(1)
  robot.disable_gripper()
  robot.disable_arm()
```

顺序不能颠倒:先夹爪,后机械臂。

说明:

- `piper_init.disable_arm(robot)` 不用于第一版。
- 统一使用现有 `piper-teleop` 和 `lerobot` 都采用的 `robot.disable_arm()`。

---

## 4. 真机验证 dataflow

第一版不做单元测试,只提供用户手动执行的 dataflow。

### 4.1 bringup smoke:只 enable + init + publish jointstate

新增:

```text
dataflow/tests/test_dora_piper_bringup.yml
```

结构:

```yaml
nodes:
  - id: dora_piper
    path: uv
    args: run dora-piper
    inputs:
      tick: dora/timer/millis/20
    outputs:
      - jointstate
      - enabled
      - at_init_pose
```

用户手动执行:

```bash
dora build dataflow/tests/test_dora_piper_bringup.yml --uv
dora start dataflow/tests/test_dora_piper_bringup.yml --uv
```

预期:

- 真机连接 CAN。
- 自动 enable。
- 自动移动到 `INIT_JOINT_POSITION`。
- 日志输出 `enabled=True`, `at_init_pose=True/False`。
- 用户停止 dataflow 后,走 `safe_shutdown`。

### 4.2 action-dispatcher → dora-piper 真机回放

新增:

```text
dataflow/tests/test_action_dispatcher_dora_piper.yml
```

结构:

```yaml
nodes:
  - id: action_dispatcher
    path: uv
    args: run action-dispatcher
    env:
      ACTION_ID: wave
      ACTIONS_DIR: ../../actions
    inputs:
      tick: dora/timer/millis/20
    outputs:
      - joint_action
      - speech_text
      - done

  - id: dora_piper
    path: uv
    args: run dora-piper
    inputs:
      tick: dora/timer/millis/20
      joint_action:
        source: action_dispatcher/joint_action
        queue_size: 1
    outputs:
      - jointstate
      - enabled
      - at_init_pose
```

用户手动执行:

```bash
dora build dataflow/tests/test_action_dispatcher_dora_piper.yml --uv
dora start dataflow/tests/test_action_dispatcher_dora_piper.yml --uv
```

预期:

- dora-piper 启动后真机到初始位。
- action-dispatcher 开始回放 `actions/wave.npz`。
- 真机执行 wave 轨迹。
- `wave.npz` 当前约 56 秒且末帧不回初始位,这是已知风险;用户需随时准备硬件急停。

### 4.3 不由 Agent 执行的命令

以下命令只由用户执行:

```bash
dora start dataflow/tests/test_dora_piper_bringup.yml --uv
dora start dataflow/tests/test_action_dispatcher_dora_piper.yml --uv
```

Agent 只允许执行:

```bash
dora build ...
```

---

## 5. 可能受影响的文件或模块

预计新增:

```text
nodes/dora-piper/
nodes/dora-piper/pyproject.toml
nodes/dora-piper/dora_piper/__init__.py
nodes/dora-piper/dora_piper/node.py
dataflow/tests/test_dora_piper_bringup.yml
dataflow/tests/test_action_dispatcher_dora_piper.yml
```

可能更新:

```text
docs/src/README.md
docs/src/SUMMARY.md
uv.lock
```

不应修改:

```text
actions/wave.npz
nodes/piper-teleop/
nodes/rerun-piper-sim/
nodes/action-dispatcher/
nodes/mujoco-sim-publisher/
docs/references/
```

---

## 6. 潜在风险和边界情况

### 6.1 真机自动 enable + 自动移动

风险:启动 dataflow 后机械臂会立即上电使能并移动到 `INIT_JOINT_POSITION`。

缓解:

- dataflow 名称和 README 明确这是“真机”测试。
- 用户手动执行 `dora start` 前确认工作区无障碍、硬件急停可用。
- Agent 不执行真机 start。

### 6.2 `wave.npz` 时长与末帧异常

当前 `wave.npz`:

- 约 2796 帧
- 按 20ms 回放约 56 秒
- 末帧不在 `INIT_JOINT_POSITION`

风险:真机动作可能过慢、动作后不回初始位。

缓解:

- 第一轮真机验证只观察轨迹是否能被执行。
- 不把这次验证等同于最终动作质量验收。
- 如确认录制时长异常,后续重录 wave 或修正 recorder/dispatcher 时间语义。

### 6.3 每帧 `set_arm_mode(speed=10)` 的开销

风险:每帧发送 mode control 可能增加 CAN 消息负担或影响轨迹平滑度。

当前决策:

- 按用户要求第一版每帧都调用。
- 若真机验证发现抖动或延迟,再改为“启动后一次 + 阻塞 move 后一次 + 异常恢复”。

### 6.4 阻塞 move 期间输入积压

启动到 init 或退出到 safe position 时,dora-piper 主循环尚未处理/已停止处理输入。

缓解:

- `joint_action` 输入在 dataflow 中设置 `queue_size: 1`。
- 第一版 action-dispatcher 在 dora-piper 启动期间可能已经开始输出;队列只保留最新帧。
- 这是第一版的已知限制;后续接 `arm-supervisor grant` 后再从协议上避免“未 ready 就回放”。

### 6.5 失能导致机械臂掉落风险

piper-control 源码中 `piper_init.disable_arm` 注释明确 disable 会让 arm 失去支撑。
当前 `safe_shutdown` 先移动到 `SAFE_DISABLE_POSITION`,再失能。

缓解:

- 不在任意状态直接 disable arm。
- 保持先 `disable_gripper`,后 `disable_arm`。
- 用户现场看护。

### 6.6 `set_installation_pos` 不确定

piper-control 源码建议连接后调用,但当前已验证代码未调用。

缓解:

- 第一版默认不调用。
- 提供 env 开关 `PIPER_SET_INSTALLATION_POS=true`。
- 若后续确认必须,再改默认。

---

## 7. 实施步骤

### 批次 A:节点骨架与常量

1. 新建 `nodes/dora-piper/`。
2. 添加 `pyproject.toml`,依赖:
   - `dora-rs`
   - `numpy`
   - `pyarrow`
   - `piper-control`
3. 新建 `dora_piper/node.py`。
4. 定义常量:初始位、安全位、速度、阈值、夹爪范围。

### 批次 B:真机控制函数

实现:

- `probe_enabled(check_fn)`
- `connect_and_enable()`
- `builtin_move(robot, target)`
- `get_jointstate(robot)`
- `apply_joint_action(robot, action)`
- `safe_shutdown(robot)`

不写单元测试。实现时严格对照:

- `docs/references/lerobot/piper.py`
- `nodes/piper-teleop/piper_teleop/bringup.py`
- piper-control 源码

### 批次 C:dora 主循环

1. `Node()`。
2. 连接、enable、自动 move 到 `INIT_JOINT_POSITION`。
3. publish `enabled`, `at_init_pose`, `jointstate`。
4. 事件循环:
   - `tick` → publish `jointstate`
   - `joint_action` → 每帧 set mode + 写入
   - `disable_request=True` → break
5. `finally` → `safe_shutdown`。

### 批次 D:真机验证 dataflow

新增:

- `dataflow/tests/test_dora_piper_bringup.yml`
- `dataflow/tests/test_action_dispatcher_dora_piper.yml`

Agent 只运行:

```bash
dora build dataflow/tests/test_dora_piper_bringup.yml --uv
dora build dataflow/tests/test_action_dispatcher_dora_piper.yml --uv
```

用户手动运行 `dora start`。

### 批次 E:文档同步

1. 更新 `docs/src/README.md` 节点清单与真机测试命令。
2. 如实现与方案有偏离,创建 explain 文档并更新 `SUMMARY.md`。
3. 完成后停下,等待用户真机验证反馈。

---

## 8. 明确暂不做的事

- 不加 `TEACH_MODE`。
- 不接 `arm-supervisor`。
- 不接 Web STOP。
- 不实现 `enable_request` 重入。
- 不做单元测试。
- 不由 Agent 执行真机 `dora start`。
- 不修改 action-dispatcher 的 56 秒轨迹问题。
- 不重录或修改 `actions/wave.npz`。

---

## 9. 待确认问题

当前用户已明确确认:

1. 启动后自动 enable。
2. 启动后自动移动到 `INIT_JOINT_POSITION`。
3. `joint_action` 写入每帧都 `set_arm_mode(speed=10)`。
4. 测试链路为 `action-dispatcher → dora-piper`。
5. 不加 `TEACH_MODE`。
6. 不做单元测试,改为提供用户手动执行的真机 dataflow。

实现前仍需用户现场确认:

1. 真机工作空间已清空。
2. 硬件急停可立即触达。
3. CAN 已 up,或允许 piper-control 自动 activate。
4. 能接受 `wave.npz` 当前约 56 秒、末帧不回初始位的风险。

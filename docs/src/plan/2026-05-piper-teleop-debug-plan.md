# 手柄遥操调试节点方案

> 把 `docs/references/tests/hardware/connect_init.py` 与
> `docs/references/tests/gamepad/gamepad_joint_control.py` 改编为 dora 节点,
> 用于 teach mode 录制 NPZ 轨迹的调试场景。
> 上位 plan:`2026-05-system-architecture-baseline-plan.md` Phase 2 第 3 步
> "teach_record dataflow 跑通"。

---

## 1. 功能目标

提供一套 dora dataflow,可:

1. 上电启动机械臂(CAN 连接 → 使能 → 回零位)
2. 进入手柄遥操模式,使用 Xbox 风格手柄实时控制 6 关节 + 夹爪
3. 退出时安全失能(先夹爪后臂)
4. 配合 `rerun-piper-sim` 实时可视化关节状态(可选)
5. 为后续 NPZ 轨迹录制提供数据源:gamepad 节点预留 `record_event` 输出接口,实际录制由独立的 `teach-recorder` 节点完成(留给下一份 plan)

非目标(本方案不做):

- 不实现轨迹文件(NPZ)的写入逻辑——独立 `teach-recorder` 节点订阅 `jointstate + record_event` 写 NPZ,作为后续单独的 plan 范围
- 不替代未来的生产级 `dora-piper` 节点——本方案是 debug 工具,精简版
- 不做仿真模式分支——本节点直接驱动真机,仿真路径走 `mujoco-sim-publisher`

---

## 2. 当前问题与动机

参考脚本是"双终端"设计:`connect_init.py` 在终端 A 阻塞等 Enter 维持机械臂使能,
`gamepad_joint_control.py` 在终端 B 用**独立的第二个** `PiperInterface` 共享同一
CAN 接管控制。这种设计在裸脚本时合理(无 IPC),但搬到 dora 之后:

- dora 已提供节点间事件通道,无需"双连接共享 CAN"这个变通
- 节点退出生命周期、使能/失能、回零阻塞等需要重新设计

因此本方案不是简单的"代码搬运",而是把脚本逻辑拆解到符合 dora 模式的两个节点。
piper-teleop 是独立的 debug 工具,不预设与未来 `dora-piper` 节点的代码复用关系。

---

## 3. 方案设计

### 3.1 节点拆分

新建 node 包 `nodes/piper-teleop/`(连字符,包内 Python 包 `piper_teleop`),
包含两个 entry point 脚本:

| 脚本 | 来源 | 职责 |
|---|---|---|
| `piper_teleop/bringup.py` | `connect_init.py` 改编 | 持有 `PiperInterface`,负责 CAN 连接 / 使能 / 回零 / 持续 publish jointstate / 接收 joint_action 写入 / 退出时 safe_shutdown |
| `piper_teleop/gamepad.py` | `gamepad_joint_control.py` 改编 | pygame 读手柄,等 bringup ready 后进入控制循环,publish joint_action |

入口名建议:`piper-bringup`、`piper-gamepad`。

### 3.2 节点接口

#### `piper-bringup`

- **dora 输入**
  - `joint_action: pa.Array<float32>[7]` — 6 关节(rad) + 1 夹爪(m)
  - `home_request: pa.Array<bool>` — 触发回零
  - `disable_request: pa.Array<bool>` — 触发 safe_shutdown 并退出
- **dora 输出**
  - `jointstate: pa.Array<float32>[7]` — 周期 publish 当前关节角 + 夹爪位置
  - `enabled: pa.Array<bool>` — 使能完成信号(供 gamepad 节点判断 ready)
  - `at_zero: pa.Array<bool>` — 已到零位信号
- **生命周期(改编自 connect_init.py)**
  1. 启动:`find_ports → activate → active_ports → PiperInterface(can_port)`
  2. 多次采样使能状态;未使能则 `piper_init.reset_arm(POSITION_VELOCITY, JOINT)` + `piper_init.reset_gripper`
  3. `BuiltinJointPositionController` 阻塞 move 到 `INIT_JOINT_POSITION = [-π/2, 0, 0, 0, 0, 0]`,完成后 publish `enabled=True` + `at_zero=True`
  4. 进入主循环:每 20ms publish jointstate;收到 `joint_action` 调用 `set_arm_mode(speed) + command_joint_positions + command_gripper`
  5. 收到 `home_request`:见第 5 节阻塞/状态机方案选择
  6. 收到 `disable_request` 或节点退出:`safe_shutdown` = move 到 `SAFE_DISABLE_POSITION` → `disable_gripper` → `disable_arm`
- **常量保留**:`INIT_JOINT_POSITION`、`SAFE_DISABLE_POSITION`、`JOINT_SAFE_SPEED=10`、`MOVE_TIMEOUT=12`、`MOVE_THRESHOLD=0.01` 沿用原脚本

#### `piper-gamepad`

- **dora 输入**
  - `enabled: from piper-bringup/enabled`(必须为 True 才进入控制循环)
  - `at_zero: from piper-bringup/at_zero`(必须为 True 才进入控制循环)
  - `jointstate: from piper-bringup/jointstate`(初始化 `target_q`、用于超前窗口裁剪)
  - `tick: dora/timer/millis/5`(~200Hz,对齐原脚本 `CONTROL_LOOP_WAIT_MS`)
- **dora 输出**
  - `joint_action: pa.Array<float32>[7]` — 给 bringup 写入
  - `home_request: pa.Array<bool>` — Y 键触发
  - `disable_request: pa.Array<bool>` — A 键触发(退出前回零的逻辑收进 bringup 的 disable 流程)
  - `record_event: pa.Array<str>` — 接口预留,值为 "start" / "stop";本任务**不监听 Start 键、不实际 publish**,具体行为留给 teach-recorder plan
- **行为(改编自 gamepad_joint_control.py)**
  - `pygame.init` + 热插拔事件处理(原脚本逻辑保留)
  - 摇杆/D-pad → 关节增量;扳机 → 夹爪;LB/RB → 速度档(`SPEED_FACTORS`、`ARM_MODE_SPEEDS` 沿用)
  - 死区、关节限位裁剪、`TARGET_MAX_LEAD` 超前窗口 — 全部沿用
  - 每个 tick:从最新 `jointstate` 读 `current_q` → 计算 `target_q` → publish `joint_action`(7 元素)
  - 不再自己持有 `PiperInterface`,**不直接调用 piper-control**

### 3.3 dataflow YAML

新增 `dataflow/teleop/teach_record.yml`:

```yaml
nodes:
  - id: piper_bringup
    path: uv
    args: run piper-bringup
    inputs:
      joint_action:
        source: piper_gamepad/joint_action
        queue_size: 1   # 防御回零阻塞期间的事件堆积,只保留最新一帧
      home_request: piper_gamepad/home_request
      disable_request: piper_gamepad/disable_request
    outputs:
      - jointstate
      - enabled
      - at_zero

  - id: piper_gamepad
    path: uv
    args: run piper-gamepad
    inputs:
      enabled: piper_bringup/enabled
      at_zero: piper_bringup/at_zero
      jointstate: piper_bringup/jointstate
      tick: dora/timer/millis/5
    outputs:
      - joint_action
      - home_request
      - disable_request
      - record_event
```

可选:加 `rerun-piper-sim` 节点订阅 `jointstate` 做实时可视化(沿用现有节点,不修改)。

---

## 4. 受影响的文件 / 模块

**新建**:

- `nodes/piper-teleop/pyproject.toml`
- `nodes/piper-teleop/piper_teleop/__init__.py`
- `nodes/piper-teleop/piper_teleop/bringup.py`
- `nodes/piper-teleop/piper_teleop/gamepad.py`
- `nodes/piper-teleop/tests/__init__.py`
- `nodes/piper-teleop/tests/test_pure_functions.py` — 纯函数测试(deadzone / limit / clip / lead window)
- `nodes/piper-teleop/tests/test_smoke.py` — 模块导入冒烟测试
- `dataflow/teleop/teach_record.yml`

**只读参考(本任务不改)**:

- `docs/references/tests/hardware/connect_init.py`
- `docs/references/tests/gamepad/gamepad_joint_control.py`
- `nodes/rerun-piper-sim/rerun_piper_sim/robot_state_publisher.py`(三步连接 + jointstate 周期 publish 模式参考)
- `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md`

**不动**:

- 已有 `rerun-dabai-dc1`、`rerun-piper-sim` 节点
- `pyproject.toml`(根)— `nodes/*` glob 自动发现新节点
- 基线 plan 文档(本节点是 Phase 2 第 3 步的实现细节,不改 baseline)

---

## 5. 风险与边界情况

### 5.1 真机风险(高)

- 本节点直接驱动真机,**没有仿真版本**。CLAUDE.md 规定"任何涉及真机控制的代码必须先在仿真验证再由用户上真机手动测试"
- 可在仿真侧验证的:dora 接线 / 消息流 / pygame 事件读取 / 纯函数(deadzone, limit, lead window)
- **不可在仿真侧验证的**:`PiperInterface` 调用、CAN 时序、`BuiltinJointPositionController` 阻塞行为、safe_shutdown 顺序
- 所以仿真验证的范围:把 bringup 中的 `PiperInterface` 调用替换为打印/mock,跑通 dataflow 接线;**真机测试由你手动上**

### 5.2 dora 节点的阻塞性

`BuiltinJointPositionController.move_to_position` 是阻塞调用(原脚本 timeout=12s)。
dora 节点主循环阻塞期间不能处理新事件。

**决策**:启动期回零、disable 期回安全位、运行期 Y 键回零三处都允许 bringup 主循环短暂阻塞,
不引入 supervisor 风格的状态机(那会让 debug 工具的复杂度接近完整 supervisor)。
理由:阻塞窗口短(≤12s),debug 场景下用户可接受等待。

防御措施:dataflow YAML 中 piper-bringup 的 `joint_action` 输入设
`queue_size: 1`(见 §3.3),回零阻塞期间 gamepad 持续发的 joint_action
只保留最新一帧,阻塞结束后不会回放积压指令。

### 5.3 CAN 共享与连接生命周期

原脚本"双 PiperInterface 共享同一 CAN"是 dora 化前的变通,本方案改为单连接(由 bringup 持有),
gamepad 不再持有 piper-control 依赖。这样:

- gamepad 节点的 pyproject.toml 不依赖 `piper-control`、`piper-sdk`、`python-can`
- bringup 节点是 piper-control 的唯一进程,CAN 占用归一
- 风险:之后真要做"两个进程共享 CAN"的场景需要另设计,不在本任务范围

### 5.4 pygame 在节点中的事件循环

dora 节点的主循环是 `for event in node`,而 pygame 也有自己的事件循环 `pygame.event.get()`。
gamepad 节点要把"每个 tick 事件"翻译为"读一次 pygame 事件 + 读一次手柄状态 + publish 一次 joint_action"。
原脚本是 `pygame.time.wait(5ms)` 自循环,改造后由 dora `tick: timer/millis/5` 驱动,语义等价。

### 5.5 关节限位与 NaN

原脚本沿用 URDF 关节限位 `JOINT_LIMITS`,本方案保留。joint_action 里 7 个 float 数:
- 6 关节按 `JOINT_LIMITS` 裁剪
- 夹爪按 `[0.0, 0.1]` 裁剪
- gamepad 节点 publish 前已裁剪,bringup 收到后**再裁一次防御**(防止上游异常)

### 5.6 退出顺序

- A 键触发 → gamepad publish `disable_request=True`
- bringup 收到 → 进入 disable 流程:move 到 `SAFE_DISABLE_POSITION` → `disable_gripper` → `disable_arm` → 节点退出
- gamepad 节点本身不做 disable(它没 PiperInterface);A 键 publish 完后等 bringup 退出,自己也退出
- 异常退出(`Ctrl+C`、节点崩溃):bringup 在 finally 里执行 safe_shutdown

### 5.7 `set_installation_pos` 与两个 `disable_arm`

learn 文档列出的两个待探索问题:

1. `set_installation_pos(UPRIGHT)` 是否必须 — 原 `connect_init.py` 没调用,本节点先不调用,与原脚本一致
2. `robot.disable_arm()` vs `piper_init.disable_arm(robot)` — 原 `connect_init.py` 用前者,本节点沿用前者

这两个待探索问题不在本方案中解决,留给未来 `dora-piper` 实现时决议。

---

## 6. 实施步骤

> 实施按 4 个批次推进。每个批次内部 Claude Code 自主推进,批次结束才停下汇报、等你确认。
> 代码总改动量目标 < 200 行/节点。

### 批次 A:纯仿真,无真机风险

- 建包结构:`nodes/piper-teleop/` 目录、`pyproject.toml`(`build-system` + `[project.scripts]` 两个 entry point + `[dependency-groups] dev = ["pytest"]`),`__init__.py`、`tests/`,跑 `uv sync` 验证 entry point 注册
- 抽 piper-gamepad 的纯函数:`apply_deadzone`、`clip_target_to_limits`、`apply_lead_window`、`compute_target_increment`,纯 numpy 实现 + pytest 单元测试
- piper-gamepad 主流程:pygame 初始化 + 事件循环 + 读最新 jointstate + 计算 target_q + publish joint_action;用 mock bringup 接收 joint_action 打印,验证 dora 接线

完成后停下汇报。

### 批次 B:首次接触真机,必须停

- piper-bringup 的启动 + 退出流程:CAN 连接 + 使能 + 回零 + safe_shutdown
- 在仿真侧用 mock PiperInterface 验证 dora 接线、消息流、退出顺序

完成后停下汇报。**这一步之后由你手动跑一次真机验证使能 + 回零,Claude Code 不自己跑真机命令。**

### 批次 C:真机控制,必须停

- piper-bringup 的 `joint_action` 写入:`set_arm_mode(speed) + command_joint_positions + command_gripper`
- 仍在 mock PiperInterface 下验证消息流

完成后停下汇报。**这一步之后由你手动上真机做小幅运动测试,Claude Code 不自己跑真机命令。**

### 批次 D:联调准备

- 写 dataflow YAML:`dataflow/teleop/teach_record.yml`
- 联调准备文档(若需要)

完成后停下汇报。**真机联调由你执行 `dora start dataflow/teleop/teach_record.yml`。**

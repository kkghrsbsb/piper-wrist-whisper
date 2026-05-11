# Agent 串行 Dora 真机控制节点方案

> 本方案只规划 `nodes-for-agent/` 与 `dataflow-for-agent/` 下的实验节点,
> 不修改现有 `nodes/`、`dataflow/` 生产/验证链路。该节点集合未来会迁移到另一个项目中,
> 当前仓库只作为实验孵化位置。

---

## 1. 功能目标

建立一套专供 agent 调度的 dora 节点与 dataflow:

```text
agent
  -> dora start dataflow-for-agent/<step>.yml
  -> 等待命令自然完成
  -> dora start 下一个 dataflow
```

核心目标:

1. agent 可以串行执行多个 `dora start ...yml` 命令。
2. prepare / replay 达成目标后自然退出,不需要 agent 手动 Ctrl+C。
3. prepare / replay 退出后不执行失能流程,最终由 disable 阶段显式失能。
4. 复用当前动作文件目录 `actions/`。
5. 回放命令通过环境变量指定 `ACTION_ID`。
6. 节点名称、包名、dataflow 名称与现有 `nodes/` 明确区分。

需要支持 3 个阶段:

| 阶段 | 目标 | 终止语义 |
|---|---|---|
| 1. 准备初始位 | 检测机械臂失能/使能状态,必要时 enable,移动到 `INIT_JOINT_POSITION` | 打印 ready 后自然退出,不失能 |
| 2. 回放动作 | 确保在 init,加载 `actions/{ACTION_ID}.npz`,执行轨迹并回到 init | 打印 done 后自然退出,不失能 |
| 3. 失能收尾 | 检测机械臂是否使能,若使能则执行规定失能流程 | 完成后程序自然退出 |

---

## 2. 当前问题或动机

现有节点主要面向人工验证或主项目链路:

- `nodes/dora-piper`: 启动后自动 enable + 到 init,退出时一定执行 `safe_shutdown`。
- `nodes/action-dispatcher`: 只负责读取 NPZ 并输出 `joint_action`。
- `dataflow/tests/test_action_dispatcher_dora_piper.yml`: 用于人工真机测试,不是 agent 串行流程。

agent 串行控制的需求不同:

1. **准备阶段完成后不能自动失能。**
   agent 需要先让机械臂保持在 `INIT_JOINT_POSITION`,然后启动动作回放。

2. **回放阶段完成后也不能自动失能。**
   动作结束后机械臂应停在 `INIT_JOINT_POSITION`,等待下一步或最终失能流程。

3. **失能必须由独立阶段显式触发。**
   这样 agent 的流程更可控,不会因为中间 dataflow 自然退出而自动把机械臂带走。

4. **节点集合将迁移到其它项目。**
   因此需要独立目录、独立包名、独立 dataflow,减少与当前项目主线耦合。

---

## 3. 方案设计

### 3.1 目录和命名

建议目录:

```text
nodes-for-agent/
  agent-piper-session/
  agent-action-replay/

dataflow-for-agent/
  01_prepare_init.yml
  02_replay_action.yml
  03_safe_disable.yml
```

Python 包名建议:

```text
agent_piper_session
agent_action_replay
```

entry points:

```text
agent-piper-prepare = "agent_piper_session.prepare:main"
agent-piper-disable = "agent_piper_session.disable:main"
agent-action-replay = "agent_action_replay.replay:main"
```

命名原则:

- 不使用 `dora-piper`、`action-dispatcher` 原名。
- 不从 `nodes/` import 业务代码,避免迁移时断依赖。
- 可以复制必要逻辑并在注释中注明来源。
- 常量命名保持一致,尤其 `INIT_JOINT_POSITION` / `SAFE_DISABLE_POSITION`。

### 3.2 节点一: `agent-piper-prepare`

职责:

```text
连接 CAN
检测 arm/gripper enable 状态
必要时 reset_arm / reset_gripper
移动到 INIT_JOINT_POSITION
publish ready/at_init_pose/jointstate
打印 ready 后自然退出
退出后不执行 safe_shutdown
```

建议输入:无。

建议输出:

| 输出 | 类型 | 说明 |
|---|---|---|
| `enabled` | bool | arm/gripper enable 成功 |
| `at_init_pose` | bool | move init 成功 |
| `jointstate` | float32[7] | 当前关节 + 夹爪 |
| `ready` | string/bool | 可选,给 agent 日志识别 |

退出语义:

- 打印 `AGENT_READY at_init_pose=True` 后自然退出。
- 不调用 `safe_shutdown()`。
- 不调用 `disable_arm()` / `disable_gripper()`。

关键约束:

- 启动目标必须是 `INIT_JOINT_POSITION`,不是 `ZERO_POSITION`。
- 只在启动和阻塞 move 前后设置必要 arm mode。
- 不做动作回放。

### 3.3 节点二: `agent-action-replay`

职责:

```text
连接 CAN
检测 arm/gripper enable 状态
若未 enable,执行 enable
确保当前位置接近 INIT_JOINT_POSITION,否则阻塞 move 到 init
加载 actions/{ACTION_ID}.npz
按 timestamps 优先回放 joint_action 到真机
轨迹结束后确认/移动到 INIT_JOINT_POSITION
打印 done 后自然退出
退出后不执行 safe_shutdown
```

环境变量:

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `ACTION_ID` | 是 | 无 | 选择 `actions/{ACTION_ID}.npz` |
| `ACTIONS_DIR` | 否 | `../actions` 或 `../../actions` | 由 yml CWD 决定 |
| `REPLAY_TICK_MS` | 否 | `20` | 真机回放频率,默认 50Hz |
| `DONE_HOLD_SECONDS` | 否 | `1.0` | 结束后保持 init 的时间 |

建议输出:

| 输出 | 类型 | 说明 |
|---|---|---|
| `jointstate` | float32[7] | 当前关节 + 夹爪 |
| `started` | string/bool | 开始回放 |
| `done` | string | 完成的 action id |
| `at_init_pose` | bool | 回放结束后是否在 init |

回放实现选择:

#### 方案 A: 单节点内置回放和真机写入

`agent-action-replay` 直接复制/借鉴:

- `nodes/action-dispatcher/action_dispatcher/dispatcher.py` 的 NPZ 读取和 timestamp 调度。
- `nodes/dora-piper/dora_piper/node.py` 的 CAN 连接、enable、builtin move、真机写入。

优点:

- dataflow 简单,agent 只启动一个节点。
- 没有 dora 队列丢帧问题。
- 更适合迁移到独立项目。

缺点:

- 代码重复较多。
- 需要自己维护 replay + piper 两部分逻辑。

#### 方案 B: 两节点组合

```text
agent-action-dispatcher -> agent-piper-writer
```

优点:

- 保留 dora 节点边界。
- 更接近当前架构。

缺点:

- agent 迁移时要带两个包。
- dora 队列、tick、退出顺序更复杂。
- 之前真机回放已暴露过 `queue_size` / 写入频率的调参问题。

建议采用 **方案 A**。

原因:本节点集合面向 agent 串行命令执行,不是长期运行的数据流系统。单节点能减少
dataflow 内部竞争,也更容易保证自然退出后“不失能”。

### 3.4 节点三: `agent-piper-disable`

职责:

```text
连接 CAN
检测 arm/gripper enable 状态
如果 arm/gripper 都未 enable,打印状态并退出
如果已 enable,执行规定失能流程
程序自然退出
```

规定失能流程:

```text
move SAFE_DISABLE_POSITION
sleep 1s
disable_gripper()
disable_arm()
exit 0
```

建议输出:

| 输出 | 类型 | 说明 |
|---|---|---|
| `disabled` | bool | 已执行/确认失能 |

退出语义:

- 正常完成后退出。
- 如果 Ctrl+C 发生在失能过程中,应尽量继续当前安全流程还是立即退出,需要谨慎设计。
- 建议第一版不捕获 Ctrl+C,让用户/agent 避免中断第 3 阶段。

---

## 4. dataflow 设计

### 4.1 `01_prepare_init.yml`

```yaml
nodes:
  - id: agent_piper_prepare
    path: uv
    args: run agent-piper-prepare
    inputs:
      tick: dora/timer/millis/100
    outputs:
      - enabled
      - at_init_pose
      - jointstate
      - ready
```

agent 行为:

```bash
dora start dataflow-for-agent/01_prepare_init.yml --uv
# 看到 AGENT_READY 后 dataflow 自然结束
```

预期结果:

- 机械臂使能。
- 机械臂停在 `INIT_JOINT_POSITION`。
- dataflow 结束后不失能。

### 4.2 `02_replay_action.yml`

```yaml
nodes:
  - id: agent_action_replay
    path: uv
    args: run agent-action-replay
    env:
      ACTION_ID: wave
      ACTIONS_DIR: ../actions
    outputs:
      - started
      - done
      - at_init_pose
      - jointstate
```

启动:

```bash
dora start dataflow-for-agent/02_replay_action.yml --uv
# 看到 AGENT_ACTION_DONE 后 dataflow 自然结束
```

预期结果:

- 回放 `actions/wave.npz`。
- 轨迹结束后停在 `INIT_JOINT_POSITION`。
- dataflow 结束后不失能。

### 4.3 `03_safe_disable.yml`

```yaml
nodes:
  - id: agent_piper_disable
    path: uv
    args: run agent-piper-disable
    outputs:
      - disabled
```

启动:

```bash
dora start dataflow-for-agent/03_safe_disable.yml --uv
```

预期结果:

- 若机械臂已使能:走 `SAFE_DISABLE_POSITION -> disable_gripper -> disable_arm`。
- 若机械臂未使能:打印状态并退出。
- 程序自然结束,agent 不需要 Ctrl+C。

---

## 5. 可能受影响的文件或模块

计划新增:

```text
nodes-for-agent/agent-piper-session/
nodes-for-agent/agent-action-replay/
dataflow-for-agent/01_prepare_init.yml
dataflow-for-agent/02_replay_action.yml
dataflow-for-agent/03_safe_disable.yml
```

可能需要同步:

```text
pyproject.toml
uv.lock
docs/src/README.md
docs/src/SUMMARY.md
```

仅作为参考,不直接依赖:

```text
nodes/dora-piper/dora_piper/node.py
nodes/action-dispatcher/action_dispatcher/dispatcher.py
nodes/piper-teleop/piper_teleop/bringup.py
docs/references/lerobot/piper.py
docs/references/piper_control/
```

---

## 6. 潜在风险和边界情况

### 6.1 自然退出后不失能的风险

准备和回放阶段故意保持机械臂使能,这是 agent 串行流程所需,但也是安全风险。

缓解:

- dataflow 名称明确区分 `prepare` / `replay` / `disable`。
- 日志明确提示 `"exit without disable"`。
- README 或 plan 中写清楚:最终必须执行 `03_safe_disable.yml`。

### 6.2 agent 判断完成状态

`dora start` 是附着模式,agent 需要从 stdout 判断:

- `ready`
- `done action_id=...`
- `disabled`

第一版应输出稳定、可 grep 的日志行,例如:

```text
AGENT_READY at_init_pose=True
AGENT_ACTION_DONE action_id=wave at_init_pose=True
AGENT_DISABLED ok=True
```

### 6.3 轨迹结束不在 init

虽然新 recorder 已保证末尾 init 保持段,但旧动作文件可能不满足。

建议:

- replay 节点加载后检查首尾帧。
- 若末帧不接近 init,打印 warning。
- 回放结束后强制 `builtin_move(INIT_JOINT_POSITION)` 或至少确认当前位置。

为了 agent 稳定性,建议第一版 **回放结束后强制 move init**。

### 6.4 CAN 发送频率

真机不适合过高频 CAN 命令。此前 200Hz 回放曾出现:

```text
JointCtrl_J34 send failed: SendCanMessage(SEND_MESSAGE_FAILED (100017))
```

建议:

- 默认 50Hz (`REPLAY_TICK_MS=20`)。
- 不每帧 `set_arm_mode`。
- gripper 位置变化低于阈值时不重复发送 gripper 命令。
- 记录发送失败次数,但第一版不做复杂自动恢复。

### 6.5 与当前项目主线耦合

该节点集合未来迁移到别的项目,因此:

- 不 import `nodes/` 下的 Python 包。
- 不依赖当前 dataflow 路径。
- 对 `actions/` 路径通过 `ACTIONS_DIR` 配置。

---

## 7. 实施步骤

### 批次 A: 目录与最小公共逻辑

1. 建立 `nodes-for-agent/agent-piper-session/`。
2. 建立 `nodes-for-agent/agent-action-replay/`。
3. 为两个包写独立 `pyproject.toml`。
4. 抽出各自包内的常量:
   - `INIT_JOINT_POSITION`
   - `SAFE_DISABLE_POSITION`
   - `JOINT_SAFE_SPEED`
   - `GRIPPER_EFFORT`
   - `JOINT_LIMITS`
5. 复制/改写 CAN connect、enable probe、builtin move、jointstate 读取函数。

### 批次 B: `agent-piper-prepare`

1. 实现连接/enable。
2. 启动后移动到 `INIT_JOINT_POSITION`。
3. 输出稳定日志 `AGENT_READY at_init_pose=True`。
4. 完成后自然退出,finally 不执行失能。
5. 新建 `dataflow-for-agent/01_prepare_init.yml`。
6. 只做 build 验证;真机由用户手动运行。

### 批次 C: `agent-action-replay`

1. 复制/改写 NPZ loader:
   - 支持 schema v2 `timestamps`
   - 兼容 legacy `dt`
2. 实现单节点内 timestamp playback。
3. 写入真机时:
   - 启动后设置 arm mode
   - 每帧只发必要控制
   - gripper 有变化再发
4. 回放结束后强制 move `INIT_JOINT_POSITION`。
5. 输出稳定日志 `AGENT_ACTION_DONE action_id=... at_init_pose=True`。
6. 新建 `dataflow-for-agent/02_replay_action.yml`。

### 批次 D: `agent-piper-disable`

1. 实现连接和 enable 状态检测。
2. 若已使能,执行 `SAFE_DISABLE_POSITION -> disable_gripper -> disable_arm`。
3. 若未使能,打印并退出。
4. 输出稳定日志 `AGENT_DISABLED ok=True`。
5. 新建 `dataflow-for-agent/03_safe_disable.yml`。

### 批次 E: 验证与迁移准备

1. `dora build dataflow-for-agent/*.yml --uv`。
2. 用户手动真机验证:
   ```bash
   dora start dataflow-for-agent/01_prepare_init.yml --uv
   dora start dataflow-for-agent/02_replay_action.yml --uv
   dora start dataflow-for-agent/03_safe_disable.yml --uv
   ```
3. 记录迁移所需文件清单。
4. 更新 `docs/src/README.md` 的实验链路说明。

---

## 8. 建议的最小实现顺序

1. 先实现 `agent-piper-prepare`。
   - 这是后续所有 agent 串行控制的安全入口。

2. 再实现 `agent-piper-disable`。
   - 在做动作回放前先确保有可靠收尾。

3. 最后实现 `agent-action-replay`。
   - 回放最复杂,涉及轨迹、时基、CAN 频率和末尾 init 保证。

这个顺序比先做回放更稳:先有“进入 init”和“退出失能”两个边界,再把动作回放放进中间。

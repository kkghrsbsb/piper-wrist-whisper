# bbt-agent-dora-piper 代码交接说明

> 面向新仓库 `bbt-agent-dora-piper` 的接手 agent。迁移目标是把当前仓库的
> `nodes-for-agent/` 迁移为新仓库 `nodes/`,把 `dataflow-for-agent/` 迁移为
> 新仓库 `dataflow/`。
>
> 对应方案: `docs/src/plan/2026-05-agent-sequential-dora-control-plan.md`

---

## 改动了什么

当前仓库新增了一组独立的 agent 真机控制节点:

```text
nodes-for-agent/
  agent-piper-session/
    agent_piper_session/
      common.py
      prepare.py
      disable.py
    pyproject.toml
    tests/test_common.py

  agent-action-replay/
    agent_action_replay/
      replay.py
    pyproject.toml
    tests/test_replay.py

dataflow-for-agent/
  01_prepare_init.yml
  02_replay_action.yml
  03_safe_disable.yml
```

迁移到 `bbt-agent-dora-piper` 后建议变为:

```text
nodes/
  agent-piper-session/
  agent-action-replay/

dataflow/
  01_prepare_init.yml
  02_replay_action.yml
  03_safe_disable.yml
```

不要迁移运行产物:

```text
dataflow-for-agent/out/
**/.pytest_cache/
**/__pycache__/
**/*.egg-info/
```

---

## 为什么改动

这组节点不是 `piper-wrist-whisper` 主项目目标的一部分,而是给 agent 直接操作
Piper 真机的最小实验链路:

```text
prepare init
-> replay action
-> safe disable
```

设计目标是让 agent 可以串行执行 `dora start ...yml`,每一步自然结束,不需要手动
Ctrl+C。前两步完成后机械臂保持 enable,第三步才显式执行失能流程。

---

## 影响了哪些部分

### `agent-piper-session`

入口:

```text
agent-piper-prepare = agent_piper_session.prepare:main
agent-piper-disable = agent_piper_session.disable:main
```

`common.py` 包含真机通用逻辑:

- CAN 发现与激活:`piper_connect.find_ports()` / `activate()` / `active_ports()`
- `PiperInterface` 创建
- arm/gripper enable 状态多次采样
- 必要时 `piper_init.reset_arm()` / `reset_gripper()`
- `BuiltinJointPositionController` 阻塞式移动
- `jointstate[7]` 读取
- `SAFE_DISABLE_POSITION` 失能流程
- 关节裁剪和 gripper 变化阈值写入

`prepare.py` 行为:

```text
connect
ensure enabled
move INIT_JOINT_POSITION
publish enabled / at_init_pose / jointstate / ready
print AGENT_READY at_init_pose=True
exit without disable
```

`disable.py` 行为:

```text
connect
probe enabled state
if enabled:
  move SAFE_DISABLE_POSITION
  sleep 1s
  disable_gripper
  disable_arm
print AGENT_DISABLED ok=True
exit
```

### `agent-action-replay`

入口:

```text
agent-action-replay = agent_action_replay.replay:main
```

`replay.py` 是单节点实现,刻意把“动作读取/调度”和“真机写入”放在一个进程里。
它借鉴了当前仓库的:

- `nodes/action-dispatcher/action_dispatcher/dispatcher.py`
- `nodes/dora-piper/dora_piper/node.py`

但没有 import 这些包,方便迁移。

核心行为:

```text
read ACTION_ID / ACTIONS_DIR
load actions/{ACTION_ID}.npz
connect Piper
ensure enabled
if current pose not init: move INIT_JOINT_POSITION
replay trajectory by timestamps if present, otherwise by dt fallback
move INIT_JOINT_POSITION after replay
publish done / at_init_pose / jointstate
print AGENT_ACTION_DONE action_id=<id> at_init_pose=True
exit without disable
```

### `dataflow-for-agent`

`01_prepare_init.yml`:

```text
agent_piper_prepare
```

`02_replay_action.yml`:

```text
agent_action_replay
ACTION_ID: wave
ACTIONS_DIR: ../actions
REPLAY_TICK_MS: "20"
DONE_HOLD_SECONDS: "1.0"
```

`03_safe_disable.yml`:

```text
agent_piper_disable
```

注意:`dora start` 的节点由 daemon 启动,不会继承
`ACTION_ID=wave dora start ...` 这种当前 shell 前缀。需要把 `ACTION_ID` 写在 YAML
的 node `env` 中,或者由 agent 生成 action-specific yml。

---

## 潜在风险或兼容性注意事项

### 真机安全

`01_prepare_init.yml` 和 `02_replay_action.yml` 完成后都不会失能。agent 必须在最终执行:

```bash
dora start dataflow/03_safe_disable.yml --uv
```

否则机械臂会保持 enable。

### 位置语义

本链路只使用 `INIT_JOINT_POSITION` 作为动作起止位:

```text
[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]
```

不要把它和旧项目里的 `ZERO_POSITION` 混用。

### ACTION_ID 传参

当前 `02_replay_action.yml` 固定:

```yaml
ACTION_ID: wave
```

如果新仓库需要动态动作,建议由 agent 写入临时 yml,或维护多个 action-specific yml:

```text
02_replay_wave.yml
02_replay_nod.yml
```

不要假设 shell 前缀会传到 dora node。

### 轨迹文件格式

支持 NPZ:

```text
joints: float32 [N, 7]
dt: float32 scalar
speech: str array
timestamps: float32 [N] optional
schema_version: int optional
```

若存在 `timestamps`,优先按时间戳调度;旧文件没有 `timestamps` 时按 `dt` fallback。

### CAN 发送频率

默认 `REPLAY_TICK_MS=20`,即约 50Hz。不要随意改成 5ms/200Hz。此前高频真机写入曾出现:

```text
JointCtrl_J34 send failed: SendCanMessage(SEND_MESSAGE_FAILED (100017))
```

`agent-action-replay` 已避免每帧 `set_arm_mode`,并且 gripper 小变化不重复发。

### 迁移依赖

两个节点包依赖:

```text
dora-rs
numpy
pyarrow
piper-control
```

新仓库需要手动配置可访问 Piper CAN 的运行环境。`piper-control` 的具体 API 参考本仓库:

```text
docs/src/learn/2026-05-piper-control-api-from-repo-usage.md
docs/references/piper_control/
```

---

## 与原方案的差异

相对 `docs/src/plan/2026-05-agent-sequential-dora-control-plan.md` 的初稿,实现阶段有两点调整:

1. `01_prepare_init.yml` 和 `02_replay_action.yml` 最终改为“完成后自然退出”,不再要求 agent
   看到状态后 Ctrl+C。
2. `02_replay_action.yml` 的 `ACTION_ID` 固定写在 YAML `env` 中,因为 `dora start`
   不会把当前 shell 的 `ACTION_ID=...` 前缀继承到 daemon-spawn 的节点进程。

其余核心目标保持一致:前两步不失能,第三步显式失能。

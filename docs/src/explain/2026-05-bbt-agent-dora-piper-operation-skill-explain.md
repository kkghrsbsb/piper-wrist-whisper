# bbt-agent-dora-piper 初版操作 Skill

> 面向新仓库 `bbt-agent-dora-piper` 中的 agent。本文是操作机械臂的初版 skill 性质文档,
> 重点是“如何安全地串行执行 dataflow”,不是代码原理说明。

---

## 适用范围

当你需要让 Piper 真机执行已录制动作时,使用这组三步流程:

```text
1. prepare init
2. replay action
3. safe disable
```

优先操作新仓库中的 runner CLI:

```bash
uv run piper-agent prepare
uv run piper-agent replay <ACTION_ID>
uv run piper-agent disable
```

底层仍使用:

```text
dataflow/
  01_prepare_init.yml
  02_replay_action.yml
  03_safe_disable.yml

nodes/
  agent-runner/
  agent-piper-session/
  agent-action-replay/
```

---

## 操作前检查

开始前确认:

1. Piper 真机已上电。
2. CAN 设备可用。
3. dora daemon/coordinator 已在正确 Python/uv 环境中运行。
4. `actions/{ACTION_ID}.npz` 存在。
5. `actions/{ACTION_ID}.npz` 是预期动作,例如 `actions/wave.npz`。
6. 操作区域安全,机械臂运动范围内无人手和障碍物。

如果不确定动作内容,不要上真机。

---

## 标准流程

### 第一步:准备初始位

运行:

```bash
uv run piper-agent prepare
```

期望看到:

```text
AGENT_READY at_init_pose=True
```

含义:

- 已连接 Piper。
- arm/gripper 已 enable。
- 机械臂已经移动到 `INIT_JOINT_POSITION`。
- dataflow 自然结束。
- 机械臂保持 enable,不会自动失能。

如果看到 `at_init_pose=False` 或节点报错,停止流程,不要继续回放动作。

### 第二步:回放动作

运行:

```bash
uv run piper-agent replay wave
```

期望看到:

```text
AGENT_ACTION_STARTED action_id=wave
AGENT_ACTION_DONE action_id=wave at_init_pose=True
```

含义:

- 已加载 `actions/wave.npz`。
- 已按 `timestamps` 或 legacy `dt` 回放。
- 回放完成后已强制移动回 `INIT_JOINT_POSITION`。
- dataflow 自然结束。
- 机械臂保持 enable,不会自动失能。

runner 会生成临时 replay yml,把 `ACTION_ID=wave` 写进 node `env`,再调用
`dora start`。agent 不需要手动修改 `dataflow/02_replay_action.yml`。

如果回放中出现 CAN send failed 但动作完成,先记录日志。若连续大量出现或机械臂运动异常,
立即进入安全处置。

### 第三步:显式失能

运行:

```bash
uv run piper-agent disable
```

期望看到:

```text
AGENT_DISABLED ok=True
```

含义:

- 如果机械臂处于 enable,节点执行:
  ```text
  SAFE_DISABLE_POSITION
  -> sleep 1s
  -> disable_gripper
  -> disable_arm
  ```
- 如果机械臂已经 disabled,节点会打印状态并自然退出。

这一步是标准流程的收尾,不要省略。

---

## 动作选择规则

优先使用 runner:

```bash
uv run piper-agent replay wave
uv run piper-agent replay nod
```

runner 负责:

```text
校验 ACTION_ID
确认 actions/{ACTION_ID}.npz 存在
生成 .agent-runs/<timestamp>_replay_<ACTION_ID>.yml
调用 dora start <generated-yml> --uv
```

不要用:

```bash
ACTION_ID=wave dora start dataflow/02_replay_action.yml --uv
```

原因:`dora start` 的节点由 daemon 启动,当前 shell 前缀不会自动传给节点。

如果 runner 不可用, fallback 是修改 YAML:

```yaml
env:
  ACTION_ID: wave
```

或手动生成临时 yml:

```text
dataflow/tmp_replay_<action_id>.yml
```

---

## 判断成功的稳定日志

agent 应优先匹配这些日志:

```text
AGENT_READY at_init_pose=True
AGENT_ACTION_STARTED action_id=<id>
AGENT_ACTION_DONE action_id=<id> at_init_pose=True
AGENT_DISABLED ok=True
```

不要依赖 dora 的内部 dataflow id,例如:

```text
019e17cf-...
```

这些 id 每次运行都会变。

---

## 出错处理

### `ACTION_ID must contain a non-empty action id`

检查 `dataflow/02_replay_action.yml` 是否包含:

```yaml
env:
  ACTION_ID: wave
```

如果使用 runner,检查命令是否带了动作名:

```bash
uv run piper-agent replay wave
```

### `action file not found`

检查:

```text
actions/{ACTION_ID}.npz
```

以及 YAML 中:

```yaml
ACTIONS_DIR: ../actions
```

路径是相对 dataflow 文件所在目录的。

如果使用 runner,它应生成绝对 `ACTIONS_DIR`;检查 `.agent-runs/` 中生成的 yml。

### `No active CAN ports found`

检查:

- 机械臂是否上电
- CAN 线是否连接
- CAN 设备是否已激活
- 当前用户是否有权限访问 CAN

### `SendCanMessage(SEND_MESSAGE_FAILED (100017))`

通常是 CAN 写入过于频繁或总线压力高。默认不要把:

```yaml
REPLAY_TICK_MS: "20"
```

改得更小。若错误大量出现,停止回放并执行:

```bash
dora start dataflow/03_safe_disable.yml --uv
```

如果 runner 可用,优先执行:

```bash
uv run piper-agent disable
```

### prepare 或 replay 失败后是否执行 disable

若机械臂可能已 enable,优先执行:

```bash
dora start dataflow/03_safe_disable.yml --uv
```

除非现场安全人员判断不应继续移动到 `SAFE_DISABLE_POSITION`。

---

## 不要做的事

- 不要把 `INIT_JOINT_POSITION` 当成 `ZERO_POSITION`。
- 不要让 replay 阶段自动 disable。
- 不要跳过最终 `03_safe_disable.yml`。
- 不要在不确认动作内容时回放未知 NPZ。
- 不要把 `REPLAY_TICK_MS` 调到 5ms/200Hz。
- 不要迁移 `out/`、`.pytest_cache/`、`__pycache__/`、`*.egg-info/` 到新仓库。
- 不要手改长期保存的 replay YAML 来完成一次性动作选择;优先使用 runner 生成临时 yml。

---

## 最小测试命令

在新仓库完成环境配置后,先跑 build:

```bash
dora build dataflow/01_prepare_init.yml --uv
dora build dataflow/02_replay_action.yml --uv
dora build dataflow/03_safe_disable.yml --uv
```

再由现场人员手动执行标准流程:

```bash
uv run piper-agent prepare
uv run piper-agent replay wave
uv run piper-agent disable
```

每一步都应自然结束。前两步结束后不会失能,第三步才失能。

如果 runner 尚未实现,临时 fallback:

```bash
dora start dataflow/01_prepare_init.yml --uv
# 确认 dataflow/02_replay_action.yml 的 ACTION_ID 后再运行
dora start dataflow/02_replay_action.yml --uv
dora start dataflow/03_safe_disable.yml --uv
```

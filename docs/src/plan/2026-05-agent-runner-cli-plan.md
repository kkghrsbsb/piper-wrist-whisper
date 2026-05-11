# bbt-agent-dora-piper Runner CLI 方案

> 本方案补充 `docs/src/plan/2026-05-agent-sequential-dora-control-plan.md`。
> 目标是在新仓库 `bbt-agent-dora-piper` 中增加一个薄 CLI,让 agent 通过稳定命令操作三段
> dataflow,避免手动修改 YAML 或依赖 `dora start` 继承 shell env。

---

## 1. 功能目标

为 `bbt-agent-dora-piper` 新增一个 runner CLI:

```bash
uv run piper-agent prepare
uv run piper-agent replay wave
uv run piper-agent disable
```

CLI 负责:

1. 提供稳定的 agent 操作入口。
2. 对 `prepare` / `disable` 直接调用固定 dataflow。
3. 对 `replay <ACTION_ID>` 渲染临时 replay dataflow,把 `ACTION_ID` 写入 YAML node env。
4. 调用 `dora start <generated-yml> --uv` 并把 stdout/stderr 透传给 agent。
5. 保留临时 yml 和运行日志路径,便于排查。

目标效果:

```text
agent 不再编辑 dataflow/02_replay_action.yml
agent 不再依赖 ACTION_ID=wave dora start ...
agent 只调用 piper-agent replay wave
```

---

## 2. 当前问题或动机

`dora start` 的节点由 dora daemon 启动,当前 shell 前缀不会自动传入 node:

```bash
ACTION_ID=wave dora start dataflow/02_replay_action.yml --uv
```

因此 `ACTION_ID` 必须写在 YAML 的 node `env` 中。

目前可行但不理想的方案:

| 方案 | 问题 |
|---|---|
| agent 手动改 YAML | 容易留下脏状态,并发/重试时不清晰 |
| 每个动作一个固定 yml | 动作数量增长后文件膨胀 |
| 节点读取 `ACTION_ID.txt` | 隐式状态强,容易串动作 |
| 默认 `ACTION_ID=wave` | 适合 smoke,不适合动态动作 |

runner CLI 是更好的边界:把“动态参数”放在 dora 外层处理,把 dora YAML 继续作为
最终执行描述。

---

## 3. 方案设计

### 3.1 新增包

建议在新仓库中新增:

```text
nodes/
  agent-runner/
    piper_agent_runner/
      __init__.py
      cli.py
      templates/
        02_replay_action.template.yml
    pyproject.toml
    tests/
      test_cli.py
```

入口:

```toml
[project.scripts]
piper-agent = "piper_agent_runner.cli:main"
```

包名和命令名都不叫 `dora-piper`,避免和真机写入节点混淆。

### 3.2 CLI 命令

#### `prepare`

```bash
uv run piper-agent prepare
```

内部执行:

```bash
dora start dataflow/01_prepare_init.yml --uv
```

成功判断:

- 子进程退出码为 0。
- stdout 中应出现 `AGENT_READY at_init_pose=True`。

第一版可以只透传输出和返回 dora exit code;后续再加日志解析。

#### `replay`

```bash
uv run piper-agent replay wave
```

内部流程:

```text
validate action_id
validate actions/wave.npz exists
render template -> .agent-runs/02_replay_action_wave.yml
dora start .agent-runs/02_replay_action_wave.yml --uv
```

生成的临时 YAML 中包含:

```yaml
env:
  ACTION_ID: wave
  ACTIONS_DIR: ../actions
  REPLAY_TICK_MS: "20"
  DONE_HOLD_SECONDS: "1.0"
```

注意 `.agent-runs/` 位于仓库根时,`ACTIONS_DIR` 应该是相对临时 YAML 所在目录的路径。
推荐第一版让 runner 生成绝对路径:

```yaml
ACTIONS_DIR: /abs/path/to/actions
```

这样临时 YAML 放在哪里都不会影响动作文件定位。

成功判断:

- 子进程退出码为 0。
- stdout 中应出现 `AGENT_ACTION_DONE action_id=wave at_init_pose=True`。

#### `disable`

```bash
uv run piper-agent disable
```

内部执行:

```bash
dora start dataflow/03_safe_disable.yml --uv
```

成功判断:

- 子进程退出码为 0。
- stdout 中应出现 `AGENT_DISABLED ok=True`。

### 3.3 参数

建议第一版支持:

```bash
uv run piper-agent prepare
uv run piper-agent replay <ACTION_ID>
uv run piper-agent disable
```

可选参数:

| 参数 | 默认 | 说明 |
|---|---|---|
| `--repo-root` | 当前工作目录 | bbt-agent-dora-piper 仓库根 |
| `--actions-dir` | `<repo-root>/actions` | 动作 NPZ 目录 |
| `--replay-tick-ms` | `20` | 写入频率 |
| `--done-hold-seconds` | `1.0` | 回 init 后保持时间 |
| `--no-uv` | false | 不给 `dora start` 附加 `--uv` |
| `--keep-run-files` | true | 第一版默认保留生成的 yml |

第一版不需要复杂 CLI 框架,用 Python 标准库 `argparse` 即可。

### 3.4 模板渲染

模板文件:

```text
nodes/agent-runner/piper_agent_runner/templates/02_replay_action.template.yml
```

模板内容建议用显式占位符:

```yaml
nodes:
  - id: agent_action_replay
    path: uv
    args: run agent-action-replay
    env:
      ACTION_ID: "{{ACTION_ID}}"
      ACTIONS_DIR: "{{ACTIONS_DIR}}"
      REPLAY_TICK_MS: "{{REPLAY_TICK_MS}}"
      DONE_HOLD_SECONDS: "{{DONE_HOLD_SECONDS}}"
    outputs:
      - started
      - done
      - at_init_pose
      - jointstate
```

渲染方式:

- 第一版可用简单 `.replace()`。
- 只允许替换固定占位符。
- `ACTION_ID` 先做白名单校验,避免写出奇怪路径或 YAML。

建议 `ACTION_ID` 规则:

```text
^[A-Za-z0-9_-]+$
```

### 3.5 临时文件路径

推荐:

```text
.agent-runs/
  20260512-001530_replay_wave.yml
```

或更简单:

```text
.agent-runs/02_replay_action_wave.yml
```

第一版建议使用时间戳文件名,避免并发/重试覆盖:

```text
.agent-runs/YYYYMMDD-HHMMSS_replay_<ACTION_ID>.yml
```

`.agent-runs/` 应加入新仓库 `.gitignore`。

### 3.6 子进程执行

runner 使用:

```python
subprocess.run(cmd, cwd=repo_root)
```

不要捕获输出作为默认行为,让 dora 日志直接透传给 agent:

```python
subprocess.run(cmd, cwd=repo_root, check=False)
```

返回码:

- dora 返回 0: runner 返回 0。
- dora 非 0: runner 返回同样的非 0。
- runner 参数错误/文件不存在:返回 2。

### 3.7 与现有 dataflow 的关系

保留:

```text
dataflow/01_prepare_init.yml
dataflow/02_replay_action.yml
dataflow/03_safe_disable.yml
```

其中 `02_replay_action.yml` 可以继续作为默认 smoke 文件,例如固定 `ACTION_ID: wave`。

runner 的 replay 不直接修改它,而是从模板生成临时 yml。

---

## 4. 可能受影响的文件或模块

新增:

```text
nodes/agent-runner/
.agent-runs/              # 运行时生成,gitignore
```

新增或调整:

```text
dataflow/02_replay_action.template.yml
```

也可以把模板放在 runner 包内:

```text
nodes/agent-runner/piper_agent_runner/templates/
```

建议更新:

```text
docs 或 README
.gitignore
```

不需要改:

```text
nodes/agent-piper-session/
nodes/agent-action-replay/
dataflow/01_prepare_init.yml
dataflow/03_safe_disable.yml
```

---

## 5. 潜在风险和边界情况

### 5.1 临时 YAML 的路径语义

如果临时 YAML 放在 `.agent-runs/`,相对路径 `../actions` 可能指向错误位置。

决策:runner 渲染绝对 `ACTIONS_DIR`,避免相对路径问题。

### 5.2 action id 注入

`ACTION_ID` 会写入 YAML 和临时文件名。

决策:只允许:

```text
A-Z a-z 0-9 _ -
```

不允许 `/`、`.`、空格、冒号等字符。

### 5.3 dora daemon 状态

runner 只是调用 `dora start`,不负责启动 daemon/coordinator。新仓库环境需要自己保证 dora 可用。

### 5.4 prepare/replay 后不失能

runner 不能在 replay 失败时自动 disable,否则可能在现场造成非预期移动。

建议:

- 第一版失败时只返回错误。
- 操作 skill 指导 agent:如果机械臂可能已 enable,再显式执行 `piper-agent disable`。

### 5.5 日志判断

第一版可以只返回 dora exit code。

后续可增强:

- 捕获 stdout
- 检查 `AGENT_READY`
- 检查 `AGENT_ACTION_DONE`
- 检查 `AGENT_DISABLED`

但捕获 stdout 会改变日志透传体验。第一版先保持简单。

---

## 6. 实施步骤

### 批次 A: runner 包骨架

1. 新建 `nodes/agent-runner/pyproject.toml`。
2. 新建 `piper_agent_runner/cli.py`。
3. 添加 entry point:
   ```text
   piper-agent = piper_agent_runner.cli:main
   ```
4. 实现 `argparse` 子命令:
   - `prepare`
   - `replay <ACTION_ID>`
   - `disable`

### 批次 B: 固定 dataflow 调用

1. `prepare` 调用 `dataflow/01_prepare_init.yml`。
2. `disable` 调用 `dataflow/03_safe_disable.yml`。
3. 统一 `--uv` / `--no-uv` 处理。
4. 子进程返回码透传。

### 批次 C: replay 模板渲染

1. 新建 replay template。
2. 校验 `ACTION_ID`。
3. 校验 `actions/{ACTION_ID}.npz` 存在。
4. 生成 `.agent-runs/<timestamp>_replay_<ACTION_ID>.yml`。
5. 调用 `dora start <generated-yml> --uv`。

### 批次 D: 文档和测试

1. 给 CLI 纯函数加测试:
   - action id 校验
   - replay yml 渲染
   - dora 命令构造
2. `dora build` 验证生成的 replay yml。
3. 更新操作 skill:
   ```bash
   uv run piper-agent prepare
   uv run piper-agent replay wave
   uv run piper-agent disable
   ```

---

## 7. 建议默认命令

新仓库 agent 应优先使用:

```bash
uv run piper-agent prepare
uv run piper-agent replay wave
uv run piper-agent disable
```

只在 runner 不可用或调试 dora YAML 时,才直接运行:

```bash
dora start dataflow/01_prepare_init.yml --uv
dora start dataflow/02_replay_action.yml --uv
dora start dataflow/03_safe_disable.yml --uv
```

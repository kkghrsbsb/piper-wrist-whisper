# action-dispatcher 节点方案

> 上位 plan:`2026-05-system-architecture-baseline-plan.md` Phase 1 第 4 步。
> 本方案只规划实现,不修改代码。目标是回放 `teach-recorder` 产出的
> `actions/wave.npz`,用 `mujoco-sim-publisher` 验证真实录制轨迹的仿真输出。

---

## 1. 功能目标

新增 `action-dispatcher` 节点,负责把预设动作 NPZ 轨迹转换成 20ms 周期的
`joint_action[7]` 输出。

第一版目标:

1. 加载 `actions/{action_id}.npz`。
2. 校验 NPZ schema:
   - `joints: float32 [N, 7]`
   - `dt: float32` 标量,第一版只接受约等于 `0.02`
   - `speech: str array [M]`,至少 1 条
3. 启动后直接回放 `ACTION_ID=wave` 指定的轨迹,先不接 `rule-matcher`。
4. 每个 20ms tick publish 一帧 `joint_action: pa.Array<float32>[7]`。
5. 回放开始时从 `speech` 中随机选一条 publish `speech_text: pa.Array<str>`。
6. 回放结束后 publish `done: pa.Array<str>`。
7. 用 `mujoco-sim-publisher + mujoco-sim-viewer` 验证 `actions/wave.npz` 可视化回放。

完成标志:

```text
ACTION_ID=wave dora start dataflow/tests/test_action_dispatcher_mujoco.yml --uv
→ action-dispatcher 加载 actions/wave.npz
→ MuJoCo viewer 中看到 wave 轨迹回放
→ 回放结束后 action-dispatcher publish done
```

---

## 2. 当前问题或动机

`mujoco-sim-publisher` 已经打通:

```text
joint_action[7] → MuJoCo qpos → jointstate[7] → viewer
```

但当前输入来自 `mujoco-sim-test-source`,只是人工写的平滑测试轨迹。下一步需要验证
teach mode 录制出的真实动作轨迹能否作为上游输入被逐帧回放。

当前已有真实轨迹:

```text
actions/wave.npz
```

实际检查结果:

```text
joints: (2796, 7) float32
dt: () float32
speech: (3,) <U5
speech = ["好的", "你好", "向你打招呼"]
```

注意:当前 `wave.npz` 的最后一帧并不等于计划中的 `PIPER_HOME_POSE` /
`INIT_JOINT_POSITION`,它是录制结束前的真实末帧。第一版 dispatcher 不应自动修正轨迹,
否则会掩盖录制质量问题;应忠实回放,并在加载时打印 warning。

---

## 3. 方案设计

### 3.1 节点边界

节点目录:

```text
nodes/action-dispatcher/
```

Python 包:

```text
action_dispatcher
```

entry point:

```text
action-dispatcher = "action_dispatcher.dispatcher:main"
```

职责:

- 解析 `ACTION_ID` / `ACTIONS_DIR`
- 加载并校验 NPZ
- 管理简单回放状态
- 按 tick 输出 `joint_action`
- 输出 `speech_text` 和 `done`

不包含:

- 不做 MuJoCo 或真机控制
- 不加载 piper-control
- 第一版不接 `arm-supervisor grant`
- 第一版不接 `rule-matcher/action_id`
- 不对轨迹做插值、平滑、裁剪或首尾补帧

### 3.2 第一版启动模式

为了尽快验证 `wave.npz`,第一版采用 env 直启:

```bash
ACTION_ID=wave dora start dataflow/tests/test_action_dispatcher_mujoco.yml --uv
```

环境变量:

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ACTION_ID` | 必填 | 例如 `wave` |
| `ACTIONS_DIR` | `actions` | 从 dora 节点 CWD 解析,测试 YAML 应传 `../../actions` |
| `DISPATCH_ON_START` | `true` | 第一版默认启动后自动回放 |
| `DONE_HOLD_TICKS` | `1` | 结束后额外保留末帧再发 done,第一版可固定为 1 |

后续接入 `rule-matcher` 时,再增加 dora 输入 `action_id` 并把 env 模式降级为测试模式。

### 3.3 dora 接口

第一版输入:

| 输入 | 来源 | 类型 | 说明 |
|---|---|---|---|
| `tick` | `dora/timer/millis/20` | timer | 每 tick 发送一帧 |

第一版输出:

| 输出 | 类型 | 说明 |
|---|---|---|
| `joint_action` | `pa.Array<float32>[7]` | 当前轨迹帧 |
| `speech_text` | `pa.Array<str>` | 随机选中的动作播报文本 |
| `done` | `pa.Array<str>` | 回放完成的 action_id |

后续完整接口:

| 输入 | 来源 | 类型 | 说明 |
|---|---|---|---|
| `action_id` | `rule-matcher/action_id` 或 `llm-client/action_id` | `pa.Array<str>` | 请求动作 |
| `grant` | `arm-supervisor/grant` | `pa.Array<bool>` | supervisor 授权 |
| `tick` | timer | timer | 20ms |

第一版刻意不实现 grant,原因是 `arm-supervisor` 尚未实现。为了避免未来接口漂移,
代码结构应预留状态机,但不要写半成品 dora 输入逻辑。

### 3.4 内部状态机

第一版状态:

```text
IDLE
  启动且 DISPATCH_ON_START=true → load action → PLAYING

PLAYING
  每 tick publish joints[index]
  index += 1
  index == N → DONE

DONE
  publish done(action_id)
  保持静默,不循环回放
```

关键行为:

- 回放期间不会接受新 action,因为第一版没有 action 输入。
- 不循环播放,避免误以为轨迹本身是周期动作。
- `speech_text` 在进入 `PLAYING` 时只发一次。
- `done` 只发一次。
- 回放完成后进程继续存活,等待 dataflow 被用户停止。

### 3.5 NPZ 加载与校验

建议拆纯函数:

```python
@dataclass
class ActionTrajectory:
    action_id: str
    joints: np.ndarray      # float32 [N, 7]
    dt: np.float32          # scalar
    speech: list[str]
```

函数:

- `parse_action_id(raw: str) -> str`
- `resolve_action_path(action_id: str, actions_dir: Path) -> Path`
- `load_action_npz(path: Path, action_id: str) -> ActionTrajectory`
- `choose_speech(speech: list[str], rng: random.Random) -> str`
- `is_near_pose(frame: np.ndarray, pose: np.ndarray, atol: float = 0.02) -> bool`

校验规则:

1. 文件不存在 → `SystemExit` 或清晰 `FileNotFoundError`。
2. 缺少 `joints` / `dt` / `speech` → `ValueError`。
3. `joints.ndim != 2` 或 `shape[1] != 7` → `ValueError`。
4. `joints` 为空 → `ValueError`。
5. `joints` 包含 NaN/Inf → `ValueError`。
6. `dt` 不是标量或不接近 `0.02` → `ValueError`。
7. `speech` 为空或全空白 → `ValueError`。
8. 第一帧/末帧不接近 `PIPER_HOME_POSE` → 只打印 warning,不拒绝加载。

为什么末帧只 warning:

- 当前 `actions/wave.npz` 的末帧已经不等于 `PIPER_HOME_POSE`。
- 第一版目标是验证真实记录内容,不是修复录制文件。
- 是否重录 wave 或增加录制端末帧校验,应作为后续独立任务处理。

### 3.6 与 `mujoco-sim-publisher` 联调

新增验证 dataflow:

```text
dataflow/tests/test_action_dispatcher_mujoco.yml
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

  - id: mujoco_sim_publisher
    path: uv
    args: run mujoco-sim-publisher
    inputs:
      tick: dora/timer/millis/20
      joint_action:
        source: action_dispatcher/joint_action
        queue_size: 1
    outputs:
      - jointstate

  - id: mujoco_sim_viewer
    path: uv
    args: run mujoco-sim-viewer
    inputs:
      joint_positions: mujoco_sim_publisher/jointstate
```

说明:

- 使用 `mujoco-sim-viewer` 只做人工观察。
- `speech_text` 和 `done` 第一版可以先只在 dispatcher 日志中确认,或后续加小型 logger 节点。
- `ACTIONS_DIR: ../../actions` 是必须的,因为 dora 节点 CWD 是 YAML 所在目录
  `dataflow/tests/`。

---

## 4. 可能受影响的文件或模块

预计新增:

```text
nodes/action-dispatcher/
nodes/action-dispatcher/pyproject.toml
nodes/action-dispatcher/action_dispatcher/__init__.py
nodes/action-dispatcher/action_dispatcher/dispatcher.py
nodes/action-dispatcher/tests/test_dispatcher.py
nodes/action-dispatcher/tests/test_smoke.py
dataflow/tests/test_action_dispatcher_mujoco.yml
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
nodes/rerun-piper-sim/
nodes/mujoco-sim-publisher/
nodes/teach-recorder/
```

除非实现过程中发现接口 bug,否则本任务只消费这些模块,不改它们。

---

## 5. 潜在风险和边界情况

### 5.1 录制轨迹末帧不在初始位

风险:baseline 要求动作末帧回到 home/init pose,但当前 `wave.npz` 末帧并不满足。

处理:

- 第一版忠实回放并打印 warning。
- 不自动补最后一帧到 `PIPER_HOME_POSE`。
- 后续由用户决定是否重录 wave,或给 teach-recorder 增加末帧距离提示。

### 5.2 录制帧数很长

`wave.npz` 有 2796 帧,按 20ms 回放约 55.9 秒,超过 baseline 中“单动作 < 30s”的目标。

处理:

- 第一版不裁剪,忠实验证当前录制文件。
- 打印预计时长 warning。
- 后续可重录或在 action-dispatcher 加最大时长拒绝策略;真机接入前必须重新评估。

### 5.3 dt 与 tick 不一致

风险:NPZ `dt` 可能不是 0.02,但 dispatcher tick 固定 20ms。

处理:

- 第一版只接受 `dt ~= 0.02`。
- 不做重采样。

### 5.4 speech 随机导致测试不稳定

风险:单元测试如果直接断言具体 speech,会因 random 失败。

处理:

- `choose_speech` 支持注入 `random.Random(seed)`。
- 测试只断言结果属于候选集合,或使用固定 seed。

### 5.5 输出积压

风险:下游阻塞时旧 `joint_action` 堆积。

处理:

- dataflow 下游输入设置 `queue_size: 1`。
- dispatcher 本身只按 tick 发一帧。

### 5.6 用户误把仿真回放当作真机可用

风险:MuJoCo 能显示轨迹,不代表真机执行安全。

处理:

- 文档和日志明确这是 Phase 1 仿真验证。
- 真机接入前需要 `dora-piper`、`arm-supervisor`、轨迹时长与首尾姿态重新审查。

---

## 6. 测试策略

单元测试:

1. `parse_action_id`:
   - 空字符串拒绝
   - 去除空白
2. `resolve_action_path`:
   - `wave` + `actions/` → `actions/wave.npz`
3. `load_action_npz`:
   - 读取真实 `actions/wave.npz` 成功
   - schema shape/dtype 正确
   - speech 解析为 list[str]
   - dt 约等于 0.02
4. 异常 NPZ:
   - 缺字段
   - joints shape 错
   - 空 frames
   - NaN/Inf
   - speech 为空
5. `PlaybackState` 或等价状态:
   - 第 1 个 tick 输出第 0 帧
   - 最后一帧后发 done
   - done 只发一次
6. smoke:
   - import `action_dispatcher.dispatcher`
   - main 在非 dora 环境按项目惯例抛 Exception 或 SystemExit

集成验证:

```bash
uv run pytest nodes/action-dispatcher -q
dora build dataflow/tests/test_action_dispatcher_mujoco.yml --uv
ACTION_ID=wave dora start dataflow/tests/test_action_dispatcher_mujoco.yml --uv
```

预期:

- MuJoCo viewer 打开。
- 可见 `wave.npz` 轨迹回放。
- 轨迹较长,约 56 秒。
- 日志打印选中的 speech、总帧数、预计时长、末帧 warning、done。

---

## 7. 实施步骤

### 批次 A:节点骨架与 NPZ 纯函数

1. 新建 `nodes/action-dispatcher/`。
2. 添加 `pyproject.toml` 和 entry point。
3. 实现:
   - `ActionTrajectory`
   - `parse_action_id`
   - `resolve_action_path`
   - `load_action_npz`
   - `choose_speech`
   - 姿态接近检查与 warning 文本
4. 添加单元测试,包含真实 `actions/wave.npz` schema 测试。

### 批次 B:回放状态机

1. 实现最小 `Playback` 状态对象。
2. 每 tick 返回当前帧。
3. 结束后返回 done,且 done 只触发一次。
4. 测试边界:空轨迹拒绝、最后一帧、完成后静默。

### 批次 C:dora 主循环

1. `main()` 中读取 env。
2. 启动后加载轨迹。
3. 进入 PLAYING 时 publish `speech_text`。
4. tick 逐帧 publish `joint_action`。
5. 结束 publish `done`。

### 批次 D:MuJoCo 联调 dataflow

1. 新增 `dataflow/tests/test_action_dispatcher_mujoco.yml`。
2. 接入:
   - `action-dispatcher`
   - `mujoco-sim-publisher`
   - `mujoco-sim-viewer`
3. `dora build` 验证。
4. 用户运行 `dora start` 观察 `wave.npz`。

### 批次 E:文档同步

1. 更新 `docs/src/README.md` 节点清单。
2. 如果实现产生偏离本方案的重要行为,创建 explain 文档并更新 `SUMMARY.md`。
3. 完成后停下汇报,等待下一步确认。

---

## 8. 明确暂不做的事

- 不接 `rule-matcher`。
- 不接 `arm-supervisor grant`。
- 不实现动作队列。
- 不支持执行中打断。
- 不做轨迹插值/平滑/重采样。
- 不修改或重写 `actions/wave.npz`。
- 不接 TTS,只输出 `speech_text`。

---

## 9. 待确认问题

1. 第一版是否接受 `wave.npz` 约 56 秒的完整忠实回放?
   我建议接受,因为这一步目标是验证录制文件本身。
2. 末帧不回初始位是否先只 warning,不阻断?
   我建议只 warning,后续通过重录解决。
3. 是否在测试 dataflow 中加入一个 `speech_text/done` logger 节点?
   我建议第一版不加,先从 dispatcher stdout 观察。

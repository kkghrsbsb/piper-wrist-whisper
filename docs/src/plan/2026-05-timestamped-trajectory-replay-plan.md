# 时间戳轨迹录制与回放重构方案

> 本方案是跨节点专项重构,覆盖 `nodes/teach-recorder` 与
> `nodes/action-dispatcher`。原始节点方案保留历史上下文:
>
> - `docs/src/plan/2026-05-teach-recorder-plan.md`
> - `docs/src/plan/2026-05-action-dispatcher-plan.md`
>
> 背景解读见:
>
> - `docs/src/explain/2026-05-piper-control-record-trajectories-explain.md`

---

## 1. 功能目标

解决当前 `actions/wave.npz` 暴露出的两个问题:

1. 录制文件没有真实时间戳,`action-dispatcher` 只能用固定 `dt=0.02` 回放,
   导致轨迹时长被拉长到约 55 秒。
2. 第二次按 X 回到 `INIT_JOINT_POSITION` 后,`teach-recorder` 立即停止,
   没有把最终初始位帧写入 NPZ,导致回放结束不回初始位。

重构目标:

- `teach-recorder` 记录每帧相对时间戳 `timestamps[N]`。
- 第二次 X 后不立即写文件,而是进入 `STOPPING`。
- `STOPPING` 确保机械臂已回到 `INIT_JOINT_POSITION`,并至少等待 1 秒再停止录制。
- 新 NPZ 兼容旧字段,新增 schema version。
- `action-dispatcher` 优先按 `timestamps` 调度回放。
- 旧 NPZ 没有 `timestamps` 时仍按 `dt` 兼容回放。

完成标志:

```text
重新录制 actions/wave.npz
→ NPZ 包含 joints / timestamps / dt / speech / schema_version
→ 最后一帧接近 INIT_JOINT_POSITION
→ action-dispatcher 按 timestamps 回放
→ MuJoCo 和真机回放时长接近实际录制动作时长
```

---

## 2. 当前问题或动机

当前 `teach-recorder` 的状态机:

```text
WAITING_START
  at_init_pose=True 第 1 次 → RECORDING

RECORDING
  jointstate → append
  at_init_pose=True 第 2 次 → break → 写 NPZ
```

问题一:没有时间戳。

- recorder 固定写 `dt=0.02`。
- 但 dora 事件到达频率、重复 jointstate、阻塞 move 期间的事件行为不等同于真实动作耗时。
- dispatcher 用 `N * 0.02` 推导时长,因此可能把短动作拉长。

问题二:停止过早。

`piper-bringup` 处理第二次 X 时:

```text
init_pose_request
→ builtin_move(INIT_JOINT_POSITION)
→ publish at_init_pose=True
→ 事件末尾 publish jointstate
```

recorder 在收到第二次 `at_init_pose=True` 后立即退出,没有等下一帧 jointstate,
所以末帧仍是用户按 X 前/移动前的状态。

官方 `piper_control/scripts/record_trajectories.py` 的关键可借鉴点:

- sample 中记录真实相对时间 `t`
- replay 时按 `t` 等待调度

---

## 3. 方案设计

### 3.1 NPZ schema v2

保持旧字段:

| 字段 | dtype | shape | 说明 |
|---|---|---|---|
| `joints` | float32 | `[N, 7]` | 6 关节 + 夹爪 |
| `dt` | float32 | `()` | legacy fallback,保留为 0.02 |
| `speech` | unicode str array | `[M]` | speech 变体 |

新增字段:

| 字段 | dtype | shape | 说明 |
|---|---|---|---|
| `timestamps` | float32 | `[N]` | 每帧相对录制开始的秒数 |
| `schema_version` | int32 | `()` | 固定为 2 |

兼容策略:

- 新 recorder 写 schema v2。
- 新 dispatcher 如果看到 `timestamps`,按 `timestamps` 回放。
- 旧 NPZ 没有 `timestamps`,仍按 `dt` 每 tick 一帧。

### 3.2 teach-recorder 状态机

新状态:

```text
WAITING_START
RECORDING
STOPPING
EXIT
```

行为:

```text
WAITING_START
  at_init_pose=True 第 1 次:
    start_time = time.monotonic()
    frames = []
    timestamps = []
    state = RECORDING

RECORDING
  jointstate:
    append(frame, time.monotonic() - start_time)

  at_init_pose=True 第 2 次:
    stop_signal_time = time.monotonic()
    state = STOPPING

STOPPING
  jointstate:
    append(frame, time.monotonic() - start_time)
    如果 frame 接近 INIT_JOINT_POSITION:
      init_seen = True
    如果 init_seen 且 time.monotonic() - stop_signal_time >= 1.0:
      write NPZ
      EXIT
```

用户特别要求:

> 按 X 回到初始位不是立即停止录制,要确保回到初始位,甚至要等待 1s 才停止录制。

本方案采用:

- 收到第二次 `at_init_pose=True` 后进入 `STOPPING`。
- 至少追加后续 jointstate。
- 检测到初始位后继续等待 `STOP_HOLD_SECONDS = 1.0`。
- 在等待期间继续追加 jointstate,保证末尾有稳定的初始位保持段。

### 3.3 初始位判定

使用 7 维 `INIT_POSE_7D`:

```python
INIT_POSE_7D = np.array(
    [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0],
    dtype=np.float32,
)
```

判定建议:

- 6 关节误差阈值 `INIT_JOINT_ATOL = 0.02 rad`
- 夹爪不作为硬性判定,只记录

原因:

- 当前 gripper 在录制中可能有微小非零值。
- 预设动作首尾姿态主要依赖 6 关节位置。

写文件前检查:

- 如果末帧 6 关节不接近初始位:打印 warning,仍写。
- 如果从未在 STOPPING 中看到初始位:打印 stronger warning,仍写。

不拒写的原因:

- 真机验证阶段更需要保留现场数据。
- 若录失败,用户可以重录。

### 3.4 时间戳规则

`timestamps[0]` 不强制等于 0。

推荐实现:

- 第一次进入 RECORDING 时 `start_time = time.monotonic()`。
- 每收到 jointstate 时记录 `time.monotonic() - start_time`。
- 保证 timestamps 单调递增。

如果事件时间戳相同或倒退:

- 正常情况下 `time.monotonic()` 不会倒退。
- 单测可断言 `np.diff(timestamps) >= 0`。

### 3.5 action-dispatcher 回放策略

扩展 `ActionTrajectory`:

```python
@dataclass
class ActionTrajectory:
    action_id: str
    joints: np.ndarray
    dt: np.float32
    speech: list[str]
    timestamps: np.ndarray | None
    schema_version: int
```

duration:

```python
if timestamps is not None:
    duration = timestamps[-1]
else:
    duration = len(joints) * dt
```

Playback 分两种:

#### 3.5.1 timestamp mode

每个 tick:

```text
elapsed = time.monotonic() - playback_start
while index < N and timestamps[index] <= elapsed:
    frame_to_send = joints[index]
    index += 1

if frame_to_send exists:
    publish 最新应发送帧
```

说明:

- 一个 tick 里如果时间已经越过多帧,只发送最新帧即可。
- 这避免 dora tick 抖动造成积压回放。
- 轨迹整体时长由 `timestamps[-1]` 决定。

#### 3.5.2 legacy dt mode

旧 NPZ 没有 `timestamps` 时:

```text
每 tick 发送下一帧
```

保持当前行为,用于兼容旧 `wave.npz` 或历史动作文件。

### 3.6 回放前移动到第一帧

官方脚本会先移动到轨迹第一帧。当前项目第一版暂不在 action-dispatcher 中实现这个动作,
原因:

- dora-piper 启动后已经自动移动到 `INIT_JOINT_POSITION`。
- teach-recorder 新版会保证第一帧来自初始位附近。
- 在 dispatcher 中插入额外 move 需要 supervisor 或 dora-piper 新接口,会扩大本批次范围。

后续如果需要,可单独引入 `prepare_action` 或 `move_to_start` 阶段。

---

## 4. 可能受影响的文件或模块

需要修改:

```text
nodes/teach-recorder/teach_recorder/recorder.py
nodes/teach-recorder/tests/test_recorder.py
nodes/action-dispatcher/action_dispatcher/dispatcher.py
nodes/action-dispatcher/tests/test_dispatcher.py
docs/src/README.md
```

可能新增:

```text
docs/src/explain/<timestamped-trajectory-explain>.md
```

不修改:

```text
actions/wave.npz              # 代码任务不覆盖;由用户重新录制
nodes/dora-piper/
nodes/piper-teleop/
nodes/mujoco-sim-publisher/
```

说明:

- 重新录制动作由用户执行。
- 代码实现不自动迁移旧 NPZ。

---

## 5. 潜在风险和边界情况

### 5.1 STOPPING 永远等不到初始位

可能原因:

- bringup move 失败但仍有事件流。
- 机械臂实际没到位。
- jointstate 延迟或断流。

处理:

- `STOPPING_TIMEOUT_SECONDS`,建议 5 秒。
- 超时后写文件并 warning,避免 recorder 永久挂住。

### 5.2 等待 1 秒会记录静止尾巴

这是预期行为。它保证末尾有稳定初始位帧。

后续如果动作文件过长,可做静止帧压缩;本批次先不做压缩。

### 5.3 timestamps 过密或重复

如果 dora 很快连续发事件,时间戳间隔可能很小。dispatcher timestamp mode 会在一个 tick
中跳过过期帧,只发送最新帧,不会积压。

### 5.4 旧 NPZ 兼容

旧文件没有 `timestamps` / `schema_version`。

处理:

- loader 不要求这两个字段。
- warning: `legacy trajectory: timestamps missing, falling back to dt mode`。

### 5.5 单元测试与真实时间

直接用 `time.monotonic()` 会让测试不稳定。

处理:

- recorder 内部封装 `now_fn` 或把状态机拆成可注入时间的纯对象。
- main 使用默认 `time.monotonic`。

---

## 6. 测试策略

### 6.1 teach-recorder 单元测试

更新/新增:

1. `build_npz_payload` 支持 `timestamps` 和 `schema_version`。
2. `timestamps` 长度必须等于 frames 数。
3. `timestamps` 为空或非单调时拒绝。
4. 正常流程:
   - 第 1 次 `at_init_pose=True` 开始
   - 记录若干 jointstate
   - 第 2 次 `at_init_pose=True` 进入 STOPPING
   - STOPPING 收到初始位 jointstate
   - 时间未满 1 秒不写
   - 时间满 1 秒后写 NPZ
5. STOPPING 超时:
   - 没看到 init pose
   - 超时后写 NPZ 并 warning
6. 事件流提前结束:
   - 未收到第二次 X:不写
   - STOPPING 中事件流结束:不写或 warning 后不写;建议不写,保持“录失败就重录”

### 6.2 action-dispatcher 单元测试

更新/新增:

1. loader 读取 schema v2:
   - `timestamps.shape == (N,)`
   - `schema_version == 2`
2. loader 读取旧 schema:
   - `timestamps is None`
   - fallback dt mode
3. timestamp playback:
   - elapsed 未到第一帧:可发送第一帧或等待;建议第一 tick 发送第一帧
   - elapsed 跨过多帧:只返回最新帧
   - 到末尾后 done 只发一次
4. legacy playback:
   - 保持现有每 tick 一帧行为

### 6.3 手动验证

用户重新录制:

```bash
ACTION_ID=wave ACTION_SPEECH="好的|你好|向你打招呼" \
  dora start dataflow/teleop/teach_record.yml --uv
```

检查 NPZ:

```bash
uv run python -c "
import numpy as np
d=np.load('actions/wave.npz')
print({k:(d[k].shape, d[k].dtype) for k in d})
print('duration:', float(d['timestamps'][-1]))
"
```

回放验证:

```bash
dora start dataflow/tests/test_action_dispatcher_mujoco.yml --uv
dora start dataflow/tests/test_action_dispatcher_dora_piper.yml --uv
```

---

## 7. 实施步骤

### 批次 A:teach-recorder schema v2 纯逻辑

1. 修改 `build_npz_payload(frames, timestamps, speech_list, dt=0.02)`。
2. 写入 `timestamps` 和 `schema_version=2`。
3. 增加 timestamps shape / 单调性校验。
4. 更新单元测试。

### 批次 B:teach-recorder STOPPING 状态

1. 引入 `STOP_HOLD_SECONDS=1.0`。
2. 引入 `STOPPING_TIMEOUT_SECONDS=5.0`。
3. 第 2 次 `at_init_pose=True` 后进入 STOPPING。
4. STOPPING 中继续记录 jointstate。
5. 看到 init pose 且 hold 满 1 秒后写文件。
6. 更新 main 状态机测试。

### 批次 C:action-dispatcher loader 兼容 schema v2

1. `ActionTrajectory` 增加 `timestamps` / `schema_version`。
2. `load_action_npz` 支持旧 schema 和新 schema。
3. duration 优先用 timestamps。
4. 更新 warnings:
   - 旧 schema warning
   - timestamps duration warning 仍保留

### 批次 D:action-dispatcher timestamp playback

1. 新增 timestamp playback 模式。
2. 保留 legacy dt playback。
3. main 中继续通过 tick 驱动,但每 tick 按 elapsed 选帧。
4. 更新单元测试。

### 批次 E:验证与文档

1. 运行:

```bash
uv run pytest nodes/teach-recorder -q
uv run pytest nodes/action-dispatcher -q
dora build dataflow/teleop/teach_record.yml --uv
dora build dataflow/tests/test_action_dispatcher_mujoco.yml --uv
dora build dataflow/tests/test_action_dispatcher_dora_piper.yml --uv
```

2. 更新 `docs/src/README.md` 的 NPZ schema 描述。
3. 如实现完成,写 explain 文档并更新 `SUMMARY.md`。

---

## 8. 明确暂不做的事

- 不使用 MIT mode。
- 不引入 gravity compensation。
- 不改成 1 秒一个路点。
- 不在 dispatcher 中调用 BuiltinJointPositionController。
- 不压缩静止帧。
- 不自动覆盖旧 `actions/wave.npz`。
- 不修改 `dora-piper`。

---

## 9. 待确认点

当前已确认:

- 新建专项 plan,不直接改旧 plan。
- `teach-recorder` 记录相对时间戳。
- `action-dispatcher` 优先按 timestamps 回放。
- 第二次 X 后不立即停止,要确保回到初始位,并等待约 1 秒再停止。

建议默认值:

```text
STOP_HOLD_SECONDS = 1.0
STOPPING_TIMEOUT_SECONDS = 5.0
INIT_JOINT_ATOL = 0.02
```

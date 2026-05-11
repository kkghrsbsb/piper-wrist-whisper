# 轨迹录制节点方案（teach-recorder）

> 上位 plan:`2026-05-system-architecture-baseline-plan.md` Phase 2 第 3 步
> "teach_record dataflow 跑通,录 wave / nod / home / stop 四个动作"
>
> 本方案新增 `teach-recorder` 节点,并对 `piper-teleop` 做必要的连锁修改。
> 连锁影响详见 §2。所有修改仅在批次确认后动手,**当前任务不改任何代码**。

---

## 1. 功能目标

1. 用手柄 X 键驱动机械臂移动到预设动作初始位（下文统称"初始位"）
2. X 键第一次触发 → 机械臂到位 → `teach-recorder` 开始累积 `jointstate`
3. 用户遥操完成一个动作并回到初始位后，X 键第二次触发 → 机械臂到位 → `teach-recorder` 停止累积、写 NPZ 文件、节点退出
4. 录制完成的 NPZ 文件供 `action-dispatcher` 回放

非目标（本方案不做）：
- 不实现 `action-dispatcher` 的 NPZ 加载与回放（留给后续 plan）
- 不录制多段连续动作（一次 dataflow 只录一个 `ACTION_ID`）
- teach-recorder 不校验末帧位置（由 X 键流程物理保证首尾在初始位）

---

## 2. 连锁影响评估

### 2A. 零位 ≠ 初始位（两个不同概念,严格区分）

| 概念 | 数值 | 谁去 |
|---|---|---|
| 零位 ZERO_POSITION | `[-1.5708, 0, 0, 0, 0, 0]` | bringup 启动目标 + Y 键 home_request 目标 |
| 初始位 INIT_JOINT_POSITION | `[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]` | 预设动作起止位 = X 键 init_pose_request 目标 |

初始位是 baseline plan §6 `PIPER_HOME_POSE` 6 关节部分的真实值。
7D 格式追加 `gripper=0.0`(自然收口),完整值:`[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0]`。

baseline plan §6 已同步更新此值。

### 2B. bringup 启动目标 = 零位(不是初始位)

启动 → ZERO_POSITION,publish `at_zero`。
按 X 键 → INIT_JOINT_POSITION,publish `at_init_pose`。两者不共用信号也不共用目标。

修改策略:
- `constants.py` 新增 `INIT_JOINT_POSITION`,与 `ZERO_POSITION` 并存
- `bringup.py` 启动 + `home_request` 维持 `ZERO_POSITION` + `at_zero`(原行为)
- `bringup.py` 新增 `init_pose_request` 分支:`INIT_JOINT_POSITION` + `at_init_pose`

### 2C. X 键信号链 + 双就绪信号

X 键完整信号链:

```
gamepad 按下 X 键
  → publish init_pose_request: bool
  → bringup 收到 → builtin_move(INIT_JOINT_POSITION) (阻塞)
  → bringup publish at_init_pose: bool
  → gamepad 收到 → ready=True、重置 target_initialized(下次 jointstate 自动同步 target_q)
  → teach-recorder 收到 → 第 1 次=开始录制 / 第 2 次=停止录制
```

`at_zero` **保留**(启动 + home_request),新增 `at_init_pose`(init_pose_request)。
gamepad 用单一 `ready` 标志:**任一就绪信号置 True 即可**。
teach-recorder 只监听 `at_init_pose`,因此启动不会被误触发录制(无需 startup_consumed)。

---

## 3. 方案设计

### 3.1 新节点 teach-recorder

**包目录**：`nodes/teach-recorder/`（连字符，包内 Python 包 `teach_recorder`）

**入口**：`teach-recorder = "teach_recorder.recorder:main"`

**dora 输入**

| 输入 | 来源 | 说明 |
|---|---|---|
| `jointstate` | `piper_bringup/jointstate` | float32[7],仅在 RECORDING 阶段累积 |
| `at_init_pose` | `piper_bringup/at_init_pose` | bool,第 1 次触发开始,第 2 次触发结束 |

**dora 输出**：无（纯文件 IO 节点）

**环境变量（必填）**

| 变量 | 示例 | 说明 |
|---|---|---|
| `ACTION_ID` | `wave` | NPZ 保存路径: `actions/{ACTION_ID}.npz` |
| `ACTION_SPEECH` | `"好的我来挥手\|好哦你好\|向你打招呼"` | 用 `\|` 分隔多条,至少 1 条,recorder 报错退出若未设置 |

> 文件路径中的 `actions/` 目录相对于启动 dora 的当前工作目录。若不存在则自动创建。

**NPZ schema**（对 baseline plan §4.6 的修订）

| 字段 | dtype | shape | 说明 |
|---|---|---|---|
| `joints` | float32 | `[N, 7]` | N 帧 × (6 关节 + 1 夹爪) |
| `dt` | float32 | `()` | 固定 0.02（对应 bringup 50Hz）|
| `speech` | unicode str array | `[M]` | M 条 speech 变体，≥ 1 |

**状态机（内部，无 dora 输出）**

```
WAITING_START
  at_init_pose=True 第 1 次 → 开始累积 jointstate → RECORDING

RECORDING
  每帧 jointstate → 追加到 buffer
  at_init_pose=True 第 2 次 → 停止累积 → 写 NPZ → EXIT
```

> 启动到零位时 bringup 只 publish `at_zero`,不会触发 teach-recorder。
> 第 1 次 `at_init_pose` 即用户首次按 X 键,无需 startup_consumed。
> 显式收到第 2 次 `at_init_pose` 才写 NPZ;事件流提前结束(如按 A 键 disable)
> 不写文件(方案 §5.3)。

**speech 解析**

```python
raw = os.environ.get("ACTION_SPEECH", "")
if not raw.strip():
    raise SystemExit("ACTION_SPEECH 未设置,录制终止")
speech_list = [s.strip() for s in raw.split("|") if s.strip()]
```

**NPZ 写入**

```python
joints_arr = np.array(frames, dtype=np.float32)          # [N, 7]
speech_arr = np.array(speech_list)                         # [M] str
np.savez(path, joints=joints_arr, dt=np.float32(0.02), speech=speech_arr)
```

### 3.2 piper-teleop 修改

**`constants.py`（1 处新增）**

```python
INIT_JOINT_POSITION = [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]
```

**`bringup.py`（2 处修改）**

1. 导入并保留 `ZERO_POSITION`,新增 `INIT_JOINT_POSITION`
2. 新增 `init_pose_request` 分支:
   ```python
   elif eid == "init_pose_request":
       ok = builtin_move(robot, INIT_JOINT_POSITION)
       robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
       node.send_output("at_init_pose", pa.array([ok]))
   ```

启动校准与 `home_request` 维持原行为(目标 `ZERO_POSITION`,publish `at_zero`)。

**`gamepad.py`（3 处修改）**

1. 状态缓存:旧 `at_zero` 改为单一 `ready: bool`,接收 `at_zero` 或 `at_init_pose` 任一信号都置 True;控制循环条件用 `(enabled and ready and target_initialized)`
2. 新增 X 键边沿检测 `btn_x = ButtonEdge()`
3. tick 处理中增加 X 键逻辑(在 A/Y 键之后):
   ```python
   x_pressed = btn_x.update(_get_button(joystick, "x"))
   if x_pressed:
       print("piper-gamepad: X pressed → init_pose_request")
       node.send_output("init_pose_request", pa.array([True]))
       target_initialized = False  # bringup 回初始位后 jointstate 自动同步
       continue
   ```

**测试更新**（`test_bringup.py` / `test_smoke.py`）

- `at_zero` 测试改为 `at_init_pose`
- 新增 `init_pose_request` 处理的 mock 测试（信号链：收到 → builtin_move → publish at_init_pose）

### 3.3 dataflow YAML 更新

**`dataflow/teleop/teach_record.yml`** 修改:

```yaml
nodes:
  - id: piper_bringup
    path: uv
    args: run piper-bringup
    inputs:
      tick: dora/timer/millis/20
      joint_action:
        source: piper_gamepad/joint_action
        queue_size: 1
      home_request: piper_gamepad/home_request
      disable_request: piper_gamepad/disable_request
      init_pose_request: piper_gamepad/init_pose_request   # 新增
    outputs:
      - jointstate
      - enabled
      - at_zero          # 启动 + home_request 到零位
      - at_init_pose     # 新增:init_pose_request 到初始位

  - id: piper_gamepad
    path: uv
    args: run piper-gamepad
    inputs:
      enabled: piper_bringup/enabled
      at_zero: piper_bringup/at_zero
      at_init_pose: piper_bringup/at_init_pose   # 新增
      jointstate: piper_bringup/jointstate
      tick: dora/timer/millis/5
    outputs:
      - joint_action
      - home_request
      - disable_request
      - record_event
      - init_pose_request   # 新增

  - id: teach_recorder
    path: uv
    args: run teach-recorder
    env:
      ACTION_ID: wave                              # 每次录制时覆盖
      ACTION_SPEECH: "好的|好哦你好|好的我来"      # 每次录制时覆盖
    inputs:
      jointstate: piper_bringup/jointstate
      at_init_pose: piper_bringup/at_init_pose
```

---

## 4. 受影响的文件

**新建**

```
nodes/teach-recorder/pyproject.toml
nodes/teach-recorder/teach_recorder/__init__.py
nodes/teach-recorder/teach_recorder/recorder.py
nodes/teach-recorder/tests/__init__.py
nodes/teach-recorder/tests/test_recorder.py
nodes/teach-recorder/tests/test_smoke.py
```

**修改（批次 B）**

```
nodes/piper-teleop/piper_teleop/constants.py     # 新增 INIT_JOINT_POSITION
nodes/piper-teleop/piper_teleop/bringup.py       # 见 §3.2
nodes/piper-teleop/piper_teleop/gamepad.py       # 见 §3.2
nodes/piper-teleop/tests/test_bringup.py         # 信号名 + 新 mock 测试
nodes/piper-teleop/tests/test_smoke.py           # 信号名更新
```

**修改（批次 C）**

```
dataflow/teleop/teach_record.yml                 # 见 §3.3
```

**不动**

- `rerun-dabai-dc1`、`rerun-piper-sim`
- `baseline plan`（等确认后独立修改）
- `SUMMARY.md`（本任务只增加本 plan 条目）

---

## 5. 风险与边界情况

### 5.1 ~~startup at_init_pose 被误消费~~（已通过信号分离消解）

启动只发 `at_zero`,不发 `at_init_pose`。teach-recorder 只监听 `at_init_pose`,
因此第 1 次接收必然来自用户按 X 键,无需启动消费标志。

### 5.2 X 键与 disable_request 冲突

X 键（init_pose_request）会触发 bringup 阻塞约 12s。期间 A 键（disable_request）
到达会被 queue 缓存（queue_size 默认）。阻塞结束后 bringup 处理 disable_request 退出。
这是可接受行为——用户需要等 X 键完成。

### 5.3 录制期间按 disable_request / home_request

录制开始后用户若按 A 键（disable_request），bringup 退出 → dora dataflow 结束 → NPZ 未写入。
这是 debug 工具的合理行为，不需要特殊处理（"录失败就重录"）。

Y 键（home_request）在录制期间不应使用，文档说明即可。

### 5.4 录制帧数为 0

如果用户按 X 键第 1 次后立即按第 2 次（没有遥操动作），`frames` 为空。
recorder 检测到 `len(frames) == 0` 时报错退出，不写 NPZ。

### 5.5 actions/ 目录与 NPZ 覆盖

- `actions/` 目录不存在时自动创建（`pathlib.Path.mkdir(parents=True, exist_ok=True)`）
- 目标文件已存在时：追加时间戳后缀 `actions/{ACTION_ID}_{timestamp}.npz`，不静默覆盖

### 5.6 dt 固定 0.02 与实际时戳的差异

bringup 以 50Hz 发送 jointstate（`tick: dora/timer/millis/20`）。dora 队列和处理延迟
可能导致实际帧间隔略大于 20ms。固定 dt=0.02 对回放是否产生位置漂移取决于 action-dispatcher
的实现方式。**当前采用固定 dt 是合理的简化**；如发现回放位置偏差，可在 recorder 中改为
记录实际时戳并计算平均 dt。

---

## 6. 实施步骤

> 批次内自主推进，批次末停下汇报等你确认。
> 涉及真机的批次（B / C）完成后由你手动测试，Claude Code 不自己跑 dora 命令。

### 批次 A：teach-recorder 节点（纯 Python，无真机风险）

- 建包结构：`nodes/teach-recorder/` + `pyproject.toml` + `__init__.py` + `tests/`
- 实现 `recorder.py`：speech 解析、状态机、NPZ 写入
- 单元测试：mock dora Node，测试 WAITING_START → RECORDING → EXIT 三段状态转换、
  NPZ 文件内容验证、ACTION_SPEECH 未设置时报错退出、空帧录制拒绝写文件
- 不需要 piper-teleop、不需要真机

完成后停下汇报。

### 批次 B：piper-teleop X 键 + 信号重命名（停下等真机测试）

- `constants.py`：新增 `INIT_JOINT_POSITION`
- `bringup.py`：见 §3.2（5 处修改）
- `gamepad.py`：见 §3.2（3 处修改）
- 更新所有相关单元测试（`at_zero` → `at_init_pose`，新增 init_pose_request mock 测试）
- mock PiperInterface 验证 init_pose_request 信号链（bringup 收到 → builtin_move → at_init_pose）

完成后停下汇报。**这一步之后由你手动在真机上验证 X 键行为（bringup 移动到新初始位 + at_init_pose 信号正常发出），Claude Code 不自己跑真机命令。**

### 批次 C：dataflow YAML + 联调准备（停下等真机录制测试）

- 更新 `dataflow/teleop/teach_record.yml`（见 §3.3）
- 写 `dataflow/tests/test_teach_recorder.yml`（仅 teach-recorder 的冒烟 YAML，
  用于验证环境变量读取和状态机行为，不需要真机）

完成后停下汇报。**真机录制联调（用 `dora start` 跑完整 teach_record.yml 录一个动作）由你执行，Claude Code 不自己跑 dora 命令。**

---

## 7. 需要同步修改的其他文档

**等你确认后才动手，当前任务不改这些文件。**

### 7.1 baseline plan §6 — PIPER_HOME_POSE 数值

```
文件: docs/src/plan/2026-05-system-architecture-baseline-plan.md
位置: §6 配置约定
改动: PIPER_HOME_POSE: "[0, 1.0, -1.2, 0, -0.6, 0, 0]"
   → PIPER_HOME_POSE: "[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0]"
前提: 你确认第 7 维 gripper=0.0 正确
```

### 7.2 baseline plan §4.6 — NPZ schema 与回放逻辑

```
文件: docs/src/plan/2026-05-system-architecture-baseline-plan.md
位置: §4.6 action-dispatcher，轨迹格式
改动 1: speech: str  →  speech: list[str]（存多条 speech 变体）
改动 2: 回放逻辑第 2 步加一句:
        "speech_text = random.choice(npz['speech'])"
```

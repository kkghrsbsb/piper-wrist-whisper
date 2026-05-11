# piper-control record_trajectories 参考解读

> 本文不对应一次代码实现改动,而是对
> `docs/references/piper_control/scripts/record_trajectories.py` 的参考解读,
> 结论服务于后续 `nodes/teach-recorder` 重构。
>
> 相关现有方案:
> - `docs/src/plan/2026-05-teach-recorder-plan.md`
> - `docs/src/plan/2026-05-action-dispatcher-plan.md`
> - `docs/src/plan/2026-05-dora-piper-plan.md`

---

## 1. 读到的核心逻辑

官方 `record_trajectories.py` 是一个交互式脚本,支持 1 或 2 台 Piper。
它不是 dora 节点,也不是 NPZ 格式,而是终端按键控制 + JSON 文件保存。

关键常量:

```python
RECORD_HZ = 100
REPLAY_HZ = 100
MOVE_DURATION = 1.0
```

记录逻辑:

1. 用户按 `r` 开始/停止录制。
2. 不启用 gravity compensation 时,录制前会停掉 MIT controller,并 `disable_arm()`,
   让用户手动拖动机械臂。
3. 每轮循环记录一条 sample:
   - `t`: `time.time() - start_time`
   - `q`: 每台机器人的 6 关节角
   - `gripper`: 每台机器人的夹爪位置
4. 每轮循环末尾 `time.sleep(1 / RECORD_HZ)`。
5. 用户按 `s` 保存为 JSON。

回放逻辑:

1. 如机械臂未 enable,先用 MIT mode 重新 `reset_arm`。
2. 创建 `MitJointPositionController`。
3. 先用 `move_to_position()` 在 `MOVE_DURATION=1s` 内插值移动到轨迹第一帧。
4. 正式回放时不按固定 index sleep,而是:

```python
replay_start = time.time()
for sample in trajectory:
    target_t = sample["t"]
    while time.time() - replay_start < target_t:
        time.sleep(0.001)
    ctrl.command_joints(sample["q"][name])
    robot.command_gripper(...)
```

也就是说,它保存了每条 sample 的真实相对时间,回放时按 `sample["t"]` 调度。

---

## 2. 和当前 teach-recorder 的关键差异

当前 `teach-recorder`:

- 输入来自 `piper_bringup/jointstate`
- 只保存 `joints[N,7]`
- 固定写 `dt = 0.02`
- 不记录真实时间戳
- 第二次 `at_init_pose=True` 立刻停止并写文件

官方脚本:

- 自己主动按固定 loop 读取 `robot.get_joint_positions()`
- 每条 sample 保存真实相对时间 `t`
- 回放时按 `t` 调度
- 回放前先移动到第一帧
- 录制结束不是“自动回初始位后写文件”,而是用户按键停止

因此当前 `wave.npz` 出现两个问题并不意外:

1. **时长被固定 dt 放大**:如果 dora 事件频率或重复帧与真实录制节奏不一致,
   `N * 0.02` 就不等于真实动作耗时。
2. **末帧不是 INIT**:第二次 `at_init_pose=True` 到来时,recorder 立即 `break`,
   没有把 bringup 随后 publish 的“到初始位后的 jointstate”写进文件。

---

## 3. 能不能直接用官方逻辑

不能直接照搬,但可以借鉴其中两点。

不适合直接照搬的部分:

- 它是终端交互脚本,不是 dora 节点。
- 它使用 MIT mode / `MitJointPositionController`,而当前 `piper-teleop` 和
  `dora-piper` 都走 POSITION_VELOCITY + JOINT 的 `command_joint_positions`。
- 它录制时可能 `disable_arm()` 让用户拖动机械臂,这和当前手柄遥操示教流程不同。
- 它保存 JSON,而当前动作资产是 `actions/{ACTION_ID}.npz`。
- 它的 gripper 值来自按键维护的 `gripper_positions`,不是每帧读取的实际夹爪状态。

适合借鉴的部分:

1. **记录每帧相对时间戳 `t`**  
   这是解决“55 秒被拉长”的核心。

2. **回放按时间戳调度,不是只相信固定 `dt`**  
   `action-dispatcher` 应优先使用 `timestamps` / `time` 数组。

---

## 4. 对 teach-recorder 的重构建议

建议不要改成“每 1 秒记录一个点 + BuiltinJointPositionController 逐点执行”。
那会把连续动作变成路点移动,适合 `home/stop/init` 这类固定姿态动作,
不适合 `wave/nod` 这种靠连续运动表达的动作。

建议保留当前手柄示教流程,但调整记录 schema 和停止状态机。

### 4.1 新 NPZ schema

保持兼容旧字段:

```text
joints: float32 [N, 7]
dt: float32 = 0.02              # legacy fallback
speech: str [M]
```

新增:

```text
timestamps: float32 [N]         # 每帧相对录制起点的秒数
schema_version: int = 2
```

`action-dispatcher` 回放策略:

1. 如果存在 `timestamps`,按 `timestamps[index]` 调度。
2. 如果不存在,回退到旧逻辑:按 `dt` 每 tick 一帧。

### 4.2 recorder 状态机

当前:

```text
WAITING_START
RECORDING
```

建议改成:

```text
WAITING_START
  at_init_pose=True 第 1 次 → RECORDING, start_time = time.monotonic()

RECORDING
  jointstate → append(frame, time.monotonic() - start_time)
  at_init_pose=True 第 2 次 → STOPPING

STOPPING
  下一帧 jointstate → append(final_init_frame, timestamp)
  写 NPZ → EXIT
```

这样可以保证第二次 X 触发后,回到 `INIT_JOINT_POSITION` 的最后一帧能进入文件。

### 4.3 末帧校验

写文件前检查:

```text
abs(last[:6] - INIT_JOINT_POSITION).max() <= 0.02
```

建议第一版只 warning,不拒写。原因是硬件读数可能有小误差,并且当前工作流偏向
“录失败就重录”。如果后续动作资产进入正式使用,再改为强校验。

### 4.4 静止帧压缩

可以后续再做,不建议第一版加。

可选策略:

- 保留第一帧、最后一帧。
- 中间帧如果和上一保留帧的 6 关节差异都小于阈值,并且时间间隔小于某个上限,
  可以跳过。
- 但压缩后必须保留 `timestamps`,否则动作节奏仍然会变形。

---

## 5. 对 action-dispatcher 的影响

如果 `teach-recorder` 新增 `timestamps`,则 `action-dispatcher` 需要从“每 tick 发下一帧”
变为“每 tick 检查当前 elapsed time 应该发到哪一帧”。

建议逻辑:

```text
start_time = time.monotonic()
for each tick:
  elapsed = time.monotonic() - start_time
  while next_index < N and timestamps[next_index] <= elapsed:
      publish joints[next_index]
      next_index += 1
```

这样即使 dora tick 有轻微抖动,也不会把整体轨迹时长拉长。

兼容旧文件:

```text
if "timestamps" not in npz:
    fallback to dt-based playback
```

---

## 6. 风险与注意事项

1. `timestamps` 来自 recorder 收到 `jointstate` 的时间,不是底层 CAN 采样时间。
   这已经比固定 `dt` 准确,但不是硬实时。

2. 如果 dora 事件队列积压,时间戳仍可能偏离真实物理运动。
   `piper_bringup/jointstate` 输入建议保持低延迟,不要在 recorder 里做重计算。

3. 旧 `wave.npz` 没有 `timestamps`,也没有正确末帧。它可以继续用于兼容性测试,
   但不应作为最终动作资产。

4. 官方脚本使用 MIT mode 和 gravity compensation 的部分暂不建议引入。
   那是另一套控制范式,会扩大 `dora-piper` 和 `teach-recorder` 的风险面。

---

## 7. 结论

可以借鉴官方 `record_trajectories.py`,但核心不是稀疏路点或 Builtin move,
而是:

- 记录每帧真实相对时间 `t`
- 回放时按 `t` 调度
- 回放前移动到起始帧

对当前项目最合适的重构方向是:

```text
teach-recorder: joints + timestamps + STOPPING 末帧
action-dispatcher: timestamps 优先,dt 兼容旧文件
```

这能解决当前 `wave.npz` 的两类问题:回放时长失真和末帧不回初始位。

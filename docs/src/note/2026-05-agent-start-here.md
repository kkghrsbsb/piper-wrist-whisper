# AGENT_START_HERE — 新 AI Agent 10 分钟上手指南

> 想法：让一个第一次接触本项目的 AI Coding Agent 能在 10 分钟内理解项目并开始工作。

---

## 第 1 分钟：先读这三个文件

按顺序读，不要跳：

1. `docs/src/README.md` — 项目是什么、当前有哪些节点、零位 vs 初始位的区别
2. `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md` — 为什么双模式、为什么远程 LLM、为什么 PTT
3. `docs/src/plan/2026-05-system-architecture-baseline-plan.md §2 §4 §8` — 节点清单、接口规范、Phase 划分

读完后用自己的话复述以下三点，复述对了再开始动手：

- LLM 模式和 VLM 模式的区别是什么？
- 哪些节点已实现，哪些还没有？
- Phase 1 要做什么？

---

## 第 2-3 分钟：理解当前系统是如何工作的

**已实现的完整 dataflow** 是 `dataflow/teleop/teach_record.yml`，三个节点：

```
piper-bringup
  ├─ 启动 → CAN 连接 → 使能 → 移到零位 → publish at_zero
  ├─ 收到 joint_action → command_joint_positions（写真机）
  ├─ 收到 home_request（Y键）→ 移到零位 → publish at_zero
  ├─ 收到 init_pose_request（X键）→ 移到初始位 → publish at_init_pose
  └─ 收到 disable_request（A键）→ 安全失能

piper-gamepad
  ├─ 等 enabled + (at_zero 或 at_init_pose) 都到了才激活
  ├─ 读 Xbox 手柄 → 关节增量 → publish joint_action
  ├─ Y → home_request，X → init_pose_request，A → disable_request
  └─ 第一次拿到 jointstate 时初始化 target_q（避免跳变）

teach-recorder
  ├─ 等第 1 次 at_init_pose=True（X键）→ 开始录制
  ├─ 每帧 jointstate 追加 buffer
  └─ 等第 2 次 at_init_pose=True（X键）→ 写 actions/{ACTION_ID}.npz → 退出
```

**所有消息都是 `pyarrow.Array`**：
- bool 信号：`pa.array([True])` → 取 `bool(event["value"][0].as_py())`
- float32 向量：`np.asarray(event["value"], dtype=np.float32)`

---

## 第 4 分钟：记住这两个位置，永远不要混淆

```python
# nodes/piper-teleop/piper_teleop/constants.py

ZERO_POSITION       = [-1.5708, 0, 0, 0, 0, 0]          # 安全零点，启动目标，Y键目标 → at_zero
INIT_JOINT_POSITION = [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]  # 动作起止位，X键目标 → at_init_pose
```

这两个位置**不能互换，不能合并，信号不互通**。

---

## 第 5 分钟：项目启动方式

```bash
# 安装依赖
uv sync

# 跑单元测试（不需要真机）
uv run pytest nodes/piper-teleop/tests/ -v
uv run pytest nodes/teach-recorder/tests/ -v

# 启动完整 teach 录制 dataflow（需要真机 + Xbox 手柄）
ACTION_ID=wave ACTION_SPEECH="好的|你好" dora run dataflow/teleop/teach_record.yml --uv

# 只启动 bringup 做冒烟测试（需要真机）
dora run dataflow/tests/test_piper_bringup.yml --uv

# 查看节点日志
dora logs <dataflow-id> piper_bringup
```

---

## 第 6 分钟：当前开发重点（Phase 1）

下一阶段要实现的节点，**推荐开发顺序**：

1. **`mujoco-sim-publisher`**（新建）：消费 `joint_action`，驱动 MuJoCo 仿真，publish `jointstate`。这是"不上真机也能联调"的基础，Phase 1 所有后续工作都依赖它。参考 `nodes/rerun-piper-sim/rerun_piper_sim/mujoco_sim_viewer.py`（只读，不改）。

2. **`action-dispatcher`**（新建）：加载 `actions/{action_id}.npz`，按 20ms tick 逐帧 publish `joint_action`。**不能用 `BuiltinJointPositionController`（阻塞）**，必须是事件驱动逐帧 publish。

3. **`rule-matcher`**（新建）：关键词 → action_id。纯逻辑节点，先写 pytest 再实现。

4. 三节点联调 dataflow：rule-matcher → action-dispatcher → mujoco-sim-publisher

接口规范全在 `docs/src/plan/2026-05-system-architecture-baseline-plan.md §4.1-4.9`。

---

## 第 7-8 分钟：最容易踩坑的地方

| 坑 | 说明 |
|---|---|
| dora CWD 不是 repo 根 | 节点 CWD = YAML 所在目录。`ACTIONS_DIR: ../../actions` 是对的，不是写错了 |
| queue_size: 1 不能改 | bringup 阻塞期间（最多 12s）靠它屏蔽堆积的 joint_action |
| `at_zero` ≠ `at_init_pose` | 这是两个不同物理位置的信号，不能复用，不能合并 |
| 阻塞调用在 dora 里 | `BuiltinJointPositionController.move_to_position()` 会冻住整个事件循环，production 节点（dora-piper）绝不能用 |
| bringup 里的多次采样 | `connect_and_enable()` 里 `ENABLE_PROBE_SAMPLES` 次采样是解 CAN 竞态的，不是冗余代码 |
| 安全失能顺序 | `safe_shutdown` 里先夹爪后机械臂，颠倒会出事 |
| 不改 `rerun-*` 节点 | 除非任务明确要求，否则视为只读 |
| 不改 `constants.py` 中的位置值 | 改了所有已录制 NPZ 都要重录 |

---

## 第 9 分钟：修改代码前必须知道的事

**每次只做一件事**：单节点或单功能，不超过 200 行改动。

**不要自己决定下一步**：下一步由人工指定，不是你推断的。

**接任务前先复述理解**：用自己的话说一遍你理解的任务范围，等用户确认再写代码。

**涉及真机的代码先走仿真**：新节点 → mujoco-sim-publisher 联调 → 真机验证。

**文档同步**：改了项目结构或接口，必须更新 `docs/src/README.md`；加了文档，必须更新 `docs/src/SUMMARY.md`。

---

## 第 10 分钟：文档速查表

| 要看什么 | 文件 |
|---|---|
| 整体架构与节点接口规范 | `docs/src/plan/2026-05-system-architecture-baseline-plan.md` |
| 技术决策的"为什么" | `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md` |
| piper-control SDK 怎么用 | `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md` |
| 完整的项目交接上下文 | `docs/src/explain/2026-05-handover.md` |
| 协作规则与禁忌 | `docs/src/note/2026-05-claude-code-collaboration-rules.md` |
| 当前进度概览 | `docs/src/README.md §当前进度` |

---

## 待探索的问题

- 多次协作后，AI 的"实习生→初级工程师"边界能否扩大？需要积累几次任务的反馈再判断。
- 是否需要给每次任务准备一份"仓库当前进度速查表"作为标准前置上下文？

## 相关文档

- `docs/src/explain/2026-05-handover.md` — 完整交接文档（详细版）
- `docs/src/note/2026-05-claude-code-collaboration-rules.md` — 协作规则原文

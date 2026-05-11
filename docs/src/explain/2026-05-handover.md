# HANDOVER.md — piper-wrist-whisper 项目交接文档

> 本文档面向**完全不了解本仓库**的新 AI Coding Agent（Codex 或同类工具）。
> 目的：让接手者能在不追问任何人的情况下，理解项目全貌、设计动机、已完成工作、当前状态以及下一步该做什么。
>
> 写作时间：2026-05-11
> 原开发搭档：Claude Code（claude-sonnet-4-6）

---

## 0. 阅读本文档前的最低要求

你需要了解：

- **dora-rs**：一个基于 Apache Arrow 的数据流框架。节点（Node）通过 YAML 描述的 dataflow 互连，消息类型固定为 `pyarrow.Array`。所有通信都是异步事件驱动的。
- **piper-control**：松灵 Piper 6-DOF 机械臂的 Python SDK，通过 CAN 总线控制。
- **Python uv**：本项目统一用 `uv` 管理 workspace 内所有包，不用 pip。

如果你不了解以上三项，请先读以下文件再继续：

- `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md`（piper-control 接口）
- `docs/src/plan/2026-05-system-architecture-baseline-plan.md`（整体架构与 dora 使用方式）

---

## 1. 项目目标

**为 Piper 6-DOF 机械臂构建一套双模式语音/文本交互系统。**

用户通过 Web Chat 界面说话（PTT）或打字，系统理解意图后：

- **LLM 模式**：机械臂执行预设动作（挥手、点头、回位等），TTS 播报对应台词
- **VLM 模式**：机械臂不动，摄像头拍摄画面，VLM 描述看到的内容，TTS 播出

两个模式由用户在 Web UI 上手动切换，不自动推断。

---

## 2. 整体架构概览

```
Browser localhost:3000 (Chat)
        │ WebSocket
        ▼
   web-server ──────► input-router ──► mode-router
   (FastAPI +                            │        │
   faster-whisper)                  LLM路径   VLM路径
                                        │           │
                                  rule-matcher   llm-client
                                     │    └──────────► (vision)
                                     │   no_match
                                     ▼
                                 llm-client ──► Qwen3-VL-8B (HTTP 10.100.1.93:12368)
                                     │
                                     ▼
                            action-dispatcher ──► dora-piper ──► Piper 真机 (CAN)
                                     │                │
                                     ▼                ▼
                                kokoro-tts       piper-state
                                     │                │
                              dora-pyaudio       arm-supervisor ──► safety-monitor
                                                       │
                                         mujoco-sim-publisher ──► rerun (9090)
```

**截至交接日，上图中只有以下节点已实现：**

| 节点 | 路径 | 状态 |
|---|---|---|
| `rerun-dabai-dc1` | `nodes/rerun-dabai-dc1/` | 已存在并验证 |
| `rerun-piper-sim` | `nodes/rerun-piper-sim/` | 已存在并验证 |
| `piper-bringup` | `nodes/piper-teleop/` (entry: piper-bringup) | 已实现并测试 |
| `piper-gamepad` | `nodes/piper-teleop/` (entry: piper-gamepad) | 已实现并测试 |
| `teach-recorder` | `nodes/teach-recorder/` | 已实现并测试 |

其余节点均**待开发**。

---

## 3. 目录结构与核心文件

```
piper-wrist-whisper/
├── CLAUDE.md                          ← Claude Code 协作约定（必读）
├── pyproject.toml                     ← uv workspace 根配置
├── uv.lock                            ← 锁文件，已包含 teach-recorder
├── actions/                           ← 录制好的预设动作 NPZ 文件（当前有 wave.npz）
├── docs/
│   └── src/
│       ├── README.md                  ← 项目概览（最先读这个）
│       ├── SUMMARY.md                 ← mdBook 目录（每加文档必须更新）
│       ├── plan/                      ← 设计规范文档
│       │   ├── 2026-05-system-architecture-baseline-plan.md  ← 架构总纲
│       │   ├── 2026-05-piper-teleop-debug-plan.md
│       │   └── 2026-05-teach-recorder-plan.md
│       ├── adr/
│       │   └── 2026-05-001-dual-mode-llm-vlm-architecture.md ← 6 大技术决策
│       ├── learn/
│       │   └── 2026-05-piper-control-api-from-repo-usage.md ← piper SDK 接口
│       ├── explain/
│       │   └── 2026-05-piper-teleop-debug-explain.md
│       └── note/
│           └── 2026-05-claude-code-collaboration-rules.md    ← 协作规则
├── nodes/
│   ├── piper-teleop/                  ← 手柄遥操节点包
│   │   └── piper_teleop/
│   │       ├── constants.py           ← 关节限位、位置常量、按键映射
│   │       ├── bringup.py             ← piper-bringup 入口
│   │       ├── gamepad.py             ← piper-gamepad 入口
│   │       └── pure_functions.py      ← 数学工具（可安全重构）
│   ├── teach-recorder/
│   │   └── teach_recorder/
│   │       └── recorder.py            ← teach-recorder 入口（状态机 + NPZ IO）
│   ├── rerun-dabai-dc1/               ← Orbbec 摄像头（只读参考，勿改）
│   └── rerun-piper-sim/               ← MuJoCo 可视化（只读参考，勿改）
│       └── rerun_piper_sim/
│           └── robot_state_publisher.py  ← piper-control 读取侧参考实现
└── dataflow/
    ├── teleop/
    │   └── teach_record.yml           ← 主 dataflow：bringup + gamepad + teach-recorder
    └── tests/
        ├── test_piper_bringup.yml     ← 冒烟测试：仅 bringup（需真机）
        └── test_teach_recorder.yml   ← 环境变量验证（不需真机）
```

---

## 4. 已实现节点详解

### 4.1 piper-bringup（`nodes/piper-teleop/piper_teleop/bringup.py`）

**职责**：CAN 连接、机械臂使能、关节指令写入、安全失能。

**关键设计**：

- 启动时用 `BuiltinJointPositionController` **阻塞**移动到 `ZERO_POSITION`，完成后 publish `at_zero=True`
- `queue_size: 1` 防止阻塞期间 joint_action 堆积
- 收到 `home_request` → 再次移动到 `ZERO_POSITION` → publish `at_zero`
- 收到 `init_pose_request` → 移动到 `INIT_JOINT_POSITION` → publish `at_init_pose`
- 收到 `disable_request` → 调用 `safe_shutdown`（先失能夹爪再失能机械臂）

**阻塞窗口**（约 12 秒）：

- 启动 home、Y 键 home、X 键 init_pose、A 键 disable 时均会阻塞 dora 事件循环
- 这是**已知的设计取舍**：用简单阻塞换掉异步状态机的复杂度，在 teach-record 场景下可接受

**不要轻易修改**：

- `connect_and_enable()` 里的多次采样使能状态（`ENABLE_PROBE_SAMPLES`）是解决 CAN 竞态的关键
- `safe_shutdown()` 中夹爪先于机械臂失能的顺序——反转会导致夹爪没收回就断电

### 4.2 piper-gamepad（`nodes/piper-teleop/piper_teleop/gamepad.py`）

**职责**：读 Xbox 手柄，输出关节增量。

**重要概念**：

- `ready` 标志（非 `at_zero`）：`at_zero` 或 `at_init_pose` 任一到达即设为 `True`，机械臂就绪后才允许下发 joint_action
- `target_initialized`：收到第一个 jointstate 且 ready 时初始化目标位，避免跳变
- `ButtonEdge`：边沿检测，确保按键只触发一次

**手柄按键约定**（不要随意修改映射，已与 teach-recorder 耦合）：

| 键 | 信号 |
|---|---|
| 摇杆/D-pad/扳机 | `joint_action`（关节增量） |
| Y | `home_request`（零位） |
| X | `init_pose_request`（初始位，也是录制触发键） |
| A | `disable_request` |
| LB/RB | 速度档位切换（内部状态，尚未传给 bringup） |

**已知 stub**：

- LB/RB 的速度倍率在 gamepad 内部计算，但**没有**通过信号传给 bringup，真机速度不变
- `record_event` 输出口已在 dataflow YAML 中声明，但 gamepad 实际不 publish（Start 键未监听）

### 4.3 teach-recorder（`nodes/teach-recorder/teach_recorder/recorder.py`）

**职责**：在 X 键触发初始位时，累积 jointstate 并写 NPZ。

**状态机**：

```
WAITING_START
  → 收到 at_init_pose=True（第 1 次 X 键）→ RECORDING
  
RECORDING
  → 每帧 jointstate 追加 buffer
  → 收到 at_init_pose=True（第 2 次 X 键）→ 写 NPZ → 退出
```

**关键约束**：

- 未收到第 2 次 X 就退出（如 A 键 disable）→ **不写文件**（"录失败就重录"原则）
- 空 buffer（两次 X 紧挨着）→ 写前检查，报错退出，不写空 NPZ
- `at_init_pose=False` 信号（移动失败）→ 静默忽略，不改变状态

**环境变量（必填）**：

```bash
ACTION_ID=wave                          # NPZ 文件名
ACTION_SPEECH="好的|你好|打招呼"         # 用 | 分隔的台词变体
ACTIONS_DIR=../../actions               # 相对于 YAML 所在目录，指向 repo 根/actions/
```

**NPZ schema**：

```python
{
    "joints": float32[N, 7],   # N 帧 × (6 关节 + 1 夹爪)
    "dt": float32,             # 0.02s = 50Hz
    "speech": array[str]       # M 条台词变体
}
```

---

## 5. 关键常量（`nodes/piper-teleop/piper_teleop/constants.py`）

这是两个**不同的物理位置**，信号也不共用：

| 常量 | 值 | 用途 | 信号 |
|---|---|---|---|
| `ZERO_POSITION` | `[-1.5708, 0, 0, 0, 0, 0]` | bringup 启动目标；Y 键目标 | `at_zero` |
| `INIT_JOINT_POSITION` | `[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]` | 所有预设动作起止位；X 键目标；录制参考位 | `at_init_pose` |
| `SAFE_DISABLE_POSITION` | `[-1.5708, 0, 0, 0.02, 0.5, 0]` | A 键失能前的收臂位 | — |

**绝对不能把这两个位置互换或合并**，它们的语义完全不同：
- `ZERO_POSITION` 是"机械臂回到安全零点"的概念
- `INIT_JOINT_POSITION` 是"预设动作统一起始形态"的概念，对应 baseline plan §6 的 `PIPER_HOME_POSE` 6 关节部分

---

## 6. 数据流通信约定

### dora 事件类型

所有 payload 都是 `pyarrow.Array`：

- bool 信号：`pa.array([True/False])` → 接收侧 `bool(event["value"][0].as_py())`
- float32 向量：`pa.array([...], dtype=pa.float32)` → 接收侧 `np.asarray(event["value"], dtype=np.float32)`
- str：`pa.array(["text"])` → 接收侧 `str(event["value"][0].as_py())`

### queue_size: 1

所有接受关节指令或状态信号的输入口均设 `queue_size: 1`。原因：bringup 阻塞窗口期间堆积的指令在机械臂复位后不应被执行。**不要修改此设置。**

### CWD 约定

dora 会把每个节点的工作目录设为 **dataflow YAML 所在目录**（不是 repo 根）。这就是为什么：

```yaml
# dataflow/teleop/teach_record.yml
ACTIONS_DIR: ../../actions   # 从 dataflow/teleop/ 出发，到 repo 根/actions/
```

写新节点时如果有文件 IO，必须考虑这个路径偏移。

---

## 7. 依赖与工具链

### 包管理

```bash
uv sync            # 同步所有 workspace 成员依赖
uv run <cmd>       # 在 workspace 虚拟环境中运行命令
```

`nodes/dora-pyaudio` **被 exclude 出 workspace**（来自 dora-hub，只做参考）。

### 关键依赖版本

| 依赖 | 用途 | 注意 |
|---|---|---|
| dora-rs | 数据流框架 | 版本 ≥0.5.0 |
| piper-control | 机械臂 SDK | 不在 PyPI，需本地安装 |
| pygame | 手柄输入 | 仅 piper-teleop |
| numpy | 数组运算 | 所有节点 |
| pyarrow | dora 消息格式 | 所有节点，版本 ≥23 |
| mujoco | 仿真（已有节点用） | 不要在新节点引入 mujoco 依赖 |
| rerun-sdk | 可视化 | 仅 rerun-* 节点 |

### 测试

```bash
uv run pytest nodes/piper-teleop/tests/ -v    # 38 个测试
uv run pytest nodes/teach-recorder/tests/ -v  # ~10 个测试
```

### dora 常用命令

```bash
dora build dataflow/<file>.yml --uv   # 构建（下载依赖）
dora run dataflow/<file>.yml --uv     # 启动 dataflow
dora list                              # 查看运行中的 dataflow
dora stop <dataflow-id>               # 停止
dora logs <dataflow-id> <node-id>     # 查看节点日志
```

### 以 teach_record.yml 为例（带环境变量）

```bash
ACTION_ID=wave ACTION_SPEECH="好的|你好" dora run dataflow/teleop/teach_record.yml --uv
```

---

## 8. 为什么这样设计（不是别的方案）

全部决策见 `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md`，以下是精华摘要：

### 为什么双模式而不是统一 VLA？

相机在手腕上，机械臂动起来画面就糊。如果 VLM 同时控制动作和回答问题，需要 frame-gate + idle 检测，调试成本极高。把"机械臂动"（LLM 模式）和"只看画面"（VLM 模式）分开，两条路径独立迭代，互不干扰。

### 为什么 LLM 走远程而不是本地？

Qwen3-VL-8B 已在局域网服务器 `10.100.1.93:12368` 跑了 OpenAI 兼容 API，边缘机显存不够跑 8B 模型，直接用就好。

### 为什么语音用 PTT 而不是 VAD？

机械臂电机噪声会被 whisper 转成乱码，乱码被模糊匹配命中误触发动作。PTT 根本解决问题。

### 为什么 dora-piper 自研而不用 dora-hub？

dora-hub 的版本与 piper-control 的调用方式可能不一致；更重要的是，action-dispatcher 需要 20ms 逐帧 publish，不能用阻塞的 `BuiltinJointPositionController.move_to_position`。

### 为什么用 bringup 阻塞而不是状态机？

piper-teleop 是**调试工具**，不是产品节点。用阻塞等待比异步状态机简单得多，teach 场景下可接受。未来 `dora-piper` 需要状态机（见 baseline plan §4.9）。

---

## 9. 已放弃的方案

| 方案 | 放弃原因 |
|---|---|
| VLA 单一模式（VLM 直接输出动作） | 相机在手腕上，机械臂动画面糊，frame-gate 调试成本极高 |
| dora-microphone + dora-vad + dora-distil-whisper | PTT 确定后音频源是浏览器，这三个节点无用武之地 |
| 本地 Qwen2.5-1.5B 做文本决策 | 局域网 8B 模型更强、已在跑，没理由再在本机跑一份小模型 |
| Control UI 面板 + 浮窗 | 可控功能太少，复杂度不合理 |
| frame-gate 节点 | VLM 模式机械臂不动，不需要 |
| EXECUTING 期间支持 stop 打断 | 预设动作 < 30s 且安全，用户等完再发指令是可接受的 |
| dora-hub 版 dora-piper | 控制细节（逐帧 publish、失能顺序）不满足需求 |
| iframe 嵌入 rerun | 跨域配置麻烦，DevTools 进不了 iframe |

---

## 10. 最脆弱的部分

### 10.1 CAN 连接（`bringup.py: connect_and_enable`）

piper-control 的 CAN 连接在某些环境下需要重复尝试才能成功使能。代码里有 `ENABLE_PROBE_SAMPLES` 次采样取或值的逻辑，**不要以为这是冗余代码删掉**。

### 10.2 阻塞期间的 dora 事件丢失

bringup 阻塞时，dora 的输入事件**不丢失但不处理**。`queue_size: 1` 保证只保留最新一条。如果你把 queue_size 调大，阻塞结束后会处理历史事件，可能导致意外动作。

### 10.3 teach-recorder 的文件路径

`ACTIONS_DIR` 是相对路径，相对的是 YAML 所在目录而不是 repo 根。如果移动 YAML 文件，必须同步调整这个值。

### 10.4 gamepad 的 `target_initialized` 标志

如果 gamepad 在 bringup ready 之前收到 jointstate（理论上不应该，但 dora 事件序可能不保证），会跳过初始化。这是一个竞态，目前通过依赖 bringup 的 `enabled` + `ready` 两个前置条件来规避。

### 10.5 远程 LLM 可达性（尚未实现，但未来会很脆弱）

`10.100.1.93:12368` 是局域网服务，断网或服务器重启就不可达。`safety-monitor` 的降级逻辑是关键安全措施，实现时不能省略。

---

## 11. 不要轻易重构的代码

| 文件/位置 | 理由 |
|---|---|
| `bringup.py: safe_shutdown` 中的夹爪→机械臂顺序 | 颠倒顺序会导致夹爪未收回就断电 |
| `bringup.py: connect_and_enable` 的多次采样 | 解决 CAN 初始化竞态 |
| `gamepad.py: ButtonEdge` | 边沿检测是状态机，改了容易产生连发 |
| `recorder.py: stop_received` 检查 | 保证"录失败不写文件"的安全语义 |
| `constants.py` 中的位置值 | 与真机标定、动作录制结果强耦合，改了所有 NPZ 都要重录 |
| dataflow YAML 中的 `queue_size: 1` | 阻塞期间的事件屏障 |

---

## 12. 当前项目进度

### 已完成

- [x] 项目整体架构设计（plan + adr 文档）
- [x] piper-control API 学习文档
- [x] piper-bringup 节点（CAN 连接、使能、回零、初始位、失能）
- [x] piper-gamepad 节点（Xbox 手柄输入、关节增量控制）
- [x] teach-recorder 节点（X 键录制、NPZ 写入）
- [x] teach_record.yml 完整 dataflow（三节点联调）
- [x] 单元测试 48+ 个（纯函数 14 个 + bringup 17 个 + recorder ~10 个 + smoke 7 个）
- [x] wave 动作录制（`actions/wave.npz`，格式验证通过）

### 未完成

- [ ] Phase 0：web-server（FastAPI + WebSocket，暂无 STT）
- [ ] Phase 0：前端 React 骨架（Chat 页、模式切换、STOP 按钮）
- [ ] Phase 1：input-router、mode-router
- [ ] Phase 1：rule-matcher（关键词 → action_id）
- [ ] Phase 1：arm-supervisor 最小版本（BOOT/READY/EXECUTING）
- [ ] Phase 1：action-dispatcher（NPZ 回放，逐帧 publish joint_action）
- [ ] Phase 1：mujoco-sim-publisher（消费 joint_action 驱动仿真）
- [ ] Phase 2：dora-piper（piper-control 写入侧封装）
- [ ] Phase 2：web-server 加入 faster-whisper STT
- [ ] Phase 2：前端 PTT（S 键 + MediaRecorder）
- [ ] Phase 2：录制更多动作（nod、home、stop）
- [ ] Phase 3：llm-client text/vision query
- [ ] Phase 3：safety-monitor
- [ ] Phase 3：VLM 模式前端对接

---

## 13. 下一阶段建议（Phase 1 起点）

建议按以下顺序开始，每次只实现一个节点：

1. **`mujoco-sim-publisher`**（新建，不改 `rerun-piper-sim`）
   - 消费 `joint_action: float32[7]`，驱动 MuJoCo，publish `jointstate`
   - 参考 `nodes/rerun-piper-sim/rerun_piper_sim/mujoco_sim_viewer.py` 的 MJCF 加载方式（只读，不修改）
   - 这是 Phase 1 联调的基础，没有它就没有"不上真机"的安全迭代方式

2. **`action-dispatcher`**（新建）
   - 加载 `actions/{action_id}.npz`，按 20ms tick 逐帧 publish `joint_action`
   - 不使用 `BuiltinJointPositionController`（阻塞），必须是逐帧 publish
   - 先不接 supervisor grant，简单版直接 publish 就行
   - 接口规范见 `docs/src/plan/2026-05-system-architecture-baseline-plan.md §4.6`

3. **`rule-matcher`**（新建）
   - 纯逻辑节点，先写 pytest 再实现（TDD）
   - 关键词表见 plan §4.4
   - 用 rapidfuzz.fuzz.partial_ratio >= 75

4. **联调**：rule-matcher → action-dispatcher → mujoco-sim-publisher
   - 写 dataflow YAML 把三者串起来
   - 前端还没有，用 dora 命令手动 inject text event 测试

---

## 14. 性能与实时性注意事项

### 50Hz 控制循环

bringup 的 tick 是 `dora/timer/millis/20`（50Hz）。任何在 tick 处理路径上的同步操作（piper-control API 调用、日志打印）都会影响控制频率。`print()` 用于调试，生产环境可以减少。

### 阻塞操作的影响范围

`builtin_move()` 调用 `BuiltinJointPositionController.move_to_position()`，这是**完全阻塞**的。阻塞期间整个 dora 节点的事件循环停止（`queue_size: 1` 负责收尾）。对于 teach-recorder 这个用途，最多阻塞 12 秒是可接受的。但**production dora-piper 必须不阻塞**，参见 ADR-001 决策六的分析。

### 关节角速度安全限制

`JOINT_SAFE_SPEED = 10`（piper-control speed 参数）。不要调高。这个值是在真机实验中确认安全的保守值。

### VLM 模式延迟

Qwen3-VL-8B 处理图像请求延迟 1-3 秒是预期范围。不要因为"太慢"就尝试降低 max_tokens 到 0 或发空 image——这会让 VLM 模式没有意义。

---

## 15. 调试方法与踩坑记录

### 调试 dora 节点

```bash
# 查看节点日志
dora logs <dataflow-id> piper_bringup

# 只起一个节点做 smoke test
dora run dataflow/tests/test_piper_bringup.yml --uv
```

### mock 测试策略

所有 piper-control 调用在测试中都应该 mock（`monkeypatch.setattr(bringup, "connect_and_enable", ...)`）。参考 `nodes/piper-teleop/tests/test_bringup.py` 的模式，特别是 `_make_main_env` 函数。

### 已知踩坑

1. **piper-control 多次重置**：`connect_and_enable()` 中有 `ENABLE_PROBE_SAMPLES` 次采样使能的逻辑，因为 CAN 初始化后使能状态不稳定。不是 bug，不要删。

2. **dora CWD 陷阱**：节点的 CWD 是 YAML 所在目录，不是 repo 根。`ACTIONS_DIR: ../../actions` 不是奇怪的相对路径，是正确的。

3. **at_zero vs at_init_pose 混用**：gamepad 里有个历史遗留：变量名从 `at_zero` 重构成了 `ready`，但 bringup 对外信号名不变（`at_zero` / `at_init_pose`）。不要再引入第三个"就绪"状态变量。

4. **ButtonEdge 在 gamepad 未初始化时**：gamepad 等 `enabled` + `ready` + `target_initialized` 三个条件都满足才处理手柄输入。如果按键在 ready 之前按下，会被忽略（符合预期，不是 bug）。

5. **文档与代码目录命名**：最初文档中目录名用下划线（`piper_teleop`），实际 Python 包名也是下划线，但节点 ID 用连字符（`piper-bringup`）。约定：dora node ID 用连字符，Python 包名用下划线。

6. **piper-control 两个未解决问题**（留给 dora-piper 实现时解决）：
   - `set_installation_pos(UPRIGHT)` 是否必须调用？
   - `robot.disable_arm()` vs `piper_init.disable_arm(robot)` 有什么区别？

---

## 16. 当前已知问题

| 问题 | 优先级 | 状态 |
|---|---|---|
| LB/RB 速度档位只在 gamepad 内计算，未传给 bringup | 低 | 待 Phase 2 处理 |
| record_event 输出口声明了但未 publish（Start 键未监听） | 低 | 预留接口，当前不需要 |
| `set_installation_pos` 是否必须 | 待确认 | 在 dora-piper 实现时解决 |
| kokoro-tts 中文音色质量未验证 | 中 | Phase 2 时确认 |
| faster-whisper 模型大小在边缘机上的延迟未测 | 中 | Phase 2 时实测 |
| 远程 LLM endpoint 是否需要鉴权 | 低 | 当前假设内网信任 |

---

## 17. Agent 在本项目中的推荐工作方式

以下是经过多次迭代沉淀的协作规则（详见 `docs/src/note/2026-05-claude-code-collaboration-rules.md`）：

### 核心原则：每次只做一件事

**不要**：

- 一次实现整个 Phase
- 自己决定"接下来做什么"
- 引入文档里没有提到的新依赖
- 修改 `rerun-dabai-dc1` 或 `rerun-piper-sim`（除非任务明确要求）
- 在 README.md 写实现细节

**要做**：

- 每次只实现一个节点或一个组件
- 每次改动代码行数尽量在 200 行以内
- 纯逻辑节点先写 pytest 再实现（TDD）
- 远程 LLM 调用必须在单元测试中 mock
- 实现涉及真机的代码，先用 mujoco-sim-publisher 仿真验证

### 第一次接手时的操作步骤

1. 阅读以下文档，用自己的话总结理解：
   - `docs/src/README.md`
   - `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md`
   - `docs/src/plan/2026-05-system-architecture-baseline-plan.md`

2. 列出在阅读中发现的、需要澄清的问题（不超过 3 个）

3. **等用户确认理解正确后**，再开始写代码

### 接受任务时的检查清单

- [ ] 任务范围是否明确（单节点 / 单功能）？
- [ ] 对应的接口规范在 plan 文档的哪一节？
- [ ] 是否需要先写测试？
- [ ] 是否涉及真机？（是 → 先接仿真）
- [ ] 是否修改了不该改的文件？（`rerun-*` 节点、`constants.py` 中的位置值）
- [ ] 完成后是否需要更新 `docs/src/README.md` 和 `docs/src/SUMMARY.md`？

---

## 18. 文档导航速查

| 问题 | 看哪里 |
|---|---|
| 整体架构是什么？ | `docs/src/plan/2026-05-system-architecture-baseline-plan.md` |
| 为什么这样设计？ | `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md` |
| piper-control 怎么用？ | `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md` |
| 手柄遥操节点是怎么设计的？ | `docs/src/plan/2026-05-piper-teleop-debug-plan.md` |
| 手柄遥操节点具体实现了什么？ | `docs/src/explain/2026-05-piper-teleop-debug-explain.md` |
| teach-recorder 是怎么设计的？ | `docs/src/plan/2026-05-teach-recorder-plan.md` |
| 与 AI 协作应该注意什么？ | `docs/src/note/2026-05-claude-code-collaboration-rules.md` |
| 当前进度速查 | `docs/src/README.md §当前进度` |
| Phase 划分 | `docs/src/plan/2026-05-system-architecture-baseline-plan.md §8` |

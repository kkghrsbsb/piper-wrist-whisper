# DEVELOPMENT_HISTORY — 项目开发演进记录

> 想法：按时间顺序总结本项目从开始到现在的重要开发阶段，记录设计思路和工程演进过程，不只是代码 diff。
>
> 写作时间：2026-05-11

---

## 第一阶段：从 piper-vision-demo 迁移过来（c3bfdc2，2026-05-10 15:24）

### 当时的目标

把一个已有的机器人视觉 demo 仓库重构成本项目的起点。原始仓库已经有两个验证可用的节点：`rerun-dabai-dc1`（Orbbec 摄像头 + Rerun 可视化）和 `rerun-piper-sim`（真机 → MuJoCo 镜像）。

### 做了什么

- 一次性 commit 导入了大量代码：123 个文件，618071 行增量，其中大部分是 MuJoCo 的 3D 模型资产（`.obj`/`.stl`）
- 保留了两个已有节点，配置好 uv workspace，设置 .gitignore
- 建立了 mdBook 文档框架（`docs/book.toml`，`docs/src/SUMMARY.md`）
- 创建了初版 `CLAUDE.md` 和 `docs/src/README.md`

### 关键问题：第一份 README 是一份「超前规划文档」

第一版 `docs/src/README.md`（673 行）把以下所有内容都塞进了一个文件：
- 整体架构图
- 所有节点接口规范
- 状态机定义
- 前端交互细节
- Phase 划分
- 配置约定
- 风险清单
- 协作规则

而且文档里预设了还不存在的目录（`webui/`、`scripts/`），并且命名与当时的约定不完全一致（后来发现文档里某些名字和实际代码的命名风格有出入，导致第一次让 Claude Code 读文档时提出了三个澄清问题）。

### 最重要的经验

**文档要反映当前事实，不要超前规划目录细节。** 写"webui/ 目录放前端代码"这句话是无害的，但当 AI 读到这句话后发现目录不存在，会产生困惑。一份过度超前的文档比没有文档更危险，因为它会给接手者一个虚假的"我已经理解了"的感觉。

---

## 第二阶段：文档架构重构，从一篇大文档拆成多文件（9f83e55，2026-05-10 16:44）

### 当时的目标

意识到第一份单体文档有根本性缺陷，在动手写代码之前先重构文档。

### 做了什么

把 673 行的单体文档拆成四份独立文档：

| 新文档 | 内容 | 目录 |
|---|---|---|
| `2026-05-system-architecture-baseline-plan.md` | 整体数据流、节点清单、状态机、节点接口、Phase 划分 | `plan/` |
| `2026-05-001-dual-mode-llm-vlm-architecture.md` | 6 个架构决策的"为什么" | `adr/` |
| `2026-05-piper-control-api-from-repo-usage.md` | piper SDK 接口学习笔记 | `learn/` |
| `2026-05-claude-code-collaboration-rules.md` | 与 AI 协作的方法论 | `note/` |

同时把 `docs/src/README.md` 缩减到只保留项目概览和文档导航（CLAUDE.md 规定它只维护概览）。

### 为什么这样做

第一版单体文档有一个具体的痛点：**每次微调一个 Phase 就得重写整份文档**。拆分之后，ADR 是"技术决策记录"（只在决策变了时才改），plan 是"接口规范"（节点设计时参考），learn 是"外部 API 理解"（一次性知识）。各自职责单一，互不干扰。

### 工程演进的关键思路：「文档分层」

这个阶段真正建立的不是某几个 Markdown 文件，而是一套**文档分层模型**：

```
ADR         → 为什么做这个决策（历史不变）
plan        → 接口长什么样（较稳定）
learn       → 外部工具怎么用（一次性）
explain     → 这次改了什么（每次实现后写）
note        → 我自己的思考（随时记）
```

每一层的"更新频率"不同，"受众"也不同。ADR 是给未来回顾决策的人看的，plan 是实现时的参考，explain 是回顾实现的记录。这个分层在后来的每次任务中都起了作用。

### 已知权衡

plan 文档比较重（最终 500+ 行），包含了所有节点接口。这是故意的：宁可一份大的 plan 文档，也不要 11 份小的"每个节点单独一个 plan"——后者会导致接口一致性更难维护。

---

## 第三阶段：piper-teleop 实现，第一个从零写起的节点（3f9b87d，2026-05-10 20:24）

### 当时的目标

实现一套用 Xbox 手柄遥操 Piper 机械臂的 dora 节点，目的不是产品功能，而是：

1. **验证 dora + piper-control 的集成方式**：CAN 连接、使能、写关节角，全部走 dora event 驱动
2. **建立 teach-recorder 的前置基础设施**：后续录制预设动作需要一套能把机械臂移到正确位置的工具

### 关键设计决策：阻塞 vs 状态机

这是整个阶段最重要的工程权衡。

`BuiltinJointPositionController.move_to_position()` 是一个**阻塞调用**（内部轮询，等机械臂到位才返回，最多 12 秒）。有两个选择：

**选项 A（阻塞）**：直接调用，在阻塞期间 dora 事件循环停止，靠 `queue_size: 1` 屏蔽堆积指令。

**选项 B（异步状态机）**：用 Python 定时器 + 事件轮询，在每次 tick 里判断当前位置与目标位置的距离，接近了再切状态。

选了 A。理由：

- piper-teleop 是**调试工具**，不是产品节点。允许简单。
- 状态机需要处理很多边角情况（超时、中途失能、多个 request 并发），调试工具不值得这个复杂度。
- `queue_size: 1` 是 dora 原生功能，阻塞期间的事件处理行为是可预测的。

代价：阻塞期间日志不打印，调试体验略差。

**这个决策的教训**：技术债不只有"复杂度太高"，还有"复杂度太低导致不可扩展"。piper-teleop 用阻塞是对的，但未来的 dora-piper 节点（产品用）**必须**用非阻塞方式（逐帧 publish，不用 `BuiltinJointPositionController`）。两个场景，两个不同的正确答案。

### 关键设计决策：CAN 初始化的多次采样

`connect_and_enable()` 里有一段看起来像冗余的代码：对 `is_arm_enabled()` 多次采样取或值（`ENABLE_PROBE_SAMPLES` 次）。

这不是冗余，是解决 CAN 总线初始化竞态的工程解法。CAN 总线完成激活后，piper-control 的使能状态有时要几百毫秒才稳定，单次查询会得到 `False`，但稍后再查就是 `True`。

发现这个问题的方式：参考文档里的 `docs/references/tests/hardware/connect_init.py`（原始连接脚本），以及 `rerun-piper-sim/robot_state_publisher.py`（已验证可用的读取侧实现）。

### 工程实践：纯函数隔离测试

`pure_functions.py` 把以下逻辑完全隔离成纯函数：

- `apply_deadzone()`：摇杆死区过滤
- `clip_target_to_limits()`：关节角限位
- `apply_lead_window()`：最大超前窗口（防止目标位置跑太远导致电机过冲）
- `compute_target_increment()`：轴到关节的增量映射

这 4 个函数贡献了 14 个单元测试，可以完全脱离硬件和 dora 环境跑。这是整个项目测试策略的核心：**把有计算逻辑的部分提取成纯函数，硬件交互部分用 mock 测试**。

### 最重要的经验

**参考文档（learn/）和参考代码（docs/references/）的价值远大于想象。** `piper-control-api-from-repo-usage.md` 把 piper SDK 的三步 CAN 连接、`BuiltinJointPositionController` 的阻塞语义、`command_joint_positions` 的参数范围等都提前学完，实现 bringup.py 时基本没走弯路。反过来，如果先动手实现再遇到问题再查文档，会浪费大量时间在"API 猜测"上。

---

## 第四阶段：teach-recorder，引入初始位概念（86e8c02，2026-05-11）

### 当时的目标

实现轨迹录制功能：用户通过手柄把机械臂移到某个位置，按 X 键开始录制，遥操完成后再按 X 键，系统把轨迹写成 NPZ 文件。

### 关键概念演进：零位 vs 初始位

这个阶段最重要的不是代码量，而是一个**概念区分**的引入。

在 piper-teleop 实现阶段，"机械臂就绪"只有一个状态：`at_zero`，对应 `ZERO_POSITION`。这个设计在 teach-recorder 进来之前是完整的。

但 teach-recorder 需要一个"所有预设动作的统一起止位"——这个位置和零位**不是同一个地方**：

- 零位（`ZERO_POSITION = [-π/2, 0, 0, 0, 0, 0]`）：机械臂折叠到最安全的收纳姿态，适合启动和停机
- 初始位（`INIT_JOINT_POSITION = [-π/2, 0.25, -1.0, 0.0, 0.5, 0.0]`）：机械臂伸展到一个自然的工作姿态，适合开始动作

如果把这两个位置混用（比如让初始位等于零位，或者复用 `at_zero` 信号），teach-recorder 的逻辑会变成"bringup 启动时也触发录制开始"——这显然不对。

**解决方案**：引入独立的 `INIT_JOINT_POSITION` 常量 + X 键 → `init_pose_request` → `at_init_pose` 信号链。两套信号完全独立，`at_zero` 和 `at_init_pose` 在 gamepad 里统一合并成 `ready` 标志，但这个合并只发生在 gamepad 侧（"机械臂处于已知位置就可以操作"），不影响各自信号的语义独立性。

### 关键设计：状态机简化

teach-recorder 的核心逻辑需要一个状态机，初版设计里有一个 `startup_consumed` 标志，用来跳过 bringup 启动时发出的 `at_init_pose` 信号。

这个 flag 意味着：teach-recorder 对 bringup 启动行为有假设（"启动时会发一次 at_init_pose"）。

但问题在于：**bringup 启动目标是零位，不是初始位**。启动时只会 publish `at_zero`，根本不会 publish `at_init_pose`。

所以 `startup_consumed` flag 是多余的，直接删掉，状态机简化成：

```
WAITING_START → (收到 at_init_pose=True) → RECORDING → (再次收到 at_init_pose=True) → EXIT
```

这个简化让代码少了一层意图不明的状态管理，也更好测试。

**教训**：在设计状态机时，先想清楚哪些信号的语义是你真正依赖的，哪些是你以为会发生但其实不会。多余的状态保护往往是对系统行为的错误假设的产物。

### 关键设计：「录失败就重录」原则

NPZ 文件只在收到第 2 次 X 键时才写。如果 dataflow 在录制中途被打断（比如按了 A 键 disable），teach-recorder 退出但**不写文件**。

另一个可选设计是"自动保存 buffer，中途退出也写"——但这样写出来的 NPZ 是不完整的轨迹（动作到一半），直接进 `actions/` 目录只会增加混乱。

"录失败就重录"让系统状态永远干净：`actions/` 目录里只有完整的录制结果，不会有"wave_incomplete.npz"之类的噪音。

### 一处文档错误的修正

在实现 teach-recorder 的过程中，发现 `system-architecture-baseline-plan.md §4.6` 里把 speech 字段记录为 `str`（单条），但实际设计需要多条变体（随机选一条播报，增加自然感）。在这次 commit 里同时更正了文档和 `PIPER_HOME_POSE` 的值（从一个旧的占位值改成了初始位的实际坐标）。

**教训**：设计文档中的具体数值（关节角坐标、NPZ schema）容易在实现过程中被调整，但文档不一定同步更新。每次发现文档与代码的偏差，要当场修正，不要留着"以后再说"。

---

## 第五阶段：文档交接（当日，2026-05-11）

### 当时的目标

项目将从 Claude Code 迁移给 Codex 或其他 AI Coding Agent，需要一套完整的"隐性知识"文档。

### 做了什么

- `docs/src/explain/2026-05-handover.md`：完整交接文档，18 个章节，覆盖架构、决策、踩坑、禁忌、进度
- `docs/src/note/2026-05-agent-start-here.md`：10 分钟上手指南，为新 AI 设计
- `docs/src/note/2026-05-development-history.md`：本文件

### 最重要的经验

**文档的真正价值在于"代码之外的隐性知识"**。代码说明了"做了什么"，注释说明了"为什么这段代码这么写"，但有一类知识既不在代码里、也不在注释里：

- 为什么选择 A 而放弃了 B？
- 这个看起来多余的 flag 有什么历史原因？
- 哪些地方是脆弱的、容易被改坏的？
- 这个设计在什么情况下会不够用？

这些只存在于设计对话、踩坑记录和 ADR 里。如果不主动记录，会随着人员流动消失，留下一堆"不知道为什么这样写、但谁都不敢改"的代码。

---

## 演进中的几条横向线索

### 线索一：「文档即设计」

这个项目的特点是**文档先于代码**。每个节点在实现之前都有：

1. plan 文档（接口规范 + 设计动机）
2. learn 文档（依赖的外部 API 认知）
3. 协作规则（告诉 AI 怎么操作）

这种模式让 AI 实现时的"猜测"空间大大缩小，也让每次任务的验收标准更清晰。代价是前期文档工作量较大，但整体来看节省了调试时间。

### 线索二：「限制任务粒度」

每次任务都刻意控制在单节点、200 行以内。这不是懒惰，是对"AI 实习生"定位的工程化落实：

- 粒度小 → 理解要求低 → 出错概率小
- 粒度小 → 每次完成都可以做人工验证
- 粒度小 → 文档对应关系清晰（一个节点对应一份 plan + 一份 explain）

### 线索三：「安全第一」

贯穿所有节点实现的一个主题是"出了问题不能是灾难性的"：

- `safe_shutdown` 先夹爪后机械臂：颠倒顺序会导致夹爪没收回就断电
- `queue_size: 1`：阻塞期间的指令不堆积
- "录失败就重录"：不写不完整的轨迹
- `at_zero`/`at_init_pose` 信号分开：两个位置的语义不混用
- 阻塞操作只用于调试节点，产品节点（dora-piper）必须非阻塞

### 线索四：测试策略的演进

- 第一个节点（piper-teleop）建立了测试模板：纯函数 + mock bringup + smoke test
- 第二个节点（teach-recorder）直接复用这套模板：recorder.py 的核心函数（`parse_speech`、`build_npz_payload`、`resolve_output_path`）全部是纯函数，可以脱离 dora 测试
- 测试的目标不是"覆盖率"，而是"有硬件时不跑、没硬件时也能验证核心逻辑"

---

## 待沉淀的问题

- 两个 piper-control 未解决问题（`set_installation_pos` 是否必须、两个 `disable_arm` 的区别）留给 dora-piper 实现时处理——积累到足够实现时，需要补充 learn 文档
- 随着项目规模扩大，当前单份大 plan 文档（500+ 行）是否需要拆分？目前看不需要，但接口数量翻倍后要重新评估
- 与 AI 协作的"边界扩展"：目前是"实习生"模式（严格单任务、人工验收），未来是否可以放开到"初级工程师"模式（跨任务记忆、主动提建议）？需要更多实践样本再判断

## 相关文档

- `docs/src/explain/2026-05-handover.md` — 完整交接文档
- `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md` — 6 大技术决策原文
- `docs/src/note/2026-05-claude-code-collaboration-rules.md` — 协作方法论原文
- `docs/src/plan/2026-05-system-architecture-baseline-plan.md §8` — Phase 划分原文

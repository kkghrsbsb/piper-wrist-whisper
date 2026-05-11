# piper-wrist-whisper

Piper 6-DOF 机械臂的双模式语音/文本交互系统。基于 dora-rs dataflow,
通过 Web 前端接收用户输入,以两种模式驱动机械臂:

- **LLM 模式**:文本指令 → 关键词匹配(LLM 兜底)→ 预设动作回放
- **VLM 模式**:文本 + 图像 → 远程 Qwen3-VL-8B → 场景描述,**机械臂不动**

决策模型统一走本地服务器的 OpenAI 兼容端点
(`http://10.100.1.93:12368`,模型 `Qwen3-VL-8B`)。

---

## 当前进度

仓库已存在以下验证可用的节点:

| 节点 | 路径 | 作用 |
|---|---|---|
| `rerun-dabai-dc1` | `nodes/rerun-dabai-dc1/` | Orbbec Dabai DC1 摄像头采集,Rerun 实时可视化 |
| `rerun-piper-sim` | `nodes/rerun-piper-sim/` | Piper 真机 → MuJoCo 镜像,关节状态接通,3D 模型加载 |
| `piper-teleop` | `nodes/piper-teleop/` | 手柄遥操调试:`piper-bringup` 管真机使能/回零/初始位/失能,`piper-gamepad` 读手柄发关节增量 |
| `teach-recorder` | `nodes/teach-recorder/` | X 键边沿录制 jointstate,写 `actions/{ACTION_ID}.npz`(供 action-dispatcher 回放) |

`rerun-piper-sim` 内的 `robot_state_publisher.py` 已经使用 piper-control
读取真机关节,这是后续 `dora-piper` 节点写入侧实现的重要参考。

### 关键概念:零位 vs 初始位

- **零位** `ZERO_POSITION = [-1.5708, 0, 0, 0, 0, 0]`:bringup 启动目标 + Y 键 `home_request` 目标,触发后 publish `at_zero`
- **初始位** `INIT_JOINT_POSITION = [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]`:所有预设动作的起止位,X 键 `init_pose_request` 目标,触发后 publish `at_init_pose`

二者是**不同物理位置**,信号也不复用。gamepad 把 `at_zero` 或 `at_init_pose`
任一信号视为"机械臂就绪",允许下发 `joint_action`。

### 手柄按键约定(piper-teleop)

| 键 | 行为 |
|---|---|
| 摇杆 / D-pad / 扳机 | 关节角与夹爪增量(无输入时 `target_q` 对齐当前位) |
| LB / RB | 速度档位切换 |
| Y | `home_request` → bringup 回零位 |
| X | `init_pose_request` → bringup 去初始位(也是 teach-recorder 录制起止信号) |
| A | `disable_request` → bringup 退出(走 `safe_shutdown`) |

### teach-recorder 录制流程

```
ACTION_ID=wave ACTION_SPEECH="好的|你好" dora start dataflow/teleop/teach_record.yml
# 启动 → 机械臂到零位
# 按 X 键 → 到初始位,开始累积 jointstate
# 用户遥操做完一个动作并回到初始位
# 按 X 键 → 到初始位,停止累积,写 actions/wave.npz 并退出
```

NPZ schema:`joints: float32 [N, 7]`、`dt: float32 = 0.02`、`speech: list[str]`。
未收到第 2 次 X 键就退出 dataflow(如按 A 键 disable)不会写文件——"录失败就重录"。

> **NPZ 落地路径**:dora 把每个节点的 CWD 设为 YAML 所在目录,
> `teach_record.yml` 里 `ACTIONS_DIR: ../../actions` 指向 repo 根的 `actions/`,
> 让所有动作 NPZ 集中存放,方便 action-dispatcher 统一加载。
> 命令行可覆盖,例如 `ACTIONS_DIR=/tmp/scratch_actions ACTION_ID=... dora start ...`。

---

## 快速上手

仓库根使用 `uv` workspace 管理,新增节点目录直接被 `nodes/*` glob 自动发现。

```bash
# 同步依赖
uv sync

# 仿真镜像跑一次(需连接 Piper 真机 + MuJoCo)
dora build dataflow/<某个 yml>
dora run dataflow/<某个 yml>
```

具体 dataflow 文件随 Phase 推进逐步建立,见 `docs/src/plan/`。

---

## 文档导航

本项目使用 [my-skills](https://github.com/kkghrsbsb/my-skills) 文档工作流,
所有文档分类存放在 `docs/src/<类型>/`,命名 `YYYY-MM-<主题>-<类型>.md`。

| 想看什么 | 去哪里 |
|---|---|
| 系统整体架构、节点清单、状态机、Phase 划分 | `docs/src/plan/` |
| 关键技术选型与取舍(为什么双模式 / 为什么远程 LLM 等) | `docs/src/adr/` |
| 第三方代码 / 已有代码的理解笔记(如 piper-control API) | `docs/src/learn/` |
| 实施过程中的改动记录 | `docs/src/explain/` |
| 代码审查报告 | `docs/src/review/` |
| 想法、思路、待探索的方向(如 Claude Code 协作规则) | `docs/src/note/` |
| 已被新文档取代的历史版本 | `docs/src/archive/` |

完整目录见 `docs/src/SUMMARY.md`。

---

## 关键约定

- **任何涉及真机控制的代码必须先在仿真验证再由用户上真机手动测试**
- **预设动作轨迹由 teach mode 录制,不由代码生成**(由 `teach-recorder` 写入 `actions/`)
- **物理急停依赖硬件,Web 端 STOP 按钮仅辅助**
- **Web 与 dora 之间的 WebSocket 协议是契约,改动需前后端同步**
- **零位与初始位是两个不同的位置概念,信号 `at_zero` / `at_init_pose` 不互通**

更多协作约定与设计基线参见 `docs/src/plan/` 与 `docs/src/note/`。

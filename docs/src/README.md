# piper-wrist-whisper

Piper 6-DOF 机械臂的双模式语音/文本交互系统。基于 dora-rs dataflow,
通过 Web 前端接收用户输入,以两种模式驱动机械臂:

- **LLM 模式**:文本指令 → 关键词匹配(LLM 兜底)→ 预设动作回放
- **VLM 模式**:文本 + 图像 → 远程 Qwen3-VL-8B → 场景描述,**机械臂不动**

决策模型统一走本地服务器的 OpenAI 兼容端点
(`http://10.100.1.93:12368`,模型 `Qwen3-VL-8B`)。

---

## 当前进度

仓库已存在两个验证可用的节点:

| 节点 | 路径 | 作用 |
|---|---|---|
| `rerun-dabai-dc1` | `nodes/rerun-dabai-dc1/` | Orbbec Dabai DC1 摄像头采集,Rerun 实时可视化 |
| `rerun-piper-sim` | `nodes/rerun-piper-sim/` | Piper 真机 → MuJoCo 镜像,关节状态接通,3D 模型加载 |

`rerun-piper-sim` 内的 `robot_state_publisher.py` 已经使用 piper-control
读取真机关节,这是后续 `dora-piper` 节点写入侧实现的重要参考。

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
- **预设动作轨迹由 teach mode 录制,不由代码生成**
- **物理急停依赖硬件,Web 端 STOP 按钮仅辅助**
- **Web 与 dora 之间的 WebSocket 协议是契约,改动需前后端同步**

更多协作约定与设计基线参见 `docs/src/plan/` 与 `docs/src/note/`。

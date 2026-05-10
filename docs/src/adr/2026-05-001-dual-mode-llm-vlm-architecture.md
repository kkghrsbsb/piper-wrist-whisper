# ADR-001:双模式架构 + 远程 LLM + Web 前端的整体技术选型

- **状态**:已采纳
- **日期**:2026-05
- **决策范围**:系统整体形态、决策模型部署方式、前端形态、输入交互模式

本 ADR 记录项目从最初的 VLM 一体化构想,演进到当前"双模式 + 远程 LLM + Web 前端 + PTT 语音"的取舍过程。具体接口规范见 `docs/src/plan/2026-05-system-architecture-baseline-plan.md`。

---

## 决策一:运行模式分裂为 LLM 模式和 VLM 模式

### 背景

最初设想是单一的 VLA 形态:VLM 看图 + 听音 → 输出动作指令 → 机械臂执行。

### 问题

- 摄像头眼在手上,机械臂动起来画面就糊,VLM 必须配合 frame-gate + idle 检测才能用,串联调试成本极高
- 当前预设动作集合有限,场景理解的边际收益低
- 用户对"机器人会不会动"的预期不清晰,安全风险

### 决策

把系统切成两个互不依赖的运行模式,由用户在 Web UI 上显式切换:

| 模式 | 输入 | 决策器 | 输出 | 机械臂 |
|---|---|---|---|---|
| **LLM** | 文本 + 语音 | rule-matcher → llm-client (text) | 预设动作 + 预设语音 | 动 |
| **VLM** | 文本 + 语音 + 图像 | llm-client (text + image) | 场景描述文字 + 同文字 TTS | **不动** |

VLM 模式的定位是"探索/感知"模式,只描述画面,不做任何决策动作。

### 后果

- 用户对系统行为有明确预期,安全
- VLM 模式不需要 frame-gate(机械臂不动,画面不会糊)
- 两条决策路径完全独立,可分别迭代,互不干扰
- `frame-gate` 节点从架构里彻底删除,不留 stub

---

## 决策二:决策模型走远程 OpenAI 兼容端点

### 背景

最初构想是在边缘机本地跑 Qwen2.5-1.5B 做轻量决策。

### 问题

- 模型 Qwen3-VL-8B 已经在本地服务器(`http://10.100.1.93:12368`)以 OpenAI 兼容端点跑起来,本机不需要再装一份
- 8B 模型本机推理对显存要求高,边缘机扛不住
- LLM 模式和 VLM 模式如果用不同模型,接口和基础设施会分裂

### 决策

LLM 模式与 VLM 模式共用同一个远程 endpoint 的同一个模型 Qwen3-VL-8B:

- LLM 模式 payload 只含 text,system prompt 强制 JSON 输出 `{"action_id": "..."}`,白名单校验
- VLM 模式 payload 含 text + image_url,自由文本输出场景描述

### 后果

- 两条决策路径合并到单一 `llm-client` 节点,内部按事件类型分支
- 集中部署便于统一升级模型
- 引入新依赖:远程服务可达性。`safety-monitor` 加 LLM 不可达告警条件,降级时 `rule-matcher` 仍可用,不进 FAULT
- 远程调用延迟成为新风险:VLM 模式 1-3s 是预期,前端需立即显示"思考中"指示

---

## 决策三:Web 前端 + 全屏 Chat,不做 Control UI 与浮窗

### 背景

最初设想是顶栏切换的 Chat UI / Control UI 双面板,加可拖拽分割线,加浮窗形态等富交互。

### 问题

- 实际可控功能很少(模式切换、TTS toggle、紧急停止、回零),不需要 Control 面板承载
- 浮窗 + 主面板的状态管理复杂,边角 case 多
- iframe 嵌入 rerun 会让前端调试困难(DevTools 不能进 iframe、postMessage 跨域配置麻烦)

### 决策

- **localhost:3000**:全屏 Chat 页面,顶栏放模式切换器和 TTS toggle,底栏放文本输入和 PTT 状态指示,右下角悬浮一个红色 STOP 按钮
- **localhost:9090**:rerun web viewer 独立标签页,前端不嵌入
- 不做 Control UI、不做浮窗

### 后果

- 前端代码量大幅减少,组件清单稳定
- 用户场景:左半屏 rerun,右半屏 Chat
- rerun 高频图像数据不挤占 Web WebSocket 带宽
- Web 端 STOP 仅辅助手段,**真正安全依赖硬件急停**——这一点必须在文档和部署时反复强调

---

## 决策四:语音输入用 PTT(S 键),不用持续 VAD

### 背景

最初设想是 `dora-microphone` 持续采音 + `dora-vad` 自动断句 + `dora-distil-whisper` 转录。

### 问题

- 机械臂电机噪声、环境噪声、键盘敲击声会被 whisper 转出乱码,然后乱码被 rule-matcher 模糊匹配命中,误触发动作
- 持续监听对用户也不友好,不知道什么时候在录、什么时候不在
- 文本和语音同时存在时的优先级问题没有干净答案

### 决策

- 浏览器 `MediaRecorder` 在用户按住 S 键时才录音 → 松开发送 → web-server 转录
- Chat 页面有"激活"概念:用户最后操作过 Chat 元素 → activated;失焦 → deactivated;deactivated 时 S 键不响应
- 文本输入框聚焦时 S 键不触发 PTT,避开打字冲突

### 后果

- 误触发概率几乎为零
- 不需要 dora-microphone、dora-vad、dora-distil-whisper 三个 dora 节点
- 音频处理整体收进 web-server(faster-whisper),减少节点间传递

---

## 决策五:STT 收进 web-server,不做独立 dora 节点

### 背景

dora-hub 提供了 `dora-microphone + dora-vad + dora-distil-whisper` 的现成组合。

### 问题

- 决策四确定用 PTT 后,音频源已经从浏览器 MediaRecorder 来,而非麦克风采集节点
- web-server 已经持有 WebSocket 接收的音频流
- 再 publish 给独立 STT 节点会增加一跳延迟

### 决策

`web-server` 节点内部直接调用 `faster-whisper`:
- 浏览器流式发 webm/opus chunk
- web-server 解码 → 16kHz PCM → faster-whisper 转录
- 直接 publish `voice_text` event 给 input-router

### 后果

- web-server 进程变重,需要加载 whisper 模型(一次性 ~10s 启动延迟)
- 节点数减少,dataflow 简化
- dora 端只看到一个干净的 `voice_text` topic
- Phase 2 才接入,Phase 0/1 web-server 暂不含 STT

---

## 决策六:`dora-piper` 节点自研,不用 dora-hub 现成版

### 背景

dora-hub 提供 `dora-piper` 节点。

### 问题

- 仓库已有 `rerun-piper-sim/robot_state_publisher.py` 在用 piper-control,内部约定与 dora-hub 版本可能不一致
- 控制写入侧需要细粒度控制(预设动作回放是 20ms 周期逐帧 publish),需配合 `command_joint_positions` + `set_arm_mode(speed=...)` 而非 `BuiltinJointPositionController.move_to_position`(后者是阻塞调用)
- 失能流程有顺序约束(先夹爪后机械臂),需要明确实现

### 决策

`dora-piper` 节点本仓库自研,基于 piper-control 接口
(详见 `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md`)。
读取侧参考 `rerun-piper-sim/robot_state_publisher.py`。

### 后果

- 接口由本项目掌握,与 `arm-supervisor` 状态机配合更紧密
- 失能/使能/teach mode 切换的细节可控
- 待探索问题(`set_installation_pos` 是否必须、两个 `disable_arm` 差异)在实现时一并解决

---

## 不再做的事

- **不做 frame-gate**:VLM 模式机械臂不动,画面不会糊;LLM 模式不需要图像
- **不做 Phase 4 image lane 预留**:不留半成品 stub,需要时再加干净的实现
- **不做 dora-microphone / dora-vad / dora-distil-whisper**:PTT 模式下都不需要
- **不做 Control UI 面板**:状态展示统一进 rerun,操作集中在 Chat 顶栏与右下 STOP
- **不做 EXECUTING 期间的 stop 打断机制**:预设动作由 teach mode 录制时已保证安全,单个动作 < 30s,用户等动作完成再发新指令是可接受的交互成本。`stop` 走正常 action_id 路径(对应空轨迹或回 home 的预设动作),不绕过状态屏蔽。supervisor 与 dispatcher 的设计因此简化

---

## 相关文档

- `docs/src/plan/2026-05-system-architecture-baseline-plan.md` — 接口规范、状态机、Phase 划分
- `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md` — piper-control 接口认知
- `docs/src/note/2026-05-claude-code-collaboration-rules.md` — 协作规则

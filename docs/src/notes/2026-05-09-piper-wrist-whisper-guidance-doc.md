# Piper Dual-Mode Voice/Text Robot — 项目指导文档 (v3)

> 本文档作为后续 Claude Code 协作开发与人工讨论的共同基线。所有节点接口、状态机定义、目录结构、风险清单都以此为准。本期交付包含 LLM 模式(决策做动作)与 VLM 模式(只描述场景),用户通过 Web 端切换。

---

## 0. 一句话定位

基于 dora-rs 中间件,通过 Web 前端接收文本/语音指令,以 **双模式**(LLM 决策做动作 / VLM 只描述场景)驱动 Agilex Piper 6 自由度机械臂,具备真机/仿真同步与 rerun 可视化。决策模型统一走本地服务器的 Qwen3-VL-8B(OpenAI 兼容端点)。

---

## 1. 设计取舍

### 双模式的产品形态
| 模式 | 输入 | 决策器 | 输出 | 机械臂 |
|---|---|---|---|---|
| **LLM** | 文本 + 语音 | rule-matcher → llm-client (text) | 预设动作 + 预设语音 | 动 |
| **VLM** | 文本 + 语音 + 图像 | llm-client (text + image) | 场景描述文字 + 同文字 TTS | **不动** |

VLM 模式不做决策动作,因为:
- 摄像头眼在手上,动起来画面糊,语义不可靠
- VLM 模式定位是"探索/感知"模式,先让用户看 VLM 怎么理解画面
- 这个划分让用户对系统行为有明确预期,安全

模式由用户在 Web UI 上切换,后端 `arm-supervisor` 维护当前 mode,`mode-router` 根据 mode 把 user_text 路由到对应通道。

### 为什么用远程 LLM endpoint
- 模型 Qwen3-VL-8B 已经在本地服务器跑起来(`http://10.100.1.93:12368/v1/chat/completions`)
- 8B 模型本机推理对显存要求高,边缘机扛不住
- OpenAI 协议天然支持 image_url 字段,LLM 模式和 VLM 模式共用一个 endpoint,只是 payload 不同
- 集中部署便于统一升级模型

### 为什么 STT 不做 dora 节点
- web-server 节点已经持有音频流(浏览器 PTT → WebSocket → web-server)
- 在 web-server 进程内直接调 `faster-whisper` 比再 publish 给 dora 节点延迟更低
- 减少节点数量,简化 dataflow
- 代价:web-server 进程变重,需要加载 whisper 模型;一次性成本

### 为什么前端只做 Chat 页 + 独立 rerun 标签页
- iframe 嵌入 rerun 会导致前端调试困难(DevTools 看不到 iframe 内部、postMessage 复杂)
- 两个标签页各自独立,前端调试干净
- 用户场景:左半屏 rerun,右半屏 Chat;调试场景同样
- 前端不做 Control UI、不做浮窗,localhost:3000 是全屏 Chat 页面

### 不做的事
- 不做 frame-gate 节点(VLM 模式机械臂不动,画面不会糊)
- 不做 Control UI 面板(状态展示统一进 rerun viewer 或 Chat 顶栏)
- 不做物理急停的 Web 替代(物理安全依赖硬件,Web STOP 仅辅助)

---

## 2. 整体数据流

```
Browser localhost:3000 (Chat 页) ◄──── WebSocket ────► web-server ──► input-router
                                                          │              │
Browser localhost:9090 (rerun)   ◄────────────────────────┘              ▼
                                                                    mode-router
                                                                    │       │
                                                          mode=LLM ◄┘       └► mode=VLM
                                                                ▼               ▼
                                                        rule-matcher        camera ─┐
                                                            │                       ▼
                                                            ▼                   llm-client
                                                        llm-client ──── HTTP ────► Qwen3-VL-8B
                                                            │                       │
                                                            ▼                       │
                                                    action-dispatcher               │
                                                            │                       │
                                                            ├─► dora-piper          │
                                                            │     │                 │
                                                            │     ▼                 │
                                                            │   piper-state         │
                                                            │     │                 │
                                                            ├─► kokoro-tts ◄────────┤
                                                            │                       │
                                                            └─► mujoco-sim          │
                                                                                    │
                                                            arm-supervisor ◄────────┘
                                                                  │
                                                                  └─► safety-monitor

camera ─────────────────► dora-rerun (port 9090)
piper-state ─────────────► dora-rerun
scene_desc ──────────────► dora-rerun (via web-server 路由,可选)
```

详细的层级版本见架构图 SVG。

---

## 3. 节点清单

| 节点 ID | 来源 | 作用 |
|---|---|---|
| `camera` | dora-hub `dora-pyorbbecksdk` 或 `opencv-video-capture` | RGB 帧;送 rerun 与 VLM 模式 |
| `piper-state` | 本仓库自研 dora-piper 的 publish 部分 | 周期 publish jointstate |
| `dora-piper` | **本仓库自研** | piper SDK 封装,CAN 控制 |
| `web-server` | **本仓库自研** | FastAPI + WebSocket + STT,Web 前端唯一桥 |
| `input-router` | **本仓库自研** | 合并 voice/text → user_text |
| `mode-router` | **本仓库自研** | 按 mode 路由 user_text 到 LLM/VLM 通道 |
| `rule-matcher` | **本仓库自研** | 关键词→action_id;仅 LLM 模式 |
| `llm-client` | **本仓库自研** | 远程 Qwen3-VL-8B HTTP 客户端,处理 text/text+image 两种请求 |
| `action-dispatcher` | **本仓库自研** | 加载预设动作轨迹回放;仅 LLM 模式 |
| `arm-supervisor` | **本仓库自研** | FSM + mode 状态 |
| `safety-monitor` | **本仓库自研** | 异常监控 |
| `mujoco-sim` | **本仓库自研** | 仿真同步 |
| `kokoro-tts` | dora-hub | 文本转语音 |
| `dora-pyaudio` | dora-hub | 播放 TTS 输出 |
| `dora-rerun` | dora-hub | 可视化(独立 9090 端口) |

**已删除/不使用的节点**(相对前几版)
- ~~`frame-gate`~~ — 不做 VLM 时机械臂动的场景,无需 idle 检测
- ~~`dora-microphone`~~ — 音频从浏览器 MediaRecorder 来,不通过 dora 节点
- ~~`dora-vad`~~ — PTT 模式下用户控制录音起止,VAD 不必要
- ~~`dora-distil-whisper`~~ — STT 在 web-server 内做
- ~~`text-input`~~ — 文本从前端来,不再需要 stdin 节点

---

## 4. 状态机定义(arm-supervisor)

状态机有两个正交维度:

**维度 1:执行状态(主要在 LLM 模式有意义)**
```
       enable_fun OK
BOOT ───────────────► HOMING ──── 到达零位 ────► READY
  │                     │                          │
  │ enable_fun fail     │ 超时                     │ 收到 user_text (LLM 模式)
  ▼                     ▼                          ▼
FAULT ◄──── safety alarm ◄────────────────── THINKING
  ▲                                                │
  │                                                │ 决策完成
  │ 任意节点崩溃 / CAN 断连                        ▼
  └──────────────────────────────────────── EXECUTING
                                                   │
                                                   │ 最后一帧 = home pose
                                                   ▼
                                                 READY
```

**维度 2:模式**
- `LLM`:执行状态机如上正常运转
- `VLM`:状态机长期停在 READY,user_text 不触发 THINKING/EXECUTING,而是触发 llm-client 的 vision 请求

| 状态 | 进入条件 | 退出条件 | 期间限制 |
|---|---|---|---|
| BOOT | dataflow 启动 | dora-piper 使能成功 | 屏蔽所有 user_text |
| HOMING | BOOT 完成 | 关节角接近 home pose | 屏蔽所有 user_text |
| READY | HOMING / EXECUTING 完成 | LLM 模式下收到 user_text | 接受指令 |
| THINKING | rule-matcher 收到 text | 输出 action_id 或 unknown | 屏蔽新 user_text |
| EXECUTING | dispatcher 拿到 action_id 且 supervisor 授权 | 轨迹回放完成 / 超时 / 异常 | 屏蔽 user_text |
| FAULT | 任意 alarm | 人工恢复 | 立即失能或回零 |

**模式切换规则**
- VLM → LLM:任意时刻可切换,supervisor mode 立刻更新,后续 user_text 进 LLM 通道
- LLM → VLM:仅在 READY 状态可切换;EXECUTING 期间切换请求被拒绝,等动作完再切

**关键约束**
- 所有预设动作的轨迹文件最后一帧 **必须是 home pose**,EXECUTING → READY 自然收敛
- VLM 模式下机械臂保持在 home pose,不响应任何 action 类指令(除非用户切回 LLM 模式)

---

## 5. 自研节点接口规范

### 5.1 web-server

**职责**
1. 起 FastAPI server 在 `0.0.0.0:3000`,serve 前端 React 静态文件
2. 起 WebSocket endpoint `/ws`,与浏览器双向通信
3. 内部加载 `faster-whisper` 模型(Phase 2 启用),把浏览器送来的 webm/opus 音频解码 → 16kHz PCM → 转录为文字
4. 把 web 端事件转换为 dora event publish
5. 把 dora event 转发给 web 端

**dora 输入**
- `state: from arm-supervisor/state`
- `mode: from arm-supervisor/mode`
- `scene_desc: from llm-client/scene_desc`(VLM 模式输出)
- `dispatch_speech: from action-dispatcher/speech_text`(LLM 模式输出,转发到前端文字框)
- `safety_alarm: from safety-monitor/alarm`

**dora 输出**
- `typed_text: pa.Array<str>` 用户在 Chat 文本框打字回车后发出
- `voice_text: pa.Array<str>` PTT 录音转录后发出
- `mode_request: pa.Array<str>` "LLM" 或 "VLM",用户切换模式时
- `tts_enable: pa.Array<bool>` 用户切换"输出语音"开关
- `estop_request: pa.Array<bool>` 紧急停止按钮按下
- `home_request: pa.Array<bool>` 回零按钮按下

**WebSocket 协议(命名空间清晰)**

Web → Server (command 类):
```json
{"type": "text_input", "text": "你好"}
{"type": "audio_chunk", "data": "<base64 webm/opus>"}
{"type": "audio_end"}                    // PTT 释放
{"type": "mode_set", "mode": "VLM"}
{"type": "tts_enable", "enabled": false}
{"type": "estop"}
{"type": "home"}
```

Server → Web (event 类):
```json
{"type": "state", "state": "READY"}
{"type": "mode", "mode": "LLM"}
{"type": "user_text_echo", "text": "回去", "source": "voice"}
{"type": "system_text", "text": "好的我回原位"}     // dispatcher speech 或 vlm scene_desc
{"type": "alarm", "level": "warn", "msg": "LLM endpoint 不可达"}
```

**STT 实现要点(Phase 2 启用)**
- `faster-whisper` 中文模型 `Systran/faster-whisper-small`,GPU 可用就 GPU,否则 CPU
- 浏览器 `MediaRecorder` 用 webm/opus 流式录音,每 250ms 发一个 chunk
- 用户松开 S 键 → 浏览器发 `audio_end` → web-server 拼接所有 chunk → ffmpeg 解码 PCM → whisper 转录 → publish `voice_text`
- 整条路径延迟 200-500ms,可接受

### 5.2 input-router

**输入**
- `voice_text: from web-server/voice_text`
- `typed_text: from web-server/typed_text`

**输出**
- `user_text: pa.Array<str>` 统一下发,metadata.source 标 "voice"/"text"

**实现**:简单合并,文本去空白,不做过滤。

### 5.3 mode-router

**输入**
- `user_text: from input-router/user_text`
- `mode: from arm-supervisor/mode`

**输出**
- `llm_text: pa.Array<str>` mode==LLM 时
- `vlm_text: pa.Array<str>` mode==VLM 时

**实现**:维护一个本地 mode 缓存,收到 user_text 时按当前缓存路由。Mode 信号变更时只更新缓存。

### 5.4 rule-matcher

**输入**
- `llm_text: from mode-router/llm_text`
- `state: from arm-supervisor/state`(READY 才处理,EXECUTING 期间 drop)

**输出**
- `action_id: pa.Array<str>` 命中时
- `no_match: pa.Array<str>` 未命中时,转给 llm-client

**实现**
```yaml
# rules.yaml
ACTIONS:
  wave:  { keywords: [挥手, 打招呼, 你好, hi, hello] }
  nod:   { keywords: [点头, 同意, 好的] }
  home:  { keywords: [回去, 复位, 休息, 回原位] }
  stop:  { keywords: [停, 停下, 别动] }   # 高优先级,绕过状态屏蔽
```
- `rapidfuzz.fuzz.partial_ratio >= 75` 容错
- stop 类指令最高优先级,EXECUTING 状态下也通过

### 5.5 llm-client

**职责**:统一处理两种远程 LLM 请求,LLM 模式做动作分类、VLM 模式做场景描述。

**输入**
- `text_query: from rule-matcher/no_match`(LLM 模式,纯文本)
- `vision_query: from mode-router/vlm_text`(VLM 模式,文本)
- `image: from camera/image`(VLM 模式辅助,缓存最新一帧)

**输出**
- `action_id: pa.Array<str>` LLM 模式输出
- `scene_desc: pa.Array<str>` VLM 模式输出
- `alarm: pa.Array<str>` HTTP 失败时

**配置**
```yaml
env:
  LLM_ENDPOINT: "http://10.100.1.93:12368/v1/chat/completions"
  LLM_MODEL: "/model/Qwen3-VL-8B"
  LLM_TIMEOUT_SEC: "5"
  LLM_TEXT_MAX_TOKENS: "10"
  LLM_VISION_MAX_TOKENS: "200"
  LLM_TEMPERATURE: "0.3"
```

**内部分支**
```python
def on_event(event):
    if event.id == "text_query":
        payload = build_text_payload(event.value)
        resp = http_post(payload)
        action_id = parse_action(resp)
        node.send_output("action_id", action_id)
    elif event.id == "vision_query":
        latest_image = self.image_cache  # 最新一帧
        payload = build_vision_payload(event.value, latest_image)
        resp = http_post(payload)
        scene_desc = parse_text(resp)
        node.send_output("scene_desc", scene_desc)
    elif event.id == "image":
        self.image_cache = event.value   # 仅缓存,不触发请求
```

**Text Query Prompt**
```
SYSTEM: 你是机械臂动作分类器。用户说一句话,你只输出 JSON: {"action_id": "..."}
可选 action_id: wave, nod, home, stop, unknown
规则: 无法确定时输出 unknown。不要输出任何其他内容。

USER: <user_text>
```
- `response_format: {"type": "json_object"}`
- max_tokens=10
- 解析后白名单校验,不在则降级 unknown

**Vision Query Prompt**
```
SYSTEM: 你是一个视觉描述助手。用户提问,你看图片,用一句话(不超过 30 字)
描述画面内容,回答用户。直接说,不要用"我看到"等开头。

USER: [image]
请描述: <user_text>
```
- 不要求 JSON 输出,自由文本
- max_tokens=200

**异常处理**
- HTTP 超时/连接失败 → 输出 `unknown`(text)或"无法获取画面信息"(vision),同时发 alarm
- JSON 解析失败 → 同上

### 5.6 action-dispatcher

**输入**
- `action_id: from rule-matcher/action_id` 或 `llm-client/action_id`
- `grant: from arm-supervisor/grant`
- `tick: dora/timer/millis/20`

**输出**
- `joint_action: pa.Array<float32>[7]` to dora-piper
- `speech_text: pa.Array<str>` to kokoro-tts(决策瞬间反馈,如"好的我挥手")
- `done: pa.Array<str>` 轨迹回放完成

**轨迹格式**:`actions/{action_id}.npz`
- `joints: float32 [N, 7]` 关节角(rad)+ 夹爪(mm)
- `dt: float32` 帧间隔
- `speech: str` 预设播报文本

**回放逻辑**
1. 收到 action_id → 向 supervisor 申请 grant
2. 拿到 grant → 加载 npz → 立即 publish speech_text → 按 tick 逐帧 publish joint_action
3. 期间 drop 新进来的 action_id
4. 全部 publish 完成 + piper-state 显示真机到位(误差 < 0.02 rad)→ publish done
5. 总超时 30s

### 5.7 arm-supervisor

**输入**
- `jointstate: from piper-state`
- `done: from action-dispatcher`
- `alarm: from safety-monitor`
- `request: from action-dispatcher`(grant 申请)
- `mode_request: from web-server/mode_request`
- `estop_request: from web-server/estop_request`
- `home_request: from web-server/home_request`
- `tts_enable: from web-server/tts_enable`

**输出**
- `state: pa.Array<str>`(BOOT/HOMING/READY/THINKING/EXECUTING/FAULT)
- `mode: pa.Array<str>`(LLM/VLM)
- `grant: pa.Array<bool>`
- `home_cmd: pa.Array<float32>[7]`
- `tts_gate: pa.Array<bool>` 转给 kokoro-tts 控制是否实际播放

**实现要点**
- BOOT 状态检查 piper 使能位,5s 超时进 FAULT
- HOMING 期间发 home pose 关节指令
- estop_request 收到 → 立即进 FAULT 并发 disable 给 dora-piper
- mode 切换:VLM→LLM 任意时刻;LLM→VLM 仅 READY 时
- tts_enable=False 时,把 tts_gate 置 False;kokoro-tts 节点订阅 tts_gate,gate=False 时收到的 text 直接 drop

### 5.8 safety-monitor

**输入**
- `jointstate: from piper-state`
- `state: from arm-supervisor`
- `done: from action-dispatcher`
- `llm_alarm: from llm-client`

**输出**
- `alarm: pa.Array<str>`

**告警条件**
- jointstate 时戳 > 200ms 没更新 → CAN 断连
- 任意关节角越限 ±0.05 rad → 关节越界
- EXECUTING 持续 > 30s → 动作卡死
- 任意节点 ERROR event → 节点崩溃
- llm_alarm 连续 3 次 → LLM 不可达,降级:rule-matcher 仍可用,no_match 直接出 unknown,不进 FAULT

### 5.9 dora-piper(自研)

**输入**
- `joint_action: pa.Array<float32>[7]` 关节角目标
- `disable_request: pa.Array<bool>` 安全失能
- `enable_request: pa.Array<bool>` 重新使能

**输出**
- `jointstate: pa.Array<float32>[7]` 周期 20ms publish 当前关节角

**实现要点(待用户提供 piper-control 参考接口文档后细化)**
- 启动时调用 piper SDK 使能(参考 piper_control_demo)
- 收到 joint_action → 调用 `MotionCtrl_2`,速度参数 50%
- 周期采样 jointstate publish
- 关节速度上限 0.5 rad/s(出厂 1.0,先保守)
- TEACH_MODE 环境变量:进入示教模式,电机零阻尼,只 publish jointstate 不接受 joint_action

---

## 6. 前端(localhost:3000)

### 6.1 页面布局

全屏单页,无浮窗:

```
┌──────────────────────────────────────────────┐
│ [Mode: LLM | VLM]   [TTS: on]    State: READY│  ← 顶栏
├──────────────────────────────────────────────┤
│                                              │
│      消息列表(用户 + 系统)                  │
│      ...                                     │
│                                              │
├──────────────────────────────────────────────┤
│ [文本输入框                  ] [发送]         │  ← 底栏
│ S 键 PTT(页面激活时):  ●已激活                │
└──────────────────────────────────────────────┘
                                          ┌────┐
                                          │STOP│  ← 右下悬浮急停
                                          └────┘
```

### 6.2 交互细节

- **页面激活检测**:用户点击页面任意位置 → activated;`document.hidden` 或失焦 → deactivated;deactivated 时 S 键不响应
- **S 键 PTT**:keydown S → 开始 MediaRecorder + 顶栏显示"录音中"红点;keyup S → 停止录音、发 audio_end
- **文本输入**:输入框聚焦时 S 键不触发 PTT(避开冲突);Enter 发送
- **模式切换**:顶栏切换器,LLM ↔ VLM,后端拒绝时(EXECUTING 中切 VLM)前端弹 toast
- **TTS 开关**:顶栏 toggle,影响后端 tts_gate
- **STOP 按钮**:右下悬浮红色按钮,点击 → 发 estop_request;**仅辅助手段,真正安全靠硬件急停**

### 6.3 技术栈

- React + Vite
- WebSocket 客户端(原生 API,断线自动重连)
- shadcn/ui + Tailwind 做基础组件
- 消息 schema 用 zod 校验,与后端 Pydantic 模型同源(可手动同步,Phase 1 先手动)
- 不引入状态管理库,React useState/useReducer 够用

---

## 7. 目录结构

```
piper-voice-stack/
├── README.md
├── PROJECT_GUIDE.md
├── pyproject.toml
├── dataflow/
│   ├── text_only.yml         # Phase 1 最小,前端纯文本
│   ├── full.yml              # Phase 3 完整,含语音、LLM 模式
│   ├── full_dev.yml          # 开发版,本地路径 build,带 mujoco
│   └── teach_record.yml      # 录制示教轨迹
├── nodes/
│   ├── web_server/
│   │   ├── pyproject.toml
│   │   ├── web_server/
│   │   │   ├── __init__.py
│   │   │   ├── main.py           # dora 节点入口
│   │   │   ├── server.py         # FastAPI app
│   │   │   ├── ws_handler.py     # WebSocket 处理
│   │   │   └── stt.py            # faster-whisper 封装(Phase 2)
│   │   └── README.md
│   ├── input_router/
│   ├── mode_router/
│   ├── rule_matcher/
│   │   ├── pyproject.toml
│   │   ├── rule_matcher/
│   │   │   ├── __init__.py
│   │   │   └── main.py
│   │   └── rules.yaml
│   ├── llm_client/
│   ├── action_dispatcher/
│   ├── arm_supervisor/
│   ├── safety_monitor/
│   ├── dora_piper/
│   │   ├── pyproject.toml
│   │   ├── dora_piper/
│   │   │   ├── __init__.py
│   │   │   └── main.py           # 等参考接口文档后实现
│   │   └── README.md
│   └── mujoco_sim/
├── webui/                        # 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── tsconfig.json
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── Topbar.tsx
│   │   │   ├── MessageList.tsx
│   │   │   ├── InputBar.tsx
│   │   │   ├── EstopButton.tsx
│   │   │   └── ActiveIndicator.tsx
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   ├── usePTT.ts
│   │   │   └── usePageActive.ts
│   │   └── types/
│   │       └── messages.ts       # 与后端 Pydantic 同源
│   └── dist/                     # 构建产物,由 web-server 节点 serve
├── actions/
│   ├── wave.npz
│   ├── nod.npz
│   ├── home.npz
│   └── stop.npz
├── assets/
│   ├── piper.urdf
│   └── meshes/
├── scripts/
│   ├── record_teach.py
│   ├── replay_check.py
│   ├── llm_smoke_test.py         # 单独测远程 LLM 端点,text + vision 两种
│   └── home_pose.json
└── tests/
    ├── test_rule_matcher.py
    ├── test_input_router.py
    ├── test_mode_router.py
    ├── test_llm_client_mock.py
    └── test_supervisor_fsm.py
```

---

## 8. 配置约定

```yaml
# dora dataflow 关键参数
queue_size: 1
RERUN_MEMORY_LIMIT: "15%"

# LLM endpoint
LLM_ENDPOINT: "http://10.100.1.93:12368/v1/chat/completions"
LLM_MODEL: "/model/Qwen3-VL-8B"
LLM_TIMEOUT_SEC: "5"

# Web server
WEB_HOST: "0.0.0.0"
WEB_PORT: "3000"
WHISPER_MODEL: "Systran/faster-whisper-small"
WHISPER_DEVICE: "auto"   # auto=GPU 优先 CPU 兜底

# rerun
RERUN_PORT: "9090"

# piper 安全
PIPER_VEL_LIMIT: "0.5"
PIPER_HOME_POSE: "[0, 1.0, -1.2, 0, -0.6, 0, 0]"
```

---

## 9. 风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| CAN 断连静默 | 高 | safety-monitor jointstate 时戳超时检测 |
| WebSocket 断连 | 高 | 前端自动重连;后端按 ws 断开为"输入暂停",不进 FAULT |
| LLM endpoint 不可达 | 高 | httpx 超时 + safety-monitor 降级,rule-matcher 仍工作 |
| LLM 输出不符合白名单 | 高 | response_format JSON + 白名单 + 不符则 unknown |
| Web STOP 误以为是安全保证 | 高 | 文档明确写"仅辅助";部署时强调硬件急停 |
| 模式切换中途的状态机不一致 | 高 | LLM→VLM 仅 READY 时;切换被拒时前端弹 toast |
| 预设动作之间冲突 | 中 | dispatcher 在执行中拒绝新 action_id |
| dora 节点崩溃静默 | 中 | 每个自研节点 try/except 包 main,ERROR 路由 safety-monitor |
| 远程 LLM 延迟尖峰(VLM 模式 1-3s) | 中 | 前端立即显示"思考中"指示;text 模式 max_tokens=10 控制 |
| PTT 漏键(用户松开 S 但浏览器没收到 keyup) | 中 | 前端做 5s 安全超时强制结束录音 |
| Whisper 模型加载慢 | 低 | 首次启动加载 small 模型 ~10s;web-server 启动后才允许前端连接 |
| rerun 内存泄露 | 低 | RERUN_MEMORY_LIMIT 调小 |

---

## 10. 开发节奏

### Phase 0:前端骨架 + web-server 桥接(无 dora 业务逻辑)
1. 搭仓库目录,空 dora dataflow 跑通
2. 写 web-server 节点的 FastAPI + WebSocket 部分(暂不含 STT)
3. 写前端 React 骨架:消息列表、文本输入、模式切换器、TTS toggle、STOP 按钮、激活检测
4. 联调:浏览器打字 → web-server 收到 → echo 回前端
5. **此 Phase 结束:能在 localhost:3000 跟 web-server "聊天",但不接任何决策**

### Phase 1:打通 LLM 模式文本闭环(home / wave 两个动作,无真机)
1. 写 input-router、mode-router(暂只支持 LLM 模式)
2. 写 rule-matcher 支持 home 和 wave
3. 写 arm-supervisor 最小版本(BOOT/READY/EXECUTING)
4. 写 action-dispatcher,只支持 home(发 home pose 一帧)
5. 写 mujoco-sim 节点
6. 联调:前端打"回去" → mujoco 里看见机械臂动 → 前端收到 system_text "好的我回原位"
7. **此 Phase 结束:仿真闭环跑通**

### Phase 2:Web 端语音输入 + 真机
1. 用户提供 piper-control 参考接口 → 实现 dora-piper 节点
2. real arm 替换 mujoco-sim 联调
3. teach_record dataflow 跑通,录 wave / nod / home / stop 四个动作
4. web-server 加 faster-whisper STT 模块
5. 前端 PTT 实现:S 键监听 + MediaRecorder + audio_chunk 流式传输
6. 联调:按住 S 说"挥手" → 看见机械臂挥手

### Phase 3:LLM fallback + VLM 模式 + 安全监控
1. 写 llm-client 节点,先实现 text query
2. 用 llm_smoke_test.py 验证远程端点
3. 接入 rule-matcher 的 no_match → llm-client → action-dispatcher
4. 模糊指令测试("举举手""向前看")
5. 实现 llm-client 的 vision query 分支,接入 camera 缓存
6. 前端模式切换器对接,VLM 模式下提问看图
7. 写 safety-monitor,测试 LLM 不可达、CAN 断连、关节越限

---

## 11. 与 Claude Code 协作的注意事项

- **改动范围明确**:每次只让 Claude Code 实现一个节点或一个组件
- **接口先写测试**:rule-matcher、input-router、mode-router 等纯逻辑节点先写 pytest
- **远程 LLM 调用要 mock**:llm-client 单元测试用 httpx mock,集成测试用 llm_smoke_test.py
- **真机 vs 仿真**:涉及 dora-piper 的代码,先在 full_dev.yml 用 mujoco-sim 验证再上真机
- **预设动作轨迹**:Claude Code 不要自己生成 npz,所有轨迹必须 teach mode 录制
- **dora-piper 节点等用户提供参考文档**:在用户给出 piper-control 接口前,Claude Code 不要自行实现这个节点,可以用 mock 节点占位
- **前端不引入复杂状态管理**:React useState 够用,Claude Code 不要主动加 Redux/Zustand
- **WebSocket 协议是契约**:前后端共同遵守 §5.1 的 schema,改动需双向同步

---

## 12. 已知遗留问题

1. kokoro-tts 中文音色质量待验证,可能换 TTS
2. mujoco-sim 节点的 URDF 加载与关节映射,沿用 piper-vision-demo 已有代码,确认 mujoco 版本
3. teach mode 下 piper 完全无阻尼,record_teach.py 需做关节角范围检查
4. EXECUTING 期间用户喊 "停" 应该立即打断 —— stop 类指令绕过状态屏蔽,但具体打断机制(立即失能/当前帧停止/平滑减速)需在 dispatcher 中明确
5. 远程 LLM endpoint 是否需要鉴权?当前样例无,假设内网信任
6. 双输入(打字 + PTT)在 100ms 内同时到达 input-router 时的优先级:当前先到先得
7. Phase 2 之前 dora-piper 节点的实现,等用户给参考接口文档
8. WebSocket 断开期间前端缓存的输入(用户在断网时打字)如何处理?当前丢弃
9. faster-whisper 模型大小(small / medium / large-v3)在边缘机上的延迟测试,Phase 2 实测决定

---

## 附:常用 dora 命令

```bash
dora build dataflow/full.yml --uv
dora run dataflow/full.yml --uv
dora list
dora stop <dataflow-id>
dora logs <dataflow-id> <node-id>
```

## 附:LLM endpoint 联调

参考用户提供的 curl 调用方式(本地服务器 Qwen3-VL-8B)。仓库中 `scripts/llm_smoke_test.py` 实现 text-only 和 text+image 两种最小调用,Phase 3 联调使用。

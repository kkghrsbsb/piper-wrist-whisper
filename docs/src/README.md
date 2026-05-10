# Piper Dual-Mode Voice/Text Robot — 项目指导文档 (v3)

> 本文档作为后续 Claude Code 协作开发与人工讨论的共同基线。所有节点接口、状态机定义、目录结构、风险清单都以此为准。本期交付包含 LLM 模式(决策做动作)与 VLM 模式(只描述场景),用户通过 Web 端切换。

---

## 0. 一句话定位

基于 dora-rs 中间件,通过 Web 前端接收文本/语音指令,以 **双模式**(LLM 决策做动作 / VLM 只描述场景)驱动 Agilex Piper 6 自由度机械臂,具备真机/仿真同步与 rerun 可视化。决策模型统一走本地服务器的 Qwen3-VL-8B(OpenAI 兼容端点)。

## 快速开始

```bash
dora build dataflow/full.yml --uv
dora run dataflow/full.yml --uv
dora list
dora stop <dataflow-id>
dora logs <dataflow-id> <node-id>
```

## 项目结构

```
piper-wrist-whisper/
├── dataflow/          # dora dataflow YAML 配置
├── nodes/             # 自研 dora 节点
├── webui/             # React 前端
├── actions/           # 预设动作轨迹 (.npz)
├── assets/            # URDF 与 meshes
├── scripts/           # 工具脚本
└── tests/             # 单元测试
```

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

**已删除/不使用的节点**
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

**WebSocket 协议**

Web → Server (command 类):
```json
{"type": "text_input", "text": "你好"}
{"type": "audio_chunk", "data": "<base64 webm/opus>"}
{"type": "audio_end"}
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
{"type": "system_text", "text": "好的我回原位"}
{"type": "alarm", "level": "warn", "msg": "LLM endpoint 不可达"}
```

### 5.2 input-router

**输入** `voice_text` + `typed_text`  
**输出** `user_text: pa.Array<str>` 统一下发,metadata.source 标 "voice"/"text"

### 5.3 mode-router

**输入** `user_text` + `mode`  
**输出** `llm_text`(mode==LLM) / `vlm_text`(mode==VLM)

### 5.4 rule-matcher

**输入** `llm_text` + `state`  
**输出** `action_id` / `no_match`

```yaml
# rules.yaml
ACTIONS:
  wave:  { keywords: [挥手, 打招呼, 你好, hi, hello] }
  nod:   { keywords: [点头, 同意, 好的] }
  home:  { keywords: [回去, 复位, 休息, 回原位] }
  stop:  { keywords: [停, 停下, 别动] }
```

### 5.5 llm-client

**输入** `text_query` / `vision_query` / `image`(缓存)  
**输出** `action_id` / `scene_desc` / `alarm`

```yaml
env:
  LLM_ENDPOINT: "http://10.100.1.93:12368/v1/chat/completions"
  LLM_MODEL: "/model/Qwen3-VL-8B"
  LLM_TIMEOUT_SEC: "5"
  LLM_TEXT_MAX_TOKENS: "10"
  LLM_VISION_MAX_TOKENS: "200"
  LLM_TEMPERATURE: "0.3"
```

### 5.6 action-dispatcher

**输出**
- `joint_action: pa.Array<float32>[7]` to dora-piper
- `speech_text: pa.Array<str>` to kokoro-tts
- `done: pa.Array<str>`

轨迹格式:`actions/{action_id}.npz` — `joints: float32 [N, 7]` + `dt: float32` + `speech: str`

### 5.7 arm-supervisor

**输出**
- `state: pa.Array<str>`(BOOT/HOMING/READY/THINKING/EXECUTING/FAULT)
- `mode: pa.Array<str>`(LLM/VLM)
- `grant: pa.Array<bool>`
- `home_cmd: pa.Array<float32>[7]`
- `tts_gate: pa.Array<bool>`

### 5.8 safety-monitor

**告警条件**
- jointstate 时戳 > 200ms 没更新 → CAN 断连
- 任意关节角越限 ±0.05 rad → 关节越界
- EXECUTING 持续 > 30s → 动作卡死
- llm_alarm 连续 3 次 → LLM 不可达(降级,不进 FAULT)

### 5.9 dora-piper(自研)

**输入** `joint_action` / `disable_request` / `enable_request`  
**输出** `jointstate: pa.Array<float32>[7]` 周期 20ms

---

## 6. 前端(localhost:3000)

```
┌──────────────────────────────────────────────┐
│ [Mode: LLM | VLM]   [TTS: on]    State: READY│
├──────────────────────────────────────────────┤
│      消息列表(用户 + 系统)                  │
├──────────────────────────────────────────────┤
│ [文本输入框                  ] [发送]         │
│ S 键 PTT:  ●已激活                            │
└──────────────────────────────────────────────┘
                                          ┌────┐
                                          │STOP│
                                          └────┘
```

技术栈:React + Vite + shadcn/ui + Tailwind + zod

---

## 7. 风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| CAN 断连静默 | 高 | safety-monitor jointstate 时戳超时检测 |
| WebSocket 断连 | 高 | 前端自动重连;后端按 ws 断开为"输入暂停",不进 FAULT |
| LLM endpoint 不可达 | 高 | httpx 超时 + safety-monitor 降级 |
| LLM 输出不符白名单 | 高 | response_format JSON + 白名单 + 不符则 unknown |
| Web STOP 误以为安全保证 | 高 | 文档明确:仅辅助;部署时强调硬件急停 |
| 模式切换状态机不一致 | 高 | LLM→VLM 仅 READY 时;拒绝时前端弹 toast |
| 预设动作冲突 | 中 | dispatcher 执行中拒绝新 action_id |
| dora 节点崩溃静默 | 中 | 每个自研节点 try/except,ERROR 路由 safety-monitor |
| VLM 延迟尖峰 1-3s | 中 | 前端"思考中"指示 |
| PTT 漏键 | 中 | 前端 5s 安全超时强制结束录音 |

---

## 8. 开发节奏

- **Phase 0**:前端骨架 + web-server 桥接,联调 echo
- **Phase 1**:LLM 文本闭环(home/wave),仿真跑通
- **Phase 2**:语音输入 + 真机 + teach record
- **Phase 3**:LLM fallback + VLM 模式 + safety-monitor

---

## 9. 与 Claude Code 协作注意事项

- 每次只让 Claude Code 实现一个节点或一个组件
- rule-matcher、input-router、mode-router 等纯逻辑节点先写 pytest
- llm-client 单元测试用 httpx mock,集成测试用 `llm_smoke_test.py`
- dora-piper 节点等用户提供参考接口文档后再实现,先用 mock 占位
- 前端不引入 Redux/Zustand,React useState 够用
- WebSocket 协议是契约,改动需前后端双向同步

---

## 10. 已知遗留问题

1. kokoro-tts 中文音色质量待验证
2. mujoco-sim URDF 加载与关节映射,确认 mujoco 版本
3. teach mode 下 piper 无阻尼,record_teach.py 需做关节角范围检查
4. EXECUTING 期间"停"指令的打断机制(立即失能/当前帧停/平滑减速)待明确
5. 远程 LLM endpoint 鉴权:当前假设内网信任
6. 双输入 100ms 内同时到达 input-router 的优先级:先到先得
7. dora-piper 节点实现等用户给参考接口文档
8. WebSocket 断开期间前端缓存输入的处理:当前丢弃
9. faster-whisper 模型大小在边缘机上的延迟:Phase 2 实测决定

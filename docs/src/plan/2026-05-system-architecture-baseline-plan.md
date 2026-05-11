# 系统架构基线方案

> 本文档是项目的**长期设计基线**,描述整体数据流、节点清单、状态机、节点接口规范、风险清单与 Phase 划分。
> 关键技术取舍的"为什么"见 `docs/src/adr/2026-05-001-dual-mode-llm-vlm-architecture.md`。
> piper-control 接口认知见 `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md`。

---

## 1. 整体数据流

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
```

---

## 2. 节点清单

| 节点 ID | 状态 | 来源 / 路径 | 作用 |
|---|---|---|---|
| `rerun-dabai-dc1` | **已存在** | `nodes/rerun-dabai-dc1/` | Orbbec 摄像头采集 + Rerun 可视化 |
| `rerun-piper-sim` | **已存在** | `nodes/rerun-piper-sim/` | Piper 真机 → MuJoCo 镜像,关节状态接通 |
| `web-server` | 待开发 | `nodes/web-server/` | FastAPI + WebSocket + STT,Web 前端唯一桥 |
| `input-router` | 待开发 | `nodes/input-router/` | 合并 voice_text / typed_text → user_text |
| `mode-router` | 待开发 | `nodes/mode-router/` | 按 supervisor.mode 路由 user_text |
| `rule-matcher` | 待开发 | `nodes/rule-matcher/` | 关键词→action_id;仅 LLM 模式 |
| `llm-client` | 待开发 | `nodes/llm-client/` | 远程 Qwen3-VL-8B HTTP 客户端 |
| `action-dispatcher` | 待开发 | `nodes/action-dispatcher/` | 加载预设动作轨迹回放 |
| `arm-supervisor` | 待开发 | `nodes/arm-supervisor/` | FSM + mode 状态 |
| `safety-monitor` | 待开发 | `nodes/safety-monitor/` | 异常监控 |
| `dora-piper` | 待开发 | `nodes/dora-piper/` | piper-control 写入侧封装 |
| `mujoco-sim-publisher` | 待开发 | `nodes/mujoco-sim-publisher/` | 消费 action-dispatcher 的 joint_action,驱动 MuJoCo 仿真,publish 仿真侧 jointstate;Phase 1 用,不依赖 CAN 真机 |
| `kokoro-tts` | 外部依赖 | dora-hub | 文本转语音 |
| `dora-pyaudio` | 外部依赖 | dora-hub(已在仓库 exclude) | 播放 TTS 输出 |
| `dora-rerun` | 外部依赖 | dora-hub | 可视化(独立 9090 端口) |

**节点目录命名约定**:连字符,与已有 `rerun-dabai-dc1`、`rerun-piper-sim` 一致。

---

## 3. 状态机定义(`arm-supervisor`)

状态机有两个正交维度。

### 维度一:执行状态(主要在 LLM 模式有意义)

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

### 维度二:模式

- **LLM**:执行状态机如上正常运转
- **VLM**:状态机长期停在 READY,user_text 不触发 THINKING/EXECUTING,而是触发 llm-client 的 vision 请求

### 状态语义

| 状态 | 进入条件 | 退出条件 | 期间限制 |
|---|---|---|---|
| BOOT | dataflow 启动 | dora-piper 使能成功(参考 piper-control 三步 connect + reset_arm + reset_gripper) | 屏蔽所有 user_text |
| HOMING | BOOT 完成 | 关节角接近 home pose(误差 < 0.02 rad) | 屏蔽所有 user_text |
| READY | HOMING / EXECUTING 完成 | LLM 模式下收到 user_text | 接受指令 |
| THINKING | rule-matcher 收到 text | 输出 action_id 或 unknown | 屏蔽新 user_text |
| EXECUTING | dispatcher 拿到 action_id 且 supervisor 授权 | 轨迹回放完成 / 超时 / 异常 | 屏蔽 user_text |
| FAULT | 任意 alarm | 人工恢复 | 立即失能或回零 |

### 模式切换规则

- **VLM → LLM**:任意时刻可切换,supervisor mode 立刻更新,后续 user_text 进 LLM 通道
- **LLM → VLM**:仅在 READY 状态可切换;EXECUTING 期间切换请求被拒绝,等动作完再切

### 关键约束

- 所有预设动作的轨迹文件最后一帧 **必须是 home pose**,EXECUTING → READY 自然收敛
- VLM 模式下机械臂保持在 home pose,不响应任何 action 类指令(除非用户切回 LLM 模式)

---

## 4. 自研节点接口规范

### 4.1 web-server

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

- `typed_text: pa.Array<str>`
- `voice_text: pa.Array<str>`
- `mode_request: pa.Array<str>` "LLM" 或 "VLM"
- `tts_enable: pa.Array<bool>`
- `estop_request: pa.Array<bool>`
- `home_request: pa.Array<bool>`

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

**STT 实现要点(Phase 2 启用)**

- `faster-whisper` 中文模型 `Systran/faster-whisper-small`,GPU 可用就 GPU,否则 CPU
- 浏览器 `MediaRecorder` 用 webm/opus 流式录音,每 250ms 发一个 chunk
- 用户松开 S 键 → 浏览器发 `audio_end` → web-server 拼接所有 chunk → ffmpeg 解码 PCM → whisper 转录 → publish `voice_text`

### 4.2 input-router

**输入**
- `voice_text: from web-server/voice_text`
- `typed_text: from web-server/typed_text`

**输出**
- `user_text: pa.Array<str>` 统一下发,metadata.source 标 "voice"/"text"

**实现**:简单合并,文本去空白,不做过滤。

### 4.3 mode-router

**输入**
- `user_text: from input-router/user_text`
- `mode: from arm-supervisor/mode`

**输出**
- `llm_text: pa.Array<str>` mode==LLM 时
- `vlm_text: pa.Array<str>` mode==VLM 时

**实现**:维护本地 mode 缓存,收到 user_text 时按当前缓存路由。

### 4.4 rule-matcher

**输入**
- `llm_text: from mode-router/llm_text`
- `state: from arm-supervisor/state`(READY 才处理)

**输出**
- `action_id: pa.Array<str>` 命中时
- `no_match: pa.Array<str>` 未命中时,转给 llm-client

**规则配置 `rules.yaml`**

```yaml
ACTIONS:
  wave:  { keywords: [挥手, 打招呼, 你好, hi, hello] }
  nod:   { keywords: [点头, 同意, 好的] }
  home:  { keywords: [回去, 复位, 休息, 回原位] }
  stop:  { keywords: [停, 停下, 别动] }
```

- `rapidfuzz.fuzz.partial_ratio >= 75` 容错

### 4.5 llm-client

**职责**:统一处理两种远程 LLM 请求。

**输入**
- `text_query: from rule-matcher/no_match`(LLM 模式)
- `vision_query: from mode-router/vlm_text`(VLM 模式)
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

### 4.6 action-dispatcher

**输入**
- `action_id: from rule-matcher/action_id` 或 `llm-client/action_id`
- `grant: from arm-supervisor/grant`
- `tick: dora/timer/millis/20`

**输出**
- `joint_action: pa.Array<float32>[7]` to dora-piper(6 关节 + 1 夹爪)
- `speech_text: pa.Array<str>` to kokoro-tts
- `done: pa.Array<str>` 轨迹回放完成

**轨迹格式**:`actions/{action_id}.npz`
- `joints: float32 [N, 7]`
- `dt: float32` 帧间隔
- `speech: list[str]` 预设播报文本变体数组(≥1 条),回放时随机选一条

**回放逻辑**
1. 收到 action_id → 向 supervisor 申请 grant
2. 拿到 grant → 加载 npz → `speech_text = random.choice(npz['speech'])` → 立即 publish speech_text → 按 tick 逐帧 publish joint_action
3. 期间 drop 新进来的 action_id
4. 全部 publish 完成 + piper-state 显示真机到位(误差 < 0.02 rad)→ publish done
5. 总超时 30s

**与 piper-control 的对接**:不使用 `BuiltinJointPositionController.move_to_position`(阻塞),而是逐帧 publish 给 dora-piper,后者通过 `command_joint_positions` + `command_gripper` 写入。

### 4.7 arm-supervisor

**输入**
- `jointstate: from dora-piper`
- `done: from action-dispatcher`
- `alarm: from safety-monitor`
- `request: from action-dispatcher`(grant 申请)
- `mode_request: from web-server`
- `estop_request: from web-server`
- `home_request: from web-server`
- `tts_enable: from web-server`

**输出**
- `state: pa.Array<str>` (BOOT/HOMING/READY/THINKING/EXECUTING/FAULT)
- `mode: pa.Array<str>` (LLM/VLM)
- `grant: pa.Array<bool>`
- `home_cmd: pa.Array<float32>[7]`
- `tts_gate: pa.Array<bool>`

**实现要点**
- BOOT 状态:dora-piper 上报使能完成才进 HOMING
- HOMING:发 home pose 关节指令,等真机收敛
- estop_request:立即进 FAULT,发 disable 信号给 dora-piper
- 模式切换:VLM→LLM 任意时刻;LLM→VLM 仅 READY 时
- tts_enable=False 时,tts_gate=False;kokoro-tts 订阅 tts_gate 决定是否实际播放

### 4.8 safety-monitor

**输入**
- `jointstate: from dora-piper`
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

### 4.9 dora-piper(自研)

> 接口认知详见 `docs/src/learn/2026-05-piper-control-api-from-repo-usage.md`。
> 读取侧已有参考实现:`nodes/rerun-piper-sim/robot_state_publisher.py`。

**输入**
- `joint_action: pa.Array<float32>[7]` 6 关节角(rad)+ 1 夹爪位置(m)
- `disable_request: pa.Array<bool>` 安全失能(先夹爪后机械臂)
- `enable_request: pa.Array<bool>` 重新使能

**输出**
- `jointstate: pa.Array<float32>[7]` 周期 20ms publish 当前关节角 + 夹爪位置
- `enabled: pa.Array<bool>` 使能状态(供 supervisor 判断 BOOT 是否完成)

**初始化流程**(参考 learn 文档)
1. 三步 CAN 连接:`find_ports()` → `activate(ports)` → `active_ports()`
2. `PiperInterface(can_port)` 构造
3. `set_installation_pos(ArmInstallationPos.UPRIGHT)` —— 待探索是否必须,先按 demo 调用
4. `piper_init.reset_arm(robot, arm_controller=POSITION_VELOCITY, move_mode=JOINT)`
5. `piper_init.reset_gripper(robot)`
6. 多次采样 `is_arm_enabled()` 取或值,确认使能
7. publish `enabled=True`

**控制写入**(收到 joint_action)
- `set_arm_mode(speed=10)` —— 安全保守值
- `command_joint_positions(positions[:6])`
- `command_gripper(position=positions[6], effort=0.5)`

**失能流程**(收到 disable_request)
- `disable_gripper()` 先
- `disable_arm()` 后

**TEACH_MODE 环境变量**
- 进入示教模式,只 publish jointstate 不接受 joint_action
- 用于 `teach_record` dataflow

**待探索问题(实现时一并解决)**
- `set_installation_pos` 是否必须
- `robot.disable_arm()` vs `piper_init.disable_arm(robot)` 的差异

---

## 5. 前端(localhost:3000)

### 5.1 页面布局

全屏单页,无浮窗:

```
┌──────────────────────────────────────────────┐
│ [Mode: LLM | VLM]   [TTS: on]    State: READY│  ← 顶栏
├──────────────────────────────────────────────┤
│      消息列表(用户 + 系统)                  │
├──────────────────────────────────────────────┤
│ [文本输入框                  ] [发送]         │  ← 底栏
│ S 键 PTT(页面激活时):  ●已激活                │
└──────────────────────────────────────────────┘
                                          ┌────┐
                                          │STOP│  ← 右下悬浮急停
                                          └────┘
```

### 5.2 交互细节

- **激活检测**:用户点击页面任意位置 → activated;`document.hidden` 或失焦 → deactivated;deactivated 时 S 键不响应
- **S 键 PTT**:keydown S → 开始 MediaRecorder + 顶栏显示"录音中"红点;keyup S → 停止录音、发 audio_end
- **文本输入**:输入框聚焦时 S 键不触发 PTT;Enter 发送
- **模式切换**:顶栏切换器,LLM ↔ VLM,后端拒绝时(EXECUTING 中切 VLM)前端弹 toast
- **TTS 开关**:顶栏 toggle,影响后端 tts_gate
- **STOP 按钮**:右下悬浮红色按钮,点击 → 发 estop_request;**仅辅助手段,真正安全靠硬件急停**

### 5.3 技术栈

- React + Vite
- WebSocket 客户端(原生 API,断线自动重连)
- shadcn/ui + Tailwind 做基础组件
- 消息 schema 与后端 Pydantic 模型同源(Phase 1 手动同步)
- 不引入复杂状态管理库

---

## 6. 配置约定

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
WHISPER_DEVICE: "auto"

# rerun
RERUN_PORT: "9090"

# piper 安全
PIPER_ARM_SPEED: "10"             # set_arm_mode(speed=10)
PIPER_GRIPPER_EFFORT: "0.5"
PIPER_HOME_POSE: "[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0]"  # 7 维:6 关节 + 夹爪(gripper=0.0 自然收口)
```

---

## 7. 风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| CAN 断连静默 | 高 | safety-monitor jointstate 时戳超时检测 |
| WebSocket 断连 | 高 | 前端自动重连;后端按 ws 断开为"输入暂停",不进 FAULT |
| LLM endpoint 不可达 | 高 | httpx 超时 + safety-monitor 降级,rule-matcher 仍工作 |
| LLM 输出不符合白名单 | 高 | response_format JSON + 白名单 + 不符则 unknown |
| Web STOP 误以为是安全保证 | 高 | 文档明确"仅辅助";部署时强调硬件急停 |
| 模式切换中途的状态机不一致 | 高 | LLM→VLM 仅 READY 时;切换被拒时前端弹 toast |
| 预设动作之间冲突 | 中 | dispatcher 在执行中拒绝新 action_id |
| dora 节点崩溃静默 | 中 | 每个自研节点 try/except 包 main,ERROR 路由 safety-monitor |
| 远程 LLM 延迟尖峰(VLM 1-3s) | 中 | 前端立即显示"思考中"指示;text 模式 max_tokens=10 控制 |
| PTT 漏键 | 中 | 前端 5s 安全超时强制结束录音 |
| Whisper 模型加载慢 | 低 | 首次启动加载 small 模型 ~10s;web-server 启动后才允许前端连接 |
| rerun 内存泄露 | 低 | RERUN_MEMORY_LIMIT 调小 |

---

## 8. Phase 划分

> Phase 划分是**参考路径**,不是契约。每个 Phase 完成后由人工评估再决定下一步,不让 Claude Code 自主连贯推进。

### Phase 0:前端骨架 + web-server 桥接(无 dora 业务逻辑)

1. 仓库结构补齐(`scripts/`、`webui/` 等目录)
2. 写 web-server 节点的 FastAPI + WebSocket(暂不含 STT)
3. 写前端 React 骨架:消息列表、文本输入、模式切换器、TTS toggle、STOP 按钮、激活检测
4. 联调:浏览器打字 → web-server 收到 → echo 回前端

**完成标志**:能在 localhost:3000 跟 web-server "聊天",但不接任何决策。

### Phase 1:打通 LLM 模式文本闭环(home / wave 两个动作,无真机)

1. 写 input-router、mode-router(暂只支持 LLM 模式)
2. 写 rule-matcher 支持 home 和 wave
3. 写 arm-supervisor 最小版本(BOOT/READY/EXECUTING)
4. 写 action-dispatcher,只支持 home(发 home pose 一帧)
5. 实现 `mujoco-sim-publisher`(新建节点,不修改 `rerun-piper-sim`),消费 `joint_action` 驱动 MuJoCo,可参考 `rerun-piper-sim` 的 MJCF 加载与关节映射代码(只读参考,不改 `rerun-piper-sim`)

**完成标志**:前端打"回去" → mujoco 里看见机械臂动 → 前端收到 system_text。

### Phase 2:Web 端语音输入 + 真机

1. 实现 `dora-piper` 节点(基于 piper-control learn 文档)
2. real arm 替换 mujoco 联调
3. teach_record dataflow 跑通,录 wave / nod / home / stop 四个动作
4. web-server 加 faster-whisper STT 模块
5. 前端 PTT 实现:S 键 + MediaRecorder + audio_chunk 流式传输

**完成标志**:按住 S 说"挥手" → 真机挥手。

### Phase 3:LLM fallback + VLM 模式 + 安全监控

1. 写 llm-client 节点的 text query 分支
2. 验证远程端点(独立 smoke test)
3. 接入 rule-matcher 的 no_match → llm-client → action-dispatcher
4. 实现 llm-client 的 vision query 分支,接入 camera 缓存
5. 前端模式切换器对接,VLM 模式提问看图
6. 写 safety-monitor

**完成标志**:模糊指令能兜底成功;VLM 模式能描述画面;CAN 断连/LLM 不可达能降级不崩。

---

## 9. 已知遗留问题

1. kokoro-tts 中文音色质量待验证,可能换 TTS
2. `mujoco-sim-publisher` 新建,MJCF 模型与关节映射可参考 `rerun-piper-sim`,确认 mujoco 版本一致
3. teach mode 下 piper 完全无阻尼,record 流程需关节角范围检查
4. 远程 LLM endpoint 是否需要鉴权?当前样例无,假设内网信任
5. 双输入(打字 + PTT)在 100ms 内同时到达 input-router 时的优先级:当前先到先得
6. WebSocket 断开期间前端缓存的输入(用户在断网时打字)如何处理?当前丢弃
7. faster-whisper 模型大小(small / medium / large-v3)在边缘机上的延迟测试,Phase 2 实测决定
8. piper-control 待探索问题(`set_installation_pos`、两个 `disable_arm`)在 dora-piper 实现时一并解决

---

## 附:常用 dora 命令

```bash
dora build dataflow/<file>.yml --uv
dora run dataflow/<file>.yml --uv
dora list
dora stop <dataflow-id>
dora logs <dataflow-id> <node-id>
```

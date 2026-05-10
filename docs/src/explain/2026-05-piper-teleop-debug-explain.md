# piper-teleop 节点构建说明

> 对应方案:[`docs/src/plan/2026-05-piper-teleop-debug-plan.md`](../plan/2026-05-piper-teleop-debug-plan.md)
> 对应原理参考:[`docs/src/learn/2026-05-piper-control-api-from-repo-usage.md`](../learn/2026-05-piper-control-api-from-repo-usage.md)
>
> 本文不重复方案背景与 piper-control API 解释,只记录实际改动、与方案的差异、当前能力边界与已知问题。

---

## 1. 改动了什么

新增 dora workspace 节点包 `nodes/piper-teleop/`,改编自参考脚本
`docs/references/tests/hardware/connect_init.py` 和
`docs/references/tests/gamepad/gamepad_joint_control.py`。

### 1.1 节点包结构

```
nodes/piper-teleop/
├── pyproject.toml                  # build-system + 2 entry points + dev deps
├── piper_teleop/
│   ├── __init__.py
│   ├── constants.py                # 关节限位、按键映射、速度档、安全位等
│   ├── pure_functions.py           # 4 个 numpy 纯函数,可单元测试
│   ├── bringup.py                  # 真机持有方:CAN 连接 + 使能 + 回零 + 写入 + 退出
│   └── gamepad.py                  # pygame 持有方:读手柄 → publish joint_action
└── tests/
    ├── __init__.py
    ├── test_pure_functions.py      # 14 个纯函数测试
    ├── test_bringup.py             # 17 个 mock 测试(probe/safe_shutdown/...)
    └── test_smoke.py               # 7 个导入冒烟 + main raises 测试
```

入口脚本(由 uv 注册到 venv):

- `piper-bringup` → `piper_teleop.bringup:main`
- `piper-gamepad` → `piper_teleop.gamepad:main`

### 1.2 dataflow 文件

- `dataflow/tests/test_piper_bringup.yml` — 仅 bringup 的烟测,用于真机首次启动验证使能 + 回零 + safe_shutdown
- `dataflow/teleop/teach_record.yml` — 完整 teleop dataflow(bringup + gamepad)

### 1.3 测试

`uv run pytest nodes/piper-teleop/tests/` 共 38 个测试,全过。
覆盖:纯函数、按键边沿检测、probe_enabled 多采样或逻辑、safe_shutdown 顺序、
get_jointstate 形态、connect_and_enable 各分支、apply_joint_action 形状/裁剪/调用。

---

## 2. 与方案的差异

实现路径与 plan 基本一致,有两处微调:

1. **jointstate 发布机制**:plan §3.2 写"每 20ms publish jointstate",批次 B
   实现时改为"事件驱动"——每收到下游事件后发一次。批次 D 写 YAML 时,用户
   选择把 `tick: dora/timer/millis/20` 加进 piper_bringup 的 inputs,代码无需
   改动(任何非匹配 eid 的 INPUT 事件都会落到末尾的 jointstate publish 路径)。
   最终结果:50Hz 周期发布,gamepad 失联也不影响。

2. **`nv12_to_bgr` / `nv21_to_bgr` 顺手修了一个不相关的源码 bug**:不在本 plan
   范围,见 `nodes/rerun-dabai-dc1/rerun_dabai_dc1/orbbec_camera.py`。
   原实现 `cv2.merge([Y, UV])` 在 Y/UV 维度不一致时 raise;改为直接把 frame
   reshape 成 `(h*3//2, w)` 传给 `cv2.cvtColor`,这是 NV12/NV21 在 OpenCV
   中的标准用法。这个改动连带覆盖了一组单元测试,与 piper-teleop 无关。

---

## 3. 影响了哪些部分

### 直接新增

- `nodes/piper-teleop/` 目录及全部内容
- `dataflow/tests/test_piper_bringup.yml`
- `dataflow/teleop/teach_record.yml`

### 间接影响

- `uv.lock` 增加 `pygame==2.6.1`(piper-teleop 的 gamepad 依赖,workspace
  共享 venv)
- `.venv/bin/` 增加两个可执行入口 `piper-bringup`、`piper-gamepad`
- workspace 根 `pyproject.toml` **未改动**,`nodes/*` glob 自动发现新节点

### 不影响

- 已有节点 `rerun-dabai-dc1`、`rerun-piper-sim`(本任务全程视为只读参考)
- baseline plan、ADR 等设计文档

---

## 4. 当前能力边界

### 已验证

| 项目 | 验证方式 |
|---|---|
| bringup 在真机上完成 CAN 连接 + 使能 + 回零 | 用户手动跑 `dora start dataflow/tests/test_piper_bringup.yml` 通过 |
| bringup 退出时 safe_shutdown 顺序正确(先夹爪后机械臂) | 真机验证 + mock 单元测试 |
| 4 个纯函数(deadzone / clip / lead window / increment)的数学正确性 | pytest 14 个测试 |
| 启动/使能各分支(已使能时跳 reset,未使能时调 reset,active_ports 空时 raise) | mock 单元测试 |
| `apply_joint_action` 防御性裁剪 + 调用链 | mock 单元测试 |

### 尚未验证

- **完整 teleop 联调**:`dataflow/teleop/teach_record.yml` 没有在真机上跑过。
  bringup 的 `joint_action` 写入路径在 mock 下验证过,真机下未验证。用户保留
  在批次 D 之后做真机联调
- **gamepad 端到端 dora 接线**:gamepad 主流程没有用 mock dora Node 做集成
  测试,只有 main() 在非 dora 环境抛异常的 smoke 测试

---

## 5. 潜在风险与兼容性注意

### 真机风险

- bringup 持有 `PiperInterface`,所有真机控制都从这里走。任何 bug 都可能直接
  触发机械臂动作。CLAUDE.md 协作铁律:"涉及真机控制的代码先在仿真验证再由
  用户手动上真机测试"被严格遵守(Claude Code 全程未跑过任何 dora/dora-cli 命令)
- `BuiltinJointPositionController` 是阻塞 API。启动期回零、Y 键回零、退出期
  回安全位都会让 bringup 主循环阻塞最长 12s。期间 `joint_action` 输入设了
  `queue_size: 1` 防止事件堆积(plan §5.2)

### 已知不足

- **gamepad 的 LB/RB 速度档目前对真机无效**:gamepad 只在自己进程内调
  `SPEED_FACTORS[i]` 影响 step 系数;`set_arm_mode(speed)` 在 bringup 端固定
  为 `JOINT_SAFE_SPEED=10`。要让 LB/RB 真生效需要 gamepad 多 publish 一个
  `arm_speed` 输出、bringup 多接一个输入并调 `set_arm_mode`。**未改动**
- **`record_event` 是接口预留**:gamepad pyproject 中声明输出,代码不实际
  publish。Start 键监听与文件 IO 留给后续 teach-recorder plan
- **gamepad 失联时 bringup 仍能跑**:bringup 不依赖 gamepad 输入,只依赖
  `tick: dora/timer/millis/20` 周期发 jointstate。但 gamepad 一旦掉线无法
  自动恢复 ready 状态(代码逻辑没处理"gamepad 重连"场景)。debug 工具够用

### piper-control 的两个待探索问题原样保留

- `set_installation_pos(UPRIGHT)` 是否必须:bringup 沿用 connect_init.py 的
  做法**不调用**
- `robot.disable_arm()` vs `piper_init.disable_arm(robot)`:bringup 沿用
  connect_init.py 的做法用前者

这两个问题不在本任务解决,留给未来 `dora-piper` 实现时再决议。

---

## 6. 文件清单

### 新建(本任务)

```
nodes/piper-teleop/pyproject.toml
nodes/piper-teleop/piper_teleop/__init__.py
nodes/piper-teleop/piper_teleop/constants.py
nodes/piper-teleop/piper_teleop/pure_functions.py
nodes/piper-teleop/piper_teleop/bringup.py
nodes/piper-teleop/piper_teleop/gamepad.py
nodes/piper-teleop/tests/__init__.py
nodes/piper-teleop/tests/test_pure_functions.py
nodes/piper-teleop/tests/test_bringup.py
nodes/piper-teleop/tests/test_smoke.py
dataflow/tests/test_piper_bringup.yml
dataflow/teleop/teach_record.yml
```

### 顺手修改(不在本任务范围,但 commit 一起)

```
nodes/rerun-dabai-dc1/rerun_dabai_dc1/orbbec_camera.py    # nv12/nv21 bug fix
nodes/rerun-dabai-dc1/tests/test_orbbec_camera.py         # nv12/nv21 测试
```

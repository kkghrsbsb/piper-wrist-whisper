# mujoco-sim-publisher 节点方案

> 上位 plan:`2026-05-system-architecture-baseline-plan.md` Phase 1 第 5 步。
> 本方案只规划实现,不修改代码。目标是在无真机 CAN 的情况下验证
> `action-dispatcher` 输出的轨迹能驱动 MuJoCo 仿真。

---

## 1. 功能目标

新增 `mujoco-sim-publisher` 节点,作为 Phase 1 的仿真执行端:

1. 消费 `action-dispatcher/joint_action` 的 7 维关节目标:
   - 6 个机械臂关节角,单位 rad
   - 1 个夹爪开合位置,单位 m
2. 将关节目标写入 Piper MJCF 模型的 `qpos`,执行 `mj_forward`。
3. 周期 publish 仿真侧 `jointstate: pa.Array<float32>[7]`,供后续:
   - `arm-supervisor` 判断动作到位
   - Rerun 或其他 viewer 可视化
   - 测试 action-dispatcher 的轨迹输出
4. 不连接真机、不依赖 CAN、不调用 `piper-control`。

完成标志:

```text
测试输入一段 joint_action 轨迹
→ mujoco-sim-publisher 按输入更新 MuJoCo qpos
→ 输出 sim jointstate[7]
→ 可在后续 dataflow 中替代 dora-piper 的 jointstate 来源
```

---

## 2. 当前问题或动机

已有 `rerun-piper-sim` 节点能把真机 `joint_positions` 显示到 MuJoCo/Rerun,
但它的输入来自 `robot_state_publisher`,仍依赖真机状态读取。Phase 1 需要先验证
`action-dispatcher` 的轨迹回放,不能要求 CAN 或 Piper 真机在线。

因此需要一个新的仿真执行端:

- 输入接口对齐未来 `dora-piper`:消费 `joint_action[7]`
- 输出接口对齐未来 `dora-piper`:publish `jointstate[7]`
- 只做仿真状态推进,不承担轨迹调度、状态机授权、Rerun 可视化或真机控制

这让后续可以用同一套上游链路在两个后端之间切换:

```text
Phase 1: action-dispatcher → mujoco-sim-publisher → arm-supervisor
Phase 2: action-dispatcher → dora-piper           → arm-supervisor
```

---

## 3. 方案设计

### 3.1 节点边界

节点目录:

```text
nodes/mujoco-sim-publisher/
```

Python 包:

```text
mujoco_sim_publisher
```

entry point:

```text
mujoco-sim-publisher = "mujoco_sim_publisher.publisher:main"
```

职责只包含:

- 加载 Piper MJCF 模型
- 接收最新 `joint_action`
- 更新 MuJoCo `data.qpos`
- publish `jointstate`

不包含:

- 不实现 action-dispatcher
- 不实现 Rerun viewer
- 不修改 `nodes/rerun-piper-sim`
- 不连接 Piper 真机
- 不处理 supervisor grant

### 3.2 dora 接口

输入:

| 输入 | 来源 | 类型 | 说明 |
|---|---|---|---|
| `joint_action` | `action-dispatcher/joint_action` | `pa.Array<float32>[7]` | 最新关节目标 |
| `tick` | `dora/timer/millis/20` | timer | 20ms 周期 publish jointstate |

输出:

| 输出 | 类型 | 说明 |
|---|---|---|
| `jointstate` | `pa.Array<float32>[7]` | 仿真侧当前关节状态 |

行为:

1. 启动时加载 MJCF,初始化 `data = MjData(model)`。
2. 维护 `latest_joint_action`,初始值用 `PIPER_HOME_POSE`
   `[-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0]`。
3. 收到 `joint_action` 时:
   - 校验长度必须为 7
   - 转为 `np.float32`
   - 对夹爪限制到 `[0.0, 0.1]`
   - 写入 MuJoCo `qpos`
4. 收到 `tick` 时:
   - 对当前 `latest_joint_action` 再写一次 MuJoCo
   - `mj_forward(model, data)`
   - publish `jointstate`

说明:MuJoCo 在这里主要作为运动学状态容器,不做动力学积分。action-dispatcher 已经输出逐帧目标位,
本节点不应自行插值或平滑,否则会掩盖轨迹回放问题。

### 3.3 MJCF 复用策略

参考但不修改:

```text
nodes/rerun-piper-sim/rerun_piper_sim/agilex_piper/scene.xml
nodes/rerun-piper-sim/rerun_piper_sim/mujoco_sim_viewer.py
nodes/rerun-piper-sim/rerun_piper_sim/rerun_mjcf_viewer.py
```

默认 MJCF 路径:

```text
../rerun-piper-sim/rerun_piper_sim/agilex_piper/scene.xml
```

解析方式建议:

- 从 `nodes/mujoco-sim-publisher/mujoco_sim_publisher/publisher.py` 的 `__file__`
  反推到 `nodes/rerun-piper-sim/.../scene.xml`
- 支持环境变量 `PIPER_MJCF_PATH` 覆盖,方便后续替换模型或测试临时模型
- 启动时检查文件存在,不存在则抛清晰错误

不建议复制 MJCF 资产到新节点。复制会让后续模型修复需要改两份文件,容易漂移。

### 3.4 关节映射

沿用现有 `rerun-piper-sim` 映射:

```python
data.qpos[:6] = joints[:6]
finger_pos = joints[6] * (0.035 / 0.1)
data.qpos[6] = finger_pos
data.qpos[7] = -finger_pos
```

常量:

```python
GRIPPER_SCALE = 0.035 / 0.1
GRIPPER_LIMITS = (0.0, 0.1)
```

输出 `jointstate` 仍保持项目统一的 7 维语义,即夹爪值输出原始米制 `[0.0, 0.1]`,
不输出 MuJoCo finger joint 的缩放后值。

### 3.5 dataflow 验证

新增测试/开发用 dataflow:

```text
dataflow/tests/test_mujoco_sim_publisher.yml
```

Phase 1 初期 `action-dispatcher` 尚未实现时,可先配一个小型测试输入节点,
只在测试 dataflow 中使用,周期发送几帧固定 `joint_action`。等 action-dispatcher 完成后,
再把输入源替换为真实 `action-dispatcher/joint_action`。

长期 Phase 1 dataflow 形态:

```yaml
nodes:
  - id: action_dispatcher
    outputs:
      - joint_action

  - id: mujoco_sim_publisher
    path: uv
    args: run mujoco-sim-publisher
    inputs:
      tick: dora/timer/millis/20
      joint_action:
        source: action_dispatcher/joint_action
        queue_size: 1
    outputs:
      - jointstate
```

`queue_size: 1` 保持与真机写入端一致,防止上游高频输出积压旧目标。

---

## 4. 可能受影响的文件或模块

预计新增:

```text
nodes/mujoco-sim-publisher/
nodes/mujoco-sim-publisher/pyproject.toml
nodes/mujoco-sim-publisher/mujoco_sim_publisher/__init__.py
nodes/mujoco-sim-publisher/mujoco_sim_publisher/publisher.py
nodes/mujoco-sim-publisher/tests/test_publisher.py
dataflow/tests/test_mujoco_sim_publisher.yml
```

可能更新:

```text
docs/src/README.md
docs/src/SUMMARY.md
pyproject.toml
```

说明:

- 新节点位于 `nodes/*`,会被 uv workspace 自动发现。
- 如果新节点需要 import `rerun_piper_sim` 包来定位资产,则根 `pyproject.toml`
  的 `[tool.uv.sources]` 已有 `rerun_piper_sim = { workspace = true }`,新节点自己的
  `pyproject.toml` 里声明依赖即可。
- 本任务不应修改 `nodes/rerun-piper-sim/` 的代码或 MJCF 资产。
- 如果实现改变了项目结构、运行命令或安全说明,需要同步更新 `docs/src/README.md`。

---

## 5. 潜在风险和边界情况

### 5.1 MuJoCo 关节映射漂移

风险:MJCF 中 `qpos` 顺序变化后,硬编码 `qpos[:6]`, `qpos[6]`, `qpos[7]` 会错。

缓解:

- 第一版明确复用当前 `rerun-piper-sim` 的映射。
- 单元测试固定 7 维输入到 `qpos` 的写入结果。
- 后续如模型结构变化,先更新映射测试,再改实现。

### 5.2 夹爪单位混淆

风险:项目接口使用米制夹爪 `[0.0, 0.1]`,MuJoCo finger joint 使用缩放值。

缓解:

- 输入输出都保持 7 维项目语义。
- 只在写 `data.qpos[6:8]` 时做 `GRIPPER_SCALE` 转换。

### 5.3 仿真输出被误认为真机安全验证

风险:MuJoCo 只能验证数据链路和关节目标可视化,不能验证 CAN 时序、piper-control 行为、
碰撞风险或真实电机响应。

缓解:

- 文档和汇报中明确:本节点只用于无真机 Phase 1 验证。
- 后续接入 `dora-piper` 前仍需 mock 测试和人工真机验证。

### 5.4 action-dispatcher 尚未存在

风险:没有真实上游时,节点难以端到端观察。

缓解:

- 单元测试覆盖核心映射逻辑。
- 测试 dataflow 使用临时固定轨迹输入源。
- action-dispatcher 完成后再做真实轨迹联调。

### 5.5 GUI viewer 与 headless 环境

风险:`mujoco.viewer.launch_passive` 需要图形环境,不适合 CI 或远程无显示环境。

缓解:

- `mujoco-sim-publisher` 不启动 GUI viewer。
- 可视化由后续 Rerun viewer 或独立 viewer 消费 `jointstate` 完成。

---

## 6. 测试策略

优先覆盖纯逻辑和节点冒烟:

1. `apply_joint_action_to_data` 单元测试:
   - 7 维输入写入 `qpos[:6]`
   - 夹爪 `0.1` 映射为 `0.035` 和 `-0.035`
   - 夹爪超限时裁剪
2. 输入校验测试:
   - 长度不是 7 时抛 `ValueError`
   - 非数值或 NaN 的处理策略明确,建议拒绝并保留上一帧
3. `resolve_mjcf_path` 测试:
   - 默认路径存在
   - `PIPER_MJCF_PATH` 覆盖生效
4. smoke 测试:
   - import `mujoco_sim_publisher.publisher`
   - 在非 dora 环境调用 `main()` 应按 dora 官方示例抛 Exception

不做:

- 不在单元测试里启动 GUI viewer
- 不要求 CI 跑完整 dora dataflow
- 不接真机

---

## 7. 实施步骤

### 批次 A:节点骨架与纯函数

1. 新建 `nodes/mujoco-sim-publisher/` Python 包。
2. 添加 `pyproject.toml`:
   - `dora-rs`
   - `mujoco`
   - `numpy`
   - `pyarrow`
   - dev 依赖 `pytest`
3. 实现纯函数:
   - `resolve_mjcf_path`
   - `validate_joint_action`
   - `apply_joint_action_to_data`
4. 添加单元测试。

### 批次 B:dora 主循环

1. 实现 `publisher.py::main()`。
2. 处理 `joint_action` 与 `tick` 事件。
3. publish `jointstate`。
4. 添加 main smoke 测试。

### 批次 C:dataflow 验证

1. 新增 `dataflow/tests/test_mujoco_sim_publisher.yml`。
2. 加临时固定轨迹输入源或等待 action-dispatcher 后接入真实上游。
3. 用 `dora build ... --uv` 验证 dataflow 配置。
4. 如需要可用 Rerun viewer 消费 `jointstate` 做人工观察,但不把 viewer 放进本节点职责。

### 批次 D:文档同步

1. 若新增运行命令或节点清单,更新 `docs/src/README.md`。
2. 若创建 explain 文档,同步 `docs/src/SUMMARY.md`。
3. 完成后停下汇报,等待用户确认下一节点。

---

## 8. 待确认问题

1. 测试 dataflow 是否允许新增一个仅用于测试的固定 `joint_action` 输入节点?
   如果不允许,可以等 `action-dispatcher` 完成后再做 dataflow 联调。
2. 第一版是否只 publish `jointstate`,不集成 Rerun 可视化?
   我建议只 publish `jointstate`,保持节点边界干净。
3. NaN/Inf 输入策略:建议拒绝该帧并保留上一帧,同时打印 warning。

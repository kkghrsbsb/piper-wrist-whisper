# robot/sim

通过 dora-rs dataflow 将 Piper 真机关节状态实时镜像到 MuJoCo 仿真模型。

## 现状

- `datarobot_state_publisher`：通过 piper-control 读取真机关节位置（CAN 总线），发布到 dora
- `mujoco_sim_viewer`：MuJoCo passive viewer 3D 窗口，镜像真机姿态
- `rerun_mjcf_viewer`：使用 rerun-loader-mjcf 在 Rerun 中渲染 MJCF 3D 模型 + 关节角时间序列

关节状态已接通，3D 模型可加载显示。

## MJCF 模型

来源：[yanyuze1/agilex_arm_mujoco](https://github.com/yanyuze1/agilex_arm_mujoco/tree/devel/agilex_arm/agilex_piper)

8 个关节：joint1–joint6（机械臂，rad）+ joint7/joint8（夹爪两指，slide，m）。
夹爪映射：piper-control `angle` ∈ [0, 0.1] m → 每指 ∈ [0, 0.035] m（缩放系数 0.35）。

"""MuJoCo simulation backend for joint_action playback.

The node mirrors the future dora-piper interface for Phase 1:

- input:  joint_action float32[7] (6 joints rad + gripper m)
- output: jointstate   float32[7] (same project-level units)

It does not connect to CAN, does not call piper-control, and does not launch a GUI.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pyarrow as pa
from dora import Node
from mujoco import MjData, MjModel, mj_forward

GRIPPER_SCALE = 0.035 / 0.1
GRIPPER_LIMITS = (0.0, 0.1)
PIPER_HOME_POSE = np.array(
    [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0],
    dtype=np.float32,
)


def resolve_mjcf_path(env: dict[str, str] | None = None) -> Path:
    """Resolve Piper MJCF scene path, allowing PIPER_MJCF_PATH override."""
    env = os.environ if env is None else env
    override = env.get("PIPER_MJCF_PATH", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        nodes_dir = Path(__file__).resolve().parents[2]
        path = (
            nodes_dir
            / "rerun-piper-sim"
            / "rerun_piper_sim"
            / "agilex_piper"
            / "scene.xml"
        )

    if not path.exists():
        raise FileNotFoundError(f"Piper MJCF scene.xml not found: {path}")
    return path


def validate_joint_action(value) -> np.ndarray:
    """Return a finite float32 joint_action[7], with gripper clipped to limits."""
    action = np.asarray(value, dtype=np.float32)
    if action.shape != (7,):
        raise ValueError(f"joint_action must be shape (7,), got {action.shape}")
    if not np.all(np.isfinite(action)):
        raise ValueError("joint_action contains NaN or Inf")

    action = action.copy()
    action[6] = np.clip(action[6], *GRIPPER_LIMITS)
    return action


def apply_joint_action_to_data(data: MjData, joint_action: np.ndarray) -> None:
    """Write a validated project-level joint_action into MuJoCo qpos."""
    action = validate_joint_action(joint_action)
    data.qpos[:6] = action[:6]
    finger_pos = float(action[6]) * GRIPPER_SCALE
    data.qpos[6] = finger_pos
    data.qpos[7] = -finger_pos


def main():
    model = MjModel.from_xml_path(str(resolve_mjcf_path()))
    data = MjData(model)
    latest_joint_action = PIPER_HOME_POSE.copy()
    apply_joint_action_to_data(data, latest_joint_action)
    mj_forward(model, data)

    node = Node()
    print("mujoco-sim-publisher: ready")

    for event in node:
        if event["type"] != "INPUT":
            continue

        eid = event["id"]

        if eid == "joint_action":
            try:
                latest_joint_action = validate_joint_action(event["value"])
            except ValueError as exc:
                print(f"mujoco-sim-publisher: invalid joint_action ignored: {exc}")
                continue
            apply_joint_action_to_data(data, latest_joint_action)
            mj_forward(model, data)

        elif eid == "tick":
            apply_joint_action_to_data(data, latest_joint_action)
            mj_forward(model, data)
            node.send_output(
                "jointstate",
                pa.array(latest_joint_action, type=pa.float32()),
            )


if __name__ == "__main__":
    main()

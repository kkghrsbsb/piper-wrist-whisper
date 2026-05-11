"""Replay an action NPZ directly to Piper for agent serial execution.

This node intentionally combines the action-dispatcher and dora-piper roles so
the experimental agent flow can be migrated as a small self-contained unit.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
from dora import Node
from piper_control import (
    piper_connect,
    piper_control,
    piper_init,
    piper_interface,
)

DT_SECONDS = 0.02
DT_ATOL = 1e-4
INIT_POSE_ATOL = 0.02
MAX_EXPECTED_DURATION_SEC = 30.0

INIT_JOINT_POSITION = [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]
INIT_POSE_7D = np.array([*INIT_JOINT_POSITION, 0.0], dtype=np.float32)
SAFE_DISABLE_POSITION = [-1.5708, 0.0, 0.0, 0.02, 0.5, 0.0]
JOINT_SAFE_SPEED = 10
GRIPPER_EFFORT = 0.5
GRIPPER_RANGE = (0.0, 0.1)
GRIPPER_EPS = 1e-4
MOVE_TIMEOUT = 12.0
MOVE_THRESHOLD = 0.01
ENABLE_PROBE_SAMPLES = 5
ENABLE_PROBE_INITIAL_DELAY = 0.3
ENABLE_PROBE_INTERVAL = 0.05

JOINT_LIMITS = [
    (-2.6179, 2.6179),
    (0.0, 3.14),
    (-2.967, 0.0),
    (-1.745, 1.745),
    (-1.22, 1.22),
    (-2.09439, 2.09439),
]


@dataclass(frozen=True)
class ActionTrajectory:
    action_id: str
    joints: np.ndarray
    dt: np.float32
    speech: list[str]
    timestamps: np.ndarray | None = None
    schema_version: int = 1

    @property
    def duration_sec(self) -> float:
        if self.timestamps is not None:
            return float(self.timestamps[-1])
        return int(self.joints.shape[0]) * float(self.dt)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def parse_action_id(raw: str) -> str:
    action_id = raw.strip()
    if not action_id:
        raise ValueError("ACTION_ID must contain a non-empty action id")
    return action_id


def resolve_action_path(action_id: str, actions_dir: Path) -> Path:
    return actions_dir / f"{action_id}.npz"


def load_action_npz(path: Path, action_id: str) -> ActionTrajectory:
    if not path.exists():
        raise FileNotFoundError(f"action file not found: {path}")

    with np.load(path) as data:
        required = {"joints", "dt", "speech"}
        missing = required.difference(data.files)
        if missing:
            raise ValueError(f"action npz missing fields: {sorted(missing)}")

        joints = np.asarray(data["joints"], dtype=np.float32)
        dt = np.asarray(data["dt"], dtype=np.float32)
        raw_speech = np.asarray(data["speech"])
        timestamps = (
            np.asarray(data["timestamps"], dtype=np.float32)
            if "timestamps" in data.files
            else None
        )
        schema_version = (
            int(np.asarray(data["schema_version"]).item())
            if "schema_version" in data.files
            else 1
        )

    if joints.ndim != 2 or joints.shape[1] != 7:
        raise ValueError(f"joints must be [N, 7], got shape {joints.shape}")
    if joints.shape[0] == 0:
        raise ValueError("joints is empty")
    if not np.all(np.isfinite(joints)):
        raise ValueError("joints contains NaN or Inf")

    if dt.shape != ():
        raise ValueError(f"dt must be a scalar, got shape {dt.shape}")
    dt_value = np.float32(dt.item())
    if not np.isfinite(dt_value):
        raise ValueError("dt contains NaN or Inf")
    if not np.isclose(float(dt_value), DT_SECONDS, atol=DT_ATOL):
        raise ValueError(f"dt must be close to {DT_SECONDS}, got {float(dt_value)}")

    speech = [str(s).strip() for s in raw_speech.tolist()]
    speech = [s for s in speech if s]
    if not speech:
        raise ValueError("speech must contain at least one non-empty entry")

    if timestamps is not None:
        if timestamps.shape != (joints.shape[0],):
            raise ValueError(
                "timestamps must be [N], "
                f"got shape {timestamps.shape} for {joints.shape[0]} frames"
            )
        if not np.all(np.isfinite(timestamps)):
            raise ValueError("timestamps contains NaN or Inf")
        if np.any(np.diff(timestamps) < 0):
            raise ValueError("timestamps must be monotonic")

    return ActionTrajectory(
        action_id=action_id,
        joints=joints,
        dt=dt_value,
        speech=speech,
        timestamps=timestamps,
        schema_version=schema_version,
    )


def is_near_init_pose(frame: np.ndarray) -> bool:
    return bool(np.allclose(frame[:6], INIT_JOINT_POSITION, atol=INIT_POSE_ATOL))


def trajectory_warnings(trajectory: ActionTrajectory) -> list[str]:
    warnings: list[str] = []
    if not is_near_init_pose(trajectory.joints[0]):
        warnings.append("first frame is not near INIT_JOINT_POSITION")
    if not is_near_init_pose(trajectory.joints[-1]):
        warnings.append("last frame is not near INIT_JOINT_POSITION")
    if trajectory.duration_sec > MAX_EXPECTED_DURATION_SEC:
        warnings.append(
            f"trajectory duration {trajectory.duration_sec:.1f}s exceeds "
            f"{MAX_EXPECTED_DURATION_SEC:.1f}s"
        )
    return warnings


def probe_enabled(check_fn, *, label: str) -> bool:
    time.sleep(ENABLE_PROBE_INITIAL_DELAY)
    results = [check_fn()]
    for _ in range(ENABLE_PROBE_SAMPLES - 1):
        time.sleep(ENABLE_PROBE_INTERVAL)
        results.append(check_fn())
    print(f"agent-action-replay: {label} enable probe samples={results}")
    return any(results)


def connect_robot():
    ports = piper_connect.find_ports()
    print(f"agent-action-replay: found ports {ports}")
    piper_connect.activate(ports)
    active = piper_connect.active_ports()
    if not active:
        raise RuntimeError("No active CAN ports found")
    print(f"agent-action-replay: active ports {active}")

    robot = piper_interface.PiperInterface(can_port=active[0])
    print(f"agent-action-replay: PiperInterface created on {active[0]}")
    if env_bool("PIPER_SET_INSTALLATION_POS", False):
        print("agent-action-replay: set_installation_pos(UPRIGHT)")
        robot.set_installation_pos(piper_interface.ArmInstallationPos.UPRIGHT)
    return robot


def ensure_enabled(robot) -> None:
    if not probe_enabled(robot.is_arm_enabled, label="arm"):
        print("agent-action-replay: arm not enabled, resetting arm")
        piper_init.reset_arm(
            robot,
            arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
            move_mode=piper_interface.MoveMode.JOINT,
        )
    if not probe_enabled(robot.is_gripper_enabled, label="gripper"):
        print("agent-action-replay: gripper not enabled, resetting gripper")
        piper_init.reset_gripper(robot)


def builtin_move(robot, target: list[float]) -> bool:
    with piper_control.BuiltinJointPositionController(
        robot,
        rest_position=None,
    ) as controller:
        robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
        print(f"agent-action-replay: moving to {target}")
        ok = controller.move_to_position(
            target,
            threshold=MOVE_THRESHOLD,
            timeout=MOVE_TIMEOUT,
        )
        print(f"agent-action-replay: reached target={ok}")
        return ok


def get_jointstate(robot) -> np.ndarray:
    joints = robot.get_joint_positions()
    gripper_pos, _ = robot.get_gripper_state()
    return np.array(list(joints) + [float(gripper_pos)], dtype=np.float32)


def jointstate_arrow(robot) -> pa.Array:
    return pa.array(get_jointstate(robot), type=pa.float32())


def clip_joints(joints: np.ndarray) -> np.ndarray:
    out = joints.astype(np.float32, copy=True)
    for idx, (lo, hi) in enumerate(JOINT_LIMITS):
        out[idx] = np.clip(out[idx], lo, hi)
    return out


def command_frame(
    robot,
    frame: np.ndarray,
    *,
    last_gripper: float | None,
    gripper_eps: float,
) -> float | None:
    joints = clip_joints(frame[:6])
    gripper = float(np.clip(frame[6], *GRIPPER_RANGE))
    robot.command_joint_positions(joints.tolist())
    if last_gripper is None or abs(gripper - last_gripper) > gripper_eps:
        robot.command_gripper(position=gripper, effort=GRIPPER_EFFORT)
        return gripper
    return last_gripper


def due_frame_indices(
    trajectory: ActionTrajectory,
    elapsed: float,
    index: int,
) -> tuple[int | None, int]:
    if trajectory.timestamps is None:
        if index < trajectory.joints.shape[0]:
            return index, index + 1
        return None, index

    frame_index = None
    timestamps = trajectory.timestamps
    while index < trajectory.joints.shape[0] and float(timestamps[index]) <= elapsed:
        frame_index = index
        index += 1
    return frame_index, index


def replay_trajectory(
    robot,
    node: Node,
    trajectory: ActionTrajectory,
    *,
    tick_sec: float,
    gripper_eps: float,
) -> None:
    robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
    node.send_output("started", pa.array([trajectory.action_id]))
    print(f"AGENT_ACTION_STARTED action_id={trajectory.action_id}")

    start = time.monotonic()
    index = 0
    last_gripper = None
    last_jointstate_publish = 0.0

    while index < trajectory.joints.shape[0]:
        now = time.monotonic()
        elapsed = now - start
        frame_index, index = due_frame_indices(trajectory, elapsed, index)
        if frame_index is not None:
            last_gripper = command_frame(
                robot,
                trajectory.joints[frame_index],
                last_gripper=last_gripper,
                gripper_eps=gripper_eps,
            )

        if now - last_jointstate_publish >= 0.1:
            node.send_output("jointstate", jointstate_arrow(robot))
            last_jointstate_publish = now

        time.sleep(tick_sec)


def main():
    node = Node()

    try:
        action_id = parse_action_id(os.environ.get("ACTION_ID", ""))
        actions_dir = Path(os.environ.get("ACTIONS_DIR", "actions"))
        replay_tick_sec = env_float("REPLAY_TICK_MS", 20.0) / 1000.0
        done_hold_seconds = env_float("DONE_HOLD_SECONDS", 1.0)
        gripper_eps = env_float("GRIPPER_EPS", GRIPPER_EPS)
        trajectory = load_action_npz(
            resolve_action_path(action_id, actions_dir),
            action_id,
        )
    except Exception as exc:
        raise SystemExit(f"agent-action-replay: {exc}") from exc

    print(
        f"agent-action-replay: loaded action_id={trajectory.action_id} "
        f"frames={trajectory.joints.shape[0]} "
        f"duration={trajectory.duration_sec:.2f}s "
        f"schema_version={trajectory.schema_version} "
        f"timebase={'timestamps' if trajectory.timestamps is not None else 'dt'}"
    )
    for warning in trajectory_warnings(trajectory):
        print(f"agent-action-replay: warning: {warning}")

    robot = None
    try:
        robot = connect_robot()
        ensure_enabled(robot)

        current = get_jointstate(robot)
        if not is_near_init_pose(current):
            print("agent-action-replay: current pose is not init, moving to init")
            builtin_move(robot, INIT_JOINT_POSITION)

        replay_trajectory(
            robot,
            node,
            trajectory,
            tick_sec=replay_tick_sec,
            gripper_eps=gripper_eps,
        )

        at_init_pose = builtin_move(robot, INIT_JOINT_POSITION)
        robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
        time.sleep(done_hold_seconds)
        node.send_output("jointstate", jointstate_arrow(robot))
        node.send_output("at_init_pose", pa.array([at_init_pose]))
        node.send_output("done", pa.array([trajectory.action_id]))
        print(
            f"AGENT_ACTION_DONE action_id={trajectory.action_id} "
            f"at_init_pose={at_init_pose}"
        )

    except KeyboardInterrupt:
        print("agent-action-replay: interrupted, leaving arm enabled")
        raise
    finally:
        if robot is not None:
            print("agent-action-replay: exit without disable")


if __name__ == "__main__":
    main()

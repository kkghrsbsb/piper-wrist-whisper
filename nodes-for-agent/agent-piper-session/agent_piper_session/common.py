"""Shared Piper helpers for agent-oriented one-shot dora nodes.

This package intentionally does not import from nodes/dora-piper so it can be
copied to another project with minimal coupling.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

import numpy as np
import pyarrow as pa
from piper_control import (
    piper_connect,
    piper_control,
    piper_init,
    piper_interface,
)

INIT_JOINT_POSITION = [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0]
SAFE_DISABLE_POSITION = [-1.5708, 0.0, 0.0, 0.02, 0.5, 0.0]
JOINT_SAFE_SPEED = 10
GRIPPER_EFFORT = 0.5
GRIPPER_RANGE = (0.0, 0.1)
MOVE_TIMEOUT = 12.0
MOVE_THRESHOLD = 0.01
SAFE_SHUTDOWN_PAUSE_SEC = 1.0
ENABLE_PROBE_SAMPLES = 5
ENABLE_PROBE_INITIAL_DELAY = 0.3
ENABLE_PROBE_INTERVAL = 0.05
INIT_POSE_ATOL = 0.02
GRIPPER_EPS = 1e-4

JOINT_LIMITS = [
    (-2.6179, 2.6179),
    (0.0, 3.14),
    (-2.967, 0.0),
    (-1.745, 1.745),
    (-1.22, 1.22),
    (-2.09439, 2.09439),
]


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


def probe_enabled(
    check_fn: Callable[[], bool],
    *,
    label: str,
    samples: int = ENABLE_PROBE_SAMPLES,
    initial_delay: float = ENABLE_PROBE_INITIAL_DELAY,
    interval: float = ENABLE_PROBE_INTERVAL,
) -> bool:
    """Probe enable state several times; any True means enabled."""
    time.sleep(initial_delay)
    results = [check_fn()]
    for _ in range(samples - 1):
        time.sleep(interval)
        results.append(check_fn())
    print(f"agent-piper: {label} enable probe samples={results}")
    return any(results)


def clip_joints(joints: np.ndarray) -> np.ndarray:
    out = joints.astype(np.float32, copy=True)
    for idx, (lo, hi) in enumerate(JOINT_LIMITS):
        out[idx] = np.clip(out[idx], lo, hi)
    return out


def connect_robot():
    ports = piper_connect.find_ports()
    print(f"agent-piper: found ports {ports}")
    piper_connect.activate(ports)
    active = piper_connect.active_ports()
    if not active:
        raise RuntimeError("No active CAN ports found")
    print(f"agent-piper: active ports {active}")

    robot = piper_interface.PiperInterface(can_port=active[0])
    print(f"agent-piper: PiperInterface created on {active[0]}")

    if env_bool("PIPER_SET_INSTALLATION_POS", False):
        print("agent-piper: set_installation_pos(UPRIGHT)")
        robot.set_installation_pos(piper_interface.ArmInstallationPos.UPRIGHT)

    return robot


def ensure_enabled(robot) -> tuple[bool, bool]:
    arm_enabled = probe_enabled(robot.is_arm_enabled, label="arm")
    if not arm_enabled:
        print("agent-piper: arm not enabled, resetting arm")
        piper_init.reset_arm(
            robot,
            arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
            move_mode=piper_interface.MoveMode.JOINT,
        )
        arm_enabled = True

    gripper_enabled = probe_enabled(robot.is_gripper_enabled, label="gripper")
    if not gripper_enabled:
        print("agent-piper: gripper not enabled, resetting gripper")
        piper_init.reset_gripper(robot)
        gripper_enabled = True

    return arm_enabled, gripper_enabled


def builtin_move(
    robot,
    target: list[float],
    *,
    speed: int = JOINT_SAFE_SPEED,
    threshold: float = MOVE_THRESHOLD,
    timeout: float = MOVE_TIMEOUT,
) -> bool:
    """Blocking move using piper-control's BuiltinJointPositionController."""
    with piper_control.BuiltinJointPositionController(
        robot,
        rest_position=None,
    ) as controller:
        robot.set_arm_mode(speed=speed)
        print(f"agent-piper: moving to {target}")
        ok = controller.move_to_position(
            target,
            threshold=threshold,
            timeout=timeout,
        )
        print(f"agent-piper: reached target={ok}")
        return ok


def get_jointstate(robot) -> np.ndarray:
    joints = robot.get_joint_positions()
    gripper_pos, _ = robot.get_gripper_state()
    return np.array(list(joints) + [float(gripper_pos)], dtype=np.float32)


def jointstate_arrow(robot) -> pa.Array:
    return pa.array(get_jointstate(robot), type=pa.float32())


def is_near_init_pose(jointstate: np.ndarray, atol: float = INIT_POSE_ATOL) -> bool:
    return bool(np.allclose(jointstate[:6], INIT_JOINT_POSITION, atol=atol))


def apply_joint_action(
    robot,
    action_value,
    *,
    last_gripper: float | None = None,
    gripper_eps: float = GRIPPER_EPS,
) -> float | None:
    """Validate and write a joint action.

    Returns the latest commanded gripper value. This intentionally does not call
    set_arm_mode on every frame; callers should set mode before replay starts.
    """
    action = np.asarray(action_value, dtype=np.float32)
    if action.shape != (7,):
        print(f"agent-piper: skip joint_action with shape {action.shape}")
        return last_gripper
    if not np.all(np.isfinite(action)):
        print("agent-piper: skip joint_action containing NaN or Inf")
        return last_gripper

    joints = clip_joints(action[:6])
    gripper = float(np.clip(action[6], *GRIPPER_RANGE))

    robot.command_joint_positions(joints.tolist())
    if last_gripper is None or abs(gripper - last_gripper) > gripper_eps:
        robot.command_gripper(position=gripper, effort=GRIPPER_EFFORT)
        return gripper
    return last_gripper


def safe_disable(robot, pause_sec: float = SAFE_SHUTDOWN_PAUSE_SEC) -> None:
    print("agent-piper: safe_disable -> SAFE_DISABLE_POSITION")
    builtin_move(robot, SAFE_DISABLE_POSITION)
    time.sleep(pause_sec)
    print("agent-piper: disable_gripper")
    robot.disable_gripper()
    print("agent-piper: disable_arm")
    robot.disable_arm()

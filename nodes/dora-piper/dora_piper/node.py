"""Real Piper arm writer node.

This node is intentionally narrow: it connects to the real arm, enables it,
moves to the shared INIT_JOINT_POSITION, publishes jointstate at 50Hz, and
writes incoming joint_action frames to piper-control.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

import numpy as np
import pyarrow as pa
from dora import Node
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

JOINT_LIMITS = [
    (-2.6179, 2.6179),
    (0.0, 3.14),
    (-2.967, 0.0),
    (-1.745, 1.745),
    (-1.22, 1.22),
    (-2.09439, 2.09439),
]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def probe_enabled(
    check_fn: Callable[[], bool],
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
    print(f"dora-piper: enable probe samples={results}")
    return any(results)


def clip_joints(joints: np.ndarray) -> np.ndarray:
    out = joints.astype(np.float32, copy=True)
    for idx, (lo, hi) in enumerate(JOINT_LIMITS):
        out[idx] = np.clip(out[idx], lo, hi)
    return out


def connect_and_enable():
    """Connect CAN, create PiperInterface, and enable arm/gripper if needed."""
    ports = piper_connect.find_ports()
    print(f"dora-piper: found ports {ports}")
    piper_connect.activate(ports)
    active = piper_connect.active_ports()
    if not active:
        raise RuntimeError("No active CAN ports found")
    print(f"dora-piper: active ports {active}")

    robot = piper_interface.PiperInterface(can_port=active[0])
    print(f"dora-piper: PiperInterface created on {active[0]}")

    if _env_bool("PIPER_SET_INSTALLATION_POS", False):
        print("dora-piper: set_installation_pos(UPRIGHT)")
        robot.set_installation_pos(piper_interface.ArmInstallationPos.UPRIGHT)

    if not probe_enabled(robot.is_arm_enabled):
        print("dora-piper: arm not enabled, resetting arm...")
        piper_init.reset_arm(
            robot,
            arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
            move_mode=piper_interface.MoveMode.JOINT,
        )

    if not probe_enabled(robot.is_gripper_enabled):
        print("dora-piper: gripper not enabled, resetting gripper...")
        piper_init.reset_gripper(robot)

    return robot


def builtin_move(
    robot,
    target: list[float],
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
        print(f"dora-piper: moving to {target}")
        ok = controller.move_to_position(
            target,
            threshold=threshold,
            timeout=timeout,
        )
        print(f"dora-piper: reached target={ok}")
        return ok


def get_jointstate(robot) -> np.ndarray:
    joints = robot.get_joint_positions()
    gripper_pos, _ = robot.get_gripper_state()
    return np.array(list(joints) + [float(gripper_pos)], dtype=np.float32)


def apply_joint_action(robot, action_value) -> bool:
    """Validate and write one joint_action frame. Returns whether it was sent."""
    action = np.asarray(action_value, dtype=np.float32)
    if action.shape != (7,):
        print(f"dora-piper: skip joint_action with shape {action.shape}")
        return False
    if not np.all(np.isfinite(action)):
        print("dora-piper: skip joint_action containing NaN or Inf")
        return False

    joints = clip_joints(action[:6])
    gripper = float(np.clip(action[6], *GRIPPER_RANGE))

    robot.set_arm_mode(speed=JOINT_SAFE_SPEED)
    robot.command_joint_positions(joints.tolist())
    robot.command_gripper(position=gripper, effort=GRIPPER_EFFORT)
    return True


def safe_shutdown(robot, pause_sec: float = SAFE_SHUTDOWN_PAUSE_SEC) -> None:
    """Move to a safe position, then disable gripper before arm."""
    print("dora-piper: safe_shutdown -> SAFE_DISABLE_POSITION")
    builtin_move(robot, SAFE_DISABLE_POSITION)
    time.sleep(pause_sec)
    print("dora-piper: disable_gripper")
    robot.disable_gripper()
    print("dora-piper: disable_arm")
    robot.disable_arm()
    print("dora-piper: shutdown complete")


def main():
    node = Node()
    robot = None

    try:
        robot = connect_and_enable()

        at_init_pose = builtin_move(robot, INIT_JOINT_POSITION)
        robot.set_arm_mode(speed=JOINT_SAFE_SPEED)

        node.send_output("enabled", pa.array([True]))
        node.send_output("at_init_pose", pa.array([at_init_pose]))
        node.send_output("jointstate", pa.array(get_jointstate(robot), type=pa.float32()))
        print("dora-piper: ready")

        for event in node:
            if event["type"] != "INPUT":
                continue

            eid = event["id"]
            if eid == "tick":
                node.send_output(
                    "jointstate",
                    pa.array(get_jointstate(robot), type=pa.float32()),
                )
            elif eid == "joint_action":
                apply_joint_action(robot, event["value"])
            elif eid == "disable_request":
                requested = bool(event["value"][0].as_py())
                if requested:
                    print("dora-piper: disable_request -> exiting")
                    break

    finally:
        if robot is not None:
            safe_shutdown(robot)


if __name__ == "__main__":
    main()

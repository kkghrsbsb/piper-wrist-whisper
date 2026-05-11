"""Prepare Piper for an agent-controlled serial flow.

The node exits after reaching INIT_JOINT_POSITION and intentionally leaves the
arm enabled.
"""

from __future__ import annotations

import pyarrow as pa
from dora import Node

from agent_piper_session.common import (
    INIT_JOINT_POSITION,
    JOINT_SAFE_SPEED,
    builtin_move,
    connect_robot,
    ensure_enabled,
    jointstate_arrow,
)


def main():
    node = Node()
    robot = None

    try:
        robot = connect_robot()
        arm_enabled, gripper_enabled = ensure_enabled(robot)
        at_init_pose = builtin_move(robot, INIT_JOINT_POSITION)
        robot.set_arm_mode(speed=JOINT_SAFE_SPEED)

        node.send_output("enabled", pa.array([arm_enabled and gripper_enabled]))
        node.send_output("at_init_pose", pa.array([at_init_pose]))
        node.send_output("jointstate", jointstate_arrow(robot))
        node.send_output("ready", pa.array([at_init_pose]))
        print(f"AGENT_READY at_init_pose={at_init_pose}")

    except KeyboardInterrupt:
        print("agent-piper-prepare: interrupted, leaving arm enabled")
        raise
    finally:
        if robot is not None:
            print("agent-piper-prepare: exit without disable")


if __name__ == "__main__":
    main()

"""Disable Piper at the end of an agent-controlled serial flow."""

from __future__ import annotations

import pyarrow as pa
from dora import Node

from agent_piper_session.common import (
    connect_robot,
    probe_enabled,
    safe_disable,
)


def main():
    node = Node()
    robot = connect_robot()

    arm_enabled = probe_enabled(robot.is_arm_enabled, label="arm")
    gripper_enabled = probe_enabled(robot.is_gripper_enabled, label="gripper")
    if arm_enabled or gripper_enabled:
        safe_disable(robot)
        disabled = True
    else:
        print("agent-piper-disable: arm and gripper already disabled")
        disabled = True

    node.send_output("disabled", pa.array([disabled]))
    print(f"AGENT_DISABLED ok={disabled}")


if __name__ == "__main__":
    main()

"""Small dora source for test dataflows.

This is not a production node. It emits a smooth, low-amplitude joint_action
sequence so the MuJoCo simulation backend can be viewed before action-dispatcher
exists.
"""

import numpy as np
import pyarrow as pa
from dora import Node

HOME = np.array([-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0], dtype=np.float32)
PERIOD_STEPS = 400  # 400 * 20ms = 8s per cycle.


def build_joint_action(step: int) -> np.ndarray:
    """Return a smooth demo pose for a 20ms tick stream."""
    phase = 2.0 * np.pi * ((step % PERIOD_STEPS) / PERIOD_STEPS)
    action = HOME.copy()
    action[1] += 0.10 * np.sin(phase)
    action[2] += 0.08 * np.sin(phase + np.pi / 3.0)
    action[4] += 0.06 * np.sin(phase + np.pi)
    action[6] = 0.03 + 0.02 * (0.5 + 0.5 * np.sin(phase - np.pi / 2.0))
    return action.astype(np.float32)


def main():
    node = Node()
    step = 0

    for event in node:
        if event["type"] != "INPUT" or event["id"] != "tick":
            continue
        node.send_output(
            "joint_action",
            pa.array(build_joint_action(step), type=pa.float32()),
        )
        step += 1


if __name__ == "__main__":
    main()

from pathlib import Path

import mujoco
import mujoco.viewer
import numpy as np
from mujoco import MjData, MjModel, mj_forward
from dora import Node

XML_PATH = Path(__file__).parent / "agilex_piper" / "scene.xml"

GRIPPER_SCALE = 0.035 / 0.1


def main():
    model = MjModel.from_xml_path(str(XML_PATH))
    data = MjData(model)

    with mujoco.viewer.launch_passive(model, data) as viewer:
        node = Node()

        for event in node:
            if event["type"] != "INPUT" or event["id"] != "joint_positions":
                continue

            if not viewer.is_running():
                break

            joints = np.asarray(event["value"], dtype=np.float32)
            data.qpos[:6] = joints[:6]
            finger_pos = float(joints[6]) * GRIPPER_SCALE
            data.qpos[6] = finger_pos
            data.qpos[7] = -finger_pos
            mj_forward(model, data)
            viewer.sync()


if __name__ == "__main__":
    main()

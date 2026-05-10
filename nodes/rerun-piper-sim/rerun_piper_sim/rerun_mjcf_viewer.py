import time
from pathlib import Path

import numpy as np
import rerun as rr
import rerun_loader_mjcf
from dora import Node
from mujoco import MjData, MjModel, mj_forward

XML_PATH = Path(__file__).parent / "agilex_piper" / "scene.xml"

GRIPPER_SCALE = 0.035 / 0.1
JOINT_NAMES = ["j1", "j2", "j3", "j4", "j5", "j6", "gripper"]


def main():
    model = MjModel.from_xml_path(str(XML_PATH))
    data = MjData(model)
    logger = rerun_loader_mjcf.MJCFLogger(model, entity_path_prefix="piper")

    rr.init("piper-sim", spawn=True)
    rr.set_time("sim_time", duration=0.0)
    logger.log_model()

    t0 = time.monotonic()
    node = Node()

    for event in node:
        if event["type"] != "INPUT" or event["id"] != "joint_positions":
            continue

        joints = np.asarray(event["value"], dtype=np.float32)
        data.qpos[:6] = joints[:6]
        finger_pos = float(joints[6]) * GRIPPER_SCALE
        data.qpos[6] = finger_pos
        data.qpos[7] = -finger_pos
        mj_forward(model, data)

        rr.set_time("sim_time", duration=time.monotonic() - t0)
        logger.log_data(data)

        for i, name in enumerate(JOINT_NAMES):
            rr.log(f"piper/joints/{name}", rr.Scalars(float(joints[i])))


if __name__ == "__main__":
    main()

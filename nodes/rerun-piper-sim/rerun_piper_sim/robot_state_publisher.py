import pyarrow as pa
from dora import Node
from piper_control import piper_connect, piper_interface


def connect_robot() -> piper_interface.PiperInterface:
    ports = piper_connect.find_ports()
    piper_connect.activate(ports)
    ports = piper_connect.active_ports()
    if not ports:
        raise RuntimeError("No ports found. Make sure the Piper is connected and turned on.")
    robot = piper_interface.PiperInterface(can_port=ports[0])
    print(f"connected on {ports[0]}")
    return robot


def main():
    robot = connect_robot()
    node = Node()

    for event in node:
        if event["type"] != "INPUT":
            continue

        joints = robot.get_joint_positions()  # list[float], 6 rad
        angle, _ = robot.get_gripper_state()  # float, m, [0, 0.1]

        node.send_output(
            "joint_positions",
            pa.array(list(joints) + [angle], type=pa.float32()),
        )


if __name__ == "__main__":
    main()

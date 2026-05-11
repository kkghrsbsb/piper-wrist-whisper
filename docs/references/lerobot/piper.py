import time
from dataclasses import dataclass

from piper_control import piper_connect, piper_control, piper_init, piper_interface

# builtin_control_move
MOVE_TIMEOUT_SECONDS = 12.0
MOVE_THRESHOLD = 0.01

# 6 joints, rad
INIT_JOINT_POSITION = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
# safe position before disable, rad
SAFE_DISABLE_POSITION = [0.0, 0.0, 0.0, 0.02, 0.5, 0.0]

INIT_GRIPPER_POSITION = 0.0
# gripper effort range: [0, 2]
GRIPPER_EFFORT_NOW = 0.5

# builtin position controller speed (0-100)
JOINT_SAFE_SPEED = 10


# To auto-activate CAN on USB plug, run piper-generate-udev-rule at repo root:
# sudo ./piper-generate-udev-rule -i can0 -b 1000000
# from: https://github.com/Reimagine-Robotics/piper_control/blob/main/scripts/piper-generate-udev-rule
def connect_can():
    """Find and activate Piper CAN ports.

    Returns:
        list[str]: active CAN port names (e.g. ["can0"])

    Raises:
        ValueError: if no active CAN ports found
    """
    ports = piper_connect.find_ports()
    print(f"Piper ports: {ports}")

    piper_connect.activate(ports)
    ports = piper_connect.active_ports()

    if not ports:
        raise ValueError(
            "No ports found. Make sure the Piper is connected and turned on."
        )

    return ports


@dataclass
class PiperMotorsBusConfig:
    motors: dict[str, tuple[int, str]]
    # CAN port name. None = auto-discover in connect().
    port: str | None = None


class PiperMotorsBus:
    """Piper motor bus based on piper_control.

    PiperInterface is lazily created in connect() to avoid triggering
    CAN discovery on import or Config instantiation.
    """

    def __init__(self, config: PiperMotorsBusConfig) -> None:
        self.motors = config.motors
        self._config_port = config.port
        self.robot: piper_interface.PiperInterface | None = None
        self._is_connected = False
        self._is_calibrated = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_calibrated(self) -> bool:
        return self._is_calibrated

    @property
    def motor_names(self) -> list[str]:
        return list(self.motors.keys())

    @property
    def motor_models(self) -> list[str]:
        return [model for _, model in self.motors.values()]

    @property
    def motor_indices(self) -> list[int]:
        return [idx for idx, _ in self.motors.values()]

    def probe_arm_enabled_state(
        self,
        settle_seconds=0.3,
        sample_count=5,
        sample_interval=0.05,
    ):
        """Probe arm enabled state via multiple samples."""
        time.sleep(settle_seconds)
        enabled_samples = []
        for _ in range(sample_count):
            enabled_samples.append(self.robot.is_arm_enabled())
            time.sleep(sample_interval)

        is_enabled = any(enabled_samples)
        if is_enabled:
            print(f"arm appears enabled (samples={enabled_samples}), skip reset_arm.")
        else:
            print(f"arm appears disabled (samples={enabled_samples}).")

        return is_enabled

    def probe_gripper_enabled_state(
        self,
        settle_seconds=0.3,
        sample_count=5,
        sample_interval=0.05,
    ):
        """Probe gripper enabled state via multiple samples."""
        time.sleep(settle_seconds)
        enabled_samples = []
        for _ in range(sample_count):
            enabled_samples.append(self.robot.is_gripper_enabled())
            time.sleep(sample_interval)

        is_enabled = any(enabled_samples)
        if is_enabled:
            print(
                f"gripper appears enabled (samples={enabled_samples}), skip enable_gripper."
            )
        else:
            print(f"gripper appears disabled (samples={enabled_samples}).")

        return is_enabled

    def builtin_control_move(
        self,
        reach_position=None,
        safe_speed=JOINT_SAFE_SPEED,
        threshold=MOVE_THRESHOLD,
        timeout=MOVE_TIMEOUT_SECONDS,
    ):
        """Blocking move to a fixed position using BuiltinJointPositionController."""
        with piper_control.BuiltinJointPositionController(
            self.robot,
            rest_position=None,
        ) as controller:
            self.robot.set_arm_mode(speed=safe_speed)
            print(f"moving to position: {reach_position}")
            success = controller.move_to_position(
                reach_position,
                threshold=threshold,
                timeout=timeout,
            )
            print(f"reached target: {success}")

    def safe_shutdown(self):
        """Move to safe position then disable arm and gripper."""
        self.builtin_control_move(reach_position=SAFE_DISABLE_POSITION)

        time.sleep(1)
        self.robot.disable_gripper()
        self.robot.disable_arm()

    def _ensure_robot(self) -> None:
        """Lazily create PiperInterface: discover CAN ports and connect."""
        if self.robot is not None:
            return
        ports = connect_can()
        port = self._config_port if self._config_port else ports[0]
        if port not in ports:
            raise ValueError(
                f"Configured port '{port}' not found in active CAN ports: {ports}"
            )
        self.robot = piper_interface.PiperInterface(can_port=port)
        print(f"PiperInterface created on port: {port}")

    def connect(self, enable: bool) -> None:
        """Enable or disable the arm.

        Args:
            enable: True to enable (start controller), False to disable (shutdown).
        """
        if enable:
            self._ensure_robot()
            is_arm_enabled = self.probe_arm_enabled_state()
            if not is_arm_enabled:
                print("resetting arm")
                piper_init.reset_arm(
                    self.robot,
                    arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
                    move_mode=piper_interface.MoveMode.JOINT,
                )
                is_arm_enabled = True

            is_gripper_enabled = self.probe_gripper_enabled_state()
            if not is_gripper_enabled:
                print("resetting gripper")
                piper_init.reset_gripper(self.robot)
                is_gripper_enabled = True

            print(f"arm enabled: {is_arm_enabled}")
            print(f"current joints: {self.robot.get_joint_positions()}")
            print(f"gripper enabled: {is_gripper_enabled}")
            print(f"current gripper state: {self.robot.get_gripper_state()}")

            self.robot.show_status()
            self._is_connected = True
        else:
            self.safe_shutdown()
            self._is_connected = False

    def set_calibration(self):
        return

    def revert_calibration(self):
        return

    def apply_calibration(self) -> None:
        """Move to init (zero) position."""
        self.builtin_control_move(reach_position=INIT_JOINT_POSITION)
        self._is_calibrated = True

    def apply_calibration_master(self) -> None:
        """Move master arm to init (zero) position."""
        self.builtin_control_move(reach_position=INIT_JOINT_POSITION)
        self._is_calibrated = True

    def read(self) -> dict[str, float]:
        """Read current joint positions and gripper state.

        Returns:
            dict with keys joint_1..joint_6 and gripper, values in rad.
            piper_control handles unit conversion internally.
        """
        joints = self.robot.get_joint_positions()
        gripper_pos, _ = self.robot.get_gripper_state()
        return {
            "joint_1": joints[0],
            "joint_2": joints[1],
            "joint_3": joints[2],
            "joint_4": joints[3],
            "joint_5": joints[4],
            "joint_6": joints[5],
            "gripper": gripper_pos,
        }

    def write(self, target_joints: list[float]) -> None:
        """Send joint position command.

        Args:
            target_joints: list of 7 floats [j1..j6, gripper], in rad.
        """
        q = [float(x) for x in target_joints[:6]]

        gripper_pos = float(target_joints[6])

        self.robot.command_joint_positions(q)
        self.robot.command_gripper(position=gripper_pos, effort=GRIPPER_EFFORT_NOW)

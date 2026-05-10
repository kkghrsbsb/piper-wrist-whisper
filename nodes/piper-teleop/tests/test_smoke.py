import pytest


def test_import_constants():
    from piper_teleop import constants

    assert len(constants.JOINT_LIMITS) == 6
    assert len(constants.SPEED_FACTORS) == len(constants.ARM_MODE_SPEEDS)


def test_import_pure_functions():
    from piper_teleop import pure_functions

    assert hasattr(pure_functions, "apply_deadzone")
    assert hasattr(pure_functions, "clip_target_to_limits")
    assert hasattr(pure_functions, "apply_lead_window")
    assert hasattr(pure_functions, "compute_target_increment")


def test_import_gamepad_module():
    from piper_teleop import gamepad

    assert hasattr(gamepad, "main")
    assert hasattr(gamepad, "ButtonEdge")


def test_import_bringup_module():
    from piper_teleop import bringup

    assert hasattr(bringup, "main")


def test_button_edge_rising_only():
    from piper_teleop.gamepad import ButtonEdge

    e = ButtonEdge()
    assert e.update(False) is False
    assert e.update(True) is True   # rising
    assert e.update(True) is False  # held, no edge
    assert e.update(False) is False
    assert e.update(True) is True   # rising again


def test_gamepad_main_raises_outside_dora():
    from piper_teleop.gamepad import main

    with pytest.raises(Exception):
        main()


def test_bringup_main_raises_outside_dora():
    """main() 第一步是 Node(),非 dora 环境会抛 RuntimeError。"""
    from piper_teleop.bringup import main

    with pytest.raises(Exception):
        main()

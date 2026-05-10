import pytest


def test_main_raises_outside_robot_or_dora():
    from rerun_piper_sim.robot_state_publisher import main

    with pytest.raises(Exception):
        main()

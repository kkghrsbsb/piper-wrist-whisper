import pytest


def test_xml_path_exists():
    from rerun_piper_sim.rerun_mjcf_viewer import XML_PATH

    assert XML_PATH.exists(), f"scene.xml not found at {XML_PATH}"


def test_main_raises_outside_dora():
    from rerun_piper_sim.rerun_mjcf_viewer import main

    with pytest.raises(RuntimeError):
        main()

from pathlib import Path

import mujoco
import numpy as np
import pytest

from mujoco_sim_publisher import publisher


def test_resolve_mjcf_path_default_exists():
    path = publisher.resolve_mjcf_path()

    assert path.exists()
    assert path.name == "scene.xml"


def test_resolve_mjcf_path_env_override(tmp_path: Path):
    custom = tmp_path / "scene.xml"
    custom.write_text("<mujoco/>")

    assert publisher.resolve_mjcf_path({"PIPER_MJCF_PATH": str(custom)}) == custom


def test_resolve_mjcf_path_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        publisher.resolve_mjcf_path({"PIPER_MJCF_PATH": str(tmp_path / "missing.xml")})


def test_validate_joint_action_accepts_float32_shape_7():
    action = publisher.validate_joint_action([0, 1, 2, 3, 4, 5, 0.02])

    assert action.dtype == np.float32
    assert action.shape == (7,)


def test_validate_joint_action_rejects_wrong_shape():
    with pytest.raises(ValueError, match=r"shape \(7,\)"):
        publisher.validate_joint_action(np.zeros(6, dtype=np.float32))


def test_validate_joint_action_rejects_nan_and_inf():
    with pytest.raises(ValueError, match="NaN or Inf"):
        publisher.validate_joint_action([0, 1, 2, 3, 4, 5, np.nan])

    with pytest.raises(ValueError, match="NaN or Inf"):
        publisher.validate_joint_action([0, 1, 2, 3, 4, 5, np.inf])


def test_validate_joint_action_clips_gripper():
    low = publisher.validate_joint_action([0, 0, 0, 0, 0, 0, -1.0])
    high = publisher.validate_joint_action([0, 0, 0, 0, 0, 0, 1.0])

    assert low[6] == pytest.approx(0.0)
    assert high[6] == pytest.approx(0.1)


def test_apply_joint_action_to_data_writes_qpos_mapping():
    model = mujoco.MjModel.from_xml_path(str(publisher.resolve_mjcf_path()))
    data = mujoco.MjData(model)
    action = np.array([1, 2, 3, 4, 5, 6, 0.1], dtype=np.float32)

    publisher.apply_joint_action_to_data(data, action)

    np.testing.assert_allclose(data.qpos[:6], action[:6])
    assert data.qpos[6] == pytest.approx(0.035)
    assert data.qpos[7] == pytest.approx(-0.035)


def test_main_raises_outside_dora():
    with pytest.raises(RuntimeError):
        publisher.main()

from pathlib import Path

import numpy as np
import pytest

from agent_action_replay import replay


def _write_npz(
    path: Path,
    *,
    joints: np.ndarray | None = None,
    timestamps: np.ndarray | None = None,
):
    if joints is None:
        joints = np.array(
            [
                replay.INIT_POSE_7D,
                replay.INIT_POSE_7D
                + np.array([0.0, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            ],
            dtype=np.float32,
        )
    kwargs = {
        "joints": joints,
        "dt": np.float32(0.02),
        "speech": np.array(["hello"]),
    }
    if timestamps is not None:
        kwargs["timestamps"] = timestamps.astype(np.float32)
        kwargs["schema_version"] = np.int32(2)
    np.savez(path, **kwargs)


def test_parse_action_id_strips_whitespace():
    assert replay.parse_action_id("  wave  ") == "wave"


def test_parse_action_id_empty_raises():
    with pytest.raises(ValueError, match="ACTION_ID"):
        replay.parse_action_id("  ")


def test_load_action_npz_legacy(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(path)

    traj = replay.load_action_npz(path, "wave")

    assert traj.action_id == "wave"
    assert traj.joints.shape == (2, 7)
    assert traj.timestamps is None
    assert traj.schema_version == 1


def test_load_action_npz_with_timestamps(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(path, timestamps=np.array([0.0, 0.1], dtype=np.float32))

    traj = replay.load_action_npz(path, "wave")

    assert traj.schema_version == 2
    np.testing.assert_array_equal(
        traj.timestamps,
        np.array([0.0, 0.1], dtype=np.float32),
    )
    assert traj.duration_sec == pytest.approx(0.1)


def test_load_action_npz_rejects_non_monotonic_timestamps(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, timestamps=np.array([0.2, 0.1], dtype=np.float32))

    with pytest.raises(ValueError, match="monotonic"):
        replay.load_action_npz(path, "bad")


def test_due_frame_indices_legacy(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(path)
    traj = replay.load_action_npz(path, "wave")

    idx0, next0 = replay.due_frame_indices(traj, elapsed=0.0, index=0)
    idx1, next1 = replay.due_frame_indices(traj, elapsed=0.0, index=next0)
    idx2, next2 = replay.due_frame_indices(traj, elapsed=0.0, index=next1)

    assert (idx0, next0) == (0, 1)
    assert (idx1, next1) == (1, 2)
    assert (idx2, next2) == (None, 2)


def test_due_frame_indices_timestamped_returns_latest_due(tmp_path: Path):
    path = tmp_path / "wave.npz"
    joints = np.repeat(replay.INIT_POSE_7D[None, :], 3, axis=0)
    _write_npz(
        path,
        joints=joints,
        timestamps=np.array([0.1, 0.2, 0.4], dtype=np.float32),
    )
    traj = replay.load_action_npz(path, "wave")

    idx, next_idx = replay.due_frame_indices(traj, elapsed=0.25, index=0)

    assert idx == 1
    assert next_idx == 2

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
import pytest

from action_dispatcher import dispatcher


def _write_npz(
    path: Path,
    joints: np.ndarray | None = None,
    dt=np.float32(0.02),
    speech: list[str] | None = None,
    timestamps: np.ndarray | None = None,
    schema_version: np.int32 | None = None,
):
    if joints is None:
        joints = np.array(
            [
                dispatcher.PIPER_HOME_POSE,
                dispatcher.PIPER_HOME_POSE + np.array(
                    [0.0, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
                    dtype=np.float32,
                ),
            ],
            dtype=np.float32,
        )
    if speech is None:
        speech = ["hello"]
    kwargs = {
        "joints": joints,
        "dt": dt,
        "speech": np.array(speech),
    }
    if timestamps is not None:
        kwargs["timestamps"] = np.asarray(timestamps, dtype=np.float32)
    if schema_version is not None:
        kwargs["schema_version"] = np.int32(schema_version)
    np.savez(path, **kwargs)


def test_parse_action_id_strips_whitespace():
    assert dispatcher.parse_action_id("  wave  ") == "wave"


def test_parse_action_id_empty_raises():
    with pytest.raises(ValueError, match="ACTION_ID"):
        dispatcher.parse_action_id("  ")


def test_resolve_action_path():
    assert dispatcher.resolve_action_path("wave", Path("actions")) == Path(
        "actions/wave.npz"
    )


def test_load_real_wave_npz_schema():
    path = Path(__file__).resolve().parents[3] / "actions" / "wave.npz"

    traj = dispatcher.load_action_npz(path, "wave")

    assert traj.action_id == "wave"
    assert traj.joints.shape == (2796, 7)
    assert traj.joints.dtype == np.float32
    assert float(traj.dt) == pytest.approx(0.02)
    assert traj.speech == ["好的", "你好", "向你打招呼"]


def test_load_action_npz_success(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(path)

    traj = dispatcher.load_action_npz(path, "wave")

    assert traj.joints.shape == (2, 7)
    assert traj.speech == ["hello"]
    assert traj.timestamps is None
    assert traj.schema_version == 1


def test_load_action_npz_schema_v2_with_timestamps(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(
        path,
        timestamps=np.array([0.05, 0.20], dtype=np.float32),
        schema_version=np.int32(2),
    )

    traj = dispatcher.load_action_npz(path, "wave")

    assert traj.schema_version == 2
    np.testing.assert_array_equal(
        traj.timestamps,
        np.array([0.05, 0.20], dtype=np.float32),
    )
    assert traj.duration_sec == pytest.approx(0.20)


def test_load_action_npz_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        dispatcher.load_action_npz(tmp_path / "missing.npz", "wave")


def test_load_action_npz_missing_field_raises(tmp_path: Path):
    path = tmp_path / "bad.npz"
    np.savez(path, joints=np.zeros((1, 7), dtype=np.float32), dt=np.float32(0.02))

    with pytest.raises(ValueError, match="missing fields"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_wrong_joint_shape(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, joints=np.zeros((1, 6), dtype=np.float32))

    with pytest.raises(ValueError, match=r"\[N, 7\]"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_empty_joints(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, joints=np.zeros((0, 7), dtype=np.float32))

    with pytest.raises(ValueError, match="empty"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_nan_joints(tmp_path: Path):
    path = tmp_path / "bad.npz"
    joints = np.zeros((1, 7), dtype=np.float32)
    joints[0, 0] = np.nan
    _write_npz(path, joints=joints)

    with pytest.raises(ValueError, match="NaN or Inf"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_non_scalar_dt(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, dt=np.array([0.02], dtype=np.float32))

    with pytest.raises(ValueError, match="scalar"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_wrong_dt(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, dt=np.float32(0.01))

    with pytest.raises(ValueError, match="dt must be close"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_empty_speech(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, speech=["", "  "])

    with pytest.raises(ValueError, match="speech"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_wrong_timestamp_shape(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, timestamps=np.array([0.0], dtype=np.float32))

    with pytest.raises(ValueError, match="timestamps"):
        dispatcher.load_action_npz(path, "bad")


def test_load_action_npz_rejects_non_monotonic_timestamps(tmp_path: Path):
    path = tmp_path / "bad.npz"
    _write_npz(path, timestamps=np.array([0.2, 0.1], dtype=np.float32))

    with pytest.raises(ValueError, match="monotonic"):
        dispatcher.load_action_npz(path, "bad")


def test_choose_speech_with_seed_is_candidate():
    speech = ["a", "b", "c"]

    assert dispatcher.choose_speech(speech, rng=__import__("random").Random(0)) in speech


def test_trajectory_warnings_for_real_wave_npz():
    path = Path(__file__).resolve().parents[3] / "actions" / "wave.npz"
    traj = dispatcher.load_action_npz(path, "wave")

    warnings = dispatcher.trajectory_warnings(traj)

    assert any("last frame" in warning for warning in warnings)
    assert any("duration" in warning for warning in warnings)


def test_playback_outputs_frames_then_done_once(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(path)
    traj = dispatcher.load_action_npz(path, "wave")
    playback = dispatcher.Playback(traj, done_hold_ticks=1)

    frame0, done0 = playback.tick()
    frame1, done1 = playback.tick()
    hold, done_hold = playback.tick()
    no_frame, done2 = playback.tick()
    again, done3 = playback.tick()

    np.testing.assert_array_equal(frame0, traj.joints[0])
    np.testing.assert_array_equal(frame1, traj.joints[1])
    np.testing.assert_array_equal(hold, traj.joints[-1])
    assert not done0
    assert not done1
    assert not done_hold
    assert no_frame is None
    assert done2
    assert again is None
    assert not done3


def test_timestamped_playback_outputs_latest_due_frame(tmp_path: Path):
    path = tmp_path / "wave.npz"
    _write_npz(
        path,
        joints=np.array(
            [
                dispatcher.PIPER_HOME_POSE,
                dispatcher.PIPER_HOME_POSE + np.array(
                    [0.0, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
                    dtype=np.float32,
                ),
                dispatcher.PIPER_HOME_POSE + np.array(
                    [0.0, 0.02, 0.0, 0.0, 0.0, 0.0, 0.0],
                    dtype=np.float32,
                ),
            ],
            dtype=np.float32,
        ),
        timestamps=np.array([0.1, 0.2, 0.4], dtype=np.float32),
        schema_version=np.int32(2),
    )
    traj = dispatcher.load_action_npz(path, "wave")
    playback = dispatcher.Playback(traj, done_hold_ticks=1)

    no_frame, done0 = playback.tick(now=100.0)
    frame1, done1 = playback.tick(now=100.25)
    frame2, done2 = playback.tick(now=100.45)
    hold, done_hold = playback.tick(now=100.50)
    no_frame2, done3 = playback.tick(now=100.55)

    assert no_frame is None
    assert not done0
    np.testing.assert_array_equal(frame1, traj.joints[1])
    np.testing.assert_array_equal(frame2, traj.joints[2])
    np.testing.assert_array_equal(hold, traj.joints[-1])
    assert not done1
    assert not done2
    assert not done_hold
    assert no_frame2 is None
    assert done3


def _input_event(eid: str, value=None):
    return {"type": "INPUT", "id": eid, "value": value}


def _make_node(events):
    node = MagicMock()
    node.__iter__ = MagicMock(return_value=iter(events))
    return node


def test_main_replays_npz_and_publishes_done(tmp_path: Path, monkeypatch):
    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    _write_npz(actions_dir / "wave.npz")
    events = [_input_event("tick") for _ in range(4)]
    node = _make_node(events)

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTIONS_DIR", str(actions_dir))
    monkeypatch.setattr(dispatcher, "Node", lambda: node)

    dispatcher.main()

    output_names = [call.args[0] for call in node.send_output.call_args_list]
    assert output_names == [
        "speech_text",
        "joint_action",
        "joint_action",
        "joint_action",
        "done",
    ]

    first_action = node.send_output.call_args_list[1].args[1]
    assert isinstance(first_action, pa.Array)
    assert len(first_action) == 7


def test_main_missing_action_id_exits(monkeypatch):
    monkeypatch.setattr(dispatcher, "Node", lambda: _make_node([]))
    monkeypatch.delenv("ACTION_ID", raising=False)

    with pytest.raises(SystemExit, match="ACTION_ID"):
        dispatcher.main()

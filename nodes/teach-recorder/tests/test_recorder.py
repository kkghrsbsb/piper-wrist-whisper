"""teach-recorder 单元测试。

由 dora Node 驱动的 main() 流程涉及环境变量 + 事件循环,这里用 mock Node
模拟 INPUT 事件序列,验证状态机三段 (WAITING_START → RECORDING → EXIT)、
NPZ 内容、ACTION_SPEECH 缺失报错、空帧拒写四类行为。
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
import pytest

from teach_recorder import recorder


# ── parse_speech ───────────────────────────────────────────


def test_parse_speech_single_entry():
    assert recorder.parse_speech("hello") == ["hello"]


def test_parse_speech_multiple_entries():
    assert recorder.parse_speech("a|b|c") == ["a", "b", "c"]


def test_parse_speech_strips_whitespace():
    assert recorder.parse_speech("  你好  | 你 好哦  ") == ["你好", "你 好哦"]


def test_parse_speech_drops_empty_segments():
    assert recorder.parse_speech("a||b|") == ["a", "b"]


def test_parse_speech_empty_raises():
    with pytest.raises(ValueError):
        recorder.parse_speech("")


def test_parse_speech_only_whitespace_raises():
    with pytest.raises(ValueError):
        recorder.parse_speech("   |  | ")


# ── resolve_output_path ────────────────────────────────────


def test_resolve_output_path_creates_base_dir(tmp_path: Path):
    base = tmp_path / "actions"
    p = recorder.resolve_output_path("wave", base)
    assert base.exists()
    assert p == base / "wave.npz"


def test_resolve_output_path_no_collision(tmp_path: Path):
    p = recorder.resolve_output_path("wave", tmp_path)
    assert p == tmp_path / "wave.npz"


def test_resolve_output_path_appends_timestamp_on_collision(tmp_path: Path):
    (tmp_path / "wave.npz").write_bytes(b"existing")
    p = recorder.resolve_output_path("wave", tmp_path, timestamp="20260511-120000")
    assert p == tmp_path / "wave_20260511-120000.npz"


# ── build_npz_payload ──────────────────────────────────────


def test_build_npz_payload_shapes_and_dtypes():
    frames = [np.full(7, i, dtype=np.float32) for i in range(3)]
    payload = recorder.build_npz_payload(frames, ["a", "b"])
    assert payload["joints"].shape == (3, 7)
    assert payload["joints"].dtype == np.float32
    assert float(payload["dt"]) == pytest.approx(0.02)
    assert list(payload["speech"]) == ["a", "b"]


def test_build_npz_payload_empty_frames_raises():
    with pytest.raises(ValueError, match="empty"):
        recorder.build_npz_payload([], ["a"])


def test_build_npz_payload_rejects_wrong_shape():
    frames = [np.zeros(6, dtype=np.float32)]
    with pytest.raises(ValueError, match=r"\[N, 7\]"):
        recorder.build_npz_payload(frames, ["a"])


# ── main() 状态机端到端 ─────────────────────────────────────


def _input_event(eid: str, value):
    return {"type": "INPUT", "id": eid, "value": value}


def _make_node(events):
    """构造一个迭代 events 的假 Node。"""
    node = MagicMock()
    node.__iter__ = MagicMock(return_value=iter(events))
    return node


def _bool_arr(b: bool):
    return pa.array([b])


def _frame(v: float):
    return pa.array(np.full(7, v, dtype=np.float32))


def test_main_full_recording_cycle(tmp_path: Path, monkeypatch):
    """启动到零位不发 at_init_pose → 第 1 次 X 开始 → 3 帧 → 第 2 次 X 结束 → 写 NPZ。"""
    events = [
        _input_event("jointstate", _frame(99.0)),       # 未 recording,丢弃
        _input_event("at_init_pose", _bool_arr(True)),  # 第 1 次 X → 开始
        _input_event("jointstate", _frame(1.0)),
        _input_event("jointstate", _frame(2.0)),
        _input_event("jointstate", _frame(3.0)),
        _input_event("at_init_pose", _bool_arr(True)),  # 第 2 次 X → 结束
    ]

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTION_SPEECH", "你好|hello")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node(events))

    recorder.main()

    out = tmp_path / "actions" / "wave.npz"
    assert out.exists()
    data = np.load(out)
    np.testing.assert_array_equal(
        data["joints"],
        np.array(
            [[1.0] * 7, [2.0] * 7, [3.0] * 7],
            dtype=np.float32,
        ),
    )
    assert float(data["dt"]) == pytest.approx(0.02)
    assert list(data["speech"]) == ["你好", "hello"]


def test_main_action_speech_missing_exits(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.delenv("ACTION_SPEECH", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node([]))

    with pytest.raises(SystemExit, match="ACTION_SPEECH"):
        recorder.main()


def test_main_action_id_missing_exits(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ACTION_ID", raising=False)
    monkeypatch.setenv("ACTION_SPEECH", "你好")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node([]))

    with pytest.raises(SystemExit, match="ACTION_ID"):
        recorder.main()


def test_main_empty_recording_refuses_to_write(tmp_path: Path, monkeypatch):
    """用户按 X 第 1 次后立刻按第 2 次,没有任何 jointstate → 报错退出,不写文件。"""
    events = [
        _input_event("at_init_pose", _bool_arr(True)),  # 第 1 次 X
        _input_event("at_init_pose", _bool_arr(True)),  # 第 2 次 X
    ]

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTION_SPEECH", "你好")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node(events))

    with pytest.raises(SystemExit, match="empty"):
        recorder.main()

    assert not (tmp_path / "actions" / "wave.npz").exists()


def test_main_at_init_pose_false_ignored(tmp_path: Path, monkeypatch):
    """at_init_pose=False 表示 bringup move 失败,recorder 应忽略,不推进状态。"""
    events = [
        _input_event("at_init_pose", _bool_arr(False)),  # move 失败 → 忽略
        _input_event("at_init_pose", _bool_arr(True)),   # 第 1 次 X → 开始
        _input_event("jointstate", _frame(1.0)),
        _input_event("at_init_pose", _bool_arr(True)),   # 第 2 次 X → 结束
    ]

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTION_SPEECH", "你好")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node(events))

    recorder.main()

    data = np.load(tmp_path / "actions" / "wave.npz")
    assert data["joints"].shape == (1, 7)


def test_main_event_stream_ends_before_stop_does_not_write(
    tmp_path: Path, monkeypatch
):
    """事件流自然结束(dora dataflow 中止)但用户未按第 2 次 X → 不写 NPZ(方案 §5.3)。"""
    events = [
        _input_event("at_init_pose", _bool_arr(True)),  # 第 1 次 X
        _input_event("jointstate", _frame(1.0)),
        # 没有第 2 次 X,事件流结束
    ]

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTION_SPEECH", "你好")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node(events))

    recorder.main()

    assert not (tmp_path / "actions" / "wave.npz").exists()


def test_main_respects_actions_dir_env(tmp_path: Path, monkeypatch):
    """ACTIONS_DIR 环境变量覆盖默认 `actions` 目录。"""
    events = [
        _input_event("at_init_pose", _bool_arr(True)),
        _input_event("jointstate", _frame(1.0)),
        _input_event("at_init_pose", _bool_arr(True)),
    ]
    custom_dir = tmp_path / "custom_actions"

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTION_SPEECH", "你好")
    monkeypatch.setenv("ACTIONS_DIR", str(custom_dir))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node(events))

    recorder.main()

    assert (custom_dir / "wave.npz").exists()
    assert not (tmp_path / "actions").exists()


def test_main_collision_appends_timestamp(tmp_path: Path, monkeypatch):
    """目标文件已存在时,新录制走时间戳后缀,不覆盖。"""
    (tmp_path / "actions").mkdir()
    (tmp_path / "actions" / "wave.npz").write_bytes(b"old")

    events = [
        _input_event("at_init_pose", _bool_arr(True)),  # 第 1 次 X
        _input_event("jointstate", _frame(1.0)),
        _input_event("at_init_pose", _bool_arr(True)),  # 第 2 次 X
    ]

    monkeypatch.setenv("ACTION_ID", "wave")
    monkeypatch.setenv("ACTION_SPEECH", "你好")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "Node", lambda: _make_node(events))

    recorder.main()

    # 原文件未被覆盖
    assert (tmp_path / "actions" / "wave.npz").read_bytes() == b"old"
    # 新文件以 wave_ 开头
    new_files = [
        p for p in (tmp_path / "actions").glob("wave_*.npz")
    ]
    assert len(new_files) == 1

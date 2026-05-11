"""Replay recorded NPZ action trajectories as dora joint_action frames."""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
from dora import Node

DT_SECONDS = 0.02
DT_ATOL = 1e-4
POSE_ATOL = 0.02
MAX_EXPECTED_DURATION_SEC = 30.0
PIPER_HOME_POSE = np.array(
    [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0, 0.0],
    dtype=np.float32,
)


@dataclass(frozen=True)
class ActionTrajectory:
    action_id: str
    joints: np.ndarray
    dt: np.float32
    speech: list[str]
    timestamps: np.ndarray | None = None
    schema_version: int = 1

    @property
    def duration_sec(self) -> float:
        if self.timestamps is not None:
            return float(self.timestamps[-1])
        return int(self.joints.shape[0]) * float(self.dt)


class Playback:
    """Minimal one-shot trajectory playback state."""

    def __init__(self, trajectory: ActionTrajectory, done_hold_ticks: int = 1):
        if done_hold_ticks < 0:
            raise ValueError("done_hold_ticks must be >= 0")
        self.trajectory = trajectory
        self.index = 0
        self.done_hold_ticks = done_hold_ticks
        self._done_sent = False
        self._start_time: float | None = None

    def tick(self, now: float | None = None) -> tuple[np.ndarray | None, bool]:
        """Return (joint_action, done_now) for one dora tick."""
        if self.trajectory.timestamps is not None:
            return self._tick_timestamped(now)
        return self._tick_legacy()

    def _tick_legacy(self) -> tuple[np.ndarray | None, bool]:
        if self.index < self.trajectory.joints.shape[0]:
            frame = self.trajectory.joints[self.index]
            self.index += 1
            return frame, False
        return self._tick_done_hold()

    def _tick_timestamped(
        self,
        now: float | None,
    ) -> tuple[np.ndarray | None, bool]:
        if now is None:
            now = time.monotonic()
        if self._start_time is None:
            self._start_time = now

        if self.index >= self.trajectory.joints.shape[0]:
            return self._tick_done_hold()

        elapsed = now - self._start_time
        frame: np.ndarray | None = None
        timestamps = self.trajectory.timestamps
        assert timestamps is not None
        while (
            self.index < self.trajectory.joints.shape[0]
            and float(timestamps[self.index]) <= elapsed
        ):
            frame = self.trajectory.joints[self.index]
            self.index += 1

        return frame, False

    def _tick_done_hold(self) -> tuple[np.ndarray | None, bool]:
        if self.done_hold_ticks > 0:
            self.done_hold_ticks -= 1
            return self.trajectory.joints[-1], False

        if not self._done_sent:
            self._done_sent = True
            return None, True

        return None, False


def parse_action_id(raw: str) -> str:
    action_id = raw.strip()
    if not action_id:
        raise ValueError("ACTION_ID must contain a non-empty action id")
    return action_id


def resolve_action_path(action_id: str, actions_dir: Path) -> Path:
    return actions_dir / f"{action_id}.npz"


def load_action_npz(path: Path, action_id: str) -> ActionTrajectory:
    if not path.exists():
        raise FileNotFoundError(f"action file not found: {path}")

    with np.load(path) as data:
        required = {"joints", "dt", "speech"}
        missing = required.difference(data.files)
        if missing:
            raise ValueError(f"action npz missing fields: {sorted(missing)}")

        joints = np.asarray(data["joints"], dtype=np.float32)
        dt = np.asarray(data["dt"], dtype=np.float32)
        raw_speech = np.asarray(data["speech"])
        timestamps = (
            np.asarray(data["timestamps"], dtype=np.float32)
            if "timestamps" in data.files
            else None
        )
        schema_version = (
            int(np.asarray(data["schema_version"]).item())
            if "schema_version" in data.files
            else 1
        )

    if joints.ndim != 2 or joints.shape[1] != 7:
        raise ValueError(f"joints must be [N, 7], got shape {joints.shape}")
    if joints.shape[0] == 0:
        raise ValueError("joints is empty")
    if not np.all(np.isfinite(joints)):
        raise ValueError("joints contains NaN or Inf")

    if dt.shape != ():
        raise ValueError(f"dt must be a scalar, got shape {dt.shape}")
    dt_value = np.float32(dt.item())
    if not np.isfinite(dt_value):
        raise ValueError("dt contains NaN or Inf")
    if not np.isclose(float(dt_value), DT_SECONDS, atol=DT_ATOL):
        raise ValueError(f"dt must be close to {DT_SECONDS}, got {float(dt_value)}")

    speech = [str(s).strip() for s in raw_speech.tolist()]
    speech = [s for s in speech if s]
    if not speech:
        raise ValueError("speech must contain at least one non-empty entry")

    if timestamps is not None:
        if timestamps.shape != (joints.shape[0],):
            raise ValueError(
                "timestamps must be [N], "
                f"got shape {timestamps.shape} for {joints.shape[0]} frames"
            )
        if not np.all(np.isfinite(timestamps)):
            raise ValueError("timestamps contains NaN or Inf")
        if np.any(np.diff(timestamps) < 0):
            raise ValueError("timestamps must be monotonic")

    return ActionTrajectory(
        action_id=action_id,
        joints=joints,
        dt=dt_value,
        speech=speech,
        timestamps=timestamps,
        schema_version=schema_version,
    )


def choose_speech(speech: list[str], rng: random.Random | None = None) -> str:
    if not speech:
        raise ValueError("speech must not be empty")
    rng = random if rng is None else rng
    return rng.choice(speech)


def is_near_pose(frame: np.ndarray, pose: np.ndarray = PIPER_HOME_POSE) -> bool:
    return bool(np.allclose(frame, pose, atol=POSE_ATOL))


def trajectory_warnings(trajectory: ActionTrajectory) -> list[str]:
    warnings: list[str] = []
    if not is_near_pose(trajectory.joints[0]):
        warnings.append("first frame is not near PIPER_HOME_POSE")
    if not is_near_pose(trajectory.joints[-1]):
        warnings.append("last frame is not near PIPER_HOME_POSE")
    if trajectory.duration_sec > MAX_EXPECTED_DURATION_SEC:
        warnings.append(
            f"trajectory duration {trajectory.duration_sec:.1f}s exceeds "
            f"{MAX_EXPECTED_DURATION_SEC:.1f}s"
        )
    return warnings


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def main():
    node = Node()

    try:
        action_id = parse_action_id(os.environ.get("ACTION_ID", ""))
        actions_dir = Path(os.environ.get("ACTIONS_DIR", "actions"))
        action_path = resolve_action_path(action_id, actions_dir)
        trajectory = load_action_npz(action_path, action_id)
        done_hold_ticks = _env_int("DONE_HOLD_TICKS", 1)
        dispatch_on_start = _env_bool("DISPATCH_ON_START", True)
    except Exception as exc:
        raise SystemExit(f"action-dispatcher: {exc}") from exc

    print(
        f"action-dispatcher: loaded {action_path} "
        f"frames={trajectory.joints.shape[0]} dt={float(trajectory.dt):.3f} "
        f"duration={trajectory.duration_sec:.1f}s "
        f"schema_version={trajectory.schema_version} "
        f"timebase={'timestamps' if trajectory.timestamps is not None else 'dt'} "
        f"speech_variants={len(trajectory.speech)}"
    )
    for warning in trajectory_warnings(trajectory):
        print(f"action-dispatcher: warning: {warning}")

    playback = Playback(trajectory, done_hold_ticks=done_hold_ticks)
    started = dispatch_on_start
    speech_sent = False
    done_sent = False

    if not started:
        print("action-dispatcher: DISPATCH_ON_START=false, idle")

    for event in node:
        if event["type"] != "INPUT" or event["id"] != "tick":
            continue
        if not started or done_sent:
            continue

        if not speech_sent:
            speech_text = choose_speech(trajectory.speech)
            node.send_output("speech_text", pa.array([speech_text]))
            speech_sent = True
            print(f"action-dispatcher: speech_text={speech_text}")

        frame, done_now = playback.tick(time.monotonic())
        if frame is not None:
            node.send_output("joint_action", pa.array(frame, type=pa.float32()))
        if done_now:
            node.send_output("done", pa.array([trajectory.action_id]))
            done_sent = True
            print(f"action-dispatcher: done action_id={trajectory.action_id}")


if __name__ == "__main__":
    main()

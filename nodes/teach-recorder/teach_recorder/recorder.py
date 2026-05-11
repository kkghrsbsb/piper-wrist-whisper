"""teach-recorder 节点:按 X 键边沿累积 jointstate,写 actions/{ACTION_ID}.npz。

状态机(详见 docs/src/plan/2026-05-teach-recorder-plan.md §3.1):

  WAITING_START
    收到 at_init_pose=True(用户第 1 次按 X)→ 进入 RECORDING

  RECORDING
    每帧 jointstate 追加 buffer 和相对时间戳。
    收到 at_init_pose=True(用户第 2 次按 X)→ 进入 STOPPING。

  STOPPING
    继续追加 jointstate,直到看到 INIT_JOINT_POSITION 且至少保持 1 秒后写 NPZ。

注:bringup 启动到 ZERO_POSITION 时只 publish at_zero,不会触发 teach-recorder,
因此不再需要 startup_consumed 标志。

dora 输入: jointstate (float32[7])、at_init_pose (bool)
dora 输出: 无(纯文件 IO 节点)
"""

import os
import time
from pathlib import Path
from typing import Iterable

import numpy as np
import pyarrow as pa
from dora import Node

DT_SECONDS = 0.02  # 与 bringup 50Hz tick 对应
SCHEMA_VERSION = 2
STOP_HOLD_SECONDS = 1.0
STOPPING_TIMEOUT_SECONDS = 5.0
INIT_JOINT_ATOL = 0.02
INIT_JOINT_POSITION = np.array(
    [-1.5708, 0.25, -1.0, 0.0, 0.5, 0.0],
    dtype=np.float32,
)


def parse_speech(raw: str) -> list[str]:
    """解析 ACTION_SPEECH:用 `|` 分隔多条变体,空白和空段被丢弃。

    至少返回 1 条;若 raw 为空或全是空白,抛 ValueError。
    """
    parts = [s.strip() for s in raw.split("|")]
    parts = [s for s in parts if s]
    if not parts:
        raise ValueError("ACTION_SPEECH must contain at least one non-empty entry")
    return parts


def resolve_output_path(
    action_id: str,
    base_dir: Path,
    timestamp: str | None = None,
) -> Path:
    """返回 NPZ 输出路径。已存在时追加时间戳后缀,不静默覆盖。"""
    base_dir.mkdir(parents=True, exist_ok=True)
    candidate = base_dir / f"{action_id}.npz"
    if not candidate.exists():
        return candidate
    ts = timestamp if timestamp is not None else time.strftime("%Y%m%d-%H%M%S")
    return base_dir / f"{action_id}_{ts}.npz"


def build_npz_payload(
    frames: Iterable[np.ndarray],
    speech_list: list[str],
    timestamps: Iterable[float] | None = None,
    dt: float = DT_SECONDS,
) -> dict:
    """把 buffer 整理成 np.savez 的 kwargs。frames 不能为空。"""
    frames_list = list(frames)
    if not frames_list:
        raise ValueError("frames is empty, refusing to write NPZ")
    joints_arr = np.asarray(frames_list, dtype=np.float32)
    if joints_arr.ndim != 2 or joints_arr.shape[1] != 7:
        raise ValueError(
            f"frames must be [N, 7], got shape {joints_arr.shape}"
        )
    if timestamps is None:
        timestamps_arr = (
            np.arange(joints_arr.shape[0], dtype=np.float32) * np.float32(dt)
        )
    else:
        timestamps_arr = np.asarray(list(timestamps), dtype=np.float32)
    if timestamps_arr.shape != (joints_arr.shape[0],):
        raise ValueError(
            "timestamps must be [N], "
            f"got shape {timestamps_arr.shape} for {joints_arr.shape[0]} frames"
        )
    if not np.all(np.isfinite(timestamps_arr)):
        raise ValueError("timestamps contains NaN or Inf")
    if np.any(np.diff(timestamps_arr) < 0):
        raise ValueError("timestamps must be monotonic")
    return {
        "joints": joints_arr,
        "timestamps": timestamps_arr,
        "dt": np.float32(dt),
        "speech": np.array(speech_list),
        "schema_version": np.int32(SCHEMA_VERSION),
    }


def _bool_from_event(value) -> bool:
    """从 dora INPUT event 的 value (pyarrow Array) 提取布尔。

    bringup 用 pa.array([True/False]) 发布;取第一个元素。
    """
    if isinstance(value, pa.Array):
        return bool(value[0].as_py())
    arr = np.asarray(value)
    return bool(arr.flat[0])


def is_near_init_pose(frame: np.ndarray) -> bool:
    """只用 6 关节判定是否回到 INIT_JOINT_POSITION,夹爪不作为硬约束。"""
    return bool(np.allclose(frame[:6], INIT_JOINT_POSITION, atol=INIT_JOINT_ATOL))


def main():
    node = Node()

    action_id = os.environ.get("ACTION_ID", "").strip()
    if not action_id:
        raise SystemExit("teach-recorder: ACTION_ID 未设置,录制终止")

    raw_speech = os.environ.get("ACTION_SPEECH", "")
    try:
        speech_list = parse_speech(raw_speech)
    except ValueError as e:
        raise SystemExit(f"teach-recorder: {e}")

    # ACTIONS_DIR 默认相对 CWD = dora 把每个节点的 CWD 设为 YAML 所在目录,
    # 因此 dataflow YAML 应显式传 `ACTIONS_DIR: ../../actions` 让 NPZ 落到 repo 根。
    base_dir = Path(os.environ.get("ACTIONS_DIR", "actions"))
    print(
        f"teach-recorder: ACTION_ID={action_id} "
        f"speech_variants={len(speech_list)} "
        f"actions_dir={base_dir.resolve()}"
    )

    # 状态
    state = "WAITING_START"
    start_time: float | None = None
    stop_signal_time: float | None = None
    init_seen = False
    should_write = False
    frames: list[np.ndarray] = []
    timestamps: list[float] = []

    print("teach-recorder: WAITING_START (press X to start recording)")

    for event in node:
        if event["type"] != "INPUT":
            continue

        now = time.monotonic()
        eid = event["id"]

        if eid == "at_init_pose":
            ok = _bool_from_event(event["value"])
            if not ok:
                # 到位失败:忽略本次信号
                print("teach-recorder: at_init_pose=False, ignored")
                continue

            if state == "WAITING_START":
                state = "RECORDING"
                start_time = now
                frames = []
                timestamps = []
                print("teach-recorder: RECORDING (press X again to stop)")
                continue

            if state == "RECORDING":
                state = "STOPPING"
                stop_signal_time = now
                init_seen = False
                print(
                    "teach-recorder: STOPPING "
                    f"(waiting for init pose + {STOP_HOLD_SECONDS:.1f}s hold, "
                    f"frames={len(frames)})"
                )
                continue

            # STOPPING 中重复 at_init_pose=True 不改变状态,等待 jointstate 确认末帧。
            continue

        elif eid == "jointstate":
            if state not in {"RECORDING", "STOPPING"}:
                continue
            arr = np.asarray(event["value"], dtype=np.float32)
            if arr.shape != (7,):
                print(f"teach-recorder: skip frame with shape {arr.shape}")
                continue
            frames.append(arr)
            if start_time is None:
                raise SystemExit("teach-recorder: internal error: start_time is None")
            timestamps.append(now - start_time)

            if state == "STOPPING":
                if is_near_init_pose(arr):
                    init_seen = True
                if stop_signal_time is None:
                    raise SystemExit(
                        "teach-recorder: internal error: stop_signal_time is None"
                    )
                stopped_for = now - stop_signal_time
                if init_seen and stopped_for >= STOP_HOLD_SECONDS:
                    should_write = True
                    print(
                        "teach-recorder: stop condition satisfied "
                        f"(frames={len(frames)}, hold={stopped_for:.2f}s)"
                    )
                    break
                if stopped_for >= STOPPING_TIMEOUT_SECONDS:
                    should_write = True
                    print(
                        "teach-recorder: warning: STOPPING timeout, "
                        f"init_seen={init_seen}, frames={len(frames)}"
                    )
                    break

    if not should_write:
        # 用户未按第 2 次 X 就退出(dataflow 中止 / 事件流耗尽)→ 不写文件
        # 方案 §5.3:"录失败就重录"。
        print(
            f"teach-recorder: exited before stop condition "
            f"(state={state}, frames={len(frames)}), nothing saved"
        )
        return

    try:
        payload = build_npz_payload(frames, speech_list, timestamps=timestamps)
    except ValueError as e:
        raise SystemExit(f"teach-recorder: {e}")

    if not is_near_init_pose(payload["joints"][-1]):
        print("teach-recorder: warning: last frame is not near INIT_JOINT_POSITION")

    out_path = resolve_output_path(action_id, base_dir)
    np.savez(out_path, **payload)
    print(
        f"teach-recorder: saved {out_path} "
        f"(N={payload['joints'].shape[0]}, "
        f"duration={float(payload['timestamps'][-1]):.2f}s, "
        f"dt={float(payload['dt'])})"
    )


if __name__ == "__main__":
    main()

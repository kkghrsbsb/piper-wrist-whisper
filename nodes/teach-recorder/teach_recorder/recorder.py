"""teach-recorder 节点:按 X 键边沿累积 jointstate,写 actions/{ACTION_ID}.npz。

状态机(详见 docs/src/plan/2026-05-teach-recorder-plan.md §3.1):

  WAITING_START
    收到 at_init_pose=True(用户第 1 次按 X)→ 进入 RECORDING

  RECORDING
    每帧 jointstate 追加 buffer。
    收到 at_init_pose=True(用户第 2 次按 X)→ 写 NPZ → 退出。

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
    return {
        "joints": joints_arr,
        "dt": np.float32(dt),
        "speech": np.array(speech_list),
    }


def _bool_from_event(value) -> bool:
    """从 dora INPUT event 的 value (pyarrow Array) 提取布尔。

    bringup 用 pa.array([True/False]) 发布;取第一个元素。
    """
    if isinstance(value, pa.Array):
        return bool(value[0].as_py())
    arr = np.asarray(value)
    return bool(arr.flat[0])


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
    recording = False
    stop_received = False
    frames: list[np.ndarray] = []

    print("teach-recorder: WAITING_START (press X to start recording)")

    for event in node:
        if event["type"] != "INPUT":
            continue

        eid = event["id"]

        if eid == "at_init_pose":
            ok = _bool_from_event(event["value"])
            if not ok:
                # 到位失败:忽略本次信号
                print("teach-recorder: at_init_pose=False, ignored")
                continue

            if not recording:
                recording = True
                frames = []
                print("teach-recorder: RECORDING (press X again to stop)")
                continue

            # recording=True 时再次触发 → 结束并写文件
            stop_received = True
            print(f"teach-recorder: stop signal received, frames={len(frames)}")
            break

        elif eid == "jointstate":
            if not recording:
                continue
            arr = np.asarray(event["value"], dtype=np.float32)
            if arr.shape != (7,):
                print(f"teach-recorder: skip frame with shape {arr.shape}")
                continue
            frames.append(arr)

    if not stop_received:
        # 用户未按第 2 次 X 就退出(dataflow 中止 / 事件流耗尽)→ 不写文件
        # 方案 §5.3:"录失败就重录"。
        print(
            f"teach-recorder: exited without stop signal "
            f"(recording={recording}, frames={len(frames)}), nothing saved"
        )
        return

    try:
        payload = build_npz_payload(frames, speech_list)
    except ValueError as e:
        raise SystemExit(f"teach-recorder: {e}")

    out_path = resolve_output_path(action_id, base_dir)
    np.savez(out_path, **payload)
    print(
        f"teach-recorder: saved {out_path} "
        f"(N={payload['joints'].shape[0]}, dt={float(payload['dt'])})"
    )


if __name__ == "__main__":
    main()

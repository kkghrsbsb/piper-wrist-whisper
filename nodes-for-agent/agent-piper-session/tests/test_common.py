import numpy as np

from agent_piper_session import common


def test_clip_joints_clips_each_joint_limit():
    raw = np.array([99, -99, 99, 99, 99, -99], dtype=np.float32)

    clipped = common.clip_joints(raw)

    expected = np.array([2.6179, 0.0, 0.0, 1.745, 1.22, -2.09439], dtype=np.float32)
    np.testing.assert_allclose(clipped, expected)


def test_is_near_init_pose_ignores_gripper():
    frame = np.array([*common.INIT_JOINT_POSITION, 0.08], dtype=np.float32)

    assert common.is_near_init_pose(frame)


def test_is_near_init_pose_rejects_joint_error():
    frame = np.array([*common.INIT_JOINT_POSITION, 0.0], dtype=np.float32)
    frame[1] += 0.1

    assert not common.is_near_init_pose(frame)

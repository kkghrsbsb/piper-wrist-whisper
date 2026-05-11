"""piper-bringup 启动 + 退出流程的单元测试,使用 mock PiperInterface。

只测试可隔离的辅助函数(probe_enabled / safe_shutdown / get_jointstate /
connect_and_enable);main() 的 dora 事件循环只做 smoke(在 test_smoke.py)。
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from piper_teleop import bringup
from piper_teleop.constants import (
    GRIPPER_EFFORT,
    INIT_JOINT_POSITION,
    JOINT_LIMITS,
    JOINT_SAFE_SPEED,
    SAFE_DISABLE_POSITION,
    ZERO_POSITION,
)


# ── probe_enabled ──────────────────────────────────────────


def test_probe_enabled_all_true():
    fn = MagicMock(return_value=True)
    assert bringup.probe_enabled(fn, samples=5, initial_delay=0, interval=0) is True
    assert fn.call_count == 5


def test_probe_enabled_all_false():
    fn = MagicMock(return_value=False)
    assert bringup.probe_enabled(fn, samples=5, initial_delay=0, interval=0) is False
    assert fn.call_count == 5


def test_probe_enabled_or_logic_one_true():
    """5 次采样中任意一次为 True 即视为已使能。"""
    fn = MagicMock(side_effect=[False, False, True, False, False])
    assert bringup.probe_enabled(fn, samples=5, initial_delay=0, interval=0) is True


# ── safe_shutdown ──────────────────────────────────────────


def test_safe_shutdown_calls_in_correct_order(monkeypatch):
    """safe_shutdown 顺序:move 到安全位 → disable_gripper → disable_arm。"""
    calls = []

    def fake_move(robot, target):
        calls.append(("move", list(target)))
        return True

    monkeypatch.setattr(bringup, "builtin_move", fake_move)

    robot = MagicMock()
    robot.disable_gripper.side_effect = lambda: calls.append(("disable_gripper",))
    robot.disable_arm.side_effect = lambda: calls.append(("disable_arm",))

    bringup.safe_shutdown(robot, pause_sec=0)

    assert calls[0] == ("move", SAFE_DISABLE_POSITION)
    assert calls[1] == ("disable_gripper",)
    assert calls[2] == ("disable_arm",)


def test_safe_shutdown_disables_gripper_before_arm(monkeypatch):
    """夹爪必须在机械臂之前失能(原 connect_init.py 的安全约定)。"""
    monkeypatch.setattr(bringup, "builtin_move", lambda r, t: True)

    robot = MagicMock()
    bringup.safe_shutdown(robot, pause_sec=0)

    # MagicMock 的 mock_calls 顺序就是真实调用顺序
    method_names = [c[0] for c in robot.mock_calls]
    gripper_idx = method_names.index("disable_gripper")
    arm_idx = method_names.index("disable_arm")
    assert gripper_idx < arm_idx


# ── get_jointstate ─────────────────────────────────────────


def test_get_jointstate_format():
    """返回 7 元素 float32 数组:6 关节 + 1 夹爪。"""
    robot = MagicMock()
    robot.get_joint_positions.return_value = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    robot.get_gripper_state.return_value = (0.05, 0.5)

    state = bringup.get_jointstate(robot)

    assert state.shape == (7,)
    assert state.dtype == np.float32
    np.testing.assert_allclose(state[:6], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    assert state[6] == pytest.approx(0.05)


def test_get_jointstate_drops_gripper_effort():
    """gripper_state 是 (pos, effort) 元组,只取 pos。"""
    robot = MagicMock()
    robot.get_joint_positions.return_value = [0.0] * 6
    robot.get_gripper_state.return_value = (0.07, 999.0)

    state = bringup.get_jointstate(robot)
    assert state[6] == pytest.approx(0.07)


# ── connect_and_enable ─────────────────────────────────────


def test_connect_and_enable_raises_when_no_active_ports(monkeypatch):
    monkeypatch.setattr(bringup.piper_connect, "find_ports", lambda: [])
    monkeypatch.setattr(bringup.piper_connect, "activate", lambda ports: None)
    monkeypatch.setattr(bringup.piper_connect, "active_ports", lambda: [])

    with pytest.raises(RuntimeError, match="No active CAN ports"):
        bringup.connect_and_enable()


def test_connect_and_enable_skips_reset_when_already_enabled(monkeypatch):
    """已使能时不调用 reset_arm / reset_gripper。"""
    fake_robot = MagicMock()
    fake_robot.is_arm_enabled.return_value = True
    fake_robot.is_gripper_enabled.return_value = True

    monkeypatch.setattr(bringup.piper_connect, "find_ports", lambda: ["can0"])
    monkeypatch.setattr(bringup.piper_connect, "activate", lambda ports: None)
    monkeypatch.setattr(bringup.piper_connect, "active_ports", lambda: ["can0"])
    monkeypatch.setattr(
        bringup.piper_interface, "PiperInterface", lambda can_port: fake_robot
    )
    # 跳过 sleep 加速测试
    monkeypatch.setattr(bringup.time, "sleep", lambda s: None)

    reset_arm = MagicMock()
    reset_gripper = MagicMock()
    monkeypatch.setattr(bringup.piper_init, "reset_arm", reset_arm)
    monkeypatch.setattr(bringup.piper_init, "reset_gripper", reset_gripper)

    robot = bringup.connect_and_enable()

    assert robot is fake_robot
    reset_arm.assert_not_called()
    reset_gripper.assert_not_called()


def test_connect_and_enable_resets_when_not_enabled(monkeypatch):
    """未使能时调用 reset_arm + reset_gripper。"""
    fake_robot = MagicMock()
    fake_robot.is_arm_enabled.return_value = False
    fake_robot.is_gripper_enabled.return_value = False

    monkeypatch.setattr(bringup.piper_connect, "find_ports", lambda: ["can0"])
    monkeypatch.setattr(bringup.piper_connect, "activate", lambda ports: None)
    monkeypatch.setattr(bringup.piper_connect, "active_ports", lambda: ["can0"])
    monkeypatch.setattr(
        bringup.piper_interface, "PiperInterface", lambda can_port: fake_robot
    )
    monkeypatch.setattr(bringup.time, "sleep", lambda s: None)

    reset_arm = MagicMock()
    reset_gripper = MagicMock()
    monkeypatch.setattr(bringup.piper_init, "reset_arm", reset_arm)
    monkeypatch.setattr(bringup.piper_init, "reset_gripper", reset_gripper)

    bringup.connect_and_enable()

    reset_arm.assert_called_once()
    reset_gripper.assert_called_once()


def test_connect_and_enable_uses_first_active_port(monkeypatch):
    """active_ports() 返回多个时,使用第一个。"""
    captured = {}

    def fake_constructor(can_port):
        captured["port"] = can_port
        m = MagicMock()
        m.is_arm_enabled.return_value = True
        m.is_gripper_enabled.return_value = True
        return m

    monkeypatch.setattr(bringup.piper_connect, "find_ports", lambda: ["can0", "can1"])
    monkeypatch.setattr(bringup.piper_connect, "activate", lambda ports: None)
    monkeypatch.setattr(
        bringup.piper_connect, "active_ports", lambda: ["can0", "can1"]
    )
    monkeypatch.setattr(bringup.piper_interface, "PiperInterface", fake_constructor)
    monkeypatch.setattr(bringup.time, "sleep", lambda s: None)

    bringup.connect_and_enable()
    assert captured["port"] == "can0"


# ── builtin_move ───────────────────────────────────────────


def test_builtin_move_sets_arm_mode_and_calls_controller(monkeypatch):
    """builtin_move 进入 controller → set_arm_mode → move_to_position。"""
    robot = MagicMock()

    fake_ctrl = MagicMock()
    fake_ctrl.move_to_position.return_value = True

    fake_ctx = MagicMock()
    fake_ctx.__enter__ = MagicMock(return_value=fake_ctrl)
    fake_ctx.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        bringup.piper_control,
        "BuiltinJointPositionController",
        lambda r, rest_position=None: fake_ctx,
    )

    ok = bringup.builtin_move(robot, ZERO_POSITION, speed=10, threshold=0.01, timeout=8.0)

    assert ok is True
    robot.set_arm_mode.assert_called_once_with(speed=10)
    fake_ctrl.move_to_position.assert_called_once_with(
        ZERO_POSITION, threshold=0.01, timeout=8.0
    )


# ── apply_joint_action ─────────────────────────────────────


def test_apply_joint_action_calls_command_methods():
    robot = MagicMock()
    action = np.array([0.1, 0.2, -0.3, 0.4, 0.5, -0.6, 0.05], dtype=np.float32)

    bringup.apply_joint_action(robot, action)

    # command_joint_positions 收 list[float],长度 6
    robot.command_joint_positions.assert_called_once()
    args, _ = robot.command_joint_positions.call_args
    joints_arg = args[0]
    assert isinstance(joints_arg, list)
    assert len(joints_arg) == 6
    np.testing.assert_allclose(joints_arg, [0.1, 0.2, -0.3, 0.4, 0.5, -0.6], atol=1e-6)

    # command_gripper(position, effort)
    robot.command_gripper.assert_called_once_with(pytest.approx(0.05), GRIPPER_EFFORT)


def test_apply_joint_action_clips_joint_limits():
    """超出 JOINT_LIMITS 的关节角应被裁剪。"""
    robot = MagicMock()
    # J2 上界 3.14,这里给 5.0;J3 上界 0,这里给 1.0
    action = np.array([0.0, 5.0, 1.0, 0.0, 0.0, 0.0, 0.05], dtype=np.float32)

    bringup.apply_joint_action(robot, action)

    args, _ = robot.command_joint_positions.call_args
    joints = args[0]
    assert joints[1] == pytest.approx(JOINT_LIMITS[1][1])  # J2 → 上界 3.14
    assert joints[2] == pytest.approx(JOINT_LIMITS[2][1])  # J3 → 上界 0


def test_apply_joint_action_clips_gripper_range():
    """夹爪位置超出 [0, 0.1] 应被裁剪。"""
    robot = MagicMock()
    action_high = np.array([0.0] * 6 + [0.5], dtype=np.float32)
    action_low = np.array([0.0] * 6 + [-0.2], dtype=np.float32)

    bringup.apply_joint_action(robot, action_high)
    args_high, _ = robot.command_gripper.call_args
    assert args_high[0] == pytest.approx(0.1)

    robot.reset_mock()
    bringup.apply_joint_action(robot, action_low)
    args_low, _ = robot.command_gripper.call_args
    assert args_low[0] == pytest.approx(0.0)


def test_apply_joint_action_uses_constant_effort():
    """夹爪 effort 固定来自 GRIPPER_EFFORT,不来自 action。"""
    robot = MagicMock()
    action = np.zeros(7, dtype=np.float32)

    bringup.apply_joint_action(robot, action)

    args, _ = robot.command_gripper.call_args
    assert args[1] == GRIPPER_EFFORT


def test_apply_joint_action_rejects_wrong_shape():
    robot = MagicMock()
    with pytest.raises(ValueError, match="shape"):
        bringup.apply_joint_action(robot, np.zeros(6, dtype=np.float32))
    with pytest.raises(ValueError, match="shape"):
        bringup.apply_joint_action(robot, np.zeros(8, dtype=np.float32))
    robot.command_joint_positions.assert_not_called()
    robot.command_gripper.assert_not_called()


# ── main() 事件分支 ─────────────────────────────────────────


def _input(eid, value):
    return {"type": "INPUT", "id": eid, "value": value}


def _make_main_env(monkeypatch, events):
    """共享的 main() 测试夹具:mock 真机连接 + builtin_move + Node。

    返回 (fake_robot, fake_node, move_calls)。
    """
    fake_robot = MagicMock()
    fake_robot.get_joint_positions.return_value = [0.0] * 6
    fake_robot.get_gripper_state.return_value = (0.0, 0.0)

    monkeypatch.setattr(bringup, "connect_and_enable", lambda: fake_robot)

    move_calls = []

    def fake_move(robot, target, **kwargs):
        move_calls.append(list(target))
        return True

    monkeypatch.setattr(bringup, "builtin_move", fake_move)
    monkeypatch.setattr(bringup, "safe_shutdown", lambda r, **kw: None)

    fake_node = MagicMock()
    fake_node.__iter__ = MagicMock(return_value=iter(events))
    monkeypatch.setattr(bringup, "Node", lambda: fake_node)

    return fake_robot, fake_node, move_calls


def _outputs_by_id(fake_node):
    """把 fake_node.send_output 的所有调用按 id 聚合成 {id: [values]}。"""
    out: dict[str, list] = {}
    for call in fake_node.send_output.call_args_list:
        eid = call.args[0]
        out.setdefault(eid, []).append(call.args[1])
    return out


def test_main_startup_moves_to_zero_and_publishes_at_zero(monkeypatch):
    """启动校准 → builtin_move(ZERO_POSITION) → publish at_zero。

    零位是 bringup 启动目标(也是 Y 键 home_request 目标)。
    初始位是预设动作起止位,由 X 键 init_pose_request 触发,启动不去那里。
    """
    events = [_input("disable_request", None)]  # 立刻退出主循环
    fake_robot, fake_node, move_calls = _make_main_env(monkeypatch, events)

    bringup.main()

    # 首次 move 目标是 ZERO_POSITION
    assert move_calls[0] == list(ZERO_POSITION)
    outputs = _outputs_by_id(fake_node)
    # 启动 publish enabled / at_zero / jointstate
    assert "enabled" in outputs
    assert "at_zero" in outputs
    assert "jointstate" in outputs
    # 启动不该发 at_init_pose(那个只由 init_pose_request 触发)
    assert "at_init_pose" not in outputs


def test_main_init_pose_request_moves_to_init_pose_and_publishes_at_init_pose(
    monkeypatch,
):
    """收到 init_pose_request → builtin_move(INIT_JOINT_POSITION) → publish at_init_pose。"""
    events = [
        _input("init_pose_request", None),
        _input("disable_request", None),
    ]
    fake_robot, fake_node, move_calls = _make_main_env(monkeypatch, events)

    bringup.main()

    # 启动 move 到 ZERO,init_pose_request move 到 INIT_JOINT_POSITION
    assert move_calls[0] == list(ZERO_POSITION)
    assert list(INIT_JOINT_POSITION) in move_calls

    # 处理完 init_pose_request 后切回 command 模式
    set_arm_mode_calls = [
        c for c in fake_robot.set_arm_mode.call_args_list
        if c.kwargs.get("speed") == JOINT_SAFE_SPEED
    ]
    assert len(set_arm_mode_calls) >= 2  # 启动后 + init_pose_request 后

    # 启动 publish at_zero,init_pose_request publish at_init_pose
    outputs = _outputs_by_id(fake_node)
    assert len(outputs["at_zero"]) == 1
    assert len(outputs["at_init_pose"]) == 1


def test_main_home_request_moves_to_zero(monkeypatch):
    """Y 键 home_request 目标是 ZERO_POSITION,publish at_zero。"""
    events = [
        _input("home_request", None),
        _input("disable_request", None),
    ]
    fake_robot, fake_node, move_calls = _make_main_env(monkeypatch, events)

    bringup.main()

    # 启动 + home_request 都 move 到 ZERO_POSITION,从不去 INIT_JOINT_POSITION
    assert list(INIT_JOINT_POSITION) not in move_calls
    assert all(m == list(ZERO_POSITION) for m in move_calls)
    # 两次 at_zero(启动 + home_request),没有 at_init_pose
    outputs = _outputs_by_id(fake_node)
    assert len(outputs["at_zero"]) == 2
    assert "at_init_pose" not in outputs


def test_main_disable_request_exits_loop(monkeypatch):
    """disable_request 触发 break,safe_shutdown 在 finally 中调用。"""
    events = [_input("disable_request", None)]
    fake_robot, fake_node, move_calls = _make_main_env(monkeypatch, events)

    shutdown_called = []
    monkeypatch.setattr(
        bringup, "safe_shutdown", lambda r, **kw: shutdown_called.append(True)
    )

    bringup.main()

    assert shutdown_called == [True]

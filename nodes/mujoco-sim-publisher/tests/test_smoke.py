def test_import_publisher():
    from mujoco_sim_publisher import publisher

    assert hasattr(publisher, "main")
    assert hasattr(publisher, "resolve_mjcf_path")
    assert hasattr(publisher, "validate_joint_action")
    assert hasattr(publisher, "apply_joint_action_to_data")


def test_demo_source_is_smooth_and_bounded():
    from mujoco_sim_publisher import test_source

    first = test_source.build_joint_action(0)
    second = test_source.build_joint_action(1)

    assert first.shape == (7,)
    assert second.shape == (7,)
    assert abs(float(second[1] - first[1])) < 0.01
    assert 0.0 <= float(first[6]) <= 0.1
    assert 0.0 <= float(second[6]) <= 0.1

import pytest


def test_import_recorder():
    from teach_recorder import recorder

    assert hasattr(recorder, "main")
    assert hasattr(recorder, "parse_speech")
    assert hasattr(recorder, "resolve_output_path")
    assert hasattr(recorder, "build_npz_payload")


def test_recorder_main_raises_outside_dora():
    """main() 第一步是 Node(),非 dora 环境会抛异常。"""
    from teach_recorder.recorder import main

    with pytest.raises(Exception):
        main()

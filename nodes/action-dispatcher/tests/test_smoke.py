import pytest


def test_import_dispatcher():
    from action_dispatcher import dispatcher

    assert hasattr(dispatcher, "main")
    assert hasattr(dispatcher, "load_action_npz")
    assert hasattr(dispatcher, "Playback")


def test_main_raises_outside_dora():
    from action_dispatcher.dispatcher import main

    with pytest.raises(RuntimeError):
        main()

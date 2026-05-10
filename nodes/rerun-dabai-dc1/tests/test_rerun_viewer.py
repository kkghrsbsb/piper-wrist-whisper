import pytest


def test_main_raises_outside_dora():
    from rerun_dabai_dc1.rerun_viewer import main

    with pytest.raises(RuntimeError):
        main()

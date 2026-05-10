import numpy as np
import pytest


def test_temporal_filter_first_frame_unchanged():
    from rerun_dabai_dc1.orbbec_camera import TemporalFilter

    f = TemporalFilter(alpha=0.5)
    frame = np.ones((4, 4, 3), dtype=np.float32) * 42
    result = f.process(frame)
    np.testing.assert_array_equal(result, frame)


def test_temporal_filter_blends():
    from rerun_dabai_dc1.orbbec_camera import TemporalFilter

    f = TemporalFilter(alpha=0.5)
    frame1 = np.zeros((4, 4, 3), dtype=np.float32)
    frame2 = np.ones((4, 4, 3), dtype=np.float32) * 100
    f.process(frame1)
    result = f.process(frame2)
    # alpha * frame2 + (1 - alpha) * frame1 = 0.5 * 100 + 0.5 * 0 = 50
    np.testing.assert_allclose(result, 50, atol=1)


def test_yuyv_to_bgr_output_shape():
    from rerun_dabai_dc1.orbbec_camera import yuyv_to_bgr

    h, w = 4, 4
    frame = np.zeros(h * w * 2, dtype=np.uint8)
    result = yuyv_to_bgr(frame, w, h)
    assert result.shape == (h, w, 3)


def test_nv12_to_bgr_output_shape():
    from rerun_dabai_dc1.orbbec_camera import nv12_to_bgr

    h, w = 4, 4
    frame = np.zeros(h * w * 3 // 2, dtype=np.uint8)
    result = nv12_to_bgr(frame, w, h)
    assert result.shape == (h, w, 3)


def test_nv21_to_bgr_output_shape():
    from rerun_dabai_dc1.orbbec_camera import nv21_to_bgr

    h, w = 4, 4
    frame = np.zeros(h * w * 3 // 2, dtype=np.uint8)
    result = nv21_to_bgr(frame, w, h)
    assert result.shape == (h, w, 3)


def test_main_raises_outside_dora():
    from rerun_dabai_dc1.orbbec_camera import main

    with pytest.raises(Exception):
        main()

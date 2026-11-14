"""Проверки переходов NDVI с общим валидным покрытием."""

from __future__ import annotations

import numpy as np

from glist.tools.landcover import compare_classifications


def test_identical_valid_inputs_have_no_lost_or_new() -> None:
    start = np.ones((4, 4), dtype=np.uint8)
    end = np.ones((4, 4), dtype=np.uint8)
    valid = np.ones((4, 4), dtype=bool)

    retained, lost, new = compare_classifications(start, end, valid)

    assert retained.all()
    assert not lost.any()
    assert not new.any()


def test_known_transitions_produce_expected_classes() -> None:
    start = np.array([[1, 1, 0, 0]], dtype=np.uint8)
    end = np.array([[1, 0, 1, 0]], dtype=np.uint8)
    valid = np.ones_like(start, dtype=bool)

    retained, lost, new = compare_classifications(start, end, valid)

    np.testing.assert_array_equal(retained, [[True, False, False, False]])
    np.testing.assert_array_equal(lost, [[False, True, False, False]])
    np.testing.assert_array_equal(new, [[False, False, True, False]])


def test_invalid_pixels_are_not_changes() -> None:
    start = np.array([[1, 1, 0]], dtype=np.uint8)
    end = np.array([[0, 1, 1]], dtype=np.uint8)
    valid = np.array([[False, True, False]], dtype=bool)

    retained, lost, new = compare_classifications(start, end, valid)

    # В невалидных пикселях переходы 1→0 и 0→1 не считаются изменением.
    np.testing.assert_array_equal(retained, [[False, True, False]])
    assert not lost.any()
    assert not new.any()

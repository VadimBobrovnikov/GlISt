"""Landcover classification tool (T052).

Provides binary vegetation classification and change detection
between two time periods based on NDVI thresholds.
"""

from typing import Any

import numpy as np


def classify_vegetation(ndvi: np.ndarray, threshold: float = 0.3) -> np.ndarray:
    """Binary vegetation classification based on NDVI threshold.

    Classification rules:
    - NDVI > threshold → vegetation (1)
    - NDVI <= threshold → non-vegetation (0)

    Args:
        ndvi: NDVI array (float).
        threshold: NDVI threshold for vegetation classification (default: 0.3).

    Returns:
        Binary array (dtype=uint8) where 1 = vegetation, 0 = non-vegetation.
    """
    binary = (ndvi > threshold).astype(np.uint8)
    return binary


def detect_changes(
    classification_start: np.ndarray,
    classification_end: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compare two vegetation classifications to detect changes.

    (Alias/wrapper for API compatibility with test imports.)

    Computes three change zones:
    - retained: was vegetation (1) and still vegetation (1)
    - lost: was vegetation (1) and became non-vegetation (0)
    - new: was non-vegetation (0) and became vegetation (1)

    Args:
        classification_start: Binary classification from start period (1=veg, 0=no veg).
        classification_end: Binary classification from end period (1=veg, 0=no veg).

    Returns:
        Tuple of (retained, lost, new) binary arrays.
    """
    return compare_classifications(classification_start, classification_end)


def compare_classifications(
    classification_start: np.ndarray,
    classification_end: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compare two vegetation classifications to detect changes.

    Computes three change zones:
    - retained: was vegetation (1) and still vegetation (1)
    - lost: was vegetation (1) and became non-vegetation (0)
    - new: was non-vegetation (0) and became vegetation (1)

    Args:
        classification_start: Binary classification from start period (1=veg, 0=no veg).
        classification_end: Binary classification from end period (1=veg, 0=no veg).

    Returns:
        Tuple of (retained, lost, new) binary arrays.
    """
    # Ensure uint8 for bitwise operations
    start = classification_start.astype(np.uint8)
    end = classification_end.astype(np.uint8)

    # retained: 1 -> 1 (vegetation persisted)
    retained = (start & end).astype(bool)

    # lost: 1 -> 0 (vegetation disappeared)
    lost = ((start == 1) & (end == 0)).astype(bool)

    # new: 0 -> 1 (new vegetation appeared)
    new = ((start == 0) & (end == 1)).astype(bool)

    return retained, lost, new


def detect_change_zones(
    ndvi_start: np.ndarray,
    ndvi_end: np.ndarray,
    threshold: float = 0.3,
) -> dict[str, Any]:
    """Detect vegetation change zones between two time periods.

    Full pipeline: calculate NDVI for both periods, classify vegetation,
    then compare classifications to find retained, lost, and new zones.

    Args:
        ndvi_start: NDVI array from start period.
        ndvi_end: NDVI array from end period.
        threshold: NDVI threshold for vegetation classification (default: 0.3).

    Returns:
        Dictionary with keys:
        - "retained": binary mask of persisted vegetation
        - "lost": binary mask of lost vegetation
        - "new": binary mask of new vegetation
        - "class_start": binary classification of start period
        - "class_end": binary classification of end period
    """
    # Classify both periods
    class_start = classify_vegetation(ndvi_start, threshold)
    class_end = classify_vegetation(ndvi_end, threshold)

    # Compare classifications
    retained, lost, new = compare_classifications(class_start, class_end)

    return {
        "retained": retained,
        "lost": lost,
        "new": new,
        "class_start": class_start,
        "class_end": class_end,
    }
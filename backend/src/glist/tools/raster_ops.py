"""Raster operations tool (T051).

Provides functions for working with raster data using rasterio and numpy:
- NDVI calculation
- Cloud masking
- Median composite creation
"""

from typing import Any, cast

import numpy as np
import rasterio


def calculate_ndvi(red_band: np.ndarray, nir_band: np.ndarray) -> np.ndarray:
    """Calculate Normalized Difference Vegetation Index (NDVI).

    NDVI = (NIR - RED) / (NIR + RED)

    Args:
        red_band: Red band array (float or int).
        nir_band: Near-infrared band array (float or int).

    Returns:
        NDVI array as float32. Values outside [−1, 1] are clipped.
        Returns array of same shape as inputs.
    """
    # Convert to float to avoid integer division issues
    red = red_band.astype(np.float32)
    nir = nir_band.astype(np.float32)

    # Handle division by zero: where NIR + RED == 0, set NDVI to 0
    denominator = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(denominator != 0, (nir - red) / denominator, 0.0)

    # Clip to valid NDVI range [-1, 1]
    ndvi = np.clip(ndvi, -1.0, 1.0)
    return ndvi


def mask_clouds(
    nir_band: np.ndarray,
    red_band: np.ndarray,
    nir_threshold: float = 0.2,
    red_threshold: float = 0.1,
) -> np.ndarray:
    """Simple cloud masking based on band reflectance thresholds.

    Clouds typically have high NIR and low RED reflectance.

    Args:
        nir_band: Near-infrared band array.
        red_band: Red band array.
        nir_threshold: NIR reflectance threshold above which pixels are suspicious.
        red_threshold: RED reflectance threshold below which pixels are suspicious.

    Returns:
        Boolean mask array where True = likely cloud, False = likely clear.
    """
    nir_high = nir_band > nir_threshold
    red_low = red_band < red_threshold
    cloud_mask = nir_high & red_low
    return cloud_mask


def mask_clouds_ndvi_threshold(
    ndvi: np.ndarray, ndvi_threshold: float = 0.1
) -> np.ndarray:
    """Mask pixels with extremely low or high NDVI (potential clouds/snow).

    Args:
        ndvi: NDVI array.
        ndvi_threshold: Pixels with |NDVI| > threshold are masked.

    Returns:
        Boolean mask where True = likely cloud/snow (should be masked).
    """
    # Extreme NDVI values may indicate clouds (very high) or snow (very high positive)
    # or water/barren (very negative). Use moderate threshold.
    extreme = np.abs(ndvi) > ndvi_threshold
    return extreme


def median_composite(
    arrays: list[np.ndarray], axis: int = 0
) -> np.ndarray:
    """Create a median composite from a list of raster arrays.

    Args:
        arrays: List of numpy arrays of the same shape.
        axis: Axis along which to compute median (default: 0, i.e., across images).

    Returns:
        Median composite array of the same shape as input arrays.
    """
    if not arrays:
        raise ValueError("Array list is empty")

    # Check all arrays have the same shape
    shape = arrays[0].shape
    for i, arr in enumerate(arrays):
        if arr.shape != shape:
            raise ValueError(f"Array {i} has shape {arr.shape}, expected {shape}")

    # Compute median along the specified axis
    composite = np.median(np.stack(arrays, axis=axis), axis=axis)
    return cast(np.ndarray, composite).astype(np.float32)


def read_band(
    filepath: str, band_number: int = 1, scale: float = 1.0
) -> tuple[np.ndarray, dict[str, Any]]:
    """Read a band from a GeoTIFF file.

    Args:
        filepath: Path to GeoTIFF file.
        band_number: Band number to read (1-indexed).
        scale: Scale factor to apply (e.g., 0.0001 for S2 reflectance).

    Returns:
        Tuple of (band_array, affine_transform).
    """
    with rasterio.open(filepath) as src:
        band = src.read(band_number) * scale
        transform = src.transform
    return band, transform
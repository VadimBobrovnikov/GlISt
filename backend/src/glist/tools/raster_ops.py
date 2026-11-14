"""Raster operations tool (T051).

Provides functions for working with raster data using rasterio and numpy:
- NDVI calculation
- Cloud masking
- Median composite creation
"""

import logging
import math
import numbers
import warnings
from typing import Any, cast

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject as warp_reproject
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

logger = logging.getLogger(__name__)

_SCALE_EPS = 1e-12


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


# SCL-классы Sentinel-2 L2A, которые считаем непригодными:
# 0 no-data, 1 saturated, 3 cloud shadow, 8/9 cloud, 10 cirrus, 11 snow/ice.
_SCL_INVALID_CLASSES = frozenset({0, 1, 3, 8, 9, 10, 11})


def mask_scl(
    scl: np.ndarray,
    invalid_classes: set[int] | frozenset[int] | None = None,
) -> np.ndarray:
    """Возвращает маску непригодных пикселей по SCL Sentinel-2.

    Args:
        scl: Массив классов SCL (uint8/uint16).
        invalid_classes: Классы, считающиеся непригодными; по умолчанию
            ``{0, 1, 3, 8, 9, 10, 11}`` (дыры, тени, облака, снег).

    Returns:
        Boolean-массив True = пиксель нужно исключить из композита.
    """
    classes = (
        invalid_classes
        if invalid_classes is not None
        else _SCL_INVALID_CLASSES
    )
    return np.isin(scl.astype(np.int32), list(classes))


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

    # Compute median along the specified axis (NaN-aware: masked pixels ignored)
    stacked = np.stack(arrays, axis=axis)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        composite = np.nanmedian(stacked, axis=axis)
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


def resolve_band_scaling(
    declared_scale: float | None,
    declared_offset: float | None,
    requested_scale: float = 1.0,
    requested_offset: float = 0.0,
) -> tuple[float, float, str]:
    """Выбирает ровно одну коррекцию яркости: из растра или запрошенную.

    Семантика STAC/GDAL: ``value = dn * scale + offset``, где ``offset`` уже
    в единицах результата (отражательная способность). Если растр объявляет
    собственную шкалу/смещение, они применяются ровно один раз, а запрошенные
    значения не добавляются (иначе была бы двойная коррекция).

    Смещение BOA_ADD_OFFSET в DN нужно привести к единицам результата заранее:
    ``offset = boa_dn_offset * scale`` (это делает вызывающий код явно).

    Args:
        declared_scale: Шкала из метаданных растра (``src.scales``).
        declared_offset: Смещение из метаданных растра (``src.offsets``).
        requested_scale: Шкала, запрошенная вызывающим кодом.
        requested_offset: Смещение в единицах результата.

    Returns:
        ``(scale, offset, source)``; ``source`` — ``"dataset"`` или
        ``"requested"``.
    """
    if (
        declared_scale is not None
        and declared_offset is not None
        and (
            abs(declared_scale - 1.0) > _SCALE_EPS
            or abs(declared_offset) > _SCALE_EPS
        )
    ):
        return float(declared_scale), float(declared_offset), "dataset"
    return float(requested_scale), float(requested_offset), "requested"


def _dataset_scaling(
    src: Any,
    band_number: int,
) -> tuple[float | None, float | None]:
    """Читает объявленные scale/offset растра (только реальные числа)."""
    index = band_number - 1
    try:
        scales = src.scales
        offsets = src.offsets
    except Exception:  # noqa: BLE001 — метаданные не обязательны
        return None, None
    scale_value = None
    offset_value = None
    try:
        raw_scale = scales[index] if scales else None
        if isinstance(raw_scale, numbers.Real):
            scale_value = float(cast(float, raw_scale))
    except (TypeError, ValueError, IndexError):
        scale_value = None
    try:
        raw_offset = offsets[index] if offsets else None
        if isinstance(raw_offset, numbers.Real):
            offset_value = float(cast(float, raw_offset))
    except (TypeError, ValueError, IndexError):
        offset_value = None
    return scale_value, offset_value


def grid_pixel_area_m2(
    transform: Affine,
    crs: Any,
    latitude: float,
) -> float | None:
    """Площадь пикселя сетки в м² (географическая CRS — приближённо).

    Для площадных CRS используется определитель аффинного преобразования и
    коэффициент единиц. Для географических CRS ширина/высота пикселя в
    градусах переводится в метры по широте центра окна. Если CRS неизвестна,
    площадь не рассчитывается (``None``) — выдумывать единицы нельзя.
    """
    determinant = abs(
        float(transform.a) * float(transform.e)
        - float(transform.b) * float(transform.d)
    )
    if determinant <= 0:
        return None
    try:
        crs_obj = CRS.from_user_input(crs) if crs is not None else None
    except Exception:  # noqa: BLE001 — неизвестная CRS
        return None
    if crs_obj is None:
        return None
    if crs_obj.is_projected:
        factor = 1.0
        try:
            units = crs_obj.linear_units_factor
            if isinstance(units, tuple) and len(units) >= 2:
                factor = float(units[1])
        except Exception:  # noqa: BLE001 — оставляем метры
            factor = 1.0
        return determinant * factor * factor
    if crs_obj.is_geographic:
        lat = math.radians(latitude)
        m_per_deg_lat = (
            111132.92
            - 559.82 * math.cos(2 * lat)
            + 1.175 * math.cos(4 * lat)
        )
        m_per_deg_lon = (
            111412.84 * math.cos(lat)
            - 93.5 * math.cos(3 * lat)
            + 0.118 * math.cos(5 * lat)
        )
        return (
            abs(float(transform.a)) * m_per_deg_lon
            * abs(float(transform.e)) * m_per_deg_lat
        )
    return None


def reproject_to_grid(
    data: np.ndarray,
    *,
    src_transform: Affine,
    src_crs: Any,
    dst_transform: Affine,
    dst_crs: Any,
    dst_shape: tuple[int, int],
    resampling: Resampling = Resampling.nearest,
) -> np.ndarray:
    """Приводит растр к целевой сетке (CRS/transform/shape).

    Нужен, чтобы сравнивать даты пиксель-в-пиксель, когда сетки сцен
    различаются. Геометрия/площади полигонов здесь не считаются.
    """
    rows, cols = int(dst_shape[0]), int(dst_shape[1])
    if rows <= 0 or cols <= 0 or data.size == 0:
        return np.full((max(rows, 0), max(cols, 0)), np.nan, dtype=np.float32)
    destination = np.full((rows, cols), np.nan, dtype=np.float32)
    warp_reproject(
        source=data.astype(np.float32),
        destination=destination,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return destination


def _read_options(src: Any) -> dict[str, Any]:
    """Опции чтения окна: у края тайла окно не должно обрезаться.

    ``from_bounds`` может дать отрицательный ``col_off``/``row_off`` (bbox
    выходит за тайл). Без ``boundless`` rasterio вернёт только пересечение,
    и композит молча сожмётся до самого узкого окна. Для реального датасета
    читаем окно целиком, а пиксели вне тайла заполняем 0: SCL-класс 0 —
    NoData, поэтому RED/NIR там будут замаскированы.
    """
    if isinstance(src.width, numbers.Integral) and isinstance(
        src.height, numbers.Integral
    ):
        return {"boundless": True, "fill_value": 0}
    # Моки/нестандартные датасеты: сохраняем обычное чтение.
    return {}


def read_cog_window(
    cog_url: str,
    bbox: tuple[float, float, float, float],
    band_number: int = 1,
    scale: float = 1.0,
    out_shape: tuple[int, int] | None = None,
    resampling: Resampling = Resampling.nearest,
    offset: float = 0.0,
) -> tuple[np.ndarray, Any]:
    """Читает только окно COG-файла, покрывающее bbox (EPSG:4326).

    Окно вычисляется в системе координат растра; читается один band,
    без загрузки всего файла (важно для облачных COG Sentinel-2).

    Args:
        cog_url: URL или путь к Cloud-Optimized GeoTIFF.
        bbox: (minx, miny, maxx, maxy) в EPSG:4326.
        band_number: Номер band в файле (по умолчанию 1).
        scale: Множитель значений (например, 0.0001 для reflectance S2).
        out_shape: Привести окно к размеру ``(rows, cols)`` (например, SCL 20 м
            к сетке RED/NIR 10 м).
        resampling: Метод ресемплинга при ``out_shape``.
        offset: Смещение в единицах результата (отражательная способность):
            ``value = dn * scale + offset``. Для BOA_ADD_OFFSET в DN приведите
            заранее: ``offset = boa_dn * scale``. Объявленные в растре
            scale/offset имеют приоритет и не применяются повторно.

    Returns:
        Кортеж ``(data, transform)``: массив окна и Affine-трансформ окна.
    """
    left, bottom, right, top = bbox
    with (
        rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            GDAL_HTTP_CONNECTTIMEOUT="10",
            GDAL_HTTP_TIMEOUT="30",
            GDAL_HTTP_MAX_RETRY="1",
        ),
        rasterio.open(cog_url) as src,
    ):
        if src.crs is not None and src.crs.to_epsg() != 4326:
            left, bottom, right, top = transform_bounds(
                "EPSG:4326",
                src.crs,
                left,
                bottom,
                right,
                top,
                densify_pts=21,
            )
        window = from_bounds(
            left,
            bottom,
            right,
            top,
            transform=src.transform,
        )
        base_transform = src.window_transform(window)
        declared_scale, declared_offset = _dataset_scaling(src, band_number)
        scale, offset, scaling_source = resolve_band_scaling(
            declared_scale, declared_offset, scale, offset
        )
        if scaling_source == "dataset":
            logger.debug(
                "read_cog_window: использую scale/offset растра %s (%s, %s)",
                cog_url,
                scale,
                offset,
            )
        if out_shape is None:
            raw = src.read(band_number, window=window, **_read_options(src))
            data = raw.astype(np.float32) * scale + offset
            transform = base_transform
        else:
            out_rows, out_cols = int(out_shape[0]), int(out_shape[1])
            if out_rows <= 0 or out_cols <= 0:
                raise ValueError("out_shape должен содержать положительные размеры")
            raw = src.read(
                band_number,
                window=window,
                out_shape=(out_rows, out_cols),
                resampling=resampling,
                **_read_options(src),
            )
            data = raw.astype(np.float32) * scale + offset
            transform = base_transform @ Affine.scale(
                window.width / out_cols,
                window.height / out_rows,
            )
    return data, transform
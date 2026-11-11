from datetime import UTC, datetime

import aacgmv2
import numpy as np
import ppigrf
from numpy.typing import NDArray

_FALLBACK_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
_FALLBACK_NOTE = "AACGM: дата вне покрытия коэффициентов, использована эпоха 2025-01-01"


def aacgm(
    lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64], alt_km: NDArray[np.float64], t: datetime
) -> tuple[NDArray[np.float64], NDArray[np.float64], list[str]]:
    """Инвариантная широта и долгота AACGM-v2 в градусах и список ограничений.

    Точки, где AACGM не определён (вблизи магнитного экватора), остаются NaN.
    При дате вне покрытия коэффициентов расчёт повторяется на эпохе 2025-01-01 с ограничением.
    """
    limitations: list[str] = []
    for epoch in (t, _FALLBACK_EPOCH):
        try:
            mlat, mlon, _ = aacgmv2.get_aacgm_coord_arr(
                lat_deg, lon_deg, alt_km, epoch.replace(tzinfo=None), method="TRACE"
            )
        except (ValueError, RuntimeError) as exc:
            limitations.append(f"AACGM: {exc}")
            continue
        if epoch is _FALLBACK_EPOCH:
            limitations.append(_FALLBACK_NOTE)
        return np.asarray(mlat, dtype=np.float64), np.asarray(mlon, dtype=np.float64), limitations
    raise RuntimeError("AACGM недоступен: " + "; ".join(limitations))


def l_shell(mlat_deg: NDArray[np.float64]) -> NDArray[np.float64]:
    """Дипольная оболочка L = 1 / cos²(mlat)."""
    c = np.cos(np.radians(mlat_deg))
    return np.asarray(1.0 / (c * c), dtype=np.float64)


def b_total_nt(
    lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64], alt_km: NDArray[np.float64], t: datetime
) -> NDArray[np.float64]:
    """Модуль поля IGRF в нТл по ppigrf; вход геодезический, высота над эллипсоидом.

    ppigrf.igrf принимает долготу, широту, высоту в км и дату; возвращает восток, север, вверх формы (1, N).
    """
    be, bn, bu = ppigrf.igrf(lon_deg, lat_deg, alt_km, t.replace(tzinfo=None))
    total = np.sqrt(
        np.asarray(be, dtype=np.float64) ** 2
        + np.asarray(bn, dtype=np.float64) ** 2
        + np.asarray(bu, dtype=np.float64) ** 2
    )
    return np.asarray(total, dtype=np.float64).reshape(-1)


def in_saa(b_total: NDArray[np.float64], threshold_nt: float | None) -> NDArray[np.bool_]:
    """Точки с полем ниже порога; без сверенного порога все точки False (D31, D34)."""
    if threshold_nt is None:
        return np.zeros(b_total.shape, dtype=bool)
    return np.asarray(b_total < threshold_nt, dtype=bool)

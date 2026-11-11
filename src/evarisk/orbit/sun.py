import math
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray

EARTH_RADIUS_KM = 6371.0
_J2000 = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)


def _solar_longitude_rad(d: float) -> float:
    """Видимая эклиптическая долгота Солнца по USNO «Approximate Solar Coordinates», d это сутки от J2000."""
    g = math.radians((357.529 + 0.98560028 * d) % 360.0)
    q = (280.459 + 0.98564736 * d) % 360.0
    return math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))


def solar_longitude_deg(t: datetime) -> float:
    """Видимая эклиптическая долгота Солнца в градусах, [0, 360).

    Долгота отсчитывается от равноденствия даты; в таблицах метеорных потоков долгота даётся в J2000 и отличается
    на прецессию, около 0.014°/год (0.3° или 8 ч в 2024 году). При переносе дат между годами разница почти
    полностью сокращается.
    """
    d = (t - _J2000).total_seconds() / 86400.0
    return math.degrees(_solar_longitude_rad(d)) % 360.0


def sun_direction_gcrs(t: datetime) -> NDArray[np.float64]:
    """Единичный вектор на Солнце в экваториальной системе даты.

    Алгоритм USNO «Approximate Solar Coordinates», точность около 0.01° на 1950–2050.
    """
    d = (t - _J2000).total_seconds() / 86400.0
    lam = _solar_longitude_rad(d)
    eps = np.radians(23.439 - 0.00000036 * d)
    x = np.cos(lam)
    y = np.cos(eps) * np.sin(lam)
    z = np.sin(eps) * np.sin(lam)
    vec = np.array([x, y, z], dtype=np.float64)
    return np.asarray(vec / np.sqrt(x * x + y * y + z * z), dtype=np.float64)


def is_sunlit(r_km: NDArray[np.float64], sun_dir: NDArray[np.float64]) -> bool:
    """Цилиндрическая тень Земли радиусом EARTH_RADIUS_KM вдоль направления на Солнце."""
    along = float(np.dot(r_km, sun_dir))
    if along >= 0.0:
        return True
    perp = float(np.linalg.norm(r_km - along * sun_dir))
    return perp > EARTH_RADIUS_KM

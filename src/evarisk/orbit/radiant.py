import numpy as np
from numpy.typing import NDArray


def radiant_unit_gcrs(ra_deg: float, dec_deg: float) -> NDArray[np.float64]:
    """Единичный вектор на радиант в экваториальной системе по прямому восхождению и склонению в градусах."""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], dtype=np.float64)


def radiant_visible(
    r_km: NDArray[np.float64], radiant: NDArray[np.float64], shield_radius_km: float
) -> NDArray[np.bool_]:
    """Радиант выше опущенного горизонта экранирующей сферы для каждой точки r_km формы (N, 3).

    Опущенный горизонт лежит на угле 90° + arccos(shield_radius / |r|) от местной вертикали;
    сфера радиуса shield_radius_km это Земля с атмосферой (D29).
    """
    norms = np.linalg.norm(r_km, axis=1)
    if np.any(norms <= shield_radius_km):
        raise ValueError("точка траектории внутри экранирующей сферы")
    cos_angle = (r_km @ radiant) / norms
    dip = np.arccos(shield_radius_km / norms)
    return np.asarray(cos_angle > np.cos(np.pi / 2 + dip), dtype=bool)

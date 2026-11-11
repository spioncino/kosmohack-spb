from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from skyfield.api import load, wgs84
from skyfield.constants import AU_KM
from skyfield.positionlib import Geocentric

_TS = load.timescale(builtin=True)
_EARTH_CENTER = 399


def geodetic_from_gcrs(
    times: list[datetime], r_km: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Геодезические координаты WGS84 из GCRS: широта °, долгота ° в (-180, 180], высота км над эллипсоидом."""
    t = _TS.from_datetimes(times)
    pos = Geocentric(r_km.T / AU_KM, None, t, center=_EARTH_CENTER)
    geo = wgs84.geographic_position_of(pos)
    lon = np.asarray(geo.longitude.degrees, dtype=np.float64)
    lon = np.where(lon <= -180.0, lon + 360.0, lon)
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    lat = np.asarray(geo.latitude.degrees, dtype=np.float64)
    alt = np.asarray(geo.elevation.km, dtype=np.float64)
    return lat, np.asarray(lon, dtype=np.float64), alt

from datetime import UTC, datetime

import numpy as np

from evarisk.orbit.sun import is_sunlit, solar_longitude_deg, sun_direction_gcrs


def test_declination_at_solstice_and_equinox() -> None:
    dec_june = np.degrees(np.arcsin(sun_direction_gcrs(datetime(2024, 6, 20, 20, 51, tzinfo=UTC))[2]))
    dec_march = np.degrees(np.arcsin(sun_direction_gcrs(datetime(2024, 3, 20, 3, 6, tzinfo=UTC))[2]))
    assert abs(dec_june - 23.44) < 0.05 and abs(dec_march) < 0.05


def test_shadow_geometry() -> None:
    s = np.array([1.0, 0.0, 0.0])
    assert is_sunlit(np.array([6800.0, 0.0, 0.0]), s)
    assert not is_sunlit(np.array([-6800.0, 0.0, 0.0]), s)
    assert is_sunlit(np.array([-6800.0, 6400.0, 0.0]), s)


def test_solar_longitude_at_equinox_and_solstice() -> None:
    # Равноденствие 2024-03-20 03:06 UTC и солнцестояние 2024-06-20 20:51 UTC по астрономическому альманаху.
    assert (
        min(
            solar_longitude_deg(datetime(2024, 3, 20, 3, 6, tzinfo=UTC)),
            360.0 - solar_longitude_deg(datetime(2024, 3, 20, 3, 6, tzinfo=UTC)),
        )
        < 0.05
    )
    assert abs(solar_longitude_deg(datetime(2024, 6, 20, 20, 51, tzinfo=UTC)) - 90.0) < 0.05

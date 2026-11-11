import numpy as np
import pytest

from evarisk.orbit.radiant import radiant_unit_gcrs, radiant_visible


def test_radiant_vector_from_ra_dec() -> None:
    v = radiant_unit_gcrs(0.0, 0.0)
    assert np.allclose(v, [1.0, 0.0, 0.0])
    v = radiant_unit_gcrs(90.0, 0.0)
    assert np.allclose(v, [0.0, 1.0, 0.0], atol=1e-12)
    assert np.allclose(radiant_unit_gcrs(0.0, 90.0), [0.0, 0.0, 1.0], atol=1e-12)
    assert abs(np.linalg.norm(radiant_unit_gcrs(338.0, -1.0)) - 1.0) < 1e-12


def test_visibility_horizon_dip() -> None:
    radiant = np.array([1.0, 0.0, 0.0])
    r = 6786.0
    above = np.array([[r, 0.0, 0.0]])
    opposite = np.array([[-r, 0.0, 0.0]])
    assert radiant_visible(above, radiant, 6471.0)[0]
    assert not radiant_visible(opposite, radiant, 6471.0)[0]
    dip = np.degrees(np.arccos(6471.0 / r))
    just_visible = np.array([[r * np.cos(np.radians(90 + dip - 0.5)), r * np.sin(np.radians(90 + dip - 0.5)), 0.0]])
    just_hidden = np.array([[r * np.cos(np.radians(90 + dip + 0.5)), r * np.sin(np.radians(90 + dip + 0.5)), 0.0]])
    assert radiant_visible(just_visible, radiant, 6471.0)[0] and not radiant_visible(just_hidden, radiant, 6471.0)[0]
    assert 17.0 < dip < 18.0


def test_visibility_is_vectorised_over_points() -> None:
    radiant = np.array([0.0, 0.0, 1.0])
    r = 6786.0
    points = np.array([[0.0, 0.0, r], [r, 0.0, 0.0], [0.0, 0.0, -r]])
    visible = radiant_visible(points, radiant, 6471.0)
    assert visible.dtype == bool and visible.tolist() == [True, True, False]


def test_below_shield_rejected() -> None:
    with pytest.raises(ValueError):
        radiant_visible(np.array([[6000.0, 0.0, 0.0]]), np.array([1.0, 0.0, 0.0]), 6471.0)

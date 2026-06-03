"""Unit tests for Lorentzian floor + β-separation analysis.

Single-sideband (SSB) convention: the white-noise floor is the *minimum*
of S_ν over the chosen band, and FWHM_Lorentz = π · S₀.
"""
import numpy as np
import pytest

from app.analysis import BETA_SLOPE, beta_line, fit_lorentz_floor, integrate_beta


def test_lorentz_floor_uses_minimum_of_band():
    # Arrange: within [1e6, 1e7] the values are {5, 2, 8, 6}; min=2, median=5.5
    freq = np.array([1e5, 1e6, 3e6, 5e6, 1e7])
    s_nu = np.array([99.0, 5.0, 2.0, 8.0, 6.0])

    # Act
    fit = fit_lorentz_floor(freq, s_nu, f_min=1e6, f_max=1e7)

    # Assert: floor is the MINIMUM in band, not the median
    assert fit is not None
    assert fit.s0_hz2_per_hz == pytest.approx(2.0)
    assert fit.fwhm_hz == pytest.approx(np.pi * 2.0)
    assert fit.n_points == 4  # the 1e5 point is outside the band


def test_lorentz_floor_ignores_nonpositive_and_nonfinite():
    freq = np.array([1e6, 2e6, 3e6, 4e6, 5e6])
    s_nu = np.array([np.nan, -1.0, 4.0, 7.0, 9.0])

    fit = fit_lorentz_floor(freq, s_nu, f_min=1e6, f_max=1e7)

    assert fit is not None
    assert fit.s0_hz2_per_hz == pytest.approx(4.0)  # min of the valid {4, 7, 9}
    assert fit.n_points == 3


def test_lorentz_floor_returns_none_when_too_few_points():
    freq = np.array([1e6, 2e6])
    s_nu = np.array([1.0, 2.0])

    assert fit_lorentz_floor(freq, s_nu, f_min=1e8, f_max=1e9) is None


def test_beta_line_slope_is_di_domenico_one_sided():
    assert BETA_SLOPE == pytest.approx(8.0 * np.log(2.0) / np.pi**2)
    assert BETA_SLOPE == pytest.approx(0.5618, abs=1e-4)
    f = np.array([1.0, 10.0, 100.0])
    np.testing.assert_allclose(beta_line(f), BETA_SLOPE * f)


def test_integrate_beta_area_and_gaussian_fwhm():
    # S_ν far above the β-line over [1, 1000] → positive area
    freq = np.array([1.0, 10.0, 100.0, 1000.0])
    s_nu = np.full(4, 1e3)

    res = integrate_beta(freq, s_nu, f_min=1.0, f_max=1000.0)

    assert res is not None
    assert res.area_hz2 > 0
    assert res.fwhm_gauss_hz == pytest.approx(
        np.sqrt(8.0 * np.log(2.0) * res.area_hz2)
    )
    assert res.fraction_above_beta == pytest.approx(1.0)

"""Unit tests for ProcessResult derived spectra.

pycosh emits a two-sided PSD; the app displays the single-sideband (SSB)
spectrum, i.e. the physical spectra carry a factor-2 conversion.
"""
import numpy as np

from app.processor import SSB_FACTOR, ProcessRequest, ProcessResult


def _make_result(**overrides) -> ProcessResult:
    freq = np.array([1.0, 2.0, 4.0])
    gfilter = np.array([2.0, 2.0, 2.0])
    psd = np.array([10.0, 20.0, 40.0])
    req = ProcessRequest(
        v1=np.zeros(8),
        v2=None,
        sample_rate=1e9,
        delay_freq=1e6,
        bw_segment=(1e3, 1e4),
        offset_start_ratio=10,
        range_start=None,
        range_stop=None,
    )
    kwargs = dict(
        freq=freq,
        gfilter=gfilter,
        psd11=psd,
        psd11_err=psd / 10.0,
        psd22=psd,
        psd22_err=psd / 10.0,
        psd12=psd,
        psd12_err=psd / 10.0,
        request=req,
    )
    kwargs.update(overrides)
    return ProcessResult(**kwargs)


def test_ssb_factor_is_two():
    assert SSB_FACTOR == 2.0


def test_s_nu_applies_single_sideband_factor():
    r = _make_result()
    np.testing.assert_allclose(r.s_nu_11, SSB_FACTOR * np.abs(r.psd11) / r.gfilter)
    np.testing.assert_allclose(r.s_nu_22, SSB_FACTOR * np.abs(r.psd22) / r.gfilter)
    np.testing.assert_allclose(r.s_nu_12, SSB_FACTOR * np.abs(r.psd12) / r.gfilter)
    np.testing.assert_allclose(
        r.s_nu_12_err, SSB_FACTOR * np.abs(r.psd12_err) / r.gfilter
    )


def test_s_phi_is_s_nu_over_f_squared_and_keeps_ssb_factor():
    r = _make_result()
    np.testing.assert_allclose(r.s_phi_12, r.s_nu_12 / r.freq**2)
    np.testing.assert_allclose(
        r.s_phi_12, SSB_FACTOR * np.abs(r.psd12) / r.gfilter / r.freq**2
    )

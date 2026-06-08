"""Unit tests for CSV spectrum export and binary (.npy/.npz) record I/O."""
import numpy as np
import pandas as pd
import pytest

from app.data_io import (
    DualBpdData,
    export_spectrum,
    is_data_path,
    load_npy,
    load_npz,
    load_record,
    load_two_records,
    save_dual_bpd_npy,
    save_dual_bpd_npz,
    save_record,
)


def _sample(n: int = 256, dual: bool = True) -> DualBpdData:
    t = np.arange(n) / 1e6
    v1 = np.sin(2 * np.pi * 1e4 * t)
    v2 = np.cos(2 * np.pi * 1e4 * t) if dual else None
    return DualBpdData(t=t, v1=v1, v2=v2, sample_rate=1e6,
                       source_files=())


def test_npz_round_trips_dual_channel(tmp_path):
    src = _sample(dual=True)
    out = tmp_path / "rec.npz"

    save_dual_bpd_npz(out, src)
    loaded = load_npz(out)

    np.testing.assert_allclose(loaded.t, src.t)
    np.testing.assert_allclose(loaded.v1, src.v1)
    np.testing.assert_allclose(loaded.v2, src.v2)
    assert loaded.sample_rate == pytest.approx(src.sample_rate)


def test_npz_round_trips_single_channel(tmp_path):
    src = _sample(dual=False)
    out = tmp_path / "rec.npz"

    save_dual_bpd_npz(out, src)
    loaded = load_npz(out)

    assert loaded.v2 is None
    np.testing.assert_allclose(loaded.v1, src.v1)


def test_npy_round_trips_and_keeps_float64(tmp_path):
    src = _sample(dual=True)
    out = tmp_path / "rec.npy"

    save_dual_bpd_npy(out, src)
    loaded = load_npy(out)

    assert loaded.v1.dtype == np.float64
    np.testing.assert_allclose(loaded.v2, src.v2)


def test_load_record_dispatches_on_suffix(tmp_path):
    src = _sample(dual=True)
    npz_path = tmp_path / "rec.npz"
    npy_path = tmp_path / "rec.npy"
    save_dual_bpd_npz(npz_path, src)
    save_dual_bpd_npy(npy_path, src)

    for path in (npz_path, npy_path):
        loaded = load_record(path)
        np.testing.assert_allclose(loaded.v1, src.v1)


def test_load_record_rejects_unknown_suffix(tmp_path):
    bogus = tmp_path / "data.bin"
    bogus.write_bytes(b"\x00\x01")
    with pytest.raises(ValueError):
        load_record(bogus)


def test_load_two_records_combines_and_truncates(tmp_path):
    a = _sample(n=300, dual=False)
    b = _sample(n=256, dual=False)
    pa = tmp_path / "a.npz"
    pb = tmp_path / "b.npy"
    save_dual_bpd_npz(pa, a)
    save_dual_bpd_npy(pb, b)

    combined = load_two_records(pa, pb)

    assert combined.n_samples == 256  # truncated to the shorter record
    assert combined.v2 is not None
    np.testing.assert_allclose(combined.v1, a.v1[:256])


def test_is_data_path_recognises_supported_suffixes():
    assert is_data_path("x.csv") and is_data_path("x.npy") and is_data_path("x.npz")
    assert not is_data_path("x.txt")


@pytest.mark.parametrize("suffix", [".csv", ".npy", ".npz"])
def test_save_record_dispatches_and_round_trips(tmp_path, suffix):
    src = _sample(dual=True)
    out = tmp_path / f"rec{suffix}"

    save_record(out, src)
    loaded = load_record(out)

    np.testing.assert_allclose(loaded.v1, src.v1)
    np.testing.assert_allclose(loaded.v2, src.v2)


def test_save_record_rejects_unknown_suffix(tmp_path):
    with pytest.raises(ValueError):
        save_record(tmp_path / "rec.bin", _sample())


def test_export_spectrum_writes_metadata_and_all_columns(tmp_path):
    # Arrange
    freq = np.array([10.0, 100.0, 1000.0])
    cols = {
        "S_nu_cross_Hz2_per_Hz": np.array([1.0, 2.0, 3.0]),
        "S_phi_cross_rad2_per_Hz": np.array([0.01, 0.002, 0.0003]),
    }
    meta = {"spectrum_convention": "single-sideband (SSB)", "FSR_Hz": "214000"}
    out = tmp_path / "spec.csv"

    # Act
    export_spectrum(out, freq, cols, metadata=meta)

    # Assert: metadata header lines are commented, then the data round-trips
    text = out.read_text()
    assert "# spectrum_convention: single-sideband (SSB)" in text
    assert "# FSR_Hz: 214000" in text

    df = pd.read_csv(out, comment="#")
    assert list(df.columns) == [
        "frequency_Hz",
        "S_nu_cross_Hz2_per_Hz",
        "S_phi_cross_rad2_per_Hz",
    ]
    np.testing.assert_allclose(df["frequency_Hz"].to_numpy(), freq)
    np.testing.assert_allclose(
        df["S_nu_cross_Hz2_per_Hz"].to_numpy(), cols["S_nu_cross_Hz2_per_Hz"]
    )

"""Unit tests for CSV spectrum export."""
import numpy as np
import pandas as pd

from app.data_io import export_spectrum


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

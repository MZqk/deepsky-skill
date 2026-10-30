"""Stage 4 photometric fit diagnostics parsed from the bound run log."""

import pytest

from deep_sky_siril_processing import color_fit_quality

DEGENERATE_LOG = """
log: SPCC Linear Fits
log: Image R/G = 1.473937 + -0.010339 * Catalog R/G (sigma: 0.927355)
log: Image B/G = 0.830228 + -0.002359 * Catalog B/G (sigma: 0.520884)
log: Found a solution for color calibration using 963 stars. Factors:
log: K0: 0.562
log: K1: 0.827
log: K2: 1.000
log: The photometric color calibration seems to have found an imprecise solution, consider correcting the image gradient first
log: 1480 stars excluded from the calculation
"""

HEALTHY_LOG = """
log: Image R/G = 0.180000 + 0.910000 * Catalog R/G (sigma: 0.041000)
log: Image B/G = 0.120000 + 0.880000 * Catalog B/G (sigma: 0.038000)
log: Found a solution for color calibration using 812 stars. Factors:
log: K0: 1.020
log: K1: 1.000
log: K2: 0.980
"""


def test_degenerate_spcc_log_is_flagged_and_bound_to_its_lines():
    quality = color_fit_quality(DEGENERATE_LOG, "spcc")
    assert quality["schema"] == "starun-siril.color-fit-quality.v1"
    assert quality["verdict"] == "degenerate"
    assert quality["checks"]["slope_below_scatter"] is True
    assert quality["checks"]["gains_match_grey_world"] is True
    assert quality["white_balance"] == [0.562, 0.827, 1.0]
    assert quality["stars"] == {"used": 963, "excluded": 1480}
    assert quality["imprecise_warning"] is True
    assert quality["warnings"] == ["pcc_imprecise_solution"]
    assert [row["ratio"] for row in quality["fits"]] == ["R/G", "B/G"]
    assert quality["fits"][0]["slope"] == pytest.approx(-0.010339)
    assert quality["fits"][0]["sigma"] == pytest.approx(0.927355)
    assert quality["fits"][0]["line"] < quality["fits"][1]["line"]


def test_healthy_fit_stays_acceptable():
    quality = color_fit_quality(HEALTHY_LOG, "pcc")
    assert quality["verdict"] == "acceptable"
    assert quality["checks"]["slope_below_scatter"] is False
    assert quality["imprecise_warning"] is False
    assert quality["warnings"] == []


def test_large_scatter_without_fit_lines_is_degraded():
    quality = color_fit_quality("K0: 0.9\nK1: 1.0\nK2: 1.1\n"
                                "The photometric color calibration seems to have found an imprecise solution",
                                "pcc")
    assert quality["verdict"] == "degraded"
    assert quality["fits"] == []
    assert quality["white_balance"] == [0.9, 1.0, 1.1]


def test_logs_without_fit_evidence_gain_no_field():
    assert color_fit_quality("Found a solution for color calibration using 2215 stars.", "pcc") is None
    assert color_fit_quality("", "spcc") is None
    assert color_fit_quality(DEGENERATE_LOG, "neutral") is None


def test_route_history_must_end_with_the_declared_method(tmp_path):
    import hashlib
    import json

    import pytest as _pytest

    from deep_sky_siril_processing import _calibration_evidence

    source = tmp_path / "parent.fit"
    source.write_bytes(b"parent")
    digest = hashlib.sha256(b"parent").hexdigest()
    good = {"method": "neutral", "source_sha256": digest,
            "route_history": [{"method": "spcc", "status": "degraded", "reason": "fit slopes within scatter"},
                              {"method": "neutral", "status": "adopted", "reason": "no usable photometric solution"}]}
    assert _calibration_evidence("# color-calibration: " + json.dumps(good), source)["method"] == "neutral"
    bad = dict(good, route_history=[{"method": "neutral", "status": "adopted", "reason": "x"},
                                    {"method": "spcc", "status": "skipped", "reason": "y"}])
    with _pytest.raises(Exception, match="route_history"):
        _calibration_evidence("# color-calibration: " + json.dumps(bad), source)

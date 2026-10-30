import numpy as np
import pytest

from filter_metadata import filter_from_header
from deep_sky_siril_session import detect_master_input_state
import deep_sky_siril_session as sessions
from .test_deep_sky_siril import _session, _write_fits
from .test_native_processing import write_fits
from .test_xisf_metrics import xisf


def test_seestar_master_light_xisf_detection(tmp_path):
    path = tmp_path / "seestar_m33.xisf"
    # Create float XISF with small linear median (~0.01)
    data = np.full((3, 16, 16), 0.01, dtype=np.float32)
    keywords = [
        ("IMAGETYP", "'Master Light'"),
        ("TELESCOP", "'S30 Pro_c7e83a55'"),
        ("FILTER", "'IRCUT'"),
        ("WBPPCROP", "'(0,0)x(15,15)'"),
    ]
    xisf(path, data, keywords=keywords)
    header = {"IMAGETYP": "Master Light", "TELESCOP": "S30 Pro_c7e83a55", "FILTER": "IRCUT", "WBPPCROP": "(0,0)x(15,15)"}
    prof = filter_from_header(header)
    state, evidence = detect_master_input_state(path, header, prof)
    assert (state, evidence) == ("unknown", [])


def test_dwarf_native_fits_detection(tmp_path):
    path = tmp_path / "dwarf3_m31.fits"
    # DWARF float with ~0.25 pedestal
    values = np.full((3, 16, 16), 0.25, dtype=np.float32)
    extra = (
        ("ORIGIN", "'DWARFLAB'"),
        ("TELESCOP", "'DWARF 3'"),
        ("CAMERA", "'TELE'"),
        ("FILTER", "'Astro'"),
        ("RESTACK", 0),
    )
    write_fits(path, values, extra=extra)
    header = {"ORIGIN": "DWARFLAB", "TELESCOP": "DWARF 3", "CAMERA": "TELE", "FILTER": "Astro", "RESTACK": 0}
    prof = filter_from_header(header)
    state, evidence = detect_master_input_state(path, header, prof)
    assert (state, evidence) == ("unknown", [])


def test_seestar_native_stack_fits_detection(tmp_path):
    path = tmp_path / "SeestarS30-NGC6888-60s114_LP.fit"
    values = np.full((3, 16, 16), 0.02, dtype=np.float32)
    extra = (
        ("CREATOR", "'ZWO Seestar S30'"),
        ("TELESCOP", "'S30_ac8f962b'"),
        ("FILTER", "'LP'"),
        ("STACKCNT", 114),
        ("TOTALEXP", 6840.0),
    )
    write_fits(path, values, extra=extra)
    header = {"CREATOR": "ZWO Seestar S30", "TELESCOP": "S30_ac8f962b", "FILTER": "LP", "STACKCNT": 114, "TOTALEXP": 6840.0}
    prof = filter_from_header(header)
    state, evidence = detect_master_input_state(path, header, prof)
    assert (state, evidence) == ("unknown", [])


def test_video_stacked_solar_lunar_is_unknown(tmp_path):
    path = tmp_path / "SeestarS30-Video_Stacked_Lunar_20251127-211435.fit"
    extra_cards = (
        "CREATOR = 'ZWO Seestar S30'",
        "OBJECT  = 'Video_Stacked_Lunar'",
    )
    _write_fits(path, color_ready=True, extra_cards=extra_cards)
    header = {"CREATOR": "ZWO Seestar S30", "OBJECT": "Video_Stacked_Lunar"}
    prof = filter_from_header(header)
    state, evidence = detect_master_input_state(path, header, prof)
    assert state == "unknown"
    assert evidence == []


def test_unstacked_synthetic_card_remains_unknown(tmp_path):
    path = tmp_path / "generic_card.fits"
    extra_cards = (
        "TELESCOP= 'S30 Pro_c7e83a55'",
        "FILTER  = 'IRCUT'",
    )
    _write_fits(path, color_ready=True, extra_cards=extra_cards)
    header = {"TELESCOP": "S30 Pro_c7e83a55", "FILTER": "IRCUT"}
    prof = filter_from_header(header)
    state, evidence = detect_master_input_state(path, header, prof)
    assert state == "unknown"
    assert evidence == []


def test_init_session_auto_detection_integration(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, input_state="unknown")
    source = tmp_path / "SeestarS30Pro-M33-60s85_IRCUT.xisf"
    data = np.full((3, 16, 16), 0.01, dtype=np.float32)
    keywords = [
        ("IMAGETYP", "'Master Light'"),
        ("TELESCOP", "'S30 Pro_c7e83a55'"),
        ("FILTER", "'IRCUT'"),
        ("WBPPCROP", "'(0,0)x(15,15)'"),
    ]
    xisf(source, data, keywords=keywords)
    target = tmp_path / "session_auto_detected"
    result = sessions.init_session(
        str(source),
        str(target),
        input_state="auto",
        state_evidence=[],
        channel_mode="unknown",
        channel_map=None,
        target_name=None,
        target_type="unknown",
        style="natural",
        stars="adaptive",
        offline=True,
        keep_intermediates=False,
    )
    context = result["context"]
    assert context["input_state"] == "unknown"
    assert context["state_evidence"] == []
    assert context["channel_mode"] == "broadband"
    assert context["filter_profile"]["class"] == "broadband"
    assert context["filter_profile"]["device_model"] == "s30-pro"

    # Verify session.json persistence
    loaded = sessions.load_session(target)[1]["context"]
    assert loaded["input_state"] == "unknown"
    assert loaded["state_evidence"] == context["state_evidence"]

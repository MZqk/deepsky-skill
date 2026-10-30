from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from filter_metadata import classify_filter, filter_from_header
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError
from .test_deep_sky_siril import _session, _write_fits


@pytest.mark.parametrize("name,kind,lines", [
    ("Ha + OIII", "dual_band", ["H-alpha", "OIII"]),
    ("Hα / OⅢ", "dual_band", ["H-alpha", "OIII"]),
    ("dual SII+OIII", "dual_band", ["OIII", "SII"]),
    ("Ha_OIII", "dual_band", ["H-alpha", "OIII"]),
    ("Ha+OIII+SII", "multi_band", ["H-alpha", "OIII", "SII"]),
    ("Hα", "narrowband", ["H-alpha"]),
    ("H-alpha 7nm", "narrowband", ["H-alpha"]),
    ("O3", "narrowband", ["OIII"]),
    ("SII", "narrowband", ["SII"]),
    ("L-Ultimate", "dual_band", ["H-alpha", "OIII"]),
    ("Duo-Band", "dual_band", []),
    ("IR CUT", "broadband", []),
    ("UV/IR-Cut", "broadband", []),
    ("Green", "broadband_channel", []),
    ("L", "luminance", []),
    ("LP", "unknown", []),
    ("alpha", "unknown", []),
    ("s20", "unknown", []),
    ("", "unknown", []),
])
def test_filter_names(name, kind, lines):
    profile = classify_filter(name)
    assert profile["class"] == kind
    assert profile["lines"] == lines


def test_header_aliases_and_device_bound_lp():
    profile = filter_from_header({"FILTER": "  ", "FILTERID": "OIII"})
    assert profile["class"] == "narrowband" and profile["header_key"] == "FILTERID"
    header = {"FILTER": "LP", "TELESCOP": "S30 Pro_c7e83a55", "CREATOR": "ZWO Seestar S30 Pro", "WIDECAM": 0}
    profile = filter_from_header(header)
    assert profile["class"] == "dual_band" and profile["lines"] == ["H-alpha", "OIII"]
    assert profile["source"] == "header" and profile["matched_by"] == "device_filter_mapping"
    assert profile["reference"] == "https://us.seestar.com/products/seestar-s30-pro"
    assert filter_from_header({"FILTER": "LP", "INSTRUME": "imx585"})["class"] == "unknown"
    assert filter_from_header({**header, "WIDECAM": 1})["class"] == "unknown"
    assert filter_from_header({**header, "TELESCOP": "Seestar S50"})["class"] == "unknown"
    assert filter_from_header({"FILTNAME": {"value": "Ha + OIII"}})["class"] == "dual_band"


@pytest.mark.parametrize("declared", ["unknown", "broadband"])
def test_first_fits_filter_detection_does_not_infer_linearity(tmp_path, monkeypatch, declared):
    _, source, _ = _session(tmp_path, monkeypatch, input_state="unknown", color_ready=True)
    _write_fits(source, color_ready=True, extra_cards=(
        "FILTER  = 'LP'", "TELESCOP= 'S30 Pro_c7e83a55'", "CREATOR = 'ZWO Seestar S30 Pro'", "WIDECAM = 0",
    ))
    kwargs = dict(input_state="auto", state_evidence=[], channel_mode=declared, channel_map=None,
                  target_name=None, target_type="unknown", style="natural", stars="adaptive",
                  offline=True, keep_intermediates=False)
    target = tmp_path / "detected"
    if declared == "broadband":
        with pytest.raises(ContractError) as raised:
            sessions.init_session(str(source), str(target), **kwargs)
        assert raised.value.code == "filter_evidence_conflict"
        assert not target.exists()
        return
    result = sessions.init_session(str(source), str(target), **kwargs)
    context = sessions.load_session(target)[1]["context"]
    assert context == result["context"]
    assert context["input_state"] == "unknown" and not context["state_evidence"]
    assert context["channel_mode"] == "dualband-osc" and context["channel_map"] is None
    assert context["filter_profile"]["matched_by"] == "device_filter_mapping"


def test_independent_skills_ship_identical_filter_rules():
    assert (ROOT / "scripts/filter_metadata.py").read_bytes() == (
        ROOT.parent / "deep-sky-processor/scripts/filter_metadata.py"
    ).read_bytes()


@pytest.mark.parametrize("key", ["INSTRUME", "TELESCOP", "TELESCOPE", "CREATOR", "ORIGIN"])
@pytest.mark.parametrize("name,model", [
    ("DWARF mini", "dwarf-mini"), ("dwarflab_dwarf-mini", "dwarf-mini"),
    ("DWARFLAB DWARF MINI", "dwarf-mini"), ("DWARF II", "dwarf-ii"),
    ("DWARF2", "dwarf-ii"), ("DWARFLAB-DWARF-Ⅱ", "dwarf-ii"),
    ("DWARF 3", "dwarf-3"), ("dwarf_iii", "dwarf-3"),
    ("DWARFLAB DWARF Ⅲ", "dwarf-3"), ("DWARF–3", "dwarf-3"),
    ("DWARF Draco", "dwarf-draco"), ("DRACO", "dwarf-draco"),
    ("DWARFLAB Draco", "dwarf-draco"), ("dwarf_draco", "dwarf-draco"),
    ("DRACO-Smart", None), ("DWARF Draconis", None),
])
def test_dwarf_model_aliases_without_selected_filter(key, name, model):
    profile = filter_from_header({key: name, "FILTER": " ", "FILTERID": None, "FILTNAME": ""})
    if model is None:
        assert "device_model" not in profile
    else:
        assert profile["device_model"] == model
    assert profile["class"] == "unknown" and profile["lines"] == []
    assert profile["source"] == "unavailable" and "matched_by" not in profile


@pytest.mark.parametrize("model", ["DWARF mini", "DWARF 3"])
@pytest.mark.parametrize("name", ["Duo-Band", "DuoBand", "Duo Band", "Dual-Band"])
def test_dwarf_selected_duo_filter(model, name):
    profile = filter_from_header({"INSTRUME": model, "FILTER": name, "CAMERA": "TELE"})
    assert profile["class"] == "dual_band" and profile["lines"] == ["H-alpha", "OIII"]
    assert profile["matched_by"] == "device_filter_mapping"
    assert profile["reference"].startswith("https://help.dwarflab.com/en/docs/")
    assert profile["raw_filter"] == name and profile["header_key"] == "FILTER"


@pytest.mark.parametrize("model,names", [
    ("DWARF mini", ["Astro", "Astro Filter"]),
    ("DWARF II", ["VIS", "VIS Filter"]),
    ("DWARF 3", ["Astro", "Astro Filter", "VIS", "VIS Filter"]),
])
def test_dwarf_selected_broadband_filters(model, names):
    for name in names:
        profile = filter_from_header({"TELESCOP": model, "FILTERID": name})
        assert profile["class"] == "broadband" and profile["lines"] == []
        assert profile["matched_by"] == "device_filter_mapping" and "dwarflab.com/" in profile["reference"]


@pytest.mark.parametrize("name", ["Duo-Band", "DuoBand", "Duo Band", "Dual-Band"])
def test_dwarf_draco_selected_duo_filter(name):
    profile = filter_from_header({"INSTRUME": "DWARF Draco", "FILTER": name, "CAMERA": "TELE"})
    assert profile["class"] == "dual_band" and profile["lines"] == ["H-alpha", "OIII"]
    assert profile["device_model"] == "dwarf-draco"
    assert profile["matched_by"] == "device_filter_mapping"
    assert profile["reference"] == "https://www.dwarflab.com/products/draco"


def test_dwarf_draco_broadband_sho_and_wide_gating():
    astro = filter_from_header({"TELESCOP": "DWARFLAB Draco", "FILTER": "Astro"})
    assert astro["class"] == "broadband" and astro["lines"] == []
    assert astro["reference"] == "https://www.dwarflab.com/products/draco"
    sho = filter_from_header({"INSTRUME": "DWARF Draco", "FILTER": "SII + OIII"})
    assert sho["class"] == "dual_band" and sho["lines"] == ["OIII", "SII"] and "matched_by" not in sho
    assert filter_from_header({"INSTRUME": "DWARF Draco", "FILTER": "VIS"})["class"] == "unknown"
    assert filter_from_header({"INSTRUME": "DWARF Draco", "FILTER": "Astro", "WIDECAM": 1})["class"] == "unknown"
    assert filter_from_header({"INSTRUME": "DWARF Draco", "FILTER": "LP"})["class"] == "unknown"


@pytest.mark.parametrize("header", [
    {"INSTRUME": "DWARF 30"}, {"INSTRUME": "DWARF IIII"}, {"INSTRUME": "DWARF mini2"},
    {"INSTRUME": "Other DWARF 3"}, {"INSTRUME": "DWARFLAB"},
    {"INSTRUME": "DWARF mini", "TELESCOP": "DWARF 3"},
    {"INSTRUME": "DWARF mini", "ORIGIN": "Seestar S30"},
    {"INSTRUME": "DWARF mini", "CAMERA": "WIDE"},
    {"INSTRUME": "DWARF mini", "CAMERA": "other"},
    {"INSTRUME": "DWARF mini", "CAMERA": 0},
    {"INSTRUME": "DWARF mini", "CAMERA": False},
    {"INSTRUME": "DWARF mini", "LENS": "wide angle"},
    {"INSTRUME": "DWARF mini", "LENSNAME": "unknown lens"},
    {"INSTRUME": "DWARF mini", "WIDECAM": 1},
])
@pytest.mark.parametrize("name,kind", [("Duo-Band", "dual_band"), ("Astro", "unknown")])
def test_dwarf_mapping_requires_unambiguous_telephoto_metadata(header, name, kind):
    profile = filter_from_header({**header, "FILTER": name})
    assert profile["class"] == kind and profile["lines"] == []
    assert "matched_by" not in profile


@pytest.mark.parametrize("name,kind", [("IR Pass", "unknown"), ("Astro", "unknown"),
                                      ("Duo-Band", "dual_band"), ("LP", "unknown")])
def test_dwarf_ii_keeps_generic_filter_meaning(name, kind):
    profile = filter_from_header({"INSTRUME": "DWARF II", "FILTER": name})
    assert profile["device_model"] == "dwarf-ii"
    assert profile["class"] == kind and profile["lines"] == [] and "matched_by" not in profile


@pytest.mark.parametrize("model", ["DWARF mini", "DWARF II", "DWARF 3"])
@pytest.mark.parametrize("name,kind,lines", [("SII + OIII", "dual_band", ["OIII", "SII"]),
                                          ("Hα", "narrowband", ["H-alpha"]),
                                          ("Optolong L-Ultimate", "dual_band", ["H-alpha", "OIII"])])
def test_explicit_lines_and_external_filters_precede_dwarf_mapping(model, name, kind, lines):
    profile = filter_from_header({"INSTRUME": model, "FILTER": name, "FILTERID": "Astro"})
    assert profile["class"] == kind and profile["lines"] == lines
    assert "device_model" in profile and "matched_by" not in profile


def test_dwarf_consistent_models_and_lp_do_not_borrow_seestar_mapping():
    header = {"INSTRUME": "DWARF mini", "TELESCOP": "DWARFLAB DWARF-mini", "ORIGIN": "DWARFLAB"}
    assert filter_from_header({**header, "FILTNAME": {"value": "Duo-Band"}})["lines"] == ["H-alpha", "OIII"]
    assert filter_from_header({**header, "FILTER": "LP"})["class"] == "unknown"
    assert filter_from_header({**header, "FILTER": "LP", "CREATOR": "ZWO Seestar S50"})["class"] == "unknown"
    assert filter_from_header({**header, "FILTER": "Astro", "CAMERA": " "})["class"] == "broadband"
    assert filter_from_header({**header, "FILTER": "V I S"})["class"] == "unknown"


@pytest.mark.parametrize("container", ["fits", "xisf"])
@pytest.mark.parametrize("channels", [1, 3])
@pytest.mark.parametrize("filter_name,kind", [("Duo-Band", "dual_band"), ("Astro", "broadband"), (None, "unknown")])
def test_init_freezes_dwarf_filter_provenance_without_domain_or_channel_roles(tmp_path, monkeypatch, container, channels, filter_name, kind):
    import numpy as np
    from .test_xisf_metrics import xisf
    _, _, _ = _session(tmp_path, monkeypatch, input_state="unknown")
    source = tmp_path / ("dwarf." + container)
    keywords = [("INSTRUME", "'DWARF mini'"), ("CAMERA", "'TELE'")]
    if filter_name:
        keywords.append(("FILTER", repr(filter_name)))
    if container == "fits":
        _write_fits(source, color_ready=channels == 3, extra_cards=tuple(f"{k:<8}= {v}" for k, v in keywords))
    else:
        xisf(source, np.full((channels, 8, 12), .001, dtype=np.float32), keywords=keywords)
    target = tmp_path / "detected"
    channel_map = {"R": "recorded-red", "G": "recorded-green", "B": "recorded-blue"}
    result = sessions.init_session(str(source), str(target), input_state="auto", state_evidence=[],
        channel_mode="unknown", channel_map=channel_map, target_name=None, target_type="unknown",
        style="natural", stars="adaptive", offline=True, keep_intermediates=False)
    context = result["context"]
    assert context["input_state"] == "unknown" and context["state_evidence"] == []
    assert context["channel_map"] == channel_map
    assert context["filter_profile"]["device_model"] == "dwarf-mini"
    assert context["filter_profile"]["class"] == kind
    expected = "narrowband" if channels == 1 and kind == "dual_band" else "mono" if channels == 1 else \
               "dualband-osc" if kind == "dual_band" else "broadband" if kind == "broadband" else "unknown"
    assert context["channel_mode"] == expected
    assert result["execution_policy"]["filter_metadata"] == "dwarf_explicit_filters_v1"
    assert sessions.load_session(target)[1]["context"] == context


@pytest.mark.parametrize("container", ["fits", "xisf"])
def test_dwarf_duoband_rejects_conflicting_broadband_declaration(tmp_path, monkeypatch, container):
    import numpy as np
    from .test_xisf_metrics import xisf
    _session(tmp_path, monkeypatch, input_state="unknown")
    source = tmp_path / ("dwarf." + container)
    keywords = [("INSTRUME", "'DWARF mini'"), ("FILTER", "'Duo-Band'")]
    if container == "fits":
        _write_fits(source, color_ready=True, extra_cards=tuple(f"{k:<8}= {v}" for k, v in keywords))
    else:
        xisf(source, np.full((3, 8, 12), .01, dtype=np.float32), keywords=keywords)
    target = tmp_path / "invalid"
    with pytest.raises(ContractError, match="conflicts with header filter"):
        sessions.init_session(str(source), str(target), input_state="auto", state_evidence=[],
            channel_mode="broadband", channel_map=None, target_name=None, target_type="unknown",
            style="natural", stars="adaptive", offline=True, keep_intermediates=False)
    assert not target.exists()


@pytest.mark.parametrize("policy", [sessions._FIXED_REVIEW_EXECUTION_POLICY, sessions._DWARF_FILTER_EXECUTION_POLICY])
def test_outgoing_fixed_review_policy_and_knowledge_replay_finalize_without_rewriting(tmp_path, monkeypatch, policy):
    from .test_deep_sky_siril import core, _delivery_run, _review, _selection
    session, source, _ = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(session, source, monkeypatch)
    payload = core.load_json(session / "session.json")
    payload["execution_policy"] = dict(policy)
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(session / "session.json", payload)
    provenance_path = session / receipt["script_provenance"]["path"]
    provenance = core.load_json(provenance_path)
    for name, digest, size in [
        ("SKILL.md", "ff91ef4838277cb70b65001309e200127fc0be33bb6d647969ba4b2dc409fe3f", 11982),
        ("references/cli-contract.md", "3dce484f3314d7bac683b1c04fd9e07085c3389567303d77345b994afba966fc", 40028),
        ("references/session-contract.md", "bda64f4b70582d199b1ec85f7692ee27b7f663b5b0169a478078958a3fb6b520", 20378),
        ("references/stages/stage-04-astrometry-color.md", "0ff23108cca6173b333eb6244b8e9f0e153a4b166ea1e7b714705b97dec298b0", 26503),
    ]:
        assert (digest, size) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        record = {"path": name, "sha256": digest, "size": size, "role": "supporting"}
        provenance["references"].append({key: value for key, value in record.items() if key != "size"})
        receipt["knowledge_validation"]["references"].append(record)
    core.atomic_write_json(provenance_path, provenance)
    receipt["script_provenance"].update(core.fingerprint(provenance_path, include_path=False))
    receipt_path = session / "runs" / (receipt["id"] + ".json")
    core.atomic_write_json(receipt_path, receipt)
    manifest = core.load_json(session / "manifest.json")
    next(entry for entry in manifest["runs"] if entry["id"] == receipt["id"])["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)
    review = _review(session, receipt["id"], candidate)
    selection = _selection(session, receipt["id"], candidate, review)
    frozen = {path: path.read_bytes() for path in [session / "session.json", receipt_path, review]}
    args = dict(protocol="delivery.render", script_value=str(session / receipt["script"]["path"]),
                source_value=str(source), expected_values=[str(candidate), str(candidate.with_suffix(".fit"))], timeout=30)
    assert core.run_script(str(session), **args)["replayed"]
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)["status"] == "success"
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)["status"] == "success"
    assert all(path.read_bytes() == data for path, data in frozen.items())


@pytest.mark.parametrize("header,expected_model", [
    ({"TELESCOP": "S30 Pro_6a110e03", "INSTRUME": "imx585"}, "s30-pro"),
    ({"TELESCOP": "S30P_75e4ea92", "CREATOR": "ZWO Seestar S30P"}, "s30-pro"),
    ({"TELESCOP": "S30_ac8f962b", "CREATOR": "ZWO Seestar S30"}, "s30"),
    ({"TELESCOP": "S50_38f02919"}, "s50"),
    ({"TELESCOP": "S50 Pro_1d4f7c60", "INSTRUME": "imx586"}, "s50-pro"),
    ({"CREATOR": "ZWO Seestar S50P", "ORIGIN": "Seestar S50P_a1b2c3d4"}, "s50-pro"),
])
def test_seestar_xisf_and_s30p_aliases(header, expected_model):
    profile = filter_from_header({**header, "FILTER": "LP"})
    assert profile["class"] == "dual_band"
    assert profile["device_model"] == expected_model
    assert profile["lines"] == ["H-alpha", "OIII"]
    assert profile["matched_by"] == "device_filter_mapping"


def test_seestar_s50_pro_lp_mapping_is_device_bound():
    header = {"FILTER": "LP", "TELESCOP": "S50 Pro_1d4f7c60", "CREATOR": "ZWO Seestar S50 Pro", "WIDECAM": 0}
    profile = filter_from_header(header)
    assert profile["class"] == "dual_band" and profile["device_model"] == "s50-pro"
    assert profile["reference"] == "https://us.seestar.com/products/seestar-s50-pro-smart-telescope"
    assert filter_from_header({**header, "WIDECAM": 1})["class"] == "unknown"
    ambiguous = {**header, "TELESCOP": "S50 Pro_1d4f7c60", "CREATOR": "ZWO Seestar S50"}
    assert filter_from_header(ambiguous)["class"] == "unknown" and "device_model" not in filter_from_header(ambiguous)
    assert filter_from_header({"FILTER": "LP", "TELESCOP": "S50 Pro_1d4f7c60"})["class"] == "dual_band"
    assert filter_from_header({"FILTER": "LP", "TELESCOP": "Seestar S50 Pro"})["device_model"] == "s50-pro"


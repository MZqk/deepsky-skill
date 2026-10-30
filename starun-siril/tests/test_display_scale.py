"""Display-only viewing-size panels: geometry, format safety, determinism and retention."""
import json
from pathlib import Path

import pytest
from PIL import Image

import deep_sky_siril_contract as contract
import deep_sky_siril_display_scale as scale


def make_session(tmp_path):
    session = tmp_path/"session"
    (session/"previews").mkdir(parents=True)
    (session/"session.json").write_text(json.dumps({"execution_policy": {}}))
    return session


def jpeg(path, size=(320, 240), value=(80, 40, 20), quality=95):
    Image.new("RGB", size, value).save(path, quality=quality)
    return path


def test_fit_downscales_by_long_edge_and_never_upscales():
    assert scale.fit(320, 240, 1024) == (320, 240, 1.0)
    assert scale.fit(2048, 1024, 1024) == (1024, 512, 0.5)
    assert scale.fit(1001, 701, 640) == (640, 448, 640/1001)
    for bad in (0, -1, 639, 4097, True, 1024.0):
        with pytest.raises(contract.ContractError, match='long edge'):
            scale.fit(320, 240, bad)


def test_scientific_and_renamed_payloads_are_rejected(tmp_path):
    session = make_session(tmp_path)
    (session/"previews/raw.fit").write_text("not an image")
    with pytest.raises(contract.ContractError, match='scientific formats'):
        scale.prepare(session, "previews/raw.fit", None, "080-display", 1024)
    Image.new("RGB", (320, 240)).save(session/"previews/renamed.png", format="TIFF")
    with pytest.raises(contract.ContractError, match='renamed'):
        scale.prepare(session, "previews/renamed.png", None, "080-display", 1024)


def test_reference_must_share_dimensions_and_paths_stay_inside_the_session(tmp_path):
    session = make_session(tmp_path)
    jpeg(session/"previews/parent.jpg")
    jpeg(session/"previews/candidate.jpg", size=(300, 240))
    with pytest.raises(contract.ContractError, match='identical pixel dimensions'):
        scale.prepare(session, "previews/candidate.jpg", "previews/parent.jpg", "080-display", 1024)
    with pytest.raises((OSError, contract.ContractError)):
        scale.prepare(session, "../session.json", None, "080-display", 1024)
    (session/"previews/link.jpg").symlink_to(session/"previews/parent.jpg")
    with pytest.raises(contract.ContractError, match='symlink'):
        scale.prepare(session, "previews/link.jpg", None, "080-display", 1024)


def test_render_is_deterministic_and_prepare_is_idempotent(tmp_path):
    session = make_session(tmp_path)
    jpeg(session/"previews/parent.jpg")
    jpeg(session/"previews/candidate.jpg", value=(120, 60, 30))
    first = scale.prepare(session, "previews/candidate.jpg", "previews/parent.jpg", "080-display", 1024)
    second = scale.prepare(session, "previews/candidate.jpg", "previews/parent.jpg", "080-display", 1024)
    assert first["status"] == "prepared" and second["status"] == "unchanged"
    assert first["output"] == second["output"]
    receipt = json.loads((session/"reports/080-display/display-scale.json").read_text())
    assert receipt["render"]["display_size"] == [320, 240]
    assert receipt["render"]["upscaled"] is False
    assert receipt["display_only"] is True and receipt["scientific_parent"] is False
    assert receipt["reference"]["path"] == "previews/parent.jpg"


def test_changed_inputs_do_not_overwrite_existing_evidence(tmp_path):
    session = make_session(tmp_path)
    jpeg(session/"previews/candidate.jpg")
    first = scale.prepare(session, "previews/candidate.jpg", None, "080-display", 1024)
    jpeg(session/"previews/candidate.jpg", value=(10, 90, 10))
    with pytest.raises(contract.ContractError, match='new run id'):
        scale.prepare(session, "previews/candidate.jpg", None, "080-display", 1024)
    assert contract.sha256_file(session/first["output"]["path"]) == first["output"]["sha256"]


def test_retained_paths_bind_the_panel_and_detect_tampering(tmp_path):
    session = make_session(tmp_path)
    jpeg(session/"previews/candidate.jpg")
    scale.prepare(session, "previews/candidate.jpg", None, "080-display", 1024)
    assert scale.retained_paths(session) == {"reports/080-display/display-scale.png"}
    (session/"reports/080-display/display-scale.png").write_bytes(b"tampered")
    with pytest.raises(contract.ContractError, match='changed'):
        scale.retained_paths(session)


def test_alpha_and_exif_orientation_are_recorded(tmp_path):
    session = make_session(tmp_path)
    Image.new("RGBA", (320, 240), (10, 20, 30, 128)).save(session/"previews/alpha.png")
    scale.prepare(session, "previews/alpha.png", None, "080-alpha", 1024)
    alpha = json.loads((session/"reports/080-alpha/display-scale.json").read_text())
    assert alpha["render"]["flattened_alpha"] is True
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (320, 240), (40, 50, 60)).save(session/"previews/rotated.jpg", exif=exif)
    scale.prepare(session, "previews/rotated.jpg", None, "081-rotated", 1024)
    rotated = json.loads((session/"reports/081-rotated/display-scale.json").read_text())
    assert rotated["render"]["exif_orientation_applied"] is True
    assert rotated["inputs"]["width"] == 240 and rotated["inputs"]["height"] == 320

"""Scientific XISF statistics, immutable reporting and explicit unsupported cases."""
import base64
import hashlib
import json
import struct
import xml.etree.ElementTree as ET
import zlib

import numpy as np
import pytest

from .test_native_processing import write_fits
from .test_deep_sky_siril import _session, _input_inspect_run, core, session_state
import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_metrics as metrics
from deep_sky_siril_contract import EXECUTION_POLICY, sha256_file


def xisf(path, values, *, sample="Float32", endian="little", storage="planar",
         location="attachment", codec=None, bounds="0:1", extra=False, attributes=None, keywords=()):
    codes = {"UInt8": "u1", "UInt16": "u2", "UInt32": "u4", "Float32": "f4", "Float64": "f8"}
    array = np.asarray(values, dtype=("<" if endian == "little" else ">")+codes[sample])
    channels, height, width = array.shape
    payload = (array if storage == "planar" else array.transpose(1, 2, 0)).tobytes()
    size = len(payload)
    if codec:
        if codec.endswith("+sh"):
            payload = np.frombuffer(payload, dtype=np.uint8).reshape(-1, array.itemsize).T.copy().tobytes()
        payload = zlib.compress(payload)
    attrs = {"id": "first-image", "geometry": f"{width}:{height}:{channels}", "sampleFormat": sample,
             "colorSpace": "RGB" if channels == 3 else "Gray", "byteOrder": endian, "pixelStorage": storage,
             "location": f"attachment:4096:{len(payload)}" if location == "attachment" else "embedded",
             "checksum": "sha256:"+hashlib.sha256(payload).hexdigest()}
    if sample.startswith("Float") and bounds is not None:
        attrs["bounds"] = bounds
    if codec:
        attrs["compression"] = f"{codec}:{size}"+(f":{array.itemsize}" if codec.endswith("+sh") else "")
    attrs.update(attributes or {})
    root = ET.Element("xisf", {"version": "1.0", "xmlns": "http://www.pixinsight.com/xisf"})
    image = ET.SubElement(root, "Image", attrs)
    for name, value in keywords:
        ET.SubElement(image, "FITSKeyword", {"name": name, "value": str(value)})
    if location == "embedded":
        ET.SubElement(image, "Data", {"encoding": "base64"}).text = base64.b64encode(payload).decode()
    if extra:
        ET.SubElement(root, "Image", {"id": "integration", "geometry": "1:1:1",
            "sampleFormat": "UInt8", "colorSpace": "Gray",
            "location": f"attachment:{4096+len(payload)}:1"})
    header = ET.tostring(root)
    prefix = b"XISF0100"+struct.pack("<I", len(header))+bytes(4)+header
    assert len(prefix) < 4096
    path.write_bytes(prefix.ljust(4096, b"\0")+(payload if location == "attachment" else b"")+b"\xff")
    return array


@pytest.mark.parametrize("channels", [1, 3])
@pytest.mark.parametrize("sample", ["UInt8", "UInt16", "UInt32", "Float32", "Float64"])
@pytest.mark.parametrize("endian,storage,location,codec", [
    ("little", "planar", "attachment", None),
    ("big", "normal", "attachment", None),
    ("big", "planar", "embedded", "zlib"),
    ("little", "normal", "attachment", "zlib+sh"),
    ("big", "normal", "embedded", "zlib+sh"),
])
def test_xisf_and_fits_same_pixels_have_same_statistics(tmp_path, channels, sample, endian, storage, location, codec):
    values = np.tile([-.05, 0, .00005, .1, .2, .6, 1, 1.2], (channels, 2, 1))
    if sample.startswith("UInt"):
        maximum = np.iinfo(np.dtype("u"+str(int(sample[4:])//8))).max
        values = np.rint(np.clip(values, 0, 1)*maximum)
    path = tmp_path/"source.xisf"
    encoded = xisf(path, values, sample=sample, endian=endian, storage=storage, location=location, codec=codec)
    before = path.read_bytes()
    raw, scale, zero, selection = artifacts.read_xisf_pixels(path)
    np.testing.assert_array_equal(raw, encoded)
    assert not raw.flags.writeable and selection["image_index"] == 0
    normalized = encoded.astype(float)*scale+zero
    fits = tmp_path/"source.fit"; write_fits(fits, normalized, bitpix=-64)
    actual = metrics.analyze_image_histogram(path)
    expected = metrics.analyze_image_histogram(fits)
    assert actual["status"] == expected["status"] == "measured"
    assert actual["measurement_domain"] == "scientific"
    assert actual["pixel_count"] == expected["pixel_count"]
    for a, b in zip(actual["channels"], expected["channels"]):
        assert a == pytest.approx(b, abs=1e-14)
    assert path.read_bytes() == before


def test_image_zero_selection_declared_bounds_and_nonfinite_pixels(tmp_path):
    data = np.asarray([[[8, 10, 12, 15], [20, 22, np.nan, np.inf]]]*3)
    path = tmp_path/"multiple.xisf"
    xisf(path, data, bounds="10:20", extra=True)
    report = metrics.analyze_image_histogram(path)
    assert report["image_index"] == 0 and report["image_id"] == "first-image" and report["image_count"] == 2
    assert report["bounds"] == [10., 20.] and report["status"] == "uncertain"
    for row in report["channels"]:
        assert row["nonfinite_count"] == 2 and row["finite_count"] == 6
        assert row["shadow_clip_rate"] == 2/6 and row["highlight_sat_rate"] == 2/6
        assert row["image_median"] == pytest.approx(.35)
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("low,high", [(.3, 1.1), (-.7, .4)])
def test_declared_float_endpoints_do_not_drift_in_normalized_statistics(tmp_path, low, high):
    path = tmp_path/"endpoints.xisf"
    xisf(path, np.asarray([[[low, high]]]), sample="Float64", bounds=f"{low}:{high}")
    row = metrics.analyze_image_histogram(path)["channels"][0]
    assert row["shadow_clip_rate"] == row["highlight_sat_rate"] == .5
    assert row["near_black_rate"] == 0
    assert row["image_median"] == .5


@pytest.mark.parametrize("bounds", [None, "", "0", "0:1:2", "1:1", "2:1", "nan:1", "0:inf", "-1e308:1e308"])
def test_invalid_float_bounds_remain_uncertain_without_display_fallback(tmp_path, monkeypatch, bounds):
    path = tmp_path/"bad.xisf"; xisf(path, np.full((1, 2, 4), .2), bounds=bounds)
    monkeypatch.setattr("PIL.Image.open", lambda *a, **kw: pytest.fail("XISF reached Pillow"))
    result = metrics.analyze_image_histogram(path)
    assert result["status"] == "uncertain" and "bounds" in result["error"]


@pytest.mark.parametrize("attributes", [
    {"byteOrder": "middle"}, {"pixelStorage": "unknown"}, {"sampleFormat": "Complex32"},
    {"sampleFormat": "Int32"}, {"compression": "lz4:32"}, {"compression": "zstd:32"},
    {"compression": "zlib:33"}, {"compression": "zlib+sh:32:8"},
    {"checksum": "sha256:"+"0"*64}, {"location": "path:/tmp/external"},
    {"location": "attachment:2:32"}, {"geometry": "2:4:4"},
])
def test_invalid_or_unsupported_encoding_is_explicit(tmp_path, attributes):
    path = tmp_path/"bad.xisf"; xisf(path, np.full((1, 2, 4), .2), attributes=attributes)
    result = metrics.analyze_image_histogram(path)
    assert result["status"] == "uncertain" and result["error"]
    assert "UnidentifiedImageError" not in result["error"] and "channels" not in result


@pytest.mark.parametrize("issue", ["truncated", "corrupt", "oversized", "dtd"])
def test_damaged_payload_rejected_without_scientific_repair(tmp_path, issue):
    path = tmp_path/"bad.xisf"; xisf(path, np.full((1, 2, 4), .2), codec="zlib+sh")
    data = path.read_bytes()
    if issue == "truncated":
        path.write_bytes(data[:-5])
    elif issue == "corrupt":
        path.write_bytes(data[:4096]+b"x"*20)
    elif issue == "oversized":
        # No checksum, so the bounded zlib decoder itself must reject the expansion.
        replacement = zlib.compress(bytes(100000))
        header = f'<xisf version="1.0"><Image geometry="4:2:1" colorSpace="Gray" sampleFormat="Float32" bounds="0:1" location="attachment:4096:{len(replacement)}" compression="zlib:32"/></xisf>'.encode()
        prefix=b"XISF0100"+struct.pack("<I", len(header))+bytes(4)+header
        path.write_bytes(prefix.ljust(4096,b"\0")+replacement)
    else:
        header=b'<!DOCTYPE xisf [<!ENTITY external SYSTEM "file:///tmp/secret">]><xisf version="1.0"/>'
        path.write_bytes(b"XISF0100"+struct.pack("<I",len(header))+bytes(4)+header)
    before=path.read_bytes()
    assert metrics.analyze_image_histogram(path)["status"] == "uncertain"
    assert path.read_bytes() == before


def test_mixed_fits_candidate_xisf_parent_and_roi_boundary(tmp_path):
    source=tmp_path/"parent.xisf"; array=xisf(source,np.full((3,2,4),.2))
    candidate=tmp_path/"candidate.fit"; write_fits(candidate,array)
    report=metrics.generate_metric_report(run_id="010-input",protocol="input.inspect",
        candidate_path=candidate,parent_path=source)
    assert report["candidate"]["metrics"]["status"] == report["parent"]["metrics"]["status"] == "measured"
    assert report["recommended_verdict"] == "uncertain"
    contract={"schema":"starun-siril.regions.v1","reference":{"sha256":sha256_file(source),"width":4,"height":2},
              "regions":[{"id":"core","role":"core","rect":[0,0,2,2]}]}
    regions=tmp_path/"regions.json"; regions.write_text(json.dumps(contract))
    assert metrics.measure_regions(candidate,source,regions)["status"] == "unsupported"


@pytest.mark.parametrize("valid", [False, True])
def test_input_inspection_measures_source_and_surfaces_errors_without_receipt_rewrite(tmp_path, monkeypatch, valid):
    _session(tmp_path,monkeypatch)
    source=tmp_path/"input.xisf"; xisf(source,np.full((3,8,12),.2),bounds="0:1" if valid else None,extra=True)
    session=tmp_path/"xisf-session"
    session_state.init_session(str(source),str(session),input_state="linear",
        state_evidence=["user confirmed input_state=linear; synthetic stacked master"],channel_mode="broadband",channel_map=None,
        target_name="Synthetic",target_type="galaxy",style="natural",stars="adaptive",offline=False,keep_intermediates=True)
    result,direct,linked=_input_inspect_run(session,source,monkeypatch)
    assert result["status"] == "success"
    disk=core.load_json(session/"runs/010-input.json")
    assert "report_warnings" not in disk
    report=core.load_json(session/"reports/010-input/metrics.json")
    assert report["recommended_verdict"] == "uncertain"
    if valid:
        assert "report_warnings" not in result
        assert report["candidate"]["metrics"]["image_count"] == 2
        assert report["candidate"]["metrics"]["channels"][0]["image_median"] == pytest.approx(.2)
    else:
        warning=result["report_warnings"][0]
        assert warning["code"] == "metrics_measurement_unavailable" and "bounds" in warning["message"]
        assert warning["report_path"] == "reports/010-input/metrics.json"
    before={p:p.read_bytes() for p in session.rglob("*") if p.is_file()}
    replay=core.run_script(str(session),protocol="input.inspect",script_value=str(session/disk["script"]["path"]),
        source_value=str(source),expected_values=[str(direct),str(linked)],timeout=30)
    assert replay["replayed"] and all(p.read_bytes()==data for p,data in before.items())


def test_xisf_drift_during_statistics_is_uncertain(tmp_path,monkeypatch):
    source=tmp_path/"input.xisf"; xisf(source,np.full((1,2,4),.2))
    measure=metrics._channel_statistics
    def drift(*args,**kwargs):
        result=measure(*args,**kwargs)
        with source.open("ab") as f:
            f.write(b"x")
        return result
    monkeypatch.setattr(metrics,"_channel_statistics",drift)
    result=metrics.analyze_image_histogram(source)
    assert result["status"]=="uncertain" and "changed during measurement" in result["error"]


def test_v7_policy_is_frozen():
    previous=[p for p in session_state._EXECUTION_POLICY_HISTORY if p.get("guidance")=="stage_recompose_review_v7"]
    assert len(previous)==1
    assert previous[0]=={**{key: value for key, value in {k:v for k,v in session_state._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}.items()
                            if key not in {"recomposition_review", "filter_metadata", "background_sampling"}},"background":"heldout_material_gate_v3","neutral":"heldout_benefit_safety_v1", "guidance":"stage_recompose_review_v7"}
    assert session_state.known_execution_policy(previous[0])
    assert not session_state.execution_policy_is_current({"execution_policy":previous[0]})

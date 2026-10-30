"""Scientific S3 source frames, private controls and audited native identity."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from .test_xisf_metrics import xisf
from .test_background_validation import regions_for, write_fits, display_tail
from deep_sky_siril_artifacts import read_scientific_pixels
from deep_sky_siril_contract import sha256_file, ContractError, EXECUTION_POLICY
from deep_sky_siril_background import load_regions, measure_background, validate_samples, SAMPLING_POLICY
from siril_background_samples_scientific import verify_image_precision
import siril_auto_samples as samples
import deep_sky_siril_processing as processing
import deep_sky_siril_session as session_state
import deep_sky_siril_metrics as metrics

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("channels", [1, 3])
@pytest.mark.parametrize("sample", ["UInt8", "UInt16", "UInt32", "Float32", "Float64"])
@pytest.mark.parametrize("endian,storage,location,codec", [
    ("little", "planar", "attachment", None),
    ("big", "normal", "attachment", None),
    ("big", "planar", "embedded", "zlib"),
    ("little", "normal", "attachment", "zlib+sh"),
    ("big", "normal", "embedded", "zlib+sh"),
])
def test_scientific_frame_matches_asymmetric_fits(tmp_path, channels, sample, endian, storage, location, codec):
    values = np.arange(channels*5*7).reshape(channels, 5, 7) / 200
    bounds = "2:5" if sample.startswith("Float") else None
    if bounds:
        values = 2 + 3*values
    else:
        values = np.rint(values * np.iinfo("u"+str(int(sample[4:])//8)).max)
    source = tmp_path/"source.xisf"
    encoded = xisf(source, values, sample=sample, endian=endian, storage=storage,
                   location=location, codec=codec, bounds=bounds, extra=True)
    normalized = ((encoded.astype(float)-2)/3 if bounds else
                  encoded.astype(float)*(1/np.iinfo(encoded.dtype).max))
    expected = normalized[:, ::-1]
    fits = tmp_path/"source.fit"; write_fits(fits, expected, bitpix=-64)
    actual, selection = read_scientific_pixels(source)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(actual, read_scientific_pixels(fits)[0])
    assert selection["image_index"] == 0 and selection["image_count"] == 2
    assert selection["row_order"] == "fits_bottom_up"
    assert not actual.flags.writeable


@pytest.mark.parametrize("top_down", [False, True])
def test_native_identity_binds_source_selection_and_endpoints(tmp_path, top_down):
    source = tmp_path/"source.xisf"
    values = np.array([[[-.1, 0, 1], [.4, .8, 1.2]]], dtype=np.float32)
    xisf(source, values)
    native = values if top_down else values[:, ::-1]
    image = SimpleNamespace(data=native, bitpix=-32, top_down=top_down)
    evidence = verify_image_precision(image, source)
    assert evidence["scientific_source"] == read_scientific_pixels(source)[1]
    assert evidence["max_abs_normalized_error"] == 0
    assert evidence["endpoints_preserved"]
    wrong = native.copy(); wrong[0, 0, 0] += .01
    with pytest.raises(ValueError, match="ULP"):
        verify_image_precision(SimpleNamespace(data=wrong, bitpix=-32, top_down=top_down), source)
    with pytest.raises(ValueError, match="float32"):
        verify_image_precision(SimpleNamespace(data=native.astype(float), bitpix=-64), source)


def test_private_xisf_controls_and_mixed_measurement_keep_top_origin(tmp_path):
    y, x = np.indices((600, 600))
    values = np.stack([.1 + (x+.4*y)*.00003, .1+(2*x+y)*.00001, .1+(x+y)*.00002]).astype(np.float32)
    source = tmp_path/"source.xisf"; xisf(source, values)
    fits = tmp_path/"source.fit"; write_fits(fits, values[:, ::-1])
    candidate = tmp_path/"candidate.fit"; write_fits(candidate, np.full_like(values, .1))
    regions = tmp_path/"regions.json"; regions_for(source, regions)
    load_regions(regions, source, scientific=True)
    with pytest.raises(ValueError, match="FITS"):
        load_regions(regions, source)
    measured = measure_background(source, candidate, regions, regions_sha256=sha256_file(regions))
    assert measured["status"] == "passed" and measured["decision"] == "review_required"
    first = measured["heldout_regions"][0]
    assert first["parent"]["R"] == pytest.approx(np.median(values[0,25:100,25:100]))
    regions_for(fits, regions)
    equivalent = measure_background(fits, candidate, regions, regions_sha256=sha256_file(regions))
    assert measured["dimensions"] == equivalent["dimensions"]
    assert measured["heldout_regions"] == equivalent["heldout_regions"]
    regions_for(source, regions)
    assert metrics.measure_regions(candidate, source, regions)["status"] == "unsupported"


@pytest.mark.parametrize("kind", ["bounds", "nan", "truncated", "checksum"])
def test_science_failure_is_blocked_not_skip(tmp_path, kind):
    source = tmp_path/"source.xisf"
    values = np.full((1, 600, 600), .1)
    if kind == "nan":
        values[0, 300, 300] = np.nan
    xisf(source, values, bounds=None if kind == "bounds" else "0:1")
    if kind == "truncated":
        source.write_bytes(source.read_bytes()[:5000])
    elif kind == "checksum":
        raw = bytearray(source.read_bytes()); raw[-10] ^= 1; source.write_bytes(raw)
    candidate = tmp_path/"candidate.fit"; write_fits(candidate, np.full((600,600), .1))
    regions = tmp_path/"regions.json"; regions_for(source, regions)
    result = measure_background(source, candidate, regions, regions_sha256=sha256_file(regions))
    assert result["status"] == "uncertain" and result["decision"] == "blocked"
    assert result["reasons"]
    with pytest.raises((ValueError, RuntimeError)):
        samples.generate_background_samples(source)


def test_preview_cannot_replace_science_and_source_hash_drift_rejects(tmp_path, monkeypatch):
    source = tmp_path/"source.xisf"; xisf(source, np.full((1,600,600),.1))
    planes, w, h, domain = samples._load_sampling_image(source, None)
    assert domain == "scientific-xisf" and (w,h) == (600,600)
    assert np.all(planes == np.float32(.1))
    original = samples._exclusion_mask
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        raw=bytearray(source.read_bytes()); raw[-10]^=1; source.write_bytes(raw)
        return result
    monkeypatch.setattr(samples, "_exclusion_mask", drift)
    with pytest.raises(ValueError, match="changed"):
        samples.generate_background_samples(source)


@pytest.mark.parametrize("change", ["missing", "factor", "extra", "late", "stretch"])
def test_v4_requires_immediate_single_identity_before_bridge(tmp_path, change):
    source=tmp_path/"source.xisf"; candidate=tmp_path/"artifacts/c.fit"
    commands=[["load",str(source)],["fmul","1"],["pyscript","bridge"],["subsky","1","-existing"],
              ["save",str(candidate)],*display_tail(source,candidate,tmp_path/"previews/p.jpg",tmp_path/"previews/c.jpg")]
    if change=="missing": commands.pop(1)
    elif change=="factor": commands[1][1]="1.01"
    elif change=="extra": commands[1].append("-foo")
    elif change=="late": commands.insert(1,["stat","main"])
    else: commands.insert(2,["mtf","0",".08","1"])
    with pytest.raises(ContractError):
        processing.validate_background_display(tmp_path,source,candidate,commands,policy="heldout_material_gate_v4")


def test_complete_v8_policy_is_historical():
    old=[p for p in session_state._EXECUTION_POLICY_HISTORY if p.get("guidance")=="stage_xisf_metrics_v8"]
    assert old==[{**{key: value for key, value in {k:v for k,v in session_state._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}.items()
                            if key not in {"recomposition_review", "filter_metadata", "background_sampling"}},"neutral":"heldout_benefit_safety_v1", "guidance":"stage_xisf_metrics_v8","background":"heldout_material_gate_v3"}]
    assert session_state.known_execution_policy(old[0])
    assert not session_state.execution_policy_is_current({"execution_policy":old[0]})


@pytest.mark.parametrize("source_format", ["FITS", "XISF"])
@pytest.mark.parametrize("order", [1, 2])
def test_native_known_gradient_installs_scientific_samples(tmp_path, source_format, order):
    binary=Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
    if not binary.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    y,x=np.indices((600,600))
    values=np.stack([.1+(x+.4*y)*.00003,.1+(2*x+y)*.00001,.1+(x+y)*.00002]).astype(np.float32)
    source=tmp_path/"gradient.fit"
    if source_format == "FITS":
        write_fits(source,values[:,::-1])
    else:
        import struct
        import xml.etree.ElementTree as ET
        source=tmp_path/"gradient.xisf"
        root=ET.Element("xisf", {"version":"1.0", "xmlns":"http://www.pixinsight.com/xisf"})
        image=ET.SubElement(root,"Image", {"id":"gradient", "geometry":"600:600:3",
            "sampleFormat":"Float32", "bounds":"0:1", "imageType":"MasterLight",
            "colorSpace":"RGB", "location":f"attachment:4096:{values.nbytes}"})
        ET.SubElement(image,"FITSKeyword", {"name":"IMAGETYP", "value":"'Master Light'", "comment":"Controlled gradient"})
        header=ET.tostring(root)
        source.write_bytes((b"XISF0100"+struct.pack("<I",len(header))+bytes(4)+header).ljust(4096,b"\0")+values.astype("<f4").tobytes())
    regions=tmp_path/"regions.json";doc=regions_for(source,regions)
    doc['regions'].append({'id': 'protected-shell', 'role': 'faint', 'rect': [220,220,380,380]})
    regions.write_text(json.dumps(doc))
    fit={'schema':'starun-siril.background-sample-contract.v1',
         'source':{'path':str(source),**doc['reference']},
         'fit_samples':[{'id':f'manual-{i}','x':x,'y':y} for i,(x,y) in enumerate(
             [(150,70),(450,70),(50,160),(550,160),(150,260),(450,260),
              (150,340),(450,340),(50,440),(550,440),(150,530),(450,530)])]}
    validate_samples(fit,doc,sampling_policy=SAMPLING_POLICY)
    contract=tmp_path/"samples.json";contract.write_text(json.dumps(fit))
    injection=tmp_path/"injection.json";candidate=tmp_path/"candidate.fit"
    bridge=ROOT/"scripts/siril_background_samples_scientific.py"
    script=tmp_path/"gradient.ssf"
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\n'
                      f'pyscript "{bridge}" --contract "{contract}" --contract-sha256 {sha256_file(contract)} '
                      f'--expected-source "{source}" --receipt "{injection}"\nsubsky {order} -existing\n'
                      f'save "{candidate.with_suffix("")}" -chksum\nclose\n')
    config=tmp_path/"s.ini";config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\n")
    result=subprocess.run([str(binary),f"--initfile={config}",f"--directory={tmp_path}","--offline",f"--script={script}"],
                          capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    receipt=json.loads(injection.read_text())
    assert receipt["status"]=="verified" and receipt["installed_count"]==len(fit["fit_samples"])
    assert receipt["image_precision"]["scientific_source"]["row_order"]=="fits_bottom_up"
    measured=measure_background(source,candidate,regions,regions_sha256=sha256_file(regions),sampling_policy=SAMPLING_POLICY)
    assert measured["status"]=="passed" and measured["decision"]=="review_required"
    assert max(measured["dimensions"][c]["candidate_span"] for c in ["R","G","B"])<1e-6


@pytest.mark.parametrize("background,adapter,valid", [
    ("heldout_material_gate_v4", "siril_background_samples_scientific.py", True),
    ("heldout_material_gate_v4", "siril_background_samples.py", False),
    ("heldout_material_gate_v3", "siril_background_samples.py", True),
    ("heldout_material_gate_v3", "siril_background_samples_scientific.py", False),
])
def test_static_background_route_is_frozen(tmp_path, background, adapter, valid):
    from deep_sky_siril_validation import _validate_pyscript
    report=tmp_path/"reports/030-background";report.mkdir(parents=True)
    source=tmp_path/"source.fit";write_fits(source,np.full((40,40),.1))
    contract=report/"samples.json"
    contract.write_text(json.dumps({"schema":"starun-siril.background-sample-contract.v1",
        "source":{"path":str(source),"sha256":sha256_file(source),"width":40,"height":40},
        "fit_samples":[{"id":"sample-1","x":20,"y":20}]}))
    tokens=["pyscript",str(ROOT/"scripts"/adapter),"--contract",str(contract),
            "--contract-sha256",sha256_file(contract),"--expected-source",str(source),
            "--receipt",str(report/"receipt.json")]
    policy=json.loads((ROOT/"references/command-policy.json").read_text())
    args=dict(session=tmp_path,source=source,policy=policy,execution_policy={"background":background})
    if valid:
        assert _validate_pyscript("background.subtract",tokens,**args)==report/"receipt.json"
    else:
        with pytest.raises(ContractError,match="frozen policy"):
            _validate_pyscript("background.subtract",tokens,**args)


@pytest.mark.parametrize("mutation", ["valid", "hash", "size", "unknown", "content"])
def test_v8_manual_policy_replay_is_exact(mutation):
    from dataclasses import replace
    import query_siril_manual as query
    from siril_manual_bundle import BundleError
    import copy
    snapshot=query.verify_bundle();indexes=query._load_indexes(snapshot)
    live=query._load_policy(snapshot,indexes)
    digest="43461f3cd50fc9ee3210db84e1de44fad842940dbcdd8c8ec8564c5f21b8bd73"
    capture=replace(live.capture,data=query._V8_COMMAND_POLICY_BYTES,sha256=digest,size_bytes=3956)
    previous=query._load_policy(snapshot,indexes,capture=capture)
    documents=[query._envelope(snapshot,"command",query._command_result(indexes,previous,name,set()))
               for name in ("subsky","pyscript","fmul")]
    payload={"execution_policy":copy.deepcopy(next(p for p in session_state._EXECUTION_POLICY_HISTORY
        if p.get("guidance")=="stage_xisf_metrics_v8")),
        "knowledge":{"command_policy":{"path":query.POLICY_PATH,"sha256":digest,"size":3956}}}
    if mutation=="hash":payload["knowledge"]["command_policy"]["sha256"]="0"*64
    elif mutation=="size":payload["knowledge"]["command_policy"]["size"]+=1
    elif mutation=="unknown":payload["execution_policy"]["guidance"]="unknown"
    elif mutation=="content":documents[0]["result"]["documentation"]["usage"]+=" forged"
    if mutation=="valid":
        assert list(query.verify_query_evidence_documents(documents,session_payload=payload))==documents
    else:
        with pytest.raises(BundleError):
            query.verify_query_evidence_documents(documents,session_payload=payload)

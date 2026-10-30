"""Precision preparation, failed artifact isolation and native receipt lifecycle."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import subprocess

import numpy as np
import pytest

from . import test_deep_sky_siril as support
from .test_starnet_linear_bridge import native, Siril, echo
from .test_native_processing import write_fits
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_contract as contract
import siril_starnet_linear as bridge


def setup_run(native, monkeypatch):
    root, _, _, base = native
    original_probe = support._fake_probe
    def probe(*args, **options):
        value = original_probe(*args, **options)
        value["tools"]["starnet"] = base["runtime"]
        return value
    monkeypatch.setattr(support, "_fake_probe", probe)
    session, source, _ = support._session(root, monkeypatch, input_state="linear", width=512, height=512)
    directory = session / "reports/060-stars"
    directory.mkdir()
    full, primary, layer = [session / "artifacts" / name for name in ("full.fit", "starless.fit", "layer.fit")]
    document = deepcopy(base)
    document.update(source={"path":str(source), "sha256":contract.sha256_file(source)},
                    input_fits=str(full), work_dir=str(session/"runtime/starnet/060-stars"))
    contract_path, receipt_path = directory/"contract.json", directory/"bridge.json"
    contract_path.write_text(json.dumps(document))
    script = session/"scripts/060-stars.ssf"
    script.write_text(
        f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\nsave "{full.with_suffix("")}" -chksum\n'
        f'load "{source}"\nfmul 1\n'
        f'pyscript "{base["runtime"]["adapter"]["path"]}" --contract "{contract_path}" '
        f'--contract-sha256 {contract.sha256_file(contract_path)} --expected-source "{source}" --receipt "{receipt_path}"\n'
        f'save "{primary.with_suffix("")}" -chksum\nclose\n'
        'pm "$artifacts/full$ - $artifacts/starless$"\n'
        f'save "{layer.with_suffix("")}" -chksum\nclose\n')
    support._write_ssf_provenance(session,script,"stars.separate")
    previews=[session/"previews/starless.jpg",session/"previews/layer.jpg"]
    text=script.read_text().replace(
        f'save "{primary.with_suffix("")}" -chksum\nclose',
        f'save "{primary.with_suffix("")}" -chksum\nautostretch -linked -2.8 0.22\nsavejpg "{previews[0].with_suffix("")}" 95\nclose').replace(
        f'save "{layer.with_suffix("")}" -chksum\nclose',
        f'save "{layer.with_suffix("")}" -chksum\nautostretch -linked -2.8 0.22\nsavejpg "{previews[1].with_suffix("")}" 95\nclose')
    script.write_text(text)
    support._write_ssf_provenance(session,script,"stars.separate")
    expected=[full,primary,layer,receipt_path,*previews]
    options=dict(protocol="stars.separate",script_value=script,source_value=source,
                 expected_values=expected,primary_output_value=primary,timeout=30)
    monkeypatch.setattr(core,"_decode_scientific_output",lambda path,**kwargs:
                        dict(passed=True,decoder={"artifact_fingerprint":contract.fingerprint(path,include_path=False)}))
    monkeypatch.setattr(core,"_try_generate_run_metrics",lambda *args,**kwargs:None)
    return session,source,document,options


@pytest.mark.parametrize("mutation",["first","second","factor","extra","after","stretch","wrong_parent"])
def test_native_preparation_rejects_mutation(native,monkeypatch,mutation):
    session,source,document,options=setup_run(native,monkeypatch)
    text=options["script_value"].read_text()
    if mutation=="first": text=text.replace("fmul 1\n","",1)
    elif mutation=="second":
        index=text.rfind("fmul 1\n"); text=text[:index]+text[index+7:]
    elif mutation=="factor": text=text.replace("fmul 1","fmul 2",1)
    elif mutation=="extra": text=text.replace("fmul 1","fmul 1 1",1)
    elif mutation=="after": text+="fmul 1\n"
    elif mutation=="stretch": text=text.replace("fmul 1\n","fmul 1\nmtf 0 0.5 1\n",1)
    else: text=text.replace(f'load "{source}"',f'load "{session}/artifacts/other.fit"',2)
    with pytest.raises(contract.ContractError):
        processing.prepare_processing(session,contract.load_json(session/"session.json"),"stars.separate",source,
                                      Path(document["input_fits"]).with_name("starless.fit"),text,
                                      {},None,None,None,None,run_id="060-stars")


@pytest.mark.parametrize("mode",["missing","precision","tampered","fatal","closure","partial","success"])
def test_run_checks_bridge_and_isolates_only_execution_failure(native,monkeypatch,mode):
    session,source,document,options=setup_run(native,monkeypatch)
    full,primary,layer,receipt_path=options["expected_values"][:4]
    raw,scale,zero=core.artifacts.read_fits_pixels(source)
    values=(np.asarray(raw,dtype=float)*scale+zero).astype("float32")
    def run(argv,**kwargs):
        write_fits(full,values)
        if mode=="precision": shutil.copyfile(source,full)
        if mode in {"missing","precision","partial"}:
            write_fits(primary,values)
        else:
            with monkeypatch.context() as scoped:
                scoped.setattr(bridge.subprocess,"run",echo)
                evidence=bridge.process_starless(Siril(full),document,Path(document["work_dir"]))
            shutil.copyfile(evidence["argv"][4],primary)
            receipt={"schema":bridge.RECEIPT_SCHEMA,"status":"verified",
                     "contract_sha256":contract.sha256_file(receipt_path.with_name("contract.json")),
                     **{key:document[key] for key in ("source","runtime","stride")},**evidence}
            if mode=="tampered": receipt["linear_stretch"]["target_background"]=.1
            receipt_path.write_text(json.dumps(receipt))
        write_fits(layer,np.ones_like(values)*.1 if mode=="closure" else np.zeros_like(values))
        for preview in options["expected_values"][4:]:
            support._write_jpeg(preview,size=(512,512))
        if mode=="partial":
            collision=session/"reports/060-stars/rejected/artifacts/full.fit"
            collision.parent.mkdir(parents=True)
            collision.write_bytes(b"existing evidence")
        return subprocess.CompletedProcess(argv,0,"log: Traceback (most recent call last):\n" if mode in {"precision","fatal"} else "log: Script execution finished successfully.\n")
    monkeypatch.setattr(core.subprocess,"run",run)
    result=core.run_script(session,**options)
    if mode=="success":
        assert result["status"]=="success" and result["processing_checks"]["bridge"]["status"]=="verified"
        assert all(p.is_file() for p in options["expected_values"])
        assert not (session/"reports/060-stars/rejected").exists()
        pending=core.check_review(session,"060-stars",None)
        assert pending["status"]=="review_required"
        review=pending["review"]
        review.update(verdict="reject",gates={k:("fail" if k=="structure" else "pass")
                      for k in ("structure","background","color","stars","geometry")},
                      notes="Controlled formal rejection of a synthetic candidate; preserve all original materials.")
        contract.atomic_write_json(session/"reviews/060-stars.json",review)
        assert core.check_review(session,"060-stars",session/"reviews/060-stars.json")["status"]=="review_valid"
        before=support._session_bytes(session)
        replay=core.run_script(session,**options)
        assert replay["replayed"] and support._session_bytes(session)==before
        return
    assert result["status"]=="failed" and result["exit_code"]==0
    assert result["outputs"]==[] and result["primary_output"] is None
    assert result["processing_checks"]["status"]=="failed"
    expected_code=("native_input_precision_invalid" if mode=="precision" else
                   "starnet_bridge_unverified" if mode in {"missing","tampered","partial"} else "stars_separation_failed")
    assert result["failure"]["reason_code"]==expected_code
    assert result["failure"]["log_path"]=="logs/060-stars.log"
    assert len(result["quarantine_errors"])==(1 if mode=="partial" else 0)
    assert len(result["rejected_outputs"])==(4 if mode=="partial" else 5)
    if mode=="partial":
        assert full.is_file()
        assert (session/"reports/060-stars/rejected/artifacts/full.fit").read_bytes()==b"existing evidence"
    for record in result["rejected_outputs"]:
        assert not (session/record["original_path"]).exists()
        assert contract.fingerprint_matches({**record,"path":str(session/record["path"])})
    assert receipt_path.with_name("contract.json").is_file()
    assert not sessions._verified_run_outputs(session)
    with pytest.raises(contract.ContractError): core.review_materials_for_run(session,"060-stars")
    with pytest.raises(contract.ContractError): core.run_script(session,**options)
    assert not sessions._source_is_bound(session,contract.load_json(session/"session.json"),primary)


@pytest.mark.parametrize("problem",["collision","source_link","parent_link","unlink","changed"])
def test_quarantine_preserves_partial_failure_evidence(tmp_path,monkeypatch,problem):
    session=tmp_path
    (session/"artifacts").mkdir()
    first,second=session/"artifacts/first.fit",session/"artifacts/second.fit"
    first.write_bytes(b"first"); second.write_bytes(b"second")
    target=session/"reports/060-stars/rejected/artifacts/first.fit"
    if problem=="collision":
        target.parent.mkdir(parents=True); target.write_bytes(b"existing")
    elif problem=="source_link":
        first.unlink(); first.symlink_to(second)
    elif problem=="parent_link":
        (session/"reports/060-stars").mkdir(parents=True)
        (session/"reports/060-stars/rejected").symlink_to(session/"artifacts",target_is_directory=True)
    elif problem=="unlink":
        unlink=Path.unlink
        def fail_unlink(path,*args,**kwargs):
            if path==first: raise OSError("controlled unlink failure")
            return unlink(path,*args,**kwargs)
        monkeypatch.setattr(Path,"unlink",fail_unlink)
    else:
        link=core.os.link
        def change_after_link(source,destination,**kwargs):
            link(source,destination,**kwargs)
            if source==first: source.write_bytes(b"changed")
        monkeypatch.setattr(core.os,"link",change_after_link)
    records,errors=core._quarantine_separation_outputs(session,"060-stars",[first,second])
    assert errors and (first.exists() or first.is_symlink())
    if problem=="parent_link":
        assert second.exists() and not records
    else:
        assert not second.exists()
        assert any(r["original_path"]=="artifacts/second.fit" for r in records)
    if problem=="collision": assert target.read_bytes()==b"existing"
    if problem=="source_link": assert first.is_symlink()


def test_v5_is_frozen_and_unknown_variants_are_rejected():
    previous=[p for p in sessions._EXECUTION_POLICY_HISTORY if p.get("guidance")=="stage_starnet_linear_v5"]
    assert len(previous)==1 and sessions.known_execution_policy(previous[0])
    assert previous[0]=={**{key: value for key, value in {k:v for k,v in sessions._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}.items()
                            if key not in {"recomposition_review", "filter_metadata", "background_sampling"}},"background":"heldout_material_gate_v3","neutral":"heldout_benefit_safety_v1", "guidance":"stage_starnet_linear_v5",
                         "star_diagnostics":"subject_roi_residual_v1","delivery":"paired_fits_jpeg_v1"}
    assert not sessions.execution_policy_is_current({"execution_policy":previous[0]})
    assert not sessions.known_execution_policy({**previous[0],"guidance":"stage_starnet_precision_unknown"})


@pytest.mark.parametrize("channels,bzero,bscale,identity",[(1,32768,1,True),(3,32768,1,True),(1,0,1,False),(3,32768,.5,False)])
def test_real_siril_integer_promotion_preserves_or_rejects_scaling(tmp_path,monkeypatch,channels,bzero,bscale,identity):
    executable=Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
    if not executable.is_file(): pytest.skip("Siril 1.4 runtime is unavailable")
    raw=np.arange(320,dtype=np.int32).reshape(16,20)-160
    raw[0,:2]=[-32768,32767]
    if channels==3: raw=np.stack([raw,raw,raw])
    cards=[("SIMPLE","T"),("BITPIX",16),("NAXIS",3 if channels==3 else 2),
           ("NAXIS1",20),("NAXIS2",16)]
    if channels==3: cards.append(("NAXIS3",3))
    cards += [("BZERO",bzero),("BSCALE",bscale)]
    header=("".join(f"{key:<8}= {value:>20}".ljust(80) for key,value in cards)+"END".ljust(80)).encode()
    header+=b" "*(-len(header)%2880)
    pixels=raw.astype(">i2").tobytes()
    source=tmp_path/"source.fit"
    source.write_bytes(header+pixels+b"\0"*(-len(pixels)%2880))
    before=contract.fingerprint(source,include_path=False)
    config=tmp_path/"siril.ini"
    config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\n")
    script=tmp_path/"promote.ssf"
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\n'
                      f'save "{tmp_path}/integer" -chksum\nfmul 1\nsave "{tmp_path}/first" -chksum\n'
                      f'load "{source}"\nfmul 1\nsave "{tmp_path}/second" -chksum\nclose\n')
    completed=subprocess.run([str(executable),f"--initfile={config}",f"--directory={tmp_path}",
                              "--offline",f"--script={script}"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                             text=True,timeout=60,check=False)
    (tmp_path/"siril.log").write_text(completed.stdout)
    assert completed.returncode==0,completed.stdout
    assert core.artifacts.read_fits_header(tmp_path/"integer.fit")["BITPIX"]==16
    original,scale,zero=core.artifacts.read_fits_pixels(source)
    reference=np.asarray(original,dtype=float)*scale+zero
    for name in ("first","second"):
        path=tmp_path/(name+".fit")
        assert core.artifacts.read_fits_header(path)["BITPIX"]==-32
        data,scale,zero=core.artifacts.read_fits_pixels(path)
        actual=np.asarray(data,dtype=float)*scale+zero
        rounded=reference.astype("float32")
        if identity:
            np.testing.assert_array_less(np.abs(actual-reference),2*np.abs(np.spacing(rounded)).astype(float)+np.finfo(float).tiny)
            for endpoint in (0,1):
                assert np.all(actual[reference==endpoint]==endpoint)
        else:
            assert np.max(np.abs(actual-reference)) > 1e-7
            layer=tmp_path/"layer.fit"
            write_fits(layer,np.zeros_like(actual,dtype="float32"))
            monkeypatch.setattr(processing,"validate_separation_bridge",lambda *args:None)
            with pytest.raises(contract.ContractError,match="Separation full source changed"):
                processing.validate_processing_outputs(tmp_path,source,path,
                    {"star_separation":{"full_source":path.name,"linear_stars":layer.name}})
    assert contract.fingerprint(source,include_path=False)==before




def test_legacy_preparation_rejects_new_identity_multiplication(native,monkeypatch):
    session,source,document,options=setup_run(native,monkeypatch)
    directory=session/"reports/060-stars"
    document["schema"]="starun-siril.starnet-contract.v1"
    (directory/"contract.json").write_text(json.dumps(document))
    with pytest.raises(contract.ContractError,match="Legacy separation does not allow fmul"):
        processing.prepare_processing(session,contract.load_json(session/"session.json"),"stars.separate",source,
                                      options["primary_output_value"],options["script_value"].read_text(),
                                      {},None,None,None,None,run_id="060-stars")


@pytest.mark.parametrize("mutation",["valid","current","unknown_guidance","unknown_hash","size","usage","authority"])
def test_historical_manual_evidence_uses_exact_frozen_policy(mutation):
    import query_siril_manual as query
    from dataclasses import replace
    from siril_manual_bundle import BundleError
    snapshot=query.verify_bundle()
    indexes=query._load_indexes(snapshot)
    live=query._load_policy(snapshot,indexes)
    capture=replace(live.capture,data=query._V5_COMMAND_POLICY_BYTES,
                    sha256=query._V5_COMMAND_POLICY_SHA256,size_bytes=query._V5_COMMAND_POLICY_SIZE)
    previous=query._load_policy(snapshot,indexes,capture=capture)
    documents=[query._envelope(snapshot,"command",query._command_result(indexes,previous,name,set()))
               for name in ("ght","fmul")]
    payload={"execution_policy":dict(next(p for p in sessions._EXECUTION_POLICY_HISTORY if p.get("guidance")=="stage_starnet_linear_v5")),
             "knowledge":{"command_policy":{"path":query.POLICY_PATH,"sha256":capture.sha256,"size":capture.size_bytes}}}
    assert "stars.separate" not in documents[1]["result"]["execution_policy"]["allowed_protocols"]
    assert "stars.separate" in live.allowed_protocols["fmul"]
    if mutation=="current": payload["execution_policy"]=dict(contract.EXECUTION_POLICY)
    elif mutation=="unknown_guidance": payload["execution_policy"]["guidance"]="unknown"
    elif mutation=="unknown_hash": payload["knowledge"]["command_policy"]["sha256"]="0"*64
    elif mutation=="size": payload["knowledge"]["command_policy"]["size"]+=1
    elif mutation=="usage": documents[0]["result"]["documentation"]["usage"]+=" forged"
    elif mutation=="authority": documents[1]["result"]["execution_policy"]["allowed_protocols"].append("stars.separate")
    if mutation=="valid":
        assert list(query.verify_query_evidence_documents(documents,session_payload=payload))==documents
    else:
        with pytest.raises(BundleError):
            query.verify_query_evidence_documents(documents,session_payload=payload)

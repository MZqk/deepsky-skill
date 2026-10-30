"""Preserve exclusions and soft weights, measure color honestly and retain old evidence."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

import deep_sky_siril_color as color
import deep_sky_siril_contract as contract
import deep_sky_siril_metrics as metrics
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
from .test_recomposition_proposals import write
from .test_color_evaluation import ColorNative
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration

ROOT = Path(__file__).parents[1]
PRIOR_PATH = ROOT/'tests/fixtures/vivid-preservation-prior.json'
PRIOR = contract.load_json(PRIOR_PATH)


def test_exact_outgoing_complete_fingerprints(tmp_path):
    assert sessions._PRE_VIVID_BINDINGS == PRIOR
    # prior=vivid-preservation-prior.json: color_preservation, chroma_denoise, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    assert not sessions.execution_policy_is_current({'execution_policy': PRIOR['execution_policy']})
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': PRIOR['execution_policy']})
    assert stretch.implementation(tmp_path) == PRIOR['implementation']
    assert color.helper_fingerprint(tmp_path) == PRIOR['helpers']['deep_sky_siril_color.py']
    for name,binding in PRIOR['documents'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path':name,**binding})
        assert not sessions.registered_guidance_reference({'execution_policy': contract.EXECUTION_POLICY}, {'path':name,**binding})
    mixed = {**PRIOR['implementation'], 'deep_sky_siril_color.py': stretch.implementation()['deep_sky_siril_color.py']}
    assert not stretch.implementation_matches(tmp_path,mixed)


def test_outgoing_replay_and_finalize(tmp_path,monkeypatch):
    replay_and_finalize_without_migration(tmp_path,monkeypatch,PRIOR_PATH)


def test_pink_selection_retains_normalization_and_cyan_definition():
    rgb = np.array([.6,.3,.3])[:,None,None]
    assert color.selector_weight(rgb,'red').item() == 0
    assert color.selector_weight(rgb,'red',preservation=True).item() == pytest.approx(.15625)
    np.testing.assert_array_equal(color.selector_weight(rgb,'cyan'),color.selector_weight(rgb,'cyan',preservation=True))


@pytest.mark.parametrize('start,end', [(None,.8),(.4,None),(.8,.4),(.8,.8),(-.1,.8),(.1,1.1),(True,.8),(0,float('nan'))])
def test_saturation_protection_requires_explicit_ordered_finite_numbers(start,end):
    with pytest.raises(contract.ContractError):
        color.validate_saturation_protection(dict(saturation_protect_start=start,saturation_protect_end=end))


@pytest.mark.parametrize('native',[False,True])
def test_native_and_independent_mask_keep_soft_weights_and_zero_exclusions(tmp_path,native):
    binary=Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if native and not binary.exists():pytest.skip('Siril unavailable')
    (tmp_path/'artifacts').mkdir();(tmp_path/'previews').mkdir()
    data=np.tile(np.array([.5,.3,.2])[:,None,None],(1,64,64))
    data[:,:,40:]=np.array([.5,.025,.025])[:,None,None]
    data[:,52:,40:]*=1e-9
    data[:,:4,:4]=0
    parent=write(tmp_path/'artifacts/parent.fit',data)
    params={'D':.3,'mask_mode':'none','mask_policy':color.PRESERVATION,
            'saturation_protect_start':.4,'saturation_protect_end':.8}
    script,primary,_,binding=color.script_text(tmp_path,parent,'088-color',params)
    path=tmp_path/'trial.ssf';path.write_text(script)
    if native:
        config=tmp_path/'siril.ini';config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
        from deep_sky_siril_tooling import _subprocess_environment
        result=subprocess.run([str(binary),'--offline','--initfile='+str(config),'--directory='+str(tmp_path),'--script='+str(path)],capture_output=True,text=True,timeout=60,env=_subprocess_environment('siril_runtime'))
        assert result.returncode==0,result.stdout+result.stderr
    else:
        ColorNative()(['siril','--script='+str(path),'--directory='+str(tmp_path)])
    check=processing.measure_local_saturation(tmp_path,parent,primary,binding)
    mask=color.pixels(tmp_path/binding['layers']['mask'])[0]
    assert float(np.median(mask[12:52,12:25]))==pytest.approx(.5,abs=1e-6)
    assert np.all(mask[:,40:]==0)
    np.testing.assert_array_equal(color.pixels(primary)[:,:,40:],color.pixels(parent)[:,:,40:])
    assert max(check['max_abs_errors'].values())<=1e-6
    assert check['protected_max_abs']==[0,0,0]
    raw=color.pixels(tmp_path/binding['layers']['mask_base'])[0]
    assert np.all(mask<=raw+1e-7)


def protection_scene(tmp_path):
    data=np.full((3,40,48),.02)
    data[:,4:16,20:32]=np.array([.18,.06,.05])[:,None,None]
    data[:,20:32,20:32]=np.array([.06,.09,.2])[:,None,None]
    source=write(tmp_path/'source.fit',data)
    rows=[{'id':'sky','role':'background','rect':[0,0,12,12]},
          {'id':'diffuse-red','role':'faint','rect':[20,24,32,36]},
          {'id':'reflection-blue','role':'faint','rect':[20,8,32,20]}]
    regions=tmp_path/'regions.json'
    contract.atomic_write_json(regions,{'schema':'starun-siril.regions.v1','reference':{'sha256':contract.sha256_file(source),'width':48,'height':40},'regions':rows})
    return source,regions,data


def test_rgb_protection_explains_calibration_gains_and_unmeasurable_background(tmp_path):
    source,regions,data=protection_scene(tmp_path)
    gains=np.array([.8,1.,1.15])
    candidate=write(tmp_path/'candidate.fit',(data-.02)*gains[:,None,None]+.025)
    report=metrics.measure_color_protection(source,candidate,regions,gains=gains.tolist())
    assert {r['id'] for r in report['regions']}=={'diffuse-red','reflection-blue'}
    for row in report['regions']:
        assert max(abs(v) for v in row['gain_adjusted_signal_delta'])<1e-7
        assert row['signal_chromaticity']['parent']['status']=='measured'
        assert row['signal_chromaticity']['parent']['rgb_fraction']!=row['signal_chromaticity']['candidate']['rgb_fraction']
    unavailable=metrics.measure_color_protection(source,candidate,regions,sky_ids=[])
    assert all(r['signal_chromaticity']['candidate']['status']=='unavailable' for r in unavailable['regions'])
    doc=contract.load_json(regions);doc['reference']['sha256']='0'*64;contract.atomic_write_json(regions,doc)
    with pytest.raises(ValueError,match='hash changed'):metrics.measure_color_protection(source,candidate,regions)


def test_color_protection_rejects_geometry_change_and_cannot_accept_degraded_region(tmp_path):
    source,regions,data=protection_scene(tmp_path)
    candidate=write(tmp_path/'candidate.fit',data[:,:,:-1])
    with pytest.raises(ValueError,match='geometry'):metrics.measure_color_protection(source,candidate,regions)
    from deep_sky_siril_core import _color_protection_skeleton,_validate_color_protection
    report=metrics.measure_color_protection(source,source,regions);report['regions_path']='reports/regions.json'
    run={'processing_checks':{'color_protection':report}}
    block=_color_protection_skeleton(run)
    for name,value in block['structures'].items():value['not_applicable_reason']='Applicability is assessed from the synthetic scene'
    for row in block['regions']:row.update(continuity='Continuous',color='Preserved',weak_edges='Retained',assessment='acceptable')
    review={'verdict':'accept','color_protection_assessment':block,'inspected_materials':[{'path':'reports/regions.json'}]}
    _validate_color_protection(run,review)
    block['regions'][0]['assessment']='degraded'
    with pytest.raises(contract.ContractError,match='Neutral sky'): _validate_color_protection(run,review)

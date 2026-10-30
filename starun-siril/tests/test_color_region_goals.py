"""Regional growth goals and hard protected rectangles share one native color proposal."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

import deep_sky_siril_color as color
import deep_sky_siril_subject_mask as subject
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
from .test_color_evaluation import scene, execute, review, save_review, rebind_fixture_parent
from .test_recomposition_proposals import case
from ._policy_pins import replay_and_finalize_without_migration, assert_contract_domains

ROOT=Path(__file__).parents[1]
PRIOR=contract.load_json(ROOT/'tests/fixtures/color-region-goals-prior.json')
REAL_RUN=subprocess.run
BINARY=Path('/Applications/Siril.app/Contents/MacOS/siril-cli')


@pytest.fixture
def regional(scene):
    session,parent,regions,native=scene
    values=np.full((3,64,64),.03)
    values[:,20:36,28:44]=np.array([.25,.30,.34])[:,None,None]
    values[:,20:36,36:44]=np.array([.5,.1,.1])[:,None,None]
    rebind_fixture_parent(scene,values)
    doc=contract.load_json(regions)
    doc['regions'].insert(1,{'id':'red-filament','role':'faint','rect':[36,28,44,44]})
    contract.atomic_write_json(regions,doc)
    goals={'schema':color.REGIONAL_SCHEMA,'regions':color.record(session,regions),
           'goals':[{'region':r['id'],'intent':'enhance','target_gain':.02} if r['id']=='subject'
                    else {'region':r['id'],'intent':'preserve'} for r in doc['regions']]}
    path=session/'reports/region-goals.json';contract.atomic_write_json(path,goals)
    return scene,path,goals


def solve(regional,run_id='085-regional',**changes):
    scene,path,_=regional
    params={'region_goals':path,'strength':.5,'saturation_protect_start':.7,'saturation_protect_end':.95,
            'goal':'Enhance the pale blue shell while retaining the red filament',
            'parameter_reason':'One explicit color proposal with observed regional intents'}
    return color.solve(*scene[:3],run_id,**(params|changes))


def test_pale_blue_grows_without_requiring_red_growth_and_formal_review(regional):
    scene,path,_=regional;session,parent,*_=scene
    solved=solve(regional);report=contract.load_json(Path(solved['report']))
    assert solved['status']=='solved' and report['target_gain'] is report['achieved_gain'] is None
    goals={r['region']:r for r in report['target_results']}
    assert goals['subject']['pixels']==128 and goals['subject']['target_reached'] is True
    assert goals['red-filament']['max_abs_change']==0 and goals['red-filament']['target_reached'] is None
    red=next(r for r in report['trials'][0]['regions'] if r['id']=='red-filament')
    assert red['candidate']['saturation_median']==red['parent']['saturation_median']
    assert report['target_reached'] is True
    receipt,args=execute(scene,solved)
    checked=receipt['processing_checks']['color_curve']
    assert checked['region_goals']==color.record(session,path)
    assert checked['target_gain'] is checked['achieved_gain'] is None
    assert checked['trial_max_abs_error']<=1e-6
    draft=review(session,receipt)
    assert {r['intent'] for r in draft['color_assessment']['regions']}=={'enhance','preserve'}
    bad=copy.deepcopy(draft);next(r for r in bad['color_assessment']['regions'] if r['intent']=='preserve')['assessment']='degraded'
    with pytest.raises(contract.ContractError):color.validate_assessment(session,receipt,bad)
    bad=copy.deepcopy(draft);bad['color_assessment']['regions'][0]['intent']='preserve'
    with pytest.raises(contract.ContractError):color.validate_assessment(session,receipt,bad)
    save_review(session,receipt,draft)
    assert core.run_script(session,**args)['replayed']


@pytest.mark.parametrize('change',['missing','duplicate','unknown','nonfaint','bool','nan','zero','preserve_gain','covered','roi_drift'])
def test_invalid_goals_fail_before_native_or_trial_directory(regional,change):
    scene,path,goals=regional
    if change=='missing':goals['goals'].pop()
    elif change=='duplicate':goals['goals'].append(goals['goals'][0])
    elif change=='unknown':goals['goals'][0]['region']='missing'
    elif change=='nonfaint':goals['goals'][-1].update(intent='enhance',target_gain=.1)
    elif change in ('bool','nan','zero'):goals['goals'][0]['target_gain']={'bool':True,'nan':float('nan'),'zero':0}[change]
    elif change=='preserve_gain':goals['goals'][1]['target_gain']=0
    elif change=='covered':
        doc=contract.load_json(scene[2]);doc['regions'][1]['rect']=[28,28,44,44];contract.atomic_write_json(scene[2],doc)
        goals['regions']=color.record(scene[0],scene[2])
    else:goals['regions']['sha256']='0'*64
    if change=='nan':path.write_text(json.dumps(goals))
    else:contract.atomic_write_json(path,goals)
    with pytest.raises(contract.ContractError):solve(regional)
    assert not scene[-1].calls and not (scene[0]/'reports/085-regional').exists()


def test_all_preserve_skips_without_identity(regional):
    scene,path,goals=regional
    goals['goals']=[{'region':r['region'],'intent':'preserve'} for r in goals['goals']]
    contract.atomic_write_json(path,goals)
    result=solve(regional,strength=None,saturation_protect_start=None,saturation_protect_end=None,goal=None,parameter_reason=None)
    report=contract.load_json(Path(result['report']))
    assert result['status']=='skipped' and not report['trials'] and not scene[-1].calls


def test_distinct_targets_are_diagnostic_and_low_saturation_is_not_zero(regional):
    scene,path,goals=regional;goals['goals'][0]['target_gain']=4;contract.atomic_write_json(path,goals)
    result=solve(regional);report=contract.load_json(Path(result['report']))
    assert report['target_reached'] is False and result['status']=='solved'
    receipt,_=execute(scene,result);color.validate_assessment(scene[0],receipt,review(scene[0],receipt))
    values=color.pixels(scene[1]).copy();values[:,20:36,28:36]=.2
    rebind_fixture_parent(scene,values);goals['regions']=color.record(scene[0],scene[2]);contract.atomic_write_json(path,goals)
    rows,state=color.measure_region_goals(scene[1],scene[1],contract.load_json(scene[2])['regions'],goals)
    assert state is None and rows[0]['achieved_gain'] is None and rows[0]['saturation_median_change']==0


def test_mask_revision_selector_and_target_file_drift(regional):
    scene,path,goals=regional;session=scene[0]
    first=solve(regional)
    second=solve(regional,'086-cyan',mask_mode='luminance',selector='cyan',previous=first['report'],observation='The blue shell is isolated from the protected red filament')
    assert second['status']=='solved'
    receipt,args=execute(scene,second,'087-formal')
    assert receipt['processing_checks']['color_curve']['target_results'][0]['selector_pixels']>0
    goals['goals'][0]['target_gain']=.1;contract.atomic_write_json(path,goals)
    with pytest.raises(contract.ContractError):core.run_script(session,**args)


def test_recomputed_results_and_undeclared_fill_are_rejected(regional):
    scene,path,_=regional;session=scene[0]
    result=solve(regional);report=contract.load_json(Path(result['report']));report['target_results'][0]['achieved_gain']+=.1
    contract.atomic_write_json(Path(result['report']),report)
    with pytest.raises(contract.ContractError):color.validate_evaluation_evidence(session,scene[1],report)
    with pytest.raises(contract.ContractError):solve(regional,'086-both',target_gain=.1)


@pytest.fixture
def native_regional(regional,monkeypatch):
    if not BINARY.exists():pytest.skip('Siril unavailable')
    scene,_,_=regional;session=scene[0]
    monkeypatch.setattr(subprocess,'run',REAL_RUN)
    path=session/'reports/tool-probe.json';probe=contract.load_json(path)
    probe['tools']['siril_cli'].update(path=str(BINARY),version='1.4.4',compatible=True,fingerprint=contract.fingerprint(BINARY))
    contract.atomic_write_json(path,probe);monkeypatch.setattr(core.tooling,'probe_tools',lambda *,offline=False:probe)
    payload=contract.load_json(session/'session.json');payload['tool_probe']['sha256']=contract.sha256_file(path)
    payload.pop('session_sha256');payload['session_sha256']=contract.stable_hash(payload);contract.atomic_write_json(session/'session.json',payload)
    return regional


@pytest.mark.parametrize('edge',[False,True])
@pytest.mark.parametrize('curve',['ght','invght'])
def test_native_protected_pixels_feather_and_formal_equivalence(native_regional,edge,curve):
    regional=native_regional;scene,path,goals=regional
    if edge:
        values=color.pixels(scene[1]).copy();values[:,56:64,:8]=np.array([.5,.1,.1])[:,None,None]
        rebind_fixture_parent(scene,values)
        doc=contract.load_json(scene[2]);doc['regions'][1]['rect']=[0,0,8,8];contract.atomic_write_json(scene[2],doc)
        goals['regions']=color.record(scene[0],scene[2]);contract.atomic_write_json(path,goals)
    options={'curve':'invght','sp':.5,'hp':.7} if curve=='invght' else {}
    result=solve(regional,**options);assert result['status']=='solved'
    report=contract.load_json(Path(result['report']));candidate=color.pixels(color.bound_file(scene[0],report['trials'][0]['candidate']))
    source=color.pixels(scene[1]);crop=(slice(None),slice(56,64),slice(0,8)) if edge else (slice(None),slice(20,36),slice(36,44))
    np.testing.assert_array_equal(candidate[crop],source[crop])
    trial=Path(result['report']).parent/'trial';mask=color.pixels(next((trial/'artifacts').glob('*-mask.fit')))[0]
    assert mask[crop[1:]].max()==0
    assert np.any((mask[20:36,28:36]>0)&(mask[20:36,28:36]<1))
    receipt,_=execute(scene,result)
    assert receipt['processing_checks']['color_curve']['trial_max_abs_error']<=1e-6
    assert max(receipt['processing_checks']['unmasked_color']['protected_max_abs'])==0


def test_history_freezes_exact_sources_and_finalizes_without_migration(tmp_path,monkeypatch):
    assert sessions._PRE_REGION_COLOR_BINDINGS==PRIOR
    assert_contract_domains(PRIOR['execution_policy'])
    contract.atomic_write_json(tmp_path/'session.json',{'execution_policy':PRIOR['execution_policy']})
    assert stretch.implementation(tmp_path)==PRIOR['implementation']
    assert color.helper_fingerprint(tmp_path)==PRIOR['helpers']['deep_sky_siril_color.py']
    current=contract.load_json(ROOT/'references/command-policy.json')
    current['protocol_commands']['color.finish'].remove('fill')
    current['protocol_commands']['color.finish'].remove('invght')
    current['protocol_commands']['stars.recompose'].remove('rgbcomp')
    current['protocol_commands']['stars.recompose'].remove('update_key')
    current['protocol_commands']['stars.recompose'].remove('modasinh')
    assert current==json.loads(PRIOR['command_policy'])
    replay_and_finalize_without_migration(tmp_path/'old',monkeypatch,ROOT/'tests/fixtures/color-region-goals-prior.json')


def test_multiple_goals_and_observed_goal_revision(regional):
    scene,path,goals=regional
    first=solve(regional)
    revised=copy.deepcopy(goals)
    revised['goals'][1]={'region':'red-filament','intent':'enhance','target_gain':.3}
    fresh=scene[0]/'reports/revised-goals.json';contract.atomic_write_json(fresh,revised)
    with pytest.raises(contract.ContractError):solve(regional,'086-unobserved',region_goals=fresh)
    second=solve(regional,'086-observed',region_goals=fresh,previous=first['report'],observation='The previously excluded red ROI is now an explicit secondary display target')
    report=contract.load_json(Path(second['report']))
    assert [r['target_gain'] for r in report['target_results'] if r['intent']=='enhance']==[.02,.3]
    assert report['target_reached'] is False
    receipt,_=execute(scene,second,'087-revised')
    assert color.record(scene[0],path)['path'] in color.review_paths(scene[0],receipt)


def test_fill_may_only_modify_the_bound_mono_mask(regional):
    from .test_color_evaluation import assets,support
    scene,path,_=regional;session=scene[0]
    result=solve(regional);args=assets(scene,result)
    script=args['script_value'];text=script.read_text().replace('fill 0 36 28 8 16','fill 0 35 28 8 16')
    script.write_text(text);support._write_ssf_provenance(session,script,'color.finish')
    calls=len(scene[-1].calls)
    with pytest.raises(contract.ContractError):core.run_script(session,**args,validate_only=True)
    assert len(scene[-1].calls)==calls


def test_goal_cli_is_explicit_and_exclusive(monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(color,'solve',lambda *a,**k: calls.append(k) or {'status':'skipped'})
    monkeypatch.setattr('sys.argv',['color','--session','/abs/session','--source','artifacts/source.fit',
        '--regions','reports/regions.json','--run-id','088-goals','--region-goals','reports/goals.json'])
    assert color.main()==0 and calls==[{'region_goals':'reports/goals.json'}]
    capsys.readouterr()


def test_uniform_entry_cannot_use_regional_fill(scene):
    from .test_color_evaluation import solve as uniform,assets,support
    result=uniform(scene);args=assets(scene,result)
    script=args['script_value'];text=script.read_text()
    text=text.replace('icc_assign sRGB','icc_assign sRGB\nfill 0 0 0 8 8',1)
    script.write_text(text);support._write_ssf_provenance(scene[0],script,'color.finish')
    before=len(scene[-1].calls)
    with pytest.raises(contract.ContractError,match='Fill requires'):
        core.run_script(scene[0],**args,validate_only=True)
    assert len(scene[-1].calls)==before


def test_regional_color_continues_through_stars_delivery_and_finalize(regional,case,monkeypatch):
    from . import test_recomposition_proposals as recomposition
    from . import test_deep_sky_siril as support
    scene,path,_=regional;session=scene[0]
    values=color.pixels(scene[1]).copy()
    values[:,np.all(np.isclose(values,.03),axis=0)]=.08
    rebind_fixture_parent(scene,values)
    regional[2]['regions']=color.record(session,scene[2]);contract.atomic_write_json(path,regional[2])
    result=solve(regional);run,_=execute(scene,result)
    save_review(session,run,review(session,run))
    monkeypatch.setattr(core.subprocess,'run',case[-1])
    source=session/run['primary_output']
    prepared=recomposition.reduction.prepare(session,source,'089-regional-stars')
    request=copy.deepcopy(case[3]);regions=session/request['regions']['path']
    doc=contract.load_json(regions);doc['reference']['sha256']=contract.sha256_file(source);contract.atomic_write_json(regions,doc)
    request.update(source=prepared['source'],prepared=prepared['prepared'],regions=color.record(session,regions))
    contract.atomic_write_json(case[2],request)
    next_case=(session,source,*case[2:])
    combined=recomposition.execute(next_case,recomposition.generate(next_case))
    assert combined['status']=='success',combined
    contract.atomic_write_json(session/'reviews/090-recompose.json',recomposition.accepted_draft(session,combined))
    monkeypatch.setattr(core,'_decode_scientific_output',lambda p,**k:{'passed':True,'decoder':{'artifact_fingerprint':contract.fingerprint(p,include_path=False)}})
    script,final=support._delivery_script(session,session/combined['primary_output'])
    delivered=core.run_script(session,protocol='delivery.render',script_value=script,source_value=session/combined['primary_output'],expected_values=[final,final.with_suffix('.fit')],timeout=30)
    assert delivered['status']=='success',delivered
    final_review=support._review(session,delivered['id'],final,parent_preview=session/next(r['path'] for r in combined['outputs'] if r['path'].endswith('.jpg')))
    before=path.read_bytes()
    selection=support._selection(session,delivered['id'],final,final_review)
    assert core.finalize_session(session,selection_value=selection,keep_intermediates=True)['status']=='success'
    assert path.read_bytes()==before


def test_small_preserve_roi_does_not_define_luminance_landmarks(regional):
    scene,path,goals=regional
    doc=contract.load_json(scene[2]);doc['regions'][1]['rect']=[36,28,40,32]
    contract.atomic_write_json(scene[2],doc);goals['regions']=color.record(scene[0],scene[2]);contract.atomic_write_json(path,goals)
    first=solve(regional)
    masked=solve(regional,'086-small-protection',mask_mode='luminance',previous=first['report'],observation='The small filament ROI remains protected while luminosity targets use the shell')
    assert masked['status']=='solved'


def test_cyan_without_coverage_in_enhancement_domain_skips_native(regional):
    scene,path,goals=regional
    values=color.pixels(scene[1]).copy();values[:,20:36,28:36]=np.array([.5,.1,.1])[:,None,None]
    rebind_fixture_parent(scene,values);goals['regions']=color.record(scene[0],scene[2]);contract.atomic_write_json(path,goals)
    first=solve(regional)
    calls=len(scene[-1].calls)
    result=solve(regional,'086-no-cyan',selector='cyan',mask_mode='luminance',previous=first['report'],observation='The requested selector has no coverage in the fixed enhancement domain')
    report=contract.load_json(Path(result['report']))
    assert result['status']=='skipped' and not report['trials'] and len(scene[-1].calls)==calls
    assert report['target_results'][0]['selector_pixels']==0

"""Actual ROI controls, immutable target corrections and historical evidence."""
import copy
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_stretch as stretch
import deep_sky_siril_visibility as visibility
from .test_recomposition_sources_v11 import source_case, linear_case, accept
from .test_starnet_linear_bridge import native
from .test_deep_sky_siril_stretch import context
from .test_stretch_routing_v29 import routing_case, failed_profile, goals_for, prepare
from ._policy_pins import replay_and_finalize_without_migration

ROOT=Path(__file__).parents[1]


def test_v2_requires_panel_and_keeps_partial_style_tradeoffs(source_case):
    session,*_=source_case
    run=contract.load_json(session/'runs/070-stretch.json')
    report=contract.load_json(session/run['stretch_visibility']['path'])
    review=contract.load_json(session/'reviews/070-stretch.json')
    assert report['review_contract']==visibility.BOUND_REVIEW_CONTRACT
    for status in ('not_met','uncertain'):
        invalid=copy.deepcopy(review)
        invalid['stretch_assessment']['structures'][0]['reference_visibility']=status
        with pytest.raises(contract.ContractError,match='cannot leave'):
            visibility.validate_assessment(session,run,invalid)
    invalid=copy.deepcopy(review);invalid['stretch_assessment']['structures'][0]['evidence']=[]
    with pytest.raises(contract.ContractError,match='panel'):
        visibility.validate_assessment(session,run,invalid)
    partial=copy.deepcopy(review)
    partial['stretch_assessment']['structures'][0].update(reference_visibility='partial',regression={
        'loss':'The outer synthetic envelope remains less visible than the reference.',
        'core_conflict':'not_demonstrated','core_region':None,'evidence':[report['panel']],
        'tradeoff_reason':'Keep the explicitly requested darker style; this does not claim complete visibility.'})
    visibility.validate_assessment(session,run,partial)
    assert partial['stretch_assessment']['structures'][0]['reference_visibility']=='partial'


@pytest.mark.parametrize('source_case',['chain'],indirect=True)
def test_identical_roi_pixels_cannot_claim_improvement(source_case):
    session,*_=source_case
    run=contract.load_json(session/'runs/070-stretch.json')
    report=contract.load_json(session/run['stretch_visibility']['path'])
    review=contract.load_json(session/'reviews/070-stretch.json')
    control=report['controls'][0]
    control['baseline_view']['science']=copy.deepcopy(control['candidate_view']['science'])
    path=session/'reports/identical-control-assessment.json';contract.atomic_write_json(path,report)
    binding=stretch.record(session,path)
    run['stretch_visibility']=binding;review['stretch_assessment']['visibility']=binding
    row=review['stretch_assessment']['structures'][0]
    row.update(detail_effect='improved',detail_controls=[control['id']])
    with pytest.raises(contract.ContractError,match='Identical scientific pixels'):
        visibility.validate_assessment(session,run,review)
    row.update(detail_effect='unchanged')
    visibility.validate_assessment(session,run,review)


def test_c50_style_threshold_correction_reports_no_new_image_gain(routing_case):
    case=routing_case;root=case[0]
    goals=goals_for(case)
    goals['phases'][-1]['constraints']=[{'region':'faint','statistic':'median','min':.7,'max':.8}]
    first=prepare(case,goals);item=first['candidates']['profile']
    prior=stretch.solve_stretch_parameters(root,item['request'],item['run_id'])
    assert prior['status']=='ineligible'
    before=Path(prior['parameters_path']).read_bytes()
    corrected=goals_for(case)
    corrected['revision']={'parameters':stretch.record(root,Path(prior['parameters_path'])),
        'reason':'Correct a misplaced display threshold, without claiming new pixel benefit.',
        'affected_regions':['faint'],'evidence':[], 'target_change':{
            'kind':'display_correction','reason':'The frozen native response contradicts the intended numeric display interval.',
            'tradeoff':'Accept the corrected numeric goal only after separate real-prefix review.',
            'evidence':[prior['report']['steps'][-1]['local_panel']]}}
    revised=prepare(case,corrected,'072-corrected');item=revised['candidates']['profile']
    solved=stretch.solve_stretch_parameters(root,item['request'],item['run_id'])
    assert solved['status']=='solved'
    request=solved['report']['request'];regions=contract.load_json(root/request['regions']['path'])['regions']
    values=stretch.statistics(stretch.pixels(case[1]),regions)
    report={'regions':request['regions'],'controls':[],'statistics':{'reference':values,'candidate':values}}
    diagnostics=visibility.assessment_diagnostics(root,report,{'adaptive_parameters':{
        'parameters':stretch.record(root,Path(solved['parameters_path']))},'transfer_chain':solved['report']['transfer_chain']})
    assert diagnostics['new_image_benefit_from_revision'] is False
    assert diagnostics['revision']['target_change']==corrected['revision']['target_change']
    assert all(abs(value)<1e-15 for row in diagnostics['regions'].values() for value in row['change_from_previous'].values())
    assert Path(prior['parameters_path']).read_bytes()==before


def test_v28_replay_and_finalize_preserve_original_bytes(tmp_path,monkeypatch):
    replay_and_finalize_without_migration(tmp_path,monkeypatch,ROOT/'tests/fixtures/stretch-routing-prior.json')


def test_native_full_stars_keeps_morphology_and_real_linear_reference(tmp_path,monkeypatch):
    import subprocess
    from . import test_deep_sky_siril as support
    from .test_stretch_v22 import scene
    from .test_deep_sky_siril_stretch import BINARY, write
    if not BINARY.is_file(): pytest.skip('Siril 1.4.4 unavailable')
    real_run=subprocess.run;probe=support._fake_probe
    monkeypatch.setattr(support,'_write_fits',lambda path,**kwargs:write(path,scene(1)[0]))
    monkeypatch.setattr(support,'_fake_probe',lambda *args,**kwargs:probe(BINARY,**kwargs))
    write_jpeg=support._write_jpeg
    monkeypatch.setattr(support,'_write_jpeg',lambda path,color=(18,24,32),**kwargs:write_jpeg(path,color,size=(96,96)))
    session,source,_=support._session(tmp_path,monkeypatch,input_state='linear',width=96,height=96)
    inspected,direct,view=support._input_inspect_run(session,source,monkeypatch)
    support._review(session,inspected['id'],view,parent_preview=direct)
    monkeypatch.setattr(core.subprocess,'run',real_run)
    region_path=session/'reports/full-stars-regions.json'
    regions=[{'id':name,'role':role,'rect':rect} for name,role,rect in [
        ('sky','background',[10,10,26,26]),('faint','faint',[32,32,50,50]),
        ('core','core',[58,58,70,70]),('stars','stars',[76,14,88,26])]]
    contract.atomic_write_json(region_path,{'schema':'starun-siril.regions.v1',
        'reference':{'sha256':contract.sha256_file(source),'width':96,'height':96},'regions':regions})
    constraints=[dict(region=r,statistic=s,min=0.,max=1.) for r,s in [
        ('sky','median'),('sky','mad'),('sky','hf_sigma2'),('faint','median'),
        ('core','p999'),('core','span'),('stars','p999'),('stars','span')]]
    request={'source':{'path':'@input','sha256':contract.sha256_file(source)},'regions':stretch.record(session,region_path),
        'targets':{'faint':'bright'},'operators':[{'constraints':constraints}],
        'manual_lookup':{'status':'not_needed','reason':'Explicit known synthetic native operators','evidence':[]}}
    case=(session,source,None,None,request,regions)
    prepared=prepare(case,goals_for(case,'dust_reflection'))
    item=prepared['candidates']['profile'];result=stretch.solve_stretch_parameters(session,item['request'],item['run_id'],60)
    assert result['status']=='solved',result['report'].get('reason')
    assets=stretch.generate_stretch_assets(session,parameters=result['parameters_path'])
    run=core.run_script(session,protocol='stretch',script_value=assets['script_path'],source_value=source,
        expected_values=[assets['primary_output'],assets['preview_output']],primary_output_value=assets['primary_output'],timeout=60)
    assert run['status']=='success',run
    visibility_report=contract.load_json(session/run['stretch_visibility']['path'])
    assert [s['command'] for s in run['transfer_chain']]==['asinh','ght']
    assert visibility_report['origin']['kind']=='accepted_linear_reference'
    assert visibility_report['origin']['replay_source']['path']=='@input'
    accept(session,run['id'])

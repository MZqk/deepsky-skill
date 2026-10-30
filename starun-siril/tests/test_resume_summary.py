"""Read-only recovery positions, explicit intent and geometry-bound ROI reuse."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_resume as resume
import deep_sky_siril_session as sessions
from deep_sky_siril_metrics import measure_regions
from . import test_deep_sky_siril as support
from .test_deep_sky_siril_stretch import write, context
from .test_stretch_routing_v29 import routing_case, prepare
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration


@pytest.fixture
def inspected(tmp_path,monkeypatch):
    session,source,_=support._session(tmp_path,monkeypatch,input_state='linear',channel_mode='broadband')
    run,direct,preview=support._input_inspect_run(session,source,monkeypatch)
    review=support._review(session,run['id'],preview,parent_preview=direct)
    return session,source,run,review


def record_decision(case,action='continue',next_stage=3):
    session,_,run,review_path=case
    review=contract.load_json(review_path)
    template=resume.decision_summary(session,run['id'],review,review_path)['template']
    template.update(action=action,next_stage=next_stage,reason='The inspected input is suitable; assess the measured background next.' if action=='continue' else 'Stop because the requested trustworthy sky evidence is unavailable.')
    path=session/'reports'/run['id']/'decision.json';path.parent.mkdir(exist_ok=True)
    contract.atomic_write_json(path,template)
    return path,template


def test_status_is_read_only_and_does_not_invent_continuation(inspected,monkeypatch,capsys):
    session,source,run,review=inspected
    before=support._session_bytes(session)
    def forbidden(*args,**kwargs):raise AssertionError('A status query cannot launch Siril')
    monkeypatch.setattr(core.subprocess,'run',forbidden)
    result=resume.session_status(session)
    assert result['last_accepted_parent']['path']==str(source)
    assert result['last_accepted_parent']['run_id']==run['id']
    assert result['next_stage']==2 and result['next_stage_basis']=='default_stage_evaluation_hint'
    assert result['stretch_attempts']=={'limit':3,'used':0,'remaining':3}
    assert result['continuation']['status']=='not_recorded'
    assert 'continuation_not_recorded' in {row['code'] for row in result['blocking_reasons']}
    assert support.cli.main(['status','--session',str(session)])==0
    assert json.loads(capsys.readouterr().out)['schema']==resume.SCHEMA
    assert support._session_bytes(session)==before


@pytest.mark.parametrize('action,next_stage',[('continue',3),('stop',None)])
def test_bound_decision_supplies_intent_or_specific_stop_reason(inspected,action,next_stage):
    session,_,run,review=inspected
    _,decision=record_decision(inspected,action,next_stage)
    before=support._session_bytes(session)
    result=resume.session_status(session)
    assert result['next_stage']==next_stage
    assert result['continuation']['action']==action and result['continuation']['reason']==decision['reason']
    assert ('agent_stopped' in {row['code'] for row in result['blocking_reasons']})==(action=='stop')
    assert core.check_review(session,run['id'],str(review))['continuation']['status']=='recorded'
    assert support._session_bytes(session)==before


def test_later_checkpoint_is_appended_without_rewriting_reviewed_evidence(inspected):
    session,_,run,review=inspected
    original,_=record_decision(inspected)
    previous=original.read_bytes();reviewed=review.read_bytes()
    continuation=resume.session_status(session)['continuation']
    path=Path(continuation['next_path']);value=continuation['next_template']
    value.update(stage=7,action='stop',next_stage=None,reason='The existing stretch controls need visual review before any further processing.')
    contract.atomic_write_json(path,value)
    result=resume.session_status(session)
    assert result['continuation']['stage']==7 and result['next_stage'] is None
    assert result['continuation']['path']==str(path)
    assert result['continuation']['next_path'].endswith('/decision-0002.json')
    assert original.read_bytes()==previous and review.read_bytes()==reviewed
    assert core.check_review(session,run['id'],str(review))['continuation']['stage']==7


@pytest.mark.parametrize('fault',['predecessor','gap','missing-original'])
def test_decision_revision_chain_rejects_drift_or_gaps(inspected,fault):
    session,_,_,_=inspected
    original,value=record_decision(inspected)
    continuation=resume.session_status(session)['continuation']
    path=Path(continuation['next_path']);revision=continuation['next_template']
    revision.update(stage=3,action='continue',next_stage=4,reason='The background assessment is complete; assess color calibration next.')
    contract.atomic_write_json(path,revision)
    if fault=='predecessor':
        value['reason']='Changed prior intent.';contract.atomic_write_json(original,value)
    elif fault=='gap':path.rename(path.with_name('decision-0002.json'))
    else:original.rename(original.with_suffix('.pending'))
    with pytest.raises(contract.ContractError,match='decision'):
        resume.session_status(session)


@pytest.mark.parametrize('fault',['review','receipt','reason','stage','next','unknown','action','action-object','stop-next'])
def test_stale_or_incomplete_decisions_are_rejected(inspected,fault):
    session,_,_,_=inspected
    path,value=record_decision(inspected)
    if fault in {'review','receipt'}:value['review' if fault=='review' else 'run_receipt']['sha256']='0'*64
    elif fault=='reason':value['reason']=' '
    elif fault=='stage':value['stage']=0
    elif fault=='next':value['next_stage']=True
    elif fault=='unknown':value['automatic_accept']=True
    elif fault=='action':value['action']='accept'
    elif fault=='action-object':value['action']=[]
    else:value['action']='stop'
    contract.atomic_write_json(path,value)
    with pytest.raises(contract.ContractError,match='decision'):
        resume.session_status(session)


def test_exhausted_trial_budget_does_not_prevent_reviewing_existing_evidence(inspected):
    session,source,_,_=inspected
    record_decision(inspected)
    for i in range(3):
        path=session/'reports'/f'07{i}-trial'/'scientific-attempt.json';path.parent.mkdir()
        contract.atomic_write_json(path,{'source_sha256':contract.sha256_file(source)})
    result=resume.session_status(session)
    assert result['stretch_attempts']['remaining']==0
    assert result['last_accepted_parent']['path']==str(source)
    assert not any(r['code']=='agent_stopped' for r in result['blocking_reasons'])


def test_missing_review_does_not_fabricate_an_accepted_parent(inspected):
    session,source,_,review=inspected
    review.rename(review.with_suffix('.pending'))
    result=resume.session_status(session)
    assert result['last_accepted_parent'] is None and result['next_stage']==1
    assert {'review_required','accepted_parent_missing'} <= {r['code'] for r in result['blocking_reasons']}


@pytest.mark.parametrize('fault',['run','verdict','gate'])
def test_malformed_review_values_return_contract_errors(inspected,fault):
    session,_,_,path=inspected;value=contract.load_json(path)
    if fault=='run':value['run_id']=[]
    elif fault=='verdict':value['verdict']={}
    else:value['gates']['stars']=[]
    contract.atomic_write_json(path,value)
    with pytest.raises(contract.ContractError):
        resume.session_status(session)


def test_changed_runtime_remains_a_blocker_without_erasing_recorded_acceptance(inspected):
    session,source,_,_=inspected
    probe=contract.load_json(session/'reports/tool-probe.json')
    binary=Path(probe['tools']['siril_cli']['fingerprint']['path']);binary.write_bytes(binary.read_bytes()+b'changed')
    result=resume.session_status(session)
    assert result['last_accepted_parent']['path']==str(source)
    assert 'siril_runtime_unavailable' in {r['code'] for r in result['blocking_reasons']}
    assert result['execution_validation_required']


def test_failed_run_preserves_parent_and_exposes_its_recorded_cause(inspected):
    session,source,_,_=inspected
    record_decision(inspected)
    run_id='030-failed';path=session/'runs'/(run_id+'.json')
    failed={'schema':'starun-siril.run-receipt.v1','id':run_id,'status':'failed','protocol':'background.subtract',
            'timed_out':True,'timeout_seconds':30}
    contract.atomic_write_json(path,failed)
    manifest=contract.load_json(session/'manifest.json')
    manifest['runs'].append({'id':run_id,'status':'failed','receipt':path.name,'receipt_sha256':contract.sha256_file(path)})
    contract.atomic_write_json(session/'manifest.json',manifest)
    result=resume.session_status(session)
    assert result['last_accepted_parent']['path']==str(source) and result['next_stage']==3
    issue=next(r for r in result['blocking_reasons'] if r['code']=='run_failed')
    assert issue['receipt']['path']=='runs/030-failed.json' and '30' in issue['details'][0]


def test_unknown_context_stops_at_input_confirmation(tmp_path,monkeypatch):
    session,source,_=support._session(tmp_path,monkeypatch,input_state='unknown',channel_mode='unknown')
    run,direct,preview=support._input_inspect_run(session,source,monkeypatch)
    support._review(session,run['id'],preview,parent_preview=direct)
    result=resume.session_status(session)
    assert result['next_stage']==1 and result['next_stage_basis']=='input_prerequisite'
    assert 'input_context_unknown' in {r['code'] for r in result['blocking_reasons']}


def test_finalized_summary_uses_committed_result_without_restarting(inspected,monkeypatch):
    session,source,_,_=inspected
    payload=contract.load_json(session/'session.json');manifest=contract.load_json(session/'manifest.json')
    manifest['finalization']={'selection_sha256':'a'*64}
    monkeypatch.setattr(resume,'load_session',lambda _: (session,payload,manifest))
    def committed(root,index,selection):
        assert root==session and selection=='a'*64
        return {'final':{'path':'outputs/final.fit'}},{},'preserve',True
    monkeypatch.setattr(core,'_load_committed_finalization',committed)
    before=support._session_bytes(session)
    result=resume.session_status(session)
    assert result['status']=='finalized' and result['next_stage'] is None and not result['blocking_reasons']
    assert support._session_bytes(session)==before
    with pytest.raises(contract.ContractError,match='active accepted parent'):
        resume.session_status(session,regions='reports/regions.json')


@pytest.fixture
def roi_case(tmp_path):
    session=tmp_path/'session'
    for name in ('artifacts','reports','reviews'): (session/name).mkdir(parents=True)
    source=write(tmp_path/'input.fit',np.ones((3,24,24),dtype='float32')*.1)
    target=write(session/'artifacts/041-color.fit',np.ones((3,24,24),dtype='float32')*.2)
    path=session/'reports/regions.json'
    doc={'schema':'starun-siril.regions.v1','reference':{'sha256':contract.sha256_file(source),'width':24,'height':24},
         'regions':[{'id':'body','role':'faint','rect':[4,4,20,20]}]}
    contract.atomic_write_json(path,doc)
    run={'id':'041-color','protocol':'color.calibrate','source_path':'@input','primary_output':'artifacts/041-color.fit'}
    reviews={run['id']:({'verdict':'accept','inspected_materials':[{'path':'reports/regions.json','sha256':contract.sha256_file(path)}]},None)}
    parent={'path':str(target),'relative_path':run['primary_output'],'sha256':contract.sha256_file(target)}
    return session,source,target,path,doc,run,reviews,parent


def test_roi_rebinding_is_a_read_only_proposal_and_remeasures_current_pixels(roi_case,tmp_path):
    session,source,target,path,original,run,reviews,parent=roi_case
    before={str(p):p.read_bytes() for p in session.rglob('*') if p.is_file()}
    result=resume._region_proposal(session,{'input':{'path':str(source)}},{run['id']:run},reviews,parent,path)
    proposed=result['proposed_contract']
    assert proposed['regions']==original['regions'] and proposed['reference']['sha256']==contract.sha256_file(target)
    assert original['reference']['sha256']!=proposed['reference']['sha256']
    assert result['requires_measurement'] and result['requires_visual_review'] and not result['scientific_parent']
    assert before=={str(p):p.read_bytes() for p in session.rglob('*') if p.is_file()}
    rebound=tmp_path/'new-regions.json';contract.atomic_write_json(rebound,proposed)
    measured=measure_regions(target,None,rebound,scientific=True)
    assert measured['status']=='measured'


def test_reviewed_input_coordinates_can_be_reused_without_a_processing_run(roi_case):
    session,source,_,path,original,_,_,_=roi_case
    run={'id':'010-inspect','protocol':'input.inspect','source_path':'@input','primary_output':None}
    reviews={run['id']:({'verdict':'accept','inspected_materials':[{'path':'reports/regions.json','sha256':contract.sha256_file(path)}]},None)}
    parent={'path':str(source),'relative_path':'@input','sha256':contract.sha256_file(source)}
    result=resume._region_proposal(session,{'input':{'path':str(source)}},{run['id']:run},reviews,parent,path)
    assert result['proposed_contract']==original and result['verified_lineage']==[]
    assert resume.stage_hint({'protocol':'color.map'})==(8,8)


@pytest.mark.parametrize('fault',['unrelated','crop','unaccepted','unreviewed','geometry','coordinates','role'])
def test_roi_reuse_rejects_missing_lineage_or_geometry_proof(roi_case,fault):
    session,source,target,path,doc,run,reviews,parent=roi_case
    if fault=='unrelated':doc['reference']['sha256']='0'*64
    elif fault=='crop':run['protocol']='geometry.crop-near-black'
    elif fault=='unaccepted':reviews[run['id']][0]['verdict']='reject'
    elif fault=='unreviewed':reviews[run['id']][0]['inspected_materials']=[]
    elif fault=='geometry':write(target,np.ones((3,16,16),dtype='float32'))
    elif fault=='role':doc['regions'][0]['role']=[]
    else:doc['regions'][0]['rect']=[0,0,100,100]
    contract.atomic_write_json(path,doc)
    if fault in {'coordinates','role'}:reviews[run['id']][0]['inspected_materials'][0]['sha256']=contract.sha256_file(path)
    with pytest.raises(contract.ContractError):
        resume._region_proposal(session,{'input':{'path':str(source)}},{run['id']:run},reviews,parent,path)


def test_prefix_summary_uses_existing_verified_scientific_evidence(routing_case,monkeypatch):
    session,source,payload,*_=routing_case
    prepared=prepare(routing_case);item=prepared['candidates']['profile']
    import deep_sky_siril_stretch as stretch
    result=stretch.solve_stretch_parameters(session,item['request'],item['run_id'])
    assert result['status']=='solved'
    monkeypatch.setattr(resume,'load_session',lambda _: (session,payload,{}))
    monkeypatch.setattr(resume,'_manifest_receipts',lambda *args: [])
    before=support._session_bytes(session)
    summary=resume.session_status(session,parameters=result['parameters_path'])
    assert summary['prefix_reuse']['requires_exact_prefix_match']
    assert len(summary['prefix_reuse']['prefixes'])==len(result['report']['steps'])
    assert not summary['prefix_reuse']['scientific_parent']
    assert support._session_bytes(session)==before


def test_outgoing_policy_snapshot_and_read_only_history(tmp_path,monkeypatch):
    root=Path(__file__).parents[1];fixture=root/'tests/fixtures/resume-summary-prior.json'
    prior=contract.load_json(fixture)
    assert fixture.read_bytes()==(root/'references/resume-summary-history.json').read_bytes()
    assert sessions._PRE_RESUME_BINDINGS==prior
    assert_contract_domains(prior['execution_policy'])
    replay_and_finalize_without_migration(tmp_path,monkeypatch,fixture)

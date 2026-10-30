"""Native same-prefix RGB tail comparison and strict source/ROI constraints."""
from ._stretch_factory import solve_bound, bind_exception

import copy
import json
from pathlib import Path
import pytest

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_stretch_profiles as profiles
import deep_sky_siril_session as sessions
import deep_sky_siril_visibility as visibility
from deep_sky_siril_contract import ContractError
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration
from .test_deep_sky_siril_stretch import context
from .test_starless_stretch_v24 import native_scene


def make_tails(context,monkeypatch,failed=None,*,shared_failure=False):
    root,source,payload,path,request,regions,_,_=native_scene(context)
    monkeypatch.setattr(sessions,'effective_session_payload',lambda session,value,*args:value)
    request['schema']=protected.PROPOSAL_REQUEST
    prefix=copy.deepcopy(request['operators'][0]);prefix.update(factors={'midtones':.4})
    mtf=copy.deepcopy(prefix);mtf['factors']={'midtones':.4};mtf['purpose']='Inspect a mild RGB MTF tail'
    protection=copy.deepcopy(prefix['constraints'])
    if failed:
        for row in protection:
            if row['statistic']=='median' and row['region'] in {'sky','faint'}:
                row['min']=0.
        mtf['factors']['midtones']=.1 if failed=='ghs' else .95
        next(row for row in mtf['constraints'] if row['region']=='faint')['min']=.3 if failed in {'ghs','both'} else .08
        if shared_failure:
            next(row for row in protection if row['region']=='faint')['min']=.04
    request['operators']=[prefix,mtf];path.write_text(json.dumps(request))
    tail={'purpose':'Preserve color with a same-prefix human-luminance GHS tail',
        'factors':{'D':.4,'B':1.},'landmarks':{k:{'region':r,'statistic':v} for k,r,v in
            [('LP','sky','p01'),('SP','faint','median'),('HP','core','p99')]}}
    item=bind_exception(root,request,'071-mtf',tail=tail,protection=protection if failed else None)
    left=base.solve_stretch_parameters(root,item['request'],item['run_id'],timeout=60)
    assert left['status']==('ineligible' if failed in {'mtf','both'} else 'solved'),left['report'].get('reason')
    if shared_failure:
        return root,source,payload,left,None
    plan,_=profiles.load_plan(root,payload,left['report']['request']['strategy']['plan'])
    right=base.solve_stretch_parameters(root,root/'reports'/plan['run_id']/'tail-request.json',
        profiles.candidate_run_id(plan['run_id'],'tail'),timeout=60)
    assert right['status']==('ineligible' if failed in {'ghs','both'} else 'solved'),right['report'].get('reason')
    return root,source,payload,left,right


@pytest.fixture
def tails(context,monkeypatch):
    return make_tails(context,monkeypatch)


@pytest.fixture(params=['ghs','mtf'])
def negative_tails(context,monkeypatch,request):
    return make_tails(context,monkeypatch,request.param)


def test_native_tail_evidence_and_generation(tails):
    root,source,payload,left,right=tails
    with pytest.raises(ContractError,match='compare-tail'):
        base.generate_stretch_assets(root,parameters=left['parameters_path'])
    result=profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-tail')
    binding=base.record(root,Path(result['comparison']))
    report=profiles.verify_comparison(root,payload,binding,base.record(root,Path(right['parameters_path'])))
    assert report['schema']==profiles.TAIL_COMPARISON_V2
    assert report['recommended_run_id'] is None
    assert report['display']=='identical_linear_0_1'
    assert [r['variant'] for r in report['candidates']]==['mtf','luminance_ghs']
    assert len(report['candidates'][0]['regions'])==4
    for row in report['candidates'][0]['regions']:
        assert 'endpoints' in row and 'luminance_quantiles' in row
    for candidate in (left,right):
        assets=base.generate_stretch_assets(root,parameters=candidate['parameters_path'],comparison=result['comparison'])
        script=Path(assets['script_path']).read_text()
        assert str(source) in script
        assert '# stretch-comparison:' in script
        bound=base.adaptive_binding(root,payload,source,script,candidate['report']['transfer_chain'],candidate['report']['run_id'])
        assert bound['comparisons']==[binding]


@pytest.mark.parametrize('change',['prefix','regions','source','constraints'])
def test_tail_rejects_different_prefix_roi_source_and_final_constraints(tails,monkeypatch,change):
    root,_,payload,left,right=tails
    original=base.load_parameters
    def load(session,policy,path,*args,**kwargs):
        report,bound,source=original(session,policy,path,*args,**kwargs)
        if str(path)==str(right['parameters_path']):
            report=copy.deepcopy(report)
            if change=='prefix':report['transfer_chain'][0]['parameters'][1]='.39'
            elif change=='constraints':report['request']['operators'][-1]['constraints'][0]['max']=.51
            else:report['bindings'][change]['sha256']='0'*64
        return report,bound,source
    monkeypatch.setattr(base,'load_parameters',load)
    with pytest.raises(ContractError,match='share the linear parent'):
        profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-invalid')
    assert not (root/'reports/073-invalid').exists()


def test_failed_tail_is_diagnostic_and_never_formal(negative_tails,monkeypatch):
    root,source,payload,left,right=negative_tails
    failed=next(row for row in (left,right) if row['status']=='ineligible')
    solved=next(row for row in (left,right) if row['status']=='solved')
    with pytest.raises(ContractError,match='Only solved'):
        base.load_parameters(root,payload,failed['parameters_path'])
    attempts={p:p.read_bytes() for p in root.glob('reports/*/scientific-attempt.json')}
    def unexpected_native(*args,**kwargs):
        raise AssertionError('Comparison must not launch Siril')
    monkeypatch.setattr(base.NativeTrials,'run',unexpected_native)
    result=profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-negative')
    binding=base.record(root,Path(result['comparison']));selected=base.record(root,Path(solved['parameters_path']))
    report=profiles.verify_comparison(root,payload,binding,selected)
    negative=next(row for row in report['candidates'] if row['status']=='ineligible')
    assert 'selected' not in negative and negative['requires_failure_review'] is True
    assert negative['audit']['reasons']==['regional_constraints'] and negative['protection_audit']['eligible']
    assert negative['diagnostic']['.fit']==failed['report']['steps'][-1]['trials'][0]['retained']['candidate.fit']
    assert report['recommended_run_id'] is None
    assert attempts=={p:p.read_bytes() for p in root.glob('reports/*/scientific-attempt.json')}
    materials=profiles.review_paths(root,binding,selected)
    assert negative['diagnostic']['.jpg']['path'] in materials
    with pytest.raises(ContractError,match='solved compared'):
        profiles.verify_comparison(root,payload,binding,negative['parameters'])
    with pytest.raises(ContractError,match='Only solved'):
        base.generate_stretch_assets(root,parameters=failed['parameters_path'],comparison=result['comparison'])
    assets=base.generate_stretch_assets(root,parameters=solved['parameters_path'],comparison=result['comparison'])
    script=Path(assets['script_path']).read_text()
    adaptive=base.adaptive_binding(root,payload,source,script,solved['report']['transfer_chain'],solved['report']['run_id'])
    processing={'transfer_chain':solved['report']['transfer_chain'],'adaptive_parameters':adaptive}
    primary=base.bound_file(root,solved['report']['steps'][-1]['selected']['.fit'])
    controls=visibility._control_specs(root,source,primary,processing)
    control=next(row for row in controls if row['id']=='tail')
    assert control['kind']=='tail_goal_not_met' and control['input']==negative['diagnostic']['.fit']
    with pytest.raises(ContractError,match='exists'):
        profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-negative')
    report['schema']=profiles.TAIL_COMPARISON
    with pytest.raises(ContractError,match='Only solved'):
        profiles.verify_tail_comparison(root,payload,report,selected)


def test_failed_tail_shared_protection_is_rejected(context,monkeypatch):
    root,_,payload,left,right=make_tails(context,monkeypatch,'mtf',shared_failure=True)
    with pytest.raises(ContractError,match='shared scientific protection'):
        profiles.compare_tail(root,left['parameters_path'],left['parameters_path'],'073-unsafe')
    assert not (root/'reports/073-unsafe').exists()


def test_two_failed_tails_are_not_an_authorizing_comparison(context,monkeypatch):
    root,_,_,left,right=make_tails(context,monkeypatch,'both')
    with pytest.raises(ContractError,match='at least one solved'):
        profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-no-candidate')
    assert not (root/'reports/073-no-candidate').exists()


@pytest.mark.parametrize('fault',['failed','timed_out','prefix','reasons','shadow_limit','nonfinite_native_trial',
                                'source','regions','runtime','implementation','factors','log','image','geometry','nonfinite','archive'])
def test_failed_control_evidence_is_revalidated(context,monkeypatch,fault):
    root,_,_,left,right=make_tails(context,monkeypatch,'ghs')
    report=right['report'];path=Path(right['parameters_path']);trial=report['steps'][-1]['trials'][0]
    if fault in {'failed','timed_out'}:report['status']=fault
    elif fault=='prefix':report['steps'].pop(0)
    elif fault in {'reasons','shadow_limit','nonfinite_native_trial'}:
        trial['audit']['reasons'].append('new_highlight_crossing' if fault=='reasons' else fault)
    elif fault in {'source','regions','runtime'}:report['bindings'][fault]['sha256']='0'*64
    elif fault=='implementation':report['bindings']['implementation']['deep_sky_siril_stretch.py']['sha256']='0'*64
    elif fault=='factors':report['request']['operators'][-1]['factors']['D']=.9
    elif fault in {'geometry','nonfinite'}:
        import numpy as np
        from .test_deep_sky_siril_stretch import write
        target=root/trial['retained']['candidate.fit']['path']
        values=np.zeros((3,8,8)) if fault=='geometry' else np.full(base.pixels(target).shape,np.nan)
        write(target,values)
    else:
        binding=(trial['evidence']['log'] if fault=='log' else trial['retained']['candidate.fit'] if fault=='image'
                 else report['trial_artifacts'])
        target=root/binding['path'];target.write_bytes(target.read_bytes()+b'drift')
    path.write_text(json.dumps(report))
    with pytest.raises(ContractError):
        profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-tampered')
    assert not (root/'reports/073-tampered').exists()


def test_v1_replay_and_diagnostics_without_science_permission(tails,monkeypatch):
    root,_,payload,left,right=tails
    result=profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'073-current')
    report=base.load_json(Path(result['comparison']));report['schema']=profiles.TAIL_COMPARISON
    profiles.verify_tail_comparison(root,payload,report,base.record(root,Path(left['parameters_path'])))
    monkeypatch.setattr(sessions,'execution_policy_is_current',lambda _:False)
    recovered=profiles.compare_tail(root,left['parameters_path'],right['parameters_path'],'074-diagnostic-only')
    assert recovered['status']=='comparison_ready'
    with pytest.raises(ContractError,match='Historical sessions'):
        base.generate_stretch_assets(root,parameters=left['parameters_path'],comparison=recovered['comparison'])


def test_previous_tail_policy_replay_and_exact_snapshot(tmp_path,monkeypatch):
    root=Path(__file__).parents[1]
    fixture=root/'tests/fixtures/stretch-tail-comparison-prior.json'
    prior=base.load_json(fixture)
    assert fixture.read_bytes()==(root/'references/stretch-tail-comparison-history.json').read_bytes()
    assert sessions._PRE_TAIL_COMPARISON_BINDINGS==prior
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy':prior['execution_policy']}))
    assert protected.implementation(tmp_path)==prior['implementation']
    for path,record in prior['documents'].items():
        assert sessions.registered_guidance_reference(prior,{'path':path,**record})
    replay=tmp_path/'replay';replay.mkdir()
    replay_and_finalize_without_migration(replay,monkeypatch,fixture)

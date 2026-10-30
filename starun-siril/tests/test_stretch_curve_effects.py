"""Actual-pixel MTF preflight, diagnostic GHS changes and evidence-bound short routes."""
import copy
from pathlib import Path
import time

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_preflight as checks
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_v2 as protected
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration
from ._stretch_factory import with_constraint_review
from .test_deep_sky_siril import _fake_probe
from .test_deep_sky_siril_stretch import context, write, BINARY
from .test_stretch_constraint_preflight import inspect
from .test_stretch_routing_v29 import routing_case, goals_for, prepare

ROOT = Path(__file__).parents[1]


def short_goals(case, methods=('mtf','ght')):
    goals = goals_for(case,'emission_nebula')
    chain = routes.bound_requests_for({'mode':'profile','source':{},'regions':{}},goals,{})['profile']['operators']
    goals.pop('phases')
    goals['operators'] = [copy.deepcopy(next(step for step in chain if step['method']==method)) for method in methods]
    for step in goals['operators']:
        if step['method']=='mtf':step['factors']['midtones']=.1
    goals['reason']='The actual parent and fixed ROIs support this shorter explicit route; review measured structure before adoption.'
    return goals


def test_mtf_projection_maps_channels_before_luminance():
    rgb=np.broadcast_to(np.array([.03,.12,.6])[:,None,None],(3,8,8)).copy()
    mapped=checks.mtf_pixels(rgb,.01,.2,1.)
    weights=np.array([.2126,.7152,.0722])
    actual=np.einsum('c,cyx->yx',weights,mapped)
    scalar=checks.mtf_pixels(np.einsum('c,cyx->yx',weights,rgb),.01,.2,1.)
    assert not np.allclose(actual,scalar,rtol=0,atol=1e-3)
    x=(rgb[:,0,0]-.01)/.99
    expected=(1-.2)*x/((1-.2)*x+.2*(1-x))
    assert np.allclose(mapped[:,0,0],expected,rtol=0,atol=np.finfo(np.float32).eps)
    endpoints=checks.mtf_pixels(np.array([-.1,.01,1.,1.1]),.01,.2,1.)
    assert np.array_equal(endpoints,[0.,0.,1.,1.])


@pytest.mark.parametrize('channels',[1,3])
def test_mtf_projection_matches_synthetic_native_pixels(context,channels):
    if not BINARY.exists():pytest.skip('Native Siril is unavailable')
    session,_,_,*_=context
    values=np.linspace(0.,1.,256,dtype=np.float32).reshape(16,16)
    data=np.stack([np.roll(values,offset,axis=1) for offset in range(channels)])
    source=write(session/'artifacts/mtf-curve.fit',data[0] if channels==1 else data)
    directory=session/'reports/mtf-curve';directory.mkdir()
    oracle=base.NativeTrials(session,_fake_probe(BINARY)['tools']['siril_cli'],directory,directory,time.monotonic()+60)
    output,_=oracle.run(source,[{'command':'mtf','parameters':['.01','.0016','1']}])
    predicted=checks.mtf_pixels(data.astype(float),.01,.0016,1.)
    assert np.max(np.abs(predicted-base.pixels(output))) < checks.MAPPING_TOLERANCE


def test_known_mtf_mismatch_is_rejected_before_freeze_or_trial(routing_case,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Read-only preflight cannot launch native processing')
    monkeypatch.setattr(base.subprocess,'run',forbidden)
    goals=short_goals(routing_case);goals['operators'][0]['factors']['midtones']=.5
    result,path=inspect(routing_case,with_constraint_review(goals,'exception'),mode='exception')
    assert result['schema']==checks.SCHEMA and result['status']=='needs_revision'
    assert result['output_reachability']=='partially_evaluated'
    assert any(row['code']=='mtf_mapping_outside_target' and row['region']=='sky' for row in result['issues'])
    curve=result['curve_effects'][0]
    assert curve['input']['kind']=='actual_linear_parent' and curve['status']=='mapped'
    assert float(curve['parameters']['parameters'][0])==base.safe_blackpoint([base.pixels(routing_case[1])])
    with pytest.raises(contract.ContractError,match='mtf_mapping_outside_target'):
        routes.init_requests(routing_case[0],'@input',routing_case[4]['regions']['path'],path,'071-invalid',mode='exception', stepwise=False)
    assert not (routing_case[0]/'reports/071-invalid').exists()
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


@pytest.mark.parametrize('multiple,expected',[(.5,'requires_native_verification'),(2.,'outside_target')])
@pytest.mark.parametrize('statistic',['median','p01','p99','p999','max_rgb','span'])
def test_mtf_roundoff_band_does_not_relax_native_constraints(routing_case,multiple,expected,statistic):
    session,source,payload,_,request,regions=routing_case
    data=base.pixels(source);step=short_goals(routing_case)['operators'][0]
    original=copy.deepcopy(step)
    reference={'kind':'actual_linear_parent','reference':request['source'],'statistics':protected.statistics(data,regions)}
    plan={'full_source':None}
    current={'operators':[step],'strategy':{'variant':'exception'}}
    measured=checks.curve_effect(session,payload,plan,current,1,reference,{},regions,data,{})
    value=next(row['mapped_value'] for row in measured['constraints'] if row['region']=='faint')
    if statistic!='median':
        black=base.safe_blackpoint([data])
        value=protected.statistics(checks.mtf_pixels(data,black,.1,1.),regions)['faint'][statistic]
    tolerance=checks.MAPPING_TOLERANCE*(2 if statistic=='span' else 1)
    step['constraints']=[{'region':'faint','statistic':statistic,'min':value+multiple*tolerance,'max':1.}]
    effect=checks.curve_effect(session,payload,plan,current,1,reference,{},regions,data,{})
    row=effect['constraints'][0]
    assert row['status']==expected and row['tolerance']==tolerance
    assert not base.check_constraints({'faint':{statistic:value}},step['constraints'])[0]['satisfied']
    assert original['factors']==step['factors']


@pytest.mark.parametrize('methods',[('mtf',),('ght',),('ght','ght')])
def test_initial_short_route_is_bound_without_a_previous_failure(routing_case,methods):
    if len(methods) == 1:
        with pytest.raises(contract.ContractError, match='at least two'):
            prepare(routing_case,short_goals(routing_case,methods),mode='exception')
        assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))
        return
    prepared=prepare(routing_case,short_goals(routing_case,methods),mode='exception')
    item=prepared['candidates']['exception']
    request,*_=base.read_request(routing_case[0],routing_case[2],item['request'])
    assert [step['method'] for step in request['operators']]==list(methods)
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))
    request['operators'][0]['purpose']='Unbound edit after freezing'
    contract.atomic_write_json(Path(item['request']),request)
    with pytest.raises(contract.ContractError,match='frozen plan'):
        base.read_request(routing_case[0],routing_case[2],item['request'])


def test_short_rgb_mtf_tail_still_requires_its_comparison(routing_case,monkeypatch):
    real_pixels=base.pixels
    monkeypatch.setattr(base,'pixels',lambda path:np.repeat(real_pixels(path),3,axis=0))
    with pytest.raises(contract.ContractError,match='same-prefix GHS tail'):
        prepare(routing_case,short_goals(routing_case,('ght','mtf')),mode='exception')


def test_three_step_initial_exception_is_not_an_unbound_custom_bypass(routing_case):
    with pytest.raises(contract.ContractError,match='one/two-step'):
        prepare(routing_case,short_goals(routing_case,('ght','ght','mtf')),mode='exception')


@pytest.mark.parametrize('scale',[1.,1.0001,1.2])
def test_ghs_changes_are_diagnostic_for_zero_small_and_large_effects(routing_case,scale):
    _,source,_,_,request,regions=routing_case
    data=base.pixels(source)
    before=protected.statistics(data,regions);after=protected.statistics(data*scale,regions)
    result=checks.ghs_effects(before,after,regions,request['targets'],request['source'],{'path':'diagnostic.fit','sha256':'0'*64})
    assert result['diagnostic_only'] and result['requires_visual_review']
    assert 'eligible' not in result and 'recommended_run_id' not in result
    assert result['regions']['faint']['subject_contrast']['relative_delta']==pytest.approx(scale-1)
    assert result['regions']['faint']['span']['relative_delta']==pytest.approx(scale-1)
    before['faint']['span']=0.
    assert checks.ghs_effects(before,after,regions,request['targets'],{},{} )['regions']['faint']['span']['relative_delta'] is None
    before['faint']['span']=float(np.nextafter(0.,1.))
    assert checks.ghs_effects(before,after,regions,request['targets'],{},{} )['regions']['faint']['span']['relative_delta'] is None


@pytest.mark.parametrize('failure',[False,True])
def test_native_ghs_diagnostics_do_not_add_a_stop_gate_and_are_reverified(routing_case,failure):
    goals=goals_for(routing_case)
    if failure:goals['phases'][-1]['constraints']=[{'region':'faint','statistic':'median','min':.7,'max':.8}]
    prepared=prepare(routing_case,goals);item=prepared['candidates']['profile']
    result=base.solve_stretch_parameters(routing_case[0],item['request'],item['run_id'])
    assert result['status']==('ineligible' if failure else 'solved')
    diagnostic=result['report']['steps'][-1]['effect_diagnostics']
    assert diagnostic['regions']['faint']['subject_contrast']['delta']==0
    assert len(list(routing_case[0].glob('reports/*/scientific-attempt.json')))==1
    protected.verify(routing_case[0],result['report'],routing_case[1],routing_case[-1],incomplete=failure)
    diagnostic['regions']['faint']['span']['delta']=1.
    with pytest.raises(contract.ContractError,match='effect diagnostics'):
        protected.verify(routing_case[0],result['report'],routing_case[1],routing_case[-1],incomplete=failure)


def test_first_ghs_effect_binds_the_actual_derived_channel(routing_case):
    session,source,payload,_,_,regions=routing_case
    prepared=prepare(routing_case,short_goals(routing_case,('ght','ght')),mode='exception')
    item=prepared['candidates']['exception']
    request,_,_,request_file=base.read_request(session,payload,item['request'])
    channel=write(session/'reports/prepared-channel.fit',base.pixels(source)[0]*1.01)
    result=protected.solve(session,payload,request,channel,source,None,regions,request_file,item['run_id'],
                           time.monotonic()+60,derived=True)
    assert result['status']=='solved'
    report=result['report'];diagnostic=report['steps'][0]['effect_diagnostics']
    assert diagnostic['input']==report['bindings']['derived_source']
    assert diagnostic['input']['sha256']!=request['source']['sha256']
    protected.verify(session,report,channel,regions)


@pytest.mark.parametrize('proof',[True,False])
def test_shortening_a_solved_chain_needs_actual_ghs_before_after_evidence(routing_case,proof):
    prepared=prepare(routing_case,goals_for(routing_case,'emission_nebula'))
    item=prepared['candidates']['profile']
    result=base.solve_stretch_parameters(routing_case[0],item['request'],item['run_id'])
    assert result['status']=='solved'
    goals=short_goals(routing_case)
    before=result['report']['request']['source'];after=result['report']['steps'][0]['selected']['.fit']
    goals['revision']={'parameters':base.record(routing_case[0],Path(result['parameters_path'])),
        'affected_regions':['faint'],'reason':'The actual GHS pair shows no incremental structure effect; test the shorter declared display route.',
        'target_change':None,'evidence':[before,after] if proof else [result['report']['steps'][0]['local_panel']]}
    if proof:
        next_plan=prepare(routing_case,goals,'072-short','exception')
        assert next_plan['candidates']['exception']['methods']==['mtf','ght']
    else:
        with pytest.raises(contract.ContractError,match='actual GHS input/output'):
            prepare(routing_case,goals,'072-short','exception')
    assert len(list(routing_case[0].glob('reports/*/scientific-attempt.json')))==1


def test_exception_factor_revision_reuses_route_but_route_change_needs_evidence(routing_case):
    prepared = prepare(routing_case, short_goals(routing_case), mode='exception')
    item = prepared['candidates']['exception']
    result = base.solve_stretch_parameters(routing_case[0], item['request'], item['run_id'])
    assert result['status'] == 'solved'
    goals = short_goals(routing_case)
    goals['operators'][0]['factors']['midtones'] = .11
    goals['revision'] = {'parameters': base.record(routing_case[0], Path(result['parameters_path'])),
                         'affected_regions': ['faint'], 'reason': 'Adjust the measured faint structure.',
                         'target_change': None, 'evidence': [result['report']['steps'][0]['local_panel']]}
    revised = prepare(routing_case, goals, '072-factor', 'exception')
    assert revised['candidates']['exception']['methods'] == ['mtf','ght']
    goals['operators'] = short_goals(routing_case, ('ght','ght'))['operators']
    with pytest.raises(contract.ContractError, match='actual GHS input/output'):
        prepare(routing_case, goals, '073-route', 'exception')
    assert len(list(routing_case[0].glob('reports/*/scientific-attempt.json'))) == 1


def test_outgoing_v1_preflight_and_policy_are_preserved(tmp_path,monkeypatch):
    fixture=ROOT/'tests/fixtures/stretch-curve-effects-prior.json';prior=contract.load_json(fixture)
    assert fixture.read_bytes()==(ROOT/'references/stretch-curve-effects-history.json').read_bytes()
    assert sessions._PRE_CURVE_EFFECT_BINDINGS==prior
    assert checks.enabled({'execution_policy':prior['execution_policy']})
    assert not checks.effects_enabled({'execution_policy':prior['execution_policy']})
    assert_contract_domains(prior['execution_policy'])
    replay_and_finalize_without_migration(tmp_path,monkeypatch,fixture)


def test_v1_preflight_keeps_its_nonpredictive_read_and_verification(routing_case):
    goals=short_goals(routing_case);goals['operators'][0]['factors']['midtones']=.5
    current,path=inspect(routing_case,with_constraint_review(goals,'exception'),mode='exception')
    assert current['status']=='needs_revision'
    goals=contract.load_json(path);session,_,payload,_,request,_=routing_case
    prior=contract.load_json(ROOT/'tests/fixtures/stretch-curve-effects-prior.json')
    payload={**payload,'execution_policy':prior['execution_policy']}
    plan={'mode':'exception','run_id':'071-v1','source':request['source'],
          'regions':base.record(session,session/request['regions']['path']),
          'goals':base.record(session,path),'full_source':None}
    requests=routes.bound_requests_for(plan,goals,{})
    original=checks.assess(session,payload,plan,goals,requests)
    assert original['schema']==checks.LEGACY_SCHEMA and original['status']=='ready'
    assert original['output_reachability']=='not_predicted' and 'curve_effects' not in original
    frozen=session/'reports/071-v1/constraint-preflight.json';frozen.parent.mkdir()
    contract.atomic_write_json(frozen,original);plan['constraint_preflight']=base.record(session,frozen)
    checks.verify(session,payload,plan,goals,requests)


@pytest.mark.parametrize('drift',['prefix','log','archive','regions'])
def test_reused_curve_inputs_require_complete_unchanged_evidence(routing_case,drift):
    session=routing_case[0];goals=goals_for(routing_case)
    prepared=prepare(routing_case,goals);item=prepared['candidates']['profile']
    result=base.solve_stretch_parameters(session,item['request'],item['run_id'])
    report=result['report']
    plan=contract.load_json(Path(prepared['strategy']))
    goals=contract.load_json(session/plan['goals']['path'])
    goals['revision']={'parameters':base.record(session,Path(result['parameters_path'])),
        'affected_regions':['faint'],'reason':'Inspect the same exact prefix and unchanged targets.',
        'target_change':None,'evidence':[report['steps'][-1]['local_panel']]}
    if drift=='prefix':binding=report['steps'][0]['selected']['.fit']
    elif drift=='log':binding=report['steps'][0]['trials'][0]['evidence']['log']
    elif drift=='archive':binding=report['trial_artifacts']
    else:binding=report['request']['regions']
    path=session/binding['path'];path.write_bytes(path.read_bytes()+(b' ' if drift=='regions' else b'changed'))
    result,_=inspect(routing_case,with_constraint_review(goals),'072-drift')
    assert result['status']=='needs_revision'
    assert not (session/'reports/072-drift').exists()
    assert len(list(session.glob('reports/*/scientific-attempt.json')))==1

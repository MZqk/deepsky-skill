"""Whole-chain basis coverage, immutable preflight bindings and GHS semantics."""
import copy
import json
from pathlib import Path
import sys
import time

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_preflight as checks
import deep_sky_siril_stretch_profiles as routes
from ._policy_pins import replay_and_finalize_without_migration
from ._stretch_factory import with_constraint_review
from .test_stretch_routing_v29 import routing_case, goals_for, prepare
from .test_deep_sky_siril_stretch import context, write

ROOT = Path(__file__).parents[1]


def inspect(case, goals, run_id='071-preflight', mode='profile'):
    session, _, _, _, request, _ = case
    path = session/'reports'/f'{run_id}-goals.json'
    from ._stretch_factory import with_morphology
    contract.atomic_write_json(path, with_morphology(session,request['source'],request['regions'],goals))
    result = checks.preflight_requests(session, request['source']['path'], request['regions']['path'], path, run_id, mode=mode, stepwise=False)
    return result, path


def intermediate_goals(case):
    goals = goals_for(case, 'emission_nebula')
    for phase in goals['phases'][:2]:
        phase['constraints'] += [{'region':'core','statistic':'median','min':.005,'max':.9},
                                 {'region':'core','statistic':'p99','min':.01,'max':.98}]
    return goals


def test_all_ic434_style_intermediate_bounds_are_listed_before_any_trial(routing_case):
    goals = intermediate_goals(routing_case)
    result, path = inspect(routing_case, goals)
    assert result['status']=='needs_revision'
    missing = [r for r in result['issues'] if r['code']=='missing_intermediate_basis']
    assert {(r['step'],r['statistic']) for r in missing} == {(1,'median'),(1,'p99'),(2,'median'),(2,'p99')}
    reviewed = with_constraint_review(goals)
    reviewed['constraint_review'] = [r for r in reviewed['constraint_review'] if r['statistic']=='median']
    result, path = inspect(routing_case, reviewed)
    assert {(r['step'],r['statistic']) for r in result['issues'] if r['code']=='missing_intermediate_basis'} == {(1,'p99'),(2,'p99')}
    with pytest.raises(contract.ContractError, match='p99'):
        routes.init_requests(routing_case[0], routing_case[4]['source']['path'], routing_case[4]['regions']['path'], path, '071-preflight', stepwise=False)
    assert not (routing_case[0]/'reports/071-preflight').exists()
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))
    assert not list((routing_case[0]/'reports').glob('.stretch-profile-*'))


def test_output_lower_bound_above_input_is_advisory_with_complete_basis(routing_case):
    goals = goals_for(routing_case)
    goals['phases'][0]['constraints'] = [{'region':'faint','statistic':'median','min':.7,'max':.8}]
    result, path = inspect(routing_case, with_constraint_review(goals))
    assert result['status']=='ready'
    assert result['output_reachability']=='not_predicted'
    assert any(w['code']=='input_outside_output_interval' and w['diagnostic_only'] for w in result['warnings'])
    first = [r for r in result['constraints'] if r['step']==1]
    later = [r for r in result['constraints'] if r['step']==2]
    assert all(r['input']['kind']=='actual_linear_parent' and r['input_value'] is not None for r in first)
    assert all(r['input']['kind']=='unknown' and r['input_value'] is None for r in later)
    assert not (routing_case[0]/'reports/071-preflight').exists()
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


@pytest.mark.parametrize('fault', ['missing','duplicate','wrong-step','wrong-evidence'])
def test_invalid_basis_is_rejected_before_freezing(routing_case, fault):
    goals = with_constraint_review(intermediate_goals(routing_case))
    if fault=='missing': goals['constraint_review'].pop()
    elif fault=='duplicate': goals['constraint_review'].append(copy.deepcopy(goals['constraint_review'][0]))
    elif fault=='wrong-step': goals['constraint_review'][0]['step']=99
    else: goals['constraint_review'][0]['evidence'][0]['sha256']='0'*64
    result, _ = inspect(routing_case, goals)
    assert result['status']=='needs_revision'
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def test_conflicts_and_later_step_coverage_are_aggregated(routing_case):
    goals = with_constraint_review(intermediate_goals(routing_case))
    for phase in goals['phases'][:2]:
        phase['constraints'].append({'region':'sky','statistic':'median','min':.9,'max':1.})
    result, _ = inspect(routing_case, goals)
    assert result['status']=='needs_revision'
    assert {r['step'] for r in result['issues'] if r['code']=='shared_protection_conflict'}=={1,2}
    assert all('shared_protection' in r for r in result['constraints'])


def test_same_scientific_prefix_shares_basis_with_tail(routing_case):
    goals = intermediate_goals(routing_case)
    goals['tail']={'purpose':'Compare the final transfer only','factors':{'D':.3,'B':.5},
                   'landmarks':copy.deepcopy(goals['phases'][0]['landmarks'])}
    goals=with_constraint_review(goals)
    goals['constraint_review']=[r for r in goals['constraint_review'] if r['variant']=='profile']
    result, _=inspect(routing_case,goals)
    assert result['status']=='ready'
    assert all(r['basis']['variant']=='profile' for r in result['constraints'] if r['variant']=='tail' and r['requires_basis'])


def test_hoo_channel_bases_are_separate_and_rgb_is_not_their_input(routing_case):
    goals=goals_for(routing_case)
    goals['phases'][0]['constraints']=[{'region':'core','statistic':'median','min':.005,'max':.9}]
    body=routes.bound_requests_for({'mode':'profile','source':{},'regions':{}},goals,{})['profile']['operators']
    goals['channels']={role:copy.deepcopy(body) for role in ('rgb','ha','oiii')}
    goals=with_constraint_review(goals)
    complete=copy.deepcopy(goals)
    goals['constraint_review']=[r for r in goals['constraint_review'] if r['variant']=='profile']
    result,_=inspect(routing_case,goals)
    assert {r['variant'] for r in result['issues'] if r['code']=='missing_intermediate_basis'}=={'ha','oiii'}
    result,_=inspect(routing_case,complete)
    assert result['status']=='ready'
    assert all(r['input_value'] is None and r['input']['kind']=='unknown' for r in result['constraints']
               if r['variant'] in {'ha','oiii'})


def test_all_missing_roi_protection_is_reported_across_steps(routing_case):
    goals=with_constraint_review(goals_for(routing_case))
    goals['protection']=[r for r in goals['protection'] if r['region']!='faint']
    result,_=inspect(routing_case,goals)
    missing=[r for r in result['issues'] if r['code']=='invalid_constraints' and r.get('region')=='faint']
    assert {r['step'] for r in missing}=={1,2}
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def test_empty_review_is_explicitly_required_without_intermediate_lower_bounds(routing_case):
    goals=goals_for(routing_case)
    for rows in [goals['protection'],*[phase['constraints'] for phase in goals['phases']]]:
        for row in rows:
            if row['statistic'] in checks.BRIGHTNESS:
                row['min']=0.
    result,_=inspect(routing_case,goals)
    assert [r['code'] for r in result['issues']]==['missing_constraint_review']
    goals['constraint_review']=[]
    result,_=inspect(routing_case,goals)
    assert result['status']=='ready'


@pytest.mark.parametrize('fault',['changed','missing','binding-removed'])
def test_preflight_binding_is_checked_at_request_load_and_native_claim(routing_case,fault):
    session,_,payload,_,_,_=routing_case
    prepared=prepare(routing_case)
    item=prepared['candidates']['profile']
    path=Path(prepared['constraint_preflight'])
    request=contract.load_json(Path(item['request']))
    if fault=='changed': path.write_text(path.read_text()+' ')
    elif fault=='missing': path.unlink()
    else:
        plan=contract.load_json(Path(prepared['strategy']));plan.pop('constraint_preflight')
        contract.atomic_write_json(Path(prepared['strategy']),plan)
        request['strategy']['plan']=stretch.record(session,Path(prepared['strategy']))
        contract.atomic_write_json(Path(item['request']),request)
    with pytest.raises((contract.ContractError,OSError)): stretch.read_request(session,payload,item['request'])
    with pytest.raises((contract.ContractError,OSError)): routes.claim_attempt(session,payload,item['run_id'],request)
    assert not list(session.glob('reports/*/scientific-attempt.json'))


@pytest.mark.parametrize('declaration',[None,{},'unbound',[]])
def test_unbound_native_claim_cannot_bypass_preflight(routing_case,declaration):
    with pytest.raises(contract.ContractError):
        routes.claim_attempt(routing_case[0],routing_case[2],'071-unbound',{'strategy':declaration})
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def test_unchanged_native_prefix_is_the_only_known_later_input(routing_case):
    session,_,_,_,request,_=routing_case
    goals=goals_for(routing_case,'emission_nebula')
    goals['phases'][-1]['constraints']=[{'region':'faint','statistic':'median','min':.7,'max':.8}]
    prepared=prepare(routing_case,goals)
    item=prepared['candidates']['profile']
    failed=stretch.solve_stretch_parameters(session,item['request'],item['run_id'])
    assert failed['status']=='ineligible'
    goals['revision']={'parameters':stretch.record(session,Path(failed['parameters_path'])),
        'affected_regions':['faint'],'reason':'Inspect the same measured prefixes before proposing another run.',
        'target_change':None,'evidence':[failed['report']['steps'][-1]['local_panel']]}
    result,_=inspect(routing_case,with_constraint_review(goals),'072-inspect')
    assert result['status']=='needs_revision'
    assert any(row['code']=='mtf_mapping_outside_target' for row in result['issues'])
    assert all(r['input']['kind']=='verified_native_prefix' for r in result['constraints'] if r['step']>1)
    goals['phases'][0]['factors']['D']+=.1
    result,_=inspect(routing_case,with_constraint_review(goals),'072-new-prefix')
    assert result['status']=='ready',result['issues']
    assert all(r['input']['kind']=='unknown' for r in result['constraints'] if r['step']>1)
    assert len(list(session.glob('reports/*/scientific-attempt.json')))==1


@pytest.mark.parametrize('ready',[False,True])
def test_cli_preflight_status_and_read_only_exit(routing_case,monkeypatch,capsys,ready):
    goals=intermediate_goals(routing_case)
    if ready: goals=with_constraint_review(goals)
    _,path=inspect(routing_case,goals)
    monkeypatch.setattr(sys,'argv',['stretch','preflight','--session',str(routing_case[0]),
        '--source',routing_case[4]['source']['path'],'--regions',routing_case[4]['regions']['path'],
        '--goals',str(path),'--run-id','071-preflight','--batch'])
    assert stretch.main()==(0 if ready else 1)
    assert json.loads(capsys.readouterr().out)['status']==('ready' if ready else 'needs_revision')
    assert not (routing_case[0]/'reports/071-preflight').exists()


def test_native_sp_is_not_a_fixed_point_and_hp_below_one_is_valid(context):
    session,*_=context
    from .test_deep_sky_siril import _fake_probe
    from .test_stretch_profiles_v23 import BINARY
    if not BINARY.exists():
        pytest.skip('Native Siril is unavailable')
    runtime=_fake_probe(BINARY)['tools']['siril_cli']
    data=np.tile(np.repeat(np.array([.2,.85,.9,.95],dtype=np.float32),8),(3,32,1))
    source=write(session/'artifacts/ghs-semantics.fit',data)
    directory=session/'reports/ghs-semantics';directory.mkdir()
    oracle=stretch.NativeTrials(session,runtime,directory,directory,time.monotonic()+60)
    output,_=oracle.run(source,[{'command':'ght','parameters':[
        '-D=2','-B=0.5','-LP=0','-SP=0.2','-HP=0.8','-human','-clipmode=rgbblend']}])
    value=stretch.pixels(output)
    assert np.isfinite(value).all() and value.min()>0 and value.max()<1
    assert abs(float(value[:,:,:8].mean())-.2)>1e-4
    highlights=np.array([value[:,:,i:i+8].mean() for i in (8,16,24)])
    assert abs(highlights[1]-.9)>1e-4
    assert np.allclose(np.diff(highlights)[0],np.diff(highlights)[1],rtol=0,atol=5e-7)


def test_frozen_prior_policy_replays_and_finalizes_without_new_preflight(tmp_path,monkeypatch):
    prior=ROOT/'tests/fixtures/stretch-constraint-preflight-prior.json'
    assert prior.read_bytes()==(ROOT/'references/stretch-constraint-preflight-history.json').read_bytes()
    policy=contract.load_json(prior)['execution_policy']
    assert sessions.known_execution_policy(policy) and not checks.enabled({'execution_policy':policy})
    replay_and_finalize_without_migration(tmp_path,monkeypatch,prior)

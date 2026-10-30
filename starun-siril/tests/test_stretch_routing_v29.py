"""Bound morphology routes, immutable revisions and scientific attempt accounting."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_v2 as protected
from .test_deep_sky_siril_stretch import context, write
from .test_stretch_v22 import make_v2
from ._stretch_factory import source_evidence

ROOT = Path(__file__).parents[1]
NATIVE_RUN = stretch.NativeTrials.run


@pytest.fixture
def routing_case(context, monkeypatch):
    root, source, payload, path, request, _ = context
    regions = make_v2(root, source, request)
    payload['input'].update(contract.fingerprint(source, include_path=False))
    contract.atomic_write_json(root/'session.json', payload)
    request['operators'][0]['constraints'] = [dict(row, min=0.) if row['region'] == 'faint' else row
                                             for row in request['operators'][0]['constraints']]
    def native(self, parent, chain):
        self._claim_attempt()
        self.count += 1
        data = stretch.pixels(parent)
        output = write(self.directory/f'trial-{self.count}.fit', data.astype(np.float32))
        rgb = np.repeat(data, 3, axis=0) if len(data) == 1 else data
        Image.fromarray(np.uint8(np.clip(rgb.transpose(1, 2, 0), 0, 1)*255)).save(output.with_suffix('.jpg'))
        script = self.evidence_dir/f'trial-{self.count}.ssf'
        log = self.evidence_dir/f'trial-{self.count}.log'
        script.write_text('requires 1.4.4 1.5.0\nset32bits\nload '+json.dumps(str(parent))+'\n'+
                          '\n'.join(' '.join([s['command'], *s['parameters']]) for s in chain)+
                          '\nsave '+json.dumps(str(output))+' -chksum\nclose\n')
        log.write_text('Mock native execution: identity response for routing tests')
        return output, {'script': stretch.record(root, script), 'log': stretch.record(root, log),
                        'output_sha256': contract.sha256_file(output)}
    monkeypatch.setattr(stretch.NativeTrials, 'run', native)
    return root, source, payload, path, request, regions


def goals_for(case, profile='high_dynamic_range'):
    root, _, _, _, request, _ = case
    phases = []
    for step in contract.load_json(ROOT/'references/stretch-profiles.json')[profile]['preferred_chain']:
        method = step['method']
        phase = {'constraints': [], 'factors': {'asinh': {'stretch': 2.}, 'mtf': {'midtones': .4}, 'ght': {'D': .2, 'B': .5}}[method]}
        if method == 'ght':
            phase['landmarks'] = {k: {'region': r, 'statistic': s} for k,r,s in
                                 [('LP','sky','p99'),('SP','faint','median'),('HP','core','p01')]}
        phases.append(phase)
    return {'schema': routes.BOUND_GOALS, 'profile': profile, 'reason': 'Observed synthetic faint envelope and protected core in the actual bound source.',
            'evidence':source_evidence(root,request['source']), 'targets': request['targets'], 'core_absence': None,
            'protection': copy.deepcopy(request['operators'][0]['constraints']), 'phases': phases,
            'manual_lookup': request['manual_lookup'], 'revision': None}


def prepare(case, goals=None, run_id='071-bound', mode='profile'):
    root, _, _, _, request, _ = case
    path = root/'reports'/(run_id+'-goals.json')
    from ._stretch_factory import with_constraint_review, with_morphology
    contract.atomic_write_json(path, with_morphology(root,request['source'],request['regions'],with_constraint_review(goals or goals_for(case), mode)))
    return routes.init_requests(root, request['source']['path'], request['regions']['path'], path, run_id, mode=mode, stepwise=False)


@pytest.mark.parametrize('profile', ['emission_nebula','high_dynamic_range','galaxy_halo','dust_reflection','star_cluster'])
def test_initial_chain_is_exact_and_single_candidate(routing_case, profile):
    case = routing_case
    result = prepare(case, goals_for(case, profile))
    assert set(result['candidates']) == {'profile'}
    selected = result['candidates']['profile']
    parsed, *_ = stretch.read_request(case[0], case[2], selected['request'])
    expected = contract.load_json(ROOT/'references/stretch-profiles.json')[profile]['preferred_chain']
    assert [s['method'] for s in parsed['operators']] == [s['method'] for s in expected]
    parsed['operators'].reverse()
    contract.atomic_write_json(Path(selected['request']), parsed)
    with pytest.raises(contract.ContractError, match='frozen plan'):
        stretch.read_request(case[0], case[2], selected['request'])


def test_unbound_and_initial_custom_cannot_start_native(routing_case):
    root, _, payload, path, request, _ = routing_case
    request['schema'] = protected.PROPOSAL_REQUEST
    request['operators'][0]['factors'] = {'midtones': .4}
    contract.atomic_write_json(path, request)
    with pytest.raises(contract.ContractError, match='bound morphology'):
        stretch.solve_stretch_parameters(root, path, '071-free')
    goals = goals_for(routing_case)
    goals['profile'] = 'custom'
    with pytest.raises(contract.ContractError, match='morphology'):
        prepare(routing_case, goals)
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_source_hash_alone_does_not_prove_morphology(routing_case):
    goals=goals_for(routing_case)
    goals['evidence']=[routing_case[4]['source']]
    with pytest.raises(contract.ContractError,match='committed preview'):
        prepare(routing_case,goals)
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def test_solved_plan_does_not_require_an_independent_baseline(routing_case):
    root, *_ = routing_case
    result = prepare(routing_case)
    selected = result['candidates']['profile']
    solved = stretch.solve_stretch_parameters(root, selected['request'], selected['run_id'])
    assert solved['status'] == 'solved', solved['report'].get('reason')
    assets = stretch.generate_stretch_assets(root, parameters=solved['parameters_path'])
    assert '# adaptive-stretch:' in Path(assets['script_path']).read_text()
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    with pytest.raises(contract.ContractError, match='previous native attempt'):
        prepare(routing_case, run_id='072-restart')


def test_threshold_revision_has_no_new_image_benefit(routing_case):
    root, *_ = routing_case
    initial = goals_for(routing_case)
    initial['phases'][-1]['constraints'] = [{'region':'faint','statistic':'median','min':.7,'max':.8}]
    result = prepare(routing_case, initial)
    item = result['candidates']['profile']
    failed = stretch.solve_stretch_parameters(root, item['request'], item['run_id'])
    assert failed['status'] == 'ineligible'
    before = Path(failed['parameters_path']).read_bytes()
    revised = goals_for(routing_case)
    revised['revision'] = {'parameters':stretch.record(root,Path(failed['parameters_path'])),
        'reason':'Correct the final display requirement after reviewing the frozen response.',
        'affected_regions':['faint'], 'evidence':[],
        'target_change':{'kind':'display_correction', 'reason':'The original numeric display target was not supported by the bound source comparison.',
            'tradeoff':'Retain the actual display response and independently review its first prefix.',
            'evidence':[failed['report']['steps'][-1]['local_panel']]}}
    next_plan = prepare(routing_case, revised, '072-corrected')
    plan = contract.load_json(Path(next_plan['strategy']))
    assert plan['revision_diff']['same_declared_transform']
    assert plan['revision_diff']['final_constraints']
    assert Path(failed['parameters_path']).read_bytes() == before


def test_historical_v28_fingerprints_are_registered_exactly():
    prior = contract.load_json(ROOT/'tests/fixtures/stretch-routing-prior.json')
    assert sessions._PRE_STRETCH_ROUTING_BINDINGS == prior
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current({'execution_policy':prior['execution_policy']})
    for name, record in prior['documents'].items():
        assert sessions.registered_guidance_reference({'execution_policy':prior['execution_policy']}, {'path':name,**record})


def failed_profile(case):
    goals = goals_for(case)
    goals['phases'][0]['constraints'] = [{'region':'faint','statistic':'median','min':.7,'max':.8}]
    prepared = prepare(case, goals)
    item = prepared['candidates']['profile']
    result = stretch.solve_stretch_parameters(case[0], item['request'], item['run_id'])
    assert result['status'] == 'ineligible'
    return result


def exception_goals(case, result):
    goals = goals_for(case)
    goals.pop('phases')
    request = case[4]
    goals['operators'] = [dict(copy.deepcopy(request['operators'][0]), factors={'midtones':.1})]
    goals['operators'].append({'method':'asinh', 'purpose':'Retain core gradation in the explicit alternative',
        'factors':{'stretch':2.}, 'constraints':copy.deepcopy(goals['operators'][0]['constraints'])})
    goals['revision'] = {'parameters':stretch.record(case[0],Path(result['parameters_path'])),
        'affected_regions':['faint'], 'reason':'This measured proposal misses the synthetic faint-structure requirement; evaluate the explicit alternative without disqualifying an operator family.',
        'target_change':None, 'evidence':[result['report']['steps'][-1]['local_panel']]}
    return goals


@pytest.mark.parametrize('mutation', ['reverse','insert','delete','profile'])
def test_initial_route_mutations_never_reach_native(routing_case, mutation):
    result = prepare(routing_case)
    item = result['candidates']['profile']
    path = Path(item['request']); request = contract.load_json(path)
    if mutation == 'reverse': request['operators'].reverse()
    elif mutation == 'insert': request['operators'].append(copy.deepcopy(request['operators'][0]))
    elif mutation == 'delete': request['operators'].pop()
    else: request['profile'] = 'dust_reflection'
    contract.atomic_write_json(path, request)
    with pytest.raises(contract.ContractError):
        stretch.solve_stretch_parameters(routing_case[0], path, item['run_id'])
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def test_real_measured_exception_and_three_attempt_ledger(routing_case):
    root = routing_case[0]
    failure = failed_profile(routing_case)
    goals = exception_goals(routing_case, failure)
    prepared = prepare(routing_case, goals, '072-exception', 'exception')
    item = prepared['candidates']['exception']
    result = stretch.solve_stretch_parameters(root, item['request'], item['run_id'])
    assert result['status'] == 'solved'
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 2
    routes.claim_attempt(root, routing_case[2], '073-last', result['report']['request'])
    with pytest.raises(contract.ContractError, match='three shared'):
        routes.claim_attempt(root, routing_case[2], '074-fourth', result['report']['request'])


@pytest.mark.parametrize('issue', ['hash','foreign','fake-audit','noise-only','technical','no-roi','foreign-script'])
def test_exception_proof_cannot_be_missing_altered_or_unscientific(routing_case, issue):
    result = failed_profile(routing_case)
    goals = exception_goals(routing_case, result)
    if issue == 'hash': goals['revision']['evidence'][0]['sha256'] = '0'*64
    elif issue == 'foreign': goals['revision']['evidence'] = [routing_case[4]['regions']]
    elif issue == 'no-roi': goals['revision']['affected_regions'] = []
    else:
        report = result['report']
        if issue == 'foreign-script':
            trial=report['steps'][0]['trials'][0]
            path=stretch.bound_file(routing_case[0],trial['evidence']['script'])
            path.write_text(path.read_text().replace(str(routing_case[1]),str(routing_case[0]/'reports/other-parent.fit')))
            trial['evidence']['script']=stretch.record(routing_case[0],path)
        elif issue == 'fake-audit': report['steps'][0]['trials'][0]['audit']['statistics']['faint']['median'] += .01
        elif issue == 'noise-only': report['steps'][0]['trials'][0]['audit']['reasons'] = ['noise_diagnosis']
        else: report.update(status='failed', reason='Native process could not launch')
        contract.atomic_write_json(Path(result['parameters_path']), report)
        goals['revision']['parameters'] = stretch.record(routing_case[0],Path(result['parameters_path']))
    with pytest.raises(contract.ContractError):
        prepare(routing_case, goals, '072-invalid', 'exception')
    assert len(list(routing_case[0].glob('reports/*/scientific-attempt.json'))) == 1


def test_control_cannot_authorize_a_body_route_exception(routing_case):
    goals = goals_for(routing_case)
    goals['baseline_factors'] = {'stretch': 2.}
    with pytest.raises(contract.ContractError, match='retired'):
        prepare(routing_case, goals, mode='compare')
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def test_prefix_target_changes_need_their_own_basis(routing_case):
    failed = failed_profile(routing_case)
    goals = goals_for(routing_case)
    goals['revision'] = exception_goals(routing_case, failed)['revision']
    with pytest.raises(contract.ContractError, match='target-change reason'):
        prepare(routing_case, goals, '072-invalid')
    goals['revision']['target_change'] = {'kind':'display_correction',
        'reason':'The retained prefix shows that its display minimum belongs to the completed chain.',
        'tradeoff':'Keep final visibility and scientific highlight protection unchanged.',
        'evidence':goals['revision']['evidence']}
    prepared = prepare(routing_case, goals, '073-corrected')
    plan = contract.load_json(Path(prepared['strategy']))
    assert plan['revision_diff']['step_constraints']
    assert len(list(routing_case[0].glob('reports/*/scientific-attempt.json'))) == 1


def test_final_bound_cannot_hide_weakened_shared_core_protection(routing_case):
    failed = failed_profile(routing_case)
    plan = contract.load_json(routing_case[0]/failed['report']['request']['strategy']['plan']['path'])
    goals = contract.load_json(routing_case[0]/plan['goals']['path'])
    core = next(row for row in goals['protection'] if row['region']=='core' and row['statistic']=='p999')
    goals['phases'][-1]['constraints'].append(copy.deepcopy(core))
    core['max'] = 1.
    goals['revision'] = exception_goals(routing_case, failed)['revision']
    goals['revision']['target_change'] = {'kind':'science_protection',
        'reason':'Regression test attempts to relax only intermediate shared protection.',
        'tradeoff':'The final bound is restored, which must not authorize an earlier loss.',
        'evidence':goals['revision']['evidence']}
    with pytest.raises(contract.ContractError, match='cannot weaken'):
        prepare(routing_case, goals, '072-invalid')


def test_hdr_mask_is_part_of_the_frozen_scientific_strategy(routing_case):
    failure = failed_profile(routing_case)
    goals = exception_goals(routing_case, failure)
    chain = [{'command':'mtf','parameters':['0','.4','1']}]
    goals['hdr'] = {'gentle':chain, 'faint':chain, 'mask':{'mask_low':.15,'mask_high':.75,'sigma':2}}
    prepared = prepare(routing_case, goals, '072-hdr', 'exception')
    binding = stretch.record(routing_case[0], Path(prepared['strategy']))
    changed = {'gentle_chain':chain, 'faint_chain':chain, 'mask':dict(goals['hdr']['mask'], sigma=3)}
    with pytest.raises(contract.ContractError, match='branches or mask'):
        routes.validate_external_strategy(routing_case[0], routing_case[2], routing_case[1], binding, hdr=changed)


def test_prelaunch_timeout_does_not_spend_a_scientific_attempt(routing_case):
    root, source, payload, *_ = routing_case
    prepared = prepare(routing_case)
    item = prepared['candidates']['profile']
    runtime = contract.load_json(root/'reports/tool-probe.json')['tools']['siril_cli']
    oracle = stretch.NativeTrials(root, runtime, root/'artifacts', root/'reports', 0)
    oracle.scientific_attempt = (root, payload, item['run_id'], contract.load_json(Path(item['request'])))
    with pytest.raises(TimeoutError):
        NATIVE_RUN(oracle, source, [{'command':'mtf','parameters':['0','.4','1']}])
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_profile_revision_preserves_the_original_classification(routing_case):
    failure = failed_profile(routing_case)
    replacement = goals_for(routing_case, 'dust_reflection')
    replacement['revision'] = exception_goals(routing_case, failure)['revision']
    with pytest.raises(contract.ContractError, match='exception mode'):
        prepare(routing_case, replacement, '072-new-profile')
    replacement.pop('phases')
    replacement['operators'] = failure['report']['request']['operators']
    replacement['revision']['affected_regions'] = ['core','faint']
    result = prepare(routing_case, replacement, '073-profile-revision', 'exception')
    plan = contract.load_json(Path(result['strategy']))
    assert plan['root'] == failure['report']['request']['strategy']['plan']


def test_tail_comparison_binds_without_a_single_step_control(routing_case):
    root, source, payload, _, request, _ = routing_case
    make_v2(root, source, request, 3)
    payload['input'].update(contract.fingerprint(source, include_path=False))
    contract.atomic_write_json(root/'session.json',payload)
    goals = goals_for(routing_case, 'emission_nebula')
    goals.update(tail={'purpose':'Explicit same-prefix luminance tail control',
        'factors':{'D':.2,'B':.5}, 'landmarks':copy.deepcopy(goals['phases'][0]['landmarks'])})
    prepared = prepare(routing_case, goals)
    paths = {}
    for variant in ('profile','tail'):
        item = prepared['candidates'][variant]
        result = stretch.solve_stretch_parameters(root,item['request'],item['run_id'])
        assert result['status'] == 'solved'
        paths[variant] = result['parameters_path']
    tail = routes.compare_tail(root,paths['profile'],paths['tail'],'074-tail-compare')
    with pytest.raises(contract.ContractError,match='comparison|compare-tail'):
        stretch.generate_stretch_assets(root,parameters=paths['profile'])
    assets = stretch.generate_stretch_assets(root,parameters=paths['profile'],comparison=tail['comparison'])
    text = Path(assets['script_path']).read_text()
    assert text.count('# stretch-comparison:') == 1
    binding = stretch.adaptive_binding(root,payload,source,text,assets['parameters']['transfer_chain'],assets['parameters']['run_id'])
    assert len(binding['comparisons']) == 1
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 2


def test_failed_prefix_stops_its_same_prefix_tail(routing_case):
    root, source, payload, _, request, _ = routing_case
    make_v2(root,source,request,3);payload['input'].update(contract.fingerprint(source,include_path=False))
    contract.atomic_write_json(root/'session.json',payload)
    goals=goals_for(routing_case,'emission_nebula')
    goals['phases'][0]['constraints']=[{'region':'faint','statistic':'median','min':.7,'max':.8}]
    goals['tail']={'purpose':'Same-prefix tail','factors':{'D':.2,'B':.5},'landmarks':copy.deepcopy(goals['phases'][0]['landmarks'])}
    prepared=prepare(routing_case,goals)
    main=prepared['candidates']['profile'];tail=prepared['candidates']['tail']
    assert stretch.solve_stretch_parameters(root,main['request'],main['run_id'])['status']=='ineligible'
    with pytest.raises(contract.ContractError,match='finish its exact prefix'):
        stretch.solve_stretch_parameters(root,tail['request'],tail['run_id'])
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


@pytest.mark.parametrize('profile', ['emission_nebula','high_dynamic_range','galaxy_halo','dust_reflection','star_cluster'])
def test_native_five_profiles_use_only_explicit_factors(routing_case,monkeypatch,profile):
    from .test_deep_sky_siril_stretch import BINARY, _fake_probe
    if not BINARY.is_file(): pytest.skip('Siril 1.4.4 unavailable')
    root=routing_case[0]
    contract.atomic_write_json(root/'reports/tool-probe.json',_fake_probe(BINARY))
    monkeypatch.setattr(stretch.NativeTrials,'run',NATIVE_RUN)
    monkeypatch.setattr(protected.itertools,'product',lambda *args: pytest.fail('No old parameter grids'))
    goals=goals_for(routing_case,profile)
    for row in goals['protection']:
        if row['region']=='sky' and row['statistic']=='median': row['min']=0.
    prepared=prepare(routing_case,goals)
    item=prepared['candidates']['profile']
    result=stretch.solve_stretch_parameters(root,item['request'],item['run_id'],60)
    assert result['status']=='solved',result['report'].get('reason')
    stretch.load_parameters(root,routing_case[2],result['parameters_path'])
    assert all(step['evaluation_count']==1 for step in result['report']['steps'])

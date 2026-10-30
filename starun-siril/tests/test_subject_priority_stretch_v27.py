"""One morphology-guided proposal, native prefix review and immutable v26 history."""
from ._stretch_factory import solve_bound, source_evidence

import copy
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_stretch_profiles as profiles
from ._policy_pins import assert_contract_domains, replay_and_finalize_without_migration
from .test_deep_sky_siril_stretch import context
from .test_recomposition_sources_v11 import source_case, accept, recompose_assets
from .test_starnet_linear_bridge import native
from .test_stretch_v22 import case, field_request
from .test_stretch_profiles_v23 import goals_for

ROOT = Path(__file__).parents[1]
PRIOR_PATH = ROOT/'tests/fixtures/subject-priority-stretch-prior.json'
PRIOR = contract.load_json(PRIOR_PATH)


def test_v27_only_changes_guidance_and_exact_v26_sources_remain_registered():
    # prior=subject-priority-stretch-prior.json: guidance, chroma_denoise, color_preservation,
    # recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY == PRIOR['execution_policy']
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    assert not sessions.execution_policy_is_current({'execution_policy': PRIOR['execution_policy']})
    for name, binding in PRIOR['documents'].items():
        record = {'path': name, **binding}
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
        assert not sessions.registered_guidance_reference({'execution_policy': contract.EXECUTION_POLICY}, record)
        for change in ({'path': name+'-unknown'}, {'sha256': '0'*64}, {'size': binding['size']+1}):
            assert not sessions.registered_guidance_reference(
                {'execution_policy': PRIOR['execution_policy']}, {**record, **change})
    assert contract.fingerprint(ROOT/'references/stretch-profiles.json', include_path=False) == sessions._PRE_CONTOUR_BINDINGS['profiles']
    from query_siril_manual import _HISTORICAL_COMMAND_POLICIES
    import hashlib
    policy = PRIOR['documents']['references/command-policy.json']
    raw = _HISTORICAL_COMMAND_POLICIES[policy['sha256'],policy['size']]
    assert hashlib.sha256(raw).hexdigest() == policy['sha256'] and len(raw) == policy['size']


def test_v26_helper_bindings_reject_mixing_and_new_execution(case):
    session, _, payload, path, _, _ = case
    payload['execution_policy'] = copy.deepcopy(PRIOR['execution_policy'])
    contract.atomic_write_json(session/'session.json', payload)
    assert protected.implementation(session) == PRIOR['implementation']
    assert protected.profiles_fingerprint(session) == PRIOR['profiles']
    assert protected.implementation_matches(session, PRIOR['implementation'])
    mixed = {**PRIOR['implementation'],
             'deep_sky_siril_stretch_v2.py': protected.implementation()['deep_sky_siril_stretch_v2.py']}
    assert not protected.implementation_matches(session, mixed)
    with pytest.raises(contract.ContractError, match='Historical sessions'):
        stretch.solve_stretch_parameters(session, path, '071-retired')
    with pytest.raises(contract.ContractError, match='Historical sessions'):
        stretch.generate_stretch_assets(session, parameters='reports/unwritten.json')
    assert not (session/'reports/071-retired').exists()


def test_v26_replay_and_finalize_preserve_committed_bytes(tmp_path, monkeypatch):
    replay_and_finalize_without_migration(tmp_path, monkeypatch, PRIOR_PATH)


def test_native_multi_step_proposal_without_single_curve_comparison(linear_case, monkeypatch):
    session, _, _, parent, *_ = linear_case
    path, request = field_request(session, parent, name='071-subject')
    request.update(schema=protected.PROPOSAL_REQUEST, profile='high_dynamic_range')
    first = request['operators'][0]
    first.update(method='asinh', factors={'stretch': 5.},
                 purpose='Reveal the known synthetic envelope while retaining its core')
    for constraint in first['constraints']:
        if constraint['region'] == 'sky' and constraint['statistic'] in {'mad', 'hf_sigma2'}:
            constraint['max'] = 0.
    second = copy.deepcopy(first)
    second.update(method='ght', factors={'D': .2, 'B': .5},
                  purpose='Separate the actual nonlinear envelope interval',
                  landmarks={key: {'region': region, 'statistic': statistic} for key, region, statistic in
                             [('LP', 'sky', 'p99'), ('SP', 'shell', 'median'), ('HP', 'core', 'p01')]})
    request['operators'].append(second)
    request['manual_lookup']['reason'] = 'Complete explicit Asinh and GHS fixture skeleton'
    contract.atomic_write_json(path, request)

    def forbid_search(*args, **kwargs):
        raise AssertionError('A single explicit proposal must not enter parameter search')

    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    goals = goals_for(request,'high_dynamic_range')
    goals['evidence'] = source_evidence(session,request['source'])
    from ._stretch_factory import with_constraint_review, with_morphology
    goals = with_morphology(session, request['source'], request['regions'], with_constraint_review(goals))
    goal_path = session/'reports/071-subject-goals.json'; contract.atomic_write_json(goal_path,goals)
    prepared = profiles.init_requests(session,request['source']['path'],request['regions']['path'],goal_path,'071-subject', stepwise=False)
    item = prepared['candidates']['profile']
    result = stretch.solve_stretch_parameters(session,item['request'],item['run_id'],timeout=60)
    assert result['status'] == 'solved', result['report'].get('reason')
    report = result['report']
    assert report['request']['strategy']['variant']=='profile'
    assert report['generation'] == protected.PROPOSAL_POLICY
    assert [step['method'] for step in report['steps']] == ['asinh', 'ght']
    assert [step['evaluation_count'] for step in report['steps']] == [1, 1]
    assert [step['native_evaluation_count'] for step in report['steps']] == [4, 4]
    assert all(step['prefix_audit']['noise_warnings'] for step in report['steps'])
    current = report['steps'][1]
    anchors = {token[1:].split('=')[0]: float(token.split('=')[1])
               for token in current['selected_parameters']['parameters']
               if token.startswith(('-LP=', '-SP=', '-HP='))}
    assert anchors == {key: current['input_statistics'][value['region']][value['statistic']]
                       for key, value in second['landmarks'].items()}
    assert current['input_statistics'] != report['steps'][0]['input_statistics']

    assets = stretch.generate_stretch_assets(session, parameters=result['parameters_path'])
    script = Path(assets['script_path']).read_text()
    assert '# adaptive-stretch:' in script and '# stretch-comparison:' not in script
    choices = contract.load_json(Path(assets['provenance_path']))['rationale']['parameter_choices']
    assert any('Morphology-bound strategy' in choice for choice in choices)
    run = core.run_script(session, protocol='stretch', script_value=assets['script_path'], source_value=parent,
        expected_values=[assets['primary_output'], assets['preview_output']],
        primary_output_value=assets['primary_output'], timeout=60)
    assert run['status'] == 'success', run
    assert 'comparison' not in run['adaptive_parameters']
    view = contract.load_json(session/run['stretch_visibility']['path'])
    assert view['origin']['source']['path'] == parent.relative_to(session).as_posix()
    control, = view['controls']
    assert control['kind'] == 'first_prefix'
    assert control['input'] == report['steps'][0]['selected']['.fit']
    draft = core.check_review(session, run['id'], None)['review']
    assert draft['stretch_assessment']['adoption_reason'] == ''
    assert len(draft['inspected_materials']) <= 32
    assert not list(session.rglob('comparison.json'))
    assert not list(session.rglob('baseline-request.json'))
    accept(session, run['id'])

    stars = recompose_assets(session, source=Path(assets['primary_output']), strength=.7)
    combined = core.run_script(session, protocol='stars.recompose', script_value=stars['script_path'],
        source_value=stars['source'], expected_values=stars['expected_outputs'],
        primary_output_value=stars['primary_output'], separation_run='060-stars', stretch_run=run['id'], timeout=60)
    assert combined['status'] == 'success', combined
    assert combined['transfer_chain'] == run['transfer_chain'] == report['transfer_chain']
    assert max(combined['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5

"""Morphology-only body routes, default prefix feedback and frozen legacy replay."""
import copy
import json
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_stepwise as stepwise
import deep_sky_siril_stretch_v2 as protected
from .test_stretch_routing_v29 import routing_case, prepare as batch_prepare, goals_for
from .test_deep_sky_siril_stretch import context
from .test_stretch_stepwise import prepare, submit, next_ghs, read
from ._policy_pins import assert_document_fingerprints_replayable, replay_and_finalize_without_migration

ROOT = Path(__file__).parents[1]
HISTORY = ROOT/'references/stretch-multistage-history.json'
PROFILES = ('emission_nebula', 'high_dynamic_range', 'galaxy_halo', 'dust_reflection', 'star_cluster')


@pytest.mark.parametrize('profile', PROFILES)
def test_every_morphology_defaults_to_prefix_feedback(routing_case, profile):
    result, goals = prepare(routing_case, profile=profile, execution=None)
    plan = read(result['strategy'])
    assert plan['schema'] == stepwise.PLAN
    assert len(goals['branches']['main']) >= 2
    assert read(result['request'])['previous'] is None


@pytest.mark.parametrize('profile', PROFILES)
def test_every_morphology_can_explicitly_run_a_complete_chain(routing_case, profile):
    result = batch_prepare(routing_case, goals_for(routing_case, profile))
    request = read(result['candidates']['profile']['request'])
    assert len(request['operators']) >= 2 and set(result['candidates']) == {'profile'}


def test_single_step_exception_and_comparison_never_create_trial_evidence(routing_case):
    goals = goals_for(routing_case)
    goals['operators'] = [{'method': 'asinh', 'purpose': 'Test a retired single-step body route', **goals.pop('phases')[0]}]
    with pytest.raises(contract.ContractError, match='at least two'):
        batch_prepare(routing_case, goals, mode='exception')
    with pytest.raises(contract.ContractError, match='retired'):
        routes.init_requests(routing_case[0], '@input', 'missing', 'missing', '072-retired', mode='compare')
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))
    assert not (routing_case[0]/'reports/071-bound').exists()


def test_outgoing_policy_references_and_implementation_are_exact(tmp_path, monkeypatch):
    prior = contract.load_json(HISTORY)
    assert prior == sessions._PRE_MULTISTAGE_BINDINGS
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    assert_document_fingerprints_replayable(prior['documents'])
    replay_and_finalize_without_migration(tmp_path, monkeypatch, HISTORY)


def test_retired_policy_still_accepts_its_original_single_step_semantics():
    prior = sessions._PRE_MULTISTAGE_BINDINGS
    routes.validate_body_chain(prior, [{'command': 'mtf', 'parameters': ['0', '.1', '1']}])
    with pytest.raises(contract.ContractError, match='at least two'):
        routes.validate_body_chain({'execution_policy': contract.EXECUTION_POLICY}, [{}])


def test_direct_protected_solver_cannot_bypass_the_body_rule(routing_case):
    root, source, payload, path, request, regions = routing_case
    with pytest.raises(contract.ContractError, match='at least two'):
        protected.solve(root, payload, request, source, source, None, regions, path, '071-direct', float('inf'))
    assert not (root/'reports/071-direct').exists()


def test_explicit_batch_cli_and_mutually_exclusive_execution(routing_case, monkeypatch, capsys):
    result = batch_prepare(routing_case)
    root, _, _, _, request, _ = routing_case
    plan = read(result['strategy'])
    args = ['stretch', 'preflight', '--session', str(root), '--source', request['source']['path'],
        '--regions', request['regions']['path'], '--goals', str(root/plan['goals']['path']),
        '--run-id', '072-batch', '--batch']
    monkeypatch.setattr('sys.argv', args)
    assert stretch.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ready'
    monkeypatch.setattr('sys.argv', args+['--stepwise'])
    with pytest.raises(SystemExit):
        stretch.main()


def test_hoo_dispatch_defaults_to_batch_and_rejects_explicit_stepwise(routing_case, monkeypatch):
    root, _, _, _, request, _ = routing_case
    path = root/'reports/hoo-dispatch.json'
    contract.atomic_write_json(path, {'channels': {'rgb': [], 'ha': [], 'oiii': []}})
    monkeypatch.setattr(routes, 'init_bound_requests', lambda *args, **kwargs: {'execution': 'batch'})
    assert routes.init_requests(root, '@input', request['regions']['path'], path, '071-hoo') == {'execution': 'batch'}
    with pytest.raises(contract.ContractError, match='dedicated batch'):
        routes.init_requests(root, '@input', request['regions']['path'], path, '072-hoo', stepwise=True)


@pytest.mark.parametrize('execution', ['stepwise', 'compare'])
def test_outgoing_single_step_parameters_replay_without_rewriting(routing_case, monkeypatch, execution):
    root, _, payload, *_ = routing_case
    prior = sessions._PRE_MULTISTAGE_BINDINGS
    payload['execution_policy'] = copy.deepcopy(prior['execution_policy'])
    contract.atomic_write_json(root/'session.json', payload)
    with monkeypatch.context() as old:
        old.setattr(sessions, 'EXECUTION_POLICY', copy.deepcopy(prior['execution_policy']))
        old.setattr(contract, 'EXECUTION_POLICY', copy.deepcopy(prior['execution_policy']))
        old.setattr(protected, 'implementation', lambda *args: copy.deepcopy(prior['implementation']))
        if execution == 'stepwise':
            prepared, _ = prepare(routing_case)
            first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
            final = stepwise.finish(root, submit(root, first, action='finish', name='072-old-finish'), '072-old')
            parameters = Path(final['parameters'])
        else:
            goals = goals_for(routing_case)
            goals['baseline_factors'] = {'stretch': 2.}
            prepared = batch_prepare(routing_case, goals, mode='compare')
            baseline = prepared['candidates']['baseline']
            result = stretch.solve_stretch_parameters(root, baseline['request'], baseline['run_id'])
            parameters = Path(result['parameters_path'])
    frozen = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    report, _, _ = stretch.load_parameters(root, payload, parameters)
    assert len(report['transfer_chain']) == 1
    with pytest.raises(contract.ContractError, match='Historical sessions'):
        stretch.solve_stretch_parameters(root, root/'reports/unused.json', '073-no-upgrade')
    assert frozen == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}

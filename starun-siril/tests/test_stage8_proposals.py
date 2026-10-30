"""Bounded Agent proposals, native equivalence and exact historical bindings."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from . import test_deep_sky_siril as support
from .test_multiscale_competition import request_file, run_candidate
from .test_recomposition_sources_v11 import source_case, accept, generated_run, recompose_assets
from .test_starnet_linear_bridge import native
from .test_stretch_factors_v25 import propose
from .test_stretch_v22 import case
from .test_deep_sky_siril_stretch import context
from .test_stretch_profiles_v23 import prepare, strategy_context
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_session as sessions
import deep_sky_siril_core as core
import deep_sky_siril_local_contrast as local
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_stretch_profiles as strategy
import deep_sky_star_reduction as reduction

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/stage8-proposals-prior.json').read_text())
FINISH_PRIOR = json.loads((ROOT/'tests/fixtures/protected-finish-prior.json').read_text())


@pytest.fixture
def prior_profiles(tmp_path, monkeypatch):
    profiles = contract.load_json(protected.PROFILE_PATH)
    profiles['high_dynamic_range']['guidance'] = 'Evaluate masked dual HDR only after a measured single-chain conflict. Protect the final blend.'
    path = tmp_path/'historical-stretch-profiles.json'
    path.write_text(json.dumps(profiles, indent=2)+'\n')
    assert contract.fingerprint(path, include_path=False) == PRIOR['profiles']
    monkeypatch.setattr(protected, 'PROFILE_PATH', path)


def proposal():
    return {'schema': local.PROPOSAL_REQUEST, 'source': 'artifacts/parent.fit',
            'regions': 'reports/regions.json',
            'mask': dict(low_start=.03, low_end=.10, high_start=.65, high_end=.90, sigma=2),
            'wavelet': {'amplitude': .8, 'fine_detail': True}, 'clahe': {'strength': .13},
            'manual_lookup': {'status': 'not_needed', 'reason': 'Complete native recipe', 'evidence': []}}


def proposed_file(session, parent, *, fine=True):
    path = request_file(session, parent)
    request = core.load_json(path)
    request.update(schema=local.PROPOSAL_REQUEST, wavelet={'amplitude': .8, 'fine_detail': fine},
                   clahe={'strength': .13})
    core.atomic_write_json(path, request)
    return path


@pytest.mark.parametrize('amplitude', [.01, .27, .8, 1.5])
@pytest.mark.parametrize('fine', [False, True])
def test_proposal_builds_only_two_bounded_candidates(amplitude, fine):
    request = proposal()
    request['wavelet'] = {'amplitude': amplitude, 'fine_detail': fine}
    choices = local.request_candidates(request)
    assert list(choices) == ['wavelet', 'clahe']
    expected = [1+.02*amplitude, 1+.04*amplitude] if fine else [1., 1.]
    expected += [1+.12*amplitude, 1+.08*amplitude, 1., 1.]
    assert choices['wavelet']['coefficients'] == expected
    assert choices['clahe']['strength'] == .13
    assert choices['clahe']['cliplimit'] == 1.5 and choices['clahe']['grid'] == 8


@pytest.mark.parametrize('section,key,value', [
    *[('wavelet', 'amplitude', value) for value in (0, -.1, 1.5001, True, '0.8', float('nan'), float('inf'))],
    *[('wavelet', 'fine_detail', value) for value in (0, 1, 'false', None)],
    *[('clahe', 'strength', value) for value in (0, -.1, .5001, True, '.2', float('nan'), float('inf'))],
    ('wavelet', 'coefficients', [1]*6), ('clahe', 'cliplimit', 1.5), ('clahe', 'grid', 8),
    ('mask', 'sigma', 3), ('mask', 'strength', .2),
])
def test_invalid_proposal_fields_are_rejected(section, key, value):
    request = proposal()
    request[section][key] = value
    with pytest.raises(contract.ContractError):
        local.request_candidates(request)


@pytest.mark.parametrize('strength', [.01, .5])
def test_clahe_strength_boundaries(strength):
    request = proposal(); request['clahe']['strength'] = strength
    assert local.request_candidates(request)['clahe']['strength'] == strength


@pytest.mark.parametrize('index', [4, 5])
def test_coarse_coefficients_must_remain_exactly_one(index):
    parameters = local.request_candidates(proposal())['wavelet']
    parameters['coefficients'][index] = 1+1e-13
    with pytest.raises(contract.ContractError, match='fifth detail and residual unchanged'):
        local.variant_parameters(parameters)


@pytest.mark.parametrize('policy_name', ['_MULTISCALE_EXECUTION_POLICY', '_STARLESS_STRETCH_EXECUTION_POLICY'])
def test_historical_policies_cannot_execute_new_proposals(tmp_path, monkeypatch, policy_name):
    import deep_sky_siril_session as sessions
    payload = {'execution_policy': getattr(sessions, policy_name)}
    monkeypatch.setattr(sessions, 'load_session', lambda *args: (tmp_path, payload, {}))
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda *args: payload)
    reports = tmp_path/'reports'; reports.mkdir()
    path = reports/'request.json'; path.write_text(json.dumps(proposal()))
    before = path.read_bytes()
    with pytest.raises(contract.ContractError, match='current frozen execution policy'):
        local.solve(tmp_path, path, '080-historical')
    assert list(reports.iterdir()) == [path] and path.read_bytes() == before


def test_invalid_requests_never_create_trial_evidence(source_case, monkeypatch):
    session, parent, *_ = source_case
    path = proposed_file(session, parent)
    request = core.load_json(path); request['clahe']['strength'] = .6
    core.atomic_write_json(path, request)
    import deep_sky_siril_dualband as dual
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid proposals must not run Siril')
    monkeypatch.setattr(dual, '_native', forbidden)
    with pytest.raises(contract.ContractError):
        local.solve(session, path, '080-invalid')
    assert not (session/'reports/080-invalid').exists()


@pytest.mark.parametrize('candidate,fine', [('wavelet', True), ('wavelet', False), ('clahe', False)])
def test_native_proposals_review_and_replay(source_case, candidate, fine):
    session, parent, *_ = source_case
    path = proposed_file(session, parent, fine=fine)
    result = local.solve(session, path, '080-proposal', timeout=60)
    report = local.load_parameters(session, result['parameters'])
    assert set(report['candidates']) == {'wavelet', 'clahe'}
    assert report['bindings']['helper'] == {'path': 'scripts/deep_sky_siril_local_contrast.py',
        **contract.fingerprint(ROOT/'scripts/deep_sky_siril_local_contrast.py', include_path=False)}
    assets = local.generate(session, result['parameters'], candidate)
    assert run_candidate(session, assets, validate_only=True)['status'] == 'success'
    run = run_candidate(session, assets)
    assert run['status'] == 'success', run
    checks = run['processing_checks']['local_contrast']['native']
    assert checks['positive_common_gain'] and max(checks['max_errors'].values()) < 2e-6
    before = core.artifacts.read_scientific_pixels(parent)[0]
    after = core.artifacts.read_scientific_pixels(Path(assets['primary_output']))[0]
    for channel in (0, 2):
        np.testing.assert_allclose(after[channel]/after[1], before[channel]/before[1], rtol=2e-6, atol=2e-6)
    accept(session, run['id'])
    frozen = support._session_bytes(session)
    assert run_candidate(session, assets)['replayed']
    assert support._session_bytes(session) == frozen
    report_path = Path(result['parameters'])
    report['candidates'][candidate]['parameters']['coefficients' if candidate == 'wavelet' else 'strength'] = (
        [1]*6 if candidate == 'wavelet' else .2)
    core.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='trial parameters changed'):
        local.load_parameters(session, report_path)


@pytest.mark.parametrize("schema", [local.PROPOSAL_REQUEST, local.PROTECTED_REQUEST])
def test_proposed_fine_off_requires_one_rejection_and_keeps_original_amplitude(source_case, schema):
    session, parent, *_ = source_case
    path = proposed_file(session, parent)
    request = core.load_json(path); request['schema'] = schema; core.atomic_write_json(path, request)
    report = local.solve(session, path, '080-proposal', timeout=60)['parameters']
    with pytest.raises(contract.ContractError):
        local.solve(session, path, '081-no-review', fallback_from=report, reason='High-frequency background rejected')
    assert not (session/'reports/081-no-review').exists()
    assets = local.generate(session, report, 'wavelet'); run = run_candidate(session, assets)
    draft = core.check_review(session, run['id'], None)['review']
    draft.update(verdict='reject', gates={key: 'uncertain' for key in draft['gates']},
                 notes='Synthetic fixture: background high-frequency amplification rejects the wavelet candidate.')
    draft['gates']['background'] = 'fail'
    review = session/'reviews'/f'{run["id"]}.json'; core.atomic_write_json(review, draft)
    assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    changed = core.load_json(path); changed['wavelet']['amplitude'] = .9
    altered = session/'reports/altered-request.json'; core.atomic_write_json(altered, changed)
    with pytest.raises(contract.ContractError, match='original mask and proposed parameters'):
        local.solve(session, altered, '081-changed', fallback_from=report, reason='High-frequency background rejected')
    result = local.solve(session, path, '082-fine-off', fallback_from=report,
                         reason='Inspected high-frequency background rejects fine detail', timeout=60)
    fine = local.load_parameters(session, result['parameters'])
    assert set(fine['candidates']) == {'fine-off'}
    assert fine['candidates']['fine-off']['parameters']['coefficients'] == [1., 1., 1+.12*.8, 1+.08*.8, 1., 1.]
    assets = local.generate(session, result['parameters'], 'fine-off')
    assert run_candidate(session, assets)['status'] == 'success'
    with pytest.raises(contract.ContractError, match='single fine-off'):
        local.solve(session, path, '083-repeat', fallback_from=report, reason='Repeated')


def test_initial_fine_off_cannot_be_revised_again(source_case):
    session, parent, *_ = source_case
    path = proposed_file(session, parent, fine=False)
    report = local.solve(session, path, '080-fine-off', timeout=60)['parameters']
    with pytest.raises(contract.ContractError, match='already disabled'):
        local.solve(session, path, '081-repeat', fallback_from=report, reason='Repeated')
    assert not (session/'reports/081-repeat').exists()


@pytest.mark.parametrize("proposal_request", [False, True])
def test_current_policy_rejects_mixed_helpers_and_preserves_replay_and_finalize(source_case, monkeypatch, proposal_request):
    session, parent, original, *_ = source_case
    path = proposed_file(session, parent) if proposal_request else request_file(session, parent)
    result = local.solve(session, path, '080-legacy', timeout=60)
    report_path = Path(result['parameters']); report = core.load_json(report_path)
    old_helper = FINISH_PRIOR['helper'] if proposal_request else PRIOR['helper']
    assert {k: report['bindings']['helper'][k] for k in ('sha256', 'size')} != old_helper
    report['bindings']['helper'] = {'path': 'scripts/deep_sky_siril_local_contrast.py', **old_helper}
    core.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='helper'):
        local.load_parameters(session, report_path)
    report['bindings']['helper'] = {'path':'scripts/deep_sky_siril_local_contrast.py',
        **contract.fingerprint(ROOT/'scripts/deep_sky_siril_local_contrast.py',include_path=False)}
    core.atomic_write_json(report_path, report)
    local.load_parameters(session, report_path)
    assets = local.generate(session, report_path, 'wavelet' if proposal_request else 'wavelet-100')
    run = run_candidate(session, assets); assert run['status'] == 'success'
    accept(session, run['id'])
    frozen = support._session_bytes(session)
    assert run_candidate(session, assets)['replayed']
    assert support._session_bytes(session) == frozen
    stars = recompose_assets(session, source=Path(assets['primary_output']), strength=.7)
    assert generated_run(session, stars)['status'] == 'success'
    accept(session, '090-recompose')
    execute = core.subprocess.run
    inspection, direct, diagnostic = support._input_inspect_run(session, original, monkeypatch)
    support._review(session, inspection['id'], diagnostic, parent_preview=direct)
    monkeypatch.setattr(core.subprocess, 'run', execute)
    script, candidate = support._delivery_script(session, Path(stars['primary_output']))
    delivery = core.run_script(session, protocol='delivery.render', script_value=script, source_value=stars['primary_output'],
        expected_values=[candidate, candidate.with_suffix('.fit')], timeout=30)
    assert delivery['status'] == 'success'
    review = accept(session, delivery['id'])
    selection = support._selection(session, delivery['id'], candidate, review)
    selected = core.load_json(selection)
    selected['selected_runs'] = ['060-stars', '070-stretch', run['id'], '090-recompose', delivery['id']]
    selected['review_receipts'] = [f'reviews/{name}.json' for name in selected['selected_runs']]
    core.atomic_write_json(selection, selected)
    immutable = {p: p.read_bytes() for p in [report_path, session/'session.json',
        *list((session/'runs').glob('*.json')), *list((session/'reviews').glob('*.json'))]}
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert all(p.read_bytes() == data for p, data in immutable.items())


def test_proposal_rejects_legacy_or_unknown_helpers(source_case):
    session, parent, *_ = source_case
    path = proposed_file(session, parent)
    result = local.solve(session, path, '080-proposal', timeout=60)
    report_path = Path(result['parameters']); report = core.load_json(report_path)
    report['bindings']['helper'] = {'path': 'scripts/deep_sky_siril_local_contrast.py', **PRIOR['helper']}
    core.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='binding changed: helper'):
        local.load_parameters(session, report_path)
    report['bindings']['helper']['sha256'] = '0'*64; core.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='binding changed: helper'):
        local.load_parameters(session, report_path)


def test_exact_prior_stretch_bindings_replay_and_reject_mixed_sets(case, monkeypatch, prior_profiles):
    session, _, payload, path, request, _ = case
    propose(path, request)
    payload['execution_policy'] = copy.deepcopy(PRIOR['execution_policy'])
    core.atomic_write_json(session/'session.json', payload)
    # Reconstruct an old solve under its frozen policy; production remains replay-only.
    with monkeypatch.context() as old:
        old.setattr(sessions, 'execution_policy_is_current', lambda _: True)
        old.setattr(protected, 'implementation', lambda *args: copy.deepcopy(PRIOR['implementation']))
        result = stretch.solve_stretch_parameters(session, path, '071-prior')
    report_path = Path(result['parameters_path']); report = core.load_json(report_path)
    report['bindings']['implementation'] = copy.deepcopy(PRIOR['implementation'])
    core.atomic_write_json(report_path, report)
    before = report_path.read_bytes()
    stretch.load_parameters(session, payload, report_path)
    assert report_path.read_bytes() == before
    assert protected.implementation_matches(session, PRIOR['implementation'])
    report['bindings']['implementation']['deep_sky_siril_local_contrast.py'] = protected.implementation()['deep_sky_siril_local_contrast.py']
    core.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='implementation'):
        stretch.load_parameters(session, payload, report_path)


def test_exact_prior_strategy_bindings_remain_immutable(strategy_context, monkeypatch, prior_profiles):
    session, _, payload, *_ = strategy_context
    payload['execution_policy'] = copy.deepcopy(PRIOR['execution_policy'])
    core.atomic_write_json(session/'session.json', payload)
    with monkeypatch.context() as old:
        from . import _stretch_factory
        old.setattr(_stretch_factory, 'with_morphology', lambda session, source, regions, goals: goals)
        old.setattr(sessions, 'execution_policy_is_current', lambda _: True)
        old.setattr(protected, 'implementation', lambda *args: copy.deepcopy(PRIOR['implementation']))
        result, _ = prepare(strategy_context)
    path = Path(result['strategy']); before = path.read_bytes()
    strategy.load_plan(session, payload, stretch.record(session, path))
    assert path.read_bytes() == before


def test_outgoing_bindings_and_document_fingerprints_are_exact():
    import deep_sky_siril_session as sessions
    # prior=stage8-proposals-prior.json: 13 个域标签
    assert_contract_domains(PRIOR['execution_policy'])
    assert local.LEGACY_HELPER == PRIOR['helper']
    assert protected._PRE_STAGE8_BINDINGS == {k: PRIOR[k] for k in ('execution_policy', 'implementation', 'profiles')}
    for name, record in PRIOR['documents'].items():
        assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]

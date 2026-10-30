"""Initial morphology-selected HDR, native protection and receipt-bound revisions."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_v2 as protected
from . import test_deep_sky_siril as support
from ._policy_pins import replay_and_finalize_without_migration
from .test_hdr_stretch import stretch_text
from .test_deep_sky_siril_stretch import context
from .test_starnet_linear_bridge import native
from .test_recomposition_sources_v11 import linear_case, accept
from .test_stretch_routing_v29 import routing_case, goals_for, prepare
from .test_stretch_v22 import field_request

PROFILES = ('emission_nebula', 'high_dynamic_range', 'galaxy_halo', 'dust_reflection', 'star_cluster')
HISTORY = Path(__file__).parents[1]/'references/stretch-hdr-history.json'


def hdr_goals(case, profile='high_dynamic_range'):
    goals = goals_for(case, profile)
    goals.pop('phases')
    goals['operators'] = [{'method': 'mtf', 'purpose': 'Declare the protected synthetic HDR display goal.',
                          'factors': {'midtones': .2}, 'constraints': []}]
    goals['hdr'] = {'gentle': [{'command': 'mtf', 'parameters': ['0', '0.2', '1']}],
                    'faint': [{'command': 'mtf', 'parameters': ['0', '0.06', '1']}],
                    'mask': {'mask_low': .15, 'mask_high': .75, 'sigma': 2}}
    return goals


@pytest.mark.parametrize('profile', PROFILES)
def test_initial_hdr_keeps_each_morphology_without_a_prior_trial(routing_case, profile):
    root, source, payload, *_ = routing_case
    prepared = prepare(routing_case, hdr_goals(routing_case, profile), mode='exception')
    binding = stretch.record(root, Path(prepared['strategy']))
    plan, requests = routes.load_plan(root, payload, binding)
    goals = contract.load_json(root/plan['goals']['path'])
    assert goals['profile'] == profile and goals['revision'] is None
    assert plan['root'] is None
    preflight = contract.load_json(root/plan['constraint_preflight']['path'])
    assert preflight['output_reachability'] == 'not_predicted' and not preflight['curve_effects']
    assert {row['input']['reason'] for row in preflight['constraints']} == {'The native masked HDR blend has not run'}
    assert not list(root.glob('reports/*/scientific-attempt.json'))
    assert requests['exception']['targets'] == routing_case[4]['targets']
    with pytest.raises(contract.ContractError, match='not ordinary solve'):
        item = prepared['candidates']['exception']
        stretch.solve_stretch_parameters(root, item['request'], item['run_id'])
    assert not list(root.glob('reports/*/scientific-attempt.json'))


@pytest.mark.parametrize('fault', ['empty', 'command', 'parameters', 'mask', 'protection', 'preview', 'morphology'])
def test_initial_hdr_cannot_bypass_existing_boundaries(routing_case, fault):
    goals = hdr_goals(routing_case)
    if fault == 'empty': goals['hdr']['gentle'] = []
    elif fault == 'command': goals['hdr']['faint'][0]['command'] = 'autostretch'
    elif fault == 'parameters': goals['hdr']['gentle'][0]['parameters'] = ['0', '1', '1']
    elif fault == 'mask': goals['hdr']['mask']['mask_high'] = .1
    elif fault == 'protection': goals['protection'] = [r for r in goals['protection'] if r['region'] != 'core']
    elif fault == 'preview': goals['evidence'] = [routing_case[4]['source']]
    else: goals['profile'] = 'custom'
    with pytest.raises(contract.ContractError):
        prepare(routing_case, goals, mode='exception')
    assert not list(routing_case[0].glob('reports/*/scientific-attempt.json'))


def native_hdr(linear_case, profile, *, fail_native=False, monkeypatch=None, full_stars=False):
    root, _, _, source, _, _, separation = linear_case
    _, request = field_request(root, source)
    if full_stars:
        source = linear_case[2]
        script = root/'scripts/010-input.ssf'
        direct, view = [root/'previews'/name for name in ('010-direct.jpg', '010-reference.jpg')]
        script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\n'
            f'savejpg "{direct.with_suffix("")}" 95\nautostretch -linked -2.8 0.22\n'
            f'savejpg "{view.with_suffix("")}" 95\nclose\n')
        support._write_ssf_provenance(root, script, 'input.inspect')
        inspected = core.run_script(root, protocol='input.inspect', script_value=script,
            source_value=source, expected_values=[direct, view], timeout=60)
        assert inspected['status'] == 'success', inspected
        support._review(root, inspected['id'], view, parent_preview=direct)
        path = root/request['regions']['path']
        regions = contract.load_json(path)
        regions['reference']['sha256'] = contract.sha256_file(source)
        contract.atomic_write_json(path, regions)
        request['source'] = {'path': '@input', 'sha256': contract.sha256_file(source)}
        request['regions'] = stretch.record(root, path)
    regions = contract.load_json(root/request['regions']['path'])['regions']
    case = (root, source, contract.load_json(root/'session.json'), None, request, regions)
    prepared = prepare(case, hdr_goals(case, profile), '069-initial-hdr', 'exception')
    binding = stretch.record(root, Path(prepared['strategy']))
    plan, _ = routes.load_plan(root, case[2], binding)
    goals = contract.load_json(root/plan['goals']['path'])
    full = None if full_stars else separation['expected_values'][0]
    guard = {key: request[key] for key in ('source', 'regions', 'targets', 'core_absence')}
    guard.update(schema='starun-siril.stretch-protection.v2', profile=profile,
                 full_source=stretch.record(root, full) if full else None, constraints=goals['protection'])
    guard_path = root/'reports/initial-hdr-protection.json'
    contract.atomic_write_json(guard_path, guard)
    text, primary, outputs = stretch_text(root, source, 3)
    text = ('# stretch-strategy: '+json.dumps(binding)+'\n# stretch-protection: '+
            json.dumps(stretch.record(root, guard_path))+'\n'+text)
    layers = [root/'artifacts'/('initial-full-'+name+'.fit') for name in ('gentle', 'faint', 'mixed')]
    view = root/'previews/initial-full.jpg'
    for role, output in zip(('gentle', 'faint'), layers if full else []):
        commands = '\n'.join(' '.join([step['command'], *step['parameters']]) for step in goals['hdr'][role])
        text += f'load "{full}"\n{commands}\nsave "{output}" -chksum\nclose\n'
    if full:
        text += (f'pm "$artifacts/initial-full-gentle$ * $artifacts/070-mask-rgb$ + $artifacts/initial-full-faint$ * (1 - $artifacts/070-mask-rgb$)" -nosum\n'
                 f'save "{layers[2]}" -chksum\nicc_assign sRGB\nsavejpg "{view.with_suffix("")}" 95\nclose\n')
        outputs += [*layers, view]
    script = root/'scripts/070-initial-hdr.ssf'
    script.write_text(text)
    support._write_ssf_provenance(root, script, 'stretch')
    if fail_native:
        monkeypatch.setattr(core.subprocess, 'run', lambda argv, **kwargs:
            subprocess.CompletedProcess(argv, 1, 'Error: synthetic native failure before writing outputs.\n'))
    result = core.run_script(root, protocol='stretch', script_value=script, source_value=source,
        primary_output_value=primary, expected_values=outputs, timeout=60)
    assert result['status'] == ('failed' if fail_native else 'success'), result
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    return case, goals, result


@pytest.mark.parametrize('profile', PROFILES)
def test_native_initial_hdr_protects_all_five_profiles(linear_case, profile):
    case, goals, result = native_hdr(linear_case, profile)
    root = case[0]
    assert result['processing_checks']['hdr']['protection']['candidate']['eligible']
    assert max(result['processing_checks']['hdr']['blend_max_abs']) <= 1e-6
    assert result['hdr_transfer']['gentle_chain'] == goals['hdr']['gentle']
    assert result['hdr_transfer']['faint_chain'] == goals['hdr']['faint']
    assert result['trial_artifacts']
    accept(root, result['id'])
    assert core.check_review(root, result['id'], root/'reviews'/(result['id']+'.json'))['status'] == 'review_valid'


def test_native_full_stars_hdr_retains_cluster_protection(linear_case):
    case, _, result = native_hdr(linear_case, 'star_cluster', full_stars=True)
    assert result['hdr_transfer']['protection']['full_source'] is None
    audit = result['processing_checks']['hdr']['protection']['candidate']
    assert audit['eligible'] and any(row['region'] == 'stars' for row in audit['constraints'])


def test_solved_single_chain_does_not_require_a_conflict_before_hdr(routing_case):
    root, *_ = routing_case
    first = prepare(routing_case)['candidates']['profile']
    solved = stretch.solve_stretch_parameters(root, first['request'], first['run_id'])
    assert solved['status'] == 'solved'
    goals = hdr_goals(routing_case)
    goals['revision'] = {'parameters': stretch.record(root, Path(solved['parameters_path'])),
        'affected_regions': ['core', 'faint'], 'reason': 'Choose HDR from the observed morphology and retained native structure.',
        'evidence': [solved['report']['steps'][0]['local_panel']], 'target_change': None}
    prepared = prepare(routing_case, goals, '072-selected-hdr', 'exception')
    plan, _ = routes.load_plan(root, routing_case[2], stretch.record(root, Path(prepared['strategy'])))
    assert plan['revision_diff']['route_changed']
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


@pytest.mark.parametrize('failed', [False, True])
def test_hdr_revision_uses_native_receipt_and_preserves_attempt_budget(linear_case, monkeypatch, failed):
    case, goals, result = native_hdr(linear_case, 'high_dynamic_range', fail_native=failed, monkeypatch=monkeypatch)
    root = case[0]
    receipt = root/'runs'/(result['id']+'.json')
    frozen = receipt.read_bytes()
    goals = copy.deepcopy(goals)
    goals['hdr']['mask']['sigma'] = 3
    goals['revision'] = {'parameters': stretch.record(root, receipt), 'affected_regions': ['core', 'shell'],
        'reason': 'Revise the synthetic core-to-shell transition using the actual HDR run evidence.',
        'evidence': [result['log']], 'target_change': None}
    prepared = prepare(case, goals, '072-hdr-revision', 'exception')
    binding = stretch.record(root, Path(prepared['strategy']))
    plan, _ = routes.load_plan(root, case[2], binding)
    assert plan['root'] == result['hdr_transfer']['strategy']
    assert plan['revision_diff']['parameters_changed']
    assert not plan['revision_diff']['same_declared_transform']
    unchanged = copy.deepcopy(goals)
    unchanged['hdr']['mask']['sigma'] = 2
    unchanged['operators'][0]['factors']['midtones'] = .3
    other = prepare(case, unchanged, '072-hdr-same-native', 'exception')
    diff = contract.load_json(Path(other['strategy']))['revision_diff']
    assert not diff['parameters_changed'] and diff['same_declared_transform']
    for run_id in ('072-hdr-revision', '073-hdr-last'):
        routes.claim_attempt(root, case[2], run_id, {'source': plan['source'], 'strategy': binding})
    with pytest.raises(contract.ContractError, match='three shared'):
        routes.claim_attempt(root, case[2], '074-hdr-extra', {'source': plan['source'], 'strategy': binding})
    assert receipt.read_bytes() == frozen
    goals['revision']['parameters']['sha256'] = '0'*64
    with pytest.raises(contract.ContractError, match='hash changed'):
        prepare(case, goals, '075-hdr-forged', 'exception')


def test_previous_hdr_policy_and_evidence_remain_replay_only(tmp_path, monkeypatch):
    prior = contract.load_json(HISTORY)
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': prior['execution_policy']})
    assert protected.implementation(tmp_path) == prior['implementation']
    assert protected.profiles_fingerprint(tmp_path) == prior['profiles']
    for name in ('references/stages/stage-07-stretch.md', 'references/stage7-hdr.md'):
        record = {'path': name, **prior['documents'][name]}
        assert sessions.registered_guidance_reference(prior, record)
        assert not sessions.registered_guidance_reference({'execution_policy': contract.EXECUTION_POLICY}, record)
    replay_and_finalize_without_migration(tmp_path/'old-session', monkeypatch, HISTORY)

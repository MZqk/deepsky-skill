"""Starless subject eligibility and immutable full-replay diagnostics."""
from ._stretch_factory import solve_bound, historical_parameters

import copy
import json
from pathlib import Path
import time

import numpy as np
import pytest

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError, fingerprint, sha256_file
from ._policy_pins import assert_contract_domains
from .test_deep_sky_siril import _fake_probe
from .test_deep_sky_siril_stretch import context, BINARY, write
from .test_stretch_v22 import case, make_v2, trial, field_request
from .test_recomposition_sources_v11 import source_case, accept, recompose_assets
from .test_starnet_linear_bridge import native


@pytest.fixture(autouse=True)
def retired_operator_producer(monkeypatch, request):
    if request.node.originalname.startswith('test_exact_') or 'document_evidence' in request.node.originalname:
        return
    from ._policy_pins import outgoing_stretch_producer
    outgoing_stretch_producer(monkeypatch)


def test_full_diagnostics_do_not_prune_subject_or_override_its_failures():
    row = trial(100, .01, .2)
    row['full_audit'] = {'eligible': False, 'reasons': ['regional_constraints', 'new_highlight_crossing']}
    assert protected.select([row])[0] is None
    assert protected.select([row], full_diagnostic=True)[0] == 0
    row['audit']['eligible'] = False
    assert protected.select([row], full_diagnostic=True)[0] is None
    row['audit']['eligible'] = True
    row['full_audit'] = {'eligible': False, 'nonfinite_count': 1}
    assert protected.select([row], full_diagnostic=True)[0] is None


def test_stellar_rois_remain_required_but_budgets_are_optional(case):
    _, _, _, _, request, regions = case
    request['operators'][0]['constraints'] = [r for r in request['operators'][0]['constraints'] if r['region'] != 'stars']
    protected.validate(request, regions, full=True, full_diagnostic=True)
    with pytest.raises(ContractError, match='Every step'):
        protected.validate(request, regions, full=True)
    with pytest.raises(ContractError, match='stellar'):
        protected.validate(request, [r for r in regions if r['role'] != 'stars'], full=True, full_diagnostic=True)
    request['operators'][0]['constraints'] = [r for r in request['operators'][0]['constraints'] if r['region'] != 'core']
    with pytest.raises(ContractError, match='Every step'):
        protected.validate(request, regions, full=True, full_diagnostic=True)


def test_solver_keeps_failed_stellar_diagnostics_and_rejects_role_drift(case, monkeypatch):
    root, source, payload, path, request, _ = case
    full = write(root/'artifacts/full.fit', base.pixels(source)+.0001)
    monkeypatch.setattr(base, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    for row in request['operators'][0]['constraints']:
        if row['region'] == 'stars':
            row.update(min=0., max=0.)
    path.write_text(json.dumps(request))
    solved = solve_bound(root, path, '071-subject')
    assert solved['status'] == 'solved', solved['report'].get('reason')
    report, report_path, _ = base.load_parameters(root, payload, solved['parameters_path'])
    assert report['full_replay_role'] == 'diagnostic'
    step = report['steps'][0]
    assert step['prefix_audit']['eligible']
    assert not step['full_prefix_audit']['eligible']
    assert all(c['region'] != 'stars' for c in step['prefix_audit']['constraints'])
    output = base.bound_file(root, step['selected']['.fit'])
    assert protected.measure_output(root, source, output, report)['status'] == 'passed'
    assert step['selected-full']['.jpg']['path'] in protected.review_paths(root, {'parameters': base.record(root, report_path)})
    report.pop('full_replay_role')
    report_path.write_text(json.dumps(report))
    with pytest.raises(ContractError, match='Full replay role'):
        base.load_parameters(root, payload, report_path)


def native_scene(context):
    if not BINARY.is_file():
        pytest.skip('Native Siril 1.4.4 unavailable')
    root, source, payload, path, request, _ = context
    regions = make_v2(root, source, request, 3)
    full = base.pixels(source).astype('float32')
    full[0, 74:78, 80:84] = np.nextafter(np.float32(1), np.float32(0))
    full[1, 74:78, 80:84] = .8
    full[2, 74:78, 80:84] = .5
    full_path = write(root/'artifacts/full.fit', full)
    payload['input'].update(fingerprint(source, include_path=False))
    (root/'session.json').write_text(json.dumps(payload))
    runtime = _fake_probe(BINARY)['tools']['siril_cli']
    (root/'reports/tool-probe.json').write_text(json.dumps(_fake_probe(BINARY)))
    return root, source, payload, path, request, regions, full_path, runtime


@pytest.mark.parametrize('strength', [1.5, 2., 3.])
def test_native_strong_ghs_preserves_subject_and_records_clipped_stellar_core(context, strength):
    root, source, payload, _, request, regions, full, runtime = native_scene(context)
    directory = root/'reports/strong-native'
    directory.mkdir()
    oracle = base.NativeTrials(root, runtime, directory, directory, time.monotonic()+60)
    tokens = base.tokens_for('ght', (strength, 1.), 0., {'LP': .02, 'SP': .1, 'HP': .35})
    parameter = {'command': tokens[0], 'parameters': tokens[1:]}
    output, evidence = oracle.run(source, [parameter])
    full_output, full_evidence = oracle.run(full, [parameter])
    constraints = protected.subject_constraints(request['operators'][0]['constraints'], regions, full_diagnostic=True)
    subject = protected.audit(base.pixels(source), base.pixels(output), regions, constraints, request['targets'])
    stars = protected.audit(base.pixels(full), base.pixels(full_output), regions, [], request['targets'], stellar_only=True)
    row = {'coordinates': [strength, 1.], 'parameters': parameter, 'audit': subject, 'full_audit': stars}
    assert subject['eligible']
    assert subject['statistics']['faint']['median'] > .17
    assert sum(r['new_highlight'] for r in stars['endpoints']) == 16
    assert 'new_highlight_crossing' in stars['reasons']
    assert protected.select([row])[0] is None
    assert protected.select([row], full_diagnostic=True)[0] == 0
    assert evidence['log']['sha256'] and full_evidence['log']['sha256']
    invalid = base.pixels(output).copy()
    invalid[0, 30, 62] = 1.
    assert not protected.audit(base.pixels(source), invalid, regions, constraints, request['targets'])['eligible']


def test_native_solver_selects_subject_despite_full_highlight_failures(context, monkeypatch):
    root, source, payload, path, request, regions, full, _ = native_scene(context)
    monkeypatch.setattr(base, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    step = request['operators'][0]
    step.update(method='ght', factors={'D':2.,'B':1.}, landmarks={k: {'region': r, 'statistic': s} for k, r, s in
        [('LP', 'sky', 'p99'), ('SP', 'faint', 'median'), ('HP', 'core', 'p01')]})
    for row in step['constraints']:
        if row['region'] == 'faint':
            row.update(min=.18, max=.5)
    path.write_text(json.dumps(request))
    solved = solve_bound(root, path, '071-strong-subject', timeout=180)
    assert solved['status'] == 'solved', solved['report'].get('reason')
    report, report_path, _ = base.load_parameters(root, payload, solved['parameters_path'])
    trace = report['steps'][0]
    assert trace['prefix_audit']['eligible'] and not trace['full_prefix_audit']['eligible']
    assert any(1.5 <= r['coordinates'][0] <= 3. and r['audit']['eligible'] and not r['full_audit']['eligible'] for r in trace['trials'])
    assert trace['evaluation_count'] == 1
    output = base.bound_file(root, trace['selected']['.fit'])
    assert protected.measure_output(root, source, output, report)['status'] == 'passed'
    base.bound_file(root, trace['selected-full']['.jpg']).write_bytes(b'drift')
    with pytest.raises(ContractError, match='hash changed'):
        base.load_parameters(root, payload, report_path)


def test_hdr_subject_scope_keeps_finite_full_diagnostics_and_old_semantics(case):
    root, source, payload, _, request, regions = case
    full_data = base.pixels(source).copy()
    full = write(root/'artifacts/full.fit', full_data)
    full_data[:, 76, 82] = 1.
    control = write(root/'artifacts/full-control.fit', full_data)
    protection = root/'reports/protection.json'
    protection.write_text(json.dumps({'constraints': request['operators'][0]['constraints'], 'targets': request['targets']}))
    binding = {'request': base.record(root, protection), 'regions': request['regions'], 'full_source': base.record(root, full),
               'implementation': protected.implementation(root), 'profiles': protected.profiles_fingerprint(root)}
    checked = protected.measure_protection(root, source, source, binding, control)
    assert checked['candidate']['eligible'] and not checked['full_replay']['eligible']
    assert checked['full_replay_role'] == 'diagnostic'
    payload['execution_policy'] = copy.deepcopy(sessions._PROFILE_STRETCH_EXECUTION_POLICY)
    (root/'session.json').write_text(json.dumps(payload))
    binding.update(implementation=protected.implementation(root), profiles=protected.profiles_fingerprint(root))
    with pytest.raises(ContractError, match='stellar protection'):
        protected.measure_protection(root, source, source, binding, control)


def test_unbound_derived_source_requires_its_hoo_attempt(case):
    root, source, payload, path, request, _ = case
    derived = write(root/'reports/ha.fit', base.pixels(source))
    path.write_text(json.dumps(request))
    with pytest.raises(ContractError, match='bound HOO scientific attempt'):
        solve_bound(root, path, '071-derived', _derived_source=derived)
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_exact_v23_history_bindings_and_command_policy(case, monkeypatch):
    root, _, payload, path, _, _ = case
    frozen = json.loads((Path(__file__).parents[1]/'tests/fixtures/stretch-policy-v23.json').read_text())
    assert sessions._PROFILE_STRETCH_EXECUTION_POLICY == frozen['execution_policy']
    # prior=stretch-policy-v23.json: 15 个域标签
    assert_contract_domains(frozen['execution_policy'])
    assert sessions.known_execution_policy(frozen['execution_policy'])
    current_commands = json.loads((Path(__file__).parents[1]/'references/command-policy.json').read_text())
    for command in ('epf','split','pm','rgbcomp','icc_assign'):
        current_commands['protocol_commands']['restoration.denoise-nonlinear'].remove(command)
    for command in ('split', 'pm', 'gauss', 'rgbcomp'):
        current_commands['protocol_commands']['tone.adjust'].remove(command)
    current_commands['protocol_commands']['restoration.denoise'].remove('pyscript')
    assert current_commands['pyscript_protocols'].pop('restoration.denoise') == 'siril_deepsnr.py'
    current_commands['protocol_commands']['color.finish'].remove('fill')
    current_commands['protocol_commands']['color.finish'].remove('invght')
    current_commands['protocol_commands']['stars.recompose'].remove('rgbcomp')
    current_commands['protocol_commands']['stars.recompose'].remove('update_key')
    current_commands['protocol_commands']['stars.recompose'].remove('modasinh')
    assert current_commands == json.loads(frozen['command_policy'])
    with monkeypatch.context() as old:
        from ._policy_pins import outgoing_stretch_producer
        outgoing_stretch_producer(old)
        old.setitem(payload, 'execution_policy', copy.deepcopy(sessions._PRE_MULTISTAGE_BINDINGS['execution_policy']))
        (root/'session.json').write_text(json.dumps(payload))
        old.setattr(protected, 'noise_is_diagnostic', lambda _: False)
        old.setattr(base, 'optimization_enabled', lambda _: False)
        solved = solve_bound(root, path, '071-old-bindings')
    (root/'session.json').write_text(json.dumps(payload))
    report_path, report = historical_parameters(root, solved, frozen)
    payload['execution_policy'] = frozen['execution_policy']
    (root/'session.json').write_text(json.dumps(payload))
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    base.load_parameters(root, payload, report_path)
    assert all(p.read_bytes() == value for p, value in before.items())
    with pytest.raises(ContractError, match='replay/finalize-only'):
        base.solve_stretch_parameters(root, path, '072-old-solve')
    import deep_sky_siril_stretch_profiles as profiles
    with pytest.raises(ContractError, match='replay/finalize-only'):
        profiles.compare(root, path, [])
    with pytest.raises(ContractError, match='replay/finalize-only'):
        profiles.init_requests(root, '@input', 'missing', 'missing', '072-old-request', search=True, stepwise=False)


def test_v23_document_evidence_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    replay_and_finalize_without_migration(
        tmp_path, monkeypatch, Path(__file__).parents[1] / 'tests/fixtures/stretch-policy-v23.json')


def test_native_review_and_stage9_residual_stay_bound(linear_case):
    import deep_sky_siril_core as core
    import deep_sky_star_reduction as reduction
    session, _, _, parent, *_ = linear_case
    path, request = field_request(session, parent, name='071-subject')
    for row in request['operators'][0]['constraints']:
        if row['region'] == 'stars':
            row.update(min=0., max=0.)
    path.write_text(json.dumps(request))
    solved = solve_bound(session, path, '071-subject', timeout=180)
    assert solved['status'] == 'solved', solved['report'].get('reason')
    assets = base.generate_stretch_assets(session, parameters=solved['parameters_path'],run_id='071-subject')
    run = core.run_script(session, protocol='stretch', script_value=assets['script_path'], source_value=parent,
        expected_values=[assets['primary_output'], assets['preview_output']], primary_output_value=assets['primary_output'], timeout=60)
    assert run['status'] == 'success', run
    assert run['adaptive_parameters']['bindings']['full_source']
    draft = core.load_json(accept(session, '071-subject'))
    assert any('selected-full.jpg' in row['path'] for row in draft['inspected_materials'])
    missing = copy.deepcopy(draft)
    missing['inspected_materials'] = [r for r in missing['inspected_materials'] if 'selected-full.jpg' not in r['path']]
    missing.update(verdict='accept', gates={key: 'pass' for key in missing['gates']}, notes='Synthetic missing-full diagnostic rejection')
    review = session/'reviews/missing-full.json'
    review.write_text(json.dumps(missing))
    with pytest.raises(ContractError, match='matching full replay'):
        core.check_review(session, '071-subject', review)
    review.unlink()
    accept(session, '071-subject')
    recomposed = recompose_assets(session, source=assets['primary_output'], strength=.7)
    final = core.run_script(session, protocol='stars.recompose', script_value=recomposed['script_path'], source_value=recomposed['source'],
        expected_values=recomposed['expected_outputs'], primary_output_value=recomposed['primary_output'],
        separation_run='060-stars', stretch_run='071-subject', timeout=60)
    assert final['status'] == 'success', final
    assert final['transfer_chain'] == run['transfer_chain']
    assert max(final['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5
    assert max(final['processing_checks']['closure']['per_channel_final_max_abs']) <= 1e-5

"""Immutable prefix feedback, native replay, candidate budget and HDR integration."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as routes
import deep_sky_siril_stretch_stepwise as stepwise
import deep_sky_siril_stretch_v2 as protected
from .test_deep_sky_siril_stretch import context
from .test_stretch_routing_v29 import routing_case, goals_for, NATIVE_RUN
from .test_deep_sky_siril_stretch import BINARY
from .test_deep_sky_siril import _fake_probe
from .test_stretch_v22 import make_v2, field_request
from .test_starnet_linear_bridge import native
from .test_recomposition_sources_v11 import linear_case, accept
import deep_sky_siril_core as core
from ._stretch_factory import with_morphology, source_evidence


def read(path):
    return contract.load_json(Path(path))


def prepare(case, *, name='071-stepwise', hdr=False, revision=None, goals=None, profile='high_dynamic_range', final_min=None, hdr_tail=False, execution=True):
    root, source, payload, _, request, _ = case
    if goals is None:
        old = goals_for(case, profile)
        definitions = [{'method': row['method'], 'purpose': row['purpose']} for row in
            read(protected.PROFILE_PATH)[profile]['preferred_chain']]
        initial = {**definitions[0], **copy.deepcopy(old['phases'][0])}
        goals = {key: copy.deepcopy(old[key]) for key in ('profile', 'reason', 'evidence', 'targets', 'core_absence', 'protection', 'manual_lookup')}
        goals['protection'] = [dict(row, min=0.) if row['statistic'] == 'median' else row for row in goals['protection']]
        goals.update(schema=stepwise.GOALS, final_constraints=[], initial=initial, revision=revision,
            branches={'gentle': definitions, 'faint': definitions} if hdr else {'main': definitions})
        if hdr_tail:
            goals['branches']['faint'] = goals['branches']['faint']+[{'method': 'mtf', 'purpose': 'Complete the synthetic final display brightness.'}]
        if final_min is not None:
            goals['final_constraints'] = [{'region': 'faint', 'statistic': 'median', 'min': final_min, 'max': 1.}]
        goals = with_morphology(root, request['source'], request['regions'], goals)
    path = root/'reports'/(name+'-goals.json')
    contract.atomic_write_json(path, goals)
    result = routes.init_requests(root, request['source']['path'], request['regions']['path'], path, name,
        stepwise=execution, mode='exception' if goals.get('tail_control') or goals.get('revision') else 'profile')
    return result, goals


def submit(root, previous, *, action='step', step=None, role=None, mask=None, name='072-next'):
    path = Path(previous['parameters']) if isinstance(previous, dict) else Path(previous)
    report = read(path)
    branch = report['proposal']['branch']
    trace = report['branches'][branch]['steps'][-1]
    regions = read(root/report['bindings']['regions']['path'])['regions']
    proposal = {'schema': stepwise.REQUEST, 'strategy': report['request']['strategy'],
        'previous': stretch.record(root, path), 'action': action, 'branch': role or branch,
        'feedback': {'materials': [stretch.record(root, path), trace['selected']['.jpg'], trace['local_panel']],
            'observations': [{'region': row['id'], 'observation': 'Inspected the actual prefix; this synthetic ROI retains the declared structure.'} for row in regions],
            'reason': 'Select the next transformation from the actual measured prefix.'}}
    from deep_sky_siril_artifact_focus import plan_for, skeleton
    plan, focus_goals, _ = plan_for(root, read(root/'session.json'), proposal['strategy'])
    proposal['feedback']['artifacts'] = skeleton(root, read(root/'session.json'), plan, focus_goals, proposal['previous'])
    artifact = proposal['feedback']['artifacts']
    if artifact['report']:
        packet = read(root/artifact['report']['path'])
        proposal['feedback']['materials'].append(packet['panel'])
        for area in artifact['areas']:
            area.update(baseline='present', change='unchanged', observation='The controlled synthetic crop retains its known texture.',
                evidence=[packet['panel']], decision_reason='The observed synthetic texture remains acceptable for this test.')
    if action == 'finish' and mask is not None:
        gentle = report['branches']['gentle']['steps'][-1]
        proposal['feedback']['materials'] += [gentle['selected']['.jpg'], gentle['local_panel']]
    if action == 'step':
        branch_steps = report['branches'][role or branch]['steps']
        proposal.update(step=step, anchor_domain=branch_steps[-1]['selected']['.fit'] if branch_steps else report['request']['source'])
    else:
        proposal['reason'] = 'The inspected subject is sufficiently visible; preserve the current structure.'
        if action == 'finish':
            proposal['mask'] = mask
    request = root/'reports'/(name+'-request.json')
    stepwise.write(request, proposal)
    return request


def next_ghs(goals, role='main'):
    return {**goals['branches'][role][1], 'factors': {'D': .2, 'B': .5}, 'constraints': [],
        'landmarks': {k: {'region': r, 'statistic': s} for k, r, s in
            [('LP', 'sky', 'p99'), ('SP', next(iter(goals['targets'])), 'median'), ('HP', 'core', 'p01')]}}


def test_future_factors_absent_and_every_step_pauses(routing_case):
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case)
    plan = read(prepared['strategy'])
    assert all(set(step) == {'method', 'purpose'} for step in goals['branches']['main'])
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    assert first['status'] == 'awaiting_next_step' and first['step'] == 1
    report, _, _ = stepwise.load_report(root, payload, first['parameters'])
    assert len(report['steps']) == 1 and len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    with pytest.raises(contract.ContractError, match='explicitly finished'):
        stretch.generate_stretch_assets(root, parameters=first['parameters'])
    proposal = submit(root, first, step=next_ghs(goals))
    second = stretch.solve_stretch_parameters(root, proposal, '072-prefix')
    assert second['status'] == 'awaiting_next_step'
    final_request = submit(root, second, action='finish', name='073-finish')
    final = stepwise.finish(root, final_request, '073-final')
    assert final['status'] == 'solved'
    report, _, _ = stretch.load_parameters(root, payload, final['parameters'])
    assert len(report['transfer_chain']) == 2 and report['steps'][0] == read(first['parameters'])['steps'][0]
    assert report['steps'][1]['anchor_domain'] == report['steps'][0]['selected']['.fit']
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    assert stepwise.status(root, payload, Path(final['parameters']))['pending'] == 'generate'
    generated = stretch.generate_stretch_assets(root, parameters=final['parameters'], run_id='074-formal')
    assert str(stretch.source_file(root, payload, plan['source'])) in Path(generated['script_path']).read_text()


def test_early_finish_and_prefix_successor_are_exclusive(routing_case):
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    request = submit(root, first, action='finish', name='072-early')
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(contract.ContractError, match='at least two'):
        stepwise.finish(root, request, '072-final')
    assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    next_request = submit(root, first, step=next_ghs(goals), name='072-next')
    second = stretch.solve_stretch_parameters(root, next_request, '072-prefix')
    request = submit(root, second, action='finish', name='073-finish')
    final = stepwise.finish(root, request, '073-final')
    report, _, _ = stretch.load_parameters(root, payload, final['parameters'])
    assert len(report['transfer_chain']) == 2
    other = submit(root, first, step=next_ghs(goals), name='074-fork')
    with pytest.raises(contract.ContractError, match='actual successor'):
        stretch.solve_stretch_parameters(root, other, '073-fork')
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


@pytest.mark.parametrize('fault', ['feedback', 'anchor_domain', 'future_factors', 'request', 'source', 'roi', 'preview', 'log', 'prefix', 'receipt'])
def test_prefix_drift_and_unreviewed_continuation_block(routing_case, fault):
    root, source, payload, *_ = routing_case
    prepared, goals = prepare(routing_case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    proposal_path = submit(root, first, step=next_ghs(goals))
    proposal = read(proposal_path)
    report = read(first['parameters'])
    if fault == 'feedback':
        proposal['feedback']['materials'] = []
    elif fault == 'anchor_domain':
        proposal['anchor_domain'] = report['request']['source']
    elif fault == 'future_factors':
        proposal['future_factors'] = {'D': 1.}
    else:
        victim = {'source': source, 'roi': root/report['bindings']['regions']['path'],
            'preview': root/report['steps'][0]['selected']['.jpg']['path'],
            'prefix': root/report['steps'][0]['selected']['.fit']['path'],
            'log': root/report['steps'][0]['selected_replay']['log']['path'],
            'request': root/report['bindings']['request']['path'], 'receipt': Path(first['parameters'])}[fault]
        victim.write_bytes(victim.read_bytes()+b'\n')
    proposal_path.write_text(json.dumps(proposal))
    with pytest.raises((contract.ContractError, ValueError)):
        stretch.solve_stretch_parameters(root, proposal_path, '072-bad')
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


@pytest.mark.parametrize('real', [False, True])
def test_hdr_explicit_branch_end_and_actual_gentle_mask_domain(routing_case, monkeypatch, real):
    root, _, payload, *_ = routing_case
    if real:
        if not BINARY.exists():
            pytest.skip('Native Siril is unavailable')
        contract.atomic_write_json(root/'reports/tool-probe.json', _fake_probe(BINARY))
        monkeypatch.setattr(stretch.NativeTrials, 'run', NATIVE_RUN)
    prepared, goals = prepare(routing_case, hdr=True)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-gentle')
    end = stepwise.finish(root, submit(root, first, action='finish_branch', name='072-end'), '072-end')
    wrong = submit(root, end, action='finish', name='073-too-early')
    with pytest.raises(contract.ContractError, match='gentle and faint'):
        stepwise.finish(root, wrong, '073-final')
    request = submit(root, end, role='faint', step=goals['initial'], name='073-faint')
    faint = stretch.solve_stretch_parameters(root, request, '073-faint')
    end2 = stepwise.finish(root, submit(root, faint, action='finish_branch', name='074-end'), '074-end')
    gentle = read(first['parameters'])['branches']['gentle']['steps'][-1]['selected']['.fit']
    mask = {'domain': gentle, 'parameters': {'mask_low': .15, 'mask_high': .75, 'sigma': 2}}
    final = stepwise.finish(root, submit(root, end2, action='finish', mask=mask, name='075-final'), '075-final')
    assert final['status'] == 'solved'
    stepwise.load_report(root, payload, final['parameters'], finished=True)
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    generated = stretch.generate_stretch_assets(root, parameters=final['parameters'], run_id='076-hdr')
    assert generated['mode'] == 'hdr' and '# stepwise-stretch:' in Path(generated['script_path']).read_text()
    if real:
        from .test_hdr_stretch import run_native
        from deep_sky_siril_hdr import validate_stretch, measure_outputs
        run_native(root, Path(generated['script_path']).read_text())
        source = Path(generated['parent_path'])
        primary = Path(generated['primary_output'])
        hdr = validate_stretch(root, source, primary, Path(generated['script_path']).read_text())
        measured = measure_outputs(root, primary, {'hdr_transfer': hdr}, source=source)
        assert max(measured['blend_max_abs']) <= 1e-6


@pytest.mark.skipif(not BINARY.exists(), reason='Native Siril is unavailable')
@pytest.mark.parametrize('channels', [1, 3])
def test_native_prefix_anchors_and_original_source_replay(routing_case, monkeypatch, channels):
    root, source, payload, _, request, _ = routing_case
    make_v2(root, source, request, channels=channels)
    payload['input'].update(contract.fingerprint(source, include_path=False))
    contract.atomic_write_json(root/'session.json', payload)
    contract.atomic_write_json(root/'reports/tool-probe.json', _fake_probe(BINARY))
    monkeypatch.setattr(stretch.NativeTrials, 'run', NATIVE_RUN)
    prepared, goals = prepare(routing_case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-native', timeout=60)
    assert first['status'] == 'awaiting_next_step', first
    first_report = read(first['parameters'])
    first_values = protected.statistics(stretch.pixels(root/first_report['steps'][0]['selected']['.fit']['path']), routing_case[5])
    second = stretch.solve_stretch_parameters(root, submit(root, first, step=next_ghs(goals)), '072-native', timeout=60)
    assert second['status'] == 'awaiting_next_step', second
    final = stepwise.finish(root, submit(root, second, action='finish', name='073-native-finish'), '073-native-final')
    report, _, _ = stretch.load_parameters(root, payload, final['parameters'])
    assert report['steps'][1]['landmarks']['SP'] == first_values['faint']['median']
    assert report['steps'][1]['landmarks']['SP'] != protected.statistics(stretch.pixels(source), routing_case[5])['faint']['median']
    assert report['steps'][0] == first_report['steps'][0]
    generated = stretch.generate_stretch_assets(root, parameters=final['parameters'], run_id='074-native-formal')
    from .test_hdr_stretch import run_native
    run_native(root, Path(generated['script_path']).read_text())
    selected = stretch.pixels(root/report['steps'][-1]['selected']['.fit']['path'])
    actual = stretch.pixels(Path(generated['primary_output']))
    assert np.array_equal(selected, actual)
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_native_hdr_shared_mask_final_review_and_recomposition(linear_case):
    from . import test_deep_sky_siril as support
    root, _, _, source, _, _, _ = linear_case
    original = linear_case[2]
    inspected_script = root/'scripts/010-input.ssf'
    direct, reference = root/'previews/010-input-direct.jpg', root/'previews/010-input-autostretch.jpg'
    inspected_script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{original}"\n'
        f'savejpg "{direct.with_suffix("")}" 95\nautostretch -linked -2.8 0.22\n'
        f'savejpg "{reference.with_suffix("")}" 95\nclose\n')
    support._write_ssf_provenance(root, inspected_script, 'input.inspect')
    inspected = core.run_script(root, protocol='input.inspect', script_value=inspected_script,
        source_value=original, expected_values=[direct, reference], timeout=60)
    assert inspected['status'] == 'success', inspected
    support._review(root, inspected['id'], reference, parent_preview=direct)
    _, request = field_request(root, source)
    regions = read(root/request['regions']['path'])['regions']
    case = (root, source, read(root/'session.json'), None, request, regions)
    prepared, goals = prepare(case, hdr=True, hdr_tail=True)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-gentle', timeout=60)
    assert first['status'] == 'awaiting_next_step', first
    gentle2 = stretch.solve_stretch_parameters(root, submit(root, first, role='gentle', step=next_ghs(goals, 'gentle'), name='071-gentle2'), '071-gentle2', timeout=60)
    assert gentle2['status'] == 'awaiting_next_step', gentle2
    import deep_sky_siril_artifact_focus as artifacts
    from .test_artifact_focus import add_area
    artifact_path, _ = add_area(root, goals['artifact_focus'], name='071-hdr-focus', area_id='core-transition', rect=[244, 244, 270, 270], risk='hdr_transition')
    def review_materials(result, material_id):
        artifacts.prepare(root, artifact_path, material_id, parameters=result['parameters'])
    review_materials(gentle2, '071-gentle-focus')
    end = stepwise.finish(root, submit(root, gentle2, action='finish_branch', name='072-end'), '072-end')
    review_materials(end, '072-gentle-focus')
    faint_step = copy.deepcopy(goals['initial'])
    faint_step['factors'] = {'stretch': 4.}
    faint = stretch.solve_stretch_parameters(root, submit(root, end, role='faint', step=faint_step, name='073-faint'), '073-faint', timeout=60)
    assert faint['status'] == 'awaiting_next_step', faint
    review_materials(faint, '073-faint-focus')
    faint2 = stretch.solve_stretch_parameters(root, submit(root, faint, role='faint', step=next_ghs(goals, 'faint'), name='073-faint2'), '073-faint2', timeout=60)
    assert faint2['status'] == 'awaiting_next_step', faint2
    review_materials(faint2, '073-faint2-focus')
    tail = {**goals['branches']['faint'][-1], 'factors': {'midtones': .15}, 'constraints': []}
    faint3 = stretch.solve_stretch_parameters(root, submit(root, faint2, role='faint', step=tail, name='073-faint3'), '073-faint3', timeout=60)
    assert faint3['status'] == 'awaiting_next_step', faint3
    review_materials(faint3, '073-faint3-focus')
    end2 = stepwise.finish(root, submit(root, faint3, action='finish_branch', name='074-end'), '074-end')
    review_materials(end2, '074-faint-focus')
    gentle = read(gentle2['parameters'])['branches']['gentle']['steps'][-1]['selected']['.fit']
    mask = {'domain': gentle, 'parameters': {'mask_low': .15, 'mask_high': .75, 'sigma': 2}}
    final = stepwise.finish(root, submit(root, end2, action='finish', mask=mask, name='075-final'), '075-final')
    generated = stretch.generate_stretch_assets(root, parameters=final['parameters'], run_id='076-hdr')
    receipt = core.run_script(root, protocol='stretch', script_value=generated['script_path'],
        source_value=source, primary_output_value=generated['primary_output'], expected_values=generated['expected_outputs'], timeout=60)
    assert receipt['status'] == 'success', receipt
    assert max(receipt['processing_checks']['hdr']['blend_max_abs']) <= 1e-6
    assert receipt['hdr_transfer']['stepwise_parameters'] == stretch.record(root, Path(final['parameters']))
    artifacts.prepare(root, artifact_path, '076-hdr-focus', run=receipt['id'])
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    accept(root, receipt['id'])
    assert core.check_review(root, receipt['id'], root/'reviews'/(receipt['id']+'.json'))['status'] == 'review_valid'
    from .test_recomposition_sources_v11 import recompose_assets
    assets = recompose_assets(root, source=Path(receipt['primary_output']))
    merged = core.run_script(root, protocol='stars.recompose', script_value=assets['script_path'],
        source_value=assets['source'], expected_values=assets['expected_outputs'], primary_output_value=assets['primary_output'],
        separation_run='060-stars', stretch_run=receipt['id'], timeout=60)
    assert merged['status'] == 'success', merged
    assert max(merged['processing_checks']['closure']['per_channel_final_max_abs']) <= 1e-5
    accept(root, merged['id'])
    from deep_sky_siril_stretch_trials import retained_paths
    script, jpeg = support._delivery_script(root, Path(assets['primary_output']))
    delivered = core.run_script(root, protocol='delivery.render', script_value=script, source_value=assets['primary_output'],
        expected_values=[jpeg, jpeg.with_suffix('.fit')], timeout=60)
    assert delivered['status'] == 'success', delivered
    review = accept(root, delivered['id'])
    archived = retained_paths(root, read(root/'session.json')) | artifacts.retained_paths(root, read(root/'session.json'))
    selection = support._selection(root, delivered['id'], jpeg, review)
    finalized = core.finalize_session(root, selection_value=selection, keep_intermediates=False)
    assert finalized['status'] == 'success'
    assert all((root/path).is_file() for path in archived)
    assert core.finalize_session(root, selection_value=selection, keep_intermediates=False) == finalized


def test_final_display_goals_do_not_reject_an_intermediate_prefix(routing_case):
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case, final_min=.7)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    assert first['status'] == 'awaiting_next_step'
    second = stretch.solve_stretch_parameters(root, submit(root, first, step=next_ghs(goals), name='072-next'), '072-prefix')
    final = stepwise.finish(root, submit(root, second, action='finish', name='073-finish'), '073-final')
    assert final['status'] == 'ineligible'
    report, _, _ = stepwise.load_report(root, payload, final['parameters'])
    assert report['final_audit']['reasons'] == ['regional_constraints']
    with pytest.raises(contract.ContractError, match='explicitly finished'):
        stretch.generate_stretch_assets(root, parameters=final['parameters'])


@pytest.mark.parametrize('failure', [False, True, 'timeout'])
def test_candidate_retries_and_revisions_share_global_three_attempts(routing_case, monkeypatch, failure):
    root, _, payload, *_ = routing_case
    if failure:
        def fail(self, source, chain):
            self._claim_attempt()
            (self.evidence_dir/'failed.log').write_text('Native failure after scientific launch.')
            if failure == 'timeout':
                raise TimeoutError('Native timeout after launch')
            stretch.fail('Native failure after launch')
        monkeypatch.setattr(stretch.NativeTrials, 'run', fail)
    previous = None
    for index in range(1, 5):
        revision = None if previous is None else {'parameters': stretch.record(root, Path(previous['parameters'])), 'reason': 'Start an independent retry or comparison candidate.'}
        prepared, _ = prepare(routing_case, name=f'07{index}-candidate', revision=revision)
        if index == 4:
            with pytest.raises(contract.ContractError, match='three shared scientific attempts'):
                stretch.solve_stretch_parameters(root, prepared['request'], '074-prefix')
            break
        previous = stretch.solve_stretch_parameters(root, prepared['request'], f'07{index}-prefix')
        assert previous['status'] == ('timed_out' if failure == 'timeout' else 'failed' if failure else 'awaiting_next_step')
        stepwise.load_report(root, payload, previous['parameters'])
        assert len(list(root.glob('reports/*/scientific-attempt.json'))) == index
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 3


def test_invalid_static_proposal_and_read_only_status_do_not_launch_siril(routing_case, monkeypatch):
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case)
    original = stretch.NativeTrials.run
    monkeypatch.setattr(stretch.NativeTrials, 'run', lambda *args: pytest.fail('Status must not invoke Siril'))
    rows = stepwise.candidate_statuses(root, payload)
    assert rows[0]['current_step'] == 0 and rows[0]['remaining_attempts'] == 3
    first_path = Path(prepared['request'])
    saved = first_path.read_bytes()
    proposal = read(first_path)
    proposal['step']['factors'] = {'stretch': -1}
    first_path.write_text(json.dumps(proposal))
    with pytest.raises(contract.ContractError):
        stretch.solve_stretch_parameters(root, first_path, '071-invalid')
    assert not list(root.glob('reports/*/scientific-attempt.json'))
    first_path.write_bytes(saved)
    monkeypatch.setattr(stretch.NativeTrials, 'run', original)
    first = stretch.solve_stretch_parameters(root, first_path, '071-prefix')
    monkeypatch.setattr(stretch.NativeTrials, 'run', lambda *args: pytest.fail('Recovery must not invoke Siril'))
    assert stepwise.candidate_statuses(root, payload)[0]['current_step'] == 1
    assert stepwise.status(root, payload, Path(first['parameters']))['remaining_attempts'] == 2


def test_concurrent_alternative_successors_cannot_share_a_candidate(routing_case):
    from concurrent.futures import ThreadPoolExecutor
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    requests = []
    for index, value in enumerate((.2, .4)):
        step = next_ghs(goals)
        step['factors']['D'] = value
        requests.append(submit(root, first, step=step, name=f'072-next-{index}'))
    def execute(index):
        try:
            return stretch.solve_stretch_parameters(root, requests[index], f'072-prefix-{index}')['status']
        except contract.ContractError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(execute, range(2)))
    assert rows.count('awaiting_next_step') == 1 and sum('actual successor' in row for row in rows) == 1
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def complete_main(root, prepared, goals, prefix):
    current = stretch.solve_stretch_parameters(root, prepared['request'], prefix+'-1')
    for index, definition in enumerate(goals['branches']['main'][1:], 2):
        operator = next_ghs(goals) if definition['method'] == 'ght' else {**definition, 'factors': {'midtones': .4}, 'constraints': []}
        operator.update(definition)
        current = stretch.solve_stretch_parameters(root, submit(root, current, step=operator, name=prefix+'-request-'+str(index)), prefix+'-'+str(index))
        assert current['status'] == 'awaiting_next_step', current
    return stepwise.finish(root, submit(root, current, action='finish', name=prefix+'-finish'), prefix+'-final')


@pytest.mark.parametrize('negative, explicit_control', [(False, True), (True, True), (False, False)])
def test_completed_stepwise_rgb_tails_and_negative_control(routing_case, monkeypatch, negative, explicit_control):
    root, source, payload, _, request, _ = routing_case
    make_v2(root, source, request, channels=3)
    payload['input'].update(contract.fingerprint(source, include_path=False))
    contract.atomic_write_json(root/'session.json', payload)
    original = stretch.NativeTrials.run
    if negative:
        def native(self, parent, chain):
            output, evidence = original(self, parent, chain)
            if len(chain) == 3 and chain[-1]['command'] == 'ght':
                from .test_deep_sky_siril_stretch import write
                write(output, stretch.pixels(output).astype(np.float32)*.5)
                evidence['output_sha256'] = contract.sha256_file(output)
            return output, evidence
        monkeypatch.setattr(stretch.NativeTrials, 'run', native)
    prepared, goals = prepare(routing_case, profile='galaxy_halo', final_min=.08 if negative else None)
    mtf = complete_main(root, prepared, goals, '071-mtf')
    assert mtf['status'] == 'solved'
    with pytest.raises(contract.ContractError, match='compare-tail'):
        stretch.generate_stretch_assets(root, parameters=mtf['parameters'])
    control = copy.deepcopy(goals)
    control['revision'] = {'parameters': stretch.record(root, Path(mtf['parameters'])), 'reason': 'Independent same-prefix GHS tail control.'}
    if explicit_control:
        control['tail_control'] = stretch.record(root, Path(mtf['parameters']))
    control['branches']['main'][-1] = {'method': 'ght', 'purpose': 'Compare a luminance GHS tail against RGB MTF.'}
    prepared2, _ = prepare(routing_case, goals=control, name='072-control')
    ghs = complete_main(root, prepared2, control, '072-ghs')
    assert ghs['status'] == ('ineligible' if negative else 'solved')
    if ghs['status'] == 'solved':
        with pytest.raises(contract.ContractError, match='compare-tail'):
            stretch.generate_stretch_assets(root, parameters=ghs['parameters'])
    compared = routes.compare_tail(root, mtf['parameters'], ghs['parameters'], '073-tail')
    assert compared['status'] == 'comparison_ready'
    generated = stretch.generate_stretch_assets(root, parameters=mtf['parameters'], comparison=compared['comparison'], run_id='074-formal')
    assert '# stretch-comparison:' in Path(generated['script_path']).read_text()
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 2


def test_native_ordinary_formal_review_status_and_immutable_replay(linear_case, monkeypatch):
    from deep_sky_siril_resume import session_status
    from deep_sky_siril_stretch_trials import retained_paths
    root, _, _, source, _, _, _ = linear_case
    _, request = field_request(root, source)
    regions = read(root/request['regions']['path'])['regions']
    case = (root, source, read(root/'session.json'), None, request, regions)
    prepared, goals = prepare(case)
    final = complete_main(root, prepared, goals, '071-native')
    assert final['status'] == 'solved', final
    generated = stretch.generate_stretch_assets(root, parameters=final['parameters'], run_id='074-formal')
    options = dict(protocol='stretch', script_value=generated['script_path'], source_value=source,
        primary_output_value=generated['primary_output'], expected_values=[generated['primary_output'], generated['preview_output']], timeout=60)
    receipt = core.run_script(root, **options)
    assert receipt['status'] == 'success', receipt
    accept(root, receipt['id'])
    assert core.check_review(root, receipt['id'], root/'reviews'/(receipt['id']+'.json'))['status'] == 'review_valid'
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    monkeypatch.setattr(core.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Immutable replay and recovery must not launch Siril'))
    result = session_status(root, parameters=Path(final['parameters']))
    assert result['prefix_reuse']['candidate'] == '071-stepwise'
    assert result['stretch_attempts']['used'] == 1
    assert retained_paths(root, case[2])
    assert core.run_script(root, **options)['replayed']
    with pytest.raises(contract.ContractError, match='already launched'):
        stepwise.claim_formal(root, case[2], stretch.record(root, Path(final['parameters'])), '075-retry')


def test_outgoing_policy_and_full_bindings_are_replay_only(tmp_path, monkeypatch):
    import deep_sky_siril_session as sessions
    from ._policy_pins import replay_and_finalize_without_migration
    prior = read(Path(__file__).parents[1]/'references/stretch-stepwise-history.json')
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': prior['execution_policy']})
    assert protected.implementation(tmp_path) == prior['implementation']
    assert protected.profiles_fingerprint(tmp_path) == prior['profiles']
    for name in ('references/stages/stage-07-stretch.md', 'references/stage7-hdr.md'):
        assert sessions.registered_guidance_reference(prior, {'path': name, **prior['documents'][name]})
    replay_and_finalize_without_migration(tmp_path/'replay', monkeypatch, Path(__file__).parents[1]/'references/stretch-stepwise-history.json')


def test_static_error_before_native_launch_keeps_the_prefix_available(routing_case, monkeypatch):
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    next_request = submit(root, first, step=next_ghs(goals), name='072-static')
    original = stretch.NativeTrials.run
    def static_error(*args):
        raise ValueError('Static preparation error before native launch')
    monkeypatch.setattr(stretch.NativeTrials, 'run', static_error)
    failed = stretch.solve_stretch_parameters(root, next_request, '072-static')
    assert failed['status'] == 'failed' and failed['pending'] == 'correct_static_request'
    stepwise.load_report(root, payload, failed['parameters'])
    assert not (Path(first['parameters']).parent/'stepwise-successor.json').exists()
    monkeypatch.setattr(stretch.NativeTrials, 'run', original)
    corrected = submit(root, first, step=next_ghs(goals), name='073-corrected')
    resumed = stretch.solve_stretch_parameters(root, corrected, '073-prefix')
    assert resumed['status'] == 'awaiting_next_step'
    stepwise.load_report(root, payload, failed['parameters'])
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_stepwise_cli_preflight_and_success_exit_codes(routing_case, monkeypatch, capsys):
    root, _, _, _, request, _ = routing_case
    prepared, goals = prepare(routing_case)
    options = ['--session', str(root), '--source', request['source']['path'], '--regions', request['regions']['path'],
        '--goals', str(root/'reports/071-stepwise-goals.json'), '--run-id', '071-preflight']
    monkeypatch.setattr('sys.argv', ['stretch', 'preflight', *options])
    assert stretch.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'ready' and result['future_factors'] == 'undeclared'
    assert not (root/'reports/071-preflight').exists()
    monkeypatch.setattr('sys.argv', ['stretch', 'solve', '--session', str(root), '--request', prepared['request'], '--run-id', '071-prefix'])
    assert stretch.main() == 0
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'awaiting_next_step'
    finish_request = submit(root, first, action='finish', name='072-finish')
    monkeypatch.setattr('sys.argv', ['stretch', 'finish', '--session', str(root), '--request', str(finish_request), '--run-id', '072-final'])
    assert stretch.main() == 1
    assert 'at least two' in json.loads(capsys.readouterr().out)['reason']
    assert not (root/'reports/072-final').exists()
    second = stretch.solve_stretch_parameters(root, submit(root, first, step=next_ghs(goals), name='072-next'), '072-prefix')
    finish_request = submit(root, second, action='finish', name='073-finish')
    monkeypatch.setattr('sys.argv', ['stretch', 'finish', '--session', str(root), '--request', str(finish_request), '--run-id', '073-final'])
    assert stretch.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'solved'


def test_missing_successor_claim_cannot_create_a_free_fork(routing_case):
    root, _, _, *_ = routing_case
    prepared, goals = prepare(routing_case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    second = stretch.solve_stretch_parameters(root, submit(root, first, step=next_ghs(goals)), '072-prefix')
    assert second['status'] == 'awaiting_next_step'
    (Path(first['parameters']).parent/'stepwise-successor.json').unlink()
    alternative = next_ghs(goals)
    alternative['factors']['D'] = .4
    request = submit(root, first, step=alternative, name='073-fork')
    with pytest.raises(contract.ContractError, match='lost its exclusive claim'):
        stretch.solve_stretch_parameters(root, request, '073-fork')
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_full_stars_require_stellar_protection_regions(routing_case):
    root, _, _, _, request, _ = routing_case
    region_path = root/request['regions']['path']
    regions = read(region_path)
    regions['regions'] = [r for r in regions['regions'] if r['role'] != 'stars']
    contract.atomic_write_json(region_path, regions)
    request['regions'] = stretch.record(root, region_path)
    request['operators'][0]['constraints'] = [r for r in request['operators'][0]['constraints'] if r['region'] != 'stars']
    with pytest.raises(contract.ContractError, match='stellar protection'):
        prepare(routing_case)
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_another_initial_plan_cannot_reset_the_first_morphology(routing_case):
    root, _, _, *_ = routing_case
    prepare(routing_case)
    with pytest.raises(contract.ContractError, match='morphology classification'):
        prepare(routing_case, name='072-reset', profile='dust_reflection')
    assert not list(root.glob('reports/*/scientific-attempt.json'))

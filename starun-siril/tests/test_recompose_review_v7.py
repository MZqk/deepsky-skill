"""Signed-residual warning review and actual final composition under frozen v7."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from .test_native_processing import native, recompose_steps, write_fits
from .test_deep_sky_siril import (_session, _delivery_run, _delivery_script, _write_delivery_outputs,
    _input_inspect_run, _fake_probe, _write_ssf_provenance, _review, _selection, _write_jpeg, core, session_state, tooling)
from deep_sky_siril_contract import EXECUTION_POLICY, fingerprint, sha256_file


def case(root, channels=3, enhanced=False, strength=.8, negative=-.0004, target_type='galaxy'):
    (root / 'artifacts').mkdir(exist_ok=True)
    chain = [{'command': 'mtf', 'parameters': ['0', '.5', '1']}]
    original, starless, parent = [root / name for name in
        ('input.fit', 'artifacts/linear-a.fit', 'artifacts/parent.fit')]
    base = np.full((channels, 40, 40), .2)
    stars = np.full_like(base, negative)
    stars[:, 20:24, 20:24] = .12
    current = base * (.9 if enhanced else 1)
    steps, outputs = recompose_steps(root, chain, original=original, starless=starless, source=parent)
    steps[-2][1] = '$artifacts/parent$ + ' + str(strength) + ' * $artifacts/stars$'
    for path, array in [(original, base+stars), (starless, base), (parent, current),
                        *zip(outputs, (base+stars, base, stars, current+strength*stars))]:
        write_fits(path, array)
    branch = native.validate_recomposition(root, steps, parent, original, starless, outputs[-1], chain)
    metadata = {'branch_binding': branch, 'target_type': target_type}
    return parent, outputs, metadata, steps, original, starless


@pytest.mark.parametrize('channels', [1, 3])
@pytest.mark.parametrize('enhanced,strength', [(False, 1), (False, .8), (True, .3), (True, .8)])
def test_signed_ratio_warns_on_every_valid_branch_without_modifying_pixels(tmp_path, channels, enhanced, strength):
    parent, outputs, metadata, *_ = case(tmp_path, channels, enhanced, strength)
    before = {path: fingerprint(path, include_path=False) for path in [parent, *outputs]}
    result = native.validate_processing_outputs(tmp_path, parent, outputs[-1], metadata)['closure']
    assert result['policy'] == 'subject_roi_residual_v2'
    assert len(result['warnings']) == channels and not result['violations']
    assert result['negative_energy_ratio_role'] == 'review_trigger'
    assert all(r > .010 for r in result['per_channel_neg_energy_ratio'])
    assert max(result['per_channel_final_max_abs']) < 1e-6
    assert all(row['endpoint_counts']['candidate']['shadow'] == 0 for row in result['channels'])
    assert {path: fingerprint(path, include_path=False) for path in before} == before
    with pytest.raises(native.ProcessingEvidenceError):
        native.validate_processing_outputs(tmp_path, parent, outputs[-1], metadata,
                                          execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})


@pytest.mark.parametrize('issue', ['missing-parent', 'missing-final', 'geometry', 'nonfinite',
                                  'closure', 'minimum', 'rescale', 'clip'])
def test_final_formula_and_remaining_hard_gates_reject(tmp_path, issue):
    parent, outputs, metadata, *_ = case(tmp_path)
    read = lambda path: native.read_fits_pixels(path)[0].astype(float)
    if issue == 'missing-parent':
        parent.unlink()
    elif issue == 'missing-final':
        outputs[-1].unlink()
    elif issue == 'geometry':
        write_fits(outputs[-1], read(outputs[-1])[:, :-1])
    elif issue == 'nonfinite':
        array = read(outputs[-1]); array[1, 0, 0] = np.nan; write_fits(outputs[-1], array)
    elif issue == 'closure':
        array = read(outputs[0]); array[1, 0, 0] += .00002; write_fits(outputs[0], array)
    elif issue == 'minimum':
        array = read(outputs[2]); array[1, 0, 0] = -.1001; write_fits(outputs[2], array)
        write_fits(outputs[0], read(outputs[1]) + array)
        write_fits(outputs[-1], read(parent) + .8*array)
    elif issue == 'rescale':
        write_fits(outputs[-1], read(outputs[-1])*.9)
    else:
        array = read(outputs[2]); array[1, 0, 0] = -.05; write_fits(outputs[2], array)
        write_fits(outputs[0], read(outputs[1]) + array)
        value = read(parent); value[1, 0, 0] = .01; write_fits(parent, value)
        write_fits(outputs[-1], np.clip(value + .8*array, 0, 1))
    with pytest.raises(native.ProcessingEvidenceError) as exc:
        native.validate_processing_outputs(tmp_path, parent, outputs[-1], metadata)
    result = exc.value.processing_checks['closure']
    assert len(result['channels']) == 3 and result['violations']
    assert result['warnings']
    json.dumps(result, allow_nan=False)


def receipt_case(root, **kwargs):
    parent, outputs, metadata, steps, original, starless = case(root, **{'enhanced': True, **kwargs})
    (root/'runs').mkdir(); (root/'scripts').mkdir(); (root/'reviews').mkdir(); (root/'reports').mkdir()
    script = root/'scripts/090-recompose.ssf'
    script.write_text('requires 1.4.4 1.5.0\nset32bits\n' +
                      '\n'.join(' '.join(json.dumps(t) for t in row) for row in steps))
    separation = {'id': '060-stars', 'protocol': 'stars.separate', 'source_path': '@input',
                  'primary_output': starless.relative_to(root).as_posix()}
    stretch = {'id': '070-stretch', 'protocol': 'stretch', 'source_path': separation['primary_output'],
               'transfer_chain': [{'command': 'mtf', 'parameters': ['0', '.5', '1']}],
               'branch_binding': {'separation_run': separation['id']}}
    for record in (separation, stretch):
        (root/'runs'/f"{record['id']}.json").write_text(json.dumps(record))
    branch = metadata['branch_binding']
    branch.update(separation_run=separation['id'], stretch_run=stretch['id'],
                  separation_receipt_sha256=sha256_file(root/'runs/060-stars.json'),
                  stretch_receipt_sha256=sha256_file(root/'runs/070-stretch.json'))
    receipt = {'id': '090-recompose', 'protocol': 'stars.recompose', 'status': 'success',
               'source_path': parent.relative_to(root).as_posix(), 'source': fingerprint(parent, include_path=False),
               'primary_output': outputs[-1].relative_to(root).as_posix(), 'branch_binding': branch,
               'script': {'path': script.relative_to(root).as_posix()},
               'transfer_chain': stretch['transfer_chain'],
               'outputs': [{'path': path.relative_to(root).as_posix(), **fingerprint(path, include_path=False)} for path in outputs],
               'processing_checks': {'status': 'passed', **native.validate_processing_outputs(root, parent, outputs[-1], metadata)}}
    (root/'previews').mkdir()
    preview = root/'previews/unusual-final.jpeg'
    _write_jpeg(preview, size=(40, 40))
    receipt['outputs'].append({'path': preview.relative_to(root).as_posix(), **fingerprint(preview, include_path=False)})
    from deep_sky_siril_metrics import generate_metric_report
    generate_metric_report(run_id=receipt['id'], protocol=receipt['protocol'], candidate_path=outputs[-1],
        parent_path=parent, output_path=root/'reports/090-recompose/metrics.json')
    payload = {'input': {'path': str(original)}, 'context': {'target_type': metadata['target_type']},
               'execution_policy': {k: v for k, v in EXECUTION_POLICY.items() if k != 'recomposition_parameters'}}
    (root/'session.json').write_text(json.dumps(payload))
    (root/'runs/090-recompose.json').write_text(json.dumps(receipt))
    return receipt, payload, outputs


@pytest.mark.parametrize('mutation', ['warning', 'strength', 'chain', 'parent-hash', 'layer-hash', 'separation', 'geometry'])
def test_v2_replay_rejects_forged_or_drifting_evidence(tmp_path, mutation):
    receipt, payload, outputs = receipt_case(tmp_path)
    assert native.verify_recomposition_receipt(tmp_path, payload, receipt)
    forged = copy.deepcopy(receipt)
    if mutation == 'warning':
        forged['processing_checks']['closure']['warnings'] = []
    elif mutation == 'strength':
        forged['branch_binding']['strength'] = .5
    elif mutation == 'chain':
        forged['transfer_chain'][0]['parameters'][1] = '.1'
    elif mutation == 'parent-hash':
        forged['source']['sha256'] = '0'*64
    elif mutation == 'layer-hash':
        outputs[2].write_bytes(outputs[2].read_bytes()+b'drift')
    elif mutation == 'separation':
        forged['branch_binding']['separation_receipt_sha256'] = '0'*64
    else:
        raw = native.read_fits_pixels(outputs[-1])[0]
        write_fits(outputs[-1], raw[:, :-1])
    assert not native.verify_recomposition_receipt(tmp_path, payload, forged)


@pytest.mark.parametrize('mutation', ['receipt', 'parent', 'layer', 'candidate', 'gate', 'tamper', 'reject', 'uncertain', 'accept'])
def test_warning_review_binds_actual_materials_and_five_gates(tmp_path, monkeypatch, mutation):
    receipt, payload, outputs = receipt_case(tmp_path)
    monkeypatch.setattr(session_state, 'load_session', lambda value: (tmp_path, payload, {}))
    monkeypatch.setattr(core, '_load_run', lambda *a: (tmp_path/'runs/090-recompose.json', receipt))
    draft = core.check_review(tmp_path, receipt['id'], None)
    assert draft['status'] == 'review_required'
    review = draft['review']
    assert receipt['source_path'] in {row['path'] for row in review['inspected_materials']}
    review.update(verdict='accept', gates={gate: 'pass' for gate in review['gates']},
                  notes='Synthetic fixture: checked core, outer target, companions, sky, stars and signed locations.')
    roles = {'receipt': 'runs/090-recompose.json', 'parent': receipt['source_path'],
             'layer': receipt['branch_binding']['display_stars'], 'candidate': receipt['primary_output']}
    if mutation in roles:
        review['inspected_materials'] = [row for row in review['inspected_materials'] if row['path'] != roles[mutation]]
    elif mutation == 'gate':
        review['gates']['stars'] = 'not_applicable'
    elif mutation == 'tamper':
        review['inspected_materials'][0]['sha256'] = '0'*64
    elif mutation in ('reject', 'uncertain'):
        review['verdict'] = mutation
        review['gates']['structure'] = 'fail' if mutation == 'reject' else 'uncertain'
    path = tmp_path/'reviews/090-recompose.json'; path.write_text(json.dumps(review))
    if mutation in (*roles, 'gate', 'tamper'):
        with pytest.raises(core.ContractError):
            core.check_review(tmp_path, receipt['id'], path)
    else:
        assert core.check_review(tmp_path, receipt['id'], path)['review']['verdict'] == review['verdict']


def review_case(root, monkeypatch, **kwargs):
    receipt, payload, outputs = receipt_case(root, **kwargs)
    monkeypatch.setattr(session_state, 'load_session', lambda _: (root, payload, {}))
    monkeypatch.setattr(core, '_load_run', lambda *_: (root/'runs/090-recompose.json', receipt))
    return receipt, payload, outputs


def accepted_draft(root, receipt):
    draft = core.review_materials_for_run(str(root), receipt['id'])
    draft.update(verdict='accept', gates={name: 'pass' for name in draft['gates']},
                 notes='Synthetic review of all fixed material roles.')
    return draft


@pytest.mark.parametrize('channels', [1, 3])
@pytest.mark.parametrize('target,limit', [('nebula', .005), ('galaxy', .010)])
@pytest.mark.parametrize('side', [-1, 1])
@pytest.mark.parametrize('enhanced,strength', [(False, 1), (True, .3), (True, .8)])
def test_fixed_roles_are_independent_of_warning_boundary(tmp_path, monkeypatch, channels, target, limit, side, enhanced, strength):
    ratio = limit * (1 + side * .001)
    negative = -ratio * (.12 * 16) / (1584 * (1 - ratio))
    receipt, payload, _ = review_case(tmp_path, monkeypatch, channels=channels, target_type=target,
        negative=negative, enhanced=enhanced, strength=strength)
    assert bool(receipt['processing_checks']['closure']['warnings']) == (side > 0)
    assert native.verify_recomposition_receipt(tmp_path, payload, receipt)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    draft = accepted_draft(tmp_path, receipt)
    paths = [row['path'] for row in draft['inspected_materials']]
    assert paths == sorted(paths) and len(paths) == len(set(paths)) == 10
    expected = {'@input', 'artifacts/linear-a.fit', receipt['source_path'], receipt['primary_output'],
        *(receipt['branch_binding'][role] for role in ('full_baseline', 'original_starless', 'display_stars')),
        'previews/unusual-final.jpeg', 'runs/090-recompose.json', 'reports/090-recompose/metrics.json'}
    assert set(paths) == expected
    assert all(p.read_bytes() == data for p, data in before.items())
    path = tmp_path/'reviews/090-recompose.json'
    path.write_text(json.dumps(draft))
    assert core.check_review(tmp_path, receipt['id'], path)['status'] == 'review_valid'


@pytest.mark.parametrize('role', ['input', 'linear', 'parent', 'baseline', 'starless', 'stars', 'candidate', 'preview', 'receipt', 'metrics'])
def test_each_fixed_material_is_required_on_clean_acceptance(tmp_path, monkeypatch, role):
    receipt, _, _ = review_case(tmp_path, monkeypatch, negative=0)
    draft = accepted_draft(tmp_path, receipt)
    roles = dict(input='@input', linear='artifacts/linear-a.fit', parent=receipt['source_path'],
        baseline=receipt['branch_binding']['full_baseline'], starless=receipt['branch_binding']['original_starless'],
        stars=receipt['branch_binding']['display_stars'], candidate=receipt['primary_output'],
        preview='previews/unusual-final.jpeg', receipt='runs/090-recompose.json', metrics='reports/090-recompose/metrics.json')
    draft['inspected_materials'] = [row for row in draft['inspected_materials'] if row['path'] != roles[role]]
    path = tmp_path/'reviews/090-recompose.json'
    path.write_text(json.dumps(draft))
    with pytest.raises(core.ContractError, match='Stage 9.*(missing|required|requires)'):
        core.check_review(tmp_path, receipt['id'], path)
    draft.update(verdict='uncertain', gates={name: 'uncertain' for name in draft['gates']})
    path.write_text(json.dumps(draft))
    assert core.check_review(tmp_path, receipt['id'], path)['status'] == 'review_valid'


@pytest.mark.parametrize('issue', ['missing', 'malformed', 'wrong_run', 'wrong_protocol', 'wrong_parent', 'wrong_candidate', 'symlink', 'drift'])
def test_fixed_metrics_fail_closed_with_recovery_guidance(tmp_path, monkeypatch, issue):
    receipt, _, _ = review_case(tmp_path, monkeypatch, negative=0)
    path = tmp_path/'reports/090-recompose/metrics.json'
    if issue == 'missing':
        path.unlink()
    elif issue == 'malformed':
        path.write_text('{')
    elif issue == 'symlink':
        copy = tmp_path/'copied-metrics.json'
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    elif issue == 'drift':
        fingerprint_real = core.fingerprint
        calls = []
        def changed(target, **kwargs):
            record = fingerprint_real(target, **kwargs)
            if target == path:
                calls.append(True)
                if len(calls) > 1:
                    record['sha256'] = '0' * 64
            return record
        monkeypatch.setattr(core, 'fingerprint', changed)
    else:
        report = json.loads(path.read_text())
        if issue in {'wrong_run', 'wrong_protocol'}:
            report['run_id' if issue == 'wrong_run' else 'protocol'] = 'other'
        else:
            report['parent' if issue == 'wrong_parent' else 'candidate']['path'] = str(tmp_path/'other.fit')
        path.write_text(json.dumps(report))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(core.ContractError) as error:
        core.review_materials_for_run(str(tmp_path), receipt['id'])
    if issue not in {'symlink', 'drift'}:
        assert 'deep_sky_siril_metrics.py --session' in str(error.value)
    assert all(p.read_bytes() == data for p, data in before.items())


def test_recovery_report_satisfies_fixed_role_and_is_bound(tmp_path, monkeypatch):
    receipt, payload, _ = review_case(tmp_path, monkeypatch, negative=0)
    # Exercise the existing recovery publisher and validator with this synthetic replay.
    binding = {'run_receipt_sha256': sha256_file(tmp_path/'runs/090-recompose.json'),
        'inputs': {'parent': fingerprint(tmp_path/receipt['source_path']), 'candidate': fingerprint(tmp_path/receipt['primary_output'])}}
    arguments = dict(run_id=receipt['id'], protocol=receipt['protocol'],
        candidate_path=tmp_path/receipt['primary_output'], parent_path=tmp_path/receipt['source_path'],
        stars_tsv_path=None, background_validation=None, neutral_validation=None)
    monkeypatch.setattr(core, '_metric_recovery_inputs', lambda *args, **kwargs: (tmp_path, binding, arguments))
    recovered = core.recover_run_metrics(str(tmp_path), receipt['id'])
    assert core.recover_run_metrics(str(tmp_path), receipt['id'])['reused']
    original = tmp_path/'reports/090-recompose/metrics.json'
    assert core._recomposition_metric_material(tmp_path, receipt)['path'] == 'reports/090-recompose/metrics.json'
    recovery_path = recovered['report']['path']
    draft = accepted_draft(tmp_path, receipt)
    draft['inspected_materials'] = [row for row in draft['inspected_materials'] if row['path'] != 'reports/090-recompose/metrics.json']
    draft['inspected_materials'].append({key: recovered['report'][key] for key in ('path', 'sha256')})
    review = tmp_path/'reviews/090-recompose.json'
    review.write_text(json.dumps(draft))
    assert core.check_review(tmp_path, receipt['id'], review)['status'] == 'review_valid'
    original.write_text('{')
    assert recovery_path in {row['path'] for row in core.review_materials_for_run(str(tmp_path), receipt['id'])['inspected_materials']}
    assert core._recomposition_metric_material(tmp_path, receipt)['path'] == recovery_path
    document = core.load_json(tmp_path/recovery_path)
    document['recovery_binding']['inputs']['parent']['sha256'] = '0' * 64
    core.atomic_write_json(tmp_path/recovery_path, document)
    with pytest.raises(core.ContractError, match='requires a valid metrics report'):
        core.review_materials_for_run(str(tmp_path), receipt['id'])


@pytest.mark.parametrize('warning', [False, True])
def test_historical_partial_review_keeps_original_rules(tmp_path, monkeypatch, warning):
    receipt, payload, _ = review_case(tmp_path, monkeypatch, negative=-.0004 if warning else 0)
    draft = accepted_draft(tmp_path, receipt)
    payload['execution_policy'] = dict(session_state._V10_EXECUTION_POLICY)
    required = {receipt['source_path'], receipt['primary_output'], 'previews/unusual-final.jpeg',
        *(receipt['branch_binding'][role] for role in ('full_baseline', 'original_starless', 'display_stars'))}
    if warning:
        required.add('runs/090-recompose.json')
    draft['inspected_materials'] = [row for row in draft['inspected_materials'] if row['path'] in required]
    path = tmp_path/'reviews/090-recompose.json'
    path.write_text(json.dumps(draft))
    (tmp_path/'reports/090-recompose/metrics.json').unlink()
    before = path.read_bytes()
    assert core.check_review(tmp_path, receipt['id'], path)['status'] == 'review_valid'
    assert path.read_bytes() == before
    with pytest.raises(core.ContractError, match='requires a valid metrics report'):
        core.check_review(tmp_path, receipt['id'], None)


def test_additional_hdr_materials_do_not_create_a_count_gate(tmp_path, monkeypatch):
    receipt, _, _ = review_case(tmp_path, monkeypatch, negative=0)
    additional = tmp_path/'artifacts/hdr-gentle.fit'
    write_fits(additional, np.full((3, 40, 40), .2))
    receipt['outputs'].append({'path': 'artifacts/hdr-gentle.fit', **fingerprint(additional, include_path=False)})
    core.atomic_write_json(tmp_path/'runs/090-recompose.json', receipt)
    draft = accepted_draft(tmp_path, receipt)
    assert len(draft['inspected_materials']) == 11
    path = tmp_path/'reviews/090-recompose.json'
    path.write_text(json.dumps(draft))
    assert core.check_review(tmp_path, receipt['id'], path)['status'] == 'review_valid'


def test_uncertain_metrics_remain_valid_diagnostic_evidence(tmp_path, monkeypatch):
    receipt, _, _ = review_case(tmp_path, monkeypatch, negative=0)
    metric = tmp_path/'reports/090-recompose/metrics.json'
    report = core.load_json(metric)
    report['candidate']['metrics'] = {'status': 'uncertain', 'error': 'Synthetic measurement limitation'}
    report['recommended_verdict'] = 'uncertain'
    core.atomic_write_json(metric, report)
    draft = accepted_draft(tmp_path, receipt)
    path = tmp_path/'reviews/090-recompose.json'
    core.atomic_write_json(path, draft)
    assert core.check_review(tmp_path, receipt['id'], path)['status'] == 'review_valid'


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'symlink'])
def test_fixed_scientific_materials_cannot_be_replaced(tmp_path, monkeypatch, mutation):
    receipt, _, outputs = review_case(tmp_path, monkeypatch, negative=0)
    draft = accepted_draft(tmp_path, receipt)
    path = tmp_path/'reviews/090-recompose.json'
    core.atomic_write_json(path, draft)
    target = outputs[2]
    if mutation == 'missing':
        target.unlink()
    elif mutation == 'changed':
        write_fits(target, np.full((3, 40, 40), .1))
    else:
        copy = tmp_path/'different-stars.fit'
        copy.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(copy)
    with pytest.raises(core.ContractError):
        core.check_review(tmp_path, receipt['id'], path)


def test_v10_policy_snapshot_is_complete_and_review_specific():
    assert {**{key: value for key, value in {k:v for k,v in session_state._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}.items() if key not in {'recomposition_review', 'filter_metadata', 'background_sampling'}}, 'neutral': 'heldout_benefit_safety_v1', 'guidance': 'stage4_xisf_inputs_v10'} == session_state._V10_EXECUTION_POLICY
    assert session_state.known_execution_policy(session_state._V10_EXECUTION_POLICY)
    assert not session_state.execution_policy_is_current({'execution_policy': session_state._V10_EXECUTION_POLICY})
    assert EXECUTION_POLICY['recomposition_review'] == 'stellar_color_assessment_v2'
    assert {**{key: value for key, value in {k:v for k,v in session_state._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}.items() if key not in {'filter_metadata', 'background_sampling'}}, 'neutral': 'heldout_benefit_safety_v1', 'guidance': 'stage4_xisf_inputs_v10'} == session_state._FIXED_REVIEW_EXECUTION_POLICY
    assert session_state.known_execution_policy(session_state._FIXED_REVIEW_EXECUTION_POLICY)


def test_fixed_recomposition_review_continues_to_delivery_and_finalize(tmp_path, monkeypatch):
    data = tmp_path/'data'
    data.mkdir()
    sample, _, _ = receipt_case(data, enhanced=False, negative=0)
    binary = tmp_path/'siril-cli'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    monkeypatch.setattr(tooling, 'probe_tools', lambda **_: _fake_probe(binary))
    root = tmp_path/'session'
    original = data/'input.fit'
    session_state.init_session(str(original), str(root), input_state='linear',
        state_evidence=['user confirmed input_state=linear; synthetic stacked fixture'], channel_mode='broadband', channel_map=None,
        target_name='Synthetic galaxy', target_type='galaxy', style='natural', stars='adaptive',
        offline=False, keep_intermediates=True)
    prior = core.load_json(Path(__file__).parents[1]/'tests/fixtures/recomposition-prior.json')
    payload = core.load_json(root/'session.json')
    payload['execution_policy'] = prior['execution_policy']
    payload.pop('session_sha256'); payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(root/'session.json', payload)
    current_policy = session_state.execution_policy_is_current
    # Construct the historical fixture before testing its current read-only finalize path.
    monkeypatch.setattr(session_state, 'execution_policy_is_current', lambda value: value['execution_policy'] == prior['execution_policy'])
    inspection, direct, diagnostic = _input_inspect_run(root, original, monkeypatch)
    _review(root, inspection['id'], diagnostic, parent_preview=direct)
    for relative in ('artifacts/linear-a.fit', 'artifacts/parent.fit'):
        shutil.copyfile(data/relative, root/relative)
    frozen = []
    for name in ('060-stars', '070-stretch'):
        record = core.load_json(data/'runs'/f'{name}.json')
        source = root/('artifacts/linear-a.fit' if name == '060-stars' else 'artifacts/parent.fit')
        record.update(status='success', image_domain='linear' if name == '060-stars' else 'nonlinear',
            primary_output=source.relative_to(root).as_posix(),
            outputs=[{'path': source.relative_to(root).as_posix(), **fingerprint(source, include_path=False)}],
            branch_binding={'separation_run': '060-stars'})
        core.atomic_write_json(root/'runs'/f'{name}.json', record)
        frozen.append(record)
    load_run = core._load_run
    def load(session, name):
        record = next((row for row in frozen if row['id'] == name), None)
        return (root/'runs'/f'{name}.json', record) if record else load_run(session, name)
    monkeypatch.setattr(core, '_load_run', load)
    verified = session_state._verified_success_receipts
    def prior_receipts(session):
        return [*frozen, *verified(session)]
    monkeypatch.setattr(session_state, '_verified_success_receipts', prior_receipts)
    for record in frozen:
        _review(root, record['id'], root/record['primary_output'], parent_preview=direct)
    script = root/'scripts/090-recompose.ssf'
    text = (data/sample['script']['path']).read_text().replace(str(data/'artifacts'), str(root/'artifacts'))
    text += f'\nsavejpg "{root}/previews/unusual-final" 95\nclose\n'
    script.write_text(text)
    _write_ssf_provenance(root, script, 'stars.recompose')
    parent, primary = root/sample['source_path'], root/sample['primary_output']
    final_script, final = _delivery_script(root, primary)
    def execute(command, **_kwargs):
        executing = Path(next(value.split('=', 1)[1] for value in command if value.startswith('--script=')))
        if executing == script:
            for output in sample['outputs']:
                target = root/output['path']
                if target.suffix == '.jpeg':
                    _write_jpeg(root/'previews/unusual-final.jpg', size=(40, 40))
                else:
                    shutil.copyfile(data/output['path'], target)
        else:
            assert executing == final_script
            _write_delivery_outputs(primary, final)
        return subprocess.CompletedProcess(command, 0, '')
    monkeypatch.setattr(core.subprocess, 'run', execute)
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **_: dict(passed=True,
        decoder={'artifact_fingerprint': fingerprint(path, include_path=False)}))
    expected = [root/row['path'] for row in sample['outputs'] if not row['path'].endswith('.jpeg')]
    expected.append(root/'previews/unusual-final.jpg')
    run = core.run_script(str(root), protocol='stars.recompose', script_value=str(script), source_value=str(parent),
        expected_values=list(map(str, expected)), primary_output_value=str(primary),
        separation_run='060-stars', stretch_run='070-stretch', timeout=30)
    draft = accepted_draft(root, run)
    review = root/'reviews/090-recompose.json'
    core.atomic_write_json(review, draft)
    assert core.check_review(root, run['id'], review)['status'] == 'review_valid'
    delivery = core.run_script(str(root), protocol='delivery.render', script_value=str(final_script),
        source_value=str(primary), expected_values=[str(final), str(final.with_suffix('.fit'))], timeout=30)
    review = root/'reviews/120-delivery.json'
    core.atomic_write_json(review, accepted_draft(root, delivery))
    selection = _selection(root, delivery['id'], final, review)
    monkeypatch.setattr(session_state, 'execution_policy_is_current', current_policy)
    assert core.finalize_session(str(root), selection_value=str(selection), keep_intermediates=True)['status'] == 'success'


@pytest.mark.parametrize('target_visible', [False, True])
@pytest.mark.parametrize('hard_failure', [None, 'black', 'white'])
def test_delivery_low_global_span_is_never_an_automatic_accept(tmp_path, target_visible, hard_failure):
    values = np.full((100, 100, 3), 37 if hard_failure is None else 5 if hard_failure == 'black' else 220, dtype=np.uint8)
    if target_visible:
        values[46:54, 46:54] = 150
    jpeg = tmp_path/'display.jpg'; Image.fromarray(values).save(jpeg, quality=95)
    current = native.measure_histogram_gate(jpeg, policy='delivery_histogram_gate_v2')
    assert current['warnings'][0]['code'] == 'global_contrast_span_low'
    assert current['status'] == ('passed' if hard_failure is None else 'failed')
    assert 'accept' not in current.values()
    old = native.measure_histogram_gate(jpeg)
    assert old['status'] == 'failed'
    assert 'warnings' not in old


@pytest.mark.parametrize('keep', [False, True])
def test_v2_warning_delivery_pause_accept_resume_and_immutable_commit(tmp_path, monkeypatch, keep):
    old, _, _ = _session(tmp_path, monkeypatch)
    source = tmp_path/'visible-master.fit'
    values = np.full((1, 100, 100), .145); values[:, 46:54, 46:54] = .6
    write_fits(source, values)
    root = tmp_path/'new'
    session_state.init_session(str(source), str(root), input_state='nonlinear',
        state_evidence=['user confirmed input_state=nonlinear; synthetic stacked fixture'], channel_mode='broadband', channel_map=None,
        target_name='Synthetic small target', target_type='galaxy', style='natural', stars='adaptive',
        offline=False, keep_intermediates=keep)
    run, jpeg = _delivery_run(root, source, monkeypatch)
    assert run['delivery_export']['policy'] == 'paired_fits_jpeg_v2'
    assert run['processing_checks']['delivery_export']['histogram_gate']['warnings']
    assert core.check_review(root, run['id'], None)['status'] == 'review_required'
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    script = root/run['script']['path']
    replay = core.run_script(str(root), protocol='delivery.render', script_value=str(script), source_value=str(source),
        expected_values=[str(root/record['path']) for record in run['outputs']], timeout=30)
    assert replay['replayed'] and all(path.read_bytes() == data for path, data in before.items())
    review = _review(root, run['id'], jpeg)
    selection = _selection(root, run['id'], jpeg, review)
    result = core.finalize_session(str(root), selection_value=str(selection), keep_intermediates=keep)
    assert result['status'] == 'success'
    frozen = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert core.finalize_session(str(root), selection_value=str(selection), keep_intermediates=keep) == result
    assert all(path.read_bytes() == data for path, data in frozen.items())
    review.write_bytes(review.read_bytes()+b' ')
    with pytest.raises(core.ContractError, match='Committed evidence changed'):
        core.finalize_session(str(root), selection_value=str(selection), keep_intermediates=keep)


def test_v6_snapshot_is_complete_and_read_only():
    previous = [entry for entry in session_state._EXECUTION_POLICY_HISTORY if entry.get('guidance') == 'stage_starnet_precision_v6']
    assert len(previous) == 1 and session_state.known_execution_policy(previous[0])
    assert previous[0]['star_diagnostics'] == 'subject_roi_residual_v1'
    assert previous[0]['delivery'] == 'paired_fits_jpeg_v1'
    assert not session_state.execution_policy_is_current({'execution_policy': previous[0]})

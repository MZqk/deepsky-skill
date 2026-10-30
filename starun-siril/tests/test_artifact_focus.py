"""Review-only artifact areas preserve scientific transforms and trial accounting."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

import deep_sky_siril_artifact_focus as focus
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_stepwise as stepwise
from .test_deep_sky_siril_stretch import context
from .test_stretch_routing_v29 import routing_case
from .test_stretch_stepwise import prepare, submit, read, next_ghs, complete_main
from .test_starnet_linear_bridge import native
from .test_recomposition_sources_v11 import linear_case, accept
from .test_stretch_v22 import field_request


def add_area(root, old, *, name='071-focus', area_id='star-edge', rect=None, risk='halo'):
    value = copy.deepcopy(read(root/old['path']))
    value.update(previous=old, none_reason=None)
    value['areas'].append({'id': area_id, 'rect': rect or [76, 14, 88, 26], 'risks': [risk],
        'reason': 'Inspect the stellar boundary and its surrounding texture in this actual synthetic scene.'})
    path = root/'reports'/(name+'.json')
    focus.write(path, value)
    return path, stretch.record(root, path)


def first_prefix(case):
    root = case[0]
    prepared, goals = prepare(case)
    first = stretch.solve_stretch_parameters(root, prepared['request'], '071-prefix')
    assert first['status'] == 'awaiting_next_step'
    return prepared, goals, first


def test_source_materials_do_not_run_siril_or_change_scientific_rois(routing_case, monkeypatch):
    root, _, payload, _, request, _ = routing_case
    prepared, goals = prepare(routing_case)
    path, binding = add_area(root, goals['artifact_focus'])
    before = (root/request['regions']['path']).read_bytes()
    monkeypatch.setattr(stretch.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Artifact materials cannot launch Siril'))
    result = focus.prepare(root, path, '071-source-focus', source=request['source']['path'])
    packet = focus.verify_report(root, payload, result['report'])
    assert packet['phase'] == 'initial' and packet['scientific_parent'] is False
    assert result['scientific_attempts_added'] == 0
    assert not list(root.glob('reports/*/scientific-attempt.json'))
    assert (root/request['regions']['path']).read_bytes() == before
    with Image.open(root/packet['panel']['path']) as panel, Image.open(root/packet['frames']['linear_reference']['path']) as original:
        area = read(path)['areas'][0]['rect']
        width, height = area[2]-area[0], area[3]-area[1]
        top = panel.height-(height+48)+24
        assert np.array_equal(np.array(panel.crop((8, top, 8+width, top+height))), np.array(original.convert('RGB').crop(tuple(area))))


def test_appended_areas_are_reviewed_without_a_new_candidate(routing_case):
    root, _, payload, _, request, _ = routing_case
    prepared, goals, first = first_prefix(routing_case)
    frozen = read(first['parameters'])
    regions = (root/request['regions']['path']).read_bytes()
    path, binding = add_area(root, goals['artifact_focus'])
    result = focus.prepare(root, path, '071-artifacts', parameters=first['parameters'])
    proposal = submit(root, first, step=next_ghs(goals))
    data = read(proposal)
    row = data['feedback']['artifacts']['areas'][0]
    row.update(change='new_or_worse', observation='A slight ring is more visible in the inspected synthetic crop.',
        decision_reason='Retain the small peripheral cost to compare the next controlled subject transformation.')
    proposal.write_text(json.dumps(data))
    second = stretch.solve_stretch_parameters(root, proposal, '072-prefix')
    assert second['status'] == 'awaiting_next_step'
    report, _, _ = stepwise.load_report(root, payload, second['parameters'])
    assert report['steps'][0] == frozen['steps'][0]
    assert report['candidate'] == frozen['candidate']
    assert (root/request['regions']['path']).read_bytes() == regions
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1
    pending = stepwise.status(root, payload, second['parameters'])['artifact_review']
    assert pending['pending_areas'] == ['star-edge'] and not pending['materials_prepared']
    focus.prepare(root, path, '072-artifacts', parameters=second['parameters'])
    final = stepwise.finish(root, submit(root, second, action='finish', name='073-finish'), '073-final')
    assert final['status'] == 'solved'


@pytest.mark.parametrize('fault', ['missing_area', 'blank_observation', 'blank_reason', 'wrong_risk', 'missing_evidence', 'wrong_target', 'wrong_focus', 'panel_drift'])
def test_unbound_or_incomplete_artifact_feedback_is_rejected(routing_case, fault):
    root, _, _, *_ = routing_case
    _, goals, first = first_prefix(routing_case)
    path, _ = add_area(root, goals['artifact_focus'])
    packet = focus.prepare(root, path, '071-artifacts', parameters=first['parameters'])
    proposal = submit(root, first, step=next_ghs(goals))
    value = read(proposal)
    block = value['feedback']['artifacts']
    if fault == 'missing_area': block['areas'] = []
    elif fault == 'blank_observation': block['areas'][0]['observation'] = ' '
    elif fault == 'blank_reason': block['areas'][0]['decision_reason'] = ''
    elif fault == 'wrong_risk': block['areas'][0]['risks'] = ['color_fringe']
    elif fault == 'missing_evidence': block['areas'][0]['evidence'] = []
    elif fault == 'wrong_focus': block['focus'] = goals['artifact_focus']
    elif fault == 'wrong_target':
        source_packet = focus.prepare(root, path, '071-other-target', source=read(first['parameters'])['request']['source']['path'])
        block['report'] = source_packet['report']
    else:
        image = Path(packet['panel']); image.write_bytes(image.read_bytes()+b'drift')
    proposal.write_text(json.dumps(value))
    with pytest.raises(contract.ContractError):
        stretch.solve_stretch_parameters(root, proposal, '072-rejected')
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


@pytest.mark.parametrize('fault', ['outside', 'role', 'risks', 'source', 'geometry', 'no_reason', 'no_preview', 'remove', 'move'])
def test_focus_definitions_preserve_actual_geometry_and_existing_areas(routing_case, fault):
    root, _, payload, *_ = routing_case
    _, goals, first = first_prefix(routing_case)
    path, binding = add_area(root, goals['artifact_focus'])
    focus.prepare(root, path, '071-artifacts', parameters=first['parameters'])
    new_path, _ = add_area(root, binding, name='072-focus', area_id='core-edge', rect=[54, 52, 74, 74], risk='ringing')
    value = read(new_path)
    if fault == 'outside': value['areas'][-1]['rect'][2] = 1000
    elif fault == 'role': value['areas'][-1]['role'] = 'artifact'
    elif fault == 'risks': value['areas'][-1]['risks'] = []
    elif fault == 'source': value['source']['sha256'] = 'f'*64
    elif fault == 'geometry': value['geometry']['width'] = 1000
    elif fault == 'no_reason': value['areas'][-1]['reason'] = ''
    elif fault == 'no_preview': value['evidence'] = []
    elif fault == 'remove': value['areas'].pop(0)
    else: value['areas'][0]['rect'][0] -= 1
    new_path.write_text(json.dumps(value))
    with pytest.raises(contract.ContractError):
        focus.prepare(root, new_path, '072-rejected', parameters=first['parameters'])
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_concurrent_additions_require_a_union_and_cannot_drop_areas(routing_case):
    root, _, payload, *_ = routing_case
    _, goals, first = first_prefix(routing_case)
    paths = [add_area(root, goals['artifact_focus'], name='071-focus-'+str(i), area_id='edge-'+str(i))[0] for i in range(2)]
    def prepare_one(index):
        try:
            return focus.prepare(root, paths[index], '071-materials-'+str(index), parameters=first['parameters'])
        except contract.ContractError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare_one, range(2)))
    winner = next(row for row in results if isinstance(row, dict))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert any('latest registered' in row for row in results if isinstance(row, str))
    path, binding = add_area(root, winner['focus'], name='072-union', area_id='other-edge', rect=[54, 52, 74, 74])
    focus.prepare(root, path, '072-union-materials', parameters=first['parameters'])
    plan, focus_goals, _ = focus.plan_for(root, payload, read(first['parameters'])['request']['strategy'])
    assert focus.head(root, payload, plan, focus_goals) == binding
    assert len(focus.definition(root, payload, binding, plan['source'])['areas']) == 2
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_old_prefix_evidence_stays_valid_after_additions(routing_case):
    root, _, payload, *_ = routing_case
    _, goals, first = first_prefix(routing_case)
    second = stretch.solve_stretch_parameters(root, submit(root, first, step=next_ghs(goals)), '072-prefix')
    path, _ = add_area(root, goals['artifact_focus'])
    focus.prepare(root, path, '072-artifacts', parameters=second['parameters'])
    stepwise.load_report(root, payload, first['parameters'])
    stepwise.load_report(root, payload, second['parameters'])
    stale = submit(root, second, action='finish', name='073-stale')
    value = read(stale)
    panel = read(root/value['feedback']['artifacts']['report']['path'])['panel']['path']
    value['feedback']['materials'] = [r for r in value['feedback']['materials'] if r['path'] != panel]
    value['feedback']['artifacts'] = {'focus': goals['artifact_focus'], 'report': None, 'areas': [],
        'none_reason': read(root/goals['artifact_focus']['path'])['none_reason']}
    stale.write_text(json.dumps(value))
    with pytest.raises(contract.ContractError, match='newly registered'):
        stepwise.finish(root, stale, '073-rejected')


def test_hdr_branches_share_appended_focus_areas(routing_case):
    root, _, payload, *_ = routing_case
    prepared, goals = prepare(routing_case, hdr=True)
    gentle = stretch.solve_stretch_parameters(root, prepared['request'], '071-gentle')
    path, binding = add_area(root, goals['artifact_focus'], risk='hdr_transition')
    focus.prepare(root, path, '071-artifacts', parameters=gentle['parameters'])
    end = stepwise.finish(root, submit(root, gentle, action='finish_branch', name='072-gentle-end'), '072-gentle-end')
    focus.prepare(root, path, '072-artifacts', parameters=end['parameters'])
    faint = stretch.solve_stretch_parameters(root, submit(root, end, role='faint', step=goals['initial'], name='073-faint'), '073-faint')
    assert faint['status'] == 'awaiting_next_step'
    report, _, _ = stepwise.load_report(root, payload, faint['parameters'])
    assert report['proposal']['feedback']['artifacts']['focus'] == binding
    assert report['proposal']['feedback']['artifacts']['areas'][0]['risks'] == ['hdr_transition']
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_native_formal_review_records_tradeoffs_and_preserves_material_limit(linear_case):
    root, _, _, source, _, _, _ = linear_case
    _, request = field_request(root, source)
    regions = read(root/request['regions']['path'])['regions']
    payload = read(root/'session.json')
    case = (root, source, payload, None, request, regions)
    prepared, goals = prepare(case)
    final = complete_main(root, prepared, goals, '071-native')
    path, binding = add_area(root, goals['artifact_focus'], rect=[90, 90, 130, 130], risk='separation_patch')
    focus.prepare(root, path, '072-focus', parameters=final['parameters'])
    assets = stretch.generate_stretch_assets(root, parameters=final['parameters'], run_id='074-formal')
    receipt = core.run_script(root, protocol='stretch', script_value=assets['script_path'], source_value=source,
        primary_output_value=assets['primary_output'], expected_values=[assets['primary_output'], assets['preview_output']], timeout=60)
    assert receipt['status'] == 'success', receipt
    draft = core.check_review(root, receipt['id'], None)['review']
    assert draft['stretch_assessment']['artifacts']['report'] is None
    packet = focus.prepare(root, path, '074-focus', run=receipt['id'])
    draft = core.check_review(root, receipt['id'], None)['review']
    assert draft['stretch_assessment']['artifacts']['report'] == packet['report']
    assert len(draft['inspected_materials']) <= 32
    visibility = read(root/receipt['stretch_visibility']['path'])
    assert visibility['panel']['path'] not in {r['path'] for r in draft['inspected_materials']}
    review_path = accept(root, receipt['id'])
    review = read(review_path)
    area = review['stretch_assessment']['artifacts']['areas'][0]
    area.update(change='new_or_worse', observation='A minor peripheral patch is more conspicuous in the bound comparison.',
        decision_reason='The documented peripheral cost is acceptable for this controlled comparison; existing gates still pass.')
    review_path.write_text(json.dumps(review))
    assert core.check_review(root, receipt['id'], review_path)['status'] == 'review_valid'
    bad = copy.deepcopy(review)
    bad['gates']['structure'] = 'fail'
    rejected = root/'reviews/074-hard-gate.json'
    focus.write(rejected, bad)
    with pytest.raises(contract.ContractError, match='failed or uncertain gate'):
        core.check_review(root, receipt['id'], rejected)
    rejected.unlink()
    more, _ = add_area(root, binding, name='075-new-focus', area_id='core-edge', rect=[244, 244, 270, 270], risk='ringing')
    with pytest.raises(contract.ContractError, match='submitted formal review'):
        focus.prepare(root, more, '075-rejected', run=receipt['id'])
    assert focus.retained_paths(root, payload)
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


def test_outgoing_policy_and_helpers_remain_immutable(tmp_path, monkeypatch):
    import deep_sky_siril_session as sessions
    import deep_sky_siril_stretch_v2 as protected
    from ._policy_pins import replay_and_finalize_without_migration
    history = Path(__file__).parents[1]/'references/artifact-focus-history.json'
    prior = read(history)
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(prior['execution_policy'])
    assert sessions.known_execution_policy(prior['execution_policy'])
    assert not sessions.execution_policy_is_current(prior)
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': prior['execution_policy']})
    assert protected.implementation(tmp_path) == prior['implementation']
    assert protected.profiles_fingerprint(tmp_path) == prior['profiles']
    replay_and_finalize_without_migration(tmp_path/'replay', monkeypatch, history)


def test_batch_parameters_use_existing_native_prefixes_without_rewriting_them(routing_case):
    from .test_stretch_routing_v29 import prepare as batch_prepare
    root, _, payload, *_ = routing_case
    prepared = batch_prepare(routing_case)
    item = prepared['candidates']['profile']
    result = stretch.solve_stretch_parameters(root, item['request'], item['run_id'])
    path = Path(result['parameters_path'])
    original = path.read_bytes()
    plan = read(prepared['strategy'])
    goals = read(root/plan['goals']['path'])
    definition_path, _ = add_area(root, goals['artifact_focus'])
    material = focus.prepare(root, definition_path, '073-batch-focus', parameters=path)
    packet = focus.verify_report(root, payload, material['report'])
    report = read(path)
    assert packet['frames']['candidate'] == report['steps'][-1]['selected']['.jpg']
    assert packet['frames']['previous_prefix'] == report['steps'][-2]['selected']['.jpg']
    assert path.read_bytes() == original
    assert len(list(root.glob('reports/*/scientific-attempt.json'))) == 1


@pytest.mark.parametrize('fault', ['reason', 'preview', 'missing_binding'])
def test_empty_focus_declarations_still_need_explicit_source_evidence(routing_case, fault):
    root, _, payload, *_ = routing_case
    _, goals = prepare(routing_case)
    if fault == 'missing_binding':
        goals.pop('artifact_focus')
        with pytest.raises(contract.ContractError, match='initial review-focus'):
            prepare(routing_case, name='072-missing-focus', goals=goals)
    else:
        value = read(root/goals['artifact_focus']['path'])
        value['none_reason' if fault == 'reason' else 'evidence'] = '' if fault == 'reason' else []
        path = root/'reports/072-invalid-focus.json'
        focus.write(path, value)
        with pytest.raises(contract.ContractError):
            focus.definition(root, payload, stretch.record(root, path), value['source'])
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_pending_focus_queries_remain_read_only(routing_case, monkeypatch):
    root, _, payload, *_ = routing_case
    _, goals, first = first_prefix(routing_case)
    path, _ = add_area(root, goals['artifact_focus'])
    focus.prepare(root, path, '071-artifacts', parameters=first['parameters'])
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(stretch.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Recovery must not launch Siril'))
    result = stepwise.status(root, payload, first['parameters'])
    assert result['artifact_review']['pending_areas'] == ['star-edge']
    assert result['artifact_review']['materials_prepared']
    assert all(p.read_bytes() == data for p, data in before.items())
    assert set(before) == {p for p in root.rglob('*') if p.is_file()}


def test_artifact_focus_cli_prepares_materials_without_launching_siril(routing_case, monkeypatch, capsys):
    root, _, _, _, request, _ = routing_case
    _, goals = prepare(routing_case)
    path, _ = add_area(root, goals['artifact_focus'])
    monkeypatch.setattr(stretch.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Artifact CLI must not launch Siril'))
    monkeypatch.setattr('sys.argv', ['stretch', 'artifact-focus', '--session', str(root), '--focus', str(path),
        '--source', request['source']['path'], '--run-id', '071-focus-cli'])
    assert stretch.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'prepared' and result['scientific_attempts_added'] == 0
    assert (root/result['report']['path']).exists()
    assert not list(root.glob('reports/*/scientific-attempt.json'))

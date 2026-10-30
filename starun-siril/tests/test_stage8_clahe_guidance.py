"""Executable guidance examples and exact, immutable outgoing receipt replay."""
import copy
import json
from pathlib import Path
import re

import numpy as np
import pytest
from PIL import Image

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_local_contrast as local
import deep_sky_siril_session as sessions
from . import test_deep_sky_siril as support
from .test_local_hdr import scene, solve, execute, review
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-08-starless-enhance.md'
OUTGOING = {'sha256': 'debaccf041ba746bb59e9da2828bc7b0dc6c8f314d8474cda392f91b8f9f5656', 'size': 28526}


def examples():
    section = (ROOT/DOCUMENT).read_text().split('<a id="clahe-shell-comparison"></a>', 1)[1]
    section = section.split('<a id="protected-local-contrast"></a>', 1)[0]
    return re.findall(r'^```python\n(.*?)^```', section, re.M | re.S)


def run_example(code, session):
    code = code.replace('Path("/abs/session")', f'Path({str(session)!r})')
    return exec(compile(code, DOCUMENT, 'exec'), {})


@pytest.mark.parametrize('candidate,mandatory', [('wavelet', 29), ('clahe', 22)])
def test_examples_preserve_siblings_and_one_to_one_scientific_crops(scene, capsys, candidate, mandatory):
    session, _, original, simulator = scene
    request = contract.load_json(original)
    regions_path = session/request['regions']
    regions = contract.load_json(regions_path)
    regions['regions'][-1]['id'] = 'shell-main'
    regions['regions'] += [
        {'id': 'shell-other', 'role': 'faint', 'rect': [176, 128, 200, 144]},
        {'id': 'dark-lane', 'role': 'faint', 'rect': [128, 176, 144, 192]},
        {'id': 'bright-core', 'role': 'core', 'rect': [240, 176, 248, 192]}]
    contract.atomic_write_json(regions_path, regions)
    contract.atomic_write_json(session/'reports/080-request.json', request)
    requests_code, panel_code = examples()
    run_example(requests_code, session)
    paths = [session/f'reports/080-clahe-{suffix}-request.json' for suffix in ('010', '015')]
    requests = [contract.load_json(path) for path in paths]
    expected = copy.deepcopy(requests[0]); expected['clahe']['strength'] = .15
    assert requests[0]['clahe']['strength'] == .10 and expected == requests[1]
    choices = [local.request_candidates(value) for value in requests]
    assert choices[0]['wavelet'] == choices[1]['wavelet']
    assert all(c['clahe']['cliplimit'] == 1.5 and c['clahe']['grid'] == 8 for c in choices)
    results = [local.solve(session, path, f'080-clahe-{suffix}')
               for path, suffix in zip(paths, ('010', '015'))]
    reports = [local.load_parameters(session, result['parameters']) for result in results]
    assert len(simulator.calls) == 4
    assert all(list(report['candidates']) == ['clahe', 'wavelet'] for report in reports)
    science_before = {p: p.read_bytes() for p in session.rglob('*.fit')}
    run_example(panel_code, session)
    record = json.loads(capsys.readouterr().out)
    assert record['selection'] is None and all(v is None for v in record['observations'].values())
    assert science_before == {p: p.read_bytes() for p in session.rglob('*.fit')}
    panel = Image.open(session/record['bindings']['panel']['path'])
    rects = [row['rect'] for row in regions['regions']]
    cell_width = max(x1-x0 for x0, y0, x1, y1 in rects)
    source = local.read_scientific_pixels(session/reports[0]['bindings']['source']['path'])[0]
    height, width = source.shape[1:]
    scale = min(1.0, 1024/max(width, height))
    tw, th = max(1, round(width*scale)), max(1, round(height*scale))
    column_width = max(cell_width, tw)
    assert panel.size == (5*(column_width+8)+8, th+32+sum(y1-y0+32 for x0, y0, x1, y1 in rects)+8)
    top = 8+th+32
    columns = [('parent', reports[0]['bindings']['source']['path']), *[
        (f'{method}-{suffix}', report['candidates'][method]['output']['path'])
        for method in ('wavelet', 'clahe') for suffix, report in zip(('010', '015'), reports)]]
    for region in regions['regions']:
        x0, y0, x1, y1 = region['rect']
        for column, (label, path) in enumerate(columns):
            values = local.read_scientific_pixels(session/path)[0]
            crop = values[:, values.shape[1]-y1:values.shape[1]-y0, x0:x1][:, ::-1]
            expected = np.rint(np.clip(np.moveaxis(crop, 0, -1), 0, 1)*255).astype(np.uint8)
            left = 8+column*(column_width+8)
            assert np.array_equal(np.asarray(panel.crop((left, top+24, left+x1-x0, top+24+y1-y0))), expected)
            if region['id'] == 'shell-main':
                amplitude = next(row['amplitude'] for row in local.energies(values, regions['regions'], scales=(2,))
                                 if row['id'] == 'shell-main')
                assert record['E2'][label] == amplitude
        top += y1-y0+32
    assert np.array_equal(local.read_scientific_pixels(session/columns[1][1])[0],
                          local.read_scientific_pixels(session/columns[2][1])[0])
    assert all((session/row['output']['path']).exists() for report in reports for row in report['candidates'].values())
    for label, gain in record['gains'].items():
        assert gain == record['E2'][label]/record['E2']['parent']
    run, _ = execute(scene, results[0]['parameters'], candidate)
    draft = core.check_review(session, run['id'], None)['review']
    assert len(draft['inspected_materials']) == mandatory and draft['verdict'] is None
    record.update(observations={r['id']: 'Synthetic comparison inspected' for r in regions['regions']},
                  selection=candidate, reason='Synthetic material binding check')
    comparison = session/'reports/080-comparison.json'; contract.atomic_write_json(comparison, record)
    for path in (Path(results[1]['parameters']), session/record['bindings']['panel']['path'], comparison):
        draft['inspected_materials'].append({'path': str(path.relative_to(session)), 'sha256': contract.sha256_file(path)})
    assert len(draft['inspected_materials']) == mandatory+3 <= 32
    draft.update(verdict='accept', gates={key: 'pass' for key in draft['gates']}, notes='Synthetic comparison binding check')
    saved = session/'reviews'/f"{run['id']}.json"; contract.atomic_write_json(saved, draft)
    assert core.check_review(session, run['id'], saved)['status'] == 'review_valid'


def test_outgoing_stage8_guidance_registration_is_exact():
    payload = {'execution_policy': sessions._PRE_DEEPSNR_EXECUTION_POLICY}
    record = {'path': DOCUMENT, **OUTGOING}
    assert sessions._CLAHE_COMPARISON_REPLAY_REFERENCES == {DOCUMENT: OUTGOING}
    assert contract.stable_hash(payload['execution_policy']) == sessions._CLAHE_COMPARISON_REPLAY_POLICY_HASH
    assert sessions.registered_guidance_reference(payload, record)
    assert (OUTGOING['sha256'], OUTGOING['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'sha256': '0'*64}, {'size': OUTGOING['size']+1}, {'path': DOCUMENT+'.unknown'}):
        assert not sessions.registered_guidance_reference(payload, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


@pytest.fixture
def predecessor_policy(monkeypatch):
    monkeypatch.setattr(contract, 'EXECUTION_POLICY', dict(sessions._PRE_DEEPSNR_EXECUTION_POLICY))
    monkeypatch.setattr(sessions, 'EXECUTION_POLICY', dict(sessions._PRE_DEEPSNR_EXECUTION_POLICY))


def test_outgoing_stage8_receipt_replays_without_rewriting_evidence(predecessor_policy, scene):
    result = solve(scene); run, args = execute(scene, result['parameters']); review(scene, run)
    _freeze_historical_evidence(scene[0], run_id=run['id'], snapshot={
        'execution_policy': contract.EXECUTION_POLICY, 'documents': {DOCUMENT: OUTGOING}})
    before = support._session_bytes(scene[0]); calls = len(scene[3].calls)
    assert core.run_script(scene[0], **args)['replayed']
    assert core.check_review(scene[0], run['id'], scene[0]/f"reviews/{run['id']}.json")['status'] == 'review_valid'
    assert len(scene[3].calls) == calls and support._session_bytes(scene[0]) == before

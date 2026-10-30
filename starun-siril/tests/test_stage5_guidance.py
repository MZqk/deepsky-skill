"""Executable scale summaries and immutable pre-guidance Stage 5 evidence."""
import json
from pathlib import Path
import re
import subprocess

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_denoise as denoise
import deep_sky_siril_session as sessions
from . import test_deep_sky_siril as support
from .test_linear_denoise import case, fallback_request, MockNative, regions
from .test_psf_spatial import v2_run
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-05-linear-restoration.md'
OUTGOING = {
    'SKILL.md': {'sha256': '147d2d60f6b1c536d4ce0c20a9c020267ffaeac414c87995d3484a561cf24e3e', 'size': 26317},
    DOCUMENT: {'sha256': 'f22d0da605503fee1e4fb8c7aeee8d4f8462989e1c6b651068051e840df5adb1', 'size': 27995}}


def run_summary(session, analysis, capsys):
    path = session/'reports/050-denoise-analysis.json'
    contract.atomic_write_json(path, analysis)
    before = support._session_bytes(session)
    section = (ROOT/DOCUMENT).read_text().split('<a id="stage5-diagnostic-summary"></a>', 1)[1]
    section = section.split('下例 v1', 1)[0]
    code, = re.findall(r'^```python\n(.*?)^```', section, re.M | re.S)
    code = code.replace('Path("/abs/session")', f'Path({str(session)!r})')
    exec(compile(code, DOCUMENT, 'exec'), {})
    result = json.loads(capsys.readouterr().out)
    assert support._session_bytes(session) == before
    return result


def test_summary_consumes_actual_analysis_and_preserves_unmeasurable_scales(case, capsys):
    session, _, payload, _, request = case
    analysis, _ = denoise.analyze_source(session, payload, request['source'], request['regions'], request['display_mtf'])
    result = run_summary(session, analysis, capsys)
    assert {key: result[key] for key in ('source', 'regions', 'display_mtf')} == {
        key: analysis[key] for key in ('source', 'regions', 'display_mtf')}
    assert [row['sigma'] for row in result['scales']] == [1, 2, 4, 8]
    first = result['scales'][0]
    values = {row['id']: row['amplitude'] for row in analysis['multiscale'] if row['sigma'] == 1}
    assert first['sky_count'] == 1 and first['sky_amplitude_median'] == values['sky']
    assert first['subjects'][0]['diagnostic_ratio'] == pytest.approx(values['filament']/values['sky'])
    for scale in result['scales'][1:]:
        assert scale['sky_count'] == 0 and scale['sky_amplitude_median'] is None
        assert scale['unavailable_sky'] == [{'id': 'sky', 'reason': 'Insufficient real 4-sigma context'}]
        assert all(row['diagnostic_ratio'] is None for row in scale['subjects'])
    assert not {'decision', 'parameters', 'recommended_verdict'} & result.keys()


@pytest.fixture
def analysis(tmp_path):
    session = tmp_path/'session'; (session/'reports').mkdir(parents=True)
    document = {'source': {'path': '@input', 'sha256': 'a'*64},
        'regions': {'path': 'reports/regions.json', 'sha256': 'b'*64}, 'display_mtf': [0, .15, 1],
        'measurements': {'regions': [{'id': name, 'role': role} for name, role in (
            ('sky-a', 'background'), ('sky-b', 'background'), ('shell', 'faint'), ('rim', 'core'))]},
        'multiscale': []}
    for index, sigma in enumerate((1, 2, 4, 8), 1):
        for name, value in [('sky-a', index), ('sky-b', 3*index),
                            ('shell', 2*index*(index+3)), ('rim', 4*index*(index+3))]:
            document['multiscale'].append({'id': name, 'sigma': sigma, 'status': 'measured',
                                           'amplitude': value, 'reason': None})
    return session, document


def test_summary_uses_each_scales_sky_median_and_keeps_each_subject(analysis, capsys):
    session, document = analysis
    result = run_summary(session, document, capsys)
    assert [row['sky_amplitude_median'] for row in result['scales']] == [2, 4, 6, 8]
    assert [row['subjects'][0]['diagnostic_ratio'] for row in result['scales']] == [4, 5, 6, 7]
    assert [row['subjects'][1]['diagnostic_ratio'] for row in result['scales']] == [8, 10, 12, 14]


@pytest.mark.parametrize('issue', ['partial_sky', 'no_sky', 'missing_sky', 'zero_sky',
                                  'missing_subject', 'unmeasurable_subject', 'invalid_subject'])
def test_summary_reports_missing_data_and_zero_denominators_without_filling(analysis, capsys, issue):
    session, document = analysis
    rows = document['multiscale']
    if issue == 'partial_sky':
        rows.remove(next(row for row in rows if row['id'] == 'sky-b' and row['sigma'] == 1))
    elif issue == 'no_sky':
        document['measurements']['regions'] = [r for r in document['measurements']['regions'] if r['role'] != 'background']
    elif issue == 'missing_sky':
        document['multiscale'] = [r for r in rows if not (r['id'].startswith('sky-') and r['sigma'] == 1)]
    elif issue == 'zero_sky':
        for row in rows:
            if row['id'].startswith('sky-') and row['sigma'] == 1: row['amplitude'] = 0
    else:
        subject = next(row for row in rows if row['id'] == 'shell' and row['sigma'] == 1)
        if issue == 'missing_subject': rows.remove(subject)
        elif issue == 'unmeasurable_subject':
            subject.update(status='unmeasurable', amplitude=None, reason='Insufficient real 4-sigma context')
        else: subject['amplitude'] = True
    first = run_summary(session, document, capsys)['scales'][0]
    shell = first['subjects'][0]
    if issue == 'partial_sky':
        assert first['sky_count'] == 1 and first['sky_amplitude_median'] == 1
        assert first['unavailable_sky'] == [{'id': 'sky-b', 'reason': 'Missing scale measurement'}]
        assert shell['diagnostic_ratio'] == 8
    else:
        assert shell['diagnostic_ratio'] is None and shell['ratio_reason']
        if issue in {'no_sky', 'missing_sky'}:
            assert first['sky_count'] == 0 and first['sky_amplitude_median'] is None
        elif issue == 'zero_sky':
            assert first['sky_count'] == 2 and first['sky_amplitude_median'] == 0
            assert shell['ratio_reason'] == 'Zero background amplitude'
        else:
            assert shell['amplitude'] is None and shell['amplitude_reason'] == shell['ratio_reason']


def test_old_guidance_is_registered_only_for_exact_fingerprints_and_policy():
    assert sessions._STAGE5_GUIDANCE_REPLAY_REFERENCES == OUTGOING
    policy = sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY
    assert contract.stable_hash(policy) == sessions._STAGE5_GUIDANCE_REPLAY_POLICY_HASH
    for name, binding in OUTGOING.items():
        record = {'path': name, **binding}
        assert sessions.registered_guidance_reference({'execution_policy': policy}, record)
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for change in ({'path': name+'-unknown'}, {'sha256': '0'*64}, {'size': binding['size']+1}):
            assert not sessions.registered_guidance_reference({'execution_policy': policy}, {**record, **change})
        assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def accept(session, run):
    draft = core.check_review(session, run['id'], None)['review']
    draft.update(verdict='accept', gates={key: 'pass' for key in draft['gates']},
                 notes='Synthetic compatibility fixture; no real image quality claim')
    if run['protocol'] == 'restoration.denoise':
        draft['denoise_assessment'].update(noise_benefit='observed', noise_observation='Known synthetic smoothing',
                                           adoption_reason='Synthetic compatibility fixture')
        for row in draft['denoise_assessment']['structures']:
            row.update(retention='retained', observation='Synthetic compatibility fixture')
    path = session/f"reviews/{run['id']}.json"; contract.atomic_write_json(path, draft)
    assert core.check_review(session, run['id'], path)['status'] == 'review_valid'
    return path


@pytest.mark.parametrize('protocol', ['restoration.denoise', 'restoration.deconvolve'])
def test_old_stage5_reference_replays_and_finalizes_without_rewriting(tmp_path, monkeypatch, protocol):
    if protocol == 'restoration.deconvolve':
        session, _, args, run = v2_run(tmp_path, monkeypatch)
    else:
        original = support._write_fits
        with monkeypatch.context() as patch:
            patch.setattr(support, '_write_fits', lambda path, **kwargs: original(path, **{**kwargs, 'nonzero': True}))
            session, source, _ = support._session(tmp_path, monkeypatch, input_state='linear', channel_mode='mono', width=96, height=96)
        inspected, _, _ = support._input_inspect_run(session, source, monkeypatch)
        accept(session, inspected)
        request = fallback_request(session, {'source': {'path': '@input', 'sha256': contract.sha256_file(source)},
            'regions': regions(session, source), 'display_mtf': [0, .15, 1], 'decision': 'trial',
            'reason': 'Synthetic compatibility fixture', 'parameters': {'modulation': .2, 'cosmetic': False},
            'parameter_reason': 'Synthetic compatibility fixture',
            'manual_lookup': {'status': 'not_needed', 'reason': 'Documented recipe', 'evidence': []}})
        request_path = session/'reports/proposal.json'; contract.atomic_write_json(request_path, request)
        assets = denoise.generate(session, request_path, '050-denoise')
        monkeypatch.setattr(core.subprocess, 'run', MockNative())
        args = dict(protocol=protocol, script_value=assets['script'], source_value=source,
            primary_output_value=assets['primary_output'], expected_values=assets['expected'], timeout=30)
        run = core.run_script(session, **args)
    assert run['status'] == 'success'
    inspected = core.load_json(session/'runs/010-input.json')
    accept(session, inspected)
    accept(session, run)
    # Construct the historical continuation under its original execution contract.
    payload = core.load_json(session/'session.json')
    payload['execution_policy'] = sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY.copy()
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session/'session.json',payload)
    if run.get('linear_denoise'):
        from deep_sky_siril_stretch_v2 import _historical_bindings
        run['linear_denoise']['helper'] = _historical_bindings(session)['implementation']['deep_sky_siril_denoise.py']
        core.atomic_write_json(session/f"runs/{run['id']}.json", run)
    _freeze_historical_evidence(session, snapshot={
        'execution_policy': sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY, 'documents': OUTGOING})
    with monkeypatch.context() as historical:
        historical.setattr(sessions,'EXECUTION_POLICY',sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY)
        source = session/run['primary_output']
        primary = session/'artifacts/070-stretch.fit'; view = session/'previews/070-stretch.jpg'
        script = session/'scripts/070-stretch.ssf'
        script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nmtf 0 0.1 1\n'
                          f'save "{primary}" -chksum\nsavejpg "{view.with_suffix("")}" 95\nclose\n')
        support._write_ssf_provenance(session, script, 'stretch')

        def render(argv, **kwargs):
            support._write_delivery_outputs(source, view)
            view.with_suffix('.fit').replace(primary)
            return subprocess.CompletedProcess(argv, 0, '')

        monkeypatch.setattr(core.subprocess, 'run', render)
        monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
            {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(path, include_path=False)}})
        stretched = core.run_script(session, protocol='stretch', script_value=script, source_value=source,
            primary_output_value=primary, expected_values=[primary, view], timeout=30)
        assert stretched['status'] == 'success', stretched
        accept(session, stretched)
        delivery_script, final = support._delivery_script(session, primary)

        def deliver(argv, **kwargs):
            support._write_delivery_outputs(primary, final)
            return subprocess.CompletedProcess(argv, 0, '')

        monkeypatch.setattr(core.subprocess, 'run', deliver)
        delivered = core.run_script(session, protocol='delivery.render', script_value=delivery_script, source_value=primary,
            expected_values=[final, final.with_suffix('.fit')], timeout=30)
        assert delivered['status'] == 'success', delivered
        review = accept(session, delivered)
    _freeze_historical_evidence(session, snapshot={
        'execution_policy': sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY, 'documents': OUTGOING})
    before = support._session_bytes(session)
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: pytest.fail('Replay or finalize invoked a native process'))
    assert core.run_script(session, **args)['replayed']
    assert core.check_review(session, run['id'], session/f"reviews/{run['id']}.json")['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    selection = support._selection(session, delivered['id'], final, review)
    selected = core.load_json(selection)
    selected['selected_runs'] = [row['id'] for row in (inspected, run, stretched, delivered)]
    selected['review_receipts'] = [f'reviews/{run_id}.json' for run_id in selected['selected_runs']]
    contract.atomic_write_json(selection, selected)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    audit = core.load_json(session/'reports/final-audit.json')
    assert run['id'] in audit['selected_runs']
    for name, data in before.items():
        if name.split('/')[0] in {'runs', 'reviews', 'reports', 'scripts', 'artifacts'}:
            assert (session/name).read_bytes() == data

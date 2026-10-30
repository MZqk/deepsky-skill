"""HDR method eligibility and frozen replay, using simulated Siril I/O only."""
import copy
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pytest
from PIL import Image

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_local_contrast as local
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
import deep_sky_star_reduction as reduction
from . import test_deep_sky_siril as support
from .test_recomposition_proposals import write
from .test_recomposition_proposals import accepted_draft
from .test_recomposition_proposals import write_catalog
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/local-hdr-prior.json').read_text())


class LocalNative:
    """Test arithmetic and recipe equivalence, not Siril's wavelet or CLAHE algorithms."""
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        script = Path(next(a.split('=', 1)[1] for a in argv if a.startswith('--script=')))
        root = Path(next(a.split('=', 1)[1] for a in argv if a.startswith('--directory=')))
        commands = processing.script_commands(script.read_text())
        self.calls.append(commands)
        current, planes = None, None
        for row in commands:
            command = row[0]
            if command == 'load':
                current = (np.asarray(Image.open(row[1]).convert('RGB')).transpose(2, 0, 1)[:, ::-1]/255
                           if Path(row[1]).suffix == '.jpg' else local.read_scientific_pixels(Path(row[1]))[0].copy())
            elif command == 'findstar':
                write_catalog(row, x=150., y=150.)
            elif command == 'mtf':
                low, mid, high = map(float, row[1:]); x = np.clip((current-low)/(high-low), 0, 1)
                current = (mid-1)*x/((2*mid-1)*x-mid)
            elif command == 'split':
                for path, values in zip(row[1:], current):
                    write(Path(path).with_suffix('.fit'), values[None])
            elif command == 'pm':
                images = []
                def image(match):
                    path = Path(match[1]+'.fit')
                    images.append(local.read_scientific_pixels(path if path.is_absolute() else root/path)[0])
                    return f'images[{len(images)-1}]'
                expression = re.sub(r'\$([^$]+)\$', image, row[1]).replace('^', '**')
                current = eval(expression, {'__builtins__': {}},
                               {'images': images, 'min': np.minimum, 'max': np.maximum})
            elif command == 'wavelet':
                current = current.astype(float)
                detail = current-sum(np.roll(current, shift, axis) for shift, axis in
                                     ((1, 1), (-1, 1), (1, 2), (-1, 2)))/4
                planes = [detail/(2**i) for i in range(5)]
                planes.append(current-sum(planes))
            elif command == 'wrecons':
                current = sum(float(c)*plane for c, plane in zip(row[1:], planes))
            elif command == 'clahe':
                current = current.mean()+(current-current.mean())*1.03
            elif command == 'gauss':
                current = current.copy()
            elif command == 'rgbcomp':
                current = np.concatenate([local.read_scientific_pixels(Path(p))[0] for p in row[1:4]])
                write(Path(row[4][5:]).with_suffix('.fit'), current)
            elif command == 'save':
                write(Path(row[1]).with_suffix('.fit'), current)
            elif command == 'savejpg':
                values = np.repeat(current, 3, axis=0) if len(current) == 1 else current
                Image.fromarray(np.uint8(np.clip(values[:, ::-1].transpose(1, 2, 0), 0, 1)*255)).save(Path(row[1]).with_suffix('.jpg'))
        channels, height, width = current.shape
        stats = ''.join(c+' layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n'
                        for c in (('B&W',) if channels == 1 else ('Red', 'Green', 'Blue')))
        return subprocess.CompletedProcess(argv, 0,
            f'Reading FITS: file fixture.fit, {channels} layer(s), {width}x{height} pixels, 32 bits\n'+stats)


@pytest.fixture
def scene(tmp_path, monkeypatch, request):
    mode = getattr(request, 'param', 'single')
    y, x = np.indices((384, 384)); rng = np.random.default_rng(72)
    lum = .1+rng.normal(0, .0005, x.shape)
    lum[160:224, 160:224] += (.015*np.sin(x*.7)*np.cos(y*.9))[160:224, 160:224]
    values = np.stack([lum*1.05, lum, lum*.95])
    with monkeypatch.context() as scoped:
        scoped.setattr(support, '_write_fits', lambda path, **kwargs: write(path, values))
        session, original, _ = support._session(tmp_path, monkeypatch, input_state='linear', width=384, height=384)
    inspected, direct, diagnostic = support._input_inspect_run(session, original, monkeypatch)
    support._review(session, inspected['id'], diagnostic, parent_preview=direct)
    full = write(session/'artifacts/full.fit', values)
    linear = write(session/'artifacts/linear-starless.fit', values*.8)
    parent = write(session/'artifacts/nonlinear-starless.fit', values*.8)
    chain = [{'command': 'mtf', 'parameters': ['0', '.5', '1']}]
    records = [
        {'id': '060-stars', 'protocol': 'stars.separate', 'source_path': '@input',
         'primary_output': 'artifacts/linear-starless.fit', 'image_domain': 'linear',
         'star_separation': {'full_source': 'artifacts/full.fit'}},
        {'id': '070-stretch', 'protocol': 'stretch', 'source_path': 'artifacts/linear-starless.fit',
         'primary_output': 'artifacts/nonlinear-starless.fit', 'image_domain': 'nonlinear', 'transfer_chain': chain}]
    if mode.startswith('hdr'):
        mask = write(session/'artifacts/frozen-mask.fit', np.full((1, 384, 384), .4))
        rgb_mask = write(session/'artifacts/frozen-mask-rgb.fit', np.full_like(values, .4))
        records[1].update(transfer_chain=[], hdr_transfer={'gentle_chain': chain, 'faint_chain': chain,
            'mask': {'path': str(mask.relative_to(session)), 'blend_path': str(rgb_mask.relative_to(session))}})
    if mode == 'hoo':
        parameters = session/'reports/hoo-parameters.json'; parameters.write_text('{}')
        records[1]['dualband_transfer'] = {'parameters': reduction.record(session, parameters)}
    if mode in {'hdr-tone', 'hdr-color'}:
        parent = write(session/'artifacts/adjusted.fit', values*.8+.001)
        records.append({'id': '075-adjust', 'protocol': 'tone.adjust' if mode == 'hdr-tone' else 'color.finish',
            'source_path': 'artifacts/nonlinear-starless.fit', 'primary_output': 'artifacts/adjusted.fit',
            'image_domain': 'nonlinear', 'transfer_chain': []})
    manifest = contract.load_json(session/'manifest.json')
    for row in records:
        row.update(schema='starun-siril.run-receipt.v1', status='success', branch_binding={'separation_run': '060-stars'},
                   outputs=[reduction.record(session, session/row['primary_output'])])
        if mode.startswith('hdr') and row['id'] == '070-stretch':
            row['outputs'].extend(reduction.record(session, p) for p in (mask, rgb_mask))
        path = session/'runs'/(row['id']+'.json'); contract.atomic_write_json(path, row)
        manifest['runs'].append({'id': row['id'], 'status': 'success', 'receipt': path.name,
                                'receipt_sha256': contract.sha256_file(path)})
    contract.atomic_write_json(session/'manifest.json', manifest)
    load, verify, accepted = core._load_run, sessions._receipt_bindings_unchanged, core._accepted_review
    ids = {row['id'] for row in records}
    def load_run(session, name):
        found = next((row for row in records if row['id'] == name), None)
        return (session/'runs'/(name+'.json'), found) if found else load(session, name)
    monkeypatch.setattr(core, '_load_run', load_run)
    monkeypatch.setattr(sessions, '_receipt_bindings_unchanged', lambda session, payload, row:
                        True if row['id'] in ids else verify(session, payload, row))
    for row in records:
        support._review(session, row['id'], session/row['primary_output'], parent_preview=direct)
    def accepted_parent(session, name):
        if name not in ids: return accepted(session, name)
        path = session/'reviews'/(name+'.json'); review = contract.load_json(path)
        assert review['verdict'] == 'accept' and review['run_receipt_sha256'] == contract.sha256_file(session/'runs'/(name+'.json'))
        return review, {'path': str(path.relative_to(session)), 'sha256': contract.sha256_file(path)}
    monkeypatch.setattr(core, '_accepted_review', accepted_parent)
    native = LocalNative(); monkeypatch.setattr(core.subprocess, 'run', native)
    regions = session/'reports/local-regions.json'
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 384, 'height': 384}, 'regions': [
            {'id': 'sky-a', 'role': 'background', 'rect': [72, 72, 88, 88]},
            {'id': 'sky-b', 'role': 'background', 'rect': [296, 72, 312, 88]},
            {'id': 'sky-c', 'role': 'background', 'rect': [72, 296, 88, 312]},
            {'id': 'shell', 'role': 'faint', 'rect': [176, 176, 208, 208]}]})
    path = session/'reports/request.json'
    contract.atomic_write_json(path, {'schema': local.PROTECTED_REQUEST, 'source': str(parent.relative_to(session)),
        'regions': str(regions.relative_to(session)),
        'mask': dict(low_start=.03, low_end=.08, high_start=.65, high_end=.9, sigma=2),
        'wavelet': {'amplitude': .8, 'fine_detail': True}, 'clahe': {'strength': .13},
        'manual_lookup': {'status': 'not_needed', 'reason': 'Complete native recipe', 'evidence': []}})
    return session, parent, path, native


def solve(scene, run_id='080-compare', **kwargs):
    return local.solve(scene[0], scene[2], run_id, **kwargs)


def execute(scene, report, candidate='wavelet', **options):
    assets = local.generate(scene[0], report, candidate)
    args = dict(protocol='structure.local-contrast', script_value=assets['script'], source_value=assets['source'],
                primary_output_value=assets['primary_output'], expected_values=assets['expected_outputs'], timeout=30)
    return core.run_script(scene[0], **args, **options), args


def review(scene, run, *, verdict='accept', background='pass', structure='pass'):
    draft = core.check_review(scene[0], run['id'], None)['review']
    draft.update(verdict=verdict, notes='Synthetic subject, shell, background and highlight comparisons inspected',
                 gates={k: 'pass' for k in draft['gates']})
    draft['gates'].update(background=background, structure=structure)
    path = scene[0]/'reviews'/(run['id']+'.json'); contract.atomic_write_json(path, draft)
    return path, core.check_review(scene[0], run['id'], path)


@pytest.mark.parametrize('scene', ['single', 'hdr', 'hoo', 'hdr-tone', 'hdr-color'], indirect=True)
def test_actual_candidates_and_formal_equivalence(scene):
    result = solve(scene); report = local.load_parameters(scene[0], result['parameters'])
    methods = report['method_eligibility']; hdr = bool(methods['hdr_runs'])
    assert set(report['candidates']) == ({'wavelet'} if hdr else {'wavelet', 'clahe'})
    assert methods['clahe'] == {'allowed': not hdr, 'reason': 'hdr_lineage' if hdr else 'eligible'}
    assert sum(row[0] == 'clahe' for call in scene[3].calls for row in call) == (0 if hdr else 1)
    checked, args = execute(scene, result['parameters'], validate_only=True)
    assert checked['status'] == 'success' and checked['mode'] == 'validate_only'
    run = core.run_script(scene[0], **args); assert run['status'] == 'success', run.get('processing_checks')
    assert run['local_contrast_guard']['method_eligibility'] == methods
    assert max(run['processing_checks']['local_contrast']['native']['max_errors'].values()) < 2e-6
    draft = core.check_review(scene[0], run['id'], None)['review']
    assert draft['verdict'] is None and len(draft['inspected_materials']) <= 32
    assert review(scene, run)[1]['status'] == 'review_valid'
    before = support._session_bytes(scene[0])
    assert core.run_script(scene[0], **args)['replayed']
    assert support._session_bytes(scene[0]) == before


@pytest.mark.parametrize('issue', ['eligibility', 'missing-eligibility', 'missing', 'extra', 'source', 'regions', 'lineage', 'request', 'helper'])
@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
def test_bound_evidence_and_candidate_sets_cannot_drift(scene, issue):
    result = solve(scene); path = Path(result['parameters']); report = contract.load_json(path)
    if issue == 'eligibility': report['method_eligibility']['clahe']['allowed'] = True
    elif issue == 'missing-eligibility': report.pop('method_eligibility')
    elif issue == 'missing': report['candidates'].clear()
    elif issue == 'extra': report['candidates']['clahe'] = copy.deepcopy(report['candidates']['wavelet'])
    elif issue == 'helper': report['bindings']['helper'] = {'path': 'scripts/deep_sky_siril_local_contrast.py', **PRIOR['helper']}
    else:
        target = (scene[0]/report['bindings']['lineage'][-1]['path'] if issue == 'lineage'
                  else scene[0]/report['bindings'][issue]['path'])
        target.write_bytes(target.read_bytes()+b'\n')
    contract.atomic_write_json(path, report)
    with pytest.raises(contract.ContractError): local.load_parameters(scene[0], path)


@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
@pytest.mark.parametrize('method', ['explicit-clahe', 'legacy-clahe', 'spoofed-wavelet'])
def test_handwritten_ssf_cannot_bypass_hdr_method_gate(scene, method):
    session, source, path, native = scene
    primary = session/'artifacts/081-handwritten.fit'
    parameters = local.request_candidates(contract.load_json(path))['clahe']
    rows = local.recipe(session, source, primary, parameters)
    declaration = {**parameters, 'regions': 'reports/local-regions.json'}
    if method == 'legacy-clahe': declaration.pop('method')
    if method == 'spoofed-wavelet': declaration = {**local.request_candidates(contract.load_json(path))['wavelet'], 'regions': 'reports/local-regions.json'}
    script = session/'scripts/081-handwritten.ssf'
    script.write_text('\n'.join(['requires 1.4.4 1.5.0', 'set32bits', '# local-contrast: '+json.dumps(declaration), *rows])+'\n')
    if method == 'legacy-clahe':
        from .test_local_contrast_guards import script_assets
        (session/'reports/080-local-contrast-regions.json').write_bytes((session/'reports/local-regions.json').read_bytes())
        script, primary, expected = script_assets(session, source)
    else:
        from deep_sky_siril_validation import _normalized_write
        expected = set()
        for row in processing.script_commands(script.read_text()):
            if row[0] in {'save', 'savejpg'}: expected.add(_normalized_write(row[0], row[1]))
            elif row[0] == 'split': expected.update(_normalized_write('split', p) for p in row[1:])
            elif row[0] == 'rgbcomp': expected.add(primary)
    support._write_ssf_provenance(session, script, 'structure.local-contrast')
    count = len(native.calls)
    with pytest.raises(contract.ContractError, match='HDR and CLAHE|native luminance recipe'):
        core.run_script(session, protocol='structure.local-contrast', script_value=script, source_value=source,
                        primary_output_value=primary, expected_values=expected, validate_only=True, timeout=30)
    assert len(native.calls) == count and not primary.exists()


@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
def test_hdr_fine_off_is_one_observed_same_parent_revision(scene):
    result = solve(scene); run, _ = execute(scene, result['parameters'])
    assert review(scene, run, verdict='reject', background='fail')[1]['status'] == 'review_valid'
    revised = solve(scene, '082-fine-off', fallback_from=result['parameters'], reason='Synthetic background high-frequency rejection')
    report = local.load_parameters(scene[0], revised['parameters'])
    assert set(report['candidates']) == {'fine-off'}
    assert report['candidates']['fine-off']['parameters']['coefficients'][:2] == [1., 1.]
    assert execute(scene, revised['parameters'], 'fine-off')[0]['status'] == 'success'
    with pytest.raises(contract.ContractError, match='single fine-off'):
        solve(scene, '083-repeat', fallback_from=result['parameters'], reason='Repeated')
    assert all(row[0] != 'clahe' for call in scene[3].calls for row in call)


@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
@pytest.mark.parametrize('gate,value', [('structure', 'fail'), ('structure', 'uncertain'), ('background', 'fail'), ('background', 'uncertain')])
def test_technical_closure_cannot_accept_failed_or_uncertain_review(scene, gate, value):
    result = solve(scene); run, _ = execute(scene, result['parameters'])
    with pytest.raises(contract.ContractError): review(scene, run, **{gate: value})


def test_outgoing_policy_references_and_complete_implementation_are_registered(tmp_path):
    # prior=local-hdr-prior.json: guidance, chroma_denoise, color_preservation, local_contrast_hdr,
    # recomposition_geometry, linear_denoise_backend, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_LOCAL_HDR_EXECUTION_POLICY == PRIOR['execution_policy']
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation'])
    mixed['deep_sky_siril_local_contrast.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_local_contrast.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    assert local.PRE_HDR_HELPER == PRIOR['helper']
    for name, fp in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp})


@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
@pytest.mark.parametrize('schema', [local.REQUEST_SCHEMA, local.PROPOSAL_REQUEST, local.PROTECTED_REQUEST])
def test_supported_request_shapes_preserve_their_wavelet_candidates(scene, schema):
    request = contract.load_json(scene[2]); request['schema'] = schema
    if schema == local.REQUEST_SCHEMA:
        request.pop('wavelet'); request.pop('clahe')
    contract.atomic_write_json(scene[2], request)
    result = solve(scene)
    assert set(result['candidates']) == (set(local.WEIGHTS) if schema == local.REQUEST_SCHEMA else {'wavelet'})
    with pytest.raises(contract.ContractError, match='actual finite trial candidate'):
        local.generate(scene[0], result['parameters'], 'clahe')


@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
def test_excluded_clahe_parameters_are_still_validated_before_trials(scene):
    request = contract.load_json(scene[2]); request['clahe']['strength'] = .6
    contract.atomic_write_json(scene[2], request)
    with pytest.raises(contract.ContractError): solve(scene)
    assert not scene[3].calls and not (scene[0]/'reports/080-compare').exists()


@pytest.mark.parametrize('scene', ['hdr', 'hdr-tone', 'hdr-color'], indirect=True)
def test_old_hdr_policy_still_excludes_both_methods(scene):
    payload = contract.load_json(scene[0]/'session.json'); payload['execution_policy'] = PRIOR['execution_policy']
    payload.pop('session_sha256'); payload['session_sha256'] = core.stable_hash(payload)
    contract.atomic_write_json(scene[0]/'session.json', payload)
    with pytest.raises(contract.ContractError, match='current frozen execution policy'): solve(scene)
    state = processing.source_state(scene[0], payload, scene[1])
    with pytest.raises(contract.ContractError, match='HDR and CLAHE'):
        local.lineage_binding(scene[0], scene[1], state['lineage'], policy=local.MULTISCALE_POLICY)
    assert not scene[3].calls


def recompose(scene, run):
    session = scene[0]; source = session/run['primary_output']
    prepared = reduction.prepare(session, source, '089-stars')
    regions = session/'reports/star-regions.json'
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 384, 'height': 384}, 'regions': [
            {'id': 'shell', 'role': 'faint', 'rect': [176, 176, 208, 208]},
            {'id': 'stellar-core', 'role': 'stars', 'rect': [260, 260, 280, 280]}]})
    request = session/'reports/stars-request.json'
    contract.atomic_write_json(request, {'schema': reduction.REQUEST, 'source': prepared['source'],
        'prepared': prepared['prepared'], 'regions': reduction.record(session, regions), 'strength': .654321,
        'goal': 'Retain stars and the enhanced shell', 'parameter_reason': 'Inspected synthetic prepared layers',
        'manual_lookup': {'status': 'not_needed', 'reason': 'Frozen recipe', 'evidence': []}})
    assets = reduction.generate_recompose_assets(session, request=request, run_id='090-recompose')
    args = dict(protocol='stars.recompose', script_value=assets['script_path'], source_value=source,
        primary_output_value=assets['primary_output'], expected_values=assets['expected_outputs'],
        separation_run=assets['separation_run'], stretch_run=assets['stretch_run'], timeout=30)
    combined = core.run_script(session, **args)
    assert combined['status'] == 'success', combined.get('processing_checks')
    return combined, args


@pytest.mark.parametrize('scene', ['hdr'], indirect=True)
def test_hdr_wavelet_recomposes_with_the_same_frozen_mask_and_blocks_accumulation(scene):
    result = solve(scene); run, _ = execute(scene, result['parameters']); review(scene, run)
    combined, _ = recompose(scene, run)
    original = contract.load_json(scene[0]/'runs/070-stretch.json')
    assert combined['hdr_transfer'] == original['hdr_transfer']
    draft = accepted_draft(scene[0], combined)
    path = scene[0]/'reviews/090-recompose.json'; contract.atomic_write_json(path, draft)
    assert core.check_review(scene[0], combined['id'], path)['status'] == 'review_valid'
    request = contract.load_json(scene[2])
    request['source'] = run['primary_output']
    next_request = scene[0]/'reports/084-request.json'; contract.atomic_write_json(next_request, request)
    count = len(scene[3].calls)
    with pytest.raises(contract.ContractError, match='Only one local enhancement'):
        local.solve(scene[0], next_request, '084-accumulated')
    assert len(scene[3].calls) == count


def test_historical_local_evidence_replays_and_finalizes_without_migration(scene, monkeypatch):
    session = scene[0]; snapshot = {**PRIOR, 'documents': PRIOR['references']}
    inspected = next(row['id'] for row in contract.load_json(session/'manifest.json')['runs']
                     if 'knowledge_validation' in contract.load_json(session/'runs'/row['receipt']))
    _freeze_historical_evidence(session, snapshot=snapshot, run_id=inspected)
    # Reconstruct original receipts under the old policy; production remains read-only.
    with monkeypatch.context() as old:
        old.setattr(sessions, 'execution_policy_is_current', lambda _: True)
        capture = local.fingerprint
        old.setattr(local, 'fingerprint', lambda path, **kwargs:
            PRIOR['helper'].copy() if Path(path).resolve() == Path(local.__file__).resolve() else capture(path, **kwargs))
        result = solve(scene); path = Path(result['parameters']); report = contract.load_json(path)
        assert 'method_eligibility' not in report
        report['bindings']['helper'] = {'path': 'scripts/deep_sky_siril_local_contrast.py', **PRIOR['helper']}
        contract.atomic_write_json(path, report)
        run, args = execute(scene, path)
        _freeze_historical_evidence(session, snapshot=snapshot, run_id=run['id'])
        review(scene, run)
        combined, star_args = recompose(scene, run)
        _freeze_historical_evidence(session, snapshot=snapshot, run_id=combined['id'])
        star_review = session/'reviews/090-recompose.json'
        contract.atomic_write_json(star_review, accepted_draft(session, combined))
        old.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
            {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(path, include_path=False)}})
        script, final = support._delivery_script(session, session/combined['primary_output'])
        delivered = core.run_script(session, protocol='delivery.render', script_value=script,
            source_value=session/combined['primary_output'], expected_values=[final, final.with_suffix('.fit')], timeout=30)
        assert delivered['status'] == 'success'
        _freeze_historical_evidence(session, snapshot=snapshot, run_id=delivered['id'])
        final_review = support._review(session, delivered['id'], final,
            parent_preview=session/next(row['path'] for row in combined['outputs'] if row['path'].endswith('.jpg')))
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert core.run_script(session, **star_args)['replayed']
    assert support._session_bytes(session) == before
    selection = support._selection(session, delivered['id'], final, final_review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    for folder in ('runs', 'reviews', 'reports/080-compare'):
        for path in (session/folder).rglob('*'):
            if path.is_file(): assert path.read_bytes() == before[str(path.relative_to(session))]
    with pytest.raises(contract.ContractError, match='current frozen execution policy'): solve(scene, '084-new-trial')
    with pytest.raises(contract.ContractError, match='current frozen execution policy'):
        local.generate(session, result['parameters'], 'wavelet')

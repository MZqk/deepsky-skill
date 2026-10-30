"""Unmasked-first color comparisons with simulated native I/O, never Siril."""
import copy
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pytest
from PIL import Image

import deep_sky_siril_color as color
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
from . import test_deep_sky_siril as support
from . import test_recomposition_proposals as recomposition
from .test_recomposition_proposals import case, write
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/color-evaluation-prior.json').read_text())


class ColorNative:
    """Simulate recipe agreement; the synthetic saturation operator is not Siril GHS."""
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        script = Path(next(s.split('=', 1)[1] for s in argv if s.startswith('--script=')))
        root = Path(next(s.split('=', 1)[1] for s in argv if s.startswith('--directory=')))
        self.calls.append(script)
        current = None
        for row in processing.script_commands(script.read_text()):
            command = row[0]
            if command == 'load':
                current = (np.asarray(Image.open(row[1]).convert('RGB')).transpose(2, 0, 1)[:, ::-1]/255
                           if Path(row[1]).suffix == '.jpg' else color.pixels(Path(row[1])).copy())
            elif command in ('ght', 'invght'):
                strength = float(row[1][3:])
                peak = current.max(axis=0, keepdims=True)
                current = peak-(peak-current)*(1+strength*.1)
            elif command == 'split':
                for path, data in zip(row[1:], current):
                    write(Path(path).with_suffix('.fit'), data[None])
            elif command == 'pm':
                images = []
                def variable(match):
                    path = Path(match[1]+'.fit')
                    images.append(color.pixels(path if path.is_absolute() else root/path))
                    return f'images[{len(images)-1}]'
                expression = re.sub(r'\$([^$]+)\$', variable, row[1]).replace('^', '**')
                current = eval(expression, {'__builtins__': {}},
                               {'images': images, 'min': np.minimum, 'max': np.maximum, 'iif': np.where})
            elif command == 'fill':
                _,value,x,y,w,h=row
                x,y,w,h=map(int,(x,y,w,h));height=current.shape[1]
                current[:,height-y-h:height-y,x:x+w]=float(value)
            elif command == 'gauss':
                # Mask formula and formal/trial agreement are under test, not a blur implementation.
                current = current.copy()
            elif command == 'rgbcomp':
                current = np.concatenate([color.pixels(Path(p)) for p in row[1:4]])
                write(Path(row[4][5:]).with_suffix('.fit'), current)
            elif command == 'save':
                write(Path(row[1]).with_suffix('.fit'), current)
            elif command == 'savejpg':
                data = np.repeat(current, 3, axis=0) if len(current) == 1 else current
                Image.fromarray(np.uint8(np.clip(data[:, ::-1].transpose(1, 2, 0), 0, 1)*255)).save(Path(row[1]).with_suffix('.jpg'))
        return subprocess.CompletedProcess(argv, 0,
            'Reading FITS: file synthetic.fit, 3 layer(s), 64x64 pixels, 32 bits\n'+
            ''.join(c+' layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n'
                    for c in ('Red', 'Green', 'Blue')))


@pytest.fixture
def scene(case, monkeypatch):
    session, parent, *_ = case
    rows = [{'id': 'subject', 'role': 'faint', 'rect': [28, 28, 44, 44]},
            {'id': 'sky', 'role': 'background', 'rect': [48, 48, 60, 60]},
            {'id': 'stars', 'role': 'stars', 'rect': [12, 40, 24, 52]}]
    regions = session/'reports/color-regions.json'
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 64, 'height': 64}, 'regions': rows})
    native = ColorNative()
    monkeypatch.setattr(color.subprocess, 'run', native)
    return session, parent, regions, native


def solve(scene, run_id='085-color', **options):
    session, parent, regions, _ = scene
    params = {'target_gain': .08, 'strength': .432123456789,
              'saturation_protect_start': .7, 'saturation_protect_end': .95, 'goal': 'Reveal the remaining subject color contrast',
              'parameter_reason': 'One explicit proposal based on the current bound synthetic parent'}
    return color.solve(session, parent, regions, run_id, **(params | options))


def assets(scene, result, run_id='086-color'):
    session, source, *_ = scene
    text, primary, outputs, _ = color.script_text(session, source, run_id, result['parameters'], report=result['report'])
    script = session/'scripts'/(run_id+'.ssf'); script.write_text(text)
    support._write_ssf_provenance(session, script, 'color.finish')
    return {'protocol': 'color.finish', 'script_value': script, 'source_value': source,
            'primary_output_value': primary, 'expected_values': outputs, 'timeout': 30}


def execute(scene, result, run_id='086-color'):
    args = assets(scene, result, run_id)
    receipt = core.run_script(scene[0], **args)
    assert receipt['status'] == 'success', receipt.get('processing_checks')
    return receipt, args


def review(session, receipt, verdict='accept'):
    draft = core.check_review(session, receipt['id'], None)['review']
    draft.update(verdict=verdict, notes='Actual synthetic comparison inspected',
                 gates={k: 'pass' for k in draft['gates']})
    block = draft['color_assessment']
    block.update(goal_status='met', additional_benefit='observed',
                 benefit_observation='Subject gains color without unacceptable changes in protected regions',
                 adoption_reason='Observed synthetic benefit with acceptable subject, background and stars')
    for row in block['regions']:
        row.update(assessment='acceptable', observation='Bound region retains acceptable color and transitions')
    return draft


def save_review(session, receipt, draft):
    path = session/'reviews'/(receipt['id']+'.json'); contract.atomic_write_json(path, draft)
    return path


@pytest.mark.parametrize('strength', [.000123456789, .432123456789, 1.2, 10])
def test_one_explicit_unmasked_trial_and_no_mask_precondition(scene, monkeypatch, strength):
    monkeypatch.setattr(color, 'derive_mask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No mask before unmasked comparison')))
    result = solve(scene, strength=strength)
    report = contract.load_json(Path(result['report']))
    assert result['status'] == 'solved' and len(report['trials']) == 1
    assert report['selected_parameters'] == {'D': strength, 'mask_mode': 'none', 'mask_policy': color.PRESERVATION,
        'saturation_protect_start': .7, 'saturation_protect_end': .95}
    trial = report['trials'][0]
    assert len(scene[-1].calls) == 1
    assert f'-D={strength}' in (scene[0]/trial['script']['path']).read_text()
    assert (scene[0]/trial['candidate']['path']).is_file()
    assert set(trial['previews']) == {'parent', 'mask', 'candidate'}
    assert (scene[0]/trial['panels']['path']).is_file()
    assert 'channel_medians' in trial['regions'][0]['candidate']


@pytest.mark.parametrize('change', [dict(strength=None), dict(strength=True), dict(strength=float('nan')),
    dict(strength=float('inf')), dict(strength=0), dict(strength=10.01), dict(goal=''),
    dict(parameter_reason=None), dict(target_gain=None), dict(mask_mode='luminance'),
    dict(selector='cyan'), dict(observation='Unbound observation')])
def test_missing_or_invalid_proposals_never_execute(scene, change):
    before = len(scene[-1].calls)
    with pytest.raises(contract.ContractError): solve(scene, **change)
    assert len(scene[-1].calls) == before
    assert not (scene[0]/'reports/085-color').exists()


def test_zero_target_skips_without_strength_mask_or_identity(scene, monkeypatch):
    monkeypatch.setattr(color, 'derive_mask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No mask on skip')))
    result = solve(scene, target_gain=0, strength=None, goal=None, parameter_reason=None)
    report = contract.load_json(Path(result['report']))
    assert result['status'] == 'skipped' and report['trials'] == [] and result['parameters'] is None
    assert not scene[-1].calls and not list((Path(result['report']).parent).rglob('*.fit'))


def test_mask_is_observed_same_parent_revision_and_replays_identically(scene):
    unmasked = solve(scene)
    masked = solve(scene, run_id='087-mask-comparison', mask_mode='luminance', previous=unmasked['report'],
                   observation='The unmasked sky color is distracting; compare luminance protection at the same strength')
    assert masked['status'] == 'solved'
    receipt, args = execute(scene, masked, '088-masked')
    checks = receipt['processing_checks']['color_curve']
    assert checks['trial_max_abs_error'] < 1e-6 and checks['mask_mode'] == 'luminance'
    assert receipt['source_path'] == str(scene[1].relative_to(scene[0]))
    draft = review(scene[0], receipt)
    assert len(draft['inspected_materials']) <= 32
    path = save_review(scene[0], receipt, draft)
    assert core.check_review(scene[0], receipt['id'], path)['status'] == 'review_valid'
    frozen = support._session_bytes(scene[0])
    assert core.run_script(scene[0], **args)['replayed']
    assert support._session_bytes(scene[0]) == frozen


def test_unavailable_mask_preserves_the_actual_unmasked_comparison(scene, monkeypatch):
    unmasked = solve(scene)
    monkeypatch.setattr(color, 'derive_mask', lambda *a, **k: None)
    result = solve(scene, run_id='087-no-mask', mask_mode='luminance', previous=unmasked['report'],
                   observation='Test whether luminance protection can address the observed sky cost')
    report = contract.load_json(Path(result['report']))
    assert result['status'] == 'skipped' and report['mask_strategy'] == 'unavailable' and not report['trials']
    assert (scene[0]/contract.load_json(Path(unmasked['report']))['trials'][0]['candidate']['path']).exists()
    assert len(scene[-1].calls) == 1


def test_observed_revision_required_and_mask_first_strength_is_constant(scene):
    first = solve(scene)
    with pytest.raises(contract.ContractError, match='observation-bound'):
        solve(scene, run_id='087-unobserved')
    with pytest.raises(contract.ContractError, match='same strength'):
        solve(scene, run_id='088-changed-mask', strength=.7, mask_mode='luminance', previous=first['report'], observation='Compare a mask')
    revised = solve(scene, run_id='089-strength', strength=.7, previous=first['report'], observation='The initial subject color remains too weak')
    assert revised['parameters']['D'] == .7 and len(scene[-1].calls) == 2
    for script in scene[-1].calls:
        loads = [row[1] for row in processing.script_commands(script.read_text()) if row[0] == 'load']
        assert loads == [str(scene[1]), str(scene[1])]


@pytest.mark.parametrize('what', ['regions', 'source', 'preview', 'candidate', 'script', 'log', 'panels', 'previous'])
def test_drifting_material_rejects_before_formal_execution(scene, what):
    first = solve(scene)
    result = first
    if what == 'previous':
        result = solve(scene, run_id='087-revision', previous=first['report'], observation='Test a measured revision')
    args = assets(scene, result)
    report = contract.load_json(Path(result['report'])); trial = report['trials'][0]
    path = (scene[1] if what == 'source' else scene[2] if what == 'regions'
            else Path(first['report']) if what == 'previous'
            else scene[0]/(trial['previews']['candidate']['path'] if what == 'preview' else trial[what]['path']))
    path.write_bytes(path.read_bytes()+b'\n')
    count = len(scene[-1].calls)
    with pytest.raises(contract.ContractError): core.run_script(scene[0], validate_only=True, **args)
    assert len(scene[-1].calls) == count


@pytest.mark.parametrize('mutation', ['strength', 'extra_transform', 'missing_declaration', 'changed_mode'])
def test_handwritten_ssf_cannot_bypass_the_bound_recipe(scene, mutation):
    result = solve(scene); args = assets(scene, result)
    script = args['script_value']; text = script.read_text()
    if mutation == 'strength': text = text.replace('-D=0.432123456789', '-D=0.9')
    elif mutation == 'extra_transform': text = text.replace('ght ', 'mtf 0 0.4 1\nght ')
    elif mutation == 'missing_declaration': text = '\n'.join(s for s in text.splitlines() if not s.startswith('# color-curve:'))+'\n'
    else: text = text.replace('"mask_mode": "none"', '"mask_mode": "luminance"')
    script.write_text(text); support._write_ssf_provenance(scene[0], script, 'color.finish')
    with pytest.raises(contract.ContractError): core.run_script(scene[0], validate_only=True, **args)


@pytest.mark.parametrize('failure', ['benefit', 'goal', 'subject', 'sky', 'stars', 'uncertain', 'missing_assessment', 'missing_comparison'])
def test_technical_gain_does_not_accept_visual_damage_or_uncertainty(scene, failure):
    result = solve(scene); receipt, _ = execute(scene, result)
    assert receipt['processing_checks']['color_curve']['regions'][0]['saturation_median_change'] > 0
    draft = review(scene[0], receipt); block = draft['color_assessment']
    if failure == 'benefit': block['additional_benefit'] = 'not_observed'
    elif failure == 'goal': block['goal_status'] = 'uncertain'
    elif failure in ('subject', 'sky', 'stars'):
        next(r for r in block['regions'] if r['region']['id'] == failure)['assessment'] = 'degraded'
    elif failure == 'uncertain': block['regions'][0]['assessment'] = 'uncertain'
    elif failure == 'missing_assessment': draft.pop('color_assessment')
    else:
        panel = contract.load_json(Path(result['report']))['trials'][0]['panels']['path']
        draft['inspected_materials'] = [r for r in draft['inspected_materials'] if r['path'] != panel]
    path = save_review(scene[0], receipt, draft)
    with pytest.raises(contract.ContractError): core.check_review(scene[0], receipt['id'], path)


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
def test_unmasked_acceptance_replay_and_recomposition_keep_frozen_branch(scene, case, monkeypatch):
    result = solve(scene); receipt, args = execute(scene, result)
    path = save_review(scene[0], receipt, review(scene[0], receipt))
    assert core.check_review(scene[0], receipt['id'], path)['status'] == 'review_valid'
    frozen = support._session_bytes(scene[0]); assert core.run_script(scene[0], **args)['replayed']
    assert support._session_bytes(scene[0]) == frozen
    assert receipt['branch_binding']['separation_run'] == '060-stars'
    assert receipt['color_finish'][0]['parameters'][0] == '-D=0.432123456789'
    assert receipt['processing_checks']['color_curve']['recommended_verdict'] == 'uncertain'
    monkeypatch.setattr(core.subprocess, 'run', case[-1])
    source = args['primary_output_value']
    prepared = recomposition.reduction.prepare(scene[0], source, '089-color-stars')
    request_path, request = case[2], case[3]
    regions = scene[0]/request['regions']['path']; document = contract.load_json(regions)
    document['reference']['sha256'] = contract.sha256_file(source); contract.atomic_write_json(regions, document)
    request.update(source=prepared['source'], prepared=prepared['prepared'], regions=color.record(scene[0], regions))
    contract.atomic_write_json(request_path, request)
    next_case = (scene[0], source, *case[2:])
    combined = recomposition.execute(next_case, recomposition.generate(next_case))
    assert combined['status'] == 'success' and combined['source_path'] == receipt['primary_output']
    _, original = core._load_run(scene[0], '070-stretch')
    assert combined['transfer_chain'] == original['transfer_chain']
    if original.get('hdr_transfer'): assert combined['hdr_transfer'] == original['hdr_transfer']


def test_outgoing_policy_documents_and_implementation_are_pinned(tmp_path):
    # prior=color-evaluation-prior.json: guidance, chroma_denoise, color_preservation, color_evaluation,
    # local_contrast_hdr, recomposition_geometry, linear_denoise_backend, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_COLOR_EXECUTION_POLICY == PRIOR['execution_policy']
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation']); mixed['deep_sky_siril_color.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_color.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    for name, fp in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp})
    assert color.helper_fingerprint(tmp_path) == PRIOR['color_helper']


def test_unchanged_pixels_cannot_claim_observed_benefit(scene, monkeypatch):
    result = solve(scene, strength=1e-15)
    receipt, _ = execute(scene, result)
    assert receipt['processing_checks']['color_curve']['max_abs_change'] == 0
    path = save_review(scene[0], receipt, review(scene[0], receipt))
    with pytest.raises(contract.ContractError, match='Unchanged pixels'):
        core.check_review(scene[0], receipt['id'], path)


def test_failed_endpoint_trial_is_retained_for_observed_mask_revision(scene, monkeypatch):
    original = scene[-1]
    def endpoints(argv, **kwargs):
        completed = original(argv, **kwargs)
        script = Path(next(s.split('=', 1)[1] for s in argv if s.startswith('--script=')))
        saves = [Path(row[1]).with_suffix('.fit') for row in processing.script_commands(script.read_text()) if row[0] == 'save']
        enhanced, candidate = saves[-2:]
        values = color.pixels(enhanced); values[0, 0, 0] = -.01; write(enhanced, values)
        before = color.pixels(scene[1]); mask = color.pixels(saves[4])
        write(candidate, before+mask*(values-before))
        return completed
    monkeypatch.setattr(color.subprocess, 'run', endpoints)
    first = solve(scene)
    report = contract.load_json(Path(first['report']))
    assert first['status'] == 'skipped' and first['parameters'] is None
    assert report['trials'][0]['checks']['endpoint_counts']['new_shadow'][0] == 1
    assert (scene[0]/report['trials'][0]['candidate']['path']).exists()
    monkeypatch.setattr(color.subprocess, 'run', original)
    masked = solve(scene, run_id='087-protect', mask_mode='luminance', previous=first['report'],
                   observation='The unmasked comparison introduces a shadow endpoint; inspect a protected revision')
    assert masked['status'] == 'solved'


def test_cli_forwards_explicit_proposal_and_observation(monkeypatch, capsys):
    import sys
    calls = []
    monkeypatch.setattr(color, 'solve', lambda *a, **k: calls.append((a, k)) or {'status': 'solved'})
    monkeypatch.setattr(sys, 'argv', ['color', '--session', '/abs/session', '--source', '/abs/source.fit',
        '--regions', '/abs/regions.json', '--run-id', '088-color', '--target-gain', '.08', '--strength', '.432123456789',
        '--goal', 'Remaining color goal', '--parameter-reason', 'Observed reason', '--mask-mode', 'luminance',
        '--previous', '/abs/session/reports/prior.json', '--observation', 'Observed sky color cost'])
    assert color.main() == 0
    assert calls[0][1] == {'target_gain': .08, 'strength': .432123456789, 'goal': 'Remaining color goal',
        'parameter_reason': 'Observed reason', 'mask_mode': 'luminance',
        'previous': '/abs/session/reports/prior.json', 'observation': 'Observed sky color cost'}


def test_prior_masked_color_evidence_replays_and_finalizes_without_new_assessment(scene, monkeypatch):
    session, parent, *_ = scene
    from ._legacy_color import legacy_color_producer
    with legacy_color_producer(monkeypatch):
        first = solve(scene, strength=.3)
        masked = solve(scene, run_id='087-mask', strength=.3, mask_mode='luminance', previous=first['report'], observation='Protect the observed sky color')
        receipt, args = execute(scene, masked, '088-legacy-color')
        color_review = save_review(session, receipt, review(session, receipt))
        monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
            {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(path, include_path=False)}})
        delivery_script, final = support._delivery_script(session, args['primary_output_value'])
        delivered = core.run_script(session, protocol='delivery.render', script_value=delivery_script,
            source_value=args['primary_output_value'], expected_values=[final, final.with_suffix('.fit')], timeout=30)
        assert delivered['status'] == 'success'
        final_review = support._review(session, delivered['id'], final, parent_preview=args['expected_values'][-1])
    # Model original masked bytes, then use the normal read-only replay/finalize path.
    payload = contract.load_json(session/'session.json'); payload['execution_policy'] = PRIOR['execution_policy']
    payload.pop('session_sha256'); payload['session_sha256'] = core.stable_hash(payload)
    contract.atomic_write_json(session/'session.json', payload)
    report_path = Path(masked['report']); report = contract.load_json(report_path)
    for key in ('evaluation', 'proposal', 'previous', 'observation'): report.pop(key, None)
    report['selected_parameters'].pop('mask_mode')
    contract.atomic_write_json(report_path, report)
    p = {**report['selected_parameters'], 'parameters': str(report_path)}
    script = args['script_value']; text = script.read_text()
    text = '\n'.join('# color-curve: '+json.dumps(p) if row.startswith('# color-curve:') else row for row in text.splitlines())+'\n'
    script.write_text(text)
    binding = processing.validate_local_saturation(session, parent, args['primary_output_value'], processing.script_commands(text), p)
    receipt['script'].update(contract.fingerprint(script, include_path=False))
    receipt['local_saturation'] = binding; receipt.pop('color_evaluation')
    receipt['processing_checks']['color_curve'] = color.measure_curve(session, parent, args['primary_output_value'], binding)
    path = session/'runs'/(receipt['id']+'.json'); contract.atomic_write_json(path, receipt)
    legacy_review = contract.load_json(color_review); legacy_review.pop('color_assessment')
    for item in legacy_review['inspected_materials']:
        if item['path'] != '@input':
            item['sha256'] = contract.sha256_file(session/item['path'])
    contract.atomic_write_json(color_review, legacy_review)
    manifest = contract.load_json(session/'manifest.json')
    next(row for row in manifest['runs'] if row['id'] == receipt['id'])['receipt_sha256'] = contract.sha256_file(path)
    synthetic = [row for row in manifest['runs'] if 'knowledge_validation' not in contract.load_json(session/'runs'/row['receipt'])]
    manifest['runs'] = [row for row in manifest['runs'] if row not in synthetic]
    contract.atomic_write_json(session/'manifest.json', manifest)
    snapshot = {**PRIOR, 'documents': PRIOR['references']}
    _freeze_historical_evidence(session, snapshot=snapshot)
    manifest = contract.load_json(session/'manifest.json'); manifest['runs'].extend(synthetic)
    contract.atomic_write_json(session/'manifest.json', manifest)
    draft = core.check_review(session, receipt['id'], None)['review']
    assert 'color_assessment' not in draft
    draft.update(verdict='accept', notes='Original historical masked review', gates={k: 'pass' for k in draft['gates']})
    review_path = save_review(session, receipt, draft)
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert core.check_review(session, receipt['id'], review_path)['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    selection = support._selection(session, delivered['id'], final, final_review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    for folder in ('runs', 'reviews'):
        for path in (session/folder).glob('*.json'):
            assert path.read_bytes() == before[str(path.relative_to(session))]
    with pytest.raises(contract.ContractError, match='current frozen execution policy'):
        solve(scene, run_id='089-forbidden')


@pytest.mark.parametrize('changed', ['EXPTIME', 'CRVAL1', 'CD1_1'])
def test_unmasked_metadata_preservation_remains_a_hard_gate(tmp_path, changed):
    from .test_native_processing import write_fits
    headers = [('EXPTIME', 60), ('STACKCNT', 30), ('CRPIX1', 32), ('CRPIX2', 32),
               ('CRVAL1', 120), ('CRVAL2', 30), ('CTYPE1', 'RA---TAN'), ('CTYPE2', 'DEC--TAN'),
               ('CUNIT1', 'deg'), ('CUNIT2', 'deg'), ('CD1_1', -.001), ('CD1_2', 0), ('CD2_1', 0), ('CD2_2', .001)]
    source, candidate = tmp_path/'source.fit', tmp_path/'candidate.fit'
    values = np.full((3, 64, 64), .2)
    write_fits(source, values, headers); write_fits(candidate, values, headers)
    assert color.measure_unmasked_curve(tmp_path, source, candidate, {})['geometry_unchanged']
    changed_headers = [(key, value+1 if key == changed else value) for key, value in headers]
    write_fits(candidate, values, changed_headers)
    with pytest.raises(contract.ContractError): color.measure_unmasked_curve(tmp_path, source, candidate, {})


def test_masked_strength_is_bound_beyond_twelve_significant_digits(scene):
    first = solve(scene)
    masked = solve(scene, run_id='087-mask', mask_mode='luminance', previous=first['report'], observation='Compare sky protection at the original D')
    args = assets(scene, masked)
    script = args['script_value']
    script.write_text(script.read_text().replace('-D=0.432123456789', '-D=0.43212345678901'))
    support._write_ssf_provenance(scene[0], script, 'color.finish')
    with pytest.raises(contract.ContractError, match='Actual color strength'):
        core.run_script(scene[0], validate_only=True, **args)


def test_formal_pixels_must_match_the_retained_native_candidate(scene):
    result = solve(scene); receipt, args = execute(scene, result)
    data = color.pixels(args['primary_output_value']); data[0, 0, 0] += .001
    write(args['primary_output_value'], data)
    with pytest.raises(contract.ContractError, match='Formal color pixels'):
        color.measure_curve(scene[0], scene[1], args['primary_output_value'], receipt['color_curve'])


def rebind_fixture_parent(scene, values):
    session, parent, regions, _ = scene
    write(parent, values)
    path, accepted = core._load_run(session, '080-subject')
    accepted['outputs'] = [color.record(session, parent)]; contract.atomic_write_json(path, accepted)
    manifest = contract.load_json(session/'manifest.json')
    next(r for r in manifest['runs'] if r['id'] == accepted['id'])['receipt_sha256'] = contract.sha256_file(path)
    contract.atomic_write_json(session/'manifest.json', manifest)
    review_path = session/'reviews/080-subject.json'; prior_review = contract.load_json(review_path)
    prior_review['run_receipt_sha256'] = contract.sha256_file(path)
    for item in prior_review['inspected_materials']:
        if item['path'] == parent.relative_to(session).as_posix(): item['sha256'] = contract.sha256_file(parent)
    contract.atomic_write_json(review_path, prior_review)
    document = contract.load_json(regions); document['reference']['sha256'] = contract.sha256_file(parent)
    contract.atomic_write_json(regions, document)


def test_cyan_masked_comparison_binds_the_new_helper_evidence(scene):
    rebind_fixture_parent(scene, color.pixels(scene[1])[::-1])
    first = solve(scene)
    masked = solve(scene, run_id='087-cyan', mask_mode='luminance', selector='cyan', previous=first['report'],
                   observation='Use the actual unmasked comparison to protect non-cyan colors')
    assert masked['status'] == 'solved'
    receipt, _ = execute(scene, masked)
    assert receipt['local_saturation']['parameters']['selector'] == 'cyan'
    assert receipt['processing_checks']['color_curve']['regions'][0]['selector_pixels'] > 0
    assert receipt['processing_checks']['color_curve']['trial_max_abs_error'] < 1e-6


def test_legacy_local_tone_keeps_its_original_helper_binding(tmp_path):
    for name in ('reports', 'artifacts', 'previews'): (tmp_path/name).mkdir()
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    values = np.full((3, 64, 64), .02)
    values[:, 16:32, 32:48] = np.array([.4, .1, .1])[:, None, None]
    source = write(tmp_path/'artifacts/parent.fit', values)
    regions = tmp_path/'reports/regions.json'
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 64, 'height': 64},
        'regions': [{'id': 'sky', 'role': 'background', 'rect': [0, 0, 16, 16]},
                    {'id': 'subject', 'role': 'faint', 'rect': [32, 32, 48, 48]}]})
    parameters = {'selector': 'red', 'midtones': .48, 'regions': str(regions)}
    primary = tmp_path/'artifacts/tone.fit'
    text, _, binding = color.local_tone_script(tmp_path, source, primary, parameters)
    assert binding['helper'] == {'path': 'scripts/deep_sky_siril_color.py', **PRIOR['color_helper']}
    assert color.validate_local_tone(tmp_path, source, primary, processing.script_commands(text), parameters) == binding


@pytest.mark.parametrize('rgb,warning', [([.5, .05, .05], 'high_subject_saturation'),
                                        ([.2, .2, .2], 'relative_gain_unmeasurable')])
def test_saturation_statistics_do_not_replace_an_actual_unmasked_trial(scene, rgb, warning):
    values = np.broadcast_to(np.array(rgb)[:, None, None], (3, 64, 64)).copy()
    rebind_fixture_parent(scene, values)
    result = solve(scene)
    report = contract.load_json(Path(result['report']))
    assert len(scene[-1].calls) == len(report['trials']) == 1
    assert warning in {row['code'] for row in report['warnings']}
    if warning == 'relative_gain_unmeasurable':
        assert report['achieved_gain'] is None and report['target_reached'] is None


def test_full_rgb_input_uses_the_same_unmasked_contract_without_starless_source(tmp_path, monkeypatch):
    values = np.broadcast_to(np.array([.3, .25, .2])[:, None, None], (3, 64, 64)).copy()
    with monkeypatch.context() as scoped:
        scoped.setattr(support, '_write_fits', lambda path, **kwargs: write(path, values))
        session, parent, _ = support._session(tmp_path, monkeypatch, input_state='nonlinear', color_ready=True, width=64, height=64)
    inspection, direct, diagnostic = support._input_inspect_run(session, parent, monkeypatch)
    support._review(session, inspection['id'], diagnostic, parent_preview=direct)
    regions = session/'reports/color-regions.json'
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 64, 'height': 64},
        'regions': [{'id': 'subject', 'role': 'faint', 'rect': [28, 28, 44, 44]},
                    {'id': 'sky', 'role': 'background', 'rect': [48, 48, 60, 60]},
                    {'id': 'stars', 'role': 'stars', 'rect': [12, 40, 24, 52]}]})
    native = ColorNative(); monkeypatch.setattr(color.subprocess, 'run', native)
    full = session, parent, regions, native
    result = solve(full)
    assert contract.load_json(Path(result['report']))['starless'] is False
    receipt, _ = execute(full, result)
    assert receipt['source_path'] == '@input' and not receipt['branch_binding'].get('separation_run')
    path = save_review(session, receipt, review(session, receipt))
    assert core.check_review(session, receipt['id'], path)['status'] == 'review_valid'

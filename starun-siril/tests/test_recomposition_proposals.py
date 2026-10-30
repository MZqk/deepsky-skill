"""Explicit recomposition proposals, native-recipe mocks and immutable review evidence."""
import copy
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

import deep_sky_star_reduction as reduction
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_core as core
import deep_sky_siril_stretch_v2 as stretch
from . import test_deep_sky_siril as support
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/recomposition-prior.json').read_text())


def write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, np.asarray(values, dtype='float32').squeeze(), overwrite=True)
    return path


def write_catalog(row, *, x=18., y=45.):
    from .test_psf_provenance import native_model
    import siril_psf_samples as bridge
    layer = int(next(a.split('=', 1)[1] for a in row if a.startswith('-layer=')))
    output = Path(next(a.split('=', 1)[1] for a in row if a.startswith('-out=')))
    bridge.export_models(output, bridge.serialize_stars([native_model(xpos=x, ypos=y, layer=layer,
        profile=0, fwhmx=2.63, fwhmy=2.63)]), {'setfindstar': ['reset'], 'layer': layer, 'maxstars': 500})


class NativeMock:
    """Evaluate these fixture recipes without invoking Siril or asserting image quality."""
    def __init__(self, session):
        self.session, self.calls = session, []

    def __call__(self, argv, **kwargs):
        script = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--script=')))
        self.calls.append(script)
        current = None
        for row in processing.script_commands(script.read_text()):
            if row[0] == 'load':
                if Path(row[1]).suffix == '.tif':
                    current = np.asarray(Image.open(row[1]))[::-1][None]
                elif Path(row[1]).suffix == '.jpg':
                    current = np.asarray(Image.open(row[1]).convert('RGB')).transpose(2, 0, 1)[:, ::-1]/255
                else:
                    current = reduction.read_scientific_pixels(Path(row[1]))[0].copy()
            elif row[0] == 'findstar':
                write_catalog(row)
            elif row[0] == 'mtf':
                low, mid, high = map(float, row[1:]); x = np.clip((current-low)/(high-low), 0, 1)
                current = (mid-1)*x/((2*mid-1)*x-mid)
            elif row[0] == 'pm':
                images = []
                def image(match):
                    path = Path(match[1]+'.fit')
                    values = reduction.read_scientific_pixels(path if path.is_absolute() else self.session/path)[0]
                    images.append(values)
                    return f'images[{len(images)-1}]'
                expression = re.sub(r'\$([^$]+)\$', image, row[1])
                current = eval(expression, {'__builtins__': {}}, {'images': images})
            elif row[0] == 'rgbcomp':
                current = np.concatenate([reduction.read_scientific_pixels(Path(p))[0] for p in row[1:4]])
                write(Path(row[4].split('=', 1)[1]).with_suffix('.fit'), current)
            elif row[0] == 'save': write(Path(row[1]).with_suffix('.fit'), current)
            elif row[0] == 'savejpg':
                path = Path(row[1]).with_suffix('.jpg')
                data = np.repeat(current, 3, axis=0) if len(current) == 1 else current
                Image.fromarray(np.uint8(np.clip(data[:, ::-1].transpose(1, 2, 0), 0, 1)*255)).save(path)
        channels, height, width = current.shape
        stats = ''.join(name+' layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n'
                        for name in (('B&W',) if channels == 1 else ('Red', 'Green', 'Blue')))
        return subprocess.CompletedProcess(argv, 0,
            f'Reading FITS: file fixture.fit, {channels} layer(s), {width}x{height} pixels, 32 bits\n'+stats)


@pytest.fixture
def case(tmp_path, monkeypatch, request):
    mode = getattr(request, 'param', 'single')
    y, x = np.indices((64, 64))
    base = .08+.03*np.exp(-((x-32)**2+(y-32)**2)/200)
    stars = .2*np.exp(-((x-18)**2+(y-18)**2)/5)
    values = np.stack([base+stars, (base+stars)*.95, (base+stars)*.85])
    if mode == 'mono': values = values[:1]
    with monkeypatch.context() as scoped:
        scoped.setattr(support, '_write_fits', lambda path, **kwargs: write(path, values))
        session, original, _ = support._session(tmp_path, monkeypatch, input_state='linear', width=64, height=64)
    inspected, direct, diagnostic = support._input_inspect_run(session, original, monkeypatch)
    support._review(session, inspected['id'], diagnostic, parent_preview=direct)
    full = write(session/'artifacts/separation-full.fit', values)
    linear = write(session/'artifacts/linear-starless.fit', values*.8)
    nonlin = write(session/'artifacts/stretch-starless.fit', values*.8)
    parent = write(session/'artifacts/subject-enhanced.fit', values*.8+.025)
    chain = [{'command': 'mtf', 'parameters': ['0', '.5', '1']}]
    records = [
        {'id': '060-stars', 'protocol': 'stars.separate', 'source_path': '@input', 'primary_output': str(linear.relative_to(session)),
         'image_domain': 'linear', 'star_separation': {'full_source': str(full.relative_to(session))}},
        {'id': '070-stretch', 'protocol': 'stretch', 'source_path': str(linear.relative_to(session)),
         'primary_output': str(nonlin.relative_to(session)), 'image_domain': 'nonlinear', 'transfer_chain': chain},
        {'id': '080-subject', 'protocol': 'structure.local-contrast', 'source_path': str(nonlin.relative_to(session)),
         'primary_output': str(parent.relative_to(session)), 'image_domain': 'nonlinear', 'transfer_chain': chain}]
    if mode == 'hdr':
        mask = write(session/'artifacts/frozen-mask.fit', np.full_like(values[:1], .4))
        rgb_mask = write(session/'artifacts/frozen-mask-rgb.fit', np.full_like(values, .4))
        records[1].update(transfer_chain=[], hdr_transfer={'gentle_chain': chain,
            'faint_chain': [{'command': 'mtf', 'parameters': ['0', '.4', '1']}],
            'mask': {'path': str(mask.relative_to(session)), 'blend_path': str(rgb_mask.relative_to(session))}})
    if mode == 'hoo':
        parameters = session/'reports/hoo-parameters.json'; parameters.write_text('{}')
        records[1]['dualband_transfer'] = {'parameters': reduction.record(session, parameters)}
    manifest = contract.load_json(session/'manifest.json')
    for row in records:
        row.update(schema='starun-siril.run-receipt.v1', status='success', branch_binding={'separation_run': '060-stars'},
                   outputs=[reduction.record(session, session/row['primary_output'])])
        if mode == 'hdr' and row['id'] == '070-stretch':
            row['outputs'].extend(reduction.record(session, p) for p in (mask, rgb_mask))
            row['source'] = contract.fingerprint(linear, include_path=False)
            from deep_sky_siril_stretch_trials import create
            row['trial_artifacts'] = create(session, row)
        path = session/'runs'/(row['id']+'.json'); contract.atomic_write_json(path, row)
        manifest['runs'].append({'id': row['id'], 'status': 'success', 'receipt': path.name,
                                'receipt_sha256': contract.sha256_file(path)})
    contract.atomic_write_json(session/'manifest.json', manifest)
    load_run, verify_bindings = core._load_run, sessions._receipt_bindings_unchanged
    def load(session, name):
        found = next((row for row in records if row['id'] == name), None)
        return (session/'runs'/(name+'.json'), found) if found else load_run(session, name)
    monkeypatch.setattr(core, '_load_run', load)
    monkeypatch.setattr(sessions, '_receipt_bindings_unchanged', lambda session, payload, receipt:
        True if receipt['id'] in {row['id'] for row in records} else verify_bindings(session, payload, receipt))
    for row in records: support._review(session, row['id'], session/row['primary_output'], parent_preview=direct)
    accepted = core._accepted_review
    def accepted_parent(session, name):
        if name not in {row['id'] for row in records}: return accepted(session, name)
        path = session/'reviews'/(name+'.json')
        review = contract.load_json(path)
        assert review['verdict'] == 'accept' and review['run_receipt_sha256'] == contract.sha256_file(session/'runs'/(name+'.json'))
        return review, {'path': str(path.relative_to(session)), 'sha256': contract.sha256_file(path)}
    monkeypatch.setattr(core, '_accepted_review', accepted_parent)
    native = NativeMock(session); monkeypatch.setattr(core.subprocess, 'run', native)
    prepared = reduction.prepare(session, parent, '089-prepared')
    region_path = session/'reports/regions.json'
    contract.atomic_write_json(region_path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 64, 'height': 64},
        'regions': [{'id': 'subject', 'role': 'faint', 'rect': [28, 28, 44, 44]},
                    {'id': 'stellar-core', 'role': 'stars', 'rect': [12, 40, 24, 52]},
                    {'id': 'stellar-halo', 'role': 'stars', 'rect': [8, 36, 28, 56]}]})
    proposal = {'schema': reduction.REQUEST, 'source': prepared['source'], 'prepared': prepared['prepared'],
        'regions': reduction.record(session, region_path), 'strength': .654321, 'goal': 'Retain stellar presence and reveal the enhanced subject',
        'parameter_reason': 'Actual prepared star/subject material informs this single proposal',
        'manual_lookup': {'status': 'not_needed', 'reason': 'Documented frozen replay', 'evidence': []}}
    path = session/'reports/proposal.json'; contract.atomic_write_json(path, proposal)
    return session, parent, path, proposal, native


def generate(case, run_id='090-recompose'):
    session, _, path, _, _ = case
    return reduction.generate_recompose_assets(session, request=path, run_id=run_id)


def execute(case, assets):
    session, parent, *_ = case
    return core.run_script(session, protocol='stars.recompose', script_value=assets['script_path'], source_value=parent,
        expected_values=assets['expected_outputs'], primary_output_value=assets['primary_output'],
        separation_run=assets['separation_run'], stretch_run=assets['stretch_run'], timeout=30)


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
def test_prepared_and_formal_replay_use_the_actual_parent_and_explicit_parameters(case, monkeypatch):
    session, parent, path, proposal, native = case
    monkeypatch.setattr(reduction, 'compute_adaptive_star_strength', lambda *args: pytest.fail('No automatic strength selection'))
    assets = generate(case)
    assert assets['strength'] == .654321
    assert '.654321' in Path(assets['script_path']).read_text()
    report = contract.load_json(session/proposal['prepared']['path'])
    assert (session/report['previews']['signed-stars']['path']).is_file()
    assert report['signed_preview_abs_scale'] > 0
    a = reduction.read_scientific_pixels(parent)[0]
    stars = reduction.read_scientific_pixels(session/report['layers']['display_stars']['path'])[0]
    control = reduction.read_scientific_pixels(session/report['control']['path'])[0]
    np.testing.assert_allclose(control, a+stars, atol=1e-7)
    assert not np.allclose(control, reduction.read_scientific_pixels(session/report['layers']['full_baseline']['path'])[0])
    run = execute(case, assets)
    assert run['status'] == 'success', run.get('processing_checks')
    assert max(run['processing_checks']['closure']['per_channel_final_max_abs']) < 1e-5
    draft = core.check_review(session, run['id'], None)['review']
    assert draft['recomposition_assessment']['goal_status'] is None
    assert len(draft['inspected_materials']) <= 32
    materials = [row['path'] for row in draft['inspected_materials']]
    assert materials == sorted(set(materials))
    diagnostics = contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    for row in diagnostics['signed_residual']:
        for channel in row['channels']:
            assert channel['contribution_mean'] == channel['mean']*proposal['strength']
    before = support._session_bytes(session)
    assert execute(case, assets)['replayed'] is True
    assert support._session_bytes(session) == before


@pytest.mark.parametrize('strength', [.3, 1., .3999999])
def test_contribution_boundaries_and_full_precision(case, strength):
    session, _, path, proposal, _ = case
    proposal['strength'] = strength; contract.atomic_write_json(path, proposal)
    assets = generate(case)
    assert assets['strength'] == strength
    commands = processing.script_commands(Path(assets['script_path']).read_text())
    expression = [row[1] for row in commands if row[0] == 'pm'][-1]
    assert float(expression.split(' + ')[1].split(' * ')[0]) == strength


@pytest.mark.parametrize('change', ['missing', 'bool', 'low', 'high', 'string', 'reason', 'nan', 'infinity', 'manual'])
def test_invalid_or_missing_strength_and_rationale_never_default(case, change):
    session, _, path, proposal, _ = case
    if change == 'missing': proposal.pop('strength')
    elif change == 'reason': proposal['parameter_reason'] = ''
    elif change == 'manual': proposal['manual_lookup']['status'] = 'performed'
    elif change in ('nan', 'infinity'): proposal['strength'] = float('nan' if change == 'nan' else 'inf')
    else: proposal['strength'] = {'bool': True, 'low': .2999, 'high': 1.0001, 'string': '.6'}[change]
    path.write_text(json.dumps(proposal))
    with pytest.raises(contract.ContractError): generate(case)
    assert not (session/'scripts/090-recompose.ssf').exists()


@pytest.mark.parametrize('field', ['source', 'prepared', 'regions'])
def test_proposal_rejects_size_fingerprint_drift(case, field):
    _, _, path, proposal, _ = case
    proposal[field]['size'] += 1
    contract.atomic_write_json(path, proposal)
    with pytest.raises(contract.ContractError): generate(case)


@pytest.mark.parametrize('change', ['source', 'regions', 'prepared', 'request', 'command', 'unbound'])
def test_handwritten_scripts_cannot_bypass_or_drift_from_the_proposal(case, change):
    session, parent, path, proposal, _ = case
    assets = generate(case)
    script = Path(assets['script_path'])
    if change == 'source': write(parent, np.zeros((3, 64, 64)))
    elif change == 'regions': (session/proposal['regions']['path']).write_text('{}')
    elif change == 'prepared': (session/proposal['prepared']['path']).write_text('{}')
    elif change == 'request': path.write_text(path.read_text()+'\n')
    elif change == 'command': script.write_text(script.read_text().replace(format(proposal['strength'], '.17g'), '.5'))
    else: script.write_text('\n'.join(row for row in script.read_text().splitlines() if not row.startswith('# recomposition-proposal:')))
    if change in ('command', 'unbound'):
        # A fresh sidecar cannot authorize a handwritten script that bypasses the proposal.
        provenance = Path(assets['provenance_path']); value = contract.load_json(provenance)
        value['script']['sha256'] = contract.sha256_file(script); contract.atomic_write_json(provenance, value)
    with pytest.raises(contract.ContractError):
        execute(case, assets)
    assert not Path(assets['primary_output']).exists()


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
def test_prepared_transfer_and_hdr_mask_drift_prevent_generation(case):
    session, _, _, proposal, _ = case
    prepared = contract.load_json(session/proposal['prepared']['path'])
    receipt = session/prepared['bindings']['stretch']['path']
    stretch_run = contract.load_json(receipt)
    if stretch_run.get('hdr_transfer'):
        mask = session/stretch_run['hdr_transfer']['mask']['blend_path']
        write(mask, np.ones((3, 64, 64)))
    else:
        receipt.write_text(receipt.read_text()+'\n')
    with pytest.raises(contract.ContractError): generate(case)


def test_handwritten_equivalent_strength_and_fresh_sidecar_remain_valid(case):
    assets = generate(case); script = Path(assets['script_path'])
    script.write_text(script.read_text().replace(format(assets['strength'], '.17g'), str(assets['strength'])))
    provenance = Path(assets['provenance_path']); value = contract.load_json(provenance)
    value['script']['sha256'] = contract.sha256_file(script); contract.atomic_write_json(provenance, value)
    assert execute(case, assets)['status'] == 'success'


def test_generation_rechecks_reference_fingerprints_before_publication(case, monkeypatch):
    bindings = reduction.protocol_reference_bindings
    def changed(protocol):
        records = bindings(protocol); records[0]['sha256'] = '0'*64
        return records
    monkeypatch.setattr(reduction, 'protocol_reference_bindings', changed)
    with pytest.raises(contract.ContractError, match='reference changed'): generate(case)
    assert not (case[0]/'scripts/090-recompose.ssf').exists()


def accepted_draft(session, run):
    draft = core.check_review(session, run['id'], None)['review']
    draft.update(verdict='accept', gates={key: 'pass' for key in draft['gates']},
                 notes='Synthetic contract fixture: all actual bound materials inspected')
    block = draft['recomposition_assessment']
    block.update(goal_status='met', adoption_reason='Synthetic fixture: the requested stellar presence and protected subject are acceptable')
    if 'contribution_assessment' in block:
        block['contribution_assessment'].update(assessment='acceptable', observation='Inspected the same-source contribution balance')
    for row in block['regions']:
        row.update(assessment='acceptable', observation='Known fixture structure and star colors are acceptable')
        for check in row.get('stellar_checks',{}).values():
            check.update(assessment='acceptable', observation='Inspected the corresponding fixed stellar fixture evidence')
    return draft


@pytest.mark.parametrize('region,result', [('subject', 'degraded'), ('stellar-core', 'degraded'),
    ('stellar-halo', 'degraded'), ('stellar-core', 'uncertain')])
def test_closed_arithmetic_does_not_accept_failed_subject_core_halo_or_color_review(case, region, result):
    session, *_ = case
    run = execute(case, generate(case))
    assert run['status'] == 'success'
    draft = accepted_draft(session, run)
    row = next(row for row in draft['recomposition_assessment']['regions'] if row['region']['id'] == region)
    row.update(assessment=result, observation='Actual inspected fixture shows a protected-region problem, including stellar color')
    path = session/'reviews/090-recompose.json'; contract.atomic_write_json(path, draft)
    with pytest.raises(contract.ContractError, match='protected regions'): core.check_review(session, run['id'], path)
    draft['verdict'] = 'uncertain' if result == 'uncertain' else 'reject'
    contract.atomic_write_json(path, draft)
    assert core.check_review(session, run['id'], path)['status'] == 'review_valid'


@pytest.mark.parametrize('strength', [.3, 1.])
def test_explicit_goal_can_accept_full_contribution_without_a_reduction_benefit(case, strength):
    session, _, path, proposal, _ = case
    proposal.update(strength=strength, goal='Keep the explicitly requested stellar presence')
    contract.atomic_write_json(path, proposal)
    run = execute(case, generate(case))
    assert run['status'] == 'success'
    diagnostics = contract.load_json(session/run['processing_checks']['recomposition']['report']['path'])
    assert diagnostics['recommended_verdict'] == 'uncertain'
    draft = accepted_draft(session, run)
    path = session/'reviews/090-recompose.json'; contract.atomic_write_json(path, draft)
    assert core.check_review(session, run['id'], path)['status'] == 'review_valid'
    draft['recomposition_assessment']['goal_status'] = 'not_met'; contract.atomic_write_json(path, draft)
    with pytest.raises(contract.ContractError): core.check_review(session, run['id'], path)


@pytest.mark.parametrize('material', ['panel', 'candidate', 'preview'])
def test_observed_revision_uses_a_new_id_same_parent_and_frozen_stars(case, material):
    session, _, path, proposal, _ = case
    run = execute(case, generate(case))
    assert run['status'] == 'success'
    evidence = (run['processing_checks']['recomposition']['panel'] if material == 'panel'
                else reduction.record(session, session/run['primary_output']) if material == 'candidate'
                else next(row for row in run['outputs'] if row['path'].endswith('.jpg')))
    revised = {**proposal, 'strength': .432123, 'revision': {'request': reduction.record(session, path), 'run_id': run['id'],
        'evidence': [evidence], 'reason': 'Observed stellar presence obscures the protected subject'}}
    path = session/'reports/revised.json'; contract.atomic_write_json(path, revised)
    assets = reduction.generate_recompose_assets(session, request=path, run_id='091-revised')
    assert assets['strength'] == .432123 and execute(case, assets)['status'] == 'success'
    revised['source'] = reduction.record(session, Path(assets['primary_output']))
    bad = session/'reports/cumulative.json'; contract.atomic_write_json(bad, revised)
    with pytest.raises(contract.ContractError, match='source'): reduction.generate_recompose_assets(session, request=bad, run_id='092-invalid')


def test_repeated_candidate_requires_observations_and_a_revision_binding(case):
    session, _, _, proposal, _ = case
    assert execute(case, generate(case))['status'] == 'success'
    path = session/'reports/unobserved.json'
    contract.atomic_write_json(path, {**proposal, 'strength': .4})
    with pytest.raises(contract.ContractError, match='observation-bound revision'):
        reduction.generate_recompose_assets(session, request=path, run_id='091-unobserved')
    assert not (session/'scripts/091-unobserved.ssf').exists()


def test_missing_diagnostics_cannot_be_repaired_by_replay(case):
    session, *_ = case
    assets = generate(case); run = execute(case, assets)
    assert run['status'] == 'success'
    path = session/run['processing_checks']['recomposition']['report']['path']; path.unlink()
    with pytest.raises(contract.ContractError): execute(case, assets)
    assert not path.exists()


def test_outgoing_policy_documents_and_complete_implementation_remain_immutable(tmp_path):
    # prior=recomposition-prior.json: 10 个域标签
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_RECOMPOSITION_EXECUTION_POLICY == PRIOR['execution_policy']
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation']); mixed['deep_sky_siril_core.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_core.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    for name, fp in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp})
        assert not sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp, 'size': fp['size']+1})


@pytest.mark.parametrize('case', ['single', 'hdr', 'hoo'], indirect=True)
@pytest.mark.parametrize('history', [False, True])
def test_recomposition_replay_and_finalize_preserve_new_and_historical_evidence(case, monkeypatch, history):
    session, parent, *_ = case
    assets = generate(case); run = execute(case, assets)
    assert run['status'] == 'success'
    review = session/'reviews/090-recompose.json'; contract.atomic_write_json(review, accepted_draft(session, run))
    assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    script, final = support._delivery_script(session, Path(assets['primary_output']))
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
        {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(path, include_path=False)}})
    delivery = core.run_script(session, protocol='delivery.render', script_value=script,
        source_value=assets['primary_output'], expected_values=[final, final.with_suffix('.fit')], timeout=30)
    assert delivery['status'] == 'success', delivery.get('processing_checks')
    draft = core.check_review(session, delivery['id'], None)['review']
    draft.update(verdict='accept', gates={key: 'pass' for key in draft['gates']}, notes='Known synthetic final fixture retained')
    final_review = session/'reviews/120-delivery.json'; contract.atomic_write_json(final_review, draft)
    if history:
        prior_path = session/'runs/090-recompose.json'; prior = contract.load_json(prior_path)
        prior.pop('recomposition_proposal'); prior['processing_checks'].pop('recomposition')
        contract.atomic_write_json(prior_path, prior)
        draft = contract.load_json(review); draft.pop('recomposition_assessment'); contract.atomic_write_json(review, draft)
        _freeze_historical_evidence(session, snapshot=PRIOR, run_id='090-recompose')
        delivery = contract.load_json(session/'runs/120-delivery.json')
        for binding in delivery['review_bindings']:
            binding['sha256'] = contract.sha256_file(session/binding['path'])
        contract.atomic_write_json(session/'runs/120-delivery.json', delivery)
        manifest = contract.load_json(session/'manifest.json')
        next(row for row in manifest['runs'] if row['id'] == delivery['id'])['receipt_sha256'] = contract.sha256_file(session/'runs/120-delivery.json')
        contract.atomic_write_json(session/'manifest.json', manifest)
        draft = contract.load_json(final_review); draft['run_receipt_sha256'] = contract.sha256_file(session/'runs/120-delivery.json')
        contract.atomic_write_json(final_review, draft)
    before = support._session_bytes(session)
    assert execute(case, assets)['replayed'] is True
    assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    selection = support._selection(session, delivery['id'], final, final_review)
    result = core.finalize_session(session, selection_value=selection, keep_intermediates=True)
    assert result['status'] == 'success'
    for p in (session/'runs').glob('*.json'):
        assert p.read_bytes() == before[str(p.relative_to(session))]

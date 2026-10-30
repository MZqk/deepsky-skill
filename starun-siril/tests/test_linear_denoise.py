"""Agent proposals, texture-first review, mocked execution and immutable history."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_denoise as denoise
import deep_sky_siril_dualband as dual
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as protected
from . import test_deep_sky_siril as support
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence
from .test_stretch_v22 import make_v2
from ._stretch_factory import bind_hoo
import deep_sky_siril_stretch as base_stretch
from .test_xisf_metrics import xisf

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/linear-denoise-prior.json').read_text())


def write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, np.asarray(values, dtype='float32').squeeze(), overwrite=True)
    return path


def regions(session, source):
    rows = [{'id': 'sky', 'role': 'background', 'rect': [4, 4, 24, 24]},
            {'id': 'filament', 'role': 'faint', 'rect': [36, 36, 60, 60]},
            {'id': 'rim', 'role': 'core', 'rect': [64, 50, 80, 66]}]
    path = session/'reports/denoise-regions.json'
    path.write_text(json.dumps({'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 96, 'height': 96}, 'regions': rows}))
    return denoise.record(session, path)


def fallback_request(session, request):
    assessment = session/'reports/deepsnr-applicability.json'
    contract.atomic_write_json(assessment, {'schema': 'starun-siril.deepsnr-applicability.v1',
        'source': request['source'], 'decision': 'inapplicable', 'reason': 'Synthetic source is below the native 512x512 minimum'})
    request.update(schema=denoise.SCHEMA_V2, backend='nlbayes',
        fallback={'reason': 'Native dimensions are insufficient', 'evidence': [denoise.record(session, assessment)]})
    return request


@pytest.fixture
def case(tmp_path, monkeypatch):
    session = tmp_path/'session'
    for name in ('artifacts', 'previews', 'reports', 'scripts', 'runs', 'reviews'):
        (session/name).mkdir(parents=True)
    y, x = np.indices((96, 96))
    signal = .04+.02*np.exp(-((x-46)/1.2)**2)
    data = signal+np.random.default_rng(22).normal(0, .001, signal.shape)
    source = write(tmp_path/'master.fit', data)
    payload = {'input': {'path': str(source)}, 'context': {'input_state': 'linear', 'channel_mode': 'mono'},
               'execution_policy': dict(contract.EXECUTION_POLICY)}
    (session/'session.json').write_text(json.dumps(payload))
    monkeypatch.setattr(sessions, 'load_session', lambda _: (session, payload, {}))
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda *args: payload)
    request = {'schema': denoise.SCHEMA, 'source': {'path': '@input', 'sha256': contract.sha256_file(source)},
               'regions': regions(session, source), 'display_mtf': [0, .15, 1],
               'decision': 'trial', 'reason': 'Background grain interferes with the intended faint-shell display',
               'parameters': {'modulation': .12, 'cosmetic': False},
               'parameter_reason': 'One Agent proposal protecting the visible thin filament',
               'manual_lookup': {'status': 'not_needed', 'reason': 'Documented common variant', 'evidence': []}}
    fallback_request(session, request)
    path = session/'reports/denoise-request.json'; path.write_text(json.dumps(request))
    return session, source, payload, path, request


@pytest.mark.parametrize('parameters,expected', [
    ({'modulation': .12, 'cosmetic': False}, ['denoise', '-nocosmetic', '-mod=0.12']),
    ({'modulation': .8, 'cosmetic': True}, ['denoise', '-mod=0.80000000000000004']),
    ({'modulation': 1, 'cosmetic': False}, ['denoise', '-nocosmetic', '-mod=1'])])
def test_explicit_agent_parameters_generate_the_actual_command(case, parameters, expected):
    session, source, payload, path, request = case
    request['parameters'] = parameters; path.write_text(json.dumps(request))
    result = denoise.generate(session, path, '050-denoise')
    value = Path(result['script']).read_text()
    assert [row for row in processing.script_commands(value) if row[0] == 'denoise'] == [expected]
    metadata = processing.prepare_processing(session, payload, 'restoration.denoise', source,
        Path(result['primary_output']), value, {}, None, None, None, None, run_id='050-denoise')
    assert metadata['linear_denoise']['parameters'] == parameters
    assert metadata['linear_denoise']['source'] == request['source']
    assert all(not Path(p).exists() for p in result['expected'])


@pytest.mark.parametrize('change', ['parameters', 'modulation', 'cosmetic', 'reason', 'range', 'boolean'])
def test_missing_or_invalid_parameters_never_default(case, change):
    session, _, _, path, request = case
    if change == 'parameters': request.pop('parameters')
    elif change in {'modulation', 'cosmetic'}: request['parameters'].pop(change)
    elif change == 'reason': request.pop('parameter_reason')
    elif change == 'range': request['parameters']['modulation'] = 1.01
    else: request['parameters']['modulation'] = True
    path.write_text(json.dumps(request))
    with pytest.raises(contract.ContractError): denoise.generate(session, path, '050-invalid')
    assert not (session/'scripts/050-invalid.ssf').exists()
    assert not list((session/'artifacts').iterdir())


@pytest.mark.parametrize('decision,zero', [('skip', False), ('trial', True)])
def test_skip_creates_no_scientific_candidate_or_identity_run(case, decision, zero):
    session, _, _, path, request = case
    request['decision'] = decision
    if zero: request['parameters']['modulation'] = 0
    else:
        request.pop('parameters'); request.pop('parameter_reason')
    path.write_text(json.dumps(request))
    result = denoise.generate(session, path, '050-skip')
    assert result['status'] == 'skipped' and result['expected'] == []
    assert not list((session/'scripts').iterdir()) and not list((session/'artifacts').iterdir())
    assert not list((session/'runs').iterdir())


@pytest.mark.parametrize('change', ['source', 'regions', 'proposal', 'command', 'cosmetic', 'display', 'unbound'])
def test_handwritten_ssf_and_all_evidence_drift_are_rejected(case, change):
    session, source, payload, path, request = case
    assets = denoise.generate(session, path, '050-denoise')
    value = Path(assets['script']).read_text()
    if change == 'source': write(source, np.zeros((96, 96)))
    elif change == 'regions': (session/request['regions']['path']).write_text('{}')
    elif change == 'proposal': path.write_text(path.read_text()+'\n')
    elif change == 'command': value = value.replace('-mod=0.12', '-mod=0.6')
    elif change == 'cosmetic': value = value.replace('-nocosmetic ', '')
    elif change == 'display': value = value.replace('mtf 0 0.14999999999999999 1', 'mtf 0 0.2 1')
    else: value = '\n'.join(row for row in value.splitlines() if not row.startswith('# linear-denoise:'))
    with pytest.raises((contract.ContractError, ValueError)):
        processing.prepare_processing(session, payload, 'restoration.denoise', source, Path(assets['primary_output']),
                                      value, {}, None, None, None, None, run_id='050-denoise')


def candidate(case, *, loss=False, bright=False):
    session, source, payload, path, _ = case
    assets = denoise.generate(session, path, '050-denoise')
    metadata = processing.prepare_processing(session, payload, 'restoration.denoise', source, Path(assets['primary_output']),
        Path(assets['script']).read_text(), {}, None, None, None, None, run_id='050-denoise')
    a, _ = denoise.read_scientific_pixels(source)
    y, x = np.indices((96, 96)); signal = .04+.02*np.exp(-((x-46)/1.2)**2)
    data = signal+(a[0]-signal)*.5
    if loss: data[:, 44:49] = .04
    if bright: data[10, 10] = 1.2
    write(Path(assets['primary_output']), data)
    for p in assets['expected'][1:]: Image.new('RGB', (96, 96)).save(p)
    checked = processing.validate_processing_outputs(session, source, Path(assets['primary_output']), metadata,
                                                       execution_policy=payload['execution_policy'], write_diagnostics=True)
    run = {'id': '050-denoise', 'protocol': 'restoration.denoise', **metadata, 'processing_checks': checked}
    return run, assets


@pytest.mark.parametrize('retention', ['loss', 'uncertain'])
def test_mad_improvement_cannot_accept_filament_loss_or_uncertainty(case, retention):
    session, _, _, _, _ = case
    run, _ = candidate(case, loss=True)
    report = contract.load_json(session/run['processing_checks']['linear_denoise']['report']['path'])
    assert report['regional_changes'][0]['channels'][0]['mad_change'] < 0
    assert report['recommended_verdict'] == 'uncertain' and 'denoise_assessment' not in report
    assert any(row['status'] == 'unmeasurable' and row['amplitude'] is None for row in report['multiscale'])
    block = denoise.assessment_skeleton(session, run)
    assert block['noise_benefit'] is None and all(r['retention'] is None for r in block['structures'])
    block.update(noise_benefit='observed', noise_observation='The bound sky crop has less grain',
                 adoption_reason='Actual filament observation controls this decision')
    for row in block['structures']: row.update(retention=retention, observation='The thin filament is lost or cannot be established')
    review = {'denoise_assessment': block, 'verdict': 'accept', 'gates': {'structure': 'pass', 'background': 'pass'},
              'inspected_materials': [{'path': k} for k in denoise.review_paths(session, run)]}
    with pytest.raises(contract.ContractError, match='retained structures'): denoise.validate_assessment(session, run, review)
    review['verdict'] = 'uncertain' if retention == 'uncertain' else 'reject'
    denoise.validate_assessment(session, run, review)


@pytest.mark.parametrize('benefit', ['not_observed', 'uncertain', 'observed'])
def test_agent_review_requires_benefit_and_every_bound_protected_roi(case, benefit):
    session, _, _, _, _ = case
    run, _ = candidate(case)
    block = denoise.assessment_skeleton(session, run)
    block.update(noise_benefit=benefit, noise_observation='Observed in the actual sky material', adoption_reason='Reviewed every protected structure')
    for row in block['structures']: row.update(retention='retained', observation='Continuity and edge contrast retained in the bound material')
    review = {'denoise_assessment': block, 'verdict': 'accept', 'gates': {'structure': 'pass', 'background': 'pass'},
              'inspected_materials': [{'path': k} for k in denoise.review_paths(session, run)]}
    if benefit == 'observed': denoise.validate_assessment(session, run, review)
    else:
        with pytest.raises(contract.ContractError): denoise.validate_assessment(session, run, review)
    block['structures'].pop()
    with pytest.raises(contract.ContractError): denoise.validate_assessment(session, run, review)


def test_diagnostics_replay_is_read_only_and_binds_actual_materials(case):
    session, source, payload, _, _ = case
    run, assets = candidate(case)
    paths = [session/row['path'] for row in run['processing_checks']['linear_denoise'].values() if isinstance(row, dict)]
    before = {p: p.read_bytes() for p in paths}
    assert processing.validate_processing_outputs(session, source, Path(assets['primary_output']), run,
        execution_policy=payload['execution_policy']) == run['processing_checks']
    assert {p: p.read_bytes() for p in paths} == before
    paths[0].unlink()
    with pytest.raises((OSError, contract.ContractError)):
        processing.validate_processing_outputs(session, source, Path(assets['primary_output']), run,
                                               execution_policy=payload['execution_policy'])
    assert not paths[0].exists()


def test_region_review_materials_are_native_detail_with_a_locator(case):
    session, _, _, _, _ = case
    run, _ = candidate(case)
    checked = run['processing_checks']['linear_denoise']
    materials = contract.load_json(session/checked['report']['path'])['review_materials']
    assert materials['schema'] == denoise.REVIEW_MATERIALS
    assert materials['manifest']['sha256'] == contract.sha256_file(session/materials['manifest']['path'])
    assert materials['index']['downsample'] == 1
    with Image.open(session/materials['index']['view']['path']) as index:
        assert max(index.size) <= denoise.VIEW_MAX
    for row in materials['regions']:
        width, height = row['rect'][2]-row['rect'][0], row['rect'][3]-row['rect'][1]
        assert row['zoom'] == denoise._zoom(width, height) == denoise.ZOOM_MAX and row['native'] is False
        assert row['view']['sha256'] == contract.sha256_file(session/row['view']['path'])
        assert row['residual_scales']['common'] == materials['index']['common_residual_scale']
        with Image.open(session/row['view']['path']) as view:
            tiles, remainder = divmod(view.size[0], width*row['zoom'])
            assert remainder == 0 and tiles in (3, 4)
            assert view.size[1] == height*row['zoom']+44


def test_highlight_index_lists_every_sample_with_its_native_core_view(case):
    session, _, _, _, _ = case
    run, _ = candidate(case, bright=True)
    checked = run['processing_checks']['linear_denoise']
    row = contract.load_json(session/checked['report']['path'])['review_materials']['highlights'][0]
    assert row['count'] == 1 and row['bbox'] == [10, 85, 11, 86]
    assert row['parent_count'] == 0 and row['candidate_count'] == 1
    assert row['pixels'][0]['x'] == 10 and row['pixels'][0]['y'] == 85
    assert row['pixels'][0]['parent'] < 1 and row['pixels'][0]['candidate'] == pytest.approx(1.2)
    assert row['tiles'] == 1 and row['tiles_truncated'] is False and row['pixels_truncated'] is False
    assert row['view']['sha256'] == contract.sha256_file(session/row['view']['path'])
    with Image.open(session/row['view']['path']) as view:
        assert view.size[0] >= 3*denoise.HIGHLIGHT_CROP and view.size[1] >= denoise.HIGHLIGHT_CROP


def test_review_binds_the_manifest_locator_and_each_region_material(case):
    session, _, _, _, _ = case
    run, _ = candidate(case)
    checked = run['processing_checks']['linear_denoise']
    paths = denoise.review_paths(session, run)
    assert {row['path'] for row in checked['review_files']} <= set(paths)
    assert checked['panel']['path'] not in paths and len(paths)+1 <= 32
    block = denoise.assessment_skeleton(session, run)
    materials = contract.load_json(session/checked['report']['path'])['review_materials']
    views = {row['id']: row for row in materials['regions']}
    assert [row['region']['id'] for row in block['structures']] == [row['id'] for row in materials['regions']
                                                                    if row['role'] != 'background']
    for row in block['structures']:
        material = views[row['region']['id']]
        assert {'view', 'slices', 'residual_scales', 'highlights'} <= set(row)
        assert row['view']['sha256'] == contract.sha256_file(session/row['view']['path'])
        assert row['residual_scales'] == material['residual_scales']
    block.update(noise_benefit='observed', noise_observation='The bound sky crop has less grain',
                 adoption_reason='Every bound region was inspected at native detail')
    for row in block['structures']:
        row.update(retention='retained', observation='Continuity and edge contrast retained')
    denoise.validate_assessment(session, run, {
        'denoise_assessment': block, 'verdict': 'accept', 'gates': {'structure': 'pass', 'background': 'pass'},
        'inspected_materials': [{'path': key} for key in paths]})


def test_large_regions_keep_native_detail_and_add_bounded_slices():
    before = np.full((1, 900, 1200), .5, dtype=np.float32)
    after = np.full((1, 900, 1200), .25, dtype=np.float32)
    material = denoise._review_materials(before, after, [{'id': 'shell', 'role': 'core', 'rect': [0, 0, 1200, 900]}],
                                        [0, .15, 1])
    row = material['regions'][0]
    assert row['zoom'] == 1 and row['native'] is True
    assert len(row['slices']) == 6
    assert all(sliced['rect'][2]-sliced['rect'][0] <= denoise.SLICE
               and sliced['rect'][3]-sliced['rect'][1] <= denoise.SLICE for sliced in row['slices'])
    names = [name for name, _ in material['files']]
    assert names[0] == 'denoise-index.png' and 'denoise-region-shell.png' in names
    assert len([name for name in names if '-slice-' in name]) == 6


def test_prior_diagnostics_without_review_materials_replay_unchanged(case):
    session, source, payload, _, _ = case
    run, assets = candidate(case)
    checked = run['processing_checks']['linear_denoise']
    report_path = session/checked['report']['path']
    report = contract.load_json(report_path)
    materials = report.pop('review_materials')
    for path in [materials['manifest']['path'], materials['index']['view']['path'],
                 *(row['view']['path'] for row in materials['regions']),
                 *(row['view']['path'] for row in materials['highlights'] if row['view'])]:
        (session/path).unlink()
    report_path.unlink()
    contract.atomic_write_json(report_path, report)
    checked.pop('review_files')
    checked['report'] = denoise.record(session, report_path)
    assert processing.validate_processing_outputs(session, source, Path(assets['primary_output']), run,
        execution_policy=payload['execution_policy']) == run['processing_checks']
    assert {path.name for path in report_path.parent.iterdir() if path.suffix == '.png'} == {'denoise-regions.png'}


def test_analysis_reads_rgb_xisf_and_never_proposes_strength(case):
    session, source, payload, _, request = case
    data, _ = denoise.read_scientific_pixels(source)
    image = source.with_suffix('.xisf'); xisf(image, np.repeat(data[:, ::-1], 3, axis=0))
    payload['input']['path'] = str(image)
    binding = {'path': '@input', 'sha256': contract.sha256_file(image)}
    analysis, _ = denoise.analyze_source(session, payload, binding, regions(session, image), [0, .15, 1])
    assert analysis['scientific_source']['source_format'] == 'XISF'
    assert len(analysis['measurements']['regions'][0]['candidate']['channels']) == 3
    assert 'parameters' not in analysis


def test_revision_requires_actual_evidence_new_id_and_same_parent(case, monkeypatch):
    session, _, _, path, request = case
    run, _ = candidate(case)
    contract.atomic_write_json(session/'runs/050-denoise.json', run)
    monkeypatch.setattr(core, '_load_run', lambda *args: (session/'runs/050-denoise.json', run))
    revised = copy.deepcopy(request)
    revised.update(parameters={'modulation': .06, 'cosmetic': False}, revision={
        'request': denoise.record(session, path), 'run_id': '050-denoise',
        'evidence': [run['processing_checks']['linear_denoise']['panel']], 'reason': 'Actual filament residual requires less modulation'})
    revision_path = session/'reports/revised.json'; revision_path.write_text(json.dumps(revised))
    result = denoise.generate(session, revision_path, '051-revised')
    assert '-mod=0.059999999999999998' in Path(result['script']).read_text()
    with pytest.raises(contract.ContractError): denoise.generate(session, revision_path, '050-denoise')
    revised['revision']['evidence'] = []; revision_path.write_text(json.dumps(revised))
    with pytest.raises(contract.ContractError): denoise.generate(session, revision_path, '052-invalid')


def test_cumulative_linear_denoise_is_rejected(case, monkeypatch):
    session, _, payload, path, _ = case
    monkeypatch.setattr(processing, 'source_state', lambda *args: {'domain': 'linear', 'run': None,
        'lineage': [{'protocol': 'restoration.denoise'}]})
    with pytest.raises(contract.ContractError, match='accumulate'): denoise.request_binding(session, payload, path)


def test_complete_outgoing_policy_documents_and_implementation_are_exact(tmp_path):
    # prior=linear-denoise-prior.json: 11 个域标签
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._PRE_DENOISE_EXECUTION_POLICY == PRIOR['execution_policy']
    assert sessions.known_execution_policy(PRIOR['execution_policy'])
    assert not sessions.execution_policy_is_current({'execution_policy': PRIOR['execution_policy']})
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert protected.implementation_matches(tmp_path, PRIOR['implementation'])
    changed = copy.deepcopy(PRIOR['implementation'])
    changed['deep_sky_siril_dualband.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_dualband.py', include_path=False)
    assert not protected.implementation_matches(tmp_path, changed)
    for name, fp in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp})
        assert not sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **fp, 'size': fp['size']+1})


@pytest.mark.parametrize('version', [1, 2, 3])
@pytest.mark.parametrize('enabled', [False, True])
def test_historical_hoo_recipe_bytes_are_unchanged(tmp_path, version, enabled):
    (tmp_path/'reports').mkdir()
    regions_path = tmp_path/'reports/regions.json'
    regions_path.write_text(json.dumps({'regions': [
        {'id': name, 'role': role, 'rect': rect} for name, role, rect in (
            ('sky', 'background', [0, 0, 10, 10]), ('filament', 'faint', [15, 15, 25, 25]),
            ('shell', 'faint', [30, 30, 40, 40]), ('core', 'core', [45, 45, 55, 55]),
            ('stars', 'stars', [60, 60, 70, 70]))]}))
    params = tmp_path/'reports/parameters.json'; params.write_text('{}')
    report = {'request': {'schema': f'starun-siril.dualband-request.v{version}',
        'regions': dual.record(tmp_path, regions_path), 'oiii_denoise': {'enabled': enabled, 'reason': 'Frozen recipe comparison'}},
        'bindings': {'request': dual.record(tmp_path, params)}}
    chains = {role: [{'command': 'mtf', 'parameters': ['0', '.2', '1']}] for role in dual.ROLES}
    value, *_ = dual.script_text(tmp_path, tmp_path/'artifacts/060-starless.fit', '075-hoo', params, report, chains)
    assert hashlib.sha256(value.replace(str(tmp_path), '/SESSION').encode()).hexdigest() == PRIOR['hoo_recipes'][f'v{version}-{enabled}']


def test_historical_policy_cannot_authorize_new_denoise(case):
    session, _, payload, path, _ = case
    payload['execution_policy'] = PRIOR['execution_policy']
    with pytest.raises(contract.ContractError, match='current frozen'): denoise.generate(session, path, '050-new')
    assert not (session/'reports/050-new').exists()


class MockNative:
    """Exercise recipes and evidence, without claiming to emulate NL-Bayes quality."""
    def __init__(self):
        self.commands = []
        self.session = None

    def __call__(self, argv, **kwargs):
        script = Path(next(v.split('=', 1)[1] for v in argv if v.startswith('--script=')))
        current = None
        for row in processing.script_commands(script.read_text()):
            self.commands.append(row)
            if row[0] == 'load': current = denoise.read_scientific_pixels(Path(row[1]))[0].copy()
            elif row[0] == 'split':
                for name, plane in zip(row[1:], current): write(Path(name).with_suffix('.fit'), plane)
            elif row[0] == 'pm':
                import re
                files = re.findall(r'\$([^$]+)\$', row[1])
                paths = [Path(p).with_suffix('.fit') for p in files]
                planes = [denoise.read_scientific_pixels(p if p.is_absolute() else self.session/p)[0] for p in paths]
                current = (planes[0]+planes[1])*.5 if '* 0.5' in row[1] else .8*planes[0]+.2*planes[1]
            elif row[0] == 'denoise':
                modulation = float(next(v.split('=', 1)[1] for v in row if v.startswith('-mod=')))
                current = current*(1-modulation)+np.mean(current, axis=(1, 2), keepdims=True)*modulation
            elif row[0] == 'save': write(Path(row[1]).with_suffix('.fit'), current)
            elif row[0] == 'rgbcomp':
                current = np.concatenate([denoise.read_scientific_pixels(Path(p))[0] for p in row[1:4]])
                write(Path(row[4].split('=', 1)[1]).with_suffix('.fit'), current)
            elif row[0] == 'savejpg':
                Image.new('RGB', (96, 96), (40, 40, 40)).save(Path(row[1]).with_suffix('.jpg'))
        channels, height, width = current.shape
        names = ('B&W',) if channels == 1 else ('Red', 'Green', 'Blue')
        stats = ''.join(name+' layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n' for name in names)
        return subprocess.CompletedProcess(argv, 0,
            f'Reading FITS: file synthetic.fit, {channels} layer(s), {width}x{height} pixels, 32 bits\n'+stats)


@pytest.mark.parametrize('history', [False, True])
def test_stage5_mocked_run_review_and_historical_replay(tmp_path, monkeypatch, history):
    session, source, _ = support._session(tmp_path, monkeypatch, input_state='linear', channel_mode='mono', width=96, height=96)
    inspected, direct, diagnostic = support._input_inspect_run(session, source, monkeypatch)
    support._review(session, inspected['id'], diagnostic, parent_preview=direct)
    payload = sessions.load_session(session)[1]
    request = {'schema': denoise.SCHEMA, 'source': {'path': '@input', 'sha256': contract.sha256_file(source)},
        'regions': regions(session, source), 'display_mtf': [0, .15, 1], 'decision': 'trial',
        'reason': 'Synthetic execution contract', 'parameters': {'modulation': .2, 'cosmetic': False},
        'parameter_reason': 'Explicit synthetic parameter', 'manual_lookup': {'status': 'not_needed', 'reason': 'Documented common variant', 'evidence': []}}
    fallback_request(session, request)
    path = session/'reports/proposal.json'; path.write_text(json.dumps(request))
    assets = denoise.generate(session, path, '050-denoise')
    native = MockNative(); monkeypatch.setattr(core.subprocess, 'run', native)
    args = dict(protocol='restoration.denoise', script_value=assets['script'], source_value=source,
                primary_output_value=assets['primary_output'], expected_values=assets['expected'], timeout=30)
    run = core.run_script(session, **args)
    assert run['status'] == 'success', json.dumps(run)
    draft = core.check_review(session, run['id'], None)['review']
    assert draft['denoise_assessment']['noise_benefit'] is None
    draft.update(verdict='uncertain', notes='Synthetic data cannot establish visual quality', gates={k: 'uncertain' for k in draft['gates']})
    draft['denoise_assessment'].update(noise_benefit='uncertain', noise_observation='No actual visual quality validation', adoption_reason='Retain the parent')
    for row in draft['denoise_assessment']['structures']: row.update(retention='uncertain', observation='No native image validation')
    review = session/'reviews/050-denoise.json'; contract.atomic_write_json(review, draft)
    assert core.check_review(session, run['id'], str(review))['status'] == 'review_valid'
    claimed = copy.deepcopy(draft)
    claimed.update(verdict='accept', gates={key: 'pass' for key in claimed['gates']})
    claimed['denoise_assessment']['noise_benefit'] = 'observed'
    for row in claimed['denoise_assessment']['structures']: row['retention'] = 'retained'
    contract.atomic_write_json(review, claimed)
    with pytest.raises(contract.ContractError, match='unchanged candidate'):
        core.check_review(session, run['id'], str(review))
    contract.atomic_write_json(review, draft)
    if history:
        run_path = session/'runs/050-denoise.json'; old = contract.load_json(run_path)
        old.pop('linear_denoise'); old['processing_checks'].pop('linear_denoise')
        contract.atomic_write_json(run_path, old)
        draft.pop('denoise_assessment'); contract.atomic_write_json(review, draft)
        _freeze_historical_evidence(session, snapshot={**PRIOR, 'documents': PRIOR['references']})
    before = support._session_bytes(session)
    replay = core.run_script(session, **args)
    assert replay['status'] == 'success' and replay['replayed'] is True
    assert core.check_review(session, run['id'], str(review))['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    if not history:
        revised = {**request, 'parameters': {'modulation': .1, 'cosmetic': False}, 'revision': {
            'request': denoise.record(session, path), 'run_id': run['id'],
            'evidence': [run['processing_checks']['linear_denoise']['panel']], 'reason': 'Observe the actual prior material before revising'}}
        revision_path = session/'reports/revised.json'; contract.atomic_write_json(revision_path, revised)
        assets = denoise.generate(session, revision_path, '051-revised')
        assert assets['status'] == 'proposed'
        revised_run = core.run_script(session, protocol='restoration.denoise', script_value=assets['script'], source_value=source,
            primary_output_value=assets['primary_output'], expected_values=assets['expected'], timeout=30)
        assert revised_run['status'] == 'success', revised_run
        assert core.check_review(session, revised_run['id'], None)['review']['denoise_assessment']['noise_benefit'] is None


@pytest.fixture
def hoo_case(case, monkeypatch):
    session, original, payload, _, _ = case
    parent = session/'artifacts/060-starless.fit'
    request = {'regions': {'path': 'reports/hoo-regions.json'}}
    make_v2(session, parent, request, channels=3)
    request['source'] = {'path': str(parent.relative_to(session)), 'sha256': contract.sha256_file(parent)}
    for row in request['operators']: row['factors'] = {'midtones': .2}
    request.update(schema=dual.V4, operators={role: copy.deepcopy(request['operators']) for role in dual.ROLES},
        manual_lookup={'status': 'not_needed', 'reason': 'Documented v4 recipe', 'evidence': []})
    full = write(session/'artifacts/060-full.fit', dual.pixels(parent)+.001)
    payload['context'].update(dualband='auto', channel_mode='dualband-osc', channel_map=None,
        filter_profile={'class': 'dual_band', 'lines': ['H-alpha', 'OIII'], 'source': 'header'})
    runtime = original.parent/'siril-cli'; runtime.write_text('#!/bin/sh\nexit 0\n'); runtime.chmod(0o755)
    (session/'reports/tool-probe.json').write_text(json.dumps(support._fake_probe(runtime)))
    separation = {'id': '060-stars', 'protocol': 'stars.separate', 'primary_output': str(parent.relative_to(session))}
    contract.atomic_write_json(session/'runs/060-stars.json', separation)
    contract.atomic_write_json(session/'reviews/060-stars.json', {'verdict': 'accept',
        'run_receipt_sha256': contract.sha256_file(session/'runs/060-stars.json')})
    monkeypatch.setattr(processing, 'source_state', lambda *args: {'domain': 'linear', 'run': separation})
    monkeypatch.setattr(dual, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(base_stretch,'accepted_parent',lambda *args: full)
    native = MockNative(); native.session = session
    monkeypatch.setattr(dual.subprocess, 'run', native)
    def child_solve(session, path, run_id, timeout, _derived_source=None, _shared_attempt=None):
        directory = session/'reports'/run_id; directory.mkdir()
        preview = directory/'selected-full.jpg'; Image.new('RGB', (96, 96)).save(preview)
        document = {'request': contract.load_json(path), 'transfer_chain': [{'command': 'mtf', 'parameters': ['0', '.2', '1']}],
            'bindings': {'full_source': dual.record(session, full)},
            'steps': [{'selected-full': {'.jpg': dual.record(session, preview)}}]}
        if _derived_source is not None: document['bindings']['derived_source'] = dual.record(session, _derived_source)
        report = directory/'parameters.json'; contract.atomic_write_json(report, document)
        return {'status': 'solved', 'parameters_path': str(report), 'report': document}
    monkeypatch.setattr(dual, 'solve_stretch_parameters', child_solve)
    monkeypatch.setattr(dual, 'load_parameters', lambda session, payload, path, **kwargs: (contract.load_json(path), None, path))
    monkeypatch.setattr(protected, 'measure_output', lambda *args, **kwargs: {'audit': {'constraints': []}})
    return session, parent, payload, request, native


@pytest.mark.parametrize('modulation,cosmetic', [(.08, False), (.7, True), (1., False)])
def test_hoo_prepare_trial_formal_and_independent_denoise_review(hoo_case, modulation, cosmetic):
    session, parent, payload, request, native = hoo_case
    prepared = dual.prepare(session, str(parent), '074-oiii-input', [0, .15, 1])
    oiii = session/prepared['oiii']['path']
    np.testing.assert_allclose(dual.pixels(oiii), (dual.pixels(parent)[1:2]+dual.pixels(parent)[2:3])*.5, atol=1e-7)
    proposal = {'schema': denoise.SCHEMA, 'source': {k: prepared['oiii'][k] for k in ('path', 'sha256')},
        'regions': regions(session, oiii), 'display_mtf': [0, .15, 1], 'derived_context': prepared['prepared'],
        'decision': 'trial', 'reason': 'Review actual monochrome OIII grain independently',
        'parameters': {'modulation': modulation, 'cosmetic': cosmetic}, 'parameter_reason': 'Explicit Agent-proposed OIII parameters',
        'manual_lookup': request['manual_lookup']}
    path = session/'reports/oiii-proposal.json'; contract.atomic_write_json(path, proposal)
    request['oiii_denoise'] = {'enabled': True, 'reason': proposal['reason'], 'proposal': dual.record(session, path)}
    path = session/'reports/hoo-request.json'; contract.atomic_write_json(path, request)
    path,request=bind_hoo(session,request,'073-hoo-route')
    solved=dual.solve(session,path,'075-hoo')
    assert solved['status'] == 'solved', solved
    report = contract.load_json(Path(solved['report']))
    assert report['linear_denoise']['parameters'] == proposal['parameters']
    trial_command = [row for row in native.commands if row[0] == 'denoise'][-1]
    assets = dual.generate(session, solved['report'])
    script = Path(assets['script']); value = script.read_text()
    assert contract.load_json(script.with_suffix('.provenance.json'))['references'] == [
        *contract.protocol_reference_bindings('stretch', variant='hoo'),
        *({**row, 'role': 'supporting'} for row in contract.protocol_reference_bindings('restoration.denoise'))]
    assert [row for row in processing.script_commands(value) if row[0] == 'denoise'] == [trial_command]
    assert trial_command == denoise.operator(proposal['parameters'])
    native(['mock', '--script='+str(script)])
    binding = dual.validate_stretch(session, payload, parent, Path(assets['primary_output']), value, '075-hoo')
    checks = dual.measure_outputs(session, parent, Path(assets['primary_output']), binding, write_diagnostics=True)
    assert checks['linear_denoise']['requires_visual_review'] is True
    assert dual.measure_outputs(session, parent, Path(assets['primary_output']), binding) == checks
    run = {'id': '075-hoo', 'source_path': str(parent.relative_to(session)), 'protocol': 'stretch',
           'dualband_transfer': binding, 'processing_checks': {'dualband': checks}}
    run_path = session/'runs/075-hoo.json'; contract.atomic_write_json(run_path, run)
    draft = core._review_skeleton(run_path, run, [{'path': key, 'sha256': contract.sha256_file(p)}
        for key, p in dual.review_paths(session, run).items()])
    assert len(draft['inspected_materials'])+1 <= 32  # Stage 7 adds its actual visibility panel.
    assert draft['denoise_assessment']['noise_benefit'] is None
    assert 'oiii-control' in binding['layers']
    with pytest.raises(contract.ContractError): denoise.validate_assessment(session, run, {**draft, 'verdict': 'accept'})
    path.write_text(path.read_text()+'\n')
    with pytest.raises(contract.ContractError): dual.validate_stretch(session, payload, parent, Path(assets['primary_output']), value, '075-hoo')


def test_new_hoo_cannot_fall_back_to_legacy_modulation(hoo_case):
    session, _, payload, request, native = hoo_case
    request.update(schema='starun-siril.dualband-request.v3', oiii_denoise={'enabled': True, 'reason': 'Missing common proposal'})
    path = session/'reports/hoo-request.json'; contract.atomic_write_json(path, request)
    with pytest.raises(contract.ContractError, match='v4'): dual.solve(session, path, '075-invalid')
    request['schema'] = dual.V4; contract.atomic_write_json(path, request)
    with pytest.raises(contract.ContractError): dual.solve(session, path, '076-invalid')
    assert not any(row[0] == 'denoise' for row in native.commands)


def test_hoo_without_denoise_has_no_common_candidate(hoo_case):
    session, _, _, request, native = hoo_case
    request['oiii_denoise'] = {'enabled': False, 'reason': 'No reviewable noise benefit expected'}
    path = session/'reports/hoo-request.json'; contract.atomic_write_json(path, request)
    path,request=bind_hoo(session,request,'073-hoo-route')
    solved=dual.solve(session,path,'075-no-denoise')
    assert solved['status'] == 'solved', solved
    assets = dual.generate(session, solved['report'])
    assert 'denoise ' not in Path(assets['script']).read_text()
    assert not any(row[0] == 'denoise' for row in native.commands)

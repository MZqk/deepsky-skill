"""Protected ROI selection, retained native evidence and historical stretch compatibility."""
from ._stretch_factory import solve_bound, bind_exception, bind_hoo

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, fingerprint, sha256_file
from .test_deep_sky_siril_stretch import context, mock_trials, write, BINARY
from .test_deep_sky_siril import _fake_probe
from .test_dualband_hoo import dual_case
from .test_recomposition_sources_v11 import source_case, linear_case, accept, recompose_assets
from .test_starnet_linear_bridge import native
from ._policy_pins import assert_pinned_history

@pytest.fixture(autouse=True)
def retired_operator_producer(monkeypatch, request):
    if request.node.originalname.startswith('test_exact_') or 'document_evidence' in request.node.originalname:
        return
    from ._policy_pins import outgoing_stretch_producer
    outgoing_stretch_producer(monkeypatch)



def scene(channels=1):
    y, x = np.indices((96, 96))
    data = .02+x*.00001+np.random.default_rng(211).normal(0, .0002, x.shape)
    data[46:64, 32:50] += .08
    data[26:38, 58:70] += .3+np.linspace(0, .03, 12)
    data += .35*np.exp(-((x-82)**2+(y-76)**2)/5)
    return np.array([data*factor for factor in ([1.] if channels == 1 else [1.1, 1., .8])], dtype='float32')


def make_v2(root, source, request, channels=1):
    data = scene(channels)
    write(source, data[0] if channels == 1 else data)
    regions = [
        {'id': 'sky', 'role': 'background', 'rect': [10, 10, 26, 26]},
        {'id': 'faint', 'role': 'faint', 'rect': [32, 32, 50, 50]},
        {'id': 'core', 'role': 'core', 'rect': [58, 58, 70, 70]},
        {'id': 'stars', 'role': 'stars', 'rect': [76, 14, 88, 26]},
    ]
    region_path = root/request['regions']['path']
    region_path.write_text(json.dumps({'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': sha256_file(source), 'width': 96, 'height': 96}, 'regions': regions}))
    request.update(schema=protected.REQUEST, profile='custom', targets={'faint': 'bright'}, core_absence=None,
                   source={'path': '@input', 'sha256': sha256_file(source)})
    request['regions']['sha256'] = sha256_file(region_path)
    request['operators'] = [{'method': 'mtf', 'purpose': 'Separate faint emission within explicit sky budgets',
        'constraints': [
            {'region': 'sky', 'statistic': 'median', 'min': .005, 'max': .5},
            {'region': 'sky', 'statistic': 'mad', 'min': 0., 'max': .04},
            {'region': 'sky', 'statistic': 'hf_sigma2', 'min': 0., 'max': .04},
            {'region': 'faint', 'statistic': 'median', 'min': .04, 'max': .8},
            {'region': 'core', 'statistic': 'p999', 'min': 0., 'max': .999},
            {'region': 'core', 'statistic': 'span', 'min': .00001, 'max': .99},
            {'region': 'stars', 'statistic': 'p999', 'min': 0., 'max': .999},
            {'region': 'stars', 'statistic': 'span', 'min': .00001, 'max': .99},
        ]}]
    return regions


@pytest.fixture
def case(context, monkeypatch):
    root, source, payload, path, request, _ = context
    regions = make_v2(root, source, request)
    payload['input'].update(fingerprint(source, include_path=False))
    (root/'session.json').write_text(json.dumps(payload))
    path.write_text(json.dumps(request))
    mock_trials(monkeypatch)
    original = stretch.NativeTrials.run
    def run(self, *args):
        output, evidence = original(self, *args)
        data = stretch.pixels(output)
        if len(data) == 1:
            data = np.repeat(data, 3, axis=0)
        Image.fromarray(np.uint8(np.clip(data.transpose(1, 2, 0), 0, 1)*255)).save(output.with_suffix('.jpg'))
        return output, evidence
    monkeypatch.setattr(stretch.NativeTrials, 'run', run)
    return root, source, payload, path, request, regions


@pytest.mark.parametrize('change', ['budget', 'core', 'target', 'context', 'overlap'])
def test_missing_protection_is_rejected(case, change):
    root, source, payload, path, request, regions = case
    if change == 'budget':
        request['operators'][0]['constraints'] = [c for c in request['operators'][0]['constraints'] if c['statistic'] != 'hf_sigma2']
    elif change == 'core':
        request['operators'][0]['constraints'] = [c for c in request['operators'][0]['constraints'] if c['region'] != 'core']
    elif change == 'target':
        request['targets'] = {}
    else:
        regions[0]['rect'] = [0, 0, 16, 16] if change == 'context' else regions[1]['rect']
        doc = json.loads((root/request['regions']['path']).read_text())
        doc['regions'] = regions
        (root/request['regions']['path']).write_text(json.dumps(doc))
        request['regions']['sha256'] = sha256_file(root/request['regions']['path'])
    path.write_text(json.dumps(request))
    with pytest.raises(ContractError):
        stretch.solve_stretch_parameters(root, path, '070-v2')


def test_core_absence_and_stellar_requirement(case):
    root, source, payload, path, request, regions = case
    without_core = [r for r in regions if r['role'] != 'core']
    with pytest.raises(ContractError, match='no-visible-core'):
        protected.validate(request, without_core)
    request['core_absence'] = 'No compact bright core is visible in this synthetic diffuse scene'
    request['operators'][0]['constraints'] = [c for c in request['operators'][0]['constraints'] if c['region'] != 'core']
    protected.validate(request, without_core)
    with pytest.raises(ContractError, match='stellar'):
        protected.validate(request, [r for r in without_core if r['role'] != 'stars'], full=True)


def trial(score, hf, mid):
    return {'coordinates': [mid], 'parameters': {'command': 'mtf', 'parameters': ['0', str(mid), '1']},
            'audit': {'eligible': True, 'separation': score, 'sky_hf': hf}}


def test_near_best_noise_tie_and_stable_fallback():
    rows = [trial(100, .04, .1), trial(96, .01, .2), trial(94.9, .0001, .3)]
    assert protected.select(rows)[0] == 1
    rows[1]['audit']['eligible'] = False
    assert protected.select(rows)[0] == 0
    rows = [trial(None, .0001, .1), trial(None, .04, .4)]
    assert protected.select(rows)[0] == 1
    assert protected.select(rows) == protected.select(copy.deepcopy(rows))


def test_direction_weakest_target_and_affine_invariance(case):
    _, source, _, _, request, regions = case
    data = stretch.pixels(source)
    constraints = request['operators'][0]['constraints']
    a = protected.audit(data, data, regions, [], {'faint': 'bright'})
    b = protected.audit(data, data*2+.1, regions, [], {'faint': 'bright'})
    assert a['separation'] == pytest.approx(b['separation'])
    wrong = protected.audit(data, data, regions, [], {'faint': 'dark'})
    assert 'target_direction' in wrong['reasons']
    dark = data.copy()
    dark[:, 46:64, 32:50] = .001
    assert protected.audit(dark, dark, regions, [], {'faint': 'dark'})['eligible']
    faint2 = {'id': 'weak', 'role': 'faint', 'rect': [35, 70, 45, 80]}
    result = protected.audit(data, data, [*regions, faint2], [], {'faint': 'bright', 'weak': 'bright'})
    assert result['separation'] < a['separation']


def test_zero_mad_and_highlight_spatial_detection(case):
    _, source, _, _, request, regions = case
    data = stretch.pixels(source)
    data[:, 62:94, 2:34] = .02
    result = protected.audit(data, data, regions, [], {'faint': 'bright'})
    assert result['separation'] is None
    assert result['score_fallback'] == 'sky_mad_at_most_1e-12'
    rgb = np.repeat(data, 3, axis=0)
    rgb[0, 30, 62] = 1
    after = rgb.copy()
    after[1, 31, 63] = 1
    assert np.percentile(after, 99) < 1
    result = protected.audit(rgb, after, regions, [], {'faint': 'bright'})
    assert result['endpoints'][0]['new_highlight'] == 0
    assert result['roi_endpoints']['core'][1]['new_highlight'] == 1
    assert not result['eligible']


def test_bounded_solve_retained_evidence_and_formal_output(case):
    root, source, payload, path, request, regions = case
    result = solve_bound(root, path, '070-v2')
    assert result['status'] == 'solved', result['report'].get('reason')
    report = result['report']
    step = report['steps'][0]
    assert step['evaluation_count']==1 and step['native_evaluation_count']==2
    assert len(step['representative_trials']) <= 3
    assert not any(json.loads(review.read_text()).get('run_id') == report['run_id']
                   for review in (root/'reviews').glob('*.json'))
    generated = stretch.generate_stretch_assets(root, parameters=result['parameters_path'])
    binding = stretch.adaptive_binding(root, payload, source, Path(generated['script_path']).read_text(),
                                       report['transfer_chain'], '070-v2')
    chosen = root/step['selected']['.fit']['path']
    assert protected.measure_output(root, source, chosen, report)['status'] == 'passed'
    paths = protected.review_paths(root, binding)
    assert step['local_panel']['path'] in paths
    assert len(paths) <= 4
    changed = stretch.pixels(chosen).copy()
    changed[:, 0, 0] += .001
    other = write(root/'artifacts/changed.fit', changed)
    with pytest.raises(ContractError, match='differs'):
        protected.measure_output(root, source, other, report)
    (root/step['local_panel']['path']).write_bytes(b'changed')
    with pytest.raises(ContractError, match='hash changed'):
        stretch.load_parameters(root, payload, result['parameters_path'])


def test_full_replay_retains_noise_budget_warnings(case, monkeypatch):
    root, source, payload, path, request, regions = case
    full = write(root/'artifacts/full.fit', stretch.pixels(source)+.0001)
    monkeypatch.setattr(stretch, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    result = solve_bound(root, path, '070-v2')
    assert result['status'] == 'solved', result['report'].get('reason')
    assert result['report']['steps'][0]['native_evaluation_count'] <= 128
    assert 'selected-full' in result['report']['steps'][0]
    stretch.load_parameters(root, payload, result['parameters_path'])
    request['operators'][0]['constraints'][2]['max'] = 0
    path.write_text(json.dumps(request))
    result = solve_bound(root, path, '071-budget')
    assert result['status'] == 'solved', result['report'].get('reason')
    assert result['report']['steps'][-1]['prefix_audit']['noise_warnings']
    stretch.load_parameters(root, payload, result['parameters_path'])


@pytest.mark.parametrize('method,channels', [('mtf', 1), ('asinh', 3), ('ght', 3)])
def test_native_protected_operators(case, monkeypatch, method, channels):
    if not BINARY.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    root, source, payload, path, request, _ = case
    regions = make_v2(root, source, request, channels)
    from astropy.io import fits
    with fits.open(source, mode='update') as hdus:
        hdus[0].header.update(CTYPE1='RA---TAN', CTYPE2='DEC--TAN', CRPIX1=48., CRPIX2=48.,
                              CRVAL1=23.123456789, CRVAL2=30.123456789,
                              CD1_1=-.001, CD1_2=0., CD2_1=0., CD2_2=.001, EQUINOX=2000.)
    request['source']['sha256'] = sha256_file(source)
    region_path = root/request['regions']['path']
    document = json.loads(region_path.read_text())
    document['reference']['sha256'] = sha256_file(source)
    region_path.write_text(json.dumps(document))
    request['regions']['sha256'] = sha256_file(region_path)
    payload['input'].update(fingerprint(source, include_path=False))
    (root/'session.json').write_text(json.dumps(payload))
    request['operators'][0]['method'] = method
    if method == 'ght':
        request['operators'][0]['landmarks'] = {k: {'region': r, 'statistic': s} for k, r, s in
            [('LP', 'sky', 'p99'), ('SP', 'faint', 'median'), ('HP', 'core', 'p01')]}
    path.write_text(json.dumps(request))
    (root/'reports/tool-probe.json').write_text(json.dumps(_fake_probe(BINARY)))
    monkeypatch.undo()
    from ._policy_pins import outgoing_stretch_producer
    outgoing_stretch_producer(monkeypatch)
    monkeypatch.setattr(sessions, 'load_session', lambda _: (root, payload, {}))
    monkeypatch.setattr(stretch, 'accepted_parent', lambda *args: None)
    result = solve_bound(root, path, '070-native-v2', timeout=180)
    assert result['status'] == 'solved', result['report'].get('reason')
    report, _, _ = stretch.load_parameters(root, payload, result['parameters_path'])
    output = root/report['steps'][-1]['selected']['.fit']['path']
    assert protected.measure_output(root, source, output, report)['status'] == 'passed'
    changed = root/'artifacts/wcs-drift.fit'
    changed.write_bytes(output.read_bytes())
    with fits.open(changed, mode='update') as hdus:
        hdus[0].header['CRVAL1'] += .01
    with pytest.raises(ContractError, match='WCS'):
        protected.measure_output(root, source, changed, report)


def test_exact_v21_history_and_profiles():
    root = Path(__file__).parents[1]
    before = json.loads((root/'tests/fixtures/stretch-policy-v21.json').read_text())
    assert_pinned_history(before, session_constant=sessions._MULTISCALE_EXECUTION_POLICY,
        command_mode='trim', domains={'stretch': protected.POLICY})
    assert len(json.loads(protected.PROFILE_PATH.read_text())) == 6


def field_request(session, parent, *, name='071-protected'):
    regions = [
        {'id': 'sky', 'role': 'background', 'rect': [20, 20, 60, 60]},
        {'id': 'shell', 'role': 'faint', 'rect': [240, 240, 272, 272]},
        {'id': 'core', 'role': 'core', 'rect': [250, 250, 262, 262]},
        {'id': 'stars', 'role': 'stars', 'rect': [100, 100, 120, 120]},
    ]
    region_path = session/'reports'/(name+'-regions.json')
    region_path.write_text(json.dumps({'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': sha256_file(parent), 'width': 512, 'height': 512}, 'regions': regions}))
    constraints = [
        {'region': 'sky', 'statistic': 'median', 'min': .001, 'max': .5},
        {'region': 'sky', 'statistic': 'mad', 'min': 0., 'max': .05},
        {'region': 'sky', 'statistic': 'hf_sigma2', 'min': 0., 'max': .05},
        {'region': 'shell', 'statistic': 'median', 'min': .01, 'max': .99},
        {'region': 'core', 'statistic': 'p999', 'min': 0., 'max': .999},
        {'region': 'core', 'statistic': 'span', 'min': 0., 'max': .99},
        {'region': 'stars', 'statistic': 'p999', 'min': 0., 'max': .999},
        {'region': 'stars', 'statistic': 'span', 'min': 0., 'max': .99},
    ]
    request = {'schema': protected.REQUEST, 'source': {'path': parent.relative_to(session).as_posix(), 'sha256': sha256_file(parent)},
        'regions': stretch.record(session, region_path), 'profile': 'custom', 'targets': {'shell': 'bright'}, 'core_absence': None,
        'operators': [{'method': 'mtf', 'purpose': 'Protect synthetic core and faint shell', 'constraints': constraints}],
        'manual_lookup': {'status': 'not_needed', 'reason': 'Complete explicit MTF skeleton', 'evidence': []}}
    path = session/'reports'/(name+'-request.json')
    path.write_text(json.dumps(request))
    return path, request


def test_native_v22_receipt_review_and_rgb_recomposition(linear_case):
    import deep_sky_siril_core as core
    import deep_sky_star_reduction as reduction
    session, _, original, parent, *_ = linear_case
    path, _ = field_request(session, parent)
    result = solve_bound(session, path, '071-protected', timeout=180)
    assert result['status'] == 'solved', result['report'].get('reason')
    assets = stretch.generate_stretch_assets(session, parameters=result['parameters_path'],run_id='071-protected')
    args = dict(protocol='stretch', script_value=assets['script_path'], source_value=parent,
        expected_values=[assets['primary_output'], assets['preview_output']], primary_output_value=assets['primary_output'], timeout=60)
    receipt = core.run_script(session, **args)
    assert receipt['status'] == 'success', receipt
    draft = core.check_review(session, '071-protected', None)['review']
    assert len(draft['inspected_materials']) <= 32
    missing = copy.deepcopy(draft)
    missing['inspected_materials'] = [r for r in missing['inspected_materials'] if not r['path'].endswith('fixed-rois.png')]
    missing.update(verdict='accept', gates={key: 'pass' for key in missing['gates']}, notes='Synthetic missing-material rejection')
    review = session/'reviews/missing-v22.json'
    review.write_text(json.dumps(missing))
    with pytest.raises(ContractError, match='fixed ROI|adoption reason'):
        core.check_review(session, '071-protected', review)
    review.unlink()
    accept(session, '071-protected')
    assert core.run_script(session, **args)['replayed']
    final = recompose_assets(session, source=assets['primary_output'], strength=.7)
    run = core.run_script(session, protocol='stars.recompose', script_value=final['script_path'], source_value=final['source'],
        expected_values=final['expected_outputs'], primary_output_value=final['primary_output'],
        separation_run='060-stars', stretch_run='071-protected', timeout=60)
    assert run['status'] == 'success', run
    assert max(run['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5


@pytest.mark.parametrize('denoise', [False, True])
def test_native_protected_hoo_independent_budgets(dual_case, denoise):
    import deep_sky_siril_dualband as dual
    import deep_sky_siril_core as core
    session, _, _, parent, *_ = dual_case
    path, request = field_request(session, parent, name='075-protected-hoo')
    request['schema'] = 'starun-siril.dualband-request.v4'
    operators = request['operators']
    for step in operators: step['factors']={'midtones':.1}
    request['operators'] = {role: copy.deepcopy(operators) for role in dual.ROLES}
    request['operators']['oiii'][0]['constraints'][0]['max'] = .6
    request['oiii_denoise'] = {'enabled': denoise, 'reason': 'Synthetic independent curve and budget regression'}
    path.write_text(json.dumps(request))
    bad = copy.deepcopy(request)
    bad['operators']['oiii'][0]['constraints'] = [c for c in bad['operators']['oiii'][0]['constraints'] if c['statistic'] != 'hf_sigma2']
    bad_path = session/'reports/bad-oiii-budget.json'
    bad_path.write_text(json.dumps(bad))
    if denoise:
        with pytest.raises(ContractError):
            dual.solve(session,path,'075-legacy-denoise',60)
        assert not list(session.glob('reports/*/scientific-attempt.json'))
        return
    with pytest.raises(ContractError,match='budgets'):
        bind_hoo(session,bad,'074-missing-budget')
    assert not (session/'reports/074-missing-budget').exists()
    path,request=bind_hoo(session,request,'074-hoo-route')
    result=dual.solve(session,path,'075-protected-hoo',60)
    assert result['status'] == 'solved', result
    assets = dual.generate(session, result['report'])
    receipt = core.run_script(session, protocol='stretch', script_value=assets['script'], source_value=parent,
        expected_values=assets['expected'], primary_output_value=assets['primary_output'], timeout=60)
    assert receipt['status'] == 'success', receipt
    assert receipt['processing_checks']['dualband']['protection']['endpoints']
    draft = core.check_review(session, '075-protected-hoo', None)['review']
    assert len(draft['inspected_materials']) <= 32
    assert any('selected-full.jpg' in row['path'] for row in draft['inspected_materials'])
    accept(session, '075-protected-hoo')
    import deep_sky_star_reduction as reduction
    stars = recompose_assets(session, source=assets['primary_output'], strength=.7)
    combined = core.run_script(session, protocol='stars.recompose', script_value=stars['script_path'], source_value=stars['source'],
        expected_values=stars['expected_outputs'], primary_output_value=stars['primary_output'],
        separation_run='060-stars', stretch_run='075-protected-hoo', timeout=60)
    assert combined['status'] == 'success', combined
    assert combined['transfer_chain'] == receipt['transfer_chain']
    assert max(combined['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5


@pytest.mark.parametrize('full_branch', [False, True])
def test_native_final_hdr_protection_and_shared_mask(case, monkeypatch, full_branch):
    import deep_sky_siril_hdr as hdr
    from .test_hdr_stretch import stretch_text, run_native
    root, source, payload, path, request, regions = case
    full = None
    if full_branch:
        full = write(root/'artifacts/linear-full.fit', stretch.pixels(source)[0]+.0001)
    monkeypatch.setattr(stretch, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    protection = {k: request[k] for k in ('source', 'regions', 'profile', 'targets', 'core_absence')}
    protection.update(schema='starun-siril.stretch-protection.v2', full_source=stretch.record(root, full) if full else None,
                      constraints=request['operators'][0]['constraints'])
    protection['constraints'][0]['max'] = .8
    protection['constraints'][3]['max'] = .999
    protection_path = root/'reports/hdr-protection.json'
    protection_path.write_text(json.dumps(protection))
    text, primary, outputs = stretch_text(root, source, 1)
    text = text.replace('set32bits\n', 'set32bits\n# stretch-protection: '+json.dumps(stretch.record(root, protection_path))+'\n', 1)
    if full:
        g, f, mixed = [root/'artifacts'/('full-'+key+'.fit') for key in ('gentle', 'faint', 'mixed')]
        preview = root/'previews/full-mixed.jpg'
        text += (f'load "{full}"\nmtf 0 0.2 1\nsave "{g}" -chksum\nclose\n'
                 f'load "{full}"\nmtf 0 0.06 1\nsave "{f}" -chksum\nclose\n'
                 f'pm "$artifacts/full-gentle$ * $artifacts/070-mask$ + $artifacts/full-faint$ * (1 - $artifacts/070-mask$)" -nosum\n'
                 f'save "{mixed}" -chksum\nicc_assign sRGB\nsavejpg "{preview.with_suffix("")}" 95\nclose\n')
        outputs += [g, f, mixed, preview]
    text = text.replace('mtf 0 0.06 1', 'mtf 0 0.06 0.2')
    transfer = hdr.validate_stretch(root, source, primary, text)
    run_native(root, text)
    assert np.count_nonzero(stretch.pixels(root/transfer['layers']['faint']) >= 1) > 0
    checks = hdr.measure_outputs(root, primary, {'hdr_transfer': transfer}, source=source)
    assert checks['protection']['candidate']['eligible']
    assert bool(checks['protection']['full_replay']) == full_branch
    assert max(checks['blend_max_abs']) < 1e-6
    assert len(hdr.protection_review_paths(root, transfer)) == (3 if full else 2)
    if full:
        with pytest.raises(ContractError):
            protected.measure_protection(root, source, primary, transfer['protection'])
    changed = stretch.pixels(primary)
    changed[0, 0, 0] = 1
    bad = write(root/'artifacts/hdr-new-endpoint.fit', changed[0])
    with pytest.raises(ContractError):
        protected.measure_protection(root, source, bad, transfer['protection'], mixed if full else None)


@pytest.mark.parametrize('issue', ['request', 'roi', 'runtime', 'trial-log', 'selected', 'ranking', 'implementation', 'downgrade'])
def test_protected_evidence_drift_rejected(case, monkeypatch, issue):
    root, source, payload, path, request, regions = case
    result = solve_bound(root, path, '070-drift')
    assert result['status'] == 'solved'
    report = result['report']
    if issue in {'request', 'roi'}:
        target = stretch.bound_file(root,report['bindings']['request']) if issue == 'request' else root/request['regions']['path']
        target.write_bytes(target.read_bytes()+b' ')
    elif issue == 'runtime':
        target = Path(report['bindings']['runtime']['path'])
        target.write_bytes(target.read_bytes()+b'changed')
    elif issue == 'trial-log':
        target = root/report['steps'][0]['trials'][0]['evidence']['log']['path']
        target.write_text('Error: invalid trial\n')
    elif issue == 'selected':
        (root/report['steps'][0]['selected']['.fit']['path']).unlink()
    elif issue in {'ranking', 'downgrade'}:
        target = Path(result['parameters_path'])
        document = json.loads(target.read_text())
        if issue == 'ranking':
            document['steps'][0]['representative_trials'] = []
        else:
            document['schema'] = 'starun-siril.stretch-parameters.v1'
        target.write_text(json.dumps(document))
    else:
        monkeypatch.setattr(protected, 'implementation', lambda *args: {})
    with pytest.raises((ContractError, OSError)):
        stretch.generate_stretch_assets(root, parameters=result['parameters_path'])


def test_v2_current_domain_prefix_and_timeout(case, monkeypatch):
    root, source, payload, path, request, regions = case
    request['operators'][0]['factors']={'midtones':.4}
    next(c for c in request['operators'][0]['constraints'] if c['region']=='sky' and c['statistic']=='median')['min']=0.
    request['operators'].append(copy.deepcopy(request['operators'][0]))
    path.write_text(json.dumps(request))
    result = solve_bound(root, path, '070-two-step')
    assert result['status'] == 'solved', result['report'].get('reason')
    first, second = result['report']['steps']
    assert second['input_domain'] == 'nonlinear_intermediate'
    assert second['input_sha256'] == first['selected_replay']['output_sha256']
    assert first['input_statistics'] != second['input_statistics']
    stretch.load_parameters(root, payload, result['parameters_path'])
    def timeout(*args):
        raise TimeoutError('Synthetic bounded timeout')
    monkeypatch.setattr(stretch.NativeTrials, 'run', timeout)
    assert solve_bound(root, path, '071-timeout')['status'] == 'timed_out'


@pytest.mark.parametrize('prior_implementation',[False,True])
@pytest.mark.parametrize('source_case',['hdr'],indirect=True)
def test_native_protected_hdr_full_lifecycle(source_case,monkeypatch,prior_implementation):
    import deep_sky_siril_core as core
    import deep_sky_siril_hdr as hdr
    session,primary,_,parent,*_=source_case
    receipt=json.loads((session/'runs/070-stretch.json').read_text())
    payload=json.loads((session/'session.json').read_text())
    if prior_implementation:
        prior=json.loads((Path(__file__).parents[1]/'tests/fixtures/reference-stretch-prior.json').read_text())
        mixed=copy.deepcopy(receipt)
        mixed['hdr_transfer']['protection']['implementation']=prior['implementation']
        with pytest.raises(ContractError, match='archive'):
            hdr.verify_receipt(session,payload,mixed)
        assert hdr.verify_receipt(session,payload,receipt)
        return
    assert receipt['processing_checks']['hdr']['protection']['full_replay']['eligible']
    assert core.run_script(session,protocol='stretch',script_value=session/receipt['script']['path'],source_value=parent,
        primary_output_value=primary,expected_values=[session/r['path'] for r in receipt['outputs']],timeout=60)['replayed']
    assets=recompose_assets(session,source=primary,strength=.7)
    run=core.run_script(session,protocol='stars.recompose',script_value=assets['script_path'],source_value=assets['source'],
        expected_values=assets['expected_outputs'],primary_output_value=assets['primary_output'],
        separation_run='060-stars',stretch_run='070-stretch',timeout=60)
    assert run['status']=='success',run
    assert max(run['processing_checks']['closure']['per_channel_max_abs'])<=1e-5

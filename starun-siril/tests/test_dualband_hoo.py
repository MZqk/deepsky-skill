"""Native display estimates, independent curves, RGB stars and immutable v19 replay."""
import copy
import json
from pathlib import Path
import time

import numpy as np
import pytest

from .test_recomposition_sources_v11 import recompose_assets
from .test_starnet_linear_bridge import native
from . import test_recomposition_sources_v11 as sources
from . import test_deep_sky_siril as support
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence
from .test_native_processing import write_fits
from ._policy_pins import assert_pinned_history
from ._stretch_factory import bind_hoo, explicit_request
import deep_sky_siril as cli
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_dualband as dual
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_star_reduction as reduction

ROOT = Path(__file__).parents[1]
BINARY = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')


def test_defaults_and_exact_v19_history():
    parser = cli.build_parser()
    args = ['init', 'input.fit', '--session', '/tmp/new-session']
    assert parser.parse_args(args).dualband == 'off'
    assert parser.parse_args([*args, '--dualband', 'auto']).dualband == 'auto'
    assert parser.parse_args([*args, '--dualband', 'off']).dualband == 'off'
    baseline = json.loads((ROOT/'tests/fixtures/dualband-policy-v19.json').read_text())
    assert_pinned_history(baseline, session_constant=sessions._VIVID_EXECUTION_POLICY,
        command_mode='hash', domains={'dualband': dual.POLICY})


def test_python_init_opt_in_is_frozen_and_not_recovered(tmp_path, monkeypatch):
    original, source, _ = support._session(tmp_path, monkeypatch, input_state='linear', color_ready=True)
    context = sessions.load_session(original)[1]['context']
    assert context['dualband'] == 'off'
    kwargs = {key: value for key, value in context.items() if key not in {'filter_profile', 'dualband'}}
    kwargs['state_evidence'] = ['user confirmed input_state=linear; channel_mode=broadband; stacked, unstretched master']
    enabled = tmp_path/'enabled'
    sessions.init_session(str(source), str(enabled), **kwargs, dualband='auto')
    assert sessions.load_session(enabled)[1]['context']['dualband'] == 'auto'
    before = support._session_bytes(enabled)
    recovered = sessions.init_session(str(source), str(tmp_path/'recovered'),
        **{**kwargs, 'input_state': 'auto', 'state_evidence': [], 'channel_mode': 'unknown'})
    assert recovered['context']['input_state'] == 'linear'
    assert recovered['context']['dualband'] == 'off'
    disabled = sessions.init_session(str(source), str(tmp_path/'disabled'), **kwargs, dualband='off')
    assert disabled['context']['dualband'] == 'off'
    assert sessions.load_session(enabled)[1]['context']['dualband'] == 'auto'
    assert support._session_bytes(enabled) == before


@pytest.mark.parametrize('change', ['off', 'broadband', 'nonlinear', 'unknown-filter', 'generic-lp', 'no-separation', 'mapped'])
def test_applicability_never_guesses_lines(change):
    payload = {'execution_policy': {'dualband': dual.POLICY}, 'context': {'dualband': 'auto',
        'channel_mode': 'dualband-osc', 'channel_map': None,
        'filter_profile': {'class': 'dual_band', 'lines': ['H-alpha', 'OIII'], 'source': 'header'}}}
    state = {'domain': 'linear', 'run': {'protocol': 'stars.separate'}}
    assert dual.applicability(payload, state) is None
    if change == 'off': payload['context']['dualband'] = 'off'
    elif change == 'broadband': payload['context']['channel_mode'] = 'broadband'
    elif change == 'nonlinear': state['domain'] = 'nonlinear'
    elif change == 'unknown-filter': payload['context']['filter_profile']['source'] = 'unavailable'
    elif change == 'generic-lp': payload['context']['filter_profile']['lines'] = []
    elif change == 'mapped': payload['context']['channel_map'] = {'red': 'Ha', 'green': 'OIII', 'blue': 'OIII'}
    else: state['run'] = None
    assert dual.applicability(payload, state)
    if change in {'unknown-filter', 'generic-lp'}:
        assert dual.applicability(payload, state, {'source': 'user', 'lines': ['H-alpha', 'OIII'],
            'evidence': 'User explicitly identified the acquisition as Ha/OIII dualband'}) is None


@pytest.fixture
def dual_case(native, monkeypatch, request):
    init = sessions.init_session
    writer = sources.write_fits
    def init_dual(*args, **kwargs):
        kwargs['channel_mode'] = 'dualband-osc'
        kwargs['dualband'] = 'auto'
        return init(*args, **kwargs)
    def write_dual(path, values, extra=(), **kwargs):
        if Path(path).name == 'master.fit':
            values = np.asarray(values) + np.random.default_rng(17).normal(0, .0002, np.asarray(values).shape)
        return writer(path, values, [*extra, ('FILTER', "'L-Extreme'")], **kwargs)
    monkeypatch.setattr(sessions, 'init_session', init_dual)
    monkeypatch.setattr(sources, 'write_fits', write_dual)
    return sources.linear_case.__wrapped__(native, monkeypatch, request)


def request_for(session, parent, *, denoise=False):
    regions = {'schema': 'starun-siril.regions.v1', 'reference': {'sha256': contract.sha256_file(parent),
        'width': 512, 'height': 512}, 'regions': [
        {'id': 'sky', 'role': 'background', 'rect': [10, 10, 42, 42]},
        {'id': 'shell', 'role': 'faint', 'rect': [240, 240, 272, 272]},
        {'id': 'core', 'role': 'core', 'rect': [250, 250, 262, 262]},
        {'id': 'outer', 'role': 'faint', 'rect': [280, 250, 290, 270]},
        {'id': 'stars', 'role': 'stars', 'rect': [100, 100, 120, 120]}]}
    path = session/'reports/dual-regions.json'
    contract.atomic_write_json(path, regions)
    operators = {}
    for role in dual.ROLES:
        operators[role] = [{'method': 'mtf', 'purpose': 'Inspect independent display contrast',
            'constraints': [{'region': 'sky', 'statistic': 'median', 'min': .001, 'max': .6},
                            {'region': 'shell', 'statistic': 'median', 'min': .4 if role == 'ha' else .05, 'max': .8},
                            {'region': 'outer', 'statistic': 'median', 'min': .001, 'max': .8},
                            {'region': 'core', 'statistic': 'median', 'min': .01, 'max': .99}]}]
    request = {'schema': 'starun-siril.dualband-request.v1',
        'source': {'path': parent.relative_to(session).as_posix(), 'sha256': contract.sha256_file(parent)},
        'regions': dual.record(session, path), 'operators': operators,
        'oiii_denoise': {'enabled': denoise, 'reason': 'Synthetic fixture: compare optional OIII noise suppression'},
        'manual_lookup': {'status': 'not_needed', 'reason': 'Exact documented native dualband skeleton', 'evidence': []}}
    request.update(profile='high_dynamic_range', targets={'shell':'bright','outer':'bright'}, core_absence=None)
    request['operators'] = {role:explicit_request(session, {**request, 'operators':steps})['operators']
                            for role,steps in request['operators'].items()}
    for steps in request['operators'].values():
        steps[0]['factors'] = {'midtones':.1}
    path, _ = bind_hoo(session, request, '074-hoo-strategy', body_stretch=50.)
    return path


@pytest.mark.parametrize('denoise', [False, True])
def test_native_dualband_lifecycle_and_original_rgb_stars(dual_case, denoise):
    session, _, original, parent, *_ = dual_case
    request = request_for(session, parent, denoise=denoise)
    if denoise:
        with pytest.raises(contract.ContractError, match='actual noise evidence'):
            dual.solve(session, request, '075-hoo', 180)
        assert not list(session.glob('reports/*/scientific-attempt.json'))
        return
    result = dual.solve(session, request, '075-hoo', 180)
    assert result['status'] == 'solved', result
    for name in ('075-hoo.ssf', '075-hoo.provenance.json'):
        link = session/'scripts'/name
        target = session.parent/('unwritten-'+name)
        link.symlink_to(target)
        with pytest.raises(contract.ContractError, match='preserve'):
            dual.generate(session, result['report'])
        assert not target.exists()
        link.unlink()
    generated = dual.generate(session, result['report'])
    script = Path(generated['script'])
    assert contract.load_json(script.with_suffix('.provenance.json'))['references'] == contract.protocol_reference_bindings('stretch', variant='hoo')
    arguments = dict(protocol='stretch', script_value=script, source_value=parent,
        expected_values=generated['expected'], primary_output_value=generated['primary_output'], timeout=60)
    payload = contract.load_json(session/'session.json')
    text = script.read_text()
    for changed in (text.replace('0.8 *', '0.7 *'), text.replace('fmul 1', 'fmul 2'),
                    text.replace('mtf ', 'mtf 0 0.5 1\nmtf ', 1)):
        with pytest.raises(contract.ContractError):
            dual.validate_stretch(session, payload, parent, Path(generated['primary_output']), changed, '075-hoo')
    report_path = Path(result['report'])
    original_report = report_path.read_bytes()
    forged = json.loads(original_report)
    forged['bindings']['runtime']['version'] = '1.4.0'
    report_path.write_text(json.dumps(forged))
    with pytest.raises(contract.ContractError, match='runtime'):
        dual.parameter_binding(session, payload, report_path)
    report_path.write_bytes(original_report)
    for key, value in [('band_semantics', 'pure_lines'), ('star_color_route', 'hoo'), ('requires_visual_review', False)]:
        forged = json.loads(original_report)
        forged[key] = value
        report_path.write_text(json.dumps(forged))
        with pytest.raises(contract.ContractError):
            dual.parameter_binding(session, payload, report_path)
    report_path.write_bytes(original_report)
    for path in (parent, session/json.loads(original_report)['bindings']['regions']['path']):
        before = path.read_bytes()
        path.write_bytes(before+b' ')
        with pytest.raises(contract.ContractError):
            dual.parameter_binding(session, payload, report_path)
        path.write_bytes(before)
    assert core.run_script(session, validate_only=True, **arguments)['status'] == 'success'
    receipt = core.run_script(session, **arguments)
    assert receipt['status'] == 'success', receipt
    binding = receipt['dualband_transfer']
    assert receipt['transfer_chain'] == binding['chains']['rgb']
    assert binding['chains']['ha'] != binding['chains']['oiii']
    assert receipt['processing_checks']['dualband']['formula_max_abs']['palette'] < 1e-6
    assert ('oiii-denoised' in binding['layers']) == denoise
    import deep_sky_siril_artifact_focus as focus
    if focus.enabled(contract.load_json(session/'session.json')):
        plan, goals, _, _, _, _ = focus.run_frames(session, contract.load_json(session/'session.json'), receipt)
        value = contract.load_json(session/goals['artifact_focus']['path'])
        value.update(previous=goals['artifact_focus'], none_reason=None, areas=[{'id':'palette-edge','rect':[1,1,16,16],
            'risks':['color_fringe'],'reason':'Inspect an actual small palette-transition crop in this synthetic fixture.'}])
        artifact_path=session/'reports/075-hoo-focus.json'
        focus.write(artifact_path,value)
        focus.prepare(session,artifact_path,'075-hoo-focus',run=receipt['id'])
    draft = core.review_materials_for_run(session, receipt['id'])
    assert len(draft['inspected_materials']) <= 32
    sources.accept(session, receipt['id'])
    committed = support._session_bytes(session)
    assert core.run_script(session, **arguments)['replayed']
    assert core.check_review(session, receipt['id'], session/f"reviews/{receipt['id']}.json")['status'] == 'review_valid'
    assert support._session_bytes(session) == committed
    mapped = Path(generated['primary_output'])
    remapped = session/'artifacts/080-remapped.fit'
    with pytest.raises(contract.ContractError, match='already has its HOO display palette'):
        processing.prepare_processing(session, payload, 'color.map', mapped, remapped,
            f'load "{mapped}"\nsave "{remapped}"\n', {}, None, None,
            lambda name: core._load_run(session, name), lambda name: core._accepted_review(session, name)[0],
            run_id='080-remapped')
    assert not remapped.exists()
    review_path = session/'reviews/075-hoo.json'
    review = contract.load_json(review_path)
    incomplete = copy.deepcopy(review)
    incomplete['inspected_materials'] = [r for r in review['inspected_materials'] if r['path'] != binding['previews']['oiii-control']]
    contract.atomic_write_json(review_path, incomplete)
    with pytest.raises(contract.ContractError, match='Dualband review'):
        core._validate_review(session, review_path, receipt['id'])
    contract.atomic_write_json(review_path, review)
    output = recompose_assets(session_path=session, source=Path(generated['primary_output']),
                                                 strength=1.)
    combined = core.run_script(session, protocol='stars.recompose', script_value=output['script_path'],
        source_value=output['source'], expected_values=output['expected_outputs'],
        primary_output_value=output['primary_output'], separation_run='060-stars', stretch_run='075-hoo', timeout=60)
    assert combined['status'] == 'success', combined
    assert combined['dualband_origin']['star_color_route'] == 'original_dualband_rgb'
    assert max(combined['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5
    rgb_stars = dual.pixels(session/combined['branch_binding']['display_stars'])
    rgb_base = dual.pixels(session/combined['branch_binding']['full_baseline'])
    rgb_starless = dual.pixels(session/binding['layers']['rgb-baseline'])
    assert np.max(np.abs(rgb_stars-(rgb_base-rgb_starless))) < 1e-6
    primary = dual.pixels(Path(generated['primary_output']))
    final = dual.pixels(Path(output['primary_output']))
    assert np.max(np.abs(final-primary-rgb_stars)) < 1e-6
    # The palette is intentionally different; closure belongs to the RGB branch.
    assert np.max(np.abs(final-rgb_base)) > .01
    report_path = Path(result['report'])
    before = report_path.read_bytes()
    report = json.loads(before)
    report['request']['oiii_denoise']['enabled'] = not denoise
    report_path.write_text(json.dumps(report))
    with pytest.raises(contract.ContractError):
        dual.generate(session, report_path)
    report_path.write_bytes(before)
    report = json.loads(before)
    chain_path = session/report['chains']['ha']['path']
    child = contract.load_json(chain_path)
    log = session/child['steps'][0]['selected_replay']['log']['path']
    log.write_text(log.read_text()+'\ndrift')
    with pytest.raises(contract.ContractError):
        core.run_script(session, **arguments)


def test_native_signed_extraction(tmp_path):
    if not BINARY.is_file():
        pytest.skip('Native Siril unavailable')
    root = tmp_path.resolve()
    source, h, g, b, o = [root/(name+'.fit') for name in ('source', 'h', 'g', 'b', 'o')]
    data = np.random.default_rng(7).normal(.01, .02, (3, 64, 64)).astype('float32')
    write_fits(source, data)
    runtime = {'path': str(BINARY), 'fingerprint': contract.fingerprint(BINARY)}
    q = lambda p: json.dumps(str(p))
    v = lambda p: '$'+str(p.with_suffix(''))+'$'
    dual._native(root, runtime, root, 'signed-extract', ['load '+q(source), 'fmul 1',
        'split '+' '.join(q(p.with_suffix('')) for p in (h,g,b)),
        'pm '+q(f'({v(g)} + {v(b)}) * 0.5')+' -nosum', 'save '+q(o.with_suffix(''))+' -chksum'], o, time.monotonic()+30)
    assert np.min(dual.pixels(o)) < 0
    assert np.max(np.abs(dual.pixels(o)-(data[1:2].astype(float)+data[2:3])*.5)) < 1e-7


def test_ordinary_stretch_does_not_gain_denoise_authority(tmp_path, monkeypatch):
    payload = {'context': {'input_state':'linear'}, 'execution_policy': {}}
    source, primary = tmp_path/'source.fit', tmp_path/'out.fit'
    monkeypatch.setattr(processing, 'source_state', lambda *a: {'domain':'linear', 'run':None, 'separation_run':None})
    text = f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\ndenoise -mod=0.35\nsave "{primary}"\n'
    with pytest.raises(contract.ContractError, match='strict dualband'):
        processing.prepare_processing(tmp_path,payload,'stretch',source,primary,text,{},None,None,None,None,run_id='075-hoo')

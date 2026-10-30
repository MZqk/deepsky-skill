"""Protected masks, display color selectors and exact historical bindings."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from . import test_deep_sky_siril as support
from .test_recomposition_sources_v11 import source_case, accept, generated_run, recompose_assets
from .test_starnet_linear_bridge import native
from .test_multiscale_competition import request_file, run_candidate
from .test_stage8_proposals import proposal
from .test_dualband_hoo import dual_case
from .test_stretch_v22 import field_request
from ._stretch_factory import bind_hoo, explicit_request
import deep_sky_siril_dualband as dual
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_color as color
import deep_sky_siril_local_contrast as local
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
import deep_sky_star_reduction as reduction

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/protected-finish-prior.json').read_text())


def test_v3_changes_mask_support_only():
    request = proposal()
    old = local.request_candidates(request)
    request['schema'] = local.PROTECTED_REQUEST
    new = local.request_candidates(request)
    assert len(new) == 2
    for name, p in old.items():
        assert new[name] == {**p, 'mask_support': 'source_exclusion_v2'}
    request['wavelet'] = {'amplitude': .4, 'fine_detail': False}
    assert local.request_candidates(request)['wavelet']['coefficients'] == [1, 1, 1.048, 1.032, 1, 1]
    with pytest.raises(contract.ContractError):
        local.request_candidates(request, fine_off=True)


def test_texture_reports_every_roi_and_keeps_the_weakest_gate():
    rows = [{'id': 'sky', 'role': 'background', 'sigma': 2, 'amplitude': .01},
            {'id': 'shock', 'role': 'faint', 'sigma': 2, 'amplitude': .031},
            {'id': 'edge', 'role': 'faint', 'sigma': 2, 'amplitude': .0116}]
    with pytest.raises(contract.ContractError) as exc:
        local.texture(rows)
    assert all(s in str(exc.value) for s in ('ROI=edge', 'shock', 'background_amplitude', 'not physical SNR'))
    assert local.texture(rows[:2])['texture_snr'] == 3.1


@pytest.mark.parametrize('selector', ['cyan', 'red'])
def test_selector_support_and_continuity(selector):
    t = np.array([-1e-7, 0, 1e-7])
    values = (np.stack([1-t, np.ones(3), np.ones(3)]) if selector == 'cyan'
              else np.stack([2+t, np.ones(3), np.ones(3)]))
    weights = color.selector_weight(values, selector)
    assert weights[0] == weights[1] == 0 < weights[2] < 1e-12
    assert np.array_equal(color.selector_weight(np.zeros((3, 3)), selector), np.zeros(3))


@pytest.mark.parametrize('change', [{'midtones': v} for v in (True, 0, .5, float('nan'), float('inf'), '0.48')]
                         + [{'selector': 'cyan'}, {'gain': .1}, {'regions': ''}])
def test_invalid_local_tone_is_rejected(change):
    p = {'selector': 'red', 'midtones': .48, 'regions': 'reports/regions.json', **change}
    with pytest.raises((contract.ContractError, ValueError)):
        color.tone_declaration('# local-tone: '+json.dumps(p))


@pytest.mark.parametrize('selector,target', [('cyan', None), ('red', .1), (True, .1), ([], .1)])
def test_invalid_selector_never_loads_session(monkeypatch, selector, target):
    monkeypatch.setattr(sessions, 'load_session', lambda *a: pytest.fail('Must validate before session access'))
    with pytest.raises(contract.ContractError):
        color.solve('/missing', 'missing.fit', 'missing.json', '088-color', selector=selector, target_gain=target)


def region_file(session, parent, selector):
    path = session/f'reports/{selector}-regions.json'
    rect = [238, 248, 252, 264] if selector == 'cyan' else [260, 248, 274, 264]
    support.core.atomic_write_json(path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 512, 'height': 512},
        'regions': [{'id': 'sky', 'role': 'background', 'rect': [80, 80, 104, 104]},
                    {'id': selector, 'role': 'faint', 'rect': rect}]})
    return path


def formal(session, source, primary, text, outputs, protocol, run_id):
    if protocol == 'tone.adjust':
        from .test_tone_adjust import _tone_proposal
        text = _tone_proposal(session, source, text, run_id)
    script = session/f'scripts/{run_id}.ssf'; script.write_text(text)
    support._write_ssf_provenance(session, script, protocol)
    options = dict(protocol=protocol, script_value=str(script), source_value=str(source),
                   primary_output_value=str(primary), expected_values=list(map(str, outputs)), timeout=60)
    assert core.run_script(session, validate_only=True, **options)['status'] == 'success'
    run = core.run_script(session, **options)
    assert run['status'] == 'success', run
    accept(session, run['id'])
    frozen = support._session_bytes(session)
    assert core.run_script(session, **options)['replayed']
    assert support._session_bytes(session) == frozen
    return run, options


@pytest.mark.parametrize('candidate', ['wavelet', 'clahe'])
def test_native_v3_trial_formal_review_and_replay(source_case, candidate):
    session, parent, *_ = source_case
    path = request_file(session, parent)
    request = core.load_json(path)
    request['mask'].update(low_start=.2, low_end=.3, high_start=.6)
    request.update(schema=local.PROTECTED_REQUEST, wavelet={'amplitude': .4, 'fine_detail': False}, clahe={'strength': .13})
    core.atomic_write_json(path, request)
    result = local.solve(session, path, '080-protected', timeout=60)
    report = local.load_parameters(session, result['parameters'])
    assert set(report['candidates']) == {'wavelet', 'clahe'}
    assets = local.generate(session, result['parameters'], candidate)
    run = run_candidate(session, assets)
    assert run['status'] == 'success', run
    checks = run['processing_checks']['local_contrast']['native']
    assert checks['max_errors']['mask_minimum'] < 2e-6
    binding = run['local_contrast_guard']['native']['files']
    before, after = color.pixels(parent), color.pixels(Path(assets['primary_output']))
    base = color.pixels(session/binding['mask-base'])[0]
    blurred = color.pixels(session/binding['mask-smoothed'])[0]
    np.testing.assert_array_equal(color.pixels(session/binding['mask'])[0], np.minimum(base, blurred))
    excluded = base == 0
    assert excluded.any() and np.array_equal(before[:, excluded], after[:, excluded])
    accept(session, run['id'])
    assert run_candidate(session, assets)['replayed']
    data = core.load_json(Path(result['parameters']))
    data['bindings']['helper'] = {'path': 'scripts/deep_sky_siril_local_contrast.py', **PRIOR['helper']}
    core.atomic_write_json(Path(result['parameters']), data)
    with pytest.raises(contract.ContractError, match='Prior helpers|parameter binding changed: helper'):
        local.load_parameters(session, result['parameters'])


@pytest.mark.parametrize('source_case, expected_status', [('selective', 'solved'), ('selective-tied', 'solved')], indirect=['source_case'])
def test_native_red_then_cyan_review_replay_and_stars(source_case, monkeypatch, expected_status):
    session, parent, original, *_ = source_case
    request_path = request_file(session, parent, '080-detail')
    request = core.load_json(request_path)
    request.update(schema=local.PROTECTED_REQUEST, wavelet={'amplitude': .4, 'fine_detail': False}, clahe={'strength': .13})
    request['mask'].update(low_start=.2, low_end=.3, high_start=.6)
    core.atomic_write_json(request_path, request)
    trial = local.solve(session, request_path, '080-detail', timeout=60)
    detail = local.generate(session, trial['parameters'], 'wavelet')
    enhanced = run_candidate(session, detail)
    assert enhanced['status'] == 'success'
    accept(session, enhanced['id'])
    parent = Path(detail['primary_output'])
    regions = region_file(session, parent, 'red')
    p = {'selector': 'red', 'midtones': .48, 'regions': regions.relative_to(session).as_posix()}
    primary = session/'artifacts/085-red.fit'
    text, outputs, _ = color.local_tone_script(session, parent, primary, p)
    with pytest.raises(contract.ContractError, match='local-tone declaration'):
        processing.validate_adjustment(session, parent, primary, processing.script_commands(text))
    run, options = formal(session, parent, primary, text, outputs, 'tone.adjust', '085-red')
    assert run['processing_checks']['local_tone']['positive_common_gain']
    assert max(run['processing_checks']['local_tone']['protected_max_abs']) == 0
    with pytest.raises(contract.ContractError, match='exactly match'):
        color.validate_local_tone(session, parent, primary,
            processing.script_commands(text.replace('mtf 0 0.48 1', 'mtf 0 0.47 1')), p)
    review_path = session/'reviews/085-red.json'
    original_review = review_path.read_bytes()
    incomplete = core.load_json(review_path)
    incomplete['inspected_materials'] = [r for r in incomplete['inspected_materials'] if r['path'] != run['local_tone']['previews']['mask']]
    core.atomic_write_json(review_path, incomplete)
    with pytest.raises(contract.ContractError):
        core.check_review(session, run['id'], review_path)
    review_path.write_bytes(original_review)
    original_regions = regions.read_bytes()
    regions.write_bytes(original_regions+b' ')
    with pytest.raises(contract.ContractError):
        color.measure_local_tone(session, parent, primary, run['local_tone'])
    regions.write_bytes(original_regions)
    before, after = color.pixels(parent), color.pixels(primary)
    np.testing.assert_allclose(after[0]/after[1], before[0]/before[1], rtol=2e-6)
    assert np.any(after > before)
    draft = core.review_materials_for_run(session, run['id'])
    assert len(draft['inspected_materials']) <= 32
    assert regions.relative_to(session).as_posix() in {r['path'] for r in draft['inspected_materials']}
    regions = region_file(session, primary, 'cyan')
    zero = color.solve(session, primary, regions, '088-zero', selector='cyan', target_gain=0)
    assert zero['status'] == 'skipped' and not core.load_json(Path(zero['report']))['trials']
    unmasked = color.solve(session, primary, regions, '087-cyan-probe', target_gain=.15, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                           goal='Assess the remaining cyan subject color', parameter_reason='One explicit native color comparison')
    solved = color.solve(session, primary, regions, '088-cyan', selector='cyan', target_gain=.15, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                         mask_mode='luminance', goal='Protect other regions while enhancing the cyan subject',
                         parameter_reason='Compare the existing selector and luminance protection at the same D',
                         previous=unmasked['report'], observation='The full-color comparison motivates selective protection')
    report = core.load_json(Path(solved['report']))
    assert report['selector'] == 'cyan'
    assert solved['status'] == expected_status
    if solved['status'] == 'skipped':
        assert not any(t['eligible'] for t in report['trials'])
        assert len(report['trials']) <= 1
        return
    assert solved['status'] == 'solved', report
    text, candidate, outputs, _ = color.script_text(session, primary, '088-cyan', solved['parameters'], report=solved['report'])
    cyan, _ = formal(session, primary, candidate, text, outputs, 'color.finish', '088-cyan')
    assert cyan['local_saturation']['parameters']['selector'] == 'cyan'
    assert cyan['processing_checks']['color_curve']['regions'][1]['selector_pixels'] > 0
    assets = recompose_assets(session, source=Path(candidate), strength=.39)
    stars = generated_run(session, assets)
    assert stars['status'] == 'success', stars
    assert max(stars['processing_checks']['closure']['per_channel_max_abs']) < 1e-5
    accept(session, stars['id'])
    execute = core.subprocess.run
    inspection, direct, diagnostic = support._input_inspect_run(session, original, monkeypatch)
    support._review(session, inspection['id'], diagnostic, parent_preview=direct)
    monkeypatch.setattr(core.subprocess, 'run', execute)
    script, final = support._delivery_script(session, session/stars['primary_output'])
    delivered = core.run_script(session, protocol='delivery.render', script_value=script, source_value=str(session/stars['primary_output']),
        expected_values=[final, final.with_suffix('.fit')], timeout=30)
    assert delivered['status'] == 'success'
    review = accept(session, delivered['id'])
    selection = support._selection(session, delivered['id'], final, review)
    selected = core.load_json(selection)
    selected['selected_runs'] = ['060-stars', '070-stretch', enhanced['id'], '085-red', '088-cyan', '090-recompose', delivered['id']]
    selected['review_receipts'] = [f'reviews/{n}.json' for n in selected['selected_runs']]
    core.atomic_write_json(selection, selected)
    immutable = {p: p.read_bytes() for p in [*list((session/'runs').glob('*.json')), *list((session/'reviews').glob('*.json'))]}
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert all(p.read_bytes() == data for p, data in immutable.items())


def test_current_and_prior_binding_sets_are_exact(tmp_path):
    core.atomic_write_json(tmp_path/'session.json', {'execution_policy': PRIOR['execution_policy']})
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation'])
    mixed['deep_sky_siril_processing.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_processing.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    from query_siril_manual import _HISTORICAL_COMMAND_POLICIES
    old = PRIOR['documents']['references/command-policy.json']
    assert _HISTORICAL_COMMAND_POLICIES[old['sha256'], old['size']] == PRIOR['command_policy'].encode()


def test_cli_forwards_explicit_cyan_target(monkeypatch, capsys):
    import sys
    calls = []
    monkeypatch.setattr(color, 'solve', lambda *a, **k: calls.append((a, k)) or {'status': 'skipped'})
    monkeypatch.setattr(sys, 'argv', ['color', '--session', '/session', '--source', 'input.fit',
        '--regions', 'reports/cyan.json', '--run-id', '088-cyan', '--selector', 'cyan', '--target-gain', '.15'])
    assert color.main() == 0
    assert calls[0][1] == {'selector': 'cyan', 'target_gain': .15}
    assert json.loads(capsys.readouterr().out)['status'] == 'skipped'


@pytest.mark.parametrize('text', [
    '# local-tone: {"selector":"red","selector":"red","midtones":0.48,"regions":"reports/r.json"}',
    '# local-tone: {}\n# local-tone: {}', '# local-tone {}',
])
def test_local_tone_rejects_ambiguous_declarations(text):
    with pytest.raises((contract.ContractError, ValueError)):
        color.tone_declaration(text)


@pytest.mark.parametrize('section,key,value', [
    ('wavelet', 'amplitude', True), ('wavelet', 'amplitude', float('nan')),
    ('wavelet', 'fine_detail', 0), ('clahe', 'strength', float('inf')),
    ('mask', 'sigma', 3), ('mask', 'support', 'arbitrary'),
])
def test_v3_preserves_parameter_boundaries(section, key, value):
    request = proposal(); request['schema'] = local.PROTECTED_REQUEST
    request[section][key] = value
    with pytest.raises(contract.ContractError):
        local.request_candidates(request)


def test_native_hoo_accepts_explicit_selector_without_remapping(dual_case):
    session, _, _, parent, *_ = dual_case
    path, request = field_request(session, parent, name='075-hoo')
    request['schema'] = 'starun-siril.dualband-request.v4'
    step = request['operators'][0]
    step['factors'] = {'midtones': .1}
    request['operators'] = {role: [copy.deepcopy(step)] for role in dual.ROLES}
    request['oiii_denoise'] = {'enabled': False, 'reason': 'No additional denoising in this explicit synthetic proposal'}
    request['operators'] = {role:explicit_request(session, {**request,'operators':steps})['operators']
                            for role,steps in request['operators'].items()}
    path, request = bind_hoo(session, request, '074-hoo-strategy')
    solved = dual.solve(session, path, '075-hoo', 180)
    assert solved['status'] == 'solved'
    assets = dual.generate(session, solved['report'])
    receipt = core.run_script(session, protocol='stretch', script_value=assets['script'], source_value=str(parent),
        expected_values=assets['expected'], primary_output_value=assets['primary_output'], timeout=60)
    assert receipt['status'] == 'success'
    accept(session, receipt['id'])
    mapped = Path(assets['primary_output'])
    regions = region_file(session, mapped, 'cyan')
    before = contract.fingerprint(mapped, include_path=False)
    result = color.solve(session, mapped, regions, '088-cyan-zero', selector='cyan', target_gain=0)
    assert result['status'] == 'skipped'
    report = core.load_json(Path(result['report']))
    assert report['selector'] == 'cyan' and report['starless'] and not report['trials']
    assert contract.fingerprint(mapped, include_path=False) == before
    remapped = session/'artifacts/080-remapped.fit'
    with pytest.raises(contract.ContractError, match='already has its HOO display palette'):
        processing.prepare_processing(session, core.load_json(session/'session.json'), 'color.map', mapped, remapped,
            f'load "{mapped}"\nsave "{remapped}"\n', {}, None, None,
            lambda n: core._load_run(session, n), lambda n: core._accepted_review(session, n)[0], run_id='080-remapped')
    assert not remapped.exists()

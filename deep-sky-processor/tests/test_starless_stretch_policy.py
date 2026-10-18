"""Regression coverage for anchored starless curves and their entrypoints."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from agent_protocol import intent_to_overrides
from agent_workflow import apply_action, initialize_session
from artifact_gates import check_channel_clipping, check_core_plateau
from config_fingerprint import assign_step, stage_fingerprint
from fits_io import read_image, write_image
from pipeline import run_pipeline
from stretch import background_compressed_stretch, render_protected_stretch


def scene(size=128, compact=False):
    yy, xx = np.mgrid[:size, :size]
    radius = 2 if compact else size / 7
    y = .002 + .008 * np.exp(-((xx-size/2)**2 + (yy-size/2)**2) / (2*radius**2))
    y += np.random.default_rng(18).normal(0, 2e-6, y.shape)
    return np.stack([y*1.5, y, y*.5], axis=2).astype(np.float32)


def layers(tmp_path, image=None):
    starless = scene() if image is None else image
    stars = np.zeros_like(starless)
    stars[15:18, 15:18] = .15
    paths = {name: tmp_path / (name + '.tif') for name in ('source', 'starless', 'stars')}
    for name, data in (('source', starless + stars), ('starless', starless), ('stars', stars)):
        write_image(data, paths[name])
        Path(str(paths[name]) + '.meta.json').write_text(json.dumps({'is_linear': True}))
    return paths, starless, stars


def run(tmp_path, paths, name, **kwargs):
    return run_pipeline(
        str(paths['starless']), str(tmp_path / (name + '.tif')),
        steps='stretch', preset='light', input_is_linear=True,
        auto_crop_edges=False, background_neutralize_lock=False,
        star_layers={**{key: str(value) for key, value in paths.items()}, 'domain': 'linear'},
        **kwargs)


def test_anchored_curve_preserves_color_zeros_and_extra_channels():
    image = scene()
    image[0, 0] = 0
    alpha = np.linspace(0, 1, image.shape[0]*image.shape[1]).reshape(image.shape[:2])
    rgba = np.dstack([image, alpha]).astype(np.float32)
    out, report = background_compressed_stretch(rgba, subject_midtones=.25)
    assert out.dtype == np.float32 and out.shape == rgba.shape
    assert np.isfinite(out).all() and out.min() >= 0
    assert out[..., :3].max() < .95
    assert np.array_equal(out[..., 3], rgba[..., 3])
    assert np.array_equal(out[0, 0, :3], image[0, 0])
    nonzero = image[..., 1] > 0
    assert np.allclose(out[..., 0][nonzero] / out[..., 1][nonzero], 1.5, rtol=2e-6)
    assert np.allclose(out[..., 2][nonzero] / out[..., 1][nonzero], .5, rtol=2e-6)
    assert report['subject_samples'] >= 25 and report['anchor_status'] == 'available'
    assert abs(report['actual_subject'] - .25) <= .0125
    assert abs(report['actual_background'] - .035) <= .0035


def test_strong_color_white_anchor_has_no_flattened_highlights():
    y = np.linspace(.002, .08, 160000, dtype=np.float32).reshape(400, 400)
    image = np.stack([y*1.5, y, y*.5], axis=2)
    out, report = background_compressed_stretch(image, protect=0)
    assert out.max() < .95
    assert not check_channel_clipping(out)['escalate']
    assert not check_core_plateau(out, image)['escalate']
    highlights = out.mean(2)[y > report['measured_sky']]
    assert np.all(np.diff(highlights) > 0)


def test_bright_outlier_does_not_dim_other_pixels():
    image = scene()
    changed = image.copy()
    changed[0, 0] = [100, 1, 0]
    before, _ = background_compressed_stretch(image, protect=0)
    after, _ = background_compressed_stretch(changed, protect=0)
    # One outlier may move robust statistics slightly, but cannot cap the whole image.
    assert np.allclose(before[40:80, 40:80], after[40:80, 40:80], rtol=.001)


@pytest.mark.parametrize('midtones', [.035, .4, float('nan'), float('inf')])
def test_invalid_subject_targets_are_rejected(midtones):
    with pytest.raises(ValueError, match='Subject midtones'):
        background_compressed_stretch(scene(), subject_midtones=midtones)


def test_pure_noise_falls_back_without_lowering_detection_threshold():
    y = np.random.default_rng(91).normal(.002, .0001, (128, 128)).astype(np.float32)
    out, report = background_compressed_stretch(y, subject_midtones=.25)
    assert report['method'] == 'pi_mtf'
    assert report['fallback_reason'] == 'insufficient_subject_samples'
    assert report['review_required'] and report['subject_detection_sigma'] == 3
    assert out.shape == y.shape and np.isfinite(out).all()


def test_small_planetary_subject_uses_a_resolved_anchor():
    out, report = background_compressed_stretch(scene(256, compact=True),
                                               subject_midtones=.25, mask_sigma=1,
                                               target_type='planetary_nebula')
    assert report['anchor_status'] == 'available'
    assert abs(report['actual_subject'] - .25) <= .0125
    assert not check_channel_clipping(out)['escalate']


def test_subject_lift_keeps_background_and_residual_point_sources_conservative():
    image = scene(256)
    yy, xx = np.mgrid[:256, :256]
    image += (.0002 * xx / 255)[..., None]
    clean = image.copy()
    image += (.03 * np.exp(-((xx-195)**2 + (yy-65)**2) / 8))[..., None]
    point_mask = ((xx-195)**2 + (yy-65)**2 <= 9).astype(np.float32)
    baseline, _ = render_protected_stretch(image, 'pi_mtf', {'target_bg': .035})
    low_frequency = gaussian_filter(image.mean(2), 5)
    background_samples = low_frequency <= np.percentile(low_frequency, 25)
    background_gain = .035 / np.median(baseline.mean(2)[background_samples])
    with patch('star_tools.detect_stars', return_value=point_mask):
        out, report = background_compressed_stretch(image, subject_midtones=.25)
        clean_out, _ = background_compressed_stretch(clean, subject_midtones=.25)
    clean_reference, _ = render_protected_stretch(clean, 'pi_mtf', {'target_bg': .035})
    clean_gain = .035 / np.median(clean_reference.mean(2)[background_samples])
    # Preserve the diffuse pedestal while limiting the residual point profile.
    point_excess = out[65, 201].mean() - clean_out[65, 201].mean()
    reference_excess = (baseline[65, 201].mean() * background_gain
                        - clean_reference[65, 201].mean() * clean_gain)
    assert abs(point_excess - reference_excess) < .02
    assert report['point_source_guard_fraction'] > 0
    assert report['actual_subject'] == pytest.approx(.25, abs=.0125)
    assert report['actual_background'] == pytest.approx(.035, abs=.0035)
    background = xx < 30
    relative_noise = lambda y: np.median(np.abs(y - np.median(y))) / np.median(y)
    assert relative_noise(out.mean(2)[background]) <= 1.6 * relative_noise(
        baseline.mean(2)[background])
    core_mask = ((xx-128)**2 + (yy-128)**2 <= 9).astype(np.float32)
    with patch('star_tools.detect_stars', return_value=core_mask):
        core_protected, _ = background_compressed_stretch(clean, subject_midtones=.25)
    with patch('star_tools.detect_stars', return_value=np.zeros_like(core_mask)):
        unprotected, _ = background_compressed_stretch(clean, subject_midtones=.25)
    assert core_protected[128, 128].mean() >= .8 * unprotected[128, 128].mean()


def test_full_frame_dark_structure_stays_darker_than_its_carrier():
    yy, xx = np.mgrid[:128, :128]
    y = .003 + .002 * (xx / 127) - .001 * np.exp(-((xx-64)/10)**2)
    image = np.stack([y, y, y], axis=2).astype(np.float32)
    out, report = background_compressed_stretch(image, subject_midtones=.20)
    assert report['anchor_status'] == 'available'
    assert np.median(out[:, 62:66]) < np.median(out[:, 90:105])
    assert np.ptp(out[:, 10:30]) > 0


def test_subject_blend_calibration_stays_bounded_with_bright_compact_cores():
    yy, xx = np.mgrid[:128, :128]
    for strength in [.001, .1, 1., 10.]:
        lum = .001 + strength * np.exp(-((xx-64)**2 + (yy-64)**2) / (2*15**2))
        image = np.stack([lum*1.5, lum, lum*.5], axis=2).astype(np.float32)
        with patch('star_tools.detect_stars', return_value=np.zeros_like(lum)):
            out, report = background_compressed_stretch(image, subject_midtones=.25)
        assert np.isfinite(out).all() and out.min() >= 0 and out.max() < .95
        assert 0 <= report['subject_lift_gain'] <= 2
        if not report['subject_target_reached']:
            assert report['review_required']


@pytest.mark.parametrize('kind,target', [
    ('galaxy', .25), ('emission_nebula', .25), ('planetary_nebula', .25),
    ('reflection_nebula', .20), ('dark_nebula', .20), ('wide_field', .25),
])
def test_policy_three_dispatches_all_valid_starless_layers(tmp_path, kind, target):
    paths, _, _ = layers(tmp_path)
    result = run(tmp_path, paths, 'out', target_type=kind)
    diagnostics = result['stretch_diagnostics']
    assert diagnostics['selected_method'] == 'background_compressed'
    selected = diagnostics.get('rejected_initial', {}).get('curve', diagnostics)
    assert selected['subject_midtones'] == target
    assert selected['mask_sigma'] == (1 if kind == 'planetary_nebula' else 5)
    assert result['runtime']['stretch_policy_version'] == 3


def test_same_call_external_and_resumed_layers_have_identical_pixels(tmp_path):
    paths, starless, stars = layers(tmp_path)
    report = {'fallback_applied': False, 'repair_quality_score': 1., 'repair_method': 'fixture'}
    common = dict(steps='star_remove,stretch', preset='light', target_type='emission_nebula',
                  input_is_linear=True, auto_crop_edges=False, background_neutralize_lock=False)
    with patch('pipeline.separate_stars', return_value=(starless, stars, stars.max(2), report)):
        same = run_pipeline(str(paths['source']), str(tmp_path/'same.tif'), **common)
    external = run_pipeline(str(paths['source']), str(tmp_path/'external.tif'),
                            external_starless=str(paths['starless']), **common)
    resumed = run(tmp_path, paths, 'resumed', target_type='emission_nebula')
    expected, _ = read_image(same['outputs']['image'])
    for result in (external, resumed):
        actual, _ = read_image(result['outputs']['image'])
        assert np.array_equal(actual, expected)
        assert result['stretch_diagnostics']['applied_method'] == same['stretch_diagnostics']['applied_method']


@pytest.mark.parametrize('method,overrides,expected', [
    ('pi_mtf', {'stretch_method': 'background_compressed'}, 'pi_mtf'),
    ('masked_ghs_standard', None, 'masked_ghs_standard'),
    ('auto', {'stretch_method': 'emission'}, 'emission'),
])
def test_explicit_methods_precede_starless_auto_policy(tmp_path, method, overrides, expected):
    paths, _, _ = layers(tmp_path)
    result = run(tmp_path, paths, 'explicit', target_type='galaxy',
                 stretch_method=method, override_params=overrides)
    assert result['stretch_diagnostics']['selected_method'] == expected


def test_legacy_policy_and_explicit_anchor_disable(tmp_path):
    paths, _, _ = layers(tmp_path)
    legacy = run(tmp_path, paths, 'legacy', target_type='reflection_nebula',
                 override_params={'stretch_policy_version': 2})
    assert legacy['stretch_diagnostics']['selected_method'] == 'arcsinh'
    disabled = run(tmp_path, paths, 'disabled', target_type='galaxy',
                   override_params={'stretch_subject_midtones': None})
    assert disabled['stretch_diagnostics']['anchor_status'] == 'disabled'
    assert disabled['stretch_diagnostics']['subject_midtones'] is None


def test_unavailable_anchor_remains_an_unresolved_quality_issue(tmp_path):
    y = np.random.default_rng(91).normal(.002, .0001, (128, 128)).astype(np.float32)
    paths, _, _ = layers(tmp_path, np.stack([y, y, y], axis=2))
    result = run(tmp_path, paths, 'noise', target_type='galaxy')
    assert result['status'] == 'review_required'
    assert any(issue['code'] == 'STARLESS_ANCHOR_UNAVAILABLE'
               for issue in result['quality_state']['issues'])


def test_rejected_subject_candidate_retries_once_with_reduced_target(tmp_path):
    paths, image, _ = layers(tmp_path)
    calls = []

    def reject_first(source, **kwargs):
        calls.append(kwargs['subject_midtones'])
        out, report = background_compressed_stretch(source, **kwargs)
        return (np.full_like(image, .8), report) if len(calls) == 1 else (out, report)

    with (patch('pipeline.background_compressed_stretch', side_effect=reject_first),
          patch('fidelity_gates.evaluate_fidelity_gates', return_value={
              'status': 'success', 'gates': [], 'context': {}})):
        result = run(tmp_path, paths, 'retry', target_type='galaxy')
    assert calls == [.25, .035 + .5 * (.25 - .035)]
    assert result['stretch_recovery'] == 'reduced_subject_midtones'
    assert result['stretch_diagnostics']['rejected_initial']['curve']['subject_midtones'] == .25
    assert result['execution']['used_params']['stretch']['stretch_subject_midtones'] == calls[-1]
    assert result['stretch_diagnostics']['actual_subject'] == pytest.approx(calls[-1], abs=.01)


def test_subject_target_changes_the_stretch_fingerprint_and_intent():
    assert assign_step('stretch_subject_midtones') == 'stretch'
    assert stage_fingerprint({'stretch_subject_midtones': .25}) != stage_fingerprint(
        {'stretch_subject_midtones': .27})
    assert intent_to_overrides({'subject_visibility': 'increase_slightly'}) == {
        'stretch_factor': 52., 'stretch_subject_midtones': .27}
    assert intent_to_overrides({'subject_visibility': 'increase'}) == {
        'stretch_factor': 65., 'stretch_subject_midtones': .30}


def test_old_agent_intent_does_not_silently_enable_subject_anchoring(tmp_path):
    paths, _, _ = layers(tmp_path)
    state = initialize_session(paths['starless'], tmp_path/'session', target_type='galaxy')
    state['stretch_policy_version'] = 2
    (tmp_path/'session/session.json').write_text(json.dumps(state))
    result = apply_action(tmp_path/'session', {
        'operation': 'run_step', 'step': 'stretch', 'preset': 'light',
        'stretch_method': 'background_compressed',
        'intent': {'subject_visibility': 'increase'},
        'auto_crop_edges': False, 'background_neutralize_lock': False,
    })['result']['pipeline_result']
    assert result['stretch_diagnostics']['policy_version'] == 2
    assert result['stretch_diagnostics']['anchor_status'] == 'disabled'


def test_background_compressed_api_cli_and_agent_use_identical_pixels(tmp_path):
    paths, _, _ = layers(tmp_path)
    parameters = {'stretch_subject_midtones': .25, 'target_bg': .035}
    common = dict(steps='stretch', preset='light', target_type='galaxy',
                  input_is_linear=True, auto_crop_edges=False, background_neutralize_lock=False,
                  stretch_method='background_compressed', override_params=parameters)
    direct = run_pipeline(str(paths['starless']), str(tmp_path/'direct.tif'), **common)
    subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[1]/'scripts/pipeline.py'),
        str(paths['starless']), str(tmp_path/'cli.tif'), '--steps', 'stretch',
        '--strength', 'light', '--target-type', 'galaxy', '--input-linear',
        '--stretch-method', 'background_compressed', '--override-params', json.dumps(parameters),
        '--no-auto-crop-edges', '--no-background-neutralize',
    ], check=True, capture_output=True)
    initialize_session(paths['starless'], tmp_path/'session', target_type='galaxy')
    agent = apply_action(tmp_path/'session', {
        'operation': 'run_step', 'step': 'stretch', 'preset': 'light',
        'stretch_method': 'background_compressed', 'params': parameters,
        'input_is_linear': True, 'auto_crop_edges': False, 'background_neutralize_lock': False,
    })['result']['pipeline_result']
    expected, _ = read_image(direct['outputs']['image'])
    for path in (tmp_path/'cli.tif', agent['outputs']['image']):
        actual, _ = read_image(path)
        assert np.array_equal(actual, expected)
    for result in (direct, agent):
        consumed = result['execution']['used_params']['stretch']
        if result['stretch_diagnostics']['applied_method'] == 'background_compressed':
            assert consumed['stretch_subject_midtones'] == .25
        else:
            assert 'stretch_subject_midtones' not in consumed
            assert result['execution']['unused_params']['stretch_subject_midtones']['value'] == .25
        assert 'stretch_factor' not in consumed
        assert 'ghs_protect_strength' not in consumed


def test_fidelity_failure_retries_then_preserves_safe_mtf_evidence(tmp_path):
    paths, _, _ = layers(tmp_path)
    calls = []
    gate = {'code': 'BACKGROUND_RELATIVE_NOISE_BOUNDED', 'status': 'failed',
            'escalate': True, 'message': 'Injected failure', 'value': 2., 'threshold': 1.6}

    def reject_subject(candidate, reference, **kwargs):
        calls.append(candidate)
        failed = not np.array_equal(candidate, reference)
        return {'status': 'review_required' if failed else 'success',
                'gates': [gate] if failed else [], 'context': {}}

    with patch('fidelity_gates.evaluate_fidelity_gates', side_effect=reject_subject):
        result = run(tmp_path, paths, 'fidelity', target_type='galaxy')
    diagnostics = result['stretch_diagnostics']
    assert len(calls) == 3
    assert result['stretch_recovery'] == 'conservative_pi_mtf'
    assert diagnostics['applied_method'] == 'pi_mtf'
    assert diagnostics['fidelity_gates']['status'] == 'success'
    assert diagnostics['rejected_initial']['display_gates']['fidelity_gates']['gates'] == [gate]
    assert diagnostics['subject_retry']['display_gates']['fidelity_gates']['gates'] == [gate]

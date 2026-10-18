"""Regression coverage for the three-master acceptance failures."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import tifffile
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from agent_workflow import initialize_session, apply_action, apply_review, load_state
from artifact_gates import check_channel_clipping
from denoise import validate_applied_denoise
from fits_io import write_image, read_image, read_capture_metadata, extract_capture_metadata, build_physical_priors
from pipeline import run_pipeline
from star_tools import separate_stars


def scene(size=96):
    yy, xx = np.mgrid[:size, :size]
    base = 0.02 + 0.04 * np.exp(-((xx-size/2)**2 + (yy-size/2)**2) / (size/5)**2)
    stars = np.zeros_like(base)
    for x, y in ((20, 22), (65, 24), (35, 66)):
        stars += 0.2 * np.exp(-((xx-x)**2 + (yy-y)**2) / 4)
    return np.repeat((base + stars)[..., None], 3, axis=2).astype(np.float32), np.repeat(base[..., None], 3, axis=2).astype(np.float32)


def run(directory, source, step, **kwargs):
    return run_pipeline(str(source), str(directory / 'out.tif'), steps=step,
                        work_dir=str(directory / 'artifacts'), preset=kwargs.pop('preset', 'light'),
                        auto_crop_edges=False, background_neutralize_lock=False, **kwargs)


def test_adaptive_three_entrypoints_and_metadata(tmp_path):
    source = tmp_path / 'source.tif'
    write_image(scene()[0], source)
    metadata = extract_capture_metadata({'EXPTIME': 12000, 'FILTER': 'Duo-Band', 'DET-TEMP': 10})
    metadata['exposure_kind'] = 'integration'
    Path(str(source) + '.meta.json').write_text(json.dumps({'capture_metadata': metadata, 'is_linear': True}))
    direct = run(tmp_path / 'direct', source, 'stretch', preset='adaptive', target_type='emission_nebula')
    cli_result = tmp_path / 'cli.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/pipeline.py'), str(source), str(tmp_path / 'cli.tif'),
                    '--steps', 'stretch', '--strength', 'adaptive', '--target-type', 'emission_nebula',
                    '--no-auto-crop-edges', '--no-background-neutralize', '--result-json', str(cli_result)],
                   check=True, capture_output=True, text=True)
    cli = json.loads(cli_result.read_text())
    session = tmp_path / 'session'
    initialize_session(source, session, target_type='emission_nebula')
    agent = apply_action(session, {'operation': 'run_step', 'step': 'stretch', 'preset': 'adaptive',
                                  'auto_crop_edges': False, 'background_neutralize_lock': False})['result']
    for result in (cli, agent['pipeline_result']):
        for key in ('stretch_factor', 'star_stretch_factor', 'stretch_gamma', 'saturation'):
            assert result['effective_config']['params'][key] == direct['effective_config']['params'][key]
        assert result['capture_metadata'] == metadata
        assert result['effective_config']['input_is_linear'] is True
    assert agent['review']['context']['effective_params'] == agent['pipeline_result']['effective_config']['params']
    assert 'long_subexposure_protect_star_cores' not in direct['physical_priors']['recommendations']
    assert read_capture_metadata(direct['outputs']['image'])[0] == metadata


def test_channel_clipping_localizes_red_only_and_preserves_existing():
    before = np.full((64, 64, 3), 0.1, dtype=np.float32)
    after = before.copy()
    after[20:32, 30:42, 0] = 1
    gate = check_channel_clipping(after, before)
    assert gate['status'] == 'failed' and gate['escalate']
    assert np.mean(after, axis=-1).max() < 0.995
    assert gate['value']['r']['regions'][0]['area_px'] == 144
    assert gate['value']['g']['n_regions'] == 0
    assert check_channel_clipping(after, after)['status'] == 'passed'
    grown = after.copy()
    grown[15:38, 25:48, 0] = 1
    assert check_channel_clipping(grown, after)['status'] == 'failed'
    specks = before.copy()
    specks[::10, ::10, 0] = 1
    assert check_channel_clipping(specks, before)['status'] == 'passed'


def test_layers_resume_without_reseparation_or_double_stretch(tmp_path):
    image, base = scene()
    source, external = tmp_path / 'source.tif', tmp_path / 'external.tif'
    write_image(image, source)
    write_image(base, external)
    full = run(tmp_path / 'full', source, 'star_remove,stretch,star_process,star_combine',
               input_is_linear=True, external_starless=str(external), stretch_method='arcsinh')
    separated = run(tmp_path / 'separated', source, 'star_remove', input_is_linear=True, external_starless=str(external))
    np.testing.assert_allclose(read_image(separated['outputs']['image'])[0], base)
    with patch('pipeline.separate_stars', side_effect=AssertionError('Repeated separation')):
        stretched = run(tmp_path / 'stretched', separated['outputs']['image'], 'stretch',
                        star_layers=separated['star_layers'], stretch_method='arcsinh')
        processed = run(tmp_path / 'processed', stretched['outputs']['image'], 'star_process', star_layers=stretched['star_layers'])
        combined = run(tmp_path / 'combined', processed['outputs']['image'], 'star_combine', star_layers=processed['star_layers'])
    assert processed['effective_config']['steps'] == ['star_process']
    assert combined['effective_config']['steps'] == ['star_combine']
    assert processed['effective_config']['params']['star_stretch_factor'] == full['effective_config']['params']['star_stretch_factor']
    np.testing.assert_allclose(read_image(combined['outputs']['image'])[0], read_image(full['outputs']['image'])[0], atol=1e-6)
    assert combined['star_layers'] is None
    with pytest.raises(ValueError, match='source'):
        run(tmp_path / 'wrong', source, 'stretch', star_layers=separated['star_layers'])
    with pytest.raises(ValueError, match='already'):
        run(tmp_path / 'double', stretched['outputs']['image'], 'stretch', star_layers=stretched['star_layers'])
    with pytest.raises(ValueError, match='Roll back'):
        run(tmp_path / 'stale', stretched['outputs']['image'], 'dbe', star_layers=stretched['star_layers'])
    tifffile.imwrite(separated['star_layers']['source'], image * .9)
    with pytest.raises(ValueError, match='modified'):
        run(tmp_path / 'modified', separated['outputs']['image'], 'stretch', star_layers=separated['star_layers'])


def test_agent_layer_accept_variant_and_rollback(tmp_path):
    image, base = scene()
    source, external, session = tmp_path / 'source.tif', tmp_path / 'external.tif', tmp_path / 'session'
    write_image(image, source)
    write_image(base, external)
    initialize_session(source, session, target_type='wide_field')
    removed = apply_action(session, {'operation': 'run_step', 'step': 'star_remove', 'preset': 'light',
                                    'external_starless': str(external), 'params': {'input_is_linear': True},
                                    'auto_crop_edges': False})['result']['artifact']
    apply_action(session, {'operation': 'accept'})
    variants = apply_action(session, {'operation': 'create_variants', 'step': 'stretch', 'preset': 'light',
                                     'stretch_method': 'arcsinh', 'background_neutralize_lock': False,
                                     'variants': [{'id': 'a'}, {'id': 'b', 'params': {'stretch_factor': 12}}]})['result']['candidates']
    selected = variants[1]['artifact']
    quality = variants[1]['review']['quality_state']
    evidence = tmp_path / 'fixture_review.json'
    evidence.write_text(json.dumps({'fixture': 'Three positive Gaussian stars without rings'}))
    apply_review(session, {'verdict': 'accept', 'artifact': selected,
                          'artifact_sha256': quality['artifact_sha256'],
                          'resolutions': [{'issue_id': issue['id'], 'outcome': 'false_positive',
                                           'reason': 'Known synthetic fixture has insufficient sampling, no ring',
                                           'evidence': [str(evidence)]} for issue in quality['issues']]})
    processed = apply_action(session, {'operation': 'run_step', 'step': 'star_process', 'preset': 'light',
                                      'background_neutralize_lock': False})['result']
    assert processed['review']['source']['after'] == processed['pipeline_result']['star_layers']['processed_stars']
    assert not any(g['code'] in ('BACKGROUND_CRUSHED', 'BACKGROUND_LOW', 'CORNER_NONUNIFORM')
                   for g in processed['review']['quality_gates'])
    apply_action(session, {'operation': 'rollback', 'artifact': removed})
    state, _ = load_state(session)
    assert state['artifact_state'][state['current_artifact']]['star_layers']['domain'] == 'linear'


def test_low_noise_skips_starlet_and_memory_skips_are_incomplete(tmp_path):
    source = tmp_path / 'source.tif'
    write_image(np.full((512, 512, 3), 0.17, np.float32), source)
    with patch('pipeline.denoise_luminance_chroma', side_effect=AssertionError('Low-noise fallback')):
        result = run(tmp_path / 'quiet', source, 'pre_denoise', input_is_linear=True)
    assert result['pre_denoise']['reason'] == 'low_noise'
    assert result['pre_denoise']['fallback_backend'] is None
    result = run(tmp_path / 'memory', source, 'star_remove', input_is_linear=True, low_memory=True)
    assert result['status'] == 'partial_success'
    assert result['memory']['skipped_steps'] == ['star_remove']


def test_applied_noise_floor_retry_and_drift_rejection():
    rng = np.random.default_rng(42)
    image = (0.1 + rng.normal(0, 0.002, (96, 96, 3))).astype(np.float32)
    candidate = gaussian_filter(image, (1.5, 1.5, 0))
    accepted, report = validate_applied_denoise(image, candidate)
    assert report['blend_multiplier'] == 0.5
    assert accepted is not None and report['noise_ratio'] >= 0.5
    accepted, report = validate_applied_denoise(image, image * 1.2)
    assert accepted is None and not report['passed']


def test_starlet_rejection_keeps_stage_input(tmp_path):
    image, _ = scene()
    source = tmp_path / 'source.tif'
    write_image(image, source)
    with patch('pipeline.denoise_luminance_chroma', side_effect=lambda x, **kwargs: x * 1.2):
        result = run(tmp_path / 'rejected', source, 'pre_denoise', deepsnr='never', input_is_linear=True)
    assert result['status'] == 'review_required'
    assert result['pre_denoise']['fallback_applied'] is False
    assert result['pre_denoise']['applied_validation']['passed'] is False
    np.testing.assert_array_equal(read_image(result['outputs']['image'])[0], image)


def test_subject_protection_external_and_starnet(tmp_path):
    image, base = scene()
    mask = np.zeros(image.shape[:2], np.float32)
    mask[16:29, 14:27] = 1
    external = separate_stars(image, external_starless=base, exclude_mask=mask)[0:2]
    with patch('star_tools.find_starnet_executable', return_value='/fake/starnet'), \
         patch('star_tools.run_starnet_cli', return_value=(True, base, {})), \
         patch('star_tools.estimate_star_removal_quality', return_value={'repair_quality_score': 1, 'nebula_damage_ratio': 0}):
        neural = separate_stars(image, method='starnet', exclude_mask=mask)[0:2]
    builtin = separate_stars(image, method='inpaint', exclude_mask=mask)[0:2]
    for starless, stars in (external, neural, builtin):
        np.testing.assert_array_equal(starless[mask == 1], image[mask == 1])
        assert np.max(stars[mask == 1]) == 0
    source = tmp_path / 'source.tif'
    write_image(image, source)
    # 星系不再硬拒绝去星：无 subject_mask 时走「核球保护 + 分离」路径，
    # 这样才能把被亮星占据的高光余量让给星系主体。
    galaxy = run(tmp_path / 'galaxy', source, 'star_remove', target_type='galaxy')
    assert 'star_remove' in galaxy['execution']['executed_steps']
    mask_path = tmp_path / 'bad_mask.tif'
    tifffile.imwrite(mask_path, np.full(mask.shape, 2.0, np.float32))
    with pytest.raises(ValueError, match='Subject mask'):
        run(tmp_path / 'invalid', source, 'star_remove', subject_mask=str(mask_path), input_is_linear=True)


def test_exposure_semantics_only_explicit_subexposure_drives_priors():
    metadata = extract_capture_metadata({'EXPTIME': 12000, 'RESTACK': 0, 'ORIGIN': 'DWARFLAB'})
    assert metadata['exposure_kind'] == 'integration'
    assert 'long_subexposure_protect_star_cores' not in build_physical_priors(metadata)['recommendations']
    metadata['subexposure_seconds'] = 30
    assert 'long_subexposure_protect_star_cores' not in build_physical_priors(metadata)['recommendations']
    metadata['subexposure_seconds'] = 300
    assert 'long_subexposure_protect_star_cores' in build_physical_priors(metadata)['recommendations']


@pytest.mark.parametrize('recovered', [True, False])
def test_stretch_fallback_rechecks_clipping_and_records_effective_params(tmp_path, recovered):
    image, _ = scene()
    source = tmp_path / 'source.tif'
    write_image(image, source)
    bad = image.copy()
    bad[15:80, 15:80, 0] = 1
    with patch('pipeline.apply_luminance_stretch', side_effect=[bad, bad, image * 2 if recovered else bad]) as stretch:
        result = run(tmp_path / 'fallback', source, 'stretch', input_is_linear=True, stretch_method='masked_ghs')
    assert stretch.call_count == 3
    assert stretch.call_args.kwargs['gamma'] == .4
    if recovered:
        assert result['stretch_recovery'] == 'conservative_masked_ghs'
        assert result['effective_config']['params']['stretch_gamma'] == .4
        assert result['effective_config']['params']['highlight_pctl'] == 100
        assert not any(g.get('escalate', True) for g in result['quality_gates'] if g['code'] == 'CHANNEL_CLIPPING')
    else:
        assert result['stretch_recovery'] == 'failed'
        assert result['status'] == 'review_required'


@pytest.mark.parametrize('layers', [[], {}, {'domain': 'linear'}])
def test_invalid_layer_state_is_rejected_at_entry(tmp_path, layers):
    with pytest.raises(ValueError, match='[Ss]tar layer'):
        run(tmp_path / 'invalid', 'unused-source.tif', 'stretch', star_layers=layers)

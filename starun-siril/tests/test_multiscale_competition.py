"""Native B-spline reconstruction, sibling competition, bound review and history."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from .test_starnet_linear_bridge import native
from .test_recomposition_sources_v11 import source_case, accept, generated_run, recompose_assets
from .test_hdr_stretch import run_native
from .test_native_processing import write_fits
from ._policy_pins import assert_pinned_history
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_local_contrast as local
import deep_sky_siril_session as sessions
import deep_sky_star_reduction as reduction

ROOT = Path(__file__).parents[1]


def request_file(session, parent, prefix='080'):
    regions = session/f'reports/{prefix}-regions.json'
    rows = [{'id': name, 'role': role, 'rect': rect} for name, role, rect in [
        ('sky-a', 'background', [80, 80, 104, 104]),
        ('sky-b', 'background', [384, 80, 408, 104]),
        ('sky-c', 'background', [80, 384, 104, 408]),
        ('faint', 'faint', [240, 240, 272, 272]),
    ]]
    core.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(parent), 'width': 512, 'height': 512}, 'regions': rows})
    request = session/f'reports/{prefix}-request.json'
    core.atomic_write_json(request, {'schema': local.REQUEST_SCHEMA,
        'source': parent.relative_to(session).as_posix(), 'regions': regions.relative_to(session).as_posix(),
        'mask': dict(low_start=.03, low_end=.10, high_start=.65, high_end=.90, sigma=2),
        'manual_lookup': {'status': 'not_needed', 'reason': 'Complete native six-plane protocol recipe', 'evidence': []}})
    return request


def run_candidate(session, assets, **kwargs):
    return core.run_script(session, protocol='structure.local-contrast', script_value=assets['script'],
        source_value=assets['source'], expected_values=assets['expected_outputs'],
        primary_output_value=assets['primary_output'], timeout=30, **kwargs)


def test_exact_v20_policy_commands_and_documents():
    baseline = json.loads((ROOT/'tests/fixtures/multiscale-policy-v20.json').read_text())
    assert_pinned_history(baseline, session_constant=sessions._DUALBAND_EXECUTION_POLICY,
        policy_key='policy', command_mode='hash', command_policy_key='command_policy_bytes',
        domains={'local_contrast': local.MULTISCALE_POLICY})


def test_native_six_planes_reconstruct_signed_values_and_seventh_logs_failure(tmp_path):
    y, x = np.mgrid[:512, :512]
    values = (.2+.04*np.sin(x/4)+.1*np.exp(-((x-256)**2+(y-256)**2)/50)).astype('float32')
    values[40, 40] = -.005; values[70, 70] = 1.02
    source = tmp_path/'source.fit'; write_fits(source, values)
    rows = [f'load "{source}"', 'wavelet 6 2', 'wrecons 1 1 1 1 1 1', f'save "{tmp_path}/identity"']
    for i in range(6):
        rows += ['wrecons '+' '.join(str(int(i == j)) for j in range(6)), f'save "{tmp_path}/plane-{i}"']
    rows += ['wrecons 1.02 1.04 1.12 1.08 1 1', f'save "{tmp_path}/weighted"', 'close']
    run_native(tmp_path, 'requires 1.4.4 1.5.0\nset32bits\n'+'\n'.join(rows)+'\n')
    read = lambda name: core.artifacts.read_scientific_pixels(tmp_path/(name+'.fit'))[0][0].astype(float)
    layers = [read('plane-'+str(i)) for i in range(6)]
    np.testing.assert_allclose(read('identity'), values, rtol=0, atol=1e-6)
    np.testing.assert_allclose(sum(layers), values, rtol=0, atol=1e-6)
    np.testing.assert_allclose(sum(c*p for c, p in zip(local.WEIGHTS['wavelet-100'], layers)), read('weighted'), rtol=0, atol=1e-6)
    assert read('weighted').min() < 0 and read('weighted').max() > 1
    assert abs(np.median(layers[5])-.2) < .005
    import subprocess
    text = f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nwavelet 7 2\nwrecons 1 1 1 1 1 1 1\nsave "{tmp_path}/invalid"\nclose\n'
    script = tmp_path/'seven.ssf'; script.write_text(text)
    (tmp_path/'s.ini').write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run(['/Applications/Siril.app/Contents/MacOS/siril-cli', '--offline', '--directory='+str(tmp_path), '--initfile='+str(tmp_path/'s.ini'), '--script='+str(script)], capture_output=True, text=True)
    assert result.returncode == 0 and 'out of bounds' in result.stdout
    diagnostics = core.artifacts.diagnose_siril_log(result.stdout+result.stderr, exit_code=0, timed_out=False, execution_valid=True)
    assert diagnostics['status'] == 'failed'


@pytest.mark.parametrize('key', ['wavelet-150', 'clahe'])
def test_native_competition_review_replay_and_rgb_stars(source_case, key):
    session, parent, *_ = source_case
    request = request_file(session, parent)
    result = local.solve(session, request, '080-compare', timeout=60)
    assert result['status'] == 'review_required'
    report = local.load_parameters(session, result['parameters'])
    assert set(report['candidates']) == {*local.WEIGHTS, 'clahe'}
    assert not list((session/'runs').glob('080-*'))
    assets = local.generate(session, result['parameters'], key)
    assert run_candidate(session, assets, validate_only=True)['mode'] == 'validate_only'
    run = run_candidate(session, assets)
    assert run['status'] == 'success', run
    native_checks = run['processing_checks']['local_contrast']['native']
    assert native_checks['positive_common_gain'] and max(native_checks['max_errors'].values()) < 2e-6
    assert native_checks['coarse_scale_amplitudes']
    draft = core.check_review(session, run['id'], None)['review']
    assert len(draft['inspected_materials']) <= 32
    assert {r['path'] for r in draft['inspected_materials']} >= local.parameter_review_paths(session, Path(result['parameters']).relative_to(session).as_posix())
    review = accept(session, run['id'])
    assert run_candidate(session, assets)['replayed']
    doc = core.load_json(review); removed = report['candidates']['clahe']['local_preview']
    doc['inspected_materials'] = [r for r in doc['inspected_materials'] if r['path'] != removed]
    core.atomic_write_json(review, doc)
    with pytest.raises(contract.ContractError, match='missing bound materials'):
        core.check_review(session, run['id'], review)
    accept(session, run['id'])
    with pytest.raises(contract.ContractError, match='one local enhancement'):
        local.solve(session, request_file(session, Path(assets['primary_output']), '081'), '081-stacked', timeout=60)
    generated = recompose_assets(session_path=session, source=Path(assets['primary_output']), strength=.45)
    recomposed = generated_run(session, generated)
    assert recomposed['status'] == 'success'
    assert max(recomposed['processing_checks']['closure']['per_channel_max_abs']) < 1e-5
    accept(session, recomposed['id'])


@pytest.mark.parametrize('change', ['source', 'regions', 'runtime', 'report', 'trial', 'ssf', 'coarse', 'coefficient'])
def test_drift_and_invalid_variants_are_rejected(source_case, change):
    session, parent, *_ = source_case
    request = request_file(session, parent)
    if change == 'coarse':
        regions = session/'reports/080-regions.json'; doc = core.load_json(regions); doc['regions'][0]['rect'] = [48, 48, 72, 72]; core.atomic_write_json(regions, doc)
        with pytest.raises(contract.ContractError, match='64-pixel'):
            local.solve(session, request, '080-invalid')
        return
    result = local.solve(session, request, '080-compare', timeout=60)
    assets = local.generate(session, result['parameters'], 'wavelet-100')
    if change == 'source': parent.write_bytes(parent.read_bytes()+b' ')
    elif change == 'regions':
        region = session/'reports/080-regions.json'; region.write_bytes(region.read_bytes()+b' ')
    elif change == 'runtime':
        probe = core.load_json(session/'reports/tool-probe.json'); Path(probe['tools']['siril_cli']['path']).write_bytes(b'changed')
    elif change == 'report':
        doc = core.load_json(Path(result['parameters'])); doc['candidates']['wavelet-100']['parameters']['coefficients'][4] = 1.1; core.atomic_write_json(Path(result['parameters']), doc)
    elif change == 'trial':
        doc = core.load_json(Path(result['parameters'])); file = session/doc['candidates']['clahe']['local_preview']; file.write_bytes(b'changed')
    else:
        script = Path(assets['script']); text = script.read_text().replace('wavelet 6 2', 'wavelet 7 2') if change == 'ssf' else script.read_text().replace('wrecons 1.02 1.04 1.12 1.08 1 1', 'wrecons 1.02 1.04 1.12 1.08 1.1 1')
        script.write_text(text)
        from .test_deep_sky_siril import _write_ssf_provenance
        _write_ssf_provenance(session, script, 'structure.local-contrast')
    with pytest.raises((contract.ContractError, OSError, ValueError)):
        run_candidate(session, assets, validate_only=True)


def test_native_peak_background_neutral_and_color_ratio_protection(tmp_path):
    for name in ('artifacts', 'previews'): (tmp_path/name).mkdir()
    y, x = np.mgrid[:512, :512]
    lum = .2+.015*np.sin(x/3)*np.exp(-((x-256)**2+(y-256)**2)/10000)
    values = np.stack([lum*.8, lum, lum*.6]).astype('float32')
    values[:, :100, :100] = .01
    values[0, :100, 400:] = .95; values[1:, :100, 400:] = .1
    values[:, 400:, :100] = np.stack([lum[400:, :100]]*3)
    source = tmp_path/'source.fit'; write_fits(source, values)
    primary = tmp_path/'artifacts/080-wavelet.fit'
    p = dict(method='wavelet', coefficients=local.WEIGHTS['wavelet-150'], low_start=.03, low_end=.10, high_start=.65, high_end=.90, sigma=2)
    run_native(tmp_path, '\n'.join(['requires 1.4.4 1.5.0', 'set32bits', *local.recipe(tmp_path, source, primary, p)])+'\n')
    read = lambda path: core.artifacts.read_scientific_pixels(path)[0]
    after = read(primary); mask = read(primary.with_name('080-wavelet-mask.fit'))[0]
    np.testing.assert_allclose(after[:, 20:60, 20:60], values[:, 20:60, 20:60], rtol=0, atol=2e-6)
    assert mask[20:60, 430:470].max() < 1e-6
    np.testing.assert_allclose(after[:, 20:60, 430:470], values[:, 20:60, 430:470], rtol=0, atol=2e-6)
    np.testing.assert_allclose(after[0, 200:300, 200:300]/after[1, 200:300, 200:300], .8, rtol=0, atol=2e-6)
    np.testing.assert_allclose(after[2, 200:300, 200:300]/after[1, 200:300, 200:300], .6, rtol=0, atol=2e-6)
    np.testing.assert_allclose(after[0, 430:470, 20:60], after[1, 430:470, 20:60], rtol=0, atol=2e-6)


def test_fine_off_requires_three_actual_rejections_and_allows_one_revision(source_case):
    session, parent, *_ = source_case
    request = request_file(session, parent)
    report = local.solve(session, request, '080-compare', timeout=60)['parameters']
    with pytest.raises(contract.ContractError):
        local.solve(session, request, '081-no-review', fallback_from=report, reason='Synthetic high-frequency rejection', timeout=60)
    for key in local.WEIGHTS:
        assets = local.generate(session, report, key); run = run_candidate(session, assets)
        assert run['status'] == 'success'
        draft = core.check_review(session, run['id'], None)['review']
        draft.update(verdict='reject', gates={k: 'uncertain' for k in draft['gates']}, notes='Synthetic negative review exercises the fine-off boundary; background high-frequency noise rejected.')
        review = session/'reviews'/f'{run["id"]}.json'; core.atomic_write_json(review, draft)
        assert core.check_review(session, run['id'], review)['status'] == 'review_valid'
    fine = local.solve(session, request, '082-fine-off', fallback_from=report, reason='All three synthetic high-frequency background reviews rejected', timeout=60)
    loaded = local.load_parameters(session, fine['parameters'])
    assert set(loaded['candidates']) == {'fine-off'}
    assert loaded['candidates']['fine-off']['parameters']['coefficients'] == local.FINE_OFF
    assets = local.generate(session, fine['parameters'], 'fine-off'); run = run_candidate(session, assets)
    assert run['status'] == 'success'
    assert len(core.check_review(session, run['id'], None)['review']['inspected_materials']) <= 32
    accept(session, run['id'])
    with pytest.raises(contract.ContractError, match='single fine-off'):
        local.solve(session, request, '083-second-revision', fallback_from=report, reason='Repeated', timeout=60)


@pytest.mark.parametrize('source_case', ['hdr', 'flat-noise'], indirect=True)
def test_hdr_only_allows_wavelets_and_low_texture_can_be_trialed(source_case):
    session, parent, *_ = source_case
    request = request_file(session, parent)
    if core.load_json(session/'runs/070-stretch.json').get('hdr_transfer'):
        result = local.solve(session, request, '080-hdr-wavelets', timeout=60)
        assert result['status'] == 'review_required' and set(result['candidates']) == set(local.WEIGHTS)
        report = local.load_parameters(session,result['parameters'])
        assert report['method_eligibility']['clahe']['allowed'] is False
    else:
        result = local.solve(session, request, '080-low-texture', timeout=60)
        assert result['status'] == 'review_required'
        report = local.load_parameters(session, result['parameters'])
        assert report['eligibility']['texture_snr'] < 1.7
        assert report['eligibility']['warnings'] and len(report['candidates']) == 4

"""Native neutral scientific containers, frozen arithmetic and display bindings."""
import copy
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from .test_native_processing import neutral_case, write_fits
from .test_xisf_metrics import xisf
import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_processing as processing
import deep_sky_siril_metrics as metrics
import deep_sky_siril_session as sessions
from deep_sky_siril_neutral import NATIVE_NEUTRAL_POLICY, measure_neutral_validation
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, sha256_file
from deep_sky_siril_tooling import discover_siril, _subprocess_environment


def scientific_case(tmp_path, container='xisf', sample='Float32', *, bounds='0:1', extra=False):
    original, candidate, payload, probe, text, regions, old = neutral_case(tmp_path, channel='dualband-osc')
    values, _ = artifacts.read_scientific_pixels(original)
    header = artifacts.read_scientific_header(original)
    integer = sample == 'UInt16'
    encoded = np.rint(np.clip(values, 0, 1)*65535).astype(np.uint16) if integer else values
    source = tmp_path / ('master.' + container)
    if container == 'xisf':
        keywords = [(key, repr(value) if isinstance(value, str) else value)
                    for key, value in header.items() if key.startswith(('CTYPE', 'CUNIT', 'CR', 'CD'))]
        xisf(source, encoded[:, ::-1], sample=sample, bounds=bounds, extra=extra, keywords=keywords)
    elif integer:
        h = fits.Header({key: value for key, value in header.items() if key.startswith(('CTYPE', 'CUNIT', 'CR', 'CD'))})
        fits.writeto(source, encoded, h)
    else:
        source.write_bytes(original.read_bytes())
    region_contract = json.loads(regions.read_text())
    region_contract['reference']['sha256'] = sha256_file(source)
    regions.write_text(json.dumps(region_contract))
    coefficients = processing.neutral_coefficients(source, regions, background_partition=old['background_partition'],
                                                   policy=NATIVE_NEUTRAL_POLICY)
    text = text.replace(str(original), str(source))
    evidence = {'method': 'neutral', 'source_sha256': sha256_file(source), 'background_regions': 'reports/regions.json',
                'background_partition': coefficients['background_partition']}
    text = '\n'.join('# color-calibration: ' + json.dumps(evidence) if line.startswith('# color-calibration:') else line
                     for line in text.splitlines()) + '\n'
    for i in range(3):
        before = f"($artifacts/raw-{i}$ - {old['background'][i]:.17g}) * 1 + {old['target']:.17g}"
        after = f"($artifacts/raw-{i}$ - {coefficients['background'][i]:.17g}) * 1 + {coefficients['target']:.17g}"
        text = text.replace(before, after)
    payload['input']['path'] = str(source)
    return source, candidate, payload, probe, text, regions, coefficients


def prepare(case):
    source, candidate, payload, probe, text, _, _ = case
    return processing.prepare_processing(candidate.parents[1], payload, 'color.calibrate', source, candidate,
                                         text, probe, None, None, None, None)


@pytest.mark.parametrize('container,sample', [('fits', 'Float32'), ('fits', 'UInt16'),
                                              ('xisf', 'UInt16'), ('xisf', 'Float32'), ('xisf', 'Float64')])
def test_real_siril_scientific_neutral_preserves_unit_gain_signals_and_wcs(tmp_path, container, sample):
    case = scientific_case(tmp_path, container, sample, extra=container == 'xisf')
    source, candidate, payload, probe, text, regions, coefficients = case
    metadata = prepare(case)
    binary = discover_siril()
    if binary is None:
        pytest.skip('Native Siril unavailable')
    config = tmp_path / 'siril.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
    script = tmp_path / 'native-neutral.ssf'; script.write_text(text)
    before = source.read_bytes()
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}', '--offline', f'--script={script}'],
                            capture_output=True, text=True, timeout=30, env=_subprocess_environment('siril_runtime'))
    assert result.returncode == 0, result.stdout + result.stderr
    checks = processing.validate_processing_outputs(tmp_path, source, candidate, metadata, execution_policy=EXECUTION_POLICY)
    report = checks['color_calibration']['neutral_validation']
    assert report['policy'] == NATIVE_NEUTRAL_POLICY and report['recommended_verdict'] == 'uncertain'
    selection = report['bindings']['scientific_sources']['parent']
    assert selection == metadata['scientific_source'] and selection['image_index'] == 0
    assert selection['row_order'] == 'fits_bottom_up'
    assert coefficients['gains'] == [1, 1, 1] and coefficients['white_reference'] is None
    assert artifacts.read_fits_header(candidate)['BITPIX'] == -32
    assert processing.complete_wcs(candidate)
    parent, _ = artifacts.read_scientific_pixels(source)
    output, _ = artifacts.read_scientific_pixels(candidate)
    np.testing.assert_allclose(output, parent - np.asarray(coefficients['background'])[:, None, None] + coefficients['target'], atol=1e-6, rtol=0)
    for roi in report['benefit']['regions']:
        assert max(roi['candidate_rgb']) - min(roi['candidate_rgb']) < 1e-7
    assert max(abs(x) for x in report['safety']['regions'][0]['gain_adjusted_signal_delta']) < 1e-7
    assert source.read_bytes() == before
    assert metrics.measure_regions(candidate, source, regions)['status'] == ('unsupported' if container == 'xisf' else 'measured')
    assert (tmp_path / 'previews/neutral-parent.jpg').is_file()
    assert (tmp_path / 'previews/neutral-candidate.jpg').is_file()
    changed = copy.deepcopy(metadata); changed['scientific_source']['image_index'] = 1
    with pytest.raises(ContractError, match='selection'):
        processing.validate_processing_outputs(tmp_path, source, candidate, changed, execution_policy=EXECUTION_POLICY)
    write_fits(candidate, output[:, ::-1])
    with pytest.raises(ContractError):
        processing.validate_processing_outputs(tmp_path, source, candidate, metadata, execution_policy=EXECUTION_POLICY)


@pytest.mark.parametrize('change', ['missing', 'double', 'gain', 'transform', 'display', 'wrong-preview'])
def test_native_neutral_rejects_extra_operations_and_unmatched_displays(tmp_path, change):
    case = list(scientific_case(tmp_path))
    text = case[4]
    if change == 'missing': text = text.replace('fmul 1\n', '')
    if change == 'double': text = text.replace('fmul 1\n', 'fmul 1\nfmul 1\n')
    if change == 'gain': text = text.replace(' * 1 +', ' * 1.1 +', 1)
    if change == 'transform': text = text.replace('fmul 1\n', 'fmul 1\nmtf 0 0.5 1\n')
    if change == 'display': text = text.replace('mtf 0 0.15 1', 'mtf 0 0.2 1', 1)
    if change == 'wrong-preview': text = text.replace('previews/neutral-parent', 'previews/neutral-candidate')
    case[4] = text
    with pytest.raises(ContractError): prepare(case)
    assert not case[1].exists()


def test_nonunit_bounds_and_corrupt_xisf_cannot_execute(tmp_path):
    case = scientific_case(tmp_path, bounds='-1:1')
    with pytest.raises(ContractError, match='bounds'): prepare(case)
    case[0].write_bytes(case[0].read_bytes()[:-8])
    with pytest.raises((ContractError, ValueError)): prepare(case)
    assert not case[1].exists()


def test_legacy_neutral_retains_original_fields_and_rejects_native_promotion(tmp_path):
    case = list(scientific_case(tmp_path, 'fits'))
    case[2]['execution_policy'] = dict(sessions._PROTECTED_BACKGROUND_EXECUTION_POLICY)
    with pytest.raises(ContractError, match='fmul'): prepare(case)
    case[4] = case[4].replace('fmul 1\n', '')
    tail = case[4].index('rgbcomp')
    tail = case[4].index('\n', tail)
    case[4] = case[4][:tail+1] + f'load "{case[1]}"\nstat main\nclose\n'
    metadata = prepare(case)
    assert 'policy' not in metadata['color_calibration'] and 'scientific_source' not in metadata
    values, _ = artifacts.read_scientific_pixels(case[0]); coefficients = case[6]
    from .test_native_processing import COLOR_WCS
    write_fits(case[1], values - np.asarray(coefficients['background'])[:,None,None] + coefficients['target'], COLOR_WCS)
    report = processing.validate_processing_outputs(tmp_path, case[0], case[1], metadata,
                   execution_policy=case[2]['execution_policy'])['color_calibration']['neutral_validation']
    assert report['policy'] == 'heldout_benefit_safety_v1'
    assert set(report['bindings']) == {'parent', 'candidate', 'regions', 'coefficients_sha256'}


@pytest.mark.parametrize('issue', ['protected', 'few-fit', 'mixed', 'small', 'nonfinite'])
def test_native_neutral_rejects_unusable_or_nonindependent_sky(tmp_path, issue):
    case = list(scientific_case(tmp_path))
    document = json.loads(case[5].read_text())
    evidence = json.loads(next(line.split(':', 1)[1] for line in case[4].splitlines() if line.startswith('# color-calibration:')))
    if issue == 'protected': document['regions'].append({'id': 'emission', 'role': 'faint', 'rect': [7, 7, 10, 10]})
    if issue == 'small': document['regions'][0]['rect'] = [0, 0, 7, 8]
    if issue == 'few-fit': evidence['background_partition']['fit'] = ['bg-0', 'bg-1']
    if issue == 'mixed': evidence['background_partition']['validation'][0] = 'bg-0'
    if issue == 'nonfinite':
        values, _ = artifacts.read_scientific_pixels(case[0])
        values = values.copy(); values[0, 0, 0] = np.nan
        xisf(case[0], values[:, ::-1])
        document['reference']['sha256'] = evidence['source_sha256'] = sha256_file(case[0])
    case[5].write_text(json.dumps(document))
    case[4] = '\n'.join('# color-calibration: '+json.dumps(evidence) if line.startswith('# color-calibration:') else line
                        for line in case[4].splitlines()) + '\n'
    with pytest.raises(ContractError): prepare(case)
    assert not case[1].exists()


def test_outgoing_neutral_command_policy_is_exact_and_known():
    import query_siril_manual as query
    import hashlib
    raw = query._NATIVE_NEUTRAL_PREDECESSOR_POLICY_BYTES
    key = (hashlib.sha256(raw).hexdigest(), len(raw))
    assert key == ('43fb9a1323a8bd27d4c4e3ab3e761c741b57d942f0988fb29febddecb5ec6613', 4018)
    assert key in sessions._LEGACY_REFERENCE_FINGERPRINTS['references/command-policy.json']
    assert query._HISTORICAL_COMMAND_POLICIES[key] == raw
    assert 'fmul' not in json.loads(raw)['protocol_commands']['color.calibrate']
    assert {**{k:v for k,v in sessions._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}, 'neutral': 'heldout_benefit_safety_v1', 'guidance': 'stage4_xisf_inputs_v10'} == sessions._PROTECTED_BACKGROUND_EXECUTION_POLICY
    assert sessions.known_execution_policy(sessions._PROTECTED_BACKGROUND_EXECUTION_POLICY)
    assert not sessions.execution_policy_is_current({'execution_policy': sessions._PROTECTED_BACKGROUND_EXECUTION_POLICY})


@pytest.mark.parametrize('operation', ['fmul 1', 'fmul 2', 'mtf 0 0.2 1'])
def test_native_neutral_authorization_does_not_relax_photometric_science(tmp_path, operation):
    from .test_native_processing import color_case, pcc_script
    source, candidate, payload, probe = color_case(tmp_path)
    payload['execution_policy'] = dict(EXECUTION_POLICY)
    text = pcc_script(source).replace(f'load "{source}"\n', f'load "{source}"\n{operation}\n')
    with pytest.raises(ContractError):
        processing.prepare_processing(tmp_path, payload, 'color.calibrate', source, candidate, text, probe, None, None, None, None)
    assert not candidate.exists()

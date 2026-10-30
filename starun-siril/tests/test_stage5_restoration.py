"""Scientific Stage 5 inputs, native precision, paired displays and frozen replay."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from astropy.io import fits

from .test_xisf_metrics import xisf
from .test_psf_provenance import sample_contract, SETTINGS
import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_processing as processing
import deep_sky_siril_psf as diagnostics
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, sha256_file
from deep_sky_siril_tooling import discover_siril, _subprocess_environment

WCS = {'CTYPE1': 'RA---TAN', 'CTYPE2': 'DEC--TAN', 'CRPIX1': 64., 'CRPIX2': 64.,
       'CRVAL1': 23., 'CRVAL2': 30., 'CD1_1': -.001, 'CD1_2': 0., 'CD2_1': 0., 'CD2_2': .001}


def restoration_case(tmp_path, *, operation='deconvolve', container='fits', sample='Float32', channels=3, wcs=True):
    for folder in ('artifacts', 'previews', 'reports/055-restoration'):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    yy, xx = np.indices((128,128)); plane = .025 + 1e-5*yy
    for y in range(16,120,20):
        for x in range(16,120,20):
            plane += (.06+.15*(x+y)/256)*(1+((xx-x)**2+(yy-y)**2)/9)**-3
    plane += np.random.default_rng(10).normal(0,.0002,plane.shape)
    values = np.stack([plane*(.95+.04*c) for c in range(channels)]).astype(np.float32)
    encoded = np.rint(values*65535).astype(np.uint16) if sample == 'UInt16' else values
    source = tmp_path / ('source.' + container)
    if container == 'fits':
        fits.writeto(source, encoded[0] if channels == 1 else encoded, fits.Header(WCS if wcs else {}))
    else:
        xisf(source, encoded[:,::-1], sample=sample, extra=True,
             keywords=[(key, repr(value) if isinstance(value,str) else value) for key,value in (WCS if wcs else {}).items()])
    primary = tmp_path / 'artifacts/055-restoration.fit'
    directory = tmp_path / 'reports/055-restoration'
    contract = sample_contract(source, directory, channels=channels, width=128, height=128)
    contract['psf']['symmetric'] = True
    contract_path = directory / 'contract.json'; contract_path.write_text(json.dumps(contract))
    prefix = f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\n'
    if operation == 'deconvolve':
        prefix += 'setfindstar '+' '.join(SETTINGS)+'\n'
        prefix += (f'pyscript "{Path(processing.__file__).parent}/siril_psf_samples.py" --contract "{contract_path}" '
                   f'--contract-sha256 {sha256_file(contract_path)} --expected-source "{source}" --receipt "{directory}/bridge.json"\n'
                   f'rl "-loadpsf={directory}/psf.fit" -iters=6 -tv -alpha=3000.0\n')
    else:
        import deep_sky_siril_denoise as denoise
        region_path = directory/'denoise-regions.json'
        region_path.write_text(json.dumps({'schema': 'starun-siril.regions.v1',
            'reference': {'sha256': sha256_file(source), 'width': 128, 'height': 128},
            'regions': [{'id': 'sky', 'role': 'background', 'rect': [1, 1, 10, 10]},
                        {'id': 'detail', 'role': 'faint', 'rect': [40, 40, 64, 64]}]}))
        proposal = directory/'denoise-request.json'
        from .test_linear_denoise import fallback_request
        proposal.write_text(json.dumps(fallback_request(tmp_path, {'schema': denoise.SCHEMA,
            'source': {'path': '@input', 'sha256': sha256_file(source)}, 'regions': denoise.record(tmp_path, region_path),
            'display_mtf': [0, .15, 1], 'decision': 'trial', 'reason': 'Synthetic restoration contract',
            'parameters': {'modulation': .35, 'cosmetic': True}, 'parameter_reason': 'Explicit fixture parameters',
            'manual_lookup': {'status': 'not_needed', 'reason': 'Documented variant', 'evidence': []}})))
        prefix += '# linear-denoise: '+json.dumps(denoise.record(tmp_path, proposal))+'\n'
        prefix += 'denoise -mod=0.35\n'
    text = prefix + f'save "{primary}" -chksum\nclose\n'
    if operation == 'deconvolve':
        text += (f'load "{primary}"\nsetfindstar '+' '.join(SETTINGS)+'\n'
                 +f'findstar -layer={contract["detection"]["layer"]} -maxstars=500 "-out={directory}/after.tsv"\nclose\n')
    text += (f'load "{source}"\nmtf 0 0.15 1\nsavejpg "{tmp_path}/previews/055-parent" 95\nclose\n'
             f'load "{primary}"\nmtf 0 0.15 1\nsavejpg "{tmp_path}/previews/055-candidate" 95\nclose\n')
    payload = {'input': {'path': str(source)}, 'context': {'input_state':'linear', 'channel_mode':'mono' if channels==1 else 'broadband'},
               'execution_policy':dict(EXECUTION_POLICY)}
    (tmp_path/"session.json").write_text(json.dumps(payload))
    return source, primary, payload, text, contract_path


def prepare(case):
    source, primary, payload, text, _ = case
    operation = 'deconvolve' if '\nrl ' in text else 'denoise'
    return processing.prepare_processing(primary.parents[1], payload, 'restoration.'+operation, source, primary,
                                         text, {}, None, None, None, None, run_id='055-restoration')


@pytest.mark.parametrize('operation', ['denoise','deconvolve'])
@pytest.mark.parametrize('container,sample,channels,wcs', [('fits','Float32',3,True),('fits','UInt16',1,False),
                                                        ('xisf','Float32',1,True),('xisf','UInt16',3,True)])
def test_real_siril_restoration_scientific_inputs(tmp_path, operation, container, sample, channels, wcs):
    case = restoration_case(tmp_path, operation=operation, container=container, sample=sample, channels=channels, wcs=wcs)
    source, primary, payload, text, _ = case
    metadata = prepare(case); before = source.read_bytes()
    binary = discover_siril()
    if binary is None: pytest.skip('Native Siril unavailable')
    config = tmp_path / 's.ini'; config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
    script = tmp_path / 'restoration.ssf'; script.write_text(text)
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}', '--offline', f'--script={script}'],
                            capture_output=True, text=True, timeout=60, env=_subprocess_environment('siril_runtime'))
    assert result.returncode == 0, result.stdout+result.stderr
    checks = processing.validate_processing_outputs(tmp_path, source, primary, metadata, execution_policy=EXECUTION_POLICY, write_diagnostics=True)
    proof = checks['restoration_validation']
    assert proof['status'] == 'passed' and artifacts.read_fits_header(primary)['BITPIX'] == -32
    assert proof['scientific_sources']['parent']['image_index'] == 0
    assert proof['scientific_sources']['parent']['row_order'] == 'fits_bottom_up'
    assert proof['scientific_sources']['parent'] == metadata['restoration_validation']['scientific_source']
    assert proof['wcs'] == ('preserved' if wcs else 'absent_or_incomplete')
    assert source.read_bytes() == before
    parent_pixels, _ = artifacts.read_scientific_pixels(source)
    candidate_pixels, _ = artifacts.read_scientific_pixels(primary)
    for parent, candidate in zip(parent_pixels, candidate_pixels):
        parent_star, candidate_star = parent[88:105, 88:105], candidate[88:105, 88:105]
        parent_peak = np.array(np.unravel_index(np.argmax(parent_star), parent_star.shape))
        candidate_peak = np.array(np.unravel_index(np.argmax(candidate_star), candidate_star.shape))
        assert np.max(np.abs(parent_peak - candidate_peak)) <= 1
    assert (tmp_path/'previews/055-parent.jpg').is_file() and (tmp_path/'previews/055-candidate.jpg').is_file()
    if operation == 'deconvolve':
        report = diagnostics.measure_deconvolution(tmp_path, source, primary, metadata['deconvolution_binding'], result.stdout+result.stderr, write_reports=True)
        assert report['psf_samples']['status'] == 'verified'
        assert report['matched_fwhm']['status'] == 'measured' and report['matched_fwhm']['matched_count'] > 0
        assert report['spatial_reports']['status'] == 'bound'
        assert report['requires_visual_review'] is True
    altered = copy.deepcopy(metadata); altered['restoration_validation']['scientific_source']['image_index'] = 1
    with pytest.raises(ContractError): processing.validate_processing_outputs(tmp_path, source, primary, altered, execution_policy=EXECUTION_POLICY)
    if wcs:
        with fits.open(primary, mode='update') as hdus: hdus[0].header['CRVAL1'] += .1
        with pytest.raises(ContractError,match='WCS'): processing.validate_processing_outputs(tmp_path, source, primary, metadata, execution_policy=EXECUTION_POLICY)


@pytest.mark.parametrize('change', ['missing','double','gain','transform','source','preview','transfer','operator','bounds','nonfinite','broken'])
def test_restoration_invalid_scientific_contract_fails_before_execution(tmp_path, change):
    case = list(restoration_case(tmp_path, operation='denoise', container='xisf'))
    if change == 'missing': case[3] = case[3].replace('fmul 1\n','')
    if change == 'double': case[3] = case[3].replace('fmul 1\n','fmul 1\nfmul 1\n')
    if change == 'gain': case[3] = case[3].replace('fmul 1\n','fmul 2\n')
    if change == 'transform': case[3] = case[3].replace('fmul 1\n','fmul 1\nmtf 0 0.2 1\n')
    if change == 'source': case[3] = case[3].replace('fmul 1\n',f'fmul 1\nload "{case[0]}"\n')
    if change == 'preview': case[3] = case[3].replace('previews/055-parent','previews/055-candidate')
    if change == 'transfer': case[3] = case[3].replace('mtf 0 0.15 1','mtf 0 0.2 1',1)
    if change == 'operator': case[3] = case[3].replace('denoise -mod=0.35','denoise -mod=0.35\ndenoise -mod=0.35')
    if change == 'bounds': case[0].write_bytes(case[0].read_bytes().replace(b'bounds="0:1"', b'bounds="0:2"'))
    if change == 'nonfinite':
        values, _ = artifacts.read_scientific_pixels(case[0]); values=values.copy(); values[0,0,0]=np.nan
        xisf(case[0],values[:,::-1])
    if change == 'broken': case[0].write_bytes(b'XISF0100 invalid')
    with pytest.raises((ContractError,ValueError)): prepare(case)
    assert not case[1].exists()


def test_stage5_output_geometry_finite_pixels_and_float32_are_required(tmp_path):
    case = restoration_case(tmp_path,operation='denoise',wcs=False)
    source,primary,*_ = case; metadata = prepare(case)
    for values in [np.zeros((3,127,128),np.float32),np.full((3,128,128),np.nan,np.float32),np.zeros((3,128,128),np.uint16)]:
        fits.writeto(primary,values,overwrite=True)
        with pytest.raises(ContractError): processing.validate_processing_outputs(tmp_path,source,primary,metadata,execution_policy=EXECUTION_POLICY)


def test_complete_native_neutral_policy_and_command_bytes_remain_historical():
    import query_siril_manual as query
    assert {**{k:v for k,v in sessions._MULTISCALE_EXECUTION_POLICY.items() if k not in {'restoration', 'local_contrast', 'color_curve', 'dualband'}}, 'guidance':'stage4_xisf_inputs_v10'} == sessions._NATIVE_NEUTRAL_EXECUTION_POLICY
    assert sessions.known_execution_policy(sessions._NATIVE_NEUTRAL_EXECUTION_POLICY)
    raw=query._RESTORATION_PREDECESSOR_POLICY_BYTES; key=(hashlib.sha256(raw).hexdigest(),len(raw))
    assert query._HISTORICAL_COMMAND_POLICIES[key] == raw
    assert key in sessions._LEGACY_REFERENCE_FINGERPRINTS['references/command-policy.json']
    assert 'fmul' not in json.loads(raw)['protocol_commands']['restoration.deconvolve']

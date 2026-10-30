"""Native XISF Stage 4 metadata, scientific frames and immutable policy history."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from .test_xisf_metrics import xisf
from .test_native_processing import write_fits, pcc_script, spcc_script
from .test_deep_sky_siril import _fake_probe, _failed_selection, _input_inspect_run, _protection_regions, _write_jpeg, _write_ssf_provenance, core, tooling
from .test_input_confirmation import _inspected, _confirm, _replay
import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, sha256_file

SIRIL = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
WCS = {'CTYPE1': "'RA---TAN'", 'CTYPE2': "'DEC--TAN'", 'CRPIX1': 6, 'CRPIX2': 4,
       'CRVAL1': 23.828939, 'CRVAL2': 30.7817, 'CUNIT1': "'deg'", 'CUNIT2': "'deg'",
       'CD1_1': -.001, 'CD1_2': 0, 'CD2_1': 0, 'CD2_2': .001}
PRIORS = {'RA': 23.828939, 'DEC': 30.7817, 'FOCALLEN': 150, 'XPIXSZ': 2.9,
          'FILTER': "'IRCUT'", 'INSTRUME': "'Seestar S30'", 'EXPTIME': 60}


def _source(tmp_path, *, channels=3, wcs=True, matrix='CD', keywords=None, **kwargs):
    y, x = np.indices((8, 12))
    values = np.stack([.05 + .006*x + .011*y + .015*c for c in range(channels)]).astype(np.float32)
    header = {**PRIORS, **(WCS if wcs else {})}
    if matrix == 'PC' and wcs:
        for key in ('CD1_1', 'CD1_2', 'CD2_1', 'CD2_2'):
            del header[key]
        header.update(CDELT1=-.001, CDELT2=.001, PC1_1=1, PC1_2=0, PC2_1=0, PC2_2=1)
    path = tmp_path / 'source.xisf'
    xisf(path, values, keywords=keywords if keywords is not None else header.items(), **kwargs)
    return path, values


def _with_protection(session, source, text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith('# color-calibration:'):
            evidence = json.loads(line.split(':', 1)[1])
            evidence['protection_regions'] = _protection_regions(session, source)
            lines[index] = '# color-calibration: ' + json.dumps(evidence)
    return '\n'.join(lines)


def _prepare(tmp_path, source, text, protocol='color.calibrate', *, channel='broadband', offline=False):
    primary = tmp_path / 'result.fit'
    if protocol == 'color.calibrate':
        text = _with_protection(tmp_path, source, text)
    payload = {'input': {'path': str(source)}, 'context': {'input_state': 'linear', 'channel_mode': channel,
               'offline': offline}, 'execution_policy': dict(EXECUTION_POLICY)}
    names = {'monosensor': ['Test Sensor'], 'redfilter': ['Test R'], 'greenfilter': ['Test G'],
             'bluefilter': ['Test B'], 'whiteref': ['Average Spiral Galaxy']}
    probe = {'tools': {'spcc_profiles': {'compatible': True, 'names': names}}}
    return primary, processing.prepare_processing(tmp_path, payload, protocol, source, primary,
        text, probe, None, None, None, None)


@pytest.fixture(autouse=True)
def isolated_receipts(monkeypatch):
    monkeypatch.setattr(sessions, '_verified_success_receipts', lambda _: [])


@pytest.mark.parametrize('matrix', ['CD', 'PC'])
def test_header_reads_selected_geometry_and_finite_wcs(tmp_path, matrix):
    source, _ = _source(tmp_path, matrix=matrix, extra=True)
    before = source.read_bytes()
    header = artifacts.read_scientific_header(source)
    assert header['NAXIS1'] == 12 and header['NAXIS2'] == 8 and header['NAXIS3'] == 3
    assert header['FILTER'] == 'IRCUT' and header['FOCALLEN'] == 150
    assert processing.complete_wcs(source)
    assert source.read_bytes() == before


def test_header_parses_quoted_strings_duplicates_and_actual_dimensions(tmp_path):
    keywords = [*PRIORS.items(), ('FOCALLEN', '150.0'), ('HISTORY', 'first'), ('HISTORY', 'second'),
                ('OBSERVER', "'O''Brien'"), ('NAXIS1', 1080), ('NAXIS2', 1920), ('XPIXSZ', '2.9D+00')]
    source, _ = _source(tmp_path, keywords=keywords)
    header = artifacts.read_scientific_header(source)
    assert header['OBSERVER'] == "O'Brien"
    assert header['XPIXSZ'] == 2.9
    assert (header['NAXIS1'], header['NAXIS2']) == (12, 8)


@pytest.mark.parametrize('key,value', [('RA', 'NaN'), ('DEC', 'Inf'), ('CRPIX1', 'NaN'),
                                     ('CD1_1', '1e999'), ('FOCALLEN', 'T'), ('FILTER', "'Ha'"), ('RA', 99)])
def test_invalid_or_conflicting_metadata_fails_closed(tmp_path, key, value):
    source, _ = _source(tmp_path, keywords=[*PRIORS.items(), *WCS.items(), (key, value)])
    with pytest.raises(ContractError):
        artifacts.read_scientific_header(source)


@pytest.mark.parametrize('kind', ['malformed', 'entity', 'external'])
def test_header_rejects_unsafe_xml(tmp_path, kind):
    import struct
    xml = {'malformed': b'<xisf>', 'entity': b'<!DOCTYPE xisf [<!ENTITY bad "value">]><xisf version="1.0"/>',
           'external': b'<xisf version="1.0"><Image location="file:/tmp/input.fit"/></xisf>'}[kind]
    path = tmp_path / 'bad.xisf'
    path.write_bytes(b'XISF0100' + struct.pack('<I', len(xml)) + bytes(4) + xml)
    with pytest.raises(ContractError):
        artifacts.read_scientific_header(path)


def test_header_rejects_drift_during_parse(tmp_path, monkeypatch):
    source, _ = _source(tmp_path)
    original = artifacts._read_xisf_xml
    def drifting(path):
        result = original(path)
        path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(artifacts, '_read_xisf_xml', drifting)
    with pytest.raises(ContractError, match='changed'):
        artifacts.read_scientific_header(source)


@pytest.mark.parametrize('method', ['pcc', 'spcc'])
def test_photometry_accepts_xisf_and_validates_fits_output(tmp_path, method):
    source, _ = _source(tmp_path)
    text = pcc_script(source) if method == 'pcc' else spcc_script(source)
    primary, metadata = _prepare(tmp_path, source, text)
    values, identity = artifacts.read_scientific_pixels(source)
    write_fits(primary, (values * np.array([1.03, .99, 1.04])[:, None, None]).astype(np.float32),
               extra=list(artifacts.read_scientific_header(source).items()))
    result = processing.validate_processing_outputs(tmp_path, source, primary, metadata)
    assert result['scientific_source'] == identity
    assert result['scientific_source']['image_index'] == 0
    assert result['scientific_source']['row_order'] == 'fits_bottom_up'
    assert result['color_calibration']['method'] == method
    assert result['color_calibration']['geometry_unchanged']


@pytest.mark.parametrize('case', ['mono', 'no_wcs', 'filter', 'unknown_channel', 'neutral', 'nonfinite', 'wrong_axes'])
def test_calibration_retains_scientific_gates(tmp_path, case):
    source, _ = _source(tmp_path, channels=1 if case == 'mono' else 3, wcs=case != 'no_wcs',
                         keywords=[*WCS.items(), *{**PRIORS, 'FILTER': "'Ha'"}.items()] if case == 'filter' else None)
    if case == 'nonfinite':
        data = np.full((3, 8, 12), .1, dtype=np.float32)
        data[0, 0, 0] = np.nan
        xisf(source, data, keywords={**PRIORS, **WCS}.items())
    text = pcc_script(source) if case != 'neutral' else f'load "{source}"\nsplit r g b\npm "1"'
    if case == 'wrong_axes':
        primary, metadata = _prepare(tmp_path, source, text)
        values, _ = artifacts.read_scientific_pixels(source)
        header = artifacts.read_scientific_header(source)
        header['CUNIT1'] = 'rad'
        write_fits(primary, values.astype(np.float32), extra=list(header.items()))
        with pytest.raises(ContractError, match='axes or units'):
            processing.validate_processing_outputs(tmp_path, source, primary, metadata)
    else:
        with pytest.raises(ContractError):
            _prepare(tmp_path, source, text, channel='unknown' if case == 'unknown_channel' else 'broadband')


@pytest.mark.parametrize('solved', [False, True])
@pytest.mark.parametrize('matrix', ['CD', 'PC'])
def test_astrometry_request_outcome_and_identity(tmp_path, solved, matrix):
    source, _ = _source(tmp_path, wcs=solved, matrix=matrix)
    text = f'load "{source}"' + ('' if solved else '\nplatesolve -catalog=gaia -noflip -nocrop')
    primary, metadata = _prepare(tmp_path, source, text, 'astrometry.solve')
    expected = 'preserved_existing_solution' if solved else 'solve_requested_remote_gaia'
    assert metadata['astrometry']['status'] == expected
    values, identity = artifacts.read_scientific_pixels(source)
    header = artifacts.read_scientific_header(source) if solved else {**PRIORS, **WCS}
    write_fits(primary, values.astype(np.float32), extra=list(header.items()))
    result = processing.validate_processing_outputs(tmp_path, source, primary, metadata)
    assert result['scientific_source'] == identity
    assert result['astrometry']['status'] == ('preserved_existing_solution' if solved else 'solved_remote_gaia')
    write_fits(primary, values[:, ::-1].astype(np.float32), extra=list(header.items()))
    with pytest.raises(ContractError, match='pixel values or orientation'):
        processing.validate_processing_outputs(tmp_path, source, primary, metadata)


def test_missing_wcs_priors_offline_and_output_format_remain_guarded(tmp_path):
    source, _ = _source(tmp_path, wcs=False, keywords=[])
    command = f'load "{source}"\nplatesolve -catalog=gaia -noflip -nocrop'
    with pytest.raises(ContractError, match='file evidence'):
        _prepare(tmp_path, source, command, 'astrometry.solve')
    with pytest.raises(ContractError, match='Offline'):
        _prepare(tmp_path, source, command, 'astrometry.solve', offline=True)
    evidence = {'source': 'user', 'evidence': 'Confirmed field center and camera sampling',
                'ra': 23.828939, 'dec': 30.7817, 'focal': 150, 'pixelsize': 2.9}
    text = '# astrometry-evidence: ' + json.dumps(evidence) + command + ' 23.828939,30.7817 -focal=150 -pixelsize=2.9'
    # The declaration occupies its own comment line.
    text = text.replace(command, '\n' + command)
    _, metadata = _prepare(tmp_path, source, text, 'astrometry.solve')
    assert metadata['astrometry_evidence'] == evidence


@pytest.mark.parametrize('sample,bounds', [('Float32', '0:1'), ('Float64', '0:1'), ('UInt16', None)])
def test_real_siril_preserves_xisf_frame_and_existing_wcs(tmp_path, sample, bounds):
    if not SIRIL.is_file():
        pytest.skip('Siril is unavailable')
    source, values = _source(tmp_path, sample=sample, bounds=bounds)
    if sample == 'UInt16':
        xisf(source, (values * 65535).astype(np.uint16), sample=sample, bounds=bounds,
             keywords={**PRIORS, **WCS}.items())
    text = f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"'
    primary, metadata = _prepare(tmp_path, source, text, 'astrometry.solve')
    script = tmp_path / 'native.ssf'
    script.write_text(text + f'\nsave "{primary}" -chksum\nclose\n')
    config = tmp_path / 'native.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    before = source.read_bytes()
    executed = subprocess.run([str(SIRIL), f'--initfile={config}', f'--directory={tmp_path}', '--offline', f'--script={script}'],
                              capture_output=True, text=True, timeout=30)
    assert executed.returncode == 0, executed.stdout + executed.stderr
    result = processing.validate_processing_outputs(tmp_path, source, primary, metadata)
    assert result['astrometry']['status'] == 'preserved_existing_solution'
    assert source.read_bytes() == before


def test_non_unit_bounds_are_rejected_before_native_processing(tmp_path):
    source, values = _source(tmp_path)
    xisf(source, values * 10 + 10, bounds='10:20', keywords={**PRIORS, **WCS}.items())
    with pytest.raises(ContractError, match='imports non-unit bounds'):
        _prepare(tmp_path, source, f'load "{source}"', 'astrometry.solve')
    with pytest.raises(ContractError, match='imports non-unit bounds'):
        _prepare(tmp_path, source, pcc_script(source))


def test_xisf_photometry_cannot_write_an_xisf_candidate(tmp_path):
    source, _ = _source(tmp_path)
    payload = {'input': {'path': str(source)}, 'context': {'input_state': 'linear', 'channel_mode': 'broadband', 'offline': False}}
    with pytest.raises(ContractError, match='requires a FITS scientific output'):
        processing.prepare_processing(tmp_path, payload, 'color.calibrate', source, tmp_path / 'candidate.xisf',
            pcc_script(source), {}, None, None, None, None)


@pytest.mark.parametrize('declared', ['unknown', 'broadband', 'dualband-osc', 'mono'])
def test_init_freezes_xisf_filter_and_geometry_without_inferring_linearity(tmp_path, monkeypatch, declared):
    source, _ = _source(tmp_path, wcs=False)
    runtime = tmp_path / 'siril-cli'
    runtime.write_text('#!/bin/sh\nexit 0\n')
    runtime.chmod(0o755)
    monkeypatch.setattr(tooling, 'probe_tools', lambda **_kwargs: _fake_probe(runtime))
    target = tmp_path / 'session'
    kwargs = dict(input_state='auto', state_evidence=[], channel_mode=declared, channel_map=None,
                  target_name='M33', target_type='galaxy', style='natural', stars='adaptive',
                  offline=False, keep_intermediates=True)
    if declared in {'dualband-osc', 'mono'}:
        reason = 'conflicts with header filter' if declared == 'dualband-osc' else 'channel count'
        with pytest.raises(ContractError, match=reason):
            sessions.init_session(str(source), str(target), **kwargs)
        assert not target.exists()
    else:
        result = sessions.init_session(str(source), str(target), **kwargs)
        assert result['context']['input_state'] == 'unknown'
        assert result['context']['channel_mode'] == 'broadband'
        assert result['context']['filter_profile']['raw_filter'] == 'IRCUT'
        assert result['context']['filter_profile']['class'] == 'broadband'
        assert result['execution_policy']['guidance'] == EXECUTION_POLICY['guidance']


@pytest.mark.parametrize('selected', ['Astro', 'Duo-Band'])
def test_dwarf_metadata_does_not_supply_pcc_compatibility(tmp_path, selected):
    source, _ = _source(tmp_path, keywords=[*WCS.items(), ('INSTRUME', "'DWARF mini'"), ('FILTER', repr(selected))])
    with pytest.raises(ContractError, match='sourced standard_rgb'):
        _prepare(tmp_path, source, pcc_script(source, compatibility={}))
    if selected == 'Duo-Band':
        with pytest.raises(ContractError, match='actual filter'):
            _prepare(tmp_path, source, pcc_script(source))
    else:
        assert _prepare(tmp_path, source, pcc_script(source))[1]['color_calibration']['method'] == 'pcc'


@pytest.mark.parametrize('selected', ['Astro', 'Duo-Band'])
def test_dwarf_metadata_does_not_supply_spcc_response_evidence(tmp_path, selected):
    source, _ = _source(tmp_path, keywords=[*WCS.items(), ('INSTRUME', "'DWARF 3'"), ('FILTER', repr(selected))])
    with pytest.raises(ContractError):
        _prepare(tmp_path, source, spcc_script(source, equipment=False))
    with pytest.raises(ContractError):
        _prepare(tmp_path, source, spcc_script(source, omitted='monosensor'))


@pytest.mark.parametrize('protocol', ['astrometry.solve', 'color.calibrate'])
def test_xisf_siril_run_replay_binds_scientific_frame(tmp_path, monkeypatch, protocol):
    # Full receipts use the real lineage verifier rather than the isolated unit fixture.
    monkeypatch.undo()
    source, _ = _source(tmp_path)
    runtime = tmp_path / 'siril-cli'
    runtime.write_text('#!/bin/sh\nexit 0\n')
    runtime.chmod(0o755)
    monkeypatch.setattr(tooling, 'probe_tools', lambda **_kwargs: _fake_probe(runtime))
    session = tmp_path / 'session'
    sessions.init_session(str(source), str(session), input_state='linear',
        state_evidence=['user confirmed input_state=linear; stacked, unstretched master'],
        channel_mode='broadband', channel_map=None, target_name='M33', target_type='galaxy',
        style='natural', stars='adaptive', offline=False, keep_intermediates=True)
    _input_inspect_run(session, source, monkeypatch)
    primary = session / 'artifacts/040-result.fit'
    preview = session / 'previews/040-result.jpg'
    script = session / 'scripts/040-stage4.ssf'
    body = f'load "{source}"' if protocol == 'astrometry.solve' else _with_protection(session, source, pcc_script(source))
    script.write_text('requires 1.4.4 1.5.0\nset32bits\n' + body +
        f'\nsave "{primary}" -chksum\nsavejpg "{preview.with_suffix("")}" 95\nclose\n')
    _write_ssf_provenance(session, script, protocol)
    def execute(command, **_kwargs):
        values, _ = artifacts.read_scientific_pixels(source)
        write_fits(primary, values.astype(np.float32), extra=list(artifacts.read_scientific_header(source).items()))
        _write_jpeg(preview, size=(12, 8))
        return subprocess.CompletedProcess(command, 0, '')
    monkeypatch.setattr(core.subprocess, 'run', execute)
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **_kwargs:
        dict(passed=True, decoder={'artifact_fingerprint': core.fingerprint(path, include_path=False)}))
    args = dict(protocol=protocol, script_value=str(script), source_value=str(source),
                expected_values=[str(primary), str(preview)], timeout=30)
    result = core.run_script(str(session), **args)
    assert result['status'] == 'success'
    assert result['scientific_source'] == result['processing_checks']['scientific_source']
    assert result['scientific_source']['sha256'] == sha256_file(source)
    assert core.run_script(str(session), **args)['replayed']
    _, raw, _ = sessions.load_session(session)
    result['scientific_source']['image_index'] = 1
    assert not sessions._receipt_bindings_unchanged(session, raw, result)


@pytest.mark.parametrize('history', ['v9', 'v10', 'fixed-review', 'dwarf-filters', 'protected-background', 'native-neutral', 'restoration', 'v11', 'v12', 'v13'])
def test_historical_confirmation_replays_and_recovers_but_cannot_create_new_work(tmp_path, monkeypatch, history):
    monkeypatch.undo()
    session, source, _, receipt, *_ = _inspected(tmp_path, monkeypatch, channel_mode='unknown')
    confirmed = _confirm(session, channel='broadband')
    recovery = core.recover_run_metrics(str(session), receipt['id'])
    raw = core.load_json(session / 'session.json')
    raw['execution_policy'] = dict({'v9': sessions._V9_EXECUTION_POLICY, 'v10': sessions._V10_EXECUTION_POLICY,
                                    'fixed-review': sessions._FIXED_REVIEW_EXECUTION_POLICY,
                                    'dwarf-filters': sessions._DWARF_FILTER_EXECUTION_POLICY,
                                    'protected-background': sessions._PROTECTED_BACKGROUND_EXECUTION_POLICY,
                                    'native-neutral': sessions._NATIVE_NEUTRAL_EXECUTION_POLICY,
                                    'restoration': sessions._RESTORATION_EXECUTION_POLICY,
                                    'v11': sessions._EXPLICIT_RECOMPOSITION_EXECUTION_POLICY,
                                    'v12': sessions._SHARED_PROTOCOL_DOCS_EXECUTION_POLICY,
                                    'v13': sessions._AI_MORPHOLOGY_EXECUTION_POLICY}[history])
    raw.pop('session_sha256')
    raw['session_sha256'] = core.stable_hash(raw)
    core.atomic_write_json(session / 'session.json', raw)
    path = session / confirmed['confirmation']['path']
    record = core.load_json(path)
    record['session_sha256'] = raw['session_sha256']
    core.atomic_write_json(path, record)
    manifest = core.load_json(session / 'manifest.json')
    manifest['input_confirmation'].update(core.fingerprint(path, include_path=False))
    core.atomic_write_json(session / 'manifest.json', manifest)
    before = {p: p.read_bytes() for p in session.rglob('*') if p.is_file()}
    _, loaded, _ = sessions.load_session(session)
    assert sessions.known_execution_policy(loaded['execution_policy'])
    assert not sessions.execution_policy_is_current(loaded)
    assert sessions.effective_session_payload(session, loaded)['context']['input_state'] == 'linear'
    assert _replay(session, source, receipt)['replayed']
    assert core._validate_metric_recovery(session, receipt['id'], session/recovery['report']['path'])['run_id'] == receipt['id']
    with pytest.raises(ContractError, match='Legacy'):
        core.recover_run_metrics(str(session), receipt['id'])
    with pytest.raises(ContractError, match='Legacy'):
        _confirm(session)
    with pytest.raises(ContractError, match='Legacy'):
        core.run_script(str(session), protocol='input.inspect', script_value=str(session / receipt['script']['path']),
            source_value=str(source), expected_values=[str(tmp_path / 'new.jpg')], timeout=30, validate_only=True)
    kwargs = {key: value for key, value in loaded['context'].items() if key != 'filter_profile'}
    kwargs.update(input_state='auto', state_evidence=[], channel_mode='unknown')
    recovered = sessions.init_session(str(source), str(tmp_path / 'new-session'), **kwargs)
    assert recovered['context']['input_state'] == 'linear'
    assert recovered['context']['channel_mode'] == 'broadband'
    assert recovered['execution_policy']['guidance'] == EXECUTION_POLICY['guidance']
    assert record['session_sha256'] in recovered['context']['state_evidence'][0]
    for p, data in before.items():
        assert p.read_bytes() == data
    result = core.finalize_session(str(session), selection_value=str(_failed_selection(session)), keep_intermediates=True)
    assert result['status'] == 'failed'
    assert core.load_json(session / 'reports/final-audit.json')['input_confirmation'] == manifest['input_confirmation']
    for p, data in before.items():
        if p != session / 'manifest.json':
            assert p.read_bytes() == data

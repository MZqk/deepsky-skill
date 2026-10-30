"""Native sample provenance and diagnostic-only matched star measurements."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from .test_native_processing import ROOT, write_fits
from deep_sky_siril_contract import sha256_file
import siril_psf_samples as bridge
import deep_sky_siril_psf as diagnostics


SETTINGS = ['reset', '-moffat', '-sigma=1.00', '-roundness=0.35', '-minA=0.01', '-maxA=0.95']


def star_table(path, entries, *, settings='sigma=1.00 roundness=0.35 radius=5 profile=1 layer=0 max_stars=500'):
    header = '# star#\tlayer\tA\tbeta\tX\tY\tFWHMx [px]\tFWHMy [px]\tangle\tSat\tProfile\n'
    rows = [f'{index}\t0\t0.15\t3\t{x}\t{y}\t{fx}\t{fy}\t0\t{sat}\tMoffat\n'
            for index, x, y, fx, fy, sat in entries]
    path.write_text('# ' + settings + '\n' + header + ''.join(rows))
    return diagnostics.parse_native_stars(path)


@pytest.mark.parametrize('distance,matched', [(0, 1), (1, 1), (1.00001, 0)])
def test_bidirectional_matching_radius_and_measured_pairs(tmp_path, distance, matched):
    before = star_table(tmp_path / 'before.tsv', [(1, 10, 10, 4, 2, 0)])
    after = star_table(tmp_path / 'after.tsv', [(8, 10 + distance, 10, 3, 1.5, 0)])
    report = diagnostics.matched_fwhm(before, after, geometry_unchanged=True)
    assert report['matched_count'] == matched and report['requires_visual_review'] is True
    if matched:
        assert report['pairs'][0]['measurements']['fwhm_mean']['ratio'] == .75
        assert report['summary']['fwhmx']['difference']['median'] == -1
        assert report['pairs'][0]['before_line'] == 3
    else:
        assert report['status'] == 'uncertain'
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('issue', ['crowded', 'duplicate-position', 'duplicate-id', 'saturated', 'nonfinite', 'missing', 'settings', 'geometry', 'units'])
def test_matching_excludes_ambiguous_or_noncomparable_evidence(tmp_path, issue):
    a = [(1, 10, 10, 4, 2, 0), (2, 30, 30, 4, 2, 0)]
    b = [(8, 10, 10, 3, 1.5, 0), (9, 30, 30, 3, 1.5, 0)]
    if issue == 'crowded':
        b.append((10, 10.5, 10, 3, 1.5, 0))
    elif issue == 'duplicate-position':
        a.append((3, 10, 10, 4, 2, 0))
    elif issue == 'duplicate-id':
        a.append((1, 11, 11, 4, 2, 0))
    elif issue == 'saturated':
        b[0] = (8, 10, 10, 3, 1.5, 1)
    elif issue == 'nonfinite':
        b[0] = (8, 10, 10, float('nan'), 1.5, 0)
    elif issue == 'missing':
        b = b[1:]
    before = star_table(tmp_path / 'before.tsv', a)
    path = tmp_path / 'after.tsv'
    after = star_table(path, b)
    if issue == 'settings':
        after['settings']['sigma'] = 2
    if issue == 'units':
        path.write_text(path.read_text().replace('FWHMx [px]', 'FWHMx [arcsec]'))
        after = diagnostics.parse_native_stars(path)
    report = diagnostics.matched_fwhm(before, after, geometry_unchanged=issue != 'geometry')
    if issue in ('settings', 'geometry', 'units'):
        assert report['status'] == 'uncertain' and not report['pairs']
    else:
        assert report['matched_count'] == 1
        assert report['pairs'][0]['before_id'] == 2
    json.dumps(report, allow_nan=False)


def test_m31_redetection_is_not_proof_of_actual_psf_sample_identity():
    lines = ['log: Running command: findstar', 'log: Found 500 Moffat profile stars in image, channel #1 (FWHM 3.308871)',
             'log: Running command: makepsf', 'log: No stars detected. Finding suitable non-saturated stars...',
             'log: Found 538 suitably bright, non-saturated stars.', 'log: Running command: rl']
    report = diagnostics.psf_log_evidence('\n'.join(lines))
    assert report['redetected'] is True
    assert report['findings'][-1] == {'line_number': 5, 'text': lines[4], 'kind': 'psf_detection', 'count': 538}


@pytest.mark.parametrize('saturated', [False, True])
def test_native_model_serialization_preserves_saturation_and_precision(saturated):
    from types import SimpleNamespace
    model = SimpleNamespace(xpos=10.123456789, ypos=5., layer=0, profile=1, fwhmx=4., fwhmy=3.,
                            A=.15, beta=3., angle=-2., has_saturated=saturated)
    row = bridge.serialize_stars([model])[0]
    assert row['xpos'] == model.xpos and row['included_in_psf'] is not saturated
    model.A = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        bridge.serialize_stars([model])


@pytest.mark.parametrize('geometry_changed', [False, True])
def test_missing_sample_proof_is_diagnostic_and_matching_binds_files(tmp_path, geometry_changed):
    source, candidate = tmp_path / 'source.fit', tmp_path / 'candidate.fit'
    write_fits(source, np.full((4, 4), .1))
    write_fits(candidate, np.full((3 if geometry_changed else 4, 4), .1))
    reports = tmp_path / 'reports'
    reports.mkdir()
    star_table(reports / 'before.tsv', [(1, 1, 1, 4, 3, 0)])
    star_table(reports / 'after.tsv', [(5, 1, 1, 3, 2, 0)])
    result = diagnostics.measure_deconvolution(tmp_path, source, candidate,
        {'parent_tsv': 'reports/before.tsv', 'candidate_tsv': 'reports/after.tsv'},
        'log: Running command: makepsf\nlog: No stars detected. Finding suitable non-saturated stars...\n'
        'log: Found 538 suitably bright, non-saturated stars.')
    assert result['psf_samples']['status'] == 'uncertain'
    assert result['psf_samples']['detected_count'] == 538 and result['psf_samples']['samples'] is None
    assert result['matched_fwhm']['status'] == ('uncertain' if geometry_changed else 'measured')
    assert result['materials']['parent_tsv']['sha256'] == sha256_file(reports / 'before.tsv')
    assert 'verdict' not in result and 'accepted' not in result
    json.dumps(result, allow_nan=False)


def sample_contract(source, root, channels=1, width=384, height=384):
    return {'schema': bridge.CONTRACT_SCHEMA,
            'source': {'path': str(source), 'sha256': sha256_file(source),
                       'width': width, 'height': height, 'channels': channels},
            'detection': {'setfindstar': SETTINGS, 'layer': 0 if channels == 1 else 1, 'maxstars': 500},
            'stars_tsv': str(root / 'before.tsv'),
            'psf': {'path': str(root / 'psf.fit'), 'kernel_size': 31, 'symmetric': False}}


@pytest.mark.parametrize('channels', [1, 3])
def test_real_siril_bridge_binds_native_manual_psf_models(tmp_path, channels):
    binary = shutil.which('siril-cli') or '/Applications/Siril.app/Contents/MacOS/siril-cli'
    if not Path(binary).is_file():
        pytest.skip('Real Siril is unavailable')
    yy, xx = np.mgrid[:384, :384]
    plane = np.full((384, 384), .025)
    for y in range(40, 350, 45):
        for x in range(40, 350, 45):
            amplitude = .05 + .20 * (x + y) / 700
            plane += amplitude * (1 + ((xx - x)**2 + (yy - y)**2) / 9)**-3
    plane += .7 * (1 + ((xx - 360)**2 + (yy - 360)**2) / 9)**-3
    source = tmp_path / 'source.fit'
    write_fits(source, plane if channels == 1 else np.stack([plane] * 3))
    reports = tmp_path / 'reports' / '055-deconvolve'
    reports.mkdir(parents=True)
    candidate = tmp_path / 'artifacts' / '055-deconvolve.fit'
    candidate.parent.mkdir()
    document = sample_contract(source, reports, channels)
    contract = reports / 'contract.json'
    contract.write_text(json.dumps(document))
    script = tmp_path / 'psf.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\n'
                      + 'setfindstar ' + ' '.join(SETTINGS) + '\n'
                      + f'pyscript "{ROOT}/scripts/siril_psf_samples.py" --contract "{contract}" '
                      + f'--contract-sha256 {sha256_file(contract)} --expected-source "{source}" --receipt "{reports}/bridge.json"\n'
                      + f'rl "-loadpsf={reports}/psf.fit" -iters=6 -tv -alpha=3000.0\n'
                      + f'save "{candidate}" -chksum\nclose\nload "{candidate}"\n'
                      + 'setfindstar ' + ' '.join(SETTINGS) + '\n'
                      + f'findstar -layer={document["detection"]["layer"]} -maxstars=500 "-out={reports}/after.tsv"\nclose\n')
    config = tmp_path / 's.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    before = sha256_file(source)
    result = subprocess.run([binary, f'--initfile={config}', f'--directory={tmp_path}', '--offline',
                             f'--script={script}'], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((reports / 'bridge.json').read_text())
    assert receipt['status'] == 'sample_bound' and receipt['included_count'] >= 20
    assert 'No stars detected. Finding' not in result.stdout + result.stderr
    assert (reports / 'psf.fit').is_file()
    assert sha256_file(source) == before
    assert len(receipt['stars']) == receipt['detected_count']
    assert all(row['layer'] == document['detection']['layer'] for row in receipt['stars'])
    parsed = diagnostics.parse_native_stars(reports / 'before.tsv')
    assert parsed['status'] == 'measured' and len(parsed['rows']) == receipt['detected_count']
    assert receipt['psf']['method'] == 'sample_derived_manual'
    assert receipt['psf']['parameters'] == bridge.model_parameters(receipt['stars'])

    from deep_sky_siril_processing import script_commands
    binding = diagnostics.prepare_deconvolution(tmp_path, source, candidate, script_commands(script.read_text()))
    measured = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, result.stdout + result.stderr, write_reports=True)
    assert measured['psf_samples']['status'] == 'verified', measured
    assert measured['matched_fwhm']['status'] == 'measured', measured
    assert measured['matched_fwhm']['matched_count'] >= 20
    assert measured['status'] == 'measured'
    assert measured['spatial_reports']['status'] == 'bound'
    assert measured['psf_samples']['orientation']['mean_angle_deg'] == receipt['psf']['parameters']['angle']
    json.dumps(measured, allow_nan=False)


def native_model(**changes):
    from types import SimpleNamespace
    return SimpleNamespace(**dict(dict(xpos=4.123456789, ypos=3., layer=0, profile=1,
        fwhmx=4., fwhmy=3., A=.15, beta=3., angle=-2., has_saturated=False), **changes))


class FakeNativeSiril:
    def __init__(self, document, models):
        self.document, self.models, self.commands = document, models, []

    def get_image_stars(self, *, channel):
        assert channel == self.document['detection']['layer']
        return self.models

    def cmd(self, *args):
        self.commands.append(args)
        write_fits(Path(self.document['psf']['path']), np.full((7, 7), .01))


def material_fixture(tmp_path):
    source, candidate = tmp_path / 'source.fit', tmp_path / 'candidate.fit'
    write_fits(source, np.full((8, 12), .1))
    write_fits(candidate, np.full((8, 12), .1))
    reports = tmp_path / 'reports' / '055-deconvolve'
    reports.mkdir(parents=True)
    document = sample_contract(source, reports, width=12, height=8)
    document['psf']['kernel_size'] = 7
    contract = reports / 'contract.json'
    contract.write_text(json.dumps(document))
    return source, candidate, reports, contract, document


@pytest.mark.parametrize('case', ['bound', 'saturated', 'nonfinite', 'unavailable', 'too-many', 'unrepresentable'])
def test_capture_records_exact_models_and_explicit_fallback(tmp_path, case):
    source, _, reports, _, document = material_fixture(tmp_path)
    models = [native_model(), native_model(xpos=8., has_saturated=True)]
    if case == 'saturated':
        models[0].has_saturated = True
    elif case == 'nonfinite':
        models[0].beta = float('inf')
    elif case == 'unavailable':
        models = []
    elif case == 'too-many':
        models = [native_model(xpos=float(i)) for i in range(501)]
    elif case == 'unrepresentable':
        models[0].beta = 11.
    native = FakeNativeSiril(document, models)
    before = sha256_file(source)
    receipt = bridge.capture(native, document)
    assert sha256_file(source) == before
    bound = case in ('bound', 'too-many')
    assert receipt['status'] == ('sample_bound' if bound else 'uncertain')
    assert native.commands[0][1] == ('manual' if bound else 'stars')
    assert receipt['psf']['method'] == ('sample_derived_manual' if bound else 'native_stars_fallback')
    if case == 'bound':
        assert receipt['included_count'] == 1 and receipt['excluded_count'] == 1
        assert receipt['stars'][1]['exclusion_reason'] == 'saturated'
        assert receipt['psf']['parameters']['fwhm'] == 4
    if case == 'too-many':
        assert receipt['native_detected_count'] == 501 and receipt['detected_count'] == 500
    assert diagnostics.parse_native_stars(reports / 'before.tsv')['status'] == 'measured'
    json.dumps(receipt, allow_nan=False)


@pytest.mark.parametrize('case', ['hash', 'source', 'layer', 'maxstars', 'settings', 'kernel', 'path', 'symlink'])
def test_sample_contract_rejects_invalid_bindings(tmp_path, case):
    source, _, reports, _, document = material_fixture(tmp_path)
    if case == 'hash':
        document['source']['sha256'] = '0' * 64
    elif case == 'source':
        document['source']['path'] = str(tmp_path / 'wrong.fit')
    elif case == 'layer':
        document['detection']['layer'] = 1
    elif case == 'maxstars':
        document['detection']['maxstars'] = 501
    elif case == 'settings':
        document['detection']['setfindstar'] = ['reset', '-sigma=nan']
    elif case == 'kernel':
        document['psf']['kernel_size'] = 8
    elif case == 'path':
        document['psf']['path'] = str(tmp_path / 'outside.fit')
    elif case == 'symlink':
        Path(document['stars_tsv']).symlink_to(tmp_path / 'outside.tsv')
    with pytest.raises(ValueError):
        bridge.validate_contract(document, source, reports)


def test_measurement_verifies_chain_and_retains_integrity_failure(tmp_path):
    source, candidate, reports, contract, document = material_fixture(tmp_path)
    native = FakeNativeSiril(document, [native_model()])
    receipt = bridge.capture(native, document)
    receipt.update(contract_sha256=sha256_file(contract), sirilpy_version=bridge.VERSION)
    bridge_path = reports / 'bridge.json'
    bridge_path.write_text(json.dumps(receipt))
    bridge.export_models(reports / 'after.tsv', bridge.serialize_stars([native_model(fwhmx=3., fwhmy=2.)]), document['detection'])
    binding = dict(policy=bridge.POLICY, spatial_reports={
        "json": "reports/055-deconvolve/psf-spatial.json", "markdown": "reports/055-deconvolve/psf-spatial.md"}, contract=str(contract.relative_to(tmp_path)), contract_sha256=sha256_file(contract),
        bridge_receipt=str(bridge_path.relative_to(tmp_path)), parent_tsv='reports/055-deconvolve/before.tsv',
        candidate_tsv='reports/055-deconvolve/after.tsv', psf='reports/055-deconvolve/psf.fit')
    result = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, '')
    assert result['status'] == 'measured'
    assert result['psf_samples']['included_count'] == 1  # No new sample-count gate.
    assert result['psf_samples']['warnings']
    receipt['psf']['sha256'] = '0' * 64
    bridge_path.write_text(json.dumps(receipt))
    from deep_sky_siril_processing import ProcessingEvidenceError
    with pytest.raises(ProcessingEvidenceError) as error:
        diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, '')
    assert error.value.processing_checks['deconvolution']['matched_fwhm']['matched_count'] == 1


def deconvolution_session(tmp_path, monkeypatch, *, bridge_enabled=True, legacy=False):
    from .test_deep_sky_siril import _session, _input_inspect_run, _write_ssf_provenance
    if legacy:
        import deep_sky_siril_session as state
        monkeypatch.setattr(state, 'EXECUTION_POLICY', dict(state._NATIVE_NEUTRAL_EXECUTION_POLICY))
    session, source, _ = _session(tmp_path, monkeypatch, input_state='linear')
    _input_inspect_run(session, source, monkeypatch)
    reports = session / 'reports' / '055-deconvolve'
    reports.mkdir()
    document = sample_contract(source, reports, width=12, height=8)
    document['psf']['kernel_size'] = 7
    contract = reports / 'contract.json'
    contract.write_text(json.dumps(document))
    primary = session / 'artifacts' / '055-deconvolve.fit'
    script = session / 'scripts' / '055-deconvolve.ssf'
    adapter = (f'pyscript "{ROOT}/scripts/siril_psf_samples.py" --contract "{contract}" '
               f'--contract-sha256 {sha256_file(contract)} --expected-source "{source}" --receipt "{reports}/bridge.json"\n'
               if bridge_enabled else f'findstar -layer=0 -maxstars=500 "-out={reports}/before.tsv"\n'
               f'makepsf stars -ks=7 "-savepsf={reports}/psf.fit"\n')
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\nsetfindstar ' + ' '.join(SETTINGS) + '\n'
        + adapter + f'rl "-loadpsf={reports}/psf.fit" -iters=6 -tv -alpha=3000.0\n'
        + f'save "{primary}" -chksum\nclose\nload "{primary}"\nsetfindstar ' + ' '.join(SETTINGS) + '\n'
        + f'findstar -layer=0 -maxstars=500 "-out={reports}/after.tsv"\nclose\n'
        + f'load "{source}"\nmtf 0 0.15 1\nsavejpg "{session}/previews/055-parent" 95\nclose\n'
        + f'load "{primary}"\nmtf 0 0.15 1\nsavejpg "{session}/previews/055-candidate" 95\nclose\n')
    if legacy:
        text = script.read_text().replace('fmul 1\n', '')
        text = text[:text.index(f'load "{source}"', text.index('after.tsv'))]
        script.write_text(text)
    _write_ssf_provenance(session, script, 'restoration.deconvolve')
    expected = [primary, reports / 'before.tsv', reports / 'after.tsv', reports / 'psf.fit',
                session / 'previews/055-parent.jpg', session / 'previews/055-candidate.jpg']
    if legacy:
        expected = [path for path in expected if path.suffix != '.jpg']
    if bridge_enabled:
        expected.append(reports / 'bridge.json')
    kwargs = dict(protocol='restoration.deconvolve', script_value=str(script), source_value=str(source),
                  primary_output_value=str(primary), expected_values=list(map(str, expected)), timeout=30)
    return session, source, reports, document, script, kwargs


@pytest.mark.parametrize('case', ['order', 'redetect', 'switch', 'psf', 'undeclared', 'hash', 'geometry'])
def test_bridge_frame_rejected_before_execution_without_receipt(tmp_path, monkeypatch, case):
    from .test_deep_sky_siril import core, _write_ssf_provenance
    session, source, reports, document, script, kwargs = deconvolution_session(tmp_path, monkeypatch)
    text = script.read_text()
    if case == 'order':
        text = text.replace('pyscript ', 'stat main\npyscript ')
    elif case == 'redetect':
        text = text.replace('rl ', f'findstar -layer=0 -maxstars=500 "-out={reports}/after.tsv"\nrl ')
    elif case == 'switch':
        text = text.replace('pyscript ', f'load "{source}"\npyscript ')
    elif case == 'psf':
        text = text.replace(f'-loadpsf={reports}/psf.fit', f'-loadpsf={reports}/other.fit')
    elif case == 'undeclared':
        kwargs['expected_values'].remove(str(reports / 'before.tsv'))
    elif case in ('hash', 'geometry'):
        contract = reports / 'contract.json'
        if case == 'geometry':
            document['source']['width'] += 1
            contract.write_text(json.dumps(document))
            # Update the SSF hash explicitly; geometry still must be rejected.
            import re
            text = re.sub(r'--contract-sha256 [a-f0-9]{64}', '--contract-sha256 ' + sha256_file(contract), text)
        else:
            contract.write_text(contract.read_text() + '\n')
    script.write_text(text)
    _write_ssf_provenance(session, script, 'restoration.deconvolve')
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: pytest.fail('Rejected script executed'))
    for static in (True, False):
        with pytest.raises(core.ContractError):
            core.run_script(str(session), **kwargs, validate_only=static)
    assert not (session / 'runs/055-deconvolve.json').exists()
    assert not (reports / 'before.tsv').exists()


@pytest.mark.parametrize('case', ['bound', 'missing', 'fatal', 'drift'])
def test_run_retains_diagnostics_replays_and_rejects_tampering(tmp_path, monkeypatch, case):
    from .test_deep_sky_siril import core, _write_fits
    import deep_sky_siril_session as state
    session, source, reports, document, script, kwargs = deconvolution_session(tmp_path, monkeypatch, bridge_enabled=case != 'missing')
    def fake_run(command, **_kwargs):
        write_fits(Path(kwargs['primary_output_value']), np.zeros((8, 12)))
        from .test_deep_sky_siril import _write_jpeg
        for name in ('055-parent', '055-candidate'):
            _write_jpeg(session / 'previews' / (name + '.jpg'), size=(12, 8))
        if case != 'missing':
            receipt = bridge.capture(FakeNativeSiril(document, [native_model()]), document)
            receipt.update(contract_sha256=sha256_file(reports / 'contract.json'), sirilpy_version=bridge.VERSION)
            (reports / 'bridge.json').write_text(json.dumps(receipt))
        else:
            bridge.export_models(reports / 'before.tsv', bridge.serialize_stars([native_model()]), document['detection'])
            write_fits(reports / 'psf.fit', np.full((7, 7), .01))
        bridge.export_models(reports / 'after.tsv', bridge.serialize_stars([native_model(fwhmx=3., fwhmy=2.)]), document['detection'])
        if case == 'drift':
            (reports / 'contract.json').write_text(json.dumps(document) + '\n')
        return subprocess.CompletedProcess(command, 0, 'log: Running command: makepsf\n'
            + ('log: Error: native failure\n' if case == 'fatal' else ''))
    monkeypatch.setattr(core.subprocess, 'run', fake_run)
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **k:
        dict(passed=True, decoder={'artifact_fingerprint': core.fingerprint(path, include_path=False)}))
    run = core.run_script(str(session), **kwargs)
    checks = run['processing_checks']['deconvolution']
    assert run['status'] == ('failed' if case in ('fatal', 'drift') else 'success')
    assert checks['psf_samples']['status'] == ('verified' if case in ('bound', 'fatal') else 'uncertain')
    if case == 'drift':
        assert checks['integrity_reason'] == 'PSF sample contract changed during execution'
    elif case in ('bound', 'missing'):
        replay = core.run_script(str(session), **kwargs)
        assert replay['replayed'] is True
        assert state._verified_run_outputs(session)
        receipt_path = session / 'runs/055-deconvolve.json'
        run['processing_checks']['deconvolution']['status'] = 'fabricated'
        core.atomic_write_json(receipt_path, run)
        manifest = core.load_json(session / 'manifest.json')
        next(r for r in manifest['runs'] if r['id'] == run['id'])['receipt_sha256'] = sha256_file(receipt_path)
        core.atomic_write_json(session / 'manifest.json', manifest)
        with pytest.raises(core.ContractError):
            core.run_script(str(session), **kwargs)


def test_native_psf_failure_writes_captured_evidence_and_remains_failed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source, candidate, reports, contract, document = material_fixture(tmp_path)
    class FailingSiril(FakeNativeSiril):
        def cmd(self, *args):
            raise RuntimeError('Native processing failed')
        def connect(self):
            pass
        def get_image_filename(self):
            return str(source)
        def get_image_shape(self):
            return (1, 8, 12)
    monkeypatch.setitem(sys.modules, 'sirilpy', SimpleNamespace(check_module_version=lambda v: True,
        __version__=bridge.VERSION, SirilInterface=lambda: FailingSiril(document, [native_model()])))
    receipt_path = reports / 'bridge.json'
    with pytest.raises(bridge.PsfGenerationError):
        bridge.main(['--contract', str(contract), '--contract-sha256', sha256_file(contract),
                     '--expected-source', str(source), '--receipt', str(receipt_path)])
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'failed' and len(receipt['stars']) == 1
    assert receipt['psf']['sha256'] is None and not Path(document['psf']['path']).exists()
    binding = dict(policy=bridge.POLICY, spatial_reports={
        "json": "reports/055-deconvolve/psf-spatial.json", "markdown": "reports/055-deconvolve/psf-spatial.md"}, contract=str(contract.relative_to(tmp_path)), contract_sha256=sha256_file(contract),
        bridge_receipt=str(receipt_path.relative_to(tmp_path)), parent_tsv=str(Path(document['stars_tsv']).relative_to(tmp_path)),
        psf=str(Path(document['psf']['path']).relative_to(tmp_path)))
    report = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, '')
    assert report['psf_samples']['status'] == 'uncertain'
    # The receipt binds the captured models by count; the rows stay in the bridge receipt.
    assert report['psf_samples']['samples'] == {'count': 1, 'rows_inline': False,
        'source': 'materials.parent_tsv / materials.candidate_tsv', 'requires_visual_review': True}
    assert 'Native PSF output is unavailable' in report['psf_samples']['reasons']


def test_legacy_inline_receipts_still_replay_after_externalization():
    # A receipt written before the externalization inlined the rows twice. Older sessions
    # must keep replaying, so both shapes normalize to the same compact reference.
    inline = {
        'status': 'measured',
        'psf_samples': {'status': 'verified', 'captured_samples': [{'id': 1}, {'id': 2}],
                        'samples': [{'id': 1}, {'id': 2}], 'detected_count': 2},
        'matched_fwhm': {'status': 'measured', 'matched_count': 2,
                         'pairs': [{'before_id': 1}, {'before_id': 2}],
                         'summary': {'fwhmx': {'difference': {'median': -1}}}},
    }
    compact = {
        'status': 'measured',
        'psf_samples': {'status': 'verified', 'samples': diagnostics._rows_reference(2), 'detected_count': 2},
        'matched_fwhm': {'status': 'measured', 'matched_count': 2,
                         'pairs': diagnostics._rows_reference(2),
                         'summary': {'fwhmx': {'difference': {'median': -1}}}},
    }
    assert diagnostics.normalize_deconvolution_evidence(inline) == compact
    assert diagnostics.normalize_deconvolution_evidence(compact) == compact
    assert diagnostics.normalize_deconvolution_evidence(None) is None
    # The measured summary a review cites survives normalization untouched.
    assert diagnostics.normalize_deconvolution_evidence(inline)['matched_fwhm']['summary'] == compact['matched_fwhm']['summary']


def test_bulk_star_rows_stay_out_of_the_receipt_but_remain_bound(tmp_path):
    source, candidate, reports, contract, document = material_fixture(tmp_path)
    # 2 px spacing keeps every star uniquely matched inside the 1.0 px match radius.
    entries = [(index + 1, 2 * (index % 6), 2 * (index // 6), 4., 3., 0) for index in range(24)]
    star_table(reports / 'before.tsv', entries)
    star_table(reports / 'after.tsv', [(index + 101, 2 * (index % 6), 2 * (index // 6), 3., 2., 0) for index in range(24)])
    result = diagnostics.measure_deconvolution(tmp_path, source, candidate,
        {'parent_tsv': 'reports/055-deconvolve/before.tsv',
         'candidate_tsv': 'reports/055-deconvolve/after.tsv'}, '')
    assert result['matched_fwhm']['status'] == 'measured'
    assert result['matched_fwhm']['matched_count'] == 24
    assert result['matched_fwhm']['pairs'] == {'count': 24, 'rows_inline': False,
        'source': 'materials.parent_tsv / materials.candidate_tsv', 'requires_visual_review': True}
    # The measured summary a review actually cites is preserved in full.
    assert result['matched_fwhm']['summary']['fwhmx']['difference']['median'] == -1
    # Rows remain reproducible from the hash-bound materials.
    assert len(diagnostics.parse_native_stars(reports / 'before.tsv')['rows']) == 24
    assert 'captured_samples' not in json.dumps(result)
    assert len(json.dumps(result)) < 4000


@pytest.mark.parametrize('dimensions', [(1e-320, 1e308), (1e-320, 1e-320)])
def test_finite_but_unmeasurable_pairs_remain_diagnostic(tmp_path, dimensions):
    before = star_table(tmp_path / 'before.tsv', [(1, 4, 3, *dimensions, 0)])
    after = star_table(tmp_path / 'after.tsv', [(2, 4, 3, 4, 3, 0)])
    report = diagnostics.matched_fwhm(before, after, geometry_unchanged=True)
    assert report['status'] == 'uncertain' and report['invalid_pair_count'] == 1
    json.dumps(report, allow_nan=False)


def test_unsupported_geometry_does_not_add_an_execution_gate(tmp_path):
    source, candidate = tmp_path / 'source.xisf', tmp_path / 'candidate.xisf'
    source.write_bytes(b'unmeasurable read-only geometry fixture')
    candidate.write_bytes(b'unmeasurable read-only geometry fixture')
    reports = tmp_path / 'reports'
    reports.mkdir()
    star_table(reports / 'before.tsv', [(1, 4, 3, 4, 3, 0)])
    star_table(reports / 'after.tsv', [(2, 4, 3, 3, 2, 0)])
    report = diagnostics.measure_deconvolution(tmp_path, source, candidate,
        dict(parent_tsv='reports/before.tsv', candidate_tsv='reports/after.tsv'), '')
    assert report['matched_fwhm']['status'] == 'uncertain'
    assert not report['matched_fwhm']['pairs']
    assert 'Geometry or channel count is not confirmed unchanged' in report['matched_fwhm']['reasons']


def test_matched_diagnostics_require_no_additional_python_dependency(tmp_path, monkeypatch):
    before = star_table(tmp_path / 'before.tsv', [(1, 4, 3, 4, 3, 0)])
    after = star_table(tmp_path / 'after.tsv', [(2, 4, 3, 3, 2, 0)])
    monkeypatch.setitem(sys.modules, 'numpy', None)
    report = diagnostics.matched_fwhm(before, after, geometry_unchanged=True)
    assert report['summary']['fwhmx']['difference'] == dict(p05=-1, median=-1, p95=-1)

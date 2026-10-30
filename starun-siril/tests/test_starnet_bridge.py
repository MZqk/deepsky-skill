"""Forward tests of the pinned bridge's TIFF and inverse-MTF path."""
from pathlib import Path
import os
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
tifffile = pytest.importorskip("tifffile")

ROOT = Path(__file__).resolve().parents[1]
import siril_starnet as bridge
from deep_sky_siril_contract import sha256_file


def test_pinned_source_and_linear_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("imagecodecs")
    lock = json.loads((ROOT / 'references/starnet-source.json').read_text())
    assert lock['adapter_sha256'] == sha256_file(ROOT / 'scripts/siril_starnet.py')
    monkeypatch.setattr(bridge, 'np', np, raising=False)
    monkeypatch.setattr(bridge, 'tifffile', tifffile, raising=False)
    def echo_cli(argv, **kwargs):
        source, output = Path(argv[argv.index('-i')+1]), Path(argv[argv.index('-o')+1])
        tifffile.imwrite(output, tifffile.imread(source))
        assert kwargs['cwd'] == output.parent
        assert '-u' not in argv and '-d' not in argv and '-m' not in argv
        return SimpleNamespace(returncode=0, stdout='StarNet test handoff')
    monkeypatch.setattr(bridge.subprocess, 'run', echo_cli)
    runtime = {'executable': {'path': '/test/starnet2'}, 'model': {'path': '/test/model.onnx'}}
    for shape in ((12,16), (3,12,16)):
        source = np.linspace(.02, .06, np.prod(shape), dtype=np.float32).reshape(shape)
        result, evidence = bridge.process_starless(source, runtime, 256, tmp_path / ('work-' + str(len(shape))), lambda x: None)
        assert result.shape == source.shape and np.isfinite(result).all()
        assert np.max(np.abs(result - source)) < 1e-5
        assert evidence['handoff_bits'] == 16
    # The executor owns cleanup after scientific output validation and receipt commit.
    assert len(list(tmp_path.glob('work-*/handoff-*'))) == 2


@pytest.mark.parametrize('failure', ['geometry', 'nonfinite', 'extra_file'])
def test_invalid_cli_outputs_are_rejected_and_retained(tmp_path, monkeypatch, failure):
    pytest.importorskip("imagecodecs")
    monkeypatch.setattr(bridge, 'np', np, raising=False)
    monkeypatch.setattr(bridge, 'tifffile', tifffile, raising=False)
    def invalid_cli(argv, **kwargs):
        output = Path(argv[argv.index('-o')+1])
        values = np.ones((8,8) if failure == 'geometry' else (12,16), dtype=np.float32)
        if failure == 'nonfinite': values[0,0] = np.nan
        tifffile.imwrite(output, values)
        if failure == 'extra_file': (output.parent / 'unexpected.txt').write_text('unexpected')
        return SimpleNamespace(returncode=0, stdout='test')
    monkeypatch.setattr(bridge.subprocess, 'run', invalid_cli)
    with pytest.raises(ValueError):
        bridge.process_starless(np.full((12,16), .03, dtype=np.float32),
                                {'executable': {'path': '/test/starnet2'}, 'model': {'path': '/test/model.onnx'}},
                                256, tmp_path / 'work', lambda x: None)
    assert list((tmp_path / 'work').glob('handoff-*/starless.tif'))


def test_missing_dependency_never_installs(monkeypatch):
    def missing(name): raise ModuleNotFoundError(name)
    monkeypatch.setattr(bridge.importlib, 'import_module', missing)
    with pytest.raises(ModuleNotFoundError): bridge.probe_runtime()


@pytest.mark.skipif(os.environ.get('STARUN_SIRIL_REAL_TESTS') != '1', reason='Opt-in real Siril/StarNet inference')
def test_real_siril_starnet_pipeline(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    import deep_sky_siril_core as core
    import deep_sky_siril_session as session_state
    from .test_native_processing import write_fits
    from .test_deep_sky_siril import _write_ssf_provenance
    source = tmp_path / 'synthetic-linear.fit'
    y, x = np.mgrid[:512, :512]
    base = .02 + .001*x/512 + .03*np.exp(-((x-256)**2+(y-256)**2)/8000)
    for cx, cy in ((100,100), (180,320), (390,200), (360,370)):
        base += .35*np.exp(-((x-cx)**2+(y-cy)**2)/5)
    pixels = np.stack((base*.95, base, base*1.03)).astype(np.float32)
    write_fits(source, pixels)
    original_hash = sha256_file(source)
    session = tmp_path / 'session'
    session_state.init_session(str(source), str(session), input_state='linear',
        state_evidence=['user confirmed input_state=linear; synthetic scene'], channel_mode='broadband',
        channel_map=None, target_name='synthetic', target_type='unknown', style='natural', stars='preserve',
        offline=True, keep_intermediates=True)
    probe = core.load_json(session / 'reports/tool-probe.json')
    assert probe['tools']['starnet']['compatible'], probe['tools']['starnet']
    report_dir = session / 'reports/060-stars'; report_dir.mkdir()
    contract_path, receipt_path = report_dir / 'contract.json', report_dir / 'bridge.json'
    document = {'schema': bridge.CONTRACT_SCHEMA, 'source': {'path': str(source), 'sha256': original_hash},
                'runtime': probe['tools']['starnet'], 'stride': 256,
                'work_dir': str(session / 'runtime/starnet/060-stars')}
    if document['runtime']['version'] == '2.6.2':
        document.update(schema='starun-siril.starnet-contract.v2',
                        input_fits=str(session / 'artifacts/060-full-source.fit'),
                        linear_stretch={'shadows_clipping': -2.8, 'target_background': 0.25})
    core.atomic_write_json(contract_path, document)
    full, starless, stars = [session / 'artifacts' / n for n in ('060-full-source.fit','060-starless.fit','060-star-layer.fit')]
    script = session / 'scripts/060-stars.ssf'
    script.write_text('\n'.join(['requires 1.4.4 1.5.0', 'set32bits', f'load "{source}"',
        f'save "{full.with_suffix("")}" -chksum', f'load "{source}"',
        f'pyscript "{document['runtime']['adapter']['path']}" --contract "{contract_path}" --contract-sha256 {sha256_file(contract_path)} --expected-source "{source}" --receipt "{receipt_path}"',
        f'save "{starless.with_suffix("")}" -chksum', 'close',
        'pm "$artifacts/060-full-source$ - $artifacts/060-starless$"',
        f'save "{stars.with_suffix("")}" -chksum', 'close']) + '\n')
    _write_ssf_provenance(session, script, 'stars.separate')
    arguments = dict(protocol='stars.separate', script_value=str(script), source_value=str(source),
                     expected_values=list(map(str, (full,starless,stars,receipt_path))), primary_output_value=str(starless), timeout=180)
    result = core.run_script(str(session), **arguments)
    assert result['status'] == 'success', json.dumps(result, indent=2)
    assert result['processing_checks']['star_separation']['max_abs_closure'] <= 1e-5
    assert result['runtime_bindings_unchanged'] is True and sha256_file(source) == original_hash
    assert not list((session / 'runtime/starnet/060-stars').glob('handoff-*'))
    assert core.run_script(str(session), **arguments)['replayed'] is True


@pytest.mark.parametrize('version', [None, '2.0.0', '2.4.9', '2.6.3', '2.7.0'])
def test_unqualified_cli_version_is_refused_before_python(tmp_path, monkeypatch, version):
    import deep_sky_siril_tooling as tooling
    executable, siril, model = [tmp_path / n for n in ('starnet2', 'siril-cli', 'model.onnx')]
    for p in (executable, siril, model): p.write_bytes(b'test')
    monkeypatch.setattr(tooling, 'discover_siril', lambda: siril)
    monkeypatch.setattr(tooling, '_configured_executable', lambda name: executable)
    monkeypatch.setattr(tooling, '_discover_starnet_model', lambda path: model)
    monkeypatch.setattr(tooling, '_tool_version', lambda path: (version if path == executable else '1.4.4', 'test'))
    def unexpected_probe(path): raise AssertionError('Incompatible CLI must not start a Python bridge')
    monkeypatch.setattr(tooling, '_probe_starnet_python', unexpected_probe)
    result = tooling.probe_tools(offline=True)['tools']['starnet']
    assert result['compatible'] is False and 'starnet_version_incompatible' in result['reasons']

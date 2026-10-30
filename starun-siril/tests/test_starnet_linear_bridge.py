"""Native float32 handoff, frozen routing and audit boundary regression tests."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
import siril_starnet_linear as bridge
import siril_starnet as legacy
import deep_sky_siril_contract as contract
import deep_sky_siril_tooling as tooling
import deep_sky_siril_session as sessions
from .test_native_processing import write_fits


@pytest.fixture
def native(tmp_path):
    (tmp_path / 'artifacts').mkdir()
    source = tmp_path / 'source.fit'
    y, x = np.mgrid[:512, :512]
    data = np.stack((.02 + x * .00002, .03 + y * .00001, np.full(x.shape, .04))).astype('float32')
    data[:, 0, 0] = -.001
    write_fits(source, data)
    prepared = tmp_path / 'artifacts/full.fit'
    shutil.copyfile(source, prepared)
    executable, model, module = [tmp_path / n for n in ('starnet2', 'weights.onnx', 'module.py')]
    for path in (executable, model, module):
        path.write_bytes(b'fixture')
    runtime = {'compatible': True, 'version': '2.6.2', 'reasons': [],
        'adapter': contract.fingerprint(ROOT / 'scripts/siril_starnet_linear.py'),
        'source_lock': contract.fingerprint(ROOT / 'references/starnet-linear-source.json'),
        'executable': contract.fingerprint(executable), 'model': contract.fingerprint_resource(model, kind='file'),
        'python': {'status': 'verified', 'interpreter': contract.fingerprint(Path(sys.executable).resolve()),
            'dependencies': {name: {'module': contract.fingerprint(module)} for name in legacy.DEPENDENCIES}}}
    document = {'schema': bridge.CONTRACT_SCHEMA, 'source': {'path': str(source), 'sha256': contract.sha256_file(source)},
        'runtime': runtime, 'stride': 256, 'work_dir': str(tmp_path / 'runtime/starnet/060-stars'),
        'input_fits': str(prepared), 'linear_stretch': dict(bridge.DEFAULT_LINEAR_STRETCH)}
    return tmp_path, source, data, document


class Siril:
    def __init__(self, path):
        self.path = path
        self.messages = []
    def get_image(self):
        data, _ = bridge.read_native_fits(self.path)
        return SimpleNamespace(data=data.astype('float32'), bitpix=-32, top_down=False)
    def log(self, text):
        self.messages.append(text)
    def cmd(self, command, path):
        assert command == 'load'
        self.path = Path(path.strip('"'))


def echo(argv, **options):
    source, output = Path(argv[2]), Path(argv[4])
    assert source.suffix == output.suffix == '.fit'
    assert options['cwd'] == source.parent
    assert '--linear' in argv and not set(argv) & {'-u', '-d', '-m', '-n'}
    shutil.copyfile(source, output)
    return SimpleNamespace(returncode=0, stdout='native raw log\n')


@pytest.mark.parametrize('mono', [False, True])
def test_native_handoff_preserves_float_pixels_negative_and_constant(native, monkeypatch, mono):
    session, source, data, document = native
    prepared = Path(document['input_fits'])
    if mono:
        write_fits(prepared, data[:1]); write_fits(source, data[:1])
        document['source']['sha256'] = contract.sha256_file(source)
    work = bridge.validate_contract(document, source, document['runtime'], session, '060-stars')
    monkeypatch.setattr(bridge.subprocess, 'run', echo)
    evidence = bridge.process_starless(Siril(source), document, work)
    output = session / 'artifacts/starless.fit'
    shutil.copyfile(evidence['argv'][4], output)
    receipt = {'schema': bridge.RECEIPT_SCHEMA, 'status': 'verified', 'contract_sha256': 'a'*64,
               **{k: document[k] for k in ('source', 'runtime', 'stride')}, **evidence}
    bridge.validate_receipt(document, receipt, output, prepared)
    actual, _ = bridge.read_native_fits(output)
    np.testing.assert_array_equal(actual, data[:1] if mono else data)
    assert evidence['input_negative_count'] == (1 if mono else 3)
    assert evidence['stdout'] == 'native raw log\n'
    assert (work / 'native.log').read_text() == evidence['stdout']
    assert (work / Path(evidence['argv'][2]).parent.name / 'input.fit').read_bytes() == prepared.read_bytes()
    for field, value in [('linear_stretch', {'shadows_clipping': -2.8, 'target_background': .1}),
                         ('input_negative_count', 0), ('argv', evidence['argv'] + ['-u'])]:
        changed = deepcopy(receipt); changed[field] = value
        with pytest.raises(ValueError):
            bridge.validate_receipt(document, changed, output, prepared)
    changed = deepcopy(receipt); changed['native_output']['pixels_sha256'] = '0'*64
    with pytest.raises(ValueError):
        bridge.validate_receipt(document, changed, output, prepared)
    write_fits(output, np.asarray(actual, dtype='float32') + .001)
    with pytest.raises(ValueError):
        bridge.validate_receipt(document, receipt, output, prepared)


@pytest.mark.parametrize('parameters', [None, {}, {'shadows_clipping': -2.8},
    {'shadows_clipping': True, 'target_background': .25},
    {'shadows_clipping': -2.8, 'target_background': False},
    {'shadows_clipping': 0, 'target_background': .25},
    {'shadows_clipping': float('-inf'), 'target_background': .25},
    {'shadows_clipping': -2.8, 'target_background': 1},
    {'shadows_clipping': -2.8, 'target_background': 0},
    {'shadows_clipping': -2.8, 'target_background': .25, 'extra': 0}])
def test_native_parameter_rejection(native, parameters):
    session, source, _, document = native
    document['linear_stretch'] = parameters
    with pytest.raises((ValueError, contract.ContractError)):
        bridge.validate_contract(document, source, document['runtime'], session, '060-stars')


@pytest.mark.parametrize('mutation', ['source', 'version', 'adapter', 'runtime_drift', 'extra', 'input_path'])
def test_native_contract_trust_boundaries(native, mutation):
    session, source, _, document = native
    if mutation == 'source': document['source']['sha256'] = '0'*64
    elif mutation == 'version': document['runtime']['version'] = '2.6.1'
    elif mutation == 'adapter': document['runtime']['adapter']['path'] = str(ROOT/'scripts/siril_starnet.py')
    elif mutation == 'runtime_drift': Path(document['runtime']['executable']['path']).write_bytes(b'drift')
    elif mutation == 'extra': document['extra'] = 1
    else: document['input_fits'] = str(source)
    with pytest.raises((ValueError, contract.ContractError)):
        bridge.validate_contract(document, source, document['runtime'], session, '060-stars')


@pytest.mark.parametrize('peak', [1.0003171, 1.01])
def test_above_one_reaches_native_unchanged_with_clipping_advisory(native, monkeypatch, peak):
    session, source, data, document = native
    prepared = Path(document['input_fits'])
    data[0, 0, 1] = peak
    for path in (source, prepared):
        write_fits(path, data)
    document['source']['sha256'] = contract.sha256_file(source)
    before = prepared.read_bytes()
    warning = 'Warning: linear input above 1 was clipped by the native tool.\n'

    def native_clip(argv, **options):
        assert Path(argv[2]).read_bytes() == before
        result = echo(argv, **options)
        write_fits(Path(argv[4]), np.minimum(data, 1))
        result.stdout = warning
        return result

    monkeypatch.setattr(bridge.subprocess, 'run', native_clip)
    work = bridge.validate_contract(document, source, document['runtime'], session, '060-stars')
    siril = Siril(source)
    evidence = bridge.process_starless(siril, document, work)
    assert prepared.read_bytes() == source.read_bytes() == before
    assert any('will clip 1 samples above one' in message for message in siril.messages)
    assert evidence['stdout'] == (work / 'native.log').read_text() == warning
    output = session / 'artifacts/starless.fit'
    shutil.copyfile(evidence['argv'][4], output)
    receipt = {'schema': bridge.RECEIPT_SCHEMA, 'status': 'verified', 'contract_sha256': 'a'*64,
               **{k: document[k] for k in ('source', 'runtime', 'stride')}, **evidence}
    bridge.validate_receipt(document, receipt, output, prepared)
    actual, _ = bridge.read_native_fits(output)
    np.testing.assert_array_equal(actual, np.minimum(data, 1))


@pytest.mark.parametrize('failure', ['nonfinite', 'integer', 'loaded_drift', 'geometry', 'extra_file', 'exit'])
def test_native_failure_retains_evidence_without_range_repair(native, monkeypatch, failure):
    session, source, data, document = native
    prepared = Path(document['input_fits']); work = Path(document['work_dir'])
    if failure in {'nonfinite', 'integer'}:
        altered = data.copy(); altered[:, 0, 0] = np.nan
        if failure == 'integer':
            from astropy.io import fits
            fits.PrimaryHDU((data * 10000).astype('int16')).writeto(prepared, overwrite=True)
        else:
            write_fits(prepared, altered)
    if failure == 'loaded_drift': write_fits(source, data + .001)
    def bad_cli(argv, **options):
        result = echo(argv, **options)
        if failure == 'geometry': write_fits(Path(argv[4]), data[:, :511])
        if failure == 'extra_file': (Path(argv[4]).parent / 'undeclared.fit').write_bytes(b'extra')
        if failure == 'exit': result.returncode = 7
        return result
    monkeypatch.setattr(bridge.subprocess, 'run', bad_cli)
    before = prepared.read_bytes()
    with pytest.raises(ValueError): bridge.process_starless(Siril(source), document, work)
    assert prepared.read_bytes() == before
    if failure in {'geometry', 'extra_file', 'exit'}:
        assert list(work.glob('handoff-*/input.fit'))
        assert (work/'native.log').read_text() == 'native raw log\n'


@pytest.mark.parametrize('version,adapter', [('2.5.0','siril_starnet.py'), ('2.6.1','siril_starnet.py'), ('2.6.2','siril_starnet_linear.py')])
def test_probe_routes_only_confirmed_native_version(native, monkeypatch, version, adapter):
    session, _, _, document = native
    executable = Path(document['runtime']['executable']['path'])
    siril = session/'siril-cli'; siril.write_bytes(b'siril')
    monkeypatch.setattr(tooling, 'discover_siril', lambda: siril)
    monkeypatch.setattr(tooling, '_configured_executable', lambda name: executable)
    monkeypatch.setattr(tooling, '_discover_starnet_model', lambda path: Path(document['runtime']['model']['path']))
    monkeypatch.setattr(tooling, '_tool_version', lambda path: (version if path == executable else '1.4.4', 'fixture'))
    monkeypatch.setattr(tooling, '_probe_starnet_python', lambda path: document['runtime']['python'])
    monkeypatch.setattr(tooling, '_probe_spcc_profiles', lambda path: {})
    monkeypatch.setattr(tooling, '_probe_sirilpy_bridge', lambda: {})
    runtime = tooling.probe_tools(offline=True)['tools']['starnet']
    assert runtime['compatible'] is True
    assert Path(runtime['adapter']['path']).name == adapter


def test_json_duplicates_and_historical_policy_are_fail_closed(tmp_path):
    path = tmp_path/'contract.json'; path.write_text('{"target_background":0.1,"target_background":0.25}')
    with pytest.raises(ValueError): bridge.load_json(path)
    previous = [p for p in sessions._EXECUTION_POLICY_HISTORY if p.get('guidance') == 'stage_evidence_limits_v4']
    assert len(previous) == 1 and previous[0]['starnet'] == 'pinned_python_bridge_v1'
    assert sessions.known_execution_policy(previous[0])
    assert not sessions.known_execution_policy({**previous[0], 'starnet': 'versioned_linear_bridge_v2'})
    lock = contract.load_json(ROOT/'references/starnet-linear-source.json')
    assert lock['legacy_adapter_sha256'] == contract.sha256_file(ROOT/'scripts/siril_starnet.py')
    assert lock['adapter_sha256'] == contract.sha256_file(ROOT/'scripts/siril_starnet_linear.py')


@pytest.mark.parametrize('adapter', ['siril_starnet_linear.py', 'siril_starnet.py', 'siril_auto_samples.py'])
def test_static_pyscript_requires_its_frozen_bridge(native, adapter):
    from deep_sky_siril_validation import _validate_pyscript
    session, source, _, document = native
    directory = session / 'reports/060-stars'; directory.mkdir(parents=True)
    path, receipt = directory/'contract.json', directory/'bridge.json'
    path.write_text(json.dumps(document))
    tokens = ['pyscript', str(ROOT/'scripts'/adapter), '--contract', str(path),
              '--contract-sha256', contract.sha256_file(path), '--expected-source', str(source), '--receipt', str(receipt)]
    policy = contract.load_json(ROOT/'references/command-policy.json')
    arguments = dict(protocol='stars.separate', session=session, source=source, policy=policy,
                     probe={'tools':{'starnet':document['runtime']}}, run_id='060-stars')
    if adapter == 'siril_starnet_linear.py':
        assert _validate_pyscript(tokens=tokens, **arguments) == receipt
    else:
        with pytest.raises(contract.ContractError): _validate_pyscript(tokens=tokens, **arguments)


def test_native_runtime_binds_legacy_validator_bytes(native, monkeypatch):
    runtime = native[3]['runtime']
    assert tooling.starnet_runtime_unchanged(runtime)
    monkeypatch.setattr(tooling, 'sha256_file', lambda path: '0'*64)
    assert not tooling.starnet_runtime_unchanged(runtime)


@pytest.mark.parametrize('correct', [True, False])
def test_native_input_is_the_ssf_declared_full_source(native, correct):
    from deep_sky_siril_processing import prepare_processing
    session, source, _, document = native
    directory = session/'reports/060-stars'; directory.mkdir(parents=True)
    full = Path(document['input_fits'])
    primary, layer = session/'artifacts/starless.fit', session/'artifacts/layer.fit'
    if not correct: document['input_fits'] = str(session/'artifacts/other.fit')
    path, receipt = directory/'contract.json', directory/'bridge.json'
    path.write_text(json.dumps(document))
    text = (f'load "{source}"\nfmul 1\nsave "{full.with_suffix("")}" -chksum\nload "{source}"\nfmul 1\n'
            f'pyscript "{ROOT}/scripts/siril_starnet_linear.py" --contract "{path}" '
            f'--contract-sha256 {contract.sha256_file(path)} --expected-source "{source}" --receipt "{receipt}"\n'
            f'save "{primary.with_suffix("")}" -chksum\nclose\n'
            'pm "$artifacts/full$ - $artifacts/starless$"\n'
            f'save "{layer.with_suffix("")}" -chksum\nclose\n')
    args = (session, {'input':{'path':str(source)},'context':{'input_state':'linear'}},
            'stars.separate', source, primary, text, {}, None, None, None, None)
    if correct:
        assert prepare_processing(*args,run_id='060-stars')['star_separation']['full_source'] == 'artifacts/full.fit'
    else:
        with pytest.raises(contract.ContractError,match='SSF declared full-source'):
            prepare_processing(*args,run_id='060-stars')

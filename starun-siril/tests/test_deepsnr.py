"""DeepSNR priority, immutable handoffs, explicit fallback and native smoke tests."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from astropy.io import fits

import deep_sky_siril_contract as contract
import deep_sky_siril_denoise as denoise
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_tooling as tooling
import siril_deepsnr as bridge
from .test_linear_denoise import case, fallback_request
from .test_stage5_restoration import WCS
from .test_xisf_metrics import xisf
from . import test_deep_sky_siril as support

ROOT = Path(__file__).parents[1]


@pytest.fixture
def deep_case(case, monkeypatch):
    session, source, payload, path, request = case
    y, x = np.indices((512, 512))
    data = .04+.04*np.exp(-((x-230)/4)**2-((y-240)/110)**2)
    data += np.random.default_rng(14).normal(0, .001, data.shape)
    fits.writeto(source, data.astype('float32'), fits.Header(WCS), overwrite=True)
    request['source']['sha256'] = contract.sha256_file(source)
    region_path = session/request['regions']['path']
    regions = contract.load_json(region_path)
    regions['reference'].update(sha256=request['source']['sha256'], width=512, height=512)
    contract.atomic_write_json(region_path, regions)
    request['regions'] = denoise.record(session, region_path)
    executable = source.parent/'deepsnr'
    executable.write_text('#!/bin/sh\nexit 0\n'); executable.chmod(0o755)
    model = source.parent/'DeepSNR_weights_v2.onnx'; model.write_bytes(b'synthetic model')
    info = {'product': 'deepsnr', 'version': '1.3.2', 'features': {'linear_mtf': {'supported': True}},
            'models': [{'id': 2, 'color_spaces': ['rgb', 'grayscale']}]}
    runtime = {'compatible': True, 'version': '1.3.2', 'supported_version': '1.3.2',
        'executable': contract.fingerprint(executable), 'model': contract.fingerprint_resource(model, kind='file'),
        'adapter': contract.fingerprint(ROOT/'scripts/siril_deepsnr.py'),
        'capabilities': info, 'capabilities_sha256': contract.stable_hash(info), 'reasons': []}
    probe = session/'reports/tool-probe.json'; contract.atomic_write_json(probe, {'tools': {'deepsnr': runtime}})
    payload['tool_probe'] = {'path': 'reports/tool-probe.json', 'sha256': contract.sha256_file(probe)}
    request.update(schema=denoise.SCHEMA_V2, backend='deepsnr', parameters={'modulation': .4,
        'linear_stretch': {'shadows_clipping': -2.8, 'target_background': .25}})
    request.pop('fallback')
    contract.atomic_write_json(path, request)
    (session/'session.json').write_text(json.dumps(payload))
    return session, source, payload, path, request, runtime


def prepare(deep_case, run_id='050-deepsnr'):
    session, source, payload, path, _, runtime = deep_case
    assets = denoise.generate(session, path, run_id)
    metadata = processing.prepare_processing(session, payload, 'restoration.denoise', source,
        Path(assets['primary_output']), Path(assets['script']).read_text(), {'tools': {'deepsnr': runtime}},
        None, None, None, None, run_id=run_id)
    return assets, metadata


def test_deepsnr_generates_one_pinned_bridge_and_bound_parameters(deep_case):
    assets, metadata = prepare(deep_case)
    rows = processing.script_commands(Path(assets['script']).read_text())
    assert [r[0] for r in rows].count('pyscript') == 1
    assert not any(r[0] == 'denoise' for r in rows)
    assert metadata['linear_denoise']['backend'] == 'deepsnr'
    doc = contract.load_json(deep_case[0]/metadata['linear_denoise']['contract']['path'])
    argv = bridge.build_argv(doc, Path(doc['work_dir']))
    assert argv[7:12] == ['--linear', '--model', '2', '--stride', '480']
    assert not any(Path(p).exists() for p in assets['expected'])


def test_replay_accepts_only_the_unchanged_original_adapter_location(deep_case,tmp_path):
    session,source,_,_,request,runtime=deep_case
    frozen=copy.deepcopy(runtime)
    adapter=tmp_path/'original-skill/scripts/siril_deepsnr.py';adapter.parent.mkdir(parents=True)
    adapter.write_bytes((ROOT/'scripts/siril_deepsnr.py').read_bytes())
    frozen['adapter']=contract.fingerprint(adapter)
    document={'schema':bridge.CONTRACT_SCHEMA,'source':{'path':str(source),**contract.fingerprint(source,include_path=False)},
              'runtime':frozen,'parameters':request['parameters'],'work_dir':'runtime/deepsnr/050-history'}
    assert bridge.validate_contract(document,source,frozen,session,'050-history',replaying=True)==session/'runtime/deepsnr/050-history'
    with pytest.raises(ValueError,match='runtime'):
        bridge.validate_contract(document,source,frozen,session,'050-history')
    adapter.write_bytes(adapter.read_bytes()+b'\n# changed\n')
    with pytest.raises(ValueError,match='runtime'):
        bridge.validate_contract(document,source,frozen,session,'050-history',replaying=True)


@pytest.mark.parametrize('change', ['extra_stat', 'stat_before_bridge', 'missing_stat'])
def test_deepsnr_requires_the_restricted_scientific_sequence(deep_case, change):
    assets, _ = prepare(deep_case)
    rows = processing.script_commands(Path(assets['script']).read_text())
    index = next(i for i, row in enumerate(rows) if row[0] == 'stat')
    if change == 'extra_stat':
        rows.insert(index, ['stat', 'main'])
    elif change == 'stat_before_bridge':
        rows[index-1], rows[index] = rows[index], rows[index-1]
    else:
        rows.pop(index)
    with pytest.raises(contract.ContractError, match='in that order'):
        processing.prepare_restoration(deep_case[0], deep_case[1], Path(assets['primary_output']),
                                       rows, 'restoration.denoise')


@pytest.mark.parametrize('change', ['parameters', 'schema', 'fallback', 'runtime', 'source', 'geometry', 'nonfinite', 'display'])
def test_invalid_deepsnr_evidence_never_silently_falls_back(deep_case, change):
    session, source, payload, path, request, runtime = deep_case
    if change == 'parameters': request['parameters'].pop('modulation')
    elif change == 'schema': request['schema'] = denoise.SCHEMA
    elif change == 'fallback': request['fallback'] = {'reason': 'Not permitted', 'evidence': []}
    elif change == 'runtime': Path(runtime['model']['path']).write_bytes(b'changed')
    elif change in {'source', 'geometry', 'nonfinite'}:
        data = np.zeros((511, 512) if change == 'geometry' else (512, 512), np.float32)
        if change == 'nonfinite': data[0, 0] = np.nan
        fits.writeto(source, data, overwrite=True)
        if change != 'source': request['source']['sha256'] = contract.sha256_file(source)
    elif change == 'display':
        assets, _ = prepare(deep_case)
        script = Path(assets['script']).read_text().replace('mtf 0 0.14999999999999999 1', 'mtf 0 0.2 1', 1)
        with pytest.raises((ValueError, contract.ContractError)):
            processing.prepare_processing(session, payload, 'restoration.denoise', source,
                Path(assets['primary_output']), script, {}, None, None, None, None, run_id='050-deepsnr')
        return
    contract.atomic_write_json(path, request)
    with pytest.raises((ValueError, contract.ContractError)):
        denoise.generate(session, path, '050-invalid')
    assert not list((session/'artifacts').iterdir())


def test_nlbayes_requires_same_source_actual_fallback_evidence(deep_case):
    session, _, payload, path, request, _ = deep_case
    request.update(backend='nlbayes', parameters={'modulation': .2, 'cosmetic': False})
    contract.atomic_write_json(path, request)
    with pytest.raises(contract.ContractError, match='fallback'):
        denoise.request_binding(session, payload, path)
    fallback_request(session, request)
    contract.atomic_write_json(path, request)
    assert denoise.request_binding(session, payload, path)[3][0] == 'denoise'
    assessment = session/'reports/deepsnr-applicability.json'
    doc = contract.load_json(assessment); doc['source']['sha256'] = '0'*64
    contract.atomic_write_json(assessment, doc)
    request['fallback']['evidence'] = [denoise.record(session, assessment)]
    contract.atomic_write_json(path, request)
    with pytest.raises(contract.ContractError, match='Fallback needs'):
        denoise.request_binding(session, payload, path)


def test_unavailable_frozen_probe_authorizes_explicit_nlbayes_fallback(deep_case):
    session, _, payload, path, request, runtime = deep_case
    runtime['compatible'] = False
    probe = session/'reports/tool-probe.json'
    contract.atomic_write_json(probe, {'tools': {'deepsnr': runtime}})
    payload['tool_probe']['sha256'] = contract.sha256_file(probe)
    request.update(backend='nlbayes', parameters={'modulation': .2, 'cosmetic': False},
        fallback={'reason': 'DeepSNR was unavailable at initialization', 'evidence': [denoise.record(session, probe)]})
    contract.atomic_write_json(path, request)
    assert denoise.request_binding(session, payload, path)[3][0] == 'denoise'


def test_historical_v1_binding_keeps_its_original_helper_and_cannot_write(case):
    session, source, payload, path, request = case
    payload['execution_policy'] = dict(sessions._PRE_DEEPSNR_EXECUTION_POLICY)
    contract.atomic_write_json(session/'session.json', payload)
    request.update(schema=denoise.SCHEMA)
    request.pop('backend'); request.pop('fallback')
    contract.atomic_write_json(path, request)
    _, binding, _, _ = denoise.request_binding(session, payload, path, verify_parent=False)
    snapshot = contract.load_json(ROOT/'tests/fixtures/deepsnr-prior.json')
    assert binding['helper'] == snapshot['documents']['scripts/deep_sky_siril_denoise.py']
    with pytest.raises(contract.ContractError, match='implementation'):
        denoise.measure(session, source, session/'artifacts/unwritten.fit', binding, write=True)


def test_zero_modulation_skips_without_runtime_or_identity_run(deep_case):
    session, _, _, path, request, _ = deep_case
    request['parameters']['modulation'] = 0
    contract.atomic_write_json(path, request)
    assert denoise.generate(session, path, '050-skip')['status'] == 'skipped'
    assert not list((session/'scripts').iterdir())


def test_deepsnr_does_not_accumulate_after_an_accepted_denoise(deep_case, monkeypatch):
    session, _, payload, path, _, _ = deep_case
    monkeypatch.setattr(processing, 'source_state', lambda *args: {'domain': 'linear',
        'run': {'id': 'prior', 'protocol': 'restoration.denoise'}})
    with pytest.raises(contract.ContractError, match='accumulate'):
        denoise.request_binding(session, payload, path)


@pytest.mark.parametrize('output', [None, '{broken', '[]', '{"features": null, "models": null}',
    '{"product":"deepsnr","version":"1.3.2","features":{"linear_mtf":{"supported":true}},"models":[{"id":2,"color_spaces":[{}]}]}'])
def test_probe_failures_remain_optional_capability_failures(tmp_path, monkeypatch, output):
    executable = tmp_path/'deepsnr'; executable.write_text('synthetic'); executable.chmod(0o755)
    monkeypatch.setenv('DEEP_SKY_SIRIL_DEEPSNR_BIN', str(executable))
    def invoke(*args, **kwargs):
        if output is None: raise OSError('Native runtime unavailable')
        return subprocess.CompletedProcess(args, 0, output)
    monkeypatch.setattr(tooling.subprocess, 'run', invoke)
    assert not tooling.probe_deepsnr()['compatible']


@pytest.mark.parametrize('directory', [False, True])
def test_probe_discovers_both_model_formats_and_locks_runtime(tmp_path, monkeypatch, directory):
    executable = tmp_path/'deepsnr'; executable.write_text('synthetic'); executable.chmod(0o755)
    model = tmp_path/('DeepSNR_weights_v2.mlpackage' if directory else 'DeepSNR_weights_v2.onnx')
    if directory:
        model.mkdir(); (model/'weights').write_text('model')
    else: model.write_text('model')
    monkeypatch.setenv('DEEP_SKY_SIRIL_DEEPSNR_BIN', str(executable))
    monkeypatch.delenv('DEEP_SKY_SIRIL_DEEPSNR_MODEL', raising=False)
    info = {'product': 'deepsnr', 'version': '1.3.2', 'features': {'linear_mtf': {'supported': True}},
            'models': [{'id': 2, 'color_spaces': ['grayscale', 'rgb']}]}
    monkeypatch.setattr(tooling.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(info)))
    runtime = tooling.probe_deepsnr()
    assert runtime['compatible'] and tooling.deepsnr_runtime_unchanged(runtime)
    if directory: (model/'weights').write_text('drift')
    else: model.write_text('drift')
    assert not tooling.deepsnr_runtime_unchanged(runtime)
    info['version'] = '1.3.1'
    assert not tooling.probe_deepsnr()['compatible']
    monkeypatch.setenv('DEEP_SKY_SIRIL_DEEPSNR_BIN', str(tmp_path/'missing'))
    assert 'deepsnr_unavailable' in tooling.probe_deepsnr()['reasons']


def test_pre_deepsnr_policy_documents_and_command_bytes_remain_replayable():
    from ._policy_pins import assert_pinned_history
    snapshot = contract.load_json(ROOT/'tests/fixtures/deepsnr-prior.json')
    assert_pinned_history(snapshot, session_constant=sessions._PRE_DEEPSNR_EXECUTION_POLICY, command_mode='hash')
    import deep_sky_siril_stretch_v2 as stretch
    assert stretch._PRE_DEEPSNR_BINDINGS['implementation'] == snapshot['implementation']


def test_pre_deepsnr_session_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    replay_and_finalize_without_migration(tmp_path, monkeypatch, ROOT/'tests/fixtures/deepsnr-prior.json')


@pytest.mark.parametrize('failure', [False, True])
def test_real_core_run_review_and_failed_output_isolation(tmp_path, monkeypatch, failure):
    import deep_sky_siril_core as core
    runtime, binary = tooling.probe_deepsnr(), tooling.discover_siril()
    if binary is None or not runtime['compatible']:
        pytest.skip('Installed Siril/DeepSNR runtime unavailable')
    native_run = subprocess.run
    original_probe = support._fake_probe
    def complete_probe(_siril, **kwargs):
        probe = original_probe(binary, **kwargs)
        probe['tools']['deepsnr'] = runtime
        return probe
    monkeypatch.setattr(support, '_fake_probe', complete_probe)
    def synthetic_source(path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        y, x = np.indices((512, 512))
        data = .04+.03*np.exp(-((x-230)/5)**2-((y-240)/110)**2)
        data += np.random.default_rng(12).normal(0, .001, data.shape)
        fits.writeto(path, data.astype('float32'), fits.Header(WCS), overwrite=True)
    monkeypatch.setattr(support, '_write_fits', synthetic_source)
    session, source, _ = support._session(tmp_path/'native session (test)', monkeypatch,
        input_state='linear', channel_mode='mono', width=512, height=512)
    with monkeypatch.context() as scoped:
        inspected, direct, diagnostic = support._input_inspect_run(session, source, scoped)
        support._review(session, inspected['id'], diagnostic, parent_preview=direct)
    region_path = session/'reports/deep-regions.json'
    contract.atomic_write_json(region_path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 512, 'height': 512},
        'regions': [{'id': 'sky', 'role': 'background', 'rect': [20, 20, 80, 80]},
                    {'id': 'subject', 'role': 'faint', 'rect': [200, 200, 280, 280]}]})
    path = session/'reports/deep-request.json'
    contract.atomic_write_json(path, {'schema': denoise.SCHEMA_V2, 'backend': 'deepsnr',
        'source': {'path': '@input', 'sha256': contract.sha256_file(source)},
        'regions': denoise.record(session, region_path), 'display_mtf': [0, .15, 1],
        'decision': 'trial', 'reason': 'Controlled native integration test',
        'parameters': {'modulation': .4, 'linear_stretch': {'shadows_clipping': -2.8, 'target_background': .25}},
        'parameter_reason': 'Explicit synthetic modulation',
        'manual_lookup': {'status': 'not_needed', 'reason': 'Documented bridge', 'evidence': []}})
    assets = denoise.generate(session, path, '050-deepsnr')
    def invoke(argv, **kwargs):
        result = native_run(argv, **kwargs)
        if failure and '--script='+assets['script'] in argv:
            return subprocess.CompletedProcess(argv, 1, result.stdout+'\nControlled failure after output creation\n', result.stderr)
        return result
    monkeypatch.setattr(core.subprocess, 'run', invoke)
    result = core.run_script(session, protocol='restoration.denoise', script_value=assets['script'],
        source_value=source, primary_output_value=assets['primary_output'], expected_values=assets['expected'], timeout=120)
    if failure:
        assert result['status'] == 'failed' and result['primary_output'] is None and result['outputs'] == []
        assert result['rejected_outputs'] and all(not Path(p).exists() for p in assets['expected'] if Path(p).suffix != '.json')
    else:
        assert result['status'] == 'success', result
        assert core.check_review(session, result['id'], None)['review']['denoise_assessment']['noise_benefit'] is None


@pytest.mark.parametrize('container,channels,sample', [('fits', 1, 'Float32'), ('fits', 3, 'UInt16'), ('xisf', 3, 'Float32')])
def test_real_siril_deepsnr_bridge_and_immutable_mixing(deep_case, container, channels, sample):
    session, source, payload, path, request, _ = deep_case
    runtime = tooling.probe_deepsnr(); binary = tooling.discover_siril()
    if binary is None or not runtime['compatible']:
        pytest.skip('Installed Siril/DeepSNR 1.3.2 model 2 unavailable')
    values, _ = denoise.read_scientific_pixels(source)
    values = np.concatenate([values*(1+.05*c) for c in range(channels)])
    if sample == 'UInt16': values = np.rint(values*65535).astype('uint16')
    else:
        values[:, 0, 0] = -.0001
        values[:, 10, 10] = 1.01
    if container == 'xisf':
        source = source.with_suffix('.xisf')
        xisf(source, values[:, ::-1].astype('float32'), extra=True,
             keywords=[(k, repr(v) if isinstance(v, str) else v) for k, v in WCS.items()])
    else: fits.writeto(source, values.squeeze(), fits.Header(WCS), overwrite=True)
    payload['input']['path'] = str(source)
    request['source']['sha256'] = contract.sha256_file(source)
    region_path = session/request['regions']['path']; regions = contract.load_json(region_path)
    regions['reference']['sha256'] = request['source']['sha256']; contract.atomic_write_json(region_path, regions)
    request['regions'] = denoise.record(session, region_path)
    contract.atomic_write_json(path, request)
    probe = session/'reports/tool-probe.json'; contract.atomic_write_json(probe, {'tools': {'deepsnr': runtime}})
    payload['tool_probe']['sha256'] = contract.sha256_file(probe)
    contract.atomic_write_json(session/'session.json', payload)
    assets, metadata = prepare((session, source, payload, path, request, runtime))
    config = session/'siril.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nauto_update_scripts=false\nuse_spcc_repository=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={session}', '--offline',
        '--script='+assets['script']], capture_output=True, text=True, timeout=180,
        env=tooling._subprocess_environment('siril_runtime'))
    assert result.returncode == 0, (result.stdout+result.stderr)[-10000:]
    primary = Path(assets['primary_output'])
    checks = processing.validate_processing_outputs(session, source, primary, metadata,
        execution_policy=contract.EXECUTION_POLICY, write_diagnostics=True)
    assert checks['restoration_validation']['wcs'] == 'preserved'
    proof = bridge.validate_receipt(session, source, primary, metadata['linear_denoise'])
    assert proof['status'] == 'verified'
    assert proof['input_above_one_count'] == (0 if sample == 'UInt16' else channels)
    assert proof['input_above_one_region'] == (None if sample == 'UInt16' else
        {'coordinate_system': 'display_top_down', 'rect': [10, 501, 11, 502]})
    before = {p: p.read_bytes() for p in [primary, session/metadata['linear_denoise']['bridge_receipt']]}
    processing.validate_processing_outputs(session, source, primary, metadata,
        execution_policy=contract.EXECUTION_POLICY, write_diagnostics=False)
    assert all(p.read_bytes() == data for p, data in before.items())
    with fits.open(primary, mode='update') as hdus: hdus[0].data.flat[30] += .001
    with pytest.raises(ValueError, match='precision'):
        bridge.validate_receipt(session, source, primary, metadata['linear_denoise'])

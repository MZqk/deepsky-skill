"""Typed trial settings, normal Siril serialization and immutable prior evidence."""
import configparser
import copy
import io
import json
import hashlib
from pathlib import Path
import sys

import numpy as np
import pytest

import deep_sky_siril_color as color
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
from . import test_color_evaluation as evaluation
from . import test_recomposition_proposals as recomposition
from .test_color_evaluation import ColorNative, case, scene
from .test_deep_sky_siril import _session_bytes, _write_ssf_provenance
from .test_local_hdr import LocalNative
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/color-config-prior.json').read_text())
BASE = ('[core]\nforce_16bit=false\ncheck_updates=false\nfits_save_icc=true\n'
        '[gui]\nicc_autoassignment=0\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
KEYS = [('core', 'force_16bit'), ('core', 'check_updates'), ('core', 'fits_save_icc'),
        ('gui', 'icc_autoassignment'), ('gui', 'use_scripts_repository'), ('gui', 'use_spcc_repository')]


def rewrite(raw):
    parser = configparser.ConfigParser(interpolation=None); parser.read_string(raw)
    text = '# Siril serialized configuration\r\n[extra]\npercent=100% literal\n'
    for section in reversed(parser.sections()):
        text += '['+section+']\r\n'
        for key, value in reversed(list(parser[section].items())):
            value = {'false': 'OFF', 'true': 'YES'}.get(value, value)
            text += f' {key} = {value} \r\n'
    return text


def changed(raw, section, key, issue):
    parser = configparser.ConfigParser(interpolation=None); parser.read_string(raw)
    if issue == 'missing': parser.remove_option(section, key)
    elif issue == 'default':
        parser['DEFAULT'][key] = parser.get(section, key); parser.remove_option(section, key)
    elif issue == 'value': parser.set(section, key, '1' if key == 'icc_autoassignment' else str(not parser.getboolean(section, key)))
    elif issue == 'type': parser.set(section, key, '0.0' if key == 'icc_autoassignment' else '2')
    out = io.StringIO(); parser.write(out); text = out.getvalue()
    if issue == 'duplicate':
        text = text.replace('['+section+']', '['+section+']\n'+key+'='+parser.get(section, key), 1)
    return text


class SerializingNative(ColorNative):
    def __init__(self, transform=rewrite):
        super().__init__(); self.transform, self.before = transform, {}

    def __call__(self, argv, **kwargs):
        path = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--initfile=')))
        raw = path.read_text(); self.before[path] = path.read_bytes()
        result = super().__call__(argv, **kwargs)
        path.write_text(self.transform(raw))
        return result


@pytest.fixture
def serializer(scene, monkeypatch):
    native = SerializingNative(); monkeypatch.setattr(color.subprocess, 'run', native)
    return native


def test_serialization_binds_the_final_file_and_preserves_formal_replay(scene, serializer):
    result = evaluation.solve(scene); report = contract.load_json(Path(result['report']))
    binding = report['bindings']['configuration']; path = scene[0]/binding['path']
    assert result['status'] == 'solved' and serializer.before[path] != path.read_bytes()
    assert binding == color.record(scene[0], path) == color.configuration_binding(scene[0], path)
    run, args = evaluation.execute(scene, result)
    assert core.check_review(scene[0], run['id'], evaluation.save_review(scene[0], run, evaluation.review(scene[0], run)))['status'] == 'review_valid'
    before = _session_bytes(scene[0]); count = len(serializer.calls)
    assert core.run_script(scene[0], **args)['replayed']
    assert len(serializer.calls) == count and _session_bytes(scene[0]) == before


@pytest.mark.parametrize('section,key', KEYS)
@pytest.mark.parametrize('issue', ['missing', 'default', 'duplicate', 'value', 'type'])
def test_all_six_settings_require_explicit_valid_values(tmp_path, section, key, issue):
    reports = tmp_path/'reports'; reports.mkdir(); path = reports/'trial.ini'
    path.write_text(changed(BASE, section, key, issue))
    with pytest.raises(contract.ContractError, match='(?i)color trial configuration'):
        color.configuration_binding(tmp_path, path)


@pytest.mark.parametrize('section,key', KEYS)
def test_changed_settings_after_native_exit_do_not_publish_a_solved_report(scene, serializer, section, key):
    serializer.transform = lambda raw: changed(raw, section, key, 'value')
    with pytest.raises(contract.ContractError, match='Color trial configuration changed'):
        evaluation.solve(scene)
    directory = scene[0]/'reports/085-color'
    assert len(serializer.calls) == 1 and (directory/'trial.log').exists()
    assert list((directory/'trial/artifacts').glob('*.fit'))
    assert not (directory/'color-parameters.json').exists()


@pytest.mark.parametrize('section,key', KEYS)
def test_changed_settings_before_launch_never_execute(scene, serializer, monkeypatch, section, key):
    original = color.script_text
    def corrupt(root, *args, **kwargs):
        result = original(root, *args, **kwargs)
        config = Path(root).parent/'trial.ini'
        config.write_text(changed(config.read_text(), section, key, 'value'))
        return result
    monkeypatch.setattr(color, 'script_text', corrupt)
    with pytest.raises(contract.ContractError, match='Color trial configuration changed'):
        evaluation.solve(scene)
    assert not serializer.calls


def test_failure_keeps_its_directory_and_same_session_retries_with_a_new_id(scene, serializer):
    serializer.transform = lambda raw: changed(raw, 'core', 'force_16bit', 'value')
    with pytest.raises(contract.ContractError): evaluation.solve(scene)
    before = _session_bytes(scene[0]); session_bytes = (scene[0]/'session.json').read_bytes()
    serializer.transform = rewrite
    with pytest.raises(contract.ContractError, match='fresh run ID'): evaluation.solve(scene)
    assert evaluation.solve(scene, run_id='087-retry')['status'] == 'solved'
    assert (scene[0]/'session.json').read_bytes() == session_bytes
    assert all((scene[0]/name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize('after_execution', [False, True])
def test_frozen_configuration_rejects_even_unrelated_later_changes(scene, serializer, after_execution):
    result = evaluation.solve(scene)
    if after_execution:
        run, args = evaluation.execute(scene, result)
    report = contract.load_json(Path(result['report']))
    config = scene[0]/report['bindings']['configuration']['path']
    config.write_bytes(config.read_bytes()+b'\n# later change\n')
    count = len(serializer.calls)
    with pytest.raises(contract.ContractError):
        core.run_script(scene[0], **args) if after_execution else core.run_script(
            scene[0], **evaluation.assets(scene, result), validate_only=True)
    assert len(serializer.calls) == count


def test_end_of_solve_still_checks_the_frozen_file(scene, serializer, monkeypatch):
    original = color.save_region_panels
    def mutate(*args, **kwargs):
        original(*args, **kwargs)
        config = scene[0]/'reports/085-color/trial.ini'
        config.write_bytes(config.read_bytes()+b'\n# change during diagnostics\n')
    monkeypatch.setattr(color, 'save_region_panels', mutate)
    with pytest.raises(contract.ContractError, match='evidence changed during trials'):
        evaluation.solve(scene)
    assert not (scene[0]/'reports/085-color/color-parameters.json').exists()


def test_masked_revision_and_zero_target_keep_their_contracts(scene, serializer):
    zero = evaluation.solve(scene, target_gain=0, strength=None, goal=None, parameter_reason=None)
    assert zero['status'] == 'skipped' and not serializer.calls
    assert not (Path(zero['report']).parent/'trial.ini').exists()
    first = evaluation.solve(scene, run_id='087-unmasked')
    masked = evaluation.solve(scene, run_id='088-masked', mask_mode='luminance', previous=first['report'],
                              observation='Inspect luminance protection after the unmasked comparison')
    assert masked['status'] == 'solved' and len(serializer.calls) == 2
    assert evaluation.execute(scene, masked)[0]['status'] == 'success'


def test_exact_prior_bindings_and_unchanged_policy_are_registered(tmp_path):
    # prior=color-config-prior.json: guidance, chroma_denoise, color_preservation,
    # recomposition_geometry, linear_denoise_backend, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': PRIOR['execution_policy']}))
    assert color.helper_matches(tmp_path, PRIOR['helper'])
    assert not color.helper_matches(tmp_path, {**PRIOR['helper'], 'size': PRIOR['helper']['size']+1})
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation'])
    mixed['deep_sky_siril_color.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_color.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    for name, binding in PRIOR['references'].items():
        assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {'path': name, **binding})
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': {**PRIOR['execution_policy'], 'unknown': 'unsupported'}}))
    assert not color.helper_matches(tmp_path, PRIOR['helper'])


def test_new_session_rejects_prior_helper_without_rewriting_it(scene):
    result = evaluation.solve(scene); path = Path(result['report']); report = contract.load_json(path)
    report['bindings']['helper'] = PRIOR['helper']; contract.atomic_write_json(path, report)
    before = path.read_bytes()
    with pytest.raises(contract.ContractError, match='implementation'):
        color.validate_parameter_binding(scene[0], scene[1], {**result['parameters'], 'parameters': str(path)})
    assert path.read_bytes() == before


def test_prior_color_receipt_replays_and_finalizes_with_its_original_evidence(scene, case, monkeypatch):
    session = scene[0]
    from ._legacy_color import legacy_color_producer
    with legacy_color_producer(monkeypatch):
        result = evaluation.solve(scene); path = Path(result['report']); report = contract.load_json(path)
        report['bindings']['helper'] = PRIOR['helper']; contract.atomic_write_json(path, report)
        run, args = evaluation.execute(scene, result)
    _freeze_historical_evidence(session, snapshot={**PRIOR, 'documents': PRIOR['references']}, run_id=run['id'])
    evaluation.save_review(session, run, evaluation.review(session, run))
    monkeypatch.setattr(core.subprocess, 'run', case[-1])
    source = args['primary_output_value']
    from .test_recomposition_geometry import legacy_producer
    with legacy_producer(monkeypatch):
        prepared = recomposition.reduction.prepare(session, source, '089-color-stars')
        request_path, request = case[2], case[3]
        regions = session/request['regions']['path']; doc = contract.load_json(regions)
        doc['reference']['sha256'] = contract.sha256_file(source); contract.atomic_write_json(regions, doc)
        request.update(source=prepared['source'], prepared=prepared['prepared'], regions=color.record(session, regions))
        contract.atomic_write_json(request_path, request)
        next_case = (session, source, *case[2:])
        combined = recomposition.execute(next_case, recomposition.generate(next_case))
        assert combined['status'] == 'success'
        stars_review = session/'reviews/090-recompose.json'
        contract.atomic_write_json(stars_review, recomposition.accepted_draft(session, combined))
        monkeypatch.setattr(core, '_decode_scientific_output', lambda p, **kwargs:
            {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(p, include_path=False)}})
        script, final = evaluation.support._delivery_script(session, session/combined['primary_output'])
        delivered = core.run_script(session, protocol='delivery.render', script_value=script,
            source_value=session/combined['primary_output'], expected_values=[final, final.with_suffix('.fit')], timeout=30)
        assert delivered['status'] == 'success'
        final_review = evaluation.support._review(session, delivered['id'], final,
            parent_preview=session/next(row['path'] for row in combined['outputs'] if row['path'].endswith('.jpg')))
    before = _session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert _session_bytes(session) == before
    selection = evaluation.support._selection(session, delivered['id'], final, final_review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert path.read_bytes() == before[str(path.relative_to(session))]
    for folder in ('runs', 'reviews'):
        for item in (session/folder).glob('*.json'):
            assert item.read_bytes() == before[str(item.relative_to(session))]


def test_prior_local_tone_helper_replays_but_unknown_helpers_fail(scene, monkeypatch):
    session, source, regions, _ = scene
    values = np.full((3, 64, 64), .02)
    values[:, 20:48, 20:48] = np.array([.4, .1, .1])[:, None, None]
    evaluation.rebind_fixture_parent(scene, values)
    monkeypatch.setattr(core.subprocess, 'run', LocalNative())
    primary = session/'artifacts/085-red.fit'
    text, outputs, _ = color.local_tone_script(session, source, primary,
        {'selector': 'red', 'midtones': .48, 'regions': str(regions)})
    proposal = {'schema': 'starun-siril.tone-proposal.v1', 'regions': color.record(session, regions),
        'goal': 'Reveal the remaining red subject transition', 'upstream_assessment': 'The accepted stretch retains the subject',
        'remaining_issue': 'The red transition remains weak in the synthetic fixture',
        'parameter_reason': 'One protected local-tone proposal based on the current source'}
    script = session/'scripts/085-red.ssf'; script.write_text('# tone-adjust: '+json.dumps(proposal)+'\n'+text)
    _write_ssf_provenance(session, script, 'tone.adjust')
    args = dict(protocol='tone.adjust', script_value=script, source_value=source,
                primary_output_value=primary, expected_values=outputs, timeout=30)
    run = core.run_script(session, **args); assert run['status'] == 'success'
    run['local_tone']['helper'] = {'path': 'scripts/deep_sky_siril_color.py', **PRIOR['helper']}
    path = session/'runs/085-red.json'; contract.atomic_write_json(path, run)
    manifest = contract.load_json(session/'manifest.json')
    next(row for row in manifest['runs'] if row['id'] == run['id'])['receipt_sha256'] = contract.sha256_file(path)
    contract.atomic_write_json(session/'manifest.json', manifest)
    before = _session_bytes(session)
    with pytest.raises(contract.ContractError):
        core.run_script(session, **args)
    assert _session_bytes(session) == before
    with pytest.raises(contract.ContractError, match='Local tone helper changed'):
        color.validate_local_tone(session, source, primary, processing.script_commands(text),
            {'selector': 'red', 'midtones': .48, 'regions': str(regions)},
            recorded_helper={**run['local_tone']['helper'], 'sha256': '0'*64})
    invalid = {**run, 'local_tone': None}
    assert not sessions._receipt_bindings_unchanged(session, contract.load_json(session/'session.json'), invalid)


def test_native_default_solve_serializes_configuration(tmp_path, monkeypatch, capsys):
    from . import test_vivid_color as vivid
    if not vivid.BINARY.is_file(): pytest.skip('Compatible Siril unavailable')
    version, _ = vivid.tooling._tool_version(vivid.BINARY)
    parsed = vivid.tooling._version_tuple(version)
    assert parsed and vivid.tooling.SIRIL_MINIMUM <= parsed < vivid.tooling.SIRIL_OBSOLETE
    probe = vivid._fake_probe(vivid.BINARY); probe['tools']['siril_cli']['version'] = version
    monkeypatch.setattr(vivid, '_fake_probe', lambda *a, **k: copy.deepcopy(probe))
    session, source, regions = vivid.setup_session(tmp_path, monkeypatch, style='natural')
    original = color.subprocess.run; captures = []
    def tracked(argv, **kwargs):
        config = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--initfile=')))
        before = config.read_bytes()
        completed = original(argv, **kwargs)
        captures.append((config, before, config.read_bytes()))
        return completed
    monkeypatch.setattr(color.subprocess, 'run', tracked)
    original_source = contract.sha256_file(source)
    monkeypatch.setattr(sys, 'argv', ['deep_sky_siril_color.py', '--session', str(session), '--source', str(source),
        '--regions', str(regions), '--run-id', '088-config-smoke', '--target-gain', '0.08', '--strength', '0.3',
        '--saturation-protect-start', '.7', '--saturation-protect-end', '.95',
        '--goal', 'Validate the default color solve configuration lifecycle',
        '--parameter-reason', 'One explicit synthetic native smoke test', '--timeout', '60'])
    assert color.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'solved' and result['requires_visual_review'] is True
    report = contract.load_json(Path(result['report']))
    assert len(captures) == len(report['trials']) == 1
    config, before, after = captures[0]
    assert report['selected_parameters'] == {'D': .3, 'mask_mode': 'none', 'mask_policy': color.PRESERVATION,
        'saturation_protect_start': .7, 'saturation_protect_end': .95}
    assert report['bindings']['configuration'] == color.configuration_binding(session, config) == color.record(session, config)
    color.validate_parameter_binding(session, source, {**result['parameters'], 'parameters': result['report']})
    assert contract.sha256_file(source) == original_source
    summary = {'version': version, 'status': result['status'], 'native_color_calls': len(captures),
        'configuration_rewritten': before != after, 'before_sha256': hashlib.sha256(before).hexdigest(),
        'after_sha256': hashlib.sha256(after).hexdigest(), 'six_settings_valid': True, 'report': result['report']}
    (tmp_path/'native-color-smoke.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))

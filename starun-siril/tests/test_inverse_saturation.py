"""Optional native inverse saturation, immutable proposals and legacy replay."""
import colorsys
import json
from pathlib import Path
import sys

import numpy as np
import pytest

import deep_sky_siril_color as color
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch
from deep_sky_siril_artifacts import diagnose_siril_log
from . import test_color_evaluation as evaluation
from . import test_deep_sky_siril as support
from .test_color_evaluation import scene
from .test_recomposition_proposals import case
from .test_hdr_stretch import run_native
from .test_native_processing import write_fits
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence
from .test_vivid_color import setup_session
from ._policy_pins import replay_and_finalize_without_migration

ROOT = Path(__file__).parents[1]
HISTORY = ROOT/'references/color-curve-variants-history.json'


def inverse(scene, run_id='085-inverse', **changes):
    return evaluation.solve(scene, run_id, **({'curve': 'invght', 'sp': .5, 'hp': .7} | changes))


def declaration(**changes):
    return {'D': 1.2, 'curve': 'invght', 'SP': .5, 'HP': .7, 'mask_mode': 'none',
            'mask_policy': color.PRESERVATION, 'saturation_protect_start': .7,
            'saturation_protect_end': .95, 'parameters': 'reports/color-parameters.json', **changes}


@pytest.mark.parametrize('sp,hp', [(0, 0), (0, 1), (.5, .5), (.5, .7), (1, 1)])
def test_inverse_native_parameter_boundaries(sp, hp):
    parameters = color.curve_parameters('invght', sp, hp)
    tokens = color.curve_operator(1.2, parameters)
    assert tokens == ['invght', '-D=1.2', '-B=0', '-LP=0', f'-SP={sp}', f'-HP={hp}', '-sat', '-clipmode=rgbblend']
    assert color.validate_curve_operator(tokens) == {'D': 1.2, **parameters}
    assert color.curve_declaration('# color-curve: '+json.dumps(declaration(SP=sp, HP=hp)))['SP'] == sp
    processing.validate_parameters(tokens, 'color.finish')
    for protocol in ('stretch', 'tone.adjust', 'stars.recompose'):
        with pytest.raises(contract.ContractError, match='restricted'):
            processing.validate_parameters(tokens, protocol)


@pytest.mark.parametrize('changes', [
    {'D': True}, {'D': 0}, {'D': 10.01}, {'D': float('nan')},
    {'SP': None}, {'HP': None}, {'SP': True}, {'HP': False}, {'SP': '0.5'},
    {'SP': -.01}, {'SP': .8}, {'HP': 1.01}, {'HP': float('inf')},
    {'curve': 'ght'}, {'curve': 'inverse'}, {'curve': True}, {'B': 0}, {'LP': 0},
])
def test_inverse_declaration_rejects_invalid_or_unbound_fields(changes):
    with pytest.raises((contract.ContractError, ValueError)):
        color.curve_declaration('# color-curve: '+json.dumps(declaration(**changes)))


@pytest.mark.parametrize('index,value', [(0, 'ght'), (2, '-B=1'), (3, '-LP=.1'),
                                         (4, '-SP=nan'), (5, '-HP=.4'),
                                         (6, '-human'), (7, '-clipmode=clip')])
def test_inverse_command_rejects_other_native_options(index, value):
    tokens = color.curve_operator(1.2, color.curve_parameters('invght', .5, .7))
    tokens[index] = value
    with pytest.raises(contract.ContractError):
        color.validate_curve_operator(tokens)
    with pytest.raises(contract.ContractError):
        color.validate_curve_operator([*color.curve_operator(1.2, declaration()), 'R'])


def test_cli_forwards_inverse_options_without_adding_forward_fields(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(color, 'solve', lambda *args, **kwargs: calls.append(kwargs) or {'status': 'skipped'})
    base = ['color', '--session', '/session', '--source', 'parent.fit', '--regions', 'regions.json',
            '--run-id', '085-color', '--target-gain', '.1']
    monkeypatch.setattr(sys, 'argv', base+['--curve', 'invght', '--strength', '1.2', '--sp', '.5', '--hp', '.7'])
    assert color.main() == 0
    assert calls[-1] == {'target_gain': .1, 'curve': 'invght', 'strength': 1.2, 'sp': .5, 'hp': .7}
    monkeypatch.setattr(sys, 'argv', base)
    assert color.main() == 0 and calls[-1] == {'target_gain': .1}
    capsys.readouterr()


@pytest.mark.parametrize('changes', [{'sp': None}, {'hp': None}, {'sp': True}, {'hp': float('nan')},
                                      {'sp': .8}, {'strength': True}, {'strength': float('inf')},
                                      {'curve': 'ght'}, {'curve': 'other'}])
def test_invalid_inverse_proposal_never_creates_trial_evidence(scene, changes):
    with pytest.raises(contract.ContractError):
        inverse(scene, **changes)
    assert not scene[3].calls and not (scene[0]/'reports/085-inverse').exists()


def test_zero_inverse_target_needs_no_curve_numbers_or_trial(scene):
    result = inverse(scene, target_gain=0, strength=None, sp=None, hp=None,
                     saturation_protect_start=None, saturation_protect_end=None)
    report = contract.load_json(Path(result['report']))
    assert result['status'] == 'skipped' and report['proposal'] is None
    assert not report['trials'] and not scene[3].calls


@pytest.mark.parametrize('mask_mode', ['none', 'luminance'])
def test_inverse_trial_formal_review_and_replay(scene, mask_mode):
    initial = inverse(scene)
    result = (inverse(scene, '085-masked', mask_mode='luminance', previous=initial['report'],
                      observation='Inspect protection at the same inverse curve') if mask_mode == 'luminance' else initial)
    assert result['status'] == 'solved', result
    report = contract.load_json(Path(result['report']))
    assert len(report['trials']) == 1
    assert {key: report['proposal'][key] for key in color.CURVE_KEYS} == {'curve': 'invght', 'SP': .5, 'HP': .7}
    receipt, args = evaluation.execute(scene, result)
    chain = receipt['color_finish']
    assert len(chain) == 1 and chain[0]['command'] == 'invght'
    assert receipt['processing_checks']['color_curve']['trial_max_abs_error'] <= 1e-6
    binding = receipt.get('color_curve', receipt.get('local_saturation'))
    before, after = color.pixels(scene[1]), color.pixels(scene[0]/receipt['primary_output'])
    mask = color.pixels(scene[0]/binding['layers']['mask'])[0]
    np.testing.assert_array_equal(after[:, mask == 0], before[:, mask == 0])
    draft = evaluation.review(scene[0], receipt)
    draft['color_assessment']['regions'][0]['assessment'] = 'degraded'
    with pytest.raises(contract.ContractError):
        color.validate_assessment(scene[0], receipt, draft)
    draft['color_assessment']['regions'][0]['assessment'] = 'acceptable'
    evaluation.save_review(scene[0], receipt, draft)
    frozen = support._session_bytes(scene[0])
    assert core.run_script(scene[0], **args)['replayed']
    assert support._session_bytes(scene[0]) == frozen


@pytest.mark.parametrize('changes', [{'sp': .51}, {'hp': .71}, {'strength': .5},
                                      {'curve': 'ght', 'sp': None, 'hp': None}])
def test_first_luminance_mask_keeps_the_complete_inverse_curve(scene, changes):
    first = inverse(scene)
    calls = len(scene[3].calls)
    with pytest.raises(contract.ContractError, match='same parent/ROIs'):
        inverse(scene, '085-mask', mask_mode='luminance', previous=first['report'],
                observation='A mask must isolate its own contribution', **changes)
    assert len(scene[3].calls) == calls


def test_observed_same_parent_revision_can_select_inverse_after_forward(scene):
    first = evaluation.solve(scene)
    result = inverse(scene, previous=first['report'], observation='Compare the inverse low and mid saturation response')
    assert result['status'] == 'solved'
    report = contract.load_json(Path(result['report']))
    color.validate_evaluation_evidence(scene[0], scene[1], report)
    assert len(report['trials']) == 1 and report['previous']['path'] == Path(first['report']).relative_to(scene[0]).as_posix()


@pytest.mark.parametrize('key,value', [('D', .4321234567891), ('SP', .5000000000001), ('HP', .7000000000001)])
def test_formal_inverse_rejects_changes_beyond_twelve_significant_digits(scene, key, value):
    result = inverse(scene)
    args = evaluation.assets(scene, result)
    path = args['script_value']
    original = result['parameters'][key]
    path.write_text(path.read_text().replace(f'-{key}={original}', f'-{key}={value}'))
    support._write_ssf_provenance(scene[0], path, 'color.finish')
    with pytest.raises(contract.ContractError, match='strength or curve'):
        core.run_script(scene[0], validate_only=True, **args)


@pytest.mark.parametrize('key,value', [('curve', 'ght'), ('SP', .51), ('HP', .71)])
def test_inverse_report_cannot_drift_from_its_proposal(scene, key, value):
    result = inverse(scene)
    report = contract.load_json(Path(result['report']))
    report['proposal'][key] = value
    with pytest.raises(contract.ContractError, match='shape differs'):
        color.validate_evaluation_evidence(scene[0], scene[1], report)


def test_old_policy_does_not_authorize_inverse_parameters(scene):
    result = inverse(scene)
    contract.atomic_write_json(scene[0]/'session.json', {'execution_policy': sessions._PRE_COLOR_CURVE_VARIANTS_BINDINGS['execution_policy']})
    with pytest.raises(contract.ContractError, match='frozen execution policy'):
        color.validate_parameter_binding(scene[0], scene[1], {**result['parameters'], 'parameters': result['report']})


def test_exact_history_and_all_retained_color_helpers(tmp_path):
    assert contract.fingerprint(HISTORY, include_path=False) == {
        'sha256': 'aa76daeae6cd9d1e429f263e579e448031650ef6887ffe8a6b36477c1f208346', 'size': 11517}
    prior = contract.load_json(HISTORY)
    assert sessions._PRE_COLOR_CURVE_VARIANTS_BINDINGS == prior
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(prior['execution_policy'])
    from query_siril_manual import _HISTORICAL_COMMAND_POLICIES
    record = prior['documents']['references/command-policy.json']
    assert _HISTORICAL_COMMAND_POLICIES[record['sha256'], record['size']] == prior['command_policy'].encode()
    for path in (ROOT/'references').glob('*-history.json'):
        old = contract.load_json(path)
        implementation = old.get('implementation', {})
        if 'deep_sky_siril_color.py' not in implementation:
            continue
        contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': old['execution_policy']})
        assert sessions.known_execution_policy(old['execution_policy'])
        assert color.helper_fingerprint(tmp_path) == implementation['deep_sky_siril_color.py'], path.name
        assert stretch.implementation_matches(tmp_path, implementation), path.name
    contract.atomic_write_json(tmp_path/'session.json', {'execution_policy': prior['execution_policy']})
    mixed = {**prior['implementation'], 'deep_sky_siril_color.py': contract.fingerprint(ROOT/'scripts/deep_sky_siril_color.py', include_path=False)}
    assert not stretch.implementation_matches(tmp_path, mixed)


def test_previous_forward_evidence_replays_and_finalizes_unchanged(scene, tmp_path, monkeypatch):
    prior = sessions._PRE_COLOR_CURVE_VARIANTS_BINDINGS
    inspected = next(row['id'] for row in contract.load_json(scene[0]/'manifest.json')['runs']
                     if 'knowledge_validation' in contract.load_json(scene[0]/'runs'/row['receipt']))
    _freeze_historical_evidence(scene[0], snapshot=prior, run_id=inspected)
    with monkeypatch.context() as old:
        old.setattr(sessions, 'execution_policy_is_current', lambda _: True)
        result = evaluation.solve(scene)
        assert not set(color.CURVE_KEYS) & result['parameters'].keys()
        receipt, args = evaluation.execute(scene, result)
        _freeze_historical_evidence(scene[0], snapshot=prior, run_id=receipt['id'])
        evaluation.save_review(scene[0], receipt, evaluation.review(scene[0], receipt))
    frozen = support._session_bytes(scene[0])
    assert core.run_script(scene[0], **args)['replayed']
    assert support._session_bytes(scene[0]) == frozen
    replay_and_finalize_without_migration(tmp_path/'finalize', monkeypatch, HISTORY)


def test_native_inverse_response_masks_and_hue_diagnostics(tmp_path):
    for name in ('artifacts', 'previews'):
        (tmp_path/name).mkdir()
    sample = np.linspace(0, 1, 1001)
    before = np.array([[colorsys.hsv_to_rgb(hue/360, saturation, value) for saturation in sample]
        for value in (.2, .55, .9) for hue in (15, 75, 135, 195, 255, 315, 60, 180, 300)], dtype='float32').transpose(2, 0, 1)
    source = tmp_path/'parent.fit'; write_fits(source, before)
    shape = color.curve_parameters('invght', .5, .7)
    rows = ['requires 1.4.4 1.5.0', 'set32bits']
    for name, strength, parameters in [('forward', .6, {}), ('inverse', 1.2, shape)]:
        rows += [f'load "{source}"', ' '.join(color.curve_operator(strength, parameters)), f'save "{tmp_path/name}" -chksum']
    for index, (sp, hp) in enumerate(((0, 0), (.5, .5), (1, 1))):
        parameters = color.curve_parameters('invght', sp, hp)
        rows += [f'load "{source}"', ' '.join(color.curve_operator(1.2, parameters)), f'save "{tmp_path}/boundary-{index}" -chksum']
    result = run_native(tmp_path, '\n'.join([*rows, 'close'])+'\n')
    assert diagnose_siril_log(result.stdout+result.stderr, exit_code=result.returncode,
        timed_out=False, execution_valid=True)['status'] != 'failed'
    for index in range(3):
        boundary = color.pixels(tmp_path/f'boundary-{index}.fit')
        assert boundary.shape == before.shape and np.isfinite(boundary).all()
    forward, inverse_pixels = [color.pixels(tmp_path/(name+'.fit')) for name in ('forward', 'inverse')]
    forward_s, _ = color.rgb_statistics(forward)
    inverse_s, _ = color.rgb_statistics(inverse_pixels)
    assert np.median(inverse_s[:, 150]) > np.median(forward_s[:, 150])
    assert np.median(inverse_s[:, 300]) > np.median(forward_s[:, 300])
    assert np.median(inverse_s[:, 900]) < np.median(forward_s[:, 900])
    assert np.isfinite(inverse_pixels).all()
    # Independent hue arithmetic reports native equal-channel anomalies without adding an acceptance gate.
    measured = color.color_statistics(source, tmp_path/'inverse.fit', [{'id': 'palette', 'role': 'faint', 'rect': [200, 0, 400, before.shape[1]]}])
    hue_errors = []
    for a, b in zip(before[:, :, 200:400].transpose(1, 2, 0).reshape(-1, 3), inverse_pixels[:, :, 200:400].transpose(1, 2, 0).reshape(-1, 3)):
        delta = abs(colorsys.rgb_to_hsv(*map(float, a))[0]-colorsys.rgb_to_hsv(*map(float, b))[0])*360
        hue_errors.append(min(delta, 360-delta))
    assert measured[0]['hue_max_degrees'] == pytest.approx(max(hue_errors), abs=1e-3)
    parameters = {**shape, 'D': 1.2, 'mask_mode': 'none', 'mask_policy': color.PRESERVATION,
                  'saturation_protect_start': .7, 'saturation_protect_end': .95}
    text, primary, _, binding = color.script_text(tmp_path, source, '088-masked', parameters)
    result = run_native(tmp_path, text)
    assert diagnose_siril_log(result.stdout+result.stderr, exit_code=result.returncode,
        timed_out=False, execution_valid=True)['status'] != 'failed'
    mask = color.pixels(tmp_path/binding['layers']['mask'])[0]
    after = color.pixels(primary)
    assert np.any(mask == 0) and np.any((mask > 0) & (mask < 1))
    np.testing.assert_array_equal(after[:, mask == 0], before[:, mask == 0])
    checks = processing.measure_local_saturation(tmp_path, source, primary, binding)
    assert max(checks['max_abs_errors'].values()) <= 1e-6


@pytest.mark.parametrize('mask_mode', ['none', 'luminance'])
def test_native_full_rgb_inverse_formal_equivalence_and_replay(tmp_path, monkeypatch, mask_mode):
    session, source, regions = setup_session(tmp_path, monkeypatch)
    options = {'target_gain': .01, 'strength': .3, 'curve': 'invght', 'sp': .5, 'hp': .7,
               'saturation_protect_start': .7, 'saturation_protect_end': .95,
               'goal': 'Improve the displayed subject color', 'parameter_reason': 'One explicit inverse curve'}
    result = color.solve(session, source, regions, '085-inverse', **options)
    if mask_mode == 'luminance':
        result = color.solve(session, source, regions, '085-mask', **options, mask_mode=mask_mode,
            previous=result['report'], observation='Compare the luminance mask at the same inverse curve')
    assert result['status'] == 'solved', result
    text, primary, outputs, _ = color.script_text(session, source, '088-inverse', result['parameters'], report=result['report'])
    script = session/'scripts/088-inverse.ssf'; script.write_text(text)
    support._write_ssf_provenance(session, script, 'color.finish')
    args = dict(protocol='color.finish', script_value=script, source_value=source,
                primary_output_value=primary, expected_values=outputs, timeout=60)
    receipt = core.run_script(session, **args)
    assert receipt['status'] == 'success', receipt
    assert receipt['color_finish'][0]['command'] == 'invght'
    assert receipt['processing_checks']['color_curve']['trial_max_abs_error'] <= 1e-6
    evaluation.save_review(session, receipt, evaluation.review(session, receipt))
    frozen = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    assert support._session_bytes(session) == frozen


"""Bounded native vivid curves, unchanged hue, source contracts and old evidence."""
import copy
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from .test_deep_sky_siril import (
    _fake_probe, _input_inspect_run, _review, _write_ssf_provenance, core, tooling,
)
from .test_native_processing import write_fits, native
from ._policy_pins import assert_pinned_history
import deep_sky_siril_color as color
import deep_sky_siril_session as sessions
import deep_sky_siril_contract as contract
import deep_sky_siril as cli

ROOT = Path(__file__).parents[1]
BINARY = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')


def test_style_default_and_explicit_values():
    parser = cli.build_parser()
    base = ['init', 'input.fit', '--session', '/tmp/session']
    assert parser.parse_args(base).style == 'vivid'
    for style in ('natural', 'balanced', 'artistic', 'vivid'):
        assert parser.parse_args([*base, '--style', style]).style == style


def test_v18_policy_and_authority_remain_exact_replay_only():
    baseline = json.loads((ROOT/'tests/fixtures/vivid-policy-v18.json').read_text())
    old = baseline['execution_policy']
    assert_pinned_history(baseline, session_constant=sessions._BENEFIT_FIRST_EXECUTION_POLICY,
        command_mode='hash', check_contract=False)
    assert sessions._VIVID_EXECUTION_POLICY == {**old, 'guidance': 'vivid_color_flow_v19', 'color_curve': color.POLICY}
    assert not sessions.known_execution_policy({**old, 'color_curve': color.POLICY})


@pytest.mark.parametrize('change', [
    {'D': True}, {'D': float('nan')}, {'D': .4}, {'sigma': 0}, {'low_end': .03},
    {'parameters': 3}, {'extra': 1},
])
def test_curve_declaration_rejects_invalid_values(change):
    values = dict(D=.3, low_start=.03, low_end=.08, high_start=.65, high_end=.9,
                  sigma=2, parameters='reports/088-curve/color-parameters.json')
    with pytest.raises((contract.ContractError, ValueError)):
        color.curve_declaration('# color-curve: '+json.dumps({**values, **change}))


def test_curve_command_is_not_independent_rgb_or_luminance():
    color.validate_curve_operator(color.curve_operator(.3))
    for tokens in (color.curve_operator(.3)+['R'],
                   [s.replace('-sat', '-human') for s in color.curve_operator(.3)],
                   [s.replace('-clipmode=rgbblend', '-clipmode=clip') for s in color.curve_operator(.3)]):
        with pytest.raises(contract.ContractError):
            native.validate_parameters(tokens, 'color.finish')
    with pytest.raises(contract.ContractError):
        native.validate_parameters(color.curve_operator(.3), 'tone.adjust')


def scene():
    data = np.full((3, 128, 128), .02, dtype=np.float32)
    data[:, 64:112, 64:112] = np.array([.30, .25, .20])[:, None, None]
    data[:, 16:48, 64:96] = np.array([.97, .85, .80])[:, None, None]
    data[:, 64:96, 16:48] = .25
    return data


def setup_session(tmp_path, monkeypatch, *, style='vivid', neutral=False):
    if not BINARY.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    source = tmp_path/'external source.fit'
    write_fits(source, np.full((3,128,128),.2) if neutral else scene(),
               [('EXPTIME', 60), ('STACKCNT', 85)])
    monkeypatch.setattr(tooling, 'probe_tools', lambda **kwargs: _fake_probe(BINARY))
    root = tmp_path/'session with spaces'
    sessions.init_session(str(source), str(root), input_state='nonlinear',
                         state_evidence=['user confirmed input_state=nonlinear; calibrated, stacked master'],
                         channel_mode='broadband', channel_map=None, target_name='Test', target_type='galaxy',
                         style=style, stars='preserve', offline=True, keep_intermediates=True)
    with monkeypatch.context() as context:
        receipt, direct, diagnostic = _input_inspect_run(root, source, context)
    _review(root, receipt['id'], diagnostic, parent_preview=direct)
    regions = [{'id':'sky', 'role':'background', 'rect':[0,0,16,16]},
               {'id':'faint', 'role':'faint', 'rect':[70,20,100,50]},
               {'id':'core', 'role':'core', 'rect':[70,85,90,105]},
               {'id':'neutral', 'role':'stars', 'rect':[20,35,40,55]}]
    path = root/'reports/color-regions.json'
    path.write_text(json.dumps({'schema':'starun-siril.regions.v1',
                               'reference':{'sha256':contract.sha256_file(source),'width':128,'height':128},
                               'regions':regions}))
    return root, source, path


@pytest.mark.parametrize('style,target_gain', [('vivid', .1), ('natural', .08)])
def test_native_solve_execution_review_replay_and_drift(tmp_path, monkeypatch, style, target_gain):
    root, source, regions = setup_session(tmp_path, monkeypatch, style=style)
    original = contract.sha256_file(source)
    solved = color.solve(root, source, regions, '088-curve', 60, target_gain=target_gain, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                         goal='Improve the remaining subject color', parameter_reason='One explicit native fixture proposal')
    assert solved['status'] == 'solved'
    report_path = Path(solved['report'])
    report = contract.load_json(report_path)
    assert report['target_gain'] == target_gain
    assert report['requires_visual_review']
    assert len(report['trials']) == 1
    assert report['trials'][-1]['D'] == solved['parameters']['D']
    original_report = report_path.read_bytes()
    forged = copy.deepcopy(report)
    forged['bindings']['runtime']['version'] = '1.4.0'
    report_path.write_text(json.dumps(forged))
    with pytest.raises(contract.ContractError, match='frozen Siril'):
        color.validate_parameter_binding(root, source, {**solved['parameters'], 'parameters':str(report_path)})
    report_path.write_bytes(original_report)
    for trial in report['trials']:
        assert all(v == 0 for key in ('new_shadow','new_highlight') for v in trial['checks']['endpoint_counts'][key])
    text, primary, outputs, _ = color.script_text(root, source, '088-curve', solved['parameters'],
                                                 report=report_path.relative_to(root).as_posix())
    parameters = native.local_saturation_declaration(text)
    binding = color.validate_unmasked_curve(root, source, primary, native.script_commands(text), parameters)
    assert binding['policy'] == color.POLICY
    script = root/'scripts/088-curve.ssf'; script.write_text(text)
    _write_ssf_provenance(root, script, 'color.finish')
    args = dict(protocol='color.finish', script_value=str(script), source_value=str(source),
                primary_output_value=str(primary), expected_values=list(map(str,outputs)), timeout=60)
    core.run_script(str(root), validate_only=True, **args)
    receipt = core.run_script(str(root), **args)
    assert receipt['status'] == 'success'
    assert receipt['processing_checks']['color_curve']['target_gain'] == report['target_gain']
    assert sessions.load_session(str(root))[1]['context']['style'] == style
    assert receipt['color_finish'][0]['command'] == 'ght'
    checks = receipt['processing_checks']['unmasked_color']
    assert checks['geometry_unchanged']
    rows = receipt['processing_checks']['color_curve']['regions']
    faint = next(r for r in rows if r['role'] == 'faint')
    assert faint['candidate']['saturation_median'] > faint['parent']['saturation_median']
    assert faint['hue_max_degrees'] < .001
    assert next(r for r in rows if r['id']=='neutral')['candidate']['saturation_median'] == 0
    assert contract.sha256_file(source) == original
    assert core.run_script(str(root), **args)['replayed']
    skeleton = core.review_materials_for_run(str(root), receipt['id'])
    assert report_path.relative_to(root).as_posix() in {r['path'] for r in skeleton['inspected_materials']}
    assessment = skeleton['color_assessment']
    assessment.update(goal_status='met', additional_benefit='observed',
                      benefit_observation='Actual native fixture subject color improves', adoption_reason='Protected regions remain acceptable')
    for row in assessment['regions']:
        row.update(assessment='acceptable', observation='Actual bound native region inspected')
    skeleton.update(verdict='accept', gates={k:'pass' for k in skeleton['gates']},
                    notes='Synthetic native fixture: neutral sky and high peak protected, hue preserved.')
    review = root/'reviews/088-curve.json'; core.atomic_write_json(review,skeleton)
    core._validate_review(root,str(review),receipt['id'])
    incomplete = copy.deepcopy(skeleton)
    incomplete['inspected_materials'] = [r for r in incomplete['inspected_materials'] if r['path'] != report_path.relative_to(root).as_posix()]
    core.atomic_write_json(review,incomplete)
    with pytest.raises(contract.ContractError, match='Local saturation review'):
        core._validate_review(root,str(review),receipt['id'])
    core.atomic_write_json(review,skeleton)
    log = root/report['trials'][0]['log']['path']; log.write_text(log.read_text()+'\ndrift')
    with pytest.raises(contract.ContractError):
        core.run_script(str(root), **args)


@pytest.mark.parametrize('style,neutral,target_gain', [
    ('natural', False, None), ('balanced', False, None), ('artistic', False, None),
    ('vivid', True, None), ('vivid', True, .08),
])
def test_explicit_goal_and_neutral_source_evaluate_without_mask_skip(tmp_path, monkeypatch, style, neutral, target_gain):
    root, source, regions = setup_session(tmp_path, monkeypatch,style=style,neutral=neutral)
    if target_gain is None:
        with pytest.raises(contract.ContractError, match='explicit target'):
            color.solve(root, source, regions, '088-skip', 60)
        return
    result = color.solve(root, source, regions, '088-probe', 60, target_gain=target_gain, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                         goal='Assess actual color changes', parameter_reason='Explicit neutral-source diagnostic')
    report = contract.load_json(Path(result['report']))
    assert len(report['trials']) == 1
    assert report['trials'][0]['gain'] is None


def test_solver_rejects_source_and_region_drift(tmp_path,monkeypatch):
    root,source,regions=setup_session(tmp_path,monkeypatch)
    document=contract.load_json(regions)
    document['reference']['sha256']='0'*64; regions.write_text(json.dumps(document))
    with pytest.raises((contract.ContractError,ValueError),match='hash|reference'):
        color.solve(root,source,regions,'088-drift',60, target_gain=.08, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                    goal='Assess remaining color', parameter_reason='Explicit source-binding test')


@pytest.mark.parametrize('target_gain', [.2, .06])
def test_starless_gain_and_evidence_binding(tmp_path, monkeypatch, target_gain):
    root, source, regions = setup_session(tmp_path, monkeypatch)
    actual_state = native.source_state(root, sessions.load_session(str(root))[1], source)
    state = {**actual_state, 'separation_run': '060-separate'}
    frozen_chain = [{'command':'mtf', 'parameters':['0','0.01','1']}]
    state['run'] = {'id':'070-stretch', 'protocol':'stretch', 'transfer_chain':frozen_chain}
    monkeypatch.setattr(native, 'source_state', lambda *args: state)
    original_review = core._accepted_review
    monkeypatch.setattr(core, '_accepted_review', lambda session, run: None if run in {'060-separate','070-stretch'} else original_review(session, run))
    result = color.solve(root, source, regions, '088-starless', 60, target_gain=target_gain, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                         goal='Improve the remaining starless color', parameter_reason='One explicit native fixture proposal')
    report = contract.load_json(Path(result['report']))
    assert report['starless'] and report['target_gain'] == target_gain
    assert result['status'] == 'solved'
    text, primary, _, _ = color.script_text(root, source, '088-starless', result['parameters'], report=result['report'])
    metadata = native.prepare_processing(root, sessions.load_session(str(root))[1], 'color.finish', source,
                                         primary, text, {}, None, None, None, lambda run: None)
    assert metadata['transfer_chain'] == frozen_chain
    assert metadata['branch_binding']['separation_run'] == '060-separate'
    state['separation_run'] = None
    with pytest.raises(contract.ContractError, match='different branch'):
        native.prepare_processing(root, sessions.load_session(str(root))[1], 'color.finish', source,
                                  primary, text, {}, None, None, None, lambda run: None)
    state['separation_run'] = '060-separate'
    with pytest.raises(contract.ContractError, match='already exists'):
        color.solve(root, source, regions, '088-starless', 60, target_gain=target_gain, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                    goal='Improve the remaining starless color', parameter_reason='One explicit native fixture proposal')
    report['trials'][0]['log']['sha256'] = '0'*64
    Path(result['report']).write_text(json.dumps(report))
    with pytest.raises(contract.ContractError, match='hash changed'):
        color.validate_parameter_binding(root, source, {**result['parameters'], 'parameters':result['report']})


def test_solver_does_not_use_rejected_parent(tmp_path, monkeypatch):
    root, source, regions = setup_session(tmp_path, monkeypatch)
    reviews = list((root/'reviews').glob('*.json'))
    document = contract.load_json(reviews[0]); document['verdict'] = 'reject'
    core.atomic_write_json(reviews[0], document)
    with pytest.raises(contract.ContractError, match='Accepted visual review'):
        color.solve(root, source, regions, '088-rejected', 60, target_gain=.08, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                    goal='Assess remaining color', parameter_reason='Explicit source-acceptance test')


def test_native_narrow_mask_respects_float32_luminance_stage(tmp_path):
    from .test_local_saturation import run_native
    if not BINARY.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    for name in ('scripts','artifacts','previews'):
        (tmp_path/name).mkdir()
    rng=np.random.default_rng(142)
    data=np.array([.25,.18,.15])[:,None,None]+rng.uniform(-.005,.005,(3,128,128))
    source=tmp_path/'source.fit';write_fits(source,data)
    parameters=dict(D=.3,low_start=.18906741777276995,low_end=.19763285993784668,
                    high_start=.5436396896839142,high_end=.7586456614732743,sigma=2)
    text,primary,_,binding=color.script_text(tmp_path,source,'088-narrow',parameters)
    run_native(tmp_path,text)
    checks=native.measure_local_saturation(tmp_path,source,primary,binding)
    assert max(checks['max_abs_errors'].values())<1e-6
    assert max(checks['protected_max_abs'])<1e-6


@pytest.mark.parametrize('target_gain', [-.01, float('nan'), float('inf'), -float('inf'),
                                        True, False, '0.08', [], {}])
def test_invalid_target_fails_before_session_access_or_outputs(tmp_path, monkeypatch, target_gain):
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid targets must not access the session or run Siril')
    monkeypatch.setattr(sessions, 'load_session', forbidden)
    monkeypatch.setattr(color.subprocess, 'run', forbidden)
    with pytest.raises(contract.ContractError, match='finite nonnegative number'):
        color.solve(tmp_path/'session', tmp_path/'source.fit', tmp_path/'regions.json',
                    '088-invalid', target_gain=target_gain)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('target_gain', [None, .08, 0])
def test_cli_forwards_optional_target_gain(monkeypatch, capsys, target_gain):
    calls = []
    def solve(*args, **kwargs):
        calls.append((args, kwargs))
        return {'status': 'skipped'}
    arguments = ['color', '--session', '/abs/session', '--source', '/abs/source.fit',
                 '--regions', '/abs/regions.json', '--run-id', '088-cli']
    if target_gain is not None:
        arguments += ['--target-gain', str(target_gain)]
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(color, 'solve', solve)
    assert color.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'skipped'
    assert calls == [(('/abs/session', '/abs/source.fit', '/abs/regions.json', '088-cli', 1800),
                      {'target_gain': target_gain})]


@pytest.mark.parametrize('target_gain', ['nan', 'inf', '-inf', '-0.01'])
def test_cli_rejects_invalid_targets(tmp_path, monkeypatch, capsys, target_gain):
    monkeypatch.setattr(sys, 'argv', ['color', '--session', str(tmp_path/'session'),
        '--source', str(tmp_path/'source.fit'), '--regions', str(tmp_path/'regions.json'),
        '--run-id', '088-invalid', '--target-gain='+target_gain])
    assert color.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'failed' and 'finite nonnegative number' in result['reason']
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('style', ['vivid', 'natural'])
def test_zero_target_preserves_parent_without_trials(tmp_path, monkeypatch, style):
    root, source, regions = setup_session(tmp_path, monkeypatch, style=style)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    original = contract.sha256_file(source)
    def forbidden(*args, **kwargs):
        raise AssertionError('Zero targets must not run Siril')
    monkeypatch.setattr(color.subprocess, 'run', forbidden)
    result = color.solve(root, source, regions, '088-zero', target_gain=0)
    report = contract.load_json(Path(result['report']))
    assert result['status'] == 'skipped' and result['parameters'] is None
    assert report['target_gain'] == 0 and report['trials'] == []
    assert 'Zero target' in report['reason']
    assert not (Path(result['report']).parent/'trial.ini').exists()
    assert all(path.read_bytes() == data for path, data in before.items())
    assert contract.sha256_file(source) == original

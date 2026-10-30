"""Subject-first eligibility, measured overlap fallback and frozen historical rules."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from .test_native_processing import write_fits
from .test_local_contrast_guards import unit_case, regions_file
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_color as color
import deep_sky_siril_local_contrast as local
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch_v2 as stretch

ROOT = Path(__file__).parents[1]
PRIOR = json.loads((ROOT/'tests/fixtures/subject-enhancement-prior.json').read_text())


def texture_rows(value=1.0258168731882285):
    return [{'id': 'sky', 'role': 'background', 'sigma': 2, 'amplitude': .014030617880545075},
            {'id': 'rim', 'role': 'faint', 'sigma': 2, 'amplitude': .04},
            {'id': 'outer-shell', 'role': 'faint', 'sigma': 2, 'amplitude': .014030617880545075*value}]


def test_low_texture_allows_trial_and_retains_every_subject():
    result = local.texture(texture_rows(), diagnostic_only=True)
    assert result['role'] == 'diagnostic_only'
    assert result['texture_snr'] == pytest.approx(1.0258168731882285)
    assert [r['id'] for r in result['ratios']] == ['rim', 'outer-shell']
    assert result['warnings'] == [{'id': 'outer-shell', 'ratio': result['texture_snr'],
                                   'code': 'low_texture_ratio', 'threshold': 1.7}]
    with pytest.raises(contract.ContractError, match='outer-shell'):
        local.texture(texture_rows())


@pytest.mark.parametrize('floor', [0, 1e-12, float('nan'), float('inf')])
@pytest.mark.parametrize('diagnostic_only', [False, True])
def test_unavailable_measurement_remains_blocked(floor, diagnostic_only):
    rows = texture_rows(); rows[0]['amplitude'] = floor
    with pytest.raises(contract.ContractError):
        local.texture(rows, diagnostic_only=diagnostic_only)


def test_low_texture_survives_prepare_and_candidate_measurement(tmp_path):
    source, primary, commands, _, lineage = unit_case(tmp_path)
    rng = np.random.default_rng(42)
    values = np.stack([.2+rng.normal(0, .001, (384, 384))]*3)
    write_fits(source, values); write_fits(primary, values)
    regions = regions_file(tmp_path, source)
    binding = local.prepare(tmp_path, source, primary, commands,
                            regions.relative_to(tmp_path).as_posix(), lineage, diagnostic_only=True)
    assert binding['texture_diagnostic_only'] is True
    assert binding['eligibility']['texture_snr'] < 1.7
    measured = local.measure(tmp_path, source, primary, binding)
    assert measured['status'] == 'measured'
    assert measured['texture_diagnostics'] == binding['eligibility']
    assert any(w['code'] == 'low_texture_ratio' for w in measured['warnings'])
    binding.pop('texture_diagnostic_only')
    with pytest.raises(contract.ContractError):
        local.measure(tmp_path, source, primary, binding)


def mask_case(tmp_path, *, overlap=True, indistinguishable=False):
    source = tmp_path/'parent.fit'
    data = np.full((3, 64, 64), .02)
    sky = np.linspace(.18, .32, 256).reshape(16, 16) if overlap else np.full((16, 16), .02)
    target = sky if indistinguishable else np.linspace(.25, .43, 256).reshape(16, 16)
    data[:, 48:64, :16] = sky
    data[:, 16:32, 32:48] = np.array([1.1, 1, .9])[:, None, None]*target
    if indistinguishable:
        data[:, 16:32, 32:48] = sky
    write_fits(source, data)
    rows = [{'id': 'sky', 'role': 'background', 'rect': [0, 0, 16, 16]},
            {'id': 'outer-shell', 'role': 'faint', 'rect': [32, 32, 48, 48]}]
    return source, rows


def test_overlapping_tails_use_ordered_measured_medians(tmp_path):
    source, rows = mask_case(tmp_path)
    assert color.derive_mask(source, rows) is None
    mask = color.derive_mask(source, rows, allow_overlap=True)
    assert mask['low_start'] == pytest.approx(.25)
    expected_luminance = .2126*1.1+.7152+.0722*.9
    assert mask['low_end'] == pytest.approx(.34*expected_luminance)
    assert 0 <= mask['low_start'] < mask['low_end'] <= mask['high_start'] < mask['high_end'] <= 1


def test_strict_mask_is_retained_when_already_ordered(tmp_path):
    source, rows = mask_case(tmp_path, overlap=False)
    assert color.derive_mask(source, rows, allow_overlap=True) == color.derive_mask(source, rows)


def test_indistinguishable_medians_do_not_create_an_artificial_interval(tmp_path):
    source, rows = mask_case(tmp_path, indistinguishable=True)
    assert color.derive_mask(source, rows, allow_overlap=True) is None


def test_overlap_parameters_are_bound_to_source_and_policy(tmp_path, monkeypatch):
    source, rows = mask_case(tmp_path)
    reports = tmp_path/'reports'; reports.mkdir()
    regions = reports/'regions.json'; regions.write_text(json.dumps({'regions': rows}))
    configuration = reports/'trial.ini'; configuration.write_text('[core]\nforce_16bit=false\n')
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': sessions._PRE_COLOR_EXECUTION_POLICY}))
    runtime = {'fingerprint': {'path': '/unused'}}
    (reports/'tool-probe.json').write_text(json.dumps({'tools': {'siril_cli': runtime}}))
    monkeypatch.setattr(color, 'fingerprint_matches', lambda _: True)
    parameters = {'D': .15, **color.derive_mask(source, rows, allow_overlap=True)}
    report = {'schema': 'starun-siril.color-parameters.v1', 'policy': color.POLICY, 'status': 'solved',
              'starless': True, 'subject_enhancement': 'benefit_first_v1', 'mask_strategy': 'overlap_medians',
              'selected_parameters': parameters, 'trials': [],
              'bindings': {'source': {'path': str(source), **contract.fingerprint(source, include_path=False)},
                           'regions': color.record(tmp_path, regions),
                           'configuration': color.record(tmp_path, configuration), 'runtime': runtime}}
    path = reports/'parameters.json'
    path.write_text(json.dumps(report))
    declaration = {**parameters, 'parameters': 'reports/parameters.json'}
    assert color.validate_parameter_binding(tmp_path, source, declaration)['starless']
    for change in ({'subject_enhancement': None}, {'mask_strategy': 'strict_percentiles'}):
        path.write_text(json.dumps({**report, **change}))
        with pytest.raises(contract.ContractError):
            color.validate_parameter_binding(tmp_path, source, declaration)
    path.write_text(json.dumps(report))
    source_values = color.pixels(source); source_values[:, 48:64, :16] += .02
    write_fits(source, source_values)
    with pytest.raises(contract.ContractError, match='source changed'):
        color.validate_parameter_binding(tmp_path, source, declaration)


def test_overlap_reaches_a_native_trial_without_executing_siril(tmp_path, monkeypatch):
    import deep_sky_siril_core as core
    import deep_sky_siril_metrics as metrics
    source, rows = mask_case(tmp_path)
    reports = tmp_path/'reports'; reports.mkdir(); (tmp_path/'runtime').mkdir()
    regions = reports/'regions.json'; regions.write_text(json.dumps({'regions': rows}))
    payload = {'execution_policy': contract.EXECUTION_POLICY, 'input': {'path': str(source)},
               'context': {'channel_mode': 'dualband-osc', 'style': 'vivid'}}
    (tmp_path/'session.json').write_text(json.dumps(payload))
    runtime = {'compatible': True, 'path': '/unused', 'fingerprint': {}}
    (reports/'tool-probe.json').write_text(json.dumps({'tools': {'siril_cli': runtime}}))
    monkeypatch.setattr(sessions, 'load_session', lambda _: (tmp_path, payload, {}))
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda *args: payload)
    monkeypatch.setattr(processing, 'source_state', lambda *args:
                        {'domain': 'nonlinear', 'separation_run': '060-stars', 'run': {'id': '071-stretch'}, 'lineage': []})
    monkeypatch.setattr(core, '_accepted_review', lambda *args: None)
    monkeypatch.setattr(metrics, 'measure_regions', lambda *args: {'status': 'measured'})
    monkeypatch.setattr(color, 'fingerprint_matches', lambda _: True)
    def stop_before_execution(*args, **kwargs):
        raise RuntimeError('native trial reached')
    monkeypatch.setattr(color.subprocess, 'run', stop_before_execution)
    original = source.read_bytes()
    with pytest.raises(RuntimeError, match='native trial reached'):
        color.solve(tmp_path, source, regions, '088-overlap', target_gain=.08, strength=.3, saturation_protect_start=.7, saturation_protect_end=.95,
                    goal='Assess remaining subject color', parameter_reason='Explicit synthetic proposal')
    assert source.read_bytes() == original
    assert not (reports/'088-overlap/color-parameters.json').exists()


def test_low_texture_solver_reaches_trial_without_executing_siril(tmp_path, monkeypatch):
    import deep_sky_siril_dualband as dual
    source, _, _, _, lineage = unit_case(tmp_path)
    rng = np.random.default_rng(42)
    values = np.stack([.2+rng.normal(0, .001, (384, 384))]*3)
    write_fits(source, values)
    rows = [{'id': f'sky-{i}', 'role': 'background', 'rect': rect}
            for i, rect in enumerate(([64, 64, 88, 88], [280, 64, 304, 88], [64, 280, 88, 304]))]
    rows.append({'id': 'outer-shell', 'role': 'faint', 'rect': [176, 176, 208, 208]})
    (tmp_path/'reports/regions.json').write_text(json.dumps({'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 384, 'height': 384}, 'regions': rows}))
    payload = {'execution_policy': contract.EXECUTION_POLICY}
    runtime = {'compatible': True, 'fingerprint': {}}
    (tmp_path/'reports/tool-probe.json').write_text(json.dumps({'tools': {'siril_cli': runtime}}))
    request = {'schema': local.PROTECTED_REQUEST, 'source': source.relative_to(tmp_path).as_posix(),
        'regions': 'reports/regions.json', 'mask': dict(low_start=.03, low_end=.1, high_start=.65, high_end=.9, sigma=2),
        'wavelet': {'amplitude': .4, 'fine_detail': False}, 'clahe': {'strength': .15},
        'manual_lookup': {'status': 'not_needed', 'reason': 'Documented recipe', 'evidence': []}}
    path = tmp_path/'reports/request.json'; path.write_text(json.dumps(request))
    monkeypatch.setattr(sessions, 'load_session', lambda _: (tmp_path, payload, {}))
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda *args: payload)
    monkeypatch.setattr(local, '_accepted_source', lambda *args: {'lineage': lineage})
    monkeypatch.setattr(local, 'fingerprint_matches', lambda _: True)
    def stop_before_execution(*args, **kwargs):
        raise RuntimeError('native trial reached')
    monkeypatch.setattr(dual, '_native', stop_before_execution)
    with pytest.raises(RuntimeError, match='native trial reached'):
        local.solve(tmp_path, path, '080-low-texture')
    assert not (tmp_path/'reports/080-low-texture/parameters.json').exists()


def test_outgoing_policy_and_complete_implementation_remain_replay_only(tmp_path):
    old = PRIOR['execution_policy']
    # prior=subject-enhancement-prior.json: 12 个域标签
    assert_contract_domains(old)
    assert sessions._STRICT_ENHANCEMENT_EXECUTION_POLICY == old
    assert sessions.known_execution_policy(old)
    assert not sessions.execution_policy_is_current({'execution_policy': old})
    (tmp_path/'session.json').write_text(json.dumps({'execution_policy': old}))
    assert stretch.implementation_matches(tmp_path, PRIOR['implementation'])
    mixed = copy.deepcopy(PRIOR['implementation'])
    mixed['deep_sky_siril_local_contrast.py'] = contract.fingerprint(ROOT/'scripts/deep_sky_siril_local_contrast.py', include_path=False)
    assert not stretch.implementation_matches(tmp_path, mixed)
    assert local.STRICT_TEXTURE_HELPER == PRIOR['implementation']['deep_sky_siril_local_contrast.py']

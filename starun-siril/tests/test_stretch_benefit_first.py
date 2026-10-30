"""Diagnostic noise, benefit ranking, replay integrity and historical protection."""
import copy
import json
from pathlib import Path

import pytest

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_profiles as profiles
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY
from .test_deep_sky_siril_stretch import context
from .test_stretch_v22 import case
from ._stretch_factory import solve_bound


@pytest.mark.parametrize('statistic', ['mad', 'hf_sigma2'])
def test_only_sky_noise_constraints_become_advisory(case, statistic):
    _, source, _, _, request, regions = case
    data = base.pixels(source)
    constraints = [{'region': 'sky', 'statistic': statistic, 'min': 0., 'max': 0.}]
    legacy = protected.audit(data, data, regions, constraints, request['targets'])
    current = protected.audit(data, data, regions, constraints, request['targets'], noise_diagnostic=True)
    assert not legacy['eligible']
    assert current['eligible'] and current['noise_warnings']
    assert current['constraints'] == legacy['constraints']
    assert current['subject_contrast'] > 0
    constraints.append({'region': 'core', 'statistic': 'p999', 'min': 0., 'max': 0.})
    assert not protected.audit(data, data, regions, constraints, request['targets'], noise_diagnostic=True)['eligible']
    clipped = data.copy()
    clipped[:, 40, 40] = 1.
    result = protected.audit(data, clipped, regions, [], request['targets'], noise_diagnostic=True)
    assert not result['eligible'] and 'new_highlight_crossing' in result['reasons']


def test_ranking_prefers_contrast_even_with_more_noise_and_lower_normalized_score():
    trials = [
        {'audit': {'eligible': True, 'subject_contrast': contrast, 'separation': score, 'sky_hf': noise},
         'parameters': {'command': 'mtf', 'parameters': ['0', '.2', '1']}, 'coordinates': (index,)}
        for index, (contrast, score, noise) in enumerate(((.1, 20., .01), (.2, 10., .1)))
    ]
    assert protected.select(trials)[0] == 0
    assert protected.select(trials, noise_diagnostic=True)[0] == 1
    rows = [{'status': 'solved', 'run_id': str(index), 'audit': row['audit'], 'transfer_cost': index}
            for index, row in enumerate(trials)]
    assert profiles.recommend(rows) == '0'
    assert profiles.recommend(rows, noise_diagnostic=True) == '1'


def test_noise_warning_survives_proposal_replay_and_formal_measurement(case):
    root, source, payload, path, request, _ = case
    request['schema'] = protected.PROPOSAL_REQUEST
    request['operators'][0]['factors'] = {'midtones': .05}
    request['operators'][0]['constraints'][2]['max'] = 0.
    detail = copy.deepcopy(request['operators'][0])
    detail.update(method='asinh', purpose='Separate the synthetic subject after initial display preparation',
                  factors={'stretch': 1.2})
    request['operators'].append(detail)
    path.write_text(json.dumps(request))
    result = solve_bound(root, path, '070-benefit')
    assert result['status'] == 'solved', result['report'].get('reason')
    report, _, _ = base.load_parameters(root, payload, result['parameters_path'])
    output = base.bound_file(root, report['steps'][-1]['selected']['.fit'])
    measured = protected.measure_output(root, source, output, report)
    assert measured['audit']['noise_warnings']
    assert report['steps'][0]['evaluation_count'] == 1
    report['steps'][0]['trials'][0]['audit']['noise_warnings'] = []
    Path(result['parameters_path']).write_text(json.dumps(report))
    with pytest.raises(ContractError, match='regional measurements'):
        base.load_parameters(root, payload, result['parameters_path'])


def test_old_session_keeps_noise_gates_and_exact_implementation(tmp_path):
    payload = {'execution_policy': sessions._AGENT_PROPOSAL_EXECUTION_POLICY}
    (tmp_path/'session.json').write_text(json.dumps(payload))
    assert not protected.noise_is_diagnostic(payload)
    assert protected.noise_is_diagnostic({'execution_policy': EXECUTION_POLICY})
    binding = copy.deepcopy(protected._V25_BINDINGS['implementation'])
    assert protected.implementation_matches(tmp_path, binding)
    binding['deep_sky_siril_stretch_v2.py'] = protected.implementation()['deep_sky_siril_stretch_v2.py']
    assert not protected.implementation_matches(tmp_path, binding)

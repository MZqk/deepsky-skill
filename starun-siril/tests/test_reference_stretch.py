"""Reference-derived MTF seeds and mandatory cross-stage visibility review."""
import copy
import json
import sys
from pathlib import Path

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_visibility as visibility
from .test_recomposition_sources_v11 import source_case, accept
from .test_starnet_linear_bridge import native
from .test_stretch_v22 import field_request, case
from .test_deep_sky_siril_stretch import context
from ._stretch_factory import solve_bound, historical_parameters


@pytest.mark.parametrize('x,target', [(.001, .2), (.02, .5), (.3, .7), (.8, .4)])
def test_reference_inverse_mtf(x, target):
    mid = visibility.inverse_midtones(x, target)
    actual = (mid-1)*x/((2*mid-1)*x-mid)
    assert actual == pytest.approx(target)


@pytest.mark.parametrize('x,target', [(0., .2), (1., .2), (.1, 1.), (True, .2), (.1, float('nan'))])
def test_reference_inverse_rejects_invalid_anchors(x, target):
    with pytest.raises(contract.ContractError):
        visibility.inverse_midtones(x, target)


def test_native_bound_reference_review_and_unbound_inversion_rejection(source_case):
    session, _, _, parent, *_ = source_case
    path, request = field_request(session, parent, name='071-reference')
    request.update(schema=protected.PROPOSAL_REQUEST, profile='custom')
    request['operators'][0]['factors'] = {'midtones': .5}
    path.write_text(json.dumps(request))
    count = len(list(session.glob('reports/*/scientific-attempt.json')))
    with pytest.raises(contract.ContractError, match='unbound baseline inversion is legacy-only'):
        visibility.propose(session, path, 'shell', '071-reference-seed', 60)
    assert not (session/'reports/071-reference-seed').exists()
    assert len(list(session.glob('reports/*/scientific-attempt.json'))) == count
    run = contract.load_json(session/'runs/070-stretch.json')
    binding = run['stretch_visibility']
    paths = visibility.review_paths(session, binding, run)
    comparison = contract.load_json(session/binding['path'])
    assert comparison['origin']['preview']['path'].endswith('/starless.jpg')
    assert comparison['review_contract'] == visibility.BOUND_REVIEW_CONTRACT
    with pytest.raises(contract.ContractError, match='another formal'):
        visibility.review_paths(session, binding, {**run, 'primary_output':'artifacts/other.fit'})
    review_path = session/'reviews/070-stretch.json'
    before = review_path.read_bytes()
    review = json.loads(before)
    panel = next(name for name in paths if name.endswith('reference-candidate-rois.png'))
    review['inspected_materials'] = [row for row in review['inspected_materials'] if row['path'] != panel]
    contract.atomic_write_json(review_path, review)
    with pytest.raises(contract.ContractError, match='Stage 7 review'):
        core.check_review(session, run['id'], review_path)
    review_path.write_bytes(before)
    args = dict(protocol='stretch', script_value=session/run['script']['path'], source_value=parent,
        expected_values=[session/row['path'] for row in run['outputs']],
        primary_output_value=session/run['primary_output'], timeout=60)
    assert core.run_script(session, **args)['replayed']
    original = (session/panel).read_bytes()
    (session/panel).write_bytes(original+b'drift')
    with pytest.raises(contract.ContractError):
        core.run_script(session, **args)
    (session/panel).write_bytes(original)
    assert core.run_script(session, **args)['replayed']


def test_bad_anchor_does_not_create_proposal_artifacts(case):
    session, _, _, path, request, _ = case
    request.update(schema=protected.PROPOSAL_REQUEST, profile='custom')
    request['operators'][0]['factors'] = {'midtones': .5}
    path.write_text(json.dumps(request))
    with pytest.raises(contract.ContractError, match='unbound baseline inversion is legacy-only'):
        visibility.propose(session, path, 'core', '071-no-proposal')
    assert not (session/'reports/071-no-proposal').exists()


def test_exact_pre_reference_implementation_remains_readable(case, monkeypatch):
    session, _, payload, path, request, _ = case
    request.update(schema=protected.PROPOSAL_REQUEST, profile='custom')
    request['operators'][0]['factors'] = {'midtones': .05}
    path.write_text(json.dumps(request))
    with monkeypatch.context() as legacy:
        from ._policy_pins import outgoing_stretch_producer
        outgoing_stretch_producer(legacy)
        legacy.setitem(payload, 'execution_policy', copy.deepcopy(contract.EXECUTION_POLICY))
        contract.atomic_write_json(session/'session.json', payload)
        legacy.setattr(base, 'optimization_enabled', lambda payload: False)
        result = solve_bound(session, path, '071-pre-reference')
    contract.atomic_write_json(session/'session.json', payload)
    snapshot = contract.load_json(Path(__file__).parent/'fixtures/reference-stretch-prior.json')
    assert protected._PRE_REFERENCE_BINDINGS == {k:snapshot[k] for k in ('execution_policy','implementation','profiles')}
    original_path = Path(result['parameters_path']); original = original_path.read_bytes()
    report_path, report = historical_parameters(session, result, snapshot)
    assert original_path.read_bytes() == original
    before = report_path.read_bytes()
    with pytest.raises(contract.ContractError, match='bound morphology'):
        base.load_parameters(session, payload, report_path)
    payload['execution_policy'] = copy.deepcopy(snapshot['execution_policy'])
    contract.atomic_write_json(session/'session.json', payload)
    base.load_parameters(session, payload, report_path)
    assert report_path.read_bytes() == before
    report['bindings']['implementation']['deep_sky_siril_stretch.py'] = protected.implementation()['deep_sky_siril_stretch.py']
    contract.atomic_write_json(report_path, report)
    with pytest.raises(contract.ContractError, match='implementation'):
        base.load_parameters(session, payload, report_path)


def test_baseline_cli_returns_proposed_status(monkeypatch, capsys):
    captured = []
    monkeypatch.setattr(visibility, 'propose', lambda *args: captured.append(args) or {'status': 'proposed'})
    monkeypatch.setattr(sys, 'argv', ['stretch', 'baseline', '--session', '/session',
        '--request', '/session/reports/request.json', '--anchor-region', 'shell', '--run-id', '071-reference'])
    assert base.main() == 0
    assert captured[0][2:] == ('shell', '071-reference', 1800)
    assert json.loads(capsys.readouterr().out)['status'] == 'proposed'


@pytest.mark.parametrize('source_case', ['hdr'], indirect=True)
def test_hdr_visibility_compares_final_blend(source_case):
    session, primary, *_ = source_case
    run = contract.load_json(session/'runs/070-stretch.json')
    report = contract.load_json(session/run['stretch_visibility']['path'])
    assert report['candidate'] == base.record(session, primary)
    assert report['statistics']['candidate'] == base.statistics(base.pixels(primary),
        contract.load_json(session/report['regions']['path'])['regions'])
    assert len(visibility.review_paths(session, run['stretch_visibility'], run)) == 1

"""Single Agent factor proposals, native verification and immutable legacy evidence."""
import copy
import json
from pathlib import Path

import pytest

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_stretch_profiles as profiles
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError
from ._policy_pins import assert_contract_domains
from ._stretch_factory import solve_bound, bind_exception, bind_hoo, historical_parameters, source_evidence
from .test_deep_sky_siril_stretch import context
from .test_stretch_v22 import case, field_request
from .test_stretch_profiles_v23 import goals_for
from .test_starless_stretch_v24 import native_scene
from .test_recomposition_sources_v11 import source_case, linear_case, accept, recompose_assets
from .test_starnet_linear_bridge import native
from .test_dualband_hoo import dual_case
from .test_shared_protocol_docs_v12 import STRETCH_DISPLAY_BASELINE, _freeze_historical_evidence

@pytest.fixture(autouse=True)
def retired_operator_producer(monkeypatch, request):
    if request.node.originalname.startswith('test_exact_') or 'document_evidence' in request.node.originalname:
        return
    from ._policy_pins import outgoing_stretch_producer
    outgoing_stretch_producer(monkeypatch)



def propose(path, request, factors=None):
    request['schema'] = protected.PROPOSAL_REQUEST
    request['operators'][0]['factors'] = {'midtones': .05} if factors is None else factors
    path.write_text(json.dumps(request))


def forbid_search(*args, **kwargs):
    raise AssertionError('A factor proposal must never enter grid search')


@pytest.mark.parametrize('full', [False, True])
def test_one_proposal_and_native_budget(case, monkeypatch, full):
    root, source, payload, path, request, _ = case
    if full:
        from .test_deep_sky_siril_stretch import write
        original = write(root/'artifacts/full.fit', base.pixels(source)+.0001)
        monkeypatch.setattr(base, 'accepted_parent', lambda *args: original)
        monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    propose(path, request)
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    solved = solve_bound(root, path, '071-proposal')
    assert solved['status'] == 'solved', solved['report'].get('reason')
    report, _, _ = base.load_parameters(root, payload, solved['parameters_path'])
    trace = report['steps'][0]
    assert report['generation'] == protected.PROPOSAL_POLICY
    assert trace['evaluation_count'] == len(trace['trials']) == 1
    assert trace['native_evaluation_count'] == (4 if full else 2)
    assert float(report['transfer_chain'][0]['parameters'][1]) == .05
    assert report['request']['operators'][0]['factors'] == {'midtones': .05}
    output = base.bound_file(root, trace['selected']['.fit'])
    assert protected.measure_output(root, source, output, report)['generation'] == protected.PROPOSAL_POLICY


def test_ineligible_proposal_keeps_evidence_without_retry(case, monkeypatch):
    root, source, _, path, request, _ = case
    request['operators'][0]['method'] = 'asinh'
    propose(path, request, {'stretch': 5.})
    constraint = next(row for row in request['operators'][0]['constraints'] if row['region'] == 'faint' and row['statistic'] == 'median')
    constraint.update(min=0., max=0.)
    request['operators'].append(copy.deepcopy(request['operators'][0]))
    path.write_text(json.dumps(request))
    digest = base.sha256_file(source)
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    solved = solve_bound(root, path, '071-ineligible')
    assert solved['status'] == 'ineligible'
    report = solved['report']
    assert len(report['steps']) == 1 and report['transfer_chain'] == []
    trace = report['steps'][0]
    assert trace['evaluation_count'] == trace['native_evaluation_count'] == 1
    assert trace['selected_trial'] is None
    retained = trace['trials'][0]['retained']
    assert base.bound_file(root, retained['candidate.fit']).is_file()
    assert base.bound_file(root, retained['candidate.jpg']).is_file()
    assert 'hf_sigma2' in str(trace['trials'][0]['audit']['constraints'])
    assert base.sha256_file(source) == digest
    with pytest.raises(ContractError, match='solved'):
        base.generate_stretch_assets(root, parameters=solved['parameters_path'])


@pytest.mark.parametrize('factors', [{}, {'midtones': 0}, {'midtones': 1}, {'midtones': True},
    {'midtones': '0.05'}, {'wrong': .05}, {'midtones': .05, 'extra': 1}])
def test_invalid_factors_fail_before_native_launch(case, monkeypatch, factors):
    root, _, _, path, request, _ = case
    propose(path, request, factors)
    monkeypatch.setattr(base.NativeTrials, 'run', forbid_search)
    with pytest.raises(ContractError):
        solve_bound(root, path, '071-bad-factors')
    assert not (root/'reports/071-bad-factors').exists()


@pytest.mark.parametrize('method', [None, [], {}, True, 'unknown'])
def test_malformed_method_is_a_contract_error(method):
    with pytest.raises(ContractError, match='factor names'):
        protected.factor_values({'method': method, 'factors': {}})


@pytest.mark.parametrize('shape', [-.5, 15.1])
def test_ghs_shape_respects_frozen_native_cli_bounds(case, monkeypatch, shape):
    root, _, _, path, request, _ = case
    request['operators'][0].update(method='ght', landmarks={k: {'region': r, 'statistic': s} for k, r, s in
        [('LP', 'sky', 'p99'), ('SP', 'faint', 'median'), ('HP', 'core', 'p01')]})
    propose(path, request, {'D': 1.5, 'B': shape})
    monkeypatch.setattr(base.NativeTrials, 'run', forbid_search)
    with pytest.raises(ContractError, match='Siril 1.4.4 CLI'):
        solve_bound(root, path, '071-bad-shape')
    assert not (root/'reports/071-bad-shape').exists()


def test_nonfinite_proposal_stops_without_local_panel(case, monkeypatch):
    import numpy as np
    from .test_deep_sky_siril_stretch import write
    root, _, _, path, request, _ = case
    propose(path, request)
    item=bind_exception(root,request,'071-nonfinite-proposal')
    path=Path(item['request'])
    original = base.NativeTrials.run
    def run(self, *args):
        output, evidence = original(self, *args)
        data = base.pixels(output);data[0, 0, 0] = np.nan
        write(output, data)
        return output, evidence
    monkeypatch.setattr(base.NativeTrials, 'run', run)
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    result = solve_bound(root,path,item['run_id'])
    assert result['status'] == 'ineligible'
    trace = result['report']['steps'][0]
    assert trace['native_evaluation_count'] == 1 and 'local_panel' not in trace
    assert trace['trials'][0]['audit']['nonfinite_count'] == 1
    assert base.bound_file(root, trace['trials'][0]['retained']['candidate.fit']).is_file()


@pytest.mark.parametrize('command,status,exit_code', [
    ('init-request', 'prepared', 0), ('compare', 'comparison_ready', 0), ('compare', 'retain_parent', 1)])
def test_cli_strategy_statuses(monkeypatch, capsys, command, status, exit_code):
    import sys
    if command == 'init-request':
        arguments = ['--source', '@input', '--regions', 'reports/regions.json', '--goals', 'reports/goals.json', '--run-id', '071-agent']
        monkeypatch.setattr(profiles, 'init_requests', lambda *args, **kwargs: {'status': status})
    else:
        arguments = ['--strategy', 'reports/strategy.json', '--parameters', 'reports/parameters.json']
        monkeypatch.setattr(profiles, 'compare', lambda *args: {'status': status})
    monkeypatch.setattr(sys, 'argv', ['stretch', command, '--session', '/unused/session', *arguments])
    assert base.main() == exit_code
    assert json.loads(capsys.readouterr().out)['status'] == status


@pytest.mark.parametrize('change', ['mode', 'coordinates', 'trials', 'factors'])
def test_proposal_mode_and_exact_values_cannot_drift(case, change):
    root, _, payload, path, request, _ = case
    propose(path, request)
    result = solve_bound(root, path, '071-bound-factors')
    assert result['status'] == 'solved', result['report'].get('reason')
    report_path = Path(result['parameters_path']);report = json.loads(report_path.read_text())
    if change == 'mode':
        report['generation'] = 'bounded_search_v1'
    elif change == 'coordinates':
        report['steps'][0]['trials'][0]['coordinates'][0] += .01
    elif change == 'trials':
        report['steps'][0]['trials'].append(copy.deepcopy(report['steps'][0]['trials'][0]))
    else:
        request['operators'][0]['factors']['midtones'] = .06
        bound_path=base.bound_file(root,report['bindings']['request'])
        changed=copy.deepcopy(report['request']);changed['operators'][0]['factors']={'midtones':.06}
        bound_path.write_text(json.dumps(changed))
        report['request'] = changed
        report['bindings']['request'] = base.record(root, bound_path)
    report_path.write_text(json.dumps(report))
    with pytest.raises(ContractError, match='generation mode|proposal factors|frozen plan'):
        base.load_parameters(root, payload, report_path)


@pytest.mark.parametrize('strength', [1.5, 3.])
def test_native_strong_explicit_ghs(context, monkeypatch, strength):
    root, source, payload, path, request, _, full, _ = native_scene(context)
    monkeypatch.setattr(base, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    step = request['operators'][0]
    step.update(method='ght', landmarks={k: {'region': r, 'statistic': s} for k, r, s in
        [('LP', 'sky', 'p99'), ('SP', 'faint', 'median'), ('HP', 'core', 'p01')]})
    propose(path, request, {'D': strength, 'B': 1.})
    solved = solve_bound(root, path, '071-native-factors', timeout=60)
    assert solved['status'] == 'solved', solved['report'].get('reason')
    report, _, _ = base.load_parameters(root, payload, solved['parameters_path'])
    trace = report['steps'][0]
    assert trace['native_evaluation_count'] == 4
    assert trace['prefix_audit']['eligible'] and not trace['full_prefix_audit']['eligible']
    assert report['full_replay_role'] == 'diagnostic'
    assert float(report['transfer_chain'][0]['parameters'][0].split('=')[1]) == strength
    output = base.bound_file(root, trace['selected']['.fit'])
    assert protected.measure_output(root, source, output, report)['status'] == 'passed'


def test_native_prefix_anchors_use_actual_current_domain(context, monkeypatch):
    root, _, payload, path, request, _, full, _ = native_scene(context)
    monkeypatch.setattr(base, 'accepted_parent', lambda *args: full)
    monkeypatch.setattr(sessions, 'effective_session_payload', lambda session, value, *args: value)
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    first = request['operators'][0]
    first.update(method='ght', landmarks={k: {'region': r, 'statistic': s} for k, r, s in
        [('LP', 'sky', 'p99'), ('SP', 'faint', 'median'), ('HP', 'core', 'p01')]})
    propose(path, request, {'D': .1, 'B': 1.})
    second = copy.deepcopy(first);second['factors'] = {'D': .2, 'B': .5};request['operators'].append(second)
    path.write_text(json.dumps(request))
    result = solve_bound(root, path, '071-prefix-factors', timeout=60)
    assert result['status'] == 'solved', result['report'].get('reason')
    report, _, _ = base.load_parameters(root, payload, result['parameters_path'])
    assert len(report['steps']) == 2
    assert report['steps'][0]['input_statistics'] != report['steps'][1]['input_statistics']
    for trace, step in zip(report['steps'], request['operators']):
        actual = {token[1:].split('=')[0]: float(token.split('=')[1]) for token in trace['selected_parameters']['parameters'] if token.startswith(('-LP=', '-SP=', '-HP='))}
        assert actual == {k: trace['input_statistics'][v['region']][v['statistic']] for k, v in step['landmarks'].items()}
        assert trace['evaluation_count'] == 1 and trace['native_evaluation_count'] == 4


def test_new_goals_require_factors_and_reject_search(case):
    root,_,_,_,request,_=case
    goals=goals_for(request,'star_cluster')
    goals['phases'][0].pop('factors')
    path=root/'reports/old-goals.json';path.write_text(json.dumps(goals))
    with pytest.raises(ContractError,match='explicit factors'):
        profiles.init_requests(root,'@input',request['regions']['path'],path,'071-default', stepwise=False)
    with pytest.raises(ContractError,match='search'):
        profiles.init_requests(root,'@input',request['regions']['path'],path,'071-search',search=True, stepwise=False)
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_native_proposal_comparison_tone_and_rgb_recomposition(linear_case, monkeypatch):
    import deep_sky_siril_core as core
    import deep_sky_star_reduction as reduction
    from .test_tone_adjust import _tone_script
    session, _, _, parent, *_ = linear_case
    _, request = field_request(session, parent, name='071-agent')
    # RGB MTF tails require their own comparison, covered by test_stretch_tail.
    goals = goals_for(request, 'high_dynamic_range')
    goals.update(schema=profiles.BOUND_GOALS, baseline_factors={'stretch':5.},evidence=source_evidence(session,request['source']))
    goals['phases'][0]['factors'] = {'stretch': 5.}
    goals['phases'][1]['factors'] = {'D': .2, 'B': .5}
    from ._stretch_factory import with_constraint_review
    from ._stretch_factory import with_morphology
    goals = with_morphology(session,request['source'],request['regions'],with_constraint_review(goals, 'compare'))
    path = session/'reports/agent-goals.json';path.write_text(json.dumps(goals))
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    strategy = profiles.init_requests(session, request['source']['path'], request['regions']['path'], path, '072-agent', mode='compare', stepwise=False)
    parameters=[];calls=0
    for variant in profiles.VARIANTS:
        item=strategy['candidates'][variant]
        result = solve_bound(session, item['request'], item['run_id'], timeout=60)
        assert result['status'] == 'solved', result['report'].get('reason')
        calls += sum(s['native_evaluation_count'] for s in result['report']['steps'])
        parameters.append(result['parameters_path'])
    assert calls == 12
    comparison = profiles.compare(session, strategy['strategy'], parameters)
    assert comparison['status'] == 'comparison_ready'
    assets = base.generate_stretch_assets(session, parameters=parameters[1], comparison=comparison['comparison'])
    run = core.run_script(session, protocol='stretch', script_value=assets['script_path'], source_value=parent,
        expected_values=[assets['primary_output'], assets['preview_output']], primary_output_value=assets['primary_output'], timeout=60)
    assert run['status'] == 'success', run
    assert run['processing_checks']['adaptive_parameters']['generation'] == protected.PROPOSAL_POLICY
    draft = core.check_review(session, run['id'], None)['review']
    assert any('selected-full.jpg' in m['path'] for m in draft['inspected_materials'])
    assert len(draft['inspected_materials']) <= 32
    accept(session, run['id'])
    from .test_deep_sky_siril import _session_bytes
    current = _session_bytes(session)
    replayed=core.run_script(session,protocol='stretch',script_value=assets['script_path'],source_value=parent,
        expected_values=[assets['primary_output'],assets['preview_output']],primary_output_value=assets['primary_output'],timeout=60)
    assert replayed['replayed'] and _session_bytes(session)==current
    script, outputs = _tone_script(session, Path(assets['primary_output']), operations='mtf 0 0.45 1')
    tone = core.run_script(session, protocol='tone.adjust', script_value=script, source_value=assets['primary_output'],
        expected_values=outputs, primary_output_value=outputs[0], timeout=60)
    assert tone['status'] == 'success', tone
    assert tone['transfer_chain'] == run['transfer_chain']
    accept(session, tone['id'])
    stars = recompose_assets(session, source=outputs[0], strength=.7)
    combined = core.run_script(session, protocol='stars.recompose', script_value=stars['script_path'], source_value=stars['source'],
        expected_values=stars['expected_outputs'], primary_output_value=stars['primary_output'],
        separation_run='060-stars', stretch_run=run['id'], timeout=60)
    assert combined['status'] == 'success', combined
    assert combined['transfer_chain'] == run['transfer_chain']
    assert max(combined['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5


def test_native_dualband_three_explicit_factor_chains(dual_case, monkeypatch):
    import deep_sky_siril_dualband as dual
    import deep_sky_siril_core as core
    session, _, _, parent, *_ = dual_case
    path, request = field_request(session, parent, name='075-agent-hoo')
    request['schema'] = 'starun-siril.dualband-request.v4'
    step = request['operators'][0];step['factors'] = {'midtones': .1}
    request['operators'] = {role: [copy.deepcopy(step)] for role in dual.ROLES}
    request['oiii_denoise'] = {'enabled': False, 'reason': 'No additional denoising in this explicit synthetic proposal'}
    path.write_text(json.dumps(request))
    monkeypatch.setattr(protected.itertools, 'product', forbid_search)
    path,request=bind_hoo(session,request,'074-channel-plan')
    result = dual.solve(session, path, '075-agent-hoo', 90)
    assert result['status'] == 'solved', result
    report = json.loads(Path(result['report']).read_text())
    for role, binding in report['chains'].items():
        child = json.loads(base.bound_file(session, binding).read_text())
        assert child['request']['schema'] == protected.PROPOSAL_REQUEST
        assert child['generation'] == protected.PROPOSAL_POLICY
        assert child['steps'][0]['native_evaluation_count'] == (4 if role == 'rgb' else 2)
    assets = dual.generate(session, result['report'])
    run = core.run_script(session, protocol='stretch', script_value=assets['script'], source_value=parent,
        expected_values=assets['expected'], primary_output_value=assets['primary_output'], timeout=60)
    assert run['status'] == 'success', run
    assert run['processing_checks']['dualband']['protection']['endpoints']


def test_exact_v24_history_and_parameter_replay(case, monkeypatch):
    root, _, payload, path, _, _ = case
    frozen=json.loads((Path(__file__).parents[1]/'tests/fixtures/stretch-policy-v24.json').read_text())
    assert sessions._STARLESS_STRETCH_EXECUTION_POLICY == frozen['execution_policy']
    # prior=stretch-policy-v24.json: 14 个域标签
    assert_contract_domains(frozen['execution_policy'])
    current = json.loads((Path(__file__).parents[1]/'references/command-policy.json').read_text())
    for command in ('epf','split','pm','rgbcomp','icc_assign'):
        current['protocol_commands']['restoration.denoise-nonlinear'].remove(command)
    for command in ('split', 'pm', 'gauss', 'rgbcomp'):
        current['protocol_commands']['tone.adjust'].remove(command)
    current['protocol_commands']['restoration.denoise'].remove('pyscript')
    assert current['pyscript_protocols'].pop('restoration.denoise') == 'siril_deepsnr.py'
    current['protocol_commands']['color.finish'].remove('fill')
    current['protocol_commands']['color.finish'].remove('invght')
    current['protocol_commands']['stars.recompose'].remove('rgbcomp')
    current['protocol_commands']['stars.recompose'].remove('update_key')
    current['protocol_commands']['stars.recompose'].remove('modasinh')
    assert current == json.loads(frozen['command_policy'])
    with monkeypatch.context() as legacy:
        from ._policy_pins import outgoing_stretch_producer
        outgoing_stretch_producer(legacy)
        legacy.setitem(payload, 'execution_policy', copy.deepcopy(sessions._PRE_MULTISTAGE_BINDINGS['execution_policy']))
        (root/'session.json').write_text(json.dumps(payload))
        legacy.setattr(protected, 'noise_is_diagnostic', lambda payload: False)
        legacy.setattr(base, 'optimization_enabled', lambda payload: False)
        result=solve_bound(root,path,'071-history')
    (root/'session.json').write_text(json.dumps(payload))
    assert result['status']=='solved'
    report_path,report=historical_parameters(root,result,frozen)
    report['bindings'].update(frozen['bindings']);report_path.write_text(json.dumps(report))
    payload['execution_policy']=frozen['execution_policy'];(root/'session.json').write_text(json.dumps(payload))
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    base.load_parameters(root,payload,report_path)
    assert all(p.read_bytes()==b for p,b in before.items())
    with pytest.raises(ContractError,match='replay/finalize-only'):
        base.solve_stretch_parameters(root,path,'072-forbidden')


@pytest.mark.parametrize('profile,marker,extra', [
    ('galaxy_halo', 'Declared galaxy_halo profile is unbound', 'no v23 chain comparison exists'),
    ('custom', 'Explicit custom-profile request', 'no morphology chain is defined or compared'),
])
def test_unbound_proposal_is_rejected_before_native(case, profile, marker, extra):
    root, source, payload, path, request, _ = case
    request['profile'] = profile
    propose(path, request)
    with pytest.raises(ContractError,match='bound morphology'):
        base.solve_stretch_parameters(root,path,'071-unbound')
    assert not list(root.glob('reports/*/scientific-attempt.json'))


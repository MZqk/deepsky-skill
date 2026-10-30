"""Morphology chains, fair native comparisons and immutable strategy evidence."""
import copy
import json
from pathlib import Path

import pytest
import numpy as np

import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_profiles as strategy
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_session as sessions
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, fingerprint, sha256_file
from .test_deep_sky_siril_stretch import context, BINARY, write
from .test_deep_sky_siril import _fake_probe
from .test_stretch_v22 import make_v2, field_request, case
from .test_recomposition_sources_v11 import source_case, linear_case, accept, recompose_assets
from .test_starnet_linear_bridge import native
from ._policy_pins import assert_pinned_history
from ._stretch_factory import solve_bound, historical_parameters, source_evidence

@pytest.fixture(autouse=True)
def retired_operator_producer(monkeypatch, request):
    if request.node.originalname.startswith('test_exact_') or 'document_evidence' in request.node.originalname:
        return
    from ._policy_pins import outgoing_stretch_producer
    outgoing_stretch_producer(monkeypatch)


CHAINS = {
    'emission_nebula': ['ght', 'ght', 'mtf'],
    'high_dynamic_range': ['asinh', 'ght'],
    'galaxy_halo': ['asinh', 'ght', 'mtf'],
    'dust_reflection': ['asinh', 'ght'],
    'star_cluster': ['asinh', 'mtf'],
}


def goals_for(request, profile):
    constraints = request['operators'][0]['constraints']
    protection = [c for c in constraints if c['region'] not in request['targets']]
    subject = [c for c in constraints if c['region'] in request['targets']]
    phases = []
    for method in CHAINS[profile]:
        phase = {'constraints': copy.deepcopy(subject), 'factors': copy.deepcopy({'mtf':{'midtones':.1},'asinh':{'stretch':5.},'ght':{'D':.2,'B':.5}}[method])}
        if method == 'ght':
            target = next(iter(request['targets']))
            phase['landmarks'] = {k: {'region': r, 'statistic': s} for k, r, s in
                                 [('LP', 'sky', 'p99'), ('SP', target, 'median'), ('HP', 'core', 'p01')]}
        phases.append(phase)
    return {'schema': strategy.BOUND_GOALS, 'profile': profile, 'evidence':[request['source']], 'revision':None,
            'reason': 'Synthetic morphology-specific candidate with explicit measured protection regions.',
            'targets': request['targets'], 'core_absence': request['core_absence'],
            'protection': protection, 'phases': phases, 'manual_lookup': request['manual_lookup']}


@pytest.fixture
def strategy_context(context):
    root, source, payload, path, request, _ = context
    regions = make_v2(root, source, request, 3)
    payload['execution_policy'] = copy.deepcopy(EXECUTION_POLICY)
    payload['input'].update(fingerprint(source, include_path=False))
    (root/'session.json').write_text(json.dumps(payload))
    path.write_text(json.dumps(request))
    return root, source, payload, path, request, regions


def comparison_goals(case,profile):
    root, source, payload, _, request, _ = case
    goals = goals_for(request, profile)
    goals['evidence']=source_evidence(root,request['source'])
    goals['baseline_factors'] = {'midtones':.1} if profile=='emission_nebula' else {'stretch':5.}
    if stretch.pixels(source).shape[0]==3 and CHAINS[profile][-1]=='mtf':
        goals['tail']={'purpose':'Explicit same-prefix luminance tail','factors':{'D':.2,'B':.5},
            'landmarks':{k:{'region':r,'statistic':v} for k,r,v in [('LP','sky','p99'),('SP',next(iter(request['targets'])),'median'),('HP','core','p01')]}}
    from ._stretch_factory import with_constraint_review
    from ._stretch_factory import with_morphology
    return with_morphology(root,request['source'],request['regions'],with_constraint_review(goals, 'compare'))


def prepare(case,profile='emission_nebula',run_id='071-strategy'):
    root,source,payload,_,request,_=case
    goals=comparison_goals(case,profile)
    if not strategy.routing_required(payload):
        goals['schema'] = strategy.PROPOSAL_GOALS
        for key in ('evidence','revision','tail','constraint_review'):
            goals.pop(key,None)
    goals_path = root/'reports'/(run_id+'-goals.json')
    goals_path.write_text(json.dumps(goals))
    result = strategy.init_requests(root, request['source']['path'], request['regions']['path'], goals_path, run_id, mode='compare', stepwise=False)
    return result, goals_path


@pytest.mark.parametrize('profile', CHAINS)
def test_profile_templates_preserve_explicit_scientific_goals(strategy_context, profile):
    root, source, payload, _, request, _ = strategy_context
    result, _ = prepare(strategy_context, profile)
    assert result['status'] == 'prepared'
    baseline, selected = [result['candidates'][v] for v in strategy.VARIANTS]
    assert selected['methods'] == CHAINS[profile]
    for candidate in (baseline, selected):
        parsed, parent, _, path = stretch.read_request(root, payload, candidate['request'])
        assert parent == source
        strategy.validate_request(root, payload, parsed, path, candidate['run_id'])
        assert parsed['source'] == request['source']
    left, right = [json.loads(Path(c['request']).read_text()) for c in (baseline, selected)]
    assert left['operators'][-1]['constraints'] == right['operators'][-1]['constraints']
    assert payload['context']['target_type'] == 'nebula'
    with pytest.raises(ContractError, match='already exists'):
        strategy.init_requests(root, '@input', request['regions']['path'], result['strategy'], '071-strategy', mode='compare', stepwise=False)


@pytest.mark.parametrize('issue', ['phase-count', 'sky-budget', 'conflict', 'landmark', 'profile', 'no-evidence'])
def test_invalid_goals_leave_no_strategy_evidence(strategy_context, issue):
    root, _, _, _, request, _ = strategy_context
    goals = comparison_goals(strategy_context,'emission_nebula')
    if issue == 'phase-count':
        goals['phases'].pop()
    elif issue == 'sky-budget':
        goals['protection'] = [r for r in goals['protection'] if r['statistic'] != 'hf_sigma2']
    elif issue == 'conflict':
        goals['phases'][-1]['constraints'].append({'region': 'sky', 'statistic': 'mad', 'min': .8, 'max': 1.})
    elif issue == 'landmark':
        goals['phases'][0]['landmarks']['SP'] = {'region': 'sky', 'statistic': 'median', 'mad_scale': 1.5}
    elif issue == 'profile':
        goals['profile'] = 'custom'
    else:
        goals['reason'] = ''
    path = root/'reports/goals.json'
    path.write_text(json.dumps(goals))
    with pytest.raises(ContractError):
        strategy.init_requests(root, '@input', request['regions']['path'], path, '071-invalid', mode='compare', stepwise=False)
    assert not (root/'reports/071-invalid').exists()


@pytest.mark.parametrize('issue', ['source', 'roi', 'goals', 'runtime', 'methods', 'run-id'])
def test_strategy_drift_is_rejected_before_native_execution(strategy_context, issue):
    root, source, payload, _, request, _ = strategy_context
    result, goals = prepare(strategy_context)
    candidate = result['candidates']['profile']
    if issue == 'source':
        source.write_bytes(source.read_bytes()+b'drift')
    elif issue == 'roi':
        (root/request['regions']['path']).write_text('{}')
    elif issue == 'goals':
        goals.write_text(goals.read_text()+' ')
    elif issue == 'runtime':
        probe = root/'reports/tool-probe.json'
        value = json.loads(probe.read_text())
        value['tools']['siril_cli']['compatible'] = False
        probe.write_text(json.dumps(value))
    elif issue == 'methods':
        path = Path(candidate['request'])
        value = json.loads(path.read_text())
        value['operators'].pop()
        path.write_text(json.dumps(value))
    with pytest.raises(ContractError):
        stretch.solve_stretch_parameters(root, candidate['request'], '071-wrong' if issue == 'run-id' else candidate['run_id'])
    assert not (root/'reports'/candidate['run_id']).exists()


def test_chain_ranking_has_no_step_count_penalty():
    rows = [{'run_id': name, 'status': 'solved', 'audit': {'separation': score, 'sky_hf': hf}, 'transfer_cost': cost}
            for name, score, hf, cost in [('baseline', 100., .03, .1), ('profile', 96., .02, .2)]]
    assert strategy.recommend(rows) == 'profile'
    rows[1]['audit']['separation'] = 94.
    assert strategy.recommend(rows) == 'baseline'
    for row in rows:
        row['audit']['separation'] = None
    assert strategy.recommend(rows) == 'baseline'
    assert strategy.recommend([{'status': 'failed'}]) is None


def test_invalid_native_numbers_are_rejected_per_trial_only_in_v3(strategy_context):
    root, source, _, _, request, regions = strategy_context
    before = stretch.pixels(source)
    broken = before.copy()
    broken[0, 0, 0] = np.nan
    path = write(root/'reports/nonfinite.fit', broken)
    rows = request['operators'][0]['constraints']
    with pytest.raises(ContractError, match='nonfinite_count=1'):
        protected.trial_audit(before, path, regions, rows, request['targets'])
    audit = protected.trial_audit(before, path, regions, rows, request['targets'], reject_nonfinite=True)
    assert audit['nonfinite_count'] == 1
    assert protected.select([{'audit': audit}]) == (None, [])


def test_phase_goals_do_not_force_the_final_target_on_the_first_step(strategy_context):
    root, _, _, _, request, _ = strategy_context
    goals = comparison_goals(strategy_context,'emission_nebula')
    goals['phases'][0]['constraints'][0]['min'] = .01
    goals['phases'][-1]['constraints'][0]['min'] = .12
    goals['phases'][1]['constraints'].append({'region': 'sky', 'statistic': 'mad', 'min': 0., 'max': .02})
    path = root/'reports/phased-goals.json'
    from ._stretch_factory import with_constraint_review
    goals = with_constraint_review(goals, 'compare')
    path.write_text(json.dumps(goals))
    result = strategy.init_requests(root, '@input', request['regions']['path'], path, '071-phased', mode='compare', stepwise=False)
    baseline = json.loads(Path(result['candidates']['baseline']['request']).read_text())
    cascade = json.loads(Path(result['candidates']['profile']['request']).read_text())
    first = next(c for c in cascade['operators'][0]['constraints'] if c['region'] == 'faint')
    last = next(c for c in cascade['operators'][-1]['constraints'] if c['region'] == 'faint')
    assert first['min'] == .01 and last['min'] == .12
    assert baseline['operators'][0]['constraints'] == cascade['operators'][-1]['constraints']
    sky = next(c for c in cascade['operators'][1]['constraints'] if c['region'] == 'sky' and c['statistic'] == 'mad')
    assert sky['max'] == .02


def test_no_completed_attempts_retains_parent(strategy_context, monkeypatch):
    root, _, payload, _, _, _ = strategy_context
    result, _ = prepare(strategy_context, 'star_cluster')
    def timeout(*args):
        raise TimeoutError('Synthetic attempt timeout before native launch')
    monkeypatch.setattr(stretch.NativeTrials, 'run', timeout)
    paths = []
    for variant in strategy.VARIANTS:
        item=result['candidates'][variant]
        attempt = stretch.solve_stretch_parameters(root, item['request'], item['run_id'])
        assert attempt['status'] == 'timed_out'
        paths.append(attempt['parameters_path'])
    compared = strategy.compare(root, result['strategy'], paths)
    assert compared['status'] == 'retain_parent'
    report = strategy.verify_comparison(root, payload, stretch.record(root, Path(compared['comparison'])))
    assert report['recommended_run_id'] is None
    assert all(r['requires_failure_review'] for r in report['candidates'])
    with pytest.raises(ContractError, match='Only solved'):
        stretch.generate_stretch_assets(root, parameters=paths[0], comparison=compared['comparison'])


def test_v22_sessions_cannot_create_new_strategy_evidence(strategy_context):
    root, _, payload, path, request, _ = strategy_context
    payload['execution_policy'] = copy.deepcopy(sessions._ROI_STRETCH_EXECUTION_POLICY)
    with pytest.raises(ContractError, match='replay/finalize-only'):
        stretch.solve_stretch_parameters(root, path, '071-old-solve')
    with pytest.raises(ContractError, match='replay/finalize-only'):
        stretch.generate_stretch_assets(root, legacy_mtf=True)
    goals = root/'reports/old-goals.json'
    goals.write_text(json.dumps(goals_for(request, 'emission_nebula')))
    with pytest.raises(ContractError, match='replay/finalize-only'):
        strategy.init_requests(root, '@input', request['regions']['path'], goals, '071-old-strategy', search=True, stepwise=False)
    assert not (root/'reports/071-old-solve').exists()


def test_exact_v22_parameter_bindings_replay_only_under_the_old_policy(case, monkeypatch):
    root, source, payload, path, _, _ = case
    with monkeypatch.context() as old:
        from ._policy_pins import outgoing_stretch_producer
        outgoing_stretch_producer(old)
        old.setitem(payload, 'execution_policy', copy.deepcopy(sessions._PRE_MULTISTAGE_BINDINGS['execution_policy']))
        (root/'session.json').write_text(json.dumps(payload))
        old.setattr(protected, 'noise_is_diagnostic', lambda _: False)
        old.setattr(stretch, 'optimization_enabled', lambda _: False)
        solved = solve_bound(root,path,'071-old-binding')
    (root/'session.json').write_text(json.dumps(payload))
    assert solved['status'] == 'solved'
    report_path,report=historical_parameters(root,solved,{'execution_policy':sessions._ROI_STRETCH_EXECUTION_POLICY,'bindings':protected._V22_BINDINGS})
    report['bindings'].update(copy.deepcopy(protected._V22_BINDINGS))
    report_path.write_text(json.dumps(report))
    current = copy.deepcopy(payload['execution_policy'])
    payload['execution_policy'] = copy.deepcopy(sessions._ROI_STRETCH_EXECUTION_POLICY)
    (root/'session.json').write_text(json.dumps(payload))
    frozen = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    stretch.load_parameters(root, payload, report_path)
    assert all(p.read_bytes() == data for p, data in frozen.items())
    payload['execution_policy'] = current
    (root/'session.json').write_text(json.dumps(payload))
    with pytest.raises(ContractError, match='implementation|morphology|v4'):
        stretch.load_parameters(root, payload, report_path)


@pytest.mark.parametrize('profile', CHAINS)
def test_native_profile_chain_and_same_parent_comparison(strategy_context, profile):
    if not BINARY.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    root, source, payload, _, _, _ = strategy_context
    (root/'reports/tool-probe.json').write_text(json.dumps(_fake_probe(BINARY)))
    result, goals = prepare(strategy_context, profile)
    parameters = []
    for variant in strategy.VARIANTS:
        candidate = result['candidates'][variant]
        solved = stretch.solve_stretch_parameters(root, candidate['request'], candidate['run_id'], timeout=180)
        assert solved['status'] == 'solved', solved['report'].get('reason')
        parameters.append(solved['parameters_path'])
        report, _, _ = stretch.load_parameters(root, payload, solved['parameters_path'])
        assert [r['command'] for r in report['transfer_chain']] == candidate['methods']
        for phase, trace in zip(report['request']['operators'], report['steps']):
            assert trace['native_evaluation_count'] <= 128
            if phase['method'] == 'ght':
                for key, landmark in phase['landmarks'].items():
                    value = trace['input_statistics'][landmark['region']][landmark['statistic']]
                    assert float(next(p.split('=')[1] for p in trace['selected_parameters']['parameters'] if p.startswith('-'+key+'='))) == pytest.approx(value)
        output = root/report['steps'][-1]['selected']['.fit']['path']
        assert protected.measure_output(root, source, output, report)['status'] == 'passed'
    compared = strategy.compare(root, result['strategy'], parameters)
    binding = stretch.record(root, Path(compared['comparison']))
    assert compared['status'] == 'comparison_ready'
    document = strategy.verify_comparison(root, payload, binding)
    assert document['candidates'][0]['audit']['constraints'] != []
    with pytest.raises(ContractError, match='comparison'):
        stretch.generate_stretch_assets(root, parameters=parameters[1])
    comparisons=[compared['comparison']]
    if 'tail' in result['candidates']:
        item=result['candidates']['tail']
        solved=stretch.solve_stretch_parameters(root,item['request'],item['run_id'],60)
        assert solved['status']=='solved',solved['report'].get('reason')
        tail=strategy.compare_tail(root,parameters[1],solved['parameters_path'],'074-native-tail')
        comparisons.append(tail['comparison'])
    assets=stretch.generate_stretch_assets(root,parameters=parameters[1],comparison=comparisons)
    text=Path(assets['script_path']).read_text()
    frozen=stretch.adaptive_binding(root,payload,source,text,assets['parameters']['transfer_chain'],assets['parameters']['run_id'])
    assert binding in frozen['comparisons']
    stripped='\n'.join(line for line in text.splitlines() if not line.startswith('# stretch-comparison:'))
    with pytest.raises(ContractError,match='comparison'):
        stretch.adaptive_binding(root,payload,source,stripped,assets['parameters']['transfer_chain'],assets['parameters']['run_id'])


def test_native_comparison_review_and_rgb_recomposition(linear_case):
    import deep_sky_siril_core as core
    import deep_sky_star_reduction as reduction
    from .test_deep_sky_siril import _write_ssf_provenance
    session, _, original, parent, *_ = linear_case
    direct, diagnostic = [session/'previews'/('010-input-'+name+'.jpg') for name in ('direct', 'autostretch')]
    inspection = session/'scripts/010-input.ssf'
    inspection.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{original}"\nstat main\n'
                          f'savejpg "{direct.with_suffix("")}" 95\nclose\nload "{original}"\n'
                          f'autostretch -linked -2.8 0.22\nsavejpg "{diagnostic.with_suffix("")}" 95\nclose\n')
    _write_ssf_provenance(session, inspection, 'input.inspect')
    inspected = core.run_script(session, protocol='input.inspect', script_value=inspection, source_value=original,
                                 expected_values=[direct, diagnostic], timeout=60)
    assert inspected['status'] == 'success', inspected
    accept(session, '010-input')
    _, request = field_request(session, parent, name='071-profile')
    goals = session/'reports/profile-goals.json'
    design = goals_for(request, 'high_dynamic_range')
    design['baseline_factors']={'stretch':5.}
    design['evidence']=source_evidence(session,request['source'])
    from ._stretch_factory import with_constraint_review
    from ._stretch_factory import with_morphology
    design=with_morphology(session,request['source'],request['regions'],with_constraint_review(design, 'compare'))
    goals.write_text(json.dumps(design))
    result = strategy.init_requests(session, request['source']['path'], request['regions']['path'], goals, '072-profile', mode='compare', stepwise=False)
    paths = []
    for variant in strategy.VARIANTS:
        candidate=result['candidates'][variant]
        solved = stretch.solve_stretch_parameters(session, candidate['request'], candidate['run_id'], timeout=180)
        assert solved['status'] == 'solved', solved['report'].get('reason')
        paths.append(solved['parameters_path'])
    comparison = strategy.compare(session, result['strategy'], paths)
    profile = next(p for p in paths if '-profile/stretch-parameters.json' in p)
    assets = stretch.generate_stretch_assets(session, parameters=profile, comparison=comparison['comparison'])
    args = dict(protocol='stretch', script_value=assets['script_path'], source_value=parent,
                expected_values=[assets['primary_output'], assets['preview_output']], primary_output_value=assets['primary_output'], timeout=60)
    assert core.run_script(session, **args, validate_only=True)['status'] == 'success'
    receipt = core.run_script(session, **args)
    assert receipt['status'] == 'success', receipt
    assert receipt['adaptive_parameters']['comparisons'][0]['path'].endswith('/comparison.json')
    run_id = assets['parameters']['run_id']
    draft = core.check_review(session, run_id, None)['review']
    assert len(draft['inspected_materials']) <= 32
    draft['inspected_materials'] = [r for r in draft['inspected_materials'] if not r['path'].endswith('comparison-rois.png')]
    draft.update(verdict='accept', gates={key: 'pass' for key in draft['gates']}, notes='Synthetic missing-comparison rejection.')
    path = session/'reviews/missing-comparison.json'
    path.write_text(json.dumps(draft))
    with pytest.raises(ContractError, match='fixed ROI|adoption reason'):
        core.check_review(session, run_id, path)
    path.unlink()
    accept(session, run_id)
    assert core.run_script(session, **args)['replayed']
    from .test_tone_adjust import _tone_script
    tone_script, tone_outputs = _tone_script(session,Path(assets['primary_output']),operations='mtf 0 0.1 1')
    tone = core.run_script(session, protocol='tone.adjust', script_value=tone_script,
                           source_value=assets['primary_output'], expected_values=tone_outputs,
                           primary_output_value=tone_outputs[0], timeout=60)
    assert tone['status'] == 'success', tone
    assert tone['transfer_chain'] == receipt['transfer_chain']
    accept(session, '085-tone')
    final = recompose_assets(session, source=tone_outputs[0], strength=.7)
    run = core.run_script(session, protocol='stars.recompose', script_value=final['script_path'], source_value=final['source'],
                          expected_values=final['expected_outputs'], primary_output_value=final['primary_output'],
                          separation_run='060-stars', stretch_run=run_id, timeout=60)
    assert run['status'] == 'success', run
    assert max(run['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5
    accept(session, run['id'])
    from .test_deep_sky_siril import _delivery_script, _selection
    delivery_script, jpeg = _delivery_script(session, Path(final['primary_output']))
    delivery = core.run_script(session, protocol='delivery.render', script_value=delivery_script,
                               source_value=final['primary_output'], expected_values=[jpeg.with_suffix('.fit'), jpeg],
                               primary_output_value=jpeg.with_suffix('.fit'), timeout=60)
    assert delivery['status'] == 'success', delivery
    review = accept(session, delivery['id'])
    selection = _selection(session, delivery['id'], jpeg, review, status='partial_success',
                            limitations=[{'code': 'synthetic_fixture', 'message': 'Native synthetic lifecycle; no real celestial pixel acceptance.'}])
    finalized = core.finalize_session(session, selection_value=selection, keep_intermediates=True)
    assert finalized['status'] == 'partial_success'
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True) == finalized


def test_exact_v22_history_and_command_authorization():
    root = Path(__file__).parents[1]
    before = json.loads((root/'tests/fixtures/stretch-policy-v22.json').read_text())
    assert_pinned_history(before, session_constant=sessions._ROI_STRETCH_EXECUTION_POLICY,
        command_mode='trim', domains={'stretch_strategy': strategy.POLICY})


def test_v22_document_evidence_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    from ._policy_pins import replay_and_finalize_without_migration
    replay_and_finalize_without_migration(
        tmp_path, monkeypatch, Path(__file__).parents[1] / 'tests/fixtures/stretch-policy-v22.json')

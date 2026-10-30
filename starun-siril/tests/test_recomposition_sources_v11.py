"""Explicit accepted nonlinear sources and native display-domain generation."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from . import test_deep_sky_siril as support
from .test_starnet_linear_bridge import native, Siril, echo
from .test_starnet_precision_v6 import setup_run
from .test_native_processing import write_fits
from .test_hdr_stretch import stretch_text
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
import deep_sky_siril_contract as contract
import deep_sky_star_reduction as reduction
import siril_starnet_linear as bridge

BINARY = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')


def accept(session, run_id):
    draft = core.check_review(session, run_id, None)['review']
    draft.update(verdict='accept', gates={name: 'pass' for name in draft['gates']},
                 notes='Synthetic fixture: inspected all bound review materials.')
    if 'stretch_assessment' in draft:
        assessment = draft['stretch_assessment']
        report = core.load_json(session/assessment['visibility']['path'])
        for row in assessment['structures']:
            row.update(reference_visibility='met', detail_effect='unchanged' if report['controls'] else 'not_applicable',
                       observation='Synthetic fixture: known subject retained in bound same-ROI comparisons.')
            if 'detail_controls' in row:
                row.update(detail_controls=[c['id'] for c in report['controls'] if c['kind'] != 'tail_color_comparison'],
                           evidence=[report['panel']])
        assessment['adoption_reason'] = 'Synthetic fixture: complete reference and control review without a visual quality score.'
    if 'recomposition_assessment' in draft:
        block = draft['recomposition_assessment']
        block.update(goal_status='met', adoption_reason='Known synthetic star and subject fixture retained')
        if 'contribution_assessment' in block:
            block['contribution_assessment'].update(assessment='acceptable', observation='Inspected the synthetic contribution balance')
        for row in block['regions']:
            row.update(assessment='acceptable', observation='Inspected actual fixture core, halo, color and subject material')
            for check in row.get('stellar_checks',{}).values():
                check.update(assessment='acceptable',observation='Inspected the corresponding fixed stellar fixture evidence')
    if 'tone_assessment' in draft:
        block = draft['tone_assessment']
        block.update(goal_status='met', additional_benefit='observed', benefit_observation='Known fixture change',
                     adoption_reason='Known synthetic tone fixture benefit')
        for row in block['regions']:
            row.update(assessment='acceptable', observation='Known synthetic protected region retained')
    if 'color_assessment' in draft:
        block = draft['color_assessment']
        block.update(goal_status='met', additional_benefit='observed', benefit_observation='Known fixture color change',
                     adoption_reason='Known fixture color benefit with acceptable protected regions')
        for row in block['regions']:
            row.update(assessment='acceptable', observation='Actual bound fixture region inspected')
    if draft.get('stretch_assessment', {}).get('artifacts', {}).get('report'):
        packet = contract.load_json(session/draft['stretch_assessment']['artifacts']['report']['path'])
        for row in draft['stretch_assessment']['artifacts']['areas']:
            row.update(baseline='present', change='unchanged', observation='Actual synthetic artifact focus crop inspected.',
                evidence=[packet['panel']], decision_reason='The controlled synthetic texture is retained.')
    path = session / 'reviews' / f'{run_id}.json'
    core.atomic_write_json(path, draft)
    assert core.check_review(session, run_id, path)['status'] == 'review_valid'
    return path


@pytest.fixture
def linear_case(native, monkeypatch, request):
    if not BINARY.is_file():
        pytest.skip('Native Siril 1.4.4 unavailable')
    mode=getattr(request,'param',None)
    if mode is None:
        spec=getattr(getattr(request,'node',None),'callspec',None)
        mode=spec.params.get('source_case','single') if spec else 'single'
    real_run = subprocess.run
    metrics = core._try_generate_run_metrics
    def source_image(path, *, width, height, **kwargs):
        y, x = np.mgrid[:height, :width]
        image = .02 + .00001*x + .00002*y + .2*np.exp(-((x-width/2)**2+(y-height/2)**2)/200)
        if mode == 'flat-noise':
            image = .02+np.random.default_rng(9).normal(0, .0001, (height, width))
        rgb = np.stack([image*.95, image, image*.9])
        if mode in {'selective', 'selective-tied'}:
            rgb = np.stack([image*.55, image*.7, image*(.7 if mode == 'selective-tied' else .65)])
            rgb[:, :, width//2:] = np.stack([image, image*.2, image*.2])[:, :, width//2:]
        write_fits(path, rgb)
    monkeypatch.setattr(support, '_write_fits', source_image)
    session, original, document, options = setup_run(native, monkeypatch)
    full, starless, stars, receipt_path = options['expected_values'][:4]
    values, scale, zero = core.artifacts.read_fits_pixels(original)
    values = (np.asarray(values, dtype=float) * scale + zero).astype('float32')

    def separated_echo(argv, **kwargs):
        result = echo(argv, **kwargs)
        pixels, _ = bridge.read_native_fits(Path(argv[4]))
        write_fits(Path(argv[4]), pixels * .9)
        return result

    def execute(argv, **kwargs):
        script = Path(next(value.split('=', 1)[1] for value in argv if value.startswith('--script=')))
        if script.stem != '060-stars':
            return real_run([str(BINARY), *argv[1:]], **kwargs)
        write_fits(full, values)
        with monkeypatch.context() as scoped:
            scoped.setattr(bridge.subprocess, 'run', separated_echo)
            evidence = bridge.process_starless(Siril(full), document, Path(document['work_dir']))
        shutil.copyfile(evidence['argv'][4], starless)
        write_fits(stars, values - bridge.read_native_fits(starless)[0])
        receipt = {'schema': bridge.RECEIPT_SCHEMA, 'status': 'verified',
                   'contract_sha256': contract.sha256_file(receipt_path.with_name('contract.json')),
                   **{key: document[key] for key in ('source', 'runtime', 'stride')}, **evidence}
        receipt_path.write_text(json.dumps(receipt))
        for preview in options['expected_values'][4:]:
            support._write_jpeg(preview, size=(512, 512))
        return subprocess.CompletedProcess(argv, 0, 'log: Script execution finished successfully.\n')

    monkeypatch.setattr(core.subprocess, 'run', execute)
    result = core.run_script(session, **options)
    assert result['status'] == 'success', result
    accept(session, '060-stars')
    monkeypatch.setattr(core, '_try_generate_run_metrics', metrics)
    return session, None, original, starless, stars, None, options


@pytest.fixture
def source_case(linear_case, request):
    session, _, original, starless, stars, _, options = linear_case
    full = options['expected_values'][0]
    mode = getattr(request, 'param', 'single')
    values, _, _ = core.artifacts.read_fits_pixels(starless)
    region_path = session/'reports/manual-stretch-regions.json'
    contract.atomic_write_json(region_path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(starless), 'width': 512, 'height': 512},
        'regions': [{'id': 'sky', 'role': 'background', 'rect': [10, 10, 42, 42]},
                    {'id': 'shell', 'role': 'faint', 'rect': [240, 240, 272, 272]},
                    {'id': 'core', 'role': 'core', 'rect': [250, 250, 262, 262]},
                    {'id': 'stars', 'role': 'stars', 'rect': [250, 250, 262, 262]}]})
    import deep_sky_siril_stretch as stretch
    from ._stretch_factory import bind_exception
    constraints = [dict(region=r, statistic=s, min=0., max=1.) for r,s in
        [('sky','median'),('sky','mad'),('sky','hf_sigma2'),('shell','median'),
         ('core','p999'),('core','span'),('stars','p999'),('stars','span')]]
    operators = [{'method':'mtf', 'purpose':'Explicit synthetic display transfer',
                  'factors':{'midtones':.1}, 'constraints':constraints}]
    if mode not in {'chain', 'hdr'}:
        operators += [{'method':'asinh', 'purpose':'Retain synthetic core gradation after the first display step',
                       'factors':{'stretch':1.01}, 'constraints':constraints}]
    if mode == 'chain':
        operators += [{'method':'asinh','purpose':'Synthetic color-preserving interval', 'factors':{'stretch':2.},'constraints':constraints},
            {'method':'ght','purpose':'Synthetic current-domain detail interval', 'factors':{'D':.2,'B':0.},'constraints':constraints,
             'landmarks':{k:{'region':r,'statistic':s} for k,r,s in
                [('LP','sky','p99'),('SP','shell','median'),('HP','core','p99')]}}]
    request = {'schema':'starun-siril.stretch-request.v4', 'source':{'path':starless.relative_to(session).as_posix(),'sha256':contract.sha256_file(starless)},
        'regions':stretch.record(session,region_path), 'profile':'custom', 'targets':{'shell':'bright'}, 'core_absence':None,
        'operators':operators,'manual_lookup':{'status':'not_needed','reason':'Explicit fixture factors from the documented operators','evidence':[]}}
    if mode=='flat-noise':
        measured=stretch.statistics(stretch.pixels(starless),contract.load_json(region_path)['regions'])
        request['targets']['shell']='bright' if measured['shell']['median']>measured['sky']['median'] else 'dark'
    if mode == 'hdr':
        text, primary, expected = stretch_text(session, starless, values.shape[0])
        branches = {'gentle':[{'command':'mtf','parameters':['0','0.2','1']}],
                    'faint':[{'command':'mtf','parameters':['0','0.06','1']}],
                    'mask':{'mask_low':.15,'mask_high':.75,'sigma':2}}
        item = bind_exception(session, request, '069-hdr-route', hdr=branches)
        bound = contract.load_json(Path(item['request']))['strategy']['plan']
        text = '# stretch-strategy: '+json.dumps(bound)+'\n'+text
        guard = {k:request[k] for k in ('source','regions','profile','targets','core_absence')}
        guard.update(schema='starun-siril.stretch-protection.v2', full_source=stretch.record(session,full), constraints=constraints)
        guard_path=session/'reports/hdr-protection.json';contract.atomic_write_json(guard_path,guard)
        text='# stretch-protection: '+json.dumps(stretch.record(session,guard_path))+'\n'+text
        g,f,m=[session/'artifacts'/('hdr-full-'+name+'.fit') for name in ('gentle','faint','mixed')]
        view=session/'previews/hdr-full.jpg'
        text+=(f'load "{full}"\nmtf 0 0.2 1\nsave "{g}" -chksum\nclose\n'
               f'load "{full}"\nmtf 0 0.06 1\nsave "{f}" -chksum\nclose\n'
               f'pm "$artifacts/hdr-full-gentle$ * $artifacts/070-mask-rgb$ + $artifacts/hdr-full-faint$ * (1 - $artifacts/070-mask-rgb$)" -nosum\n'
               f'save "{m}" -chksum\nicc_assign sRGB\nsavejpg "{view.with_suffix("")}" 95\nclose\n')
        expected += [g,f,m,view]
        text = '# stretch-visibility-regions: '+json.dumps(request['regions'])+'\n'+text
        script = session/'scripts/070-stretch.ssf'; script.write_text(text)
        support._write_ssf_provenance(session, script, 'stretch')
    else:
        item = bind_exception(session, request, '069-fixture-route')
        solved = stretch.solve_stretch_parameters(session, item['request'], item['run_id'], timeout=60)
        assert solved['status'] == 'solved', solved['report'].get('reason')
        assets = stretch.generate_stretch_assets(session, parameters=solved['parameters_path'], run_id='070-stretch')
        script, primary = Path(assets['script_path']), Path(assets['primary_output'])
        expected = [primary, Path(assets['preview_output'])]
    result = core.run_script(session, protocol='stretch', script_value=script, source_value=starless,
                            expected_values=expected, primary_output_value=primary, timeout=30)
    assert result['status'] == 'success', result
    review = accept(session, '070-stretch')
    return session, primary, original, starless, stars, review, options


def recompose_assets(session_path, *, source, strength=.65):
    """Build an explicit fixture proposal; the constant is a test choice, never a runtime default."""
    session = Path(session_path)
    prepared = reduction.prepare(session, source, '089-recomposition-input')
    data = reduction.read_scientific_pixels(session/prepared['source']['path'])[0]
    height, width = data.shape[1:]
    regions = session/'reports/recomposition-regions.json'
    contract.atomic_write_json(regions, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': prepared['source']['sha256'], 'width': width, 'height': height},
        'regions': [{'id': 'subject', 'role': 'faint', 'rect': [width//3, height//3, width//2, height//2]},
                    {'id': 'stars', 'role': 'stars', 'rect': [width//8, height//8, width//4, height//4]}]})
    request = session/'reports/recomposition-request.json'
    contract.atomic_write_json(request, {'schema': reduction.REQUEST, 'source': prepared['source'],
        'prepared': prepared['prepared'], 'regions': reduction.record(session, regions), 'strength': strength,
        'goal': 'Explicit synthetic fixture contribution', 'parameter_reason': 'Selected by the regression case',
        'manual_lookup': {'status': 'not_needed', 'reason': 'Documented frozen recipe', 'evidence': []}})
    return reduction.generate_recompose_assets(session, request=request, run_id='090-recompose')


def generated_run(session, result, *, validate_only=False):
    return core.run_script(session, protocol='stars.recompose', script_value=result['script_path'],
        source_value=result['source'], expected_values=result['expected_outputs'],
        primary_output_value=result['primary_output'], separation_run='060-stars', stretch_run='070-stretch',
        timeout=30, validate_only=validate_only)


@pytest.mark.parametrize('source_case', ['single', 'chain', 'hdr'], indirect=True)
@pytest.mark.parametrize('strength', [.3, 1.])
def test_generated_native_recomposition_uses_exact_source_chain_and_closes(source_case, strength):
    session, parent, *_ = source_case
    before = {path: path.read_bytes() for path in session.rglob('*') if path.is_file()}
    result = recompose_assets(session_path=session, source=Path(parent.relative_to(session)), strength=strength)
    assert result['source'] == str(parent) and result['strength'] == strength
    assert all(path.read_bytes() == data for path, data in before.items())
    validated = generated_run(session, result, validate_only=True)
    assert validated['status'] == 'success' and validated['mode'] == 'validate_only'
    run = generated_run(session, result)
    assert run['status'] == 'success', run
    assert run['source_path'] == parent.relative_to(session).as_posix()
    assert max(run['processing_checks']['closure']['per_channel_max_abs']) <= 1e-5
    assert max(run['processing_checks']['closure']['per_channel_final_max_abs']) <= 1e-5
    accept(session, '090-recompose')
    before = support._session_bytes(session)
    assert generated_run(session, result)['replayed']
    assert support._session_bytes(session) == before


def test_generation_resolves_the_actual_accepted_stretch_ancestor(source_case):
    session, _, _, starless, *_ = source_case
    source = add_stretch_branch(session, starless)
    result = recompose_assets(session, source=source, strength=1.)
    assert result['source'] == str(source)
    assert '070-stretch' not in Path(result['script_path']).read_text()


@pytest.mark.parametrize('issue', ['linear', 'auxiliary', 'outside', 'symlink', 'missing', 'source-drift',
                                   'receipt-drift', 'review-drift', 'duplicate-review', 'reject', 'uncertain',
                                   'finalized', 'ssf-exists', 'provenance-exists', 'mid-read-drift',
                                   'missing-receipt', 'missing-review', 'runtime-drift', 'knowledge-drift', 'output-symlink'])
def test_generation_refuses_wrong_or_drifting_materials_without_generated_asset_writes(source_case, monkeypatch, issue):
    session, parent, original, starless, stars, review, _ = source_case
    source = parent
    if issue in {'linear', 'auxiliary', 'outside'}:
        source = {'linear': starless, 'auxiliary': stars, 'outside': original}[issue]
    elif issue == 'symlink':
        source = session / 'artifacts/alias.fit'; source.symlink_to(parent)
    elif issue == 'missing':
        parent.unlink()
    elif issue == 'source-drift':
        parent.write_bytes(parent.read_bytes() + b'drift')
    elif issue == 'receipt-drift':
        path = session / 'runs/070-stretch.json'; path.write_bytes(path.read_bytes() + b' ')
    elif issue == 'review-drift':
        record = contract.load_json(review); record['run_receipt_sha256'] = '0' * 64
        review.write_text(json.dumps(record))
    elif issue == 'duplicate-review':
        shutil.copyfile(review, session / 'reviews/duplicate.json')
    elif issue in {'reject', 'uncertain'}:
        record = contract.load_json(review); record['verdict'] = issue
        if issue == 'reject': record['gates']['stars'] = 'fail'
        review.write_text(json.dumps(record))
    elif issue == 'other-stretch':
        source = add_stretch_branch(session, starless)
    elif issue == 'missing-receipt':
        (session / 'runs/060-stars.json').unlink()
    elif issue == 'missing-review':
        review.unlink()
    elif issue == 'runtime-drift':
        runtime = contract.load_json(session / 'reports/tool-probe.json')['tools']['siril_cli']
        Path(runtime['path']).write_bytes(b'changed runtime')
    elif issue == 'knowledge-drift':
        references = reduction.protocol_reference_bindings
        def changed(protocol):
            records = references(protocol)
            records[0]['sha256'] = '0' * 64
            return records
        monkeypatch.setattr(reduction, 'protocol_reference_bindings', changed)
    elif issue == 'output-symlink':
        full = session / 'artifacts/full.fit'
        saved = session.parent / 'preserved-full.fit'; saved.write_bytes(full.read_bytes())
        full.unlink(); full.symlink_to(saved)
    elif issue == 'finalized':
        manifest = contract.load_json(session / 'manifest.json'); manifest['finalization'] = {'state': 'committed'}
        contract.atomic_write_json(session / 'manifest.json', manifest)
    elif issue in {'ssf-exists', 'provenance-exists'}:
        path = session / ('scripts/090-recompose.ssf' if issue == 'ssf-exists' else 'scripts/090-recompose.provenance.json')
        path.write_bytes(b'existing immutable asset')
    else:
        unchanged = reduction._unchanged
        def drift(*args):
            parent.write_bytes(parent.read_bytes() + b'drift')
            return unchanged(*args)
        monkeypatch.setattr(reduction, '_unchanged', drift)
    before = support._session_bytes(session)
    with pytest.raises(contract.ContractError):
        recompose_assets(session, source=source)
    # The intentional drift is external to generation; no SSF/provenance is published.
    for name in ('090-recompose.ssf', '090-recompose.provenance.json'):
        path = session / 'scripts' / name
        relative = path.relative_to(session).as_posix()
        if relative in before:
            assert path.is_file() and path.read_bytes() == before[relative]
        else:
            assert not path.exists()
    if issue != 'mid-read-drift':
        after = support._session_bytes(session)
        assert all(after.get(name) == data for name, data in before.items())
        assert all(name.startswith('reports/089-recomposition-input/') or name in {
            'reports/recomposition-regions.json', 'reports/recomposition-request.json'} for name in after.keys()-before.keys())


def add_stretch_branch(session, starless):
    import copy
    import deep_sky_siril_stretch as stretch
    from ._stretch_factory import bind_exception
    request=contract.load_json(session/'reports/069-fixture-route/exception-request.json')
    request.pop('strategy');request['operators']=copy.deepcopy(request['operators'][:2])
    request['operators'][0]['factors']={'midtones':.2}
    item=bind_exception(session,request,'068-alternative-route')
    solved=stretch.solve_stretch_parameters(session,item['request'],item['run_id'])
    assert solved['status']=='solved',solved['report'].get('reason')
    assets=stretch.generate_stretch_assets(session,parameters=solved['parameters_path'],run_id='071-alternate')
    run=core.run_script(session,protocol='stretch',script_value=assets['script_path'],source_value=starless,
        expected_values=[assets['primary_output'],assets['preview_output']],primary_output_value=assets['primary_output'],timeout=30)
    assert run['status']=='success',run
    accept(session,'071-alternate')
    return Path(assets['primary_output'])


def test_generate_requires_a_proposal_but_compute_interface_stays_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv',
                        ['reduction', 'generate', '--session', str(tmp_path)])
    with pytest.raises(SystemExit) as exc: reduction.main()
    assert exc.value.code == 2 and not list(tmp_path.iterdir())
    with pytest.raises(TypeError): reduction.generate_recompose_assets(tmp_path)
    assert reduction.compute_adaptive_star_strength('nebula', 'dualband-osc', .2, .005)[0] == .5


def test_current_policy_changes_guidance_only_and_retains_complete_predecessor():
    assert {**{k: v for k, v in sessions._MULTISCALE_EXECUTION_POLICY.items() if k not in {'local_contrast', 'color_curve', 'dualband'}}, 'guidance': 'stage4_xisf_inputs_v10'} == sessions._RESTORATION_EXECUTION_POLICY
    assert sessions.known_execution_policy(sessions._RESTORATION_EXECUTION_POLICY)
    assert not sessions.execution_policy_is_current({'execution_policy': sessions._RESTORATION_EXECUTION_POLICY})


@pytest.mark.parametrize('verdict', ['accept', 'reject', 'uncertain'])
def test_generation_uses_explicit_nonstandard_stage8_parent(source_case, verdict):
    session, parent, *_ = source_case
    output = session / 'artifacts/detail/custom-finished.fit'
    output.parent.mkdir()
    preview = session / 'previews/custom-finished.jpg'
    script = session / 'scripts/081-reviewed-detail.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{parent}"\ndenoise -nocosmetic -mod=0.2\nsave "{output}" -chksum\nsavejpg "{preview.with_suffix("")}" 95\nclose\n')
    support._write_ssf_provenance(session, script, 'restoration.denoise-nonlinear')
    run = core.run_script(session, protocol='restoration.denoise-nonlinear', script_value=script,
                         source_value=parent, expected_values=[output, preview], primary_output_value=output, timeout=30)
    assert run['status'] == 'success', run
    draft = core.check_review(session, run['id'], None)['review']
    draft.update(verdict=verdict, notes='Synthetic explicit Stage 8 parent review.',
                 gates={name: 'pass' for name in draft['gates']})
    if verdict == 'reject': draft['gates']['structure'] = 'fail'
    contract.atomic_write_json(session / 'reviews/081-reviewed-detail.json', draft)
    # A familiar filename contains a stale unrelated hint; generation must ignore it.
    contract.atomic_write_json(session / 'runs/080-contrast.json', {'primary_output': 'artifacts/wrong.fit'})
    before = support._session_bytes(session)
    if verdict != 'accept':
        with pytest.raises(contract.ContractError, match='Accepted visual review'):
            recompose_assets(session, source=output)
        assert support._session_bytes(session) == before
        return
    result = recompose_assets(session, source=output)
    assert '$artifacts/detail/custom-finished$' in Path(result['script_path']).read_text()
    assert generated_run(session, result)['status'] == 'success'


@pytest.mark.parametrize('issue', ['linear-star-layer', 'independent-stretch', 'missing-replay', 'changed-chain'])
def test_execution_rejects_domain_mismatch_with_replay_remedy(source_case, issue):
    session, parent, _, _, stars, *_ = source_case
    result = recompose_assets(session, source=parent, strength=1.)
    path = Path(result['script_path']); text = path.read_text()
    original = text
    transfer = next(row for row in processing.script_commands(text) if row[0] == 'mtf')
    command = ' '.join(transfer)
    if issue == 'linear-star-layer':
        text = text.replace('1 * $artifacts/090-recompose-display-stars$', '1.00 * $artifacts/layer$')
    elif issue == 'independent-stretch':
        text = text.replace('save "'+str(session / 'artifacts/090-recompose-display-stars.fit')+'"',
                            'asinh -human 2 0 -clipmode=rgbblend\nsave "'+str(session / 'artifacts/090-recompose-display-stars.fit')+'"')
    elif issue == 'missing-replay': text = text.replace(command+'\n', '')
    else:
        transfer[2] = '.2'
        text = text.replace(command, ' '.join(transfer), 1)
    assert text != original
    path.write_text(text)
    support._write_ssf_provenance(session, path, 'stars.recompose')
    with pytest.raises(contract.ContractError) as error:
        generated_run(session, result, validate_only=True)
    assert any(value in str(error.value) for value in ('display_stars', 'frozen chain', 'display space', 'unverified session artifact'))
    assert not (session / 'runs/090-recompose.json').exists()


def test_generated_recomposition_can_finalize_without_rewriting_evidence(source_case, monkeypatch):
    session, parent, original, *_ = source_case
    result = recompose_assets(session, source=parent, strength=1.)
    assert generated_run(session, result)['status'] == 'success'
    recompose_review = accept(session, '090-recompose')
    execute = core.subprocess.run
    inspection, direct, diagnostic = support._input_inspect_run(session, original, monkeypatch)
    support._review(session, inspection['id'], diagnostic, parent_preview=direct)
    monkeypatch.setattr(core.subprocess, 'run', execute)
    script, candidate = support._delivery_script(session, Path(result['primary_output']))
    delivery = core.run_script(session, protocol='delivery.render', script_value=script,
                              source_value=result['primary_output'], expected_values=[candidate, candidate.with_suffix('.fit')],
                              timeout=30)
    assert delivery['status'] == 'success', delivery
    delivery_review = accept(session, delivery['id'])
    selection = support._selection(session, delivery['id'], candidate, delivery_review)
    record = contract.load_json(selection)
    record['selected_runs'] = ['060-stars', '070-stretch', '090-recompose', delivery['id']]
    record['review_receipts'] = ['reviews/060-stars.json', 'reviews/070-stretch.json',
                                 recompose_review.relative_to(session).as_posix(), delivery_review.relative_to(session).as_posix()]
    contract.atomic_write_json(selection, record)
    before = {path: path.read_bytes() for path in [session / 'session.json', *list((session / 'runs').glob('*.json')), *list((session / 'reviews').glob('*.json'))]}
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert all(path.read_bytes() == data for path, data in before.items())
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'



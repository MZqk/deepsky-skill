"""Native Stage 6 visibility references, safe MTF seeds and fixed review panels."""
from __future__ import annotations

import copy
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw

import deep_sky_siril_stretch as base
from deep_sky_siril_contract import RUN_ID_PATTERN, atomic_write_json, load_json, session_path

SCHEMA = 'starun-siril.stretch-visibility.v1'
PROPOSAL = 'starun-siril.reference-baseline.v1'
ROOTS = ('reports', 'runs', 'reviews', 'scripts', 'artifacts', 'previews')
REVIEW_CONTRACT = 'roi_reference_assessment_v1'
BOUND_REVIEW_CONTRACT = 'roi_reference_assessment_v2'


def stage6_reference(session, payload, source):
    from deep_sky_siril_processing import source_state
    from deep_sky_siril_core import _load_run, _accepted_review
    state = source_state(session, payload, source)
    if not state['separation_run']:
        from deep_sky_siril_stretch_profiles import routing_required
        if not routing_required(payload):
            return None
        from deep_sky_siril_session import _verified_success_receipts
        for receipt in reversed(_verified_success_receipts(session)):
            try:
                _, script, preview, transfer = _reference_transfer(session, receipt)
                review, review_binding = _accepted_review(session, receipt['id'])
            except base.ContractError:
                continue
            primary, _, _, _ = _reference_transfer(session, receipt)
            return {'separation_run': receipt['id'], 'kind': 'accepted_linear_reference',
                'receipt': base.record(session, session/'runs'/(receipt['id']+'.json')),
                'review': base.record(session, session_path(session, review_binding['path'], must_exist=True, allowed_roots=('reviews',))),
                'script': base.record(session, script), 'source': source_record(session, primary),
                'replay_source': source_record(session, source), 'preview': base.record(session, preview), 'transfer': transfer}
        base.fail('Full-stars stretch requires an accepted native autostretch reference before scientific review')
    path, receipt = _load_run(session, state['separation_run'])
    review, _ = _accepted_review(session, receipt['id'])
    review_path = session/'reviews'/(receipt['id']+'.json')
    if not review_path.is_file() or load_json(review_path) != review:
        candidates = [p for p in (session/'reviews').glob('*.json') if load_json(p).get('run_id') == receipt['id']]
        if len(candidates) != 1:
            base.fail('Visibility reference requires one accepted separation review')
        review_path = candidates[0]
    primary, script, preview, transfer = _reference_transfer(session, receipt)
    return {'separation_run': receipt['id'], 'receipt': base.record(session, path),
            'review': base.record(session, review_path), 'script': base.record(session, script),
            'source': base.record(session, primary), 'preview': base.record(session, preview),
            'transfer': transfer}


def _reference_transfer(session, receipt):
    from deep_sky_siril_processing import script_commands
    primary = ((Path(load_json(session/'session.json')['input']['path']) if receipt['source_path']=='@input' else session/receipt['source_path'])
               if receipt['protocol']=='input.inspect' and not receipt.get('primary_output') else session/receipt['primary_output'])
    script = session/receipt['script']['path']
    commands = script_commands(script.read_text())
    current, matches = None, []
    from deep_sky_siril_validation import _normalized_write
    for i, row in enumerate(commands):
        if row[0] in {'load', 'save'}:
            current = Path(_normalized_write(row[0], row[1]))
            if not current.is_absolute():
                current = session/current
        elif row[0] == 'pm':
            current = None
        elif row[0] == 'autostretch':
            if current == primary and i+1 < len(commands) and commands[i+1][0] == 'savejpg':
                preview = Path(_normalized_write('savejpg', commands[i+1][1]))
                preview = preview if preview.is_absolute() else session/preview
                matches.append((row, preview))
            current = None
    if len(matches) != 1:
        base.fail('Locate exactly one native autostretch preview of the accepted Stage 6 starless source')
    row, preview = matches[0]
    if len(row) != 4 or row[1] != '-linked':
        base.fail('Visibility reference requires an explicit linked autostretch')
    if not any(item['path'] == preview.relative_to(session).as_posix() for item in receipt['outputs']):
        base.fail('Stage 6 visibility preview is not a committed output')
    return primary, script, preview, [{'command': row[0], 'parameters': row[1:]}]


def _oracle(session, directory, deadline):
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    oracle = base.NativeTrials(session, runtime, directory, directory, deadline)
    oracle.previews = True
    return oracle


def _records(session, output, evidence):
    return {'science': base.record(session, output), 'preview': base.record(session, output.with_suffix('.jpg')),
            'script': evidence['script'], 'log': evidence['log']}


def _bound(session, record):
    if isinstance(record, dict) and record.get('path') == '@input':
        return base.source_file(session, load_json(session/'session.json'), {key: record[key] for key in ('path', 'sha256')})
    return base.bound_file(session, record, ROOTS)


def source_record(session, source):
    payload = load_json(session/'session.json')
    if source == Path(payload['input']['path']):
        return {'path': '@input', 'sha256': payload['input']['sha256'], 'size': source.stat().st_size}
    return base.record(session, source)


def _panel(reference, candidate, regions, path, comparisons=(), reference_label='Stage 6 reference'):
    pairs = [(reference_label+' | candidate', reference, candidate), *comparisons]
    panels = []
    for title, left, right in pairs:
        with Image.open(left) as first, Image.open(right) as second:
            panels.append(_pair_panel(first, second, regions, title))
    canvas = Image.new('RGB', (640, sum(panel.height for panel in panels)), '#161616')
    y = 0
    for panel in panels:
        canvas.paste(panel, (0, y))
        y += panel.height
    with Image.open(reference) as original:
        canvas.save(path, icc_profile=original.info.get('icc_profile'))


def _pair_panel(first, second, regions, title):
    if first.size != second.size:
        base.fail('Visibility views must have identical geometry')
    rows = [('Full frame', None), *[(r['id'], r['rect']) for r in regions]]
    canvas = Image.new('RGB', (640, 170*len(rows)), '#161616')
    draw = ImageDraw.Draw(canvas)
    for index, (label, rect) in enumerate(rows):
        draw.text((5, index*170+3), label+' / '+title, fill='white')
        for column, original in enumerate((first, second)):
            crop = original.convert('RGB')
            if rect is not None:
                crop = crop.crop(tuple(rect))
            crop.thumbnail((310, 140))
            canvas.paste(crop, (column*320+5, index*170+24))
    return canvas


def inverse_midtones(x, target):
    x, target = base.number(x), base.number(target)
    if not 0 < x < 1 or not 0 < target < 1:
        base.fail('Reference anchor must have finite interior input and display medians')
    result = x*(target-1)/(2*x*target-target-x)
    if not base.PROTOCOL_MIN_MTF <= result <= base.PROTOCOL_MAX_MTF:
        base.fail('Reference MTF seed is outside the existing native range; choose another anchor')
    return result


def propose(session_value, request_value, anchor_region, run_id, timeout=1800):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    from deep_sky_siril_stretch_v2 import PROPOSAL_REQUEST, full_is_diagnostic, subject_constraints, audit
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        base.fail('Historical sessions are replay/finalize-only')
    from deep_sky_siril_stretch_profiles import routing_required
    if routing_required(payload):
        base.fail('New strategy-bound sessions require explicit factors; unbound baseline inversion is legacy-only')
    if not RUN_ID_PATTERN.fullmatch(run_id) or not 1 <= base.number(timeout) <= 86400:
        base.fail('Invalid reference baseline run ID or timeout')
    request, source, regions, request_path = base.read_request(session, payload, request_value)
    if (request['schema'] != PROPOSAL_REQUEST or request['profile'] != 'custom' or 'strategy' in request
        or 'baseline_proposal' in request or request['operators'][0]['method'] != 'mtf'):
        base.fail('Baseline preparation requires an explicit custom v4 request starting with MTF')
    if anchor_region not in {r['id'] for r in regions if r['role'] == 'faint'}:
        base.fail('Choose a declared faint-structure ROI as the reference anchor')
    full = base.accepted_parent(session, payload, source)
    origin = stage6_reference(session, payload, source)
    if origin is None:
        base.fail('Reference baseline requires an accepted Stage 6 starless branch')
    data = base.pixels(source)
    black = base.safe_blackpoint([data]+([base.pixels(full)] if full and not full_is_diagnostic(payload) else []))
    constraints = subject_constraints(request['operators'][0]['constraints'], regions,
        full_diagnostic=bool(full) and full_is_diagnostic(payload))
    directory = session_path(session, f'reports/{run_id}', allowed_roots=('reports',))
    if directory.exists() or directory.is_symlink():
        base.fail('Reference baseline evidence already exists; use a new run ID')
    directory.mkdir()
    oracle = _oracle(session, directory, time.monotonic()+timeout)
    reference, evidence = oracle.run(_bound(session, origin['source']), origin['transfer'])
    ref = base.pixels(reference)
    if ref.shape != data.shape:
        base.fail('Stage 6 reference and current linear parent differ in geometry')
    input_median = base.statistics(data, regions)[anchor_region]['median']
    target = base.statistics(ref, regions)[anchor_region]['median']
    midtones = inverse_midtones((input_median-black)/(1-black), target)
    chain = [{'command': 'mtf', 'parameters': [format(x, '.17g') for x in (black, midtones, 1.)]}]
    trial, trial_evidence = oracle.run(source, chain)
    actual = base.pixels(trial)
    report = {'schema': PROPOSAL, 'source': base.record(session, source), 'regions': request['regions'],
              'input_request': base.record(session, request_path), 'origin': origin,
              'reference': _records(session, reference, evidence), 'trial': _records(session, trial, trial_evidence),
              'anchor_region': anchor_region, 'input_median': input_median, 'reference_median': target,
              'safe_blackpoint': black, 'factors': {'midtones': midtones}, 'transfer': chain,
              'audit': audit(data, actual, regions, constraints, request['targets'],
                             noise_diagnostic=True), 'requires_visual_review': True}
    if full:
        report['full_source'] = base.record(session, full)
        full_trial, full_evidence = oracle.run(full, chain)
        report['full_trial'] = _records(session, full_trial, full_evidence)
    proposed = copy.deepcopy(request)
    proposed['operators'][0]['factors'] = report['factors']
    report['operator'] = proposed['operators'][0]
    report_path = directory/'baseline-proposal.json'
    atomic_write_json(report_path, report)
    proposed['baseline_proposal'] = base.record(session, report_path)
    output = directory/'baseline-request.json'
    atomic_write_json(output, proposed)
    return {'status': 'proposed', 'request': str(output), 'proposal': str(report_path),
            'factors': report['factors'], 'eligible': report['audit']['eligible'], 'requires_visual_review': True}


def verify_proposal(session, request):
    try:
        return _verify_proposal(session, request)
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        base.fail(f'Malformed reference baseline evidence: {type(exc).__name__}')


def _verify_proposal(session, request):
    path = _bound(session, request['baseline_proposal'])
    report = load_json(path)
    if (report.get('schema') != PROPOSAL or report.get('operator') != request['operators'][0]
        or report.get('regions') != request['regions']):
        base.fail('Baseline proposal, first MTF operator or regions changed')
    original = load_json(_bound(session, report['input_request']))
    expected = copy.deepcopy(original)
    expected['operators'][0]['factors'] = report['factors']
    if expected['operators'][0] != request['operators'][0]:
        base.fail('Reference baseline operator changed; prepare a new proposal')
    # Later operators are independent proposals; the baseline binds only the first MTF and its context.
    expected['operators'] = request['operators']
    expected['baseline_proposal'] = request['baseline_proposal']
    if request != expected:
        base.fail('Reference baseline request changed; prepare a new proposal')
    source = _bound(session, report['source'])
    if request['source'] != {key: report['source'][key] for key in ('path', 'sha256')}:
        base.fail('Baseline proposal belongs to another scientific parent')
    regions = load_json(_bound(session, report['regions']))['regions']
    _verify_origin(session, report['origin'])
    _verify_trial(session, report['reference'], _bound(session, report['origin']['source']), report['origin']['transfer'])
    _verify_trial(session, report['trial'], source, report['transfer'])
    data = base.pixels(source)
    from deep_sky_siril_stretch_v2 import full_is_diagnostic, subject_constraints, audit
    layers = [data]
    if 'full_source' in report:
        full = _bound(session, report['full_source'])
        _verify_trial(session, report['full_trial'], full, report['transfer'])
        if not full_is_diagnostic(load_json(session/'session.json')):
            layers.append(base.pixels(full))
    black = base.safe_blackpoint(layers)
    target = base.statistics(base.pixels(_bound(session, report['reference']['science'])), regions)[report['anchor_region']]['median']
    x = base.statistics(data, regions)[report['anchor_region']]['median']
    factors = {'midtones': inverse_midtones((x-black)/(1-black), target)}
    transfer = [{'command': 'mtf', 'parameters': [format(n, '.17g') for n in (black, factors['midtones'], 1.)]}]
    actual = base.pixels(_bound(session, report['trial']['science']))
    constraints = subject_constraints(request['operators'][0]['constraints'], regions,
        full_diagnostic=bool(report.get('full_source')) and full_is_diagnostic(load_json(session/'session.json')))
    if (report['safe_blackpoint'] != black or report['reference_median'] != target or report['input_median'] != x
        or report['factors'] != factors or report['transfer'] != transfer
        or report['audit'] != audit(data, actual, regions, constraints,
                                    request['targets'], noise_diagnostic=True)
        or report.get('requires_visual_review') is not True):
        base.fail('Reference baseline arithmetic or scientific protection changed')
    return report


def _verify_trial(session, group, source, chain):
    from deep_sky_siril_processing import script_commands, transfer_chain
    for record in group.values():
        _bound(session, record)
    output = _bound(session, group['science'])
    script = _bound(session, group['script']).read_text()
    commands = script_commands(script)
    loads = [row for row in commands if row[0] == 'load']
    saves = [row for row in commands if row[0] == 'save']
    previews = [row for row in commands if row[0] == 'savejpg']
    if (len(loads) != 1 or Path(loads[0][1]) != source or len(saves) != 1 or Path(saves[0][1]) != output
        or transfer_chain(script, output) != chain or ['icc_assign', 'sRGB'] not in commands
        or len(previews) != 1 or Path(previews[0][1]).with_suffix('.jpg') != _bound(session, group['preview'])):
        base.fail('Visibility native script differs from its source, transfer or sRGB views')


def _verify_origin(session, origin):
    for key in ('receipt', 'review', 'script', 'source', 'preview'):
        _bound(session, origin[key])
    entries = load_json(session/'manifest.json')['runs']
    if not any(entry['id'] == origin['separation_run'] and entry.get('status') == 'success'
               and entry['receipt_sha256'] == origin['receipt']['sha256'] for entry in entries):
        base.fail('Visibility reference is not a committed separation receipt')
    receipt = load_json(_bound(session, origin['receipt']))
    review = load_json(_bound(session, origin['review']))
    primary, script, preview, transfer = _reference_transfer(session, receipt)
    outputs = {row['path']: row for row in receipt['outputs']}
    if ((receipt['protocol'] != 'stars.separate' and origin.get('kind') != 'accepted_linear_reference') or receipt['status'] != 'success'
        or receipt['id'] != origin['separation_run'] or review['run_id'] != receipt['id']
        or any(origin[key] != (source_record(session, path) if key=='source' else base.record(session, path)) for key, path in
               (('source', primary), ('script', script), ('preview', preview)))
        or origin['preview']['sha256'] != outputs[origin['preview']['path']]['sha256']
        or origin['source']['sha256'] != (receipt['source']['sha256'] if receipt['protocol']=='input.inspect' and not receipt.get('primary_output') else outputs[origin['source']['path']]['sha256'])
        or origin['script']['sha256'] != receipt['script']['sha256'] or origin['transfer'] != transfer
        or review['verdict'] != 'accept' or review['run_receipt_sha256'] != origin['receipt']['sha256']):
        base.fail('Visibility reference separation or acceptance changed')
    if origin.get('kind') == 'accepted_linear_reference':
        _bound(session, origin['replay_source'])
        domain = receipt['image_domain']
        if receipt['protocol'] == 'input.inspect':
            from deep_sky_siril_session import effective_session_payload
            domain = effective_session_payload(session, load_json(session/'session.json'))['context']['input_state']
        if domain != 'linear':
            base.fail('Reference replay must use the current accepted linear parent')


def _control_specs(session, source, primary, processing):
    """Resolve real controls from validated processing evidence, never from filenames."""
    from deep_sky_siril_stretch_profiles import TAIL_COMPARISON_SCHEMAS
    def spec(name, kind, input_path, chain, output, evidence=()):
        return {'id': name, 'kind': kind, 'input': base.record(session, input_path),
                'transfer': chain, 'candidate': base.record(session, output), 'evidence': list(evidence)}

    dual = processing.get('dualband_transfer')
    if dual:
        parent = load_json(_bound(session, dual['parameters']))
        result = []
        for role in ('ha', 'oiii'):
            child_binding = parent['chains'][role]
            child = load_json(_bound(session, child_binding))
            if len(child['transfer_chain']) > 1:
                first = child['steps'][0].get('selected', {}).get('.fit')
                initial = _bound(session, first or child['bindings']['derived_source'])
                result.append(spec(role, 'monochrome_first_prefix', initial,
                    [] if first else child['transfer_chain'][:1],
                    session/dual['layers'][role+'-stretched'], (dual['parameters'], child_binding)))
        return result
    hdr = processing.get('hdr_transfer')
    if hdr:
        return [spec('hdr', 'hdr_gentle', session/hdr['layers']['gentle'], [], primary)]
    chain = processing.get('transfer_chain', [])
    if len(chain) <= 1:
        return []
    adaptive = processing.get('adaptive_parameters')
    if adaptive:
        report = load_json(_bound(session, adaptive['parameters']))
        if 'comparisons' in adaptive:
            controls = []
            first = report['steps'][0].get('selected', {}).get('.fit')
            if first:
                controls.append(spec('base', 'first_prefix', _bound(session, first), [], primary, (adaptive['parameters'],)))
            for binding in adaptive['comparisons']:
                comparison = load_json(_bound(session, binding))
                if comparison.get('schema') in TAIL_COMPARISON_SCHEMAS:
                    other = next(row for row in comparison['candidates'] if row['parameters'] != adaptive['parameters'])
                    solved = other['status'] == 'solved'
                    control = other['selected'] if solved else other['diagnostic']
                    controls.append(spec('tail', 'tail_color_comparison' if solved else 'tail_goal_not_met', _bound(session, control['.fit']), [],
                        primary, (binding, other['parameters'])))
                else:
                    baseline = next(row for row in comparison['candidates'] if row['variant'] == 'baseline')
                    if baseline['status'] == 'solved':
                        controls.append(spec('single', 'morphology_single_curve', _bound(session, baseline['selected']['.fit']), [],
                            primary, (binding, baseline['parameters'])))
            return controls
        if adaptive.get('comparison'):
            comparison = load_json(_bound(session, adaptive['comparison']))
            if comparison.get('schema') in TAIL_COMPARISON_SCHEMAS:
                other = next(row for row in comparison['candidates'] if row['parameters'] != adaptive['parameters'])
                solved = other['status'] == 'solved'
                control = other['selected'] if solved else other['diagnostic']
                return [spec('tail', 'tail_color_comparison' if solved else 'tail_goal_not_met', _bound(session, control['.fit']), [],
                             primary, (adaptive['comparison'], other['parameters']))]
            baseline = next(row for row in comparison['candidates'] if row['variant'] == 'baseline')
            if baseline['status'] == 'solved':
                return [spec('base', 'morphology_single_curve', _bound(session, baseline['selected']['.fit']), [],
                             primary, (adaptive['comparison'], baseline['parameters']))]
        first = report['steps'][0].get('selected', {}).get('.fit')
        if first:
            return [spec('base', 'first_prefix', _bound(session, first), [], primary, (adaptive['parameters'],))]
    return [spec('base', 'first_prefix', source, chain[:1], primary)]


def build(session, payload, source, primary, processing, run_id, timeout):
    origin = stage6_reference(session, payload, source)
    if origin is None:
        return None
    adaptive = processing.get('adaptive_parameters', {})
    hdr = processing.get('hdr_transfer', {}).get('protection', {})
    dual = processing.get('dualband_transfer', {})
    region_record = adaptive.get('bindings', {}).get('regions') or hdr.get('regions') or dual.get('regions')
    if region_record is None:
        script = session/'scripts'/(run_id+'.ssf')
        declarations = [line.split(':', 1)[1].strip() for line in script.read_text().splitlines()
                        if line.strip().startswith('# stretch-visibility-regions:')]
        if len(declarations) == 1:
            region_record = base.strict_json_bytes(declarations[0].encode(), document='visibility regions')
    if region_record is None:
        base.fail('Stage 7 starless review requires protected request regions for reference comparison')
    regions_path = _bound(session, region_record)
    from deep_sky_siril_metrics import measure_regions
    if measure_regions(primary, source, regions_path)['status'] != 'measured':
        base.fail('Visibility regions must bind the current parent and candidate geometry')
    regions = load_json(regions_path)['regions']
    directory = session_path(session, f'reports/{run_id}/visibility', allowed_roots=('reports',))
    if directory.exists() or directory.is_symlink():
        base.fail('Visibility review evidence already exists')
    directory.mkdir(parents=True)
    oracle = _oracle(session, directory, time.monotonic()+timeout)
    reference, evidence = oracle.run(_bound(session, origin.get('replay_source', origin['source'])), origin['transfer'])
    candidate, candidate_evidence = oracle.run(primary, [])
    if base.pixels(reference).shape != base.pixels(primary).shape:
        base.fail('Visibility comparison requires unchanged Stage 6 geometry')
    controls, comparisons = [], []
    for spec in _control_specs(session, source, primary, processing):
        initial, initial_evidence = oracle.run(_bound(session, spec['input']), spec['transfer'])
        output = _bound(session, spec['candidate'])
        final, final_evidence = ((candidate, candidate_evidence) if output == primary else oracle.run(output, []))
        controls.append({**spec, 'baseline_view': _records(session, initial, initial_evidence),
                         'candidate_view': _records(session, final, final_evidence)})
        comparisons.append((spec['id']+' / '+spec['kind']+' | final',
                            initial.with_suffix('.jpg'), final.with_suffix('.jpg')))
    panel = directory/'reference-candidate-rois.png'
    _panel(reference.with_suffix('.jpg'), candidate.with_suffix('.jpg'), regions, panel, comparisons,
           reference_label='Accepted linear reference' if origin.get('kind') == 'accepted_linear_reference' else 'Stage 6 reference')
    from deep_sky_siril_stretch_profiles import routing_required
    report = {'schema': SCHEMA, 'origin': origin, 'source': source_record(session, source),
              'candidate': base.record(session, primary), 'regions': region_record,
              'reference': _records(session, reference, evidence), 'candidate_view': _records(session, candidate, candidate_evidence),
              'panel': base.record(session, panel), 'display': 'native_srgb_same_geometry_and_roi_scale',
              'statistics': {'reference': base.statistics(base.pixels(reference), regions),
                             'candidate': base.statistics(base.pixels(primary), regions)},
              'requires_visual_review': True, 'review_contract': BOUND_REVIEW_CONTRACT if routing_required(payload) else REVIEW_CONTRACT, 'controls': controls}
    if routing_required(payload):
        report['diagnostics'] = assessment_diagnostics(session, report, processing)
    path = directory/'visibility.json'
    atomic_write_json(path, report)
    return base.record(session, path)


def review_paths(session, binding, receipt=None):
    report = load_json(_bound(session, binding))
    if report.get('schema') != SCHEMA or report.get('display') != 'native_srgb_same_geometry_and_roi_scale':
        base.fail('Invalid cross-stage visibility evidence')
    _verify_origin(session, report['origin'])
    if report['origin'].get('replay_source', report['source']) != report['source']:
        base.fail('Visibility reference replay differs from the current linear parent')
    if receipt is not None and (receipt['protocol'] != 'stretch'
        or receipt['source_path'] != report['source']['path']
        or receipt['source']['sha256'] != report['source']['sha256']
        or receipt['primary_output'] != report['candidate']['path']
        or not any(row['path'] == report['candidate']['path'] and row['sha256'] == report['candidate']['sha256']
                   for row in receipt['outputs'])):
        base.fail('Visibility comparison belongs to another formal source or candidate')
    _verify_trial(session, report['reference'], _bound(session, report['origin'].get('replay_source', report['origin']['source'])), report['origin']['transfer'])
    _verify_trial(session, report['candidate_view'], _bound(session, report['candidate']), [])
    if 'review_contract' in report:
        if report['review_contract'] not in {REVIEW_CONTRACT, BOUND_REVIEW_CONTRACT} or not isinstance(report.get('controls'), list):
            base.fail('Unknown Stage 7 review contract')
        specs = [{key: value for key, value in control.items() if key not in {'baseline_view', 'candidate_view'}}
                 for control in report['controls']]
        if receipt is not None and specs != _control_specs(session, _bound(session, report['source']),
                                                          _bound(session, report['candidate']), receipt):
            base.fail('Stage 7 detail controls differ from the actual frozen branches')
        for control in report['controls']:
            for record in [control['input'], control['candidate'], *control['evidence']]:
                _bound(session, record)
            _verify_trial(session, control['baseline_view'], _bound(session, control['input']), control['transfer'])
            _verify_trial(session, control['candidate_view'], _bound(session, control['candidate']), [])
    records = [binding, report['source'], report['candidate'], report['regions'], report['panel']]
    records += [report['origin'][key] for key in ('receipt', 'review', 'script', 'source', 'preview')]
    for group in ('reference', 'candidate_view'):
        records += list(report[group].values())
    paths = {record['path']: _bound(session, record) for record in records}
    regions = load_json(_bound(session, report['regions']))['regions']
    actual = {'reference': base.statistics(base.pixels(_bound(session, report['reference']['science'])), regions),
              'candidate': base.statistics(base.pixels(_bound(session, report['candidate'])), regions)}
    if actual != report['statistics'] or report.get('requires_visual_review') is not True:
        base.fail('Visibility region statistics or review requirement changed')
    if report.get('review_contract') == BOUND_REVIEW_CONTRACT and receipt is not None:
        if report.get('diagnostics') != assessment_diagnostics(session, report, receipt):
            base.fail('Stage 7 comparison diagnostics changed')
    # The receipt binds the full report; one panel preserves HOO's 32-item limit and shows both full views and ROIs.
    return {report['panel']['path']: paths[report['panel']['path']]}


def same_region_pixels(session, report, control, reference):
    regions = load_json(_bound(session, reference['regions']))['regions']
    rect = next(row['rect'] for row in regions if row['id'] == reference['id'])
    x0, y0, x1, y1 = rect
    a = base.pixels(_bound(session, control['baseline_view']['science']))
    b = base.pixels(_bound(session, control['candidate_view']['science']))
    a = a[:, a.shape[1]-y1:a.shape[1]-y0, x0:x1]
    b = b[:, b.shape[1]-y1:b.shape[1]-y0, x0:x1]
    return a.shape == b.shape and np.array_equal(a, b)


def assessment_diagnostics(session, report, processing):
    adaptive = processing.get('adaptive_parameters', {})
    parameters = load_json(_bound(session, adaptive['parameters'])) if adaptive else None
    dual = load_json(_bound(session, processing['dualband_transfer']['parameters'])) if processing.get('dualband_transfer') else None
    strategy = (parameters['request'].get('strategy', {}).get('plan') if parameters else
                dual['request'].get('strategy') if dual else processing.get('hdr_transfer', {}).get('strategy'))
    revision = None
    if strategy:
        from deep_sky_siril_stretch_profiles import load_plan
        payload = load_json(session/'session.json')
        plan, _ = load_plan(session, payload, strategy)
        revision = plan.get('revision_diff')
    previous_statistics = None
    previous_proposal = None
    if revision:
        goals = load_json(_bound(session, plan['goals']))
        previous = load_json(_bound(session, goals['revision']['parameters']))
        trials = previous.get('steps', [{}])[-1].get('trials', []) if previous.get('steps') else []
        previous_statistics = trials[0]['audit'].get('statistics') if trials else None
        old_chain = [step.get('selected_parameters') or step['trials'][0]['parameters'] for step in previous['steps'] if step.get('trials')]
        previous_proposal = {'parameters': goals['revision']['parameters'], 'actual_native_chain': old_chain,
            'statistics': previous_statistics, 'derived_source': previous['bindings'].get('derived_source')}
        if previous_proposal['derived_source']:
            previous_statistics = None
        revision = {**revision, 'target_change': goals['revision']['target_change'],
                    'reason': goals['revision']['reason'], 'affected_regions': goals['revision']['affected_regions'],
                    'same_scientific_transform': bool(parameters) and len(old_chain) == len(previous['request']['operators']) and old_chain == parameters['transfer_chain']}
    regions = load_json(_bound(session, report['regions']))['regions']
    skies = [row['id'] for row in regions if row['role'] == 'background']
    control_values = {control['id']: {kind: base.statistics(base.pixels(_bound(session, control[key]['science'])), regions)
                      for kind,key in (('baseline','baseline_view'),('candidate','candidate_view'))} for control in report['controls']}
    rows = {}
    for region in regions:
        name = region['id']
        rows[name] = {kind: {'statistics': report['statistics'][kind][name],
            'contrast_to_sky': {sky: report['statistics'][kind][name]['median'] - report['statistics'][kind][sky]['median'] for sky in skies}}
            for kind in ('reference', 'candidate')}
        rows[name]['controls'] = {key: {kind: {'statistics': values[name], 'contrast_to_sky': {
            sky: values[name]['median']-values[sky]['median'] for sky in skies}} for kind,values in pair.items()}
            for key,pair in control_values.items()}
        if previous_statistics and name in previous_statistics:
            rows[name]['previous_proposal'] = previous_statistics[name]
            rows[name]['change_from_previous'] = {key: value - previous_statistics[name][key]
                for key, value in report['statistics']['candidate'][name].items() if key in previous_statistics[name]}
    transfers = processing.get('transfer_chain')
    if dual:
        transfers = {role: load_json(_bound(session, binding))['transfer_chain'] for role, binding in dual['chains'].items()}
    if processing.get('hdr_transfer'):
        transfers = {key: processing['hdr_transfer'][key] for key in ('gentle_chain', 'faint_chain')}
    return {'actual_transfer_chain': transfers, 'revision': revision, 'previous_proposal': previous_proposal,
            'new_image_benefit_from_revision': False if revision and revision['same_scientific_transform'] else None,
            'regions': rows, 'interpretation': 'Measurements identify review questions; no universal brightness or contrast pass threshold.'}


def assessment_skeleton(session, binding):
    report = load_json(_bound(session, binding))
    if report.get('review_contract') not in {REVIEW_CONTRACT, BOUND_REVIEW_CONTRACT}:
        return None
    regions = load_json(_bound(session, report['regions']))['regions']
    block = {'visibility': binding, 'structures': [
        {'region': {'regions': report['regions'], 'id': row['id']},
         'reference_visibility': None, 'detail_effect': None, 'observation': '', 'regression': None}
        for row in regions if row['role'] in {'faint', 'core'}], 'adoption_reason': ''}
    if report['review_contract'] == BOUND_REVIEW_CONTRACT:
        for row in block['structures']:
            row.update(detail_controls=[], evidence=[])
    return block


def validate_assessment(session, run, review):
    try:
        _validate_assessment(session, run, review)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, ValueError, OSError) as exc:
        base.fail(f'Malformed Stage 7 assessment: {type(exc).__name__}')


def _validate_assessment(session, run, review):
    binding = run.get('stretch_visibility')
    report = load_json(_bound(session, binding)) if binding else {}
    block = review.get('stretch_assessment')
    if report.get('review_contract') not in {REVIEW_CONTRACT, BOUND_REVIEW_CONTRACT}:
        if 'stretch_assessment' in review:
            base.fail('Stage 7 assessment requires its marked reference report')
        return
    from deep_sky_siril_artifact_focus import enabled as artifact_enabled
    fields = {'visibility', 'structures', 'adoption_reason'} | ({'artifacts'} if artifact_enabled(load_json(session/'session.json')) else set())
    if (not isinstance(block, dict) or set(block) != fields
        or block['visibility'] != binding):
        base.fail('Stage 7 assessment must bind this run and express its structural conclusions')

    def text(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000

    if not text(block['adoption_reason']):
        base.fail('Stage 7 assessment requires an actual adoption reason')
    from deep_sky_siril_metrics import measure_regions
    source, candidate = _bound(session, report['source']), _bound(session, report['candidate'])
    cache = {}

    def region(reference):
        if not isinstance(reference, dict) or set(reference) != {'regions', 'id'}:
            base.fail('Stage 7 assessment requires bound region IDs')
        record = reference['regions']
        if (not isinstance(record, dict) or not {'path', 'sha256'} <= record.keys()
            or set(record)-{'path', 'sha256', 'size'}):
            base.fail('Invalid assessment region binding')
        path = _bound(session, record)
        region_path = path.relative_to(session).as_posix()
        if 'size' in record and (type(record['size']) is not int or record['size'] != path.stat().st_size):
            base.fail('Assessment region size changed')
        if region_path not in cache:
            measured = measure_regions(candidate, source, path)
            if measured['status'] != 'measured':
                base.fail('Assessment regions must bind the current parent and geometry')
            cache[region_path] = {row['id']: row for row in load_json(path)['regions']}
        row = cache[region_path].get(reference['id'])
        if row is None or row['role'] not in {'core', 'faint'}:
            base.fail('Unknown or non-subject Stage 7 assessment ROI')
        return (region_path, reference['id']), row

    required_path = _bound(session, report['regions']).relative_to(session).as_posix()
    required = {(required_path, row['id']) for row in
                load_json(_bound(session, report['regions']))['regions'] if row['role'] in {'core', 'faint'}}
    if not isinstance(block['structures'], list) or not 1 <= len(block['structures']) <= 64:
        base.fail('Stage 7 assessment requires 1..64 explicit structures')
    allowed = [report['panel'], report['candidate'], base.record(session, session/'runs'/(run['id']+'.json'))]
    for key in ('reference', 'candidate_view'):
        allowed += [report[key]['science'], report[key]['preview']]
    for control in report['controls']:
        allowed += [control['input'], control['candidate'], *control['evidence']]
        for key in ('baseline_view', 'candidate_view'):
            allowed += [control[key]['science'], control[key]['preview']]
    seen = set()
    bound_review = report['review_contract'] == BOUND_REVIEW_CONTRACT
    for row in block['structures']:
        fields = {'region', 'reference_visibility', 'detail_effect', 'observation', 'regression'}
        if bound_review:
            fields |= {'detail_controls', 'evidence'}
        if not isinstance(row, dict) or set(row) != fields:
            base.fail('Incomplete Stage 7 structural conclusion')
        key, _ = region(row['region'])
        if key in seen:
            base.fail('Duplicate Stage 7 assessment ROI')
        seen.add(key)
        reference, detail = row['reference_visibility'], row['detail_effect']
        if (reference not in {'met', 'partial', 'not_met', 'uncertain'}
            or detail not in {'improved', 'unchanged', 'regressed', 'not_applicable', 'uncertain'}
            or not text(row['observation'])):
            base.fail('Stage 7 assessment needs explicit reference and detail observations')
        if (detail == 'not_applicable') != (not report['controls']):
            base.fail('Detail assessment must match the actual presence of a baseline control')
        if review['verdict'] == 'accept' and key in required and (reference == 'uncertain' or detail == 'uncertain'):
            base.fail('Accepted Stage 7 assessment cannot leave required structures uncertain')
        if bound_review:
            if review['verdict'] == 'accept' and key in required and reference == 'not_met':
                base.fail('Accepted Stage 7 assessment cannot leave a primary structure not_met')
            controls = {control['id']: control for control in report['controls']
                        if control['kind'] not in {'tail_color_comparison', 'tail_goal_not_met'}}
            ids = row['detail_controls']
            if (not isinstance(ids, list) or len(ids) != len(set(ids)) or any(i not in controls for i in ids)
                or bool(ids) != bool(controls)):
                base.fail('Detail conclusions must name actual subject-enhancement controls')
            if (not isinstance(row['evidence'], list) or not row['evidence']
                or not any(e.get('path') == report['panel']['path'] and e.get('sha256') == report['panel']['sha256'] for e in row['evidence'])):
                base.fail('Structural conclusions must cite the actual same-display comparison panel')
            for evidence in row['evidence']:
                if not any(evidence.get('path') == known.get('path') and evidence.get('sha256') == known.get('sha256') for known in allowed):
                    base.fail('Structural evidence is not a bound same-source comparison')
                _bound(session, evidence)
            if detail == 'improved' and any(same_region_pixels(session, report, controls[i], row['region']) for i in ids):
                base.fail('Identical scientific pixels cannot demonstrate improved detail against the named control')
        loss = row['regression']
        if reference in {'partial', 'not_met'} or detail == 'regressed':
            if (not isinstance(loss, dict) or set(loss) != {'loss', 'core_conflict', 'core_region', 'evidence', 'tradeoff_reason'}
                or not text(loss['loss']) or not text(loss['tradeoff_reason'])
                or loss['core_conflict'] not in {'demonstrated', 'not_demonstrated', 'uncertain'}
                or not isinstance(loss['evidence'], list) or len(loss['evidence']) > 32):
                base.fail('Stage 7 regression requires its loss, core-conflict conclusion and tradeoff reason')
            if bound_review and not loss['evidence']:
                base.fail('Partial adoption requires actual comparison evidence, including style tradeoffs')
            if loss['core_region'] is not None and region(loss['core_region'])[1]['role'] != 'core':
                base.fail('Claimed core conflict must name an actual core ROI')
            for evidence in loss['evidence']:
                if (not isinstance(evidence, dict) or not {'path', 'sha256'} <= evidence.keys()
                    or set(evidence)-{'path', 'sha256', 'size'}
                    or not any(all(evidence[key] == known[key] for key in ('path', 'sha256')) for known in allowed)):
                    base.fail('Core-conflict evidence is not a bound same-source comparison or report')
                path = _bound(session, evidence)
                if 'size' in evidence and (type(evidence['size']) is not int or evidence['size'] != path.stat().st_size):
                    base.fail('Core-conflict evidence size changed')
            if loss['core_conflict'] == 'demonstrated' and (loss['core_region'] is None or not loss['evidence']):
                base.fail('Demonstrated core conflict requires a core ROI and actual comparison evidence')
        elif loss is not None:
            base.fail('Regression explanation must correspond to a declared visibility loss or regression')
    if not required <= seen:
        base.fail('Stage 7 assessment omitted required subject or core ROIs')

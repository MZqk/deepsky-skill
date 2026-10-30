"""Build morphology-guided stretch requests and compare verified native chains."""
from __future__ import annotations

import copy
import json
from itertools import zip_longest
from pathlib import Path
import tempfile

import numpy as np

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
from deep_sky_siril_contract import fingerprint, fingerprint_matches, load_json, sha256_file

POLICY = 'morphology_chain_comparison_v1'
REQUEST = 'starun-siril.stretch-request.v3'
GOALS = 'starun-siril.stretch-goals.v1'
PROPOSAL_GOALS = 'starun-siril.stretch-goals.v2'
PLAN = 'starun-siril.stretch-strategy.v1'
COMPARISON = 'starun-siril.stretch-comparison.v1'
VARIANTS = ('baseline', 'profile')
BOUND_PLAN = 'starun-siril.stretch-strategy.v2'
BOUND_GOALS = 'starun-siril.stretch-goals.v3'
ROUTING_POLICY = 'morphology_multistage_v1'
HDR_POLICY = 'morphology_selected_v1'


def routing_required(payload):
    return payload.get('execution_policy', {}).get('stretch_routing') in {'profile_bound_v1', ROUTING_POLICY}


def multistage_required(payload):
    return payload.get('execution_policy', {}).get('stretch_routing') == ROUTING_POLICY


def validate_body_chain(payload, chain):
    if multistage_required(payload) and len(chain) < 2:
        base.fail('Stage 7 body candidates require at least two executed stretch steps')


def read_json(path):
    return base.strict_json_bytes(path.read_bytes(), document='stretch strategy')


def merge_constraints(protection, phase, *, issues=None, location=None):
    """Intersect phase goals with shared protection; never relax a shared budget."""
    if not isinstance(protection, list) or not isinstance(phase, list):
        base.fail('Strategy protection and phase constraints must be lists')
    rows = {}
    for row in [*protection, *phase]:
        if not isinstance(row, dict) or set(row) != {'region', 'statistic', 'min', 'max'}:
            base.fail('Strategy constraints require region, statistic, min and max')
        if not isinstance(row['region'], str) or not isinstance(row['statistic'], str):
            base.fail('Strategy constraint identifiers must be strings')
        key = (row['region'], row['statistic'])
        value = {**row, 'min': base.number(row['min']), 'max': base.number(row['max'])}
        if key in rows:
            value.update(min=max(rows[key]['min'], value['min']), max=min(rows[key]['max'], value['max']))
        if value['min'] > value['max']:
            if issues is None:
                base.fail('Phase goals conflict with shared protection')
            issues.append({'code': 'shared_protection_conflict', **(location or {}),
                           'region': key[0], 'statistic': key[1], 'min': value['min'], 'max': value['max']})
        rows[key] = value
    return list(rows.values())


def candidate_run_id(run_id, variant):
    return run_id + ('-base' if variant == 'baseline' else '-' + variant)


def bound_requests_for(plan, goals, binding, *, issues=None):
    mode = plan.get('mode')
    fields = {'schema', 'profile', 'reason', 'evidence', 'targets', 'core_absence',
              'protection', 'manual_lookup', 'revision'}
    fields.add('operators' if mode == 'exception' else 'phases')
    if mode == 'compare':
        fields.add('baseline_factors')
    optional = {'tail', 'channels', 'hdr', 'constraint_review', 'morphology', 'artifact_focus'}
    profiles = load_json(protected.PROFILE_PATH)
    if (mode not in {'profile', 'compare', 'exception'} or not isinstance(goals, dict)
        or goals.get('schema') != BOUND_GOALS or set(goals) - optional != fields
        or not isinstance(goals.get('profile'), str) or goals['profile'] not in profiles or goals['profile'] == 'custom'
        or not isinstance(goals['reason'], str) or not goals['reason'].strip()
        or not isinstance(goals['evidence'], list) or not 1 <= len(goals['evidence']) <= 16
        or not isinstance(goals['protection'], list) or not goals['protection']):
        base.fail('Bound goals require a morphology, source evidence, reason and shared protection')
    if mode == 'exception':
        operators = copy.deepcopy(goals['operators'])
        if not isinstance(operators, list) or not operators:
            base.fail('Measured exceptions require explicit operators')
        for index, step in enumerate(operators, 1):
            if not isinstance(step, dict) or 'constraints' not in step:
                base.fail('Exception operators require their explicit constraints')
            step['constraints'] = merge_constraints(goals['protection'], step['constraints'], issues=issues,
                location={'variant': 'exception', 'step': index})
    else:
        phases = goals['phases']
        recipe = profiles[goals['profile']]['preferred_chain']
        if not isinstance(phases, list) or len(phases) != len(recipe):
            base.fail('Initial operators must match the exact morphology chain')
        operators = []
        for index, (definition, phase) in enumerate(zip(recipe, phases), 1):
            required = {'constraints', 'factors'} | ({'landmarks'} if definition['method'] == 'ght' else set())
            if not isinstance(phase, dict) or set(phase) != required:
                base.fail('Profile phases require exact explicit factors and current-domain landmarks')
            operators.append({**copy.deepcopy(phase), 'method': definition['method'],
                'purpose': definition['purpose'] + ' ' + goals['reason'],
                'constraints': merge_constraints(goals['protection'], phase['constraints'], issues=issues,
                    location={'variant': 'profile', 'step': index})})
    common = {'schema': protected.PROPOSAL_REQUEST, 'source': plan['source'], 'regions': plan['regions'],
              'profile': 'custom' if mode == 'exception' else goals['profile'], 'targets': goals['targets'],
              'core_absence': goals['core_absence'], 'manual_lookup': goals['manual_lookup']}
    if 'artifact_focus' in goals:
        common['artifact_focus'] = goals['artifact_focus']
    chains = {'exception' if mode == 'exception' else 'profile': operators}
    if mode == 'compare':
        chains['baseline'] = [{'method': profiles[goals['profile']]['baseline'],
            'purpose': 'Explicit same-parent morphology control. ' + goals['reason'],
            'factors': copy.deepcopy(goals['baseline_factors']),
            'constraints': copy.deepcopy(operators[-1]['constraints'])}]
    if 'tail' in goals:
        tail = goals['tail']
        if (operators[-1].get('method') != 'mtf' or len(operators) < 2
            or not isinstance(tail, dict) or set(tail) != {'purpose', 'factors', 'landmarks'}):
            base.fail('Tail controls require an MTF chain and explicit luminance GHS factors')
        chains['tail'] = copy.deepcopy(operators[:-1]) + [{**copy.deepcopy(tail), 'method': 'ght',
            'constraints': copy.deepcopy(operators[-1]['constraints'])}]
    if 'channels' in goals:
        if not isinstance(goals['channels'], dict) or set(goals['channels']) != {'rgb', 'ha', 'oiii'}:
            base.fail('HOO strategy channels must declare exact RGB, Ha and OIII operators')
        for role, channel in goals['channels'].items():
            if (not isinstance(channel, list) or not 1 <= len(channel) <= 32
                or any(not isinstance(step, dict) or 'constraints' not in step for step in channel)):
                base.fail('HOO channels require explicit nonempty operators')
            if role != 'rgb' and [s.get('method') for s in channel] != [s['method'] for s in operators]:
                base.fail('Ha/OIII body chains must follow the bound morphology strategy')
            chains[role] = copy.deepcopy(channel)
            for index, step in enumerate(chains[role], 1):
                step['constraints'] = merge_constraints(goals['protection'], step['constraints'], issues=issues,
                    location={'variant': role, 'step': index})
    if 'hdr' in goals:
        if mode != 'exception' or not isinstance(goals['hdr'], dict) or set(goals['hdr']) != {'gentle', 'faint', 'mask'}:
            base.fail('Masked HDR requires an explicit morphology-bound strategy with native branches and mask parameters')
        if 'channels' in goals or 'tail' in goals:
            base.fail('Masked HDR cannot combine HOO channels or ordinary tail controls')
        from deep_sky_siril_hdr import declaration
        from deep_sky_siril_processing import TRANSFORMS, validate_parameters
        declaration('# hdr-stretch: '+json.dumps(goals['hdr']['mask']))
        for role in ('gentle', 'faint'):
            branch = goals['hdr'][role]
            if not isinstance(branch, list) or not 1 <= len(branch) <= 32:
                base.fail('HDR branches require explicit bounded native transfer chains')
            for step in branch:
                if (not isinstance(step, dict) or set(step) != {'command', 'parameters'}
                    or not isinstance(step['command'], str) or step['command'] not in TRANSFORMS
                    or not isinstance(step['parameters'], list) or any(not isinstance(p, str) for p in step['parameters'])):
                    base.fail('HDR branches require explicit native MTF/Asinh/GHS parameters')
                validate_parameters([step['command'], *step['parameters']], 'stretch')
                if step['command'] == 'ght' and not all(any(p.startswith('-'+key+'=') for p in step['parameters']) for key in ('D','B','LP','SP','HP')):
                    base.fail('HDR GHS requires all five numeric parameters explicitly')
    return {variant: {**copy.deepcopy(common), 'operators': chain,
            'strategy': {'plan': binding, 'variant': variant}} for variant, chain in chains.items()}


def _previous_attempt(session, payload, revision, source):
    if (not isinstance(revision, dict) or set(revision)-{'conflict_review', 'unverifiable_plan_reason'} != {'parameters', 'reason', 'target_change', 'affected_regions', 'evidence'}
        or not isinstance(revision['reason'], str) or not revision['reason'].strip()
        or not isinstance(revision['affected_regions'], list) or not revision['affected_regions']
        or any(not isinstance(value, str) for value in revision['affected_regions'])
        or not isinstance(revision['evidence'], list)):
        base.fail('Revisions require previous parameters, affected ROIs, a concrete reason and evidence')
    change = revision['target_change']
    if change is not None and (not isinstance(change, dict) or set(change) != {'kind', 'reason', 'tradeoff', 'evidence'}
        or not isinstance(change['kind'], str) or change['kind'] not in {'display_correction', 'science_protection'}
        or any(not isinstance(change[k], str) or not change[k].strip() for k in ('reason', 'tradeoff'))
        or not isinstance(change['evidence'], list) or not change['evidence']):
        base.fail('Target changes require a separate correction/protection kind, basis, tradeoff and actual evidence')
    path = base.bound_file(session, revision['parameters'], ('reports', 'runs'))
    report = read_json(path)
    if report.get('schema') == 'starun-siril.run-receipt.v1':
        if (payload.get('execution_policy', {}).get('stretch_hdr') != HDR_POLICY
            or report.get('protocol') != 'stretch' or report.get('status') not in {'success', 'failed'}
            or not report.get('hdr_transfer', {}).get('strategy') or report.get('source_path') != source['path']
            or report.get('source', {}).get('sha256') != source['sha256']):
            base.fail('HDR revisions require the actual same-source native stretch receipt')
        entries = read_json(session/'manifest.json')['runs']
        if (path != session/'runs'/(report['id']+'.json') or sum(
            row.get('id') == report['id'] and row.get('status') == report['status']
            and row.get('receipt') == path.name and row.get('receipt_sha256') == sha256_file(path)
            for row in entries) != 1):
            base.fail('HDR revision receipt differs from the committed native run')
        plan, expected = load_plan(session, payload, report['hdr_transfer']['strategy'])
        script = base.bound_file(session, report['script'], ('scripts',))
        evidence = [report['script'], report['log'], *report['outputs']]
        for item in evidence:
            base.bound_file(session, item, ('scripts', 'runs', 'logs', 'artifacts', 'previews'))
        from deep_sky_siril_hdr import validate_stretch
        actual = validate_stretch(session, base.source_file(session, payload, source),
            base.session_path(session, report['primary_output'], allowed_roots=('artifacts',)), script.read_text(), verify_parent=False)
        if {**actual, 'strategy': report['hdr_transfer']['strategy']} != report['hdr_transfer']:
            base.fail('HDR revision native branches differ from their frozen receipt')
        if report['status'] == 'success':
            from deep_sky_siril_core import _load_run
            _load_run(session, report['id'])
        request = expected['exception']
        return {'request': request, 'bindings': {'request': revision['parameters']}, 'steps': [],
                'status': 'solved' if report['status'] == 'success' else 'failed',
                'native_hdr': True, 'revision_evidence': evidence}, plan
    request = report.get('request', {})
    if request.get('source') != source or 'strategy' not in request:
        base.fail('Revision evidence must use the same accepted linear parent and bound strategy')
    excuse = revision.get('unverifiable_plan_reason')
    if excuse is not None:
        # A host-compatibility fix to the solving implementation changes its frozen
        # fingerprint, so an earlier failed plan can no longer be re-derived. The
        # previous report must still be a real failed run of this parent, and its
        # plan and goals bytes are verified as recorded.
        if (not isinstance(excuse, str) or not excuse.strip()
                or report.get('status') not in {'failed', 'ineligible'}):
            base.fail('A superseded plan requires a failed report and a concrete reason')
        plan = read_json(base.bound_file(session, request['strategy']['plan']))
        if plan.get('source') != source or not isinstance(plan.get('goals'), dict):
            base.fail('Revision evidence must use the same accepted linear parent and bound strategy')
        base.bound_file(session, plan['goals'], ('reports',))
        return report, plan
    plan, expected = load_plan(session, payload, request['strategy']['plan'])
    candidate(session, payload, path, plan, expected)
    return report, plan


def _revision_diff(previous, request, old_regions, new_regions, old_goals, current_goals, *, hdr_aware=False):
    old = previous['request']
    scientific = lambda steps: [{key: step[key] for key in ('method', 'factors', 'landmarks') if key in step} for step in steps]
    old_rows = {(row['region'], row['statistic']): row for row in old['operators'][-1]['constraints']}
    new_rows = {(row['region'], row['statistic']): row for row in request['operators'][-1]['constraints']}
    changed = [{'region': key[0], 'statistic': key[1], 'previous': old_rows.get(key), 'current': new_rows.get(key)}
               for key in sorted(old_rows.keys() | new_rows.keys()) if old_rows.get(key) != new_rows.get(key)]
    hdr_changed = hdr_aware and old_goals.get('hdr') != current_goals.get('hdr')
    hdr_pair = hdr_aware and 'hdr' in old_goals and 'hdr' in current_goals
    return {**({'morphology_changes': {'previous':old_goals.get('morphology'),'current':current_goals.get('morphology')}
               if old_goals.get('morphology') != current_goals.get('morphology') else None}
              if 'morphology' in old_goals or 'morphology' in current_goals else {}),
            'previous_request': previous['bindings']['request'],
            'previous_profile': old_goals['profile'], 'current_profile': current_goals['profile'],
            'parameters_changed': hdr_changed or not hdr_pair and scientific(old['operators']) != scientific(request['operators']),
            'route_changed': [s['method'] for s in old['operators']] != [s['method'] for s in request['operators']] or hdr_aware and ('hdr' in old_goals) != ('hdr' in current_goals),
            'regions_changed': old_regions != new_regions, 'final_constraints': changed,
            'targets': {'previous': old['targets'], 'current': request['targets']} if old['targets'] != request['targets'] else None,
            'step_constraints': [{'step': index+1, 'previous': a, 'current': b}
                for index, (a,b) in enumerate(zip_longest(
                    [s['constraints'] for s in old['operators']], [s['constraints'] for s in request['operators']])) if a != b],
            'strategy_changes': {key: {'previous': old_goals.get(key), 'current': current_goals.get(key)}
                                 for key in ('channels', 'hdr', 'baseline_factors', 'tail') if old_goals.get(key) != current_goals.get(key)},
            'same_declared_transform': (hdr_pair or scientific(old['operators']) == scientific(request['operators'])) and old_regions == new_regions and not hdr_changed,
            'previous_status': previous['status']}


def source_previews(session, payload, binding):
    from deep_sky_siril_processing import script_commands
    from deep_sky_siril_validation import _normalized_write
    source = base.source_file(session, payload, binding)
    previews = {}
    if (session/'manifest.json').is_file():
        for entry in load_json(session/'manifest.json')['runs']:
            if entry.get('status') != 'success':
                continue
            receipt = base.session_path(session, 'runs/'+entry['receipt'], must_exist=True, allowed_roots=('runs',))
            if sha256_file(receipt) != entry['receipt_sha256']:
                base.fail('Morphology source receipt changed')
            run = read_json(receipt)
            inspected = (run.get('protocol') == 'input.inspect' and run.get('source_path') == binding['path']
                         and run.get('source', {}).get('sha256') == binding['sha256'])
            created = (run.get('primary_output') == binding['path'] and any(
                row.get('path') == binding['path'] and row.get('sha256') == binding['sha256'] for row in run.get('outputs', [])))
            if not inspected and not created:
                continue
            if not isinstance(run.get('script'), dict):
                continue
            script = base.bound_file(session, run['script'], ('scripts',))
            current = None
            for row in script_commands(script.read_text()):
                if row[0] in {'load', 'save'}:
                    path = Path(_normalized_write(row[0], row[1]))
                    current = path if path.is_absolute() else session/path
                elif row[0] == 'savejpg' and current == source:
                    path = Path(_normalized_write('savejpg', row[1]))
                    path = path if path.is_absolute() else session/path
                    for output in run['outputs']:
                        if output['path'] == path.relative_to(session).as_posix():
                            base.bound_file(session, output, ('previews', 'artifacts'))
                            previews[output['path']] = output
                elif row[0] not in base.METHODS | {'requires', 'set32bits', 'icc_assign', 'stat', 'savejpg'}:
                    current = None
    return list(previews.values())


def _verify_bound_context(session, payload, plan, goals, requests, *, initializing=False):
    from deep_sky_siril_stretch_preflight import effects_enabled
    from deep_sky_siril_artifact_focus import validate_initial
    if multistage_required(payload):
        if plan['mode'] == 'compare':
            base.fail('Single-step baseline comparisons are retired; use a morphology multistage proposal')
        if 'hdr' not in goals:
            validate_body_chain(payload, requests['exception' if plan['mode'] == 'exception' else 'profile']['operators'])
        for role in ('ha', 'oiii'):
            if role in requests:
                validate_body_chain(payload, requests[role]['operators'])
    validate_initial(session, payload, plan, goals)
    source = base.source_file(session, payload, plan['source'])
    allowed = {row['path']: row['sha256'] for row in source_previews(session, payload, plan['source'])}
    if not any(isinstance(row, dict) and allowed.get(row.get('path')) == row.get('sha256') for row in goals['evidence']):
        base.fail('Morphology evidence requires an actual committed preview of the accepted linear parent')
    for row in goals['evidence']:
        if not isinstance(row, dict):
            base.fail('Morphology evidence requires file bindings')
        if row.get('path') == plan['source']['path']:
            if row.get('sha256') != plan['source']['sha256']:
                base.fail('Morphology source evidence changed')
        else:
            base.bound_file(session, row, ('reports', 'previews', 'artifacts'))
    regions = read_json(base.bound_file(session, plan['regions']))
    if goals['profile'] == 'high_dynamic_range' and not any(r['role'] == 'core' for r in regions['regions']):
        base.fail('A high-dynamic-range strategy requires an observed protected core ROI')
    revision = goals['revision']
    morphology_hdr = 'hdr' in goals and payload.get('execution_policy', {}).get('stretch_hdr') == HDR_POLICY
    if revision is None:
        if plan['mode'] == 'exception' and not morphology_hdr:
            operators = requests['exception']['operators']
            allowed = load_json(protected.PROFILE_PATH)[goals['profile']]['operators']
            if (not effects_enabled(payload) or not 1 <= len(operators) <= 2 or 'hdr' in goals
                or any(step.get('method') not in allowed for step in operators)):
                base.fail('Initial exceptions require an evidence-bound one/two-step morphology route')
        if plan['revision_diff'] is not None:
            base.fail('Unexpected revision diagnostics')
    else:
        previous, old_plan = _previous_attempt(session, payload, revision, plan['source'])
        root = old_plan['root'] or base.record(session, base.bound_file(session, previous['request']['strategy']['plan']))
        if plan['root'] != root:
            base.fail('Revision cannot reset its original morphology plan')
        old_regions = read_json(base.bound_file(session, old_plan['regions']))
        old_goals = read_json(base.bound_file(session, old_plan['goals']))
        old_profile = old_goals['profile']
        main = requests['exception' if plan['mode'] == 'exception' else 'profile']
        diff = _revision_diff(previous, main, old_regions, regions, old_goals, goals,
            hdr_aware=payload.get('execution_policy', {}).get('stretch_hdr') == HDR_POLICY)
        if diff != plan['revision_diff']:
            base.fail('Revision differences changed')
        if not set(revision['affected_regions']) <= {r['id'] for r in regions['regions']}:
            base.fail('Revision affected ROIs must name actual current structures')
        diff_route = diff['route_changed'] or old_profile != goals['profile']
        if diff_route and plan['mode'] != 'exception':
            base.fail('Changing operators or chain length requires measured exception mode')
        if diff.get('morphology_changes'):
            old_morph, new_morph = old_goals.get('morphology',{}), goals.get('morphology',{})
            before = {r['region']:r for r in old_morph.get('regions',[])}
            after = {r['region']:r for r in new_morph.get('regions',[])}
            affected = {r for r in before.keys()|after.keys() if before.get(r) != after.get(r)}
            if any(old_morph.get(k) != new_morph.get(k) for k in ('primary_subject','processing_goal')):
                affected.update(r for r,row in {**before,**after}.items() if row['relation']=='primary')
            if not affected <= set(revision['affected_regions']):
                base.fail('Morphology revisions must identify every affected ROI')
        channel_goals_changed = any(
            [s['constraints'] for s in old_goals.get('channels', {}).get(role, [])] !=
            [s['constraints'] for s in goals.get('channels', {}).get(role, [])] for role in ('rgb', 'ha', 'oiii'))
        if (diff['final_constraints'] or diff['regions_changed'] or diff['targets'] or channel_goals_changed
            or not diff['route_changed'] and diff['step_constraints']) and revision['target_change'] is None:
            base.fail('Display target or ROI revisions require a separate target-change reason')
        allowed_evidence = [revision['parameters'], *previous.get('revision_evidence', [])]
        for step in previous['steps']:
            for trial in step.get('trials', []):
                allowed_evidence.extend(trial.get('retained', {}).values())
            if step.get('local_panel'):
                allowed_evidence.append(step['local_panel'])
            allowed_evidence.extend(step.get('selected', {}).values())
        allowed_evidence.append(previous['request']['source'])
        if previous['bindings'].get('derived_source'):
            allowed_evidence.append(previous['bindings']['derived_source'])
        evidence = revision['evidence'] + (revision['target_change']['evidence'] if revision['target_change'] else [])
        for item in evidence:
            if not isinstance(item, dict) or not any(item.get('path') == e.get('path') and item.get('sha256') == e.get('sha256') for e in allowed_evidence):
                base.fail('Revision proof must be bound same-source scientific evidence')
            if item == previous['request']['source']:
                base.source_file(session,payload,item)
            else:
                base.bound_file(session, item, ('reports','runs','logs','scripts','previews','artifacts'))
        old_protection = {r['id']: r for r in old_regions['regions'] if r['role'] in {'core', 'stars'}}
        if any(row not in regions['regions'] for row in old_protection.values()):
            base.fail('Revisions cannot remove, relabel or move existing core and stellar protection')
        protection_pairs = [(previous['request']['operators'][-1]['constraints'], main['operators'][-1]['constraints']),
                            (old_goals['protection'], goals['protection'])]
        for role in old_goals.get('channels', {}).keys() & goals.get('channels', {}).keys():
            protection_pairs.append((old_goals['channels'][role][-1]['constraints'], goals['channels'][role][-1]['constraints']))
        for old_rows, new_rows in protection_pairs:
            new_rows = {(r['region'], r['statistic']): r for r in new_rows}
            for old in old_rows:
                key = (old['region'], old['statistic'])
                if key[0] in old_protection and key[1] in {'p99', 'p999', 'max_rgb', 'span'}:
                    new = new_rows.get(key)
                    if new is None or (new['min'] < old['min'] if key[1] == 'span' else new['max'] > old['max']):
                        base.fail('Revisions cannot weaken existing highlight or core-span protection')
        same_exception_route = (payload.get('execution_policy', {}).get('stretch_optimization') == 'highlight_budget_mtf_v2'
                                and previous['request']['strategy']['variant'] == 'exception'
                                and not diff_route)
        if plan['mode'] == 'exception' and not same_exception_route and not morphology_hdr and not previous.get('native_hdr'):
            if previous['request']['strategy']['variant'] not in {'profile', 'exception', 'ha', 'oiii'}:
                base.fail('A control or RGB-star proposal cannot authorize a body-route exception')
            native = [trial for step in previous['steps'] for trial in step.get('trials', [])]
            failures = [reason for trial in native for reason in trial['audit'].get('reasons', [])
                        if reason in {'regional_constraints', 'shadow_limit', 'new_highlight_crossing', 'target_direction'}]
            if previous['status'] not in {'solved', 'ineligible'} or not native:
                base.fail('An exception requires measured scientific failure or explicit same-source conflict evidence')
            derived = previous['bindings'].get('derived_source')
            scientific_source = base.bound_file(session, derived) if derived else source
            protected.verify(session, previous, scientific_source, old_regions['regions'], incomplete=previous['status'] != 'solved')
            if failures:
                failed_ids = {row['region'] for trial in native for row in trial['audit'].get('constraints', [])
                              if not row['satisfied'] and row['statistic'] not in {'mad', 'hf_sigma2'}}
                if not failed_ids <= set(revision['affected_regions']):
                    base.fail('Affected ROIs do not include the measured failing structure')
            else:
                shorter = effects_enabled(payload) and len(main['operators']) < len(previous['request']['operators'])
                proof = {(item['path'],item['sha256']) for item in revision['evidence']}
                pairs = []
                for index, step in enumerate(previous['steps']):
                    if step['method'] != 'ght':
                        continue
                    before = previous['bindings'].get('derived_source',previous['request']['source']) if index == 0 else previous['steps'][index-1].get('selected',{}).get('.fit')
                    after = step.get('selected',{}).get('.fit')
                    if before and after:
                        pairs.append(all((item['path'],item['sha256']) in proof for item in (before,after)))
                shortening_basis = shorter and any(pairs) and set(revision['affected_regions']) & set(main['targets'])
                if not shortening_basis:
                    if not revision.get('conflict_review'):
                        base.fail('Bind a reviewed conflict or actual GHS input/output evidence for a shorter route')
                    from deep_sky_siril_core import _validate_review, _load_run
                    review_path = base.bound_file(session, revision['conflict_review'], ('reviews',))
                    review = read_json(review_path)
                    _, run = _load_run(session, review['run_id'])
                    _validate_review(session, str(review_path), review['run_id'])
                    if run.get('adaptive_parameters', {}).get('parameters') != revision['parameters']:
                        base.fail('Conflict review belongs to a different native proposal')
                    conflicts = {row['region']['id'] for row in review.get('stretch_assessment', {}).get('structures', [])
                                 if (row.get('regression') or {}).get('core_conflict') == 'demonstrated'}
                    if not conflicts.intersection(revision['affected_regions']):
                        base.fail('Exception requires a demonstrated same-source protection conflict in the affected ROIs')
    root_binding = plan['root']
    if root_binding:
        root_plan = read_json(base.bound_file(session, root_binding))
        root_goals = read_json(base.bound_file(session, root_plan['goals']))
        if (root_plan.get('root') is not None or root_plan['source'] != plan['source']
            or root_goals['profile'] != goals['profile'] and plan['mode'] != 'exception'):
            base.fail('Changing profile or source cannot masquerade as another initial proposal')
    marker = session/'reports'/('.stretch-profile-' + plan['source']['sha256'] + '.json')
    if not initializing and not marker.is_file():
        base.fail('The immutable first morphology classification is missing')
    if marker.exists() and read_json(marker) != (root_binding or base.record(session, session/'reports'/plan['run_id']/'strategy.json')):
        base.fail('The accepted linear parent already has a different initial morphology plan')
    return source, regions['regions']


def init_bound_requests(session, payload, source_value, regions_value, goals_value, run_id, mode, *, preflight=False):
    if mode not in {'profile', 'compare', 'exception'} or base.RUN_ID_PATTERN.fullmatch(str(run_id)) is None:
        base.fail('Invalid bound strategy mode or run ID')
    source = Path(payload['input']['path']) if str(source_value) == '@input' else base.session_path(session, source_value, must_exist=True, allowed_roots=('artifacts',))
    source_binding = {'path': str(source_value) if str(source_value) == '@input' else source.relative_to(session).as_posix(), 'sha256': sha256_file(source)}
    base.source_file(session, payload, source_binding)
    full = base.accepted_parent(session, payload, source)
    regions_path = base.session_path(session, regions_value, must_exist=True, allowed_roots=('reports',))
    from deep_sky_siril_metrics import measure_regions
    if measure_regions(source, None, regions_path)['status'] != 'measured':
        base.fail('Strategy ROIs must bind finite current-parent scientific geometry')
    goals_path = base.session_path(session, goals_value, must_exist=True, allowed_roots=('reports',))
    goals = read_json(goals_path)
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not fingerprint_matches(runtime.get('fingerprint', {})):
        base.fail('Frozen Siril runtime is missing or changed')
    directory = base.session_path(session, 'reports/' + run_id, allowed_roots=('reports',))
    if not preflight and (directory.exists() or directory.is_symlink()):
        base.fail('Strategy evidence already exists; use a new run ID')
    root = None
    previous = None
    if isinstance(goals, dict) and goals.get('revision') is not None:
        previous, old_plan = _previous_attempt(session, payload, goals['revision'], source_binding)
        root = old_plan['root'] or previous['request']['strategy']['plan']
    marker = session/'reports'/('.stretch-profile-' + source_binding['sha256'] + '.json')
    if not marker.exists() and any(read_json(p).get('source_sha256') == source_binding['sha256']
                                   for p in session.glob('reports/*/scientific-attempt.json')):
        base.fail('Existing native attempts cannot reset their missing first morphology classification')
    if marker.exists() and root is None:
        first = read_json(marker)
        first_plan = read_json(base.bound_file(session, first))
        attempts = [p for p in session.glob('reports/*/scientific-attempt.json') if read_json(p).get('source_sha256') == source_binding['sha256']]
        if attempts:
            base.fail('A previous native attempt exists; bind it as a revision instead of restarting')
        root = first
    plan = {'schema': BOUND_PLAN, 'policy': POLICY, 'mode': mode, 'root': root, 'revision_diff': None,
            'run_id': run_id, 'source': source_binding, 'full_source': base.record(session, full) if full else None,
            'regions': base.record(session, regions_path), 'goals': base.record(session, goals_path),
            'runtime': runtime, 'implementation': protected.implementation(), 'profiles': fingerprint(protected.PROFILE_PATH, include_path=False)}
    dummy = {'path': 'reports/' + run_id + '/strategy.json', 'sha256': '0' * 64}
    from deep_sky_siril_stretch_preflight import enabled as preflight_enabled, assess, require_ready
    issues = [] if preflight_enabled(payload) or preflight else None
    requests = requests_for(plan, goals, dummy, issues=issues)
    if previous:
        old_regions = read_json(base.bound_file(session, previous['request']['regions']))
        old_goals = read_json(base.bound_file(session, old_plan['goals']))
        plan['revision_diff'] = _revision_diff(previous, requests['exception' if mode == 'exception' else 'profile'], old_regions, read_json(regions_path), old_goals, goals,
            hdr_aware=payload.get('execution_policy', {}).get('stretch_hdr') == HDR_POLICY)
    source, regions = _verify_bound_context(session, payload, plan, goals, requests, initializing=True)
    for variant, request in requests.items():
        if base.RUN_ID_PATTERN.fullmatch(candidate_run_id(run_id, variant)) is None:
            base.fail('Strategy candidate run ID is too long')
        for step in request['operators']:
            protected.proposal_tokens(step, 0., {'LP': 0., 'SP': .5, 'HP': 1.})
            fields = {'method', 'purpose', 'factors', 'constraints'} | ({'landmarks'} if step['method'] == 'ght' else set())
            if set(step) != fields or not isinstance(step['purpose'], str) or not step['purpose'].strip():
                base.fail('Frozen operators require exact explicit proposal fields')
            if step['method'] == 'ght' and (not isinstance(step['landmarks'], dict) or set(step['landmarks']) != {'LP', 'SP', 'HP'}):
                base.fail('Frozen GHS operators require exact LP/SP/HP landmarks')
            for anchor in step.get('landmarks', {}).values():
                if (not isinstance(anchor, dict) or set(anchor) != {'region', 'statistic'}
                    or not isinstance(anchor['region'], str) or not isinstance(anchor['statistic'], str)
                    or anchor['region'] not in {r['id'] for r in regions} or anchor['statistic'] not in base.STATISTICS):
                    base.fail('Frozen GHS anchors require actual current-domain ROI statistics')
        try:
            constraint_issues = [] if issues is not None else None
            protected.validate(request, regions, full=True,
                full_diagnostic=bool(full) and protected.full_is_diagnostic(payload), issues=constraint_issues)
            if issues is not None:
                issues.extend({**issue, 'variant': variant} for issue in constraint_issues)
        except base.ContractError as exc:
            if issues is None:
                raise
            issues.append({'code': 'invalid_constraints', 'variant': variant, 'reason': str(exc)})
    main = requests['exception' if mode == 'exception' else 'profile']
    if len(main['operators']) > 1 and main['operators'][-1]['method'] == 'mtf' and base.pixels(source).shape[0] == 3 and 'tail' not in requests and 'channels' not in goals and 'hdr' not in goals:
        base.fail('RGB MTF strategies require an explicit same-prefix GHS tail proposal before trial')
    preflight_report = assess(session, payload, plan, goals, requests, issues) if issues is not None else None
    if preflight:
        return preflight_report
    if preflight_report is not None:
        require_ready(preflight_report)
        content = json.dumps(preflight_report, indent=2, allow_nan=False)+'\n'
        import hashlib
        plan['constraint_preflight'] = {'path': 'reports/'+run_id+'/constraint-preflight.json',
            'sha256': hashlib.sha256(content.encode()).hexdigest(), 'size': len(content.encode())}
    content = json.dumps(plan, indent=2, allow_nan=False)+'\n'
    import hashlib
    binding = {'path': dummy['path'], 'sha256': hashlib.sha256(content.encode()).hexdigest(), 'size': len(content.encode())}
    requests = requests_for(plan, goals, binding)
    directory.mkdir()
    if preflight_report is not None:
        with (directory/'constraint-preflight.json').open('x') as stream:
            json.dump(preflight_report, stream, indent=2, allow_nan=False); stream.write('\n')
    (directory/'strategy.json').write_text(content)
    outputs = {}
    for variant, request in requests.items():
        path = directory/(variant+'-request.json')
        with path.open('x') as stream:
            json.dump(request, stream, indent=2, allow_nan=False); stream.write('\n')
        outputs[variant] = {'request': str(path), 'run_id': candidate_run_id(run_id, variant), 'methods': [s['method'] for s in request['operators']]}
    if not marker.exists():
        with marker.open('x') as stream:
            json.dump(binding, stream, allow_nan=False); stream.write('\n')
    return {'status': 'prepared', 'strategy': str(directory/'strategy.json'), 'candidates': outputs, 'requires_visual_review': True,
            **({'constraint_preflight': str(directory/'constraint-preflight.json')} if preflight_report is not None else {})}


def claim_attempt(session, payload, run_id, request):
    if not routing_required(payload):
        return
    from deep_sky_siril_stretch_preflight import enabled as preflight_enabled
    if preflight_enabled(payload):
        declaration = request.get('strategy', {})
        if not isinstance(declaration, dict):
            base.fail('Scientific attempts require a bound whole-chain constraint preflight')
        load_plan(session, payload, declaration.get('plan', declaration))
    import fcntl
    from datetime import datetime, timezone
    with (session/'reports/.stretch-attempts.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(list(session.glob('reports/*/scientific-attempt.json'))) >= 3:
            base.fail('Stage 7 has exhausted its three shared scientific attempts')
        path = session/'reports'/run_id/'scientific-attempt.json'
        # Some host file-operation shims raise a plain OSError carrying EEXIST even
        # when the directory already exists, so exist_ok alone cannot be trusted.
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            json.dump({'source_sha256': request['source']['sha256'], 'request': request,
                       'started_at': datetime.now(timezone.utc).isoformat()}, stream, allow_nan=False)
            stream.write('\n')


def verify_candidate_comparisons(session, payload, report, parameters, bindings):
    verified = {}
    for binding in bindings:
        comparison = verify_comparison(session, payload, binding, parameters)
        kind = TAIL_COMPARISON if comparison['schema'] in TAIL_COMPARISON_SCHEMAS else comparison['schema']
        if kind in verified:
            base.fail('Declare at most one comparison of each stretch comparison type')
        verified[kind] = comparison
    if routing_required(payload):
        declaration = report['request'].get('strategy')
        if declaration is None:
            base.fail('Formal stretch generation requires a bound morphology strategy')
        plan, _ = load_plan(session, payload, declaration['plan'])
        variant = declaration['variant']
        if variant in {'baseline', 'ha', 'oiii', 'rgb'}:
            base.fail('A single-curve control needs a measured exception plan before formal adoption')
        if plan['schema'] == 'starun-siril.stretch-strategy.v3':
            goals = read_json(base.bound_file(session, plan['goals']))
            tail_control = bool(goals.get('tail_control'))
            if goals.get('revision') and len(report['transfer_chain']) > 1 and report['transfer_chain'][-1]['command'] == 'ght':
                previous = read_json(base.bound_file(session, goals['revision']['parameters'], ('reports', 'runs')))
                old_chain = previous.get('transfer_chain', [])
                tail_control |= bool(old_chain and old_chain[-1]['command'] == 'mtf'
                    and old_chain[:-1] == report['transfer_chain'][:-1]
                    and all(previous.get('bindings', {}).get(k) == report['bindings'].get(k) for k in ('source', 'regions')))
            if tail_control and TAIL_COMPARISON not in verified:
                base.fail('Stepwise GHS tail controls require their same-prefix compare-tail report')
        if plan['mode'] == 'compare' and COMPARISON not in verified:
            base.fail('Explicit morphology comparison requires its same-parent comparison report')
        source = base.source_file(session, payload, report['request']['source'])
        if (base.requires_tail_comparison(payload, report, source) or variant == 'tail') and TAIL_COMPARISON not in verified:
            base.fail('RGB MTF/GHS tails require their same-prefix compare-tail report')
    return verified


def validate_external_strategy(session, payload, source, binding, *, channels=None, hdr=None):
    plan, requests = load_plan(session, payload, binding)
    if plan['schema'] == 'starun-siril.stretch-strategy.v3':
        if channels is not None or hdr is None:
            base.fail('Stepwise external strategy requires its finalized HDR branches')
        from deep_sky_siril_stretch_stepwise import validate_hdr
        validate_hdr(session, payload, source, binding, hdr)
        return plan, requests
    if plan['schema'] != BOUND_PLAN or base.source_file(session, payload, plan['source']) != source:
        base.fail('External stretch requires a bound strategy on the same accepted linear source')
    goals = read_json(base.bound_file(session, plan['goals']))
    if channels is not None:
        if 'channels' not in goals or any(role not in requests or channels[role] != requests[role]['operators'] for role in ('rgb', 'ha', 'oiii')):
            base.fail('HOO channels differ from their frozen morphology-bound proposals')
    if hdr is not None:
        if plan['mode'] != 'exception' or goals.get('hdr') != {'gentle': hdr['gentle_chain'], 'faint': hdr['faint_chain'],
                'mask': {key: hdr['mask'][key] for key in ('mask_low', 'mask_high', 'sigma')}}:
            base.fail('HDR native branches or mask differ from their frozen morphology strategy')
        protection = hdr.get('protection')
        guard = read_json(base.bound_file(session, protection['request'])) if protection else {}
        if (guard.get('regions') != plan['regions'] or guard.get('targets') != goals['targets']
            or guard.get('constraints') != goals['protection']):
            base.fail('HDR final blend requires the frozen strategy ROI and protection constraints')
    return plan, requests


def requests_for(plan, goals, plan_binding, *, issues=None):
    if plan.get('schema') == BOUND_PLAN:
        return bound_requests_for(plan, goals, plan_binding, issues=issues)
    if not isinstance(goals, dict):
        base.fail('Strategy goals must be an object')
    profiles = load_json(protected.PROFILE_PATH)
    profile = goals.get('profile')
    if not isinstance(profile, str) or profile not in profiles or profile == 'custom':
        base.fail('Choose one of the five observed morphology profiles; custom uses an explicit request')
    fields = {'schema', 'profile', 'reason', 'targets', 'core_absence', 'protection', 'phases', 'manual_lookup'}
    proposal = goals.get('schema') == PROPOSAL_GOALS
    if proposal:
        fields.add('baseline_factors')
    if (set(goals) != fields or goals.get('schema') not in {GOALS, PROPOSAL_GOALS}
        or not isinstance(goals['reason'], str) or not goals['reason'].strip()
        or not isinstance(goals['protection'], list) or not goals['protection']):
        base.fail('Declare morphology evidence, shared protection and explicit phase goals')
    recipe = profiles[profile]['preferred_chain']
    phases = goals['phases']
    if not isinstance(phases, list) or len(phases) != len(recipe):
        base.fail('Phase goals must match the selected profile chain length')
    operators = []
    for definition, phase in zip(recipe, phases):
        required = {'constraints'} | ({'landmarks'} if definition['method'] == 'ght' else set())
        if proposal:
            required.add('factors')
        if not isinstance(phase, dict) or set(phase) != required or not isinstance(phase['constraints'], list):
            base.fail('Each phase needs explicit constraints and GHS phases need current-domain landmarks')
        step = {'method': definition['method'], 'purpose': definition['purpose']+' '+goals['reason'],
                'constraints': merge_constraints(goals['protection'], phase['constraints'])}
        if 'landmarks' in phase:
            step['landmarks'] = copy.deepcopy(phase['landmarks'])
        if proposal:
            step['factors'] = copy.deepcopy(phase['factors'])
        operators.append(step)
    common = {'schema': protected.PROPOSAL_REQUEST if proposal else REQUEST, 'profile': profile, 'targets': goals['targets'], 'core_absence': goals['core_absence'],
              'source': plan['source'], 'regions': plan['regions'], 'manual_lookup': goals['manual_lookup']}
    chains = {'profile': operators, 'baseline': [{'method': profiles[profile]['baseline'],
               'purpose': 'Same-parent single-method control. '+goals['reason'],
               'constraints': copy.deepcopy(operators[-1]['constraints'])}]}
    if proposal:
        chains['baseline'][0]['factors'] = copy.deepcopy(goals['baseline_factors'])
    return {variant: {**copy.deepcopy(common), 'operators': chain,
                     'strategy': {'plan': plan_binding, 'variant': variant}}
            for variant, chain in chains.items()}


def load_plan(session, payload, binding):
    if payload.get('execution_policy', {}).get('stretch_strategy') != POLICY:
        base.fail('Profile comparison requires its frozen morphology strategy policy')
    path = base.bound_file(session, binding)
    plan = read_json(path)
    if isinstance(plan, dict) and plan.get('schema') == 'starun-siril.stretch-strategy.v3':
        from deep_sky_siril_stretch_stepwise import load_plan as load_stepwise
        frozen, goals = load_stepwise(session, payload, binding)
        return frozen, goals
    required = {'schema', 'policy', 'run_id', 'source', 'full_source', 'regions', 'goals', 'runtime', 'implementation', 'profiles'}
    if isinstance(plan, dict) and plan.get('schema') == BOUND_PLAN:
        required |= {'mode', 'root', 'revision_diff'}
        from deep_sky_siril_stretch_preflight import enabled as preflight_enabled
        if preflight_enabled(payload):
            required.add('constraint_preflight')
    if (not isinstance(plan, dict) or set(plan) != required or plan.get('schema') not in {PLAN, BOUND_PLAN}
        or plan.get('policy') != POLICY or base.RUN_ID_PATTERN.fullmatch(str(plan.get('run_id', ''))) is None
        or path != session/'reports'/plan['run_id']/'strategy.json'):
        base.fail('Malformed or misplaced stretch strategy')
    base.source_file(session, payload, plan['source'])
    if plan['full_source'] is not None:
        base.bound_file(session, plan['full_source'], ('artifacts',))
    base.bound_file(session, plan['regions'])
    goals = read_json(base.bound_file(session, plan['goals']))
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if (runtime != plan['runtime'] or not fingerprint_matches(runtime['fingerprint'])
        or not protected.implementation_matches(session, plan['implementation'])
        or protected.profiles_fingerprint(session) != plan['profiles']):
        base.fail('Strategy runtime, implementation or morphology recipes changed')
    requests = requests_for(plan, goals, base.record(session, path))
    if plan['schema'] == BOUND_PLAN:
        _verify_bound_context(session, payload, plan, goals, requests)
        from deep_sky_siril_stretch_preflight import enabled as preflight_enabled, verify as verify_preflight
        if preflight_enabled(payload):
            verify_preflight(session, payload, plan, goals, requests)
    return plan, requests


def validate_request(session, payload, request, path, run_id=None):
    declaration = request.get('strategy')
    if (not isinstance(declaration, dict) or set(declaration) != {'plan', 'variant'}
        or not isinstance(declaration['variant'], str)):
        base.fail('Profile requests require an exact strategy and candidate identity')
    plan, expected = load_plan(session, payload, declaration['plan'])
    goals = read_json(base.bound_file(session, plan['goals']))
    if 'hdr' in goals and payload.get('execution_policy', {}).get('stretch_hdr') == HDR_POLICY:
        base.fail('HDR strategies execute their native branches through run --protocol stretch, not ordinary solve')
    variant = declaration['variant']
    if (variant not in expected or request != expected[variant] or path != session/'reports'/plan['run_id']/(variant+'-request.json')
        or run_id is not None and run_id != candidate_run_id(plan['run_id'], variant)):
        base.fail('Strategy request, methods, constraints or run ID differ from the frozen plan')
    if routing_required(payload) and plan['schema'] != BOUND_PLAN:
        base.fail('New stretch execution requires a bound v2 morphology strategy')
    return plan


def init_requests(session_value, source_value, regions_value, goals_value, run_id, *, search=False, mode='profile', preflight=False, stepwise=None):
    from deep_sky_siril_session import effective_session_payload, load_session, execution_policy_is_current
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        base.fail('Historical sessions are replay/finalize-only; create a current session for new requests')
    if multistage_required(payload) and mode == 'compare':
        base.fail('Single-step baseline comparisons are retired; use a morphology multistage proposal')
    if stepwise is None:
        goals_path = base.session_path(session, goals_value, must_exist=True, allowed_roots=('reports',))
        goals = read_json(goals_path)
        stepwise = multistage_required(payload) and not (isinstance(goals, dict) and 'channels' in goals)
    elif type(stepwise) is not bool:
        base.fail('Stepwise execution must be unspecified, true or false')
    if stepwise:
        if search:
            base.fail('Stepwise requests require explicit current-step factors, without search')
        from deep_sky_siril_stretch_stepwise import init_requests as init_stepwise
        return init_stepwise(session, payload, source_value, regions_value, goals_value, run_id, mode, preflight=preflight)
    if preflight and not routing_required(payload):
        base.fail('Whole-chain preflight requires a current bound morphology strategy')
    if routing_required(payload):
        if search:
            base.fail('Bound morphology plans require explicit factors; parameter search is historical-only')
        return init_bound_requests(session, payload, source_value, regions_value, goals_value, run_id, mode, preflight=preflight)
    if payload.get('execution_policy', {}).get('stretch_strategy') != POLICY:
        base.fail('Strategy generation requires a new profile-guided session')
    if base.RUN_ID_PATTERN.fullmatch(str(run_id)) is None:
        base.fail('Invalid strategy run ID')
    for suffix in ('-base', '-profile'):
        if base.RUN_ID_PATTERN.fullmatch(run_id+suffix) is None:
            base.fail('Strategy candidate run ID is too long or invalid')
    source_ref = str(source_value)
    source = Path(payload['input']['path']) if source_ref == '@input' else base.session_path(
        session, source_ref, must_exist=True, allowed_roots=('artifacts',))
    source_binding = {'path': source_ref if source_ref == '@input' else source.relative_to(session).as_posix(),
                      'sha256': sha256_file(source)}
    base.source_file(session, payload, source_binding)
    full = base.accepted_parent(session, payload, source)
    regions_path = base.session_path(session, regions_value, must_exist=True, allowed_roots=('reports',))
    goals_path = base.session_path(session, goals_value, must_exist=True, allowed_roots=('reports',))
    goals = read_json(goals_path)
    if not isinstance(goals, dict):
        base.fail('Strategy goals must be an object')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not fingerprint_matches(runtime.get('fingerprint', {})):
        base.fail('Frozen Siril runtime is missing or changed')
    directory = base.session_path(session, f'reports/{run_id}', allowed_roots=('reports',))
    if directory.exists() or directory.is_symlink():
        base.fail('Strategy evidence already exists; use a new run ID')
    expected_goals = GOALS if search else PROPOSAL_GOALS
    if goals.get('schema') != expected_goals:
        base.fail('Default generation needs v2 goals and explicit factors; use --search with v1 goals for bounded search')
    plan = {'schema': PLAN, 'policy': POLICY, 'run_id': run_id, 'source': source_binding,
            'full_source': base.record(session, full) if full else None,
            'regions': base.record(session, regions_path), 'goals': base.record(session, goals_path),
            'runtime': runtime, 'implementation': protected.implementation(),
            'profiles': fingerprint(protected.PROFILE_PATH, include_path=False)}
    content = json.dumps(plan, indent=2, allow_nan=False)+'\n'
    import hashlib
    plan_binding = {'path': f'reports/{run_id}/strategy.json',
                    'sha256': hashlib.sha256(content.encode()).hexdigest(), 'size': len(content.encode())}
    requests = requests_for(plan, goals, plan_binding)
    # Reuse the full v2 parser before writing immutable strategy evidence.
    with tempfile.TemporaryDirectory(prefix='.stretch-preflight-', dir=session/'reports') as raw:
        for variant, request in requests.items():
            check = {k: v for k, v in request.items() if k != 'strategy'}
            check['schema'] = protected.REQUEST if search else protected.PROPOSAL_REQUEST
            path = Path(raw)/(variant+'.json')
            path.write_text(json.dumps(check))
            _, _, regions, _ = base.read_request(session, payload, path)
            protected.validate(check, regions, full=bool(full),
                               full_diagnostic=bool(full) and protected.full_is_diagnostic(payload))
            protected.statistics(base.pixels(source), regions)
    directory.mkdir()
    (directory/'strategy.json').write_text(content)
    outputs = {}
    for variant in VARIANTS:
        path = directory/(variant+'-request.json')
        with path.open('x') as handle:
            json.dump(requests[variant], handle, indent=2, allow_nan=False)
            handle.write('\n')
        outputs[variant] = {'request': str(path), 'run_id': run_id+('-base' if variant == 'baseline' else '-profile'),
                            'methods': [s['method'] for s in requests[variant]['operators']]}
    return {'status': 'prepared', 'strategy': str(directory/'strategy.json'), 'candidates': outputs,
            'requires_visual_review': True}


def candidate(session, payload, path, plan, expected):
    path = base.session_path(session, path, must_exist=True, allowed_roots=('reports',))
    report = read_json(path)
    request = report.get('request') if isinstance(report, dict) else None
    declaration = request.get('strategy') if isinstance(request, dict) else None
    variant = declaration.get('variant') if isinstance(declaration, dict) else None
    if (variant not in expected or report.get('request') != expected[variant]
        or not isinstance(report.get('bindings'), dict)
        or not isinstance(report.get('run_id'), str) or base.RUN_ID_PATTERN.fullmatch(report['run_id']) is None):
        base.fail('Comparison requires both exact strategy candidates')
    request_path = base.bound_file(session, report.get('bindings', {}).get('request', {}))
    validate_request(session, payload, report['request'], request_path, report.get('run_id'))
    if path != session/'reports'/report['run_id']/'stretch-parameters.json':
        base.fail('Comparison parameter report path differs from its run ID')
    source = base.source_file(session, payload, plan['source'])
    derived = report['bindings'].get('derived_source')
    if report['bindings'].get('full_source') != (None if derived else plan['full_source']):
        base.fail('Comparison full-source lineage differs from the accepted parent')
    scientific_source = base.bound_file(session, derived) if derived else source
    if report.get('status') == 'solved':
        base.load_parameters(session, payload, path, derived_source=scientific_source if derived else None)
        trace = report['steps'][-1]
        output = base.bound_file(session, trace['selected']['.fit'])
        regions = read_json(base.bound_file(session, plan['regions']))['regions']
        constraints = protected.subject_constraints(expected[variant]['operators'][-1]['constraints'], regions,
            full_diagnostic=bool(plan['full_source']) and protected.full_is_diagnostic(payload))
        audit = protected.audit(base.pixels(scientific_source), base.pixels(output), regions,
                                constraints, expected[variant]['targets'], noise_diagnostic=protected.noise_is_diagnostic(payload), highlight_budget=base.optimization_enabled(payload))
        if not audit['eligible']:
            base.fail('Final candidate no longer satisfies shared comparison constraints')
        result = {'variant': variant, 'run_id': report['run_id'], 'parameters': base.record(session, path),
                  'status': 'solved', 'audit': audit, 'selected': trace['selected'],
                  'full': trace.get('selected-full'),
                  'transfer_cost': float(np.mean(np.abs(base.pixels(output)-base.pixels(scientific_source))))}
    else:
        bindings = report.get('bindings', {})
        if (report.get('schema') != (protected.DERIVED if derived else protected.REPORT) or report.get('policy') != protected.POLICY
            or report.get('status') not in {'failed', 'search_exhausted', 'timed_out', 'ineligible'}
            or bindings.get('source') != {**plan['source'], 'size': source.stat().st_size}
            or bindings.get('regions') != plan['regions'] or bindings.get('runtime') != plan['runtime']
            or bindings.get('implementation') != plan['implementation'] or bindings.get('profiles') != plan['profiles']):
            base.fail('Failed candidate is missing its bound native attempt evidence')
        for step in report.get('steps', []):
            for row in step.get('trials', []):
                for evidence in (row.get('evidence', {}), row.get('evidence', {}).get('full_source', {})):
                    for key in ('script', 'log'):
                        if evidence:
                            base.bound_file(session, evidence.get(key, {}))
        result = {'variant': variant, 'run_id': report['run_id'], 'parameters': base.record(session, path),
                  'status': report['status'], 'reason': report.get('reason', 'No compliant parameters found'),
                  'audit': None, 'requires_failure_review': True}
    return result


def recommend(rows, *, noise_diagnostic=False):
    valid = [r for r in rows if r['status'] == 'solved']
    if noise_diagnostic:
        return min(valid, key=lambda r: (-r['audit']['subject_contrast'], r['run_id']))['run_id'] if valid else None
    scored = [r for r in valid if r['audit']['separation'] is not None]
    if scored:
        best = max(r['audit']['separation'] for r in scored)
        pool = [r for r in scored if r['audit']['separation'] >= best-.05*abs(best)]
        key = lambda r: (r['audit']['sky_hf'], r['transfer_cost'], r['run_id'])
    else:
        pool = valid
        key = lambda r: (r['transfer_cost'], r['audit']['sky_hf'], r['run_id'])
    return min(pool, key=key)['run_id'] if pool else None


def compare(session_value, plan_value, parameter_paths):
    from deep_sky_siril_session import effective_session_payload, load_session, execution_policy_is_current
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        base.fail('Historical sessions are replay/finalize-only; create a current session for new comparisons')
    plan_path = base.session_path(session, plan_value, must_exist=True, allowed_roots=('reports',))
    plan, expected = load_plan(session, payload, base.record(session, plan_path))
    source = base.source_file(session, payload, plan['source'])
    full = base.accepted_parent(session, payload, source)
    if plan['full_source'] != (base.record(session, full) if full else None):
        base.fail('Comparison parent or full-source lineage changed')
    if len(parameter_paths) != 2:
        base.fail('Compare exactly one baseline and one morphology chain')
    rows = [candidate(session, payload, path, plan, expected) for path in parameter_paths]
    if {r['variant'] for r in rows} != set(VARIANTS):
        base.fail('Comparison must contain two distinct strategy candidates')
    rows.sort(key=lambda r: VARIANTS.index(r['variant']))
    directory = plan_path.parent
    path, panel = directory/'comparison.json', directory/'comparison-rois.png'
    if any(p.exists() or p.is_symlink() for p in (path, panel)):
        base.fail('Comparison evidence already exists; use a new strategy run ID')
    regions = read_json(base.bound_file(session, plan['regions']))['regions']
    solved = [r for r in rows if r['status'] == 'solved']
    labels = 'Parent / '+' / '.join(r['variant'] for r in solved)+'; identical [0,1] display'
    protected.local_panel(source, [base.bound_file(session, r['selected']['.fit']) for r in solved],
                          regions, panel, title=labels)
    result = {'schema': COMPARISON, 'policy': POLICY, 'strategy': base.record(session, plan_path),
              'candidates': rows, 'recommended_run_id': recommend(rows, noise_diagnostic=protected.noise_is_diagnostic(payload)), 'panel': base.record(session, panel),
              'display': 'identical_linear_0_1', 'requires_visual_review': True,
              'selection_basis': 'Final shared constraints; within 95% of best display separation, prefer lower sky HF, then smaller pixel change. No chain-length penalty.'}
    if protected.noise_is_diagnostic(payload):
        result['selection_basis'] = 'Display-separation reference only: weakest directional subject-to-sky median contrast. Actual adoption requires Stage 6 reference visibility, highlight detail, natural transitions and color review; sky MAD/HF remain diagnostic.'
    with path.open('x') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    return {'status': 'comparison_ready' if result['recommended_run_id'] else 'retain_parent',
            'comparison': str(path), 'recommended_run_id': result['recommended_run_id'], 'requires_visual_review': True}


def verify_comparison(session, payload, binding, parameters=None):
    try:
        return _verify_comparison(session, payload, binding, parameters)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        base.fail(f'Malformed stretch comparison evidence: {type(exc).__name__}')


def _verify_comparison(session, payload, binding, parameters=None):
    path = base.bound_file(session, binding)
    report = read_json(path)
    if isinstance(report, dict) and report.get('schema') in TAIL_COMPARISON_SCHEMAS:
        return verify_tail_comparison(session, payload, report, parameters)
    if (not isinstance(report, dict) or report.get('schema') != COMPARISON or report.get('policy') != POLICY
        or report.get('display') != 'identical_linear_0_1' or report.get('requires_visual_review') is not True):
        base.fail('Invalid stretch comparison evidence')
    plan, expected = load_plan(session, payload, report.get('strategy', {}))
    if path != session/'reports'/plan['run_id']/'comparison.json':
        base.fail('Comparison path differs from its strategy')
    rows = report.get('candidates')
    if not isinstance(rows, list) or len(rows) != 2 or [r.get('variant') for r in rows] != list(VARIANTS):
        base.fail('Comparison needs both ordered candidates')
    actual = [candidate(session, payload, base.bound_file(session, r['parameters']), plan, expected) for r in rows]
    if rows != actual or report.get('recommended_run_id') != recommend(actual, noise_diagnostic=protected.noise_is_diagnostic(payload)):
        base.fail('Comparison candidates, measurements or ranking changed')
    base.bound_file(session, report.get('panel', {}))
    if parameters and not any(r['status'] == 'solved' and r['parameters'] == parameters for r in rows):
        base.fail('Formal stretch parameters are not an eligible comparison candidate')
    return report


def review_paths(session, binding, parameters):
    report = verify_comparison(session, load_json(session/'session.json'), binding, parameters)
    records = [binding, report['panel']]
    if report['schema'] in TAIL_COMPARISON_SCHEMAS:
        records.append(report['before_tail']['.jpg'])
    for row in report['candidates']:
        records.append(row['parameters'])
        if row['status'] == 'solved':
            records.append(row['selected']['.jpg'])
            if row['full']:
                records.append(row['full']['.jpg'])
        elif report['schema'] == TAIL_COMPARISON_V2:
            records.append(row['diagnostic']['.jpg'])
            if row['full']:
                records.append(row['full']['.jpg'])
    return {r['path']: base.bound_file(session, r) for r in records}


TAIL_COMPARISON = 'starun-siril.stretch-tail-comparison.v1'
TAIL_COMPARISON_V2 = 'starun-siril.stretch-tail-comparison.v2'
TAIL_COMPARISON_SCHEMAS = {TAIL_COMPARISON, TAIL_COMPARISON_V2}


def _tail_candidate(session, payload, path, *, allow_ineligible):
    path = base.session_path(session, path, must_exist=True, allowed_roots=('reports',))
    report = read_json(path)
    if report.get('schema') == 'starun-siril.stretch-parameters.v3':
        from deep_sky_siril_stretch_stepwise import load_report, load_plan as stepwise_plan
        report, path, source = load_report(session, payload, path)
        if report['proposal']['action'] != 'finish' or report['status'] not in ({'solved', 'ineligible'} if allow_ineligible else {'solved'}) or 'main' not in report['branches']:
            base.fail('Tail comparison requires an explicitly completed ordinary stepwise candidate')
        protection = None
        if report['status'] == 'ineligible':
            if report['final_audit']['reasons'] != ['regional_constraints']:
                base.fail('Failed tail controls may miss display goals only')
            plan, goals = stepwise_plan(session, payload, report['request']['strategy']['plan'])
            regions = read_json(base.bound_file(session, plan['regions']))['regions']
            constraints = protected.subject_constraints(goals['protection'], regions, full_diagnostic=bool(plan['full_source']))
            protection = protected.audit(base.pixels(source), base.pixels(base.bound_file(session, report['steps'][-1]['selected']['.fit'])),
                regions, constraints, goals['targets'], noise_diagnostic=protected.noise_is_diagnostic(payload), highlight_budget=base.optimization_enabled(payload))
            if not protection['eligible']:
                base.fail('Failed tail control violates shared scientific protection')
        return report, path, source, protection
    if not allow_ineligible or report.get('status') != 'ineligible':
        return (*base.load_parameters(session, payload, path), None)
    if (report.get('schema') != protected.REPORT or report.get('generation') != protected.PROPOSAL_POLICY
        or report.get('request', {}).get('schema') != protected.PROPOSAL_REQUEST
        or path != session/'reports'/str(report.get('run_id'))/'stretch-parameters.json'):
        base.fail('A failed tail control requires a bound explicit proposal')
    bindings = report['bindings']
    request_path = base.bound_file(session, bindings['request'])
    request, source, regions, _ = base.read_request(session, payload, request_path)
    plan = read_json(base.bound_file(session, request['strategy']['plan']))
    if (request != report['request'] or bindings['source'] != {**request['source'], 'size': source.stat().st_size}
        or bindings['regions'] != request['regions'] or bindings.get('full_source') != plan['full_source']
        or 'derived_source' in bindings or bindings['runtime'] != plan['runtime']
        or bindings['implementation'] != plan['implementation'] or bindings['profiles'] != plan['profiles']
        or report['run_id'] != candidate_run_id(plan['run_id'], request['strategy']['variant'])):
        base.fail('Failed tail control source, regions or runtime changed')
    steps = report['steps']
    if (len(steps) != len(request['operators']) or len(steps) < 2
        or len(report['transfer_chain']) != len(steps)-1
        or report['transfer_chain'] != [step['selected_parameters'] for step in steps[:-1]]
        or steps[-1].get('selected_trial') is not None or len(steps[-1].get('trials', [])) != 1):
        base.fail('Failed tail control must finish its exact prefix and final native trial')
    protected.verify(session, report, source, regions, incomplete=True)
    trial = steps[-1]['trials'][0]
    if trial['audit']['reasons'] != ['regional_constraints']:
        base.fail('Failed tail controls may miss display goals only')
    goals = read_json(base.bound_file(session, plan['goals']))
    full_diagnostic = bool(plan['full_source']) and protected.full_is_diagnostic(payload)
    constraints = protected.subject_constraints(goals['protection'], regions, full_diagnostic=full_diagnostic)
    audit = protected.audit(base.pixels(source), base.pixels(base.bound_file(session, trial['retained']['candidate.fit'])),
        regions, constraints, request['targets'], noise_diagnostic=protected.noise_is_diagnostic(payload), highlight_budget=base.optimization_enabled(payload))
    if not audit['eligible'] or (not full_diagnostic and trial.get('full_audit') and not trial['full_audit']['eligible']):
        base.fail('Failed tail control violates shared scientific protection')
    return report, path, source, audit


def tail_candidates(session, payload, paths, *, schema=TAIL_COMPARISON_V2):
    from deep_sky_siril_color import color_statistics
    from deep_sky_siril_neutral import endpoint_transitions
    if len(paths) != 2:
        base.fail('Tail comparison requires one MTF and one explicit luminance GHS report')
    try:
        loaded = [_tail_candidate(session, payload, path, allow_ineligible=schema == TAIL_COMPARISON_V2) for path in paths]
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        base.fail(f'Malformed tail comparison evidence: {type(exc).__name__}')
    reports = [row[0] for row in loaded]
    left, right = reports
    if not any(report['status'] == 'solved' for report in reports):
        base.fail('Tail comparison requires at least one solved candidate')
    chains = [report['transfer_chain'] if report['status'] == 'solved' or report['schema'] == 'starun-siril.stretch-parameters.v3' else
              report['transfer_chain']+[report['steps'][-1]['trials'][0]['parameters']] for report in reports]
    for report, chain in zip(reports, chains):
        proposal = (report.get('generation'), report['request']['schema'])
        if (proposal not in {(protected.PROPOSAL_POLICY, protected.PROPOSAL_REQUEST),
                ('stepwise_prefix_feedback_v1', 'starun-siril.stretch-request.v5')} or len(chain) < 2):
            base.fail('Tail comparison requires explicit multi-step proposals; no parameter scan')
    if (chains[0][:-1] != chains[1][:-1]
        or left['request']['operators'][:-1] != right['request']['operators'][:-1]
        or left['request']['operators'][-1]['constraints'] != right['request']['operators'][-1]['constraints']
        or left['request']['targets'] != right['request']['targets']
        or any(left['bindings'].get(k) != right['bindings'].get(k)
               for k in ('source','regions','runtime','implementation','profiles','full_source','derived_source'))):
        base.fail('Tail candidates must share the linear parent, exact prefix, ROI and final constraints')
    if (chains[0][-1]['command'] != 'mtf' or chains[1][-1]['command'] != 'ght'
        or not {'-human','-clipmode=rgbblend'} <= set(chains[1][-1]['parameters'])
        or '-sat' in chains[1][-1]['parameters']):
        base.fail('Compare MTF with GHS -human -clipmode=rgbblend in that order')
    before = base.bound_file(session, left['steps'][-2]['selected']['.fit'])
    other = base.bound_file(session, right['steps'][-2]['selected']['.fit'])
    values = base.pixels(before)
    if values.shape[0] != 3 or not np.array_equal(values, base.pixels(other)):
        base.fail('RGB tail prefixes must have identical actual scientific pixels')
    regions = read_json(base.bound_file(session, left['bindings']['regions']))['regions']
    rows = []
    for variant, (report, path, _, protection) in zip(('mtf','luminance_ghs'), loaded):
        trace = report['steps'][-1]
        solved = report['status'] == 'solved'
        trial = trace['trials'][0]
        completed = solved or report['schema'] == 'starun-siril.stretch-parameters.v3'
        selected = trace['selected'] if completed else {suffix: trial['retained']['candidate'+suffix] for suffix in ('.fit','.jpg')}
        full = trace.get('selected-full') if completed else (
            {suffix: trial['retained']['full'+suffix] for suffix in ('.fit','.jpg')} if report['bindings'].get('full_source') else None)
        output = base.bound_file(session, selected['.fit'])
        after = base.pixels(output)
        colors = color_statistics(before, output, regions, extended=True)
        for row in colors:
            region = next(r for r in regions if r['id'] == row['id'])
            x0,y0,x1,y1 = region['rect']; h = values.shape[1]
            a, b = (data[:,h-y1:h-y0,x0:x1].astype(float) for data in (values,after))
            row['endpoints'] = [endpoint_transitions(x,y) for x,y in zip(a,b)]
            row['luminance_quantiles'] = {key: dict(zip(('p10','p50','p90','p99'), map(float,
                np.quantile(np.sum(data*np.array([.2126,.7152,.0722])[:,None,None],axis=0), [.1,.5,.9,.99]))))
                for key,data in (('before_tail',a),('after_tail',b))}
        row = {'variant': variant, 'run_id': report['run_id'], 'parameters': base.record(session,path),
               'status': report['status'], 'full': full, 'regions': colors,
               'audit': report.get('final_audit', trace['prefix_audit']) if completed else trial['audit']}
        if solved:
            row['selected'] = selected
        else:
            row.update(diagnostic=selected, reason=report['reason'], requires_failure_review=True, protection_audit=protection)
        rows.append(row)
    return rows, left['steps'][-2]['selected'], left['bindings']['regions']


def compare_tail(session_value, mtf_parameters, luminance_parameters, run_id):
    from deep_sky_siril_session import load_session, effective_session_payload
    session,payload,manifest = load_session(session_value)
    payload = effective_session_payload(session,payload,manifest)
    # Only derived diagnostics may extend registered historical scientific evidence.
    if not routing_required(payload) or payload.get('execution_policy', {}).get('color_preservation') != 'vivid_preservation_v1':
        base.fail('Tail comparison requires its frozen routing and color-preservation policies')
    if not base.RUN_ID_PATTERN.fullmatch(run_id):
        base.fail('Invalid tail comparison run ID')
    rows,before,regions = tail_candidates(session,payload,[mtf_parameters,luminance_parameters])
    directory = base.session_path(session, 'reports/'+run_id, allowed_roots=('reports',))
    if directory.exists() or directory.is_symlink():
        base.fail('Tail comparison evidence exists; use a new run ID')
    directory.mkdir()
    panel = directory/'tail-rois.png'
    labels = [name+(' / solved' if row['status'] == 'solved' else ' / display goals not met')
              for name, row in zip(('MTF', 'luminance GHS'), rows)]
    protected.local_panel(base.bound_file(session,before['.fit']),
        [base.bound_file(session,(r['selected'] if r['status'] == 'solved' else r['diagnostic'])['.fit']) for r in rows],
        read_json(base.bound_file(session,regions))['regions'],panel,
        title='Before tail / '+' / '.join(labels)+'; identical [0,1] display')
    report = {'schema': TAIL_COMPARISON_V2, 'policy': 'vivid_preservation_v1', 'run_id': run_id,
              'before_tail': before, 'regions': regions, 'candidates': rows, 'panel': base.record(session,panel),
              'display': 'identical_linear_0_1', 'requires_visual_review': True, 'recommended_run_id': None,
              'selection_basis': 'Color retention, core detail and continuous weak structure at shared final constraints; brightness or saturation alone never selects a winner.'}
    path = directory/'comparison.json'
    path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return {'status': 'comparison_ready', 'comparison': str(path), 'recommended_run_id': None, 'requires_visual_review': True}


def verify_tail_comparison(session,payload,report,parameters):
    if (report.get('schema') not in TAIL_COMPARISON_SCHEMAS
        or payload.get('execution_policy', {}).get('color_preservation') != 'vivid_preservation_v1'
        or report.get('policy') != 'vivid_preservation_v1' or report.get('recommended_run_id') is not None
        or report.get('display') != 'identical_linear_0_1' or report.get('requires_visual_review') is not True):
        base.fail('Invalid tail comparison policy or automatic selection')
    rows,before,regions = tail_candidates(session,payload,[base.bound_file(session,r['parameters']) for r in report['candidates']], schema=report['schema'])
    if rows != report['candidates'] or before != report['before_tail'] or regions != report['regions']:
        base.fail('Tail comparison measurements or source bindings changed')
    if parameters and not any(r['status'] == 'solved' and r['parameters'] == parameters for r in rows):
        base.fail('Formal parameters are not a solved compared tail candidate')
    base.bound_file(session,report['panel'])
    return report

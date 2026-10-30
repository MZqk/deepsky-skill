"""Agent-reviewed, immutable Stage 7 prefixes; all scientific pixels come from Siril."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import fcntl
import json
import shutil
import tempfile
import time

import deep_sky_siril_stretch as base
import deep_sky_siril_stretch_v2 as protected
from deep_sky_siril_contract import fingerprint, fingerprint_matches, load_json, sha256_file

POLICY = 'stepwise_prefix_feedback_v1'
GOALS = 'starun-siril.stretch-goals.v4'
PLAN = 'starun-siril.stretch-strategy.v3'
REQUEST = 'starun-siril.stretch-request.v5'
REPORT = 'starun-siril.stretch-parameters.v3'


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def read(path):
    return base.strict_json_bytes(path.read_bytes(), document='stepwise evidence')


@contextmanager
def locked(directory):
    with (directory/'.stepwise.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def current(payload):
    if payload.get('execution_policy', {}).get('stretch_execution') not in {POLICY, 'stepwise_default_v2'}:
        base.fail('Stepwise execution requires its frozen execution policy')


def request_view(plan, goals, binding, branches):
    operators = [deepcopy(step['operator']) for step in branches.get('main', {}).get('steps', [])]
    return {**({'artifact_focus': goals['artifact_focus']} if 'artifact_focus' in goals else {}),
        'schema': REQUEST, 'source': plan['source'], 'regions': plan['regions'],
        'profile': goals['profile'], 'targets': goals['targets'], 'core_absence': goals['core_absence'],
        'manual_lookup': goals['manual_lookup'], 'operators': operators,
        'strategy': {'plan': binding, 'variant': 'profile' if plan['mode'] == 'profile' else 'exception'}}


def validate_constraints(plan, goals, regions, rows):
    protected.validate({**goals, 'operators': [{'constraints': rows}]}, regions,
        full=True, full_diagnostic=bool(plan['full_source']))


def validate_goals(session, payload, plan, goals):
    try:
        return _validate_goals(session, payload, plan, goals)
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        base.fail('Malformed stepwise goals: '+type(exc).__name__)


def _validate_goals(session, payload, plan, goals):
    from deep_sky_siril_morphology import assess
    from deep_sky_siril_metrics import measure_regions
    from deep_sky_siril_stretch_profiles import source_previews, merge_constraints
    fields = {'schema', 'profile', 'reason', 'evidence', 'targets', 'core_absence', 'protection',
              'final_constraints', 'manual_lookup', 'morphology', 'branches', 'initial', 'revision'}
    if not isinstance(goals, dict) or set(goals)-{'tail_control', 'artifact_focus'} != fields or goals.get('schema') != GOALS:
        base.fail('Stepwise goals require exact v4 fields; future factors must be omitted')
    from deep_sky_siril_artifact_focus import validate_initial
    validate_initial(session, payload, plan, goals)
    profiles = load_json(protected.PROFILE_PATH)
    if (goals.get('profile') not in profiles or goals['profile'] == 'custom'
        or not isinstance(goals['reason'], str) or not goals['reason'].strip()):
        base.fail('Stepwise goals require an observed morphology and reason')
    source = base.source_file(session, payload, plan['source'])
    region_path = base.bound_file(session, plan['regions'])
    if measure_regions(source, None, region_path)['status'] != 'measured':
        base.fail('Stepwise ROIs must bind finite accepted-parent geometry')
    regions = load_json(region_path)['regions']
    if plan['full_source']:
        base.bound_file(session, plan['full_source'], ('artifacts',))
    preview = source_previews(session, payload, plan['source'])
    if (not isinstance(goals['evidence'], list) or not 1 <= len(goals['evidence']) <= 16
        or not any(row in preview for row in goals['evidence'])):
        base.fail('Stepwise morphology requires a committed actual source preview')
    for item in goals['evidence']:
        if item.get('path') == plan['source']['path']:
            if item.get('sha256') != plan['source']['sha256']:
                base.fail('Stepwise source evidence changed')
        else:
            base.bound_file(session, item, ('reports', 'previews', 'artifacts'))
    morphology = assess(session, payload, plan, goals)
    if morphology['issues']:
        base.fail('Stepwise morphology is unsupported: '+json.dumps(morphology['issues']))
    branches = goals['branches']
    if not isinstance(branches, dict) or set(branches) not in ({'main'}, {'gentle', 'faint'}):
        base.fail('Stepwise branches must be main, or gentle and faint; HOO uses its existing workflow')
    if 'main' in branches:
        from deep_sky_siril_stretch_profiles import validate_body_chain
        validate_body_chain(payload, branches['main'])
    for chain in branches.values():
        if (not isinstance(chain, list) or not 1 <= len(chain) <= 32
            or any(not isinstance(step, dict) or set(step) != {'method', 'purpose'}
                or step.get('method') not in {'mtf', 'asinh', 'ght'}
                or not isinstance(step['purpose'], str) or not step['purpose'].strip() for step in chain)):
            base.fail('Freeze only operator order and purposes, without future factors or anchors')
    if set(branches) == {'main'} and plan['mode'] == 'profile' and [s['method'] for s in branches['main']] != [s['method'] for s in profiles[goals['profile']]['preferred_chain']]:
        base.fail('Stepwise main operators must follow their morphology chain or explicit exception')
    if ('main' in branches and plan['mode'] == 'exception' and goals['revision'] is None
        and not 1 <= len(branches['main']) <= 2):
        base.fail('Initial ordinary exceptions retain the one/two-step morphology rule')
    for rows in (goals['protection'], merge_constraints(goals['protection'], goals['final_constraints'])):
        validate_constraints(plan, goals, regions, rows)
    if 'tail_control' in goals:
        control, _, parent = load_report(session, payload, base.bound_file(session, goals['tail_control']), finished=True)
        chain = branches.get('main', [])
        if (parent != source or len(chain) < 2 or chain[-1]['method'] != 'ght'
            or control['transfer_chain'][-1]['command'] != 'mtf' or len(control['transfer_chain']) != len(chain)):
            base.fail('A GHS tail control requires a finished same-source multi-step MTF candidate')
    lookup = goals['manual_lookup']
    if (not isinstance(lookup, dict) or set(lookup) != {'status', 'reason', 'evidence'}
        or lookup['status'] not in {'performed', 'not_needed'} or not isinstance(lookup['reason'], str)
        or not lookup['reason'].strip() or not isinstance(lookup['evidence'], list)
        or (lookup['status'] == 'performed') != bool(lookup['evidence'])):
        base.fail('Declare authentic manual lookup evidence or a complete-skeleton rationale')
    if lookup['evidence']:
        from query_siril_manual import verify_query_evidence_documents
        documents = []
        for item in lookup['evidence']:
            path = base.bound_file(session, item)
            if not item['path'].startswith('reports/manual-evidence/'):
                base.fail('Manual evidence must be in reports/manual-evidence')
            documents.append(load_json(path))
        verify_query_evidence_documents(documents, skill_root=base.ROOT, session_payload=payload)
    return source, regions


def validate_revision(session, payload, plan, goals):
    revision = goals['revision']
    marker = session/'reports'/('.stretch-profile-'+plan['source']['sha256']+'.json')
    first = load_json(marker) if marker.exists() else None
    if first:
        first_plan = read(base.bound_file(session, first))
        first_goals = read(base.bound_file(session, first_plan['goals']))
        if first_plan['source'] != plan['source'] or (first_goals['profile'] != goals['profile'] and plan['mode'] != 'exception'):
            base.fail('A new strategy cannot reset its original source or morphology classification')
    if revision is None:
        if first and plan['root'] != first:
            base.fail('Stepwise strategy cannot reset the initial morphology')
        if any(load_json(p).get('source_sha256') == plan['source']['sha256'] for p in session.glob('reports/*/scientific-attempt.json')):
            base.fail('A native attempt exists; bind a revision to create another candidate')
        return
    if (not isinstance(revision, dict) or not {'parameters', 'reason'} <= set(revision)
        or set(revision)-{'parameters', 'reason', 'target_change', 'affected_regions', 'evidence', 'conflict_review'}
        or not isinstance(revision['reason'], str) or not revision['reason'].strip()):
        base.fail('A new candidate requires its previous parameters and a concrete revision reason')
    path = base.bound_file(session, revision['parameters'], ('reports', 'runs'))
    old = load_json(path)
    if old.get('schema') == REPORT:
        old, _, _ = load_report(session, payload, path)
        old_plan, old_goals = load_plan(session, payload, old['request']['strategy']['plan'])
    else:
        from deep_sky_siril_stretch_profiles import _previous_attempt, load_plan as old_load_plan
        # Existing batch revisions retain their stricter evidence contract.
        prior, old_plan = _previous_attempt(session, payload, revision, plan['source'])
        old_goals = load_json(base.bound_file(session, old_plan['goals']))
        old = prior
    if old_plan['source'] != plan['source'] or (first and plan['root'] != first):
        base.fail('A revision must bind its original linear parent and morphology root')
    if old_goals['profile'] != goals['profile'] and plan['mode'] != 'exception':
        base.fail('Changing the morphology profile requires an explicit exception candidate')
    previous_regions = load_json(base.bound_file(session, old_plan['regions']))['regions']
    regions = load_json(base.bound_file(session, plan['regions']))['regions']
    protected_ids = {r['id'] for r in previous_regions if r['role'] in {'core', 'stars'}}
    if any(r not in regions for r in previous_regions if r['id'] in protected_ids):
        base.fail('Revisions cannot move or remove core or stellar protection ROIs')
    rows = {(r['region'], r['statistic']): r for r in goals['protection']}
    for row in old_goals['protection']:
        key = (row['region'], row['statistic'])
        if key[0] in protected_ids and key[1] in {'p99', 'p999', 'max_rgb', 'span'}:
            new = rows.get(key)
            if new is None or (new['min'] < row['min'] if key[1] == 'span' else new['max'] > row['max']):
                base.fail('Revisions cannot weaken highlight or core-span protection')


def init_requests(session, payload, source_value, regions_value, goals_value, run_id, mode, *, preflight=False):
    current(payload)
    if mode not in {'profile', 'exception'} or base.RUN_ID_PATTERN.fullmatch(str(run_id)) is None:
        base.fail('Stepwise initialization needs a profile or exception candidate and valid run ID')
    source = Path(payload['input']['path']) if str(source_value) == '@input' else base.session_path(session, source_value, must_exist=True, allowed_roots=('artifacts',))
    binding = {'path': '@input' if str(source_value) == '@input' else source.relative_to(session).as_posix(), 'sha256': sha256_file(source)}
    full = base.accepted_parent(session, payload, source)
    regions_path = base.session_path(session, regions_value, must_exist=True, allowed_roots=('reports',))
    goals_path = base.session_path(session, goals_value, must_exist=True, allowed_roots=('reports',))
    goals = read(goals_path)
    if isinstance(goals, dict) and 'channels' in goals:
        base.fail('HOO uses its dedicated batch workflow; stepwise execution is unsupported')
    directory = session/'reports'/run_id
    if directory.exists():
        base.fail('Stepwise strategy already exists; use a new run ID')
    marker = session/'reports'/('.stretch-profile-'+binding['sha256']+'.json')
    plan = {'schema': PLAN, 'policy': POLICY, 'run_id': run_id, 'mode': mode,
        'root': load_json(marker) if marker.exists() else None, 'source': binding,
        'full_source': base.record(session, full) if full else None, 'regions': base.record(session, regions_path),
        'goals': base.record(session, goals_path), 'runtime': load_json(session/'reports/tool-probe.json')['tools']['siril_cli'],
        'implementation': protected.implementation(), 'profiles': protected.profiles_fingerprint()}
    _, regions = validate_goals(session, payload, plan, goals)
    validate_revision(session, payload, plan, goals)
    role = 'main' if 'main' in goals['branches'] else 'gentle'
    initial = {'schema': REQUEST, 'strategy': {'plan': {'path': 'reports/'+run_id+'/strategy.json', 'sha256': '0'*64}, 'variant': 'profile' if mode == 'profile' else 'exception'},
        'previous': None, 'feedback': None, 'action': 'step', 'branch': role,
        'anchor_domain': binding, 'step': goals['initial']}
    validate_step(plan, goals, regions, initial, [])
    data = base.pixels(source)
    step_parameters(payload, goals['initial'], data, protected.statistics(data, regions))
    if not plan['runtime'].get('compatible') or not fingerprint_matches(plan['runtime'].get('fingerprint', {})):
        base.fail('Frozen Siril runtime is missing or changed')
    if preflight:
        return {'schema': 'starun-siril.stretch-constraint-preflight.v3', 'status': 'ready', 'execution': POLICY,
            'branches': goals['branches'], 'current_step': goals['initial'], 'future_factors': 'undeclared',
            'final_constraints': final_constraints(goals), 'issues': [], 'requires_visual_review': True}
    directory.mkdir()
    write(directory/'strategy.json', plan)
    plan_binding = base.record(session, directory/'strategy.json')
    initial['strategy']['plan'] = plan_binding
    write(directory/'first-request.json', initial)
    if not marker.exists():
        write(marker, plan_binding)
    return {'status': 'prepared', 'strategy': str(directory/'strategy.json'),
        'request': str(directory/'first-request.json'), 'candidate': run_id, 'branch': role,
        'next_action': 'solve one step with a new run ID', 'requires_visual_review': True}


def load_plan(session, payload, binding):
    current(payload)
    path = base.bound_file(session, binding)
    plan = read(path)
    fields = {'schema', 'policy', 'run_id', 'mode', 'root', 'source', 'full_source', 'regions', 'goals', 'runtime', 'implementation', 'profiles'}
    if (not isinstance(plan, dict) or set(plan) != fields or plan.get('schema') != PLAN or plan.get('policy') != POLICY
        or base.RUN_ID_PATTERN.fullmatch(str(plan.get('run_id'))) is None or path != session/'reports'/plan['run_id']/'strategy.json'):
        base.fail('Malformed or misplaced stepwise strategy')
    if (plan['runtime'] != load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
        or not fingerprint_matches(plan['runtime'].get('fingerprint', {}))
        or not protected.implementation_matches(session, plan['implementation'])
        or plan['profiles'] != protected.profiles_fingerprint(session)):
        base.fail('Stepwise runtime, implementation or morphology recipes changed')
    goals = read(base.bound_file(session, plan['goals']))
    validate_goals(session, payload, plan, goals)
    marker = session/'reports'/('.stretch-profile-'+plan['source']['sha256']+'.json')
    if not marker.is_file() or load_json(marker) != (plan['root'] or binding):
        base.fail('Stepwise initial morphology binding changed')
    if plan['root']:
        base.bound_file(session, plan['root'])
    return plan, goals


def final_constraints(goals):
    from deep_sky_siril_stretch_profiles import merge_constraints
    return merge_constraints(goals['protection'], goals['final_constraints'])


def validate_step(plan, goals, regions, request, steps):
    from deep_sky_siril_stretch_profiles import merge_constraints
    role, step = request['branch'], request.get('step')
    chain = goals['branches'].get(role, [])
    if len(steps) >= len(chain):
        base.fail('The planned branch has no unexecuted step; explicitly finish it')
    definition = chain[len(steps)]
    fields = {'method', 'purpose', 'factors', 'constraints'} | ({'landmarks'} if definition['method'] == 'ght' else set())
    if (not isinstance(step, dict) or set(step) != fields
        or any(step.get(key) != definition[key] for key in ('method', 'purpose'))):
        base.fail('Submit only the next planned operator with its current factors and stage constraints')
    protected.proposal_tokens(step, 0., {'LP': 0., 'SP': .5, 'HP': 1.})
    rows = merge_constraints(goals['protection'], step['constraints'])
    validate_constraints(plan, goals, regions, rows)
    ids = {r['id'] for r in regions}
    if step['method'] == 'ght':
        if not isinstance(step['landmarks'], dict) or set(step['landmarks']) != {'LP', 'SP', 'HP'}:
            base.fail('GHS requires exact current-domain LP/SP/HP references')
        for ref in step['landmarks'].values():
            if (not isinstance(ref, dict) or set(ref) != {'region', 'statistic'}
                or ref['region'] not in ids or ref['statistic'] not in base.STATISTICS):
                base.fail('GHS anchors require actual current-domain ROI statistics')
    return rows


def initial_branches(goals):
    return {role: {'transfer_chain': [], 'steps': [], 'complete': False} for role in goals['branches']}


def pending(branches):
    for role in ('main', 'gentle', 'faint'):
        if role in branches and not branches[role]['complete']:
            return role
    return None


def validate_feedback(session, request, previous, regions):
    feedback = request['feedback']
    from deep_sky_siril_artifact_focus import enabled as artifact_enabled
    artifact = artifact_enabled(load_json(session/'session.json'))
    fields = {'materials', 'observations', 'reason'} | ({'artifacts'} if artifact else set())
    if (not isinstance(feedback, dict) or set(feedback) != fields
        or not isinstance(feedback['reason'], str) or not feedback['reason'].strip()
        or not isinstance(feedback['materials'], list) or not isinstance(feedback['observations'], list)):
        base.fail('Continuation requires inspected materials, concrete ROI observations and a reason')
    role = previous['proposal']['branch']
    trace = previous['branches'][role]['steps'][-1]
    required = [request['previous'], trace['selected']['.jpg'], trace['local_panel']]
    allowed = required + list(trace['selected'].values()) + list(trace.get('selected-full', {}).values())
    if artifact and feedback.get('artifacts', {}).get('report'):
        packet = load_json(base.bound_file(session, feedback['artifacts']['report'], ('artifacts',)))
        required.append(packet['panel'])
        allowed.append(packet['panel'])
    if request['action'] == 'finish' and 'gentle' in previous['branches']:
        gentle = previous['branches']['gentle']['steps'][-1]
        required += [gentle['selected']['.jpg'], gentle['local_panel']]
        allowed += [gentle['local_panel'], *gentle['selected'].values(), *gentle.get('selected-full', {}).values()]
    if any(item not in feedback['materials'] for item in required):
        base.fail('Inspect the previous receipt, actual preview and local ROI panel before continuing')
    for item in feedback['materials']:
        if item not in allowed:
            base.fail('Feedback materials must belong to the bound actual prefix')
        base.bound_file(session, item, ('reports', 'artifacts'))
    observed = set()
    for row in feedback['observations']:
        if (not isinstance(row, dict) or set(row) != {'region', 'observation'} or row['region'] in observed
            or row['region'] not in {r['id'] for r in regions} or not isinstance(row['observation'], str) or not row['observation'].strip()):
            base.fail('Feedback needs unique real ROI IDs and concrete observations')
        observed.add(row['region'])
    if observed != {r['id'] for r in regions}:
        base.fail('Observe every declared subject and protection ROI before continuing')


def read_request(session, payload, request_path, *, seen=None):
    try:
        return _read_request(session, payload, request_path, seen=seen)
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        base.fail('Malformed stepwise request: '+type(exc).__name__)


def _read_request(session, payload, request_path, *, seen=None):
    path = base.session_path(session, request_path, must_exist=True, allowed_roots=('reports',))
    request = read(path)
    action = request.get('action') if isinstance(request, dict) else None
    fields = {'schema', 'strategy', 'previous', 'feedback', 'action', 'branch'}
    fields |= {'step', 'anchor_domain'} if action == 'step' else {'reason'}
    if action == 'finish':
        fields.add('mask')
    if (not isinstance(request, dict) or set(request) != fields or request.get('schema') != REQUEST
        or action not in {'step', 'finish_branch', 'finish'}
        or not isinstance(request['strategy'], dict) or set(request['strategy']) != {'plan', 'variant'}):
        base.fail('Invalid stepwise request fields or action')
    plan, goals = load_plan(session, payload, request['strategy']['plan'])
    expected_variant = 'profile' if plan['mode'] == 'profile' else 'exception'
    if request['strategy']['variant'] != expected_variant:
        base.fail('Stepwise candidate identity differs from the frozen strategy')
    source = base.source_file(session, payload, plan['source'])
    regions = load_json(base.bound_file(session, plan['regions']))['regions']
    previous = None
    branches = initial_branches(goals)
    if request['previous'] is None:
        if (request['feedback'] is not None or action != 'step' or request['step'] != goals['initial']
            or path != session/'reports'/plan['run_id']/'first-request.json'):
            base.fail('Only the immutable initial request can start a stepwise candidate')
    else:
        previous, _, _ = load_report(session, payload, base.bound_file(session, request['previous']), seen=seen)
        if (previous['request']['strategy'] != request['strategy']
            or previous['status'] not in {'awaiting_next_step', 'branch_complete'}):
            base.fail('Continue only a successful unfinished prefix from this candidate')
        branches = deepcopy(previous['branches'])
        validate_feedback(session, request, previous, regions)
        from deep_sky_siril_artifact_focus import feedback as artifact_feedback
        artifact_feedback(session, payload, request, previous)
    role = pending(branches)
    if action == 'finish':
        if 'main' in branches:
            role = 'main'
            if not branches[role]['steps']:
                base.fail('An empty branch cannot be finalized')
            from deep_sky_siril_stretch_profiles import validate_body_chain
            validate_body_chain(payload, branches[role]['transfer_chain'])
        elif role is not None:
            base.fail('Finish gentle and faint branches explicitly before choosing an HDR mask')
        else:
            role = 'faint'
        if request['branch'] != role:
            base.fail('Finish request belongs to a different branch')
        if 'main' in branches:
            if request['mask'] is not None:
                base.fail('Ordinary stretch cannot declare an HDR mask')
        else:
            mask = request['mask']
            if not isinstance(mask, dict) or set(mask) != {'domain', 'parameters'} or mask['domain'] != branches['gentle']['steps'][-1]['selected']['.fit']:
                base.fail('HDR mask parameters must bind the actual gentle output domain')
            from deep_sky_siril_hdr import declaration
            declaration('# hdr-stretch: '+json.dumps(mask['parameters']))
    else:
        if request['branch'] != role:
            base.fail('Complete the current branch before starting the next branch')
        steps = branches[role]['steps']
        if action == 'step':
            validate_step(plan, goals, regions, request, steps)
            domain = steps[-1]['selected']['.fit'] if steps else plan['source']
            if request['anchor_domain'] != domain:
                base.fail('New anchors must bind the actual prefix input domain')
            data = base.pixels(base.bound_file(session, domain) if steps else source)
            step_parameters(payload, request['step'], data, protected.statistics(data, regions))
        elif role == 'main' or not steps:
            base.fail('Only a nonempty HDR branch can finish separately')
    if action != 'step' and (not isinstance(request['reason'], str) or not request['reason'].strip()):
        base.fail('Explicit completion requires its visual basis, including any skipped planned steps')
    return request, plan, goals, source, regions, branches, path


def prefix_audit(payload, plan, goals, regions, before, after, rows, *, hdr=False):
    constraints = protected.subject_constraints(rows, regions, full_diagnostic=bool(plan['full_source']))
    result = protected.audit(before, after, regions, constraints, goals['targets'],
        noise_diagnostic=protected.noise_is_diagnostic(payload), highlight_budget=base.optimization_enabled(payload))
    if hdr:
        result['diagnostic_only'] = True
    return result


def step_parameters(payload, step, data, statistics):
    black = base.safe_blackpoint([data], headroom=base.optimization_enabled(payload))
    landmarks = {key: statistics[ref['region']][ref['statistic']] for key, ref in step.get('landmarks', {}).items()}
    if landmarks and not 0 <= landmarks['LP'] <= landmarks['SP'] <= landmarks['HP'] <= 1:
        base.fail('Actual prefix LP/SP/HP bounds are invalid')
    tokens = protected.proposal_tokens(step, black, landmarks)
    return black, landmarks, {'command': tokens[0], 'parameters': tokens[1:]}


def aggregate(plan, goals, binding, branches, *, finished=False):
    request = request_view(plan, goals, binding, branches)
    if finished and request['operators']:
        request['operators'][-1]['constraints'] = final_constraints(goals)
    return request


def reserve_successor(session, plan, request, path, run_id, *, reserve=True):
    directory = base.bound_file(session, request['previous']).parent if request['previous'] else session/'reports'/plan['run_id']
    marker = directory/'stepwise-successor.json'
    claim = {'request': base.record(session, path), 'run_id': run_id, 'candidate': plan['run_id']}
    if marker.exists():
        if load_json(marker) != claim:
            base.fail('This prefix already has an actual successor; changed factors require a new candidate')
        base.fail('This successor was already started; inspect its report or create a new retry candidate')
    for receipt in session.glob('reports/*/stretch-parameters.json'):
        old = load_json(receipt)
        previous = old.get('proposal', {}).get('previous')
        same_parent = previous is None and request['previous'] is None or isinstance(previous, dict) and isinstance(request['previous'], dict) and previous.get('path') == request['previous'].get('path')
        native_evidence = any(item.get('path', '').endswith('.log') for item in old.get('native_records', []))
        if (old.get('schema') == REPORT and old.get('candidate') == plan['run_id']
            and same_parent and (native_evidence or old.get('pending') != 'correct_static_request')):
            base.fail('An executed successor lost its exclusive claim; restore evidence instead of forking')
    if reserve:
        write(marker, claim)


class PrefixTrials(base.NativeTrials):
    def _claim_attempt(self):
        if self.successor is not None:
            reserve_successor(*self.successor)
            self.successor = None
        super()._claim_attempt()


def run(session, payload, request_path, run_id, deadline, *, finish=False):
    current(payload)
    if base.RUN_ID_PATTERN.fullmatch(str(run_id)) is None:
        base.fail('Invalid stepwise run ID')
    request, plan, goals, source, regions, branches, path = read_request(session, payload, request_path)
    actual_full = base.accepted_parent(session, payload, source)
    if (base.record(session, actual_full) if actual_full else None) != plan['full_source']:
        base.fail('Stepwise full source differs from the accepted linear pair')
    if finish != (request['action'] != 'step'):
        base.fail('Use solve for a new step and finish for explicit completion requests')
    directory = session/'reports'/run_id
    with locked(session/'reports'/plan['run_id']):
        # Revalidate after acquiring the candidate lock; no competing successor can share its allowance.
        request, plan, goals, source, regions, branches, path = read_request(session, payload, path)
        from deep_sky_siril_artifact_focus import feedback as artifact_feedback
        if request['previous'] is not None:
            previous, _, _ = load_report(session, payload, base.bound_file(session, request['previous']))
            artifact_feedback(session, payload, request, previous, live=True)
        if directory.exists():
            base.fail('Stepwise evidence already exists; read the report without executing Siril')
        if not finish and not (session/'reports'/plan['run_id']/'scientific-attempt.json').exists() and len(list(session.glob('reports/*/scientific-attempt.json'))) >= 3:
            base.fail('Stage 7 has exhausted its three shared scientific attempts')
        reserve_successor(session, plan, request, path, run_id, reserve=False)
        directory.mkdir()
        binding = request['strategy']['plan']
        report = {'schema': REPORT, 'generation': POLICY, 'run_id': run_id, 'candidate': plan['run_id'],
            'status': 'awaiting_next_step', 'reason': None, 'proposal': request, 'branches': branches,
            'bindings': {'request': base.record(session, path), 'regions': plan['regions'],
                'source': {**plan['source'], 'size': source.stat().st_size}, 'full_source': plan['full_source'],
                'runtime': plan['runtime'], 'implementation': plan['implementation'], 'profiles': plan['profiles']},
            'request': aggregate(plan, goals, binding, branches), 'steps': [], 'transfer_chain': [],
            'scientific_parent': False, 'requires_visual_review': True}
        try:
            role = request['branch']
            branch = branches[role]
            if request['action'] == 'step':
                execute_step(session, payload, plan, goals, regions, source, request, path, branch, directory, deadline)
            elif request['action'] == 'finish_branch':
                reserve_successor(session, plan, request, path, run_id)
                branch['complete'] = True
                report['status'] = 'branch_complete'
            else:
                reserve_successor(session, plan, request, path, run_id)
                branch['complete'] = True
                report['status'] = 'solved'
                report['mask'] = request['mask']
                if 'main' in branches:
                    data = base.pixels(base.bound_file(session, branch['steps'][-1]['selected']['.fit']))
                    final = prefix_audit(payload, plan, goals, regions, base.pixels(source), data, final_constraints(goals))
                    report['final_audit'] = final
                    if not final['eligible']:
                        report.update(status='ineligible', reason='Final display constraints were not met')
        except TimeoutError as exc:
            report.update(status='timed_out', reason=str(exc))
        except (base.ContractError, OSError, ValueError) as exc:
            report.update(status='failed', reason=str(exc))
        report['request'] = aggregate(plan, goals, binding, branches, finished=report['status'] in {'solved', 'ineligible'})
        if 'main' in branches:
            report['steps'], report['transfer_chain'] = branches['main']['steps'], branches['main']['transfer_chain']
        report['pending'] = 'generate' if report['status'] == 'solved' else ('review_prefix_and_submit_next_request' if report['status'] in {'awaiting_next_step', 'branch_complete'} else 'new_candidate_required')
        if report['status'] == 'branch_complete':
            report['pending'] = 'propose_first_faint_step' if pending(branches) else 'choose_gentle_domain_mask_and_finish'
        elif report['status'] == 'awaiting_next_step' and len(branch['steps']) == len(goals['branches'][role]):
            report['pending'] = 'review_prefix_and_finish_candidate' if role == 'main' else 'review_prefix_and_finish_branch'
        report['native_records'] = [base.record(session, p) for p in sorted(directory.glob('parameter-trials/*')) if p.is_file()]
        predecessor = base.bound_file(session, request['previous']).parent if request['previous'] else session/'reports'/plan['run_id']
        if report['status'] in {'failed', 'timed_out'} and not (predecessor/'stepwise-successor.json').exists():
            report['pending'] = 'correct_static_request'
        from deep_sky_siril_stretch_trials import create
        report['trial_artifacts'] = create(session, report)
        write(directory/'stretch-parameters.json', report)
    return {'status': report['status'], 'parameters': str(directory/'stretch-parameters.json'),
        'candidate': plan['run_id'], 'branch': request['branch'], 'step': len(branches[request['branch']]['steps']),
        'pending': report['pending'], 'reason': report['reason'], 'requires_visual_review': True}


def execute_step(session, payload, plan, goals, regions, source, request, request_path, branch, directory, deadline):
    from deep_sky_siril_stretch_profiles import merge_constraints
    full = base.bound_file(session, plan['full_source'], ('artifacts',)) if plan['full_source'] else None
    steps = branch['steps']
    previous = base.bound_file(session, steps[-1]['selected']['.fit']) if steps else source
    previous_full = base.bound_file(session, steps[-1]['selected-full']['.fit']) if full and steps else full
    before = base.pixels(previous)
    full_data = base.pixels(previous_full) if full else None
    if full and full_data.shape != before.shape:
        base.fail('Full/starless prefix geometry differs')
    stats = protected.statistics(before, regions)
    step = request['step']
    black, landmarks, parameter = step_parameters(payload, step, before, stats)
    chain = branch['transfer_chain']+[parameter]
    evidence_dir = directory/'parameter-trials'
    evidence_dir.mkdir()
    trace = {'operator': deepcopy(step), 'method': step['method'], 'purpose': step['purpose'],
        'input_domain': 'nonlinear_intermediate' if steps else 'linear', 'anchor_domain': request['anchor_domain'],
        'input_sha256': sha256_file(previous), 'input_statistics': stats, 'blackpoint': black, 'landmarks': landmarks,
        'selected_parameters': parameter, 'trials': []}
    steps.append(trace)
    with tempfile.TemporaryDirectory(prefix='starun-stepwise-') as raw:
        oracle = PrefixTrials(session, plan['runtime'], Path(raw), evidence_dir, deadline)
        oracle.successor = (session, plan, request, request_path, directory.name)
        oracle.previews = True
        if not (session/'reports'/plan['run_id']/'scientific-attempt.json').exists():
            oracle.scientific_attempt = (session, payload, plan['run_id'], {**request, 'source': plan['source']})
        output, evidence = oracle.run(source, chain)
        selected = {}
        for suffix in ('.fit', '.jpg'):
            destination = evidence_dir/('selected'+suffix)
            shutil.copyfile(output.with_suffix(suffix), destination)
            selected[suffix] = base.record(session, destination)
        rows = merge_constraints(goals['protection'], step['constraints'])
        data = base.pixels(output)
        if data.shape != before.shape:
            base.fail('Stepwise replay changed scientific geometry')
        audit = prefix_audit(payload, plan, goals, regions, base.pixels(source), data, rows, hdr='main' not in goals['branches'])
        step_audit = prefix_audit(payload, plan, goals, regions, before, data, rows, hdr='main' not in goals['branches'])
        panel = evidence_dir/'roi-panel.jpg'
        protected.local_panel(previous, [output], regions, panel, title='Actual input prefix / next prefix; identical [0,1] display')
        overlay = protected.highlight_overlay(before, data, output.with_suffix('.jpg'))
        retained = {'candidate.fit': selected['.fit'], 'candidate.jpg': selected['.jpg']}
        if overlay:
            overlay_path = evidence_dir/'candidate-highlight.png'
            overlay_path.write_bytes(overlay)
            retained['candidate-highlight.png'] = base.record(session, overlay_path)
        trace.update(selected=selected, selected_replay=evidence, prefix_audit=audit, step_audit=step_audit, constraints=audit['constraints'],
            local_panel=base.record(session, panel), trials=[{'parameters': parameter, 'audit': audit, 'evidence': evidence, 'retained': retained}])
        branch['transfer_chain'] = chain
        if full:
            full_output, full_evidence = oracle.run(full, chain)
            full_selected = {}
            for suffix in ('.fit', '.jpg'):
                destination = evidence_dir/('selected-full'+suffix)
                shutil.copyfile(full_output.with_suffix(suffix), destination)
                full_selected[suffix] = base.record(session, destination)
                retained['full'+suffix] = full_selected[suffix]
            if base.pixels(full_output).shape != data.shape:
                base.fail('Full diagnostic replay changed geometry')
            stars = {r['id'] for r in regions if r['role'] == 'stars'}
            full_audit = protected.audit(base.pixels(full), base.pixels(full_output), regions,
                [r for r in rows if r['region'] in stars], goals['targets'], stellar_only=True,
                noise_diagnostic=True, highlight_budget=base.optimization_enabled(payload))
            trace.update({'selected-full': full_selected, 'selected_full_replay': full_evidence, 'full_audit': full_audit})
        if 'main' in goals['branches'] and (not audit['eligible'] or not step_audit['eligible']):
            base.fail('The actual ordinary prefix violates frozen stage or scientific protection constraints')


def verify_native(session, trace, source, chain, *, full=False):
    from deep_sky_siril_processing import script_commands, transfer_chain
    evidence = trace['selected_full_replay' if full else 'selected_replay']
    selected = trace['selected-full' if full else 'selected']
    script = base.bound_file(session, evidence['script'])
    base.bound_file(session, evidence['log'])
    output = base.bound_file(session, selected['.fit'])
    base.bound_file(session, selected['.jpg'])
    commands = script_commands(script.read_text())
    loads, saves = ([row for row in commands if row[0] == key] for key in ('load', 'save'))
    if (len(loads) != 1 or Path(loads[0][1]) != source or len(saves) != 1
        or transfer_chain(script.read_text(), Path(saves[0][1]).resolve()) != chain
        or evidence['output_sha256'] != sha256_file(output)):
        base.fail('Frozen prefix differs from its original-source native replay')
    return output


def load_report(session, payload, path, *, finished=False, seen=None):
    try:
        return _load_report(session, payload, path, finished=finished, seen=seen)
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        base.fail('Malformed stepwise evidence: '+type(exc).__name__)


def _load_report(session, payload, path, *, finished=False, seen=None):
    path = base.session_path(session, path, must_exist=True, allowed_roots=('reports',))
    seen = set() if seen is None else set(seen)
    if path in seen or len(seen) > 70:
        base.fail('Cyclic or excessive stepwise receipt chain')
    seen.add(path)
    report = read(path)
    if (report.get('schema') != REPORT or report.get('generation') != POLICY
        or base.RUN_ID_PATTERN.fullmatch(str(report.get('run_id'))) is None
        or path != session/'reports'/report['run_id']/'stretch-parameters.json'):
        base.fail('Invalid stepwise parameter receipt identity')
    if finished and report.get('status') != 'solved':
        base.fail('Only explicitly finished stepwise candidates can generate or authorize a stretch')
    proposal, plan, goals, source, regions, before, request_path = read_request(session, payload,
        base.bound_file(session, report['bindings']['request']), seen=seen)
    bindings = {'request': base.record(session, request_path), 'regions': plan['regions'],
        'source': {**plan['source'], 'size': source.stat().st_size}, 'full_source': plan['full_source'],
        'runtime': plan['runtime'], 'implementation': plan['implementation'], 'profiles': plan['profiles']}
    if report['bindings'] != bindings or report['proposal'] != proposal or report['candidate'] != plan['run_id']:
        base.fail('Stepwise request or source bindings changed')
    for item in report.get('native_records', []):
        base.bound_file(session, item)
    predecessor = base.bound_file(session, proposal['previous']).parent if proposal['previous'] else session/'reports'/plan['run_id']
    expected_claim = {'request': bindings['request'], 'run_id': report['run_id'], 'candidate': plan['run_id']}
    claim_path = predecessor/'stepwise-successor.json'
    no_native_start = report['status'] in {'failed', 'timed_out'} and proposal['action'] == 'step' and not any(
        item['path'].endswith('.log') for item in report['native_records']) and report['branches'][proposal['branch']]['transfer_chain'] == before[proposal['branch']]['transfer_chain']
    if not no_native_start and (not claim_path.exists() or load_json(claim_path) != expected_claim):
        base.fail('Stepwise actual-successor claim changed')
    role, branches = proposal['branch'], report['branches']
    expected = deepcopy(before)
    action = proposal['action']
    terminal = report['status'] in {'failed', 'timed_out'}
    if action == 'step':
        actual_steps = branches[role]['steps']
        if len(actual_steps) not in ({len(before[role]['steps']), len(before[role]['steps'])+1} if terminal else {len(before[role]['steps'])+1}):
            base.fail('Each stepwise execution must append exactly one native step')
        if len(actual_steps) > len(before[role]['steps']):
            trace = actual_steps[-1]
            if trace['operator'] != proposal['step']:
                base.fail('Executed factors differ from the submitted current step')
            expected[role]['steps'].append(trace)
            if 'selected' in trace:
                prefix = before[role]['transfer_chain']+[trace['selected_parameters']]
                expected[role]['transfer_chain'] = prefix
                output = verify_native(session, trace, source, prefix)
                if plan['full_source'] and (not terminal or 'selected-full' in trace):
                    verify_native(session, trace, base.bound_file(session, plan['full_source'], ('artifacts',)), prefix, full=True)
                input_path = base.bound_file(session, before[role]['steps'][-1]['selected']['.fit']) if before[role]['steps'] else source
                data = base.pixels(input_path)
                stats = protected.statistics(data, regions)
                black, landmarks, parameter = step_parameters(payload, proposal['step'], data, stats)
                if (trace['input_sha256'] != sha256_file(input_path) or trace['anchor_domain'] != proposal['anchor_domain']
                    or trace['input_statistics'] != stats or trace['blackpoint'] != black or trace['landmarks'] != landmarks
                    or trace['selected_parameters'] != parameter):
                    base.fail('Prefix parameters or anchors do not match the actual measured input domain')
                from deep_sky_siril_stretch_profiles import merge_constraints
                audit = prefix_audit(payload, plan, goals, regions, base.pixels(source), base.pixels(output),
                    merge_constraints(goals['protection'], proposal['step']['constraints']), hdr='main' not in branches)
                step_audit = prefix_audit(payload, plan, goals, regions, data, base.pixels(output),
                    merge_constraints(goals['protection'], proposal['step']['constraints']), hdr='main' not in branches)
                if audit != trace['prefix_audit'] or trace['step_audit'] != step_audit or (not terminal and 'main' in branches and (not audit['eligible'] or not step_audit['eligible'])):
                    base.fail('Stepwise prefix audit changed or failed scientific protection')
                trial = trace['trials']
                if (len(trial) != 1 or trial[0]['parameters'] != trace['selected_parameters']
                    or trial[0]['audit'] != audit or trial[0]['evidence'] != trace['selected_replay']
                    or trace['constraints'] != audit['constraints']
                    or any(trial[0]['retained']['candidate'+suffix] != trace['selected'][suffix] for suffix in ('.fit', '.jpg'))):
                    base.fail('Stepwise trial diagnostics differ from the selected native prefix')
                if plan['full_source'] and 'selected-full' in trace:
                    from deep_sky_siril_stretch_profiles import merge_constraints
                    rows = merge_constraints(goals['protection'], proposal['step']['constraints'])
                    stars = {r['id'] for r in regions if r['role'] == 'stars'}
                    full_source = base.bound_file(session, plan['full_source'], ('artifacts',))
                    full_output = base.bound_file(session, trace['selected-full']['.fit'])
                    full_audit = protected.audit(base.pixels(full_source), base.pixels(full_output), regions,
                        [r for r in rows if r['region'] in stars], goals['targets'], stellar_only=True,
                        noise_diagnostic=True, highlight_budget=base.optimization_enabled(payload))
                    if trace['full_audit'] != full_audit:
                        base.fail('Full diagnostic prefix measurements changed')
                base.bound_file(session, trace['local_panel'])
    elif not terminal:
        expected[role]['complete'] = True
    if branches != expected:
        base.fail('A stepwise receipt changed its already executed prefix or branch state')
    is_final = report['status'] in {'solved', 'ineligible'}
    view = aggregate(plan, goals, proposal['strategy']['plan'], branches, finished=is_final)
    if report['request'] != view or report['steps'] != branches.get('main', {}).get('steps', []) or report['transfer_chain'] != branches.get('main', {}).get('transfer_chain', []):
        base.fail('Stepwise frozen scientific recipe differs from its actual prefix chain')
    if is_final:
        if action != 'finish' or report.get('mask') != proposal['mask']:
            base.fail('Only an explicit completion request can freeze a final candidate')
        if 'main' in branches:
            final = prefix_audit(payload, plan, goals, regions, base.pixels(source),
                base.pixels(base.bound_file(session, branches['main']['steps'][-1]['selected']['.fit'])), final_constraints(goals))
            if report['final_audit'] != final or (report['status'] == 'solved') != final['eligible']:
                base.fail('Stepwise final display acceptance changed')
    marker = session/'reports'/plan['run_id']/'scientific-attempt.json'
    if any(branch['transfer_chain'] for branch in branches.values()) and (not marker.is_file() or load_json(marker)['request']['strategy'] != proposal['strategy']):
        base.fail('Stepwise prefixes lost their shared scientific-attempt claim')
    if marker.is_file():
        first = load_json(session/'reports'/plan['run_id']/'first-request.json')
        ledger = load_json(marker)
        if ledger['request'] != {**first, 'source': plan['source']} or ledger['source_sha256'] != plan['source']['sha256']:
            base.fail('Stepwise scientific-attempt root changed')
    from deep_sky_siril_stretch_trials import verify
    verify(session, report)
    return report, path, source


def load_parameters(session, payload, path, source=None, chain=None):
    report, path, bound_source = load_report(session, payload, path, finished=True)
    if 'main' not in report['branches']:
        base.fail('HDR candidates generate through their frozen dual-branch recipe')
    if source is not None and source != bound_source or chain is not None and chain != report['transfer_chain']:
        base.fail('Formal source or transfer chain differs from the finished stepwise candidate')
    return report, path, bound_source


def finish(session_value, request_value, run_id):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload):
        base.fail('Historical sessions are replay/finalize-only')
    return run(session, payload, request_value, run_id, time.monotonic(), finish=True)


def status(session, payload, path):
    report, path, _ = load_report(session, payload, path)
    role = pending(report['branches']) or report['proposal']['branch']
    result = {'parameters': base.record(session, path), 'candidate': report['candidate'],
        'status': report['status'], 'branch': role, 'current_step': len(report['branches'][role]['steps']),
        'pending': report['pending'], 'scientific_parent': False,
        'remaining_attempts': max(0, 3-len(list(session.glob('reports/*/scientific-attempt.json'))))}
    from deep_sky_siril_artifact_focus import enabled as artifact_enabled, plan_for, pending_review
    if artifact_enabled(payload):
        plan, goals, _ = plan_for(session, payload, report['request']['strategy'])
        result['artifact_review'] = pending_review(session, payload, plan, goals, base.record(session, path))
    formal = path.parent/'stepwise-formal-run.json'
    if formal.exists():
        claim = load_json(formal)
        if claim.get('parameters') != base.record(session, path) or claim.get('candidate') != report['candidate'] or base.RUN_ID_PATTERN.fullmatch(str(claim.get('run_id'))) is None:
            base.fail('The stepwise formal-run claim changed')
        receipt = session/'runs'/(claim['run_id']+'.json')
        result.update(formal_run=claim['run_id'], pending='inspect_claimed_formal_run')
        if receipt.exists():
            result['pending'] = 'review_formal_run' if load_json(receipt)['status'] == 'success' else 'new_candidate_required'
            for review in (load_json(p) for p in session.glob('reviews/*.json')):
                if review.get('run_id') != claim['run_id'] or review.get('verdict') != 'accept':
                    continue
                if artifact_enabled(payload):
                    from deep_sky_siril_artifact_focus import formal_validate
                    formal_validate(session, payload, load_json(receipt), review)
                    result['artifact_review'].update(pending_areas=[], materials_prepared=True)
                result['pending'] = 'continue_after_stage7'
    return result


def candidate_statuses(session, payload):
    rows = []
    for path in sorted(session.glob('reports/*/strategy.json')):
        if load_json(path).get('schema') != PLAN:
            continue
        plan, goals = load_plan(session, payload, base.record(session, path))
        directory, visited = path.parent, set()
        row = {'candidate': plan['run_id'], 'status': 'prepared', 'branch': 'main' if 'main' in goals['branches'] else 'gentle',
            'current_step': 0, 'pending': 'solve_first_step', 'scientific_parent': False,
            'remaining_attempts': max(0, 3-len(list(session.glob('reports/*/scientific-attempt.json'))))}
        while (directory/'stepwise-successor.json').exists():
            if directory in visited:
                base.fail('Cyclic actual-successor records')
            visited.add(directory)
            claim = load_json(directory/'stepwise-successor.json')
            base.bound_file(session, claim['request'])
            if claim['candidate'] != plan['run_id'] or base.RUN_ID_PATTERN.fullmatch(str(claim['run_id'])) is None:
                base.fail('Invalid actual-successor candidate identity')
            directory = session/'reports'/claim['run_id']
            report_path = directory/'stretch-parameters.json'
            if not report_path.exists():
                row.update(status='running_or_interrupted', pending='inspect_claimed_native_run', run_id=claim['run_id'])
                break
            row = status(session, payload, report_path)
        rows.append(row)
    return rows


def validate_hdr(session, payload, source, binding, hdr):
    final_binding = hdr.get('stepwise_parameters')
    if not final_binding:
        base.fail('Stepwise HDR requires its explicit finished parameter receipt')
    report, _, parent = load_report(session, payload, base.bound_file(session, final_binding), finished=True)
    plan, goals = load_plan(session, payload, binding)
    if (report['request']['strategy']['plan'] != binding or source != parent or 'main' in report['branches']
        or any(hdr[role+'_chain'] != report['branches'][role]['transfer_chain'] for role in ('gentle', 'faint'))
        or {k: hdr['mask'][k] for k in ('mask_low', 'mask_high', 'sigma')} != report['mask']['parameters']):
        base.fail('HDR execution differs from the finished stepwise branches or mask')
    guard = load_json(base.bound_file(session, hdr['protection']['request'])) if hdr.get('protection') else {}
    if (guard.get('source') != plan['source'] or guard.get('full_source') != plan['full_source']
        or guard.get('regions') != plan['regions'] or guard.get('targets') != goals['targets']
        or guard.get('constraints') != final_constraints(goals)):
        base.fail('Final HDR blend requires the frozen final ROI constraints and science protections')


def claim_formal(session, payload, binding, run_id):
    report, path, _ = load_report(session, payload, base.bound_file(session, binding), finished=True)
    plan, _ = load_plan(session, payload, report['request']['strategy']['plan'])
    with locked(session/'reports'/plan['run_id']):
        marker = path.parent/'stepwise-formal-run.json'
        if marker.exists():
            base.fail('This candidate already launched its formal run; native failure retries require a new candidate')
        write(marker, {'parameters': binding, 'run_id': run_id, 'candidate': report['candidate']})


def generate_hdr(session, payload, parameters, run_id=None, comparison=None):
    from deep_sky_siril_processing import _variable
    from deep_sky_siril_hdr import blend_expression, validate_stretch
    report, parameters, source = load_report(session, payload, parameters, finished=True)
    if comparison:
        base.fail('HDR uses its final blend review rather than ordinary tail controls')
    plan, goals = load_plan(session, payload, report['request']['strategy']['plan'])
    run_id = run_id or report['run_id']+'-run'
    if base.RUN_ID_PATTERN.fullmatch(run_id) is None:
        base.fail('Invalid HDR run ID')
    channels = base.pixels(source).shape[0]
    full = base.bound_file(session, plan['full_source'], ('artifacts',)) if plan['full_source'] else None
    script = session/'scripts'/(run_id+'.ssf')
    provenance = script.with_suffix('.provenance.json')
    guard_path = session/'reports'/(run_id+'-protection.json')
    outputs = []
    lines = ['requires 1.4.4 1.5.0', 'set32bits',
        '# hdr-stretch: '+json.dumps(report['mask']['parameters']),
        '# stretch-strategy: '+json.dumps(report['request']['strategy']['plan']),
        '# stepwise-stretch: '+json.dumps(base.record(session, parameters))]
    guard = {'schema': 'starun-siril.stretch-protection.v2', 'source': plan['source'],
        'full_source': plan['full_source'], 'regions': plan['regions'], 'profile': goals['profile'],
        'targets': goals['targets'], 'core_absence': goals['core_absence'], 'constraints': final_constraints(goals)}
    import hashlib
    guard_content = json.dumps(guard, indent=2, allow_nan=False)+'\n'
    lines.append('# stretch-protection: '+json.dumps({'path': guard_path.relative_to(session).as_posix(),
        'sha256': hashlib.sha256(guard_content.encode()).hexdigest(), 'size': len(guard_content.encode())}))

    def path(role, suffix='.fit'):
        return session/('previews' if suffix == '.jpg' else 'artifacts')/(run_id+'-'+role+suffix)

    def save(role, *, preview=True):
        output = path(role)
        outputs.append(output)
        lines.append('save '+json.dumps(str(output))+' -chksum')
        if preview:
            image = path(role, '.jpg')
            outputs.append(image)
            lines.append('savejpg '+json.dumps(str(image.with_suffix('')))+' 95')
        return output

    def branch(parent, role):
        lines.append('load '+json.dumps(str(parent)))
        lines.extend(' '.join([step['command'], *step['parameters']]) for step in report['branches'][role]['transfer_chain'])

    branch(source, 'gentle')
    lines.append('icc_assign sRGB')
    gentle = save('gentle')
    luminance = gentle
    if channels == 3:
        parts = [path('gentle-'+c) for c in 'rgb']
        outputs.extend(parts)
        lines.append('split '+' '.join(json.dumps(str(p.with_suffix(''))) for p in parts))
        r, g, b = (_variable(p, session) for p in parts)
        lines.append('pm '+json.dumps(f'0.2126 * {r} + 0.7152 * {g} + 0.0722 * {b}')+' -nosum')
        luminance = save('luminance', preview=False)
    lines.append('close')
    branch(source, 'faint')
    lines.append('icc_assign sRGB')
    faint = save('faint')
    lines.append('close')
    low, high, sigma = (report['mask']['parameters'][k] for k in ('mask_low', 'mask_high', 'sigma'))
    t = f'min(1, max(0, ({_variable(luminance, session)} - {low}) / {high-low}))'
    lines.extend(['pm '+json.dumps(f'{t}^2 * (3 - 2 * {t})')+' -nosum', 'gauss '+str(sigma), 'icc_assign sRGB'])
    mask = save('mask')
    blend_mask = mask
    if channels == 3:
        blend_mask = path('mask-rgb')
        outputs.append(blend_mask)
        lines.append('rgbcomp '+' '.join(json.dumps(str(mask)) for _ in range(3))+' '+json.dumps('-out='+str(blend_mask.with_suffix('')))+' -nosum')
    lines.extend(['pm '+json.dumps(blend_expression(gentle, faint, blend_mask, session))+' -nosum', 'icc_assign sRGB'])
    primary = save('hdr')
    lines.extend(['stat main', 'close'])
    if full:
        layers = []
        for role in ('gentle', 'faint'):
            branch(full, role)
            layers.append(save('full-'+role, preview=False))
            lines.append('close')
        lines.append('pm '+json.dumps(blend_expression(*layers, blend_mask, session))+' -nosum')
        save('full', preview=False)
        lines.append('icc_assign sRGB')
        image = path('full', '.jpg')
        outputs.append(image)
        lines.extend(['savejpg '+json.dumps(str(image.with_suffix('')))+' 95', 'close'])
    content = '\n'.join(lines)+'\n'
    for item in [script, provenance, guard_path, session/'runs'/(run_id+'.json'), *outputs]:
        if item.exists() or item.is_symlink():
            base.fail('HDR output or evidence already exists: '+str(item))
    with guard_path.open('x') as stream:
        stream.write(guard_content)
    hdr = validate_stretch(session, source, primary, content)
    validate_hdr(session, payload, source, report['request']['strategy']['plan'], hdr)
    with script.open('x') as stream:
        stream.write(content)
    write(provenance, {'schema': 'starun-siril.ssf-provenance.v1', 'contract_version': '1', 'run_id': run_id,
        'protocol': 'stretch', 'script': {'path': script.relative_to(session).as_posix(), 'sha256': sha256_file(script)},
        'references': base.protocol_reference_bindings('stretch', variant='hdr')+[{
            'path': 'references/stage7-stepwise.md', 'sha256': sha256_file(base.ROOT/'references/stage7-stepwise.md'), 'role': 'supporting'}],
        'command_policy': {'path': 'references/command-policy.json', 'sha256': sha256_file(base.ROOT/'references/command-policy.json')},
        'manual_lookup': goals['manual_lookup'], 'rationale': {'applicability': 'Explicit Agent-reviewed stepwise HDR.',
            'parameter_choices': ['Replay both frozen branches from the accepted linear source and use one actual-gentle-domain mask for full/starless.']}})
    return {'status': 'prepared', 'mode': 'hdr', 'script_path': str(script), 'provenance_path': str(provenance),
        'parent_path': str(source), 'primary_output': str(primary), 'expected_outputs': list(map(str, outputs)),
        'preview_outputs': [str(p) for p in outputs if p.suffix == '.jpg'], 'parameters': report}

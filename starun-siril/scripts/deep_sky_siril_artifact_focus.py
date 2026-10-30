"""Append-only Stage 7 review areas and crops of existing native previews."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import hashlib
import io
import json
import re

from PIL import Image, ImageDraw

import deep_sky_siril_stretch as base
from deep_sky_siril_contract import load_json, sha256_file

POLICY = 'structured_focus_v1'
FOCUS = 'starun-siril.review-focus.v1'
REPORT = 'starun-siril.artifact-focus.v1'
DIRECTORY = 'artifacts/stage7-reviews'
RISKS = {'stellar_residual', 'separation_patch', 'halo', 'ringing', 'flat_core',
         'color_fringe', 'gray_haze', 'structure_loss', 'texture_break', 'hdr_transition'}
CHANGES = {'not_seen', 'improved', 'unchanged', 'new_or_worse', 'uncertain'}


def enabled(payload):
    return payload.get('execution_policy', {}).get('artifact_review') == POLICY


def text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def write(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def read(path):
    return base.strict_json_bytes(path.read_bytes(), document='artifact focus')


def helper(session):
    from deep_sky_siril_stretch_v2 import implementation
    return implementation(session)['deep_sky_siril_artifact_focus.py']


def definition(session, payload, binding, source, *, seen=None):
    try:
        return _definition(session, payload, binding, source, seen=seen)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        base.fail('Malformed artifact focus: '+type(exc).__name__)


def _definition(session, payload, binding, source, *, seen=None):
    path = base.bound_file(session, binding)
    seen = set() if seen is None else set(seen)
    if path in seen or len(seen) >= 64:
        base.fail('Cyclic or excessive artifact focus history')
    seen.add(path)
    value = read(path)
    if (not isinstance(value, dict) or set(value) != {'schema', 'source', 'geometry', 'previous', 'areas', 'none_reason', 'evidence'}
        or value['schema'] != FOCUS or value['source'] != source):
        base.fail('Artifact focus must bind the exact linear parent and its review-only geometry')
    parent = base.source_file(session, payload, source)
    data = base.pixels(parent)
    height, width = data.shape[1:]
    if (value['geometry'] != {'width': width, 'height': height}
        or any(type(value['geometry'][key]) is not int for key in ('width', 'height'))):
        base.fail('Artifact focus geometry differs from the linear parent')
    areas = value['areas']
    if not isinstance(areas, list) or len(areas) > 32:
        base.fail('Declare at most 32 actual artifact focus areas')
    ids = set()
    for area in areas:
        if (not isinstance(area, dict) or set(area) != {'id', 'rect', 'risks', 'reason'}
            or not isinstance(area['id'], str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', area['id']) is None
            or area['id'] in ids or not text(area['reason']) or not isinstance(area['risks'], list)
            or not area['risks'] or any(not isinstance(r, str) or r not in RISKS for r in area['risks'])
            or len(set(area['risks'])) != len(area['risks'])):
            base.fail('Each artifact area needs a unique ID, actual risks and a selection reason')
        rect = area['rect']
        if (not isinstance(rect, list) or len(rect) != 4 or any(type(x) is not int for x in rect)
            or not 0 <= rect[0] < rect[2] <= width or not 0 <= rect[1] < rect[3] <= height):
            base.fail('Artifact focus rectangle is outside the original top-left geometry')
        ids.add(area['id'])
    if (not areas and not text(value['none_reason'])) or (areas and value['none_reason'] is not None):
        base.fail('No applicable artifact areas requires an explicit reason; do not fabricate rectangles')
    from deep_sky_siril_stretch_profiles import source_previews
    previews = source_previews(session, payload, source)
    if (not isinstance(value['evidence'], list) or not value['evidence']
        or not any(item in previews for item in value['evidence'])):
        base.fail('Artifact focus selection must cite an actual committed full-frame parent preview')
    for item in value['evidence']:
        if item not in previews:
            base.fail('Artifact selection evidence must belong to the accepted linear parent')
        image = base.bound_file(session, item, ('artifacts', 'previews'))
        with Image.open(image) as frame:
            if frame.size != (width, height):
                base.fail('Artifact selection preview geometry changed')
    if value['previous'] is not None:
        old = definition(session, payload, value['previous'], source, seen=seen)
        current = {area['id']: area for area in areas}
        if any(current.get(area['id']) != area for area in old['areas']):
            base.fail('Artifact focus additions cannot remove, move or replace registered areas')
    return value


def validate_initial(session, payload, plan, goals):
    if enabled(payload):
        if 'artifact_focus' not in goals:
            base.fail('Stage 7 requires an initial review-focus.v1 declaration, including an explicit no-area reason when applicable')
        definition(session, payload, goals['artifact_focus'], plan['source'])


def head(session, payload, plan, goals):
    binding = goals['artifact_focus']
    visited = set()
    while True:
        definition(session, payload, binding, plan['source'])
        if binding['sha256'] in visited or len(visited) >= 64:
            base.fail('Cyclic or excessive registered artifact focus updates')
        visited.add(binding['sha256'])
        path = session/'reports'/plan['run_id']/('artifact-focus-next-'+binding['sha256']+'.json')
        if not path.exists():
            return binding
        update = read(path)
        if not isinstance(update, dict) or set(update) != {'previous', 'focus'} or update['previous'] != binding:
            base.fail('Artifact focus successor record changed')
        new = definition(session, payload, update['focus'], plan['source'])
        if new['previous'] != binding:
            base.fail('Artifact focus successor does not preserve its registered predecessor')
        binding = update['focus']


@contextmanager
def locked(session, plan):
    with (session/'reports'/plan['run_id']/'.stepwise.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def plan_for(session, payload, declaration):
    from deep_sky_siril_stretch_profiles import load_plan
    binding = declaration.get('plan', declaration)
    plan, _ = load_plan(session, payload, binding)
    return plan, read(base.bound_file(session, plan['goals'])), binding


def parameter_frames(session, payload, report, path):
    frames = {}
    hdr = False
    if report.get('schema') == 'starun-siril.stretch-parameters.v3':
        hdr = 'main' not in report['branches']
        role = report['proposal']['branch']
        steps = report['branches'][role]['steps']
        if not steps or 'selected' not in steps[-1]:
            base.fail('Artifact review requires an actual native prefix preview')
        previous = steps[-2]['selected']['.jpg'] if len(steps) > 1 else None
        for branch in ('gentle', 'faint'):
            if report['proposal']['action'] == 'finish' and branch in report['branches']:
                frames[branch] = report['branches'][branch]['steps'][-1]['selected']['.jpg']
    else:
        steps = report['steps']
        if not steps or 'selected' not in steps[-1]:
            base.fail('Use a retained selected native prefix for artifact review')
        role, previous = 'main', steps[-2]['selected']['.jpg'] if len(steps) > 1 else None
    trace = steps[-1]
    if previous:
        frames['previous_prefix'] = previous
    if not hdr:
        frames['candidate'] = trace['selected']['.jpg']
    elif report['proposal']['action'] != 'finish':
        frames[role+'_prefix'] = trace['selected']['.jpg']
    else:
        role = 'branches_only'
    if 'selected-full' in trace:
        frames['full_control'] = trace['selected-full']['.jpg']
    plan, goals, plan_binding = plan_for(session, payload, report['request']['strategy'])
    return plan, goals, plan_binding, frames, trace['local_panel'], role


def run_frames(session, payload, run):
    try:
        return _run_frames(session, payload, run)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        base.fail('Malformed formal artifact review target: '+type(exc).__name__)


def _run_frames(session, payload, run):
    view = read(base.bound_file(session, run['stretch_visibility']))
    frames = {'reference': view['reference']['preview'], 'candidate': view['candidate_view']['preview']}
    declaration = None
    if run.get('adaptive_parameters'):
        params = read(base.bound_file(session, run['adaptive_parameters']['parameters']))
        declaration = params['request']['strategy']
        trace = params['steps'][-1]
        if 'selected-full' in trace:
            frames['full_control'] = trace['selected-full']['.jpg']
    elif run.get('hdr_transfer'):
        hdr = run['hdr_transfer']
        declaration = hdr['strategy']
        for role in ('gentle', 'faint', 'mask'):
            frames[role] = base.record(session, session/hdr['previews'][role])
        if hdr.get('full_control'):
            frames['full_control'] = base.record(session, session/hdr['full_control']['preview'])
    elif run.get('dualband_transfer'):
        params = read(base.bound_file(session, run['dualband_transfer']['parameters']))
        declaration = params['request']['strategy']
    if declaration is None:
        base.fail('Artifact focus review requires a bound Stage 7 strategy')
    plan, goals, plan_binding = plan_for(session, payload, declaration)
    return plan, goals, plan_binding, frames, view['panel'], 'mixed' if run.get('hdr_transfer') else 'final'


def panel_bytes(session, value, frames, context_panel):
    images = []
    for label, binding in frames.items():
        path = base.bound_file(session, binding, ('reports', 'artifacts', 'previews'))
        with Image.open(path) as image:
            frame = image.convert('RGB')
        if frame.size != (value['geometry']['width'], value['geometry']['height']):
            base.fail('Artifact comparison previews must preserve the original geometry')
        images.append((label, frame))
    if not images:
        base.fail('Artifact comparisons require existing native previews')
    overview = images[0][1].copy()
    draw = ImageDraw.Draw(overview)
    for area in value['areas']:
        x0, y0, x1, y1 = area['rect']
        draw.rectangle((x0, y0, x1-1, y1-1), outline='#ffb65c', width=1)
        draw.text((x0, max(0, y0-12)), area['id'], fill='#ffb65c')
    overview.thumbnail((640, 480))
    original = None
    if context_panel:
        with Image.open(base.bound_file(session, context_panel)) as image:
            original = image.convert('RGB')
    cell_width = max([320, *[area['rect'][2]-area['rect'][0]+16 for area in value['areas']]])
    widths = [640, cell_width*len(images), original.width if original else 0]
    height = overview.height+52+(original.height+16 if original else 0)
    height += sum(area['rect'][3]-area['rect'][1]+48 for area in value['areas'])
    canvas = Image.new('RGB', (max(widths), height), '#161616')
    draw = ImageDraw.Draw(canvas)
    y = 0
    if original:
        canvas.paste(original, (0, 0)); y = original.height+16
    draw.text((8, y), 'Artifact context / native 1:1 crops; no local stretch', fill='white')
    canvas.paste(overview, (8, y+22)); y += overview.height+52
    for area in value['areas']:
        for column, (label, image) in enumerate(images):
            draw.text((column*cell_width+8, y), area['id']+' / '+label, fill='white')
            canvas.paste(image.crop(tuple(area['rect'])), (column*cell_width+8, y+24))
        y += area['rect'][3]-area['rect'][1]+48
    output = io.BytesIO()
    profile = next((image.info.get('icc_profile') for _, image in images if image.info.get('icc_profile')), None)
    canvas.save(output, format='PNG', icc_profile=profile)
    return output.getvalue()


def material_key(target, focus):
    return hashlib.sha256((target['sha256']+focus['sha256']).encode()).hexdigest()


def material_marker(session, plan, target, focus):
    return session/'reports'/plan['run_id']/('artifact-focus-material-'+material_key(target, focus)+'.json')


def verify_report(session, payload, binding, *, target=None, frames=None, context_panel=None, require_sources=True):
    try:
        path = base.bound_file(session, binding, ('artifacts',))
        report = read(path)
        fields = {'schema', 'run_id', 'source', 'focus', 'target', 'strategy', 'phase', 'frames', 'context_panel', 'panel', 'implementation', 'scientific_parent'}
        if (not isinstance(report, dict) or set(report) != fields or report['schema'] != REPORT
            or base.RUN_ID_PATTERN.fullmatch(str(report['run_id'])) is None
            or path != session/DIRECTORY/report['run_id']/'artifact-focus.json'
            or report['scientific_parent'] is not False or report['implementation'] != helper(session)):
            base.fail('Invalid artifact focus material identity or implementation')
        if target is not None and report['target'] != target:
            base.fail('Artifact conclusions must bind the actual reviewed prefix or run')
        if frames is not None and (report['frames'] != frames or report['context_panel'] != context_panel):
            base.fail('Artifact comparison frames differ from the actual native prefix or run')
        panel = base.bound_file(session, report['panel'], ('artifacts',))
        if require_sources:
            base.bound_file(session, report['target'], ('reports', 'runs', 'artifacts')) if report['target']['path'] != '@input' else base.source_file(session, payload, report['source'])
            value = definition(session, payload, report['focus'], report['source'])
            if panel.read_bytes() != panel_bytes(session, value, report['frames'], report['context_panel']):
                base.fail('Artifact panel differs from its bound native images and rectangles')
        return report
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        base.fail('Malformed artifact focus materials: '+type(exc).__name__)


def has_submitted_review(session, plan_binding):
    for path in session.glob('reviews/*.json'):
        review = load_json(path)
        block = review.get('stretch_assessment', {}).get('artifacts', {})
        if not block.get('focus'):
            continue
        receipt_path = session/'runs'/(str(review.get('run_id'))+'.json')
        if not receipt_path.exists() or review.get('run_receipt_sha256') != sha256_file(receipt_path):
            continue
        receipt = load_json(receipt_path)
        if receipt.get('protocol') != 'stretch' or not receipt.get('stretch_visibility'):
            continue
        _, _, strategy, _, _, _ = run_frames(session, load_json(session/'session.json'), receipt)
        if strategy != plan_binding:
            continue
        from deep_sky_siril_core import _validate_review
        try:
            _validate_review(session, str(path), review['run_id'])
        except base.ContractError:
            continue
        return True
    return False


def prepare(session_value, focus_value, run_id, *, source=None, parameters=None, run=None):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload) or not enabled(payload):
        base.fail('New artifact materials require the current review policy; historical sessions are replay/finalize-only')
    if sum(item is not None for item in (source, parameters, run)) != 1 or base.RUN_ID_PATTERN.fullmatch(str(run_id)) is None:
        base.fail('Select one artifact target: source, parameters or formal run, and a new material run ID')
    focus_path = base.session_path(session, focus_value, must_exist=True, allowed_roots=('reports',))
    focus = base.record(session, focus_path)
    plan = goals = plan_binding = context = None
    if parameters is not None:
        path = base.session_path(session, parameters, must_exist=True, allowed_roots=('reports',))
        report = read(path)
        if report.get('schema') == 'starun-siril.stretch-parameters.v3':
            from deep_sky_siril_stretch_stepwise import load_report
            report, path, _ = load_report(session, payload, path)
        else:
            report, path, _ = base.load_parameters(session, payload, path)
        plan, goals, plan_binding, frames, context, phase = parameter_frames(session, payload, report, path)
        origin, target = plan['source'], base.record(session, path)
    elif run is not None:
        from deep_sky_siril_core import _load_run
        path, receipt = _load_run(session, run)
        if receipt['protocol'] != 'stretch':
            base.fail('Artifact review run targets must be Stage 7 stretch receipts')
        plan, goals, plan_binding, frames, context, phase = run_frames(session, payload, receipt)
        origin, target = plan['source'], base.record(session, path)
    else:
        source_path = Path(payload['input']['path']) if str(source) == '@input' else base.session_path(session, source, must_exist=True, allowed_roots=('artifacts',))
        origin = {'path': '@input' if str(source) == '@input' else source_path.relative_to(session).as_posix(), 'sha256': sha256_file(source_path)}
        base.accepted_parent(session, payload, source_path)
        value = definition(session, payload, focus, origin)
        frames, target, phase = {'linear_reference': value['evidence'][0]}, origin, 'initial'
    value = definition(session, payload, focus, origin)
    frames = {'linear_reference': value['evidence'][0], **frames}
    output = base.session_path(session, DIRECTORY+'/'+run_id, allowed_roots=('artifacts',))
    if output.exists():
        base.fail('Artifact review materials already exist; use a new run ID')

    def create():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.mkdir()
        panel = output/'artifact-focus.png'
        panel.write_bytes(panel_bytes(session, value, frames, context))
        packet = {'schema': REPORT, 'run_id': run_id, 'source': origin, 'focus': focus, 'target': target,
            'strategy': plan_binding, 'phase': phase, 'frames': frames, 'context_panel': context,
            'panel': base.record(session, panel), 'implementation': helper(session), 'scientific_parent': False}
        path = output/'artifact-focus.json'
        write(path, packet)
        return base.record(session, path)

    if plan:
        with locked(session, plan):
            current = head(session, payload, plan, goals)
            if focus != current:
                if value['previous'] != current:
                    base.fail('Append to the latest registered artifact focus; merge concurrent additions without dropping areas')
                if len(value['areas']) <= len(definition(session, payload, current, origin)['areas']):
                    base.fail('A focus update must append actual review areas')
                if has_submitted_review(session, plan_binding):
                    base.fail('A submitted formal review freezes this focus history; preserve its evidence')
            binding = create()
            if focus != current:
                write(session/'reports'/plan['run_id']/('artifact-focus-next-'+current['sha256']+'.json'), {'previous': current, 'focus': focus})
            marker = material_marker(session, plan, target, focus)
            if not marker.exists():
                write(marker, binding)
    else:
        binding = create()
    return {'status': 'prepared', 'report': binding, 'panel': str(output/'artifact-focus.png'),
        'focus': focus, 'target': target, 'scientific_parent': False, 'scientific_attempts_added': 0}


def skeleton(session, payload, plan, goals, target):
    focus = head(session, payload, plan, goals)
    value = definition(session, payload, focus, plan['source'])
    marker = material_marker(session, plan, target, focus)
    binding = read(marker) if marker.exists() else None
    return {'focus': focus, 'report': binding,
        'areas': [{'id': area['id'], 'risks': area['risks'], 'baseline': None, 'change': None,
                   'observation': '', 'evidence': [], 'decision_reason': ''} for area in value['areas']],
        'none_reason': value['none_reason']}


def validate_assessment(session, payload, block, plan, goals, target, frames, context, *, live=True):
    if not isinstance(block, dict) or set(block) != {'focus', 'report', 'areas', 'none_reason'}:
        base.fail('Artifact assessment requires prepared native comparison materials and every registered area')
    value = definition(session, payload, block['focus'], plan['source'])
    if live and block['focus'] != head(session, payload, plan, goals):
        base.fail('Artifact assessment omitted newly registered focus areas')
    ancestor, found = block['focus'], False
    while ancestor:
        if ancestor == goals['artifact_focus']:
            found = True
            break
        ancestor = definition(session, payload, ancestor, plan['source'])['previous']
    if not found:
        base.fail('Artifact assessment cannot replace the strategy-bound focus history')
    if block['none_reason'] != value['none_reason'] or not isinstance(block['areas'], list):
        base.fail('Artifact assessment must preserve its actual no-area declaration')
    if not value['areas'] and block['report'] is None and block['areas'] == []:
        base.bound_file(session, context)
        for image in frames.values():
            base.bound_file(session, image, ('reports', 'previews', 'artifacts'))
        return {'panel': context, 'source': plan['source'], 'focus': block['focus']}
    if not block['report']:
        base.fail('Artifact focus areas require prepared same-target image comparisons')
    expected_frames = {'linear_reference': value['evidence'][0], **frames}
    report = verify_report(session, payload, block['report'], target=target, frames=expected_frames, context_panel=context)
    if (report['source'] != plan['source'] or report['focus'] != block['focus']
        or report['strategy'] != base.record(session, session/'reports'/plan['run_id']/'strategy.json')):
        base.fail('Artifact assessment source, strategy or focus version changed')
    expected = {area['id']: area for area in value['areas']}
    seen = set()
    allowed = [report['panel'], *report['frames'].values(), *([context] if context else [])]
    for row in block['areas']:
        if (not isinstance(row, dict) or set(row) != {'id', 'risks', 'baseline', 'change', 'observation', 'evidence', 'decision_reason'}
            or row['id'] not in expected or row['id'] in seen or row['risks'] != expected[row['id']]['risks']
            or row['baseline'] not in {'present', 'absent', 'uncertain'} or row['change'] not in CHANGES
            or not text(row['observation']) or not text(row['decision_reason'])
            or not isinstance(row['evidence'], list) or report['panel'] not in row['evidence']):
            base.fail('Record each artifact area, baseline presence, change, observation, image evidence and decision reason')
        for item in row['evidence']:
            if item not in allowed:
                base.fail('Artifact evidence must be an actual same-target comparison frame or panel')
            base.bound_file(session, item, ('reports', 'previews', 'artifacts'))
        seen.add(row['id'])
    if set(expected) != seen:
        base.fail('Artifact assessment omitted registered focus areas')
    return report


def feedback(session, payload, request, previous, *, live=False):
    if not enabled(payload):
        return
    plan, goals, _ = plan_for(session, payload, request['strategy'])
    if request['previous'] is None:
        return
    _, _, _, frames, context, _ = parameter_frames(session, payload, previous, base.bound_file(session, request['previous']))
    validate_assessment(session, payload, request['feedback'].get('artifacts'), plan, goals,
        request['previous'], frames, context, live=live)
    before = previous.get('proposal', {}).get('feedback')
    prior = before['artifacts']['focus'] if before else goals['artifact_focus']
    current = request['feedback']['artifacts']['focus']
    while current != prior:
        current = definition(session, payload, current, plan['source'])['previous']
        if current is None:
            base.fail('Continuation cannot drop focus areas from an earlier prefix or HDR branch')


def formal_skeleton(session, payload, run, target, materials):
    plan, goals, _, frames, context, _ = run_frames(session, payload, run)
    block = skeleton(session, payload, plan, goals, target)
    if block['report']:
        value = definition(session, payload, block['focus'], plan['source'])
        report = verify_report(session, payload, block['report'], target=target,
            frames={'linear_reference': value['evidence'][0], **frames}, context_panel=context)
        materials = [item for item in materials if item['path'] != context['path']]
        materials.append({key: report['panel'][key] for key in ('path', 'sha256')})
    return block, materials


def formal_validate(session, payload, run, review):
    plan, goals, _, frames, context, _ = run_frames(session, payload, run)
    target = base.record(session, session/'runs'/(run['id']+'.json'))
    block = review.get('stretch_assessment', {}).get('artifacts')
    report = validate_assessment(session, payload, block, plan, goals, target, frames, context)
    seen = {item['path'] for item in review['inspected_materials']}
    if report['panel']['path'] not in seen:
        base.fail('Inspect the artifact comparison panel before completing Stage 7 review')
    return context['path']


def pending_review(session, payload, plan, goals, target):
    block = skeleton(session, payload, plan, goals, target)
    return {'focus': block['focus'], 'areas': [row['id'] for row in block['areas']],
        'pending_areas': [row['id'] for row in block['areas']],
        'materials_prepared': bool(block['report']) or not block['areas'],
        'none_reason': block['none_reason'], 'scientific_attempts_added': 0}


def retained_paths(session, payload, *, require_sources=True):
    if not enabled(payload):
        return set()
    paths = set()
    for path in sorted((session/DIRECTORY).glob('*/artifact-focus.json')):
        binding = base.record(session, path)
        report = verify_report(session, payload, binding, require_sources=require_sources)
        paths.update((binding['path'], report['panel']['path']))
        focus = report['focus']
        while focus:
            path = base.bound_file(session, focus)
            paths.add(focus['path'])
            focus = read(path)['previous']
    return paths

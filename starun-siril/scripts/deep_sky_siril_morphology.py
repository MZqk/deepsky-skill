"""Bind human morphology observations to an accepted parent and every solving ROI."""
from pathlib import Path
import json

from PIL import Image, ImageDraw

import deep_sky_siril_stretch as base
from deep_sky_siril_contract import fingerprint

POLICY = 'roi_declared_v1'
SCHEMA = 'starun-siril.morphology-context.v1'
GOALS = {
    'emission_structure': ('emission_nebula', {'diffuse_nebulosity', 'filament'}),
    'core_envelope_balance': ('high_dynamic_range', {'bright_core', 'faint_envelope'}),
    'galaxy_structure': ('galaxy_halo', {'galaxy_disk', 'spiral_arm', 'galaxy_halo', 'galaxy_dust_lane'}),
    'dust_reflection_structure': ('dust_reflection', {'reflection_nebulosity', 'dust_nebulosity', 'dark_lane'}),
    'stellar_structure': ('star_cluster', {'cluster_center', 'cluster_members', 'cluster_outskirts'}),
}
STELLAR = {'field_stars', 'stellar_residual', 'cluster_center', 'cluster_members', 'cluster_outskirts'}
FEATURES = set().union(*(v[1] for v in GOALS.values()), STELLAR, {'background', 'unresolved'})
RELATIONS = {'primary', 'companion', 'background', 'field_star'}


def enabled(payload):
    return payload.get('execution_policy', {}).get('stretch_morphology') == POLICY


def helper(session=None):
    from deep_sky_siril_stretch_v2 import implementation
    return implementation(session)['deep_sky_siril_morphology.py']


def owns_source(receipt,source):
    return ((receipt.get('protocol')=='input.inspect' and receipt.get('source_path')==source['path']
             and receipt.get('source',{}).get('sha256')==source['sha256']) or
            receipt.get('primary_output')==source['path'] and any(
                row.get('path')==source['path'] and row.get('sha256')==source['sha256'] for row in receipt.get('outputs',[])))


def parent_bindings(session,source):
    matches=[]
    for entry in base.load_json(session/'manifest.json')['runs']:
        path=base.session_path(session,'runs/'+entry['receipt'],must_exist=True,allowed_roots=('runs',))
        receipt=base.load_json(path)
        if receipt.get('status')=='success' and owns_source(receipt,source):
            if entry.get('status')!='success' or entry['receipt_sha256']!=base.sha256_file(path):
                base.fail('Morphology parent receipt changed')
            reviews=[p for p in (session/'reviews').glob('*.json') if base.load_json(p).get('run_id')==receipt['id']]
            if len(reviews)!=1 or base.load_json(reviews[0]).get('verdict')!='accept':
                base.fail('Morphology context requires the actual accepted parent review')
            matches.append({'receipt':base.record(session,path),'review':base.record(session,reviews[0])})
    if len(matches)!=1:
        base.fail('Morphology context requires one actual parent receipt')
    return matches[0]


def prepare_context(session_value, source_value, regions_value, preview_value, run_id):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    from deep_sky_siril_stretch_profiles import source_previews
    from deep_sky_siril_metrics import measure_regions
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload) or not enabled(payload):
        base.fail('Morphology context preparation requires a current session')
    if base.RUN_ID_PATTERN.fullmatch(str(run_id)) is None:
        base.fail('Invalid morphology context run ID')
    source = Path(payload['input']['path']) if str(source_value) == '@input' else base.session_path(
        session, source_value, must_exist=True, allowed_roots=('artifacts',))
    source_binding = {'path': '@input' if str(source_value) == '@input' else source.relative_to(session).as_posix(),
                      **fingerprint(source, include_path=False)}
    base.accepted_parent(session, payload, base.source_file(session, payload, {k:source_binding[k] for k in ('path','sha256')}))
    parent=parent_bindings(session,source_binding)
    regions_path = base.session_path(session, regions_value, must_exist=True, allowed_roots=('reports',))
    if measure_regions(source, None, regions_path)['status'] != 'measured':
        base.fail('Morphology ROIs require finite current-parent scientific geometry')
    preview = base.session_path(session, preview_value, must_exist=True, allowed_roots=('previews','artifacts'))
    preview_binding = base.record(session, preview)
    if preview_binding not in source_previews(session, payload, {k:source_binding[k] for k in ('path','sha256')}):
        base.fail('Morphology preview must be a committed native view of this accepted parent')
    regions = base.load_json(regions_path)
    expected_size = (regions['reference']['width'], regions['reference']['height'])
    with Image.open(preview) as original:
        if original.size != expected_size:
            base.fail('Morphology preview dimensions differ from scientific ROI geometry')
        image, icc = original.convert('RGB'), original.info.get('icc_profile')
    draw = ImageDraw.Draw(image)
    colours = {'background':'#80bfff', 'faint':'#66d9c3', 'core':'#f6b26b', 'stars':'#f080b0'}
    for row in regions['regions']:
        x0,y0,x1,y1 = row['rect']
        draw.rectangle((x0,y0,x1-1,y1-1), outline=colours[row['role']])
        draw.text((x0,max(0,y0-12)), row['id']+'/'+row['role'], fill=colours[row['role']])
    directory = base.session_path(session, 'reports/'+run_id, allowed_roots=('reports',))
    if directory.exists():
        base.fail('Morphology context already exists; use a new run ID')
    directory.mkdir()
    panel = directory/'roi-context.png'
    with panel.open('xb') as stream:
        image.save(stream, format='PNG', icc_profile=icc)
    context = {'schema':SCHEMA, 'source':source_binding, 'parent':parent, 'preview':preview_binding,
        'regions':base.record(session,regions_path), 'panel':base.record(session,panel),
        'geometry':{'width':expected_size[0], 'height':expected_size[1], 'coordinates':'preview_top_left'},
        'implementation':helper(), 'scientific_parent':False}
    path = directory/'morphology-context.json'
    with path.open('x') as stream:
        json.dump(context,stream,indent=2,allow_nan=False);stream.write('\n')
    return {'status':'prepared', 'context':base.record(session,path), 'panel':context['panel'],
            'requires_visual_review':True}


def verify_context(session, payload, binding, *, source=None, regions=None, require_sources=True):
    from deep_sky_siril_stretch_profiles import source_previews
    path = base.bound_file(session,binding)
    value = base.load_json(path)
    if (not isinstance(value,dict) or set(value) != {'schema','source','parent','preview','regions','panel','geometry','implementation','scientific_parent'}
        or value['schema'] != SCHEMA or value['implementation'] != helper(session)
        or value['scientific_parent'] is not False or path.name != 'morphology-context.json'
        or value['panel']['path'] != (path.parent/'roi-context.png').relative_to(session).as_posix()):
        base.fail('Malformed morphology context or implementation binding')
    if source is not None and any(value['source'].get(k) != source.get(k) for k in ('path','sha256')):
        base.fail('Morphology context belongs to another scientific parent')
    if regions is not None and value['regions'] != regions:
        base.fail('Morphology context belongs to another solving ROI selection')
    region_data = base.load_json(base.bound_file(session,value['regions']))
    geometry = {'width':region_data['reference']['width'],'height':region_data['reference']['height'],'coordinates':'preview_top_left'}
    if value['geometry'] != geometry or region_data['reference']['sha256'] != value['source']['sha256']:
        base.fail('Morphology context geometry or source fingerprint changed')
    preview = base.bound_file(session,value['preview'],('previews','artifacts'))
    panel = base.bound_file(session,value['panel'])
    for image_path in (preview,panel):
        with Image.open(image_path) as image:
            if image.size != (geometry['width'],geometry['height']):
                base.fail('Morphology evidence dimensions changed')
    # Verify only the pinned ancestor; walking all successful descendants would recurse through this stretch.
    receipt=base.load_json(base.bound_file(session,value['parent']['receipt'],('runs',)))
    review=base.load_json(base.bound_file(session,value['parent']['review'],('reviews',)))
    if receipt.get('status')!='success' or not owns_source(receipt,value['source']) or review.get('verdict')!='accept' or review.get('run_id')!=receipt['id']:
        base.fail('Morphology accepted-parent evidence changed')
    if require_sources:
        base.source_file(session,payload,{k:value['source'][k] for k in ('path','sha256')})
        if value['preview'] not in source_previews(session,payload,{k:value['source'][k] for k in ('path','sha256')}):
            base.fail('Morphology preview no longer belongs to its accepted parent')
    return value


def assess(session, payload, plan, goals):
    declaration = goals.get('morphology')
    issues = []
    def issue(code, **detail):
        issues.append({'code':code, **detail})
    if (not isinstance(declaration,dict) or set(declaration) != {'context','primary_subject','processing_goal','regions'}
        or not isinstance(declaration.get('primary_subject'),str) or not declaration['primary_subject'].strip()
        or not isinstance(declaration.get('processing_goal'),str) or declaration['processing_goal'] not in GOALS
        or not isinstance(declaration.get('regions'),list)):
        return {'declaration':declaration, 'issues':[{'code':'invalid_morphology_declaration'}]}
    try:
        verify_context(session,payload,declaration['context'],source=plan['source'],regions=plan['regions'])
    except (base.ContractError,OSError,ValueError,KeyError,TypeError) as exc:
        issue('invalid_morphology_context',reason=str(exc))
    profile, required = GOALS[declaration['processing_goal']]
    if profile != goals['profile']:
        issue('morphology_profile_mismatch',processing_goal=declaration['processing_goal'],expected_profile=profile,profile=goals['profile'])
    actual = {r['id']:r for r in base.load_json(base.bound_file(session,plan['regions']))['regions']}
    seen, primary = set(), {}
    for row in declaration['regions']:
        if (not isinstance(row,dict) or set(row) != {'region','relation','features','observation'}
            or not isinstance(row.get('region'),str) or not isinstance(row.get('relation'),str) or row['relation'] not in RELATIONS
            or not isinstance(row.get('features'),list) or not row['features']
            or any(not isinstance(f,str) or f not in FEATURES for f in row['features'])
            or len(set(row['features'])) != len(row['features'])
            or not isinstance(row.get('observation'),str) or not row['observation'].strip()):
            issue('invalid_morphology_roi');continue
        region = row['region']
        if region in seen:
            issue('duplicate_morphology_roi',region=region);continue
        seen.add(region)
        if region not in actual:
            issue('unknown_morphology_roi',region=region);continue
        role, features, relation = actual[region]['role'],set(row['features']),row['relation']
        valid = True
        if ((role=='background') != (relation=='background') or (role=='background') != (features=={'background'})
            or 'background' in features and role!='background' or relation=='field_star' and role!='stars'
            or 'bright_core' in features and role!='core' or 'faint_envelope' in features and role!='faint'
            or role=='stars' and not features <= STELLAR|{'unresolved'}
            or features & {'field_stars','stellar_residual'} and role!='stars'
            or 'unresolved' in features and len(features)!=1):
            issue('morphology_role_conflict',region=region,role=role);valid=False
        if valid and relation=='primary':
            primary[region] = features
    for region in actual.keys()-seen:
        issue('missing_morphology_roi',region=region)
    if profile=='high_dynamic_range':
        supported = any('bright_core' in features for features in primary.values()) and any('faint_envelope' in features for features in primary.values())
    else:
        supported = any(features & required for features in primary.values())
    if not supported:
        issue('missing_primary_morphology_structure',profile=profile,required_features=sorted(required))
    return {'declaration':declaration,'supporting_primary_rois':sorted(r for r,f in primary.items() if f & required),'issues':issues}


def retained_paths(session,payload,*,require_sources=True):
    if not enabled(payload):
        return set()
    paths = set()
    for path in sorted((session/'reports').glob('*/morphology-context.json')):
        context = verify_context(session,payload,base.record(session,path),require_sources=require_sources)
        paths.update([path.relative_to(session).as_posix(),context['panel']['path'],context['preview']['path']])
    return paths

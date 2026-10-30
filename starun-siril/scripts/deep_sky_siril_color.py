#!/usr/bin/env python3
"""Evaluate one explicit native saturation curve before choosing protection."""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone

import numpy as np

from deep_sky_siril_contract import (
    ContractError, RUN_ID_PATTERN, atomic_write_json, fingerprint, fingerprint_matches, load_json,
    session_path, sha256_file,
)
from deep_sky_siril_artifacts import read_fits_pixels, diagnose_siril_log
from deep_sky_siril_stretch import record, bound_file, pixels
from siril_manual_bundle import strict_json_bytes, BundleError

POLICY = 'masked_saturation_ghs_v1'
EVALUATION = 'unmasked_first_v1'
UNMASKED_POLICY = 'unmasked_saturation_ghs_v1'
STRENGTHS = (.15, .3, .6, .9, 1.2)
MASK_KEYS = ('low_start', 'low_end', 'high_start', 'high_end', 'sigma')
PRESERVATION = 'vivid_preservation_v1'
SATURATION_KEYS = ('saturation_protect_start', 'saturation_protect_end')
CURVE_VARIANTS = 'inverse_saturation_v1'
CURVE_KEYS = ('curve', 'SP', 'HP')
REGIONAL_POLICY = 'explicit_targets_v1'
REGIONAL_SCHEMA = 'starun-siril.color-region-goals.v1'
STRICT_MASK_HELPER = {'sha256': 'feb89cec2288f30d6acebe985a5a8c0ab58add6a25f2e227ce1057100d2b1be3', 'size': 31806}
PRE_CONFIG_HELPER = {'sha256': '376cb00432e175424a9fa5a75e5b9b75e637581fb2151d503f9df09dd685db55', 'size': 52105}
TRIAL_SETTINGS = {
    'core': {'force_16bit': False, 'check_updates': False, 'fits_save_icc': True},
    'gui': {'icc_autoassignment': 0, 'use_scripts_repository': False, 'use_spcc_repository': False},
}


def helper_fingerprint(session):
    policy = load_json(Path(session)/'session.json').get('execution_policy', {})
    from deep_sky_siril_stretch_v2 import _historical_bindings
    historical = _historical_bindings(Path(session))
    if historical and 'deep_sky_siril_color.py' in historical['implementation']:
        return historical['implementation']['deep_sky_siril_color.py']
    from deep_sky_siril_session import _PRE_VIVID_BINDINGS, _PRE_CHROMA_BINDINGS, _PRE_STAR_COLOR_BINDINGS, _PRE_REGION_COLOR_BINDINGS
    from deep_sky_siril_session import _PRE_SUBJECT_MASK_BINDINGS
    if policy == _PRE_SUBJECT_MASK_BINDINGS['execution_policy']:
        return _PRE_SUBJECT_MASK_BINDINGS['helpers']['deep_sky_siril_color.py']
    if policy == _PRE_REGION_COLOR_BINDINGS['execution_policy']:
        return _PRE_REGION_COLOR_BINDINGS['helpers']['deep_sky_siril_color.py']
    if policy == _PRE_STAR_COLOR_BINDINGS['execution_policy']:
        return _PRE_STAR_COLOR_BINDINGS['helpers']['deep_sky_siril_color.py']
    if policy == _PRE_CHROMA_BINDINGS['execution_policy']:
        return _PRE_CHROMA_BINDINGS['helpers']['deep_sky_siril_color.py']
    if policy == _PRE_VIVID_BINDINGS['execution_policy']:
        return _PRE_VIVID_BINDINGS['helpers']['deep_sky_siril_color.py']
    if policy.get('subject_enhancement') != 'benefit_first_v1':
        return STRICT_MASK_HELPER
    if policy.get('color_evaluation') != EVALUATION:
        from deep_sky_siril_session import _PRE_COLOR_HELPER
        return _PRE_COLOR_HELPER
    return fingerprint(Path(__file__).resolve(), include_path=False)


def helper_matches(session, stored):
    if stored == helper_fingerprint(session):
        return True
    from deep_sky_siril_session import known_execution_policy
    policy = load_json(Path(session)/'session.json').get('execution_policy', {})
    return (stored == PRE_CONFIG_HELPER and policy.get('color_preservation') is None and policy.get('color_evaluation') == EVALUATION
            and known_execution_policy(policy))


def configuration_binding(session, path):
    path = session_path(session, path, must_exist=True, allowed_roots=('reports',))
    try:
        raw = path.read_bytes()
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(raw.decode('utf-8'))
        # Required settings must be declared in their own section, never inherited.
        parser.defaults().clear()
        for section, settings in TRIAL_SETTINGS.items():
            for key, expected in settings.items():
                read = parser.getboolean if type(expected) is bool else parser.getint
                if read(section, key) != expected:
                    fail(f'Color trial configuration changed: {section}.{key} must be {expected!r}')
    except (OSError, UnicodeError, configparser.Error, ValueError) as exc:
        fail('Invalid color trial configuration: '+str(exc))
    return {'path': path.relative_to(session).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}


def selector_expression(r, g, b, selector, *, preservation=False):
    if selector == 'all':
        return '1'
    if selector not in ('cyan', 'red'):
        fail('Unknown display color selector')
    numerator, denominator = ((f'{g} + {b} - 2 * {r}', f'{g} + {b} + 2 * {r}')
                              if selector == 'cyan' else (f'{r} - {g} - {b}', f'{r} + {g} + {b}'))
    if selector == 'red' and preservation:
        numerator = f'{r} - max({g}, {b})'
    t = f'min(1, max(0, ({numerator}) / max(({denominator}), 0.000001)))'
    return f'({t}^2 * (3 - 2 * {t}))'


def selector_weight(values, selector, *, preservation=False):
    if selector == 'all':
        return np.ones_like(values[0])
    r, g, b = values
    if selector == 'cyan':
        numerator, denominator = g+b-2*r, g+b+2*r
    elif selector == 'red':
        numerator, denominator = (r-np.maximum(g, b) if preservation else r-g-b), r+g+b
    else:
        fail('Unknown display color selector')
    t = np.clip(numerator/np.maximum(denominator, 1e-6), 0, 1)
    return t*t*(3-2*t)


def fail(message):
    raise ContractError('color_parameters_invalid', message)


def regional_domain(region, rectangles):
    x0,y0,x1,y1 = region['rect']
    mask = np.ones((y1-y0,x1-x0),dtype=bool)
    for u0,v0,u1,v1 in rectangles:
        left,top,right,bottom = max(x0,u0),max(y0,v0),min(x1,u1),min(y1,v1)
        if left < right and top < bottom:
            mask[top-y0:bottom-y0,left-x0:right-x0] = False
    return mask


def load_region_goals(session, source, regions_binding, value):
    if load_json(session/'session.json')['execution_policy'].get('color_region_goals') != REGIONAL_POLICY:
        fail('Regional color goals require their frozen execution policy')
    if not isinstance(value,(dict,str,Path)):
        fail('Regional goals require a file path or exact binding')
    path = (bound_file(session,value) if isinstance(value,dict) else
            session_path(session,value,must_exist=True,allowed_roots=('reports',)))
    path = session_path(session,path,must_exist=True,allowed_roots=('reports',))
    if isinstance(value,dict) and record(session,path) != value:
        fail('Regional goal binding changed')
    try:
        document = strict_json_bytes(path.read_bytes(),document='regional color goals')
    except BundleError as exc:
        fail(str(exc))
    if (not isinstance(document,dict) or set(document) != {'schema','regions','goals'}
        or document['schema'] != REGIONAL_SCHEMA or document['regions'] != regions_binding
        or not isinstance(document['goals'],list)):
        fail('Regional goals require the exact bound regions and an explicit goal for every ROI')
    region_path = bound_file(session,regions_binding)
    from deep_sky_siril_metrics import measure_regions
    if measure_regions(source,None,region_path)['status'] != 'measured':
        fail('Regional color goals source or geometry changed')
    rows = load_json(region_path)['regions']
    by_id = {r['id']:r for r in rows}
    goals = {}
    for goal in document['goals']:
        if not isinstance(goal,dict) or not isinstance(goal.get('region'),str) or goal.get('region') not in by_id or goal['region'] in goals:
            fail('Unknown or duplicated regional color goal')
        intent = goal.get('intent')
        if intent == 'enhance':
            gain = goal.get('target_gain')
            if (set(goal) != {'region','intent','target_gain'} or by_id[goal['region']]['role'] != 'faint'
                or type(gain) not in (int,float) or not math.isfinite(gain) or gain <= 0):
                fail('Only faint enhance goals permit a finite positive relative target gain')
        elif intent != 'preserve' or set(goal) != {'region','intent'}:
            fail('Preserve goals must not declare a growth target')
        goals[goal['region']] = goal
    if set(goals) != set(by_id):
        fail('Regional color goals must cover every existing ROI exactly once')
    rectangles = [r['rect'] for r in rows if goals[r['id']]['intent'] == 'preserve']
    if any(not regional_domain(r,rectangles).any() for r in rows if goals[r['id']]['intent'] == 'enhance'):
        fail('An enhance ROI is fully covered by protected regions')
    return document,record(session,path),rectangles


def measure_region_goals(source, candidate, regions, document, *, selector='all'):
    before,after = pixels(source),pixels(candidate)
    if before.shape != after.shape or len(before) != 3 or not all(np.isfinite(v).all() for v in (before,after)):
        fail('Regional color measurements require finite aligned RGB pixels')
    goals = {g['region']:g for g in document['goals']}
    rectangles = [r['rect'] for r in regions if goals[r['id']]['intent'] == 'preserve']
    rows = []
    height = before.shape[1]
    for region in regions:
        goal = goals[region['id']]
        x0,y0,x1,y1 = region['rect']
        crops = [v[:,height-y1:height-y0,x0:x1][:,::-1] for v in (before,after)]
        row = {**goal,'target_gain':goal.get('target_gain'),'achieved_gain':None,'target_reached':None}
        if goal['intent'] == 'preserve':
            row.update(pixels=(y1-y0)*(x1-x0),max_abs_change=float(np.max(np.abs(crops[1]-crops[0]))))
        else:
            mask = regional_domain(region,rectangles)
            a,b = [rgb_statistics(v[:,mask])[0] for v in crops]
            old,new = float(np.median(a)),float(np.median(b))
            gain = new/old-1 if old > .0001 else None
            row.update(pixels=int(mask.sum()),parent_saturation_median=old,candidate_saturation_median=new,
                       saturation_median_change=new-old,achieved_gain=gain,
                       target_reached=gain >= goal['target_gain'] if gain is not None else None,
                       selector_pixels=int(np.count_nonzero(selector_weight(crops[0][:,mask],selector))))
        rows.append(row)
    states = [r['target_reached'] for r in rows if r['intent'] == 'enhance']
    return rows,(False if False in states else True if states and all(v is True for v in states) else None)


def regional_fill_rows(parameters):
    return [['fill','0',str(x0),str(y0),str(x1-x0),str(y1-y0)]
            for x0,y0,x1,y1 in parameters.get('protected_rects',[])]


def curve_parameters(curve='ght', sp=None, hp=None):
    if type(curve) is not str or curve not in ('ght', 'invght'):
        fail('Use curve=ght or invght')
    if curve == 'ght':
        if sp is not None or hp is not None:
            fail('Forward color curves do not accept SP or HP')
        return {}
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (sp, hp))
        or not 0 <= sp <= hp <= 1):
        fail('Inverse color curves require explicit finite 0 <= SP <= HP <= 1')
    return {'curve': curve, 'SP': sp, 'HP': hp}


def curve_operator(strength, parameters=None):
    p = parameters or {}
    shape = curve_parameters(p.get('curve', 'ght'), p.get('SP'), p.get('HP'))
    return [p.get('curve', 'ght'), f'-D={strength}', '-B=0', '-LP=0',
            f'-SP={shape["SP"]}' if shape else '-SP=0', f'-HP={shape["HP"]}' if shape else '-HP=1',
            '-sat', '-clipmode=rgbblend']


def validate_curve_operator(tokens):
    if (len(tokens) != 8 or tokens[0] not in ('ght', 'invght') or not tokens[1].startswith('-D=')
        or tokens[2:4] != ['-B=0', '-LP=0'] or tokens[6:] != ['-sat', '-clipmode=rgbblend']
        or not tokens[4].startswith('-SP=') or not tokens[5].startswith('-HP=')
        or (tokens[0] == 'ght' and tokens[4:6] != ['-SP=0', '-HP=1'])):
        fail('Color curves require explicit fixed GHS parameters, saturation and rgbblend')
    try:
        strength = float(tokens[1][3:])
        shape = (curve_parameters('invght', float(tokens[4][4:]), float(tokens[5][4:]))
                 if tokens[0] == 'invght' else {})
    except ValueError:
        fail('Invalid color curve strength or shape')
    if not math.isfinite(strength) or not 0 < strength <= 10:
        fail('Color curve strength must be finite and within (0, 10]')
    return {'D': strength, **shape}


def validate_saturation_protection(parameters):
    start, end = (parameters.get(key) for key in SATURATION_KEYS)
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
        or not 0 <= start < end <= 1):
        fail('Explicit saturation protection requires 0 <= start < end <= 1')


def mask_expression(luminance, peak, channels, parameters):
    expression = '1'
    if parameters.get('mask_mode') != 'none':
        a, b, c, d = (parameters[k] for k in MASK_KEYS[:4])
        t = f'min(1, max(0, ({luminance} - {a}) / {b-a}))'
        u = f'min(1, max(0, ({peak} - {c}) / {d-c}))'
        expression = f'({t}^2 * (3 - 2 * {t})) * (1 - {u}^2 * (3 - 2 * {u}))'
    if parameters.get('subject_mask'):
        from deep_sky_siril_subject_mask import highlight
        c,d = highlight(parameters)
        u = f'min(1, max(0, ({peak} - {c}) / {d-c}))'
        expression += f' * (1 - {u}^2 * (3 - 2 * {u}))'
    if 'selector' in parameters:
        expression += ' * '+selector_expression(*channels, parameters['selector'],
                                                preservation=parameters.get('mask_policy') == PRESERVATION)
    if SATURATION_KEYS[0] in parameters:
        validate_saturation_protection(parameters)
        start, end = (parameters[k] for k in SATURATION_KEYS)
        r, g, b = channels
        denominator = f'iif({peak} > 0, {peak}, 1)'
        saturation = f'iif({peak} > 0, ({peak} - min({r}, min({g}, {b}))) / {denominator}, 0)'
        t = f'min(1, max(0, (({saturation}) - {start}) / {end-start}))'
        expression += f' * (1 - {t}^2 * (3 - 2 * {t}))'
    return expression


def mask_coverage(mask, regions):
    height = mask.shape[-2]
    result = []
    for row in regions:
        x0, y0, x1, y1 = row['rect']
        weights = mask[..., height-y1:height-y0, x0:x1]
        result.append({'id': row['id'], 'role': row['role'],
                       'nonzero_fraction': float(np.mean(weights > 0)),
                       'effective_coverage': float(np.mean(weights)),
                       'weight_quantiles': dict(zip(('p0', 'p10', 'p50', 'p90', 'p100'),
                                                    map(float, np.quantile(weights, [0, .1, .5, .9, 1]))))})
    return result


def curve_declaration(text):
    rows = [s.strip() for s in text.splitlines() if re.match(r'#\s*color-curve\b', s.strip())]
    if not rows:
        return None
    if len(rows) != 1 or not rows[0].startswith('# color-curve:'):
        fail('Exactly one color curve declaration is required')
    try:
        values = strict_json_bytes(rows[0].split(':', 1)[1].encode(), document='color curve declaration')
    except BundleError as exc:
        fail(str(exc))
    if isinstance(values, dict) and 'mask_mode' in values:
        mode = values['mask_mode']
        keys = {'D', 'parameters', 'mask_mode'} | (set(MASK_KEYS) if mode == 'luminance' else set())
        if any(key in values for key in CURVE_KEYS):
            keys.update(CURVE_KEYS)
            if values.get('curve') != 'invght':
                fail('Only inverse color declarations include curve, SP and HP')
            curve_parameters(values['curve'], values.get('SP'), values.get('HP'))
        if 'mask_policy' in values:
            if values['mask_policy'] != PRESERVATION:
                fail('Unknown color mask policy')
            keys |= {'mask_policy', *SATURATION_KEYS}
            validate_saturation_protection(values)
        if 'subject_mask' in values:
            from deep_sky_siril_subject_mask import highlight
            keys |= {'subject_mask','high_start','high_end'}
            if mode != 'none' or values.get('mask_policy') != PRESERVATION or 'selector' in values or not isinstance(values['subject_mask'],dict):
                fail('Spatial color requires protected mask-mode none and selector all')
            highlight(values)
        if 'region_goals' in values:
            keys |= {'region_goals','protected_rects'}
            if (values.get('mask_policy') != PRESERVATION or not isinstance(values['region_goals'],dict)
                or not isinstance(values.get('protected_rects'),list) or len(values['protected_rects']) > 64
                or any(not isinstance(r,list) or len(r) != 4 or any(type(v) is not int for v in r)
                       or not 0 <= r[0] < r[2] or not 0 <= r[1] < r[3] for r in values['protected_rects'])):
                fail('Regional color requires its bound goals and exact protected rectangles')
        if (mode not in ('none', 'luminance') or set(values) not in (keys, keys | {'selector'})
            or ('selector' in values and (mode != 'luminance' or values['selector'] != 'cyan'))
            or type(values.get('D')) not in (int, float) or not math.isfinite(values['D'])
            or not 0 < values['D'] <= 10 or not isinstance(values.get('parameters'), str)
            or not values['parameters'].strip()):
            fail('Color curves require an explicit valid D, mask mode and parameter report')
        if mode == 'none':
            return values
    elif not isinstance(values, dict) or set(values) not in ({'D', *MASK_KEYS, 'parameters'},
                                                          {'D', *MASK_KEYS, 'parameters', 'selector'}):
        fail('Color curve declaration requires D, five mask parameters and a parameter report')
    if 'selector' in values and (values['selector'] != 'cyan' or values['sigma'] != 2):
        fail('Selective color curves require selector=cyan and sigma=2')
    if any(type(values[k]) not in (int, float) or not math.isfinite(values[k]) for k in ('D', *MASK_KEYS)):
        fail('Color curve values must be finite JSON numbers')
    a, b, c, d = (values[k] for k in MASK_KEYS[:4])
    if not (0 <= a < b <= c < d <= 1 and .5 <= values['sigma'] <= 16):
        fail('Invalid color curve mask bounds')
    if (('mask_mode' not in values and values['D'] not in STRENGTHS)
        or not isinstance(values['parameters'], str)):
        fail('Invalid color curve strength or parameter report')
    return values


def script_text(root, source, run_id, parameters, *, report=None, previews=True, primary=None, session=None):
    """Render diagnostic trials; formal scripts remain Agent-authored and validated."""
    from deep_sky_siril_processing import _variable
    root, source = Path(root).resolve(), Path(source).resolve()
    if parameters.get('mask_mode') == 'none' and parameters.get('mask_policy') != PRESERVATION:
        primary = Path(primary) if primary is not None else root/'artifacts'/f'{run_id}-candidate.fit'
        views = {k: root/'previews'/f'{run_id}-{k}.jpg' for k in ('parent', 'candidate')}
        q = lambda p: json.dumps(str(p), ensure_ascii=False)
        rows = ['requires 1.4.4 1.5.0', 'set32bits']
        if report is not None:
            rows.append('# color-curve: '+json.dumps({**parameters, 'parameters': str(report)}, allow_nan=False))
        rows += ['load '+q(source), 'icc_assign sRGB']
        if previews:
            rows.append('savejpg '+q(views['parent'].with_suffix(''))+' 95')
        rows += [' '.join(curve_operator(parameters['D'], parameters)), 'save '+q(primary.with_suffix(''))+' -chksum']
        if previews:
            rows.append('savejpg '+q(views['candidate'].with_suffix(''))+' 95')
        rows.append('close')
        outputs = [primary, *views.values()] if previews else [primary]
        binding = {'policy': UNMASKED_POLICY, 'parameters': parameters, 'source_sha256': sha256_file(source),
                   'previews': {k: p.relative_to(root).as_posix() for k, p in views.items()}}
        return '\n'.join(rows)+'\n', primary, outputs, binding
    paths = {k: root/'artifacts'/f'{run_id}-{k}.fit' for k in
             ('r', 'g', 'b', 'luminance', 'peak', 'mask-base', 'mask-smoothed', 'mask', 'mask-rgb', 'enhanced', 'candidate')}
    tone = 'midtones' in parameters
    if primary is not None:
        paths['candidate'] = Path(primary)
    if tone:
        paths.update({k: root/'artifacts'/f'{run_id}-{k}.fit' for k in ('l-enhanced', 'gain', 'gain-rgb')})
    views = {k: root/'previews'/f'{run_id}-{k}.jpg' for k in ('parent', 'mask', 'candidate')}
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    v = lambda p: _variable(p, root)
    expression = mask_expression(v(paths['luminance']), v(paths['peak']),
                                 [v(paths[k]) for k in ('r', 'g', 'b')], parameters)
    final_mask = (f'min({v(paths["mask-smoothed"])}, {v(paths["mask-base"])})'
                  if parameters.get('mask_policy') == PRESERVATION
                  else f'{v(paths["mask-smoothed"])} * {v(paths["mask-base"])}')
    spatial_file = None
    if parameters.get('subject_mask'):
        from deep_sky_siril_subject_mask import load as load_subject_mask
        mask_session = Path(session) if session is not None else root
        spatial = load_subject_mask(mask_session,source,parameters['subject_mask'])[0]
        spatial_file = mask_session/spatial['mask']['path']
        final_mask = f'({final_mask}) * {v(spatial_file)}'
    rows = ['requires 1.4.4 1.5.0', 'set32bits']
    if report is not None:
        rows.append('# color-curve: '+json.dumps({**parameters, 'parameters': str(report)}, allow_nan=False))
    rows += ['load '+q(source), 'icc_assign sRGB']
    if previews:
        rows += ['savejpg '+q(views['parent'].with_suffix(''))+' 95']
    rows += ['split '+' '.join(q(paths[k].with_suffix('')) for k in ('r', 'g', 'b')),
             'pm '+q(f'0.2126 * {v(paths["r"])} + 0.7152 * {v(paths["g"])} + 0.0722 * {v(paths["b"])}')+' -nosum',
             'save '+q(paths['luminance'].with_suffix(''))+' -chksum',
             'pm '+q(f'max({v(paths["r"])}, max({v(paths["g"])}, {v(paths["b"])}))')+' -nosum',
             'save '+q(paths['peak'].with_suffix(''))+' -chksum',
             'pm '+q(expression)+' -nosum', *[' '.join(row) for row in regional_fill_rows(parameters)],
             'save '+q(paths['mask-base'].with_suffix(''))+' -chksum',
             f'gauss {parameters.get("sigma", 2)}', 'save '+q(paths['mask-smoothed'].with_suffix(''))+' -chksum',
             'pm '+q(final_mask)+' -nosum',
             'save '+q(paths['mask'].with_suffix(''))+' -chksum', 'icc_assign sRGB']
    if previews:
        rows += ['savejpg '+q(views['mask'].with_suffix(''))+' 95']
    rows += ['rgbcomp '+' '.join(q(paths['mask']) for _ in range(3))+' '+q('-out='+str(paths['mask-rgb'].with_suffix('')))+' -nosum']
    if tone:
        rows += ['load '+q(paths['luminance']), f'mtf 0 {parameters["midtones"]} 1',
                 'save '+q(paths['l-enhanced'].with_suffix(''))+' -chksum',
                 'pm '+q(f'1 + ({v(paths["l-enhanced"])} - {v(paths["luminance"])}) / max({v(paths["luminance"])}, 0.000001)')+' -nosum',
                 'save '+q(paths['gain'].with_suffix(''))+' -chksum',
                 'rgbcomp '+' '.join(q(paths['gain']) for _ in range(3))+' '+q('-out='+str(paths['gain-rgb'].with_suffix('')))+' -nosum',
                 'pm '+q(f'{v(source)} * {v(paths["gain-rgb"])}')+' -nosum']
    else:
        rows += ['load '+q(source), ' '.join(curve_operator(parameters['D'], parameters))]
    rows += ['save '+q(paths['enhanced'].with_suffix(''))+' -chksum',
             'pm '+q(f'{v(source)} + {v(paths["mask-rgb"])} * ({v(paths["enhanced"])} - {v(source)})')+' -nosum',
             'icc_assign sRGB', 'save '+q(paths['candidate'].with_suffix(''))+' -chksum']
    if previews:
        rows += ['savejpg '+q(views['candidate'].with_suffix(''))+' 95']
    rows += ['close']
    relative = lambda p: p.relative_to(root).as_posix()
    binding = {'policy': 'masked_red_tone_v1' if tone else POLICY, 'parameters': parameters, 'source_sha256': sha256_file(source),
               'layers': {k.replace('-', '_'): relative(paths[k]) for k in
                          ('luminance', 'peak', 'mask-base', 'mask-smoothed', 'mask')},
               'source_channels': [relative(paths[k]) for k in ('r', 'g', 'b')],
               'mask_rgb': relative(paths['mask-rgb']),
               'previews': {k: relative(p) for k, p in views.items()}}
    if spatial_file is not None:
        binding['subject_mask_file'] = str(spatial_file)
    binding['layers']['enhanced'] = relative(paths['enhanced'])
    if tone:
        binding['tone_layers'] = {k: relative(paths[k]) for k in ('l-enhanced', 'gain', 'gain-rgb')}
    return '\n'.join(rows)+'\n', paths['candidate'], list(paths.values())+list(views.values()), binding


def rgb_statistics(values):
    peak, floor = values.max(axis=0), values.min(axis=0)
    delta = peak-floor
    saturation = np.divide(delta, peak, out=np.zeros_like(peak), where=peak > 0)
    hue = np.zeros_like(peak)
    valid = delta > 1e-12
    for index, left, right, offset in ((0, 1, 2, 0), (1, 2, 0, 2), (2, 0, 1, 4)):
        selected = valid & (values[index] == peak)
        hue[selected] = ((values[left][selected]-values[right][selected])/delta[selected]+offset) % 6
    return saturation, hue*60


def tone_declaration(text):
    rows = [s.strip() for s in text.splitlines() if re.match(r'#\s*local-tone\b', s.strip())]
    if not rows:
        return None
    if len(rows) != 1 or not rows[0].startswith('# local-tone:'):
        fail('Exactly one local tone declaration is required')
    try:
        p = strict_json_bytes(rows[0].split(':', 1)[1].encode(), document='local tone declaration')
    except BundleError as exc:
        fail(str(exc))
    if (not isinstance(p, dict) or set(p) != {'selector', 'midtones', 'regions'}
        or p['selector'] != 'red' or type(p['midtones']) not in (int, float)
        or not math.isfinite(p['midtones']) or not 0 < p['midtones'] < .5
        or not isinstance(p['regions'], str) or not p['regions'].strip()):
        fail('Local tone requires selector=red, finite midtones in (0, 0.5) and regions')
    return p


def local_tone_script(session, source, primary, parameters):
    from deep_sky_siril_metrics import measure_regions
    session, source, primary = Path(session).resolve(), Path(source).resolve(), Path(primary).resolve()
    session_path(session, primary, allowed_roots=('artifacts',))
    if source.suffix.lower() not in {'.fit', '.fits', '.fts'}:
        fail('Local tone requires a scientific RGB FITS parent')
    p = tone_declaration('# local-tone: '+json.dumps(parameters, allow_nan=False))
    regions = session_path(session, p['regions'], must_exist=True, allowed_roots=('reports',))
    before = record(session, regions)
    if measure_regions(source, None, regions)['status'] != 'measured':
        fail('Local tone regions must match the current scientific parent')
    rows = load_json(regions)['regions']
    mask = derive_mask(source, rows)
    if mask is None:
        fail('Local tone sky and subject cannot be separated; preserve the parent')
    preservation = load_json(session/'session.json').get('execution_policy', {}).get('color_preservation') == PRESERVATION
    data = pixels(source)
    if any(not np.any(selector_weight(data[:, data.shape[1]-r['rect'][3]:data.shape[1]-r['rect'][1],
                                                 r['rect'][0]:r['rect'][2]], 'red', preservation=preservation))
           for r in rows if r['role'] == 'faint'):
        fail('Every local tone target must contain red display structure; preserve the parent')
    text, _, outputs, binding = script_text(session, source, primary.stem,
        {**mask, 'selector': 'red', 'midtones': p['midtones'],
         **({'mask_policy': PRESERVATION} if preservation else {})}, primary=primary)
    if before != record(session, regions):
        fail('Local tone regions changed during preparation')
    binding['regions'] = before
    binding['helper'] = {'path': 'scripts/deep_sky_siril_color.py', **helper_fingerprint(session)}
    text = text.replace('set32bits\n', 'set32bits\n# local-tone: '+json.dumps(p)+'\n', 1)
    return text, outputs, binding


def validate_local_tone(session, source, primary, commands, parameters, *, recorded_helper=None):
    from deep_sky_siril_processing import script_commands
    expected, _, binding = local_tone_script(session, source, primary, parameters)
    def normalized(rows):
        return [[re.sub(r'(?<![A-Za-z0-9_/])(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',
                        lambda m: format(float(m[0]), '.12g'), token) for token in row]
                for row in rows if row[0] not in {'stat', 'close'}]
    if normalized(commands) != normalized(script_commands(expected)):
        fail('Local tone must exactly match the protected luminance MTF recipe')
    if recorded_helper is not None:
        if (not isinstance(recorded_helper, dict) or recorded_helper.get('path') != 'scripts/deep_sky_siril_color.py'
            or not helper_matches(session, {k: v for k, v in recorded_helper.items() if k != 'path'})):
            fail('Local tone helper changed')
        binding['helper'] = recorded_helper
    return binding


def measure_local_tone(session, source, primary, binding):
    from deep_sky_siril_processing import measure_local_saturation, ProcessingEvidenceError
    helper = binding.get('helper')
    if (not isinstance(helper, dict) or helper.get('path') != 'scripts/deep_sky_siril_color.py'
        or not helper_matches(session, {k: v for k, v in helper.items() if k != 'path'})):
        fail('Local tone helper changed')
    bound_file(session, binding['regions'])
    checks = measure_local_saturation(session, source, primary, binding)
    read = lambda p: pixels(session/p)
    lum = read(binding['layers']['luminance'])[0].astype(float)
    enhanced = read(binding['tone_layers']['l-enhanced'])[0].astype(float)
    gain = read(binding['tone_layers']['gain'])[0].astype(float)
    gain_rgb = read(binding['tone_layers']['gain-rgb'])
    parent, candidate = pixels(source), pixels(primary)
    error = lambda a, b: float(np.max(np.abs(a-b))) if a.shape == b.shape else float('inf')
    m = binding['parameters']['midtones']
    expected = (m-1)*lum/((2*m-1)*lum-m)
    errors = {'mtf': error(enhanced, expected),
              'gain': error(gain, 1+(enhanced-lum)/np.maximum(lum, 1e-6)),
              'gain_rgb': error(gain_rgb, np.broadcast_to(gain, parent.shape)),
              'common_rgb_gain': error(read(binding['layers']['enhanced']), parent.astype(float)*gain)}
    excluded = read(binding['layers']['mask_base'])[0] == 0
    checks.update(tone_errors=errors, positive_common_gain=bool(np.isfinite(gain).all() and (gain > 0).all()))
    if binding.get('parameters', {}).get('mask_policy') == PRESERVATION:
        checks['mask_coverage'] = mask_coverage(read(binding['layers']['mask']), load_json(bound_file(session,binding['regions']))['regions'])
    if (not checks['positive_common_gain'] or any(not math.isfinite(v) or v > 1e-6 for v in errors.values())
        or not np.array_equal(parent[:, excluded], candidate[:, excluded])
        or not np.any(candidate > parent)
        or any(checks['endpoint_counts'][k][c] for k in ('new_shadow', 'new_highlight') for c in range(3))):
        raise ProcessingEvidenceError('Local tone arithmetic, protected pixels or endpoints changed', {'local_tone': checks})
    return checks


def color_statistics(source, candidate, regions, *, selector='all', extended=False):
    arrays = [read_fits_pixels(p) for p in (source, candidate)]
    height = arrays[0][0].shape[1]
    rows = []
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        values = [np.asarray(a[:, height-y1:height-y0, x0:x1], dtype=float)*scale+zero
                  for a, scale, zero in arrays]
        if any(v.shape[0] != 3 or not np.isfinite(v).all() for v in values):
            fail('Color measurements require finite, matching RGB regions')
        before, after = [rgb_statistics(v) for v in values]
        valid = (before[0] > .001) & (after[0] > .001)
        hue = np.abs(after[1]-before[1])
        hue = np.minimum(hue, 360-hue)
        stats = []
        for data, (saturation, _) in zip(values, (before, after)):
            chroma = np.stack((data[0]-data[1], data[2]-data[1]))
            stats.append({'saturation_median': float(np.median(saturation)),
                          'chroma_mad': float(np.median(np.abs(chroma-np.median(chroma, axis=(1, 2), keepdims=True)))),
                          'luminance_median': float(np.median(np.einsum('c,cyx->yx', [.2126, .7152, .0722], data)))})
        rows.append({**region, 'parent': stats[0], 'candidate': stats[1],
                     'hue_max_degrees': float(hue[valid].max()) if valid.any() else None})
        if extended:
            for stats_row, data in zip(stats, values):
                stats_row.update(channel_medians=[float(np.median(v)) for v in data],
                                 negative_pixels=[int(np.count_nonzero(v < 0)) for v in data])
            rows[-1]['saturation_median_change'] = stats[1]['saturation_median']-stats[0]['saturation_median']
            rows[-1]['new_shadow'] = [int(np.count_nonzero((x > 0) & (y <= 0))) for x, y in zip(*values)]
            rows[-1]['new_highlight'] = [int(np.count_nonzero((x < 1) & (y >= 1))) for x, y in zip(*values)]
        if selector != 'all':
            rows[-1]['selector_pixels'] = int(np.count_nonzero(selector_weight(values[0], selector)))
    return rows


def derive_mask(source, regions, *, allow_overlap=False, region_goals=None):
    data = pixels(source)
    if data.shape[0] != 3:
        fail('Color curves require RGB FITS')
    height = data.shape[1]
    crops = {}
    goals = {g['region']:g for g in region_goals['goals']} if region_goals else None
    for row in regions:
        x0, y0, x1, y1 = row['rect']
        if (x1-x0)*(y1-y0) < 64 and (not goals or row['role'] in {'background','core'} or goals[row['id']]['intent']=='enhance'):
            fail('Color landmark regions require at least 64 pixels')
        crops[row['id']] = data[:, height-y1:height-y0, x0:x1]
    if goals:
        rectangles = [r['rect'] for r in regions if goals[r['id']]['intent'] == 'preserve']
        for row in regions:
            if goals[row['id']]['intent'] == 'enhance':
                crops[row['id']] = crops[row['id']][:,::-1][:,regional_domain(row,rectangles)]
    sky = [r for r in regions if r['role'] == 'background']
    target = [r for r in regions if r['role'] == 'faint' and (not goals or goals[r['id']]['intent'] == 'enhance')]
    if not sky or not target:
        fail('Trusted background and faint subject regions are required')
    for a in sky:
        for b in regions:
            if a is b:
                continue
            x0, y0, x1, y1 = a['rect']; u0, v0, u1, v1 = b['rect']
            if max(x0, u0) < min(x1, u1) and max(y0, v0) < min(y1, v1):
                fail('Background regions must not overlap other color regions')
    luminance = lambda r: np.einsum('c,c...->...', [.2126, .7152, .0722], crops[r['id']])
    low = max(float(np.percentile(luminance(r), 99)) for r in sky)
    end = min(float(np.percentile(luminance(r), 25)) for r in target)
    if allow_overlap and low >= end:
        low = max(float(np.median(luminance(r))) for r in sky)
        end = min(float(np.median(luminance(r))) for r in target)
    cores = [crops[r['id']].max(axis=0) for r in regions if r['role'] == 'core']
    high = min(.85, max(end, float(np.median([np.median(v) for v in cores])) if cores else .65))
    white = min(1., max(high+.05, float(max(np.percentile(v, 99) for v in cores)) if cores else .9))
    if not 0 <= low < end <= high < white <= 1:
        return None
    return dict(low_start=low, low_end=end, high_start=high, high_end=white, sigma=2)


def validate_parameter_binding(session, source, parameters):
    path = session_path(session, parameters['parameters'], must_exist=True, allowed_roots=('reports',))
    report = load_json(path)
    if (report.get('schema') != 'starun-siril.color-parameters.v1' or report.get('policy') != POLICY
        or report.get('status') != 'solved' or report.get('selected_parameters') != {k: v for k, v in parameters.items() if k != 'parameters'}
        or report.get('selector', 'all') != parameters.get('selector', 'all')
        or type(report.get('starless')) is not bool):
        fail('Color curve parameter report does not match the declared operation')
    source_record = report['bindings']['source']
    policy = load_json(session/'session.json').get('execution_policy', {})
    if parameters.get('curve') == 'invght' and policy.get('color_curve_variants') != CURVE_VARIANTS:
        fail('Inverse color curves require their frozen execution policy')
    if parameters.get('mask_policy') != policy.get('color_preservation'):
        fail('Color preservation recipe differs from the frozen policy')
    enhancement = policy.get('subject_enhancement')
    evaluation = policy.get('color_evaluation')
    helper = report['bindings'].get('helper')
    helper_valid = (helper_matches(session, helper) if evaluation == EVALUATION
                    else helper == {'path': 'scripts/deep_sky_siril_color.py', **helper_fingerprint(session)})
    if (parameters.get('selector') == 'cyan'
        and not helper_valid
        and not (enhancement is None and helper == {'path': 'scripts/deep_sky_siril_color.py', **STRICT_MASK_HELPER})):
        fail('Selective color helper changed')
    if source_record != {'path': str(source), 'sha256': sha256_file(source), 'size': source.stat().st_size}:
        fail('Color curve scientific source changed')
    regions = bound_file(session, report['bindings']['regions'])
    if report.get('evaluation') != evaluation:
        fail('Color evaluation differs from the frozen execution policy')
    if evaluation == EVALUATION:
        validate_evaluation_evidence(session, source, report)
        if report['proposal']['mask_mode'] != parameters.get('mask_mode'):
            fail('Color mask mode differs from the explicit proposal')
    if report.get('subject_enhancement') != enhancement:
        fail('Color mask policy differs from the frozen execution policy')
    if enhancement == 'benefit_first_v1' and parameters.get('mask_mode') != 'none':
        rows = load_json(regions)['regions']
        goals = load_region_goals(session,source,report['bindings']['regions'],report['region_goals'])[0] if report.get('region_goals') else None
        strict = derive_mask(source, rows, **({'region_goals':goals} if goals else {}))
        selected = strict if strict is not None else derive_mask(source, rows, allow_overlap=True, **({'region_goals':goals} if goals else {}))
        strategy = 'strict_percentiles' if strict is not None else 'overlap_medians'
        if (selected != {key: parameters[key] for key in MASK_KEYS}
            or report.get('mask_strategy') != strategy):
            fail('Color mask no longer matches the measured source regions')
    configuration = bound_file(session, report['bindings']['configuration'])
    for trial in report['trials']:
        for key in ('script', 'log'):
            bound_file(session, trial[key])
    frozen = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if report['bindings']['runtime'] != frozen or not fingerprint_matches(frozen['fingerprint']):
        fail('Color curve runtime differs from the frozen Siril runtime')
    return {**({'region_goals':report['region_goals']} if report.get('region_goals') else {}),
            'report': record(session, path), 'regions': record(session, regions),
            'configuration': record(session, configuration), 'starless': report['starless']}


def validate_evaluation_evidence(session, source, report):
    """Verify retained native comparisons, including each same-parent revision."""
    expected_source = {'path': str(source), 'sha256': sha256_file(source), 'size': source.stat().st_size}
    expected_regions = report['bindings']['regions']
    frozen = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    seen = set()
    while True:
        if (report.get('evaluation') != EVALUATION or report.get('policy') != POLICY
            or not helper_matches(session, report['bindings'].get('helper'))
            or report['bindings']['source'] != expected_source
            or report['bindings']['regions'] != expected_regions
            or report['bindings']['runtime'] != frozen or not fingerprint_matches(frozen['fingerprint'])):
            fail('Color proposal source, regions, implementation or frozen Siril evidence changed')
        configuration = report['bindings']['configuration']
        if (record(session, bound_file(session, expected_regions)) != expected_regions
            or configuration_binding(session, bound_file(session, configuration)) != configuration):
            fail('Color evidence binding changed')
        if len(report.get('trials', [])) != 1:
            fail('A color revision requires one retained native candidate')
        trial = report['trials'][0]
        proposal = report['proposal']
        if any(proposal.get(key) != trial['parameters'].get(key) for key in CURVE_KEYS):
            fail('Color curve shape differs from the explicit proposal')
        if (proposal.get('curve') == 'invght'
            and load_json(session/'session.json')['execution_policy'].get('color_curve_variants') != CURVE_VARIANTS):
            fail('Inverse color curves require their frozen execution policy')
        spatial = proposal.get('subject_mask')
        if spatial:
            from deep_sky_siril_subject_mask import load as load_subject_mask
            load_subject_mask(session,source,spatial)
            if not report.get('starless') or any(proposal[k] != trial['parameters'].get(k) for k in ('subject_mask','high_start','high_end')):
                fail('Spatial color trial differs from its bound proposal')
        elif 'subject_mask' in trial['parameters']:
            fail('Spatial color requires a bound proposal')
        regional = report.get('region_goals')
        if regional:
            document,goal_binding,rectangles = load_region_goals(session,source,expected_regions,regional)
            if (proposal.get('region_goals') != goal_binding or trial['parameters'].get('region_goals') != goal_binding
                or trial['parameters'].get('protected_rects') != rectangles or report.get('target_gain') is not None):
                fail('Regional color declaration differs from its bound targets')
            rows = load_json(bound_file(session,expected_regions))['regions']
            results,reached = measure_region_goals(source,bound_file(session,trial['candidate']),rows,document,selector=report.get('selector','all'))
            if (report.get('target_results') != results or report.get('target_reached') is not reached
                or report.get('achieved_gain') is not None or trial.get('gain') is not None
                or trial.get('target_results') != results):
                fail('Regional color results changed')
        elif 'region_goals' in trial['parameters'] or 'protected_rects' in trial['parameters']:
            fail('Regional mask operations require bound regional goals')
        policy = load_json(session/'session.json')['execution_policy'].get('color_preservation')
        if trial['parameters'].get('mask_policy') != policy:
            fail('Color trial mask policy changed')
        if policy == PRESERVATION:
            validate_saturation_protection(proposal)
            if any(proposal[k] != trial['parameters'].get(k) for k in SATURATION_KEYS):
                fail('Color saturation protection differs from the explicit proposal')
        if (not explanation(proposal.get('goal')) or not explanation(proposal.get('parameter_reason'))
            or trial['D'] != proposal['D'] or trial['parameters'].get('D') != proposal['D']
            or trial['parameters'].get('mask_mode') != proposal['mask_mode']
            or (report.get('status') == 'solved' and report.get('selected_parameters') != trial['parameters'])):
            fail('Color trial differs from the explicit strength')
        for item in (trial['script'], trial['log'], trial['candidate'], trial['panels'], *trial['previews'].values()):
            if record(session, bound_file(session, item)) != item:
                fail('Color native candidate or observation material changed')
        curve_declaration('# color-curve: '+json.dumps({**trial['parameters'], 'parameters': 'reports/comparison.json'}, allow_nan=False))
        candidate = bound_file(session, trial['candidate'])
        expected_script = script_text(candidate.parent.parent, source, trial['run_id'], trial['parameters'], session=session)[0]
        if bound_file(session, trial['script']).read_text() != expected_script:
            fail('Retained native color script differs from its proposed recipe')
        previous = report.get('previous')
        if previous is None:
            if report['proposal']['mask_mode'] != 'none':
                fail('Inspect an unmasked candidate before adding a luminance mask')
            return
        if (previous['path'] in seen or len(seen) >= 32 or not explanation(report.get('observation'))):
            fail('Color revisions require acyclic, observation-bound comparisons')
        seen.add(previous['path'])
        path = bound_file(session, previous)
        if record(session, path) != previous:
            fail('Previous color comparison changed')
        prior = load_json(path)
        if ((report.get('region_goals') != prior.get('region_goals'))
            and not explanation(report.get('observation'))):
            fail('Changed regional goals require actual revision observations')
        if (report['proposal']['mask_mode'] == 'luminance' and prior['proposal']['mask_mode'] == 'none'
            and any(report['proposal'].get(k) != prior['proposal'].get(k) for k in ('D', *CURVE_KEYS, *SATURATION_KEYS))):
            fail('Add a mask at the same strength first; revise strength in a separate observed candidate')
        report = prior


def explanation(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def validate_unmasked_curve(session, source, primary, commands, parameters):
    if parameters.get('mask_policy') == PRESERVATION:
        from deep_sky_siril_processing import validate_local_saturation
        return validate_local_saturation(session, source, primary, commands, parameters)
    from deep_sky_siril_processing import script_commands
    from deep_sky_siril_validation import _normalized_write
    reference = validate_parameter_binding(session, source, parameters)
    expected, _, _, binding = script_text(session, source, primary.stem, parameters, primary=primary)
    clean = lambda rows: [row for row in rows if row[0] not in {'requires', 'set32bits', 'stat', 'close'}]
    actual = clean(commands)
    previews = [row for row in actual if row[0] == 'savejpg']
    if len(previews) != 2 or any(len(row) != 3 or row[2] != '95' for row in previews):
        fail('Unmasked color requires two direct previews at quality 95')
    paths = [session_path(session, _normalized_write('savejpg', row[1]), allowed_roots=('previews',)) for row in previews]
    if len(set(paths)) != 2:
        fail('Unmasked color previews must be distinct')
    # Parse the strength numerically while retaining the exact source/write recipe.
    expected_rows = clean(script_commands(expected))
    replacement = iter(previews)
    expected_rows = [next(replacement) if row[0] == 'savejpg' else row for row in expected_rows]
    for rows in (actual, expected_rows):
        for row in rows:
            if row[0] == 'ght':
                row[1] = '-D='+repr(float(row[1][3:]))
    if actual != expected_rows:
        fail('Unmasked color must apply the declared GHS once to the bound parent')
    binding['curve_parameters'] = reference
    binding['previews'] = dict(zip(('parent', 'candidate'), (p.relative_to(session).as_posix() for p in paths)))
    return binding


def measure_unmasked_curve(session, source, primary, binding):
    if binding.get('parameters', {}).get('mask_policy') == PRESERVATION:
        from deep_sky_siril_processing import measure_local_saturation
        return measure_local_saturation(session, source, primary, binding)
    from deep_sky_siril_processing import verify_color_metadata
    verify_color_metadata(source, primary)
    before, after = pixels(source), pixels(primary)
    if before.shape != after.shape or before.shape[0] != 3 or not np.isfinite(before).all() or not np.isfinite(after).all():
        fail('Unmasked color requires aligned, finite RGB scientific pixels')
    return {'policy': UNMASKED_POLICY, 'geometry_unchanged': True,
            'max_abs_change': float(np.max(np.abs(after-before))),
            'endpoint_counts': {name: [int(np.count_nonzero(select(x, y))) for x, y in zip(before, after)]
                for name, select in (
                    ('inherited_shadow', lambda x, y: (x <= 0) & (y <= 0)),
                    ('inherited_highlight', lambda x, y: (x >= 1) & (y >= 1)),
                    ('new_shadow', lambda x, y: (x > 0) & (y <= 0)),
                    ('new_highlight', lambda x, y: (x < 1) & (y >= 1)))},
            'requires_visual_review': True}


def measure_curve(session, source, primary, binding):
    reference = binding['curve_parameters']
    path = bound_file(session, reference['report'])
    regions_path = bound_file(session, reference['regions'])
    report = load_json(path)
    selector = report['selected_parameters'].get('selector', 'all')
    rows = color_statistics(source, primary, load_json(regions_path)['regions'], selector=selector,
                            extended=report.get('evaluation') == EVALUATION)
    result = {**({'selector': selector} if selector != 'all' else {}), 'policy': POLICY, 'target_gain': report['target_gain'], 'regions': rows,
              'requires_visual_review': True, 'saturation_space': 'HSV_display_diagnostic'}
    if report.get('subject_enhancement') == 'benefit_first_v1':
        result.update(mask_strategy=report['mask_strategy'], warnings=report['warnings'])
    if report.get('evaluation') == EVALUATION:
        validate_evaluation_evidence(session, source, report)
        candidate = bound_file(session, report['trials'][0]['candidate'])
        expected, actual = pixels(candidate), pixels(primary)
        if expected.shape != actual.shape or not np.isfinite(actual).all():
            fail('Formal color output geometry or finite pixels differ from the retained native comparison')
        error = float(np.max(np.abs(expected-actual)))
        if error > 1e-6:
            fail('Formal color pixels differ from the retained native comparison')
        del expected
        before = pixels(source)
        if any(np.any((x > 0) & (y <= 0)) or np.any((x < 1) & (y >= 1)) for x, y in zip(before, actual)):
            fail('Formal color introduced new endpoints')
        result.update(evaluation=EVALUATION, mask_mode=report['proposal']['mask_mode'], proposal=report['proposal'],
                      trial_max_abs_error=error, max_abs_change=float(np.max(np.abs(actual-before))),
                      recommended_verdict='uncertain')
    if report.get('region_goals'):
        document,_,_ = load_region_goals(session,source,reference['regions'],report['region_goals'])
        result['region_goals'] = report['region_goals']
        result['achieved_gain'] = None
        result['target_results'],result['target_reached'] = measure_region_goals(source,primary,load_json(regions_path)['regions'],document,selector=selector)
    if binding.get('parameters', {}).get('mask_policy') == PRESERVATION:
        result['mask_coverage'] = mask_coverage(pixels(session/binding['layers']['mask']), load_json(regions_path)['regions'])
    if binding.get('parameters',{}).get('subject_mask'):
        from deep_sky_siril_subject_mask import weights, diagnostics
        space,_ = weights(session,source,binding['parameters']['subject_mask'])
        result['subject_mask'] = diagnostics(space,pixels(session/binding['layers']['mask'])[0],pixels(source),pixels(primary),load_json(regions_path)['regions'])
    return result


def curve_binding(run):
    return run.get('color_curve') or run.get('local_saturation', {})


def review_paths(session, run):
    if run.get('color_evaluation') != EVALUATION:
        return {}
    binding = curve_binding(run)
    reference = binding['curve_parameters']['report']
    report = load_json(bound_file(session, reference))
    items = [reference, binding['curve_parameters']['regions']]
    for comparison in (report, load_json(bound_file(session, report['previous'])) if report.get('previous') else None):
        if comparison:
            if comparison.get('proposal',{}).get('subject_mask'):
                from deep_sky_siril_subject_mask import review_paths as subject_paths
                items.extend(record(session,p) for p in subject_paths(session,Path(comparison['bindings']['source']['path']),comparison['proposal']['subject_mask']).values())
            if comparison.get('region_goals'):
                items.append(comparison['region_goals'])
            items.extend(item for trial in comparison['trials'] for item in trial['previews'].values())
            items.extend(trial['panels'] for trial in comparison['trials'])
    if report.get('previous'):
        items.append(report['previous'])
    return {item['path']: bound_file(session, item) for item in items}


def assessment_skeleton(session, run):
    if run.get('color_evaluation') != EVALUATION:
        return None
    binding = curve_binding(run)['curve_parameters']
    rows = load_json(bound_file(session, binding['regions']))['regions']
    result = {'diagnostics': binding['report'], 'goal_status': None, 'additional_benefit': None,
            'benefit_observation': '', 'adoption_reason': '',
            'regions': [{'region': {'regions': binding['regions'], 'id': row['id']},
                         'assessment': None, 'observation': ''} for row in rows]}
    report = load_json(bound_file(session,binding['report']))
    if report.get('region_goals'):
        document,_,_ = load_region_goals(session,Path(report['bindings']['source']['path']),binding['regions'],report['region_goals'])
        goals = {g['region']:g for g in document['goals']}
        for row in result['regions']:
            row['intent'] = goals[row['region']['id']]['intent']
    return result


def validate_assessment(session, run, review):
    expected = assessment_skeleton(session, run)
    block = review.get('color_assessment')
    if expected is None:
        if 'color_assessment' in review:
            fail('Color assessment requires a proposal-bound native candidate')
        return
    if (not isinstance(block, dict) or set(block) != set(expected) or block['diagnostics'] != expected['diagnostics']
        or block['goal_status'] not in ('met', 'not_met', 'uncertain')
        or block['additional_benefit'] not in ('observed', 'not_observed', 'uncertain')
        or not explanation(block['benefit_observation']) or not explanation(block['adoption_reason'])
        or not isinstance(block['regions'], list) or len(block['regions']) != len(expected['regions'])):
        fail('Assess the remaining color goal, actual benefit and every protected region')
    for row, required in zip(block['regions'], expected['regions']):
        if (not isinstance(row, dict) or set(row) != set(required) or row['region'] != required['region']
            or row['assessment'] not in ('acceptable', 'degraded', 'uncertain') or not explanation(row['observation'])
            or ('intent' in required and row['intent'] != required['intent'])):
            fail('Record actual subject, background, highlight and stellar color observations')
    if review['verdict'] == 'accept':
        if (block['goal_status'] != 'met' or block['additional_benefit'] != 'observed'
            or any(row['assessment'] != 'acceptable' for row in block['regions'])
            or not set(review_paths(session, run)) <= {item['path'] for item in review['inspected_materials']}):
            fail('Accept color only with observed benefit and acceptable protected regions')
        if run['processing_checks']['color_curve']['max_abs_change'] == 0:
            fail('Unchanged pixels do not demonstrate color benefit')


def save_region_panels(source, candidate, regions, path, *, region_goals=None):
    from PIL import Image, ImageDraw
    before, after = pixels(source), pixels(candidate)
    width = max(r['rect'][2]-r['rect'][0] for r in regions)
    panel = Image.new('RGB', (2*width, sum(r['rect'][3]-r['rect'][1]+24 for r in regions)), '#202020')
    draw, top, height = ImageDraw.Draw(panel), 0, before.shape[1]
    for row in regions:
        x0, y0, x1, y1 = row['rect']
        for index, (data, label) in enumerate(((before, 'parent'), (after, 'candidate'))):
            crop = data[:, height-y1:height-y0, x0:x1][:, ::-1].transpose(1, 2, 0)
            panel.paste(Image.fromarray(np.rint(np.clip(crop, 0, 1)*255).astype('uint8')), (index*width, top+24))
            intent = next((g['intent'] for g in region_goals['goals'] if g['region'] == row['id']), '') if region_goals else ''
            draw.text((index*width, top+3), row['id']+' '+intent+' '+label if region_goals else row['id']+' '+label, fill='white')
        top += y1-y0+24
    panel.save(path, format='PNG')


def solve(session_value, source_value, regions_value, run_id, timeout=1800, *, target_gain=None, selector='all',
          strength=None, mask_mode='none', goal=None, parameter_reason=None, previous=None, observation=None,
          saturation_protect_start=None, saturation_protect_end=None, region_goals=None,
          subject_mask=None, high_start=None, high_end=None, curve='ght', sp=None, hp=None):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current, _verified_success_receipts
    from deep_sky_siril_processing import source_state, measure_local_saturation
    from deep_sky_siril_metrics import measure_regions
    from deep_sky_siril_tooling import _subprocess_environment
    from deep_sky_siril_core import _accepted_review
    if type(selector) is not str or selector not in ('all', 'cyan'):
        fail('Use selector=all or cyan; cyan requires an explicit target gain')
    if target_gain is not None and (type(target_gain) not in (int, float)
                                    or not math.isfinite(target_gain) or target_gain < 0):
        fail('Target gain must be a finite nonnegative number')
    if (target_gain is None) == (region_goals is None):
        fail('Provide exactly one explicit target gain or regional goals file')
    if mask_mode not in ('none', 'luminance'):
        fail('Use mask_mode=none or luminance')
    if type(curve) is not str or curve not in ('ght', 'invght'):
        fail('Use curve=ght or invght')
    if curve == 'ght':
        curve_parameters(curve, sp, hp)
    protection = dict(zip(SATURATION_KEYS, (saturation_protect_start, saturation_protect_end)))
    session, payload, manifest = load_session(str(session_value))
    payload = effective_session_payload(session, payload, manifest)
    if (not execution_policy_is_current(payload) or payload['execution_policy'].get('color_curve') != POLICY
        or payload['execution_policy'].get('color_evaluation') != EVALUATION):
        fail('New color solves require the current frozen execution policy')
    if not RUN_ID_PATTERN.fullmatch(run_id) or type(timeout) is not int or not 1 <= timeout <= 86400:
        fail('Invalid color solve run ID or timeout')
    original = payload.get('input', {}).get('path')
    candidate = Path(source_value).expanduser()
    source = (Path(original).resolve() if original and candidate.is_absolute() and candidate.resolve() == Path(original).resolve()
              else session_path(session, source_value, must_exist=True, allowed_roots=('artifacts',)))
    if source.is_symlink() or source.suffix.lower() not in {'.fit', '.fits', '.fts'}:
        fail('Color solve requires an uncompressed scientific RGB FITS parent')
    state = source_state(session, payload, source)
    if selector == 'cyan' and not state['separation_run']:
        fail('Selective color requires a trusted nonlinear starless parent')
    if state['domain'] != 'nonlinear' or payload['context']['channel_mode'] in {'unknown', 'mono'}:
        fail('Color solve requires a confirmed nonlinear RGB parent')
    parent = state['run']
    if parent is None:
        inspections = [r for r in _verified_success_receipts(session) if r['protocol'] == 'input.inspect' and r['source_path'] == '@input']
        if len(inspections) != 1:
            fail('Exactly one accepted input inspection is required')
        parent = inspections[0]
    _accepted_review(session, parent['id'])
    if state['separation_run']:
        _accepted_review(session, state['separation_run'])
    spatial_parameters = {}
    if subject_mask is not None:
        from deep_sky_siril_subject_mask import load as load_subject_mask, highlight
        if not state['separation_run'] or mask_mode != 'none' or selector != 'all':
            fail('Spatial color requires a trusted nonlinear starless parent, mask-mode none and selector all')
        _,spatial_binding = load_subject_mask(session,source,subject_mask)
        spatial_parameters = {'subject_mask':spatial_binding,'high_start':high_start,'high_end':high_end}
        highlight(spatial_parameters)
    elif high_start is not None or high_end is not None:
        fail('Explicit high bounds require a subject mask')
    regions_path = session_path(session, regions_value, must_exist=True, allowed_roots=('reports',))
    if measure_regions(source, None, regions_path)['status'] != 'measured':
        fail('Color regions must match the current finite scientific source')
    regions = load_json(regions_path)['regions']
    regional = None
    goal_binding,rectangles = None,[]
    if region_goals is not None:
        regional,goal_binding,rectangles = load_region_goals(session,source,record(session,regions_path),region_goals)
    active = any(g['intent'] == 'enhance' for g in regional['goals']) if regional else target_gain > 0
    variant = {}
    if active:
        variant = curve_parameters(curve, sp, hp)
        if curve == 'invght' and payload['execution_policy'].get('color_curve_variants') != CURVE_VARIANTS:
            fail('Inverse color curves require their frozen execution policy')
        validate_saturation_protection(protection)
        if type(strength) not in (int, float) or not math.isfinite(strength) or not 0 < strength <= 10:
            fail('Provide an explicit finite strength D within (0, 10]; no grid or default is used')
        if not explanation(goal) or not explanation(parameter_reason):
            fail('Explain the remaining color goal and explicit parameter rationale')
        if selector != 'all' and mask_mode != 'luminance':
            fail('Cyan selection requires the explicit luminance-mask recipe; an unmasked probe affects all colors')
        if (previous is None) != (observation is None) or (previous is not None and not explanation(observation)):
            fail('A color revision requires both a previous comparison and an actual observation')
        if mask_mode == 'luminance' and previous is None:
            fail('Inspect an unmasked comparison before proposing a luminance mask')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not fingerprint_matches(runtime['fingerprint']):
        fail('Frozen Siril runtime is missing or changed')
    directory = session_path(session, f'reports/{run_id}', allowed_roots=('reports',))
    if directory.exists():
        fail('Color solve evidence already exists; use a fresh run ID')
    directory.mkdir(parents=True)
    source_record = {'path': str(source), 'sha256': sha256_file(source), 'size': source.stat().st_size}
    report = {'schema': 'starun-siril.color-parameters.v1', 'policy': POLICY, 'evaluation': EVALUATION,
              **({'selector': selector} if selector != 'all' else {}),
              'status': 'skipped', 'starless': bool(state['separation_run']), 'target_gain': target_gain,
              'subject_enhancement': payload['execution_policy'].get('subject_enhancement'),
              'proposal': ({'D': strength, 'mask_mode': mask_mode, 'goal': goal, 'parameter_reason': parameter_reason, **variant, **protection, **spatial_parameters}
                           if active else None),
              'bindings': {'source': source_record, 'regions': record(session, regions_path), 'runtime': runtime,
                           'helper': helper_fingerprint(session)},
              'selected_parameters': None, 'trials': [], 'warnings': [], 'requires_visual_review': True}
    if regional:
        report['region_goals'] = goal_binding
        report['target_results'],report['target_reached'] = measure_region_goals(source,source,regions,regional,selector=selector)
        report['achieved_gain'] = None
        if active:
            report['proposal']['region_goals'] = goal_binding
    baseline = color_statistics(source, source, regions, extended=True)
    if (active or not regional) and not any(r['role'] == 'faint' for r in baseline):
        fail('Color evaluation requires a protected faint subject ROI')
    report['baseline'] = baseline
    if not active:
        report['reason'] = 'All regions require preservation; preserve the parent' if regional else 'Zero target gain requested; preserve the parent'
    else:
        if previous is not None:
            previous_path = session_path(session, previous, must_exist=True, allowed_roots=('reports',))
            prior = load_json(previous_path)
            validate_evaluation_evidence(session, source, prior)
            if (prior['bindings']['regions'] != report['bindings']['regions']
                or (mask_mode == 'luminance' and prior['proposal']['mask_mode'] == 'none'
                    and any({'D': strength, **variant, **protection}.get(k) != prior['proposal'].get(k)
                            for k in ('D', *CURVE_KEYS, *SATURATION_KEYS)))):
                fail('Color revisions require the same parent/ROIs; first compare a mask at the same strength')
            if (mask_mode == 'luminance' and prior['proposal']['mask_mode'] == 'none'
                and goal_binding != prior.get('region_goals')):
                fail('First compare the luminance mask with the same regional goals')
            report.update(previous=record(session, previous_path), observation=observation)
        else:
            for prior_path in (session/'reports').glob('*/color-parameters.json'):
                prior_path = session_path(session, prior_path, must_exist=True, allowed_roots=('reports',))
                prior = load_json(prior_path)
                if (prior.get('evaluation') == EVALUATION and prior.get('trials')
                    and prior.get('bindings', {}).get('source') == source_record):
                    fail('Another color candidate from this parent requires an observation-bound revision')
        mask = None
        report['mask_strategy'] = 'none'
        if mask_mode == 'luminance':
            mask = derive_mask(source, regions, **({'region_goals':regional} if regional else {}))
            report['mask_strategy'] = 'strict_percentiles'
            if mask is None:
                mask = derive_mask(source, regions, allow_overlap=True, **({'region_goals':regional} if regional else {}))
                report['mask_strategy'] = 'overlap_medians' if mask is not None else 'unavailable'
                if mask is not None:
                    report['warnings'].append({'code': 'sky_subject_luminance_overlap',
                        'message': 'Some sky pixels may gain saturation; review actual subject benefit and background color.'})
        if mask_mode == 'luminance' and mask is None:
            report['reason'] = 'Luminance mask unavailable; retain the previous unmasked comparison for review'
        elif selector != 'all' and (any(r['selector_pixels'] == 0 for r in report['target_results'] if r['intent'] == 'enhance') if regional
                                  else any(r['selector_pixels'] == 0 for r in color_statistics(source, source, regions, selector=selector) if r['role'] == 'faint')):
            report['reason'] = 'Every target ROI must contain the requested display color; preserve the parent'
        else:
            parameters = {'D': strength, 'mask_mode': mask_mode, **(mask or {}),
                          'mask_policy': PRESERVATION, **variant, **protection, **spatial_parameters}
            if regional:
                parameters.update(region_goals=goal_binding,protected_rects=rectangles)
            if selector != 'all':
                parameters['selector'] = selector
            if spatial_parameters:
                from deep_sky_siril_subject_mask import weights
                space,_ = weights(session,source,spatial_parameters['subject_mask'])
                parent_pixels = pixels(source)
                peak = parent_pixels.max(axis=0)
                smooth = lambda t: np.clip(t,0,1)**2*(3-2*np.clip(t,0,1))
                sat = np.divide(peak-parent_pixels.min(axis=0),peak,out=np.zeros_like(peak),where=peak>0)
                effective = space*(1-smooth((peak-high_start)/(high_end-high_start)))*(1-smooth((sat-protection[SATURATION_KEYS[0]])/(protection[SATURATION_KEYS[1]]-protection[SATURATION_KEYS[0]])))
                for x0,y0,x1,y1 in rectangles or []:
                    effective[space.shape[0]-y1:space.shape[0]-y0,x0:x1] = 0
                if not effective.any():
                    report['reason'] = 'No effective spatial coverage; preserve the parent'
                    path = directory/'color-parameters.json'; atomic_write_json(path,report)
                    return {'status':'skipped','report':str(path),'reason':report['reason']}
            root = directory/'trial'
            (root/'artifacts').mkdir(parents=True); (root/'previews').mkdir()
            config = directory/'trial.ini'
            config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\nfits_save_icc=true\n'
                              '[gui]\nicc_autoassignment=0\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
            text, primary, outputs, binding = script_text(root, source, run_id, parameters, session=session)
            script, log = directory/'trial.ssf', directory/'trial.log'
            script.write_text(text)
            configuration_binding(session, config)
            if not fingerprint_matches(runtime['fingerprint']):
                fail('Color evaluation runtime changed')
            command = [runtime['path'], '--initfile='+str(config), '--directory='+str(root),
                       '--offline', '--script='+str(script)]
            started_at, started = datetime.now(timezone.utc).isoformat(), time.monotonic()
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        timeout=timeout, env=_subprocess_environment('siril_runtime'))
            except subprocess.TimeoutExpired as exc:
                output = exc.stdout or b''
                log.write_text(output.decode(errors='replace') if isinstance(output, bytes) else output)
                fail('Color evaluation timed out; trial log retained')
            log.write_text(result.stdout)
            diagnostics = diagnose_siril_log(result.stdout, exit_code=result.returncode, timed_out=False, execution_valid=primary.is_file())
            if result.returncode or diagnostics['status'] == 'failed' or not primary.is_file():
                fail('Native color trial failed; inspect '+str(log))
            report['bindings']['configuration'] = configuration_binding(session, config)
            checks = (measure_unmasked_curve(root, source, primary, binding) if mask_mode == 'none'
                      else measure_local_saturation(root, source, primary, binding))
            if parameters.get('mask_policy') == PRESERVATION:
                checks['mask_coverage'] = mask_coverage(pixels(root/binding['layers']['mask']), regions)
                if spatial_parameters:
                    checks['spatial_coverage'] = mask_coverage(space,regions)
            colors = color_statistics(source, primary, regions, selector=selector, extended=True)
            if regional:
                target_results,reached = measure_region_goals(source,primary,regions,regional,selector=selector)
                enhance_ids = {r['region'] for r in target_results if r['intent'] == 'enhance'}
            subjects = [r for r in colors if r['role'] == 'faint' and (not regional or r['id'] in enhance_ids)]
            gains = [r['candidate']['saturation_median']/r['parent']['saturation_median']-1
                     if r['parent']['saturation_median'] > .0001 else None for r in subjects]
            gain = min(gains) if not regional and all(v is not None for v in gains) else None
            safe = not any(checks['endpoint_counts'][k][c] for k in ('new_shadow', 'new_highlight') for c in range(3))
            panels = directory/'regions.png'
            save_region_panels(source, primary, regions, panels, **({'region_goals':regional} if regional else {}))
            trial = {'run_id': run_id, 'D': strength, 'parameters': parameters, 'gain': gain, 'eligible': safe, 'command': command,
                     'started_at': started_at, 'elapsed_seconds': time.monotonic()-started,
                     'script': record(session, script), 'log': record(session, log),
                     'candidate': record(session, primary),
                     'panels': record(session, panels),
                     'previews': {k: record(session, root/v) for k, v in binding['previews'].items()},
                     'checks': checks, 'regions': colors}
            if regional:
                trial['target_results'] = target_results
                report.update(target_results=target_results,target_reached=reached)
            report['trials'].append(trial)
            if (any(r['achieved_gain'] is None for r in target_results if r['intent'] == 'enhance') if regional
                else any(r['parent']['saturation_median'] <= .0001 for r in subjects)):
                report['warnings'].append({'code': 'relative_gain_unmeasurable',
                    'message': 'Near-neutral subject saturation does not support a stable relative gain; inspect actual color changes.'})
            if any(r['candidate']['saturation_median'] >= .8 for r in subjects):
                report['warnings'].append({'code': 'high_subject_saturation',
                    'message': 'Inspect subject color gradation and transitions; the median alone does not decide acceptance.'})
            report.update(achieved_gain=gain, target_reached=(reached if regional else gain >= target_gain if gain is not None else None))
            if safe:
                report.update(status='solved', selected_parameters=parameters)
            else:
                report['reason'] = 'New endpoints prevent adoption; retain the native comparison for an observed revision'
    if (sha256_file(source) != report['bindings']['source']['sha256']
        or record(session, regions_path) != report['bindings']['regions']
        or ('configuration' in report['bindings']
            and record(session, directory/'trial.ini') != report['bindings']['configuration'])
        or not fingerprint_matches(runtime['fingerprint'])):
        fail('Color solve evidence changed during trials')
    if regional and load_region_goals(session,source,report['bindings']['regions'],goal_binding)[1] != goal_binding:
        fail('Regional goals changed during the trial')
    path = directory/'color-parameters.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return {'status': report['status'], 'report': str(path), 'parameters': report['selected_parameters'],
            'requires_visual_review': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--regions', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--target-gain', type=float,
                        help='Desired relative saturation gain; 0.08 means an 8 percent increase, 0 skips trials')
    parser.add_argument('--region-goals', help='Bound per-ROI enhance/preserve goals; mutually exclusive with --target-gain')
    parser.add_argument('--selector', choices=('all', 'cyan'), default='all',
                        help='Optional display color selection; cyan requires an explicit target gain')
    parser.add_argument('--strength', type=float, help='Explicit native GHS D in (0, 10]; no automatic strength grid')
    parser.add_argument('--curve', choices=('ght', 'invght'), default='ght', help='Optional native saturation curve')
    parser.add_argument('--sp', type=float, help='Explicit inverse curve symmetry point')
    parser.add_argument('--hp', type=float, help='Explicit inverse curve highlight protection point')
    parser.add_argument('--saturation-protect-start', type=float, help='Explicit parent HSV saturation protection start')
    parser.add_argument('--saturation-protect-end', type=float, help='Explicit parent HSV saturation protection endpoint')
    parser.add_argument('--subject-mask', help='Prepared source-bound polygon mask report')
    parser.add_argument('--high-start', type=float)
    parser.add_argument('--high-end', type=float)
    parser.add_argument('--mask-mode', choices=('none', 'luminance'), default='none')
    parser.add_argument('--goal', help='Observed remaining color goal after upstream processing')
    parser.add_argument('--parameter-reason', help='Reason for this explicit strength')
    parser.add_argument('--previous', help='Previous immutable color comparison report from the same parent and ROIs')
    parser.add_argument('--observation', help='Actual previous comparison observation motivating the revision')
    args = parser.parse_args()
    try:
        options = {'region_goals': args.region_goals} if args.region_goals is not None else {'target_gain': args.target_gain}
        if args.region_goals is not None and args.target_gain is not None:
            fail('Use either target gain or regional goals')
        if args.selector != 'all':
            options['selector'] = args.selector
        if args.curve != 'ght':
            options['curve'] = args.curve
        for key in ('strength', 'sp', 'hp', 'goal', 'parameter_reason', 'previous', 'observation', 'subject_mask', 'high_start', 'high_end', *SATURATION_KEYS):
            if getattr(args, key) is not None:
                options[key] = getattr(args, key)
        if args.mask_mode != 'none':
            options['mask_mode'] = args.mask_mode
        result = solve(args.session, args.source, args.regions, args.run_id, args.timeout, **options)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'reason': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

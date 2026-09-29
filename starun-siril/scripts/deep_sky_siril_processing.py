"""Native processing contracts: validate Agent SSF and measure outputs, never generate pixels."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shlex
import re

from deep_sky_siril_contract import ContractError, fingerprint, sha256_file
from deep_sky_siril_artifacts import read_fits_header, fits_geometry, read_fits_pixels

SCIENCE = {'.fit', '.fits', '.fts', '.xisf'}
LINEAR_ONLY = {'background.subtract', 'astrometry.solve', 'color.calibrate',
               'restoration.deconvolve', 'restoration.denoise', 'stars.separate', 'stretch'}
NONLINEAR_ONLY = {'structure.local-contrast', 'restoration.denoise-nonlinear',
                  'stars.recompose', 'color.map', 'color.finish', 'delivery.render'}
TRANSFORMS = {'mtf', 'asinh', 'ght'}


def fail(message):
    raise ContractError('processing_contract_invalid', message)


def script_commands(text):
    return [shlex.split(line) for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]


def finite_number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        fail(f'Invalid numeric parameter: {value}')
    if not math.isfinite(result):
        fail('Processing parameters must be finite')
    return result


def validate_parameters(tokens, protocol):
    name = tokens[0].lower()
    if name == 'ght':
        allowed = {'D', 'B', 'LP', 'SP', 'HP', 'clipmode'}
        options = {}
        for token in tokens[1:]:
            if token == '-human':
                continue
            if not token.startswith('-') or '=' not in token:
                fail('GHS must apply linked human luminance to all channels')
            key, value = token[1:].split('=', 1)
            if key not in allowed or key in options:
                fail('Unknown or duplicate GHS option')
            options[key] = value
        if 'D' not in options or '-human' not in tokens or options.get('clipmode') != 'rgbblend':
            fail('GHS requires explicit D, -human and -clipmode=rgbblend')
        d = finite_number(options['D']); b = finite_number(options.get('B', 0))
        lp, sp, hp = [finite_number(options.get(k, v)) for k, v in [('LP', 0), ('SP', 0), ('HP', 1)]]
        if not (0 <= d <= 10 and -5 <= b <= 15 and 0 <= lp <= sp <= hp <= 1):
            fail('GHS parameters are outside D/B/LP/SP/HP bounds')
    elif name == 'mtf':
        if len(tokens) != 4:
            fail('MTF requires explicit low, midtones, high and all channels')
        lo, mid, hi = map(finite_number, tokens[1:])
        if not (0 <= lo < hi <= 1 and 0 < mid < 1):
            fail('Invalid MTF bounds')
    elif name == 'asinh':
        if len(tokens) != 5 or tokens[1] != '-human' or tokens[-1] != '-clipmode=rgbblend':
            fail('Asinh requires explicit human luminance, strength, offset and rgbblend')
        strength, offset = map(finite_number, tokens[2:4])
        if not (1 <= strength <= 1000 and 0 <= offset <= 1):
            fail('Invalid Asinh strength or offset')
    elif name == 'denoise' and protocol == 'restoration.denoise-nonlinear':
        if len(tokens) != 3 or tokens[1] != '-nocosmetic' or not tokens[2].startswith('-mod='):
            fail('Nonlinear denoise permits only -nocosmetic -mod=VALUE')
        if not 0 < finite_number(tokens[2].split('=', 1)[1]) <= .35:
            fail('Nonlinear denoise modulation must be within (0, 0.35]')
    elif name == 'platesolve':
        required = ['-catalog=localgaia', '-noflip', '-nocrop']
        if tokens[1:4] != required or len(tokens) not in {4, 7}:
            fail('Astrometry requires native local Gaia, -noflip and -nocrop')
        if len(tokens) == 7:
            if not tokens[5].startswith('-focal=') or not tokens[6].startswith('-pixelsize=') or len(tokens[4].split(',')) != 2:
                fail('User astrometry requires explicit RA,DEC, focal length and pixel size')
            ra, dec = map(finite_number, tokens[4].split(','))
            focal, pixel = [finite_number(t.split('=', 1)[1]) for t in tokens[5:]]
            if not (0 <= ra < 360 and -90 <= dec <= 90 and focal > 0 and pixel > 0):
                fail('Invalid user coordinate or sampling parameters')


def primary_output(session, expected, value):
    scientific_outputs = [p for p in expected if p.suffix.lower() in SCIENCE]
    science = [p for p in scientific_outputs if p.is_relative_to(session / 'artifacts')]
    if value is not None:
        candidate = Path(value)
        candidate = (candidate if candidate.is_absolute() else session / candidate).resolve()
        if candidate not in science:
            fail('Primary output must be a declared scientific artifact')
        return candidate
    if len(scientific_outputs) > 1:
        fail('Multiple scientific artifacts require --primary-output')
    return science[0] if science else None


def source_state(session, payload, source):
    from deep_sky_siril_session import _verified_success_receipts
    original = Path(payload['input']['path']).resolve()
    receipts = _verified_success_receipts(session) if source != original else []
    owners = {r.get('primary_output'): r for r in receipts if r.get('primary_output')}
    baselines = {r.get('branch_binding', {}).get('full_baseline'): r for r in receipts}

    def derive(path, seen):
        if path == '@input':
            return {'domain': payload['context']['input_state'], 'separation_run': None, 'run': None}
        if path in seen:
            fail('Scientific lineage contains a cycle')
        receipt = owners.get(path)
        if receipt is None:
            if path in baselines and baselines[path]:
                return {'domain': 'nonlinear', 'separation_run': None, 'run': baselines[path]}
            fail('Scientific parents must be a verified primary artifact or full-stars baseline; previews and auxiliary layers are forbidden')
        parent = derive(receipt['source_path'], seen | {path})
        protocol = receipt['protocol']
        domain = 'nonlinear' if protocol == 'stretch' else parent['domain']
        if (protocol in LINEAR_ONLY and parent['domain'] != 'linear'
            or protocol in NONLINEAR_ONLY and parent['domain'] != 'nonlinear'
            or receipt.get('image_domain') != domain):
            fail('Receipt image domain disagrees with its scientific lineage')
        separation = receipt['id'] if protocol == 'stars.separate' else parent['separation_run']
        if protocol == 'stars.recompose':
            separation = None  # Recomposition produces a full-stars parent, not another starless branch.
        if protocol != 'stars.recompose' and receipt.get('branch_binding', {}).get('separation_run') != separation:
            fail('Receipt starless branch disagrees with its scientific lineage')
        return {'domain': domain, 'separation_run': separation, 'run': receipt}

    relative = '@input' if source == original else source.relative_to(session).as_posix()
    return derive(relative, set())


def transfer_chain(text, primary):
    chain = []
    loaded = False
    for tokens in script_commands(text):
        name = tokens[0].lower()
        if name == 'load':
            if loaded:
                chain = []
            loaded = True
        elif name in TRANSFORMS | {'autostretch'}:
            chain.append({'command': name, 'parameters': tokens[1:]})
        elif name == 'save':
            from deep_sky_siril_validation import _normalized_write
            if _normalized_write('save', tokens[1]) == primary:
                return chain
    return []


def image_shape(path):
    g = fits_geometry(path)
    return g['width'], g['height'], g['channels']


def wcs_matrix(header):
    if all(k in header for k in ('CD1_1', 'CD1_2', 'CD2_1', 'CD2_2')):
        return tuple(finite_number(header[k]) for k in ('CD1_1', 'CD1_2', 'CD2_1', 'CD2_2'))
    sx, sy = [finite_number(header[k]) for k in ('CDELT1', 'CDELT2')]
    return (sx * finite_number(header.get('PC1_1', 1)), sx * finite_number(header.get('PC1_2', 0)),
            sy * finite_number(header.get('PC2_1', 0)), sy * finite_number(header.get('PC2_2', 1)))


def complete_wcs(path):
    h = read_fits_header(path)
    try:
        vals = [finite_number(h[k]) for k in ('CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2')]
        if not (str(h['CTYPE1']).startswith('RA---') and str(h['CTYPE2']).startswith('DEC--')):
            return False
        a, b, c, d = wcs_matrix(h)
        if str(h['CTYPE1']).endswith('-SIP'):
            for key in ('A_ORDER', 'B_ORDER'):
                order = finite_number(h[key])
                if order < 1 or order != int(order):
                    return False
            for key, value in h.items():
                if key.startswith(('A_', 'B_', 'AP_', 'BP_')):
                    finite_number(value)
        return a * d - b * c != 0 and -90 <= vals[3] <= 90 and 0 <= vals[2] < 360
    except (KeyError, ContractError):
        return False


def _path(value, session):
    p = Path(value)
    from deep_sky_siril_validation import _normalized_write
    return _normalized_write('save', str(p if p.is_absolute() else session / p))


def _variable(path, session):
    return '$' + path.relative_to(session).with_suffix('').as_posix() + '$'


def prepare_processing(session, payload, protocol, source, primary, text, probe,
                       separation_run, stretch_run, load_run, review_run):
    state = source_state(session, payload, source)
    if protocol in LINEAR_ONLY and state['domain'] != 'linear':
        fail(f'{protocol} requires a linear scientific parent')
    if protocol in NONLINEAR_ONLY and state['domain'] != 'nonlinear':
        fail(f'{protocol} requires a nonlinear scientific parent')
    commands = script_commands(text)
    loads = [t for t in commands if t[0] == 'load']
    if protocol != 'stars.recompose' and (not loads or Path(loads[0][1]).resolve() != source):
        fail('The first loaded image must be the declared scientific source')
    if protocol == 'stretch' and any(Path(t[1]).resolve() != source for t in loads):
        fail('Stretch cannot switch to another scientific source')
    metadata = {'primary_output': primary.relative_to(session).as_posix() if primary else None,
                'image_domain': 'nonlinear' if protocol == 'stretch' else state['domain'],
                'transfer_chain': state['run'].get('transfer_chain', []) if state['run'] else [], 'branch_binding': {'separation_run': state['separation_run']}}
    if protocol in {'structure.local-contrast', 'stars.recompose'} and not state['separation_run']:
        fail('This protocol requires a starless branch, not a recomposed full-stars parent')
    if protocol == 'stars.separate':
        metadata['branch_binding']['separation_run'] = None
        # The caller binds the actual run ID, independent of output naming.
        position = next((i for i, t in enumerate(commands) if t[0] == 'starnet'), None)
        next_save = next((t for t in commands[position + 1:] if t[0] == 'save'), None) if position is not None else None
        if not next_save or _path(next_save[1], session) != primary:
            fail('Separation primary must be the starless image saved after StarNet')
    if protocol == 'stretch':
        for t in commands:
            if t[0] == 'autostretch' and (len(t) != 4 or t[1] != '-linked'):
                fail('Full-stars autostretch must be linked with explicit parameters')
        chain = transfer_chain(text, primary)
        if not chain:
            fail('Stretch primary must be saved after a declared transfer chain')
        if state['separation_run'] and any(c['command'] == 'autostretch' for c in chain):
            fail('Starless stretch requires a fully explicit replayable MTF/Asinh/GHS chain')
        if state['separation_run']:
            for c in chain:
                if c['command'] == 'ght' and not all(any(p.startswith('-' + k + '=') for p in c['parameters']) for k in ('D', 'B', 'LP', 'SP', 'HP')):
                    fail('Starless GHS requires all five numeric parameters explicitly')
        metadata['transfer_chain'] = chain
    if protocol == 'astrometry.solve':
        if source.suffix.lower() not in {'.fit', '.fits', '.fts'}:
            fail('Offline astrometry currently requires a FITS scientific source')
        solved = complete_wcs(source)
        solves = [t for t in commands if t[0] == 'platesolve']
        if solved:
            if solves:
                fail('Existing complete WCS must be preserved without forcing a solve')
            metadata['astrometry'] = {'status': 'preserved_existing_solution'}
        else:
            catalog = probe.get('tools', {}).get('local_gaia_astro', {})
            if not catalog.get('compatible'):
                raise ContractError('local_gaia_astro_missing', 'Skipped: no frozen local Gaia astrometric catalogue; no solve claimed')
            if len(solves) != 1:
                fail('Unsolved astrometry requires one local Gaia platesolve command')
            h = read_fits_header(source)
            if len(solves[0]) == 7:
                records = [line.split(':', 1)[1] for line in text.splitlines() if line.startswith('# astrometry-evidence:')]
                try:
                    evidence = json.loads(records[0]) if len(records) == 1 else {}
                    if set(evidence) != {'source', 'evidence', 'ra', 'dec', 'focal', 'pixelsize'} or evidence['source'] != 'user' or not isinstance(evidence['evidence'], str) or not evidence['evidence'].strip():
                        fail('Explicit astrometry requires a quoted user evidence record')
                    ra, dec = map(float, solves[0][4].split(','))
                    values = [ra, dec, float(solves[0][5].split('=')[1]), float(solves[0][6].split('=')[1])]
                    if values != [finite_number(evidence[k]) for k in ('ra', 'dec', 'focal', 'pixelsize')]:
                        fail('Astrometry parameters differ from user evidence')
                except (ValueError, IndexError):
                    fail('Malformed user astrometry evidence')
                metadata['astrometry_evidence'] = evidence
            else:
                for key in ('RA', 'DEC', 'FOCALLEN', 'XPIXSZ'):
                    if key not in h:
                        fail(f'Astrometry lacks file evidence for {key}; provide user evidence explicitly')
                    finite_number(h[key])
                if not (0 <= float(h['RA']) < 360 and -90 <= float(h['DEC']) <= 90 and float(h['FOCALLEN']) > 0 and float(h['XPIXSZ']) > 0):
                    fail('Invalid coordinate or sampling metadata')
                metadata['astrometry_evidence'] = {k: h[k] for k in ('RA', 'DEC', 'FOCALLEN', 'XPIXSZ')}
            metadata['astrometry'] = {'status': 'solved_local_gaia', 'catalogue': catalog['fingerprint']}
    if protocol in {'structure.local-contrast', 'restoration.denoise-nonlinear'}:
        if not state['run']:
            fail('Native nonlinear operations require a reviewed prior run')
        review_run(state['run']['id'])
    if protocol == 'structure.local-contrast':
        if not state['separation_run']:
            fail('Local contrast requires a trusted nonlinear starless branch')
        params = [line.split(':', 1)[1] for line in text.splitlines() if line.startswith('# local-contrast:')]
        if len(params) != 1:
            fail('Local contrast requires one recorded mask boundary declaration')
        try:
            p = json.loads(params[0])
        except ValueError:
            fail('Invalid local contrast mask declaration')
        if set(p) != {'low_start', 'low_end', 'high_start', 'high_end'}:
            fail('Record all four luminance mask boundaries')
        low0, low1, high0, high1 = [finite_number(p[k]) for k in ('low_start', 'low_end', 'high_start', 'high_end')]
        if not 0 <= low0 < low1 <= high0 < high1 <= 1:
            fail('Local contrast mask boundaries must be ordered in [0,1]')
        validate_local_contrast(session, source, primary, commands, p)
        metadata['local_contrast'] = {**p, 'cliplimit': 1.5, 'grid': 8, 'strength': .20, 'sigma': 2}
    if protocol == 'restoration.denoise-nonlinear' and sum(t[0] == 'denoise' for t in commands) != 1:
        fail('Nonlinear denoise requires exactly one native denoise command')
    if protocol == 'stars.recompose':
        if not separation_run or not stretch_run:
            fail('Recomposition requires --separation-run and --stretch-run')
        sep_path, sep = load_run(separation_run); str_path, stretch = load_run(stretch_run)
        review_run(separation_run); review_run(stretch_run)
        if state['run']:
            review_run(state['run']['id'])
        if (sep['protocol'] != 'stars.separate' or stretch['protocol'] != 'stretch'
            or state['separation_run'] != separation_run
            or stretch.get('branch_binding', {}).get('separation_run') != separation_run
            or stretch['source_path'] != sep.get('primary_output')):
            fail('Separation, stretch and current starless parent belong to different branches')
        chain = stretch.get('transfer_chain', [])
        if not chain or any(c['command'] not in TRANSFORMS for c in chain):
            fail('Stretch receipt has no complete explicit transfer chain')
        original = Path(payload['input']['path']) if sep['source_path'] == '@input' else session / sep['source_path']
        starless = session / sep['primary_output']
        if image_shape(source) != image_shape(starless) or image_shape(original) != image_shape(starless):
            fail('Starless branch geometry changed')
        roles = validate_recomposition(session, commands, source, original, starless, primary, chain)
        metadata['transfer_chain'] = chain
        metadata['branch_binding'] = {**roles, 'separation_run': separation_run, 'stretch_run': stretch_run,
            'separation_receipt_sha256': sha256_file(sep_path), 'stretch_receipt_sha256': sha256_file(str_path)}
    return metadata


def validate_recomposition(session, commands, source, original, starless, primary, chain):
    # Four saved scientific artifacts carry fixed roles; no clipping/rescaling is allowed.
    steps = [t for t in commands if t[0] not in {'requires', 'set32bits', 'stat', 'close', 'savejpg'}]
    saves = [t for t in steps if t[0] == 'save']
    if len(saves) != 4:
        fail('Recomposition must save full baseline, original stretched starless, display stars and candidate')
    full, base, stars, final = [_path(t[1], session) for t in saves]
    if final != primary or len({full, base, stars, final}) != 4:
        fail('Recomposition primary/role outputs are invalid')
    replay = [[c['command'], *c['parameters']] for c in chain]
    v = lambda p: _variable(p, session)
    expected = [['load', str(original)], *replay, saves[0], ['load', str(starless)], *replay, saves[1],
                ['pm', f'{v(full)} - {v(base)}'], saves[2]]
    if steps[:len(expected)] != expected or len(steps) != len(expected) + 2 or steps[-2][0] != 'pm' or steps[-1] != saves[3]:
        fail('Recomposition must replay the exact frozen chain on both linear sources and subtract in display space')
    expression = steps[-2][1]
    prefix = f'{v(source)} + '
    suffix = f' * {v(stars)}'
    if not expression.startswith(prefix) or not expression.endswith(suffix) or len(steps[-2]) != 2:
        fail('Recomposition expression must be A_prime + strength * display_stars without clipping')
    strength = finite_number(expression[len(prefix):-len(suffix)])
    if not .70 <= strength <= 1:
        fail('Star strength must be within [0.70, 1.00]')
    return {k: p.relative_to(session).as_posix() for k, p in [('full_baseline', full), ('original_starless', base), ('display_stars', stars)]} | {'strength': strength}


def validate_local_contrast(session, source, primary, commands, bounds):
    steps = [t for t in commands if t[0] not in {'requires', 'set32bits', 'stat', 'close', 'savejpg'}]
    saves = [t for t in steps if t[0] == 'save']
    splits = [t for t in steps if t[0] == 'split']
    if len(saves) != 7 or len(splits) != 1:
        fail('Local contrast requires RGB split, luminance, enhanced luminance, mask, gain and three enhanced channels')
    r, g, b = [_path(v, session) for v in splits[0][1:]]
    lum, enhanced, mask, gain, ro, go, bo = [_path(t[1], session) for t in saves]
    v = lambda p: _variable(p, session)
    a, z, c, d = [float(bounds[k]) for k in ('low_start', 'low_end', 'high_start', 'high_end')]
    t = f'min(1, max(0, ({v(lum)} - {a}) / {z-a}))'
    u = f'min(1, max(0, ({v(lum)} - {c}) / {d-c}))'
    mask_expr = f'({t}^2 * (3 - 2 * {t})) * (1 - {u}^2 * (3 - 2 * {u}))'
    expected = [['load', str(source)], splits[0], ['pm', f'0.2126 * {v(r)} + 0.7152 * {v(g)} + 0.0722 * {v(b)}'], saves[0],
                ['clahe', '1.5', '8'], saves[1], ['pm', mask_expr], ['gauss', '2'], saves[2],
                ['pm', f'1 + 0.20 * {v(mask)} * ({v(enhanced)} - {v(lum)}) / max({v(lum)}, 0.000001)'], saves[3]]
    for channel, output in zip((r, g, b), saves[4:]):
        expected.extend([['pm', f'{v(channel)} * {v(gain)}'], output])
    expected.append(['rgbcomp', str(ro), str(go), str(bo), f'-out={primary.with_suffix("")}'])
    expected.append(['load', str(primary)])
    def normalized(rows):
        return [[re.sub(r'(?<![A-Za-z0-9_/])(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', lambda m: format(float(m[0]), '.12g'), part) for part in row] for row in rows]
    if normalized(steps) != normalized(expected):
        fail('Local contrast must use weighted luminance, a smooth protected mask and the same gain on all channels')


def validate_processing_outputs(session, source, primary, metadata):
    if not metadata.get('astrometry') and 'display_stars' not in metadata.get('branch_binding', {}):
        return {}
    try:
        import numpy as np
    except ImportError as exc:
        raise ContractError('runtime_dependency_missing', 'NumPy is required for read-only WCS/pixel closure verification; no installation is performed') from exc
    result = {}
    if metadata.get('astrometry'):
        if not primary or not complete_wcs(primary) or image_shape(source) != image_shape(primary):
            fail('Astrometry output requires finite complete WCS and unchanged geometry')
        x, sx, zx = read_fits_pixels(source); y, sy, zy = read_fits_pixels(primary)
        if not np.allclose(x * sx + zx, y * sy + zy, rtol=0, atol=1e-7, equal_nan=False):
            fail('Astrometry changed pixel values or orientation')
        if metadata['astrometry']['status'] == 'preserved_existing_solution':
            before, after = read_fits_header(source), read_fits_header(primary)
            numeric_keys = ['CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2']
            numeric_keys += [k for k in before if k.startswith(('A_', 'B_', 'AP_', 'BP_'))]
            same_numeric = all(k in after and math.isclose(finite_number(before[k]), finite_number(after[k]), rel_tol=1e-12, abs_tol=1e-10) for k in numeric_keys)
            same_matrix = all(math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-15) for x, y in zip(wcs_matrix(before), wcs_matrix(after)))
            same_axes = all(str(before.get(k, 'deg')).casefold() == str(after.get(k, 'deg')).casefold() for k in ('CTYPE1', 'CTYPE2', 'CUNIT1', 'CUNIT2'))
            if not (same_numeric and same_matrix and same_axes):
                fail('Existing WCS was altered')
        result['astrometry'] = metadata['astrometry']
    branch = metadata.get('branch_binding', {})
    if 'display_stars' in branch:
        arrays = []
        for key in ('full_baseline', 'original_starless', 'display_stars'):
            raw, scale, zero = read_fits_pixels(session / branch[key]); arrays.append((raw, scale, zero))
        errors = []; negative = []
        for ch in range(arrays[0][0].shape[0]):
            i, a, s = [np.asarray(raw[ch], dtype=float) * scale + zero for raw, scale, zero in arrays]
            if i.shape != a.shape or i.shape != s.shape or not all(np.isfinite(x).all() for x in (i, a, s)):
                fail('Nonfinite pixels or geometry mismatch in recomposition layers')
            error = float(np.max(np.abs((a + s) - i))); neg = float(np.min(s))
            errors.append(error); negative.append(neg)
            if error > 1e-5 or neg < -1e-5:
                fail(f'Recomposition rejected: closure={error}, negative residual={neg}')
        result['closure'] = {'per_channel_max_abs': errors, 'per_channel_min_residual': negative,
                             'target_leakage': 'requires_independent_visual_review'}
    return result

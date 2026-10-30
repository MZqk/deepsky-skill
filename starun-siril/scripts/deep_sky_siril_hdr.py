"""Validate native masked dual transfers and measure their immutable outputs."""
from __future__ import annotations

from pathlib import Path
import re

from deep_sky_siril_contract import fingerprint, session_path
from deep_sky_siril_artifacts import fits_geometry, inspect_xisf_container, read_fits_pixels
from siril_manual_bundle import BundleError, strict_json_bytes


def declaration(text):
    from deep_sky_siril_processing import fail, finite_number
    rows = [line.split(':', 1)[1] for line in text.splitlines()
            if line.strip().startswith('# hdr-stretch:')]
    if not rows:
        return None
    try:
        value = strict_json_bytes(rows[0].encode(), document='HDR stretch declaration') if len(rows) == 1 else None
    except BundleError as exc:
        fail(str(exc))
    if not isinstance(value, dict) or set(value) != {'mask_low', 'mask_high', 'sigma'}:
        fail('HDR stretch requires one declaration with mask_low, mask_high and sigma')
    if any(type(v) not in (int, float) for v in value.values()):
        fail('HDR mask parameters must be finite JSON numbers')
    value = {k: finite_number(v) for k, v in value.items()}
    if not (0 <= value['mask_low'] < value['mask_high'] <= 1 and .5 <= value['sigma'] <= 16):
        fail('HDR mask boundaries or sigma are outside allowed bounds')
    return value


def geometry(path):
    return inspect_xisf_container(path, allow_multiple_images=True)['geometry'] if path.suffix.lower() == '.xisf' else fits_geometry(path)


def blend_expression(gentle, faint, mask, session):
    from deep_sky_siril_processing import _variable
    g, f, m = [_variable(p, session) for p in (gentle, faint, mask)]
    return f'{g} * {m} + {f} * (1 - {m})'


def validate_stretch(session, source, primary, text, *, verify_parent=True):
    from deep_sky_siril_processing import TRANSFORMS, _path, _variable, fail, finite_number, script_commands, validate_parameters
    from deep_sky_siril_validation import _normalized_write
    parameters = declaration(text)
    if parameters is None:
        fail('HDR stretch declaration is missing')
    channels = geometry(source)['channels']
    if channels not in (1, 3):
        fail('HDR stretch requires mono or RGB geometry')
    steps = [t for t in script_commands(text) if t[0] not in {'requires', 'set32bits', 'stat', 'close'}]
    position = 0
    paths = set()

    def take(name):
        nonlocal position
        if position >= len(steps) or steps[position][0] != name:
            fail(f'HDR stretch requires {name} at operation {position + 1}')
        row = steps[position]
        position += 1
        return row

    def expect(row):
        actual = take(row[0])
        if row[0] == 'pm' and len(actual) == len(row):
            def numeric_expression(expression):
                return ''.join(part if part.startswith('$') else re.sub(
                    r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',
                    lambda m: format(float(m[0]), '.12g'), part)
                    for part in re.split(r'(\$[^$]+\$)', expression))
            matches = actual[2:] == row[2:] and numeric_expression(actual[1]) == numeric_expression(row[1])
        elif row[0] == 'gauss' and len(actual) == len(row) == 2:
            matches = finite_number(actual[1]) == float(row[1])
        else:
            matches = actual == row
        if not matches:
            fail(f'HDR stretch differs from its declared native {row[0]} operation')

    def output(command):
        row = take(command)
        if (len(row) not in (2, 3) or len(row) == 3 and
            (row[2] != '-chksum' if command == 'save' else row[2] != '95')):
            fail('HDR output options must use checksummed FITS or direct JPEG quality 95')
        path = _normalized_write(command, row[1])
        session_path(session, path, allowed_roots=('artifacts',) if command == 'save' else ('previews',))
        if path in paths or path == source:
            fail('HDR outputs must have distinct paths and preserve the source')
        paths.add(path)
        return path

    def branch():
        expect(['load', str(source)])
        chain = []
        while position < len(steps) and steps[position][0] in TRANSFORMS:
            row = take(steps[position][0])
            validate_parameters(row, 'stretch')
            if row[0] == 'ght' and not all(any(p.startswith('-' + k + '=') for p in row[1:])
                                           for k in ('D', 'B', 'LP', 'SP', 'HP')):
                fail('HDR GHS requires all five numeric parameters explicitly')
            chain.append({'command': row[0], 'parameters': row[1:]})
        if not chain:
            fail('HDR branches require explicit MTF/Asinh/GHS transfers')
        expect(['icc_assign', 'sRGB'])
        return chain, output('save'), output('savejpg')

    gentle_chain, gentle, gentle_preview = branch()
    luminance = gentle
    split_paths = []
    if channels == 3:
        row = take('split')
        if len(row) != 4:
            fail('HDR luminance requires three gentle RGB channels')
        split_paths = [_path(v, session) for v in row[1:]]
        for path in split_paths:
            session_path(session, path, allowed_roots=('artifacts',))
            if path in paths or path == source:
                fail('HDR split outputs must have distinct paths')
            paths.add(path)
        r, g, b = [_variable(p, session) for p in split_paths]
        expect(['pm', f'0.2126 * {r} + 0.7152 * {g} + 0.0722 * {b}', '-nosum'])
        luminance = output('save')
    faint_chain, faint, faint_preview = branch()
    low, high = parameters['mask_low'], parameters['mask_high']
    t = f'min(1, max(0, ({_variable(luminance, session)} - {low}) / {high-low}))'
    expect(['pm', f'{t}^2 * (3 - 2 * {t})', '-nosum'])
    expect(['gauss', str(parameters['sigma'])])
    expect(['icc_assign', 'sRGB'])
    mask = output('save')
    mask_preview = output('savejpg')
    blend_mask = mask
    if channels == 3:
        row = take('rgbcomp')
        if len(row) != 6 or row[1:4] != [str(mask)] * 3 or not row[4].startswith('-out=') or row[5] != '-nosum':
            fail('HDR RGB mask must copy the same monochrome mask to all three channels')
        blend_mask = _path(row[4][5:], session)
        session_path(session, blend_mask, allowed_roots=('artifacts',))
        if blend_mask in paths or blend_mask == source:
            fail('HDR RGB mask must use a distinct output path')
        paths.add(blend_mask)
    expect(['pm', blend_expression(gentle, faint, blend_mask, session), '-nosum'])
    expect(['icc_assign', 'sRGB'])
    if output('save') != primary:
        fail('HDR blended image must be the declared primary output')
    final_preview = output('savejpg')
    from deep_sky_siril_stretch_v2 import protection_binding
    protection = protection_binding(session, source, text, verify_parent=verify_parent)
    full_control = None
    if protection and protection['full_source']:
        full = session/protection['full_source']['path']
        full_layers = []
        for chain in (gentle_chain, faint_chain):
            expect(['load', str(full)])
            for step in chain:
                expect([step['command'], *step['parameters']])
            full_layers.append(output('save'))
        expect(['pm', blend_expression(*full_layers, blend_mask, session), '-nosum'])
        mixed = output('save')
        expect(['icc_assign', 'sRGB'])
        full_preview = output('savejpg')
        if full in paths:
            fail('HDR full-source control must preserve the original full source')
        full_control = dict(zip(('gentle', 'faint', 'mixed', 'preview'),
                                (p.relative_to(session).as_posix() for p in (*full_layers, mixed, full_preview))))
    if position != len(steps):
        fail('HDR stretch contains undeclared operations')
    relative = lambda p: p.relative_to(session).as_posix()
    stepwise = [line.split(':', 1)[1].strip() for line in text.splitlines() if line.strip().startswith('# stepwise-stretch:')]
    if len(stepwise) > 1:
        fail('Duplicate stepwise HDR completion declarations')
    from deep_sky_siril_stretch import strict_json_bytes
    final = {'stepwise_parameters': strict_json_bytes(stepwise[0].encode(), document='stepwise HDR completion')} if stepwise else {}
    return {**final, 'schema': 'starun-siril.hdr-transfer.v1', 'gentle_chain': gentle_chain, 'faint_chain': faint_chain,
            **({'protection': protection, 'full_control': full_control} if protection else {}),
            'mask': {**parameters, 'path': relative(mask), 'blend_path': relative(blend_mask),
                     'luminance': relative(luminance)},
            'layers': {'gentle': relative(gentle), 'faint': relative(faint),
                       'gentle_channels': list(map(relative, split_paths))},
            'previews': dict(zip(('gentle', 'faint', 'mask', 'candidate'),
                                map(relative, (gentle_preview, faint_preview, mask_preview, final_preview))))}


def mask_bindings(session, hdr, receipt):
    from deep_sky_siril_processing import fail
    records = {r['path']: r for r in receipt['outputs']}
    bindings = []
    for value in dict.fromkeys((hdr['mask']['path'], hdr['mask']['blend_path'])):
        path = session_path(session, value, must_exist=True, allowed_roots=('artifacts',))
        current = {'path': value, **fingerprint(path, include_path=False)}
        if records.get(value) != current:
            fail('HDR mask differs from the frozen stretch output fingerprint')
        bindings.append(current)
    return bindings


def validate_recomposition(session, commands, source, original, starless, primary, hdr):
    from deep_sky_siril_processing import TRANSFORMS, _path, _variable, fail, finite_number, validate_parameters
    steps = [t for t in commands if t[0] not in {'requires', 'set32bits', 'stat', 'close', 'savejpg'}]
    saves = [t for t in steps if t[0] == 'save']
    if len(saves) != 8:
        fail('HDR recomposition requires eight scientific outputs')
    paths = [_path(t[1], session) for t in saves]
    if len(set(paths)) != 8 or paths[-1] != primary:
        fail('HDR recomposition output roles are invalid')
    mask = session / hdr['mask']['blend_path']
    expected, pairs = [], []
    for offset, parent in ((0, original), (3, starless)):
        g, f, mixed = paths[offset:offset+3]
        for label, save in (('gentle_chain', saves[offset]), ('faint_chain', saves[offset+1])):
            chain = hdr[label]
            if not chain:
                fail('HDR receipt requires both complete transfer chains')
            for c in chain:
                if c['command'] not in TRANSFORMS:
                    fail('HDR replay permits only explicit MTF/Asinh/GHS')
                validate_parameters([c['command'], *c['parameters']], 'stretch')
            expected += [['load', str(parent)], *[[c['command'], *c['parameters']] for c in chain], save]
        expected += [['pm', blend_expression(g, f, mask, session), '-nosum'], saves[offset+2]]
        pairs.append({k: p.relative_to(session).as_posix() for k, p in zip(('gentle', 'faint', 'mixed'), (g, f, mixed))})
    full, base, stars, final = paths[2], paths[5], paths[6], paths[7]
    expected += [['pm', f'{_variable(full, session)} - {_variable(base, session)}', '-nosum'], saves[6]]
    if steps[:len(expected)] != expected or len(steps) != len(expected) + 2 or steps[-1] != saves[7]:
        fail('HDR recomposition must replay both frozen chains with the same frozen mask')
    row = steps[-2]
    prefix, suffix = f'{_variable(source, session)} + ', f' * {_variable(stars, session)}'
    if (len(row) != 3 or row[0] != 'pm' or row[2] != '-nosum'
        or not row[1].startswith(prefix) or not row[1].endswith(suffix)):
        fail('HDR recomposition requires signed additive stars without clipping or rescaling')
    strength = finite_number(row[1][len(prefix):-len(suffix)])
    if not .30 <= strength <= 1:
        fail('Star strength must be within [0.30, 1.00]')
    return ({k: p.relative_to(session).as_posix() for k, p in
             (('full_baseline', full), ('original_starless', base), ('display_stars', stars))} | {'strength': strength}, pairs)


def measure_outputs(session, primary, metadata, *, source=None, write_diagnostics=False):
    from deep_sky_siril_processing import fail
    try:
        import numpy as np
    except ImportError:
        fail('NumPy is required for read-only HDR verification; no installation is performed')
    hdr = metadata['hdr_transfer']
    for binding in metadata.get('hdr_mask_bindings', []):
        path = session_path(session, binding['path'], must_exist=True, allowed_roots=('artifacts',))
        if binding != {'path': binding['path'], **fingerprint(path, include_path=False)}:
            fail('HDR mask changed during recomposition')
    mask = session_path(session, hdr['mask']['path'], must_exist=True, allowed_roots=('artifacts',))
    blend = session_path(session, hdr['mask']['blend_path'], must_exist=True, allowed_roots=('artifacts',))
    m, ms, mz = read_fits_pixels(mask)
    b, bs, bz = read_fits_pixels(blend)
    if m.shape[0] != 1 or b.shape[0] not in (1, 3) or m.shape[1:] != b.shape[1:]:
        fail('HDR mask geometry must match the scientific layers')
    pairs = metadata.get('hdr_pairs') or [{**hdr['layers'], 'mixed': primary.relative_to(session).as_posix()}]
    if hdr.get('full_control') and not metadata.get('hdr_pairs'):
        pairs = [*pairs, hdr['full_control']]
    arrays = []
    for pair in pairs:
        loaded = [read_fits_pixels(session_path(session, pair[k], must_exist=True, allowed_roots=('artifacts',)))
                  for k in ('gentle', 'faint', 'mixed')]
        if any(a.shape != b.shape for a, _, _ in loaded):
            fail('HDR branches, blend mask and primary must have identical geometry')
        arrays.append(loaded)
    mask_error, errors, minimum, maximum = 0., [0.] * len(pairs), float('inf'), float('-inf')
    for start in range(0, m.shape[1], 256):
        sl = (slice(None), slice(start, start+256), slice(None))
        mono = np.asarray(m[sl], dtype=float) * ms + mz
        rgb = np.asarray(b[sl], dtype=float) * bs + bz
        if not np.isfinite(mono).all() or not np.isfinite(rgb).all():
            fail('HDR masks require finite scientific pixels')
        minimum, maximum = min(minimum, float(mono.min())), max(maximum, float(mono.max()))
        mask_error = max(mask_error, float(np.max(np.abs(rgb - mono))))
        for i, loaded in enumerate(arrays):
            g, f, y = [np.asarray(a[sl], dtype=float) * s + z for a, s, z in loaded]
            if not all(np.isfinite(a).all() for a in (g, f, y)):
                fail('HDR layers require finite scientific pixels')
            errors[i] = max(errors[i], float(np.max(np.abs(y - (g * rgb + f * (1-rgb))))))
    if minimum < -1e-6 or maximum > 1+1e-6 or mask_error > 1e-6 or max(errors) > 1e-6:
        fail(f'HDR verification failed: mask range [{minimum}, {maximum}], copy error {mask_error}, '
             f'blend errors {errors}; tolerance is 1e-6')
    protection = {}
    if hdr.get('protection') and metadata.get('protocol') != 'stars.recompose' and not metadata.get('hdr_pairs'):
        from deep_sky_siril_stretch_v2 import measure_protection
        if source is None:
            from deep_sky_siril_contract import load_json
            source = (Path(load_json(session/'session.json')['input']['path']) if metadata.get('source_path') == '@input' else
                      session/metadata['source_path'])
        protection['protection'] = measure_protection(session, source, primary, hdr['protection'],
            session/hdr['full_control']['mixed'] if hdr['full_control'] else None)
        from deep_sky_siril_contract import load_json
        from deep_sky_siril_stretch import optimization_enabled, pixels
        if optimization_enabled(load_json(session/'session.json')):
            from deep_sky_siril_stretch_v2 import formal_highlight_overlay
            protection['highlight_overlay'] = formal_highlight_overlay(session, pixels(source), pixels(primary),
                session/hdr['previews']['candidate'], primary, write=write_diagnostics)
    return {'mask_min': minimum, 'mask_max': maximum, 'mask_copy_max_abs': mask_error, **protection,
            'blend_max_abs': errors, 'requires_visual_review': True}


def verify_receipt(session, payload, receipt):
    """Reparse native operations and remeasure HDR evidence during immutable replay."""
    from deep_sky_siril_contract import load_json, sha256_file
    from deep_sky_siril_processing import script_commands
    script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
    primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
    text = script.read_text(encoding='utf-8')
    source = (Path(payload['input']['path']) if receipt['source_path'] == '@input' else
              session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',)))
    hdr = receipt.get('hdr_transfer')
    if receipt['protocol'] == 'stretch':
        from deep_sky_siril_stretch_trials import enabled as archive_enabled, verify as verify_archive
        if hdr and archive_enabled(payload):
            verify_archive(session, receipt)
        declared = declaration(text)
        if declared is None:
            return hdr is None and not any(t[0] in {'split', 'pm', 'gauss', 'rgbcomp'} for t in script_commands(text))
        expected = validate_stretch(session, source, primary, text, verify_parent=False)
        from deep_sky_siril_stretch_profiles import routing_required, validate_external_strategy
        if routing_required(payload):
            from deep_sky_siril_stretch import strict_json_bytes
            declarations = [line.split(':', 1)[1] for line in text.splitlines() if line.strip().startswith('# stretch-strategy:')]
            if len(declarations) != 1:
                return False
            strategy = strict_json_bytes(declarations[0].encode(), document='HDR strategy binding')
            validate_external_strategy(session, payload, source, strategy, hdr=expected)
            expected['strategy'] = strategy
        if hdr and hdr.get('protection') and expected.get('protection'):
            from deep_sky_siril_stretch_v2 import implementation_matches
            frozen = hdr['protection'].get('implementation')
            if not implementation_matches(session, frozen):
                return False
            expected['protection']['implementation'] = frozen
        if hdr != expected or receipt.get('transfer_chain') != []:
            return False
    else:
        branch = receipt['branch_binding']
        stretch_path = session_path(session, 'runs/' + branch['stretch_run'] + '.json', must_exist=True, allowed_roots=('runs',))
        stretch = load_json(stretch_path)
        if not hdr and not stretch.get('hdr_transfer'):
            return True
        if (hdr != stretch.get('hdr_transfer') or receipt.get('transfer_chain') != []
            or sha256_file(stretch_path) != branch['stretch_receipt_sha256']
            or receipt.get('hdr_mask_bindings') != mask_bindings(session, hdr, stretch)):
            return False
        sep_path = session_path(session, 'runs/' + branch['separation_run'] + '.json', must_exist=True, allowed_roots=('runs',))
        sep = load_json(sep_path)
        if sha256_file(sep_path) != branch['separation_receipt_sha256']:
            return False
        original = Path(payload['input']['path']) if sep['source_path'] == '@input' else session / sep['source_path']
        if branch.get('contour_stars'):
            from deep_sky_star_reduction import validate_contour_recomposition
            roles, pairs = validate_contour_recomposition(session, payload, script_commands(text), text,
                source, original, session/sep['primary_output'], primary, stretch, receipt['id'])
        else:
            roles, pairs = validate_recomposition(session, script_commands(text), source, original,
                                                  session / sep['primary_output'], primary, hdr)
        if any(branch.get(k) != v for k, v in roles.items()) or receipt.get('hdr_pairs') != pairs:
            return False
    return receipt.get('processing_checks', {}).get('hdr') == measure_outputs(session, primary, receipt, source=source)


def protection_review_paths(session, hdr, receipt=None):
    if not hdr.get('protection'):
        return {}
    from deep_sky_siril_stretch import bound_file
    paths = {item['path']: bound_file(session, item) for item in
             (hdr['protection']['request'], hdr['protection']['regions'])}
    if hdr.get('full_control'):
        path = hdr['full_control']['preview']
        paths[path] = session_path(session, path, must_exist=True, allowed_roots=('previews',))
    overlay = (receipt or {}).get('processing_checks', {}).get('hdr', {}).get('highlight_overlay')
    if overlay:
        paths[overlay['path']] = bound_file(session, overlay)
    return paths

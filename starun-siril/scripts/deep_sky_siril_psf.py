"""Read-only PSF provenance and matched native star diagnostics, never acceptance gates."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from deep_sky_siril_contract import ContractError, fingerprint, load_json, session_path, sha256_file
from siril_psf_samples import (LEGACY_POLICY, POLICY, RECEIPT_SCHEMA, VERSION, major_axes,
                               model_parameters, orientation_statistics, validate_contract, wrap_angle)


def prepare_deconvolution(session, source, primary, commands, *, policy=POLICY, run_id=None, restoration_policy=None):
    """Bind optional diagnostics; constrain the audited bridge's scientific source and outputs."""
    def option(row, name):
        values = [v.split('=', 1)[1] for v in row if v.startswith('-' + name + '=')]
        return values[0] if len(values) == 1 else None
    def report_path(value):
        return session_path(session, value, allowed_roots=('reports',)).relative_to(session).as_posix()
    binding = {'source_sha256': sha256_file(source), 'method': 'unbound_native',
               'parent_tsv': None, 'candidate_tsv': None, 'psf': None}
    if restoration_policy is not None:
        from deep_sky_siril_processing import RESTORATION_POLICY
        if restoration_policy != RESTORATION_POLICY:
            raise ContractError('processing_contract_invalid', 'Unknown frozen restoration policy')
        binding['restoration_policy'] = restoration_policy
    if policy not in (LEGACY_POLICY, POLICY):
        raise ContractError('processing_contract_invalid', 'Unsupported PSF provenance policy')
    if policy == POLICY:
        report_root = Path('reports') / (run_id or primary.stem)
        binding.update(policy=policy, spatial_reports={
            'json': report_path(str(report_root / 'psf-spatial.json')),
            'markdown': report_path(str(report_root / 'psf-spatial.md'))})
    rl_index = next((i for i, row in enumerate(commands) if row[0] == 'rl'), len(commands))
    for i, row in enumerate(commands):
        if row[0] == 'findstar' and option(row, 'out'):
            role = 'parent_tsv' if i < rl_index else 'candidate_tsv'
            binding[role] = report_path(option(row, 'out'))
        if row[0] == 'makepsf' and option(row, 'savepsf'):
            binding['psf'] = report_path(option(row, 'savepsf'))
    bridges = [i for i, row in enumerate(commands) if row[0] == 'pyscript']
    if bridges:
        if len(bridges) != 1:
            raise ContractError('processing_contract_invalid', 'Deconvolution requires one PSF sample bridge')
        i = bridges[0]
        args = dict(zip(commands[i][2::2], commands[i][3::2]))
        contract_path = session_path(session, args['--contract'], must_exist=True, allowed_roots=('reports',))
        document = validate_contract(load_json(contract_path), source, contract_path.parent)
        if restoration_policy:
            from deep_sky_siril_artifacts import read_scientific_header
            header = read_scientific_header(source)
            if any(document['source'][key] != value for key, value in (
                    ('width', header['NAXIS1']), ('height', header['NAXIS2']), ('channels', header.get('NAXIS3', 1)))):
                raise ContractError('processing_contract_invalid', 'PSF contract geometry differs from the scientific parent')
        psf_path = Path(document['psf']['path']).resolve()
        lead = [['load', str(source)]] + ([['fmul', '1']] if restoration_policy else [])
        if (i < len(lead)+1 or commands[i-len(lead)-1:i-1] != lead
            or commands[i - 1] != ['setfindstar', *document['detection']['setfindstar']]
            or i + 1 >= len(commands) or commands[i + 1][0] != 'rl'
            or option(commands[i + 1], 'loadpsf') is None
            or Path(option(commands[i + 1], 'loadpsf')).resolve() != psf_path
            or any(row[0] in ('findstar', 'makepsf') for row in commands[:i])):
            raise ContractError('processing_contract_invalid', 'PSF bridge must follow unchanged source/settings and immediately precede RL with its kernel')
        binding.update(method='sample_derived_manual', contract=report_path(str(contract_path)),
                       contract_sha256=sha256_file(contract_path), bridge_receipt=report_path(args['--receipt']),
                       parent_tsv=report_path(document['stars_tsv']), psf=report_path(str(psf_path)))
    if binding['candidate_tsv']:
        post = next(i for i, row in enumerate(commands) if row[0] == 'findstar'
                    and option(row, 'out') and report_path(option(row, 'out')) == binding['candidate_tsv'])
        last_load = next((row for row in reversed(commands[:post]) if row[0] == 'load'), None)
        from deep_sky_siril_validation import _normalized_write
        saved = any(row[0] == 'save' and _normalized_write('save', row[1]) == primary for row in commands[:post])
        if not primary or not saved or last_load != ['load', str(primary)]:
            # Legacy scripts remain executable; their table cannot claim candidate measurements.
            binding['candidate_table_reason'] = 'Post table was not detected from the saved, reloaded scientific candidate'
        if bridges:
            expected_settings = ['setfindstar', *document['detection']['setfindstar']]
            row = commands[post]
            if (post < 2 or commands[post - 2] != ['load', str(primary)] or commands[post - 1] != expected_settings
                or option(row, 'layer') != str(document['detection']['layer'])
                or option(row, 'maxstars') != str(document['detection']['maxstars'])):
                binding['candidate_table_reason'] = 'Candidate detection source, domain or explicit settings differ'
        elif binding['parent_tsv']:
            pre = next(i for i, row in enumerate(commands) if row[0] == 'findstar'
                       and option(row, 'out') and report_path(option(row, 'out')) == binding['parent_tsv'])
            before_settings = next((row for row in reversed(commands[:pre]) if row[0] == 'setfindstar'), None)
            after_settings = next((row for row in reversed(commands[rl_index:post]) if row[0] == 'setfindstar'), None)
            lead = [['load', str(source)]] + ([['fmul', '1']] if restoration_policy else [])
            if (pre < len(lead)+1 or commands[pre-len(lead)-1:pre-1] != lead
                or before_settings is None or before_settings[1:2] != ['reset']
                or commands[pre - 1] != before_settings or after_settings != before_settings
                or any(option(commands[pre], key) != option(commands[post], key) for key in ('layer', 'maxstars'))):
                binding['candidate_table_reason'] = 'Parent and candidate detection sources or explicit settings are not comparable'
            if policy == POLICY and (post < 2 or commands[post - 2] != ['load', str(primary)]
                                     or commands[post - 1] != before_settings):
                binding['candidate_table_reason'] = 'Candidate detection domain is not the saved, reloaded scientific image'
    if restoration_policy:
        if (any(binding[role] is None for role in ('parent_tsv', 'candidate_tsv', 'psf'))
            or rl_index == len(commands) or option(commands[rl_index], 'loadpsf') is None
            or report_path(option(commands[rl_index], 'loadpsf')) != binding['psf']
            or binding.get('candidate_table_reason')):
            raise ContractError('processing_contract_invalid', 'Stage 5 requires a bound PSF and comparable parent/candidate star tables')
    return binding


def parse_native_stars(path, *, spatial=False):
    """Parse pixel FWHM and native Siril star coordinates without permissive inference."""
    report = {'status': 'measured', 'coordinate_frame': 'native_siril_star_coordinates', 'units': 'px',
              'settings': {}, 'rows': [], 'invalid_rows': [], 'duplicate_count': 0}
    header = None
    required = {'star#', 'layer', 'a', 'beta', 'x', 'y', 'fwhmx [px]', 'fwhmy [px]', 'angle', 'sat', 'profile'}
    seen_ids, seen_positions = set(), set()
    for number, raw in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        text = raw.strip()
        if not text:
            continue
        if text.startswith('#'):
            fields = [v.strip().lower() for v in text.lstrip('#').strip().split('\t')]
            if required <= set(fields):
                if header is not None or len(fields) != len(set(fields)):
                    return {**report, 'status': 'unsupported', 'reason': 'Repeated or duplicate native star header'}
                header = fields
            for key, value in re.findall(r'(\w+)=([^\s]+)', text):
                if key in report['settings']:
                    report['status'] = 'uncertain'
                try:
                    numeric = float(value)
                    if not math.isfinite(numeric):
                        raise ValueError('Nonfinite detection setting')
                    report['settings'][key] = numeric
                except ValueError:
                    report['status'] = 'uncertain'
            continue
        if header is None:
            return {**report, 'status': 'unsupported', 'reason': 'Missing native pixel-FWHM star header'}
        values = {}
        try:
            parts = text.split('\t')
            if len(parts) != len(header):
                raise ValueError('Column count differs from the native header')
            values = dict(zip(header, (v.strip() for v in parts)))
            row = {'line_number': number, 'id': int(values['star#']), 'layer': int(values['layer']),
                   'profile': values['profile'], 'has_saturated': bool(int(values['sat']))}
            if values['sat'] not in ('0', '1') or row['profile'] not in ('Gaussian', 'Moffat'):
                raise ValueError('Unsupported native profile or saturation flag')
            for name, column in [('xpos', 'x'), ('ypos', 'y'), ('A', 'a'), ('beta', 'beta'),
                                 ('fwhmx', 'fwhmx [px]'), ('fwhmy', 'fwhmy [px]'), ('angle', 'angle')]:
                row[name] = float(values[column])
                if not math.isfinite(row[name]):
                    raise ValueError('Nonfinite star measurement')
            if (row['fwhmx'] <= 0 or row['fwhmy'] <= 0 or row['xpos'] < 0 or row['ypos'] < 0
                or row['id'] < 1 or row['layer'] not in (0, 1, 2)):
                raise ValueError('Invalid star measurement')
            position = (row['layer'], row['xpos'], row['ypos'])
            duplicate = row['id'] in seen_ids or position in seen_positions
            seen_ids.add(row['id']); seen_positions.add(position)
            row['duplicate'] = duplicate
            report['duplicate_count'] += int(duplicate)
            report['rows'].append(row)
        except (ValueError, KeyError) as exc:
            invalid = {'line_number': number, 'text': raw, 'reason': str(exc)}
            if spatial:
                for key, column, convert in [('xpos', 'x', float), ('ypos', 'y', float),
                                             ('layer', 'layer', int), ('profile', 'profile', str)]:
                    try:
                        value = convert(values[column])
                        if isinstance(value, str) or math.isfinite(value):
                            invalid[key] = value
                    except (ValueError, KeyError):
                        pass
            report['invalid_rows'].append(invalid)
    if header is None:
        report.update(status='unsupported', reason='Missing native pixel-FWHM star header')
    if not report['settings']:
        report.update(status='uncertain', reason='Native detection settings are missing')
    return report


def eligible_stars(table):
    rows = table['rows']
    return [r for r in rows if not r['has_saturated']
            and sum(q['id'] == r['id'] or (q['layer'], q['xpos'], q['ypos']) ==
                    (r['layer'], r['xpos'], r['ypos']) for q in rows) == 1]

def quantiles(values):
    ordered = sorted(values)
    measured = {}
    for label, fraction in [('p05', .05), ('median', .5), ('p95', .95)]:
        position = (len(ordered) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        weight = position - low
        measured[label] = ordered[low] * (1 - weight) + ordered[high] * weight
    return measured

def matched_fwhm(before, after, *, geometry_unchanged, policy=LEGACY_POLICY):
    result = {'status': 'uncertain', 'match_radius_px': 1.0, 'matching': 'bidirectional_unique_within_radius',
              'coordinate_frame': 'native_siril_star_coordinates', 'units': 'px', 'pairs': [],
              'requires_visual_review': True, 'reasons': [], 'summary': {}}
    if geometry_unchanged is not True:
        result['reasons'].append('Geometry or channel count is not confirmed unchanged')
    if before['status'] != 'measured' or after['status'] != 'measured':
        result['reasons'].append('Native star tables are not comparable')
    if before['settings'] != after['settings']:
        result['reasons'].append('Detection settings differ')
    result['invalid_counts'] = [len(table['invalid_rows']) for table in (before, after)]
    result['detected_counts'] = [len(table['rows']) + len(table['invalid_rows']) for table in (before, after)]
    result['duplicate_counts'] = [table['duplicate_count'] for table in (before, after)]
    result['saturated_counts'] = [sum(r['has_saturated'] for r in table['rows']) for table in (before, after)]
    if result['reasons']:
        return result
    # Exclude all occurrences of duplicated positions/IDs, including the first occurrence.
    pre, post = eligible_stars(before), eligible_stars(after)
    neighbours = [[j for j, b in enumerate(post) if a['layer'] == b['layer'] and a['profile'] == b['profile']
                   and math.hypot(a['xpos'] - b['xpos'], a['ypos'] - b['ypos']) <= 1.0] for a in pre]
    reverse = [[i for i, candidates in enumerate(neighbours) if j in candidates] for j in range(len(post))]
    result['ambiguous_counts'] = [sum(len(v) > 1 for v in graph) for graph in (neighbours, reverse)]
    for i, options in enumerate(neighbours):
        if len(options) != 1 or len(reverse[options[0]]) != 1:
            continue
        a, b = pre[i], post[options[0]]
        measurements = {}
        for name, old, new in [('fwhmx', a['fwhmx'], b['fwhmx']), ('fwhmy', a['fwhmy'], b['fwhmy']),
                               ('fwhm_mean', a['fwhmx'] / 2 + a['fwhmy'] / 2, b['fwhmx'] / 2 + b['fwhmy'] / 2),
                               ('roundness', min(a['fwhmx'], a['fwhmy']) / max(a['fwhmx'], a['fwhmy']),
                                min(b['fwhmx'], b['fwhmy']) / max(b['fwhmx'], b['fwhmy']))]:
            if old <= 0:
                measurements = {}
                break
            measurements[name] = {'before': old, 'after': new, 'difference': new - old, 'ratio': new / old,
                                  'unit': 'dimensionless' if name == 'roundness' else 'px'}
        if not measurements or any(not math.isfinite(v) for values in measurements.values() for k, v in values.items() if k != 'unit'):
            result.setdefault('invalid_pair_count', 0)
            result['invalid_pair_count'] += 1
            continue
        if policy == POLICY:
            old_axes, new_axes = major_axes(a), major_axes(b)
            applicable = old_axes[0] != old_axes[1] and new_axes[0] != new_axes[1]
            measurements['direction'] = {'status': 'measured' if applicable else 'not_applicable',
                'before_deg': old_axes[2] if applicable else None, 'after_deg': new_axes[2] if applicable else None,
                'difference_deg': wrap_angle(new_axes[2] - old_axes[2]) if applicable else None, 'period_deg': 180}
        result['pairs'].append({'before_id': a['id'], 'after_id': b['id'],
                                'before_line': a['line_number'], 'after_line': b['line_number'],
                                'before_position': [a['xpos'], a['ypos']], 'after_position': [b['xpos'], b['ypos']],
                                'layer': a['layer'], 'profile': a['profile'], 'measurements': measurements})
    n = len(result['pairs'])
    result.update(matched_count=n, unmatched_counts=[len(rows) - n for rows in (pre, post)])
    if n:
        result['status'] = 'measured'
        for name in ('fwhmx', 'fwhmy', 'fwhm_mean', 'roundness'):
            result['summary'][name] = {field: quantiles([p['measurements'][name][field] for p in result['pairs']])
                for field in ('difference', 'ratio')}
    else:
        result['reasons'].append('No valid unique matched stars')
    return result



def _distribution(values):
    return quantiles(values) if values else None


def _cell(position, geometry):
    if not geometry or position is None:
        return None
    x, y = position
    w, h = geometry['width'], geometry['height']
    if not (0 <= x < w and 0 <= y < h):
        return None
    # Compare against the same exact fractional boundaries published in the report.
    col = sum(x >= w * i / 3 for i in (1, 2))
    row = sum(y >= h * i / 3 for i in (1, 2))
    return row * 3 + col


def _coverage(table, geometry, paired_lines):
    rows, invalid = table.get('rows', []), table.get('invalid_rows', [])
    valid = eligible_stars({'rows': rows})
    duplicates = [r for r in rows if sum(q['id'] == r['id'] or (q['layer'], q['xpos'], q['ypos']) ==
                         (r['layer'], r['xpos'], r['ypos']) for q in rows) > 1]
    cells = []
    for index in range(9):
        here = lambda r: _cell([r['xpos'], r['ypos']], geometry) == index if 'xpos' in r and 'ypos' in r else False
        captured = [r for r in [*rows, *invalid] if here(r)]
        measured = [r for r in valid if here(r)]
        groups = []
        keys = sorted({(r['layer'], r['profile']) for r in captured if 'layer' in r and 'profile' in r})
        counts = lambda subset: {'captured': len(subset),
            'valid': sum(r in valid for r in subset), 'saturated': sum(r.get('has_saturated', False) for r in subset),
            'duplicate': sum(r in duplicates for r in subset), 'invalid': sum(r in invalid for r in subset),
            'unmatched': sum(r in valid and r['line_number'] not in paired_lines for r in subset)}
        for layer, profile in keys:
            subset = [r for r in captured if r.get('layer') == layer and r.get('profile') == profile]
            usable = [r for r in measured if r['layer'] == layer and r['profile'] == profile]
            metrics = {name: _distribution([r[name] for r in usable]) for name in ('fwhmx', 'fwhmy')}
            metrics['axis_ratio'] = _distribution([major_axes(r)[0] / major_axes(r)[1] for r in usable])
            if profile == 'Moffat':
                metrics['beta'] = _distribution([r['beta'] for r in usable])
            groups.append({'layer': layer, 'profile': profile, 'counts': counts(subset), 'metrics': metrics,
                           'orientation': orientation_statistics(usable)})
        cells.append({'status': 'measured' if measured and table.get('status') == 'measured' else 'uncertain',
                      'counts': counts(captured), 'groups': groups})
    captured = [*rows, *invalid]
    return {'captured_count': len(captured),
            'unassigned_count': sum(_cell([r['xpos'], r['ypos']], geometry) is None
                                    if 'xpos' in r and 'ypos' in r else True for r in captured),
            'cells': cells}


def spatial_report(tables, geometries, evidence, binding):
    """Describe captured samples only; cell measurements never establish PSF suitability."""
    geometry = geometries.get('parent_tsv')
    matched = evidence['matched_fwhm']
    pairs = matched.get('pairs', [])
    pairs = pairs if isinstance(pairs, list) else []
    coverages = {role: _coverage(tables.get(role, {}), geometry,
                   {p['before_line' if role == 'parent_tsv' else 'after_line'] for p in pairs})
                 for role in ('parent_tsv', 'candidate_tsv')}
    cells = []
    for index in range(9):
        row, col = divmod(index, 3)
        selected = [p for p in pairs if _cell(p['before_position'], geometry) == index]
        groups = []
        for layer, profile in sorted({(p['layer'], p['profile']) for p in selected}):
            subset = [p for p in selected if (p['layer'], p['profile']) == (layer, profile)]
            metrics = {name: {field: _distribution([p['measurements'][name][field] for p in subset])
                             for field in ('before', 'after', 'difference', 'ratio')}
                       for name in ('fwhmx', 'fwhmy', 'fwhm_mean', 'roundness')}
            directions = []
            for pair in subset:
                a = next(r for r in tables['parent_tsv']['rows'] if r['line_number'] == pair['before_line'])
                b = next(r for r in tables['candidate_tsv']['rows'] if r['line_number'] == pair['after_line'])
                old, new = major_axes(a), major_axes(b)
                if old[0] != old[1] and new[0] != new[1]:
                    directions.append(wrap_angle(new[2] - old[2]))
            groups.append({'layer': layer, 'profile': profile, 'matched_count': len(subset), 'metrics': metrics,
                           'direction_change': {'status': 'measured' if directions else 'not_applicable',
                               'count': len(directions), 'period_deg': 180,
                               'absolute_difference_deg': _distribution([abs(v) for v in directions])}})
        cells.append({'id': index, 'row': row, 'column': col,
                      'bounds_px': {'x': [geometry['width'] * col / 3, geometry['width'] * (col + 1) / 3],
                                    'y': [geometry['height'] * row / 3, geometry['height'] * (row + 1) / 3]}
                                   if geometry else None,
                      'parent': coverages['parent_tsv']['cells'][index],
                      'candidate': coverages['candidate_tsv']['cells'][index],
                      'matched': {'status': 'measured' if selected and matched['status'] == 'measured' else 'uncertain',
                                  'count': len(selected), 'groups': groups,
                                  'cross_cell_moves': sum(_cell(p['after_position'], geometry) != index for p in selected)}})
    samples = evidence['psf_samples']
    capture = samples.get('capture', {})
    summary = {'captured_count': coverages['parent_tsv']['captured_count'],
               'native_detected_count': capture.get('native_detected_count'),
               'capture_limit': capture.get('maxstars', tables.get('parent_tsv', {}).get('settings', {}).get('max_stars')),
               'truncated': capture.get('truncated'),
               'kernel_sample_count': samples.get('included_count') if samples['status'] == 'verified' else None,
               'matched_count': len(pairs), 'occupied_cells': sum(c['parent']['counts']['captured'] > 0 for c in cells),
               'empty_cells': [c['id'] for c in cells if c['parent']['counts']['captured'] == 0],
               'unassigned_counts': {role: c['unassigned_count'] for role, c in coverages.items()},
               'cross_cell_moves': sum(c['matched']['cross_cell_moves'] for c in cells)}
    return {'schema': 'starun-siril.psf-spatial.v1', 'policy': POLICY,
            'status': 'measured' if geometry and pairs else 'uncertain', 'requires_visual_review': True,
            'bindings': {'images': evidence['images'], 'materials': evidence['materials']},
            'coordinate_frame': {'name': 'native_siril_star_coordinates', 'origin_px': [0, 0],
                'axes': 'Origin at the top-left image edge; X increases right, Y increases down; no preview transform',
                'units': 'px', 'interval': 'half-open [start, end)', 'grid': [3, 3],
                'geometry': geometry},
            'summary': summary, 'kernel': {'status': samples['status'], 'parameters': samples.get('parameters'),
                                           'orientation': samples.get('orientation')},
            'comparability': {'status': matched['status'], 'reasons': matched.get('reasons', []),
                              'settings': {role: table.get('settings', {}) for role, table in tables.items()},
                              'match_radius_px': 1.0, 'matching': 'bidirectional_unique_within_radius'},
            'cells': cells,
            'limitations': ['Only captured samples are described; native-order truncation can bias coverage.',
                            'Empty, incomparable and unpaired cells remain uncertain. Counts do not prove regional suitability.',
                            'Saturated and duplicate counts can overlap; valid excludes both. Unlocated invalid rows are unassigned.',
                            'No held-out star validation or spatially varying PSF is performed.']}


def spatial_markdown(report):
    summary = report['summary']
    lines = ['# PSF spatial coverage', '', f"Policy: {report['policy']}; status: {report['status']}.",
             'Native Siril X/Y, top-left origin (0, 0), X right / Y down, pixels, 3x3 half-open cells; no preview coordinate transform.',
             '', f"Captured: {summary['captured_count']}; native detected: {summary['native_detected_count']}; "
             f"limit: {summary['capture_limit']}; truncated: {summary['truncated']}; "
             f"verified kernel samples: {summary['kernel_sample_count']}; matches: {summary['matched_count']}.",
             '', '## Coverage', '',
             '| Cell | X / Y bounds | Parent captured / valid / saturated / duplicate / invalid / unmatched | Candidate counts | Matched / cross-cell |',
             '| --- | --- | --- | --- | --- |']
    for cell in report['cells']:
        counts = lambda role: ' / '.join(str(v) for v in cell[role]['counts'].values())
        lines.append(f"| {cell['id']} | {cell['bounds_px']} | {counts('parent')} | {counts('candidate')} | "
                     f"{cell['matched']['count']} / {cell['matched']['cross_cell_moves']} |")
    lines.extend(['', '## Regional measurements', '',
                  'Distributions below contain p05 / median / p95. Groups keep native detection layers and models separate.',
                  'Direction differences are folded at 180 degrees; only absolute differences are summarized. Circular stars have no direction change.',
                  ''])
    for cell in report['cells']:
        lines.extend([f"### Cell {cell['id']}", ''])
        for role in ('parent', 'candidate'):
            for group in cell[role]['groups']:
                lines.append(f"- {role}, layer {group['layer']}, {group['profile']}: "
                             f"counts {group['counts']}; metrics {group['metrics']}; orientation {group['orientation']}")
        for group in cell['matched']['groups']:
            lines.append(f"- Matched layer {group['layer']}, {group['profile']}, n={group['matched_count']}: "
                         f"{group['metrics']}; direction {group['direction_change']}")
        lines.append('')
    lines.extend(['## Bindings', '', '```json', json.dumps(report['bindings'], indent=2, sort_keys=True), '```', '',
                  '## Limitations', '', *('- ' + text for text in report['limitations']), ''])
    return '\n'.join(lines)


def bind_spatial_reports(session, binding, report, *, write):
    contents = {'json': json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n',
                'markdown': spatial_markdown(report)}
    records = {}
    for role, content in contents.items():
        relative = binding['spatial_reports'][role]
        path = session_path(session, relative, allowed_roots=('reports',))
        if write and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                stream.write(content)
        if path.read_bytes() != content.encode('utf-8'):
            raise ValueError(f'Frozen PSF spatial report differs: {relative}')
        records[role] = {'path': relative, **fingerprint(path, include_path=False)}
    return {'status': 'bound', 'reports': records}


def deconvolution_evidence_matches(actual, expected):
    actual = normalize_deconvolution_evidence(actual)
    expected = normalize_deconvolution_evidence(expected)
    if not isinstance(expected, dict) or expected.get('policy') != POLICY:
        return actual == expected
    # Report I/O failure does not reverse science success; adoption still requires bound reports.
    if expected.get('spatial_reports', {}).get('status') != 'bound':
        ignored = {'spatial_reports', 'report_warnings'}
        return {k: v for k, v in actual.items() if k not in ignored} == {
            k: v for k, v in expected.items() if k not in ignored}
    return actual == expected


def psf_log_evidence(text):
    evidence, redetected = [], False
    in_psf = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r'^\s*log:\s*', '', raw)
        command = re.search(r'(?:Running command:|running command)\s+(\w+)', line)
        if command:
            if command[1] == 'makepsf':
                in_psf = True
            elif command[1] not in ('makepsf', 'findstar'):
                in_psf = False
        if 'No stars detected. Finding suitable non-saturated stars' in line and in_psf:
            redetected = True
            evidence.append({'line_number': number, 'text': raw, 'kind': 'psf_redetection'})
        count = re.search(r'Found (\d+) (?:suitably bright, non-saturated stars|(?:Gaussian|Moffat) profile stars)', line)
        if count:
            evidence.append({'line_number': number, 'text': raw, 'kind': 'psf_detection' if in_psf else 'star_detection',
                             'count': int(count[1])})
    return {'redetected': redetected, 'findings': evidence}


def _rows_reference(count):
    """Bind bulk per-star rows by count; the rows themselves stay in hash-bound materials.

    A receipt must stay small and reviewable. Every per-star row is reproducible from the
    parent/candidate star tables already bound under `materials`, so the receipt records the
    measured count and points at those tables instead of inlining thousands of rows.
    """
    return {'count': count, 'rows_inline': False, 'source': 'materials.parent_tsv / materials.candidate_tsv',
            'requires_visual_review': True}


def normalize_deconvolution_evidence(evidence):
    """Collapse bulk per-star rows in either receipt shape to one comparable form.

    Receipts written before the bulk-row externalization inlined the rows twice. Older
    sessions must still replay and finalize, so both shapes reduce to the same compact
    reference before the immutable evidence is compared.
    """
    if not isinstance(evidence, dict):
        return evidence
    normalized = dict(evidence)
    samples = normalized.get('psf_samples')
    if isinstance(samples, dict) and isinstance(samples.get('captured_samples'), list):
        samples = {k: v for k, v in samples.items() if k != 'captured_samples'}
        rows = samples.get('samples')
        if isinstance(rows, list):
            samples['samples'] = _rows_reference(len(rows))
        normalized['psf_samples'] = samples
    matched = normalized.get('matched_fwhm')
    if isinstance(matched, dict) and isinstance(matched.get('pairs'), list):
        normalized['matched_fwhm'] = {**matched, 'pairs': _rows_reference(len(matched['pairs']))}
    return normalized


def measure_deconvolution(session, source, primary, binding, log_text, *, write_reports=False):
    """Missing science evidence is diagnostic; changed frozen bindings are integrity failures."""
    policy = binding.get('policy', LEGACY_POLICY)
    if policy not in (LEGACY_POLICY, POLICY):
        raise ContractError('processing_contract_invalid', 'Unsupported PSF provenance policy')
    result = {'policy': policy, 'status': 'uncertain', 'requires_visual_review': True,
              'images': {}, 'materials': {}, 'log_evidence': psf_log_evidence(log_text),
              'psf_samples': {'status': 'uncertain', 'reasons': ['Actual PSF samples are not confirmed']},
              'matched_fwhm': {'status': 'uncertain', 'reasons': ['Comparable star tables are unavailable']}}
    counts = [row['count'] for row in result['log_evidence']['findings'] if row['kind'] == 'psf_detection']
    result['psf_samples'].update(detected_count=counts[-1] if counts else None, samples=None)
    for role, path in [('parent', source), ('candidate', primary)]:
        if path and path.is_file():
            result['images'][role] = {'path': str(path), **fingerprint(path, include_path=False)}
    for role in ('parent_tsv', 'candidate_tsv', 'psf', 'bridge_receipt', 'contract'):
        relative = binding.get(role)
        if relative:
            path = session_path(session, relative, allowed_roots=('reports',))
            if path.is_file():
                result['materials'][role] = {'path': relative, **fingerprint(path, include_path=False)}
    def integrity_failure(message):
        from deep_sky_siril_processing import ProcessingEvidenceError
        result['integrity_reason'] = message
        raise ProcessingEvidenceError(message, {'deconvolution': result})
    if binding.get('contract'):
        contract_record = result['materials'].get('contract', {})
        if contract_record.get('sha256') != binding.get('contract_sha256'):
            integrity_failure('PSF sample contract changed during execution')
        if 'bridge_receipt' in result['materials']:
            try:
                receipt = load_json(session / binding['bridge_receipt'])
            except (ContractError, ValueError, OSError) as exc:
                integrity_failure(f'PSF sample receipt cannot be read: {exc}')
            if (not isinstance(receipt, dict) or receipt.get('contract_sha256') != binding['contract_sha256']
                or receipt.get('source') != load_json(session / binding['contract'])['source']):
                integrity_failure('PSF sample receipt source or contract binding differs')
    tables = {}
    for role in ('parent_tsv', 'candidate_tsv'):
        if role in result['materials']:
            try:
                tables[role] = parse_native_stars(session / binding[role], spatial=policy == POLICY)
            except (ValueError, UnicodeError, OSError) as exc:
                tables[role] = {'status': 'unsupported', 'settings': {}, 'rows': [], 'invalid_rows': [],
                                'duplicate_count': 0, 'reason': str(exc)}
    geometries = {}
    def geometry_for(path):
        if binding.get('restoration_policy'):
            from deep_sky_siril_artifacts import read_scientific_header
            header = read_scientific_header(path)
            return {'width': header['NAXIS1'], 'height': header['NAXIS2'], 'channels': header.get('NAXIS3', 1)}
        from deep_sky_siril_artifacts import fits_geometry
        return fits_geometry(path)
    if policy == POLICY:
        from deep_sky_siril_artifacts import fits_geometry
        for role, path in [('parent_tsv', source), ('candidate_tsv', primary)]:
            try:
                geometries[role] = geometry_for(path)
            except (ContractError, ValueError, OSError, TypeError, AttributeError):
                pass
    if set(tables) == {'parent_tsv', 'candidate_tsv'}:
        from deep_sky_siril_artifacts import fits_geometry
        geometry = None
        try:
            if primary is None:
                raise ValueError('Scientific candidate is unavailable')
            a, b = geometry_for(source), geometry_for(primary)
            geometry = all(a[k] == b[k] for k in ('width', 'height', 'channels'))
            for table, image in ((tables['parent_tsv'], a), (tables['candidate_tsv'], b)):
                invalid = [row for row in table['rows'] if row['layer'] >= image['channels']
                           or row['xpos'] >= image['width'] or row['ypos'] >= image['height']]
                table['invalid_rows'].extend({'line_number': row['line_number'],
                    'reason': 'Native star coordinate or layer is outside image geometry',
                    **({k: row[k] for k in ('xpos', 'ypos', 'layer', 'profile')} if policy == POLICY else {})}
                    for row in invalid)
                table['rows'] = [row for row in table['rows'] if row not in invalid]
        except (ContractError, ValueError, OSError):
            pass
        result['matched_fwhm'] = matched_fwhm(tables['parent_tsv'], tables['candidate_tsv'],
                                            geometry_unchanged=geometry if not binding.get('candidate_table_reason') else None, policy=policy)
        if binding.get('candidate_table_reason'):
            result['matched_fwhm']['reasons'].append(binding['candidate_table_reason'])
    if binding.get('bridge_receipt') and 'bridge_receipt' in result['materials']:
        sample = load_json(session / binding['bridge_receipt'])
        samples = result['psf_samples']
        samples['reasons'] = []
        if sample.get('schema') != RECEIPT_SCHEMA or sample.get('policy') != policy:
            integrity_failure('PSF sample receipt schema or policy differs')
        if sample.get('status') != 'sample_bound':
            reasons = sample.get('reasons')
            samples['reasons'].extend(reasons if isinstance(reasons, list) and all(isinstance(r, str) for r in reasons)
                                      else ['Native PSF samples unavailable'])
        if result['log_evidence']['redetected']:
            samples['reasons'].append('Native PSF automatically redetected stars')
        document = load_json(session / binding['contract'])
        if policy == POLICY:
            native_count = sample.get('native_detected_count')
            captured_count = sample.get('detected_count')
            confirmed_count = (type(native_count) is int and type(captured_count) is int
                               and native_count >= captured_count >= 0)
            samples['capture'] = {'native_detected_count': native_count if confirmed_count else None,
                                  'maxstars': document['detection']['maxstars'],
                                  'truncated': native_count > document['detection']['maxstars'] if confirmed_count else None}
        for key, expected in [('stars_tsv', document['stars_tsv']), ('psf', document['psf']['path'])]:
            role = 'parent_tsv' if key == 'stars_tsv' else 'psf'
            supplied = sample.get(key)
            if (not isinstance(supplied, dict) or supplied.get('path') != expected
                or supplied.get('sha256') != result['materials'].get(role, {}).get('sha256')):
                integrity_failure('PSF sample receipt artifact binding differs')
        if sample.get('sirilpy_version') != VERSION:
            samples['reasons'].append('Native model runtime version is not confirmed')
        if 'psf' not in result['materials']:
            samples['reasons'].append('Native PSF output is unavailable')
        try:
            records = sample['stars']
            if not isinstance(records, list) or len(records) > document['detection']['maxstars']:
                raise ValueError('Captured PSF model list is invalid')
            table = tables.get('parent_tsv', {})
            rows = table.get('rows', [])
            from siril_psf_samples import detection_settings
            if table.get('settings') != detection_settings(document['detection']):
                raise ValueError('Captured model detection settings differ from the contract')
            if len(records) != len(rows) or table.get('status') != 'measured' or table.get('invalid_rows'):
                raise ValueError('Native model table and captured samples differ')
            for a, b in zip(records, rows):
                if (any(a[k] != b[k] for k in ('id', 'layer', 'has_saturated', 'xpos', 'ypos', 'A', 'beta', 'fwhmx', 'fwhmy', 'angle'))
                    or a['profile'] != (0 if b['profile'] == 'Gaussian' else 1)
                    or a['included_in_psf'] is not (not a['has_saturated'])
                    or a['exclusion_reason'] != ('saturated' if a['has_saturated'] else None)):
                    raise ValueError('Native model table and captured samples differ')
            parameters = model_parameters(records, symmetric=document['psf']['symmetric'], policy=policy)
            if (sample['psf']['parameters'] != parameters or sample['psf']['method'] != 'sample_derived_manual'
                or sample['detection'] != document['detection']
                or sample['detected_count'] != len(records)
                or sample['included_count'] != sum(r['included_in_psf'] for r in records)
                or sample['excluded_count'] != sum(not r['included_in_psf'] for r in records)):
                raise ValueError('Native PSF derived parameters or sample counts differ')
            expected_command = ['makepsf', 'manual', '-' + parameters['profile']]
            expected_command.extend(f'-{k}={v:.17g}' for k, v in parameters.items() if k != 'profile')
            expected_command.extend([f"-ks={document['psf']['kernel_size']}", f'"-savepsf={document["psf"]["path"]}"'])
            if (sample['psf'].get('native_command') != expected_command
                or sample.get('selection') != 'first_maxstars_in_native_order'
                or type(sample.get('native_detected_count')) is not int
                or sample['native_detected_count'] < len(records)):
                raise ValueError('Native PSF command or selection evidence differs')
            samples.update(samples=_rows_reference(len(records)), detected_count=len(records), included_count=sample['included_count'],
                           excluded_count=sample['excluded_count'], parameters=parameters,
                           method='sample_derived_manual', selection=sample.get('selection'))
            if sample['included_count'] < 20:
                samples['warnings'] = ['Fewer than 20 non-saturated PSF samples; the existing protocol review still applies']
        except (KeyError, ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            samples['reasons'].append(str(exc))
        if not samples['reasons']:
            samples['status'] = 'verified'
            if policy == POLICY:
                samples['orientation'] = orientation_statistics([r for r in records if r['included_in_psf']],
                                                                symmetric=document['psf']['symmetric'])
    if result['psf_samples']['status'] == 'verified' and result['matched_fwhm']['status'] == 'measured':
        result['status'] = 'measured'
    result['detection'] = {role: {k: v for k, v in table.items() if k != 'rows'} for role, table in tables.items()}
    # Each table's original bytes are bound even when some rows cannot be measured.
    try:
        unchanged = all(sha256_file(session / record['path']) == record['sha256'] for record in result['materials'].values())
        unchanged = unchanged and all(sha256_file(Path(record['path'])) == record['sha256'] for record in result['images'].values())
    except (ContractError, OSError):
        unchanged = False
    if not unchanged:
        integrity_failure('PSF diagnostic material changed during measurement')
    if policy == POLICY:
        try:
            spatial = spatial_report(tables, geometries, result, binding)
            result['spatial_summary'] = spatial['summary']
            result['spatial_reports'] = bind_spatial_reports(session, binding, spatial, write=write_reports)
        except Exception as exc:
            result['spatial_reports'] = {'status': 'unavailable', 'reports': {}}
            result['report_warnings'] = [f'PSF spatial report unavailable: {type(exc).__name__}: {exc}']
    # The measured summary is what a review cites; the per-pair rows stay reproducible from
    # the hash-bound star tables instead of bloating the immutable receipt.
    pairs = result['matched_fwhm'].get('pairs')
    if isinstance(pairs, list) and pairs:
        result['matched_fwhm']['pairs'] = _rows_reference(len(pairs))
    return result

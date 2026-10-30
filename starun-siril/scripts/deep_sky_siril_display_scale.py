#!/usr/bin/env python3
"""Build a display-only viewing-size comparison from already-rendered previews."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from deep_sky_siril_contract import (ContractError, RUN_ID_PATTERN, atomic_write_json,
    fingerprint, load_json, session_path, sha256_file)

SCHEMA = 'starun-siril.display-scale.v1'
DISPLAY_SUFFIXES = {'.jpg', '.jpeg', '.png'}
SCIENTIFIC_SUFFIXES = {'.fit', '.fits', '.fts', '.xisf', '.tif', '.tiff'}
PAD = 8
HEADER = 22
BACKGROUND = '#181818'
RESAMPLE = 'LANCZOS'
MIN_LONG_EDGE = 640
MAX_LONG_EDGE = 4096
MIN_SOURCE_EDGE = 16


def fail(message):
    raise ContractError('display_scale_invalid', message)


def fit(width, height, long_edge):
    """Return the downscaled size and scale; never upscale."""
    if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
        fail('Display-scale inputs need positive integer dimensions')
    if not isinstance(long_edge, int) or not MIN_LONG_EDGE <= long_edge <= MAX_LONG_EDGE:
        fail(f'Display-scale long edge must be an integer in [{MIN_LONG_EDGE}, {MAX_LONG_EDGE}]')
    scale = min(1., long_edge/max(width, height))
    return max(1, round(width*scale)), max(1, round(height*scale)), scale


def load(session, value):
    path = session_path(session, value, must_exist=True, allowed_roots=('previews', 'reports', 'artifacts'))
    suffix = path.suffix.lower()
    if suffix in SCIENTIFIC_SUFFIXES:
        fail('Display-scale reads rendered JPEG/PNG previews only; scientific formats are not accepted')
    if suffix not in DISPLAY_SUFFIXES:
        fail('Display-scale inputs must be .jpg, .jpeg or .png')
    try:
        with Image.open(path) as opened:
            image_format = opened.format
            mode = opened.mode
            orientation = opened.getexif().get(0x0112, 1)
            if image_format not in {'JPEG', 'PNG'}:
                fail(f'Display-scale rejects {image_format or "unknown"} payloads renamed to a display suffix')
            image = ImageOps.exif_transpose(opened).copy()
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        fail(f'Cannot decode display-scale input {value}: {exc}')
    flattened = mode in {'RGBA', 'LA'} or 'transparency' in image.info
    if flattened:
        canvas = Image.new('RGBA', image.size, BACKGROUND)
        canvas.alpha_composite(image.convert('RGBA'))
        image = canvas.convert('RGB')
    else:
        image = image.convert('RGB')
    if min(image.size) < MIN_SOURCE_EDGE:
        fail(f'Display-scale input {value} is smaller than {MIN_SOURCE_EDGE} px on one side')
    relative = path.relative_to(session).as_posix()
    return {'path': relative, 'image': image, 'format': image_format, 'mode': mode,
            'size': image.size, 'alpha': flattened, 'orientation': orientation != 1,
            'binding': {'path': relative, **fingerprint(path, include_path=False)}}


def render(source, reference, long_edge):
    width, height = source['size']
    if reference is not None and reference['size'] != source['size']:
        fail('Display-scale reference and candidate must share identical pixel dimensions')
    target_width, target_height, scale = fit(width, height, long_edge)
    columns = ([('reference', reference)] if reference is not None else []) + [('candidate', source)]
    canvas = Image.new('RGB', (len(columns)*(target_width+PAD)+PAD, target_height+HEADER+2*PAD), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    for index, (label, item) in enumerate(columns):
        left = PAD+index*(target_width+PAD)
        draw.text((left, PAD), f'{label} (display {max(target_width, target_height)}px)', fill='white')
        canvas.paste(item['image'].resize((target_width, target_height), Image.LANCZOS), (left, PAD+HEADER))
    buffer = io.BytesIO()
    canvas.save(buffer, format='PNG', optimize=False, compress_level=6)
    return buffer.getvalue(), {'long_edge': long_edge, 'scale': float(scale),
        'display_size': [target_width, target_height],
        'output_size': [canvas.width, canvas.height], 'pad': PAD, 'header': HEADER,
        'resample': RESAMPLE, 'background': BACKGROUND,
        'labels': [label for label, _ in columns],
        'upscaled': False, 'flattened_alpha': source['alpha'] or bool(reference and reference['alpha']),
        'exif_orientation_applied': source['orientation'] or bool(reference and reference['orientation'])}


def prepare(session_value, source_value, reference_value, run_id, long_edge):
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        fail('Display-scale run id must match NNN-name')
    session = Path(session_value).resolve()
    load_json(session/'session.json')
    source = load(session, source_value)
    reference = load(session, reference_value) if reference_value else None
    content, render_record = render(source, reference, long_edge)
    directory = session/'reports'/run_id
    directory.mkdir(parents=True, exist_ok=True)
    output = directory/'display-scale.png'
    report_path = directory/'display-scale.json'
    receipt = {'schema': SCHEMA, 'run_id': run_id, 'display_only': True,
        'scientific_parent': False, 'scientific_attempts_added': 0,
        'source': source['binding'], 'reference': reference['binding'] if reference else None,
        'inputs': {'width': source['size'][0], 'height': source['size'][1],
                   'format': source['format'], 'mode': source['mode']},
        'output': {'path': output.relative_to(session).as_posix(),
                   'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
                   'width': render_record['output_size'][0], 'height': render_record['output_size'][1]},
        'render': render_record,
        'notes': 'Inherited display mapping; no stretch, no measurement, no ROI coordinates.'}
    if output.exists() or report_path.exists():
        if not (output.exists() and report_path.exists()):
            fail('Incomplete display-scale evidence; use a new run id')
        existing = load_json(report_path)
        if (existing.get('schema') != SCHEMA
            or {k: existing.get('output', {}).get(k) for k in ('sha256', 'size')}
               != {k: receipt['output'][k] for k in ('sha256', 'size')}
            or existing != receipt):
            fail('Display-scale evidence already exists with different bytes; use a new run id')
        if output.read_bytes() != content:
            fail('Display-scale PNG differs from its frozen receipt; use a new run id')
        return {'status': 'unchanged', 'output': receipt['output'], 'report': {'path': report_path.relative_to(session).as_posix(), **fingerprint(report_path, include_path=False)}}
    with output.open('xb') as stream:
        stream.write(content)
    atomic_write_json(report_path, receipt)
    if sha256_file(output) != receipt['output']['sha256']:
        fail('Display-scale PNG changed after writing')
    return {'status': 'prepared', 'output': receipt['output'],
            'report': {'path': report_path.relative_to(session).as_posix(), **fingerprint(report_path, include_path=False)}}


def retained_paths(session, payload=None):
    """Keep display-scale panels across finalize pruning."""
    session = Path(session).resolve()
    paths = set()
    for report_path in sorted((session/'reports').glob('*/display-scale.json')):
        report = load_json(report_path)
        if report.get('schema') != SCHEMA or report.get('display_only') is not True:
            fail(f'Malformed display-scale receipt: {report_path}')
        output = report.get('output') or {}
        relative = output.get('path')
        if not isinstance(relative, str):
            fail(f'Display-scale receipt has no output path: {report_path}')
        path = session_path(session, relative, must_exist=True, allowed_roots=('reports',))
        if fingerprint(path, include_path=False) != {k: output.get(k) for k in ('sha256', 'size')}:
            fail(f'Display-scale output changed after its receipt: {relative}')
        paths.add(relative)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare',))
    parser.add_argument('--session', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--reference')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--long-edge', type=int, default=1024)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.session, args.source, args.reference, args.run_id, args.long_edge),
                         allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'reason': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

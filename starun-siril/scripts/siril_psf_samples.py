#!/usr/bin/env python3
"""Bind native Siril star models to a sample-derived native manual PSF.

Only Siril detects/models stars. This bridge writes evidence, never image pixels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct

CONTRACT_SCHEMA = 'starun-siril.psf-samples-contract.v1'
RECEIPT_SCHEMA = 'starun-siril.psf-samples-receipt.v1'
LEGACY_POLICY = 'native_star_samples_v1'
POLICY = 'native_star_samples_v2'
VERSION = '1.0.25'
MODEL_FIELDS = ('xpos', 'ypos', 'layer', 'profile', 'fwhmx', 'fwhmy', 'A', 'beta', 'angle', 'has_saturated')


class PsfGenerationError(RuntimeError):
    """Native generation failed after capture; retain the measured evidence."""

    def __init__(self, evidence):
        super().__init__('Native PSF generation failed')
        self.evidence = evidence


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_contract(document, source, directory):
    if not isinstance(document, dict) or set(document) != {'schema', 'source', 'detection', 'stars_tsv', 'psf'} or document.get('schema') != CONTRACT_SCHEMA:
        raise ValueError('Unsupported PSF sample contract')
    binding, detection = document['source'], document['detection']
    if not isinstance(binding, dict) or set(binding) != {'path', 'sha256', 'width', 'height', 'channels'}:
        raise ValueError('Invalid PSF source binding')
    if (Path(binding['path']).expanduser().resolve() != source.resolve()
        or not isinstance(binding['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', binding['sha256'])
        or sha256_file(source) != binding['sha256']):
        raise ValueError('PSF sample source fingerprint changed or differs')
    if (any(type(binding[k]) is not int or binding[k] < 1 for k in ('width', 'height', 'channels'))
        or binding['channels'] not in (1, 3)):
        raise ValueError('Invalid PSF source geometry')
    if (not isinstance(detection, dict) or set(detection) != {'setfindstar', 'layer', 'maxstars'}
        or type(detection['layer']) is not int or not 0 <= detection['layer'] < binding['channels']
        or type(detection['maxstars']) is not int or not 20 <= detection['maxstars'] <= 500):
        raise ValueError('Invalid PSF detection layer or maximum star count')
    settings = detection['setfindstar']
    allowed = {'radius', 'sigma', 'roundness', 'focal', 'pixelsize', 'convergence', 'minbeta', 'minA', 'maxA', 'maxR'}
    if not isinstance(settings, list) or not settings or settings[0] != 'reset':
        raise ValueError('PSF detection settings require an explicit reset')
    seen = set()
    for token in settings[1:]:
        if token in ('-moffat', '-gaussian', '-relax=on', '-relax=off'):
            key = 'profile' if token in ('-moffat', '-gaussian') else 'relax'
        else:
            key, sep, value = str(token).lstrip('-').partition('=')
            if not isinstance(token, str) or not token.startswith('-') or not sep or key not in allowed:
                raise ValueError('Unsupported PSF detection setting')
            if not math.isfinite(float(value)):
                raise ValueError('PSF detection settings must be finite')
        if key in seen:
            raise ValueError('Duplicate PSF detection setting')
        seen.add(key)
    path = Path(document['stars_tsv']).expanduser()
    if (any(c in str(path) for c in '\r\n\x00"')
        or not path.is_absolute() or path.is_symlink() or path.resolve().parent != directory.resolve()
        or path.suffix.lower() != '.tsv'):
        raise ValueError('PSF star table must stay beside the sample contract')
    psf = document['psf']
    if (not isinstance(psf, dict) or set(psf) != {'path', 'kernel_size', 'symmetric'}
        or type(psf['kernel_size']) is not int or psf['kernel_size'] < 3 or psf['kernel_size'] % 2 != 1
        or psf['kernel_size'] > min(binding['width'], binding['height']) or type(psf['symmetric']) is not bool):
        raise ValueError('Invalid PSF kernel contract')
    target = Path(psf['path']).expanduser()
    if (any(c in str(target) for c in '\r\n\x00"')
        or not target.is_absolute() or target.is_symlink() or target.resolve().parent != directory.resolve()
        or target.suffix.lower() not in ('.fit', '.fits', '.fts')):
        raise ValueError('PSF output must stay beside the sample contract')
    return document


def serialize_stars(stars):
    records = []
    for index, star in enumerate(stars or []):
        record = {name: getattr(star, name) for name in MODEL_FIELDS}
        record['profile'] = int(record['profile'])
        if (type(record['has_saturated']) is not bool or type(record['layer']) is not int
            or any(isinstance(record[k], bool) or not math.isfinite(float(record[k]))
                   for k in MODEL_FIELDS if k not in ('has_saturated', 'layer', 'profile'))):
            raise ValueError('Native PSF models contain invalid or nonfinite fields')
        record.update(id=index + 1, included_in_psf=not record['has_saturated'],
                      exclusion_reason='saturated' if record['has_saturated'] else None)
        records.append(record)
    return records



class IndeterminateDirection(ValueError):
    """A noncircular kernel cannot be represented by an undetermined angle."""


def wrap_angle(angle):
    return (angle + 90.0) % 180.0 - 90.0


def major_axes(row):
    x, y, angle = row['fwhmx'], row['fwhmy'], row['angle']
    if not all(math.isfinite(v) for v in (x, y, angle)) or min(x, y) <= 0:
        raise ValueError('Invalid PSF axes or angle')
    return (x, y, wrap_angle(angle)) if x >= y else (y, x, wrap_angle(angle + 90.0))


def orientation_statistics(records, *, symmetric=False):
    axes = [major_axes(row) for row in records]
    weights = [(a - b) / (a + b) for a, b, _ in axes]
    w = math.fsum(weights)
    result = {'status': 'not_applicable', 'mean_angle_deg': None, 'concentration': None,
              'weight': w, 'sample_count': len(axes), 'period_deg': 180}
    if symmetric or w == 0:
        return result
    c = math.fsum(e * math.cos(math.radians(2 * angle)) for e, (_, _, angle) in zip(weights, axes))
    s = math.fsum(e * math.sin(math.radians(2 * angle)) for e, (_, _, angle) in zip(weights, axes))
    length = math.hypot(c, s)
    result.update(status='degenerate' if length <= 64 * math.ulp(1.0) * w else 'measured',
                  concentration=min(1.0, length / w))
    if result['status'] == 'measured':
        result['mean_angle_deg'] = wrap_angle(math.degrees(math.atan2(s, c)) / 2)
    return result


def model_parameters(records, *, symmetric=False, policy=POLICY):
    """Measure means in pixel units; Siril synthesizes and normalizes the kernel."""
    selected = [r for r in records if r['included_in_psf']]
    if not selected or len({(r['layer'], r['profile']) for r in records}) != 1:
        raise ValueError('PSF models require non-saturated stars on one layer and profile')
    f32 = lambda x: struct.unpack('f', struct.pack('f', x))[0]
    mean = lambda name: f32(f32(sum(r[name] for r in selected)) / f32(len(selected)))
    if policy not in (LEGACY_POLICY, POLICY):
        raise ValueError('Unsupported PSF provenance policy')
    if policy == POLICY:
        orientation = orientation_statistics(selected, symmetric=symmetric)
        if orientation['status'] == 'degenerate':
            raise IndeterminateDirection('Noncircular PSF orientation has a numerically degenerate resultant')
        selected = [{**r, 'fwhmx': major_axes(r)[0], 'fwhmy': major_axes(r)[1]} for r in selected]
        angle = orientation['mean_angle_deg'] if orientation['status'] == 'measured' else 0.0
    else:
        angle = mean('angle')
    x, y = mean('fwhmx'), mean('fwhmy')
    profile = records[0]['profile']
    params = {'profile': 'gaussian' if profile == 0 else 'moffat', 'fwhm': x,
              'ratio': 1.0 if symmetric else f32(x / y), 'angle': angle}
    if profile not in (0, 1):
        raise ValueError('Unsupported native PSF model profile')
    if profile == 1:
        params['beta'] = mean('beta')
    if (not all(math.isfinite(v) for k, v in params.items() if k != 'profile')
        or not 0 < x <= 100 or not 1 <= params['ratio'] <= 5 or not -360 < params['angle'] <= 360
        or (profile == 1 and not 0 < params['beta'] <= 10)):
        raise ValueError('Measured PSF parameters cannot be represented by native manual PSF')
    return params


def detection_settings(detection):
    # Frozen 1.4.4 reset values, also emitted in native findstar TSV headers.
    settings = dict(sigma=1, roundness=.5, radius=5, relax=0, profile=0, minbeta=1.5,
                    minA=0, maxA=1, maxR=1, layer=detection['layer'], max_stars=detection['maxstars'])
    for token in detection['setfindstar'][1:]:
        if token in ('-moffat', '-gaussian'):
            settings['profile'] = int(token == '-moffat')
        else:
            key, value = token[1:].split('=')
            if key in settings:
                settings[key] = int(value == 'on') if key == 'relax' else float(value)
    return settings


def export_models(path, records, detection):
    """Export full-precision native model evidence, not a new detection."""
    with path.open('x', encoding='utf-8') as stream:
        stream.write('# Native SirilPy model evidence; first maxstars in native order\n')
        stream.write('# ' + ' '.join(f'{k}={v}' for k, v in detection_settings(detection).items()) + '\n')
        stream.write('# star#\tlayer\tA\tbeta\tX\tY\tFWHMx [px]\tFWHMy [px]\tangle\tSat\tProfile\n')
        for row in records:
            values = [row[k] for k in ('id', 'layer', 'A', 'beta', 'xpos', 'ypos', 'fwhmx', 'fwhmy', 'angle')]
            stream.write('\t'.join(format(v, '.17g') for v in values)
                         + f"\t{int(row['has_saturated'])}\t{'Gaussian' if row['profile'] == 0 else 'Moffat'}\n")


def capture(siril, document, *, policy=POLICY):
    """One native model query followed by native PSF generation; no Python image writes."""
    if policy not in (LEGACY_POLICY, POLICY):
        raise ValueError('Unsupported PSF provenance policy')
    detection = document['detection']
    path = Path(document['stars_tsv'])
    psf_path = Path(document['psf']['path'])
    if path.exists() or psf_path.exists():
        raise ValueError('PSF star table or kernel already exists')
    result = {'schema': RECEIPT_SCHEMA, 'policy': policy, 'status': 'uncertain',
              'source': document['source'], 'detection': detection, 'stars': [],
              'detected_count': 0, 'included_count': 0, 'excluded_count': 0, 'reasons': []}
    parameters = None
    direction_failure = None
    try:
        native_models = siril.get_image_stars(channel=detection['layer']) or []
        if policy == POLICY:
            result['native_detected_count'] = len(native_models)
        records = serialize_stars(native_models[:detection['maxstars']])
        if not records:
            raise ValueError('No native star models available')
        result.update(stars=records, native_detected_count=len(native_models), detected_count=len(records),
                      selection='first_maxstars_in_native_order',
                      included_count=sum(r['included_in_psf'] for r in records),
                      excluded_count=sum(not r['included_in_psf'] for r in records))
        parameters = model_parameters(records, symmetric=document['psf']['symmetric'], policy=policy)
        result['status'] = 'sample_bound'
    except IndeterminateDirection as exc:
        direction_failure = exc
        result.update(status='failed', orientation=orientation_statistics(records))
        result['reasons'].append(str(exc))
    except Exception as exc:
        result['reasons'].append(f'Native star model capture unavailable: {type(exc).__name__}')
    export_models(path, result['stars'], detection)
    args = ['makepsf']
    if parameters is not None:
        args.extend(['manual', '-' + parameters['profile']])
        args.extend(f'-{k}={v:.17g}' for k, v in parameters.items() if k != 'profile')
    else:
        args.append('stars')
        if document['psf']['symmetric']:
            args.append('-sym')
    args.extend([f"-ks={document['psf']['kernel_size']}", f'"-savepsf={psf_path}"'])
    result.update(stars_tsv={'path': str(path), 'sha256': sha256_file(path)},
                  psf={'path': str(psf_path), 'sha256': None,
                       'method': 'sample_derived_manual' if parameters is not None else 'native_stars_fallback',
                       'parameters': parameters, 'native_command': args})
    if direction_failure:
        result['psf'].update(method='indeterminate_direction', native_command=None)
        raise PsfGenerationError(result) from direction_failure
    # Native processing failures retain evidence and still fail the script.
    try:
        siril.cmd(*args)
        result['psf']['sha256'] = sha256_file(psf_path)
    except Exception as exc:
        result['status'] = 'failed'
        result['reasons'].append(f'Native PSF generation failed: {type(exc).__name__}')
        raise PsfGenerationError(result) from exc
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('contract', 'contract-sha256', 'expected-source', 'receipt'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    if not re.fullmatch('[a-f0-9]{64}', args.contract_sha256) or sha256_file(contract_path) != args.contract_sha256:
        raise ValueError('PSF sample contract fingerprint changed')
    source = Path(args.expected_source).expanduser().resolve()
    document = validate_contract(json.loads(contract_path.read_text()), source, contract_path.parent)
    receipt = Path(args.receipt).expanduser()
    if receipt.is_symlink() or receipt.resolve().parent != contract_path.parent or receipt.exists():
        raise ValueError('PSF sample receipt is unsafe or already exists')
    import sirilpy
    if not sirilpy.check_module_version('==' + VERSION):
        raise RuntimeError('Unsupported SirilPy version for PSF sampling')
    siril = sirilpy.SirilInterface()
    siril.connect()
    if Path(siril.get_image_filename()).resolve() != source:
        raise ValueError('Loaded PSF source differs from the declared source')
    expected_shape = tuple(document['source'][k] for k in ('channels', 'height', 'width'))
    if tuple(siril.get_image_shape()) != expected_shape:
        raise ValueError('Loaded PSF source geometry differs')
    failure = None
    try:
        result = capture(siril, document, policy=POLICY)
    except PsfGenerationError as exc:
        result, failure = exc.evidence, exc
    if sha256_file(source) != document['source']['sha256'] or sha256_file(contract_path) != args.contract_sha256:
        raise ValueError('PSF source or contract changed during capture')
    result.update(contract_sha256=args.contract_sha256, sirilpy_version=str(sirilpy.__version__))
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, allow_nan=False, sort_keys=True)
        stream.write('\n')
    if failure is not None:
        raise failure
    siril.log(f"PSF sample provenance: {result['status']}; {result['included_count']} non-saturated models.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

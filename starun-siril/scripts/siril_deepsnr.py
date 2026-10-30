#!/usr/bin/env python3
"""Audited DeepSNR 1.3.2 linear FITS bridge; Siril writes all scientific pixels."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess

from deep_sky_siril_artifacts import read_scientific_pixels, fits_geometry, read_fits_header
from deep_sky_siril_contract import (
    atomic_write_json, fingerprint, fingerprint_matches, load_json, session_path,
    sha256_file, SCRIPTS_ROOT,
)
from deep_sky_siril_tooling import deepsnr_runtime_unchanged, _subprocess_environment
from siril_background_samples import verify_image_precision

CONTRACT_SCHEMA = 'starun-siril.deepsnr-contract.v1'
RECEIPT_SCHEMA = 'starun-siril.deepsnr-receipt.v1'


def read_native_fits(path):
    import numpy as np
    header = read_fits_header(path)
    values, _ = read_scientific_pixels(path)
    if (header.get('BITPIX') != -32 or header.get('BSCALE', 1) != 1 or header.get('BZERO', 0) != 0
        or not np.isfinite(values).all()):
        raise ValueError('DeepSNR requires finite, unscaled float32 FITS')
    return values, fits_geometry(path)


def pixel_digest(values):
    import numpy as np
    return hashlib.sha256(np.asarray(values, dtype='<f4').tobytes(order='C')).hexdigest()


def validate_parameters(parameters):
    if (not isinstance(parameters, dict) or set(parameters) != {'modulation', 'linear_stretch'}
        or type(parameters['modulation']) not in (float, int)
        or not math.isfinite(parameters['modulation']) or not 0 <= parameters['modulation'] <= 1):
        raise ValueError('DeepSNR requires explicit modulation in [0, 1] and linear_stretch')
    stretch = parameters['linear_stretch']
    if (not isinstance(stretch, dict) or set(stretch) != {'shadows_clipping', 'target_background'}
        or any(type(v) not in (float, int) or not math.isfinite(v) for v in stretch.values())
        or stretch['shadows_clipping'] >= 0 or not 0 < stretch['target_background'] < 1):
        raise ValueError('Invalid explicit DeepSNR linear MTF parameters')


def validate_contract(document, source, runtime, session, run_id, *, replaying=False):
    fields = {'schema', 'source', 'runtime', 'parameters', 'work_dir'}
    if not isinstance(document, dict) or set(document) != fields or document['schema'] != CONTRACT_SCHEMA:
        raise ValueError('Invalid DeepSNR contract')
    validate_parameters(document['parameters'])
    if document['parameters']['modulation'] == 0:
        raise ValueError('Zero modulation must skip DeepSNR without an identity run')
    if document['source'] != {'path': str(source), **fingerprint(source, include_path=False)}:
        raise ValueError('DeepSNR source fingerprint changed')
    if (document['runtime'] != runtime or not deepsnr_runtime_unchanged(runtime)
        or not replaying and runtime['adapter']['path'] != str(SCRIPTS_ROOT/'siril_deepsnr.py')):
        raise ValueError('Frozen DeepSNR runtime is unavailable or changed')
    work = session_path(session, document['work_dir'], allowed_roots=('runtime',))
    if work != session/'runtime/deepsnr'/run_id or (not replaying and work.exists()):
        raise ValueError('DeepSNR requires a new run-specific work directory')
    values, selection = read_scientific_pixels(source)
    import numpy as np
    if (values.shape[0] not in (1, 3) or min(values.shape[1:]) < 512
        or not np.isfinite(values).all() or selection.get('bounds') not in (None, [0., 1.])):
        raise ValueError('DeepSNR requires finite mono/RGB scientific pixels of at least 512x512')
    return work


def build_argv(document, work):
    stretch = document['parameters']['linear_stretch']
    return [document['runtime']['executable']['path'], '--input', str(work/'input.fit'),
            '--output', str(work/'denoised.fit'), '--weights', document['runtime']['model']['path'],
            '--linear', '--model', '2', '--stride', '480',
            '--shadows-clipping', str(stretch['shadows_clipping']),
            '--target-background', str(stretch['target_background'])]


def _same_pixels(actual, expected):
    import numpy as np
    tolerance = 2*np.abs(np.spacing(np.asarray(expected, dtype=np.float32))).astype(float)
    if actual.shape != expected.shape or not np.isfinite(actual).all() or np.any(np.abs(actual-expected) > tolerance):
        raise ValueError('DeepSNR handoff or modulation lost scientific pixel precision')


def above_one_region(values):
    import numpy as np
    y, x = np.nonzero(np.any(values > 1, axis=0))
    return ({'coordinate_system': 'display_top_down',
             'rect': [int(x.min()), int(values.shape[1]-1-y.max()), int(x.max()+1), int(values.shape[1]-y.min())]}
            if x.size else None)


def process(siril, document, work):
    import numpy as np
    source = Path(document['source']['path'])
    values, selection = read_scientific_pixels(source)
    work.mkdir(parents=True, exist_ok=False)
    prepared, native = work/'input.fit', work/'denoised.fit'
    siril.cmd('save', json.dumps(str(prepared)), '-chksum')
    exported, geometry = read_native_fits(prepared)
    _same_pixels(exported, values)
    input_precision = verify_image_precision(siril.get_image(), prepared)
    above = int(np.count_nonzero(exported > 1))
    if above:
        siril.log(f'Warning: DeepSNR will clip {above} samples above one (peak {float(exported.max()):.9g}); input is passed unchanged.')
    argv = build_argv(document, work)
    log = work/'native.log'
    try:
        result = subprocess.run(argv, cwd=work, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors='replace', check=False, timeout=1800,
                                env=_subprocess_environment('tool_version'))
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b''
        log.write_text(output.decode('utf-8', errors='replace') if isinstance(output, bytes) else output)
        raise ValueError(f'DeepSNR timed out; see {log}') from exc
    log.write_text(result.stdout, encoding='utf-8')
    siril.log(result.stdout)
    if result.returncode or not native.is_file() or native.is_symlink():
        raise ValueError(f'DeepSNR failed with exit code {result.returncode}; see {log}')
    denoised, output_geometry = read_native_fits(native)
    if geometry != output_geometry or {p.name for p in work.iterdir()} != {'input.fit', 'denoised.fit', 'native.log'}:
        raise ValueError('DeepSNR changed geometry/precision or wrote undeclared outputs')
    siril.cmd('load', json.dumps(str(native)))
    native_precision = verify_image_precision(siril.get_image(), native)
    modulation = document['parameters']['modulation']
    expression = f'{modulation:.17g} * ${native.with_suffix("")}$ + {1-modulation:.17g} * ${prepared.with_suffix("")}$'
    siril.cmd('pm', json.dumps(expression), '-nosum')
    mixed = siril.get_image()
    # SirilPy exposes top-down rows; FITS scientific readers retain bottom-up rows.
    actual = np.asarray(mixed.data)
    if actual.ndim == 2:
        actual = actual[None, ...]
    expected = (modulation*denoised+(1-modulation)*exported).astype(np.float32)
    _same_pixels(actual[:, ::-1, :] if getattr(mixed, 'top_down', False) else actual, expected)
    if (not deepsnr_runtime_unchanged(document['runtime'])
        or not fingerprint_matches(document['source']) or read_scientific_pixels(source)[1] != selection):
        raise ValueError('DeepSNR runtime or source changed during processing')
    return {'input_fits': fingerprint(prepared), 'native_output': fingerprint(native),
            'input_precision': input_precision, 'native_precision': native_precision,
            'mixed_pixels_sha256': pixel_digest(expected), 'argv': argv, 'log': fingerprint(log),
            'input_negative_count': int(np.count_nonzero(exported < 0)),
            'input_above_one_count': above, 'input_above_one_region': above_one_region(exported),
            'input_peak': float(exported.max())}


def validate_receipt(session, source, candidate, binding):
    import numpy as np
    document = load_json(session_path(session, binding['contract']['path'], must_exist=True, allowed_roots=('reports',)))
    if sha256_file(session/binding['contract']['path']) != binding['contract']['sha256']:
        raise ValueError('DeepSNR bridge contract changed')
    work = validate_contract(document, source, binding['runtime'], session, binding['run_id'], replaying=True)
    receipt_path = session_path(session, binding['bridge_receipt'], must_exist=True, allowed_roots=('reports',))
    receipt = load_json(receipt_path)
    fields = {'schema', 'status', 'contract_sha256', 'source', 'runtime', 'parameters',
              'input_fits', 'native_output', 'input_precision', 'native_precision', 'mixed_pixels_sha256',
              'argv', 'log', 'input_negative_count', 'input_above_one_count', 'input_above_one_region', 'input_peak'}
    if (set(receipt) != fields or receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('status') != 'verified'
        or receipt.get('contract_sha256') != binding['contract']['sha256']
        or receipt.get('source') != document['source'] or receipt.get('runtime') != document['runtime']
        or receipt.get('parameters') != document['parameters'] or receipt.get('argv') != build_argv(document, work)):
        raise ValueError('DeepSNR receipt differs from its frozen contract')
    for key, name in [('input_fits', 'input.fit'), ('native_output', 'denoised.fit'), ('log', 'native.log')]:
        if receipt.get(key) != fingerprint(work/name):
            raise ValueError('DeepSNR bridge evidence changed: '+key)
    a, geometry = read_native_fits(work/'input.fit')
    b, output_geometry = read_native_fits(work/'denoised.fit')
    for key, image in [('input_precision', 'input_fits'), ('native_precision', 'native_output')]:
        proof = receipt.get(key)
        if (not isinstance(proof, dict) or proof.get('status') != 'verified' or proof.get('bitpix') != -32
            or proof.get('dtype') != 'float32' or proof.get('shape') != list(a.shape)
            or proof.get('source_sha256') != receipt[image]['sha256']
            or proof.get('tolerance') != 'two_float32_ULP' or proof.get('endpoints_preserved') is not True):
            raise ValueError('DeepSNR native precision proof changed')
    original, _ = read_scientific_pixels(source)
    _same_pixels(a, original)
    actual, actual_geometry = read_native_fits(candidate)
    m = document['parameters']['modulation']
    expected = (m*b+(1-m)*a).astype(np.float32)
    _same_pixels(actual, expected)
    if geometry != output_geometry or geometry != actual_geometry or receipt.get('mixed_pixels_sha256') != pixel_digest(expected):
        raise ValueError('DeepSNR geometry or mixed pixels changed')
    if (receipt.get('input_negative_count') != int(np.count_nonzero(a < 0))
        or receipt.get('input_above_one_count') != int(np.count_nonzero(a > 1))
        or receipt.get('input_above_one_region') != above_one_region(a)
        or receipt.get('input_peak') != float(a.max())):
        raise ValueError('DeepSNR range diagnostics changed')
    return {'status': 'verified', 'receipt': fingerprint(receipt_path),
            'input_above_one_count': receipt['input_above_one_count'],
            'input_above_one_region': receipt['input_above_one_region'],
            'requires_visual_review': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--contract-sha256', required=True)
    parser.add_argument('--expected-source', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    contract, source, receipt = map(lambda p: Path(p).resolve(), (args.contract, args.expected_source, args.receipt))
    if receipt.exists() or receipt.parent != contract.parent or Path(args.receipt).is_symlink():
        raise ValueError('DeepSNR receipt must be new and beside its contract')
    if sha256_file(contract) != args.contract_sha256:
        raise ValueError('DeepSNR contract changed')
    document = load_json(contract)
    session = contract.parents[2]
    work = validate_contract(document, source, document['runtime'], session, contract.parent.name)
    import sirilpy
    if not sirilpy.check_module_version('==1.0.25'):
        raise RuntimeError('DeepSNR requires SirilPy 1.0.25')
    siril = sirilpy.SirilInterface()
    siril.connect()
    try:
        if Path(siril.get_image_filename()).resolve() != source:
            raise ValueError('Siril loaded image differs from the DeepSNR source')
        evidence = process(siril, document, work)
        if sha256_file(contract) != args.contract_sha256:
            raise ValueError('DeepSNR contract changed during processing')
        atomic_write_json(receipt, {'schema': RECEIPT_SCHEMA, 'status': 'verified',
            'contract_sha256': args.contract_sha256, 'source': document['source'],
            'runtime': document['runtime'], 'parameters': document['parameters'], **evidence})
    finally:
        siril.disconnect()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

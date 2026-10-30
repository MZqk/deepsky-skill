#!/usr/bin/env python3
# Copyright (C) 2026 MZqk
# SPDX-License-Identifier: GPL-3.0-or-later
"""Audited native StarNet2 2.6.2 linear FITS bridge; no Python pixel writes."""
from __future__ import annotations

import argparse
import hashlib
import math
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

import siril_starnet as legacy
from deep_sky_siril_artifacts import fits_geometry, read_fits_pixels
from deep_sky_siril_contract import (
    SCRIPTS_ROOT, SKILL_ROOT, atomic_write_json, fingerprint,
    session_path, sha256_file,
)
from siril_background_samples import verify_image_precision

CONTRACT_SCHEMA = 'starun-siril.starnet-contract.v2'
RECEIPT_SCHEMA = 'starun-siril.starnet-receipt.v2'
DEFAULT_LINEAR_STRETCH = {'shadows_clipping': -2.8, 'target_background': 0.25}


def load_json(path):
    if Path(path).is_symlink() or not Path(path).is_file():
        raise ValueError("Native StarNet JSON is missing or unsafe")
    from siril_manual_bundle import BundleError, strict_json_bytes
    try:
        value = strict_json_bytes(Path(path).read_bytes(), document=str(path))
    except BundleError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError('Native StarNet JSON must be an object')
    return value


def validate_contract(document, source, runtime, session, run_id, *, replaying=False):
    fields = {'schema', 'source', 'runtime', 'stride', 'work_dir', 'input_fits', 'linear_stretch'}
    if not isinstance(document, dict) or set(document) != fields or document['schema'] != CONTRACT_SCHEMA:
        raise ValueError('Invalid native StarNet contract fields')
    parameters = document['linear_stretch']
    if not isinstance(parameters, dict) or set(parameters) != set(DEFAULT_LINEAR_STRETCH):
        raise ValueError('Invalid StarNet linear stretch fields')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in parameters.values()):
        raise ValueError('StarNet linear stretch requires finite numbers')
    if parameters['shadows_clipping'] >= 0 or not 0 < parameters['target_background'] < 1:
        raise ValueError('StarNet linear stretch is outside the native range')
    if (runtime.get('version') != '2.6.2'
        or runtime.get('adapter', {}).get('path') != str(SCRIPTS_ROOT / 'siril_starnet_linear.py')
        or runtime.get('source_lock', {}).get('path') != str(SKILL_ROOT / 'references/starnet-linear-source.json')):
        raise ValueError('Native linear StarNet requires its frozen 2.6.2 bridge')
    lock = load_json(SKILL_ROOT / 'references/starnet-linear-source.json')
    if (lock.get('adapter_sha256') != sha256_file(SCRIPTS_ROOT / 'siril_starnet_linear.py')
        or lock.get('legacy_adapter_sha256') != sha256_file(SCRIPTS_ROOT / 'siril_starnet.py')):
        raise ValueError('Native StarNet adapter/source lock drift')
    projected = {k: document[k] for k in ('source', 'runtime', 'stride', 'work_dir')}
    projected['schema'] = legacy.CONTRACT_SCHEMA
    work = legacy.validate_contract(projected, source, runtime, session, run_id, replaying=replaying)
    raw = document['input_fits']
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise ValueError('Native StarNet input_fits requires an absolute artifact path')
    prepared = session_path(session, raw, allowed_roots=('artifacts',))
    if prepared.suffix.lower() not in ('.fit', '.fits', '.fts') or prepared == source:
        raise ValueError('Native StarNet input must be its declared full-source FITS copy')
    return work


def read_native_fits(path):
    import numpy as np
    geometry = fits_geometry(path)
    if geometry['bitpix'] != -32:
        raise ValueError('Native StarNet requires float32 FITS')
    raw, scale, zero = read_fits_pixels(path)
    if scale != 1 or zero != 0:
        raise ValueError("Native StarNet requires normalized float32 without FITS scaling")
    data = np.asarray(raw, dtype=float) * scale + zero
    if not np.isfinite(data).all():
        raise ValueError('Native StarNet FITS has nonfinite pixels')
    return data, geometry


def pixel_digest(data):
    import numpy as np
    return hashlib.sha256(np.asarray(data, dtype='<f4').tobytes(order='C')).hexdigest()


def build_starnet_argv(runtime, input_fits, output_fits, stride, parameters):
    return [runtime['executable']['path'], '-i', str(input_fits), '-o', str(output_fits),
            '-s', str(stride), '-w', runtime['model']['path'], '--linear',
            '--shadows-clipping', str(parameters['shadows_clipping']),
            '--target-background', str(parameters['target_background'])]


def process_starless(siril, document, work):
    import numpy as np
    prepared = Path(document['input_fits'])
    pixels, geometry = read_native_fits(prepared)
    above_white = int(np.count_nonzero(pixels > 1))
    if above_white:
        siril.log(f'Warning: native linear StarNet will clip {above_white} samples above one '
                  f'(peak {float(pixels.max()):.9g}); input is passed unchanged.')
    if geometry['width'] < 512 or geometry['height'] < 512:
        raise ValueError('Native StarNet without upsampling requires at least 512x512 pixels')
    precision = verify_image_precision(siril.get_image(), prepared)
    prepared_record = fingerprint(prepared)
    work.mkdir(parents=True, exist_ok=False)
    temporary = Path(tempfile.mkdtemp(prefix='handoff-', dir=work))
    input_fits, output_fits = temporary / 'input.fit', temporary / 'starless.fit'
    shutil.copyfile(prepared, input_fits)
    if sha256_file(input_fits) != prepared_record['sha256']:
        raise ValueError('Native StarNet input copy changed')
    argv = build_starnet_argv(document['runtime'], input_fits, output_fits,
                             document['stride'], document['linear_stretch'])
    result = subprocess.run(argv, cwd=temporary, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors='replace', check=False)
    (work / "native.log").write_text(result.stdout, encoding="utf-8")
    siril.log(result.stdout)
    if result.returncode or not output_fits.is_file() or output_fits.is_symlink():
        raise ValueError(f'Native StarNet failed with exit code {result.returncode}')
    if {p.name for p in temporary.iterdir()} != {'input.fit', 'starless.fit'}:
        raise ValueError('Native StarNet wrote undeclared temporary outputs')
    output, output_geometry = read_native_fits(output_fits)
    if output_geometry != geometry:
        raise ValueError('Native StarNet changed FITS geometry or sample type')
    if sha256_file(prepared) != prepared_record['sha256'] or sha256_file(input_fits) != prepared_record['sha256']:
        raise ValueError('Native StarNet input changed during processing')
    siril.cmd('load', f'"{output_fits}"')
    output_precision = verify_image_precision(siril.get_image(), output_fits)
    return {'input_fits': prepared_record, 'image_precision': precision,
            'linear_stretch': document['linear_stretch'], 'argv': argv,
            'native_output': {**fingerprint(output_fits, include_path=False), 'geometry': output_geometry,
                              'pixels_sha256': pixel_digest(output)},
            'output_precision': output_precision, 'stdout': result.stdout,
            'input_negative_count': int(np.count_nonzero(pixels < 0))}


def validate_receipt(document, receipt, primary, full_source):
    fields = {'schema', 'status', 'contract_sha256', 'source', 'runtime', 'stride',
              'input_fits', 'image_precision', 'linear_stretch', 'argv', 'native_output',
              'output_precision', 'stdout', 'input_negative_count'}
    if not isinstance(receipt, dict) or set(receipt) != fields or receipt['schema'] != RECEIPT_SCHEMA:
        raise ValueError('Invalid native StarNet receipt fields')
    if receipt['status'] != 'verified' or any(receipt[k] != document[k] for k in ('source', 'runtime', 'stride', 'linear_stretch')):
        raise ValueError('Native StarNet receipt differs from its contract')
    if receipt['input_fits'] != fingerprint(full_source) or str(full_source) != document['input_fits']:
        raise ValueError('Native StarNet full-source fingerprint changed')
    data, geometry = read_native_fits(primary)
    source_data, source_geometry = read_native_fits(full_source)
    record = receipt['native_output']
    if (not isinstance(record, dict) or set(record) != {'sha256', 'size', 'geometry', 'pixels_sha256'}
        or record['geometry'] != geometry or geometry != source_geometry
        or record['pixels_sha256'] != pixel_digest(data)):
        raise ValueError('Native StarNet output differs from its bound float32 pixels')
    if (type(record['size']) is not int or record['size'] <= 0
        or any(not isinstance(record[k], str) or re.fullmatch(r'[a-f0-9]{64}', record[k]) is None
               for k in ('sha256', 'pixels_sha256'))):
        raise ValueError('Native StarNet output fingerprint is invalid')
    parameters = receipt['linear_stretch']
    argv = receipt['argv']
    if not isinstance(argv, list) or len(argv) != 14:
        raise ValueError('Native StarNet argv is invalid')
    work = Path(document['work_dir'])
    input_fits, output_fits = Path(argv[2]), Path(argv[4])
    if (input_fits.name != 'input.fit' or output_fits.name != 'starless.fit'
        or input_fits.parent != output_fits.parent or input_fits.parent.parent != work
        or not input_fits.parent.name.startswith('handoff-')
        or argv != build_starnet_argv(document['runtime'], input_fits, output_fits, document['stride'], parameters)):
        raise ValueError('Native StarNet argv differs from the frozen contract')
    if (not isinstance(receipt['stdout'], str) or type(receipt['input_negative_count']) is not int
        or receipt['input_negative_count'] != int((source_data < 0).sum())):
        raise ValueError('Native StarNet source diagnostics changed')
    for name in ('image_precision', 'output_precision'):
        proof = receipt[name]
        if (not isinstance(proof, dict) or proof.get('status') != 'verified' or proof.get('bitpix') != -32
            or proof.get('dtype') != 'float32' or proof.get('shape') != list(data.shape)
            or proof.get('endpoints_preserved') is not True or proof.get('tolerance') != 'two_float32_ULP'
            or type(proof.get('max_abs_normalized_error')) not in (int, float)
            or not math.isfinite(proof['max_abs_normalized_error']) or proof['max_abs_normalized_error'] < 0):
            raise ValueError('Native StarNet float32 precision proof is invalid')
    if (receipt['image_precision']['source_sha256'] != receipt['input_fits']['sha256']
        or receipt['output_precision']['source_sha256'] != record['sha256']):
        raise ValueError('Native StarNet precision fingerprint changed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--contract-sha256', required=True)
    parser.add_argument('--expected-source', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    contract_path = Path(args.contract).resolve()
    receipt = Path(args.receipt).resolve()
    source = Path(args.expected_source).resolve()
    if receipt.exists() or receipt.is_symlink() or receipt.parent != contract_path.parent:
        raise ValueError('Native StarNet receipt must be new and beside its contract')
    if sha256_file(contract_path) != args.contract_sha256:
        raise ValueError('Native StarNet contract hash changed')
    document = load_json(contract_path)
    session = contract_path.parents[2]
    work = validate_contract(document, source, document['runtime'], session, contract_path.parent.name)
    actual_runtime = legacy.probe_runtime()
    if actual_runtime != document['runtime']['python']:
        raise ValueError('Native StarNet Python dependency version drift')
    import sirilpy as s
    siril = s.SirilInterface()
    siril.connect()
    try:
        if Path(siril.get_image_filename()).resolve() != source:
            raise ValueError('Siril loaded image differs from the declared source')
        evidence = process_starless(siril, document, work)
        if (legacy.probe_runtime() != actual_runtime or sha256_file(source) != document['source']['sha256']
            or sha256_file(contract_path) != args.contract_sha256):
            raise ValueError('Native StarNet source, contract or Python changed during processing')
        legacy.validate_contract({**{k: document[k] for k in ('source', 'runtime', 'stride', 'work_dir')},
                                  'schema': legacy.CONTRACT_SCHEMA}, source, document['runtime'], session,
                                 contract_path.parent.name, replaying=True)
        atomic_write_json(receipt, {'schema': RECEIPT_SCHEMA, 'status': 'verified',
            'contract_sha256': args.contract_sha256, 'source': document['source'],
            'runtime': document['runtime'], 'stride': document['stride'], **evidence})
    finally:
        siril.disconnect()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

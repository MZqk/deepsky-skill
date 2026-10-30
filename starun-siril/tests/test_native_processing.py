"""Regression checks for native contracts and real Siril transfer arithmetic."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

from .test_knowledge_contract import protocol_text
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
import deep_sky_siril_processing as native
import deep_sky_siril_metrics as metrics
import siril_auto_samples as samples
from deep_sky_siril_contract import EXECUTION_POLICY, ContractError, classify_siril_network, sha256_file
from deep_sky_siril_artifacts import read_fits_pixels


def write_fits(path, values, extra=(), *, bitpix=-32):
    a = np.asarray(values, dtype={-32: '>f4', -64: '>f8'}[bitpix])
    if a.ndim == 2:
        a = a[None]
    cards = [('SIMPLE', 'T'), ('BITPIX', bitpix), ('NAXIS', 3 if a.shape[0] == 3 else 2),
             ('NAXIS1', a.shape[2]), ('NAXIS2', a.shape[1])]
    if a.shape[0] == 3:
        cards.append(('NAXIS3', 3))
    cards += list(extra)
    h = ''.join(f'{k:<8}= {v:>20}'.ljust(80) for k, v in cards) + 'END'.ljust(80)
    h = h.encode(); h += b' ' * (-len(h) % 2880)
    data = a.tobytes(); path.write_bytes(h + data + b'\0' * (-len(data) % 2880))


@pytest.mark.parametrize('rate,verdict', [(0, 'uncertain'), (.000099, 'uncertain'), (.0001, 'reject'), (.000101, 'reject')])
@pytest.mark.parametrize('protocol', ['stretch', 'tone.adjust'])
def test_shadow_threshold(rate, verdict, protocol):
    m = {'measurement_domain': 'scientific', 'channels': [{'shadow_clip_rate': rate}]}
    assert metrics.evaluate_stage_gates(protocol, m)[1] == verdict
    assert metrics.evaluate_stage_gates('input.inspect', m)[1] == 'uncertain'


def test_raw_channel_clipping_and_nonfinite_are_not_hidden(tmp_path):
    a = np.full((3, 100, 100), .01)
    a[0, 0, 0] = -1; a[1] = 1.1; a[2, 0, 0] = np.nan; a[2, 0, 1] = np.inf
    p = tmp_path / 'rgb.fit'; write_fits(p, a)
    m = metrics.analyze_image_histogram(p)
    assert m['status'] == 'uncertain'
    assert m['channels'][0]['shadow_clip_rate'] == .0001
    assert m['channels'][1]['highlight_sat_rate'] == 1
    assert m['channels'][2]['nonfinite_count'] == 2
    assert metrics.evaluate_stage_gates('stretch', m)[1] == 'reject'
    assert 'state_recommendation' not in m and 'bg_mad' not in m
    p.write_bytes(b'broken')
    assert metrics.analyze_image_histogram(p)['status'] == 'uncertain'
    assert metrics.evaluate_stage_gates('stretch', {})[1] == 'uncertain'


def test_jpeg_quantization_cannot_reject_scientific_clipping(tmp_path):
    p = tmp_path / 'black.jpg'; Image.new('RGB', (32, 32)).save(p)
    m = metrics.analyze_image_histogram(p)
    assert m['measurement_domain'] == 'display_quantized'
    assert all('shadow_clip_rate' not in c for c in m['channels'])
    assert metrics.evaluate_stage_gates('delivery.render', m)[1] == 'uncertain'


@pytest.mark.parametrize('width,height', [(2160, 3840), (513, 379), (80, 72)])
def test_complete_extent_exact_sampling_and_hash(tmp_path, width, height):
    p = tmp_path / 'source.fit'
    a = np.tile(np.linspace(.02, .021, width), (height, 1)); write_fits(p, a)
    grid, w, h, xs, ys = samples._load_luminance_thumbnail(p, None)
    step = int(np.ceil(max(width, height) / 512))
    assert (w, h) == (width, height) and xs[0] == ys[0] == step // 2
    assert xs[-1] >= width - step and ys[-1] >= height - step
    assert len(xs) == int(np.ceil(width / step)) and len(ys) == int(np.ceil(height / step))
    if width == 80:
        with pytest.raises(ValueError, match="at least 12"):
            samples.generate_background_samples(p, target_count=24)
        return
    contract = samples.generate_background_samples(p, target_count=24)
    assert contract['source']['sha256'] == sha256_file(p)
    assert len(contract['fit_samples']) >= 12
    assert all(s['x'] in xs and s['y'] in ys for s in contract['fit_samples'])
    assert max(s['x'] for s in contract['fit_samples']) > .50 * width
    assert max(s['y'] for s in contract['fit_samples']) > .75 * height


def test_sampling_failure_never_writes_contract_or_relaxes_mask(tmp_path, monkeypatch):
    p = tmp_path / 'source.fit'; p.write_bytes(b'broken'); out = tmp_path / 'contract.json'
    with pytest.raises(Exception):
        samples.main(['--source', str(p), '--output', str(out)])
    assert not out.exists()
    write_fits(p, np.full((8, 8), .1))
    with pytest.raises(ValueError, match='at least 12'):
        samples.generate_background_samples(p)
    write_fits(p, np.full((80, 80), .1))
    monkeypatch.setattr(samples, '_dilate_mask', lambda mask, radius: [[True] * 80 for _ in range(80)])
    with pytest.raises(ValueError, match='Use manual'):
        samples.generate_background_samples(p)


def test_real_siril_sample_box_medians_and_preview_coordinates(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    y, x = np.indices((320, 720))
    values = np.asarray([.02 + .0008 * y + .00001 * x,
                         .04 + .0002 * y + .00001 * x,
                         .08 + .0004 * y + .00001 * x])
    source = tmp_path / 'source.fit'
    write_fits(source, values)
    contract = samples.generate_background_samples(source, target_count=24)
    (tmp_path / 'samples.json').write_text(json.dumps(contract))
    # This test bridge reads native statistics only; it never changes scientific pixels.
    bridge = tmp_path / 'coordinate_probe.py'
    bridge.write_text(
        'import json, sys\nfrom pathlib import Path\nimport sirilpy\n'
        f'sys.path.insert(0, {str(ROOT / "scripts")!r})\n'
        'from siril_background_samples import install_declared_samples\n'
        's = sirilpy.SirilInterface(); s.connect()\n'
        f'root = Path({str(tmp_path)!r})\n'
        'points = json.loads((root / "samples.json").read_text())["fit_samples"]\n'
        'install_declared_samples(s, [(p["x"], p["y"]) for p in points])\n'
        'payload = [{"position": p.position, "size": p.size, "median": p.median} for p in s.get_image_bgsamples()]\n'
        '(root / "native.json").write_text(json.dumps(payload))\n')
    script = tmp_path / 'coordinates.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nsavejpg "{tmp_path}/preview" 95\npyscript "{bridge}"\nclose\n')
    config = tmp_path / 's.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}',
                             '--offline', f'--script={script}'], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = json.loads((tmp_path / 'native.json').read_text())
    preview = np.asarray(Image.open(tmp_path / 'preview.jpg'))
    assert len(installed) == len(contract['fit_samples'])
    for point, native_point in zip(contract['fit_samples'], installed):
        sx, sy = int(point['x']), int(point['y'])
        assert native_point['size'] == 25 and native_point['position'] == [sx, sy]
        raw_y = 319 - sy
        np.testing.assert_allclose(native_point['median'], np.median(values[:, raw_y - 12:raw_y + 13, sx - 12:sx + 13], axis=(1, 2)), atol=2e-7)
        np.testing.assert_allclose(preview[int(samples.preview_y(sy, 320)), sx] / 255, values[:, raw_y, sx], atol=2 / 255)


@pytest.mark.parametrize('line', [
    'ght -D=11 -human -clipmode=rgbblend', 'ght -D=1 -B=16 -human -clipmode=rgbblend',
    'ght -D=1 -LP=.5 -SP=.4 -human -clipmode=rgbblend', 'ght -D=nan -human -clipmode=rgbblend',
    'ght -D=1 -independent -clipmode=rgbblend', 'denoise -nocosmetic -mod=.36',
    'denoise -nocosmetic -mod=.2 -sos', 'platesolve -catalog=tycho2 -noflip -nocrop',
    'platesolve -catalog=localgaia -noflip', 'mtf 0 .1 1 R'] )
def test_native_parameters_reject_invalid_or_unsafe_options(line):
    with pytest.raises(ContractError):
        native.validate_parameters(native.script_commands(line)[0], 'restoration.denoise-nonlinear')


def test_primary_and_remote_contracts(tmp_path):
    (tmp_path / 'artifacts').mkdir(); a = tmp_path / 'artifacts/a.fit'; b = tmp_path / 'artifacts/b.fit'
    with pytest.raises(ContractError, match='primary-output'):
        native.primary_output(tmp_path, [a, b], None)
    assert native.primary_output(tmp_path, [a, b], str(b)) == b
    with pytest.raises(ContractError, match='primary-output'):
        native.primary_output(tmp_path, [a, tmp_path/'reports/psf.fit'], None)
    assert native.transfer_chain(f'load input.fit\nmtf 0 .1 1\nsave "{a.with_suffix(".fits")}"', a.with_suffix('.fits')) == [{'command':'mtf','parameters':['0','.1','1']}]
    with pytest.raises(ContractError):
        native.primary_output(tmp_path, [a, b], str(tmp_path / 'preview.jpg'))
    with pytest.raises(ContractError):
        classify_siril_network('platesolve -catalog=tycho2 -noflip -nocrop', protocol='astrometry.solve', session_offline=False)
    # Online default: the catalogue is recorded but no longer decides networking.
    online = classify_siril_network('platesolve -catalog=localgaia -noflip -nocrop', protocol='astrometry.solve', session_offline=False)
    assert online['catalogues'] == ['localgaia'] and not online['effective_offline']
    # The frozen offline-first rule is still reachable for legacy sessions.
    legacy = classify_siril_network('platesolve -catalog=localgaia -noflip -nocrop', protocol='astrometry.solve', session_offline=False, policy='offline_default_explicit_gaia_v2')
    assert legacy['effective_offline'] and legacy['reason'] == 'local_gaia'
    # An explicit offline session still blocks remote Gaia outright.
    with pytest.raises(ContractError):
        classify_siril_network('platesolve -catalog=gaia -noflip -nocrop', protocol='astrometry.solve', session_offline=True)


def test_domains_previews_and_automatic_branch_stretch_fail_closed(tmp_path, monkeypatch):
    import deep_sky_siril_session as session
    p = tmp_path / 'input.fit'; write_fits(p, np.full((64, 64), .1))
    parent = tmp_path / 'artifacts/starless.fit'; parent.parent.mkdir(); write_fits(parent, np.full((64, 64), .1))
    payload = {'input': {'path': str(p)}, 'context': {'input_state': 'linear'}}
    receipts = [{'id': '060-separate', 'protocol': 'stars.separate', 'source_path': '@input', 'primary_output': 'artifacts/linear-a.fit', 'image_domain': 'linear', 'branch_binding': {'separation_run': '060-separate'}},
                {'id': '070-stretch', 'protocol': 'stretch', 'source_path': 'artifacts/linear-a.fit', 'primary_output': 'artifacts/starless.fit', 'image_domain': 'nonlinear', 'branch_binding': {'separation_run': '060-separate'}}]
    monkeypatch.setattr(session, '_verified_success_receipts', lambda _: receipts)
    with pytest.raises(ContractError, match='linear scientific parent'):
        native.prepare_processing(tmp_path, payload, 'restoration.denoise', parent, tmp_path/'out.fit', '', {}, None, None, None, None)
    with pytest.raises(ContractError, match='previews'):
        native.source_state(tmp_path, payload, tmp_path / 'preview.jpg')
    receipts[:] = [{**receipts[0], 'primary_output': 'artifacts/starless.fit'}]
    text = f'load "{parent}"\nautostretch -linked -4.5 .12\nsave "{tmp_path}/out.fit"'
    with pytest.raises(ContractError, match='explicit replayable'):
        native.prepare_processing(tmp_path, payload, 'stretch', parent, tmp_path/'out.fit', text, {}, None, None, None, None)


def recompose_steps(root, chain, original=None, starless=None, source=None):
    a = root / 'artifacts'; a.mkdir(exist_ok=True)
    original = original or root/'i.fit'; starless = starless or root/'a.fit'; source = source or starless
    paths = [a/(name+'.fit') for name in ('full', 'base', 'stars', 'candidate')]
    steps = [['load', str(original)]] + [[c['command'], *c['parameters']] for c in chain] + [['save', str(paths[0])], ['load', str(starless)]]
    steps += [[c['command'], *c['parameters']] for c in chain] + [['save', str(paths[1])], ['pm', '$artifacts/full$ - $artifacts/base$'], ['save', str(paths[2])], ['pm', f'{native._variable(source, root)} + 1.0 * $artifacts/stars$'], ['save', str(paths[3])]]
    return steps, paths


@pytest.fixture(scope='module')
def recomposition_reference():
    return protocol_text("stars.recompose")


def test_documented_recomposition_skeleton_matches_runtime(tmp_path, recomposition_reference):
    blocks = re.findall(r'^```ssf\s*\n(.*?)^```\s*$', recomposition_reference, re.MULTILINE | re.DOTALL)
    assert blocks, 'Recomposition reference lacks an executable skeleton'
    for block in blocks:
        commands = native.script_commands(block.replace('/abs/session', str(tmp_path)))
        loads = [Path(tokens[1]) for tokens in commands if tokens[0] == 'load']
        saves = [native._path(tokens[1], tmp_path) for tokens in commands if tokens[0] == 'save']
        if '# HDR replay' in block:
            from deep_sky_siril_hdr import validate_recomposition as validate_hdr_recomposition
            first, second = [i for i, t in enumerate(commands) if t[0] == 'load'][:2]
            frozen = {'gentle_chain': [{'command': t[0], 'parameters': t[1:]} for t in commands[first:second] if t[0] in native.TRANSFORMS],
                      'faint_chain': [{'command': t[0], 'parameters': t[1:]} for t in commands[second:] if t[0] in native.TRANSFORMS][:1],
                      'mask': {'blend_path': 'artifacts/070-mask-rgb.fit'}}
            source = tmp_path / 'artifacts/080-local-contrast.fit'
            roles, pairs = validate_hdr_recomposition(tmp_path, commands, source, loads[0], loads[2], saves[-1], frozen)
            assert roles['full_baseline'] == saves[2].relative_to(tmp_path).as_posix()
            assert roles['display_stars'] == saves[6].relative_to(tmp_path).as_posix()
            assert len(pairs) == 2
            continue
        assert len(loads) == 2 and len(saves) == 4, 'Incomplete recomposition example'
        first_save = next(index for index, tokens in enumerate(commands) if tokens[0] == 'save')
        chain = [{'command': tokens[0], 'parameters': tokens[1:]}
                 for tokens in commands[:first_save] if tokens[0] in native.TRANSFORMS]
        assert chain, 'Recomposition example must replay an explicit transfer'
        expression = [tokens[1] for tokens in commands if tokens[0] == 'pm'][-1]
        variables = re.findall(r'\$([^$]+)\$', expression)
        assert variables, 'Missing scientific parent in the documented composition'
        source = native._path(variables[0], tmp_path)
        roles = native.validate_recomposition(tmp_path, commands, source, loads[0], loads[1], saves[-1], chain)
        assert roles['full_baseline'] == saves[0].relative_to(tmp_path).as_posix()
        assert roles['display_stars'] == saves[2].relative_to(tmp_path).as_posix()


@pytest.mark.parametrize('outside', [False, True], ids=['inside', 'outside'])
@pytest.mark.parametrize('field,target_type', [
    ('per_channel_max_abs', 'general'),
    ('per_channel_final_max_abs', 'general'),
    ('per_channel_min_residual', 'general'),
    ('per_channel_neg_energy_ratio', 'general'),
    ('per_channel_neg_energy_ratio', 'galaxy'),
    ('per_channel_neg_energy_ratio', 'cluster'),
])
def test_documented_recomposition_thresholds_match_runtime(tmp_path, recomposition_reference, field, target_type, outside):
    rows = [line.strip().strip('|').split('|') for line in recomposition_reference.splitlines() if line.startswith('|')]
    matching = [cells[1] for cells in rows if len(cells) == 3 and f'`{field}`' in cells[2]]
    assert len(matching) == 1, f'Missing or ambiguous documented boundary for {field}'
    boundary = matching[0]
    limits = re.findall(r'(<=|>=)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)', boundary, re.IGNORECASE)
    expected_operator = '>=' if field == 'per_channel_min_residual' else '<='
    assert limits and all(operator == expected_operator for operator, _ in limits)
    values = [float(value) for _, value in limits]
    overrides = set(re.findall(r'`([a-z]+)`', boundary))
    if field == 'per_channel_neg_energy_ratio':
        assert len(values) == 2 and all(0 < value < 1 for value in values)
        limit = values[1] if target_type in overrides else values[0]
    else:
        assert len(values) == 1
        limit = values[0]
        assert (limit < 0) if field == 'per_channel_min_residual' else (limit > 0)

    # Float64 FITS lets us probe close to each documented boundary without Float32 rounding.
    value = limit * (1 + (1e-6 if outside else -1e-6))
    base = np.full((3, 32, 32), .25)
    residual = np.full_like(base, 2 ** -4 if field == 'per_channel_min_residual' else 2 ** -14)
    if field == 'per_channel_min_residual':
        residual[2, 0, 0] = value
    elif field == 'per_channel_neg_energy_ratio':
        positive_mass = (residual[2].size - 1) * residual[2, 0, 0]
        residual[2, 0, 0] = -value * positive_mass / (1 - value)
    full = base + residual
    if field == 'per_channel_max_abs':
        full[2, 0, 0] += value
    for name, pixels in [('full', full), ('base', base), ('stars', residual)]:
        write_fits(tmp_path/f'{name}.fit', pixels, bitpix=-64)
    metadata = {'target_type': target_type, 'branch_binding': {
        'full_baseline': 'full.fit', 'original_starless': 'base.fit', 'display_stars': 'stars.fit', 'strength': .8}}
    candidate = base + .8*residual
    if field == 'per_channel_final_max_abs':
        candidate[2, 0, 0] += value
    write_fits(tmp_path/'candidate.fit', candidate, bitpix=-64)
    arguments = (tmp_path, tmp_path/'base.fit', tmp_path/'candidate.fit', metadata)
    if outside and field != 'per_channel_neg_energy_ratio':
        with pytest.raises(ContractError, match='Recomposition rejected'):
            native.validate_processing_outputs(*arguments)
    else:
        result = native.validate_processing_outputs(*arguments)['closure']
        expected = abs(value) if field in {'per_channel_max_abs', 'per_channel_final_max_abs'} else value
        assert result[field][2] == pytest.approx(expected, rel=1e-8, abs=1e-12)
        assert bool(result['warnings']) is (outside and field == 'per_channel_neg_energy_ratio')


def test_recomposition_rejects_parameter_mismatch_clipping_and_geometry(tmp_path):
    chain = [{'command': 'mtf', 'parameters': ['0', '.1', '1']}]
    steps, paths = recompose_steps(tmp_path, chain)
    native.validate_recomposition(tmp_path, steps, tmp_path/'a.fit', tmp_path/'i.fit', tmp_path/'a.fit', paths[-1], chain)
    steps[4][2] = '.2'
    with pytest.raises(ContractError, match='exact frozen chain'):
        native.validate_recomposition(tmp_path, steps, tmp_path/'a.fit', tmp_path/'i.fit', tmp_path/'a.fit', paths[-1], chain)
    steps, paths = recompose_steps(tmp_path, chain); steps[-2][1] = 'min(1, $a$ + $artifacts/stars$)'
    with pytest.raises(ContractError, match='without clipping'):
        native.validate_recomposition(tmp_path, steps, tmp_path/'a.fit', tmp_path/'i.fit', tmp_path/'a.fit', paths[-1], chain)
    for path, value in zip(paths[:3], (.4, .3, .1)):
        write_fits(path, np.full((3, 32, 32), value))
    metadata = {'branch_binding': {'full_baseline':'artifacts/full.fit', 'original_starless':'artifacts/base.fit', 'display_stars':'artifacts/stars.fit', 'strength': 1.0}}
    write_fits(paths[-1], np.full((3,32,32), .4))
    assert max(native.validate_processing_outputs(tmp_path, paths[1], paths[-1], metadata)['closure']['per_channel_max_abs']) <= 1e-5
    for bad in (np.nan, -.1, .2):
        write_fits(paths[2], np.full((3, 32, 32), bad))
        with pytest.raises(ContractError):
            native.validate_processing_outputs(tmp_path, paths[1], paths[-1], metadata)
    write_fits(paths[2], np.full((3, 31, 32), .1))
    with pytest.raises(ContractError):
        native.validate_processing_outputs(tmp_path, paths[1], paths[-1], metadata)


@pytest.mark.parametrize('variant', ['screen', 'positive-residual'])
def test_recomposition_rejects_alternative_blend_or_unsigned_residual(tmp_path, variant):
    chain = [{'command': 'mtf', 'parameters': ['0', '.1', '1']}]
    steps, paths = recompose_steps(tmp_path, chain)
    if variant == 'screen':
        steps[-2][1] = '1 - (1 - $a$) * (1 - $artifacts/stars$)'
        message = 'without clipping'
    else:
        steps[6][1] = 'max($artifacts/full$ - $artifacts/base$, 0)'
        message = 'exact frozen chain'
    with pytest.raises(ContractError, match=message) as error:
        native.validate_recomposition(tmp_path, steps, tmp_path/'a.fit',
                                      tmp_path/'i.fit', tmp_path/'a.fit', paths[-1], chain)
    assert error.value.code == 'processing_contract_invalid'


@pytest.mark.parametrize('strength,accepted', [(.30, True), (1.0, True), (.29999, False), (1.00001, False)])
def test_recomposition_strength_boundaries(tmp_path, strength, accepted):
    chain = [{'command': 'mtf', 'parameters': ['0', '.1', '1']}]
    steps, paths = recompose_steps(tmp_path, chain)
    steps[-2][1] = f'$a$ + {strength} * $artifacts/stars$'
    arguments = (tmp_path, steps, tmp_path/'a.fit', tmp_path/'i.fit', tmp_path/'a.fit', paths[-1], chain)
    if accepted:
        assert native.validate_recomposition(*arguments)['strength'] == strength
    else:
        with pytest.raises(ContractError, match='Star strength must be within'):
            native.validate_recomposition(*arguments)


@pytest.mark.parametrize('target_type,limit_count', [('general', 5), ('galaxy', 10), ('cluster', 10)])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_recomposition_negative_energy_ratio_boundaries(tmp_path, target_type, limit_count, offset):
    count = limit_count + offset
    base = np.full((3, 25, 40), .25)
    residual = np.full_like(base, .0625)
    residual[2].flat[:count] = -.0625
    for name, values in [('full', base + residual), ('base', base), ('stars', residual)]:
        write_fits(tmp_path/f'{name}.fit', values)
    metadata = {'target_type': target_type, 'branch_binding': {
        'full_baseline': 'full.fit', 'original_starless': 'base.fit', 'display_stars': 'stars.fit', 'strength': .8}}
    write_fits(tmp_path/'candidate.fit', base + .8*residual)
    arguments = (tmp_path, tmp_path/'base.fit', tmp_path/'candidate.fit', metadata)
    result = native.validate_processing_outputs(*arguments)['closure']
    assert result['per_channel_neg_energy_ratio'][2] == pytest.approx(count / 1000)
    assert result['max_allowed_neg_energy_ratio'] == limit_count / 1000
    assert bool(result['warnings']) is (offset > 0)
    if offset > 0:
        with pytest.raises(ContractError, match='Recomposition rejected'):
            native.validate_processing_outputs(*arguments, execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})


@pytest.mark.parametrize('closure_offset,residual_min,accepted', [
    pytest.param(9e-6, -.0625, True, id='closure-inside'),
    pytest.param(11e-6, -.0625, False, id='closure-outside'),
    pytest.param(0.0, np.nextafter(np.float32(-.10), np.float32(np.inf)), True, id='minimum-inside'),
    pytest.param(0.0, np.nextafter(np.float32(-.10), np.float32(-np.inf)), False, id='minimum-outside'),
])
def test_recomposition_closure_and_minimum_residual_boundaries(tmp_path, closure_offset, residual_min, accepted):
    # Keep the other gates well within bounds to isolate each failure.
    base = np.full((3, 25, 40), .5)
    residual = np.full_like(base, .0625)
    residual[0, 0, 0] = residual_min
    full = base + residual
    full[1, 0, 0] += closure_offset
    for name, values in [('full', full), ('base', base), ('stars', residual)]:
        write_fits(tmp_path/f'{name}.fit', values)
    metadata = {'branch_binding': {
        'full_baseline': 'full.fit', 'original_starless': 'base.fit', 'display_stars': 'stars.fit', 'strength': 1.0}}
    write_fits(tmp_path/'candidate.fit', base + residual)
    arguments = (tmp_path, tmp_path/'base.fit', tmp_path/'candidate.fit', metadata)
    if accepted:
        assert 'closure' in native.validate_processing_outputs(*arguments)
    else:
        with pytest.raises(ContractError, match='Recomposition rejected'):
            native.validate_processing_outputs(*arguments)


def test_offline_wcs_preserved_and_missing_catalogue_skipped(tmp_path):
    p = tmp_path/'wcs.fit'; output = tmp_path/'out.fit'
    cards = [('CTYPE1', "'RA---TAN'"), ('CTYPE2', "'DEC--TAN'"), ('CRPIX1', 32), ('CRPIX2', 32),
             ('CRVAL1', 98), ('CRVAL2', 4), ('CD1_1', -.001), ('CD1_2', 0), ('CD2_1', 0), ('CD2_2', .001)]
    write_fits(p, np.full((64,64), .1), cards); shutil.copy(p, output)
    payload = {'input': {'path': str(p)}, 'context': {'input_state':'linear'}}
    metadata = native.prepare_processing(tmp_path,payload,'astrometry.solve',p,output,f'load "{p}"\nsave "{output}"',{},None,None,None,None)
    assert native.validate_processing_outputs(tmp_path,p,output,metadata)['astrometry']['status'] == 'preserved_existing_solution'
    write_fits(output, np.full((64,64), .2), cards)
    with pytest.raises(ContractError, match='pixel values'):
        native.validate_processing_outputs(tmp_path,p,output,metadata)
    write_fits(p, np.full((64,64), .1))
    with pytest.raises(ContractError, match='Skipped: no frozen') as exc:
        native.prepare_processing(tmp_path,payload,'astrometry.solve',p,output,f'load "{p}"\nplatesolve -catalog=localgaia -noflip -nocrop',{},None,None,None,None)
    assert exc.value.code == 'local_gaia_astro_missing'


def test_real_siril_mtf_asinh_ghs_closure_and_negative_residual(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    y,x = np.mgrid[:64,:64]; star = .08*np.exp(-((x-32)**2+(y-32)**2)/4)
    a = np.full((3,64,64), .01); i = a + star
    write_fits(tmp_path/'i.fit',i); write_fits(tmp_path/'a.fit',a)
    chain = [{'command':'mtf','parameters':['0','.1','1']}, {'command':'asinh','parameters':['-human','2','0','-clipmode=rgbblend']},
             {'command':'ght','parameters':['-D=0.2','-B=0','-LP=0','-SP=0.2','-HP=1','-human','-clipmode=rgbblend']}]
    steps,paths = recompose_steps(tmp_path,chain,source=tmp_path/'artifacts/base.fit')
    text = 'requires 1.4.4 1.5.0\nset32bits\n'+'\n'.join(' '.join(json.dumps(v) if ' ' in v or '$' in v else v for v in row) for row in steps)+'\nclose\n'
    script=tmp_path/'test.ssf';script.write_text(text);config=tmp_path/'s.ini';config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result=subprocess.run([str(binary),f'--initfile={config}',f'--directory={tmp_path}','--offline',f'--script={script}'],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout
    roles=native.validate_recomposition(tmp_path,steps,tmp_path/'artifacts/base.fit',tmp_path/'i.fit',tmp_path/'a.fit',paths[-1],chain)
    checks=native.validate_processing_outputs(tmp_path,paths[1],paths[-1],{'branch_binding':roles})
    assert max(checks['closure']['per_channel_max_abs']) <= 1e-5
    # Use the stretched original starless parent for the display-domain candidate.
    full,scale,zero=read_fits_pixels(paths[0]);base,sb,zb=read_fits_pixels(paths[1]);stars,ss,zs=read_fits_pixels(paths[2])
    assert np.max(np.abs(base*sb+zb+stars*ss+zs-(full*scale+zero))) <= 1e-5
    final,sf,zf=read_fits_pixels(paths[-1])
    assert np.max(np.abs(final*sf+zf-(full*scale+zero))) <= 1e-5


def test_real_siril_ghs_d_scale_and_equivalence_limits(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    gradient = np.tile(np.linspace(0, 1, 4096, dtype=np.float32), (32, 1))
    mono, rgb = tmp_path/'mono.fit', tmp_path/'rgb.fit'
    write_fits(mono, gradient)
    write_fits(rgb, np.asarray([gradient, .6*gradient, .3*gradient]))
    strengths = (1., 1.5, 3.)
    transfers = []
    for index, strength in enumerate(strengths):
        transfers.extend([
            (f'ghs-{index}', mono, f'ght -D={strength:.17g} -B=1 -LP=0 -SP=0 -HP=1 -human -clipmode=rgbblend'),
            (f'mtf-{index}', mono, f'mtf 0 {1/(np.exp(strength)+1):.17g} 1'),
            (f'old-{index}', mono, f'mtf 0 {1/(strength+2):.17g} 1')])
    for name, landmarks in [('lp', '-LP=0.05 -SP=0.1 -HP=1'),
                            ('sp', '-LP=0 -SP=0.1 -HP=1'),
                            ('hp', '-LP=0 -SP=0 -HP=0.9')]:
        transfers.append((name, mono, f'ght -D=1 -B=1 {landmarks} -human -clipmode=rgbblend'))
    transfers.extend([
        ('rgb-human', rgb, 'ght -D=1 -B=1 -LP=0 -SP=0 -HP=1 -human -clipmode=rgbblend'),
        ('rgb-mtf', rgb, f'mtf 0 {1/(np.exp(1.)+1):.17g} 1')])
    lines = ['requires 1.4.4 1.5.0', 'set32bits']
    for name, source, command in transfers:
        lines.extend([f'load "{source}"', command, f'save "{tmp_path}/{name}.fit"', 'close'])
    script, config = tmp_path/'ghs-scale.ssf', tmp_path/'s.ini'
    script.write_text('\n'.join(lines)+'\n')
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}',
                             '--offline', f'--script={script}'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    outputs = {}
    for name, _, _ in transfers:
        data, scale, zero = read_fits_pixels(tmp_path/f'{name}.fit')
        outputs[name] = data.astype(np.float64)*scale+zero
    for index, _ in enumerate(strengths):
        np.testing.assert_allclose(outputs[f'ghs-{index}'], outputs[f'mtf-{index}'], rtol=0, atol=1e-6)
        assert np.max(np.abs(outputs[f'ghs-{index}']-outputs[f'old-{index}'])) > .01
        np.testing.assert_allclose(outputs[f'ghs-{index}'][0, 0, [0, -1]], [0, 1], rtol=0, atol=1e-6)
    for name in ('lp', 'sp', 'hp'):
        assert np.max(np.abs(outputs[name]-outputs['mtf-0'])) > .001
    assert np.max(np.abs(outputs['rgb-human']-outputs['rgb-mtf'])) > .001


def test_real_siril_ghs_rejects_zero_d(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    source, output = tmp_path/'source.fit', tmp_path/'output.fit'
    write_fits(source, np.full((32, 32), .2))
    script, config = tmp_path/'zero-d.ssf', tmp_path/'s.ini'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\n'
                      f'ght -D=0 -B=1 -LP=0 -SP=0 -HP=1 -human -clipmode=rgbblend\nsave "{output}"\nclose\n')
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}',
                             '--offline', f'--script={script}'], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'D must be > 0.0 and <= 10.0' in result.stdout+result.stderr
    assert not output.exists()


@pytest.mark.parametrize('strength', [1., 1.5, 3.])
def test_ghs_proposal_preserves_cli_d_scale(strength):
    import deep_sky_siril_stretch_v2 as protected
    tokens = protected.proposal_tokens({'method': 'ght', 'factors': {'D': strength, 'B': 1.}},
                                       0., {'LP': 0., 'SP': 0., 'HP': 1.})
    assert tokens[:2] == ['ght', f'-D={strength:.17g}']


def test_recomposed_parent_cannot_reenter_starless_enhancement(tmp_path, monkeypatch):
    import deep_sky_siril_session as session
    p = tmp_path/'i.fit';write_fits(p,np.full((64,64),.1));a=tmp_path/'artifacts';a.mkdir()
    payload={'input':{'path':str(p)},'context':{'input_state':'linear'}}
    receipts=[{'id':'060-separate','protocol':'stars.separate','source_path':'@input','primary_output':'artifacts/a.fit','image_domain':'linear','branch_binding':{'separation_run':'060-separate'}},
              {'id':'070-stretch','protocol':'stretch','source_path':'artifacts/a.fit','primary_output':'artifacts/f-a.fit','image_domain':'nonlinear','branch_binding':{'separation_run':'060-separate'}},
              {'id':'090-recompose','protocol':'stars.recompose','source_path':'artifacts/f-a.fit','primary_output':'artifacts/full.fit','image_domain':'nonlinear','branch_binding':{'separation_run':'060-separate'}}]
    monkeypatch.setattr(session,'_verified_success_receipts',lambda _:receipts)
    assert native.source_state(tmp_path,payload,a/'full.fit')['separation_run'] is None
    with pytest.raises(ContractError,match='starless branch'):
        native.prepare_processing(tmp_path,payload,'structure.local-contrast',a/'full.fit',a/'out.fit',f'load "{a}/full.fit"',{},None,None,None,None)
    receipts[-1]['image_domain']='linear'
    with pytest.raises(ContractError,match='domain disagrees'):
        native.source_state(tmp_path,payload,a/'full.fit')


def test_recomposition_rejects_wrong_branch_geometry_and_review(tmp_path, monkeypatch):
    import deep_sky_siril_session as session
    a=tmp_path/'artifacts';a.mkdir();p=tmp_path/'i.fit';parent=a/'stretched.fit';linear=a/'starless.fit'
    for path in (p,parent,linear):write_fits(path,np.full((64,64),.1))
    sep={'id':'060-separate','protocol':'stars.separate','source_path':'@input','primary_output':'artifacts/starless.fit','image_domain':'linear','branch_binding':{'separation_run':'060-separate'}}
    stretch={'id':'070-stretch','protocol':'stretch','source_path':'artifacts/starless.fit','primary_output':'artifacts/stretched.fit','image_domain':'nonlinear','transfer_chain':[{'command':'mtf','parameters':['0','.1','1']}],'branch_binding':{'separation_run':'060-separate'}}
    monkeypatch.setattr(session,'_verified_success_receipts',lambda _:[sep,stretch])
    payload={'input':{'path':str(p)},'context':{'input_state':'linear'}}
    record=tmp_path/'r.json';record.write_text('{}')
    load=lambda name:(record,sep if name=='060-separate' else stretch)
    def call(review=lambda _:None):
        return native.prepare_processing(tmp_path,payload,'stars.recompose',parent,a/'candidate.fit','',{},'060-separate','070-stretch',load,review)
    stretch['branch_binding']['separation_run']='wrong'
    with pytest.raises(ContractError,match='branch disagrees'):
        call()
    stretch['branch_binding']['separation_run']='060-separate'
    write_fits(parent,np.full((63,64),.1))
    with pytest.raises(ContractError,match='geometry changed'):
        call()
    write_fits(parent,np.full((64,64),.1))
    def rejected(_):raise ContractError('review_required','Rejected separation review')
    with pytest.raises(ContractError,match='Rejected separation'):
        call(rejected)


def _local_contrast_script(tmp_path, overrides=None):
    (tmp_path/'artifacts').mkdir(exist_ok=True)
    (tmp_path/'previews').mkdir(exist_ok=True)
    source = tmp_path/'source.fit'
    primary = tmp_path/'artifacts/080-local-contrast.fit'
    document = protocol_text("structure.local-contrast")
    text = document.split('```ssf\n')[1].split('```')[0].replace('/abs/current-starless.fit', str(source)).replace('/abs/session', str(tmp_path))
    line = next(line for line in text.splitlines() if line.startswith('# local-contrast:'))
    defaults = json.loads(line.split(':', 1)[1])
    bounds = {key: defaults[key] for key in ('low_start', 'low_end', 'high_start', 'high_end')}
    declared = {**bounds, **(overrides or {})}
    actual = {**defaults, **declared}
    text = text.replace(line, '# local-contrast: ' + json.dumps(declared))
    text = text.replace('clahe 1.5 8', f"clahe {actual['cliplimit']} {actual['grid']}")
    text = text.replace('gauss 2', f"gauss {actual['sigma']}")
    text = text.replace('1 + 0.20 *', f"1 + {actual['strength']} *")
    return source, primary, text, declared


def _prepare_local_contrast(tmp_path, monkeypatch, source, primary, text):
    monkeypatch.setattr(native, 'source_state', lambda *args: {
        'domain': 'nonlinear', 'run': {'id': '070-stretch'}, 'separation_run': '060-separate',
    })
    reviews = []
    metadata = native.prepare_processing(tmp_path, {}, 'structure.local-contrast', source, primary,
                                         text, {}, None, None, None, reviews.append)
    assert reviews == ['070-stretch']
    return metadata


@pytest.mark.parametrize('overrides', [
    {}, {'strength': .15}, {'cliplimit': 2.5, 'grid': 12, 'strength': .35, 'sigma': 4},
    {'cliplimit': .5, 'grid': 2, 'strength': .001, 'sigma': .5},
    {'cliplimit': 5, 'grid': 32, 'strength': .5, 'sigma': 16},
])
def test_local_contrast_defaults_overrides_and_parameter_limits(tmp_path, monkeypatch, overrides):
    source, primary, text, declared = _local_contrast_script(tmp_path, overrides)
    metadata = _prepare_local_contrast(tmp_path, monkeypatch, source, primary, text)
    expected = {'cliplimit': 1.5, 'grid': 8, 'strength': .20, 'sigma': 2, **declared}
    assert metadata['local_contrast'] == expected
    assert native.validate_local_contrast(tmp_path, source, primary, native.script_commands(text), declared) == expected
    assert type(metadata['local_contrast']['grid']) is int


@pytest.mark.parametrize('overrides', [
    {'cliplimit': .49}, {'cliplimit': 5.01}, {'cliplimit': True}, {'cliplimit': '1.5'},
    {'cliplimit': None}, {'cliplimit': []}, {'cliplimit': float('nan')},
    {'cliplimit': float('inf')}, {'cliplimit': 10**400},
    {'grid': 1}, {'grid': 33}, {'grid': 8.0}, {'grid': False},
    {'strength': 0}, {'strength': -.1}, {'strength': .51},
    {'sigma': .49}, {'sigma': 16.01}, {'sigma': {}},
    {'low_start': False}, {'low_start': '0.03'}, {'low_start': -.01},
    {'low_end': .7}, {'high_end': 1.01}, {'blend': .2},
])
def test_local_contrast_rejects_invalid_parameters(tmp_path, monkeypatch, overrides):
    source, primary, text, declared = _local_contrast_script(tmp_path, overrides)
    with pytest.raises(ContractError) as error:
        _prepare_local_contrast(tmp_path, monkeypatch, source, primary, text)
    assert error.value.code == 'processing_contract_invalid'
    with pytest.raises(ContractError):
        native.validate_local_contrast(tmp_path, source, primary, native.script_commands(text), declared)


@pytest.mark.parametrize('declaration', [
    'null', '[]', '"invalid"', '{',
    '{"low_start":0.03,"low_end":0.08,"high_start":0.65}',
    '{"low_start":0.03,"low_start":0.03,"low_end":0.08,"high_start":0.65,"high_end":0.9}',
    '{"low_start":0.03,"low_end":0.08,"high_start":0.65,"high_end":0.9,"strength":0.2,"strength":0.3}',
    '{"low_start":0.03,"low_end":0.08,"high_start":0.65,"high_end":0.9,"sigma":1e309}',
])
def test_local_contrast_rejects_non_objects_missing_and_duplicate_keys(tmp_path, monkeypatch, declaration):
    source, primary, text, _ = _local_contrast_script(tmp_path)
    line = next(line for line in text.splitlines() if line.startswith('# local-contrast:'))
    with pytest.raises(ContractError) as error:
        _prepare_local_contrast(tmp_path, monkeypatch, source, primary,
                                text.replace(line, '# local-contrast: ' + declaration))
    assert error.value.code == 'processing_contract_invalid'


@pytest.mark.parametrize('count', [0, 2])
def test_local_contrast_requires_exactly_one_declaration(tmp_path, monkeypatch, count):
    source, primary, text, _ = _local_contrast_script(tmp_path)
    line = next(line for line in text.splitlines() if line.startswith('# local-contrast:'))
    with pytest.raises(ContractError, match='one recorded'):
        _prepare_local_contrast(tmp_path, monkeypatch, source, primary, text.replace(line, '\n'.join([line] * count)))


@pytest.mark.parametrize('old,new', [
    ('clahe 1.5 8', 'clahe 2.5 8'), ('clahe 1.5 8', 'clahe 1.5 12'),
    ('gauss 2', 'gauss 4'), ('1 + 0.2 *', '1 + 0.35 *'),
    ('/ 0.05', '/ 0.04'),
    ('080-r$ * $artifacts/080-gain$', '080-r$ * 1.1 * $artifacts/080-gain$'),
])
def test_local_contrast_rejects_undeclared_command_and_formula_changes(tmp_path, old, new):
    source, primary, text, declared = _local_contrast_script(tmp_path)
    assert old in text
    with pytest.raises(ContractError, match='weighted luminance'):
        native.validate_local_contrast(tmp_path, source, primary, native.script_commands(text.replace(old, new)), declared)


@pytest.mark.parametrize('overrides', [{}, {'cliplimit': 2.5, 'grid': 12, 'strength': .35, 'sigma': 4}], ids=['defaults', 'custom'])
def test_real_siril_luminance_clahe_mask_and_shared_gain(tmp_path, overrides):
    binary=Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():pytest.skip('Siril 1.4.4 unavailable')
    a=tmp_path/'artifacts';a.mkdir();(tmp_path/'previews').mkdir();source=tmp_path/'source.fit'
    y,x=np.mgrid[:192,:192]
    lum=.015+.25*np.exp(-((x-96)**2+(y-96)**2)/1800)+.06*np.exp(-((x-70)**2+(y-90)**2)/20)
    rgb=np.stack([lum*1.1,lum,lum*.9]);write_fits(source,rgb)
    source, primary, text, declared = _local_contrast_script(tmp_path, overrides)
    commands=native.script_commands(text)
    native.validate_local_contrast(tmp_path,source,primary,commands,declared)
    script=tmp_path/'contrast.ssf';script.write_text(text);config=tmp_path/'s.ini';config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result=subprocess.run([str(binary),f'--initfile={config}',f'--directory={tmp_path}','--offline',f'--script={script}'],capture_output=True,text=True,timeout=30)
    (tmp_path/'contrast.log').write_text(result.stdout)
    assert result.returncode==0,result.stdout
    out,scale,zero=read_fits_pixels(primary);output=np.asarray(out)*scale+zero
    assert np.isfinite(output).all() and np.max(np.abs(output-rgb))>.001
    np.testing.assert_allclose(output[0]/output[1],1.1,rtol=2e-6)
    np.testing.assert_allclose(output[2]/output[1],.9,rtol=2e-6)
    mask,ms,mz=read_fits_pixels(a/'080-mask.fit');mask=np.asarray(mask)*ms+mz
    assert np.isfinite(mask).all() and mask.min()>=0 and mask.max()<=1 and mask[0,0,0]<1e-5
    with Image.open(tmp_path/'previews/080-mask.jpg') as preview:
        preview.load()
        assert preview.size == (192, 192)
    corrupted=[list(row) for row in commands]
    next(row for row in corrupted if row[0]=='clahe')[1]='3'
    with pytest.raises(ContractError,match='weighted luminance'):
        native.validate_local_contrast(tmp_path,source,primary,corrupted,declared)


def test_real_siril_preserves_existing_wcs_without_solving(tmp_path):
    binary=Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():pytest.skip('Siril 1.4.4 unavailable')
    source=tmp_path/'wcs.fit';output=tmp_path/'preserved.fit'
    y,x=np.mgrid[:64,:64];values=.01+.001*x+.0001*y
    cards=[('CTYPE1',"'RA---TAN'"),('CTYPE2',"'DEC--TAN'"),('CRPIX1',32),('CRPIX2',32),
           ('CRVAL1',98),('CRVAL2',4),('CD1_1',-.001),('CD1_2',0),('CD2_1',0),('CD2_2',.001)]
    write_fits(source,values,cards)
    payload={'input':{'path':str(source)},'context':{'input_state':'linear'}}
    text=f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nsave "{output}" -chksum\nclose\n'
    metadata=native.prepare_processing(tmp_path,payload,'astrometry.solve',source,output,text,{},None,None,None,None)
    script=tmp_path/'preserve.ssf';script.write_text(text);config=tmp_path/'s.ini';config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result=subprocess.run([str(binary),f'--initfile={config}',f'--directory={tmp_path}','--offline',f'--script={script}'],capture_output=True,text=True,timeout=30)
    (tmp_path/'preserve.log').write_text(result.stdout)
    assert result.returncode==0,result.stdout
    assert native.validate_processing_outputs(tmp_path,source,output,metadata)['astrometry']['status']=='preserved_existing_solution'
    assert 'Running command: platesolve' not in result.stdout


def region_contract(path, source, rect=(0, 0, 2, 2), width=4, height=4):
    document = {'schema': 'starun-siril.regions.v1',
                'reference': {'sha256': sha256_file(source), 'width': width, 'height': height},
                'regions': [{'id': 'background-a', 'role': 'background', 'rect': list(rect)}]}
    path.write_text(json.dumps(document))
    return document


def test_region_channels_orientation_raw_values_and_immutability(tmp_path):
    source, candidate, roi = [tmp_path / n for n in ('source.fit', 'candidate.fit', 'regions.json')]
    a = np.zeros((3, 4, 4)); a[:, :2] = .9
    a[0, 2:, :2] = [[-1, .1], [.2, 1.2]]
    a[1, 2:, :2] = .3
    a[2, 2:, :2] = [[np.nan, np.inf], [.4, .5]]
    write_fits(source, a); write_fits(candidate, a)
    region_contract(roi, source)
    before = [sha256_file(p) for p in (source, candidate)]
    report = metrics.generate_metric_report(run_id='070-stretch', protocol='stretch', candidate_path=candidate,
                                            parent_path=source, regions_path=roi, output_path=tmp_path / 'region-metrics.json')
    regions = report['region_metrics']
    assert regions['status'] == 'uncertain' and regions['recommended_verdict'] == 'uncertain'
    channels = regions['regions'][0]['candidate']['channels']
    assert channels[0]['median'] == pytest.approx(.15)
    assert channels[0]['shadow_clip_rate'] == channels[0]['highlight_sat_rate'] == .25
    assert channels[1]['mad'] == 0 and channels[1]['p01'] == pytest.approx(.3)
    assert channels[2]['finite_count'] == channels[2]['nonfinite_count'] == 2
    assert [sha256_file(p) for p in (source, candidate)] == before
    assert regions['images']['parent']['sha256'] == before[0]
    assert channels[0]['p99'] > 1 and channels[0]['p01'] < 0


@pytest.mark.parametrize('rect', [(0,0,0,1), (-1,0,2,2), (0,0,5,2), (0,True,2,2), (0,.5,2,2)])
def test_region_contract_refuses_invalid_bounds(tmp_path, rect):
    source, roi = tmp_path / 'source.fit', tmp_path / 'regions.json'
    write_fits(source, np.full((4,4), .1)); region_contract(roi, source, rect=rect)
    with pytest.raises(ValueError, match='rectangle'):
        metrics.measure_regions(source, None, roi)


def test_region_hash_duplicates_geometry_empty_and_unsupported(tmp_path):
    source, candidate, roi = [tmp_path / n for n in ('source.fit', 'candidate.fit', 'regions.json')]
    write_fits(source, np.full((4,4), .1)); write_fits(candidate, np.full((3,4), .1))
    document = region_contract(roi, source)
    assert metrics.measure_regions(candidate, source, roi)['status'] == 'uncertain'
    document['regions'].append(document['regions'][0]); roi.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='unique'):
        metrics.measure_regions(source, None, roi)
    region_contract(roi, source); write_fits(source, np.full((4,4), .2))
    with pytest.raises(ValueError, match='hash changed'):
        metrics.measure_regions(source, None, roi)
    display = tmp_path / 'preview.jpg'; Image.new('RGB', (4,4)).save(display)
    region_contract(roi, display)
    assert metrics.measure_regions(display, None, roi)['status'] == 'unsupported'
    write_fits(source, np.full((4,4), np.nan)); region_contract(roi, source)
    region = metrics.measure_regions(source, None, roi)['regions'][0]['candidate']
    assert region['status'] == 'uncertain' and region['channels'][0]['finite_count'] == 0
    assert 'median' not in region['channels'][0]
    with pytest.raises(ValueError, match='overwrite'):
        metrics.generate_metric_report(run_id='001-test', protocol='input.inspect', candidate_path=source, output_path=source)


def test_region_integer_fits_scaling(tmp_path):
    p, roi = tmp_path / 'scaled.fit', tmp_path / 'regions.json'
    a = np.full((4,4), -30000, dtype='>i2')
    cards = [('SIMPLE','T'), ('BITPIX',16), ('NAXIS',2), ('NAXIS1',4), ('NAXIS2',4), ('BZERO',32768), ('BSCALE',2)]
    h = (''.join(f'{k:<8}= {v:>20}'.ljust(80) for k,v in cards) + 'END'.ljust(80)).encode()
    h += b' ' * (-len(h) % 2880); data = a.tobytes(); p.write_bytes(h + data + b'\0' * (-len(data) % 2880))
    region_contract(roi, p)
    channel = metrics.measure_regions(p, None, roi)['regions'][0]['candidate']['channels'][0]
    assert channel['median'] == pytest.approx((-60000 + 32768) / 65535)
    assert channel['shadow_clip_rate'] == 1


def test_region_coordinate_frame_matches_real_siril_preview(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file(): pytest.skip('Siril unavailable')
    source, roi, preview = [tmp_path / n for n in ('orientation.fit', 'regions.json', 'orientation.jpg')]
    a = np.full((16,16), .1); a[-8:, :8] = .8
    write_fits(source, a)
    region_contract(roi, source, rect=(0,0,8,8), width=16, height=16)
    config = tmp_path / 's.ini'; config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    script = tmp_path / 'orientation.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nsavejpg "{preview.with_suffix("")}" 100\nclose\n')
    completed = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}', '--offline', f'--script={script}'],
                               cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout
    with Image.open(preview) as image:
        pixels = np.asarray(image)
        assert pixels[2,2].mean() > pixels[-3,2].mean()
    channel = metrics.measure_regions(source, None, roi)['regions'][0]['candidate']['channels'][0]
    assert channel['median'] == pytest.approx(.8)


def test_region_unreadable_and_cube_do_not_claim_measurement(tmp_path):
    p, roi = tmp_path / 'broken.fit', tmp_path / 'regions.json'
    p.write_bytes(b'not a FITS image'); region_contract(roi, p)
    assert metrics.measure_regions(p, None, roi)['status'] == 'uncertain'
    write_fits(p, np.full((4,4), .2), [('NAXIS',4), ('NAXIS3',1), ('NAXIS4',2)])
    region_contract(roi, p)
    report = metrics.measure_regions(p, None, roi)
    assert report['status'] == 'uncertain' and not report['regions']


def test_recomposition_star_reduction_strength_and_noise_residual_tolerance(tmp_path):
    # Test strength within [0.30, 1.00]
    p_full, p_base, p_stars, p_primary = [tmp_path / f'artifacts/{n}.fit' for n in ('full', 'base', 'stars', 'cand')]
    tmp_path.joinpath('artifacts').mkdir(exist_ok=True)
    chain = [{'command': 'mtf', 'parameters': ['0', '.1', '1']}]
    source = tmp_path / 'artifacts/starless_parent.fit'
    original = tmp_path / 'artifacts/orig.fit'
    starless = tmp_path / 'artifacts/starless.fit'
    for path in (source, original, starless):
        write_fits(path, np.full((3, 32, 32), .1))

    # Test allowed strength = 0.50 and 0.60
    for st in ('0.50', '0.60'):
        steps, paths = recompose_steps(tmp_path, chain, original=original, starless=starless, source=source)
        steps[-2][1] = f'$artifacts/starless_parent$ + {st} * $artifacts/stars$'
        roles = native.validate_recomposition(tmp_path, steps, source, original, starless, paths[-1], chain)
        assert roles['strength'] == float(st)

    # Test strength < 0.30 rejected
    steps, paths = recompose_steps(tmp_path, chain, original=original, starless=starless, source=source)
    steps[-2][1] = '$artifacts/starless_parent$ + 0.20 * $artifacts/stars$'
    with pytest.raises(ContractError, match='Star strength must be within'):
        native.validate_recomposition(tmp_path, steps, source, original, starless, paths[-1], chain)

    # Test noise residual tolerance (e.g. -0.008 with negligible negative energy passes)
    a = np.full((3, 32, 32), .2)
    s = np.full((3, 32, 32), .05)
    s[0, 0, 0] = -0.008  # isolated background shot noise fluctuation
    i = a + s  # Exact closure a + s == i

    write_fits(paths[0], i)
    write_fits(paths[1], a)
    write_fits(paths[2], s)
    roles = {'full_baseline': 'artifacts/full.fit', 'original_starless': 'artifacts/base.fit', 'display_stars': 'artifacts/stars.fit', 'strength': .6}
    write_fits(paths[-1], np.full_like(a, .1) + .6*s)
    res = native.validate_processing_outputs(tmp_path, source, paths[-1], {'branch_binding': roles})
    assert 'closure' in res
    assert res['closure']['per_channel_min_residual'][0] == pytest.approx(-0.008)

    # A large negative energy ratio warns even when the baseline closes exactly.
    s_bad = np.full((3, 32, 32), .05)
    s_bad[:, :16, :] = -0.01  # half image negative -> huge negative energy ratio
    i_bad = a + s_bad
    write_fits(paths[0], i_bad)
    write_fits(paths[2], s_bad)
    write_fits(paths[-1], np.full_like(a, .1) + .6*s_bad)
    assert native.validate_processing_outputs(tmp_path, source, paths[-1], {'branch_binding': roles})['closure']['warnings']
    with pytest.raises(ContractError, match='Recomposition rejected'):
        native.validate_processing_outputs(tmp_path, source, paths[-1], {'branch_binding': roles}, execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})

    # A ratio between 0.50% and 1.00% is rejected for general and accepted for galaxy.
    s_marginal = np.full((3, 32, 32), 0.1)
    # 80 negative pixels yield 0.8 / (94.4 + 0.8), approximately 0.84%.
    s_marginal[0, :8, :10] = -0.01
    i_marginal = a + s_marginal
    write_fits(paths[0], i_marginal)
    write_fits(paths[2], s_marginal)

    write_fits(paths[-1], np.full_like(a, .1) + .6*s_marginal)
    assert native.validate_processing_outputs(tmp_path, source, paths[-1], {'branch_binding': roles, 'target_type': 'general'})['closure']['warnings']
    # Historical general target type retains the hard ratio gate.
    with pytest.raises(ContractError, match='Recomposition rejected.*max allowed 0.0050'):
        native.validate_processing_outputs(tmp_path, source, paths[-1], {'branch_binding': roles, 'target_type': 'general'}, execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})

    # Galaxy target_type (threshold 0.010) passes
    res_galaxy = native.validate_processing_outputs(tmp_path, source, paths[-1], {'branch_binding': roles, 'target_type': 'galaxy'})
    assert 'closure' in res_galaxy
    assert res_galaxy['closure']['target_type'] == 'galaxy'
    assert res_galaxy['closure']['max_allowed_neg_energy_ratio'] == 0.010
    assert 0.005 < res_galaxy['closure']['per_channel_neg_energy_ratio'][0] <= 0.010



COLOR_WCS = [('CTYPE1', "'RA---TAN'"), ('CTYPE2', "'DEC--TAN'"),
             ('CRPIX1', 16), ('CRPIX2', 16), ('CRVAL1', 23.47), ('CRVAL2', 30.65),
             ('CD1_1', -.0003), ('CD1_2', 0), ('CD2_1', 0), ('CD2_2', .0003)]


def color_case(tmp_path, *, wcs=True, channel='broadband'):
    (tmp_path / 'artifacts').mkdir(exist_ok=True)
    (tmp_path / 'reports').mkdir(exist_ok=True)
    source, primary = tmp_path / 'input.fit', tmp_path / 'artifacts/color.fit'
    values = np.full((3, 32, 32), .1)
    write_fits(source, values, COLOR_WCS if wcs else [])
    payload = {'input': {'path': str(source)}, 'context': {'input_state': 'linear', 'channel_mode': channel, 'offline': False}}
    names = {'monosensor': ['Test Sensor'], 'redfilter': ['Test R'], 'greenfilter': ['Test G'],
             'bluefilter': ['Test B'], 'whiteref': ['Average Spiral Galaxy']}
    probe = {'tools': {'spcc_profiles': {'compatible': True, 'names': names}}}
    return source, primary, payload, probe


def color_metadata(tmp_path, source, primary, payload, probe, text):
    return native.prepare_processing(tmp_path, payload, 'color.calibrate', source, primary,
                                     text, probe, None, None, None, None)['color_calibration']


def pcc_script(source, *, compatibility=None):
    record = compatibility if compatibility is not None else {
        'value': 'standard_rgb', 'source': 'user', 'evidence': 'User confirmed ordinary RGB and IR blocking'}
    evidence = {'method': 'pcc', 'source_sha256': sha256_file(source),
                'equipment': {'pcc_compatibility': record}}
    return '# color-calibration: ' + json.dumps(evidence) + f'\nload "{source}"\npcc -catalog=gaia'


def spcc_script(source, *, omitted=None, extra='', equipment=True):
    options = {'catalog': 'gaia', 'monosensor': 'Test Sensor', 'rfilter': 'Test R', 'gfilter': 'Test G',
               'bfilter': 'Test B', 'whiteref': 'Average Spiral Galaxy'}
    if omitted:
        options.pop(omitted)
    evidence = {'method': 'spcc', 'source_sha256': sha256_file(source),
                'equipment': {key: {'source': 'acquisition', 'evidence': 'Recorded test equipment'}
                              for key in ('monosensor', 'rfilter', 'gfilter', 'bfilter')} if equipment else {},
                'white_reference': {'source': 'policy_default', 'evidence': 'Explicit standard spiral-galaxy reference'}}
    return '# color-calibration: ' + json.dumps(evidence) + '\nload "' + str(source) + '"\nspcc ' + ' '.join('"-' + k + '=' + v + '"' for k, v in options.items()) + extra


def test_remote_astrometry_without_local_catalogue_requires_real_hints(tmp_path):
    source, primary, payload, _ = color_case(tmp_path, wcs=False)
    text = f'load "{source}"\nplatesolve -catalog=gaia -noflip -nocrop'
    with pytest.raises(ContractError, match='file evidence'):
        native.prepare_processing(tmp_path, payload, 'astrometry.solve', source, primary, text, {}, None, None, None, None)
    write_fits(source, np.full((3, 32, 32), .1), [('RA', 23.47), ('DEC', 30.65), ('FOCALLEN', 746.6), ('XPIXSZ', 3.76)])
    metadata = native.prepare_processing(tmp_path, payload, 'astrometry.solve', source, primary, text, {}, None, None, None, None)
    # Static validation runs before Siril, so it may only record a requested solve.
    assert metadata['astrometry']['status'] == 'solve_requested_remote_gaia'
    assert 'catalogue' not in metadata['astrometry']
    payload['context']['offline'] = True
    with pytest.raises(ContractError) as error:
        native.prepare_processing(tmp_path, payload, 'astrometry.solve', source, primary, text, {}, None, None, None, None)
    assert error.value.code == 'network_forbidden'


def solved_wcs_cards():
    return [('CTYPE1', "'RA---TAN'"), ('CTYPE2', "'DEC--TAN'"), ('CRPIX1', 16), ('CRPIX2', 16),
            ('CRVAL1', 23.47), ('CRVAL2', 30.65), ('CD1_1', -.0004), ('CD1_2', 0),
            ('CD2_1', 0), ('CD2_2', .0004)]


def test_solved_status_is_claimed_only_after_output_is_measured(tmp_path):
    # A requested solve becomes solved_* solely through post-execution measurement.
    source, primary, payload, _ = color_case(tmp_path, wcs=False)
    write_fits(source, np.full((3, 32, 32), .1), [('RA', 23.47), ('DEC', 30.65), ('FOCALLEN', 746.6), ('XPIXSZ', 3.76)])
    text = f'load "{source}"\nplatesolve -catalog=gaia -noflip -nocrop'
    metadata = native.prepare_processing(tmp_path, payload, 'astrometry.solve', source, primary, text, {}, None, None, None, None)
    assert metadata['astrometry']['status'] == 'solve_requested_remote_gaia'
    # An output written without a solution must not be reported as achieved.
    write_fits(primary, np.full((3, 32, 32), .1))
    with pytest.raises(ContractError, match='complete WCS'):
        native.validate_processing_outputs(tmp_path, source, primary, metadata)
    write_fits(primary, np.full((3, 32, 32), .1), solved_wcs_cards())
    assert native.validate_processing_outputs(tmp_path, source, primary, metadata)['astrometry']['status'] == 'solved_remote_gaia'
    # A failed run never reaches validation, so its receipt keeps only the request.
    failed = {'status': 'failed', 'exit_code': 1, 'astrometry': metadata['astrometry']}
    assert failed['astrometry']['status'] not in ('solved_remote_gaia', 'solved_local_gaia')
    assert not Path(primary).exists() or native.ASTROMETRY_SOLVED_STATUS.get(failed['astrometry']['status'])


@pytest.mark.parametrize('channel,wcs,message', [('unknown', True, 'confirmed broadband'), ('dualband-osc', True, 'confirmed broadband'), ('broadband', False, 'WCS')])
def test_pcc_rejects_unknown_channels_or_missing_wcs(tmp_path, channel, wcs, message):
    source, primary, payload, probe = color_case(tmp_path, wcs=wcs, channel=channel)
    with pytest.raises(ContractError, match=message):
        color_metadata(tmp_path, source, primary, payload, probe, f'load "{source}"\npcc -catalog=gaia')


def test_spcc_requires_all_explicit_responses_and_sources(tmp_path):
    source, primary, payload, probe = color_case(tmp_path)
    metadata = color_metadata(tmp_path, source, primary, payload, probe, spcc_script(source))
    assert metadata['method'] == 'spcc' and metadata['claim'] == 'spectrophotometric'
    assert metadata['parameters']['whiteref'] == 'Average Spiral Galaxy'
    for invalid_source in ({}, []):
        with pytest.raises(ContractError, match='source is unsupported'):
            native._spectral_evidence({'source': invalid_source, 'evidence': 'Malformed source'}, 'Test Sensor', {})
    for missing in ('monosensor', 'rfilter', 'gfilter', 'bfilter', 'whiteref'):
        with pytest.raises(ContractError):
            color_metadata(tmp_path, source, primary, payload, probe, spcc_script(source, omitted=missing))
    with pytest.raises(ContractError, match='evidence'):
        color_metadata(tmp_path, source, primary, payload, probe, spcc_script(source, equipment=False))
    with pytest.raises(ContractError, match='duplicate'):
        color_metadata(tmp_path, source, primary, payload, probe, spcc_script(source, extra=' -rfilter=Test'))
    probe['tools']['spcc_profiles']['names']['redfilter'] = []
    with pytest.raises(ContractError) as error:
        color_metadata(tmp_path, source, primary, payload, probe, spcc_script(source))
    assert error.value.code == 'spcc_response_unavailable'
    assert color_metadata(tmp_path, source, primary, payload, probe, pcc_script(source))['method'] == 'pcc'


def test_mixed_filter_cannot_be_used_as_rgb_response_evidence(tmp_path):
    source, primary, payload, probe = color_case(tmp_path)
    write_fits(source, np.full((3, 32, 32), .1), COLOR_WCS + [('FILTER', "'mixed'")])
    text = spcc_script(source)
    record, commands = text.split('\n', 1)
    evidence = json.loads(record.split(':', 1)[1])
    evidence['equipment']['rfilter'] = {'source': 'fits_header', 'key': 'FILTER', 'evidence': 'Read FILTER from FITS'}
    with pytest.raises(ContractError, match='differs from'):
        color_metadata(tmp_path, source, primary, payload, probe, '# color-calibration: ' + json.dumps(evidence) + '\n' + commands)


def test_native_osc_spcc_accepts_bound_manufacturer_passbands_without_hoo(tmp_path):
    source, primary, payload, probe = color_case(tmp_path, channel='dualband-osc')
    write_fits(source, np.full((3, 32, 32), .1), COLOR_WCS + [('FILTER', "'dual-band'")])
    probe['tools']['spcc_profiles']['names']['oscsensor'] = ['Test OSC']
    parameters = dict(rwl=656.3, rbw=20, gwl=500.7, gbw=30, bwl=500.7, bbw=30)
    equipment = {
        'oscsensor': {'source': 'acquisition', 'evidence': 'Recorded OSC sensor'},
        'channel_layout': {'value': 'native_osc', 'source': 'user', 'evidence': 'Native RGB, no extraction or remapping'},
        'passbands': {'source': 'manufacturer', 'evidence': 'https://example.org/test-filter',
                      'filter': 'dual-band', 'parameters': parameters.copy()},
    }
    evidence = {'method': 'spcc', 'source_sha256': sha256_file(source), 'equipment': equipment,
                'white_reference': {'source': 'policy_default', 'evidence': 'Explicit standard spiral-galaxy reference'}}
    commands = (f'load "{source}"\nspcc -catalog=gaia "-oscsensor=Test OSC" '
                '"-whiteref=Average Spiral Galaxy" -narrowband ' +
                ' '.join(f'-{k}={v}' for k, v in parameters.items()))
    def run():
        return color_metadata(tmp_path, source, primary, payload, probe,
                              '# color-calibration: ' + json.dumps(evidence) + '\n' + commands)
    assert run()['equipment'] == equipment
    assert payload['context'].get('channel_map') is None
    equipment['passbands']['parameters']['rbw'] = 10
    with pytest.raises(ContractError, match='differ.*SPCC parameters'):
        run()
    equipment['passbands']['parameters']['rbw'] = 20
    equipment['passbands']['filter'] = 'another-filter'
    with pytest.raises(ContractError, match='actual header filter'):
        run()
    equipment['passbands']['filter'] = 'dual-band'
    equipment['passbands']['evidence'] = 'Guessed from RGB colours'
    with pytest.raises(ContractError, match='HTTPS'):
        run()
    equipment['passbands']['evidence'] = 'https://example.org/test-filter'
    equipment.pop('channel_layout')
    with pytest.raises(ContractError, match='native RGB history'):
        run()


@pytest.mark.parametrize('channel', ['broadband', 'dualband-osc'])
def test_pcc_display_fallback_requires_user_evidence_and_limits_color_claim(tmp_path, channel):
    source, primary, payload, probe = color_case(tmp_path, channel=channel)
    evidence = {'method': 'pcc', 'source_sha256': sha256_file(source),
                'fallback_reason': 'Actual response does not support standard RGB PCC',
                'equipment': {'pcc_fallback': {'source': 'user', 'evidence': 'User explicitly selected a display experiment'}}}
    def run():
        text = '# color-calibration: ' + json.dumps(evidence) + f'\nload "{source}"\npcc -catalog=gaia'
        return color_metadata(tmp_path, source, primary, payload, probe, text)
    result = run()
    assert result['claim'] == 'display_color_balance'
    assert result['fallback_evidence'] == evidence['equipment']['pcc_fallback']
    evidence['fallback_reason'] = None
    with pytest.raises(ContractError, match='explicit user evidence'):
        run()
    evidence['fallback_reason'] = 'Response unavailable'
    for invalid_source in ('policy_default', 'fits_header', 'acquisition'):
        evidence['equipment']['pcc_fallback']['source'] = invalid_source
        with pytest.raises(ContractError, match='explicit user evidence'):
            run()
    evidence['equipment']['pcc_fallback']['source'] = 'user'
    payload['context']['channel_mode'] = 'unknown'
    with pytest.raises(ContractError, match='confirmed broadband'):
        run()


@pytest.mark.parametrize('source_type', ['user', 'acquisition', 'manufacturer'])
def test_pcc_binds_sourced_standard_rgb_compatibility(tmp_path, source_type):
    source, primary, payload, probe = color_case(tmp_path)
    record = {'value': 'standard_rgb', 'source': source_type,
              'evidence': 'https://example.org/actual-response' if source_type == 'manufacturer' else 'Actual RGB acquisition record'}
    result = color_metadata(tmp_path, source, primary, payload, probe, pcc_script(source, compatibility=record))
    assert result['claim'] == 'photometric' and result['compatibility_evidence'] == record
    assert 'fallback_evidence' not in result


@pytest.mark.parametrize('record', [{}, {'value': 'unknown', 'source': 'user', 'evidence': 'x'},
    {'value': 'standard_rgb', 'source': 'policy_default', 'evidence': 'x'},
    {'value': 'standard_rgb', 'source': {}, 'evidence': 'x'},
    {'value': 'standard_rgb', 'source': 'acquisition', 'evidence': ''},
    {'value': 'standard_rgb', 'source': 'manufacturer', 'evidence': 'http://example.org/response'}])
def test_pcc_rejects_missing_or_invalid_compatibility(tmp_path, record):
    source, primary, payload, probe = color_case(tmp_path)
    with pytest.raises(ContractError):
        color_metadata(tmp_path, source, primary, payload, probe, pcc_script(source, compatibility=record))
    with pytest.raises(ContractError, match='compatibility evidence'):
        color_metadata(tmp_path, source, primary, payload, probe, f'load "{source}"\npcc -catalog=gaia')


def test_pcc_rejects_compatibility_conflicting_with_filter_or_display_mode(tmp_path):
    source, primary, payload, probe = color_case(tmp_path)
    write_fits(source, np.full((3, 32, 32), .1), COLOR_WCS + [('FILTER', "'L-Extreme'")])
    with pytest.raises(ContractError, match='actual filter'):
        color_metadata(tmp_path, source, primary, payload, probe, pcc_script(source))
    text = pcc_script(source)
    evidence = json.loads(text.splitlines()[0].split(': ', 1)[1])
    evidence['fallback_reason'] = 'Display only'
    evidence['equipment']['pcc_fallback'] = {'source': 'user', 'evidence': 'Display experiment'}
    text = '# color-calibration: ' + json.dumps(evidence) + '\n' + '\n'.join(text.splitlines()[1:])
    with pytest.raises(ContractError, match='conflict'):
        color_metadata(tmp_path, source, primary, payload, probe, text)


def test_separation_channels_distinguish_common_loss_raw_zero_and_absent_channels():
    plane = np.linspace(.001, .1, 10000).reshape(100, 100)
    parent = np.asarray([plane, .2 * plane, .1 * plane])
    common = metrics.compare_separation_channels(parent, parent * .3)
    assert all(c['loss_flag'] for c in common['channels'])
    assert all(c['signal_retention'] == pytest.approx(.3) for c in common['channels'])
    assert all(c['raw_zero_candidate'] == 0 for c in common['channels'])
    zeroed = parent.copy(); zeroed[1] = 0
    assert metrics.compare_separation_channels(parent, zeroed)['channels'][1]['raw_zero_candidate'] == 1
    parent[1] *= .01
    weak = metrics.compare_separation_channels(parent, zeroed)['channels'][1]
    assert not weak['present_in_parent'] and not weak['loss_flag']
    assert not any(c['loss_flag'] for c in metrics.compare_separation_channels(parent, parent)['channels'])
    assert metrics.compare_separation_channels(np.ones((3, 20, 20)), np.ones((3, 20, 20)))['status'] == 'uncertain'


def neutral_case(tmp_path, *, white=False, channel='broadband', wcs=True):
    source, primary, payload, probe = color_case(tmp_path, channel=channel, wcs=wcs)
    payload['execution_policy'] = dict(EXECUTION_POLICY)
    values = np.empty((3, 32, 32))
    for channel, background in enumerate((.02, .04, .03)):
        values[channel] = background
    values[:, 20:24, 20:24] += np.asarray((.12, .16, .08))[:, None, None]
    values[0, 12, 12] = 1.2
    values[1, 12, 12] = -.001
    write_fits(source, values, COLOR_WCS if wcs else [])
    regions = tmp_path / 'reports/regions.json'
    entries = [{'id': 'bg-' + str(i), 'role': 'background', 'rect': rect} for i, rect in enumerate(([0,0,8,8], [16,0,24,8], [0,16,8,24], [8,0,16,8], [24,0,32,8], [0,24,8,32]))]
    entries.append({'id': 'white', 'role': 'stars', 'rect': [20, 8, 24, 12]})
    regions.write_text(json.dumps({'schema': 'starun-siril.regions.v1', 'reference': {'sha256': sha256_file(source), 'width': 32, 'height': 32}, 'regions': entries}))
    reference = {'source': 'user', 'evidence': 'User identified an unsaturated white reference', 'region': 'white'} if white else None
    partition = {'fit': ['bg-0', 'bg-1', 'bg-2'], 'validation': ['bg-3', 'bg-4', 'bg-5']}
    coefficients = native.neutral_coefficients(source, regions, reference, background_partition=partition)
    evidence = {'method': 'neutral', 'source_sha256': sha256_file(source), 'background_regions': 'reports/regions.json',
                'white_reference': reference, 'background_partition': partition,
                'fallback_reason': 'Photometry unavailable in isolated test'}
    lines = ['requires 1.4.4 1.5.0', 'set32bits', '# color-calibration: ' + json.dumps(evidence), f'load "{source}"', 'fmul 1']
    raw = [tmp_path / f'artifacts/raw-{i}.fit' for i in range(3)]
    corrected = [tmp_path / f'artifacts/corrected-{i}.fit' for i in range(3)]
    lines += ['split ' + ' '.join('"' + str(p.with_suffix('')) + '"' for p in raw), 'close']
    for i in range(3):
        b, g, target = coefficients['background'][i], coefficients['gains'][i], coefficients['target']
        lines += [f'pm "($artifacts/raw-{i}$ - {b:.17g}) * {g:.17g} + {target:.17g}"', f'save "{corrected[i]}" -chksum', 'close']
    lines += ['rgbcomp ' + ' '.join('"' + str(p) + '"' for p in corrected) + f' "-out={primary}" -nosum', 'close', f'load "{source}"', 'mtf 0 0.15 1', f'savejpg "{tmp_path}/previews/neutral-parent" 95', 'close',
              f'load "{primary}"', 'mtf 0 0.15 1', f'savejpg "{tmp_path}/previews/neutral-candidate" 95', 'close']
    (tmp_path / 'previews').mkdir(exist_ok=True)
    return source, primary, payload, probe, '\n'.join(lines) + '\n', regions, coefficients


@pytest.mark.parametrize('white,channel,wcs', [(False, 'broadband', True),
                                            (True, 'broadband', True),
                                            (False, 'dualband-osc', False)])
def test_real_siril_neutral_background_and_white_reference(tmp_path, white, channel, wcs):
    binary = shutil.which('siril-cli') or '/Applications/Siril.app/Contents/MacOS/siril-cli'
    if not Path(binary).is_file():
        pytest.skip('Siril 1.4.4 not installed')
    source, primary, payload, probe, text, regions, coefficients = neutral_case(
        tmp_path, white=white, channel=channel, wcs=wcs)
    metadata = native.prepare_processing(tmp_path, payload, 'color.calibrate', source, primary, text, probe, None, None, None, None)
    script, config = tmp_path / 'neutral.ssf', tmp_path / 'siril.ini'
    script.write_text(text)
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n[gui]\nuse_scripts_repository=false\nuse_spcc_repository=false\n')
    import deep_sky_siril_tooling as tooling
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}', '--offline', f'--script={script}'],
                            capture_output=True, text=True, timeout=30, env=tooling._subprocess_environment('siril_runtime'))
    assert result.returncode == 0, result.stdout + result.stderr
    checks = native.validate_processing_outputs(tmp_path, source, primary, metadata)['color_calibration']
    assert max(checks['per_channel_max_abs']) <= 1e-6
    measured = metrics.measure_regions(primary, source, regions)
    background = [c['median'] for c in measured['regions'][0]['candidate']['channels']]
    assert max(background) - min(background) <= 1e-7
    if white:
        flux = [c['median'] for c in measured['regions'][-1]['candidate']['channels']]
        assert max(flux) - min(flux) <= 1e-7
    else:
        assert coefficients['gains'] == [1, 1, 1]
        before = measured['regions'][-1]['parent']['channels']
        after = measured['regions'][-1]['candidate']['channels']
        assert [c['median'] - coefficients['target'] for c in after] == pytest.approx(
            [c['median'] - b for c, b in zip(before, coefficients['background'])], abs=1e-7)
        assert metadata['color_calibration']['claim'] == 'background_neutralization'
    regions.write_text(regions.read_text() + ' ')
    with pytest.raises(ContractError, match='changed'):
        native.validate_processing_outputs(tmp_path, source, primary, metadata)


def test_neutral_rejects_insufficient_regions_and_unsafe_operations(tmp_path):
    source, primary, payload, probe, text, regions, _ = neutral_case(tmp_path)
    with pytest.raises(ContractError, match='formula'):
        color_metadata(tmp_path, source, primary, payload, probe, text.replace(' * 1 +', ' * 2 +', 1))
    contract = json.loads(regions.read_text())
    contract['regions'] = contract['regions'][:2]
    regions.write_text(json.dumps(contract))
    with pytest.raises(ContractError, match='three background'):
        native.neutral_coefficients(source, regions)


def test_dualband_neutral_forbids_reference_gains_and_unknown_channels(tmp_path):
    source, primary, payload, probe, text, _, _ = neutral_case(tmp_path, white=True, channel='dualband-osc')
    with pytest.raises(ContractError, match='forbids white-reference gains'):
        color_metadata(tmp_path, source, primary, payload, probe, text)
    source, primary, payload, probe, text, _, _ = neutral_case(tmp_path, channel='dualband-osc')
    assert color_metadata(tmp_path, source, primary, payload, probe, text)['coefficients']['gains'] == [1, 1, 1]
    for channel in ('unknown', 'narrowband', 'mono'):
        payload['context']['channel_mode'] = channel
        with pytest.raises(ContractError, match='confirmed broadband'):
            color_metadata(tmp_path, source, primary, payload, probe, text)


def test_auto_rgb_regions_exclude_single_channel_signal_and_match_preview_coordinates(tmp_path):
    source, regions = tmp_path / 'source.fit', tmp_path / 'regions.json'
    y, x = np.mgrid[:160, :160]
    a = np.asarray([.02 + 1e-6*x, .04 + 1e-6*x, .03 + 1e-6*x])
    a[0, 25:60, 25:60] += .2
    a[2, 90:125, 90:125] += .3
    write_fits(source, a)
    original = source.read_bytes()
    contract = samples.generate_background_regions(source, target_type='emission_nebula')
    regions.write_text(json.dumps(contract))
    report = metrics.measure_regions(source, None, regions)
    assert report['status'] == 'measured' and len(report['regions']) >= 3
    for r in report['regions']:
        x0, y0, x1, y1 = r['rect']
        expected = a[:, 160-y1:160-y0, x0:x1]
        assert expected.shape[1:] == (16, 16)
        assert all(c['finite_count'] == 256 for c in r['candidate']['channels'])
        assert np.max(expected) < .05
        assert [c['median'] for c in r['candidate']['channels']] == pytest.approx(
            np.median(expected, axis=(1, 2)), abs=1e-7)
    assert source.read_bytes() == original
    assert samples.main(['--source', str(source), '--output', str(regions), '--regions',
                         '--target-type', 'emission_nebula']) == 0
    assert json.loads(regions.read_text()) == contract


def test_auto_regions_reject_unsafe_input_without_writing_contract(tmp_path):
    source, output = tmp_path / 'source.fit', tmp_path / 'regions.json'
    write_fits(source, np.full((3, 100, 100), .1))
    with pytest.raises(ValueError, match='not a preview'):
        samples.main(['--source', str(source), '--output', str(output), '--regions', '--preview', 'unused.jpg'])
    assert not output.exists()
    for size in (7, 65):
        with pytest.raises(ValueError, match='pixel size'):
            samples.generate_background_regions(source, region_size=size)
    write_fits(source, np.full((100, 100), .1))
    with pytest.raises(ValueError, match='scientific RGB'):
        samples.generate_background_regions(source)
    write_fits(source, np.full((3, 100, 100), 0))
    with pytest.raises(ValueError, match='reliable background points|three RGB background'):
        samples.generate_background_regions(source)
    assert not output.exists()


def test_calibration_fallback_does_not_stack_previous_correction(tmp_path, monkeypatch):
    source, primary, payload, probe = color_case(tmp_path)
    prior = {'id': '040-spcc', 'protocol': 'color.calibrate', 'source_path': '@input',
             'primary_output': 'artifacts/spcc.fit', 'color_calibration': {'method': 'spcc'}}
    import deep_sky_siril_session as session
    monkeypatch.setattr(session, '_verified_success_receipts', lambda _: [prior])
    text = pcc_script(source)
    with pytest.raises(ContractError, match='pre-calibration parent'):
        native.prepare_color_calibration(tmp_path, payload, source, primary, native.script_commands(text), text, probe, {'run': prior})
    assert native.prepare_color_calibration(tmp_path, payload, source, primary, native.script_commands(text), text, probe, {'run': None})['method'] == 'pcc'


def test_split_output_normalization_matches_native_suffix_append(tmp_path):
    from deep_sky_siril_validation import _normalized_write
    assert _normalized_write('split', str(tmp_path / 'red')) == tmp_path / 'red.fit'
    assert _normalized_write('split', str(tmp_path / 'red.fit')) == tmp_path / 'red.fit.fit'


def test_native_spcc_profile_probe_is_offline_and_reads_exact_names():
    import deep_sky_siril_tooling as tooling
    binary = tooling.discover_siril()
    if binary is None:
        pytest.skip('Siril not installed')
    profiles = tooling._probe_spcc_profiles(binary)
    assert profiles['compatible'], profiles
    assert 'Average Spiral Galaxy' in profiles['names']['whiteref']
    assert profiles['source'] == 'native_spcc_list_offline'


def test_neutral_rejects_saturated_or_unidentified_white_reference(tmp_path):
    source, _, _, _, _, regions, _ = neutral_case(tmp_path, white=True)
    with pytest.raises(ContractError, match='explicitly identified'):
        native.neutral_coefficients(source, regions, {'source': 'policy_default', 'region': 'white'})
    values, scale, zero = read_fits_pixels(source)
    saturated = np.asarray(values, dtype=float) * scale + zero
    saturated[:, 20:24, 20:24] = 1.0
    write_fits(source, saturated, COLOR_WCS)
    document = json.loads(regions.read_text())
    document['reference']['sha256'] = sha256_file(source)
    regions.write_text(json.dumps(document))
    with pytest.raises(ContractError, match='unsaturated'):
        native.neutral_coefficients(source, regions, {'source': 'user', 'evidence': 'Known reference', 'region': 'white'})


def test_color_preview_stretch_cannot_be_saved_as_linear_science(tmp_path):
    source, primary, payload, probe = color_case(tmp_path)
    text = f'load "{source}"\npcc -catalog=gaia\nautostretch -linked -2.8 .22\nsave "{primary}"'
    with pytest.raises(ContractError, match='Preview stretching'):
        color_metadata(tmp_path, source, primary, payload, probe, text)


def test_neutral_receipt_region_binding_detects_later_drift(tmp_path, monkeypatch):
    import deep_sky_siril_core as core
    import deep_sky_siril_session as session
    import deep_sky_siril_tooling as tooling
    from tests.test_deep_sky_siril import _fake_probe, _write_ssf_provenance
    binary = tooling.discover_siril()
    if binary is None:
        pytest.skip('Siril not installed')
    case = tmp_path / 'case'
    case.mkdir()
    source, _, _, _, text, regions, _ = neutral_case(case)
    monkeypatch.setattr(tooling, 'probe_tools', lambda *, offline=False: _fake_probe(binary, offline=offline))
    destination = tmp_path / 'session'
    session.init_session(str(source), str(destination), input_state='linear', state_evidence=['user confirmed input_state=linear; synthetic RGB'],
        channel_mode='broadband', channel_map=None, target_name='Synthetic', target_type='general', style='natural',
        stars='adaptive', offline=True, keep_intermediates=True)
    inspect = destination / 'scripts/010-input.ssf'
    parent_preview = destination / 'previews/010-input-autostretch.jpg'
    direct = destination / 'previews/010-input-direct.jpg'
    inspect.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nstat main\n'
                       f'savejpg "{direct.with_suffix("")}" 95\nclose\nload "{source}"\n'
                       f'autostretch -linked -2.8 0.22\nsavejpg "{parent_preview.with_suffix("")}" 95\nclose\n')
    _write_ssf_provenance(destination, inspect, 'input.inspect')
    assert core.run_script(str(destination), protocol='input.inspect', script_value=str(inspect), source_value=str(source),
                           expected_values=[str(direct), str(parent_preview)], timeout=30)['status'] == 'success'
    shutil.copyfile(regions, destination / 'reports/regions.json')
    text = text.replace(str(case / 'artifacts'), str(destination / 'artifacts')).replace(str(case / 'previews'), str(destination / 'previews'))
    script = destination / 'scripts/040-neutral.ssf'
    script.write_text(text)
    _write_ssf_provenance(destination, script, 'color.calibrate')
    paths = [destination / ('artifacts/' + name + '.fit') for name in
             ('raw-0', 'raw-1', 'raw-2', 'corrected-0', 'corrected-1', 'corrected-2', 'color')]
    receipt = core.run_script(str(destination), protocol='color.calibrate', script_value=str(script),
        source_value=str(source), expected_values=[*map(str, paths), str(destination / 'previews/neutral-parent.jpg'),
                                                     str(destination / 'previews/neutral-candidate.jpg')],
        primary_output_value=str(paths[-1]), timeout=30)
    assert receipt['status'] == 'success', receipt
    payload = session.load_session(destination)[1]
    assert session._receipt_bindings_unchanged(destination, payload, receipt)
    evidence = receipt['processing_checks']['color_calibration']['neutral_validation']
    recovery = core.recover_run_metrics(str(destination), receipt['id'])
    recovered = core.load_json(destination / recovery['report']['path'])
    assert recovered['neutral_validation'] == evidence
    assert recovered['recovery_binding']['inputs']['neutral_regions'] == core.fingerprint(destination / 'reports/regions.json')
    original = (destination / 'runs/040-neutral.json').read_bytes()
    assert core.recover_run_metrics(str(destination), receipt['id'])['reused'] is True
    assert (destination / 'runs/040-neutral.json').read_bytes() == original
    from tests.test_deep_sky_siril import _review
    review_path = _review(destination, receipt['id'], paths[-1], parent_preview=parent_preview)
    review = core.load_json(review_path)
    review['inspected_materials'] += [{'path': 'previews/' + name + '.jpg', 'sha256': core.sha256_file(destination / 'previews' / (name + '.jpg'))}
                                    for name in ('neutral-parent', 'neutral-candidate')]
    review_path.write_text(json.dumps(review))
    with pytest.raises(ContractError, match='receipt evidence and region contract'):
        core._validate_review(destination, str(review_path), receipt['id'])
    review = core.load_json(review_path)
    review['inspected_materials'] += [
        {'path': 'runs/040-neutral.json', 'sha256': core.sha256_file(destination / 'runs/040-neutral.json')},
    ]
    core.atomic_write_json(review_path, review)
    assert core._validate_review(destination, str(review_path), receipt['id'])[0]['verdict'] == 'accept'
    auxiliary = dict(review['inspected_materials'][0])
    review['inspected_materials'][0] = {'path': 'artifacts/raw-0.fit', 'sha256': core.sha256_file(paths[0])}
    matched = next(item for item in review['inspected_materials'] if item['path'] == 'previews/neutral-candidate.jpg')
    review['inspected_materials'].remove(matched)
    core.atomic_write_json(review_path, review)
    with pytest.raises(ContractError, match='matching-transfer'):
        core._validate_review(destination, str(review_path), receipt['id'])
    review['inspected_materials'].append(matched)
    review['inspected_materials'][0] = auxiliary
    core.atomic_write_json(review_path, review)
    import copy
    altered = copy.deepcopy(receipt)
    altered['processing_checks']['color_calibration']['neutral_validation']['benefit']['summary']['candidate']['median'] += .1
    assert not session._receipt_bindings_unchanged(destination, payload, altered)
    altered = copy.deepcopy(receipt)
    altered['color_calibration']['background_partition']['fit'], altered['color_calibration']['background_partition']['validation'] = (
        altered['color_calibration']['background_partition']['validation'], altered['color_calibration']['background_partition']['fit'])
    assert not session._receipt_bindings_unchanged(destination, payload, altered)
    altered = copy.deepcopy(receipt)
    del altered['processing_checks']['color_calibration']['neutral_validation']
    assert not session._receipt_bindings_unchanged(destination, payload, altered)
    bound_regions = destination / 'reports/regions.json'
    bound_regions.write_text(bound_regions.read_text() + ' ')
    assert not session._receipt_bindings_unchanged(destination, payload, receipt)


def test_sip_inverse_approximation_is_refined_before_wcs_acceptance():
    from sync_fits_header import wcs_round_trip_check
    header = dict(COLOR_WCS)
    header.update(CTYPE1='RA---TAN-SIP', CTYPE2='DEC--TAN-SIP', CRPIX1=256, CRPIX2=256,
                  A_ORDER=2, B_ORDER=2, AP_ORDER=2, BP_ORDER=2,
                  A_2_0=1e-6, B_0_2=-1e-6, AP_2_0=-1e-6, BP_0_2=1e-6)
    result = wcs_round_trip_check(header, 512, 512)
    assert result['verified'], result
    assert result['max_closure_error_px'] < 1e-7

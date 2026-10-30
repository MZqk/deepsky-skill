"""Masked-transfer contracts, native mono/RGB arithmetic and immutable replay."""
from pathlib import Path
import copy
import json
import re
import subprocess

import numpy as np
import pytest

from .test_knowledge_contract import protocol_text

from .test_native_processing import write_fits, native, read_fits_pixels
from .test_deep_sky_siril import _session, _input_inspect_run, _review, _write_ssf_provenance, core, session_state
import deep_sky_siril_hdr as hdr
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, fingerprint

BINARY = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')


def stretch_text(root, source, channels=1):
    for directory in ('artifacts', 'previews', 'scripts'):
        (root / directory).mkdir(exist_ok=True)
    a, p = root / 'artifacts', root / 'previews'
    outputs = []

    def save(name):
        outputs.append(a / (name + '.fit'))
        return f'save "{a/name}" -chksum\n'

    def preview(name):
        outputs.append(p / (name + '.jpg'))
        return f'savejpg "{p/name}" 95\n'

    text = ('requires 1.4.4 1.5.0\nset32bits\n'
            '# hdr-stretch: {"mask_low": 0.15, "mask_high": 0.75, "sigma": 2}\n'
            f'load "{source}"\nmtf 0 0.2 1\nicc_assign sRGB\n')
    text += save('070-gentle') + preview('070-gentle')
    lum = '070-gentle'
    if channels == 3:
        names = [a / ('070-gentle-' + c) for c in ('r', 'g', 'b')]
        outputs += [v.with_suffix('.fit') for v in names]
        text += 'split ' + ' '.join(f'"{v}"' for v in names) + '\n'
        text += 'pm "0.2126 * $artifacts/070-gentle-r$ + 0.7152 * $artifacts/070-gentle-g$ + 0.0722 * $artifacts/070-gentle-b$" -nosum\n'
        text += save('070-luminance')
        lum = '070-luminance'
    text += f'close\nload "{source}"\nmtf 0 0.06 1\nicc_assign sRGB\n'
    text += save('070-faint') + preview('070-faint')
    t = f'min(1, max(0, ($artifacts/{lum}$ - 0.15) / 0.6))'
    text += f'close\npm "{t}^2 * (3 - 2 * {t})" -nosum\ngauss 2\nicc_assign sRGB\n'
    text += save('070-mask') + preview('070-mask')
    mask = '070-mask'
    if channels == 3:
        mono = a / '070-mask.fit'
        text += f'rgbcomp "{mono}" "{mono}" "{mono}" "-out={a}/070-mask-rgb" -nosum\n'
        outputs.append(a / '070-mask-rgb.fit')
        mask = '070-mask-rgb'
    text += f'pm "$artifacts/070-gentle$ * $artifacts/{mask}$ + $artifacts/070-faint$ * (1 - $artifacts/{mask}$)" -nosum\nicc_assign sRGB\n'
    text += save('070-hdr') + 'stat main\n' + preview('070-hdr') + 'close\n'
    return text, a / '070-hdr.fit', outputs


def recompose_text(root, original, starless, source, transfer, strength=1):
    a = root / 'artifacts'
    names = ('090-ig', '090-if', '090-full', '090-ag', '090-af', '090-base', '090-stars', '090-final')
    paths = [a / (n + '.fit') for n in names]
    text = 'requires 1.4.4 1.5.0\nset32bits\n'
    mask = root / transfer['mask']['blend_path']
    for offset, parent in ((0, original), (3, starless)):
        for index, label in ((offset, 'gentle_chain'), (offset+1, 'faint_chain')):
            text += f'load "{parent}"\n'
            text += '\n'.join(' '.join([c['command'], *c['parameters']]) for c in transfer[label]) + '\n'
            text += f'save "{paths[index]}" -chksum\nclose\n'
        text += f'pm "{hdr.blend_expression(paths[offset], paths[offset+1], mask, root)}" -nosum\n'
        text += f'save "{paths[offset+2]}" -chksum\nclose\n'
    text += f'pm "{native._variable(paths[2], root)} - {native._variable(paths[5], root)}" -nosum\n'
    text += f'save "{paths[6]}" -chksum\nclose\n'
    text += f'pm "{native._variable(source, root)} + {strength} * {native._variable(paths[6], root)}" -nosum\n'
    text += f'save "{paths[7]}" -chksum\nstat main\nsavejpg "{root}/previews/090-final" 95\nclose\n'
    return text, paths


def run_native(root, text):
    if not BINARY.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    script, config = root / 'native.ssf', root / 'native.ini'
    script.write_text(text)
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    run = subprocess.run([str(BINARY), f'--initfile={config}', f'--directory={root}', '--offline', f'--script={script}'],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    return run


def scene(channels):
    y, x = np.indices((64, 64))
    image = .005 + .7 * np.exp(-((x-32)**2 + (y-32)**2)/120)
    return np.asarray([image * factor for factor in np.linspace(.8, 1, channels)])


@pytest.mark.parametrize('channels', [1, 3])
@pytest.mark.parametrize('signed', [False, True])
def test_real_siril_hdr_and_shared_mask_recomposition(tmp_path, monkeypatch, channels, signed):
    root = tmp_path / 'session with spaces'
    root.mkdir()
    (root / 'artifacts').mkdir()
    (root / 'runs').mkdir()
    starless, original = root / 'artifacts/060-starless.fit', root / 'full.fit'
    data = scene(channels)
    write_fits(starless, data)
    residual = np.full_like(data, -.00005 if signed else .0005)
    if signed:
        residual[:, 30:34, 30:34] = .0005
    write_fits(original, data + residual)
    text, primary, outputs = stretch_text(root, starless, channels)
    transfer = hdr.validate_stretch(root, starless, primary, text)
    assert native.transfer_chain(text, primary) == []
    assert len(outputs) == (8 if channels == 1 else 13)
    run_native(root, text)
    assert all(p.is_file() for p in outputs)
    metadata = {'hdr_transfer': transfer}
    checks = native.validate_processing_outputs(root, starless, primary, metadata)['hdr']
    assert max(checks['blend_max_abs']) < 1e-6
    m, s, z = read_fits_pixels(root / transfer['mask']['path'])
    mask = m * s + z
    assert np.min(mask) < 1e-6 and np.max(mask) > .99 and np.any((mask > .01) & (mask < .99))
    frozen = {'outputs': [{'path': p.relative_to(root).as_posix(), **fingerprint(p, include_path=False)} for p in outputs]}
    bindings = hdr.mask_bindings(root, transfer, frozen)
    sep = {'id': '060-separate', 'protocol': 'stars.separate', 'source_path': '@input',
           'primary_output': 'artifacts/060-starless.fit', 'image_domain': 'linear',
           'branch_binding': {'separation_run': '060-separate'}}
    stretch = {**frozen, 'id': '070-hdr', 'protocol': 'stretch', 'source_path': sep['primary_output'],
               'primary_output': primary.relative_to(root).as_posix(), 'image_domain': 'nonlinear',
               'transfer_chain': [], 'hdr_transfer': transfer, 'branch_binding': {'separation_run': sep['id']}}
    for record in (sep, stretch):
        core.atomic_write_json(root/'runs'/f"{record['id']}.json", record)
    receipts = [sep, stretch]
    monkeypatch.setattr(session_state, '_verified_success_receipts', lambda _: receipts)
    payload = {'input': {'path': str(original)}, 'context': {'input_state': 'linear'}}
    load = lambda name: (root/'runs'/f'{name}.json', sep if name == sep['id'] else stretch)
    for strength in (1, .5):
        source = primary
        if strength == .5:
            raw, scale, zero = read_fits_pixels(primary)
            source = root / 'artifacts/080-adjusted.fit'
            write_fits(source, (raw * scale + zero) * .9)
            receipts.append({'id': '080-adjusted', 'protocol': 'tone.adjust', 'source_path': stretch['primary_output'],
                             'primary_output': source.relative_to(root).as_posix(), 'image_domain': 'nonlinear',
                             'branch_binding': {'separation_run': sep['id']}})
        replay, paths = recompose_text(root, original, starless, source, transfer, strength)
        roles, pairs = hdr.validate_recomposition(root, native.script_commands(replay), source, original, starless, paths[-1], transfer)
        run_native(root, replay)
        meta = native.prepare_processing(root, payload, 'stars.recompose', source, paths[-1], replay, {},
                                         sep['id'], stretch['id'], load, lambda _: None)
        assert meta['hdr_pairs'] == pairs and meta['hdr_mask_bindings'] == bindings
        meta['target_type'] = 'galaxy'
        measured = native.validate_processing_outputs(root, source, paths[-1], meta)
        assert max(measured['closure']['per_channel_max_abs']) < 1e-5
        assert max(measured['closure']['per_channel_final_max_abs']) < 1e-5
        if signed:
            assert len(measured['closure']['warnings']) == channels
            with pytest.raises(native.ProcessingEvidenceError):
                native.validate_processing_outputs(root, source, paths[-1], meta,
                    execution_policy={'star_diagnostics': 'subject_roi_residual_v1'})
        script = root/'scripts/090-hdr.ssf'; script.write_text(replay)
        receipt = {**meta, 'protocol': 'stars.recompose', 'script': {'path': 'scripts/090-hdr.ssf'},
                   'source_path': source.relative_to(root).as_posix(), 'processing_checks': measured}
        assert hdr.verify_receipt(root, payload, receipt)
        receipt.update(id='090-hdr', primary_output=paths[-1].relative_to(root).as_posix(),
            outputs=[{'path': path.relative_to(root).as_posix(), **fingerprint(path, include_path=False)}
                     for path in [*paths, root/'previews/090-final.jpg']])
        core.atomic_write_json(root/'runs/090-hdr.json', receipt)
        payload['execution_policy'] = dict(EXECUTION_POLICY)
        core.atomic_write_json(root/'session.json', payload)
        with monkeypatch.context() as review_context:
            review_context.setattr(session_state, 'load_session', lambda _: (root, payload, {}))
            review_context.setattr(core, '_load_run', lambda *_: (root/'runs/090-hdr.json', receipt))
            from deep_sky_siril_metrics import generate_metric_report
            generate_metric_report(run_id=receipt['id'], protocol=receipt['protocol'],
                candidate_path=paths[-1], parent_path=source, output_path=root/'reports/090-hdr/metrics.json')
            draft = core.review_materials_for_run(str(root), receipt['id'])
            materials = {row['path'] for row in draft['inspected_materials']}
            assert len(materials) > 10 and {row['path'] for row in receipt['outputs']}.issubset(materials)
            draft.update(verdict='accept', gates={name: 'pass' for name in draft['gates']},
                         notes='Synthetic HDR review with complete fixed roles and replay outputs.')
            (root/'reviews').mkdir(exist_ok=True)
            review_path = root/'reviews/090-hdr.json'
            core.atomic_write_json(review_path, draft)
            assert core.check_review(root, receipt['id'], review_path)['status'] == 'review_valid'
        payload.pop('execution_policy')
        forged = copy.deepcopy(receipt)
        forged['hdr_transfer']['faint_chain'][0]['parameters'][1] = '.07'
        assert not hdr.verify_receipt(root, payload, forged)
        with pytest.raises(ContractError, match='frozen chains'):
            hdr.validate_recomposition(root, native.script_commands(replay.replace('mtf 0 0.06 1', 'mtf 0 0.07 1', 1)),
                                       source, original, starless, paths[-1], transfer)
        if strength == 1:
            a, sa, za = read_fits_pixels(paths[2]); b, sb, zb = read_fits_pixels(paths[-1])
            np.testing.assert_allclose(a*sa+za, b*sb+zb, atol=1e-6, rtol=0)
    write_fits(root / transfer['mask']['path'], np.full((64, 64), .3))
    with pytest.raises(ContractError, match='mask changed'):
        native.validate_processing_outputs(root, source, paths[-1], meta)


@pytest.mark.parametrize('value', [
    {}, {'mask_low': .2, 'mask_high': .1, 'sigma': 2}, {'mask_low': -.1, 'mask_high': .8, 'sigma': 2},
    {'mask_low': .2, 'mask_high': .8, 'sigma': 0}, {'mask_low': .2, 'mask_high': .8, 'sigma': 17},
    {'mask_low': True, 'mask_high': .8, 'sigma': 2}, {'mask_low': .2, 'mask_high': .8, 'sigma': '2'},
    {'mask_low': .2, 'mask_high': .8, 'sigma': 2, 'extra': 1},
])
def test_invalid_mask_parameters(value):
    with pytest.raises(ContractError):
        hdr.declaration('# hdr-stretch: ' + json.dumps(value))


@pytest.mark.parametrize('old,new', [
    ('mtf 0 0.2 1', 'autostretch -linked -4.5 .08'),
    ('mtf 0 0.2 1', 'mtf 0 0.2 1 R'),
    ('mtf 0 0.2 1', 'ght -D=.2 -human -clipmode=rgbblend'),
    ('gauss 2', 'gauss 3'),
    ('+ $artifacts/070-faint$', '- $artifacts/070-faint$'),
    (' -nosum', ' -rescale'),
    ('icc_assign sRGB', 'icc_assign linear'),
])
def test_hdr_native_operations_are_frozen(tmp_path, old, new):
    source = tmp_path / 'source.fit'
    write_fits(source, scene(1))
    text, primary, _ = stretch_text(tmp_path, source)
    with pytest.raises(ContractError):
        hdr.validate_stretch(tmp_path, source, primary, text.replace(old, new, 1))


def test_duplicate_declaration_unknown_source_and_plain_stretch_pixelmath(tmp_path):
    source = tmp_path / 'source.fit'; write_fits(source, scene(1))
    text, primary, _ = stretch_text(tmp_path, source)
    with pytest.raises(ContractError):
        hdr.validate_stretch(tmp_path, source, primary, text + text.splitlines()[2])
    payload = {'input': {'path': str(source)}, 'context': {'input_state': 'linear'}}
    with pytest.raises(ContractError, match='declared scientific source|another scientific source'):
        native.prepare_processing(tmp_path, payload, 'stretch', source, primary,
                                  text.replace(str(source), str(tmp_path/'other.fit'), 1), {}, None, None, None, None)
    plain = '\n'.join(row for row in text.splitlines() if not row.startswith('# hdr-stretch:'))
    with pytest.raises(ContractError, match='HDR stretch declaration'):
        native.prepare_processing(tmp_path, payload, 'stretch', source, primary, plain, {}, None, None, None, None)


def test_unbound_hdr_cannot_create_a_formal_candidate(tmp_path, monkeypatch):
    root, source, _ = _session(tmp_path, monkeypatch, input_state='linear')
    inspection, direct, diagnostic = _input_inspect_run(root, source, monkeypatch)
    _review(root, inspection['id'], diagnostic, parent_preview=direct)
    text, primary, outputs = stretch_text(root, source)
    script = root/'scripts/070-hdr.ssf'; script.write_text(text)
    _write_ssf_provenance(root, script, 'stretch')
    with pytest.raises(ContractError, match='explicit morphology strategy'):
        core.run_script(root, protocol='stretch', script_value=script, source_value=source,
            primary_output_value=primary, expected_values=outputs, timeout=30, validate_only=True)
    assert not primary.exists()
    assert not list(root.glob('reports/*/scientific-attempt.json'))


def test_documented_hdr_stretch_skeletons(tmp_path):
    reference = protocol_text("stretch")
    blocks = [b for b in re.findall(r'^```ssf\s*\n(.*?)^```\s*$', reference, re.M | re.S) if '# hdr-stretch:' in b]
    assert len(blocks) == 2
    for index, block in enumerate(blocks):
        root = tmp_path / str(index); root.mkdir()
        (root/'artifacts').mkdir(); (root/'previews').mkdir()
        source = root/'artifacts/060-starless.fit'
        channels = 3 if '\nsplit ' in block else 1
        write_fits(source, scene(channels))
        text = block.replace('/abs/session', str(root)).replace('/abs/current-linear-parent.fit', str(source))
        primary = root/'artifacts/070-hdr.fit'
        transfer = hdr.validate_stretch(root, source, primary, text)
        run_native(root, text)
        assert max(hdr.measure_outputs(root, primary, {'hdr_transfer': transfer})['blend_max_abs']) < 1e-6

"""Regression checks for native contracts and real Siril transfer arithmetic."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deep_sky_siril_processing as native
import deep_sky_siril_metrics as metrics
import siril_auto_samples as samples
from deep_sky_siril_contract import ContractError, classify_siril_network, sha256_file
from deep_sky_siril_artifacts import read_fits_pixels


def write_fits(path, values, extra=()):
    a = np.asarray(values, dtype='>f4')
    if a.ndim == 2:
        a = a[None]
    cards = [('SIMPLE', 'T'), ('BITPIX', -32), ('NAXIS', 3 if a.shape[0] == 3 else 2),
             ('NAXIS1', a.shape[2]), ('NAXIS2', a.shape[1])]
    if a.shape[0] == 3:
        cards.append(('NAXIS3', 3))
    cards += list(extra)
    h = ''.join(f'{k:<8}= {v:>20}'.ljust(80) for k, v in cards) + 'END'.ljust(80)
    h = h.encode(); h += b' ' * (-len(h) % 2880)
    data = a.tobytes(); path.write_bytes(h + data + b'\0' * (-len(data) % 2880))


@pytest.mark.parametrize('rate,verdict', [(0, 'uncertain'), (.000099, 'uncertain'), (.0001, 'reject'), (.000101, 'reject')])
def test_shadow_threshold(rate, verdict):
    m = {'measurement_domain': 'scientific', 'channels': [{'shadow_clip_rate': rate}]}
    assert metrics.evaluate_stage_gates('stretch', m)[1] == verdict
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
    assert (w, h) == (width, height) and xs[0] == ys[0] == 0
    assert xs[-1] == width - 1 and ys[-1] == height - 1
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


@pytest.mark.parametrize('line', [
    'ght -D=11 -human -clipmode=rgbblend', 'ght -D=1 -B=16 -human -clipmode=rgbblend',
    'ght -D=1 -LP=.5 -SP=.4 -human -clipmode=rgbblend', 'ght -D=nan -human -clipmode=rgbblend',
    'ght -D=1 -independent -clipmode=rgbblend', 'denoise -nocosmetic -mod=.36',
    'denoise -nocosmetic -mod=.2 -sos', 'platesolve -catalog=gaia -noflip -nocrop',
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
        classify_siril_network('platesolve -catalog=gaia -noflip -nocrop', protocol='astrometry.solve', session_offline=False)
    assert classify_siril_network('platesolve -catalog=localgaia -noflip -nocrop', protocol='astrometry.solve', session_offline=False)['effective_offline']


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
    metadata = {'branch_binding': {'full_baseline':'artifacts/full.fit', 'original_starless':'artifacts/base.fit', 'display_stars':'artifacts/stars.fit'}}
    assert max(native.validate_processing_outputs(tmp_path, paths[0], paths[-1], metadata)['closure']['per_channel_max_abs']) <= 1e-5
    for bad in (np.nan, -.1, .2):
        write_fits(paths[2], np.full((3, 32, 32), bad))
        with pytest.raises(ContractError):
            native.validate_processing_outputs(tmp_path, paths[0], paths[-1], metadata)
    write_fits(paths[2], np.full((3, 31, 32), .1))
    with pytest.raises(ContractError):
        native.validate_processing_outputs(tmp_path, paths[0], paths[-1], metadata)


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
        native.prepare_processing(tmp_path,payload,'astrometry.solve',p,output,f'load "{p}"',{},None,None,None,None)
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
    checks=native.validate_processing_outputs(tmp_path,tmp_path/'a.fit',paths[-1],{'branch_binding':roles})
    assert max(checks['closure']['per_channel_max_abs']) <= 1e-5
    # Use the stretched original starless parent for the display-domain candidate.
    full,scale,zero=read_fits_pixels(paths[0]);base,sb,zb=read_fits_pixels(paths[1]);stars,ss,zs=read_fits_pixels(paths[2])
    assert np.max(np.abs(base*sb+zb+stars*ss+zs-(full*scale+zero))) <= 1e-5
    final,sf,zf=read_fits_pixels(paths[-1])
    assert np.max(np.abs(final*sf+zf-(full*scale+zero))) <= 1e-5


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


def test_real_siril_luminance_clahe_mask_and_shared_gain(tmp_path):
    binary=Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():pytest.skip('Siril 1.4.4 unavailable')
    a=tmp_path/'artifacts';a.mkdir();(tmp_path/'previews').mkdir();source=tmp_path/'source.fit'
    y,x=np.mgrid[:192,:192]
    lum=.015+.25*np.exp(-((x-96)**2+(y-96)**2)/1800)+.06*np.exp(-((x-70)**2+(y-90)**2)/20)
    rgb=np.stack([lum*1.1,lum,lum*.9]);write_fits(source,rgb)
    document=(ROOT/'references/protocols/structure-local-contrast.md').read_text()
    text=document.split('```ssf\n')[1].split('```')[0].replace('/abs/current-starless.fit',str(source)).replace('/abs/session',str(tmp_path))
    commands=native.script_commands(text);primary=a/'080-local-contrast.fit'
    bounds={'low_start':.03,'low_end':.08,'high_start':.65,'high_end':.9}
    native.validate_local_contrast(tmp_path,source,primary,commands,bounds)
    script=tmp_path/'contrast.ssf';script.write_text(text);config=tmp_path/'s.ini';config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result=subprocess.run([str(binary),f'--initfile={config}',f'--directory={tmp_path}','--offline',f'--script={script}'],capture_output=True,text=True,timeout=30)
    (tmp_path/'contrast.log').write_text(result.stdout)
    assert result.returncode==0,result.stdout
    out,scale,zero=read_fits_pixels(primary);output=np.asarray(out)*scale+zero
    assert np.isfinite(output).all() and np.max(np.abs(output-rgb))>.001
    np.testing.assert_allclose(output[0]/output[1],1.1,rtol=2e-6)
    np.testing.assert_allclose(output[2]/output[1],.9,rtol=2e-6)
    mask,ms,mz=read_fits_pixels(a/'080-mask.fit');mask=np.asarray(mask)*ms+mz
    assert mask.min()>=0 and mask.max()<=1 and mask[0,0,0]<1e-5
    corrupted=[list(row) for row in commands]
    next(row for row in corrupted if row[0]=='clahe')[1]='3'
    with pytest.raises(ContractError,match='weighted luminance'):
        native.validate_local_contrast(tmp_path,source,primary,corrupted,bounds)


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

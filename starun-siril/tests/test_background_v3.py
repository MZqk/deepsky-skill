"""Background sampling recovery and native precision regressions."""
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

from deep_sky_siril_artifacts import read_fits_pixels
import siril_auto_samples as samples
import deep_sky_siril_processing as processing
from deep_sky_siril_contract import ContractError, sha256_file
from siril_background_samples import verify_image_precision
from .test_background_validation import write_fits, regions_for, display_tail
from types import SimpleNamespace


def write_integer_fits(path, values):
    values = np.asarray(values, dtype=np.uint16)
    if values.ndim == 2:
        values = values[None]
    cards = [('SIMPLE', 'T'), ('BITPIX', 16), ('NAXIS', 3),
             ('NAXIS1', values.shape[2]), ('NAXIS2', values.shape[1]),
             ('NAXIS3', values.shape[0]), ('BSCALE', 1), ('BZERO', 32768)]
    header = ''.join(f'{k:<8}= {v:>20}'.ljust(80) for k, v in cards) + 'END'.ljust(80)
    header = header.encode(); header += b' ' * (-len(header) % 2880)
    pixels = (values.astype(np.int32) - 32768).astype('>i2').tobytes()
    path.write_bytes(header + pixels + b'\0' * (-len(pixels) % 2880))


def test_native_identity_promotes_integer_without_changing_science(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    values = np.arange(65536, dtype=np.uint16).reshape(256, 256)
    source = tmp_path / 'source.fit'
    write_integer_fits(source, np.stack([values, values[::-1], values[:, ::-1]]))
    import hashlib
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    bridge = tmp_path / 'read_precision.py'
    bridge.write_text('import json\nimport sirilpy\n'
                      's=sirilpy.SirilInterface();s.connect()\n'
                      'f=s.get_image()\n'
                      f'f.data.tofile({str(tmp_path / "native.bin")!r})\n'
                      f'open({str(tmp_path / "native.json")!r},"w").write(json.dumps({{"dtype":str(f.data.dtype),"shape":list(f.data.shape),"bitpix":f.bitpix}}))\n')
    script = tmp_path / 'precision.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\npyscript "{bridge}"\nsave "{tmp_path}/float"\nclose\n')
    config = tmp_path / 's.ini'
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}',
                             '--offline', f'--script={script}'], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    info = json.loads((tmp_path / 'native.json').read_text())
    assert info['dtype'] == 'float32' and info['bitpix'] == -32
    raw, scale, zero = read_fits_pixels(source)
    expected = (raw.astype(float) * scale + zero).astype(np.float32)
    native = np.fromfile(tmp_path / 'native.bin', dtype=np.float32).reshape(info['shape'])
    error = np.abs(native.astype(float) - expected.astype(float))
    assert np.all(error <= 2 * np.abs(np.spacing(expected)).astype(float))
    assert np.array_equal(native[expected == 0], expected[expected == 0])
    assert np.array_equal(native[expected == 1], expected[expected == 1])
    saved, _, _ = read_fits_pixels(tmp_path / 'float.fit')
    assert saved.dtype.kind == 'f' and saved.dtype.itemsize == 4
    np.testing.assert_array_equal(saved, native)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('step', [1, 3, 8, 13])
def test_exact_footprints_match_native_cell_intersections(step):
    from deep_sky_siril_background import intersects, sample_rect
    mask = np.zeros((15, 17), bool); mask[4, 5] = True; mask[10, 9] = True
    xs, ys = np.arange(17)*step+step//2, np.arange(15)*step+step//2
    result = samples._footprint_hits(mask, xs, ys, step)
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            assert result[iy, ix] == any(intersects(sample_rect(int(x),int(y)),
                [sx*step,sy*step,(sx+1)*step,(sy+1)*step]) for sy,sx in np.argwhere(mask))


@pytest.mark.parametrize('kind', ['small', 'bright', 'saturated', 'crowded'])
def test_sparse_statistics_keep_bright_guards(kind):
    rng = np.random.default_rng(22)
    image = .1+rng.normal(0, .0001, (3, 90, 90))
    if kind == 'small': image[:,45,45] += .0008
    elif kind == 'bright': image[0,45,45] += .003
    elif kind == 'saturated': image[1,45,45] = 1
    else:
        for y in range(34,58,4):
            for x in range(34,58,4): image[:,y,x] += .0008
    result = samples._sparse_star_check(image,45,45,[.0001]*3)
    assert result['accepted'] == (kind == 'small')
    if kind in ('bright','saturated'): assert 'bright_star_or_saturation_guard' in result['reasons']
    if kind == 'crowded': assert 'compact_star_fraction' in result['reasons']


@pytest.mark.parametrize('kind', ['red-cloud','dark-cloud','wide-halo'])
def test_recovery_preserves_channel_structure(kind):
    y,x=np.indices((400,600)); rng=np.random.default_rng(19)
    planes=.1+rng.normal(0,.00003,(3,400,600))
    amplitude = -.002 if kind == 'dark-cloud' else .02 if kind == 'wide-halo' else .002
    planes[0] += amplitude*np.exp(-((x-300)**2+(y-200)**2)/2500)
    median,peak,p95,_,xs,ys,step=samples._pool_planes(planes)
    component={};samples._exclusion_mask(median,peak,p95,'emission_nebula',components=component)
    assert component['structure_protected'][200//step,300//step]


@pytest.mark.parametrize('slope,accepted', [(2.8e-5,True), (3.2e-5,False)])
def test_sparse_median_bias_boundary(slope,accepted):
    y,_=np.indices((90,90))
    image=np.repeat((.1+y*slope)[None],3,axis=0)
    image[0,36,45]+=.0008
    result=samples._sparse_star_check(image,45,45,[.0001]*3)
    assert result['compact_star_fraction']==.04
    assert result['accepted']==accepted
    assert ('median_contamination' in result['reasons']) != accepted


@pytest.mark.parametrize('positions,accepted', [
    ([(45,45),(39,40),(43,44)],True),
    ([(45,45),(35,35),(43,44)],False),
])
def test_compact_fraction_boundary(positions,accepted):
    image=np.full((3,90,90),.1)
    for y,x in positions:image[0,y,x]+=.0008
    result=samples._sparse_star_check(image,45,45,[.0001]*3)
    assert round(result['compact_star_fraction']*625)==(62 if accepted else 63)
    assert result['accepted']==accepted
    assert ('compact_star_fraction' in result['reasons']) != accepted


def test_no_frozen_controls_never_enables_recovery(tmp_path,monkeypatch):
    source=tmp_path/'source.fit';write_fits(source,np.full((600,600),.1))
    original=samples._exclusion_mask
    def crowded(*args):
        mask,residuals,counts=original(*args);mask[:]=True
        return mask,residuals,counts
    monkeypatch.setattr(samples,'_exclusion_mask',crowded)
    monkeypatch.setattr(samples,'_sparse_star_check',lambda *a:pytest.fail('Recovery without frozen controls'))
    report={}
    with pytest.raises(ValueError,match='at least 12'):
        samples.generate_background_samples(source,diagnostics=report)
    assert report['recovery']['attempted'] is False


def test_strict_success_never_calls_recovery_and_foreground_is_excluded(tmp_path,monkeypatch):
    source,regions=tmp_path/'source.fit',tmp_path/'regions.json'
    write_fits(source,np.full((600,600),.1)); doc=regions_for(source,regions)
    doc['regions'].append({'id':'target','role':'faint','rect':[250,250,350,350]})
    regions.write_text(json.dumps(doc))
    monkeypatch.setattr(samples,'_sparse_star_check',lambda *a: pytest.fail('Unexpected recovery'))
    report={}; contract=samples.generate_background_samples(source,exclude_regions_path=regions,diagnostics=report)
    assert report['recovery']['attempted'] is False
    from deep_sky_siril_background import intersects,sample_rect
    assert all(not intersects(sample_rect(p['x'],p['y']),[250,250,350,350]) for p in contract['fit_samples'])


@pytest.mark.parametrize('source_format', ['FITS', 'XISF'])
def test_recovery_once_and_still_insufficient_emits_no_contract(tmp_path,monkeypatch,source_format):
    source,regions=tmp_path/('source.xisf' if source_format == 'XISF' else 'source.fit'),tmp_path/'regions.json'
    if source_format == 'XISF':
        from .test_xisf_metrics import xisf
        xisf(source, np.full((1,600,600),.1))
    else:
        write_fits(source,np.full((600,600),.1))
    regions_for(source,regions)
    original=samples._exclusion_mask
    def crowded(*args):
        mask,residuals,counts=original(*args);mask[:]=True
        return mask,residuals,counts
    monkeypatch.setattr(samples,'_exclusion_mask',crowded)
    report={};c=samples.generate_background_samples(source,exclude_regions_path=regions,diagnostics=report)
    assert report['strict']['coverage']['status']=='insufficient'
    assert report['recovery']['attempted'] and len(c['fit_samples'])>=12
    monkeypatch.setattr(samples,'_sparse_star_check',lambda *args:{'accepted':False,'reasons':['bright_star_or_saturation_guard']})
    with pytest.raises(ValueError,match='at least 12'):
        samples.generate_background_samples(source,exclude_regions_path=regions,diagnostics=report)
    assert report['recovery']['attempted'] and report['coverage']['count']==0


def test_roi_proposals_are_source_only_not_execution_evidence(tmp_path):
    source=tmp_path/'source.fit';write_fits(source,np.full((600,600),.1))
    before=sha256_file(source)
    doc=samples.propose_validation_regions(source)
    assert doc['schema']==samples.REGION_PROPOSAL_SCHEMA and doc['requires_visual_confirmation']
    assert len(doc['proposals'])==18
    from collections import Counter
    assert set(Counter(tuple(p['search_cell']) for p in doc['proposals']).values())=={3}
    from deep_sky_siril_background import load_regions
    regions=tmp_path/'regions.json';regions.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='contract'):load_regions(regions,source)
    assert sha256_file(source)==before


@pytest.mark.parametrize('failure',['dtype','geometry','nonfinite','value','endpoint'])
def test_precision_evidence_rejects_damage_without_writing(tmp_path,failure):
    source=tmp_path/'source.fit';write_fits(source,np.full((3,100,100),.1))
    raw,scale,zero=read_fits_pixels(source);data=(raw.astype(float)*scale+zero).astype(np.float32)
    image=SimpleNamespace(data=data,bitpix=-32,top_down=False)
    assert verify_image_precision(image,source)['status']=='verified'
    if failure=='dtype':image.data=data.astype(np.float64)
    elif failure=='geometry':image.data=data[:,:99]
    elif failure=='nonfinite':image.data[0,0,0]=np.nan
    elif failure=='value':image.data[0,0,0]+=.001
    else:
        data[:,0,0]=0;write_fits(source,data);image.data[:,0,0]=np.nextafter(np.float32(0),np.float32(1))
    before=sha256_file(source)
    with pytest.raises(ValueError):verify_image_precision(image,source)
    assert sha256_file(source)==before


@pytest.mark.parametrize('failure',['missing','duplicate','factor','after_bridge','after_fit','reload'])
def test_identity_command_is_exact_and_ordered(tmp_path,failure):
    p,c=tmp_path/'p.fit',tmp_path/'c.fit'
    commands=[['load',str(p)],['fmul','1'],['pyscript','bridge'],['subsky','1','-existing'],['save',str(c)],
              *display_tail(p,c,tmp_path/'previews/p.jpg',tmp_path/'previews/c.jpg')]
    processing.validate_background_display(tmp_path,p,c,commands)
    if failure=='missing':del commands[1]
    elif failure=='duplicate':commands.insert(1,['fmul','1'])
    elif failure=='factor':commands[1]=['fmul','1.001']
    elif failure=='after_bridge':commands.insert(3,commands.pop(1))
    elif failure=='after_fit':commands.insert(4,commands.pop(1))
    else:commands.insert(3,['load',str(p)])
    with pytest.raises(ContractError):processing.validate_background_display(tmp_path,p,c,commands)

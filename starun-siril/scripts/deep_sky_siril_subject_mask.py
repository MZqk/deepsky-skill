#!/usr/bin/env python3
"""Prepare source-bound polygon weights; scientific images remain Siril outputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np

from deep_sky_siril_contract import (ContractError, RUN_ID_PATTERN, atomic_write_json,
    fingerprint_matches, load_json, session_path, sha256_file)
from deep_sky_siril_artifacts import read_fits_header
from deep_sky_siril_stretch import pixels, record, bound_file
from siril_manual_bundle import strict_json_bytes

POLICY = 'reviewed_polygons_v1'
REQUEST = 'starun-siril.subject-mask-request.v1'
REPORT = 'starun-siril.subject-mask.v1'


def fail(message):
    raise ContractError('subject_mask_invalid', message)


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def intersects(a, b, c, d):
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False
    return cross(a, b, c)*cross(a, b, d) <= 0 and cross(c, d, a)*cross(c, d, b) <= 0


def validate_request(value, source):
    if (not isinstance(value, dict) or set(value) != {'schema','source','reference','include','exclude','feather_px'}
        or value['schema'] != REQUEST or not isinstance(value['source'], str)):
        fail('Declare a source, reference, include/exclude polygons and feather_px')
    data = pixels(source)
    if len(data) != 3 or not np.isfinite(data).all():
        fail('Subject masks require finite RGB FITS pixels')
    _, height, width = data.shape
    if (not isinstance(value['reference'],dict)
        or any(type(value['reference'].get(k)) is not int for k in ('width','height'))
        or value['reference'] != {'sha256':sha256_file(source), 'width':width, 'height':height}):
        fail('Subject mask source or geometry changed')
    feather = value['feather_px']
    if type(feather) not in (float, int) or not np.isfinite(feather) or feather <= 0:
        fail('feather_px must be finite and positive')
    for key in ('include', 'exclude'):
        polygons = value[key]
        if not isinstance(polygons, list) or len(polygons) > 64 or (key == 'include' and not polygons):
            fail('Declare one to 64 include polygons and at most 64 exclude polygons')
        for polygon in polygons:
            if (not isinstance(polygon, list) or not 3 <= len(polygon) <= 256
                or any(not isinstance(p, list) or len(p) != 2
                    or any(type(v) not in (float, int) or not np.isfinite(v) for v in p)
                    or not 0 <= p[0] <= width or not 0 <= p[1] <= height for p in polygon)):
                fail('Polygon vertices must be finite image-bound coordinates')
            edges = list(zip(polygon, polygon[1:]+polygon[:1]))
            area = sum(a[0]*b[1]-b[0]*a[1] for a,b in edges)
            if area == 0 or len({tuple(p) for p in polygon}) != len(polygon):
                fail('Polygon is degenerate or repeats vertices')
            for i,(a,b) in enumerate(edges):
                previous = polygon[i-1]
                if cross(previous,a,b) == 0 and np.dot(np.subtract(previous,a),np.subtract(b,a)) > 0:
                    fail('Polygon edges overlap')
                for j,(c,d) in enumerate(edges[:i]):
                    if j != i-1 and not (j == 0 and i == len(edges)-1) and intersects(a,b,c,d):
                        fail('Polygon self-intersects')
    return width, height


def rasterize(value):
    """Sample pixel centers in bounded row blocks, preserving exact zero support."""
    width, height = (value['reference'][k] for k in ('width', 'height'))
    output = np.zeros((height, width), dtype=np.float32)
    smooth = lambda t: t*t*(3-2*t)
    x = np.arange(width, dtype=float)[None,:]+.5
    for start in range(0, height, 128):
        y = np.arange(start, min(start+128,height), dtype=float)[:,None]+.5
        included = np.zeros((len(y),width))
        excluded = np.ones_like(included)
        for kind in ('include','exclude'):
            for polygon in value[kind]:
                inside = np.zeros_like(included,dtype=bool)
                distance = np.full_like(included,np.inf)
                for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                    dx,dy = b[0]-a[0],b[1]-a[1]
                    t = np.clip(((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy),0,1)
                    distance = np.minimum(distance,np.hypot(x-a[0]-t*dx,y-a[1]-t*dy))
                    if dy:
                        inside ^= ((a[1]>y)!=(b[1]>y)) & (x < a[0]+(y-a[1])*dx/dy)
                weight = smooth(np.clip(distance/value['feather_px'],0,1))
                if kind == 'include':
                    included = np.maximum(included,np.where(inside,weight,0))
                else:
                    excluded = np.minimum(excluded,np.where(inside,0,weight))
        output[start:start+len(y)] = np.minimum(included,excluded)
    return output


def highlight(parameters):
    a,b = (parameters.get(k) for k in ('high_start','high_end'))
    if (any(type(v) not in (int,float) or not np.isfinite(v) for v in (a,b))
        or not 0 <= a < b <= 1):
        fail('Spatial highlight protection requires 0 <= high_start < high_end <= 1')
    return a,b


def native_rows(directory):
    q = lambda p: json.dumps(str(p))
    return ['load '+q(directory/'weights.tif'),
            'save '+q(directory/'mask.fit')+' -chksum']


def load(session, source, reference):
    """Recompute geometry and verify every auxiliary asset without repairing evidence."""
    from deep_sky_siril_stretch_v2 import implementation
    path = (bound_file(session, reference) if isinstance(reference,dict) else
            session_path(session,reference,must_exist=True,allowed_roots=('reports',)))
    path = session_path(session,path,must_exist=True,allowed_roots=('reports',))
    binding = record(session,path)
    if isinstance(reference,dict) and reference != binding:
        fail('Subject mask report changed')
    payload = load_json(session/'session.json')
    if payload['execution_policy'].get('subject_mask') != POLICY:
        fail('Subject masks require the frozen reviewed_polygons_v1 policy')
    report = load_json(path)
    if (report.get('schema') != REPORT or report.get('policy') != POLICY
        or report.get('requires_visual_review') is not True
        or report.get('source') != record(session,source)
        or report.get('implementation') != implementation()):
        fail('Subject mask source, implementation or policy changed')
    request_path = bound_file(session, report['request'])
    if record(session,request_path) != report['request']:
        fail('Subject mask request changed')
    value = strict_json_bytes(request_path.read_bytes(),document='subject mask request')
    if session_path(session,value['source'],must_exist=True,allowed_roots=('artifacts',)) != source:
        fail('Subject mask belongs to a different direct parent')
    validate_request(value,source)
    for item in [report['mask'], *report['evidence']]:
        if record(session,bound_file(session,item)) != item:
            fail('Subject mask evidence changed')
    probe = session/'reports/tool-probe.json'
    frozen = load_json(probe)['tools']['siril_cli']
    if report.get('probe') != record(session,probe):
        fail('Subject mask runtime probe changed')
    if report['runtime'] != frozen or not fingerprint_matches(frozen['fingerprint']):
        fail('Subject mask runtime changed')
    directory = path.parent
    expected_script = '\n'.join(['requires 1.4.4 1.5.0','set32bits',*native_rows(directory),'close'])+'\n'
    if (directory/'native.ssf').read_text() != expected_script:
        fail('Subject mask native conversion recipe changed')
    expected = rasterize(value)[::-1]
    mask_path = bound_file(session,report['mask'])
    actual = pixels(mask_path)
    if read_fits_header(mask_path).get('BITPIX') != -32 or actual.shape != (1,*expected.shape) or not np.array_equal(actual[0],expected):
        fail('Subject mask pixels or orientation changed')
    required = {'weights.tif','mask.fit','mask.png','overlay.png','mask-review.png','native.ssf','native.log','trial.ini'}
    if (report['mask'] != record(session,directory/'mask.fit')
        or {item['path'] for item in report['evidence']} != {(directory/name).relative_to(session).as_posix() for name in required}
        or len(report['evidence']) != len(required)):
        fail('Subject mask evidence is incomplete')
    if report['status'] != ('review_required' if expected.any() else 'skipped'):
        fail('Subject mask coverage status changed')
    return report,binding


def weights(session, source, reference):
    report,binding = load(session,source,reference)
    return pixels(bound_file(session,report['mask']))[0],binding


def review_paths(session, source, reference):
    report,binding = load(session,source,reference)
    items = [binding, report['request'], *[r for r in report['evidence'] if Path(r['path']).name == 'mask-review.png']]
    return {r['path']:bound_file(session,r) for r in items}


def diagnostics(space, effective, before, after, regions):
    from deep_sky_siril_color import mask_coverage
    excluded = effective == 0
    return {'spatial_coverage':mask_coverage(space,regions),
            'effective_coverage':mask_coverage(effective,regions),
            'excluded_max_abs':float(np.max(np.abs(after[:,excluded]-before[:,excluded]))) if excluded.any() else 0.}


def prepare(session_value, request_value, run_id, timeout=1800):
    from PIL import Image
    from deep_sky_siril_session import load_session, execution_policy_is_current
    from deep_sky_siril_processing import source_state
    from deep_sky_siril_core import _accepted_review
    from deep_sky_siril_stretch_v2 import implementation
    from deep_sky_siril_dualband import _native
    session,payload,_ = load_session(session_value)
    if not execution_policy_is_current(payload) or payload['execution_policy'].get('subject_mask') != POLICY:
        fail('New masks require a current session')
    if not isinstance(run_id,str) or not RUN_ID_PATTERN.fullmatch(run_id) or type(timeout) is not int or not 1 <= timeout <= 86400:
        fail('Invalid mask run ID or timeout')
    request = session_path(session,request_value,must_exist=True,allowed_roots=('reports',))
    value = strict_json_bytes(request.read_bytes(),document='subject mask request')
    if not isinstance(value,dict):
        fail('Subject mask request must be an object')
    source = session_path(session,value.get('source',''),must_exist=True,allowed_roots=('artifacts',))
    state = source_state(session,payload,source)
    if state['domain'] != 'nonlinear' or not state['separation_run'] or not state['run']:
        fail('Subject masks require an accepted nonlinear starless parent')
    _accepted_review(session,state['run']['id'])
    _accepted_review(session,state['separation_run'])
    validate_request(value,source)
    source_binding,request_binding,code = record(session,source),record(session,request),implementation()
    probe = record(session,session/'reports/tool-probe.json')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not fingerprint_matches(runtime['fingerprint']):
        fail('A frozen compatible Siril runtime is required')
    directory = session_path(session,f'reports/{run_id}',allowed_roots=('reports',))
    if directory.exists():
        fail('Subject mask evidence already exists; use a new run ID')
    directory.mkdir()
    array = rasterize(value)
    Image.fromarray(array).save(directory/'weights.tif')
    Image.fromarray(np.rint(array*255).astype('uint8')).save(directory/'mask.png')
    display = np.clip(pixels(source)[:,::-1].transpose(1,2,0),0,1)
    overlay = display*(1-.35*array[...,None])+np.array([0.,1.,0.])*.35*array[...,None]
    Image.fromarray(np.rint(overlay*255).astype('uint8')).save(directory/'overlay.png')
    panel = Image.new('RGB',(2*array.shape[1],array.shape[0]))
    panel.paste(Image.open(directory/'mask.png').convert('RGB'),(0,0))
    panel.paste(Image.open(directory/'overlay.png'),(array.shape[1],0))
    panel.save(directory/'mask-review.png')
    _native(session,runtime,directory,'native',native_rows(directory),directory/'mask.fit',time.monotonic()+timeout)
    if source_binding != record(session,source) or request_binding != record(session,request) or code != implementation():
        fail('Subject mask source, request or implementation changed during preparation')
    report = {'schema':REPORT,'policy':POLICY,'source':source_binding,'request':request_binding,
              'implementation':code,'runtime':runtime,'probe':probe,'mask':record(session,directory/'mask.fit'),
              'status':'review_required' if array.any() else 'skipped','requires_visual_review':True,
              'evidence':[record(session,p) for p in sorted(directory.iterdir()) if p.is_file()]}
    path = directory/'subject-mask.json'
    atomic_write_json(path,report)
    load(session,source,path)
    return {'status':report['status'],'subject_mask':str(path),'binding':record(session,path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare',))
    parser.add_argument('--session',required=True)
    parser.add_argument('--request',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--timeout',type=int,default=1800)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.session,args.request,args.run_id,args.timeout),allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status':'failed','reason':str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

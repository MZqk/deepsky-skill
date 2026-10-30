"""Native, luminance-locked RGB bilateral chroma denoise and paired color probes."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess

import numpy as np

from deep_sky_siril_artifacts import read_fits_header, read_scientific_pixels
from deep_sky_siril_contract import (ContractError, RUN_ID_PATTERN, SKILL_ROOT, atomic_write_json,
    fingerprint, load_json, protocol_reference_bindings, session_path, sha256_file)
from deep_sky_siril_stretch import record
from siril_manual_bundle import strict_json_bytes

POLICY = 'chroma_bilateral_v1'
PROTOCOL = 'restoration.denoise-nonlinear'
SCHEMA = 'starun-siril.chroma-denoise-request.v1'
EPS = 1e-6
WEIGHTS = np.array([.2126, .7152, .0722])
PARAMETERS = {'strength', 'diameter', 'sigma_spatial', 'sigma_range'}


def fail(message):
    raise ContractError('chroma_denoise_invalid', message)


def pixels(path):
    values, selection = read_scientific_pixels(path)
    if values.shape[0] != 3 or not np.isfinite(values).all() or selection.get('bounds') not in (None, [0., 1.]):
        fail('Chroma denoise requires finite RGB pixels with unit float bounds')
    return values.astype(float)


def validate_request(p):
    fields = {'schema', 'source', 'regions', 'goal', 'parameter_reason', 'color_probe'} | PARAMETERS
    if not isinstance(p, dict) or set(p) != fields or p['schema'] != SCHEMA:
        fail('Invalid chroma denoise request fields or schema')
    for key in ('goal', 'parameter_reason'):
        if not isinstance(p[key], str) or not p[key].strip() or len(p[key]) > 2000:
            fail('Explain the observed color noise, goal and parameter choices')
    for key in PARAMETERS - {'diameter'}:
        if type(p[key]) not in (int, float) or not math.isfinite(p[key]):
            fail('Chroma parameters must be finite numbers')
    if (not 0 < p['strength'] <= 1 or not 0 < p['sigma_range'] <= 1
        or not 0 < p['sigma_spatial'] <= 32 or type(p['diameter']) is not int
        or p['diameter'] not in range(3, 20, 2)):
        fail('Use strength/range sigma in (0,1], spatial sigma in (0,32], odd diameter 3..19')
    probe = p['color_probe']
    from deep_sky_siril_color import validate_saturation_protection
    if (not isinstance(probe, dict) or set(probe) != {'D', 'saturation_protect_start', 'saturation_protect_end'}
        or type(probe['D']) not in (int, float) or not math.isfinite(probe['D']) or not 0 < probe['D'] <= 10):
        fail('Declare the planned GHS D and saturation protection for both color probes')
    validate_saturation_protection(probe)
    return p


def bound(session, item, roots=('reports', 'artifacts', 'previews', 'runs', 'reviews')):
    if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'size'}
        or type(item.get('size')) is not int):
        fail('Evidence requires an exact path/hash/size binding')
    path = session_path(session, item['path'], must_exist=True, allowed_roots=roots)
    if item != record(session, path):
        fail('Chroma evidence changed: '+item['path'])
    return path


def source_context(session, payload, source):
    from deep_sky_siril_processing import source_state
    from deep_sky_siril_core import _accepted_review
    from deep_sky_siril_session import execution_policy_is_current
    if not execution_policy_is_current(payload) or payload['execution_policy'].get('chroma_denoise') != POLICY:
        fail('New chroma denoise execution requires a current session')
    state = source_state(session, payload, source)
    if (state['domain'] != 'nonlinear' or not state['separation_run'] or not state['run']
        or payload['context']['channel_mode'] in {'mono', 'unknown'}):
        fail('Chroma denoise requires an accepted nonlinear RGB starless parent')
    if any(row.get('chroma_denoise') for row in state.get('lineage', [])):
        fail('Do not accumulate chroma denoise; revise from the same original parent')
    pixels(source)
    evidence = []
    for run in state.get('lineage', []):
        evidence.append(record(session, session/'runs'/(run['id']+'.json')))
    for run_id in dict.fromkeys((state['run']['id'], state['separation_run'])):
        _, review = _accepted_review(session, run_id)
        evidence.append(record(session, session/review['path']))
    return evidence


def script_text(root, source, run_id, p, *, parameters=None, primary=None):
    from deep_sky_siril_processing import _variable
    root, source = Path(root).resolve(), Path(source).resolve()
    q = lambda value: json.dumps(str(value))
    paths = {name: root/'artifacts'/(run_id+'-'+name+'.fit') for name in
             ('r', 'g', 'b', 'filtered', 'dr', 'dg', 'db', 'delta-y', 'cr', 'cg', 'cb', 'weight', 'out-r', 'out-g', 'out-b', 'candidate')}
    if primary is not None:
        paths['candidate'] = Path(primary)
    views = {name: root/'previews'/(run_id+'-'+name+'.jpg') for name in ('parent', 'weight', 'candidate')}
    v = lambda key: _variable(paths[key], root)
    rows = ['requires 1.4.4 1.5.0', 'set32bits']
    if parameters is not None:
        rows.append('# chroma-denoise: '+json.dumps({'variant': POLICY, 'parameters': parameters}))
    rows += ['load '+q(source), 'icc_assign sRGB', 'savejpg '+q(views['parent'].with_suffix(''))+' 95',
             'split '+' '.join(q(paths[k].with_suffix('')) for k in ('r', 'g', 'b')),
             f'epf -d={p["diameter"]} -si={50*math.sqrt(p["sigma_range"]):.17g} -ss={p["sigma_spatial"]:.17g} -mod=1',
             'save '+q(paths['filtered'].with_suffix(''))+' -chksum',
             'split '+' '.join(q(paths[k].with_suffix('')) for k in ('dr', 'dg', 'db'))]
    def pm(expression, key):
        rows.extend(['pm '+q(expression)+' -nosum', 'save '+q(paths[key].with_suffix(''))+' -chksum'])
    pm(' + '.join(f'{w} * ({v(d)} - {v(a)})' for w,d,a in zip(WEIGHTS, ('dr','dg','db'), ('r','g','b'))), 'delta-y')
    for a,d,c in zip(('r','g','b'), ('dr','dg','db'), ('cr','cg','cb')):
        pm(f'{v(d)} - {v(a)} - {v("delta-y")}', c)
    limits = [f'iif({v(c)} > 0, (0.999999 - {v(a)}), ({v(a)} - 0.000001)) / max(abs({v(c)}), 0.000000000001)'
              for a,c in zip(('r','g','b'), ('cr','cg','cb'))]
    limit = str(p['strength'])
    for expression in limits:
        limit = f'min({limit}, {expression})'
    low = f'min({v("r")}, min({v("g")}, {v("b")}))'
    high = f'max({v("r")}, max({v("g")}, {v("b")}))'
    pm(f'iif({low} > 0.000001, iif({high} < 0.999999, max(0, {limit}), 0), 0)', 'weight')
    rows += ['icc_assign sRGB', 'savejpg '+q(views['weight'].with_suffix(''))+' 95']
    for a,c,out in zip(('r','g','b'), ('cr','cg','cb'), ('out-r','out-g','out-b')):
        pm(f'{v(a)} + {v("weight")} * {v(c)}', out)
    rows += ['rgbcomp '+' '.join(q(paths[k]) for k in ('out-r','out-g','out-b'))+' '+q('-out='+str(paths['candidate']))+' -nosum',
             'load '+q(paths['candidate']), 'icc_assign sRGB', 'save '+q(paths['candidate'].with_suffix(''))+' -chksum',
             'stat main', 'savejpg '+q(views['candidate'].with_suffix(''))+' 95', 'close']
    return '\n'.join(rows)+'\n', paths, views


def arithmetic(parent, filtered, p):
    delta = filtered-parent
    delta -= np.einsum('c,cyx->yx', WEIGHTS, delta)[None]
    limits = np.where(delta > 0, 1-EPS-parent, parent-EPS)/np.maximum(np.abs(delta), 1e-12)
    eligible = ((parent > EPS) & (parent < 1-EPS)).all(axis=0)
    weight = np.where(eligible, np.maximum(0, np.minimum(p['strength'], limits.min(axis=0))), 0)
    return parent+weight*delta, delta, weight


def measure_recipe(source, paths, p):
    from deep_sky_siril_processing import verify_color_metadata
    a, d, output = pixels(source), pixels(paths['filtered']), pixels(paths['candidate'])
    if a.shape != d.shape or a.shape != output.shape or read_fits_header(paths['candidate']).get('BITPIX') != -32:
        fail('Chroma output must retain float32 RGB geometry')
    expected, delta, weight = arithmetic(a, d, p)
    actual_weight = read_scientific_pixels(paths['weight'])[0]
    if (actual_weight.shape != (1, *a.shape[1:]) or not np.isfinite(actual_weight).all()
        or np.any(actual_weight < 0) or np.any(actual_weight > p['strength']+EPS)):
        fail('Invalid shared RGB correction weight')
    errors = {'blend': float(np.max(np.abs(output-expected))),
              'luminance': float(np.max(np.abs(np.einsum('c,cyx->yx', WEIGHTS, output-a)))),
              'weight': float(np.max(np.abs(actual_weight[0]-weight)))}
    dy = np.einsum('c,cyx->yx', WEIGHTS, d-a)
    expected_layers = {**dict(zip(('r','g','b'), a)), **dict(zip(('dr','dg','db'), d)),
                       'delta-y': dy, **dict(zip(('cr','cg','cb'), delta)),
                       **dict(zip(('out-r','out-g','out-b'), output))}
    for name, values in expected_layers.items():
        layer = read_scientific_pixels(paths[name])[0]
        if layer.shape != (1, *a.shape[1:]) or not np.isfinite(layer).all():
            fail('Invalid chroma intermediate: '+name)
        errors[name] = float(np.max(np.abs(layer[0]-values)))
    protected = ~((a > EPS) & (a < 1-EPS)).all(axis=0)
    if (max(errors.values()) > EPS or not np.array_equal(output[:,protected], a[:,protected])
        or np.any(((a>0)&(output<=0)) | ((a<1)&(output>=1)))):
        fail('Chroma luminance, protected pixels, endpoint or native arithmetic check failed')
    verify_color_metadata(source, paths['candidate'])
    return {'status': 'measured', 'max_abs_errors': errors, 'protected_pixel_count': int(protected.sum()),
            'changed': bool(np.any(output != a)), 'new_endpoints': 0}


def load_parameters(session, item):
    path = bound(session, item, ('reports',))
    report = load_json(path)
    if report.get('schema') != 'starun-siril.chroma-denoise-parameters.v1' or report.get('policy') != POLICY:
        fail('Invalid chroma parameter report')
    validate_request(report['request'])
    from deep_sky_siril_stretch_v2 import implementation_matches
    if not implementation_matches(session, report['implementation']):
        fail('Chroma implementation changed')
    for evidence in [report['source'], report['regions'], report['request_file'], *report['upstream'], *report['files']]:
        bound(session, evidence)
    from deep_sky_siril_color import configuration_binding
    expected_configs = {str((path.parent/(name+'.ini')).relative_to(session)) for name in ('denoise','color-before','color-after')}
    if (len(report['configurations']) != 3
        or {c['path'] for c in report['configurations']} != expected_configs):
        fail('All three frozen native configurations are required')
    for config in report['configurations']:
        config_path = bound(session, config)
        configuration_binding(session, config_path)
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if report['runtime'] != runtime or fingerprint(Path(runtime['path']), include_path=False) != {
        k:runtime['fingerprint'][k] for k in ('sha256','size')}:
        fail('Frozen Siril runtime changed')
    if load_json(bound(session, report['request_file'])) != report['request']:
        fail('Chroma request differs from the frozen proposal')
    trial_id = report['trial_id']
    if not isinstance(trial_id,str) or not RUN_ID_PATTERN.fullmatch(trial_id):
        fail('Invalid chroma trial identity')
    trial_root = path.parent
    if trial_root != session/'reports'/trial_id:
        fail('Chroma report must retain its original trial directory')
    source = bound(session, report['source'])
    expected_script, trial_layers, _ = script_text(trial_root, source, trial_id, report['request'])
    if ((trial_root/'denoise.ssf').read_text() != expected_script
        or report['trial_layers'] != {k:str(v.relative_to(session)) for k,v in trial_layers.items()}
        or measure_recipe(source, trial_layers, report['request']) != report['checks']):
        fail('Chroma trial recipe or arithmetic differs from the frozen proposal')
    from deep_sky_siril_color import script_text as color_script, PRESERVATION, measure_unmasked_curve
    probe = {**report['request']['color_probe'], 'mask_mode':'none', 'mask_policy':PRESERVATION}
    required = set(trial_layers.values()) | {trial_root/'denoise.ssf', trial_root/'denoise.log', trial_root/'denoise.ini',
                                           trial_root/'chroma-regions.png', trial_root/'color-regions.png'}
    for name, image in (('before',source), ('after',trial_layers['candidate'])):
        value, candidate, outputs, binding = color_script(trial_root, image, 'color-'+name, probe)
        if (trial_root/('color-'+name+'.ssf')).read_text() != value:
            fail('Color probes must use the same planned D and saturation protection')
        required.update(outputs)
        required.update(trial_root/('color-'+name+suffix) for suffix in ('.ssf','.log','.ini'))
        measure_unmasked_curve(trial_root, image, candidate, binding)
    if not required <= {bound(session,f) for f in report['files']}:
        fail('Chroma trial evidence is incomplete')
    from deep_sky_siril_metrics import measure_regions
    if measure_regions(source, None, bound(session, report['regions']), scientific=True)['status'] != 'measured':
        fail('Chroma ROI geometry or source changed')
    return report


def declaration(value):
    rows = [r.strip() for r in value.splitlines() if r.strip().startswith('# chroma-denoise')]
    if len(rows) != 1 or not rows[0].startswith('# chroma-denoise:'):
        fail('Exactly one # chroma-denoise: declaration is required')
    p = strict_json_bytes(rows[0].split(':',1)[1].encode(), document='chroma declaration')
    if not isinstance(p, dict) or set(p) != {'variant','parameters'} or p['variant'] != POLICY:
        fail('Invalid chroma variant declaration')
    return p


def prepare(session, source, primary, value, commands, run_id):
    from deep_sky_siril_processing import script_commands
    p = declaration(value)
    report = load_parameters(session, p['parameters'])
    if report['source'] != record(session, source):
        fail('Chroma declaration uses a different parent')
    expected, paths, views = script_text(session, source, run_id, report['request'], parameters=p['parameters'], primary=primary)
    if script_commands(expected) != commands or declaration(expected) != p:
        fail('Chroma SSF must match the complete native recipe')
    return {'policy': POLICY, 'parameters': p['parameters'],
            'layers': {k:str(v.relative_to(session)) for k,v in paths.items()},
            'previews': {k:str(v.relative_to(session)) for k,v in views.items()}}


def measure(session, source, primary, binding):
    report = load_parameters(session, binding['parameters'])
    paths = {k:session_path(session, v, must_exist=True, allowed_roots=('artifacts',)) for k,v in binding['layers'].items()}
    if paths['candidate'] != primary or report['source'] != record(session, source):
        fail('Chroma scientific parent or primary changed')
    result = measure_recipe(source, paths, report['request'])
    for key in ('filtered','candidate'):
        trial = session/report['trial_layers'][key]
        if np.max(np.abs(pixels(paths[key])-pixels(trial))) > EPS:
            fail('Formal chroma pixels differ from the bound native trial')
    return {**result, 'report': binding['parameters']}


def run_native(session, root, name, value, runtime):
    from deep_sky_siril_color import TRIAL_SETTINGS, configuration_binding
    from deep_sky_siril_tooling import _subprocess_environment
    from deep_sky_siril_artifacts import diagnose_siril_log
    import configparser
    config = root/(name+'.ini')
    parser = configparser.ConfigParser()
    parser.read_dict({section:{key:str(value).lower() for key,value in values.items()}
                      for section,values in TRIAL_SETTINGS.items()})
    with config.open('w') as stream:
        parser.write(stream)
    configuration_binding(session, config)
    script, log = root/(name+'.ssf'), root/(name+'.log')
    script.write_text(value)
    offline = not (session/'session.json').exists() or load_json(session/'session.json')['context']['offline']
    try:
        result = subprocess.run([runtime['path'], *(['--offline'] if offline else []), '--initfile='+str(config), '--directory='+str(root), '--script='+str(script)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=1800, env=_subprocess_environment('siril_runtime'))
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b''
        log.write_text(output.decode(errors='replace') if isinstance(output,bytes) else output)
        fail('Chroma trial timed out; diagnostic log retained')
    log.write_text(result.stdout)
    if diagnose_siril_log(result.stdout, exit_code=result.returncode, timed_out=False, execution_valid=True)['status'] == 'failed':
        fail('Native chroma trial failed; inspect '+str(log))
    configuration_binding(session, config)
    return config


def solve(session_value, request_value, run_id):
    from deep_sky_siril_session import load_session, effective_session_payload
    from deep_sky_siril_metrics import measure_regions
    from deep_sky_siril_color import script_text as color_script, color_statistics, save_region_panels, PRESERVATION
    from deep_sky_siril_stretch_v2 import implementation
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    request_path = session_path(session, request_value, must_exist=True, allowed_roots=('reports',))
    p = validate_request(load_json(request_path))
    source = session_path(session, p['source'], must_exist=True, allowed_roots=('artifacts',))
    upstream = source_context(session, payload, source)
    regions = session_path(session, p['regions'], must_exist=True, allowed_roots=('reports',))
    measurements = measure_regions(source, None, regions, scientific=True)
    if measurements['status'] != 'measured' or not any(r['role'] in {'faint','core'} for r in measurements['regions']):
        fail('Declare actual source-bound noise, subject and color-boundary ROIs')
    if not isinstance(run_id,str) or not RUN_ID_PATTERN.fullmatch(run_id):
        fail('Invalid chroma run ID')
    root = session_path(session, 'reports/'+run_id, allowed_roots=('reports',))
    if root.exists(): fail('Preserve existing trial evidence; use a fresh run ID')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or runtime.get('version') != '1.4.4':
        fail('Chroma range conversion requires verified Siril 1.4.4')
    from deep_sky_siril_contract import fingerprint_matches
    if not fingerprint_matches(runtime['fingerprint']):
        fail('Frozen Siril binary changed before the trial')
    source_before, regions_before = record(session, source), record(session, regions)
    request_before = record(session, request_path)
    code_before = implementation()
    root.mkdir(); (root/'artifacts').mkdir(); (root/'previews').mkdir()
    value, layers, views = script_text(root, source, run_id, p)
    configs = [run_native(session, root, 'denoise', value, runtime)]
    check = measure_recipe(source, layers, p)
    probe = {**p['color_probe'], 'mask_mode':'none', 'mask_policy':PRESERVATION}
    probe_outputs = []
    for name, image in (('before',source), ('after',layers['candidate'])):
        value, candidate, _, _ = color_script(root, image, 'color-'+name, probe)
        configs.append(run_native(session, root, 'color-'+name, value, runtime))
        probe_outputs.append(candidate)
    rows = load_json(regions)['regions']
    save_region_panels(source, layers['candidate'], rows, root/'chroma-regions.png')
    save_region_panels(*probe_outputs, rows, root/'color-regions.png')
    if (record(session,source) != source_before or record(session,regions) != regions_before
        or record(session,request_path) != request_before or code_before != implementation()):
        fail('Chroma source, ROI or implementation changed during the trial')
    report = {'schema':'starun-siril.chroma-denoise-parameters.v1', 'policy':POLICY, 'request':p, 'request_file':request_before, 'trial_id':run_id,
        'source':source_before, 'regions':regions_before, 'upstream':upstream, 'runtime':runtime,
        'implementation':code_before, 'checks':check, 'configurations':[record(session,c) for c in configs],
        'trial_layers':{k:str(v.relative_to(session)) for k,v in layers.items()},
        'denoise_diagnostics':color_statistics(source, layers['candidate'], rows, extended=True),
        'color_diagnostics':{'before':color_statistics(source, probe_outputs[0], rows, extended=True),
                             'after':color_statistics(layers['candidate'], probe_outputs[1], rows, extended=True)},
        'files':[record(session,f) for f in sorted(root.rglob('*')) if f.is_file()]}
    atomic_write_json(root/'parameters.json', report)
    return {'status':'review_required', 'parameters':str(root/'parameters.json')}


def generate(session_value, parameters_value, run_id):
    from deep_sky_siril_session import load_session, effective_session_payload
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    path = session_path(session, parameters_value, must_exist=True, allowed_roots=('reports',))
    binding = record(session,path)
    report = load_parameters(session,binding)
    source = bound(session,report['source'])
    if source_context(session,payload,source) != report['upstream']:
        fail('Chroma accepted ancestry changed')
    if not isinstance(run_id,str) or not RUN_ID_PATTERN.fullmatch(run_id): fail('Invalid chroma run ID')
    script = session/'scripts'/(run_id+'.ssf')
    value, layers, views = script_text(session,source,run_id,report['request'],parameters=binding)
    provenance = script.with_suffix('.provenance.json')
    if any(p.exists() or p.is_symlink() for p in [script, provenance, session/'runs'/(run_id+'.json'), *layers.values(), *views.values()]):
        fail('Chroma generation preserves existing evidence; use a fresh run ID')
    script.write_text(value)
    atomic_write_json(provenance, {'schema':'starun-siril.ssf-provenance.v1','contract_version':'1',
        'run_id':run_id,'protocol':PROTOCOL,'script':{'path':str(script.relative_to(session)),'sha256':sha256_file(script)},
        'references':protocol_reference_bindings(PROTOCOL),
        'command_policy':{'path':'references/command-policy.json','sha256':sha256_file(SKILL_ROOT/'references/command-policy.json')},
        'manual_lookup':{'status':'not_needed','reason':'Complete version-tested native chroma recipe','evidence':[]},
        'rationale':{'applicability':report['request']['goal'],'parameter_choices':[report['request']['parameter_reason']]}})
    return {'status':'proposed','script':str(script),'primary_output':str(layers['candidate']),
            'expected':list(map(str,[*layers.values(),*views.values()]))}


def review_paths(session, run):
    binding = run['chroma_denoise']
    report = load_parameters(session,binding['parameters'])
    items = [binding['parameters'],report['regions']]
    items += [f for f in report['files'] if Path(f['path']).suffix in {'.jpg','.png'}]
    paths = {f['path']:bound(session,f) for f in items}
    paths.update({v:session/v for v in binding['previews'].values()})
    return paths


def assessment_skeleton(session, run):
    binding = run['chroma_denoise']
    report = load_parameters(session,binding['parameters'])
    rows = load_json(bound(session,report['regions']))['regions']
    return {'diagnostics':binding['parameters'],'noise_benefit':None,'noise_observation':'',
        'structures':[{'region':{'regions':report['regions'],'id':r['id']},'retention':None,'observation':''}
                      for r in rows if r['role'] != 'background'],'adoption_reason':''}


def validate_assessment(session, run, review):
    expected = assessment_skeleton(session,run)
    block = review.get('denoise_assessment')
    if (not isinstance(block,dict) or set(block) != set(expected) or block.get('diagnostics') != expected['diagnostics']
        or block.get('noise_benefit') not in {'observed','not_observed','uncertain'}
        or not isinstance(block.get('structures'),list) or len(block['structures']) != len(expected['structures'])):
        fail('Chroma review must bind diagnostics and every protected ROI')
    for key in ('noise_observation','adoption_reason'):
        if not isinstance(block.get(key),str) or not block[key].strip(): fail('Explain actual chroma benefit and adoption')
    for row,want in zip(block['structures'],expected['structures']):
        if (set(row) != set(want) or row['region'] != want['region'] or row['retention'] not in {'retained','loss','uncertain'}
            or not isinstance(row['observation'],str) or not row['observation'].strip()):
            fail('Review color boundaries, faint colors and structures in every ROI')
    if not set(review_paths(session,run)) <= {f['path'] for f in review['inspected_materials']}:
        fail('Inspect actual pre/post color probes, masks and native-resolution ROI panels')
    if review['verdict'] == 'accept' and (block['noise_benefit'] != 'observed'
        or any(r['retention'] != 'retained' for r in block['structures'])
        or any(review['gates'][k] != 'pass' for k in ('structure','color'))
        or not run['processing_checks']['chroma_denoise']['changed']):
        fail('Accept chroma denoise only with observed benefit and retained color boundaries/structures')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    for name, flag in (('solve','request'),('generate','parameters')):
        sub = commands.add_parser(name)
        sub.add_argument('--session',required=True); sub.add_argument('--'+flag,required=True)
        sub.add_argument('--run-id',required=True)
    args = parser.parse_args()
    try:
        result = (solve(args.session,args.request,args.run_id) if args.command == 'solve'
                  else generate(args.session,args.parameters,args.run_id))
        print(json.dumps(result,allow_nan=False))
    except (ContractError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status':'error','code':'chroma_denoise_invalid','message':str(exc)}))
        raise SystemExit(2)


if __name__ == '__main__':
    main()

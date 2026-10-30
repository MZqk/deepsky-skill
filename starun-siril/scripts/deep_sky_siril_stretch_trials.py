"""Preserve Stage 7 trial images without granting scientific-parent authority."""
from pathlib import Path
import json

from deep_sky_siril_contract import ContractError, RUN_ID_PATTERN, fingerprint, load_json, session_path

POLICY = 'retained_artifacts_v1'
SCHEMA = 'starun-siril.stretch-trial-archive.v1'
DIRECTORY = 'artifacts/stage7-trials'
PARAMETERS = {'starun-siril.stretch-parameters.v3', 'starun-siril.stretch-parameters.v2', 'starun-siril.derived-stretch-parameters.v2'}


def enabled(payload):
    return payload.get('execution_policy', {}).get('stretch_trial_artifacts') == POLICY


def directory(session, run_id):
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ContractError('trial_archive_invalid', 'Invalid Stage 7 trial run ID')
    return session_path(session, DIRECTORY+'/'+run_id, allowed_roots=('artifacts',))


def expected(session, evidence):
    parameter = evidence.get('schema') in PARAMETERS
    run_id = evidence['run_id'] if parameter else evidence['id']
    target = directory(session, run_id)
    images, destinations = [], set()

    def add(binding, **metadata):
        source = session_path(session, binding['path'], allowed_roots=('reports', 'artifacts', 'previews'))
        artifact = session_path(session, target/source.name, allowed_roots=('artifacts',))
        if artifact in destinations:
            raise ContractError('trial_archive_invalid', 'Stage 7 archive image names collide')
        destinations.add(artifact)
        original = {key: binding[key] for key in ('path', 'sha256', 'size')}
        images.append({**metadata, 'source': original,
            'artifact': {**original, 'path': artifact.relative_to(session).as_posix()}})

    if parameter:
        prefix = []
        steps = evidence['steps']
        if evidence.get('schema') == 'starun-siril.stretch-parameters.v3':
            steps = [step for branch in evidence['branches'].values() for step in branch['steps']
                     if step.get('local_panel', {}).get('path', '').startswith('reports/'+run_id+'/')]
        for index, step in enumerate(steps, 1):
            for trial_index, trial in enumerate(step.get('trials', []), 1):
                for role, binding in trial.get('retained', {}).items():
                    add(binding, step=index, trial=trial_index, role=role,
                        eligible=trial['audit']['eligible'], transfer_chain=prefix+[trial['parameters']])
            if step.get('local_panel'):
                add(step['local_panel'], step=index, trial=None, role='roi-panel',
                    eligible=None, transfer_chain=None)
            if step.get('selected_parameters'):
                prefix.append(step['selected_parameters'])
        bindings = evidence['bindings']
        if evidence.get('schema') == 'starun-siril.stretch-parameters.v3':
            existing = {image['source']['path'] for image in images}
            for item in evidence.get('native_records', []):
                if item['path'] not in existing and Path(item['path']).suffix in {'.fit', '.jpg', '.png'}:
                    add(item, step=None, trial=None, role='prefix-diagnostic', eligible=None, transfer_chain=None)
        source = bindings['source']
        request = bindings['request']
        derived = bindings.get('derived_source')
        origin = 'reports/'+run_id+'/stretch-parameters.json'
    else:
        if evidence.get('protocol') != 'stretch' or not evidence.get('hdr_transfer'):
            raise ContractError('trial_archive_invalid', 'Only native Stage 7 HDR runs use receipt archives')
        for binding in evidence['outputs']:
            if Path(binding['path']).suffix.lower() in {'.fit', '.fits', '.fts', '.jpg', '.jpeg', '.png'}:
                add(binding, step=None, trial=None, role='hdr-output', eligible=None, transfer_chain=None)
        if evidence.get('stretch_visibility'):
            view = load_json(session_path(session, evidence['stretch_visibility']['path'], must_exist=True, allowed_roots=('reports',)))
            add(view['panel'], step=None, trial=None, role='visibility-panel', eligible=None, transfer_chain=None)
        source, request, derived = {'path': evidence['source_path'], **evidence['source']}, None, None
        origin = 'runs/'+run_id+'.json'
    return {'schema': SCHEMA, 'run_id': run_id, 'status': evidence['status'],
        'reason': evidence.get('reason') or evidence.get('processing_checks', {}).get('reason'),
        'evidence_path': origin, 'source': source, 'request': request, 'derived_source': derived,
        'hdr_transfer': evidence.get('hdr_transfer'), 'scientific_parent': False, 'images': images}


def create(session, evidence):
    from deep_sky_siril_core import _copy_atomic
    value = expected(session, evidence)
    target = directory(session, value['run_id'])
    # Host file shims can raise a plain OSError with EEXIST for an existing
    # directory, so exist_ok alone is not reliable here.
    if not target.parent.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    for image in value['images']:
        source = session_path(session, image['source']['path'], must_exist=True,
            allowed_roots=('reports', 'artifacts', 'previews'))
        binding = {key: image['source'][key] for key in ('sha256', 'size')}
        if fingerprint(source, include_path=False) != binding:
            raise ContractError('trial_archive_invalid', 'Trial image changed before archiving: '+str(source))
        output = session_path(session, image['artifact']['path'], allowed_roots=('artifacts',))
        _copy_atomic(source, output)
        if fingerprint(output, include_path=False) != binding:
            raise ContractError('trial_archive_invalid', 'Trial archive copy differs from its source')
    path = target/'index.json'
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    return {'path': path.relative_to(session).as_posix(), **fingerprint(path, include_path=False)}


def verify(session, evidence):
    from deep_sky_siril_stretch import bound_file
    try:
        value = expected(session, evidence)
        binding = evidence['trial_artifacts']
        path = bound_file(session, binding, ('artifacts',))
        if path != directory(session, value['run_id'])/'index.json' or load_json(path) != value:
            raise ContractError('trial_archive_invalid', 'Stage 7 archive index differs from its native evidence')
        paths = {path.relative_to(session).as_posix()}
        for image in value['images']:
            bound_file(session, image['artifact'], ('artifacts',))
            paths.add(image['artifact']['path'])
        return paths
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError('trial_archive_invalid', 'Missing or invalid Stage 7 trial archive') from exc


def retained_paths(session, payload):
    if not enabled(payload):
        return set()
    paths = set()
    for path in sorted((session/'reports').glob('*/stretch-parameters.json')):
        evidence = load_json(path)
        if evidence.get('schema') in PARAMETERS:
            # A failed report may predate its archive (archiving itself can fail);
            # there is no retained image set to inventory in that case.
            if evidence.get('status') == 'failed' and not evidence.get('trial_artifacts'):
                continue
            paths.update(verify(session, evidence))
    for path in sorted((session/'runs').glob('*.json')):
        evidence = load_json(path)
        if evidence.get('protocol') == 'stretch' and evidence.get('hdr_transfer'):
            paths.update(verify(session, evidence))
    return paths

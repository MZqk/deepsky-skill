"""Boxed source context and signed local pairs without changes to solver inputs."""
import copy
import json
from pathlib import Path
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_metrics as metrics
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_visibility as visibility
from .test_deep_sky_siril_stretch import context, write
from .test_stretch_v22 import case as stretch_case
from .test_recomposition_proposals import case as replay_case
from . import test_stretch_v4_flow_guidance as flow_guidance
from ._stretch_factory import bind_exception

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-07-stretch.md'
PRIOR = contract.load_json(ROOT/'tests/fixtures/stretch-roi-pairs-guidance-prior.json')
NAMES = ('filament', 'adjacent-diffuse', 'adjacent-bright', 'dark-lane',
         'outer-edge', 'adjacent-outside', 'bright-rim', 'adjacent-inner')


def example(anchor, end, session):
    text = (ROOT/'references/stage7-diagnostics.md').read_text().split(f'<a id="{anchor}"></a>', 1)[1].split(end, 1)[0]
    code, = re.findall(r'^```python\n(.*?)^```', text, re.M | re.S)
    code = code.replace('Path("/abs/session")', f'Path({str(session)!r})')
    code = code.replace('"reports/071-morphology/profile-request.json"','"reports/069-roi/exception-request.json"')
    return code.replace('context_rect = (100, 100, 600, 600)', 'context_rect = (0, 0, 96, 96)')


@pytest.fixture
def roi_case(stretch_case, monkeypatch, request):
    channels, mode = getattr(request, 'param', (3, 'enhance'))
    session, original, _, _, proposal, _ = stretch_case
    source = session/'artifacts/linear.fit'
    linear = stretch.pixels(original)
    if channels == 3: linear = np.repeat(linear, 3, axis=0)
    write(source, linear if channels > 1 else linear[0])
    solving_path = session/proposal['regions']['path']
    solving = contract.load_json(solving_path)
    solving['reference']['sha256'] = contract.sha256_file(source)
    contract.atomic_write_json(solving_path, solving)
    proposal['regions'] = stretch.record(session, solving_path)
    proposal.update(schema=protected.PROPOSAL_REQUEST, source={'path': 'artifacts/linear.fit', 'sha256': contract.sha256_file(source)})
    proposal['operators'][0]['factors'] = {'midtones': .1}
    proposal['operators'].append(copy.deepcopy(proposal['operators'][0]))
    tail = {'purpose':'Explicit same-prefix diagnostic tail control', 'factors':{'D':.2,'B':.5},
        'landmarks':{key:{'region':region,'statistic':statistic} for key,region,statistic in
            [('LP','sky','p99'),('SP','faint','median'),('HP','core','p01')]}}
    item = bind_exception(session,proposal,'069-roi',tail=tail if channels==3 else None)
    proposal = contract.load_json(Path(item['request']))
    contract.atomic_write_json(session/'reports/070-proposal.json', proposal)
    regions = [{'id': name, 'role': 'faint', 'rect': [4+12*(i%4), 4+24*(i//4), 12+12*(i%4), 12+24*(i//4)]}
               for i, name in enumerate(NAMES)]
    control = np.full((channels, 96, 96), .2, dtype=np.float32)
    factors = np.array([1., .9, .8][:channels])
    for region, value in zip(regions, (.4, .3, .5, .35, .25, .22, .7, .4)):
        x0, y0, x1, y1 = region['rect']
        control[:, y0:y1, x0:x1] = (value*factors)[:, None, None]
    candidate = control.copy()
    if mode == 'lift': candidate += .05
    elif mode == 'grain':
        yy, xx = np.indices((96, 96)); candidate += np.where((xx+yy)%2, .02, -.02)[None]
    elif mode == 'reverse': candidate[:, 4:12, 40:48] = (.6*factors)[:, None, None]
    elif mode == 'nonfinite': candidate[0, 5, 5] = np.nan
    elif mode == 'channels': candidate = candidate[:1]
    else:
        gains = np.array([.04, .02, -.01][:channels])
        candidate[:, 4:12, 4:12] += gains[:, None, None]
    if mode == 'missing': regions = [r for r in regions if r['id'] != 'dark-lane']
    region_path = session/'reports/070-diagnostic-regions.json'
    contract.atomic_write_json(region_path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': contract.sha256_file(source), 'width': 96, 'height': 96}, 'regions': regions})
    control_path = write(session/'reports/prefix.fit', control[:, ::-1] if channels > 1 else control[0, ::-1])
    candidate_path = write(session/'artifacts/final.fit', candidate[:, ::-1] if len(candidate) > 1 else candidate[0, ::-1])
    yy, xx = np.indices((96, 96))
    original_preview = np.stack([xx, yy, (xx+3*yy)%256], axis=-1).astype(np.uint8)
    preview_path = session/'previews/starless.png'; Image.fromarray(original_preview).save(preview_path)
    previews = []
    for name, values in (('prefix', control), ('final', candidate)):
        values = np.nan_to_num(values)
        if len(values) == 1: values = np.repeat(values, 3, axis=0)
        path = session/f'reports/{name}.png'
        Image.fromarray(np.uint8(np.clip(np.moveaxis(values, 0, -1), 0, 1)*255)).save(path)
        previews.append(path)
    origin = {'source': stretch.record(session, source), 'preview': stretch.record(session, preview_path)}
    view = {'source': origin['source'], 'origin': origin, 'regions': proposal['regions'],
        'candidate': stretch.record(session, candidate_path),
        'candidate_view': {'preview': stretch.record(session, previews[1])},
        'controls': [{'kind': 'first_prefix', 'input': stretch.record(session, control_path),
                      'baseline_view': {'preview': stretch.record(session, previews[0])}}]}
    view_path = session/'reports/visibility.json'; contract.atomic_write_json(view_path, view)
    receipt = {'id': '070-v4-a1', 'status': 'success', 'stretch_visibility': stretch.record(session, view_path)}
    monkeypatch.setattr(visibility, 'stage6_reference', lambda *args: origin)
    monkeypatch.setattr(core, '_load_run', lambda *args: (session/'runs/070-v4-a1.json', receipt))
    checked = []
    monkeypatch.setattr(visibility, 'review_paths', lambda *args: checked.append(args))
    return session, proposal, regions, original_preview, control, candidate, checked, origin


def execute(roi_case, *, second=True):
    session, *_ = roi_case
    context_code = example('structure-roi-context', '<a id="adjacent-structure-pairs"></a>', session)
    exec(compile(context_code, DOCUMENT, 'exec'), {})
    if second:
        pairs_code = example('adjacent-structure-pairs', '#### 紧凑记录模板', session)
        exec(compile(pairs_code, DOCUMENT, 'exec'), {})
        return contract.load_json(session/'reports/070-v4-a1-roi-diagnostics/pairs.json')


def test_boxed_context_uses_native_coordinates_and_half_open_edges(roi_case):
    session, proposal, regions, original, *_ = roi_case
    before = {str(p): p.read_bytes() for p in session.rglob('*') if p.suffix in ('.fit', '.json', '.png')}
    execute(roi_case, second=False)
    assert all(Path(name).read_bytes() == data for name, data in before.items())
    context_path = session/'reports/070-v4-a1-roi-diagnostics/context.json'
    record = contract.load_json(context_path)
    actual = Image.open(session/record['bindings']['panel']['path'])
    expected = Image.fromarray(original.copy()); draw = ImageDraw.Draw(expected)
    protected_regions = contract.load_json(session/proposal['regions']['path'])['regions']
    for tag, colour, rows in (('solve', '#66d9c3', protected_regions), ('diag', '#f6b26b', regions)):
        for row in rows:
            a, b, c, d = row['rect']
            draw.rectangle((a, b, c-1, d-1), outline=colour)
            draw.text((a, max(0, b-12)), tag+'/'+row['id'], fill=colour)
    assert actual.size == (216, 132)
    assert np.array_equal(np.asarray(actual.crop((112, 28, 208, 124))), np.asarray(expected))
    assert actual.getpixel((112+70, 28+80)) == tuple(original[80, 70])


@pytest.mark.parametrize('roi_case', [(1, 'enhance'), (3, 'enhance'), (3, 'lift'), (3, 'grain'), (3, 'reverse')], indirect=True)
def test_pairs_use_candidate_channel_medians_and_preserve_sign_and_inputs(roi_case, request):
    session, proposal, regions, _, control, candidate, checked, _ = roi_case
    request_path = session/'reports/070-proposal.json'; before = request_path.read_bytes()
    science = {p: p.read_bytes() for p in session.rglob('*.fit')}
    result = execute(roi_case)
    assert len(checked) == 1 and result['requires_visual_review'] is True
    assert request_path.read_bytes() == before and contract.load_json(request_path)['targets'] == proposal['targets']
    assert all(path.read_bytes() == data for path, data in science.items())
    assert not {'score', 'recommended_run_id', 'verdict', 'selection'} & result.keys()
    rects = {r['id']: r['rect'] for r in regions}
    for pair in result['pairs']:
        assert pair['status'] == 'measured' and len(pair['channels']) == len(control)
        values = []
        for array in (control, candidate):
            medians = []
            for name in (pair['positive'], pair['negative']):
                x0, y0, x1, y1 = rects[name]
                medians.append(np.median(array[:, y0:y1, x0:x1].astype(float), axis=(1, 2)))
            values.append(medians[0].astype(float)-medians[1])
        for index, channel in enumerate(pair['channels']):
            assert channel['channel'] == index
            assert channel['C_control'] == pytest.approx(values[0][index])
            assert channel['C_candidate'] == pytest.approx(values[1][index])
            assert channel['delta_C'] == pytest.approx(values[1][index]-values[0][index])
    panel = Image.open(session/result['bindings']['panel']['path']); top = 8
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        for column, name in enumerate(('prefix', 'final')):
            expected = Image.open(session/f'reports/{name}.png').crop(region['rect'])
            assert np.array_equal(np.asarray(panel.crop((8+16*column, top+24, 8+16*column+x1-x0, top+24+y1-y0))), np.asarray(expected))
        top += y1-y0+24
    _, mode = request.node.callspec.params['roi_case']
    if mode in {'lift', 'grain'}:
        assert all(abs(row['delta_C']) < 1e-6 for pair in result['pairs'] for row in pair['channels'])
    if mode == 'grain':
        before = result['metrics']['control']['regions'][0]['candidate']['channels'][0]
        after = result['metrics']['candidate']['regions'][0]['candidate']['channels'][0]
        assert after['p99']-after['p01'] > before['p99']-before['p01']
    if mode == 'reverse':
        assert result['pairs'][1]['channels'][0]['C_candidate'] < 0


@pytest.mark.parametrize('roi_case', [(3, 'missing'), (3, 'nonfinite'), (3, 'channels')], indirect=True)
def test_missing_or_unavailable_measurements_are_not_filled(roi_case):
    result = execute(roi_case)
    uncertain = [pair for pair in result['pairs'] if pair['status'] == 'uncertain']
    assert uncertain and all(pair['reason'] and pair['channels'] == [] for pair in uncertain)


@pytest.mark.parametrize('issue', ['existing_context', 'preview_geometry', 'preview_hash', 'context_bounds', 'source_hash'])
def test_context_rejects_invalid_or_existing_evidence_before_publishing(roi_case, issue):
    session, _, _, _, _, _, _, origin = roi_case
    directory = session/'reports/070-v4-a1-roi-diagnostics'
    if issue == 'existing_context': directory.mkdir(); (directory/'context.png').write_bytes(b'existing')
    elif issue.startswith('preview'):
        path = session/origin['preview']['path']
        Image.new('RGB', (95, 96)).save(path)
        if issue == 'preview_geometry': origin['preview'] = stretch.record(session, path)
    elif issue == 'source_hash':
        path = session/'artifacts/linear.fit'; path.write_bytes(path.read_bytes()+b'\n')
    code = example('structure-roi-context', '<a id="adjacent-structure-pairs"></a>', session)
    if issue == 'context_bounds': code = code.replace('context_rect = (0, 0, 96, 96)', 'context_rect = (0, 0, 20, 20)')
    with pytest.raises((ValueError, contract.ContractError, FileExistsError)):
        exec(compile(code, DOCUMENT, 'exec'), {})
    if issue == 'existing_context': assert (directory/'context.png').read_bytes() == b'existing'
    else: assert not directory.exists()


@pytest.mark.parametrize('issue', ['existing_pairs', 'regions', 'source', 'candidate_geometry', 'visibility_source'])
def test_pair_diagnostics_reject_drift_without_overwriting(roi_case, issue):
    session, *_ = roi_case
    execute(roi_case, second=False)
    directory = session/'reports/070-v4-a1-roi-diagnostics'
    if issue == 'existing_pairs': (directory/'pairs.json').write_bytes(b'existing')
    elif issue in {'regions', 'source'}:
        path = session/('reports/070-diagnostic-regions.json' if issue == 'regions' else 'artifacts/linear.fit')
        path.write_bytes(path.read_bytes()+b'\n')
    elif issue == 'candidate_geometry':
        path = session/'reports/final.png'; Image.new('RGB', (95, 96)).save(path)
        view_path = session/'reports/visibility.json'; view = contract.load_json(view_path)
        view['candidate_view']['preview'] = stretch.record(session, path)
        contract.atomic_write_json(view_path, view)
        # The formal binding must still reject the changed visibility document.
    else:
        context_path = directory/'context.json'; context_record = contract.load_json(context_path)
        context_record['bindings']['source']['sha256'] = '0'*64
        contract.atomic_write_json(context_path, context_record)
    code = example('adjacent-structure-pairs', '#### 紧凑记录模板', session)
    with pytest.raises((ValueError, contract.ContractError, FileExistsError)):
        exec(compile(code, DOCUMENT, 'exec'), {})
    if issue == 'existing_pairs':
        assert (directory/'pairs.json').read_bytes() == b'existing'
    else:
        assert not (directory/'pairs.json').exists()
    assert not (directory/'pairs.png').exists()


def test_outgoing_reference_registration_is_exact():
    # prior=stretch-roi-pairs-guidance-prior.json: color_preservation, chroma_denoise, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._ROI_PAIRS_REPLAY_REFERENCES == PRIOR['documents']
    assert sessions._ROI_PAIRS_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64}, {'size': record['size']+1}):
        assert not sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_outgoing_replay_and_finalize_preserve_all_evidence(replay_case, monkeypatch):
    monkeypatch.setattr(flow_guidance, 'PRIOR', PRIOR)
    flow_guidance.test_outgoing_stretch_replay_and_finalize_preserve_evidence(replay_case, monkeypatch)

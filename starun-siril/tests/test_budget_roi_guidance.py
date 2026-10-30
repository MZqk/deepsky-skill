"""Executable budget/ROI guidance, safe stdout saving and immutable old evidence."""
import json
import copy
from pathlib import Path
import re
import subprocess

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions
import deep_sky_siril_stretch as stretch
import deep_sky_siril_stretch_v2 as protected
import deep_sky_siril_visibility as visibility
import deep_sky_star_reduction as reduction
import siril_psf_samples as bridge
from . import test_deep_sky_siril as support
from .test_deep_sky_siril_stretch import context
from .test_stretch_v22 import case as stretch_case
from .test_recomposition_proposals import case, generate, execute, accepted_draft
from .test_psf_provenance import native_model
from .test_shared_protocol_docs_v12 import _freeze_historical_evidence

ROOT = Path(__file__).parents[1]
STAGE7 = 'references/stages/stage-07-stretch.md'
STAGE9 = 'references/stages/stage-09-stars-recompose.md'
OUTGOING = {
    STAGE7: {'sha256': '2dcd0553e7c27405b654e997fffc9244bbd60d309c2aeafe50ef2d901324cfa8', 'size': 73367},
    STAGE9: {'sha256': '66624f6aa27d0cf640834b09f556ec9a38a575ea95ee3ad26aa89139cbfb086d', 'size': 31607},
    'references/cli-contract.md': {
        'sha256': 'ff4a87ecd33934cfb6d70c6e45a7c4e82fa9f8a0081ad35aa50c2582a2b0834a', 'size': 60619}}


def section(path, anchor, end):
    if path == STAGE7 and anchor == 'baseline-budget-proposal':
        path = 'references/stage7-legacy.md'
    return (ROOT/path).read_text().split(f'<a id="{anchor}"></a>', 1)[1].split(end, 1)[0]


def examples(path, anchor, end):
    return re.findall(r'^```python\n(.*?)^```', section(path, anchor, end), re.M | re.S)


def run_example(code, session):
    code = code.replace('Path("/abs/session")', f'Path({str(session)!r})')
    code = code.replace('/abs/starun-siril/scripts/', str(ROOT/'scripts')+'/')
    exec(compile(code, 'guidance-example', 'exec'), {})


@pytest.mark.parametrize('shift', [0., .2])
def test_budget_example_uses_existing_parser_and_reports_actual_conflicts(stretch_case, capsys, shift):
    session, source, payload, path, request, regions = stretch_case
    payload['execution_policy']=copy.deepcopy(sessions._PRE_STRETCH_ROUTING_BINDINGS['execution_policy'])
    contract.atomic_write_json(session/'session.json',payload)
    text = section(STAGE7, 'baseline-budget-proposal', '单链对照已达到参照可见性')
    budget = json.loads(re.search(r'例如 `(\{"region":.*?\})`', text)[1])
    budget['region'] = 'sky'
    request['schema'] = protected.PROPOSAL_REQUEST
    request['operators'][0]['factors'] = {'midtones': .05}
    constraints = request['operators'][0]['constraints']
    constraints[0] = budget
    for row in constraints:
        if row['region'] == 'sky' and row['statistic'] in {'mad', 'hf_sigma2'}:
            row['max'] = 0.
    contract.atomic_write_json(path, request)
    parsed, *_ = stretch.read_request(session, payload, path)
    assert parsed['operators'][0]['constraints'][0] == budget
    data = stretch.pixels(source)
    audit = protected.audit(data, data+shift, regions, constraints, request['targets'], noise_diagnostic=True)
    assert audit['eligible'] is (shift == 0.) and audit['noise_warnings']
    proposal = {'schema': visibility.PROPOSAL, 'anchor_region': 'faint', 'reference_median': .663, 'audit': audit}
    folder = session/'reports/070-reference'; folder.mkdir()
    report = folder/'baseline-proposal.json'; contract.atomic_write_json(report, proposal)
    before = report.read_bytes()
    code, = examples(STAGE7, 'baseline-budget-proposal', '单链对照已达到参照可见性')
    if shift:
        with pytest.raises(SystemExit, match='hard constraints failed'):
            run_example(code, session)
    else:
        run_example(code, session)
    displayed = json.loads(capsys.readouterr().out)
    assert displayed['eligible'] == audit['eligible']
    assert displayed['constraints'] == audit['constraints'] and displayed['reasons'] == audit['reasons']
    assert displayed['noise_warnings'] == audit['noise_warnings']
    assert report.read_bytes() == before


def catalog_case(tmp_path, issue='isolated'):
    session = tmp_path/'session'; folder = session/'reports/089-recomposition-input'; folder.mkdir(parents=True)
    star = native_model(xpos=20.3, ypos=31.7, layer=1)
    models = [star]
    if issue == 'empty': models = []
    elif issue == 'crowded': models.append(native_model(xpos=23.2, ypos=33.1, layer=1))
    elif issue == 'saturated': star.has_saturated = True
    elif issue == 'duplicate': models.append(native_model(xpos=20.3, ypos=31.7, layer=1))
    elif issue == 'invalid': models.append(native_model(xpos=23.2, ypos=33.1, fwhmx=0., layer=1))
    table = folder/'linear-full-stars.tsv'
    bridge.export_models(table, bridge.serialize_stars(models), {'setfindstar': ['reset'], 'layer': 1, 'maxstars': 500})
    source_sha = 'a'*64
    prepared = {'geometry_catalog': {'table': reduction.record(session, table),
        'coverage': {'detected': len(models)}, 'geometry': {'width': 100, 'height': 80}},
        'bindings': {'source': {'path': 'artifacts/subject.fit', 'sha256': source_sha, 'size': 1}}}
    contract.atomic_write_json(folder/'recomposition-prepared.json', prepared)
    contract.atomic_write_json(session/'reports/recomposition-regions.json', {
        'schema': 'starun-siril.regions.v1', 'reference': {'sha256': source_sha, 'width': 100, 'height': 80},
        'regions': [{'id': 'subject', 'role': 'faint', 'rect': [40, 40, 60, 60]},
                    {'id': 'selected-star', 'role': 'stars', 'rect': [10, 21, 31, 43]}]})
    return session, table


@pytest.mark.parametrize('issue,count,qualified', [
    ('isolated', 1, True), ('empty', 0, False), ('crowded', 2, False),
    ('saturated', 1, False), ('duplicate', 2, False), ('invalid', 1, False)])
def test_catalog_examples_keep_fixed_subpixel_samples_and_report_unqualified_regions(tmp_path, capsys, issue, count, qualified):
    session, table = catalog_case(tmp_path, issue)
    listing, checking = examples(STAGE9, 'catalog-guided-rois', '<a id="save-recomposition-analysis"></a>')
    before = support._session_bytes(session)
    run_example(listing, session)
    listed = json.loads(capsys.readouterr().out)
    if issue in {'isolated', 'invalid'}:
        assert listed['eligible_stars'][0]['xpos'] == 20.3 and listed['eligible_stars'][0]['ypos'] == 31.7
        assert listed['eligible_stars'][0]['fwhmx'] == 4.
    else:
        assert len(listed['eligible_stars']) == (2 if issue == 'crowded' else 0)
    run_example(checking, session)
    assert json.loads(capsys.readouterr().out) == [
        {'region': 'selected-star', 'catalog_count': count, 'catalog_qualified': qualified}]
    assert support._session_bytes(session) == before


@pytest.mark.parametrize('issue', ['table', 'reference'])
def test_catalog_examples_reject_binding_drift(tmp_path, issue):
    session, table = catalog_case(tmp_path)
    listing, checking = examples(STAGE9, 'catalog-guided-rois', '<a id="save-recomposition-analysis"></a>')
    if issue == 'table':
        table.write_bytes(table.read_bytes()+b'\n')
        with pytest.raises(contract.ContractError): run_example(listing, session)
    else:
        path = session/'reports/recomposition-regions.json'; document = contract.load_json(path)
        document['reference']['sha256'] = '0'*64; contract.atomic_write_json(path, document)
        with pytest.raises(AssertionError): run_example(checking, session)


@pytest.mark.parametrize('issue', ['success', 'failure', 'invalid', 'duplicate', 'existing', 'race'])
def test_analysis_save_example_publishes_only_successful_json_without_overwriting(tmp_path, monkeypatch, capsys, issue):
    session = tmp_path/'session'; (session/'reports').mkdir(parents=True)
    output = session/'reports/089-recomposition-analysis.json'
    content = b'{ "schema": "starun-siril.recomposition-analysis.v1", "requires_visual_review": true }\n'
    stdout = b'not JSON\n' if issue == 'invalid' else b'{"x":1,"x":2}\n' if issue == 'duplicate' else content
    calls = []
    def execute_cli(argv, **kwargs):
        calls.append(argv)
        assert kwargs == {'capture_output': True} and argv[3] == 'analyze'
        assert argv[4:6] == ['--session', str(session)]
        if issue == 'race': output.write_bytes(b'concurrent evidence')
        return subprocess.CompletedProcess(argv, 7 if issue == 'failure' else 0, stdout, b'analysis diagnostic\n')
    monkeypatch.setattr(subprocess, 'run', execute_cli)
    code, = examples(STAGE9, 'save-recomposition-analysis', '保存的 analysis 已包含')
    if issue == 'existing':
        output.write_bytes(b'prior evidence')
        with pytest.raises(FileExistsError): run_example(code, session)
        assert output.read_bytes() == b'prior evidence' and not calls
    elif issue == 'failure':
        with pytest.raises(SystemExit) as exc: run_example(code, session)
        assert exc.value.code == 7 and not output.exists()
    elif issue in {'invalid', 'duplicate'}:
        with pytest.raises(contract.ContractError): run_example(code, session)
        assert not output.exists()
    elif issue == 'race':
        with pytest.raises(FileExistsError): run_example(code, session)
        assert output.read_bytes() == b'concurrent evidence'
    else:
        run_example(code, session)
        assert output.read_bytes() == content
    if issue != 'existing': assert capsys.readouterr().err == 'analysis diagnostic\n'


def test_outgoing_budget_roi_guidance_registration_is_exact():
    assert sessions._BUDGET_ROI_REPLAY_REFERENCES == OUTGOING
    assert sessions._BUDGET_ROI_REPLAY_POLICY_HASH == contract.stable_hash(sessions._PRE_DEEPSNR_EXECUTION_POLICY)
    payload = {'execution_policy': sessions._PRE_DEEPSNR_EXECUTION_POLICY}
    for name, fp in OUTGOING.items():
        record = {'path': name, **fp}
        assert sessions.registered_guidance_reference(payload, record)
        assert (fp['sha256'], fp['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for change in ({'path': name+'.unknown'}, {'sha256': '0'*64}, {'size': fp['size']+1}):
            assert not sessions.registered_guidance_reference(payload, {**record, **change})
        assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


@pytest.fixture
def predecessor_runtime(monkeypatch):
    # Generate original fixtures with the frozen predecessor policy and implementation.
    monkeypatch.setattr(contract, 'EXECUTION_POLICY', dict(sessions._PRE_DEEPSNR_EXECUTION_POLICY))
    monkeypatch.setattr(sessions, 'EXECUTION_POLICY', dict(sessions._PRE_DEEPSNR_EXECUTION_POLICY))
    monkeypatch.setattr(protected, 'implementation', lambda session=None:
        protected._PRE_DEEPSNR_BINDINGS['implementation'].copy())


@pytest.mark.parametrize('protocol,case', [('stretch', 'single'), ('stars.recompose', 'single'),
    ('stars.recompose', 'hdr'), ('stars.recompose', 'hoo')], indirect=['case'])
def test_outgoing_stretch_and_recomposition_replay_and_finalize_without_rewriting(predecessor_runtime, case, monkeypatch, protocol):
    session, _, _, _, simulator = case
    if protocol == 'stars.recompose':
        assets = generate(case); run = execute(case, assets)
        review = session/f"reviews/{run['id']}.json"
        contract.atomic_write_json(review, accepted_draft(session, run))
        replay = lambda: execute(case, assets)
        primary = Path(assets['primary_output'])
    else:
        source = Path(contract.load_json(session/'session.json')['input']['path'])
        primary = session/'artifacts/072-budget-stretch.fit'; view = session/'previews/072-budget-stretch.jpg'
        script = session/'scripts/072-budget-stretch.ssf'
        script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nmtf 0 0.5 1\n'
            f'stat main\nsave "{primary}" -chksum\nsavejpg "{view.with_suffix("")}" 95\nclose\n')
        support._write_ssf_provenance(session, script, 'stretch')
        args = dict(protocol='stretch', script_value=script, source_value=source, primary_output_value=primary,
                    expected_values=[primary, view], timeout=30)
        run = core.run_script(session, **args)
        review = support._review(session, run['id'], view)
        replay = lambda: core.run_script(session, **args)
    assert run['status'] == 'success'
    _freeze_historical_evidence(session, run_id=run['id'], snapshot={
        'execution_policy': contract.EXECUTION_POLICY, 'documents': OUTGOING})
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
        {'passed': True, 'decoder': {'artifact_fingerprint': contract.fingerprint(path, include_path=False)}})
    script, final = support._delivery_script(session, primary)
    delivered = core.run_script(session, protocol='delivery.render', script_value=script, source_value=primary,
        expected_values=[final, final.with_suffix('.fit')], timeout=30)
    assert delivered['status'] == 'success'
    parent_preview = session/next(row['path'] for row in run['outputs'] if row['path'].endswith('.jpg'))
    final_review = support._review(session, delivered['id'], final, parent_preview=parent_preview)
    before = support._session_bytes(session); calls = len(simulator.calls)
    assert replay()['replayed'] and core.check_review(session, run['id'], review)['status'] == 'review_valid'
    assert support._session_bytes(session) == before and len(simulator.calls) == calls
    selection = support._selection(session, delivered['id'], final, final_review)
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    for name, data in before.items():
        if name.split('/')[0] in ('runs', 'reviews', 'reports', 'scripts'):
            assert (session/name).read_bytes() == data

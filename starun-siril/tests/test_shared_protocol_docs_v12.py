"""Canonical shared ownership, unchanged scientific examples and frozen v11 evidence."""
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

import pytest

from . import test_deep_sky_siril as support
from .test_tone_adjust import _tone_text
import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_core as core
import deep_sky_siril_session as sessions

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / 'tests/fixtures/shared-docs-v11.json').read_text())
PALETTE_BASELINE = json.loads((ROOT / 'tests/fixtures/palette-routes-prior.json').read_text())
STRETCH_DISPLAY_BASELINE = json.loads((ROOT / 'tests/fixtures/stretch-display-prior.json').read_text())
REFERENCE_BASELINE = json.loads((ROOT / 'tests/fixtures/reference-stretch-prior.json').read_text())
ASSESSMENT_BASELINE = json.loads((ROOT / 'tests/fixtures/stretch-assessment-prior.json').read_text())
FINISH_BASELINE = json.loads((ROOT / 'tests/fixtures/protected-finish-prior.json').read_text())
LINEAR_BACKGROUND_BASELINE = json.loads((ROOT / 'tests/fixtures/linear-background-guidance-v25.json').read_text())
ENHANCEMENT_BASELINE = json.loads((ROOT / 'tests/fixtures/subject-enhancement-prior.json').read_text())
DENOISE_BASELINE = json.loads((ROOT / 'tests/fixtures/linear-denoise-prior.json').read_text())
DENOISE_BASELINE['documents'] = DENOISE_BASELINE['references']
RECOMPOSITION_BASELINE = json.loads((ROOT/'tests/fixtures/recomposition-prior.json').read_text())
STAGES = [f'references/stages/stage-{name}.md' for name in (
    '03-background', '04-astrometry-color', '07-stretch', '08-starless-enhance', '10-delivery-finishing')]
SHARED = ['references/shared-protocols/tone-adjust.md', 'references/shared-protocols/color-finish.md', 'references/shared-protocols/denoise-nonlinear.md']
STAGE7_REFERENCES = [f'references/stage7-{kind}.md' for kind in ('hdr', 'hoo', 'diagnostics', 'legacy')]


def test_v12_changes_only_guidance_and_registers_exact_outgoing_knowledge():
    assert sessions._SHARED_PROTOCOL_DOCS_EXECUTION_POLICY == {**BASELINE['policy'], 'guidance': 'stage_shared_protocol_docs_v12'}
    # prior=shared-docs-v11.json: 全部 20 个域标签
    assert_contract_domains(sessions._SHARED_PROTOCOL_DOCS_EXECUTION_POLICY)
    assert sessions._EXPLICIT_RECOMPOSITION_EXECUTION_POLICY == BASELINE['policy']
    assert sessions.known_execution_policy(BASELINE['policy'])
    assert not sessions.execution_policy_is_current({'execution_policy': BASELINE['policy']})
    for path, record in BASELINE['files'].items():
        assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[path]
        current = (ROOT / path).read_text()
        assert all(f'<a id="{anchor}"></a>' in current for anchor in record['anchors'])
        headings = re.findall(r'^#{1,6} (.+)$', re.sub(r'^[ \t]*```[^\n]*\n.*?^[ \t]*```', '', current, flags=re.M | re.S), re.M)
        seen = Counter()
        anchors = set(re.findall(r'<a id="([^"]+)"', current))
        for heading in headings:
            slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
            anchors.add(slug + (f'-{seen[slug]}' if seen[slug] else ''))
            seen[slug] += 1
        assert set(record.get('heading_anchors', [])) <= anchors
    from .test_knowledge_contract import assert_v18_command_policy
    assert_v18_command_policy(BASELINE['files']['references/command-policy.json'])


def test_all_scientific_examples_are_byte_identical_after_move():
    blocks = []
    for path in STAGES + SHARED + STAGE7_REFERENCES:
        blocks += re.findall(r'^[ \t]*```[^\n]*\n.*?^[ \t]*```', (ROOT / path).read_text(), re.M | re.S)
    blocks = [re.sub(r'^# tone-adjust:.*\n', '', block, flags=re.M) for block in blocks]
    successor = json.loads((ROOT/'tests/fixtures/batch-flow-v13.json').read_text())
    # v13 refined the CLAHE example; batch v14 preserves its complete frozen scientific examples.
    # v17 adds an independently tested path/argument example without scientific operations.
    blocks = [block for block in blocks if not block.startswith('```python\nfrom pathlib import Path\nimport shlex\nimport sys\n')]
    blocks = [block for block in blocks if '/abs/skill/scripts/deep_sky_siril_color.py' not in block
              and 'deep_sky_siril_chroma.py' not in block
              and 'starun-siril.chroma-denoise-request.v1' not in block
              and 'starun-siril.color-region-goals.v1' not in block
              and 'deep_sky_siril_dualband.py' not in block
              and 'deep_sky_siril_local_contrast.py' not in block
              and 'from deep_sky_siril_local_contrast import' not in block
              and 'from deep_sky_siril_visibility import PROPOSAL' not in block
              and not block.startswith('```python\n# Stage 7 diagnostic: ')
              and 'starun-siril.local-contrast-request.v' not in block
              and 'starun-siril.stretch-goals.v1' not in block
              and 'deep_sky_siril_stretch.py init-request' not in block
              and 'deep_sky_siril_stretch.py morphology-context' not in block
              and 'deep_sky_siril_stretch.py compare-tail' not in block
              and 'deep_sky_siril_stretch.py baseline' not in block
              and '"constraint_review"' not in block]
    blocks = [block.replace(',"reference":{"path":"reports/pcc-evidence.txt","sha256":"ACTUAL_SHA256","size":123}', '') for block in blocks]
    # v22/v25 request examples change; scientific operator examples stay frozen.
    request_block = lambda block: 'starun-siril.stretch-request.v' in block or '"factors"' in block
    # The preservation policy changes only this local-color mask expression.
    expected = [b.replace('pm "$artifacts/088-mask-smoothed$ * $artifacts/088-mask-base$" -nosum',
                          'pm "min($artifacts/088-mask-smoothed$, $artifacts/088-mask-base$)" -nosum')
                if '# local-saturation:' in b else b for b in successor['scientific_blocks']]
    # Correct only the mistaken CLI D formula; preserve all historical fixture bytes.
    expected = [b.replace('```\nm = 1 / (D + 2)\n```', '```\nm = 1 / (exp(d) + 1)\n```') for b in expected]
    assert Counter(b for b in blocks if not request_block(b)) == Counter(
        b for b in expected if not request_block(b))


def test_shared_bodies_are_unique_and_stage_stubs_have_no_operator_examples():
    texts = [(ROOT / path).read_text() for path in STAGES + SHARED]
    for protocol in ('tone.adjust', 'color.finish'):
        body = f'## {protocol}\n'
        assert sum(text.count(body) for text in texts) == 1
        assert contract.PROTOCOL_STAGE_REFERENCES[protocol] == (
            f"references/shared-protocols/{protocol.replace('.', '-')}.md",)
    for path in STAGES[-2:]:
        text = (ROOT / path).read_text()
        for section in re.split(r'(?=\n<a id="(?:tone-adjust|color-finish)"></a>)', text)[1:]:
            stub = re.split(r'\n<a id="delivery-render"></a>', section)[0]
            assert '```' not in stub
    assert not (ROOT / 'references/protocols').exists()


def test_chinese_rules_keep_safety_and_current_default_semantics():
    documents = {Path(path).stem: (ROOT / path).read_text() for path in STAGES + SHARED}
    documents['stage-07-stretch'] += ''.join((ROOT/path).read_text() for path in STAGE7_REFERENCES)
    required = {
        'stage-03-background': ['25×25', '0.0001', '2,048', 'core/faint/stars', '没有可信中部天空', '其他天区同样按实际证据选择'],
        'stage-04-astrometry-color': ['bounds="0:1"', '1e-7', '1e-6', '1550 颗恒星', '1860 颗在图像外', '默认先评估', '不能凭天体名称'],
        'stage-07-stretch': ['transfer_chain=[]', '1e-6', '同一冻结蒙版', 'search_exhausted', '128', '0.01%'],
        'tone-adjust': ['1e-5', '−0.10', '0.01%', '不默认追加固定 MID', 'A′'],
        'color-finish': ['1e-6', '0.5–16', '(0, 0.20]', '不恢复物理颜色', '不能是调整前图或蒙版'],
        'stage-10-delivery-finishing': ['p99-p01', '15–100', '5%', '告警，不单独拒绝', '两个 float32 ULP'],
    }
    for name, tokens in required.items():
        assert all(token in documents[name] for token in tokens), name
    for name, text in documents.items():
        if name == 'stage-07-stretch':
            # Stage 7 retains existing English guidance; its required safety terms are checked above.
            continue
        outside_code = re.sub(r'^[ \t]*```[^\n]*\n.*?^[ \t]*```', '', text, flags=re.M | re.S)
        assert not re.search(r'^[A-Za-z][A-Za-z ]{15}', outside_code, re.M)



def test_scope_navigation_and_heading_anchors_resolve():
    paths = STAGES + SHARED + STAGE7_REFERENCES + ['SKILL.md', 'references/protocol-index.md',
        'references/stage-sequence.md', 'references/session-contract.md', 'references/session-legacy.md']
    for name in paths:
        document = ROOT / name
        for link in re.findall(r'\]\(([^)\s]+)\)', document.read_text()):
            if '://' in link:
                continue
            relative, _, fragment = link.partition('#')
            target = (document.parent / relative).resolve() if relative else document
            assert target.is_file(), (name, link)
            if fragment and target.suffix == '.md':
                text = target.read_text()
                anchors = set(re.findall(r'<a id="([^"]+)"', text))
                anchors.update(re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
                    for heading in re.findall(r'^#{1,6} (.+)$', text, re.M))
                assert unquote(fragment) in anchors, (name, link)

@pytest.mark.parametrize('baseline', [PALETTE_BASELINE, STRETCH_DISPLAY_BASELINE, REFERENCE_BASELINE, ASSESSMENT_BASELINE, FINISH_BASELINE, LINEAR_BACKGROUND_BASELINE, ENHANCEMENT_BASELINE, DENOISE_BASELINE, RECOMPOSITION_BASELINE], ids=['palette', 'stretch_display', 'reference', 'assessment', 'finish', 'linear_background', 'subject_enhancement', 'linear_denoise', 'recomposition'])
def test_committed_reference_registration_is_exact(baseline):
    # 9 个历史基线各自补齐 10-12 个域标签
    assert_contract_domains(baseline['execution_policy'])
    payload = {'execution_policy': baseline['execution_policy']}
    for name, binding in baseline['documents'].items():
        record = {'path': name, **binding}
        assert sessions.registered_guidance_reference(payload, record)
        assert (binding['sha256'], binding['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
        for changed in ({'path': name+'-unknown'}, {'sha256': '0'*64}, {'size': binding['size']+1}, {'size': True}):
            assert not sessions.registered_guidance_reference(payload, {**record, **changed})
        assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)
    name = 'references/stages/stage-07-stretch.md'
    assert len(sessions._COMMITTED_REFERENCE_FINGERPRINTS[name]) >= 2


def _freeze_historical_evidence(session, *, palette=False, run_id=None, snapshot=None):
    """Model original stage references without editing repository or user data."""
    payload = core.load_json(session / 'session.json')
    snapshot = PALETTE_BASELINE if palette else snapshot
    payload['execution_policy'] = snapshot['execution_policy'] if snapshot is not None else BASELINE['policy']
    if palette:
        payload['context']['dualband'] = 'auto'
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session / 'session.json', payload)
    manifest = core.load_json(session / 'manifest.json')
    for entry in manifest['runs']:
        if run_id is not None and entry['id'] != run_id:
            continue
        path = session / 'runs' / entry['receipt']
        receipt = core.load_json(path)
        if receipt.get('tone_proposal') and 'tone_adjustment' not in payload['execution_policy']:
            receipt.pop('tone_proposal'); receipt['processing_checks'].pop('tone_adjustment')
            review = session/'reviews'/path.name
            if review.exists():
                document = core.load_json(review); document.pop('tone_assessment', None)
                core.atomic_write_json(review, document)
        references = None
        if snapshot is not None:
            references = [{**record, **snapshot['documents'].get(record['path'], {})}
                          for record in receipt['knowledge_validation']['references']]
        elif receipt['protocol'] in {'tone.adjust', 'color.finish'}:
            paths = ['references/stages/stage-10-delivery-finishing.md']
            if receipt['protocol'] == 'color.finish':
                paths += ['references/stages/stage-08-starless-enhance.md']
            references = [{**BASELINE['files'][name], 'path': name,
                           'role': 'primary' if index == 0 else 'supporting'} for index, name in enumerate(paths)]
        if references is not None:
            for record in references:
                record.pop('anchors', None)
            receipt['knowledge_validation']['references'] = references
            provenance = session / receipt['script_provenance']['path']
            document = core.load_json(provenance)
            document['references'] = [{key: record[key] for key in ('path', 'sha256', 'role')} for record in references]
            core.atomic_write_json(provenance, document)
            receipt['script_provenance'].update(core.fingerprint(provenance, include_path=False))
        for binding in receipt.get('review_bindings', []):
            binding['sha256'] = core.sha256_file(session / binding['path'])
        core.atomic_write_json(path, receipt)
        entry['receipt_sha256'] = core.sha256_file(path)
        review = session / 'reviews' / path.name
        if review.exists():
            document = core.load_json(review)
            document['run_receipt_sha256'] = entry['receipt_sha256']
            for record in document['inspected_materials']:
                if record['path'] == path.relative_to(session).as_posix():
                    record['sha256'] = entry['receipt_sha256']
            core.atomic_write_json(review, document)
    core.atomic_write_json(session / 'manifest.json', manifest)


@pytest.mark.parametrize('history', ['v11', 'palette', 'stretch_display', 'reference', 'assessment', 'finish', 'linear_denoise', 'recomposition'])
@pytest.mark.parametrize('protocol,operation', [('tone.adjust', 'mtf 0 0.55 1'), ('color.finish', 'satu 0.05 1 6')])
def test_historical_receipts_review_replay_and_finalize_unchanged(tmp_path, monkeypatch, protocol, operation, history):
    palette = history == 'palette'
    snapshot = {'stretch_display': STRETCH_DISPLAY_BASELINE, 'reference': REFERENCE_BASELINE, 'assessment': ASSESSMENT_BASELINE, 'finish': FINISH_BASELINE, 'linear_denoise': DENOISE_BASELINE, 'recomposition': RECOMPOSITION_BASELINE}.get(history)
    current_history = palette or snapshot is not None
    session, source, _ = support._session(tmp_path, monkeypatch, color_ready=True)
    inspected, direct, diagnostic = support._input_inspect_run(session, source, monkeypatch)
    support._review(session, inspected['id'], diagnostic, parent_preview=direct)
    script = session / 'scripts/100-adjust.ssf'
    value = _tone_text(session, source, operation, '100-adjust')
    if protocol == 'tone.adjust':
        from .test_tone_adjust import _tone_proposal
        value = _tone_proposal(session, source, value, '100-adjust')
    script.write_text(value)
    support._write_ssf_provenance(session, script, protocol)
    if snapshot is not None:
        document = core.load_json(script.with_suffix('.provenance.json'))
        for name in ('references/stages/stage-07-stretch.md', 'references/target-morphology-review.md'):
            document['references'].append({'path': name, 'role': 'supporting', 'sha256': core.sha256_file(ROOT/name)})
        core.atomic_write_json(script.with_suffix('.provenance.json'), document)
    outputs = [session / 'artifacts/100-adjust.fit', session / 'previews/100-adjust-parent.jpg',
               session / 'previews/100-adjust-candidate.jpg']
    def adjusted(argv, **kwargs):
        support._write_fits(outputs[0], color_ready=True, nonzero=True)
        for path in outputs[1:]:
            support._write_jpeg(path)
        stat = "{} layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        return subprocess.CompletedProcess(argv, 0, "Reading FITS: file adjusted.fit, 3 layer(s), 12x8 pixels, 16 bits\n" +
                                           "".join(stat.format(c) for c in ("Red", "Green", "Blue")))
    monkeypatch.setattr(core.subprocess, 'run', adjusted)
    args = dict(protocol=protocol, script_value=script, source_value=source, expected_values=outputs, timeout=30)
    receipt = core.run_script(session, **args)
    assert receipt['status'] == 'success', receipt
    support._review(session, receipt['id'], outputs[2], parent_preview=outputs[1])
    delivery_script, candidate = support._delivery_script(session, outputs[0])
    def delivered(argv, **kwargs):
        support._write_delivery_outputs(outputs[0], candidate)
        return subprocess.CompletedProcess(argv, 0, '')
    monkeypatch.setattr(core.subprocess, 'run', delivered)
    monkeypatch.setattr(core, '_decode_scientific_output', lambda path, **kwargs:
        {'passed': True, 'decoder': {'artifact_fingerprint': core.fingerprint(path, include_path=False)}})
    delivery = core.run_script(session, protocol='delivery.render', script_value=delivery_script,
                              source_value=outputs[0], expected_values=[candidate, candidate.with_suffix('.fit')], timeout=30)
    assert delivery['status'] == 'success', delivery
    final_review = support._review(session, delivery['id'], candidate, parent_preview=outputs[2])
    _freeze_historical_evidence(session, palette=palette, snapshot=snapshot)
    before = support._session_bytes(session)
    assert core.run_script(session, **args)['replayed']
    if current_history:
        assert core.run_script(session, protocol='input.inspect', script_value=session/'scripts'/f"{inspected['id']}.ssf",
            source_value=source, expected_values=[direct, diagnostic], timeout=30)['replayed']
        assert core.run_script(session, protocol='delivery.render', script_value=delivery_script,
            source_value=outputs[0], expected_values=[candidate, candidate.with_suffix('.fit')], timeout=30)['replayed']
    assert core.check_review(session, receipt['id'], session / f"reviews/{receipt['id']}.json")['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    fresh = session / "scripts/999-new-adjust.ssf"
    fresh.write_text(script.read_text().replace('100-adjust', '999-new-adjust'))
    support._write_ssf_provenance(session, fresh, protocol)
    if current_history:
        document = core.load_json(fresh.with_suffix('.provenance.json'))
        name = ('references/shared-protocols/tone-adjust.md' if history == 'finish' else
                'references/stages/stage-07-stretch.md' if snapshot is not None else 'references/stages/stage-01-input-inspect.md')
        document['references'].append({'path': name, 'role': 'supporting',
                                       'sha256': (snapshot or PALETTE_BASELINE)['documents'][name]['sha256']})
        core.atomic_write_json(fresh.with_suffix('.provenance.json'), document)
    fresh_outputs = [Path(str(path).replace('100-adjust', '999-new-adjust')) for path in outputs]
    for validate_only in (True, False):
        with pytest.raises(contract.ContractError) as error:
            core.run_script(session, **{**args, 'script_value': fresh, 'expected_values': fresh_outputs}, validate_only=validate_only)
        assert error.value.code == 'legacy_session_read_only'
    selection = support._selection(session, delivery['id'], candidate, final_review)
    original_records = {path: path.read_bytes() for path in [session / 'session.json',
        *list((session / 'runs').glob('*.json')), *list((session / 'reviews').glob('*.json'))]}
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert all(path.read_bytes() == data for path, data in original_records.items())

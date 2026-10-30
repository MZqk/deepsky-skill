"""AI observations use existing review interfaces without changing scientific gates."""
from collections import Counter
import json
from pathlib import Path
import re
from urllib.parse import unquote

import pytest

from . import test_deep_sky_siril as support
from .test_input_confirmation import _inspected, _confirm
from .test_tone_adjust import _tone_script
import deep_sky_siril_contract as contract
import deep_sky_siril_core as core
import deep_sky_siril_processing as processing
import deep_sky_siril_session as sessions
from ._policy_pins import assert_contract_domains, assert_policy_retired

ROOT = Path(__file__).resolve().parents[1]
V18_BASELINE = json.loads((ROOT/'tests/fixtures/vivid-policy-v18.json').read_text())
BASELINE = json.loads((ROOT / 'tests/fixtures/ai-morphology-v12.json').read_text())


def test_v13_freezes_complete_v12_and_changes_only_guidance():
    assert sessions._AI_MORPHOLOGY_EXECUTION_POLICY == {**BASELINE['policy'], 'guidance': 'stage_ai_morphology_measurement_v13'}
    assert_contract_domains(sessions._AI_MORPHOLOGY_EXECUTION_POLICY)
    assert sessions._SHARED_PROTOCOL_DOCS_EXECUTION_POLICY == BASELINE['policy']
    assert_policy_retired(BASELINE['policy'])
    assert {key: list(value) for key, value in contract.PROTOCOL_STAGE_REFERENCES.items()} == {**BASELINE['protocol_references'], 'restoration.denoise-nonlinear': ['references/shared-protocols/denoise-nonlinear.md']}
    for path, record in BASELINE['documents'].items():
        assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[path]
        text = (ROOT / path).read_text()
        assert all(f'<a id="{anchor}"></a>' in text for anchor in record['anchors'])
        expected = record['code_blocks']
        if path == 'references/cli-contract.md':
            # v15 retires batch examples and spells out the filesystem source path.
            expected = [block.replace('--source CURRENT_PARENT', '--source /abs/current-parent.fit') for block in expected if not any(
                f'"schema": "starun-siril.{name}-decision.v1"' in block
                for name in ('color', 'restoration'))]
        if path == 'references/stages/stage-07-stretch.md':
            text += ''.join((ROOT/f'references/stage7-{kind}.md').read_text()
                            for kind in ('hdr', 'hoo', 'diagnostics', 'legacy'))
        blocks = re.findall(r'^[ \t]*```[^\n]*\n.*?^[ \t]*```', text, re.M | re.S)
        if path == 'references/cli-contract.md':
            blocks = [b for b in blocks if 'deep_sky_siril.py status --session' not in b]
        if path == 'references/stages/stage-09-stars-recompose.md':
            # Proposal examples replace the old invocation; frozen scientific operators remain exact.
            blocks = [re.sub(r'^# recomposition-proposal:.*\n', '', b, flags=re.M)
                      for b in blocks if b.startswith('```ssf\n')]
            expected = [b for b in expected if b.startswith('```ssf\n')]
        if path == 'references/stages/stage-04-astrometry-color.md':
            # v17 adds a path/argument example, exercised by its executable regression.
            blocks = [block for block in blocks if not block.startswith('```python\nfrom pathlib import Path\nimport shlex\nimport sys\n')]
            blocks = [b.replace(',"reference":{"path":"reports/pcc-evidence.txt","sha256":"ACTUAL_SHA256","size":123}', '') for b in blocks]
        if path == 'references/stages/stage-07-stretch.md':
            blocks = [b for b in blocks if 'deep_sky_siril_dualband.py' not in b
                      and 'starun-siril.stretch-goals.v1' not in b
                      and 'deep_sky_siril_stretch.py init-request' not in b
                      and 'deep_sky_siril_stretch.py baseline' not in b]
            # v22/v25 request examples change; native operator blocks stay frozen.
            blocks = [b for b in blocks if 'starun-siril.stretch-request.v' not in b and '"factors"' not in b]
            expected = [b for b in expected if 'starun-siril.stretch-request.v' not in b]
            blocks = [b for b in blocks if 'deep_sky_siril_stretch.py compare-tail' not in b
                      and 'from deep_sky_siril_visibility import PROPOSAL' not in b
                      and not b.startswith('```python\n# Stage 7 diagnostic: ')]
        if path=='references/stages/stage-07-stretch.md':
            # v29 replaces entry/binding examples; native operator blocks remain exact.
            blocks=[b for b in blocks if b.startswith('```ssf\n')]
            expected=[b for b in expected if b.startswith('```ssf\n')]
        if path == 'references/stages/stage-07-stretch.md':
            assert Counter(blocks) == Counter(expected)
        else:
            assert blocks == expected
    for path, record in BASELINE['unchanged'].items():
        if path == 'scripts/deep_sky_siril.py':
            # v15 clarified the filesystem source argument without changing CLI options.
            successor = json.loads((ROOT / 'tests/fixtures/stage3-assessment-v15.json').read_text())
            assert V18_BASELINE['runtime'][path] == successor['unchanged_runtime'][path]
        elif path == 'scripts/deep_sky_siril_core.py':
            successor = json.loads((ROOT / 'tests/fixtures/local-contrast-v13.json').read_text())
            assert successor['prior_runtime'][path] == record
        elif path == 'scripts/batch_starun_runner.py':
            successor = json.loads((ROOT/'tests/fixtures/batch-flow-v13.json').read_text())
            assert successor['runtime'][path] == record
        elif path == 'references/command-policy.json':
            from .test_knowledge_contract import assert_v18_command_policy
            assert_v18_command_policy(record)
        elif path in {'scripts/deep_sky_siril_stretch.py', 'scripts/deep_sky_star_reduction.py'}:
            prior = json.loads((ROOT/'tests/fixtures/dualband-policy-v19.json').read_text())
            assert prior['runtime'][path] == record
        elif path == 'references/review.schema.json':
            prior = json.loads((ROOT/'tests/fixtures/stretch-assessment-prior.json').read_text())
            assert prior['documents'][path] == record
            schema = json.loads((ROOT/path).read_text())
            schema['properties'].pop('recomposition_assessment')
            schema['properties'].pop('tone_assessment')
            schema['properties'].pop('color_assessment')
            schema['properties'].pop('color_protection_assessment')
            denoise = schema['properties'].pop('denoise_assessment')
            assert 'noise_benefit' in denoise['required']
            assessment = schema['properties'].pop('stretch_assessment')
            assert set(assessment['required']) == {'visibility', 'structures', 'adoption_reason'}
            schema.pop('$defs')
            schema['allOf'].pop()
            assert schema == prior['review_schema']
        elif path == 'scripts/deep_sky_siril_validation.py':
            assert V18_BASELINE['runtime'][path] == record
        else:
            assert contract.fingerprint(ROOT / path, include_path=False) == record


def test_observation_guidance_preserves_boundaries_and_handoff():
    stage = (ROOT / 'references/stages/stage-01-input-inspect.md').read_text()
    for token in ('画面主体', '亮核与微弱外缘', '恒星与星晕', '背景可分性', '限制与评估方向',
                  '可辨观察', '疑似判断', '无法判断', '2000', '不填写未经标定的置信百分比',
                  '不解析自由文本', '不判断 AI 识别是否正确', '纯知识查询与仅生成绑定脚本',
                  '形态无法判断本身不新增失败门', '保持冻结的 `target_name/target_type` 不变'):
        assert token in stage
    morphology = (ROOT / 'references/target-morphology-review.md').read_text()
    assert all(token in morphology for token in ('purpose/constraints', '显式 strength', '来源哈希',
               '实际像素域', '不表示星径缩小比例', '不自动消费 AI 判断', '不改变 Stage 顺序或增加质量门'))
    for name, tokens in {
        '06-stars-separation': ('不由名称改变冻结模型、stride', 'star layer', 'provenance'),
        '07-stretch': ('`purpose/constraints`', '当前科学测量', '不能直接沿用 Stage 1', '不根据形态标签自动选择 HDR'),
        '09-stars-recompose': ('绑定 request 的 strength', 'review.notes', '不表示星径缩小比例', '原参数范围和科学闭合容差不变'),
    }.items():
        text = (ROOT / f'references/stages/stage-{name}.md').read_text()
        assert all(token in text for token in tokens), name


def test_updated_navigation_resolves():
    for name in BASELINE['documents']:
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
                counts = Counter()
                for heading in re.findall(r'^#{1,6} (.+)$', re.sub(r'^[ \t]*```[^\n]*\n.*?^[ \t]*```', '', text, flags=re.M | re.S), re.M):
                    slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
                    anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
                    counts[slug] += 1
                assert unquote(fragment) in anchors, (name, link)


OBSERVATIONS = [
    'Visible stars dominate this synthetic field; inspect cores and halos before selecting a branch.',
    'Diffuse nonstellar structure is visible; protect faint extensions and measure current-source ROIs.',
    'The field combines a compact stellar group and diffuse structure; protect both subjects.',
    'Morphology cannot be determined; retain the declaration and record the observation limit.',
    'Candidate identity conflicts with the declared M31; preserve target_name and target_type.',
]


@pytest.mark.parametrize('observation', OBSERVATIONS)
@pytest.mark.parametrize('state,channel', [('linear', 'broadband'), ('unknown', 'broadband'),
                                         ('linear', 'unknown'), ('nonlinear', 'broadband')])
def test_notes_do_not_change_context_or_unlock_unknown(tmp_path, monkeypatch, observation, state, channel):
    session, source, _, receipt, review, *_ = _inspected(tmp_path, monkeypatch, input_state=state, channel_mode=channel)
    raw_before = (session / 'session.json').read_bytes()
    draft = core.check_review(session, receipt['id'], None)['review']
    assert draft['notes'] == '' and draft['verdict'] is None
    assert set(draft['gates'].values()) == {None}
    record = core.load_json(review)
    record['notes'] = observation
    core.atomic_write_json(review, record)
    before = support._session_bytes(session)
    assert core.check_review(session, receipt['id'], review)['status'] == 'review_valid'
    assert support._session_bytes(session) == before
    _, payload, _ = sessions.load_session(session)
    assert payload['context']['input_state'] == state and payload['context']['channel_mode'] == channel
    assert payload['context']['target_name'] == 'M31' and payload['context']['target_type'] == 'galaxy'
    assert processing.source_state(session, payload, source)['domain'] == state
    assert (session / 'session.json').read_bytes() == raw_before
    if state == 'unknown' or channel == 'unknown':
        script, outputs = _tone_script(session, source)
        before = support._session_bytes(session)
        with pytest.raises(contract.ContractError) as error:
            core.run_script(session, protocol='tone.adjust', script_value=script, source_value=source,
                            expected_values=outputs, timeout=30, validate_only=True)
        assert error.value.code == 'protocol_not_applicable'
        assert support._session_bytes(session) == before
        if state == 'unknown':
            with pytest.raises(contract.ContractError):
                _confirm(session, evidence=[observation])
            assert support._session_bytes(session) == before


def _freeze_v12(session):
    payload = core.load_json(session / 'session.json')
    payload['execution_policy'] = BASELINE['policy']
    payload.pop('session_sha256')
    payload['session_sha256'] = core.stable_hash(payload)
    core.atomic_write_json(session / 'session.json', payload)
    manifest = core.load_json(session / 'manifest.json')
    for entry in manifest['runs']:
        path = session / 'runs' / entry['receipt']
        receipt = core.load_json(path)
        references = receipt['knowledge_validation']['references']
        if receipt['protocol'] == 'input.inspect' and 'references/target-morphology-review.md' in BASELINE['documents']:
            name = 'references/target-morphology-review.md'
            references.append({'path': name, 'role': 'supporting',
                               **{key: BASELINE['documents'][name][key] for key in ('sha256', 'size')}})
        for record in references:
            previous = BASELINE['documents'].get(record['path'])
            if previous:
                record.update({key: previous[key] for key in ('sha256', 'size')})
        provenance = session / receipt['script_provenance']['path']
        document = core.load_json(provenance)
        document['references'] = [{key: record[key] for key in ('path', 'sha256', 'role')} for record in references]
        core.atomic_write_json(provenance, document)
        receipt['script_provenance'].update(core.fingerprint(provenance, include_path=False))
        for binding in receipt.get('review_bindings', []):
            binding['sha256'] = core.sha256_file(session / binding['path'])
        core.atomic_write_json(path, receipt)
        entry['receipt_sha256'] = core.sha256_file(path)
        review_path = session / 'reviews' / path.name
        if review_path.exists():
            review = core.load_json(review_path)
            review['run_receipt_sha256'] = entry['receipt_sha256']
            for material in review['inspected_materials']:
                if material['path'] == path.relative_to(session).as_posix():
                    material['sha256'] = entry['receipt_sha256']
            core.atomic_write_json(review_path, review)
    core.atomic_write_json(session / 'manifest.json', manifest)


def test_v12_old_inspect_and_morphology_references_replay_and_finalize(tmp_path, monkeypatch):
    session, source, _ = support._session(tmp_path, monkeypatch)
    receipt, candidate = support._delivery_run(session, source, monkeypatch)
    assert receipt['status'] == 'success'
    review = support._review(session, receipt['id'], candidate)
    _freeze_v12(session)
    before = support._session_bytes(session)
    inspection = core.load_json(session / 'runs/010-input.json')
    assert core.run_script(session, protocol='input.inspect', script_value=session / inspection['script']['path'],
        source_value=source, expected_values=[session / item['path'] for item in inspection['outputs']], timeout=30)['replayed']
    assert support._session_bytes(session) == before
    fresh = session / 'scripts/999-new.ssf'
    fresh.write_text((session / inspection['script']['path']).read_text())
    with pytest.raises(contract.ContractError) as error:
        core.run_script(session, protocol='input.inspect', script_value=fresh, source_value=source,
                        expected_values=[session / item['path'] for item in inspection['outputs']], timeout=30, validate_only=True)
    assert error.value.code == 'legacy_session_read_only'
    selection = support._selection(session, receipt['id'], candidate, review)
    original = {path: path.read_bytes() for path in [session / 'session.json', *list((session / 'runs').glob('*.json')),
                *list((session / 'reviews').glob('*.json'))]}
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert core.finalize_session(session, selection_value=selection, keep_intermediates=True)['status'] == 'success'
    assert all(path.read_bytes() == data for path, data in original.items())


def test_candidate_identity_does_not_fill_unknown_target_fields(tmp_path, monkeypatch):
    initialize = sessions.init_session
    def declared_unknown(*args, **kwargs):
        return initialize(*args, **{**kwargs, 'target_name': None, 'target_type': 'unknown'})
    monkeypatch.setattr(sessions, 'init_session', declared_unknown)
    session, _, _, receipt, review, *_ = _inspected(tmp_path, monkeypatch, input_state='linear')
    original = (session / 'session.json').read_bytes()
    record = core.load_json(review)
    record['notes'] = 'Diffuse shell is visible; NGC 6888 and emission_nebula are candidates, not confirmed identities.'
    core.atomic_write_json(review, record)
    assert core.check_review(session, receipt['id'], review)['status'] == 'review_valid'
    _, raw, _ = sessions.load_session(session)
    assert raw['context']['target_name'] is None and raw['context']['target_type'] == 'unknown'
    assert (session / 'session.json').read_bytes() == original

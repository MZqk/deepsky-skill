"""Stage 6 history does not override current rules or committed evidence."""
import copy
from collections import Counter
import hashlib
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions
from ._policy_pins import replay_and_finalize_without_migration
from .test_knowledge_contract import provenance_case, _validate_provenance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-06-stars-separation.md'
BASELINE = ROOT/'tests/fixtures/stage6-guidance-prior.json'
PRIOR = contract.load_json(BASELINE)


def test_current_stage6_contract_is_separate_from_history_and_preserves_science():
    text = (ROOT/DOCUMENT).read_text()
    current, history = text.split('#### 历史说明（仅回放或查询时读取）', 1)
    assert 'stage_background_xisf_v9' not in current
    assert 'session.execution_policy' in current
    assert '[`EXECUTION_POLICY`](../../scripts/deep_sky_siril_contract.py)' in current
    assert 'guidance=stage_background_xisf_v9' in history
    assert '../session-legacy.md#scientific-stage-3-sources' in history
    failure = current[current.index('执行器无论 Siril 退出码和日志终态，'):
                      current.index('接受原始线性 RGB 分星后，')].strip()
    assert hashlib.sha256(failure.encode()).hexdigest() == PRIOR['failure_contract_sha256']
    blocks = re.findall(r'^```[^\n]*\n.*?^```', text, re.M | re.S)
    assert [hashlib.sha256(block.encode()).hexdigest() for block in blocks] == PRIOR['code_blocks_sha256']
    prose = re.sub(r'^```[^\n]*\n.*?^```', '', text, flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', prose))
    counts = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', prose, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        anchors.add(slug + (f'-{counts[slug]}' if counts[slug] else ''))
        counts[slug] += 1
    assert set(PRIOR['anchors']) <= anchors


def test_stage6_guidance_registration_is_exact_without_policy_changes():
    from ._policy_pins import assert_contract_domains
    assert_contract_domains(PRIOR['execution_policy'])
    assert contract.stable_hash(PRIOR['execution_policy']) == PRIOR['execution_policy_hash']
    assert sessions._STAGE6_GUIDANCE_REPLAY_POLICY_HASH == PRIOR['execution_policy_hash']
    assert sessions._STAGE6_GUIDANCE_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert sessions.registered_guidance_reference(PRIOR, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(PRIOR, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_old_stage6_guidance_cannot_authorize_new_ssf(provenance_case):
    document = copy.deepcopy(provenance_case['payload'])
    document['references'].append({'path': DOCUMENT,
                                  'sha256': PRIOR['documents'][DOCUMENT]['sha256'], 'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, document)


def test_committed_stage6_guidance_replays_and_finalizes_without_migration(tmp_path, monkeypatch):
    references = sessions.protocol_reference_bindings

    def with_stage6_reference(protocol, **kwargs):
        return [*references(protocol, **kwargs),
                {'path': DOCUMENT, 'sha256': contract.sha256_file(ROOT/DOCUMENT), 'role': 'supporting'}]

    monkeypatch.setattr(sessions, 'protocol_reference_bindings', with_stage6_reference)
    replay_and_finalize_without_migration(tmp_path, monkeypatch, BASELINE)

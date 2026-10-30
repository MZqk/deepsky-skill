"""Current Stage 7 routing preserves examples, links and historical evidence."""
from collections import Counter
import copy
import hashlib
from pathlib import Path
import re

import pytest

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_session as sessions
from .test_knowledge_contract import provenance_case, _validate_provenance
from .test_recomposition_proposals import case as replay_case
from . import test_stretch_v4_flow_guidance as flow_guidance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-07-stretch.md'
PRIOR = contract.load_json(ROOT/'tests/fixtures/stage7-document-layout-prior.json')


def test_default_route_is_readable_before_examples_and_compatibility():
    text = (ROOT/DOCUMENT).read_text()
    ordered = ('morphology-default-chain', 'morphology-starting-chains',
               'default-stretch-sequence', 'v4-attempt-accounting',
               'agent-factor-proposals-v25', 'default-v4-flow',
               'stretch-compatibility-appendix')
    offsets = [text.index(f'<a id="{anchor}"></a>') for anchor in ordered]
    assert offsets == sorted(offsets)
    assert offsets[3] < text.index('```')
    for anchor in ('starless-subject-scope-v24', 'current-roi-protection',
                   'rgb-tail-comparison', 'masked-dual-hdr', 'dualband-hoo-stretch'):
        assert text.index(f'<a id="{anchor}"></a>') < offsets[-1]
    for anchor in ('单曲线自适应参数层', 'profile-guided-chains-v23', '请求生成与有限试跑'):
        assert text.index(f'<a id="{anchor}"></a>') > offsets[-1]
    profiles = contract.load_json(ROOT/'references/stretch-profiles.json')
    labels = {'ght': 'GHS', 'asinh': 'Asinh', 'mtf': 'MTF'}
    for name, profile in profiles.items():
        if 'preferred_chain' not in profile:
            continue
        row, = re.findall(rf'^\| {name}：([^\n]+)$', text[:offsets[2]], re.M)
        assert row.split('|')[1].strip() == ' → '.join(
            labels[step['method']] for step in profile['preferred_chain'])


def test_all_examples_and_existing_anchors_survive_the_move():
    text = (ROOT/DOCUMENT).read_text()
    assert '<a id="stretch-routing-v29"></a>' in text
    assert '--mode profile' in text and 'target_change' in text
    prose = re.sub(r'^[ \t]*```[^\n]*\n.*?^[ \t]*```', '', text, flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', text))
    anchors.update(re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
                   for heading in re.findall(r'^#{1,6} (.+)$', prose, re.M))
    assert set(PRIOR['anchors']) <= anchors


def test_outgoing_registration_is_exact_and_policy_is_unchanged():
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._STAGE7_LAYOUT_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    assert sessions._STAGE7_LAYOUT_REPLAY_REFERENCES == PRIOR['documents']
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert contract.fingerprint(ROOT/DOCUMENT, include_path=False) != PRIOR['documents'][DOCUMENT]
    assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64},
                   {'size': record['size']+1}, {'size': True}):
        assert not sessions.registered_guidance_reference(
            {'execution_policy': PRIOR['execution_policy']}, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_outgoing_reference_cannot_authorize_new_execution(provenance_case):
    payload = copy.deepcopy(provenance_case['payload'])
    payload['references'].append({'path': DOCUMENT, 'sha256': PRIOR['documents'][DOCUMENT]['sha256'],
                                  'role': 'supporting'})
    with pytest.raises(contract.ContractError, match='reference hash changed'):
        _validate_provenance(provenance_case, payload)


def test_outgoing_replay_and_finalize_preserve_evidence(replay_case, monkeypatch):
    monkeypatch.setattr(flow_guidance, 'PRIOR', PRIOR)
    flow_guidance.test_outgoing_stretch_replay_and_finalize_preserve_evidence(replay_case, monkeypatch)

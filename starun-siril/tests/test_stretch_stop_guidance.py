"""Exact outgoing guidance registration and immutable historical replay."""
from pathlib import Path

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_session as sessions
from .test_recomposition_proposals import case as replay_case
from . import test_stretch_v4_flow_guidance as flow_guidance

ROOT = Path(__file__).parents[1]
DOCUMENT = 'references/stages/stage-07-stretch.md'
PRIOR = contract.load_json(ROOT/'tests/fixtures/stretch-stop-guidance-prior.json')


def test_outgoing_registration_is_exact_and_policy_is_unchanged():
    # prior=stretch-stop-guidance-prior.json: color_preservation, chroma_denoise, recomposition_review
    assert_contract_domains(PRIOR['execution_policy'])
    assert sessions._STRETCH_STOP_REPLAY_REFERENCES == PRIOR['documents']
    assert sessions._STRETCH_STOP_REPLAY_POLICY_HASH == contract.stable_hash(PRIOR['execution_policy'])
    record = {'path': DOCUMENT, **PRIOR['documents'][DOCUMENT]}
    assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[DOCUMENT]
    assert sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, record)
    for change in ({'path': DOCUMENT+'.unknown'}, {'sha256': '0'*64}, {'size': record['size']+1}):
        assert not sessions.registered_guidance_reference({'execution_policy': PRIOR['execution_policy']}, {**record, **change})
    assert not sessions.registered_guidance_reference({'execution_policy': {}}, record)


def test_outgoing_replay_and_finalize_preserve_all_evidence(replay_case, monkeypatch):
    monkeypatch.setattr(flow_guidance, 'PRIOR', PRIOR)
    flow_guidance.test_outgoing_stretch_replay_and_finalize_preserve_evidence(replay_case, monkeypatch)

"""Retired morphology-default Stage 7 entry (v26).

Keep the explicit same-parent comparison and its single-step controls available
after the v27 default switches to one multi-step proposal.
"""
import json
from pathlib import Path

import deep_sky_siril_contract as contract
import deep_sky_siril_session as sessions

ROOT = Path(__file__).parents[1]


def profiles():
    return json.loads((ROOT/'references/stretch-profiles.json').read_text())


def test_v26_policy_is_known_and_retired():
    policy = sessions._PRE_SUBJECT_PRIORITY_EXECUTION_POLICY
    assert policy['guidance'] == 'morphology_default_stretch_flow_v26'
    assert sessions.known_execution_policy(policy)
    assert not sessions.execution_policy_is_current({'execution_policy': policy})
    assert contract.EXECUTION_POLICY['stretch_strategy'] == 'morphology_chain_comparison_v1'
    assert contract.EXECUTION_POLICY['stretch_parameters'] == 'agent_proposal_v1'


def test_every_morphology_profile_keeps_a_single_step_control():
    for name, profile in profiles().items():
        if name == 'custom':
            continue
        assert profile['baseline'] in {'mtf', 'asinh'}, name
        assert len(profile['preferred_chain']) >= 2, name


def test_stage7_preserves_explicit_comparison_and_reference_baseline():
    text = (ROOT/'references/stage7-legacy.md').read_text()
    assert 'init-request → solve → compare → generate --comparison' in text
    assert '`baseline` 是参照反求入口，不再是默认' in text
    assert 'autostretch → GHT' in text

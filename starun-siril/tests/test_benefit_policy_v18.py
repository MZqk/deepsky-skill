"""Keep prior evidence replayable while new work uses the updated adoption policy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import deep_sky_siril_contract as contract
from ._policy_pins import assert_contract_domains
import deep_sky_siril_session as sessions


BASELINE = json.loads((ROOT / 'tests/fixtures/benefit-policy-v17.json').read_text())


def test_prior_policy_and_modified_guidance_remain_replay_only():
    previous = BASELINE['execution_policy']
    assert sessions._PREPARED_STAGE4_EXECUTION_POLICY == previous
    assert sessions.known_execution_policy(previous)
    assert not sessions.execution_policy_is_current({'execution_policy': previous})
    assert sessions.execution_policy_is_current({'execution_policy': contract.EXECUTION_POLICY})
    # prior=benefit-policy-v17.json: 全部 20 个域标签
    assert_contract_domains(previous)
    assert not sessions.known_execution_policy({**previous, 'guidance': 'benefit_first_unknown'})
    for name, record in BASELINE['documents'].items():
        if contract.fingerprint(ROOT / name, include_path=False) != record:
            assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]
    lock = contract.load_json(ROOT / 'references/starnet-linear-source.json')
    assert lock['adapter_sha256'] == contract.sha256_file(ROOT / 'scripts/siril_starnet_linear.py')
    assert lock['legacy_adapter_sha256'] == contract.sha256_file(ROOT / 'scripts/siril_starnet.py')

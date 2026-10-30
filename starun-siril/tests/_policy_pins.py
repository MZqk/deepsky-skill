"""Shared assertion skeleton for version-pinning tests.

Test-only helper module: leading underscore => never collected by pytest,
and tests/ is outside release-files.txt so never shipped.

Module level imports ONLY production modules (contract/sessions) so that
importing this file can never create a cycle with test_* modules; anything
test-specific is imported lazily inside the function bodies.
"""
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

_SCRIPTS = str(ROOT / 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import deep_sky_siril_contract as contract  # noqa: E402
import deep_sky_siril_session as sessions  # noqa: E402

# Literal pin of every current execution-policy domain label.
# MUST stay a literal table: deriving it from contract.EXECUTION_POLICY would
# turn every pinning assertion below into a tautology. Each historical policy
# either lacks these keys (the table supplies them) or already carries the
# current value (the original per-version assertions pinned that implicitly
# through dict equality; this table pins it explicitly).
CURRENT_DOMAIN_LABELS = {
    'artifact_review': 'structured_focus_v1',
    'stretch_execution': 'stepwise_default_v2',
    'stage7_references': 'split_variants_v1',
    'stretch_optimization': 'highlight_budget_mtf_v2',
    'evidence_admission': 'source_bound_v1',
    'background': 'heldout_review_gate_v5',
    'subject_mask': 'reviewed_polygons_v1',
    'color_region_goals': 'explicit_targets_v1',
    'recomposition_review': 'stellar_color_assessment_v2',
    'guidance': 'evidence_bound_stretch_flow_v29',
    'stretch_routing': 'morphology_multistage_v1',
    'stretch_hdr': 'morphology_selected_v1',
    'stretch_trial_artifacts': 'retained_artifacts_v1',
    'stretch_constraint_preflight': 'curve_effect_review_v3',
    'stretch_morphology': 'roi_declared_v1',
    'stretch_tail_comparison': 'verified_negative_control_v1',
    'recovery_summary': 'explicit_stage_decisions_v1',
    'stretch_parameters': 'agent_proposal_v1',
    'starless_stretch': 'subject_only_full_diagnostic_v1',
    'stretch': 'roi_protected_selection_v2',
    'local_contrast': 'wavelet_clahe_competition_v2',
    'local_contrast_mask': 'minimum_source_support_v1',
    'local_contrast_hdr': 'wavelet_only_v1',
    'color_curve': 'masked_saturation_ghs_v1',
    'color_curve_variants': 'inverse_saturation_v1',
    'dualband': 'hoo_proxy_rgb_stars_v1',
    'stretch_strategy': 'morphology_chain_comparison_v1',
    'stretch_noise': 'diagnostic_only_v1',
    'subject_enhancement': 'benefit_first_v1',
    'linear_denoise': 'agent_proposal_v1',
    'linear_denoise_backend': 'deepsnr_primary_v1',
    'recomposition_parameters': 'agent_proposal_v1',
    'recomposition_geometry': 'fixed_catalog_profiles_v1',
    'recomposition_contour': 'local_peak_mtf_v1',
    'tone_adjustment': 'remaining_goal_v1',
    'color_evaluation': 'unmasked_first_v1',
    'color_preservation': 'vivid_preservation_v1',
    'chroma_denoise': 'chroma_bilateral_v1',
}


def assert_policy_retired(policy) -> None:
    """The policy is a known historical policy and is not the current one."""
    assert sessions.known_execution_policy(policy)
    assert not sessions.execution_policy_is_current({'execution_policy': policy})


def outgoing_stretch_producer(monkeypatch):
    """Construct retired single-step/comparison evidence under its exact outgoing policy."""
    import copy
    import deep_sky_siril_stretch_v2 as protected
    prior = sessions._PRE_MULTISTAGE_BINDINGS
    for key, value in prior['execution_policy'].items():
        monkeypatch.setitem(contract.EXECUTION_POLICY, key, value)
    monkeypatch.setattr(protected, 'implementation', lambda *args: copy.deepcopy(prior['implementation']))


def assert_contract_domains(policy, **overrides) -> None:
    """contract.EXECUTION_POLICY == historical policy + current domain labels."""
    expected = {**CURRENT_DOMAIN_LABELS, **overrides}
    assert contract.EXECUTION_POLICY == {**policy, **expected}


def assert_document_fingerprint_replayable(name, record) -> None:
    """A document whose bytes drifted must still be accepted via the legacy table."""
    if contract.fingerprint(ROOT / name, include_path=False) != record:
        assert (record['sha256'], record['size']) in sessions._LEGACY_REFERENCE_FINGERPRINTS[name]


def assert_document_fingerprints_replayable(documents) -> None:
    for name, record in documents.items():
        assert_document_fingerprint_replayable(name, record)


def assert_command_policy_hashed(baseline, *, key='command_policy') -> None:
    """Historical command policy is registered by (sha256, size) with exact bytes."""
    from query_siril_manual import _HISTORICAL_COMMAND_POLICIES
    record = baseline['documents']['references/command-policy.json']
    assert _HISTORICAL_COMMAND_POLICIES[record['sha256'], record['size']] == baseline[key].encode()


def assert_command_policy_tone_adjust_trimmed(before) -> None:
    """Current command policy minus the four tone.adjust commands equals the historical one."""
    current = json.loads((ROOT / 'references/command-policy.json').read_text())
    current['protocol_commands']['stars.recompose'].remove('rgbcomp')
    current['protocol_commands']['stars.recompose'].remove('update_key')
    current['protocol_commands']['stars.recompose'].remove('modasinh')
    current['protocol_commands']['color.finish'].remove('fill')
    current['protocol_commands']['color.finish'].remove('invght')
    for command in ('epf','split','pm','rgbcomp','icc_assign'):
        current['protocol_commands']['restoration.denoise-nonlinear'].remove(command)
    for command in ('split', 'pm', 'gauss', 'rgbcomp'):
        current['protocol_commands']['tone.adjust'].remove(command)
    current['protocol_commands']['restoration.denoise'].remove('pyscript')
    assert current['pyscript_protocols'].pop('restoration.denoise') == 'siril_deepsnr.py'
    assert current == json.loads(before['command_policy'])


def assert_pinned_history(baseline, *, session_constant, policy_key='execution_policy',
                          command_mode=None, command_policy_key='command_policy',
                          domains=None, check_contract=True, fingerprint_loop=True) -> None:
    """Shared skeleton for the static version-pinning tests.

    session_constant: the sessions._XXX_EXECUTION_POLICY constant pinned to this version.
    command_mode: None | 'hash' (registered byte lookup) | 'trim' (current minus four commands).
    """
    policy = baseline[policy_key]
    assert session_constant == policy
    assert_policy_retired(policy)
    if check_contract:
        assert_contract_domains(policy, **(domains or {}))
    if fingerprint_loop:
        assert_document_fingerprints_replayable(baseline['documents'])
    if command_mode == 'hash':
        assert_command_policy_hashed(baseline, key=command_policy_key)
    elif command_mode == 'trim':
        assert_command_policy_tone_adjust_trimmed(baseline)


def replay_and_finalize_without_migration(tmp_path, monkeypatch, baseline_path) -> None:
    """Historical document evidence replays and finalizes without any migration.

    Shared body of the v22/v23 document-evidence regression; the two public
    tests stay separate so each fixture keeps its own pinning test id.
    """
    from . import test_deep_sky_siril as support
    from . import test_ai_morphology_v13 as history
    before = json.loads(Path(baseline_path).read_text())
    session, source, _ = support._session(tmp_path, monkeypatch)
    receipt, jpeg = support._delivery_run(session, source, monkeypatch)
    review = support._review(session, receipt['id'], jpeg)
    # The existing freezer accepts an arbitrary exact policy/document snapshot.
    with monkeypatch.context() as scoped:
        scoped.setattr(history, 'BASELINE', {'policy': before['execution_policy'], 'documents': before['documents']})
        history._freeze_v12(session)
    frozen = support._session_bytes(session)
    inspection = support.core.load_json(session/'runs/010-input.json')
    assert support.core.run_script(session, protocol='input.inspect', script_value=session/inspection['script']['path'],
        source_value=source, expected_values=[session/r['path'] for r in inspection['outputs']], timeout=30)['replayed']
    assert support._session_bytes(session) == frozen
    selection = support._selection(session, receipt['id'], jpeg, review)
    payload = (session/'session.json').read_bytes()
    result = support.core.finalize_session(session, selection_value=selection, keep_intermediates=True)
    assert result['status'] == 'success'
    assert support.core.finalize_session(session, selection_value=selection, keep_intermediates=True) == result
    assert (session/'session.json').read_bytes() == payload

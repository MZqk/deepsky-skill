#!/usr/bin/env python3
"""Transactional session state and receipt lineage for standalone contract 1."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, Sequence

import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_metrics as metrics
import deep_sky_siril_tooling as tooling
from filter_metadata import _dwarf_models, _seestar_models, filter_from_header
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    PAIRED_DELIVERY_POLICIES,
    DISPLAY_IMAGE_SUFFIXES,
    EXECUTION_POLICY,
    HASH_PATTERN,
    INPUT_EXTENSIONS,
    PROTOCOL_STAGE_REFERENCES,
    protocol_reference_bindings,
    LEGACY_NETWORK_POLICIES,
    NETWORK_POLICY,
    SCHEMA_PREFIX,
    SESSION_DIRECTORIES,
    SKILL_ROOT,
    ContractError,
    _absolute,
    _fsync_directory,
    atomic_write_json,
    classify_siril_network,
    fingerprint,
    fingerprint_matches,
    load_json,
    safe_session_root,
    session_path,
    sha256_file,
    stable_hash,
    utc_now,
)
from siril_manual_bundle import (
    BundleError,
    bundle_verification_document,
    read_skill_file,
    strict_json_bytes,
    verify_bundle,
)


_POLICY_PATH = "references/command-policy.json"
_BUNDLE_EVIDENCE_PATH = "reports/manual-evidence/bundle-verification.json"
INPUT_CONFIRMATION_PATH = "reports/input-confirmation.json"
_INPUT_CONTEXT_VALUES = {
    "input_state": {"linear", "nonlinear"},
    "channel_mode": {"mono", "broadband", "narrowband", "dualband-osc"},
}
# Exact outgoing references are usable only by committed input.inspect receipts.
_PRE_CONFIRMATION_REFERENCES = {
    "SKILL.md": ("9d741831154a4a8a56d1e71692133715b60cf617cd14159492073b5366ef055d", 10365),
    "references/cli-contract.md": ("00d2e41413d5ef614e1ed529ee229cf3463243112220ab3462d03eff48505be4", 35010),
    "references/session-contract.md": ("ca76f510b57b50ba572feeee27d8bf5114862d2cb19cd97dedd3eb7ce7a519eb", 17748),
    "references/stages/stage-01-input-inspect.md": ("26a39e9af001eefdea37add6ad14150c36b5701a9c46ae5e20dbfd9cba87eac0", 5917),
    "references/delivery.md": ("3a1db092168aee9fce9ee883ead84fb70931249669ae7d5046ab7d0090ab3a9d", 4921),
}
_V9_EXECUTION_POLICY = {
    'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3',
    'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4',
    'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2',
    'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1',
    'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1',
    'guidance': 'stage_background_xisf_v9', 'local_saturation': 'luminance_peak_mask_v1',
    'delivery': 'paired_fits_jpeg_v2',
}
_V10_EXECUTION_POLICY = {
    'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3',
    'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4',
    'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2',
    'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1',
    'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1',
    'guidance': 'stage4_xisf_inputs_v10', 'local_saturation': 'luminance_peak_mask_v1',
    'delivery': 'paired_fits_jpeg_v2',
}
_FIXED_REVIEW_EXECUTION_POLICY = {
    'log_diagnostics': 'fail_closed_v3',
    'network': 'online_default_gaia_catalog_explicit_v3',
    'starnet': 'versioned_linear_bridge_v2',
    'background': 'heldout_material_gate_v4',
    'star_diagnostics': 'subject_roi_residual_v2',
    'psf_provenance': 'native_star_samples_v2',
    'reporting': 'isolated_metrics_v1',
    'neutral': 'heldout_benefit_safety_v1',
    'stretch': 'masked_dual_v1',
    'visual_review': 'subject_visibility_v1',
    'guidance': 'stage4_xisf_inputs_v10',
    'local_saturation': 'luminance_peak_mask_v1',
    'delivery': 'paired_fits_jpeg_v2',
    'recomposition_review': 'fixed_materials_v1',
}
_DWARF_FILTER_EXECUTION_POLICY = {
    'log_diagnostics': 'fail_closed_v3',
    'network': 'online_default_gaia_catalog_explicit_v3',
    'starnet': 'versioned_linear_bridge_v2',
    'background': 'heldout_material_gate_v4',
    'star_diagnostics': 'subject_roi_residual_v2',
    'psf_provenance': 'native_star_samples_v2',
    'reporting': 'isolated_metrics_v1',
    'neutral': 'heldout_benefit_safety_v1',
    'stretch': 'masked_dual_v1',
    'visual_review': 'subject_visibility_v1',
    'guidance': 'stage4_xisf_inputs_v10',
    'local_saturation': 'luminance_peak_mask_v1',
    'delivery': 'paired_fits_jpeg_v2',
    'recomposition_review': 'fixed_materials_v1',
    'filter_metadata': 'dwarf_explicit_filters_v1',
}
_PROTECTED_BACKGROUND_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'heldout_benefit_safety_v1',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'stage4_xisf_inputs_v10',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1'}

_NATIVE_NEUTRAL_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'stage4_xisf_inputs_v10',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1'}

_RETIRED_PROTOCOL_REFERENCES = frozenset(
    f"references/protocols/{protocol.replace('.', '-')}.md"
    for protocol in PROTOCOL_STAGE_REFERENCES
)
_RESTORATION_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'stage4_xisf_inputs_v10',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1'}


_LEGACY_REFERENCE_FINGERPRINTS = {
    "references/protocols/color-calibrate.md": {
        ("1d241f549828c02a55e45251c0a3374eab64aa982914a71b503b084df82c1a0b", 1707)
    },
}

_PRE_ARTIFACT_FOCUS_BINDINGS = load_json(SKILL_ROOT / 'references/artifact-focus-history.json')
for _name, _binding in _PRE_ARTIFACT_FOCUS_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STEPWISE_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-stepwise-history.json')
for _name, _binding in _PRE_STEPWISE_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_MULTISTAGE_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-multistage-history.json')
for _name, _binding in _PRE_MULTISTAGE_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_HDR_ROUTING_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-hdr-history.json')
for _name, _binding in _PRE_HDR_ROUTING_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STAGE7_DOCUMENT_BINDINGS = load_json(SKILL_ROOT / 'references/stage7-documents-history.json')
for _name, _binding in _PRE_STAGE7_DOCUMENT_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_CONTOUR_BINDINGS = load_json(SKILL_ROOT / 'references/star-contour-history.json')
for _name, _binding in _PRE_CONTOUR_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_BACKGROUND_REVIEW_BINDINGS = load_json(SKILL_ROOT / 'references/background-review-history.json')
for _name, _binding in _PRE_BACKGROUND_REVIEW_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STAGE7_REVISION_BINDINGS = load_json(SKILL_ROOT / 'references/stage7-revision-history.json')
for _name, _binding in _PRE_STAGE7_REVISION_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STAGE7_OPTIMIZATION_BINDINGS = load_json(SKILL_ROOT / 'references/stage7-adaptive-history.json')
for _name, _binding in _PRE_STAGE7_OPTIMIZATION_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_EVIDENCE_ADMISSION_BINDINGS = load_json(SKILL_ROOT / 'references/evidence-admission-history.json')
for _name, _binding in _PRE_EVIDENCE_ADMISSION_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_CURVE_EFFECT_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-curve-effects-history.json')
for _name, _binding in _PRE_CURVE_EFFECT_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_CURVE_AMPLIFICATION_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-curve-amplification-history.json')
for _name, _binding in _PRE_CURVE_AMPLIFICATION_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_RESUME_BINDINGS = load_json(SKILL_ROOT / 'references/resume-summary-history.json')
for _name, _binding in _PRE_RESUME_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_TAIL_COMPARISON_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-tail-comparison-history.json')
for _name, _binding in _PRE_TAIL_COMPARISON_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STRETCH_MORPHOLOGY_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-morphology-history.json')
for _name, _binding in _PRE_STRETCH_MORPHOLOGY_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STRETCH_PREFLIGHT_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-constraint-preflight-history.json')
for _name, _binding in _PRE_STRETCH_PREFLIGHT_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STRETCH_TRIAL_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-trial-artifacts-history.json')
for _name, _binding in _PRE_STRETCH_TRIAL_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STRETCH_ROUTING_BINDINGS = load_json(SKILL_ROOT / 'references/stretch-routing-history.json')
for _name, _binding in _PRE_STRETCH_ROUTING_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STAR_STRETCH_BINDINGS = load_json(SKILL_ROOT / 'references/star-stretch-history.json')
for _name, _binding in _PRE_STAR_STRETCH_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_COLOR_CURVE_VARIANTS_BINDINGS = load_json(SKILL_ROOT / 'references/color-curve-variants-history.json')
for _name, _binding in _PRE_COLOR_CURVE_VARIANTS_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_LOCAL_CONTRAST_MASK_BINDINGS = load_json(SKILL_ROOT / 'references/local-contrast-mask-history.json')
for _name, _binding in _PRE_LOCAL_CONTRAST_MASK_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_SUBJECT_MASK_BINDINGS = load_json(SKILL_ROOT / 'references/subject-mask-history.json')
for _name, _binding in _PRE_SUBJECT_MASK_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_REGION_COLOR_BINDINGS = load_json(SKILL_ROOT / 'references/color-region-goals-history.json')
for _name, _binding in _PRE_REGION_COLOR_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_STAR_COLOR_BINDINGS = load_json(SKILL_ROOT / 'references/star-color-history.json')
for _name, _binding in _PRE_STAR_COLOR_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_CHROMA_BINDINGS = load_json(SKILL_ROOT / 'references/chroma-denoise-history.json')
for _name, _binding in _PRE_CHROMA_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_VIVID_BINDINGS = load_json(SKILL_ROOT / 'references/vivid-preservation-history.json')
for _name, _binding in _PRE_VIVID_BINDINGS['documents'].items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

# Exact pre-v27 sources; immutable historical replay/finalize only.
_PRE_SUBJECT_PRIORITY_REFERENCES = {'SKILL.md': {'sha256': '15ea8d989d9e6c23360dd35a0b5070896e52b9dbc2654cb216668b89fd913952', 'size': 26496},
 'references/stages/stage-07-stretch.md': {'sha256': '356a6f1ec481f1dfb13d0be1c6a8da62500588860da74429f54b9857330e31e8',
                                           'size': 77082},
 'references/stage-sequence.md': {'sha256': '596501dc12125c471c35a74c8bf053a00217f5826ec5175820db304961e10be7',
                                  'size': 15471},
 'references/protocol-index.md': {'sha256': '3a8ae78a842af50a0ee1292ffddb200ccfdfd524f1528430e33ca86aaabdc018',
                                  'size': 3890},
 'references/cli-contract.md': {'sha256': '9d735fce83d7dc8ff2fc2b2727e470cb6bafcc18b15045b9ab52b655482eb045',
                                'size': 61861},
 'references/session-contract.md': {'sha256': '8148ad3e93d4764d3b93e3c773c8afe719a31ecf871a86cfe749e4941deaf5db',
                                    'size': 45582},
 'references/target-morphology-review.md': {'sha256': '9816eb278b6f7ea2c8ab1ecadf3e7fa1fab65123ee962cf92264c5c96513af2d',
                                            'size': 9106},
 'references/command-policy.json': {'sha256': '95b55398f313982a0e5b21f31aee5c6a2085e16aac9ea575a9de0f6a2cd8a0f3',
                                    'size': 4315},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'scripts/deep_sky_siril_contract.py': {'sha256': 'a5441a04115544183f4e98d702ad32271aaf7429f9f07b49293a95588f95a8a7',
                                        'size': 17841},
 'scripts/deep_sky_siril_session.py': {'sha256': '05d0c3b8358c24959f8a8d2640b2e83d5f2b98ce94c8c37b096d2887672943bf',
                                       'size': 301872},
 'scripts/deep_sky_siril_stretch_v2.py': {'sha256': 'a7380cbc55f97fb55757aee30221a5f04003ccd9c3c56df2e60db3a425eeb18b',
                                          'size': 101603},
 'scripts/deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                       'size': 62787},
 'scripts/deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                'size': 21428},
 'scripts/deep_sky_siril_local_contrast.py': {'sha256': '9b0540240ef697ca6d62cb8a73f74002809465187e83c14dda673792e625c8fa',
                                              'size': 53758},
 'scripts/deep_sky_siril_processing.py': {'sha256': 'a50489a7d8e683e972d4e363d1faca05b569500b4718b9b91d51fb2ef354f400',
                                          'size': 143475},
 'scripts/deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                   'size': 18934},
 'scripts/deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                        'size': 42748},
 'scripts/deep_sky_siril_core.py': {'sha256': '0c298e1dc5de01cde5b3dc433bbd8acaca0b386ac679ac8a27dd00ad2cfc83ac',
                                    'size': 143651},
 'scripts/deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                          'size': 31804},
 'scripts/deep_sky_siril_denoise.py': {'sha256': 'e444c9a63d8918e8c670dc2f8cda8321bb2d1d6a330d0314f0a2b355e4b009ef',
                                       'size': 34179},
 'scripts/deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                    'size': 13176},
 'scripts/deep_sky_siril_color.py': {'sha256': '5639da7745af5e73d4bdbf46e8f3d018f6cdf5667ff4c3965ca0e9aff890770f',
                                     'size': 54506},
 'scripts/deep_sky_siril_psf.py': {'sha256': 'b463f6b0575cfe2cbe5a08964e82a212ad1c953f8a858db835ca3af858eb2350',
                                   'size': 44340},
 'scripts/deep_sky_star_reduction.py': {'sha256': '502a1dae3df000c880d4cf523d4267fcbad8e027a87b1163bc61eb3cfb2946a4',
                                        'size': 64618},
 'scripts/siril_psf_samples.py': {'sha256': '694e4d34869331ca776827c2442da24c4a0d0fd3e9ab8e75cefae9083a0cd721',
                                  'size': 15655},
 'scripts/siril_deepsnr.py': {'sha256': '7c69036810fca9bc6b36f9f0473d93c228442fb00732c60ea91b19b1fd84373a',
                              'size': 13259}}
for _name, _binding in _PRE_SUBJECT_PRIORITY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

# Exact pre-DeepSNR sources; immutable historical replay/finalize only.
_PRE_DEEPSNR_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'SKILL.md': {'sha256': 'd87f86f36418adfefb7d1cb3aae7b8ee9d544b0f85b18b92868b95cc0a34686e',
              'size': 26070},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': 'cb4e23efb39b261004af29504e417df5c26ef40d91f2ca61e987867f089870f0',
                                'size': 60989},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/delivery.md': {'sha256': '795b8035b6507b0df4e4c0abf3984ce9b96e10f11b0bb94626ed6bd8f8b3f877',
                            'size': 12462},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/protocol-index.md': {'sha256': '3a8ae78a842af50a0ee1292ffddb200ccfdfd524f1528430e33ca86aaabdc018',
                                  'size': 3890},
 'references/stage-sequence.md': {'sha256': '3dfb300c418611c85908ba2aee0c5c1a7835ef8a945e84a25ef66d1ecf3f9579',
                                  'size': 15461},
 'references/shared-protocols/tone-adjust.md': {'sha256': '93ad18e5e02819ea55f95d612eafc6837bede0baa4b72ed19186892a26ee253e',
                                                'size': 12844},
 'references/shared-protocols/color-finish.md': {'sha256': '74aed0ce73c7fc99ec48465674d6a3126c35c3bd1c175ac5f46108babe0e8ab4',
                                                 'size': 18736},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-05-linear-restoration.md': {'sha256': 'c4d716da58371292c19f532a08f3e1eb68ff9003e470d3404aa92065b13cd5b7',
                                                      'size': 23630},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-07-stretch.md': {'sha256': '356a6f1ec481f1dfb13d0be1c6a8da62500588860da74429f54b9857330e31e8',
                                           'size': 77082},
 'references/stages/stage-08-starless-enhance.md': {'sha256': 'ca85cd477fe07cf9a106a52854d7374cd63d88537fa7282701fb108e0c614db1',
                                                    'size': 38712},
 'references/stages/stage-09-stars-recompose.md': {'sha256': '1c4417157fd374379a84a3424ef53627733aded79fc99d4c2e0adb75df191a6d',
                                                   'size': 37661},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '2b0e4a60e9a2592d0ac7563526dcf04ec91c74b69113f4380df11ab627a701df',
                                                      'size': 9777},
 'references/quality.md': {'sha256': 'c94e4313e29bc3d0ae029230806729da6f644bf0c9d483eb5964f9cb6360f527',
                           'size': 35733},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/target-morphology-review.md': {'sha256': '9816eb278b6f7ea2c8ab1ecadf3e7fa1fab65123ee962cf92264c5c96513af2d',
                                            'size': 9106},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/review.schema.json': {'sha256': '14322d66c83748b5fb916a511bd352e8b78a519c8a57dec9b55c7a40a872884b',
                                   'size': 15581},
 'references/session-contract.md': {'sha256': '1f43275ca160ee918ebc17724ce3a333ca8cacd07e5abc7ef9be1020edf1a413',
                                    'size': 44457},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'scripts/deep_sky_siril.py': {'sha256': 'a976c9f81a693d78c640c0fb3c02b4fc1970c713c1fe4a691040951ab62dc876',
                               'size': 9875},
 'scripts/deep_sky_siril_artifacts.py': {'sha256': '3c22f06fe68a71ffe6dc5b41aecd8f6c4406601b6d4e9c88fd6b6556b073e30a',
                                         'size': 56744},
 'scripts/deep_sky_siril_contract.py': {'sha256': '5d49533cf678ccc73b1b1fac3048e8062c9a18cdbf8940da74bade8e2338c253',
                                        'size': 17789},
 'scripts/deep_sky_siril_background.py': {'sha256': '78a9b446b12004e074cc06b85e9992b7752ecb0fcab6bff459e8fc18b560cd2d',
                                          'size': 15032},
 'scripts/deep_sky_siril_core.py': {'sha256': 'cd37e16d927e373985c10fbae4df4c7c9cd6cf67ec8098f8e89b05feb5149af8',
                                    'size': 143092},
 'scripts/deep_sky_siril_session.py': {'sha256': 'e75a66f33d4337bb4adab3f4570d428012262f201ebe6e73660c985aa3c74186',
                                       'size': 286270},
 'scripts/filter_metadata.py': {'sha256': '98cd89ba8451d6de292d1629133d78687d11333f176a36d0b8da01c2330d3b6a',
                                'size': 6343},
 'scripts/deep_sky_siril_tooling.py': {'sha256': '32d901fbcd90aad08fe7970291bd54ba09b69974d9f55dbb397994d68e23f59c',
                                       'size': 20996},
 'scripts/deep_sky_siril_validation.py': {'sha256': '456b2bc9e16ba128a0b1d8e8bfa8db3257ba6107f8d280b730618faf3c490e26',
                                          'size': 42541},
 'scripts/siril_background_samples.py': {'sha256': 'f01ea5649f165ce4cf1d4ca2e3ca2ed501a7e7e293945ba5a68ee025e6c35b36',
                                         'size': 12197},
 'scripts/siril_background_samples_scientific.py': {'sha256': 'a3ec4d493b15f72f42a77b0ce3f00888656ecb4d8855fa9072304ab642e93601',
                                                    'size': 4895},
 'scripts/siril_psf_samples.py': {'sha256': '694e4d34869331ca776827c2442da24c4a0d0fd3e9ab8e75cefae9083a0cd721',
                                  'size': 15655},
 'scripts/deep_sky_siril_psf.py': {'sha256': 'b463f6b0575cfe2cbe5a08964e82a212ad1c953f8a858db835ca3af858eb2350',
                                   'size': 44340},
 'scripts/siril_starnet_linear.py': {'sha256': '6556638d21d95855f985d2bfdfca249278b26a93908119555d8d37cc2909d28b',
                                     'size': 13289},
 'scripts/siril_starnet.py': {'sha256': '083df0e3b4411642f0980c7ba632f3bc858f0b9dd0e948904aa2cd2076f7f583',
                              'size': 15280},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'scripts/siril_auto_samples.py': {'sha256': '599eade9a932bd080f71736ec67af9292af36db40e67556787d6f0da2b572533',
                                   'size': 36167},
 'scripts/deep_sky_siril_metrics.py': {'sha256': '027732e7ec7a8bff81dab96c0a9d2e5d577ed16999645c24b630027422be427a',
                                       'size': 30447},
 'scripts/deep_sky_siril_processing.py': {'sha256': 'cd838b8c5b21eecd4d02921f58f6fbc9049300d30e439d244433e39b46c885aa',
                                          'size': 143157},
 'scripts/deep_sky_siril_color.py': {'sha256': '5639da7745af5e73d4bdbf46e8f3d018f6cdf5667ff4c3965ca0e9aff890770f',
                                     'size': 54506},
 'scripts/deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                        'size': 42748},
 'scripts/deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                       'size': 27422},
 'scripts/deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                    'size': 13176},
 'scripts/deep_sky_siril_local_contrast.py': {'sha256': '9b0540240ef697ca6d62cb8a73f74002809465187e83c14dda673792e625c8fa',
                                              'size': 53758},
 'scripts/deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                   'size': 18934},
 'scripts/deep_sky_star_reduction.py': {'sha256': '502a1dae3df000c880d4cf523d4267fcbad8e027a87b1163bc61eb3cfb2946a4',
                                        'size': 64618},
 'scripts/deep_sky_siril_geometry.py': {'sha256': 'ccbf6130e5190c4b5912dac586574f13087bff2eb6f4d2b2ad625adb477d5bcb',
                                        'size': 12174},
 'scripts/deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                       'size': 62787},
 'scripts/deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                          'size': 31804},
 'scripts/deep_sky_siril_stretch_v2.py': {'sha256': 'e5a3bd45a2d960b30cd7092e143fdaec8171d665d26afa5c15f41ae9122953c9',
                                          'size': 96276},
 'scripts/deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                'size': 21428},
 'scripts/deep_sky_siril_neutral.py': {'sha256': '0b71b4d981a1fd3b67b610c0319ee0541e4b6deb61d06827dcdaee6fe994c003',
                                       'size': 12451},
 'scripts/sync_fits_header.py': {'sha256': 'c0beb76a59ced1dcb0ed0e8d8cdc53f73ce72afac3a48a35b0c6bf0a495168df',
                                 'size': 44865},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'scripts/query_siril_manual.py': {'sha256': 'dbbfb5f16305c8fa28d9a0d6430291913179c980bf3935f1686607def1c4419d',
                                   'size': 90767},
 'scripts/siril_manual_bundle.py': {'sha256': 'cf533ce6eecec44fef89c9a10da8b43f0772dd9598b85638f4c9180653117afa',
                                    'size': 46445},
 'CHANGELOG.md': {'sha256': 'cb02b730598392bd71fd6fdf102567a282454e8b000c2b257451f64da6e3bb7e',
                  'size': 38295},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'scripts/package_release.py': {'sha256': 'f1e7ba55bb8258bdeb5f532fd10c9e83cb77315677cce0b946bfa597ee792c59',
                                'size': 44155},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'release-files.txt': {'sha256': 'e81ce37da49a5ac35d056c26231e5e15e4ea8e2cbc970fbf386927dad24f0770',
                       'size': 2447}}
for _name, _binding in _PRE_DEEPSNR_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

# Exact v19 documents; immutable historical replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('448fde8239396c03c6d8cb0931f9b4e1634b0a4a5fc3cbf67d28c8f767f61999', 16612))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('fe22abee66a2c6a7fb645aed3b2c312a4aa0756cb87341bf69ab248611c0ce2a', 16740))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('4e8017f8b73045e87aaf1ee59a8f1cb50dd7e0b9404e33ebe1f6c3007ba51e12', 26584))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('e79fdc8c5586fe4a0674f778e5583479259a33ca05bcfa0ad6b095d41ae00a0b', 31414))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/troubleshooting.md', set()).add(('09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d', 7866))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/manual-query.md', set()).add(('9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124', 5237))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-safety.md', set()).add(('864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b', 7624))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('2b0b5788e96910b3335c1388f50289423d30b20649e5d8b271bd21109131a8f1', 25691))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('a288c9825a5e0020c7ac12b1c997a55da184daaebc8ddda9e75af9ee70b049db', 49899))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/astrometry-source-preservation.md', set()).add(('6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7', 5736))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('30d3f9f2c786f4de38c404c04854813ac4966262e5eb3ad4f34b45533889a7d1', 3129))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('633bae0f10474f2bbb5ab704b519ceeda47590eccaf975111ca57e71694d369c', 8027))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('5a6bf56e57f723aada62d81f6c80df1cff83743f7ab1be5b8c6d3c074ea3d687', 10199))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('7ee0b03df9b8bb884d4b8b1c79792ca708302aef08c7dac31f8947072660bc58', 8028))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('ac882a98064afeeee5aad1cbcb03bd4cf7b92c65966f6531283c8f41317bc7be', 4112))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('be1e70bd439e3de0b3a4c536808f15025ed16015b5b9283fbc50deb88e270d66', 23159))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-01-input-inspect.md', set()).add(('ebef01b704f4df4067639374e7dadd6e583f2de401f771a202e1da78d62a0b10', 8598))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('349c727093a62c07bb02aa9eab90ae601268fdc04104e2e011f3f645f3563ca2', 18510))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('62560012e8b67bba8c63c7679b1f2c9f542851d61a15f7ec60465b7d458c3287', 20471))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('5671853b2ccc9fdb183c2e5860ddf42e00267f8933335b0bbbd1cb39eb96f444', 36942))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('9cda59d04624630545031fa869e93d86464146b8abe2c91e20d76718126a7e11', 8446))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('84dd127bc22f0974bff988fe7d8d8ab028b5f5ba64702efb0096b24cb1f6f046', 17990))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('85bb667a42d3502def9cab3e86d5740cff47555149d6f2f2027c739eba385c3b', 16863))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('c038f124397e70f8696ab01df89d4648a4340f67877307d289c9c876d39f2945', 26164))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-02-geometry.md', set()).add(('a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e', 4024))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/tone-adjust.md', set()).add(('7a1c4d4a2e268087315b254dba802280bace88177adc694927156634a9b8a829', 6915))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('43ce97e32f81bd8c86b29a12c749816b9fd91c3381389e78b4a6d51153a1a9cb', 14040))

# Exact knowledge fingerprints from the 0.2.0 release; legacy replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('7bf90f2796a2348036433dcb935c8f23d05843561225a3a7e6955c19d76b67cd', 3300))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/color-calibrate.md', set()).add(('ecbcd5b84a9bbe0aef40a2dc2cbaf43965a72ac5db7a639b06514b72d67c4e68', 1913))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/color-finish.md', set()).add(('2a7ce119f5cfdd31720d3dc22b7bd28cc5e25cf0ae9a8204c7af2cff0fd46eb9', 1295))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/color-map.md', set()).add(('3c17726210f63207f113dd3e4a9507569925c0b8461ebf67046b36dca22ddb02', 2320))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/input-inspect.md', set()).add(('948d6c2e41819266b147fa60497391ae2e69ba7dc89f8b93aafbbee49438659e', 1639))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/restoration-deconvolve.md', set()).add(('f0ab1a578ccf65c35778003bb47329e0274e9df17db330b0e8d3a304dcce1234', 1787))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/restoration-denoise.md', set()).add(('7799d054451d11a9c4d3995007bf7f747f98349608be68a2b3106516c3403234', 1263))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/stars-separate.md', set()).add(('44e68d8fa3fd5643c27ac4beabcd434beeee27b6330a08fb6503def8d70d4a74', 2557))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/stretch.md', set()).add(('6d4e81eff429ebd20d8be6b666095b7fbc901f62caa0eaee1e80f5fb1e3b0dfe', 2706))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/structure-local-contrast.md', set()).add(('b2c3056c52ae76e68e198f623e8a0467cfd47532995f1977e5c03eddb4b46738', 3069))

_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('4392415d9b409c2af5db4e85eac8bfc84611861efc89ebe50aa4be885c8cdfbd', 8301))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('3a8e82fa416a74ba4797c24e48a45820131533f2c8188d04204bb49be461db21', 4438))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('93a5833312002628a5f5c7e36db36cdf8bacafeabddaeafdb0f24efe28481fff', 3505))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('765cc1dfd2e49c8ec0657b212929924308acc12ae37e247c33aeaa851b2627fd', 4331))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-safety.md', set()).add(('773cfe6df2c891763e15fb9eba8c3c9564a97ea22a729ac31adbfd7870e5ef4d', 5620))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('1a71438c8e08ef9a370793ef7b592173a88194dfa60372b81ef73a7bc9d549fa', 4713))

# Exact pre-local-saturation knowledge; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('85872f2707c27bc2aa252cd8efdc62685614beafd3b49062d7c4e431bf48f522', 3808))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/color-finish.md', set()).add(('2790f3718131e93ddf80240c65bb382104e6038bc892c24d2521a43c59fa6832', 2802))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('458b6329f5fddc926db1005d46c3d388377d1e1063ea1663352b0648b6fe6d59', 6555))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('640b2615b804b541b52612d174b64ed6a3067317aca07c53a85c3471cd4860ed', 4809))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('1eb67839976d10bf2dc2f076dade15775320f7a87309ca68b56ec13cfb0f7fb6', 2201))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('7a49db837342b560d4ad54f77105e04bcc7664c8974bbba5fba6979127867fa0', 16083))

# Exact outgoing v4 guidance, retained only for immutable historical replay.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('db086a28edc7816af40fc7b0783874d510a99175972a63d71410469c7917dc63', 8847))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('b49de8294af4929c9b5fc13f9062308763e9db27e98b2f39b34cb091ce31c26c', 3895))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('63f4fa6c8674696dc9283b22d8bd3261e8a70978e7ccd89f73aeb6cbba65f6f7', 12383))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('275e9834fd68f1184d72a37fc19dec82e94a3e5fdcebb8c363d9508642f8085f', 19041))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('fe4f61c94a10639a026ccdb9ad0fa614f72243cd8e014d25e08c7af0421dbc72', 14383))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('92c9ff1217770dd094d61932a5c4d530d09947e65631af23facaf7f60ef55e9e', 31246))

_PRE_BACKGROUND_BASE = {"log_diagnostics": "fail_closed_v1", "network": "offline_default_explicit_gaia_v2", "starnet": "pinned_python_bridge_v1"}

# Ordered execution-policy history. A session freezes EXECUTION_POLICY; every earlier
# shipped policy stays listed here so its receipts can still be replayed or finalized
# read-only. Adding a feature means appending one entry, not renaming a chain of
# constants, and membership is tested by exact policy content rather than identity.
# Exact outgoing v5 guidance; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('f4722525b46b029ea13c1800b2ce051a927ee6ca09283d5882e34c5f968f5ec2', 9022))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('d6a9f87f245ab866da12137228463064f3d8bec6a113e6f777aa6a103eb56fb9', 3942))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('6ecea093fccecf53006853d7a674acd15bd736e747f78e4f7886d23707606b34', 13982))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('ba95cb6d2f629f0db3b548b19dfe440113b34e89511228df0719fc5312401097', 19316))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('483d4159dc186b979bad6e62ff1f77eef051f5e078faecd9317fa80e989d4e9f', 14651))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('c80be1902ad617da7c775de1599ad395c86e201f85848a2735f659701b26f699', 31656))


# Exact outgoing v8 guidance and command policy; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('c7091cd213f93dff1d48d5984d0697093091b219545658a5cf1da0be0c246e81', 9456))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('43461f3cd50fc9ee3210db84e1de44fad842940dbcdd8c8ec8564c5f21b8bd73', 3956))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('540dd9250a47b3f6dcc5c555fceb64778c035a3e1510e41b02c8071ebb58f935', 17099))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('f4c6e6dc083472a65d4372ed885120dbc15ed47434edb73c6b36c33f7393519f', 15980))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('939bf3c45e97ede457fb3827d763f6258596d903ff117bef05e36331035f9224', 21400))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('a1aaeb64f30dd130cfcdb5db5ab4146561f347969f83135e856156992654a6da', 16836))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('a5e86f7be18a4d4cbb934de2ffb10cce65e47cf78b60f99918d3abef7375d384', 34065))

# Complete outgoing v11 policy; immutable replay/finalize only.
_EXPLICIT_RECOMPOSITION_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'stage9_explicit_sources_v11',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1'}

# Complete outgoing v12 policy; immutable replay/finalize only.
_SHARED_PROTOCOL_DOCS_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'stage_shared_protocol_docs_v12',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1'}

# Complete outgoing v13 policy and exact knowledge; immutable replay/finalize only.
_AI_MORPHOLOGY_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'stage_ai_morphology_measurement_v13',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1'}
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('9e2a5901ef45fe8f1de491f2c6d7d4bd42b693bde16fede97bac04a583b7fc27', 13545))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('a1e334751d0c22f705c550e4569f86143117763e1cb341d3016e5c2b553538a3', 22547))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('1a39b3f796b2607b31acf11447aa62901bb8cc1697b55555ffd5b252a8c54b9a', 13047))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('22cd2bc808599c1015031ed8745cab27057834e4c0e24d8b25e85ccabae4b05c', 24472))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('e0b3a7a0506d6513341f40f2a71ace50124057c00d4c68b5c998b9e75c62b76b', 51074))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('a17d098c08b42f7eaf097bd70ad93edc5c4939a436499dbe77155507cc36eedf', 28398))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('0d80651840a0b7916de77723afab181470c4dfa12efb5d38dca587a7a19c1a9b', 7054))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('83a43e71e477c32c13d907e6879b2ebb0f1de72ada31701b3683113631840295', 13296))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('491b952e9e123aeb759d2c736baf808e94e6daf15a498ed39b7ad96f73ced0fe', 23431))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('d45f36055617e5c992eb8d3d8b7b52b738cc2ccf439091898f077ec49d4b3184', 4099))

_REVIEWED_BATCH_PREDECESSOR_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_ai_morphology_measurement_v13', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_REVIEWED_BATCH_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'reviewed_batch_flow_v14', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_SERIAL_AGENT_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'serial_agent_flow_v15', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_ASSESSED_STAGE3_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'assessed_stage3_flow_v16', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_PREPARED_STAGE4_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'masked_dual_v1',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'prepared_stage4_flow_v17',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'texture_hf_review_v1'}

_BENEFIT_FIRST_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'benefit_first_flow_v18', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_VIVID_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'vivid_color_flow_v19', 'color_curve': 'masked_saturation_ghs_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_DUALBAND_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'dualband_hoo_flow_v20', 'dualband': 'hoo_proxy_rgb_stars_v1', 'color_curve': 'masked_saturation_ghs_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'texture_hf_review_v1'}

_MULTISCALE_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'multiscale_structure_flow_v21', 'dualband': 'hoo_proxy_rgb_stars_v1', 'color_curve': 'masked_saturation_ghs_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'wavelet_clahe_competition_v2'}

# Exact outgoing ROI policy and document bytes; immutable replay/finalize only.
_ROI_STRETCH_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'roi_protected_selection_v2', 'visual_review': 'subject_visibility_v1', 'guidance': 'roi_guided_stretch_flow_v22', 'dualband': 'hoo_proxy_rgb_stars_v1', 'color_curve': 'masked_saturation_ghs_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'wavelet_clahe_competition_v2'}
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('51e49192cbe0b331acb154dddf6b093e3007f09156bb5913ebb4834a88bb4c4b', 18243))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('bd1859e0908bf52c475d3a71a5bbdc6a54e2339d7b63e36f0358ca937860bb45', 27661))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('e16de6acf43c3a09ac8f279c33125ce54acdbd154ca86375d837b73231b2827d', 18127))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('064e6d85794eeea3999c689c960a4cad7db40b108dda881fa7f25f87683e649c', 32804))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('d3ae9d1a897464702444d165347413f3f0a3b44189a37d501e1af0383c7a9ed3', 26841))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('2ac080cc20be60bce86849af8d6394b9d370cf3f9a08d68dbb08292811682e67', 50923))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('2da5399d59b293caa65737293e680128b96e74e681c0396d06cef9063fb4168f', 8857))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stretch-profiles.json', set()).add(('63383f2a0ba7e1c0dce979bc28dfb950dc1a969572f9dec67dc03c94263ce15d', 1669))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('7ee0b03df9b8bb884d4b8b1c79792ca708302aef08c7dac31f8947072660bc58', 8028))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('a7419a4ac4378bb4d5a06ff0f796b1b9ee11d828764112e8fe80f5ffaa9a5e09', 32869))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('26b671a4f4f370c3b04f6834038bf4d60e4c58eca9cb7cd3848f563072078e38', 19637))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('44d9a948524112ffc1d669b880dbe955edba3781d012a48b03291f6042824047', 22298))

_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('fb97f4aeac3b9795a78f800050e670636b3d13603cc7baa6ba3061e8708b5d53', 3709))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('a676219beb7edd6eace593a201ad9bf7625b935077cc7fe45771fee5ff9dc829', 8628))

_PROFILE_STRETCH_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'profile_guided_stretch_flow_v23',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2'}

_STARLESS_STRETCH_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'starless_subject_stretch_flow_v24',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2'}

# Exact outgoing Agent proposal policy; historical replay/finalize only.
_AGENT_PROPOSAL_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'agent_proposed_stretch_flow_v25',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2'}

# Exact policy before subject-first enhancement trials.
_STRICT_ENHANCEMENT_EXECUTION_POLICY = {**_AGENT_PROPOSAL_EXECUTION_POLICY, 'stretch_noise': 'diagnostic_only_v1'}

# Exact policy before Agent-proposed linear denoise.
_PRE_DENOISE_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'agent_proposed_stretch_flow_v25',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'subject_enhancement': 'benefit_first_v1'}

# Exact policy before explicit recomposition proposals.
_PRE_RECOMPOSITION_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'agent_proposed_stretch_flow_v25',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1'}

_PRE_TONE_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'agent_proposed_stretch_flow_v25',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1',
 'recomposition_parameters': 'agent_proposal_v1'}

_PRE_COLOR_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'agent_proposed_stretch_flow_v25',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1',
 'recomposition_parameters': 'agent_proposal_v1',
 'tone_adjustment': 'remaining_goal_v1'}
_PRE_MORPHOLOGY_EXECUTION_POLICY = {
    **_PRE_COLOR_EXECUTION_POLICY,
    'color_evaluation': 'unmasked_first_v1',
}
_PRE_COLOR_HELPER = {'sha256': '0670e48dc01fd46391428c7d3b7cb2ec72d595bd8aa7430ce62f9de5c4f1fe1a', 'size': 34045}

# Exact outgoing policy; immutable replay/finalize only.
_PRE_LOCAL_HDR_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'morphology_default_stretch_flow_v26',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1',
 'recomposition_parameters': 'agent_proposal_v1',
 'tone_adjustment': 'remaining_goal_v1',
 'color_evaluation': 'unmasked_first_v1'}

# Exact policy before fixed-catalog Stage 9 geometry.
_PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'morphology_default_stretch_flow_v26',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'local_contrast_hdr': 'wavelet_only_v1',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1',
 'recomposition_parameters': 'agent_proposal_v1',
 'tone_adjustment': 'remaining_goal_v1',
 'color_evaluation': 'unmasked_first_v1'}

_PRE_DEEPSNR_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'morphology_default_stretch_flow_v26',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'local_contrast_hdr': 'wavelet_only_v1',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1',
 'recomposition_parameters': 'agent_proposal_v1',
 'recomposition_geometry': 'fixed_catalog_profiles_v1',
 'tone_adjustment': 'remaining_goal_v1',
 'color_evaluation': 'unmasked_first_v1'}

_PRE_SUBJECT_PRIORITY_EXECUTION_POLICY = {'log_diagnostics': 'fail_closed_v3',
 'network': 'online_default_gaia_catalog_explicit_v3',
 'starnet': 'versioned_linear_bridge_v2',
 'background': 'heldout_material_gate_v4',
 'star_diagnostics': 'subject_roi_residual_v2',
 'psf_provenance': 'native_star_samples_v2',
 'reporting': 'isolated_metrics_v1',
 'neutral': 'scientific_native_flow_v2',
 'stretch': 'roi_protected_selection_v2',
 'visual_review': 'subject_visibility_v1',
 'guidance': 'morphology_default_stretch_flow_v26',
 'stretch_parameters': 'agent_proposal_v1',
 'stretch_noise': 'diagnostic_only_v1',
 'stretch_strategy': 'morphology_chain_comparison_v1',
 'starless_stretch': 'subject_only_full_diagnostic_v1',
 'dualband': 'hoo_proxy_rgb_stars_v1',
 'color_curve': 'masked_saturation_ghs_v1',
 'local_saturation': 'luminance_peak_mask_v1',
 'delivery': 'paired_fits_jpeg_v2',
 'recomposition_review': 'fixed_materials_v1',
 'filter_metadata': 'dwarf_explicit_filters_v1',
 'background_sampling': 'protected_regions_v1',
 'restoration': 'assessed_scientific_flow_v1',
 'local_contrast': 'wavelet_clahe_competition_v2',
 'local_contrast_hdr': 'wavelet_only_v1',
 'subject_enhancement': 'benefit_first_v1',
 'linear_denoise': 'agent_proposal_v1',
 'linear_denoise_backend': 'deepsnr_primary_v1',
 'recomposition_parameters': 'agent_proposal_v1',
 'recomposition_geometry': 'fixed_catalog_profiles_v1',
 'tone_adjustment': 'remaining_goal_v1',
 'color_evaluation': 'unmasked_first_v1'}

_EXECUTION_POLICY_HISTORY: tuple[dict[str, str], ...] = (
    dict(_PRE_MULTISTAGE_BINDINGS['execution_policy']),
    dict(_PRE_ARTIFACT_FOCUS_BINDINGS['execution_policy']),
    dict(_PRE_STEPWISE_BINDINGS['execution_policy']),
    dict(_PRE_HDR_ROUTING_BINDINGS['execution_policy']),
    dict(_PRE_STAGE7_DOCUMENT_BINDINGS['execution_policy']),
    dict(_PRE_STAGE7_REVISION_BINDINGS["execution_policy"]),
    dict(_PRE_STAGE7_OPTIMIZATION_BINDINGS["execution_policy"]),
    dict(_PRE_EVIDENCE_ADMISSION_BINDINGS["execution_policy"]),
    dict(_PRE_CURVE_EFFECT_BINDINGS["execution_policy"]),
    dict(_PRE_RESUME_BINDINGS["execution_policy"]),
    dict(_PRE_TAIL_COMPARISON_BINDINGS["execution_policy"]),
    dict(_PRE_STRETCH_MORPHOLOGY_BINDINGS["execution_policy"]),
    dict(_PRE_CONTOUR_BINDINGS["execution_policy"]),
    dict(_PRE_BACKGROUND_REVIEW_BINDINGS["execution_policy"]),
    dict(_PRE_COLOR_CURVE_VARIANTS_BINDINGS["execution_policy"]),
    dict(_PRE_LOCAL_CONTRAST_MASK_BINDINGS["execution_policy"]),
    dict(_PRE_STRETCH_PREFLIGHT_BINDINGS["execution_policy"]),
    dict(_PRE_CURVE_AMPLIFICATION_BINDINGS["execution_policy"]),
    dict(_PRE_STRETCH_TRIAL_BINDINGS["execution_policy"]),
    dict(_PRE_STRETCH_ROUTING_BINDINGS["execution_policy"]),
    dict(_PRE_STAR_STRETCH_BINDINGS["execution_policy"]),
    dict(_PRE_SUBJECT_MASK_BINDINGS["execution_policy"]),
    dict(_PRE_REGION_COLOR_BINDINGS["execution_policy"]),
    dict(_PRE_STAR_COLOR_BINDINGS["execution_policy"]),
    dict(_PRE_CHROMA_BINDINGS["execution_policy"]),
    dict(_PRE_VIVID_BINDINGS["execution_policy"]),
    dict(_PRE_SUBJECT_PRIORITY_EXECUTION_POLICY),
    dict(_PRE_DEEPSNR_EXECUTION_POLICY),
    {'log_diagnostics': 'fail_closed_v1', 'network': 'offline_default_explicit_gaia_v1'},
    {'log_diagnostics': 'fail_closed_v1', 'network': 'offline_default_explicit_gaia_v1', 'starnet': 'pinned_python_bridge_v1'},
    {'log_diagnostics': 'fail_closed_v1', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1'},
    {'log_diagnostics': 'fail_closed_v1', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_channel_gate_v1'},
    {'log_diagnostics': 'fail_closed_v1', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v2'},
    {'log_diagnostics': 'fail_closed_v1', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1'},
    {'log_diagnostics': 'fail_closed_v2', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'offline_default_explicit_gaia_v2', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'progressive_disclosure_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'evidence_bound_guidance_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'evidence_bound_guidance_v1', 'local_saturation': 'luminance_peak_mask_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'evidence_bound_guidance_v1', 'local_saturation': 'luminance_peak_mask_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'evidence_bound_guidance_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_protocol_bodies_v2', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_primary_references_v3', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_evidence_limits_v4', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_starnet_linear_v5', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_starnet_precision_v6', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v1'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_recompose_review_v7', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2'},
    {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v3', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1', 'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1', 'guidance': 'stage_xisf_metrics_v8', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2'},
    dict(_V9_EXECUTION_POLICY),
    dict(_V10_EXECUTION_POLICY),
    dict(_FIXED_REVIEW_EXECUTION_POLICY),
    dict(_DWARF_FILTER_EXECUTION_POLICY),
    dict(_PROTECTED_BACKGROUND_EXECUTION_POLICY),
    dict(_NATIVE_NEUTRAL_EXECUTION_POLICY),
    dict(_RESTORATION_EXECUTION_POLICY),
    dict(_EXPLICIT_RECOMPOSITION_EXECUTION_POLICY),
    dict(_SHARED_PROTOCOL_DOCS_EXECUTION_POLICY),
    dict(_AI_MORPHOLOGY_EXECUTION_POLICY),
    dict(_REVIEWED_BATCH_PREDECESSOR_POLICY),
    dict(_REVIEWED_BATCH_EXECUTION_POLICY),
    dict(_SERIAL_AGENT_EXECUTION_POLICY),
    dict(_ASSESSED_STAGE3_EXECUTION_POLICY),
    dict(_PREPARED_STAGE4_EXECUTION_POLICY),
    dict(_BENEFIT_FIRST_EXECUTION_POLICY),
    dict(_VIVID_EXECUTION_POLICY),
    dict(_DUALBAND_EXECUTION_POLICY),
    dict(_MULTISCALE_EXECUTION_POLICY),
    dict(_ROI_STRETCH_EXECUTION_POLICY),
    dict(_PROFILE_STRETCH_EXECUTION_POLICY),
    dict(_STARLESS_STRETCH_EXECUTION_POLICY),
    dict(_AGENT_PROPOSAL_EXECUTION_POLICY),
    dict(_STRICT_ENHANCEMENT_EXECUTION_POLICY),
    dict(_PRE_DENOISE_EXECUTION_POLICY),
    dict(_PRE_RECOMPOSITION_EXECUTION_POLICY),
    dict(_PRE_TONE_EXECUTION_POLICY),
    dict(_PRE_COLOR_EXECUTION_POLICY),
    dict(_PRE_MORPHOLOGY_EXECUTION_POLICY),
    dict(_PRE_LOCAL_HDR_EXECUTION_POLICY),
    dict(_PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY),
    dict(EXECUTION_POLICY),
)
_KNOWN_POLICY_FINGERPRINTS = frozenset(stable_hash(entry) for entry in _EXECUTION_POLICY_HISTORY)
# Sessions frozen before the online-default rule kept their policy label in their receipts.
_V1_NETWORK_POLICY = "offline_default_explicit_gaia_v1"
_V1_NETWORK_FINGERPRINTS = frozenset(
    stable_hash(entry) for entry in _EXECUTION_POLICY_HISTORY if entry["network"] == _V1_NETWORK_POLICY
)
_LEGACY_NETWORK_POLICY_FINGERPRINTS = frozenset(
    stable_hash(entry) for entry in _EXECUTION_POLICY_HISTORY if entry["network"] in LEGACY_NETWORK_POLICIES
)


def frozen_network_policy(session_policy: object) -> str:
    """Return the network policy label a session froze, defaulting to the current one."""
    if isinstance(session_policy, dict):
        label = session_policy.get("network")
        if isinstance(label, str) and (
            label == NETWORK_POLICY or stable_hash(session_policy) in _LEGACY_NETWORK_POLICY_FINGERPRINTS
        ):
            return label
    return NETWORK_POLICY


def known_execution_policy(policy: object) -> bool:
    """Report whether a policy is the current one or a retained historical one."""
    return isinstance(policy, dict) and stable_hash(policy) in _KNOWN_POLICY_FINGERPRINTS


def network_policy_for(session_policy: object) -> str | None:
    """Return the network policy a receipt recorded, or None when it predates v2."""
    if isinstance(session_policy, dict) and stable_hash(session_policy) in _V1_NETWORK_FINGERPRINTS:
        return _V1_NETWORK_POLICY
    return None

# Exact pre-online-default references; immutable replay/finalize only. Register the
# outgoing hashes before editing so sessions frozen against them keep replaying.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("5b8407cba55e926a3efae0cdcf86bf982effae012276cb7079bab60b3571c2db", 7031))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/siril-safety.md", set()).add(("56599ab9ba7a2b55cfa6a13e0a710838f119b755a756c40e018f504098da5dd9", 7314))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("404ff778da19679f3ecb30dcc385e30eddf13b22767586ed4b34799b6c28c7f3", 28451))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/astrometry-solve.md", set()).add(("44286eda49ee23c6bc2ed3e59e9cc9b0ad54a98087a41d50adcba4d73310fa3f", 4839))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/color-calibrate.md", set()).add(("1fef5f4ed6bf03a8b499fcee5b4486cbc14680331016fa1703f945035554eb81", 14972))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-04-astrometry-color.md", set()).add(("b4549a218fc7de68c3e49ecedffd6d41efa2058d9a64e34d965d7bcb61e76084", 4126))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("83b5e897fdb3c97d8b5712c58065a3e36fcec9666436904ac9060d31ec451e0e", 12102))

# Exact pre-neutral-evidence references; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/color-calibrate.md', set()).add(('279445ce802dfd5aaf8830f06c25d5b576dfe892fc58f5deb476ddce8d734859', 12240))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('359388e3af61bf3e753bc762e26a525030c45899cc942e1a4a051dc417b7b04d', 24208))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('783f16d50fd9ac4e0737f7148a1e14e4fb01040f7d3ba3b83dc29c2218c5a5cb', 8547))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('7371cd1ce9e7c76a03217c6ce1654680402dc0f5672804b6b4be32ec45f498c2', 12531))

# Exact pre-report-isolation references; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("2a15658434890e7651963b1c24450b528edb44a0bd0d276c9fad4ad81a45674a", 22266))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("538c3bdbc7332272eaa4a4c820b7721bd6e3be20ab9457c46b73b1399290fdfe", 7445))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/quality.md", set()).add(("532beadc0bafca42993f35b23558f40955692d467824d1a3ab601351ffa80de9", 11910))
# Exact pre-PSF provenance fingerprints; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("d30ae5416a93a51d61840fba284d363c5cd4870b6bf812d8092a2073570d39f7", 17820))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("aa9bb9d86fe85c0e39ffcdb433a049c25fd25fae1ad40c2acf86209cefb8f2b5", 21390))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("3654cec4564ec76dbe020a23f83c4d5ac57815d5473f3dc49c6e57ddfb775dc1", 6637))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/restoration-deconvolve.md", set()).add(("9ef6e3462199d1e110344200987033412684b0d81be75bbdd76637e21976e7c3", 3148))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-05-linear-restoration.md", set()).add(("06a88225055b0d7b1529b272be97712e9f35040436a98fb2e6d6ff4e452d1b8e", 4602))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/command-policy.json", set()).add(("89912cbbf2e9cdc429c4b8e4da12b15d576f9ea225c1be76741bce3065d17292", 3677))
# Exact pre-subject-ROI references; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/command-policy.json", set()).add(("5be5b631dab63cf2ed92f2bb0f7e7306887c5b88d4520c7b182b2664ff5fc623", 3664))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("d4db8c886ca9689fb281244c38b9a36ced7fed6bce9688dfe411c955601176d6", 17609))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/quality.md", set()).add(("f277a175543a2e2be6942b07b8273a9cf9b144ad45a898e3359e518a1410e474", 11029))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("909af953d686df0e32ab80f4a7dbeb5ffc6b39b343f670b614d09a8cc60e7096", 20840))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("46e7ae5e5b60f406f98674cc5fa8a1cead3cd555470b9d09945d5cd73ab9af96", 6185))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/stars-separate.md", set()).add(("54a47fae5655c07a01bb00d97dd0f908059e4b2b3c02991542a256b16b126569", 8786))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/stars-recompose.md", set()).add(("c210e73721ef0b484a0eef396d41f4a0cec415f2ca6fd9c37832e0cfff20f6c3", 5377))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-06-stars-separation.md", set()).add(("c906a02efb36e3bb1b9e7e77e00a05678a2b849e766fb245bb39a6d3f08b6fb6", 3894))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-09-stars-recompose.md", set()).add(("0b6465fbe6815ab1fe3a174e3598a30b7a395bfa2eeef861a6043ca37339d85c", 4747))
# Exact pre-log-v2 knowledge fingerprints; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("f7ebb5acc3a172b1a2aa7171d33ab942e3d2f93b837bd9192b7f07b2a110135a", 17476))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("44e32feeb2038aee325ce0d9a87c152e6be0fd470eed3d5ad5dd8399a046a485", 20182))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("c215d38ac31093dbb69eab92524d3e3547b6856b32fdb78ee043156b3490a697", 5847))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/input-inspect.md", set()).add(("b681a85ec428e977e3d5ca4a4b1eb0aab3ce90661d2a88f6b75d8e9fbc04d84a", 2194))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/delivery.md", set()).add(("3b96276f5337f2185c61bc252e6f9bc5c2edded896076ea2b321ca86b0b41cfa", 2513))
# Exact pre-online-Gaia reference fingerprints; immutable receipt replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('dadd32042bd2800a875bb1c54209c800c8349f78c5af0807b929d2326c684c93', 3494))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/astrometry-solve.md', set()).add(('b74609d7e1abc70bfa3b78f285f0ef8917a965475fa9ea2f783ce719b6128f1f', 2409))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/color-calibrate.md', set()).add(('05fdf47228e677c66c2145278546e5f3147f8e73672057d24cbbbcbb65c4665f', 2870))

_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('4a73912ea736e7ee614164d1c33566f7ba999dc002cf49097315be2f4d245438', 5759))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('7af6084342f435a1e67ca67eb9df968ff35b20be54bf4a5f0bb5d317d74c436b', 13394))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-safety.md', set()).add(('3bebb00b1c0730ece4f294967d45ab597b57a3e119bd890eca5c186076832f3f', 7005))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('522f82bdb351e9bbf01e9c1cfa62c0287cd51fd91ce891693bc6e2fa60a98b36', 4411))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-02-geometry.md', set()).add(('4abb02a1d627fe7c448998622fd0b4877b4dfb95fe728cc7908f5daa7d366360', 3500))

_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('fe487d466699016a0bb27d716f3e4de2f45770f1f37269a5ada8b47f17eeba69', 5672))


# Exact pre-background-gate reference fingerprints; immutable replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/background-subtract.md', set()).add(('900ca768b2aea2ee238989a4a70c727486ac16f7304f861d9a344f4996f2064f', 6379))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('52e586fcf6a270ff6738c849db238d53440441f27165e4726a0856db4022d1f9', 18499))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('c3c64a5db7ae70481b32e491245dadd044e999c6a057bb0f2d298e421a2bafbb', 9405))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('2484a0a6bfee0749f59f17e1f91c09b7c2076d2256d3ba221b5ba0ff72242944', 4669))

# Exact heldout_channel_gate_v1 references; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('b6b88820b2be99af08af5aa2563b20f6c8f7ef668283a09bc4e192592aa834ab', 16615))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('80e73a4b1ac8d4a7a535b1bf6a616eb7f413dcc17f7c75d7f18b5287a7ad6c3d', 5154))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('250af2216154052cb4dc589a37adb2931efc51babc7a7da0a6faa07e9cf0e363', 3637))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('e686b3b80f0693166465e4f341bfc4535bfdfd7cf63f83784db959e33379d6a2', 10009))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('c81afbcf8a285efb85ed1f24b85443ab372d38dc011317d61a40eb4e1caa6560', 19311))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('bba4f792e4f1ccfde17ee2256aeea63c5fb2eee897955e3cdf2de88517263098', 5150))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/background-subtract.md', set()).add(('6d1b9ec5cd3d6b149944667306c2e09988cb02010122a228419b4fc14b953e64', 8163))


# Exact heldout_material_gate_v2 knowledge; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('7f0979c48ea4ec983800e345a968167d4be9ae92b7700bb463f08d40021df37a', 17154))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('91cff5134c3f89e519d667390a346d4805dad031cd53666d35826c5a29c1ff75', 5532))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('4056d707de72b9bf0746f32ef1e645b452a0225df27481ca09035db4093b21c8', 10714))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('5ba6e78af0ae1b617f6e7cf64163f3e96f641dbe5ff2d885550eaac3a6099f37', 19867))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/background-subtract.md', set()).add(('d7fed64b19ac5d66d2dbb61274dc733da03599c8e9339b83cf2f3f9134d75266', 9877))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('94ec521048a5f635f61a80d0f3928f7ab89a0ad3c409dbf5d6b9433b4ad68a1e', 5436))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('e7c898496d44b6527339a73dd1a28af9da32dfc5575238e9bd2ce7dbca06207f', 3650))

# Exact pre-HDR knowledge fingerprints; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("f29f2e67a4310b4a7777fe62ebaa4b26b0312d1218014448fb48d9a888833173", 18110))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/command-policy.json", set()).add(("a8aae3569d3a4ae4a8583b57bfcccea08dd2408a44224a74135efb56831681e4", 3749))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/stretch.md", set()).add(("6453f255c46e4aae7ef22a97f0970189b510ed39a3734969795e15a5f5857c63", 8571))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/stars-recompose.md", set()).add(("45639dc6ef5962b79390bb1660b642c9977273c8b7157ddd83cd1f624d7a7595", 6623))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-07-stretch.md", set()).add(("c2a2f4364b9ce9b25f513151f67fe5a62a9d03c87641005fbb081d5208cfe33b", 1954))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-09-stars-recompose.md", set()).add(("ad87fa01e2ba98f91f215502be49fd6ad9a5abd3d106c4c58f4715d7ec17574d", 5253))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("b42c6e8f7426b11d76e2a32328b71f158a598d8662acc87e0c9a967f5dbdb5bb", 9841))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("0288f5fb878b29c33da9072a1f446c9bef233847dc090b97f6b46430ee67d316", 25502))

# Exact pre-subject-visibility references; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("9595473983c089f9ae7097fc2538f770780959a2dd7ad3b383224295070abb04", 18553))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-07-stretch.md", set()).add(("d0cd9892fcba200c02158f4767c5d7557fff67675621a9226b1a8c279747dfa1", 3089))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stages/stage-10-delivery-finishing.md", set()).add(("2321adf15d9637f45c4e2dff6a32a9f14d2bc0806f8d08b22c5527781ccb7a26", 3149))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/stretch.md", set()).add(("7b2b497a27e6490e791ab12de8e7e11939d98d8600beae9765c366c24ef056d1", 14846))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/tone-adjust.md", set()).add(("177112bf9e10da60f0c79c757eefb052e8a0b7ff68e031f3aa164f41b2ead47b", 5548))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/quality.md", set()).add(("fbae2f7403aa9e5d83e66b047ed890e3da889ed2afaf0028fc9a98115171cd32", 13825))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("305f50265743858b7565b87d583afcc8cf89625a64e0791553cb4a192c391f9c", 26163))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("9b3b5faaf13527e60866783490f9c67c3eddebbdcc0e9abbbfe9c4b523c5c677", 10999))

# Exact pre-disclosure guidance fingerprints; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("SKILL.md", set()).add(("7d1bf3e99fdcb78db3f5e100a8273691653755064c4933522b0261fe9810bcce", 20756))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocol-index.md", set()).add(("0716d2782db02b853b64f6d081ba4875f58da27f79999e17114f537894f2047a", 6049))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/stage-sequence.md", set()).add(("27649f748f7f453fe23097ba17cabf8a49ef4d6f11b5b70b89d2f2f5f53f10ea", 6949))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/cli-contract.md", set()).add(("47174cacb5337f3bbacc82d6c438cfe89949fa06b472ee9f65ed4c68ce02f341", 27365))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/quality.md", set()).add(("de96726b73de4ba4b9e35d02b3645a5a7d2c80a9da57cdecf2d7a9c161e9f64a", 15727))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/delivery.md", set()).add(("74b76876173bc33fd0f0d24d145b87f360b210aa96fd786143d55c72a3c7f5fd", 2609))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("32c0f6b470bacd30b0fa406765e476aabdd434a13fd78412915a51460833c5f9", 11373))

# Exact pre-evidence-bound guidance fingerprints; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/astrometry-source-preservation.md", set()).add(("215644eb50c87ca8eca3ae4d44518983a9c0efcb8a9b56aeee237482a22b5828", 6289))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/protocols/restoration-deconvolve.md", set()).add(("1aa953afc8424752237088bf2f7dd675d9dbc7fe8a15bedbed30d947e2b5d9c4", 7978))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault("references/session-contract.md", set()).add(("22fcab70582ba60a3f3486b6ac46c8e986b5b72bacfd96ba148784884ee8b096", 11907))

# Exact pre-PSF-v2 fingerprints; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('e7b9619267d19130decf488a3919767b77cfd881c9bf17e9843254146d04fee2', 7254))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/restoration-deconvolve.md', set()).add(('f2ca09641d630f304ccd119a736943a692e48de21dff75b447723517d244e70b', 7694))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('f402e3bfd7768c9e84e471e84f6454bd67750d4e126cf3d6d78236aef24c8ab4', 5053))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('854e6bebc9f128f0f56376a4b86557fba3c68e01adc7a128a3e4ba59bcc28d73', 16827))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('ddb6fc40455425e0ee44d65ee7e974511fb8c8a8440d54267c6b52cc946da4a2', 12185))

# Exact pre-paired-delivery fingerprints; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('be1c2515ab28d1ebc71a901f79bbb44de1233cdee105e8fcdf377965e4741c06', 7254))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('7a14029bce9ced799c982157ef3e91c4bce7d7ac286cfb48996af6b58775fc7c', 3867))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('5af038a78587d043c9ac576eb7363b20d1b8601b74a5ff75a3c4494f2590e47e', 3566))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocols/delivery-render.md', set()).add(('fb1c015265e6dc458f67195ad8735f3e137ad5109dab833a1c4865b84b76f8ef', 2472))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('0244986de9681eb1aca0a0194918dee3ae5289334e26b1359d4ef51aef7da484', 28492))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('780c1ca904c9351434bf8d06d897b71b303068b070a31301a38ad5149d32c9b1', 17772))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('d8307afe3c9e9845043f1c36a5a760fb0156c656bc8dc571f96ebbde21efdc3c', 12580))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('5824217259f87fd28992b78179865adfcce8f4190b64f44780fb7a79c3e80153', 3614))

# Exact pre-stage-body reference fingerprints; immutable receipt replay/finalize only.
for _path, _digest, _size in (
    ('SKILL.md', '5d7a208799b61d1c8241f17c9fd2df96ad8e0417c8a55a2fbc120730d3cd60a9', 8464),
    ('references/astrometry-source-preservation.md', 'aad3ff8e62ffd653a96bea77016bd9445c2a21ff4aa6c17ad0917ad36f3477d8', 5667),
    ('references/cli-contract.md', 'b3d5425af484e5f308a646388e64bf3eb065f3e4047eddb800130022140dfb72', 29969),
    ('references/protocol-index.md', 'ed083d2dafb57d7acf20ccab7cfe86378fe61c7929f54632cdffe7b29a46ca06', 2274),
    ('references/protocols/astrometry-solve.md', '286171c02b137a0468660a959ee4181bdab1c87bc8012858d29cf0f2b2f22555', 4930),
    ('references/protocols/background-subtract.md', '05db97bf20fa9fddcb97bd476c37452620bfcabb4149f2787da296e3d894e4e2', 12371),
    ('references/protocols/color-calibrate.md', '475f162f9e915b5c58965c58840fc28239d8396a42f0233a995376a7bc95aa6c', 15057),
    ('references/protocols/color-finish.md', '5dc0f46c969632b01b07c49e6325e815260532667cfaf8ba138e86b1e6e52050', 7797),
    ('references/protocols/color-map.md', '84ffdba5c02874f833fb69971e6da937c4699aacc30b2e752b4fc900ef1676db', 3354),
    ('references/protocols/delivery-render.md', '769cef8ba90a193c323d470d86fcf8d80e84ded707ad01eda8c2e49e2f329293', 3786),
    ('references/protocols/geometry-crop-near-black.md', '45dcd403d5c11a288c79dc427c2dae5d49c3fd0dbf130f1981fbec2a99dd6102', 1464),
    ('references/protocols/input-inspect.md', '21769e0d265c4fbe346550fdabfb12927b240c93fbde507e90b155be85fa5fcf', 2523),
    ('references/protocols/restoration-deconvolve.md', '8e6cd52766d958dce8cdb6c0626d55f827dcbb217f5b7c02f495fcfa5893944e', 10568),
    ('references/protocols/restoration-denoise-nonlinear.md', '33c29fc339734a7c9e3a8a7d4a7d066d6bd6271c9d385399cad71465940b3b1c', 879),
    ('references/protocols/restoration-denoise.md', '082ab62e1297d4b3e1dbf32e273eebba6044272a1151f6bcf7d45ec50001e172', 1445),
    ('references/protocols/stars-recompose.md', 'e4f40bb94c675058ecfe0163461511aa79949b1f97aad7bbd70f881e653854fa', 9824),
    ('references/protocols/stars-separate.md', '85e61cb4803d62585c79653a2a02bfe3068bac8e4bd50c964a019aa343be44b7', 11308),
    ('references/protocols/stretch.md', 'cdecf45cdf2277fb95645c54696477e6e250fe4ed2c36bb19b5ee489cb9d274f', 20862),
    ('references/protocols/structure-local-contrast.md', 'f8c5bbaedcaf74010b20c5a30231816d763d298eefcf18f8a0ce91b42da55a80', 5930),
    ('references/protocols/tone-adjust.md', '3cd14cbf406045aa12ff97919daa15d399bb126ad32b7efa8ef52256f4a22fdd', 6167),
    ('references/quality.md', '709d8462651dfce26ef6520225d2f64cdd99bdf0426e5f3540af5e2f663d729e', 18353),
    ('references/session-contract.md', '35384da8a5671e0d2b7b1c59d5683bbd4cc36dd4f460eb3be369dbf081bad9e0', 13483),
    ('references/ssf-provenance.schema.json', '94479cc4352e767962d8659c10f80c6806a1f65eda5a2284defbec4313a3f70e', 3961),
    ('references/stage-sequence.md', '4cce5d3726afd6fa87cc7ce7673e16cb07902bed4796c63fc7b6ac85229607d0', 6813),
    ('references/stages/stage-01-input-inspect.md', '066fd34603936093943a145b52d3eca337d7d63865219934370b13570a42a3d1', 4178),
    ('references/stages/stage-02-geometry.md', 'd7e42cc1c593c947ccd9f8ccff451886910390fdd2c391a93ffddc59c6943336', 3700),
    ('references/stages/stage-03-background.md', '11a2cf471e4d5c450b07b131b261dfcf0ef9fd2eb7b6249493f5bbf13fa0023c', 5751),
    ('references/stages/stage-04-astrometry-color.md', 'f3a3e0e97be05da2b0649110020a41910319572e259123eceda246aec7ade6ee', 4236),
    ('references/stages/stage-05-linear-restoration.md', 'e262b240b822fd62746e0eb4fd9a40698ef8bba5279447ba9aac532455788d0d', 5394),
    ('references/stages/stage-06-stars-separation.md', '28561dee5605c5ff05b1cd176cd2b4b741f263de6b0d85cb7931e3f5891b6f4f', 4313),
    ('references/stages/stage-07-stretch.md', 'b360cff411ab9d25d89590d36fac8c45f0c4908ffaaf85f1f8b9154923b0cc71', 3908),
    ('references/stages/stage-08-starless-enhance.md', '17cb89118bf40cae01f44447c1b892eb1ab2f11f08119d14b99ba18ef71209fe', 5517),
    ('references/stages/stage-09-stars-recompose.md', 'f13abeda956dc9f74ddfbc23dd731975ea94a1ad20bd28802b21272e6b10d13f', 6523),
    ('references/stages/stage-10-delivery-finishing.md', '0777b567cc066d18af63f369a61257d049ad5b49a826ec75f9bc183f05366340', 4531),
    ('references/target-morphology-review.md', 'c69e3c268ad5830b6f58836994e798959c8083590f073c4b1c57c33d40e199a9', 5551),
    ('references/troubleshooting.md', 'a69d6c60f5bb364ea258edcf856dacd34d986940448934a2d3a3bfe244ae6ebf', 7561),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))

# Exact pre-stage-primary fingerprints; immutable historical replay/finalize only.
for _path, _digest, _size in (
    ('SKILL.md', '97b6c22bae3ebee8bea3f0191d240a81212b84b17586a2861fa0f36ca1cac01f', 8697),
    ('references/cli-contract.md', '941a5703655b973f7340c3a6746b08f3830ae65ba5579aeb4d874715c9f3e43b', 30212),
    ('references/protocol-index.md', '4e443b47fa92b518851c0615e22f60ed991e78f86f87cbf6a3897507ec249e06', 2969),
    ('references/protocols/astrometry-solve.md', '6c99d4584a010949d255ce41ba4fa7fbc20203722f7321aeb85da9a17f0e9a14', 366),
    ('references/protocols/background-subtract.md', '89a543853073609c4b4c960b13f45ff954352ec9d0e238baf7d4c84678f8c19f', 366),
    ('references/protocols/color-calibrate.md', 'c75c7224642af63cd439a775596cc5d961bb8e3c8da1a7716e7264be7a81bc66', 364),
    ('references/protocols/color-finish.md', '15992ac37c44cbf8906a4011d5d64546faf7bc7523b95a4e574227d466e2ec79', 440),
    ('references/protocols/color-map.md', '404cee7d8ccda5ec0153bb1a791fc6426415f0960230612ae60d7006295d8661', 352),
    ('references/protocols/delivery-render.md', '1ffa4d75bccdaa206e8016ff33fd63eb0c418d014c300abadb92b2c10228609d', 367),
    ('references/protocols/geometry-crop-near-black.md', '3f9a50a13d0b244c13018c2fd6e4c18132bb2040838ce3bf663057aafd7767f1', 374),
    ('references/protocols/input-inspect.md', 'a21e780ef42f7f4fe528e3c550f8d76fd5e9d63be8177e27806f46d925b503c8', 357),
    ('references/protocols/restoration-deconvolve.md', '8e630aae890759eca5357f707c321b13e8d5f861743e22c9ce90c5e21a8480e0', 380),
    ('references/protocols/restoration-denoise-nonlinear.md', 'ddb01cecdad76ef330b83ea065f11f7e99d34b7667abf14221d4c727703a3124', 395),
    ('references/protocols/restoration-denoise.md', '6705a5b318ed69c38ae7a4c5608bb5157964a9714c52e453b9ed448540b04b6a', 374),
    ('references/protocols/stars-recompose.md', '58b55cbe710674eef21df5983158a869ef233ad3e0c0c493d59f61e163505d08', 363),
    ('references/protocols/stars-separate.md', '83f868cd3d48e6247dfad0423a6fe572f82fccf2d7faf491c403625eba5e25a9', 362),
    ('references/protocols/stretch.md', 'fd6178a71f91ab02d3d2098414e6b18dd69c3ebf73f6efbf8389e37ceaa4dff5', 339),
    ('references/protocols/structure-local-contrast.md', '178867026327f5efd8bd6dd3d0d869dae608496e765581685dabf5fb99804c6e', 382),
    ('references/protocols/tone-adjust.md', '9abf5b6356da01814110a5e2559792eec837e6ddfadc7bf82fc9909e6db65c0f', 359),
    ('references/session-contract.md', 'dd196f7aa1301aa89e535d2b8049f00f86b71bbb9586df383ca8a798ec7bff8b', 13920),
    ('references/ssf-provenance.schema.json', 'c7ae3304bff0cef4a7bcb310ed8bcea10606aa263ed57508bc9d1b8976500aa1', 4031),
    ('references/stage-sequence.md', '5be8ef523d9ee8b8970480ddc0aad80ce97c4d1968cc848f6ae1b73c5a510c61', 7013),
    ('references/stages/stage-01-input-inspect.md', '3368f7c2581a05ab08b4f0ceac2192a0ee7f0a27c41c94fba3e2ebf7869403b1', 5090),
    ('references/stages/stage-02-geometry.md', '3e4220023269d9ea393e0b62d24a657db78b0d49443cc54f1b025225491ad60a', 3825),
    ('references/stages/stage-03-background.md', '316baed4e49df8102cfb2d66d85d8e62f6d19640c35113bac81ca34e0f44eae6', 15431),
    ('references/stages/stage-04-astrometry-color.md', '83771ba3448b5a1f55acbf6d142c4dafaad44189bc6ace32bbc1c03f09ea2478', 21669),
    ('references/stages/stage-05-linear-restoration.md', '082c4835781c8c451d56d0436c178d0c2b405c1131517f8c3cee8afa8279a249', 15152),
    ('references/stages/stage-06-stars-separation.md', 'b66d112bbb4b1f51722525e6c043622b0cdd316c378dd4988ef6a17ad399f165', 12506),
    ('references/stages/stage-07-stretch.md', '1674a01373d5c6bebd39bf9d96e8273e935988cb24f28c1e7ac2911b1f5c9151', 22067),
    ('references/stages/stage-08-starless-enhance.md', '0cb96c888f316bd527ff58bcdfec2d177b3998a76336e08a77e0a630d035dc15', 18094),
    ('references/stages/stage-09-stars-recompose.md', '65aab9e80d1af6dd5e15d6983e0964b9d056109a7e4b5c72d210437d69586640', 12377),
    ('references/stages/stage-10-delivery-finishing.md', '0857080ec705a93a8333c6e79779d1dce68dc07dde807342ff915442cdddec6c', 15681),
    ('references/manual-query.md', '51dbe9a0dc6d24d72e901ce16df96466655e11cde634c2353034f4186899777a', 5217),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))

# Exact pre-stage-evidence-limit fingerprints; immutable v3 replay/finalize only.
for _path, _digest, _size in (
    ('SKILL.md', '7c85bdfc09deb52796446cf1756138c1a3c9546f8104f6ec12fd6cc473166832', 8681),
    ('RELEASING.md', 'f2c1e8d83c6b84535877c45601db341a128509cee6b259be055aac7f9c1d8383', 6380),
    ('references/cli-contract.md', '65284fd81b200733d16e61f78a87b36a318ab0bb2b101d3f08a89b87f8e5603d', 30214),
    ('references/protocol-index.md', '87f975b3755d4b0c911f633ad1d9b390e32c40d2bb936ee6f1bf67bbf0d48449', 3024),
    ('references/quality.md', '2a310c1d24e462935646fa48a7317866a0f2123a69ccf621cec26cb27af1a11a', 18461),
    ('references/session-contract.md', 'ed552e5ff8e4d1f1b399e24fc246cbfc034577df20f9d274e48374e788c9f271', 14105),
    ('references/stage-sequence.md', 'c15ae15c5ec9c0fff0d6abc6c628c28226c67691f59aded77d2fb03a136fcfa0', 7523),
    ('references/stages/stage-03-background.md', '238873f001c15b2175cd82e4b779d65596725aa5202969dbb3e1a0d3392f709d', 16987),
    ('references/stages/stage-04-astrometry-color.md', 'acf26b9b33e8e1042ce2cf021f7f88ee82257201f96ec951198823028d2a3c2e', 21491),
    ('references/stages/stage-05-linear-restoration.md', '2027041419e183af04c2f08618d8916d1914bdfdfcaf013e0e9e952ba2c81e64', 15020),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact pre-recomposition-review fingerprints; immutable v6 replay/finalize only.
for _path, _digest, _size in (
    ('RELEASING.md', '8d33dc3d41ea2119bd1aaf0f7672492604d3538036f5934048168ea59f42d83d', 6927),
    ('SKILL.md', 'ae352c38e1d050890c0972adc3b8ed0fb3dde50f72a68e6d262d21d0e1748e61', 9306),
    ('references/astrometry-source-preservation.md', '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7', 5736),
    ('references/cli-contract.md', 'a1a799acce24c48dbf6b87c780cdda385d7fa781efc9663d2ab2e6a2741b1633', 32386),
    ('references/delivery.md', '8df44d0c7ab8f925d8e703f4a7b176c00c29ea77baa8746f8e6eab004d4cd198', 4304),
    ('references/manual-query.md', '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124', 5237),
    ('references/protocol-index.md', 'cc1b6dd6d0970d977adf7b0bd62a3352c7a91d6ee227a668b6089815ace2de05', 3031),
    ('references/quality.md', '3096be1527111d5e6ae2358c8a59a69bc5d73d5d6ce2a2c854dcdfcc7131cdff', 19643),
    ('references/session-contract.md', '0ef155022a145ac638a3e725cf55de142e7348e2da072331ec4f68bdeb5614dd', 15071),
    ('references/siril-safety.md', '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b', 7624),
    ('references/stage-sequence.md', 'dbd366e7560555f48e1c91d8652d1b284a115c4492fd22063a95dc2194098eae', 7631),
    ('references/stages/stage-01-input-inspect.md', 'c5e2a99e73b9435df418340f0a0fb6e2b0a2d2e55a26ad7b479e94578be88c2a', 5021),
    ('references/stages/stage-02-geometry.md', '0bc566a37f00e8618451fe4358f5405b9368ac51ead561ed08eeeb0c3ba56e95', 3745),
    ('references/stages/stage-03-background.md', '540dd9250a47b3f6dcc5c555fceb64778c035a3e1510e41b02c8071ebb58f935', 17099),
    ('references/stages/stage-04-astrometry-color.md', 'f3dfbb8495694d1aabaabc228c25912c18fc49a2385b6a5a58b81e34a15dcb53', 23241),
    ('references/stages/stage-05-linear-restoration.md', '26b31bd63eeb9078d35f01452cc36c365b174d657bb2893d8f5b7940fd10f415', 16577),
    ('references/stages/stage-06-stars-separation.md', '7ec4955b7f18b1009165a982f80663f44c3eaca53ef319cab1add249eb5ff52a', 15925),
    ('references/stages/stage-07-stretch.md', '1d046abccc69036b28d0a4608d07703852753cd8073e32a809cd93b615dacc3a', 22004),
    ('references/stages/stage-08-starless-enhance.md', 'cac89ed10698a2ff6f0aa0c2fcbfe6fc1cb3ccaf4beeb1a7cdfe04106193d48d', 17995),
    ('references/stages/stage-09-stars-recompose.md', '3d334c7232b555534a01d52899958a36e48ba3b24128f5075a19c1cb8e57559a', 12294),
    ('references/stages/stage-10-delivery-finishing.md', 'c780a711c05a7be976b7a4bc986a98f18dba435a1941467b8a118f75deeb833e', 15397),
    ('references/target-morphology-review.md', 'a16fb18e53c175f1998db76dbe7a75235e5943ec43be8bc8ad541e0b69d8af10', 5656),
    ('references/troubleshooting.md', 'ef822c96bfaa9e6c5fee5d1e73b748783341d676a49b3542a8c891bb7e7084e9', 7724),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))

# Exact outgoing v7 guidance; immutable replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-01-input-inspect.md', set()).add(('c5e2a99e73b9435df418340f0a0fb6e2b0a2d2e55a26ad7b479e94578be88c2a', 5021))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('571e5f4b2d38ac8ff155d13fbfbf505c2868c9e08f1f879455447dc8f287374e', 20562))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('2736a6779d3d713ab12347b2201e8f4cd02383c8288bdf38dec5ab64de827b67', 33206))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('260961b048e72907744d4fc360dd1d5e3d901c71abb9345a46ada97685eea43c', 16157))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('597daff7204aec5ffb76c8c44bae50570c14127acad0f0c7732e6db4a02c9079', 15984))

# Exact outgoing v9 guidance, including post-inspection confirmation documentation.
for _path, _digest, _size in (
    ('SKILL.md', '33b00dfb8eefebd40d5934ba19fa1ccb355e5ac19b6156afbd79e5c46796f634', 10685),
    ('references/cli-contract.md', '7f0ce5082aadd2d67c2db15a4af14e13f9881e103831c0b5e7b9b993eb1bfa91', 37746),
    ('references/session-contract.md', 'e1fb84ac3f1bd409e2abcaba21faf854310d6d2f1bd493e878d85a8a1df4080f', 18024),
    ('references/stages/stage-04-astrometry-color.md', 'f3dfbb8495694d1aabaabc228c25912c18fc49a2385b6a5a58b81e34a15dcb53', 23241),
    ('references/stages/stage-01-input-inspect.md', '43600f32b9354bc80c4922795df82515e0463ccb265dbb9bb20287af944972cc', 6153),
    ('RELEASING.md', '8d33dc3d41ea2119bd1aaf0f7672492604d3538036f5934048168ea59f42d83d', 6927),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact outgoing v10 references; frozen review rules remain readable.
for _path, _digest, _size in (
    ('SKILL.md', 'e82d40c38508a25755edaf41847a660f3437ab4c07c14707f9edb5a040eb914a', 11373),
    ('RELEASING.md', '2f7b99f7e8cb1bd0fccf85a186e9efff784f27639988f80f22c9f2c9a5cc14ff', 7533),
    ('references/cli-contract.md', 'e1ffb6c735aea3f0e51d1e126d36fd6f9df6efdd0b61eb72bd6f4088222b304b', 38751),
    ('references/session-contract.md', '1cabeb4999f4ad1a2771d84b2b39411c8d49e1ac4a91bac4ebf3aa273b70dfeb', 19103),
    ('references/stages/stage-04-astrometry-color.md', '26c6221ca60b4a755bffe4956bd3128bd49bcb59361b4158cf8b5ba93f368afa', 24828),
    ('references/stages/stage-09-stars-recompose.md', '44787d60e522435c48a9d1dbb534138c4504c688f950fa802213cea01bcc8d94', 13923),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact outgoing fixed-review references before explicit DWARF filter mappings.
for _path, _digest, _size in (
    ('SKILL.md', 'ff91ef4838277cb70b65001309e200127fc0be33bb6d647969ba4b2dc409fe3f', 11982),
    ('references/cli-contract.md', '3dce484f3314d7bac683b1c04fd9e07085c3389567303d77345b994afba966fc', 40028),
    ('references/session-contract.md', 'bda64f4b70582d199b1ec85f7692ee27b7f663b5b0169a478078958a3fb6b520', 20378),
    ('references/stages/stage-04-astrometry-color.md', '0ff23108cca6173b333eb6244b8e9f0e153a4b166ea1e7b714705b97dec298b0', 26503),
    ('RELEASING.md', '547d40e2b24481db7522d2c73211ec51479ca505390d4e0c65b3ca4104336d54', 8245),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact outgoing DWARF-metadata references before protected background sampling.
for _path, _digest, _size in (
    ('SKILL.md', '3e35d03aa0103c185ea2ec9a09051502d651c8fa6a7155fbbba3aaf0aa7485b7', 12696),
    ('references/stages/stage-03-background.md', '71f6d96f719bf83f55ec3f47a96822f2eb8bef974f6d53579412a6225fcf2fe7', 17915),
    ('references/cli-contract.md', 'c8f2d77d3a90777f0b397303ae67a6740c62fdca934a3c9f630856bdbb16e075', 41871),
    ('references/session-contract.md', 'a4769f3cbb29cc37412a5e314237b1d634d0ab0f6ac3b7d42acfecc115213bf7', 21407),
    ('RELEASING.md', 'ac85ad41d016057b9bf291123800c117a5bb72dc7b1ee1737a6a21592db5f782', 8945),
):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact outgoing protected-background references before native scientific neutral.
for _path, _digest, _size in (('SKILL.md', 'ead12655c949a7908abafd10a40447c27c4bdbfab8409019bcfae69d97b13a7b', 13534),
 ('references/stages/stage-04-astrometry-color.md',
  '99a394e006e51d5ff54673a320d4e64e36fb57add9489d74d5a4075ac5886d43',
  27483),
 ('references/stage-sequence.md', '4883b83ebd88f9aed2fd8d0ca52ea6f88cb3616971a156bd2d8742ea16caf891', 7975),
 ('references/cli-contract.md', 'f229d9220609cb53cbd40b67d18b77b0710822f3be971a320bdcd576ccc09d1d', 42907),
 ('references/session-contract.md', 'bbdd3ea56de8b69d4ad9018f8967e8eb5829929171673c53441b6f55f815c3c0', 22424),
 ('references/delivery.md', '8d36e06481332627c8f46e352e1374075733cf4f3d43aef622c293fd579ad083', 4997),
 ('RELEASING.md', '08740d248e56073d8da7b4c6bf388bf1b3135833cc579292241be2d73c8dc8f1', 9678),
 ('references/command-policy.json', '43fb9a1323a8bd27d4c4e3ab3e761c741b57d942f0988fb29febddecb5ec6613', 4018)):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact adjacent neutral/ROI guidance before the native scientific policy.
for _path, _digest, _size in (('references/quality.md', '762d1cbe3f3f34a74a4299d73a405a324f08fe1155f932b8647c538bc5c8d7d8', 22309),
 ('references/stages/stage-03-background.md',
  'c6ea8fd275d8cd82ff0017d4b44a42500728f68a98cfd0340ea4653be89058f4',
  21940)):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact outgoing native-neutral references before assessed Stage 5 restoration.
for _path, _digest, _size in (('SKILL.md', 'd238ac6c5ebfa0146f8c905567fb7e17247c4c3dd4105902d9cba81a98d995a7', 14531),
 ('references/stages/stage-05-linear-restoration.md',
  '26b31bd63eeb9078d35f01452cc36c365b174d657bb2893d8f5b7940fd10f415',
  16577),
 ('references/stage-sequence.md', '87e43dd266764a05022a64346fe7a92fb1ecba7362f19e0dac2df536b52e01eb', 8246),
 ('references/cli-contract.md', '615913ee989d3d735cfea221c097781bd59bf0197f295968df79dc03df763cfa', 46060),
 ('references/session-contract.md', '5bee447dd28c30fecd1a78986723906fc684bd8603474e3493ad2feca2173d51', 24300),
 ('references/delivery.md', 'b31b752b3156496cb2d258e5bd991154593d5636e1e8d53957260b9b1c4584e7', 5599),
 ('references/quality.md', '76d88b0fc1eb7bc4083641b2642837603ac177976a09dd9fa83c525daa72dc46', 22603),
 ('RELEASING.md', 'ee0a1abac1439204bf42a2f02dc699a53dc24124ee24a1e86ad88f4dd82abbae', 10345),
 ('references/command-policy.json', '703355fa681cd256a1ea2c9b2f3a9bcb47a3b29e385009d6f0a77f7eb58b8862', 4045)):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))


# Exact outgoing Stage 5 guidance before explicit Stage 9 sources.
for _path, _digest, _size in (('SKILL.md', 'f7b25fdedd0c1828d560e7492d72adc42c6a32b26bcd3a218d4f2f2569f23005', 15247),
 ('references/stages/stage-09-stars-recompose.md',
  '00021b580e0f1553aeceff48575479efc651f11380817633e9eac7f42a560c25',
  15272),
 ('references/stage-sequence.md',
  '28b986fa851faf23a85dcb968a32bc9b9de020815653c32d84313b51036a653f',
  8630),
 ('references/cli-contract.md',
  '0bcc26bb51d95e4029b5cab24c8c2a67fdb00571d076e1b98275dfae830cb480',
  48854),
 ('references/session-contract.md',
  'a39a80083fab09757405592c93026180ba0027031e1850c9bac4bb3ca9a4066d',
  25781),
 ('references/delivery.md',
  '1924e1d199ffee477e2efc93cd9401f862369a6ee4dd9e8de057e876697023e4',
  6144),
 ('references/quality.md',
  'ddbd13dae8c0f0feef0d23f0b8600af68d5d1b65982d589c1b681b8bd8c2ca7e',
  23299),
 ('RELEASING.md', '81c6948498ef00fe647c9be283305cd62ca38f64cf88d66a930b4bb599474a9c', 11020)):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))



# Exact outgoing v11 knowledge before shared protocol ownership and Chinese integration.
for _path, _digest, _size in (('SKILL.md', '0239e140450171151d6eb9c13fb0f4a098cbce761dba23086616d307391df154', 15767),
 ('RELEASING.md', '811aa6597a446c5b18e67465f633d18b0e783bdcf493da42655de31686ae45f2', 11601),
 ('references/session-contract.md',
  '0fcfa51e3f5db2e9d4fac88c40bfa85306f72386f9ff2b295ee865f387951aa6',
  26533),
 ('references/troubleshooting.md', 'ef822c96bfaa9e6c5fee5d1e73b748783341d676a49b3542a8c891bb7e7084e9', 7724),
 ('references/quality.md', '4a5170e4f50ee62cb048bb6498456a1601931279641ac80e9e541ee2ad201a29', 23908),
 ('references/cli-contract.md', '46eb9ad608f6f42a296868762600ff5bb920b69aa6f83b6764738e7c9f5ac3ae', 49951),
 ('references/protocol-index.md', 'cc1b6dd6d0970d977adf7b0bd62a3352c7a91d6ee227a668b6089815ace2de05', 3031),
 ('references/stage-sequence.md', 'af5cb012afbf0fbfff1c97e4e426e994eaef686c71ead0b2aced9614da9cd39e', 9010),
 ('references/stages/stage-07-stretch.md',
  '69c308d02bebde7c3dbe718c0897ba33b3fd0f803046bf2931a49da03a5d9c9d',
  22437),
 ('references/stages/stage-09-stars-recompose.md',
  'd867d0a4663d5f64d1e7343de00f873c8eba9cea131cccfb13dc3cbb707fc65a',
  17251),
 ('references/stages/stage-04-astrometry-color.md',
  '3516d865db2fa75a53c96720f3a284e68e80efcdfe0cfa0bde73c170c2b8cc29',
  31171),
 ('references/stages/stage-10-delivery-finishing.md',
  '2fdf4c2a4cb9185bccb8e8653ad6a232387528d0fdda1a9ba2cdfbda72d6f097',
  16259),
 ('references/stages/stage-08-starless-enhance.md',
  'cac89ed10698a2ff6f0aa0c2fcbfe6fc1cb3ccaf4beeb1a7cdfe04106193d48d',
  17995),
 ('references/stages/stage-03-background.md',
  'ad9fe15c50c7fcc0523042638dd603b6d39c38304f457ae3e4ddf07997b320e7',
  21953),
 ('references/command-policy.json', 'd45f36055617e5c992eb8d3d8b7b52b738cc2ccf439091898f077ec49d4b3184', 4099)):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))



# Exact outgoing v12 guidance before AI morphology and scientific measurement handoff.
for _path, _digest, _size in (('SKILL.md', '4aae8aa292f0dc7b074c3c4e21211a8cba403c41829381de8d84d11c2b593d7b', 12454),
 ('RELEASING.md', '7a23c6347a23d2f4336d159991790c763d64acdeda909af06a548c5cbdda9267', 12421),
 ('references/session-contract.md',
  'b4ddfb37c8dbde76436407bf3eab4ff822e7a6215995d1fcf98b22220fc80d27',
  27659),
 ('references/quality.md', '593b844bdf7a719a58a82b60b767a99bbd393bbb4a485240d62070404e4c24c1', 23911),
 ('references/cli-contract.md', '8a8380469fdfaef54d83093c28a2f4dbfdaae47c686f99988d5284cb24463953', 50555),
 ('references/delivery.md', 'de3e6cc0b5f8f4b666ee948fd82774b06faa3e18dbd6b092a3647e4f22b3b4eb', 6569),
 ('references/stage-sequence.md', '499ec8f7e29922d1dccc1a28132c0b0d7af33348f4ce905670223bce0f888a4d', 8410),
 ('references/target-morphology-review.md',
  'a16fb18e53c175f1998db76dbe7a75235e5943ec43be8bc8ad541e0b69d8af10',
  5656),
 ('references/stages/stage-07-stretch.md',
  '57c16111d5baa0476aec87f6a763cae4abdb1319a61a79f24a6df6e4c2d6b2ed',
  21613),
 ('references/stages/stage-01-input-inspect.md',
  '43600f32b9354bc80c4922795df82515e0463ccb265dbb9bb20287af944972cc',
  6153),
 ('references/stages/stage-09-stars-recompose.md',
  'eb27bc66a965c5f61223b4a3db95d15cbf72711508d18793522e31104ac3c7ad',
  17245),
 ('references/stages/stage-06-stars-separation.md',
  '1b2394b71532cb2f017d6c52249db49b0bc9f71e8617b5f8aa7d263a1103761a',
  15983),
 ('references/command-policy.json', 'd45f36055617e5c992eb8d3d8b7b52b738cc2ccf439091898f077ec49d4b3184', 4099)):
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_path, set()).add((_digest, _size))




_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('73c7483fb0d05e631bf985845524e44748c78a7591b4ff08402f46be65c3b031', 13922))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('8156663e3e50009ad7490a2d67f63d2f80266a83061ab871a91517196c0b3d3a', 23835))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('7ab2266fa2c41994443c738cb199cdd59080065cc5a1abca5fcb3b87f159fd64', 13839))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/astrometry-source-preservation.md', set()).add(('6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7', 5736))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('be2482ef973c9443e1a5fa6c905b11d15dc6d91a615c4dbeeb0b1fc457ffe8be', 51608))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('d13776a07016ff36cf18600b496f5e0dce973d8e38c62df5ad8a80498581ac81', 7490))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/manual-query.md', set()).add(('9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124', 5237))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('7564810c332ef62e5672f5e5e5d6c9b388808c38f3d3f763269dc942bfaf0aad', 2957))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('c4abf2f6e4678197bffc149920493c7585277181676800725cd4c31b7f3d1f45', 24989))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('b0bf9eaa53128a7d8ba2021794f8e445eb0bddb43b97cd645f4bb3f819bae896', 29141))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('630a020497ea3d45343e37b7675beff6585eb760271f61dd4138aada5715df9b', 8318))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/tone-adjust.md', set()).add(('7a1c4d4a2e268087315b254dba802280bace88177adc694927156634a9b8a829', 6915))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-manual/MODIFICATIONS.md', set()).add(('7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c', 1497))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-manual/NOTICE.md', set()).add(('431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513', 1293))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-safety.md', set()).add(('864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b', 7624))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('78779c47a0ceb2eff277b76be3a02c8fdb393b04c61c32668bab619ca2f6ae85', 8415))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-01-input-inspect.md', set()).add(('24cda2d75f8bbc5845a71affc5817d23648a5e1c2085932fee7535800d548507', 8541))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-02-geometry.md', set()).add(('0bc566a37f00e8618451fe4358f5405b9368ac51ead561ed08eeeb0c3ba56e95', 3745))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('776bacc1127c8c05f3d8862ccc81226c53a2ac011f73c1de898b8bcd145a7975', 20833))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('d1fd672d41e81986bfebf664ac82bdff5537a4aeee1a60ae014dc4814585463e', 27624))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('b564778c390f9d6e4bfa4420d940c95440546d66723dbaca82d86810b15dd420', 19812))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('8d01c4b1c236b6aaf99b487a762529107b8b4d1d5aa1cfec7866221673dd1afe', 17006))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('cb9110ea4b4cf1e5a91558f05238ddca017c95686d9a10a4931ffb091505c26c', 22923))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('2dd2315acfe1d6aff6c82b0c4a2182df626f13c43f5b02e62ee84a47b1e0d0a3', 16637))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('454abd59409f268edf9ba5a22ac45c21063f98a1a20097cb4bc6b706b8074438', 18515))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('56159c044b644da83c8715e31be54b1a39fde8de950ce33d9c97c54d087ff6fc', 8186))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('a45078fdd3c57138e20cfd76242ae9c9d7e0d3b6d6acf8a248d26274bb402127', 7943))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/troubleshooting.md', set()).add(('2c153148e2d65c3d7042904bd7fb23dbcb7a363ea6e6bbd4c2e5a3192508a699', 7719))

# Exact outgoing v14 guidance; immutable historical replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('dabbd509abda72c9cdf441f5a73ecfdd0bcb7a445e9aed364ba5c3a12679d440', 24286))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('8f221cd6bdd4ec9d96d568177e8f94a3a5976745cbcb25fde423cf60558f1a4d', 14081))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('3ef9937aaf30b941dd4ac367426afea631e9caa41c6997596c298d72f4f95e6a', 14975))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('f51605e2a9ffe2d2594f35beb2c4f795ca40db17fe2572c92ca8bcae39002d8b', 53375))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('c4abf2f6e4678197bffc149920493c7585277181676800725cd4c31b7f3d1f45', 24989))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('747686b2d1f8b7eaa4c61c7452fcd8668f8cb620567b9bdeef6146b0d5bbf72a', 29941))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('a9fc8f9e4b0df4d78682398c552bafff6b5c1cb9937c955b70ef6ba98077ba53', 8996))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-02-geometry.md', set()).add(('da02dbe7e37b01744f5a46da420f53e1bcff69e5f2ee57fa43d2ebcc1ba565d2', 4166))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('76465aa5db1a7eadec432c6d5c01fb6e8452954e9fef9429a620a83469d52362', 21894))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('b564778c390f9d6e4bfa4420d940c95440546d66723dbaca82d86810b15dd420', 19812))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('ad17622b1ba64e3457f1250a2d3cf7a8b8eaa8c9d6094e58a665b49564f6be3f', 17640))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('a437861ad10b5fdb6ac22a28e7145e782a122b132caaffee5e70bba249afcaf6', 23433))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('454abd59409f268edf9ba5a22ac45c21063f98a1a20097cb4bc6b706b8074438', 18515))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('a45078fdd3c57138e20cfd76242ae9c9d7e0d3b6d6acf8a248d26274bb402127', 7943))


# Exact outgoing v15 guidance; immutable historical replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('30f28db1898dc635e459d4ad9bea928b3401bc0212b2ff0cf4aa74117825c23d', 14720))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('519d373b0d43703b216e13847c0aa5fad6ff18f8877e3ea3e6e6faaa4ba86651', 8549))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('e7274e7fa18c24ba830b56a3eeb8b9cee46a387c949d6878e02937c30658889c', 21280))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('7564810c332ef62e5672f5e5e5d6c9b388808c38f3d3f763269dc942bfaf0aad', 2957))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('bcb9fd5f59bb502e33640fc96107f393f536b9d00028f62f13b3c87687cd47e3', 28829))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('fd32f6b87e126315a78066d62d320c4a12f5222d101e1cea81064cf8b1d80fef', 48341))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('49e6cd5bf0f62409353b70ffe86e2aae61adde7c0d80e791ffcef69b5ce54fcd', 24705))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('de18c98d8860637efdffc9752d8ca6766b3e9a590e0364fd11e62d3046803926', 14315))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('1ccba65f11263fe6a02008eed76669f2c4d87e123d9cda93afdfa208170e5cc2', 7938))


# Exact outgoing v16 guidance; immutable historical replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('f5a4ca6254a31fce0908332933481e4d613605faa91c8e25a76bc928d0152639', 25206))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('c3990a595a3ca3dd53cf92988fc4558ae4c7a4a9d552bd22992f14d31dbf06ec', 14868))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('21d2a84009aa866f00f823da1319d34192267e22f77a6b449e402c4cfb19fba1', 15184))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('c13f5fe0bc9df6e3b77c1096fe53cca017180cbb2868c689e8bc7d0ead50c942', 48347))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('6641946365cd96a2a2fef03c54825c7213e1725e809a707da56ea2a3e8018c56', 3063))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('9173f1833f77d398da0873a50b06aa0d1914c52ca40211b81d7293c6b17acfea', 29340))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('7f1dbbe103d3b236716bf958692f9e7078b8fbef9ba169ecae9d29de5ba0dc39', 9387))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('d1fd672d41e81986bfebf664ac82bdff5537a4aeee1a60ae014dc4814585463e', 27624))


# Exact outgoing v17 guidance; immutable historical replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('5eeef9d84fe014361eb5f01f9fbe75cb3f33b1a1ce9af52708d69e887b1cc9fd', 15403))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('32c498a4bde2bfa7f661f35f0f2c788bf8396158d2d7d80cf8fa43eda2096cf9', 25703))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('c431f93c34a465387663968ed2e5344da29b4ac0f9619a960340b79d4ce4b8c9', 15603))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('agents/openai.yaml', set()).add(('179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5', 345))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('812082b3db4836400a0cf5a63954bc77f494e57c20f95333a64e3f71ad48a912', 35302))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('a1bafe7716423d9f6ca24b95568f8d0e741a5c7659485836e1b8298a28d412ab', 19634))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('f6a1392de8ece375edbd0e5f7ac34862400c734ef7643a0332ad881c4ddedec9', 17550))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('d39b7d259a5592257d7e33f25ed39e0787d49ceb6f35e2db5874b9fba1d1e1c3', 24973))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('cd534cc4769cb6fd6b8711da46edc5f222b64c989e2bd0adf2214e58550f7a08', 9453))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('7384c122e5d9a88528e747944c2244aae2a0d6b84dd930f7010e7ae844e76530', 29772))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('ba307044768ea142e374a92496e7caa312a03b1f4a942ba4958ce5e4fb541f0d', 48761))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('30e0e204df331f06d8478d478e91d2f17e8a96f822213b17585f4120fad5006e', 7453))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/starnet-linear-source.json', set()).add(('142948ab4f73ad4c6e2a605c1859abd740e78d36b0c65e0899fe57912fa7b9c7', 1099))


# Exact outgoing v18 knowledge; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('f6c69d6c58a51729c2bb24bbcd37a64e7523157e7d7157c7d673a9f7ed96a9e0', 16371))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('fc163605aff9c3af52a04c5a51c43962c3f339ffdfd947e06b3825712efc1196', 26337))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('release-files.txt', set()).add(('ea978f5ce7c33789ea0ff3b939373ede3a236a5e8b623c6ba482377d9dab1525', 2165))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('fcd49281d0176a3383daf93e80971a042e11cd914f3d4b8cc1077bc941eedb21', 16038))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('1730e01fc96f6d428bf57a9918a86f1cc842f1a2bda2c400a9c20f278d71f38a', 30790))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('d45f36055617e5c992eb8d3d8b7b52b738cc2ccf439091898f077ec49d4b3184', 4099))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('504a25859063ffb2f01e40ee3cbe5855f0b85ad132eaa7c3d2d4a7b159278d09', 49553))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('3e40ec6cca30a58af54a77c0142345fbe8cdff1419bb337e7b3d74e2048bfcca', 9959))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('56159c044b644da83c8715e31be54b1a39fde8de950ce33d9c97c54d087ff6fc', 8186))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('2dd2315acfe1d6aff6c82b0c4a2182df626f13c43f5b02e62ee84a47b1e0d0a3', 16637))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('630a020497ea3d45343e37b7675beff6585eb760271f61dd4138aada5715df9b', 8318))


# Exact outgoing v20 guidance; immutable historical replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('.pytest_cache/README.md', set()).add(('73fd6fccdd802c419a6b2d983d6c3173b7da97558ac4b589edec2dfe443db9ad', 302))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('097120a72be9eed8ee4429244f9f2a82231ee4cca7f8128402722123517f010d', 26914))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('LICENSE.md', set()).add(('88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7', 621))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('1897e6a6cfbc3d051f521ef2c938db79b622dd043f223c31dd5297208c1e006c', 17127))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('0cfe10ea269ef99da682db4da948e1860d1aa1c819596fe0c1ebdbccd37b7d47', 17263))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('THIRD_PARTY_NOTICES.md', set()).add(('486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11', 2669))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/astrometry-source-preservation.md', set()).add(('6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7', 5736))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('a7d66419b240a69805f46f72374c58c7e425bbf507df8e7723a135e2049a021b', 50171))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('6e20da98cf19448bd27adf5af41940c1a0aec5a709f205797d354df4d622cc9a', 8304))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/manual-query.md', set()).add(('9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124', 5237))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('ae7c6ce2be2d5c0ab9e42a898f4acf961bbf93f4a6a5f464f707c7dd0b5fdf4c', 3335))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('468c5a039b4133acf74f3ac68ad2e3357414fd9a73e3e481831e86535bdf93ff', 25932))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('7350545b1da5a79edd6339f2cc9ae6791d7439ffe2cc3c1d92065f01702a5f0b', 31884))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('43ce97e32f81bd8c86b29a12c749816b9fd91c3381389e78b4a6d51153a1a9cb', 14040))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/tone-adjust.md', set()).add(('7a1c4d4a2e268087315b254dba802280bace88177adc694927156634a9b8a829', 6915))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-manual/MODIFICATIONS.md', set()).add(('7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c', 1497))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-manual/NOTICE.md', set()).add(('431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513', 1293))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-safety.md', set()).add(('864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b', 7624))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('2905b8b33d8d9d92049596aae5342395008177bc15042994b0d7a54903ec320c', 10489))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-01-input-inspect.md', set()).add(('ebef01b704f4df4067639374e7dadd6e583f2de401f771a202e1da78d62a0b10', 8598))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-02-geometry.md', set()).add(('a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e', 4024))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('c038f124397e70f8696ab01df89d4648a4340f67877307d289c9c876d39f2945', 26164))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('cfe8c1eec857e0103e53f1188690e47e62db6b3cad85346381eff77e4a9178b6', 37243))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('62560012e8b67bba8c63c7679b1f2c9f542851d61a15f7ec60465b7d458c3287', 20471))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('1fb68e4e3639154b24f92692989fed4d12a0a958e57bdfdd5eab1cb507307b66', 18270))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('6f2244c775c97ddac90e2fdc749fa8e16ba4cc1e04fa43c95c16dbc35e554859', 27966))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('50c641174cc5871259d342afc8f74113a7361ffcab117d50b9b9e8607d736665', 17115))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('429a6894df969a5fea9e8b22b5d91e46063af46757c06a443952458573347a83', 19407))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('9cda59d04624630545031fa869e93d86464146b8abe2c91e20d76718126a7e11', 8446))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('7ee0b03df9b8bb884d4b8b1c79792ca708302aef08c7dac31f8947072660bc58', 8028))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/troubleshooting.md', set()).add(('09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d', 7866))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('tests/fixtures/neutral/README.md', set()).add(('3d3f5da1d5d1832a8d078bb3610df66fb358a4cc9aa7e99e15070b063441177d', 7631))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('f4ff7bcc01a3daac066fcbcef038e3d953496a1b3771bb5720cd7e8a3dd8e479', 4157))

_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('d23ed3c5fe7235e06c92f10c8e9bab661d5fe25e8e3487c4c3b2385b8ac2d309', 27266))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('LICENSE.md', set()).add(('88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7', 621))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('d17f20a5c8506680e23ae111fd60b2688f16f5704bf9a1bf8b0ad359ea4c945c', 17725))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('ebfdc213924aa9ad47b8ec49038b6afbeefb7298d4ef88ec780e1a5206f974c4', 17589))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('THIRD_PARTY_NOTICES.md', set()).add(('486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11', 2669))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/astrometry-source-preservation.md', set()).add(('6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7', 5736))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('38e80f4260edd67bbddf35c85bd6ad73230ee257fc9b467724f4f082c04fb850', 50520))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('35809c1c33305a2aa6e9e5d47a3d5c984bbffa8cf6c58361d870656c4cf44944', 4191))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('e0fe283926daf98759a6ee2bf1d04f50d7ff674aeb5cb5f84c0cabe69ec64a0c', 8592))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/manual-query.md', set()).add(('9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124', 5237))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/protocol-index.md', set()).add(('edea5d4074d96d365a6730590c8c51c61141cc4bd5bfd910f3189fcdb89fcf71', 3551))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('efa6b00db888baec3271b04a8ff9a5603a99bbb933253a7d0b13f8a6c2b5e2d6', 26372))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('eb8742b7ccbffbbe5f7f7a61fe6fd7687772ab3236f7c3f94f810ab32d5ee902', 32323))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('43ce97e32f81bd8c86b29a12c749816b9fd91c3381389e78b4a6d51153a1a9cb', 14040))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/tone-adjust.md', set()).add(('7a1c4d4a2e268087315b254dba802280bace88177adc694927156634a9b8a829', 6915))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-manual/MODIFICATIONS.md', set()).add(('7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c', 1497))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-manual/NOTICE.md', set()).add(('431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513', 1293))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/siril-safety.md', set()).add(('864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b', 7624))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('720b66012a7ff4b9b188b1b337d17756e12da7248b5f5ebb9e416e0645d2cb52', 10860))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-01-input-inspect.md', set()).add(('ebef01b704f4df4067639374e7dadd6e583f2de401f771a202e1da78d62a0b10', 8598))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-02-geometry.md', set()).add(('a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e', 4024))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('c038f124397e70f8696ab01df89d4648a4340f67877307d289c9c876d39f2945', 26164))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('cfe8c1eec857e0103e53f1188690e47e62db6b3cad85346381eff77e4a9178b6', 37243))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-05-linear-restoration.md', set()).add(('62560012e8b67bba8c63c7679b1f2c9f542851d61a15f7ec60465b7d458c3287', 20471))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-06-stars-separation.md', set()).add(('1fb68e4e3639154b24f92692989fed4d12a0a958e57bdfdd5eab1cb507307b66', 18270))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('6f2244c775c97ddac90e2fdc749fa8e16ba4cc1e04fa43c95c16dbc35e554859', 27966))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('28e8f68a274596f8eaa8c7b2b7e34216175e21ed6cda4e517bfaa1117e8645c9', 22079))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('429a6894df969a5fea9e8b22b5d91e46063af46757c06a443952458573347a83', 19407))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('9cda59d04624630545031fa869e93d86464146b8abe2c91e20d76718126a7e11', 8446))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('7ee0b03df9b8bb884d4b8b1c79792ca708302aef08c7dac31f8947072660bc58', 8028))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/troubleshooting.md', set()).add(('09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d', 7866))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('tests/fixtures/neutral/README.md', set()).add(('3d3f5da1d5d1832a8d078bb3610df66fb358a4cc9aa7e99e15070b063441177d', 7631))


# Exact outgoing v23 knowledge; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('7f251392d0fdb9e52f90f19ce366ef7378f86ab65e317dc61be67de8a78402de', 18599))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('9f476e2ddd3922aa74587ab3c38ed80c49d921dcc6292ab4347f02431f6a82f4', 28120))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('a4d836654426b49890cf42369c03a8dedea59d8a53e328058a997c1d9c7683d6', 18951))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('c1b4d97350c11c61126bb23967645aa7d4175905ddb5a6b76723dd1314ae9176', 51434))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('3541170e3ad852fe217b47aff05cb09740da3933630e15527b85d9cc22307109', 27339))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('eee96664088caf5980d7cb97895531b38fe02608410b3fe8381180e278e08d93', 33393))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('43badd5669598be8c0fadb49bb049d87338502850abd1371ad13f6b48b5be311', 9167))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('45666ffdb9236886dc81eb4d18e48bb5d06c9e5d47379378eb0864822c316eb3', 41862))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('d1162d8fe6546441ea5b97f670d3193660e88f2fe516df581463b41f6b6bfb6f', 19850))


# Exact outgoing v24 knowledge; immutable receipt replay/finalize only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('bcfe2935ce57cf5b244ef36d9e795268c1ff91d9d05315ee27b196ffd3205a58', 19048))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('41326b8498745b1eeeb5bfdb750dc36df5f2a4b6e502ec4dbc824dc8e254af0d', 28733))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('RELEASING.md', set()).add(('97e10b16e43d17ebf404693eee1763bdd46c5efae589e4fc0efb2bb681f22be7', 19405))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('b57f2f647275000fd599bfad6409dc08d752bae3e9b9e31a95ab81d977636c81', 52108))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('62c73facc70a29377faac10d547f821245a4e7d0a1ac961b63c950dab9c8c531', 28101))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('ae7f470f26950df63dbc51263ab2aee5b0a28fe8834ea85f0280fbf54ff5cf42', 34041))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('f2aae9691cb8d5643296c106f577cd05528bad1def92459c0b4c6ca22c836efc', 9446))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('a59bb5521aefd9edbf29ddd6e4f6b0d2b66ae44609f5d9190320ca367291c3bb', 43855))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('52941f1af0a1bdeadf7aad3647d4ba84b7d6007ad365cbac2ab326445b60f602', 20514))


# Exact knowledge before Agent-selected Stage 9 strength guidance; legacy replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('0f18a7f19d98254f6974a5206f7bb2f9c89a464765781a350965f3fe62e5f22e', 19694))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('0c1062d310e39d9b0565dc5ccc5eba634395e9be0dc9b5d99dad315b4b270e46', 20813))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('391051af89688dabf4877f1324cefb418cb06f71a2e23dd92ce546037fc3553b', 29952))


# Exact knowledge before Agent-selected saturation targets; legacy replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('bd8baeb43963c2b397ed954a28a37eaa4b7d10e7b4437f5df490db99916513a3', 20090))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('43ce97e32f81bd8c86b29a12c749816b9fd91c3381389e78b4a6d51153a1a9cb', 14040))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('839ee9690486bd45ba734d9f6ba8debb797de5d3c6aa18d445d84c5dd436a1b2', 22514))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-10-delivery-finishing.md', set()).add(('b6e387cf32220c6edc62835a0fa882765cb0b787aea598c2bb6b7e1131ab3002', 8823))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('2d25de00727a3c4ad6ac2686856e9d2080322c2d4fc6db5de0ffcfac875a3d4b', 30488))


# Exact knowledge before Agent-proposed Stage 8 enhancement; legacy replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('c16dd70916d525cb9adf00ce7308b4eb70936c24961176deb937ffcc7ac6a804', 20303))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('06b5244ed15d4d749071d4bd6e2ac3b9b5ff84c4ee6d4399cbca787ad8fa70d9', 22786))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('8bba835c63bae7ffc1c0befe8e50503e6081b49cf2bdc6ef7e531845625250cb', 9792))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('32e463550a6edf91bf6fb3e00e9edf2de63b59f205bb8594ab680a0950129b96', 31003))


# Exact knowledge before benefit-first stretch; immutable historical replay only.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('f42affb4f197702a4df1a09009b0adb46827acffb907a258113543fa064d0942', 20503))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('15ff051507c7cca7fe4b808894a0ae7acef9ff5dd5bd73ebc52861bd9380a0d7', 31569))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('7b93773685c103502f6ec008c170f7a2eae6dec0f8de195923f5a6e0c52bd2e8', 48995))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('b43a97d59a8f768d4094fb324d197c18713fc7b5225a512c3fdba35eddb6fba4', 11093))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('fe4cf7f67b4133ee567f150f1e7c0f395183e99152e0f00da9f4882cec053b52', 28767))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/tone-adjust.md', set()).add(('7a1c4d4a2e268087315b254dba802280bace88177adc694927156634a9b8a829', 6915))
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('75e6a57bf0c38d8cbfc789db3463829f3e1d30af457e4c45d36c6f31a0b0c6f2', 34600))


# Exact morphology reference retained in the immutable v23 snapshot.
_LEGACY_REFERENCE_FINGERPRINTS.setdefault('references/target-morphology-review.md', set()).add(('fad57a801e0966a18dc43ecb5f131328578ad1de6d0f84d4e6b9658ef972b49d', 8473))


# Exact outgoing guidance knowledge; committed replay/finalize only.
_COMMITTED_REFERENCE_POLICY_HASH = '7346a590927e4e5d0ffb439da2c19a593f383fa53aec27f9ba75ab1d0e4ac0c8'
_COMMITTED_REFERENCE_FINGERPRINTS = {
    'SKILL.md': {('b7afd18283ae7fde31a6d2d2af8dae9c2c87f8f5fa505eb9579a1cb7d6bd9825', 20992), ('d46e12b12b93abc5ef7f56e21d793732ac485379ecf4f73b82cb90b90b159d55', 21692), ('fd22314d9753ae76b206c6852711af5e83aa598dac480ca208d1015784ca09ae', 21985)},
    'CHANGELOG.md': {('2c1fbd22b57ee3f3fe71d0411a2bb534595e38a64c87a6e786eb75b5effe7b74', 33135), ('6383e7974155be4791e6602b35a59e9adbc1de8d202363301b9fe1a4a134203d', 32403), ('aa04dcf6dba24dbff3fc91b709a5e5ffa343df782548e38536dc092b8f19d65d', 33892)},
    'references/stage-sequence.md': {('04bd98a1b11def9a619ac7da649fbc6c83612f623c1309dd8d3209ac2323001e', 12066), ('1e37067bacf826cd7c28d5336322e6c38394726eec3a86656d80cd62df702638', 11680)},
    'references/cli-contract.md': {('26e1bca254c2575e75ecf7a46e6ce8f0c3f4853cefb6f10f6aabc9f736f7e716', 52790), ('7030cb1e85c39efb8c632006490804be636528e09db844462704252e4179681e', 53056)},
    'references/session-contract.md': {('4723566b7e4f775570e2f802275d16ca6f80b167f36687f3657b5efdfb1445a3', 35919), ('6755a7545c8712ccafdbdee0f695cd93547aeb4fb3331d9a9b8607f006847a6e', 35260)},
    'references/delivery.md': {('9263ced212624d78d9826490e270017081491ec1869d4d1b8630b03cc5fb6219', 10804), ('c6d2a3f2a1244bbed23df4430e5125a88cec312a64b25d693140b8c55c60ced0', 9966)},
    'references/stages/stage-01-input-inspect.md': {('ebef01b704f4df4067639374e7dadd6e583f2de401f771a202e1da78d62a0b10', 8598)},
    'references/stages/stage-04-astrometry-color.md': {('cfe8c1eec857e0103e53f1188690e47e62db6b3cad85346381eff77e4a9178b6', 37243)},
    'references/stages/stage-07-stretch.md': {('5286c1ee44327ea2b2545da2601d192f846276c389eb62ad784f8edd31c61ac6', 57585), ('74e846643e5845370347b9761637b825f7bb0939eb8592a85062b05aed24c4fd', 61971), ('81c6e8cf590a5d03368b0e8fda4ef07ab0eabe25c98a2c82814f4d64d63710fa', 58081)},
    'references/stages/stage-08-starless-enhance.md': {('1740191fcbe317bd44530d3a4d419219ff6e1aeeafe7482c6a4099558a5000af', 23907)},
    'references/stages/stage-09-stars-recompose.md': {('dc5721213215bda8c8fced92663b6e2d95f80f8a032c54e83cf5c4b8438aba66', 22025)},
    'references/stages/stage-10-delivery-finishing.md': {('4695ed2320df3694fe1eb45a07cab9c1dd299f9439b0768851dc6f6150da017d', 9164)},
    'references/target-morphology-review.md': {('cad88b61d7c2d77390614c37c5475c36c37be19f36e706df991b6d37cedf8af2', 8511), ('f680153069bb43e88d779d1c947a5d4f14ec4044593c9514054539876e882565', 8769)},
    'references/stages/stage-06-stars-separation.md': {('1fb68e4e3639154b24f92692989fed4d12a0a958e57bdfdd5eab1cb507307b66', 18270)},
}
# Exact outgoing guidance and review schema before structured Stage 7 assessment.
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('a29dbc3f6029f4d2efd6693b3366d60cd9ec99304373ed33d6341c89bc25d702', 22234))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('6f93d70f55955de6e431df2240ff5641389c64adaf4a8cf1612a232d745589bc', 34619))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-07-stretch.md', set()).add(('a3e4bc2b43857df466bff4ec4fd1068376d46e4e34c24e2193a551b564dfbb87', 67689))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('cb9783fbc5a99e09c3346dcd1077d89227aab8db1c313d684ebe30240173e6c5', 30505))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('e22c2a1f6ac61293819c6c39f560edcfe593246086bf31a6ea989663c84ed0bf', 53374))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('d3bc4f7d120ed0983ee2b8bf3e49337bc2b328680d7c158f478d2a8860d2934c', 36558))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('62abe06ba0ff642930b51a0792e22dfa1e9a952368aa1e2b8c741bb3ec547511', 11252))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/review.schema.json', set()).add(('2a08822eec652dfdb83adfb5a7fb3a1781d9955c9304c80e3401b98c0528b846', 3105))
# Exact outgoing knowledge before protected local finishing.
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('8cc1962b265fa09723dc9001e298e18660078935c97bc81ce2afa3abeb63e215', 22856))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('CHANGELOG.md', set()).add(('853414f1de4e782827998cf0a17e08c428903ee48eff77262c606083cf87dcb6', 35337))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('b5c944f25742300f7ff31ad64779ef42290b30b3e3cf26bb2db68e39224335a8', 25027))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-09-stars-recompose.md', set()).add(('b91cd98d3422082fffb0e89b92fb584955e1e6e6400f322f496f8526623e720e', 22445))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/tone-adjust.md', set()).add(('b44b499e3de48459eb867a455ae00031ea1f666c554dfee8025007672cdedfa7', 7120))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('9d48c38668ae726b226768f46383142695a124e554524a926a6ee689bc400bd8', 15037))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('db37a10da4477e83f3771f9d023202fbdbba5cbf841aa1468b9533fc88376f4e', 54030))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('52b03bd03161ccf3be84a93dc254a9c5d5fdd4d304160c4c0c1920b75ffef6a4', 37140))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('986553dabb449a01f0e59c1f6bc74a262ec80d664beeed8b661df16e2228906b', 11678))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/command-policy.json', set()).add(('35809c1c33305a2aa6e9e5d47a3d5c984bbffa8cf6c58361d870656c4cf44944', 4191))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/review.schema.json', set()).add(('48d08b17e923a10aa74ac745bf570a8f07ef5c7198773ecd963eef48dc8c3bf6', 10105))
# Exact guidance before independent linear background and neutral assessment.
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('45966a7083d1f8c6c45fe26dd7102729bf578be1689a13200941507d375bac87', 23380))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('22a48f69a92852532f5b8327194a4a940e2c42e9c059e40b693a6debb8327998', 12101))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-03-background.md', set()).add(('c038f124397e70f8696ab01df89d4648a4340f67877307d289c9c876d39f2945', 26164))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-04-astrometry-color.md', set()).add(('a4df417c1b2bf3cdea0aa6c06d425de6f63bd946e34d17517a10fb8725d228ea', 38096))
# Exact guidance before subject-first enhancement trials.
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('SKILL.md', set()).add(('9fa52104fc956fc6a191178ecbcb497bfd75b5e4861830e5d4f8a45cc3b6f035', 23668))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stages/stage-08-starless-enhance.md', set()).add(('e1d9b0461b28eaec32f8a7d88b03aac48707b670c7d5225f1ff719a191f3d6d0', 26827))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/shared-protocols/color-finish.md', set()).add(('f313b23660ba4c48e233e78620842d511ced3833dac070bfb403c66042e017ac', 16776))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/quality.md', set()).add(('2f496ae3fef177e561f01414bf19d70940520aa4c4d308a7b1439f0834016e90', 30995))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/session-contract.md', set()).add(('ad380aaa97d3ac9a40193c93aae7c2fb3bc81370e3bab13ee738a618bd606863', 37708))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/cli-contract.md', set()).add(('105ad8b28a271ff1403d38629e3f49f4ee80cca9255616cea46ea38fc0b88eb2', 54616))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/stage-sequence.md', set()).add(('2587910fab786982af2f90f1fdae02f1f62753d7f0caa40cb4af8c91c9f19a26', 12521))
_COMMITTED_REFERENCE_FINGERPRINTS.setdefault('references/delivery.md', set()).add(('2b5c9a77967a8e15eb73466b01f818d085a93537793dd2b91ceedf2423b0cf79', 12114))
for _name, _bindings in _COMMITTED_REFERENCE_FINGERPRINTS.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).update(_bindings)


# Complete outgoing knowledge for immutable replay, scoped to its exact policy.
_PRE_DENOISE_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'CHANGELOG.md': {'sha256': '842275811abee5e3563645c1838c7935c2f9a5ba100d61d4da07f984ff8bc58b',
                  'size': 35883},
 'release-files.txt': {'sha256': 'f9faa332878046002bc4f4f26c06e9e36f405b36b6484f60db8b70bb9f7a9283',
                       'size': 2382},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'SKILL.md': {'sha256': '7d3c990925d931b978369909e608d0e11565e90dffbd8a1bd6f9fd789f21de02',
              'size': 23951},
 'references/session-contract.md': {'sha256': 'acf5f6c11562cc04f2719ce0793f0a1c505719213cdf6400f3dc26b5966e374e',
                                    'size': 38347},
 'references/review.schema.json': {'sha256': '48d08b17e923a10aa74ac745bf570a8f07ef5c7198773ecd963eef48dc8c3bf6',
                                   'size': 10105},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/quality.md': {'sha256': '10b7d88ff0b798deafb9e3bdc1a96f8499015125d90194925523a9cb86a4e3f4',
                           'size': 31331},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': '99492e246fda73031d6cb4a34e7d077f8205f5fa702c01903d1c4295ef1d50c2',
                                'size': 54734},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/protocol-index.md': {'sha256': '30caf0c26544da0efa338b1049079674abe93b2bc9ac5915ba9c9f20eb1e4b5a',
                                  'size': 3798},
 'references/delivery.md': {'sha256': 'e5d88ade74e4f260c92febfe6126f7ec659360dfe11c8c683369765c1f27e6b5',
                            'size': 12449},
 'references/stage-sequence.md': {'sha256': '09c6d1aea4800ba7d8b6ea1a66f315796ccde187057335447eaad6c1706d9a1f',
                                  'size': 12946},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/target-morphology-review.md': {'sha256': '21cfcc2acc2179e0790fe099dfb65e4bde526d241b5a2b18da7d8600d6a7090a',
                                            'size': 8962},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/stages/stage-07-stretch.md': {'sha256': 'b89489de1001ed1fbbd95fd66ffa1feb8cd58d9163e8bd51592459166da71118',
                                           'size': 71223},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-09-stars-recompose.md': {'sha256': 'f846e8adbadc8bd3d1dac7d75bcc0444e152bd3d5da0e7d2e6041e8fc549a9ce',
                                                   'size': 22764},
 'references/stages/stage-05-linear-restoration.md': {'sha256': '62560012e8b67bba8c63c7679b1f2c9f542851d61a15f7ec60465b7d458c3287',
                                                      'size': 20471},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '0f0204d3b003136f9f934515f3ba64efac43ca214db82ed4114936218aa53094',
                                                      'size': 9536},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-08-starless-enhance.md': {'sha256': 'ae8bcd6c0b5fc6c1579f066035e1c7fbcbe8ef9fe393bf22609fad2ff942ac4a',
                                                    'size': 27244},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/siril-manual/LICENSE.GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                                 'size': 35057},
 'references/siril-manual/aliases.zh-en.json': {'sha256': '191f702716ef2296f5a0d0fcbd20d23c1532bd178e708cb63eab36099f1365fd',
                                                'size': 10091},
 'references/siril-manual/commands.json': {'sha256': 'c025149d2d68868d3312f9d22d128d28cbc45845bbbc89e60d6d1f63e8fbcbd4',
                                           'size': 225462},
 'references/siril-manual/files.json': {'sha256': 'fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce',
                                        'size': 185274},
 'references/siril-manual/manifest.json': {'sha256': '5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d',
                                           'size': 3063},
 'references/siril-manual/LICENSE.GFDL-1.2.txt': {'sha256': '2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4',
                                                  'size': 20394},
 'references/siril-manual/catalog.json': {'sha256': 'a509e581fa841a7f15ab291832414648f4ffe9b150166e0ceb0c2f5beb3fa21c',
                                          'size': 1405328},
 'references/siril-manual/image-selection.json': {'sha256': '6bc7d65f4198fec7fe76f23c2aab7e20a48e4584fc30fc4e052e97d59945b878',
                                                  'size': 93948},
 'references/siril-manual/MODIFICATIONS.md': {'sha256': '7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c',
                                              'size': 1497},
 'references/siril-manual/NOTICE.md': {'sha256': '431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513',
                                       'size': 1293},
 'references/shared-protocols/tone-adjust.md': {'sha256': 'c80c3ae73056a196af3430a0954b2a50649ec3ffa292e18131daca10d1e5728c',
                                                'size': 9382},
 'references/shared-protocols/color-finish.md': {'sha256': 'f1520dffdebc5f09939e8a665f01613808d59411b770b30cdeee21f0d89ba21b',
                                                 'size': 17712},
 'references/siril-manual/source/doc/astrometry/cats.txt': {'sha256': 'f6b5dd45cbe6bdf42b686719ec510292b18c325a12824088b009edd7bccd6fbe',
                                                            'size': 958},
 'references/siril-manual/source/doc/preferences/getA.txt': {'sha256': '0b204412feaf7a276333b2b5b2bf641ed3cb046066f096c0a15ed1b0c425a7dc',
                                                             'size': 14586},
 'references/siril-manual/source/doc/processing/pm_im_functions.txt': {'sha256': 'e77dc8eb51976d527770551ddd46154f8abb70b1994fad182e457233e74672b8',
                                                                       'size': 688},
 'references/siril-manual/source/doc/processing/pm_operators.txt': {'sha256': 'bbd2cc7ef958df8a1c3e4adc30d3d44eeac1842b6b306a1f6b7bb5bf74942a88',
                                                                    'size': 666},
 'references/siril-manual/source/doc/processing/pm_px_functions.txt': {'sha256': '6f492eec24103e52d6e619cf5258efeb601ae9e72abad7a3c827e87aece8dd9d',
                                                                       'size': 1647},
 'references/siril-manual/source/doc/installation/build_options.txt': {'sha256': '38900aaee0e89dad489e1d1357b18c87ac8a68bc99301aedc5b291e1a790c6ac',
                                                                       'size': 954}}
for _name, _binding in _PRE_DENOISE_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Complete outgoing recomposition knowledge, scoped to its exact frozen policy.
_PRE_RECOMPOSITION_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'CHANGELOG.md': {'sha256': '842275811abee5e3563645c1838c7935c2f9a5ba100d61d4da07f984ff8bc58b',
                  'size': 35883},
 'release-files.txt': {'sha256': 'e7b9673841ad699134624c1f97a470db85bd5990c64a64372a1961de7e6bb889',
                       'size': 2416},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'SKILL.md': {'sha256': '41789817a382224c534e41e9cef8f86cb7e8274b003d13dfc312066a3bfa571d',
              'size': 24084},
 'references/session-contract.md': {'sha256': '57d6f54d733c03807924447207d1ec7ba4f7480b60e95f8c8247d3158c1c320f',
                                    'size': 40157},
 'references/review.schema.json': {'sha256': '4fa3bcd370674c2b7f86f3bd258b15ec91a4dfc9ff40d21f0b49130c50384334',
                                   'size': 11863},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/quality.md': {'sha256': '3696d9b1a82cb4f3ca8da0197edceac54c00b650b412ab9a56b19622e37f102d',
                           'size': 32061},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': 'fb11de83cf9d4af1c724ac5567a3a0d986361ea84d78e2c279db88990ea31cc1',
                                'size': 56577},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/protocol-index.md': {'sha256': '30caf0c26544da0efa338b1049079674abe93b2bc9ac5915ba9c9f20eb1e4b5a',
                                  'size': 3798},
 'references/delivery.md': {'sha256': 'e5d88ade74e4f260c92febfe6126f7ec659360dfe11c8c683369765c1f27e6b5',
                            'size': 12449},
 'references/stage-sequence.md': {'sha256': '4272f0e473b86ab5b8433bc6640eba1f9c2f4bfe165bfe6de4ef90414f6f7c68',
                                  'size': 12969},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/target-morphology-review.md': {'sha256': '21cfcc2acc2179e0790fe099dfb65e4bde526d241b5a2b18da7d8600d6a7090a',
                                            'size': 8962},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/stages/stage-07-stretch.md': {'sha256': '2a3f48dd3659af4726e51bd3f6a5996461035787820fc73d397c1dae330e64a9',
                                           'size': 72538},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-09-stars-recompose.md': {'sha256': 'f846e8adbadc8bd3d1dac7d75bcc0444e152bd3d5da0e7d2e6041e8fc549a9ce',
                                                   'size': 22764},
 'references/stages/stage-05-linear-restoration.md': {'sha256': 'c4d716da58371292c19f532a08f3e1eb68ff9003e470d3404aa92065b13cd5b7',
                                                      'size': 23630},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '0f0204d3b003136f9f934515f3ba64efac43ca214db82ed4114936218aa53094',
                                                      'size': 9536},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-08-starless-enhance.md': {'sha256': 'ae8bcd6c0b5fc6c1579f066035e1c7fbcbe8ef9fe393bf22609fad2ff942ac4a',
                                                    'size': 27244},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/siril-manual/LICENSE.GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                                 'size': 35057},
 'references/siril-manual/aliases.zh-en.json': {'sha256': '191f702716ef2296f5a0d0fcbd20d23c1532bd178e708cb63eab36099f1365fd',
                                                'size': 10091},
 'references/siril-manual/commands.json': {'sha256': 'c025149d2d68868d3312f9d22d128d28cbc45845bbbc89e60d6d1f63e8fbcbd4',
                                           'size': 225462},
 'references/siril-manual/files.json': {'sha256': 'fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce',
                                        'size': 185274},
 'references/siril-manual/manifest.json': {'sha256': '5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d',
                                           'size': 3063},
 'references/siril-manual/LICENSE.GFDL-1.2.txt': {'sha256': '2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4',
                                                  'size': 20394},
 'references/siril-manual/catalog.json': {'sha256': 'a509e581fa841a7f15ab291832414648f4ffe9b150166e0ceb0c2f5beb3fa21c',
                                          'size': 1405328},
 'references/siril-manual/image-selection.json': {'sha256': '6bc7d65f4198fec7fe76f23c2aab7e20a48e4584fc30fc4e052e97d59945b878',
                                                  'size': 93948},
 'references/siril-manual/MODIFICATIONS.md': {'sha256': '7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c',
                                              'size': 1497},
 'references/siril-manual/NOTICE.md': {'sha256': '431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513',
                                       'size': 1293},
 'references/shared-protocols/tone-adjust.md': {'sha256': 'c80c3ae73056a196af3430a0954b2a50649ec3ffa292e18131daca10d1e5728c',
                                                'size': 9382},
 'references/shared-protocols/color-finish.md': {'sha256': 'f1520dffdebc5f09939e8a665f01613808d59411b770b30cdeee21f0d89ba21b',
                                                 'size': 17712},
 'references/siril-manual/source/doc/astrometry/cats.txt': {'sha256': 'f6b5dd45cbe6bdf42b686719ec510292b18c325a12824088b009edd7bccd6fbe',
                                                            'size': 958},
 'references/siril-manual/source/doc/preferences/getA.txt': {'sha256': '0b204412feaf7a276333b2b5b2bf641ed3cb046066f096c0a15ed1b0c425a7dc',
                                                             'size': 14586},
 'references/siril-manual/source/doc/processing/pm_im_functions.txt': {'sha256': 'e77dc8eb51976d527770551ddd46154f8abb70b1994fad182e457233e74672b8',
                                                                       'size': 688},
 'references/siril-manual/source/doc/processing/pm_operators.txt': {'sha256': 'bbd2cc7ef958df8a1c3e4adc30d3d44eeac1842b6b306a1f6b7bb5bf74942a88',
                                                                    'size': 666},
 'references/siril-manual/source/doc/processing/pm_px_functions.txt': {'sha256': '6f492eec24103e52d6e619cf5258efeb601ae9e72abad7a3c827e87aece8dd9d',
                                                                       'size': 1647},
 'references/siril-manual/source/doc/installation/build_options.txt': {'sha256': '38900aaee0e89dad489e1d1357b18c87ac8a68bc99301aedc5b291e1a790c6ac',
                                                                       'size': 954}}
for _name, _binding in _PRE_RECOMPOSITION_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_TONE_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'CHANGELOG.md': {'sha256': '2dc4caa03a7a4a3751ec7a0748859797699cd2438d2334f4de3c1944e45ac9a8',
                  'size': 37071},
 'release-files.txt': {'sha256': 'e7b9673841ad699134624c1f97a470db85bd5990c64a64372a1961de7e6bb889',
                       'size': 2416},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'SKILL.md': {'sha256': '9c851d999176401f1ced86bcc6ddf7485a4868c0aa57844ae688711f8321b455',
              'size': 24729},
 'references/session-contract.md': {'sha256': '9fefc6e43adafddf460edf72188321cd4da76a87d2f64abcff242a2317902d0c',
                                    'size': 41476},
 'references/review.schema.json': {'sha256': '3bba6e915379571b6589fbd0bd324d4988440b18237d11ccdc4d55dbe37cc08b',
                                   'size': 13427},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/quality.md': {'sha256': '90a17e69bb92f4910510f83ea9d78282284d047b74eefdf731d32e98222816da',
                           'size': 33380},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': '814fc232d06d2f0482395d8fffb508f8ab8208638af6c4fbe985ff2763716c0d',
                                'size': 58031},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/protocol-index.md': {'sha256': '30caf0c26544da0efa338b1049079674abe93b2bc9ac5915ba9c9f20eb1e4b5a',
                                  'size': 3798},
 'references/delivery.md': {'sha256': '795b8035b6507b0df4e4c0abf3984ce9b96e10f11b0bb94626ed6bd8f8b3f877',
                            'size': 12462},
 'references/stage-sequence.md': {'sha256': '3ebfb485c95ba0ba55ffa1af8d3fcb37d8358209a44358fbdc881bccbb4eb869',
                                  'size': 14288},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/target-morphology-review.md': {'sha256': '9816eb278b6f7ea2c8ab1ecadf3e7fa1fab65123ee962cf92264c5c96513af2d',
                                            'size': 9106},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/stages/stage-07-stretch.md': {'sha256': '2a3f48dd3659af4726e51bd3f6a5996461035787820fc73d397c1dae330e64a9',
                                           'size': 72538},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-09-stars-recompose.md': {'sha256': '5154de27fbeaef494975d3a42eca74ca25b3c6c21a2b96b361c7b1edab58391c',
                                                   'size': 28161},
 'references/stages/stage-05-linear-restoration.md': {'sha256': 'c4d716da58371292c19f532a08f3e1eb68ff9003e470d3404aa92065b13cd5b7',
                                                      'size': 23630},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '0f0204d3b003136f9f934515f3ba64efac43ca214db82ed4114936218aa53094',
                                                      'size': 9536},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-08-starless-enhance.md': {'sha256': 'ae8bcd6c0b5fc6c1579f066035e1c7fbcbe8ef9fe393bf22609fad2ff942ac4a',
                                                    'size': 27244},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/siril-manual/LICENSE.GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                                 'size': 35057},
 'references/siril-manual/aliases.zh-en.json': {'sha256': '191f702716ef2296f5a0d0fcbd20d23c1532bd178e708cb63eab36099f1365fd',
                                                'size': 10091},
 'references/siril-manual/commands.json': {'sha256': 'c025149d2d68868d3312f9d22d128d28cbc45845bbbc89e60d6d1f63e8fbcbd4',
                                           'size': 225462},
 'references/siril-manual/files.json': {'sha256': 'fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce',
                                        'size': 185274},
 'references/siril-manual/manifest.json': {'sha256': '5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d',
                                           'size': 3063},
 'references/siril-manual/LICENSE.GFDL-1.2.txt': {'sha256': '2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4',
                                                  'size': 20394},
 'references/siril-manual/catalog.json': {'sha256': 'a509e581fa841a7f15ab291832414648f4ffe9b150166e0ceb0c2f5beb3fa21c',
                                          'size': 1405328},
 'references/siril-manual/image-selection.json': {'sha256': '6bc7d65f4198fec7fe76f23c2aab7e20a48e4584fc30fc4e052e97d59945b878',
                                                  'size': 93948},
 'references/siril-manual/MODIFICATIONS.md': {'sha256': '7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c',
                                              'size': 1497},
 'references/siril-manual/NOTICE.md': {'sha256': '431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513',
                                       'size': 1293},
 'references/shared-protocols/tone-adjust.md': {'sha256': 'c80c3ae73056a196af3430a0954b2a50649ec3ffa292e18131daca10d1e5728c',
                                                'size': 9382},
 'references/shared-protocols/color-finish.md': {'sha256': 'f1520dffdebc5f09939e8a665f01613808d59411b770b30cdeee21f0d89ba21b',
                                                 'size': 17712},
 'references/siril-manual/source/doc/astrometry/cats.txt': {'sha256': 'f6b5dd45cbe6bdf42b686719ec510292b18c325a12824088b009edd7bccd6fbe',
                                                            'size': 958},
 'references/siril-manual/source/doc/preferences/getA.txt': {'sha256': '0b204412feaf7a276333b2b5b2bf641ed3cb046066f096c0a15ed1b0c425a7dc',
                                                             'size': 14586},
 'references/siril-manual/source/doc/processing/pm_im_functions.txt': {'sha256': 'e77dc8eb51976d527770551ddd46154f8abb70b1994fad182e457233e74672b8',
                                                                       'size': 688},
 'references/siril-manual/source/doc/processing/pm_operators.txt': {'sha256': 'bbd2cc7ef958df8a1c3e4adc30d3d44eeac1842b6b306a1f6b7bb5bf74942a88',
                                                                    'size': 666},
 'references/siril-manual/source/doc/processing/pm_px_functions.txt': {'sha256': '6f492eec24103e52d6e619cf5258efeb601ae9e72abad7a3c827e87aece8dd9d',
                                                                       'size': 1647},
 'references/siril-manual/source/doc/installation/build_options.txt': {'sha256': '38900aaee0e89dad489e1d1357b18c87ac8a68bc99301aedc5b291e1a790c6ac',
                                                                       'size': 954}}
for _name, _binding in _PRE_TONE_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding["sha256"], _binding["size"]))

_PRE_COLOR_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'CHANGELOG.md': {'sha256': '2dc4caa03a7a4a3751ec7a0748859797699cd2438d2334f4de3c1944e45ac9a8',
                  'size': 37071},
 'release-files.txt': {'sha256': 'e81ce37da49a5ac35d056c26231e5e15e4ea8e2cbc970fbf386927dad24f0770',
                       'size': 2447},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'SKILL.md': {'sha256': '365bdb16596a9a093fc8092fbcfececc51df8e329313ae2c942fd6ab883968a1',
              'size': 24985},
 'references/session-contract.md': {'sha256': '545eb2d82cc5543028fe897b43cc1a53aa77510aa54472139bc24442fd62922e',
                                    'size': 42109},
 'references/review.schema.json': {'sha256': '8f419aa2db7f6639ead5440d0d44b4140bc9ca19c74b7f855195277a4cd84bb1',
                                   'size': 15365},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/quality.md': {'sha256': 'b240396cbbc85a1792d29f0d6b84b99f7e8834a2a34f6a4429a2d879b866e8c5',
                           'size': 34096},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': 'e0da0693e0bba06a945fc5cb5e91edf269513284ecca6807763fe8135c845160',
                                'size': 58683},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/protocol-index.md': {'sha256': '30caf0c26544da0efa338b1049079674abe93b2bc9ac5915ba9c9f20eb1e4b5a',
                                  'size': 3798},
 'references/delivery.md': {'sha256': '795b8035b6507b0df4e4c0abf3984ce9b96e10f11b0bb94626ed6bd8f8b3f877',
                            'size': 12462},
 'references/stage-sequence.md': {'sha256': '15584286b595c6490ebc6ba2167d83cdf1e56ec4f30b4a795804d6be0fad2dd7',
                                  'size': 14729},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/target-morphology-review.md': {'sha256': '9816eb278b6f7ea2c8ab1ecadf3e7fa1fab65123ee962cf92264c5c96513af2d',
                                            'size': 9106},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/stages/stage-07-stretch.md': {'sha256': '2a3f48dd3659af4726e51bd3f6a5996461035787820fc73d397c1dae330e64a9',
                                           'size': 72538},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-09-stars-recompose.md': {'sha256': '5154de27fbeaef494975d3a42eca74ca25b3c6c21a2b96b361c7b1edab58391c',
                                                   'size': 28161},
 'references/stages/stage-05-linear-restoration.md': {'sha256': 'c4d716da58371292c19f532a08f3e1eb68ff9003e470d3404aa92065b13cd5b7',
                                                      'size': 23630},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '74bf73e02d9c280e8eca12edd9cdd043fcd83b123c45dfc6c2233b3a25ae5041',
                                                      'size': 9871},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-08-starless-enhance.md': {'sha256': '1bb8c79094b8aca53db276f9d5636223e1e06a41a2cb2092983622c6de128569',
                                                    'size': 27585},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/siril-manual/LICENSE.GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                                 'size': 35057},
 'references/siril-manual/aliases.zh-en.json': {'sha256': '191f702716ef2296f5a0d0fcbd20d23c1532bd178e708cb63eab36099f1365fd',
                                                'size': 10091},
 'references/siril-manual/commands.json': {'sha256': 'c025149d2d68868d3312f9d22d128d28cbc45845bbbc89e60d6d1f63e8fbcbd4',
                                           'size': 225462},
 'references/siril-manual/files.json': {'sha256': 'fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce',
                                        'size': 185274},
 'references/siril-manual/manifest.json': {'sha256': '5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d',
                                           'size': 3063},
 'references/siril-manual/LICENSE.GFDL-1.2.txt': {'sha256': '2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4',
                                                  'size': 20394},
 'references/siril-manual/catalog.json': {'sha256': 'a509e581fa841a7f15ab291832414648f4ffe9b150166e0ceb0c2f5beb3fa21c',
                                          'size': 1405328},
 'references/siril-manual/image-selection.json': {'sha256': '6bc7d65f4198fec7fe76f23c2aab7e20a48e4584fc30fc4e052e97d59945b878',
                                                  'size': 93948},
 'references/siril-manual/MODIFICATIONS.md': {'sha256': '7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c',
                                              'size': 1497},
 'references/siril-manual/NOTICE.md': {'sha256': '431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513',
                                       'size': 1293},
 'references/shared-protocols/tone-adjust.md': {'sha256': '93ad18e5e02819ea55f95d612eafc6837bede0baa4b72ed19186892a26ee253e',
                                                'size': 12844},
 'references/shared-protocols/color-finish.md': {'sha256': 'f1520dffdebc5f09939e8a665f01613808d59411b770b30cdeee21f0d89ba21b',
                                                 'size': 17712},
 'references/siril-manual/source/doc/astrometry/cats.txt': {'sha256': 'f6b5dd45cbe6bdf42b686719ec510292b18c325a12824088b009edd7bccd6fbe',
                                                            'size': 958},
 'references/siril-manual/source/doc/preferences/getA.txt': {'sha256': '0b204412feaf7a276333b2b5b2bf641ed3cb046066f096c0a15ed1b0c425a7dc',
                                                             'size': 14586},
 'references/siril-manual/source/doc/processing/pm_im_functions.txt': {'sha256': 'e77dc8eb51976d527770551ddd46154f8abb70b1994fad182e457233e74672b8',
                                                                       'size': 688},
 'references/siril-manual/source/doc/processing/pm_operators.txt': {'sha256': 'bbd2cc7ef958df8a1c3e4adc30d3d44eeac1842b6b306a1f6b7bb5bf74942a88',
                                                                    'size': 666},
 'references/siril-manual/source/doc/processing/pm_px_functions.txt': {'sha256': '6f492eec24103e52d6e619cf5258efeb601ae9e72abad7a3c827e87aece8dd9d',
                                                                       'size': 1647},
 'references/siril-manual/source/doc/installation/build_options.txt': {'sha256': '38900aaee0e89dad489e1d1357b18c87ac8a68bc99301aedc5b291e1a790c6ac',
                                                                       'size': 954}}
for _name, _binding in _PRE_COLOR_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

_PRE_LOCAL_HDR_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'CHANGELOG.md': {'sha256': 'cb02b730598392bd71fd6fdf102567a282454e8b000c2b257451f64da6e3bb7e',
                  'size': 38295},
 'release-files.txt': {'sha256': 'e81ce37da49a5ac35d056c26231e5e15e4ea8e2cbc970fbf386927dad24f0770',
                       'size': 2447},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'SKILL.md': {'sha256': '1c52084af06169ec242c1fd249d807baeafcc084443eacdc3765e42c19e2b1ee',
              'size': 25339},
 'references/session-contract.md': {'sha256': '35f000e4deec578fa43864cbf7a09366910831dd02b2556b8b485fb690cbd0d8',
                                    'size': 42993},
 'references/review.schema.json': {'sha256': '14322d66c83748b5fb916a511bd352e8b78a519c8a57dec9b55c7a40a872884b',
                                   'size': 15581},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/quality.md': {'sha256': 'e4073a06735fa57c1c03b1e155d9fe0f81859105cff3253ad7080695c39cda76',
                           'size': 34928},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': 'd01b8600886a4fc13cd6afdfb5181b6c06756a1f09254f841d62f8d0fb7fbad6',
                                'size': 59807},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/protocol-index.md': {'sha256': '3a8ae78a842af50a0ee1292ffddb200ccfdfd524f1528430e33ca86aaabdc018',
                                  'size': 3890},
 'references/delivery.md': {'sha256': '795b8035b6507b0df4e4c0abf3984ce9b96e10f11b0bb94626ed6bd8f8b3f877',
                            'size': 12462},
 'references/stage-sequence.md': {'sha256': 'caddefd72bf6deb8bfcf9c45e3a2db5f51229fbf110eea2708daba15d8e5f52d',
                                  'size': 14777},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/target-morphology-review.md': {'sha256': '9816eb278b6f7ea2c8ab1ecadf3e7fa1fab65123ee962cf92264c5c96513af2d',
                                            'size': 9106},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/stages/stage-07-stretch.md': {'sha256': 'de3e25719c3c026f2ee661f99550499a9edc248c59c951c32df99ea4ae59070c',
                                           'size': 73170},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-09-stars-recompose.md': {'sha256': '5154de27fbeaef494975d3a42eca74ca25b3c6c21a2b96b361c7b1edab58391c',
                                                   'size': 28161},
 'references/stages/stage-05-linear-restoration.md': {'sha256': 'c4d716da58371292c19f532a08f3e1eb68ff9003e470d3404aa92065b13cd5b7',
                                                      'size': 23630},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '2b0e4a60e9a2592d0ac7563526dcf04ec91c74b69113f4380df11ab627a701df',
                                                      'size': 9777},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-08-starless-enhance.md': {'sha256': '705dc8d3821d9014a3f31075fa099f9408534444afde8b97b2e284a706573e9f',
                                                    'size': 27607},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/siril-manual/LICENSE.GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                                 'size': 35057},
 'references/siril-manual/aliases.zh-en.json': {'sha256': '191f702716ef2296f5a0d0fcbd20d23c1532bd178e708cb63eab36099f1365fd',
                                                'size': 10091},
 'references/siril-manual/commands.json': {'sha256': 'c025149d2d68868d3312f9d22d128d28cbc45845bbbc89e60d6d1f63e8fbcbd4',
                                           'size': 225462},
 'references/siril-manual/files.json': {'sha256': 'fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce',
                                        'size': 185274},
 'references/siril-manual/manifest.json': {'sha256': '5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d',
                                           'size': 3063},
 'references/siril-manual/LICENSE.GFDL-1.2.txt': {'sha256': '2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4',
                                                  'size': 20394},
 'references/siril-manual/catalog.json': {'sha256': 'a509e581fa841a7f15ab291832414648f4ffe9b150166e0ceb0c2f5beb3fa21c',
                                          'size': 1405328},
 'references/siril-manual/image-selection.json': {'sha256': '6bc7d65f4198fec7fe76f23c2aab7e20a48e4584fc30fc4e052e97d59945b878',
                                                  'size': 93948},
 'references/siril-manual/MODIFICATIONS.md': {'sha256': '7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c',
                                              'size': 1497},
 'references/siril-manual/NOTICE.md': {'sha256': '431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513',
                                       'size': 1293},
 'references/shared-protocols/tone-adjust.md': {'sha256': '93ad18e5e02819ea55f95d612eafc6837bede0baa4b72ed19186892a26ee253e',
                                                'size': 12844},
 'references/shared-protocols/color-finish.md': {'sha256': 'd6c67bc1bc24c2d659311b77286e58fe89a614049bc18faf1d5272661354ce5b',
                                                 'size': 17897},
 'references/siril-manual/source/doc/astrometry/cats.txt': {'sha256': 'f6b5dd45cbe6bdf42b686719ec510292b18c325a12824088b009edd7bccd6fbe',
                                                            'size': 958},
 'references/siril-manual/source/doc/preferences/getA.txt': {'sha256': '0b204412feaf7a276333b2b5b2bf641ed3cb046066f096c0a15ed1b0c425a7dc',
                                                             'size': 14586},
 'references/siril-manual/source/doc/processing/pm_im_functions.txt': {'sha256': 'e77dc8eb51976d527770551ddd46154f8abb70b1994fad182e457233e74672b8',
                                                                       'size': 688},
 'references/siril-manual/source/doc/processing/pm_operators.txt': {'sha256': 'bbd2cc7ef958df8a1c3e4adc30d3d44eeac1842b6b306a1f6b7bb5bf74942a88',
                                                                    'size': 666},
 'references/siril-manual/source/doc/processing/pm_px_functions.txt': {'sha256': '6f492eec24103e52d6e619cf5258efeb601ae9e72abad7a3c827e87aece8dd9d',
                                                                       'size': 1647},
 'references/siril-manual/source/doc/installation/build_options.txt': {'sha256': '38900aaee0e89dad489e1d1357b18c87ac8a68bc99301aedc5b291e1a790c6ac',
                                                                       'size': 954}}
for _name, _binding in _PRE_LOCAL_HDR_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


_PRE_RECOMPOSITION_GEOMETRY_REFERENCES = {'LICENSE.md': {'sha256': '88ce538e753ff222f3b66f7d457b3c628a92786c44e78370f4de5fc0dffcf2c7',
                'size': 621},
 'RELEASING.md': {'sha256': 'b7c3b08e111fb23cbdd2c28772f8374eb0a04c18eea35d96281ea800510a9f2a',
                  'size': 19842},
 'CHANGELOG.md': {'sha256': 'cb02b730598392bd71fd6fdf102567a282454e8b000c2b257451f64da6e3bb7e',
                  'size': 38295},
 'release-files.txt': {'sha256': 'e81ce37da49a5ac35d056c26231e5e15e4ea8e2cbc970fbf386927dad24f0770',
                       'size': 2447},
 'THIRD_PARTY_NOTICES.md': {'sha256': '486f26f773946b268254f9304a3de1a5010f44af68c296560c8fe9f5df306f11',
                            'size': 2669},
 'requirements-dev.txt': {'sha256': '7b7130b11b22e4a3996dedeb8e9c3aa6f4f1c93bf03c0aa0abd75e98938d5d3f',
                          'size': 27},
 'SKILL.md': {'sha256': '577492b0cba01a1ffebf5245948e67f3d6d3eba101a8067e1bb6365d2f0cb816',
              'size': 25497},
 'references/session-contract.md': {'sha256': '22a1826b16fff1ca981eba8fa822b8cb0c34d24fc7856e56fc366cd13d7fb00d',
                                    'size': 43895},
 'references/review.schema.json': {'sha256': '14322d66c83748b5fb916a511bd352e8b78a519c8a57dec9b55c7a40a872884b',
                                   'size': 15581},
 'references/LICENSE.StarNet-GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                            'size': 35057},
 'references/troubleshooting.md': {'sha256': '09de1413a5871d9979a76781528fd1ced366177893cb47dc7c10109fc824481d',
                                   'size': 7866},
 'references/command-policy.json': {'sha256': '6af8a154c4eaff0b4fed82c9a964b00bafc29da74b89d6b694db625affd6ce8a',
                                    'size': 4250},
 'references/manual-query.md': {'sha256': '9900294086fda831a226976c2644f8a20d562cf30068c9c39db871310a5f4124',
                                'size': 5237},
 'references/final-selection.schema.json': {'sha256': '4e0e05daefb63f1d621612f355b911d2e8d52755ba51559c2407d8e73daacefb',
                                            'size': 2923},
 'references/siril-safety.md': {'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b',
                                'size': 7624},
 'references/starnet-linear-source.json': {'sha256': 'fb8c541b45ba2278de030c32f581ff036ea13626e64088b4cb7aa193f2540a8c',
                                           'size': 1273},
 'references/starnet-source.json': {'sha256': '42d4ba5d326cad67fff2f60766ed82b445d4e2eec14023a6aee46677232c7cb4',
                                    'size': 1213},
 'references/quality.md': {'sha256': '2861e19009b839b30ae5015d333f448bef9cd1b4a7579777b1e1bfec2c21908a',
                           'size': 35171},
 'references/background-sample-contract.schema.json': {'sha256': 'b090979acb0b44d299ab52a663dce23297e404fbba99b83b077ac25d32ff2d9b',
                                                       'size': 1486},
 'references/cli-contract.md': {'sha256': '8865b75d11d0feba0ad55f8cc8c3d3d5d8bde6da4d38669949700f132b157348',
                                'size': 60057},
 'references/ssf-provenance.schema.json': {'sha256': '140e75985446567e15a0b96c1fe2da70fc2678ffe3ebaeb3a3e321b6113341e3',
                                           'size': 4069},
 'references/astrometry-source-preservation.md': {'sha256': '6104b3f068e896f37730144bd23891ebf93362cf653061cbb54878d1a9b403f7',
                                                  'size': 5736},
 'references/protocol-index.md': {'sha256': '3a8ae78a842af50a0ee1292ffddb200ccfdfd524f1528430e33ca86aaabdc018',
                                  'size': 3890},
 'references/delivery.md': {'sha256': '795b8035b6507b0df4e4c0abf3984ce9b96e10f11b0bb94626ed6bd8f8b3f877',
                            'size': 12462},
 'references/stage-sequence.md': {'sha256': 'f00ff891ab50a4cb513512764756f62aa96c007cce1844363dfd9e10323d9f0c',
                                  'size': 14899},
 'references/stretch-profiles.json': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
                                      'size': 3844},
 'references/target-morphology-review.md': {'sha256': '9816eb278b6f7ea2c8ab1ecadf3e7fa1fab65123ee962cf92264c5c96513af2d',
                                            'size': 9106},
 'agents/openai.yaml': {'sha256': '179ed096389a5c36003494c7cee9d11643b873cf342e1403fc38ee84e36804d5',
                        'size': 345},
 'references/stages/stage-07-stretch.md': {'sha256': '2dcd0553e7c27405b654e997fffc9244bbd60d309c2aeafe50ef2d901324cfa8',
                                           'size': 73367},
 'references/stages/stage-01-input-inspect.md': {'sha256': 'ff0a99326be4b41a13faf7e15c178e28739753ef1ad6716531f71847202a09aa',
                                                 'size': 10925},
 'references/stages/stage-09-stars-recompose.md': {'sha256': '5154de27fbeaef494975d3a42eca74ca25b3c6c21a2b96b361c7b1edab58391c',
                                                   'size': 28161},
 'references/stages/stage-05-linear-restoration.md': {'sha256': 'c4d716da58371292c19f532a08f3e1eb68ff9003e470d3404aa92065b13cd5b7',
                                                      'size': 23630},
 'references/stages/stage-04-astrometry-color.md': {'sha256': 'b5a32eb666939b02a1dd2a57150c750fc5f5f423323a9dcc8a9b5d36ca11595c',
                                                    'size': 39565},
 'references/stages/stage-10-delivery-finishing.md': {'sha256': '2b0e4a60e9a2592d0ac7563526dcf04ec91c74b69113f4380df11ab627a701df',
                                                      'size': 9777},
 'references/stages/stage-06-stars-separation.md': {'sha256': '378f2c38ea22dd4a8d43b2bf3b30b24fe2c9ad443104189879530676f6cf50dc',
                                                    'size': 18581},
 'references/stages/stage-08-starless-enhance.md': {'sha256': 'debaccf041ba746bb59e9da2828bc7b0dc6c8f314d8474cda392f91b8f9f5656',
                                                    'size': 28526},
 'references/stages/stage-03-background.md': {'sha256': 'f24a76ea09fd9d49bee9d30d5d161825fb1e7b8fde6a175ecdb602c77a28b119',
                                              'size': 28357},
 'references/stages/stage-02-geometry.md': {'sha256': 'a9700083f74e0bd206481351ce6b72d5549284afa95466f05e4eb6fea2e0d97e',
                                            'size': 4024},
 'references/siril-manual/LICENSE.GPL-3.0.txt': {'sha256': '43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60',
                                                 'size': 35057},
 'references/siril-manual/aliases.zh-en.json': {'sha256': '191f702716ef2296f5a0d0fcbd20d23c1532bd178e708cb63eab36099f1365fd',
                                                'size': 10091},
 'references/siril-manual/commands.json': {'sha256': 'c025149d2d68868d3312f9d22d128d28cbc45845bbbc89e60d6d1f63e8fbcbd4',
                                           'size': 225462},
 'references/siril-manual/files.json': {'sha256': 'fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce',
                                        'size': 185274},
 'references/siril-manual/manifest.json': {'sha256': '5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d',
                                           'size': 3063},
 'references/siril-manual/LICENSE.GFDL-1.2.txt': {'sha256': '2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4',
                                                  'size': 20394},
 'references/siril-manual/catalog.json': {'sha256': 'a509e581fa841a7f15ab291832414648f4ffe9b150166e0ceb0c2f5beb3fa21c',
                                          'size': 1405328},
 'references/siril-manual/image-selection.json': {'sha256': '6bc7d65f4198fec7fe76f23c2aab7e20a48e4584fc30fc4e052e97d59945b878',
                                                  'size': 93948},
 'references/siril-manual/MODIFICATIONS.md': {'sha256': '7be34c8f36e6d650e9b90d9dba820568522cc1184e8d47c035f736fd6da6cb1c',
                                              'size': 1497},
 'references/siril-manual/NOTICE.md': {'sha256': '431b458f16d46f787d0b07fe21a789eb072dcaa45be234b65d5f77955f86b513',
                                       'size': 1293},
 'references/shared-protocols/tone-adjust.md': {'sha256': '93ad18e5e02819ea55f95d612eafc6837bede0baa4b72ed19186892a26ee253e',
                                                'size': 12844},
 'references/shared-protocols/color-finish.md': {'sha256': '74aed0ce73c7fc99ec48465674d6a3126c35c3bd1c175ac5f46108babe0e8ab4',
                                                 'size': 18736},
 'references/siril-manual/source/doc/astrometry/cats.txt': {'sha256': 'f6b5dd45cbe6bdf42b686719ec510292b18c325a12824088b009edd7bccd6fbe',
                                                            'size': 958},
 'references/siril-manual/source/doc/preferences/getA.txt': {'sha256': '0b204412feaf7a276333b2b5b2bf641ed3cb046066f096c0a15ed1b0c425a7dc',
                                                             'size': 14586},
 'references/siril-manual/source/doc/processing/pm_im_functions.txt': {'sha256': 'e77dc8eb51976d527770551ddd46154f8abb70b1994fad182e457233e74672b8',
                                                                       'size': 688},
 'references/siril-manual/source/doc/processing/pm_operators.txt': {'sha256': 'bbd2cc7ef958df8a1c3e4adc30d3d44eeac1842b6b306a1f6b7bb5bf74942a88',
                                                                    'size': 666},
 'references/siril-manual/source/doc/processing/pm_px_functions.txt': {'sha256': '6f492eec24103e52d6e619cf5258efeb601ae9e72abad7a3c827e87aece8dd9d',
                                                                       'size': 1647},
 'references/siril-manual/source/doc/installation/build_options.txt': {'sha256': '38900aaee0e89dad489e1d1357b18c87ac8a68bc99301aedc5b291e1a790c6ac',
                                                                       'size': 954}}
for _name, _binding in _PRE_RECOMPOSITION_GEOMETRY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding["sha256"], _binding["size"]))


def execution_policy_is_current(payload: dict[str, Any]) -> bool:
    return payload.get("execution_policy") == EXECUTION_POLICY


def pre_confirmation_inspect_reference(payload: dict[str, Any], protocol: str, record: dict[str, Any]) -> bool:
    return bool(
        protocol == "input.inspect" and known_execution_policy(payload.get("execution_policy"))
        and record.get("path") in _PRE_CONFIRMATION_REFERENCES
        and (record.get("sha256"), record.get("size")) == _PRE_CONFIRMATION_REFERENCES[record["path"]]
    )


# Exact evidence from before the compatible configuration fix.
_COLOR_CONFIG_REPLAY_POLICY_HASH = 'd8070eebb567e8614f0f282327a4c8526c13a1032222a6f38c570130f6ab3fca'
_COLOR_CONFIG_REPLAY_REFERENCES = {'references/shared-protocols/color-finish.md': {'sha256': 'd6c67bc1bc24c2d659311b77286e58fe89a614049bc18faf1d5272661354ce5b',
                                                 'size': 17897}}
for _name, _binding in _COLOR_CONFIG_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance from before the same-source CLAHE comparison instructions.
_CLAHE_COMPARISON_REPLAY_POLICY_HASH = '2d6f805da35ec200b28541097675269e4ab53ada666af47484e34b5619f0b042'
_CLAHE_COMPARISON_REPLAY_REFERENCES = {'references/stages/stage-08-starless-enhance.md': {
    'sha256': 'debaccf041ba746bb59e9da2828bc7b0dc6c8f314d8474cda392f91b8f9f5656', 'size': 28526}}
for _name, _binding in _CLAHE_COMPARISON_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance from before baseline-budget and catalog-ROI examples.
_BUDGET_ROI_REPLAY_POLICY_HASH = '2d6f805da35ec200b28541097675269e4ab53ada666af47484e34b5619f0b042'
_BUDGET_ROI_REPLAY_REFERENCES = {
    'references/stages/stage-07-stretch.md': {
        'sha256': '2dcd0553e7c27405b654e997fffc9244bbd60d309c2aeafe50ef2d901324cfa8', 'size': 73367},
    'references/stages/stage-09-stars-recompose.md': {
        'sha256': '66624f6aa27d0cf640834b09f556ec9a38a575ea95ee3ad26aa89139cbfb086d', 'size': 31607},
    'references/cli-contract.md': {
        'sha256': 'ff4a87ecd33934cfb6d70c6e45a7c4e82fa9f8a0081ad35aa50c2582a2b0834a', 'size': 60619}}
for _name, _binding in _BUDGET_ROI_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance from before independent Stage 5 assessment and scale summaries.
_STAGE5_GUIDANCE_REPLAY_POLICY_HASH = 'ae2c6af2f050dd46b64d7d7a6949fb7507061d83d07aeb80321d44de3812cc53'
_STAGE5_GUIDANCE_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': '147d2d60f6b1c536d4ce0c20a9c020267ffaeac414c87995d3484a561cf24e3e', 'size': 26317},
    'references/stages/stage-05-linear-restoration.md': {
        'sha256': 'f22d0da605503fee1e4fb8c7aeee8d4f8462989e1c6b651068051e840df5adb1', 'size': 27995}}
for _name, _binding in _STAGE5_GUIDANCE_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


_V4_FLOW_REPLAY_POLICY_HASH = '46d25c82d61dcd30dcbe95ec6cef9f52ecc1d2d8790a58a41a31088c94ae54ad'
_V4_FLOW_REPLAY_REFERENCES = {'references/stages/stage-07-stretch.md': {
    'sha256': 'dc64c6cd5bc2d06682adf96e2388d63edc0536d577d9b5ef1598b7b121bacc66', 'size': 78716}}
for _name, _binding in _V4_FLOW_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


_ROI_PAIRS_REPLAY_POLICY_HASH = '46d25c82d61dcd30dcbe95ec6cef9f52ecc1d2d8790a58a41a31088c94ae54ad'
_ROI_PAIRS_REPLAY_REFERENCES = {'references/stages/stage-07-stretch.md': {
    'sha256': '7c03faa7ce72f85bbb1ab610ee004b5c8f5e89e5da9fbfde532bf6e57220533b', 'size': 86906}}
for _name, _binding in _ROI_PAIRS_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


_STRETCH_STOP_REPLAY_POLICY_HASH = '46d25c82d61dcd30dcbe95ec6cef9f52ecc1d2d8790a58a41a31088c94ae54ad'
_STRETCH_STOP_REPLAY_REFERENCES = {'references/stages/stage-07-stretch.md': {
    'sha256': '7c67f2feed0fe91929cff8295a0134c07b402748f475ae96cd31f329ccdc5923', 'size': 99682}}
for _name, _binding in _STRETCH_STOP_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact outgoing Stage 7 guidance; committed replay/finalize only.
_STAGE7_MULTISTEP_REPLAY_POLICY_HASH = '4f396467e3a3c8206ec6afa95b0f8bb06b11a610d680f16d71e5c98c6a4bb4db'
_STAGE7_MULTISTEP_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': 'e402a1e329d7b4c6072b4e77a0c7978f5c2fdf5de116a5023c71c3b66143d7c1', 'size': 27878},
    'references/stages/stage-07-stretch.md': {
        'sha256': '0d0cc254dd9c5e8b61e8cfc28ba059549070ed3e6c422b84e79fdf92ff4c0d1d', 'size': 101205},
    'references/target-morphology-review.md': {
        'sha256': '4c08ffbde9ba22347b6db13f68be608676718f998ea03c2868def4dbc2ba9c12', 'size': 9164}}
for _name, _binding in _STAGE7_MULTISTEP_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance before correcting the GHS CLI D scale; committed replay/finalize only.
_GHS_D_GUIDANCE_REPLAY_POLICY_HASH = '780e28474a10fef6c5761897b2b1f29a9779d288b11d676a1a9515227dc2af27'
_GHS_D_GUIDANCE_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': 'bd023e15f0c26748acb900772f67e824dac6ebd6a7495b2489c645d170287964', 'size': 29019},
    'references/stages/stage-07-stretch.md': {
        'sha256': 'd846497be790a0341b5bdd07cc302491e6ab312062341958fa4caf25ba537a63', 'size': 102926}}
for _name, _binding in _GHS_D_GUIDANCE_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance before unifying route revisions and the Stage 7 attempt budget.
_STAGE7_ROUTE_BUDGET_REPLAY_POLICY_HASH = '780e28474a10fef6c5761897b2b1f29a9779d288b11d676a1a9515227dc2af27'
_STAGE7_ROUTE_BUDGET_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': '322c1e90f7c10c048c3adadfc56f7e17833446bcf8de256906a769f3d4d73e2f', 'size': 29164},
    'references/stages/stage-07-stretch.md': {
        'sha256': '860dfce44830fc19583a893c8d66b850d985be61815a69bc9ed6726b1b38554f', 'size': 103226},
    'references/target-morphology-review.md': {
        'sha256': '718d96be631c93a58a26d51eb0a48cfc6eeb9fbd3598ecc96898b0f19b8d2cfc', 'size': 9400},
    'references/session-contract.md': {
        'sha256': 'e20ae510ab0f7426abccd13af5356e4d483f5f879bbd2b2fb3704574a5c8474b', 'size': 48768},
    'references/cli-contract.md': {
        'sha256': '59e6f147ee37dd5a4e97b6cba6ccd9daff4dcdbe37535b0865f78ad8e9e8aec6', 'size': 64804},
    'references/stage-sequence.md': {
        'sha256': '0e8c02808617a14bc12bd35d166aea7872d4771ea9899b2bb0572559b23f8518', 'size': 17252}}
for _name, _binding in _STAGE7_ROUTE_BUDGET_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance before moving Stage 7 defaults ahead of compatibility material.
_STAGE7_LAYOUT_REPLAY_POLICY_HASH = '780e28474a10fef6c5761897b2b1f29a9779d288b11d676a1a9515227dc2af27'
_STAGE7_LAYOUT_REPLAY_REFERENCES = {
    'references/stages/stage-07-stretch.md': {
        'sha256': '85a97b40735db3eb386c64c9e5af2422aced3ba5827097f1df9f248fdf5ba624', 'size': 106570}}
for _name, _binding in _STAGE7_LAYOUT_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance before evidence-based Stage 3 skipping; committed replay/finalize only.
_STAGE3_SKIP_REPLAY_POLICY_HASH = 'ae62b54fa133244cabf617917b253d6055d397b82dfa8378d470be09a0d476eb'
_STAGE3_SKIP_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': 'f3c2fd1cf73d893235f3da8ece57e9b3df07a8b7a62c738bef642e50d249b097', 'size': 31079},
    'references/stages/stage-03-background.md': {
        'sha256': '7ab9dd8a35eff63022d80fec157a29cf47d4cbee74a95653f3477e31b6912a3f', 'size': 29212},
    'references/stage-sequence.md': {
        'sha256': '5fa64e5078aea2309b9b441401a1b7988d5e17fc9f7670d3c102cec89bc639b4', 'size': 17424},
    'references/session-contract.md': {
        'sha256': 'e093fd5bc8423b9fdebc3140773b4e253d927dde331a2bff39e1e13a60d1f46c', 'size': 50966}}
for _name, _binding in _STAGE3_SKIP_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact guidance before clarifying initial and revised Stage 7 exceptions.
_STAGE7_RULES_REPLAY_POLICY_HASH = '56d8ecad050853f53058fcad1990761759032eb6dd474754933d18d67f4f18e0'
_STAGE7_RULES_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': '06caedf3a851b2b70c161b1fdd68fc08e8514db477774142765717646ef94447', 'size': 36170},
    'references/stages/stage-07-stretch.md': {
        'sha256': 'b5b913ffca7abc17d4856162610f6aebb6dd594b2cb4d6723e515680036c0140', 'size': 124309},
    'references/cli-contract.md': {
        'sha256': '072888abbd855269872627fa47305761b12e69faf7bad48290d9bb2b4997481d', 'size': 73675},
    'references/target-morphology-review.md': {
        'sha256': '54670f41a94f68d2f9f3c851e57c7c15323f0d59370c079b35b793a4401c61d3', 'size': 10102}}
for _name, _binding in _STAGE7_RULES_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))

# Exact guidance before the display-scale review text; policy labels are unchanged.
_DISPLAY_SCALE_REPLAY_POLICY_HASH = 'a91e89a705e9aa54748774793efa283aa6f2ea48ecb5b7a0f03ae0402051ae61'
_DISPLAY_SCALE_REPLAY_REFERENCES = {
    'references/stages/stage-07-stretch.md': {
        'sha256': '50ae19646302fe731ee58cdb49e4aebcf4bf9d04e1f4e0473432dd758341011d', 'size': 75654},
    'references/stages/stage-08-starless-enhance.md': {
        'sha256': 'db33f87b8ff3850b17164ec85082fcb6dc9d20f2c93615b6215680ddb257a6f8', 'size': 43826}}
for _name, _binding in _DISPLAY_SCALE_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact entrypoint before moving stage details to their existing references.
_ENTRYPOINT_REPLAY_POLICY_HASH = '56d8ecad050853f53058fcad1990761759032eb6dd474754933d18d67f4f18e0'
_ENTRYPOINT_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': '585e6c9c5d1baf5ed57acb0922984416fb1ada017b78518d185f4bd5959720a5', 'size': 36231}}
for _name, _binding in _ENTRYPOINT_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact entrypoint before adding direct protocol routes.
_DIRECT_PROTOCOL_REPLAY_POLICY_HASH = '56d8ecad050853f53058fcad1990761759032eb6dd474754933d18d67f4f18e0'
_DIRECT_PROTOCOL_REPLAY_REFERENCES = {
    'SKILL.md': {'sha256': 'd6891a5c9281d0af6f495ee8604c28e88bdb950b5b558d8e4b16043d4360d62a', 'size': 8693}}
for _name, _binding in _DIRECT_PROTOCOL_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact CLI guidance before replacing stage details with canonical references.
_CLI_CONTRACT_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_CLI_CONTRACT_REPLAY_REFERENCES = {
    'references/cli-contract.md': {
        'sha256': '3d71a945b49e04fff434f3e10a8729040d12c4eef269f49563f20247a7f6613b', 'size': 73721}}
for _name, _binding in _CLI_CONTRACT_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact session guidance before separating version history from the current contract.
_SESSION_CONTRACT_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_SESSION_CONTRACT_REPLAY_REFERENCES = {
    'references/session-contract.md': {
        'sha256': 'c47b78ff1e01103f043884fd11fcf0e211cf95fa826dc82eccb81e6213097ecb', 'size': 55403}}
for _name, _binding in _SESSION_CONTRACT_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact stage order guidance before replacing algorithm details with direct links.
_STAGE_SEQUENCE_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_STAGE_SEQUENCE_REPLAY_REFERENCES = {
    'references/stage-sequence.md': {
        'sha256': 'c4bc14ad2c5300b442b1e8c3f8d19ef92e0bea7fe56cd3e64db918f884bf1a49', 'size': 18414}}
for _name, _binding in _STAGE_SEQUENCE_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact review guidance before consolidating protocol-specific rules in their bodies.
_QUALITY_CONTRACT_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_QUALITY_CONTRACT_REPLAY_REFERENCES = {
    'references/quality.md': {
        'sha256': 'cc1eaa2aace307b5fb7ec1c01afd12c101ddc7de033c98b5d6b54f43ab31e595', 'size': 39294},
    'references/stages/stage-03-background.md': {
        'sha256': 'cdb195087b869ac4d29ed52758826f3a1eca585a2ef3470175412efd4f1d7211', 'size': 35125}}
for _name, _binding in _QUALITY_CONTRACT_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact Stage 6 guidance before distinguishing current rules from historical labels.
_STAGE6_GUIDANCE_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_STAGE6_GUIDANCE_REPLAY_REFERENCES = {
    'references/stages/stage-06-stars-separation.md': {
        'sha256': '910301a63e4e64d98eb1fdbb8471107e15c61fbbe8084667eaad6b4d48b047d7', 'size': 18578}}
for _name, _binding in _STAGE6_GUIDANCE_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact safety guidance before delegating pyscript adapters to the frozen policy map.
_SIRIL_SAFETY_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_SIRIL_SAFETY_REPLAY_REFERENCES = {
    'references/siril-safety.md': {
        'sha256': '864464c63d870fe4646b4661aa87ddfe14d0d49bb7cb5853329152101c9dd00b', 'size': 7624}}
for _name, _binding in _SIRIL_SAFETY_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact Stage 7 guidance before routing the exception summary to the shared rules.
_STAGE7_EXCEPTION_LINK_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_STAGE7_EXCEPTION_LINK_REPLAY_REFERENCES = {
    'references/stages/stage-07-stretch.md': {
        'sha256': 'd70e8953d94f396b2bb7651df1cac3ce2c12e2b98d2ba641a9af6f18cec86cda', 'size': 70965}}
for _name, _binding in _STAGE7_EXCEPTION_LINK_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact stage guidance before delegating shared color and tone contracts.
_SHARED_COLOR_ROUTING_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_SHARED_COLOR_ROUTING_REPLAY_REFERENCES = {
    'references/stages/stage-08-starless-enhance.md': {
        'sha256': 'd72eeb74d48f3d3bc34fa67d1f3e90a93866b72b38c1118e7ed6c551e1c7e0ae', 'size': 45119},
    'references/stages/stage-10-delivery-finishing.md': {
        'sha256': '9d93683fbcf5de6cd5ac1a73b1bf17292dc1165bd3f67e872c16823d2487382f', 'size': 9259}}
for _name, _binding in _SHARED_COLOR_ROUTING_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact Stage 10 guidance before restoring the legacy denoise links.
_STAGE10_DENOISE_LINK_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_STAGE10_DENOISE_LINK_REPLAY_REFERENCES = {
    'references/stages/stage-10-delivery-finishing.md': {
        'sha256': 'aa9b07cd52624557f2dea10edd1a7f170a35099d530c69bd5c64d740b3834228', 'size': 8647}}
for _name, _binding in _STAGE10_DENOISE_LINK_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


# Exact delivery guidance before removing the assumed baseline disclosures.
_DELIVERY_DISCLOSURE_REPLAY_POLICY_HASH = 'a00e8a72de5ed23ea8ee0b5d9020cd366a6b509992294c364a4c622733421b83'
_DELIVERY_DISCLOSURE_REPLAY_REFERENCES = {
    'references/delivery.md': {
        'sha256': 'b2c2516797d85f3fda5535c77a15295bab008fcfe7c1e0169a71abaafd0ee062', 'size': 13934}}
for _name, _binding in _DELIVERY_DISCLOSURE_REPLAY_REFERENCES.items():
    _LEGACY_REFERENCE_FINGERPRINTS.setdefault(_name, set()).add((_binding['sha256'], _binding['size']))


def registered_guidance_reference(payload: dict[str, Any], record: dict[str, Any]) -> bool:
    if (payload.get('execution_policy') == _PRE_ARTIFACT_FOCUS_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_ARTIFACT_FOCUS_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STEPWISE_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STEPWISE_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_HDR_ROUTING_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_HDR_ROUTING_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _DELIVERY_DISCLOSURE_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _DELIVERY_DISCLOSURE_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE10_DENOISE_LINK_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE10_DENOISE_LINK_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _SHARED_COLOR_ROUTING_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _SHARED_COLOR_ROUTING_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE7_EXCEPTION_LINK_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE7_EXCEPTION_LINK_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _SIRIL_SAFETY_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _SIRIL_SAFETY_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE6_GUIDANCE_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE6_GUIDANCE_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _QUALITY_CONTRACT_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _QUALITY_CONTRACT_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE_SEQUENCE_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE_SEQUENCE_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _SESSION_CONTRACT_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _SESSION_CONTRACT_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _CLI_CONTRACT_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _CLI_CONTRACT_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STAGE7_DOCUMENT_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STAGE7_DOCUMENT_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _DIRECT_PROTOCOL_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _DIRECT_PROTOCOL_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _ENTRYPOINT_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _ENTRYPOINT_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE7_RULES_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE7_RULES_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _DISPLAY_SCALE_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _DISPLAY_SCALE_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STAGE7_REVISION_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STAGE7_REVISION_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STAGE7_OPTIMIZATION_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STAGE7_OPTIMIZATION_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_EVIDENCE_ADMISSION_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_EVIDENCE_ADMISSION_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_CURVE_EFFECT_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256','size')} == _PRE_CURVE_EFFECT_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_CURVE_AMPLIFICATION_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256','size')} == _PRE_CURVE_AMPLIFICATION_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_RESUME_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256','size')} == _PRE_RESUME_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_TAIL_COMPARISON_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256','size')} == _PRE_TAIL_COMPARISON_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STRETCH_MORPHOLOGY_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256','size')} == _PRE_STRETCH_MORPHOLOGY_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_CONTOUR_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_CONTOUR_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_MULTISTAGE_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_MULTISTAGE_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_BACKGROUND_REVIEW_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_BACKGROUND_REVIEW_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_COLOR_CURVE_VARIANTS_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_COLOR_CURVE_VARIANTS_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE3_SKIP_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE3_SKIP_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_LOCAL_CONTRAST_MASK_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_LOCAL_CONTRAST_MASK_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STRETCH_PREFLIGHT_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STRETCH_PREFLIGHT_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STRETCH_TRIAL_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STRETCH_TRIAL_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STRETCH_ROUTING_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STRETCH_ROUTING_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STAR_STRETCH_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STAR_STRETCH_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE7_LAYOUT_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE7_LAYOUT_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE7_ROUTE_BUDGET_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE7_ROUTE_BUDGET_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _GHS_D_GUIDANCE_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _GHS_D_GUIDANCE_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STAGE7_MULTISTEP_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STAGE7_MULTISTEP_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_SUBJECT_MASK_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_SUBJECT_MASK_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_REGION_COLOR_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_REGION_COLOR_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_STAR_COLOR_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_STAR_COLOR_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_CHROMA_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_CHROMA_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_VIVID_BINDINGS['execution_policy']
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_VIVID_BINDINGS['documents'].get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _STRETCH_STOP_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _STRETCH_STOP_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _ROI_PAIRS_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _ROI_PAIRS_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get('execution_policy')) == _V4_FLOW_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ('sha256', 'size')} == _V4_FLOW_REPLAY_REFERENCES.get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_SUBJECT_PRIORITY_EXECUTION_POLICY
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_SUBJECT_PRIORITY_REFERENCES.get(record.get('path'))):
        return True
    if (payload.get('execution_policy') == _PRE_DEEPSNR_EXECUTION_POLICY
        and {k: record.get(k) for k in ('sha256', 'size')} == _PRE_DEEPSNR_REFERENCES.get(record.get('path'))):
        return True
    if (stable_hash(payload.get("execution_policy")) == _STAGE5_GUIDANCE_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ("sha256", "size")} == _STAGE5_GUIDANCE_REPLAY_REFERENCES.get(record.get("path"))):
        return True
    if (stable_hash(payload.get("execution_policy")) == _BUDGET_ROI_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ("sha256", "size")} == _BUDGET_ROI_REPLAY_REFERENCES.get(record.get("path"))):
        return True
    if (stable_hash(payload.get("execution_policy")) == _CLAHE_COMPARISON_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ("sha256", "size")} == _CLAHE_COMPARISON_REPLAY_REFERENCES.get(record.get("path"))):
        return True
    if (stable_hash(payload.get("execution_policy")) == _COLOR_CONFIG_REPLAY_POLICY_HASH
        and {k: record.get(k) for k in ("sha256", "size")} == _COLOR_CONFIG_REPLAY_REFERENCES.get(record.get("path"))):
        return True
    if payload.get("execution_policy") == _PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY:
        return {k: record.get(k) for k in ("sha256", "size")} == _PRE_RECOMPOSITION_GEOMETRY_REFERENCES.get(record.get("path"))
    if payload.get("execution_policy") == _PRE_LOCAL_HDR_EXECUTION_POLICY:
        return {k: record.get(k) for k in ("sha256", "size")} == _PRE_LOCAL_HDR_REFERENCES.get(record.get("path"))
    if payload.get("execution_policy") in (_PRE_COLOR_EXECUTION_POLICY, _PRE_MORPHOLOGY_EXECUTION_POLICY):
        return {k: record.get(k) for k in ("sha256", "size")} == _PRE_COLOR_REFERENCES.get(record.get("path"))
    if payload.get("execution_policy") == _PRE_TONE_EXECUTION_POLICY:
        return {k: record.get(k) for k in ("sha256", "size")} == _PRE_TONE_REFERENCES.get(record.get("path"))
    if payload.get("execution_policy") == _PRE_RECOMPOSITION_EXECUTION_POLICY:
        return {k: record.get(k) for k in ("sha256", "size")} == _PRE_RECOMPOSITION_REFERENCES.get(record.get("path"))
    if payload.get("execution_policy") == _PRE_DENOISE_EXECUTION_POLICY:
        return {k: record.get(k) for k in ("sha256", "size")} == _PRE_DENOISE_REFERENCES.get(record.get("path"))
    return bool(
        stable_hash(payload.get("execution_policy")) == _COMMITTED_REFERENCE_POLICY_HASH
        and (record.get("sha256"), record.get("size")) in _COMMITTED_REFERENCE_FINGERPRINTS.get(record.get("path"), ())
    )


def committed_replay_reference(session: Path, payload: dict[str, Any], run_id: str,
                               reference: dict[str, Any], protocol: str) -> dict[str, Any] | None:
    """Recover an exact outgoing reference only from a manifest-bound receipt."""
    path = session_path(session, f"runs/{run_id}.json", must_exist=True, allowed_roots=("runs",))
    entries = load_json(session / "manifest.json").get("runs", [])
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == run_id]
    if (len(matches) != 1 or matches[0].get("status") != "success"
        or matches[0].get("receipt") != path.name or matches[0].get("receipt_sha256") != sha256_file(path)):
        return None
    receipt = load_json(path)
    if receipt.get("protocol") != protocol or (protocol == "input.inspect" and receipt.get("source_path") != "@input"):
        return None
    for record in receipt.get("knowledge_validation", {}).get("references", []):
        if (isinstance(record, dict) and all(record.get(key) == reference.get(key) for key in ("path", "sha256", "role"))
            and (pre_confirmation_inspect_reference(payload, protocol, record) or registered_guidance_reference(payload, record))):
            return record
    return None


def _retired_protocol_reference_matches(payload: dict[str, Any], record: dict[str, Any]) -> bool:
    """Trust registered retired references only when their whole directory is absent."""
    policy = payload.get("execution_policy")
    if (execution_policy_is_current(payload)
        or (policy is not None and not known_execution_policy(policy))
        or not isinstance(record.get("path"), str)
        or record["path"] not in _RETIRED_PROTOCOL_REFERENCES
        or HASH_PATTERN.fullmatch(str(record.get("sha256", ""))) is None
        or type(record.get("size")) is not int
        or record["size"] < 1
        or (record.get("sha256"), record.get("size")) not in
            _LEGACY_REFERENCE_FINGERPRINTS.get(record.get("path"), set())):
        return False
    try:
        for parent in (SKILL_ROOT, SKILL_ROOT / "references"):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                return False
    except OSError:
        return False
    try:
        (SKILL_ROOT / "references/protocols").lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _current_knowledge_sources() -> tuple[dict[str, Any], Any]:
    try:
        snapshot = verify_bundle(SKILL_ROOT)
        policy_capture = read_skill_file(SKILL_ROOT, _POLICY_PATH)
        policy = strict_json_bytes(policy_capture.data, document=_POLICY_PATH)
    except BundleError as exc:
        raise ContractError(
            "knowledge_bundle_invalid",
            f"Pinned Siril knowledge Bundle is invalid: {exc}",
        ) from exc
    if (
        not isinstance(policy, dict)
        or policy.get("schema") != f"{SCHEMA_PREFIX}.command-policy.v1"
        or policy.get("contract_version") != CONTRACT_VERSION
    ):
        raise ContractError(
            "knowledge_bundle_invalid",
            "Command policy does not match standalone v1",
        )
    return bundle_verification_document(snapshot), policy_capture


def _knowledge_record(payload: dict[str, Any]) -> dict[str, Any]:
    knowledge = payload.get("knowledge")
    if not isinstance(knowledge, dict):
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session lacks the required knowledge binding",
        )
    return {
        "status": "passed",
        "session_knowledge_sha256": stable_hash(knowledge),
        "bundle_evidence": knowledge.get("bundle_evidence"),
        "manual": knowledge.get("manual"),
        "command_policy": knowledge.get("command_policy"),
    }


def _verify_session_knowledge(
    session: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    knowledge = payload.get("knowledge")
    if not isinstance(knowledge, dict) or set(knowledge) != {
        "schema",
        "bundle_evidence",
        "manual",
        "command_policy",
    }:
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session knowledge binding is missing or invalid",
        )
    if knowledge.get("schema") != f"{SCHEMA_PREFIX}.knowledge.v1":
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session knowledge schema is invalid",
        )
    evidence_record = knowledge.get("bundle_evidence")
    policy_record = knowledge.get("command_policy")
    if (
        not isinstance(evidence_record, dict)
        or set(evidence_record) != {"path", "sha256", "size"}
        or evidence_record.get("path") != _BUNDLE_EVIDENCE_PATH
        or HASH_PATTERN.fullmatch(str(evidence_record.get("sha256", ""))) is None
        or isinstance(evidence_record.get("size"), bool)
        or not isinstance(evidence_record.get("size"), int)
        or evidence_record["size"] < 1
        or not isinstance(policy_record, dict)
        or set(policy_record) != {"path", "sha256", "size"}
        or policy_record.get("path") != _POLICY_PATH
        or HASH_PATTERN.fullmatch(str(policy_record.get("sha256", ""))) is None
        or isinstance(policy_record.get("size"), bool)
        or not isinstance(policy_record.get("size"), int)
        or policy_record["size"] < 1
    ):
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session knowledge fingerprints are invalid",
        )
    evidence_path = session_path(
        session,
        session / _BUNDLE_EVIDENCE_PATH,
        must_exist=True,
        allowed_roots=("reports",),
    )
    if not fingerprint_matches({**evidence_record, "path": str(evidence_path)}):
        raise ContractError(
            "knowledge_binding_drift",
            "Frozen Bundle verification evidence changed",
        )
    current_document, policy_capture = _current_knowledge_sources()
    if load_json(evidence_path) != current_document:
        raise ContractError(
            "knowledge_binding_drift",
            "Frozen Bundle verification evidence no longer matches the pinned Bundle",
        )
    if knowledge.get("manual") != current_document.get("manual"):
        raise ContractError(
            "knowledge_binding_drift",
            "Pinned Siril manual identity changed after session initialization",
        )
    if (
        (policy_record.get("sha256"), policy_record.get("size")) != (policy_capture.sha256, policy_capture.size_bytes)
        and not (not execution_policy_is_current(payload) and
                 (policy_record.get("sha256"), policy_record.get("size")) in _LEGACY_REFERENCE_FINGERPRINTS.get(_POLICY_PATH, set()))
    ):
        raise ContractError(
            "knowledge_binding_drift",
            "Command policy changed after session initialization",
        )
    return _knowledge_record(payload)

def _preflight_session_target(path: Path) -> tuple[int, int] | None:
    """Validate a prospective session root without creating or changing it."""
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        raise ContractError("unsafe_session", f"Session root is a symlink: {path}")
    try:
        if not path.is_dir():
            raise ContractError("unsafe_session", f"Session root is not a directory: {path}")
        if any(path.iterdir()):
            raise ContractError("session_not_empty", f"Session root must be empty: {path}")
        status = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ContractError("unsafe_session", f"Cannot inspect session root: {path}") from exc
    return status.st_dev, status.st_ino


def _session_target_unchanged(path: Path, identity: tuple[int, int] | None) -> bool:
    """Best-effort compare immediately before the atomic directory commit."""
    try:
        if identity is None:
            return not os.path.lexists(path)
        if not os.path.lexists(path) or path.is_symlink() or not path.is_dir():
            return False
        if any(path.iterdir()):
            return False
        status = path.stat(follow_symlinks=False)
        return (status.st_dev, status.st_ino) == identity
    except OSError:
        return False


_PIXINSIGHT_STRETCH_OPERATORS = (
    "histogramtransformation",
    "curvestransformation",
    "arcsinhstretch",
    "generalizedhyperbolicstretch",
    "ghs",
    "maskedstretch",
    "exponentialtransformation",
)


def _inspect_xisf_history(path: Path) -> dict[str, Any]:
    """Inspect XISF XML for WBPP/FBPP signatures and stretching operations."""
    result = {
        "is_wbpp": False,
        "has_stretch": False,
        "stretch_ops": [],
        "is_master_light": False,
    }
    try:
        root, _, _ = artifacts._read_xisf_xml(path)
    except Exception:
        return result

    history_texts: list[str] = []
    comment_texts: list[str] = []

    image = next((elem for elem in root if artifacts._xml_local_name(elem.tag) == 'Image'), None)
    if image is None:
        return result
    for elem in image.iter():
        tag = artifacts._xml_local_name(elem.tag)
        if tag == "FITSKeyword":
            name = elem.attrib.get("name", "").strip().upper()
            val = str(elem.attrib.get("value", ""))
            comment = str(elem.attrib.get("comment", ""))
            if name == "IMAGETYP" and "master light" in val.lower():
                result["is_master_light"] = True
            if name == "WBPPCROP":
                result["is_wbpp"] = True
            if name in ("COMMENT", "HISTORY"):
                comment_texts.append(val + " " + comment)
        elif tag == "Property":
            prop_id = elem.attrib.get("id", "")
            if "Signature:Integration" in prop_id or "Signature:Preprocessing" in prop_id:
                result["is_wbpp"] = True
            if "ProcessingHistory" in prop_id:
                if elem.text:
                    history_texts.append(elem.text)

    all_comments = " ".join(comment_texts).lower()
    if any(k in all_comments for k in (
        "pixinsight image preprocessing pipeline",
        "fast batch preprocessing",
        "weightedbatchpreprocessing",
        "imageintegration",
    )):
        result["is_wbpp"] = True

    combined_history = " ".join(history_texts).lower()
    detected_stretches = [op for op in _PIXINSIGHT_STRETCH_OPERATORS if op in combined_history]
    if detected_stretches:
        result["has_stretch"] = True
        result["stretch_ops"] = detected_stretches

    return result


def detect_master_input_state(
    input_path: Path,
    header: dict[str, Any] | None = None,
    filter_profile: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Recognize explicit Image 0 stretch history; appearance cannot prove linearity."""
    if input_path.suffix.lower() == ".xisf":
        history = _inspect_xisf_history(input_path)
        if history["has_stretch"]:
            ops = ", ".join(history["stretch_ops"])
            return "nonlinear", [f"fits header: PixInsight Image 0 processing history contains stretching ({ops}); input_state=nonlinear"]
    return "unknown", []


def _recover_input_evidence(input_path: Path, input_record: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Recover explicit declarations from validated sessions for identical bytes."""
    recovered: dict[str, str] = {}
    evidence: list[str] = []
    seen: set[str] = set()
    for path in sorted(input_path.parent.glob("*/session.json")):
        try:
            candidate = load_json(path)
            record = candidate.get("input", {})
            if not isinstance(record, dict) or any(
                record.get(key) != input_record[key] for key in ("sha256", "size")
            ):
                continue
            _, candidate, _ = load_session(path.parent)
        except (ContractError, OSError):
            continue
        candidate = effective_session_payload(path.parent, candidate)
        context = candidate["context"]
        statements = context.get("state_evidence", [])
        if not isinstance(statements, list):
            continue
        for statement in statements:
            if not isinstance(statement, str) or not statement.lower().startswith(
                ("user confirmed ", "acquisition record: ", "fits header: ")
            ):
                continue
            statement = statement.split(" [source_session=", 1)[0]
            if statement.lower().startswith("fits header: ") and "verified unstretched linear master" in statement.lower():
                continue
            supported = False
            for key, allowed in (
                ("input_state", {"linear", "nonlinear"}),
                ("channel_mode", {"mono", "broadband", "narrowband", "dualband-osc"}),
            ):
                value = context.get(key)
                if not isinstance(value, str) or value not in allowed or f"{key}={value}" not in statement.replace(";", " ").split():
                    continue
                if key in recovered and recovered[key] != value:
                    raise ContractError(
                        "input_evidence_conflict",
                        f"Conflicting {key} declarations for this master; confirm its source before initialization",
                    )
                recovered[key] = value
                supported = True
            if supported and statement not in seen:
                seen.add(statement)
                confirmation = candidate.get("input_confirmation")
                confirmation_source = (
                    f"; input_confirmation={path.parent.resolve() / confirmation['path']}"
                    f"; input_confirmation_sha256={confirmation['sha256']}" if confirmation else ""
                )
                evidence.append(
                    f"{statement} [source_session={path.resolve()}; session_sha256={candidate['session_sha256']}{confirmation_source}]"
                )
    return recovered, evidence


def init_session(
    input_value: str,
    session_value: str,
    *,
    input_state: str,
    state_evidence: Sequence[str],
    channel_mode: str,
    channel_map: dict[str, str] | None,
    target_name: str | None,
    target_type: str,
    style: str,
    stars: str,
    offline: bool,
    keep_intermediates: bool,
    container_validation: str = "siril",
    dualband: str = "off",
) -> dict[str, Any]:
    input_raw = _absolute(input_value)
    if input_raw.is_symlink() or not input_raw.is_file():
        raise ContractError("input_missing", f"Input master is missing or unsafe: {input_raw}")
    input_path = input_raw.resolve()
    if input_path.suffix.lower() not in INPUT_EXTENSIONS:
        raise ContractError("input_format_unsupported", f"Unsupported master format: {input_path.suffix}")
    if input_state not in {"auto", "linear", "nonlinear", "unknown"}:
        raise ContractError("input_state_invalid", f"Unsupported input state: {input_state}")
    evidence = list(state_evidence)
    if input_state in {"linear", "nonlinear"} and not evidence:
        raise ContractError("state_evidence_missing", "Known input state requires concrete evidence")
    if input_state in {"auto", "unknown"} and evidence:
        raise ContractError("state_evidence_invalid", "Unknown input state cannot include state evidence")
    if input_state in {"linear", "nonlinear"}:
        _validate_input_statements(evidence, {"input_state": input_state}, {"input_state": input_state, "channel_mode": channel_mode})
    if channel_mode not in {"mono", "broadband", "narrowband", "dualband-osc", "unknown"}:
        raise ContractError("channel_mode_invalid", f"Unsupported channel mode: {channel_mode}")
    if dualband not in {"auto", "off"}:
        raise ContractError("dualband_invalid", "Dualband processing must be auto or off")
    if style not in {"vivid", "natural", "balanced", "artistic"}:
        raise ContractError("style_invalid", f"Unsupported style: {style}")
    if stars not in {"adaptive", "preserve", "standalone-starless"}:
        raise ContractError("stars_policy_invalid", f"Unsupported star policy: {stars}")
    if container_validation not in {"siril", "strict"}:
        raise ContractError(
            "container_validation_invalid",
            f"Unsupported container validation mode: {container_validation}",
        )

    session = _absolute(session_value)
    target_identity = _preflight_session_target(session)
    input_record = fingerprint(input_path, role="stacked_master")
    requested_input_state = input_state
    if input_state == "auto":
        recovered, recovered_evidence = _recover_input_evidence(input_path, input_record)
        input_state = recovered.get("input_state", "unknown")
        if input_state != "unknown":
            evidence = recovered_evidence
            if channel_mode == "unknown":
                channel_mode = recovered.get("channel_mode", "unknown")
    header = artifacts.read_scientific_header(input_path) if input_path.suffix.lower() in {".fit", ".fits", ".fts", ".xisf"} else {}
    filter_profile = filter_from_header(header)
    count = 1 if header.get("NAXIS") == 2 else header.get("NAXIS3") if header.get("NAXIS") == 3 else None
    if input_path.suffix.lower() == '.xisf' and (
        (channel_mode == 'mono' and count != 1) or
        (channel_mode in {'broadband', 'dualband-osc'} and count != 3)
    ):
        raise ContractError('channel_mode_invalid', f'Declared {channel_mode} conflicts with XISF Image 0 channel count {count}')
    detected_mode = "unknown"
    filter_class = filter_profile["class"]
    if isinstance(count, int) and not isinstance(count, bool) and count in {1, 3}:
        if filter_class == "dual_band":
            detected_mode = "dualband-osc" if count == 3 else "narrowband"
        elif filter_class in {"narrowband", "multi_band"}:
            detected_mode = "narrowband"
        elif filter_class in {"broadband", "broadband_channel", "luminance"}:
            detected_mode = "broadband" if count == 3 else "mono"
        elif count == 1:
            detected_mode = "mono"
    if channel_mode == "unknown":
        channel_mode = detected_mode
    elif filter_class != "unknown" and detected_mode not in {"unknown", channel_mode}:
        raise ContractError(
            "filter_evidence_conflict",
            f"Declared channel mode {channel_mode} conflicts with header filter {filter_profile['raw_filter']} ({detected_mode}); confirm its source",
        )
    if requested_input_state in {"linear", "nonlinear"}:
        _validate_input_statements(evidence, {"input_state": input_state}, {"input_state": input_state, "channel_mode": channel_mode})
    if requested_input_state == "auto" and input_state == "unknown":
        detected_state, detected_evidence = detect_master_input_state(input_path, header, filter_profile)
        if detected_state != "unknown":
            input_state = detected_state
            evidence = detected_evidence
    if input_state == "linear" and input_path.suffix.lower() == ".xisf" and _inspect_xisf_history(input_path)["has_stretch"]:
        raise ContractError("input_evidence_conflict", "Linear input conflicts with Image 0 stretching history")
    bundle_document, policy_capture = _current_knowledge_sources()
    probe = tooling.probe_tools(offline=offline)
    staging: Path | None = None
    try:
        session.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{session.name or 'session'}.staging-",
                dir=session.parent,
            )
        )
        staging.chmod(0o700)
        for relative in SESSION_DIRECTORIES:
            (staging / relative).mkdir(parents=True, exist_ok=False)
        probe_path = staging / "reports" / "tool-probe.json"
        atomic_write_json(probe_path, probe)
        bundle_evidence_path = staging / _BUNDLE_EVIDENCE_PATH
        atomic_write_json(bundle_evidence_path, bundle_document)
        knowledge = {
            "schema": f"{SCHEMA_PREFIX}.knowledge.v1",
            "bundle_evidence": {
                **fingerprint(bundle_evidence_path, include_path=False),
                "path": _BUNDLE_EVIDENCE_PATH,
            },
            "manual": bundle_document["manual"],
            "command_policy": {
                "path": _POLICY_PATH,
                "sha256": policy_capture.sha256,
                "size": policy_capture.size_bytes,
            },
        }
        session_payload = {
            "schema": f"{SCHEMA_PREFIX}.session.v1",
            "contract_version": CONTRACT_VERSION,
            "created_at": utc_now(),
            "execution_policy": dict(EXECUTION_POLICY),
            "input": input_record,
            "context": {
                "input_state": input_state,
                "state_evidence": evidence,
                "channel_mode": channel_mode,
                "channel_map": channel_map,
                "filter_profile": filter_profile,
                "target_name": target_name,
                "target_type": target_type,
                "style": style,
                "dualband": dualband,
                "stars": stars,
                "offline": bool(offline),
                "keep_intermediates": bool(keep_intermediates),
                "container_validation": container_validation,
            },
            "tool_probe": {
                "path": "reports/tool-probe.json",
                "sha256": sha256_file(probe_path),
            },
            "knowledge": knowledge,
        }
        session_payload["session_sha256"] = stable_hash(session_payload)
        atomic_write_json(staging / "session.json", session_payload)
        manifest = {
            "schema": f"{SCHEMA_PREFIX}.manifest.v1",
            "contract_version": CONTRACT_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "input_sha256": input_record["sha256"],
            "runs": [],
            "finalization": None,
        }
        atomic_write_json(staging / "manifest.json", manifest)
        current_bundle_document, current_policy_capture = _current_knowledge_sources()
        if (
            current_bundle_document != bundle_document
            or current_policy_capture.sha256 != policy_capture.sha256
            or current_policy_capture.size_bytes != policy_capture.size_bytes
        ):
            raise ContractError(
                "knowledge_binding_drift",
                "Pinned Bundle or command policy changed during session initialization",
            )
        _fsync_directory(staging)
        if not _session_target_unchanged(session, target_identity):
            raise ContractError(
                "session_not_empty",
                f"Session root changed or became occupied during initialization: {session}",
            )
        try:
            os.replace(staging, session)
        except OSError as exc:
            raise ContractError(
                "session_init_failed",
                f"Cannot atomically commit session root: {session}",
            ) from exc
        staging = None
        _fsync_directory(session.parent)
    except ContractError:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise ContractError(
            "session_init_failed",
            f"Cannot initialize session root: {session}",
        ) from exc

    session = session.resolve()
    return {
        "schema": f"{SCHEMA_PREFIX}.init-result.v1",
        "status": "ready",
        "session": str(session),
        "contract_version": CONTRACT_VERSION,
        "execution_policy": dict(EXECUTION_POLICY),
        "input": input_record,
        "context": session_payload["context"],
        "tool_probe": probe,
        "knowledge": knowledge,
    }


def load_session(session_value: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    session = safe_session_root(session_value)
    payload = load_json(session / "session.json")
    manifest = load_json(session / "manifest.json")
    if payload.get("schema") != f"{SCHEMA_PREFIX}.session.v1" or payload.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("unsupported_session_contract", "Only standalone contract 1 sessions are supported")
    unsigned = dict(payload)
    session_hash = unsigned.pop("session_sha256", None)
    if session_hash != stable_hash(unsigned):
        raise ContractError("session_hash_drift", "session.json changed after initialization")
    if manifest.get("schema") != f"{SCHEMA_PREFIX}.manifest.v1" or manifest.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("unsupported_session_contract", "Manifest is not standalone contract 1")
    context = payload.get("context")
    if (
        not isinstance(context, dict)
        or context.get("container_validation") not in {"siril", "strict"}
    ):
        raise ContractError(
            "unsupported_session_contract",
            "Session container validation mode is invalid",
        )
    execution_policy = payload.get("execution_policy")
    if execution_policy is not None and not known_execution_policy(execution_policy):
        raise ContractError(
            "unsupported_session_contract",
            "Session execution policy is not supported; create a new session",
        )
    if manifest.get("input_sha256") != payload.get("input", {}).get("sha256"):
        raise ContractError("session_hash_drift", "Manifest input differs from session input")
    if not fingerprint_matches(payload.get("input")):
        raise ContractError("input_hash_drift", "Original stacked master changed")
    probe_record = payload.get("tool_probe")
    if (
        not isinstance(probe_record, dict)
        or set(probe_record) != {"path", "sha256"}
        or probe_record.get("path") != "reports/tool-probe.json"
        or HASH_PATTERN.fullmatch(str(probe_record.get("sha256", ""))) is None
    ):
        raise ContractError("session_hash_drift", "Session tool probe binding is invalid")
    probe_path = session_path(
        session,
        session / "reports" / "tool-probe.json",
        must_exist=True,
        allowed_roots=("reports",),
    )
    if sha256_file(probe_path) != probe_record["sha256"]:
        raise ContractError("session_hash_drift", "Frozen tool probe changed")
    _verify_session_knowledge(session, payload)
    effective_session_payload(session, payload, manifest)
    return session, payload, manifest


def _validate_input_statements(evidence: Sequence[str], required: dict[str, str], values: dict[str, str]) -> list[str]:
    statements = list(evidence)
    if (not statements or len(statements) > 32 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 500
        or item != " ".join(item.split())
        or not item.lower().startswith(("user confirmed ", "acquisition record: ", "fits header: "))
        for item in statements
    )):
        raise ContractError("state_evidence_invalid", "Confirmation requires concrete user, acquisition or header declarations")
    for key, value in required.items():
        if not any(f"{key}={value}" in item.replace(";", " ").split() for item in statements):
            raise ContractError("state_evidence_missing", f"Source evidence must explicitly declare {key}={value}")
    for key, value in values.items():
        if value == "unknown":
            continue
        for item in statements:
            declarations = [token.split("=", 1)[1] for token in item.replace(";", " ").split()
                            if token.startswith(f"{key}=")]
            if any(declared != value for declared in declarations):
                raise ContractError("input_evidence_conflict", f"Conflicting {key} evidence")
    return statements


def confirmed_input_context(payload: dict[str, Any], fields: dict[str, str], evidence: Sequence[str]) -> dict[str, Any]:
    """Validate explicit source declarations, never infer state from image appearance."""
    context = dict(payload["context"])
    if not fields or set(fields) - _INPUT_CONTEXT_VALUES.keys():
        raise ContractError("input_confirmation_invalid", "Confirmation requires unknown input context fields")
    statements = _validate_input_statements(evidence, fields, {})
    for key, allowed in _INPUT_CONTEXT_VALUES.items():
        original = context.get(key, "unknown")
        if original not in allowed | {"unknown"}:
            raise ContractError("input_confirmation_invalid", f"Invalid frozen {key}")
        value = fields.get(key, original)
        if not isinstance(value, str) or value not in allowed:
            raise ContractError("input_context_unresolved", f"Reliable {key} evidence is still required")
        if key in fields:
            if original != "unknown":
                raise ContractError("input_evidence_conflict", f"Confirmation cannot replace known {key}")
            if not any(f"{key}={value}" in item.replace(";", " ").split() for item in statements):
                raise ContractError("state_evidence_missing", f"Source evidence must explicitly declare {key}={value}")
        for item in statements:
            declarations = [token.split("=", 1)[1] for token in item.replace(";", " ").split()
                            if token.startswith(f"{key}=")]
            if any(declared != value for declared in declarations):
                raise ContractError("input_evidence_conflict", f"Conflicting {key} evidence")
        context[key] = value
    context["state_evidence"] = [*context.get("state_evidence", []), *statements]
    return context


def read_input_confirmation(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ContractError("input_confirmation_drift", "Input confirmation is missing or unsafe")
    try:
        data = path.read_bytes()
        record = strict_json_bytes(data, document=str(path))
    except (BundleError, OSError) as exc:
        raise ContractError("input_confirmation_invalid", str(exc)) from exc
    if not isinstance(record, dict):
        raise ContractError("input_confirmation_invalid", "Input confirmation must be an object")
    observed = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if fingerprint(path, include_path=False) != observed:
        raise ContractError("input_confirmation_drift", "Input confirmation changed while being read")
    return record, observed


def validate_input_confirmation(session: Path, payload: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    required = {"schema", "session_sha256", "input", "run_id", "run_receipt", "review",
                "confirmed_fields", "state_evidence", "created_at"}
    if (set(record) != required or record.get("schema") != f"{SCHEMA_PREFIX}.input-confirmation.v1"
        or not known_execution_policy(payload.get("execution_policy"))
        or record.get("session_sha256") != payload.get("session_sha256")
        or record.get("input") != {key: payload["input"][key] for key in ("sha256", "size")}
        or not isinstance(record.get("created_at"), str) or not record["created_at"]
        or not isinstance(record.get("confirmed_fields"), dict)
        or not isinstance(record.get("state_evidence"), list)):
        raise ContractError("input_confirmation_invalid", "Confirmation identity or schema is invalid")
    context = confirmed_input_context(payload, record["confirmed_fields"], record["state_evidence"])
    run_id = record["run_id"]
    from deep_sky_siril_contract import RUN_ID_PATTERN
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ContractError("input_confirmation_invalid", "Confirmation inspect run ID is invalid")
    for key, root in (("run_receipt", "runs"), ("review", "reviews")):
        binding = record[key]
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256", "size"}:
            raise ContractError("input_confirmation_invalid", f"Confirmation {key} binding is invalid")
        path = session_path(session, binding["path"], allowed_roots=(root,))
        if {**fingerprint(path, include_path=False), "path": path.relative_to(session).as_posix()} != binding:
            raise ContractError("input_confirmation_drift", f"Confirmation {key} changed")
    run = load_json(session / record["run_receipt"]["path"])
    review = load_json(session / record["review"]["path"])
    entries = load_json(session / "manifest.json").get("runs")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == run_id] if isinstance(entries, list) else []
    if (len(matches) != 1 or matches[0].get("status") != "success"
        or matches[0].get("receipt") != f"{run_id}.json"
        or matches[0].get("receipt_sha256") != record["run_receipt"]["sha256"]):
        raise ContractError("input_confirmation_drift", "Confirmed inspection is no longer manifest-bound")
    if (record["run_receipt"]["path"] != f"runs/{run_id}.json"
        or run.get("id") != run_id or run.get("status") != "success"
        or run.get("protocol") != "input.inspect" or run.get("source_path") != "@input"
        or run.get("source") != record["input"]
        or review.get("run_id") != run_id or review.get("verdict") != "accept"
        or review.get("protocol") != "input.inspect"
        or review.get("run_receipt_sha256") != record["run_receipt"]["sha256"]):
        raise ContractError("input_confirmation_invalid", "Confirmation requires an accepted immutable input inspection")
    return context


def effective_session_payload(session: Path, payload: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve committed supplemental declarations without rewriting frozen context."""
    if "input_confirmation" in payload:
        payload = load_json(session / "session.json")
        unsigned = dict(payload)
        if unsigned.pop("session_sha256", None) != stable_hash(unsigned):
            raise ContractError("session_hash_drift", "session.json changed after initialization")
    manifest = manifest if manifest is not None else load_json(session / "manifest.json")
    binding = manifest.get("input_confirmation")
    if binding is None:
        return payload
    if (not isinstance(binding, dict) or set(binding) != {"path", "sha256", "size"}
        or binding.get("path") != INPUT_CONFIRMATION_PATH):
        raise ContractError("input_confirmation_invalid", "Manifest confirmation binding is invalid")
    path = session_path(session, INPUT_CONFIRMATION_PATH, allowed_roots=("reports",))
    record, observed = read_input_confirmation(path)
    if {**observed, "path": INPUT_CONFIRMATION_PATH} != binding:
        raise ContractError("input_confirmation_drift", "Committed input confirmation changed")
    context = validate_input_confirmation(session, payload, record)
    return {**payload, "context": context, "input_confirmation": binding}


def _receipt_bindings_unchanged(
    session: Path,
    payload: dict[str, Any],
    receipt: dict[str, Any],
) -> bool:
    try:
        effective = effective_session_payload(session, payload)
        binding = effective.get("input_confirmation")
        if receipt.get("input_confirmation") != binding and (
            receipt.get("protocol") != "input.inspect" or "input_confirmation" in receipt
        ):
            return False
        if receipt.get("protocol") != "input.inspect":
            payload = effective
    except ContractError:
        return False

    def session_file_matches(record: Any, roots: Sequence[str]) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            return False
        try:
            path = session_path(
                session,
                session / record["path"],
                must_exist=True,
                allowed_roots=roots,
            )
        except ContractError:
            return False
        return fingerprint_matches({**record, "path": str(path)})

    def skill_file_matches(record: Any) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            return False
        try:
            capture = read_skill_file(SKILL_ROOT, record["path"])
        except BundleError:
            return _retired_protocol_reference_matches(payload, record)
        if capture.sha256 == record.get("sha256") and capture.size_bytes == record.get("size"):
            return True
        if (pre_confirmation_inspect_reference(payload, receipt.get("protocol"), record)
            or registered_guidance_reference(payload, record)):
            return True
        trusted = _LEGACY_REFERENCE_FINGERPRINTS.get(record["path"], set())
        return bool(
            not execution_policy_is_current(payload)
            and (record.get("sha256"), record.get("size")) in trusted
        )

    def current_receipt_evidence_matches() -> bool:
        if payload.get("execution_policy") is None:
            return True
        log_record = receipt.get("log")
        if not session_file_matches(log_record, ("logs",)):
            return False
        assert isinstance(log_record, dict)
        try:
            log_path = session_path(
                session,
                session / str(log_record["path"]),
                must_exist=True,
                allowed_roots=("logs",),
            )
            log_text = log_path.read_text(encoding="utf-8")
            script_record = receipt.get("script")
            if not isinstance(script_record, dict):
                return False
            script_path = session_path(
                session,
                session / str(script_record.get("path", "")),
                must_exist=True,
                allowed_roots=("scripts",),
            )
            # Replay under the policy the session froze, not the current one: the label
            # and the effective_offline value are both version-dependent.
            current_network = classify_siril_network(
                script_path.read_text(encoding="utf-8"),
                protocol=str(receipt.get("protocol", "")),
                session_offline=bool(payload.get("context", {}).get("offline")),
                policy=frozen_network_policy(payload.get("execution_policy")),
            )
            legacy_network = network_policy_for(payload.get("execution_policy"))
            if legacy_network is not None:
                current_network["policy"] = legacy_network
        except (ContractError, OSError, TypeError, UnicodeError, ValueError):
            return False
        validations = receipt.get("output_validations")
        if not isinstance(validations, dict) or not validations:
            return False
        outputs_valid = all(
            isinstance(result, dict) and result.get("passed") is True
            for result in validations.values()
        )
        display_names = [
            Path(path).name
            for path, result in validations.items()
            if isinstance(path, str)
            and Path(path).suffix.lower() in DISPLAY_IMAGE_SUFFIXES
            and isinstance(result, dict)
            and result.get("passed") is True
        ]
        diagnostics = artifacts.diagnose_siril_log(
            log_text,
            exit_code=int(receipt.get("exit_code", -1)),
            timed_out=receipt.get("timed_out") is True,
            execution_valid=(
                receipt.get("exit_code") == 0
                and receipt.get("timed_out") is False
                and outputs_valid
            ),
            validated_display_names=display_names,
            policy=payload["execution_policy"]["log_diagnostics"],
        )
        runtime = receipt.get("runtime")
        invocation = receipt.get("invocation")
        return bool(
            artifacts.normalize_log_diagnostics(receipt.get("log_diagnostics"))
            == artifacts.normalize_log_diagnostics(diagnostics)
            and isinstance(runtime, dict)
            and runtime.get("offline")
            == bool(payload.get("context", {}).get("offline"))
            and runtime.get("network") == current_network
            and isinstance(invocation, list)
            and all(isinstance(value, str) for value in invocation)
            and ("--offline" in invocation)
            == bool(current_network["effective_offline"])
        )

    try:
        if ((payload.get('execution_policy') or {}).get('psf_provenance') in {'native_star_samples_v1', 'native_star_samples_v2'}
            and receipt.get('protocol') == 'restoration.deconvolve'):
            from deep_sky_siril_psf import (deconvolution_evidence_matches, prepare_deconvolution,
                                             measure_deconvolution)
            from deep_sky_siril_processing import script_commands
            source = Path(payload['input']['path']) if receipt['source_path'] == '@input' else session_path(
                session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',))
            primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
            script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            binding = prepare_deconvolution(session, source, primary, script_commands(script.read_text(encoding='utf-8')),
                policy=payload['execution_policy']['psf_provenance'], run_id=receipt['id'],
                restoration_policy=payload['execution_policy'].get('restoration'))
            log = session_path(session, receipt['log']['path'], must_exist=True, allowed_roots=('logs',))
            # Bulk per-star rows were externalized after these receipts were written;
            # both shapes reduce to one comparable form so old sessions still replay.
            if (binding != receipt.get('deconvolution_binding')
                or not deconvolution_evidence_matches(
                    measure_deconvolution(session, source, primary, binding, log.read_text(encoding='utf-8')),
                    receipt.get('processing_checks', {}).get('deconvolution'))):
                return False
        if receipt.get('chroma_denoise'):
            from deep_sky_siril_chroma import prepare, measure, POLICY
            from deep_sky_siril_processing import script_commands
            source = session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',))
            primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
            script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            value = script.read_text()
            if (payload.get('execution_policy', {}).get('chroma_denoise') != POLICY
                or prepare(session, source, primary, value, script_commands(value), receipt['id']) != receipt['chroma_denoise']
                or measure(session, source, primary, receipt['chroma_denoise']) != receipt.get('processing_checks', {}).get('chroma_denoise')):
                return False
        restoration_policy = payload.get('execution_policy', {}).get('restoration')
        if restoration_policy and receipt.get('protocol') in {'restoration.denoise', 'restoration.deconvolve'}:
            from deep_sky_siril_processing import prepare_restoration, validate_processing_outputs, script_commands
            source = Path(payload['input']['path']) if receipt['source_path'] == '@input' else session_path(
                session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',))
            primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
            script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            expected = prepare_restoration(session, source, primary, script_commands(script.read_text(encoding='utf-8')), receipt['protocol'])
            if expected != receipt.get('restoration_validation') or expected['policy'] != restoration_policy:
                return False
            if receipt['protocol'] == 'restoration.denoise' and payload['execution_policy'].get('linear_denoise') == 'agent_proposal_v1':
                from deep_sky_siril_denoise import prepare as prepare_denoise
                value = script.read_text(encoding='utf-8')
                if prepare_denoise(session, payload, source, value, script_commands(value), expected,
                                   receipt['id'], verify_parent=False) != receipt.get('linear_denoise'):
                    return False
            actual = validate_processing_outputs(session, source, primary, receipt, execution_policy=payload['execution_policy'])
            if any(receipt.get('processing_checks', {}).get(key) != value for key, value in actual.items()):
                return False
        if (receipt.get('protocol') == 'stretch' and (receipt.get('dualband_transfer')
            or '# dualband-stretch' in session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',)).read_text())):
            from deep_sky_siril_dualband import declaration as dualband_declaration, validate_stretch as validate_dualband, measure_outputs as measure_dualband
            script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            text = script.read_text(encoding='utf-8')
            declared = dualband_declaration(text)
            if bool(declared) != bool(receipt.get('dualband_transfer')):
                return False
            if declared:
                source = session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',))
                primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
                effective = effective_session_payload(session, payload)
                binding = validate_dualband(session, effective, source, primary, text, receipt['id'], verify_parent=False)
                if (binding != receipt['dualband_transfer'] or binding['chains']['rgb'] != receipt.get('transfer_chain')
                    or measure_dualband(session, source, primary, binding) != receipt.get('processing_checks', {}).get('dualband')):
                    return False
        if receipt.get('adaptive_parameters') is not None:
            from deep_sky_siril_stretch import adaptive_binding, measure_adaptive_output
            from deep_sky_siril_processing import script_commands, transfer_chain
            source = (Path(payload['input']['path']) if receipt['source_path'] == '@input' else
                      session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',)))
            primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
            script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            text = script.read_text(encoding='utf-8')
            binding = adaptive_binding(session, payload, source, text, transfer_chain(text, primary), receipt['id'])
            if (binding != receipt['adaptive_parameters']
                or measure_adaptive_output(session, source, primary, binding) !=
                   receipt.get('processing_checks', {}).get('adaptive_parameters')):
                return False
        background_policy = (payload.get('execution_policy') or {}).get('background')
        if receipt.get('protocol') == 'background.subtract' and background_policy in {'heldout_channel_gate_v1', 'heldout_material_gate_v2', 'heldout_material_gate_v3', 'heldout_material_gate_v4', 'heldout_review_gate_v5'}:
            bg = receipt.get('background_validation', {})
            check = receipt.get('processing_checks', {})
            measured = check.get('background_validation', {})
            if check.get('status') != 'passed' or measured.get('status') != 'passed':
                return False
            regions = session_path(session, bg.get('regions', ''), must_exist=True, allowed_roots=('reports',))
            contract = session_path(session, bg.get('sample_contract', ''), must_exist=True, allowed_roots=('reports',))
            report = session_path(session, measured.get('path', ''), must_exist=True, allowed_roots=('reports',))
            if (report.parent != session / 'reports' / receipt['id'] or measured.get('path') != bg.get('report')
                or regions.parent != report.parent or contract.parent != report.parent
                or sha256_file(regions) != bg.get('regions_sha256')
                or sha256_file(contract) != bg.get('sample_contract_sha256')
                or fingerprint(report, include_path=False) != {k: measured.get(k) for k in ('sha256', 'size')}):
                return False
            evidence = load_json(report)
            outputs = {r['path']: r['sha256'] for r in receipt.get('outputs', [])}
            schema = 'starun-siril.background-validation.v1' if background_policy == 'heldout_channel_gate_v1' else 'starun-siril.background-validation.v2'
            if (evidence.get('schema') != schema
                or evidence.get('policy') != background_policy or evidence.get('status') != 'passed'
                or evidence.get('regions_sha256') != bg.get('regions_sha256')
                or evidence.get('images') != {'parent': receipt.get('source', {}).get('sha256'),
                                             'candidate': outputs.get(receipt.get('primary_output'))}):
                return False
            sampling_policy = (payload.get('execution_policy') or {}).get('background_sampling')
            if any(record.get('sampling_policy') != sampling_policy for record in (bg, measured, evidence)):
                return False
            if background_policy in {'heldout_material_gate_v2', 'heldout_material_gate_v3', 'heldout_material_gate_v4', 'heldout_review_gate_v5'}:
                if (evidence.get('decision') not in {'review_required', 'skip'}
                    or measured.get('decision') != evidence['decision']
                    or measured.get('warnings') != evidence.get('warnings')
                    or evidence.get('display') != bg.get('display')):
                    return False
                from deep_sky_siril_processing import validate_background_display, script_commands
                script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
                source = Path(payload['input']['path']) if receipt['source_path'] == '@input' else session / receipt['source_path']
                script_text = script.read_text(encoding='utf-8')
                commands = script_commands(script_text)
                display = validate_background_display(session, source, session / receipt['primary_output'],
                                                      commands, policy=background_policy)
                if display != bg.get('display') or any(display[k] not in outputs for k in ('parent_preview', 'candidate_preview')):
                    return False
                if background_policy in {'heldout_material_gate_v3', 'heldout_material_gate_v4', 'heldout_review_gate_v5'}:
                    bridge_path = session_path(session, bg.get('bridge_receipt', ''), must_exist=True, allowed_roots=('reports',))
                    bridge = load_json(bridge_path)
                    precision = evidence.get('image_precision', {})
                    if (precision != bridge.get('image_precision') or precision.get('status') != 'verified'
                        or precision.get('source_sha256') != receipt.get('source', {}).get('sha256')
                        or precision.get('dtype') != 'float32' or precision.get('bitpix') != -32
                        or precision.get('endpoints_preserved') is not True
                        or bridge_path.relative_to(session).as_posix() not in outputs
                        or sha256_file(bridge_path) != outputs[bridge_path.relative_to(session).as_posix()]):
                        return False
                if background_policy in {'heldout_material_gate_v4', 'heldout_review_gate_v5'}:
                    from deep_sky_siril_background import measure_background, load_regions, validate_samples
                    from deep_sky_siril_artifacts import read_fits_header
                    doc, _ = load_regions(regions, source, scientific=True, sampling_policy=sampling_policy, layout=bg.get('layout', 'standard'))
                    coverage = validate_samples(load_json(contract), doc, sampling_policy=sampling_policy, layout=bg.get('layout', 'standard'))
                    requested = [[p['x'], p['y']] for p in load_json(contract)['fit_samples']]
                    from siril_background_samples import sample_positions_match, RECEIPT_SCHEMA
                    if (bridge.get('schema') != RECEIPT_SCHEMA or bridge.get('status') != 'verified'
                        or bridge.get('source') != str(source)
                        or bridge.get('contract', {}).get('sha256') != bg['sample_contract_sha256']
                        or bridge.get('requested_positions') != requested
                        or not sample_positions_match(requested, bridge.get('installed_positions', []))
                        or read_fits_header(session / receipt['primary_output']).get('BITPIX') != -32):
                        return False
                    actual = measure_background(source, session / receipt['primary_output'], regions,
                        regions_sha256=bg['regions_sha256'], policy=background_policy, sampling_policy=sampling_policy,
                        layout=bg.get('layout', 'standard'), layout_reason=bg.get('layout_reason'))
                    if background_policy == 'heldout_review_gate_v5':
                        from deep_sky_siril_processing import background_declaration
                        declaration = background_declaration(script_text, commands, background_policy)
                        if (bg.get('policy') != background_policy
                            or measured.get('policy') != background_policy
                            or bg.get('layout') != declaration.get('layout', 'standard')
                            or bg.get('layout_reason') != declaration.get('layout_reason')
                            or bg.get('coverage') != coverage
                            or measured.get('layout') != bg.get('layout')
                            or measured.get('layout_reason') != bg.get('layout_reason')):
                            return False
                        actual['fit_coverage'] = coverage
                    if (precision.get('schema') != 'starun-siril.native-float32-identity.v1'
                        or precision.get('tolerance') != 'two_float32_ULP'
                        or precision.get('scientific_source') != actual.get('scientific_sources', {}).get('parent')
                        or precision.get('shape') != actual.get('scientific_sources', {}).get('parent', {}).get('shape')):
                        return False
                    actual.update(image_precision=precision, display=display)
                    if payload.get('execution_policy', {}).get('color_preservation') == 'vivid_preservation_v1':
                        from deep_sky_siril_metrics import measure_color_protection
                        protection = measure_color_protection(source, session / receipt['primary_output'], regions, gains=[1., 1., 1.])
                        protection['regions_path'] = bg['regions']
                        actual['color_protection'] = protection
                        if check.get('color_protection') != protection:
                            return False
                    if actual != evidence:
                        return False
        separation = receipt.get('star_separation')
        if separation and payload.get('execution_policy', {}).get('starnet') == 'versioned_linear_bridge_v2':
            document = load_json(session / separation['contract'])
            if sha256_file(session / separation['contract']) != separation['contract_sha256']:
                return False
            bridge = load_json(session / separation['bridge_receipt'])
            if bridge.get('contract_sha256') != separation['contract_sha256']:
                return False
            if document.get('schema') == 'starun-siril.starnet-contract.v2':
                from siril_starnet_linear import validate_receipt, load_json as load_starnet_json
                document = load_starnet_json(session / separation['contract'])
                bridge = load_starnet_json(session / separation['bridge_receipt'])
                validate_receipt(document, bridge, session / receipt['primary_output'], session / separation['full_source'])
        calibration = receipt.get("color_calibration")
        if (payload.get('execution_policy', {}).get('guidance') in {'stage4_xisf_inputs_v10', 'stage9_explicit_sources_v11', 'stage_shared_protocol_docs_v12', 'stage_ai_morphology_measurement_v13', 'reviewed_batch_flow_v14', 'serial_agent_flow_v15', 'assessed_stage3_flow_v16', 'prepared_stage4_flow_v17', 'benefit_first_flow_v18', 'vivid_color_flow_v19', 'dualband_hoo_flow_v20', 'multiscale_structure_flow_v21', 'roi_guided_stretch_flow_v22', 'profile_guided_stretch_flow_v23', 'starless_subject_stretch_flow_v24', 'agent_proposed_stretch_flow_v25', 'morphology_default_stretch_flow_v26', 'subject_priority_stretch_flow_v27', 'stellar_stretch_flow_v28', EXECUTION_POLICY['guidance']}
            and (receipt.get('protocol') == 'astrometry.solve' or
                 (receipt.get('protocol') == 'color.calibrate' and calibration and calibration.get('method') in {'pcc', 'spcc'}))):
            source = (Path(payload['input']['path']) if receipt['source_path'] == '@input' else
                      session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',)))
            if source.suffix.lower() == '.xisf':
                from deep_sky_siril_processing import validate_processing_outputs
                primary = session_path(session, receipt.get('primary_output', ''), must_exist=True, allowed_roots=('artifacts',))
                if primary.suffix.lower() not in {'.fit', '.fits', '.fts'}:
                    return False
                actual = validate_processing_outputs(session, source, primary, receipt, execution_policy=payload['execution_policy'])
                if any(receipt.get('processing_checks', {}).get(key) != value for key, value in actual.items()):
                    return False
        if (payload.get('execution_policy', {}).get('guidance') in {'stage_evidence_limits_v4', 'stage_starnet_linear_v5', 'stage_starnet_precision_v6', 'stage_recompose_review_v7', 'stage_xisf_metrics_v8', 'stage_background_xisf_v9', 'stage4_xisf_inputs_v10', 'stage9_explicit_sources_v11', 'stage_shared_protocol_docs_v12', 'stage_ai_morphology_measurement_v13', 'reviewed_batch_flow_v14', 'serial_agent_flow_v15', 'assessed_stage3_flow_v16', 'prepared_stage4_flow_v17', 'benefit_first_flow_v18', 'vivid_color_flow_v19', 'dualband_hoo_flow_v20', 'multiscale_structure_flow_v21', 'roi_guided_stretch_flow_v22', 'profile_guided_stretch_flow_v23', 'starless_subject_stretch_flow_v24', 'agent_proposed_stretch_flow_v25', 'morphology_default_stretch_flow_v26', 'subject_priority_stretch_flow_v27', 'stellar_stretch_flow_v28', EXECUTION_POLICY['guidance']}
            and receipt.get('protocol') == 'color.calibrate'):
            from deep_sky_siril_processing import _calibration_evidence, pcc_evidence, script_commands
            script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            text = script.read_text(encoding='utf-8')
            actual_pcc = any(row[0] == 'pcc' for row in script_commands(text))
            claimed_pcc = isinstance(calibration, dict) and calibration.get('method') == 'pcc'
            if actual_pcc != claimed_pcc:
                return False
            if actual_pcc:
                source = (Path(payload['input']['path']) if receipt['source_path'] == '@input' else
                          session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',)))
                declaration = _calibration_evidence(text, source)
                if declaration.get('method') != 'pcc':
                    return False
                expected = pcc_evidence(declaration, source, payload['context'].get('channel_mode'),
                                        session=session, execution_policy=payload.get('execution_policy'))
                if any(calibration.get(key) != expected.get(key) for key in
                       ('claim', 'compatibility_evidence', 'fallback_evidence')):
                    return False
        if ((payload.get('execution_policy') or {}).get('neutral') in {'heldout_benefit_safety_v1', 'scientific_native_flow_v2'}
            and receipt.get('protocol') == 'color.calibrate'):
            from deep_sky_siril_processing import script_commands
            actual_script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
            if any(row[0] in {'pm', 'split', 'rgbcomp'} for row in script_commands(actual_script.read_text(encoding='utf-8'))):
                if not isinstance(calibration, dict) or calibration.get('method') != 'neutral':
                    return False
        if calibration is not None:
            if not isinstance(calibration, dict) or calibration.get("source_sha256") != receipt.get("source", {}).get("sha256"):
                return False
            if (payload.get('execution_policy', {}).get('color_preservation') == 'vivid_preservation_v1'
                and calibration.get('method') != 'preserve'):
                from deep_sky_siril_processing import _calibration_evidence, validate_processing_outputs
                source = Path(payload['input']['path']) if receipt['source_path'] == '@input' else session / receipt['source_path']
                declaration = _calibration_evidence((session / receipt['script']['path']).read_text(encoding='utf-8'), source)
                region_name = declaration.get('protection_regions', declaration.get('background_regions', ''))
                regions = session_path(session, region_name, must_exist=True, allowed_roots=('reports',))
                expected = {'path': regions.relative_to(session).as_posix(), **fingerprint(regions, include_path=False)}
                if calibration.get('protection_regions') != expected or calibration.get('channel_gains') != declaration.get('channel_gains'):
                    return False
                if receipt.get('primary_output'):
                    primary = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
                    actual = validate_processing_outputs(session, source, primary, receipt, execution_policy=payload['execution_policy'])
                    if actual.get('color_protection') != receipt.get('processing_checks', {}).get('color_protection'):
                        return False
            if calibration.get("method") == "neutral":
                regions = session_path(session, calibration.get("background_regions", ""), must_exist=True, allowed_roots=("reports",))
                coefficients = calibration.get("coefficients", {})
                if fingerprint(regions, include_path=False) != coefficients.get("regions"):
                    return False
                if (payload.get('execution_policy') or {}).get('neutral') in {'heldout_benefit_safety_v1', 'scientific_native_flow_v2'}:
                    from deep_sky_siril_neutral import validate_neutral_binding, validate_partition
                    source = (Path(payload['input']['path']) if receipt['source_path'] == '@input' else
                              session_path(session, receipt['source_path'], must_exist=True, allowed_roots=('artifacts',)))
                    candidate = session_path(session, receipt['primary_output'], must_exist=True, allowed_roots=('artifacts',))
                    evidence = receipt.get('processing_checks', {}).get('color_calibration', {}).get('neutral_validation')
                    # Recompute coefficients as well as measurements; validation pixels never enter the fit.
                    from deep_sky_siril_processing import neutral_coefficients, _calibration_evidence
                    neutral_script = session_path(session, receipt['script']['path'], must_exist=True, allowed_roots=('scripts',))
                    declaration = _calibration_evidence(neutral_script.read_text(encoding='utf-8'), source)
                    partition = calibration.get('background_partition')
                    if (declaration.get('method') != 'neutral' or declaration.get('background_partition') is None
                        or validate_partition(load_json(regions)['regions'], declaration['background_partition']) != partition
                        or declaration.get('background_regions') != calibration.get('background_regions')
                        or declaration.get('white_reference') != coefficients.get('white_reference')):
                        return False
                    if (payload['context'].get('channel_mode') == 'dualband-osc'
                        and (coefficients.get('gains') != [1, 1, 1] or coefficients.get('white_reference') is not None)):
                        return False
                    if partition is None or coefficients != neutral_coefficients(source, regions, coefficients.get('white_reference'),
                                                                               background_partition=partition, policy=payload['execution_policy']['neutral']):
                        return False
                    from deep_sky_siril_processing import _validate_neutral_script
                    display = _validate_neutral_script(session, source, candidate, script_commands(neutral_script.read_text(encoding='utf-8')), coefficients, policy=payload['execution_policy']['neutral'])
                    if payload['execution_policy']['neutral'] == 'scientific_native_flow_v2':
                        if calibration.get('display') != display:
                            return False
                        from deep_sky_siril_processing import validate_processing_outputs
                        actual = validate_processing_outputs(session, source, candidate, receipt, execution_policy=payload['execution_policy'])
                        if any(receipt.get('processing_checks', {}).get(key) != value for key, value in actual.items()):
                            return False
                    validate_neutral_binding(evidence, source, candidate, regions, coefficients, partition, policy=payload['execution_policy']['neutral'])
        for binding in receipt.get("review_bindings", []):
            if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
                return False
            reviewed = session_path(session, binding["path"], must_exist=True, allowed_roots=("reviews",))
            if sha256_file(reviewed) != binding["sha256"]:
                return False
        if receipt.get("astrometry", {}).get("catalogue") and not fingerprint_matches(receipt["astrometry"]["catalogue"]):
            return False
        if (payload.get('execution_policy', {}).get('delivery') in PAIRED_DELIVERY_POLICIES
            and receipt.get('protocol') == 'delivery.render'):
            from deep_sky_siril_processing import verify_delivery_receipt
            if not verify_delivery_receipt(session, payload, receipt):
                return False
        if receipt.get('protocol') == 'structure.local-contrast':
            if payload.get('execution_policy', {}).get('local_contrast') in {'texture_hf_review_v1', 'wavelet_clahe_competition_v2'}:
                from deep_sky_siril_local_contrast import verify_receipt
                if not verify_receipt(session, receipt):
                    return False
            elif receipt.get('local_contrast_guard'):
                return False
        if receipt.get('stretch_visibility'):
            from deep_sky_siril_visibility import review_paths
            review_paths(session, receipt['stretch_visibility'], receipt)
        adjustment = receipt.get("protocol") == "tone.adjust" or (receipt.get("protocol") == "color.finish"
                     and ("color_finish" in receipt or execution_policy_is_current(payload)))
        if (payload.get('execution_policy', {}).get('star_diagnostics') == 'subject_roi_residual_v2'
            and receipt.get('protocol') == 'stars.recompose'):
            from deep_sky_siril_processing import verify_recomposition_receipt
            if not verify_recomposition_receipt(session, payload, receipt):
                return False
        elif (payload.get('execution_policy', {}).get('stretch') in {'masked_dual_v1', 'roi_protected_selection_v2'}
              and receipt.get('protocol') in {'stretch', 'stars.recompose'} and not receipt.get('dualband_transfer')):
            from deep_sky_siril_hdr import verify_receipt
            if not verify_receipt(session, payload, receipt):
                return False
        if adjustment or (receipt.get("protocol") == "stretch" and "transfer_chain" in receipt):
            from deep_sky_siril_processing import transfer_chain, script_commands, validate_adjustment
            script = session_path(session, receipt["script"]["path"], must_exist=True, allowed_roots=("scripts",))
            primary = session_path(session, receipt["primary_output"], must_exist=True, allowed_roots=("artifacts",))
            text = script.read_text(encoding="utf-8")
            if receipt["protocol"] == "stretch" and "transfer_chain" in receipt:
                if receipt["transfer_chain"] != transfer_chain(text, primary):
                    return False
            if adjustment:
                source = (Path(payload["input"]["path"]) if receipt["source_path"] == "@input" else
                          session_path(session, receipt["source_path"], must_exist=True, allowed_roots=("artifacts",)))
                key = "tone_chain" if receipt["protocol"] == "tone.adjust" else "color_finish"
                from deep_sky_siril_processing import local_saturation_declaration, validate_local_saturation, measure_local_saturation
                commands = script_commands(text)
                parameters = local_saturation_declaration(text) if receipt['protocol'] == 'color.finish' else None
                from deep_sky_siril_color import tone_declaration, validate_local_tone, measure_local_tone
                tone = tone_declaration(text)
                if tone is not None:
                    if receipt['protocol'] != 'tone.adjust' or parameters is not None:
                        return False
                    if not isinstance(receipt.get('local_tone'), dict):
                        return False
                    local = validate_local_tone(session, source, primary, commands, tone,
                                                recorded_helper=receipt['local_tone'].get('helper'))
                    chain = [{'command': row[0], 'parameters': row[1:]} for row in commands if row[0] == 'mtf']
                    if (receipt.get('local_tone') != local or receipt.get(key) != chain
                        or receipt.get('processing_checks', {}).get('local_tone') != measure_local_tone(session, source, primary, local)):
                        return False
                elif receipt.get('local_tone'):
                    return False
                elif parameters is not None and parameters.get('mask_mode') == 'none':
                    from deep_sky_siril_color import validate_unmasked_curve, measure_unmasked_curve, measure_curve, EVALUATION
                    if payload.get('execution_policy', {}).get('color_evaluation') != EVALUATION:
                        return False
                    local = validate_unmasked_curve(session, source, primary, commands, parameters)
                    chain = [{'command': row[0], 'parameters': row[1:]} for row in commands if row[0] in {'ght', 'invght'}]
                    if (receipt.get('color_curve') != local or receipt.get(key) != chain
                        or receipt.get('processing_checks', {}).get('unmasked_color') != measure_unmasked_curve(session, source, primary, local)
                        or receipt.get('processing_checks', {}).get('color_curve') != measure_curve(session, source, primary, local)):
                        return False
                elif parameters is not None:
                    curve = 'D' in parameters
                    if payload.get('execution_policy', {}).get('color_curve' if curve else 'local_saturation') != ('masked_saturation_ghs_v1' if curve else 'luminance_peak_mask_v1'):
                        return False
                    local = validate_local_saturation(session, source, primary, commands, parameters)
                    chain = [{'command': row[0], 'parameters': row[1:]} for row in commands if row[0] in {'satu', 'ght', 'invght'}]
                    if (receipt.get('local_saturation') != local or receipt.get(key) != chain
                        or receipt.get('processing_checks', {}).get('local_saturation') != measure_local_saturation(session, source, primary, local)):
                        return False
                    if curve:
                        from deep_sky_siril_color import measure_curve
                        if receipt.get('processing_checks', {}).get('color_curve') != measure_curve(session, source, primary, local):
                            return False
                elif receipt.get('local_saturation') or receipt.get(key) != validate_adjustment(session, source, primary, commands, receipt["protocol"]):
                    return False
                if receipt['protocol'] == 'color.finish':
                    expected = 'unmasked_first_v1' if parameters and 'mask_mode' in parameters else None
                    if receipt.get('color_evaluation') != expected:
                        return False
                if receipt['protocol'] == 'tone.adjust' and payload.get('execution_policy', {}).get('tone_adjustment') == 'remaining_goal_v1':
                    from deep_sky_siril_tone import verify
                    if not verify(session, payload, receipt, text, source, primary):
                        return False
    except (ContractError, BundleError, KeyError, TypeError, ValueError, OSError):
        return False
    knowledge = receipt.get("knowledge_validation")
    current = _knowledge_record(payload)
    if (
        receipt.get("knowledge_bindings_unchanged") is not True
        or receipt.get("script_unchanged") is not True
        or receipt.get("source_unchanged") is not True
        or not session_file_matches(receipt.get("script"), ("scripts",))
        or not session_file_matches(receipt.get("script_provenance"), ("scripts",))
        or not isinstance(knowledge, dict)
        or any(knowledge.get(key) != value for key, value in current.items())
        or not session_file_matches(knowledge.get("bundle_evidence"), ("reports",))
        or not skill_file_matches(knowledge.get("command_policy"))
        or not current_receipt_evidence_matches()
    ):
        return False
    references = knowledge.get("references")
    evidence = knowledge.get("manual_evidence")
    command_knowledge = receipt.get("command_knowledge")
    receipt_commands = receipt.get("commands")
    if not isinstance(receipt_commands, list) or any(
        not isinstance(command, str) for command in receipt_commands
    ):
        return False
    unique_commands = list(dict.fromkeys(receipt_commands))
    mapped_commands = (
        command_knowledge.get("commands")
        if isinstance(command_knowledge, dict)
        else None
    )
    valid_command_entries = bool(
        isinstance(mapped_commands, list)
        and all(
            isinstance(item, dict)
            and set(item)
            == {
                "command",
                "path",
                "source_sha256",
                "section_id",
                "entry_sha256",
                "scriptable",
                "policy_authorized",
            }
            and item.get("scriptable") is True
            and item.get("policy_authorized") is True
            and HASH_PATTERN.fullmatch(str(item.get("source_sha256", "")))
            is not None
            and HASH_PATTERN.fullmatch(str(item.get("entry_sha256", "")))
            is not None
            for item in mapped_commands
        )
    )
    return bool(
        isinstance(references, list)
        and references
        and all(skill_file_matches(record) for record in references)
        and isinstance(evidence, list)
        and all(session_file_matches(record, ("reports",)) for record in evidence)
        and isinstance(command_knowledge, dict)
        and command_knowledge.get("manual") == current["manual"]
        and command_knowledge.get("command_policy") == current["command_policy"]
        and command_knowledge.get("protocol") == receipt.get("protocol")
        and valid_command_entries
        and [str(item.get("command", "")).lower() for item in mapped_commands]
        == unique_commands
    )


def _verified_success_receipts(session: Path) -> list[dict[str, Any]]:
    manifest = load_json(session / "manifest.json")
    session_payload = load_json(session / "session.json")
    entries = manifest.get("runs")
    if manifest.get("schema") != f"{SCHEMA_PREFIX}.manifest.v1" or not isinstance(entries, list):
        raise ContractError("manifest_invalid", "Manifest run index is invalid")
    receipts: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("status") != "success":
            continue
        name = entry.get("receipt")
        if not isinstance(name, str) or Path(name).name != name:
            raise ContractError("manifest_invalid", "Manifest receipt path is invalid")
        receipt_path = session_path(
            session,
            session / "runs" / name,
            must_exist=True,
            allowed_roots=("runs",),
        )
        if entry.get("receipt_sha256") != sha256_file(receipt_path):
            raise ContractError("manifest_invalid", f"Run receipt changed: {name}")
        receipt = load_json(receipt_path)
        if (
            receipt.get("schema") != f"{SCHEMA_PREFIX}.run-receipt.v1"
            or receipt.get("status") != "success"
            or receipt.get("id") != entry.get("id")
            or not _receipt_bindings_unchanged(session, session_payload, receipt)
        ):
            raise ContractError("manifest_invalid", f"Run receipt is invalid: {name}")
        receipts.append(receipt)
    return receipts


def _verified_run_outputs(session: Path) -> set[Path]:
    outputs: set[Path] = set()
    for receipt in _verified_success_receipts(session):
        eligible = receipt.get('processing_checks', {}).get('background_validation', {}).get('decision') != 'skip'
        for record in receipt.get("outputs", []):
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ContractError("manifest_invalid", "Run output record is invalid")
            candidate = session_path(session, session / record["path"], must_exist=True)
            if not fingerprint_matches({**record, "path": str(candidate)}):
                raise ContractError("manifest_invalid", f"Run output changed: {record['path']}")
            if eligible:
                outputs.add(candidate.resolve())
    return outputs


def _source_is_bound(session: Path, payload: dict[str, Any], source: Path) -> bool:
    original = payload["input"]
    if source.resolve() == Path(original["path"]).resolve():
        return fingerprint_matches(original)
    return source.resolve() in _verified_run_outputs(session)


def _append_run(manifest_path: Path, manifest: dict[str, Any], receipt_path: Path, receipt: dict[str, Any]) -> None:
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise ContractError("manifest_invalid", "Manifest run index is invalid")
    runs.append(
        {
            "id": receipt["id"],
            "status": receipt["status"],
            "receipt": receipt_path.name,
            "receipt_sha256": sha256_file(receipt_path),
        }
    )
    manifest["updated_at"] = utc_now()
    atomic_write_json(manifest_path, manifest)

__all__ = [
    "execution_policy_is_current",
    "init_session",
    "load_session",
    "effective_session_payload",
]

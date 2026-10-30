"""ROI protection and bounded display-separation selection for the stretch helper."""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
import shutil
import tempfile
import time

import numpy as np

import deep_sky_siril_stretch as base
from deep_sky_siril_contract import fingerprint, fingerprint_matches, load_json, sha256_file

POLICY = 'roi_protected_selection_v2'
REQUEST = 'starun-siril.stretch-request.v2'
PROPOSAL_REQUEST = 'starun-siril.stretch-request.v4'
PROPOSAL_POLICY = 'agent_proposal_v1'
REPORT = 'starun-siril.stretch-parameters.v2'
DERIVED = 'starun-siril.derived-stretch-parameters.v2'
EXTRA_STATISTICS = {'p999', 'max_rgb', 'hf_sigma2'}
FULL_DIAGNOSTIC_POLICY = 'subject_only_full_diagnostic_v1'
PROFILE_PATH = base.ROOT/'references/stretch-profiles.json'


# Exact outgoing bindings are accepted only for read-only historical evidence.
_V22_BINDINGS = {'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '521c1bc14c1737948b2e9a2ca4feb628485ccc595643e0e4259d3574940978e0',
                                                     'size': 33251},
                    'deep_sky_siril_stretch.py': {'sha256': 'fae6f661504f567b18d4e895602cab34abf30d6a4fb54ec3f1c7286842981b30',
                                                  'size': 54953},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2caf3d692ebbc6e866278581efd71ccb4a117633821176b0edcf0e9de3610633',
                                                         'size': 44131},
                    'deep_sky_siril_processing.py': {'sha256': '0efa73ab5d0487693ecf94e21e5a1ef27200ec1338b3a68f9d06f74f8a590c88',
                                                     'size': 136428},
                    'deep_sky_siril_hdr.py': {'sha256': '0f76f18a99412e1261c5e571c48edd6cd158014e0102f6e004a46912f0719e82',
                                              'size': 18548},
                    'deep_sky_siril_dualband.py': {'sha256': '470862694cd6d2c07d67aad02e27c3d17299bc87a9bfaca7d87e7ec98bd6e7e0',
                                                   'size': 28270},
                    'deep_sky_siril_core.py': {'sha256': 'ebe0571d88faed060e5880f4b66dc2a7755957c2051c691fefe8d0e2d611a884',
                                               'size': 137623}},
 'profiles': {'sha256': '63383f2a0ba7e1c0dce979bc28dfb950dc1a969572f9dec67dc03c94263ce15d', 'size': 1669}}


_V23_BINDINGS = {'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '8d3a61f1f0f3b6e4073fe5f65d1d8aa47beaaca7d3a5d46c60e19afbfd0a3a37',
                                                     'size': 37790},
                    'deep_sky_siril_stretch.py': {'sha256': 'a4091864385d2356cd949cea0734dd1802108b752e0d25379ef3f08f15f233d1',
                                                  'size': 59452},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': 'abb5bbaa331c9a605f38b40a67d4bdd44515d632a4ae8a3dc1546f567fe9c797',
                                                           'size': 19361},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2caf3d692ebbc6e866278581efd71ccb4a117633821176b0edcf0e9de3610633',
                                                         'size': 44131},
                    'deep_sky_siril_processing.py': {'sha256': 'c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec',
                                                     'size': 136713},
                    'deep_sky_siril_hdr.py': {'sha256': '0f76f18a99412e1261c5e571c48edd6cd158014e0102f6e004a46912f0719e82',
                                              'size': 18548},
                    'deep_sky_siril_dualband.py': {'sha256': '470862694cd6d2c07d67aad02e27c3d17299bc87a9bfaca7d87e7ec98bd6e7e0',
                                                   'size': 28270},
                    'deep_sky_siril_core.py': {'sha256': 'ebe0571d88faed060e5880f4b66dc2a7755957c2051c691fefe8d0e2d611a884',
                                               'size': 137623}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163', 'size': 3844}}

_V24_BINDINGS = {'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '5c111b656d24c8a8c2ad54e2b9a8b6fa855d59015fe17d191a990ca1faae1bdc',
                                                     'size': 41881},
                    'deep_sky_siril_stretch.py': {'sha256': '7e32dacde77eef08ca93975b350dabf5f00264ccefb7c602b6eb2d374955b1e6',
                                                  'size': 59827},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0e46baba5b6cd29581b77f0279c28b99218fbb0fae2caf94b82563cad68b19e4',
                                                           'size': 20001},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2caf3d692ebbc6e866278581efd71ccb4a117633821176b0edcf0e9de3610633',
                                                         'size': 44131},
                    'deep_sky_siril_processing.py': {'sha256': 'c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec',
                                                     'size': 136713},
                    'deep_sky_siril_hdr.py': {'sha256': '0f76f18a99412e1261c5e571c48edd6cd158014e0102f6e004a46912f0719e82',
                                              'size': 18548},
                    'deep_sky_siril_dualband.py': {'sha256': '470862694cd6d2c07d67aad02e27c3d17299bc87a9bfaca7d87e7ec98bd6e7e0',
                                                   'size': 28270},
                    'deep_sky_siril_core.py': {'sha256': 'ebe0571d88faed060e5880f4b66dc2a7755957c2051c691fefe8d0e2d611a884',
                                               'size': 137623}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163', 'size': 3844}}

# Exact complete bindings before Stage 8 proposals; never combine individual hashes.
_PRE_STAGE8_BINDINGS = {
  "execution_policy": {
    "log_diagnostics": "fail_closed_v3",
    "network": "online_default_gaia_catalog_explicit_v3",
    "starnet": "versioned_linear_bridge_v2",
    "background": "heldout_material_gate_v4",
    "star_diagnostics": "subject_roi_residual_v2",
    "psf_provenance": "native_star_samples_v2",
    "reporting": "isolated_metrics_v1",
    "neutral": "scientific_native_flow_v2",
    "stretch": "roi_protected_selection_v2",
    "visual_review": "subject_visibility_v1",
    "guidance": "agent_proposed_stretch_flow_v25",
    "stretch_parameters": "agent_proposal_v1",
    "stretch_strategy": "morphology_chain_comparison_v1",
    "starless_stretch": "subject_only_full_diagnostic_v1",
    "dualband": "hoo_proxy_rgb_stars_v1",
    "color_curve": "masked_saturation_ghs_v1",
    "local_saturation": "luminance_peak_mask_v1",
    "delivery": "paired_fits_jpeg_v2",
    "recomposition_review": "fixed_materials_v1",
    "filter_metadata": "dwarf_explicit_filters_v1",
    "background_sampling": "protected_regions_v1",
    "restoration": "assessed_scientific_flow_v1",
    "local_contrast": "wavelet_clahe_competition_v2"
  },
  "implementation": {
    "deep_sky_siril_stretch_v2.py": {
      "sha256": "663ff36520c08a3bf670161618b2ec3fa930048ec253677970e3c129b5107ec3",
      "size": 47114
    },
    "deep_sky_siril_stretch.py": {
      "sha256": "ea4a97daa122d2b08ac490c0a6a5c8dde9922afe513c21c747c14ba7861517c6",
      "size": 61557
    },
    "deep_sky_siril_stretch_profiles.py": {
      "sha256": "2bda5b1976d73e5ceca4434e6373a9aaf96f9b66c2509fd56b1cf8c65fe9474a",
      "size": 20765
    },
    "deep_sky_siril_local_contrast.py": {
      "sha256": "2caf3d692ebbc6e866278581efd71ccb4a117633821176b0edcf0e9de3610633",
      "size": 44131
    },
    "deep_sky_siril_processing.py": {
      "sha256": "c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec",
      "size": 136713
    },
    "deep_sky_siril_hdr.py": {
      "sha256": "0f76f18a99412e1261c5e571c48edd6cd158014e0102f6e004a46912f0719e82",
      "size": 18548
    },
    "deep_sky_siril_dualband.py": {
      "sha256": "126135471d42248c05b39fe7344855dd13ad7cedd23d376a84e7ca8cc38cb3f3",
      "size": 29895
    },
    "deep_sky_siril_core.py": {
      "sha256": "ebe0571d88faed060e5880f4b66dc2a7755957c2051c691fefe8d0e2d611a884",
      "size": 137623
    }
  },
  "profiles": {
    "sha256": "50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163",
    "size": 3844
  }
}


# Exact implementation before diagnostic-only stretch noise.
_V25_BINDINGS = {'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '67c87e20c1b8a62161019fe659cccebaf1fcc5417216e027ebbd586ba4a59525',
                                                     'size': 50095},
                    'deep_sky_siril_stretch.py': {'sha256': 'ea4a97daa122d2b08ac490c0a6a5c8dde9922afe513c21c747c14ba7861517c6',
                                                  'size': 61557},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '3ed58c94b9511d7f39299956f8fe0c82a28aa458755a57e9aafa68b34af6d385',
                                                           'size': 20775},
                    'deep_sky_siril_local_contrast.py': {'sha256': 'e60f3d6b99f8de065412068444e9f1be482de7584acd1411708102c0c77bebd2',
                                                         'size': 47215},
                    'deep_sky_siril_processing.py': {'sha256': 'c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec',
                                                     'size': 136713},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': '126135471d42248c05b39fe7344855dd13ad7cedd23d376a84e7ca8cc38cb3f3',
                                                   'size': 29895},
                    'deep_sky_siril_core.py': {'sha256': 'ebe0571d88faed060e5880f4b66dc2a7755957c2051c691fefe8d0e2d611a884',
                                               'size': 137623}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}


# Exact implementation before reference-driven Stage 7 visibility.
_PRE_REFERENCE_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'roi_protected_selection_v2', 'visual_review': 'subject_visibility_v1', 'guidance': 'agent_proposed_stretch_flow_v25', 'stretch_parameters': 'agent_proposal_v1', 'stretch_noise': 'diagnostic_only_v1', 'stretch_strategy': 'morphology_chain_comparison_v1', 'starless_stretch': 'subject_only_full_diagnostic_v1', 'dualband': 'hoo_proxy_rgb_stars_v1', 'color_curve': 'masked_saturation_ghs_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'wavelet_clahe_competition_v2'}, 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '29d8bb55536f87b13d0a8827f15fa7547d5ceb3cd22c9928c7786f8a346f14d9', 'size': 53681}, 'deep_sky_siril_stretch.py': {'sha256': 'ea4a97daa122d2b08ac490c0a6a5c8dde9922afe513c21c747c14ba7861517c6', 'size': 61557}, 'deep_sky_siril_stretch_profiles.py': {'sha256': '52a9af295f53582699309190f36bf5f1c31b6ad4e14d230bd2807f7a8f09b42c', 'size': 21345}, 'deep_sky_siril_local_contrast.py': {'sha256': 'e60f3d6b99f8de065412068444e9f1be482de7584acd1411708102c0c77bebd2', 'size': 47215}, 'deep_sky_siril_processing.py': {'sha256': 'c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec', 'size': 136713}, 'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6', 'size': 18934}, 'deep_sky_siril_dualband.py': {'sha256': 'db7278f5a5ada8ca4823075be562f8412374ab549104ee95e5b51560830c0d40', 'size': 30116}, 'deep_sky_siril_core.py': {'sha256': 'ebe0571d88faed060e5880f4b66dc2a7755957c2051c691fefe8d0e2d611a884', 'size': 137623}}, 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163', 'size': 3844}}


# Exact implementation before structured Stage 7 assessment.
_PRE_ASSESSMENT_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'local_contrast': 'wavelet_clahe_competition_v2'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': 'b5172bb15193eec23a35a0602bf8656b270113ca9474ffe81d91b84479ab9797',
                                                     'size': 56027},
                    'deep_sky_siril_stretch.py': {'sha256': '04258dcf367e55ba3f391d3c8228d0defcea63b61c9dc4ce77bade614164a3eb',
                                                  'size': 62783},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': 'e60f3d6b99f8de065412068444e9f1be482de7584acd1411708102c0c77bebd2',
                                                         'size': 47215},
                    'deep_sky_siril_processing.py': {'sha256': 'c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec',
                                                     'size': 136713},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'db7278f5a5ada8ca4823075be562f8412374ab549104ee95e5b51560830c0d40',
                                                   'size': 30116},
                    'deep_sky_siril_core.py': {'sha256': '1b872dc8ded3e875845dd2c3428fd6a157dcc8e71f91e4d89eee108e3b377d29',
                                               'size': 138623},
                    'deep_sky_siril_visibility.py': {'sha256': '2b1e9fb3f12e1b1de7a675aef398af44ea8502f9df1714585563b97d35644ceb',
                                                     'size': 20080}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163', 'size': 3844}}


# Exact implementation before protected local finishing.
_PRE_FINISH_BINDINGS = {'execution_policy': {'background': 'heldout_material_gate_v4',
                      'background_sampling': 'protected_regions_v1',
                      'color_curve': 'masked_saturation_ghs_v1',
                      'delivery': 'paired_fits_jpeg_v2',
                      'dualband': 'hoo_proxy_rgb_stars_v1',
                      'filter_metadata': 'dwarf_explicit_filters_v1',
                      'guidance': 'agent_proposed_stretch_flow_v25',
                      'local_contrast': 'wavelet_clahe_competition_v2',
                      'local_saturation': 'luminance_peak_mask_v1',
                      'log_diagnostics': 'fail_closed_v3',
                      'network': 'online_default_gaia_catalog_explicit_v3',
                      'neutral': 'scientific_native_flow_v2',
                      'psf_provenance': 'native_star_samples_v2',
                      'recomposition_review': 'fixed_materials_v1',
                      'reporting': 'isolated_metrics_v1',
                      'restoration': 'assessed_scientific_flow_v1',
                      'star_diagnostics': 'subject_roi_residual_v2',
                      'starless_stretch': 'subject_only_full_diagnostic_v1',
                      'starnet': 'versioned_linear_bridge_v2',
                      'stretch': 'roi_protected_selection_v2',
                      'stretch_noise': 'diagnostic_only_v1',
                      'stretch_parameters': 'agent_proposal_v1',
                      'stretch_strategy': 'morphology_chain_comparison_v1',
                      'visual_review': 'subject_visibility_v1'},
 'implementation': {'deep_sky_siril_core.py': {'sha256': 'a90f8498af8b19f10d986cdc2f415e9c498042ecc1f748d7a6eb26f0de11fdfb',
                                               'size': 139142},
                    'deep_sky_siril_dualband.py': {'sha256': 'db7278f5a5ada8ca4823075be562f8412374ab549104ee95e5b51560830c0d40',
                                                   'size': 30116},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_local_contrast.py': {'sha256': 'e60f3d6b99f8de065412068444e9f1be482de7584acd1411708102c0c77bebd2',
                                                         'size': 47215},
                    'deep_sky_siril_processing.py': {'sha256': 'c915f8e34b685586d621ca1a60aef355b0be9cf5ebd1ffa416c212b39594eeec',
                                                     'size': 136713},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_stretch_v2.py': {'sha256': 'ca76b4fb9a73abde3e858252e9f35568c961555349f8241265aecfc239413127',
                                                     'size': 59627},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}


_PRE_ENHANCEMENT_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3', 'starnet': 'versioned_linear_bridge_v2', 'background': 'heldout_material_gate_v4', 'star_diagnostics': 'subject_roi_residual_v2', 'psf_provenance': 'native_star_samples_v2', 'reporting': 'isolated_metrics_v1', 'neutral': 'scientific_native_flow_v2', 'stretch': 'roi_protected_selection_v2', 'visual_review': 'subject_visibility_v1', 'guidance': 'agent_proposed_stretch_flow_v25', 'stretch_parameters': 'agent_proposal_v1', 'stretch_noise': 'diagnostic_only_v1', 'stretch_strategy': 'morphology_chain_comparison_v1', 'starless_stretch': 'subject_only_full_diagnostic_v1', 'dualband': 'hoo_proxy_rgb_stars_v1', 'color_curve': 'masked_saturation_ghs_v1', 'local_saturation': 'luminance_peak_mask_v1', 'delivery': 'paired_fits_jpeg_v2', 'recomposition_review': 'fixed_materials_v1', 'filter_metadata': 'dwarf_explicit_filters_v1', 'background_sampling': 'protected_regions_v1', 'restoration': 'assessed_scientific_flow_v1', 'local_contrast': 'wavelet_clahe_competition_v2'}, 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': 'c3ee561603e4c033ae3bd2fd51966e9e144050baad8f9eaa06179ca9e1c61e6a', 'size': 63229}, 'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256', 'size': 62787}, 'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8', 'size': 21428}, 'deep_sky_siril_local_contrast.py': {'sha256': '3ceaa201493118707ce91b5aee71ef5a5c2bca0efcb41cd1558528b34f7cdc87', 'size': 49399}, 'deep_sky_siril_processing.py': {'sha256': 'f40e414500ed8a317a60ec1fa2aa6fe62bc83590c9d64dbf9e3de433d43d7db9', 'size': 138375}, 'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6', 'size': 18934}, 'deep_sky_siril_dualband.py': {'sha256': 'db7278f5a5ada8ca4823075be562f8412374ab549104ee95e5b51560830c0d40', 'size': 30116}, 'deep_sky_siril_core.py': {'sha256': 'b520a25179b6b70383bea6481e5a0f41b95930816a197185cc1ff2d5e6ba6ae9', 'size': 139658}, 'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052', 'size': 31804}}, 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163', 'size': 3844}}


# Exact complete implementation set before linear denoise proposals.
_PRE_DENOISE_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'subject_enhancement': 'benefit_first_v1'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': 'd48bc8986f6812ac5d69e3cf3f79b22eddff312d09d1df2a2f97a6c4c5d36595',
                                                     'size': 65633},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2bd0e12fbf19640e97b6cf7773238ae72f8e9451b0c454fd1d4cc57326381748',
                                                         'size': 50689},
                    'deep_sky_siril_processing.py': {'sha256': '64424d1b4d362d134fbdf383c19a9c1e49afab5f7061d95842eae636268fdb81',
                                                     'size': 138791},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'db7278f5a5ada8ca4823075be562f8412374ab549104ee95e5b51560830c0d40',
                                                   'size': 30116},
                    'deep_sky_siril_core.py': {'sha256': 'b520a25179b6b70383bea6481e5a0f41b95930816a197185cc1ff2d5e6ba6ae9',
                                               'size': 139658},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}


# Exact outgoing implementation before recomposition proposals.
_PRE_RECOMPOSITION_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'linear_denoise': 'agent_proposal_v1'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '784bd1532789208bffae47c9b99de2d05a5b124776cdf1257ccde33527fae590',
                                                     'size': 69325},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2bd0e12fbf19640e97b6cf7773238ae72f8e9451b0c454fd1d4cc57326381748',
                                                         'size': 50689},
                    'deep_sky_siril_processing.py': {'sha256': 'b0759c5847fcfd121b63c5ee618d5f80c021fa542958cfdbef23d8e91b27bfc1',
                                                     'size': 139670},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': '7b5a3d4b9c9c014f7b9a67a9ffd16f1caf5fef77d03ddde272e4badad4ea96e1',
                                               'size': 140556},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}


# Exact outgoing implementation before remaining-goal tone review.
_PRE_TONE_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'recomposition_parameters': 'agent_proposal_v1'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '6e9273ddfd7ed17ffb8a1720cc153b0673dd7b37997ecb18619692a074b66f57',
                                                     'size': 73395},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2bd0e12fbf19640e97b6cf7773238ae72f8e9451b0c454fd1d4cc57326381748',
                                                         'size': 50689},
                    'deep_sky_siril_processing.py': {'sha256': '7f5f5bca2a648d418f7b0f60f739bb5f759929fcda21c930d3df16d471b1fb44',
                                                     'size': 140667},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': '0a7f69dcafd573c1e51e6f6d500d9324bb7bc22b3dbf52e4339a7883b32f2bcd',
                                               'size': 141455},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422},
                    'deep_sky_star_reduction.py': {'sha256': '5d0efd3786d7f929bf35415ebbb529aaa2d333498e92da229d10ff53d24bc626',
                                                   'size': 49759}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}


_PRE_COLOR_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'tone_adjustment': 'remaining_goal_v1'},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '838c698747a82e02d7628b7d9076267ea4a8d7642a9cbc64aea7b5548461c555',
                                                     'size': 77598},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2bd0e12fbf19640e97b6cf7773238ae72f8e9451b0c454fd1d4cc57326381748',
                                                         'size': 50689},
                    'deep_sky_siril_processing.py': {'sha256': 'b235d0427de73c7991122137e4438d06e742e2f599b934da29d8a9c7e2a351af',
                                                     'size': 141285},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': '59230c3c84c9507d9637a7842cc66db1c254e8046bcc90b7ebf74789fc949280',
                                               'size': 142211},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422},
                    'deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                               'size': 13176},
                    'deep_sky_star_reduction.py': {'sha256': '5d0efd3786d7f929bf35415ebbb529aaa2d333498e92da229d10ff53d24bc626',
                                                   'size': 49759}}}


_PRE_LOCAL_HDR_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'color_evaluation': 'unmasked_first_v1'},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': 'da569c15ffe66d9e69957851541608cbfcd7bb0cb000609d5d7851470f5250b3',
                                                     'size': 81987},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '2bd0e12fbf19640e97b6cf7773238ae72f8e9451b0c454fd1d4cc57326381748',
                                                         'size': 50689},
                    'deep_sky_siril_processing.py': {'sha256': '989e92d1d56dc809eadd6644408cabbe874ac1991c53fc6f1aea0a59da789163',
                                                     'size': 143071},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': 'cd37e16d927e373985c10fbae4df4c7c9cd6cf67ec8098f8e89b05feb5149af8',
                                               'size': 143092},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422},
                    'deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                               'size': 13176},
                    'deep_sky_siril_color.py': {'sha256': '376cb00432e175424a9fa5a75e5b9b75e637581fb2151d503f9df09dd685db55',
                                                'size': 52105},
                    'deep_sky_star_reduction.py': {'sha256': '5d0efd3786d7f929bf35415ebbb529aaa2d333498e92da229d10ff53d24bc626',
                                                   'size': 49759}}}

_PRE_COLOR_CONFIG_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'color_evaluation': 'unmasked_first_v1'},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '78c9239e25c283f4d1218f8961d07a3e3b6cc843fb354f4ac7a4abc425b68f30',
                                                     'size': 86631},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '9b0540240ef697ca6d62cb8a73f74002809465187e83c14dda673792e625c8fa',
                                                         'size': 53758},
                    'deep_sky_siril_processing.py': {'sha256': 'cd838b8c5b21eecd4d02921f58f6fbc9049300d30e439d244433e39b46c885aa',
                                                     'size': 143157},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': 'cd37e16d927e373985c10fbae4df4c7c9cd6cf67ec8098f8e89b05feb5149af8',
                                               'size': 143092},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422},
                    'deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                               'size': 13176},
                    'deep_sky_siril_color.py': {'sha256': '376cb00432e175424a9fa5a75e5b9b75e637581fb2151d503f9df09dd685db55',
                                                'size': 52105},
                    'deep_sky_star_reduction.py': {'sha256': '5d0efd3786d7f929bf35415ebbb529aaa2d333498e92da229d10ff53d24bc626',
                                                   'size': 49759}}}

# Exact complete implementation before fixed-catalog recomposition diagnostics.
_PRE_RECOMPOSITION_GEOMETRY_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'color_evaluation': 'unmasked_first_v1'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': '33b9b371943e23db61e0006541c1d137d2f4ac9bf7dd252f958303abc5840fa0',
                                                     'size': 91344},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '9b0540240ef697ca6d62cb8a73f74002809465187e83c14dda673792e625c8fa',
                                                         'size': 53758},
                    'deep_sky_siril_processing.py': {'sha256': 'cd838b8c5b21eecd4d02921f58f6fbc9049300d30e439d244433e39b46c885aa',
                                                     'size': 143157},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': 'cd37e16d927e373985c10fbae4df4c7c9cd6cf67ec8098f8e89b05feb5149af8',
                                               'size': 143092},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422},
                    'deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                               'size': 13176},
                    'deep_sky_siril_color.py': {'sha256': '5639da7745af5e73d4bdbf46e8f3d018f6cdf5667ff4c3965ca0e9aff890770f',
                                                'size': 54506},
                    'deep_sky_star_reduction.py': {'sha256': '5d0efd3786d7f929bf35415ebbb529aaa2d333498e92da229d10ff53d24bc626',
                                                   'size': 49759}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}


_PRE_DEEPSNR_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'color_evaluation': 'unmasked_first_v1'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': 'e5a3bd45a2d960b30cd7092e143fdaec8171d665d26afa5c15f41ae9122953c9',
                                                     'size': 96276},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '9b0540240ef697ca6d62cb8a73f74002809465187e83c14dda673792e625c8fa',
                                                         'size': 53758},
                    'deep_sky_siril_processing.py': {'sha256': 'cd838b8c5b21eecd4d02921f58f6fbc9049300d30e439d244433e39b46c885aa',
                                                     'size': 143157},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': 'cd37e16d927e373985c10fbae4df4c7c9cd6cf67ec8098f8e89b05feb5149af8',
                                               'size': 143092},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': '2f683ed60b2eb9e994cbf21559c366dbf1694dda7b2e0454f294b185a07c73a8',
                                                  'size': 27422},
                    'deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                               'size': 13176},
                    'deep_sky_siril_color.py': {'sha256': '5639da7745af5e73d4bdbf46e8f3d018f6cdf5667ff4c3965ca0e9aff890770f',
                                                'size': 54506},
                    'deep_sky_siril_psf.py': {'sha256': 'b463f6b0575cfe2cbe5a08964e82a212ad1c953f8a858db835ca3af858eb2350',
                                              'size': 44340},
                    'deep_sky_star_reduction.py': {'sha256': '502a1dae3df000c880d4cf523d4267fcbad8e027a87b1163bc61eb3cfb2946a4',
                                                   'size': 64618},
                    'siril_psf_samples.py': {'sha256': '694e4d34869331ca776827c2442da24c4a0d0fd3e9ab8e75cefae9083a0cd721',
                                             'size': 15655}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163',
              'size': 3844}}

_PRE_SUBJECT_PRIORITY_BINDINGS = {'execution_policy': {'log_diagnostics': 'fail_closed_v3',
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
                      'color_evaluation': 'unmasked_first_v1'},
 'implementation': {'deep_sky_siril_stretch_v2.py': {'sha256': 'a7380cbc55f97fb55757aee30221a5f04003ccd9c3c56df2e60db3a425eeb18b',
                                                     'size': 101603},
                    'deep_sky_siril_stretch.py': {'sha256': 'cc750a8143d9c023e00a0270b5927eadb6651d88159b95ed31ccc61c75ac4256',
                                                  'size': 62787},
                    'deep_sky_siril_stretch_profiles.py': {'sha256': '0438905bdc4cb60a2de25c4886ab8df922f3a5b7b34aa2a5bd08f4a8a3eeb2f8',
                                                           'size': 21428},
                    'deep_sky_siril_local_contrast.py': {'sha256': '9b0540240ef697ca6d62cb8a73f74002809465187e83c14dda673792e625c8fa',
                                                         'size': 53758},
                    'deep_sky_siril_processing.py': {'sha256': 'a50489a7d8e683e972d4e363d1faca05b569500b4718b9b91d51fb2ef354f400',
                                                     'size': 143475},
                    'deep_sky_siril_hdr.py': {'sha256': 'cb04cb794abe750fa02deb5d6e7b9216400d6573b87fba969e82d195bc5eefe6',
                                              'size': 18934},
                    'deep_sky_siril_dualband.py': {'sha256': 'a6b566b4c2eafc74304291acbb5846ef19becafdfcf7574bb4ef5903219c09a8',
                                                   'size': 42748},
                    'deep_sky_siril_core.py': {'sha256': '0c298e1dc5de01cde5b3dc433bbd8acaca0b386ac679ac8a27dd00ad2cfc83ac',
                                               'size': 143651},
                    'deep_sky_siril_visibility.py': {'sha256': '2f45e019b39d42a5719d2dff0d6598d2dd21e84206e9c7443cb823bc01ffd052',
                                                     'size': 31804},
                    'deep_sky_siril_denoise.py': {'sha256': 'e444c9a63d8918e8c670dc2f8cda8321bb2d1d6a330d0314f0a2b355e4b009ef',
                                                  'size': 34179},
                    'deep_sky_siril_tone.py': {'sha256': 'ff88711dbb03c4517150fdd88bc9c702ab0abd6acf77b39847a7c053fc0fb622',
                                               'size': 13176},
                    'deep_sky_siril_color.py': {'sha256': '5639da7745af5e73d4bdbf46e8f3d018f6cdf5667ff4c3965ca0e9aff890770f',
                                                'size': 54506},
                    'deep_sky_siril_psf.py': {'sha256': 'b463f6b0575cfe2cbe5a08964e82a212ad1c953f8a858db835ca3af858eb2350',
                                              'size': 44340},
                    'deep_sky_star_reduction.py': {'sha256': '502a1dae3df000c880d4cf523d4267fcbad8e027a87b1163bc61eb3cfb2946a4',
                                                   'size': 64618},
                    'siril_psf_samples.py': {'sha256': '694e4d34869331ca776827c2442da24c4a0d0fd3e9ab8e75cefae9083a0cd721',
                                             'size': 15655},
                    'siril_deepsnr.py': {'sha256': '7c69036810fca9bc6b36f9f0473d93c228442fb00732c60ea91b19b1fd84373a',
                                         'size': 13259}},
 'profiles': {'sha256': '50d85c5751f150bc5c4723babf2a8638d6ce99585b5c7c92511d814dbd897163', 'size': 3844}}


def _historical_bindings(session):
    if session is None:
        return None
    from deep_sky_siril_session import (_ROI_STRETCH_EXECUTION_POLICY, _PROFILE_STRETCH_EXECUTION_POLICY,
                                      _STARLESS_STRETCH_EXECUTION_POLICY, _AGENT_PROPOSAL_EXECUTION_POLICY)
    policy = load_json(session/'session.json').get('execution_policy')
    from deep_sky_siril_session import _PRE_MULTISTAGE_BINDINGS
    if policy == _PRE_MULTISTAGE_BINDINGS['execution_policy']:
        return _PRE_MULTISTAGE_BINDINGS
    from deep_sky_siril_session import _PRE_CONTOUR_BINDINGS
    if policy == _PRE_CONTOUR_BINDINGS['execution_policy']:
        return _PRE_CONTOUR_BINDINGS
    from deep_sky_siril_session import _PRE_ARTIFACT_FOCUS_BINDINGS
    if policy == _PRE_ARTIFACT_FOCUS_BINDINGS['execution_policy']:
        return _PRE_ARTIFACT_FOCUS_BINDINGS
    from deep_sky_siril_session import _PRE_STEPWISE_BINDINGS
    if policy == _PRE_STEPWISE_BINDINGS['execution_policy']:
        return _PRE_STEPWISE_BINDINGS
    from deep_sky_siril_session import _PRE_HDR_ROUTING_BINDINGS, _PRE_STAGE7_DOCUMENT_BINDINGS
    if policy == _PRE_HDR_ROUTING_BINDINGS['execution_policy']:
        return _PRE_HDR_ROUTING_BINDINGS
    if policy == _PRE_STAGE7_DOCUMENT_BINDINGS['execution_policy']:
        return _PRE_STAGE7_DOCUMENT_BINDINGS
    from deep_sky_siril_session import _PRE_STAGE7_REVISION_BINDINGS, _PRE_STAGE7_OPTIMIZATION_BINDINGS, _PRE_EVIDENCE_ADMISSION_BINDINGS
    if policy == _PRE_STAGE7_REVISION_BINDINGS['execution_policy']:
        return _PRE_STAGE7_REVISION_BINDINGS
    if policy == _PRE_STAGE7_OPTIMIZATION_BINDINGS['execution_policy']:
        return _PRE_STAGE7_OPTIMIZATION_BINDINGS
    if policy == _PRE_EVIDENCE_ADMISSION_BINDINGS['execution_policy']:
        return _PRE_EVIDENCE_ADMISSION_BINDINGS
    from deep_sky_siril_session import _PRE_CURVE_EFFECT_BINDINGS, _PRE_CURVE_AMPLIFICATION_BINDINGS
    if policy == _PRE_CURVE_EFFECT_BINDINGS['execution_policy']:
        return _PRE_CURVE_EFFECT_BINDINGS
    if policy == _PRE_CURVE_AMPLIFICATION_BINDINGS['execution_policy']:
        return _PRE_CURVE_AMPLIFICATION_BINDINGS
    from deep_sky_siril_session import _PRE_RESUME_BINDINGS
    if policy == _PRE_RESUME_BINDINGS['execution_policy']:
        return _PRE_RESUME_BINDINGS
    from deep_sky_siril_session import _PRE_TAIL_COMPARISON_BINDINGS
    if policy == _PRE_TAIL_COMPARISON_BINDINGS['execution_policy']:
        return _PRE_TAIL_COMPARISON_BINDINGS
    from deep_sky_siril_session import _PRE_STRETCH_MORPHOLOGY_BINDINGS
    if policy == _PRE_STRETCH_MORPHOLOGY_BINDINGS['execution_policy']:
        return _PRE_STRETCH_MORPHOLOGY_BINDINGS
    from deep_sky_siril_session import _PRE_BACKGROUND_REVIEW_BINDINGS
    if policy == _PRE_BACKGROUND_REVIEW_BINDINGS['execution_policy']:
        return _PRE_BACKGROUND_REVIEW_BINDINGS
    from deep_sky_siril_session import _PRE_VIVID_BINDINGS, _PRE_CHROMA_BINDINGS, _PRE_STAR_COLOR_BINDINGS, _PRE_REGION_COLOR_BINDINGS
    from deep_sky_siril_session import _PRE_SUBJECT_MASK_BINDINGS, _PRE_STAR_STRETCH_BINDINGS, _PRE_STRETCH_ROUTING_BINDINGS
    from deep_sky_siril_session import _PRE_STRETCH_TRIAL_BINDINGS, _PRE_STRETCH_PREFLIGHT_BINDINGS
    from deep_sky_siril_session import _PRE_LOCAL_CONTRAST_MASK_BINDINGS
    from deep_sky_siril_session import _PRE_COLOR_CURVE_VARIANTS_BINDINGS
    if policy == _PRE_COLOR_CURVE_VARIANTS_BINDINGS['execution_policy']:
        return _PRE_COLOR_CURVE_VARIANTS_BINDINGS
    if policy == _PRE_LOCAL_CONTRAST_MASK_BINDINGS['execution_policy']:
        return _PRE_LOCAL_CONTRAST_MASK_BINDINGS
    if policy == _PRE_STRETCH_PREFLIGHT_BINDINGS['execution_policy']:
        return _PRE_STRETCH_PREFLIGHT_BINDINGS
    if policy == _PRE_STRETCH_TRIAL_BINDINGS['execution_policy']:
        return _PRE_STRETCH_TRIAL_BINDINGS
    if policy == _PRE_STRETCH_ROUTING_BINDINGS['execution_policy']:
        return _PRE_STRETCH_ROUTING_BINDINGS
    if policy == _PRE_STAR_STRETCH_BINDINGS['execution_policy']:
        return _PRE_STAR_STRETCH_BINDINGS
    if policy == _PRE_SUBJECT_MASK_BINDINGS['execution_policy']:
        return _PRE_SUBJECT_MASK_BINDINGS
    if policy == _PRE_REGION_COLOR_BINDINGS['execution_policy']:
        return _PRE_REGION_COLOR_BINDINGS
    if policy == _PRE_STAR_COLOR_BINDINGS['execution_policy']:
        return _PRE_STAR_COLOR_BINDINGS
    if policy == _PRE_CHROMA_BINDINGS['execution_policy']:
        return _PRE_CHROMA_BINDINGS
    if policy == _PRE_VIVID_BINDINGS['execution_policy']:
        return _PRE_VIVID_BINDINGS
    if policy == _PRE_SUBJECT_PRIORITY_BINDINGS['execution_policy']:
        return _PRE_SUBJECT_PRIORITY_BINDINGS
    if policy == _PRE_DEEPSNR_BINDINGS['execution_policy']:
        return _PRE_DEEPSNR_BINDINGS
    if policy == _ROI_STRETCH_EXECUTION_POLICY:
        return _V22_BINDINGS
    if policy == _PROFILE_STRETCH_EXECUTION_POLICY:
        return _V23_BINDINGS
    if policy == _STARLESS_STRETCH_EXECUTION_POLICY:
        return _V24_BINDINGS
    if policy == _AGENT_PROPOSAL_EXECUTION_POLICY:
        return _V25_BINDINGS
    return None


def profiles_fingerprint(session=None):
    old = _historical_bindings(session)
    if old is None and session is not None:
        policy = load_json(session/'session.json').get('execution_policy')
        old = next((row for row in (_PRE_STAGE8_BINDINGS, _PRE_REFERENCE_BINDINGS, _PRE_ASSESSMENT_BINDINGS,
            _PRE_FINISH_BINDINGS, _PRE_ENHANCEMENT_BINDINGS, _PRE_DENOISE_BINDINGS, _PRE_RECOMPOSITION_BINDINGS,
            _PRE_TONE_BINDINGS, _PRE_COLOR_BINDINGS, _PRE_LOCAL_HDR_BINDINGS, _PRE_COLOR_CONFIG_BINDINGS,
            _PRE_RECOMPOSITION_GEOMETRY_BINDINGS) if row['execution_policy'] == policy), None)
    return old['profiles'].copy() if old else fingerprint(PROFILE_PATH, include_path=False)


def implementation(session=None):
    old = _historical_bindings(session)
    if old:
        return {name: value.copy() for name, value in old['implementation'].items()}
    names = ('artifact_focus', 'stretch_stepwise', 'stretch_v2', 'stretch', 'stretch_profiles', 'stretch_trials', 'stretch_preflight', 'morphology', 'local_contrast', 'processing', 'hdr', 'dualband', 'core', 'visibility', 'denoise', 'tone', 'color', 'psf', 'metrics', 'neutral', 'chroma', 'subject_mask', 'resume')
    return {p.name: fingerprint(p, include_path=False) for p in
            (base.ROOT/'scripts'/('deep_sky_siril_'+name+'.py') for name in names)} | {
            'deep_sky_star_reduction.py': fingerprint(base.ROOT/'scripts/deep_sky_star_reduction.py', include_path=False),
            'siril_psf_samples.py': fingerprint(base.ROOT/'scripts/siril_psf_samples.py', include_path=False),
            'siril_deepsnr.py': fingerprint(base.ROOT/'scripts/siril_deepsnr.py', include_path=False)}


def implementation_matches(session, binding):
    if binding == implementation(session):
        return True
    return any(load_json(session/'session.json').get('execution_policy') == old['execution_policy']
               and profiles_fingerprint(session) == old['profiles'] and binding == old['implementation']
               for old in (_PRE_STAGE8_BINDINGS, _PRE_REFERENCE_BINDINGS, _PRE_ASSESSMENT_BINDINGS, _PRE_FINISH_BINDINGS, _PRE_ENHANCEMENT_BINDINGS, _PRE_DENOISE_BINDINGS, _PRE_RECOMPOSITION_BINDINGS, _PRE_TONE_BINDINGS, _PRE_COLOR_BINDINGS, _PRE_LOCAL_HDR_BINDINGS, _PRE_COLOR_CONFIG_BINDINGS, _PRE_RECOMPOSITION_GEOMETRY_BINDINGS))


def full_is_diagnostic(payload):
    return payload.get('execution_policy', {}).get('starless_stretch') == FULL_DIAGNOSTIC_POLICY


def noise_is_diagnostic(payload):
    return payload.get('execution_policy', {}).get('stretch_noise') == 'diagnostic_only_v1'


def factor_values(step):
    fields = {'mtf': ('midtones',), 'asinh': ('stretch',), 'ght': ('D', 'B'),
              'autostretch': ('shadowsclip', 'targetbg')}
    method = step.get('method')
    names = fields.get(method) if isinstance(method, str) else None
    factors = step.get('factors')
    if names is None or not isinstance(factors, dict) or set(factors) != set(names):
        base.fail('Each proposed operator requires its exact explicit factor names')
    return tuple(base.number(factors[name]) for name in names)


def proposal_tokens(step, black, landmarks):
    values = factor_values(step)
    if step['method'] == 'ght' and not 0 <= values[1] <= 15:
        base.fail('The frozen Siril 1.4.4 CLI requires GHS B within [0, 15]')
    fmt = lambda value: format(float(value), '.17g')
    if step['method'] == 'mtf':
        tokens = ['mtf', fmt(black), fmt(values[0]), '1']
    elif step['method'] == 'asinh':
        tokens = ['asinh', '-human', fmt(values[0]), fmt(black), '-clipmode=rgbblend']
    else:
        return base.tokens_for(step['method'], values, black, landmarks)
    from deep_sky_siril_processing import validate_parameters
    validate_parameters(tokens, 'stretch')
    return tokens


def subject_constraints(constraints, regions, *, full_diagnostic=False):
    stars = {r['id'] for r in regions if r['role'] == 'stars'} if full_diagnostic else set()
    return [row for row in constraints if row['region'] not in stars]


def validate(request, regions, *, full=False, full_diagnostic=False, issues=None):
    profiles = load_json(PROFILE_PATH)
    if not isinstance(request.get('profile'), str) or request['profile'] not in profiles:
        base.fail('Declare a supported morphology reference or custom')
    if not {'background', 'faint'} <= {r['role'] for r in regions}:
        base.fail('Protected stretch needs trusted sky and faint-subject regions')
    faint = {r['id'] for r in regions if r['role'] == 'faint'}
    targets = request.get('targets')
    if (not isinstance(targets, dict) or set(targets) != faint
        or any(value not in ('bright', 'dark') for value in targets.values())):
        base.fail('Every faint ROI requires an explicit bright or dark target direction')
    cores = [r for r in regions if r['role'] == 'core']
    absence = request.get('core_absence')
    if (cores and absence is not None or not cores and
        (not isinstance(absence, str) or not absence.strip())):
        base.fail('Declare core protection or an actual no-visible-core reason')
    if full and not any(r['role'] == 'stars' for r in regions):
        base.fail('Separation branches require stellar protection regions')
    required = [r for r in regions if r['role'] in {'background', 'faint', 'core'}
                or full and not full_diagnostic and r['role'] == 'stars']
    steps = request['operators'] if 'operators' in request else [{'constraints': request.get('constraints')}]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        base.fail('Declare a nonempty bounded operator list')
    roles = {r['id']: r['role'] for r in regions}
    def reject(message, **location):
        if issues is None:
            base.fail(message)
        issues.append({'code': 'invalid_constraints', 'reason': message, **location})
    for index, step in enumerate(steps, 1):
        constraints = step.get('constraints') if isinstance(step, dict) else None
        if not isinstance(constraints, list) or not 1 <= len(constraints) <= 64:
            reject('Declare explicit bounded regional constraints', step=index)
            continue
        for row in constraints:
            if (not isinstance(row, dict) or set(row) != {'region', 'statistic', 'min', 'max'}
                or not isinstance(row['region'], str) or row['region'] not in roles
                or not isinstance(row['statistic'], str) or row['statistic'] not in base.STATISTICS | EXTRA_STATISTICS
                or base.number(row['min']) > base.number(row['max'])
                or row['statistic'] == 'hf_sigma2' and roles[row['region']] != 'background'):
                reject('Invalid protected regional constraint', step=index,
                       **{k: row.get(k) for k in ('region','statistic') if isinstance(row, dict)})
        for region in required:
            rows = [c for c in constraints if isinstance(c, dict) and c.get('region') == region['id']]
            stats = {c.get('statistic') for c in rows}
            if not rows:
                reject('Every step must protect all declared sky, faint, core and applicable stars ROIs', step=index, region=region['id'])
            if region['role'] == 'background' and not {'mad', 'hf_sigma2'} <= stats:
                reject('Every sky ROI needs explicit output MAD and hf_sigma2 budgets', step=index, region=region['id'])
            if region['role'] in {'core', 'stars'} and not ('span' in stats and stats & {'p99', 'p999', 'max_rgb'}):
                reject('Bright core and stellar protection require an upper brightness bound and a span constraint', step=index, region=region['id'])
    sky = [r for r in regions if r['role'] == 'background']
    for region in sky:
        for other in regions:
            if region['id'] == other['id']:
                continue
            a, b = region['rect'], other['rect']
            if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                base.fail('Trusted sky ROIs must not overlap other declared regions')


def statistics(data, regions):
    from deep_sky_siril_local_contrast import energies
    result = base.statistics(data, regions)
    height, width = data.shape[1:]
    sky = [r for r in regions if r['role'] == 'background']
    for region in sky:
        x0, y0, x1, y1 = region['rect']
        if min(x0, y0) < 8 or x1+8 > width or y1+8 > height:
            base.fail('Sky hf_sigma2 needs eight real context pixels on every side')
    hf = {r['id']: r['amplitude'] for r in energies(data, sky, scales=(2,))}
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        values = data[:, height-y1:height-y0, x0:x1]
        lum = values[0] if len(values) == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], values)
        result[region['id']].update(p999=float(np.percentile(lum, 99.9)), max_rgb=float(np.max(values)))
        if region['id'] in hf:
            result[region['id']]['hf_sigma2'] = hf[region['id']]
    return result


def endpoints(before, after):
    if before.shape != after.shape or not np.isfinite(after).all():
        base.fail('Protected stretch requires unchanged geometry and finite pixels')
    return [{'channel': i, 'existing_shadow': int(np.count_nonzero(a <= 0)),
             'existing_highlight': int(np.count_nonzero(a >= 1)),
             'new_shadow': int(np.count_nonzero((a > 0) & (b <= 0))),
             'new_highlight': int(np.count_nonzero((a < 1) & (b >= 1)))}
            for i, (a, b) in enumerate(zip(before, after))]


def highlight_overlay(before, after, preview):
    import io
    from PIL import Image, ImageFilter
    mask = np.any((before < 1) & (after >= 1), axis=0)
    if not mask.any():
        return None
    with Image.open(preview) as original:
        image = original.convert('RGB')
    if image.size != (mask.shape[1], mask.shape[0]):
        base.fail('Highlight preview geometry differs from scientific pixels')
    mask_image = Image.fromarray(np.flipud(mask).astype(np.uint8)*255, mode='L')
    outline = mask_image.filter(ImageFilter.MaxFilter(7))
    colored = Image.blend(image, Image.new('RGB', image.size, '#ff3030'), .8)
    image = Image.composite(colored, image, outline)
    image = Image.composite(Image.new('RGB', image.size, '#ffff00'), image, mask_image)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def formal_highlight_overlay(session, before, after, preview, primary, *, write=False):
    content = highlight_overlay(before, after, preview)
    if content is None:
        return None
    path = base.session_path(session, 'reports/'+primary.stem+'-stage7-highlights.png', allowed_roots=('reports',))
    if write:
        with path.open('xb') as stream:
            stream.write(content)
    elif not path.is_file() or path.read_bytes() != content:
        base.fail('Formal highlight location evidence changed')
    return base.record(session, path)


def audit(before, after, regions, constraints, targets, *, stellar_only=False, noise_diagnostic=False, highlight_budget=False):
    values = statistics(after, regions)
    rows = base.check_constraints(values, constraints)
    rates, crossings = base.clipping(after), endpoints(before, after)
    reasons = []
    sky_ids = {r['id'] for r in regions if r['role'] == 'background'}
    advisory = [row for row in rows if noise_diagnostic and row['region'] in sky_ids
                and row['statistic'] in {'mad', 'hf_sigma2'}]
    if any(not row['satisfied'] for row in rows if row not in advisory):
        reasons.append('regional_constraints')
    if any(row['shadow_clip_rate'] >= base.SHADOW_LIMIT for row in rates):
        reasons.append('shadow_limit')
    if any(row['new_highlight'] * (10000 if highlight_budget else 1) > (before.shape[1]*before.shape[2] if highlight_budget else 0) for row in crossings):
        reasons.append('new_highlight_crossing')
    sky = [values[r['id']] for r in regions if r['role'] == 'background']
    background = float(np.median([r['median'] for r in sky]))
    mad = float(np.median([r['mad'] for r in sky]))
    deltas = {key: (values[key]['median']-background)*(1 if direction == 'bright' else -1)
              for key, direction in targets.items()}
    if not stellar_only and any(value <= 0 for value in deltas.values()):
        reasons.append('target_direction')
    score = min(deltas.values())/mad if mad > 1e-12 else None
    height = before.shape[1]
    roi_endpoints = {}
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        sl = (slice(None), slice(height-y1, height-y0), slice(x0, x1))
        roi_endpoints[region['id']] = endpoints(before[sl], after[sl])
    result = {'statistics': values, 'constraints': rows, 'clipping': rates, 'endpoints': crossings,
            'roi_endpoints': roi_endpoints,
            'reasons': reasons, 'eligible': not reasons,
            'separation': None if stellar_only else score,
            'separation_role': 'display_diagnostic_not_physical_snr',
            'score_fallback': 'sky_mad_at_most_1e-12' if score is None else None,
            'sky_hf': float(np.median([r['hf_sigma2'] for r in sky]))}
    if highlight_budget:
        count = before.shape[1]*before.shape[2]
        result['highlight_budget'] = {'limit': 0.0001, 'pixel_count': count,
            'rates': [row['new_highlight']/count for row in crossings],
            'warning': any(row['new_highlight'] for row in crossings)}
    if noise_diagnostic:
        result.update(noise_diagnostic=True, noise_warnings=[row for row in advisory if not row['satisfied']],
                      subject_contrast=None if stellar_only else min(deltas.values()))
    return result


def trial_audit(before, path, regions, constraints, targets, *, stellar_only=False, reject_nonfinite=False,
                noise_diagnostic=False, highlight_budget=False):
    try:
        value = base.pixels(path)
    except base.ContractError:
        if not reject_nonfinite:
            raise
        raw, scale, zero = base.read_fits_pixels(path)
        value = np.asarray(raw, dtype=np.float64)*scale+zero
        count = int(np.count_nonzero(~np.isfinite(value)))
        if value.shape != before.shape or not count:
            raise
        return {'eligible': False, 'reasons': ['nonfinite_native_trial'], 'constraints': [],
                'separation': None, 'sky_hf': None, 'nonfinite_count': count}
    return audit(before, value, regions, constraints, targets, stellar_only=stellar_only,
                 noise_diagnostic=noise_diagnostic, highlight_budget=highlight_budget)


def select(trials, *, full_diagnostic=False, noise_diagnostic=False):
    eligible = [i for i, row in enumerate(trials) if row['audit']['eligible'] and
                (not row.get('full_audit') or
                 (not row['full_audit'].get('nonfinite_count') if full_diagnostic else row['full_audit']['eligible']))]
    if not eligible:
        return None, []
    metric = 'subject_contrast' if noise_diagnostic else 'separation'
    scored = [i for i in eligible if trials[i]['audit'][metric] is not None]
    if noise_diagnostic:
        pool = scored or eligible
        key = lambda i: (-(trials[i]['audit'][metric] or 0), i)
    elif scored:
        best = max(trials[i]['audit']['separation'] for i in scored)
        pool = [i for i in scored if trials[i]['audit']['separation'] >= .95*best]
        key = lambda i: (trials[i]['audit']['sky_hf'], *base.gentle_key(
            [trials[i]['parameters']['command'], *trials[i]['parameters']['parameters']]),
            *trials[i]['coordinates'])
    else:
        pool = eligible
        key = lambda i: (*base.gentle_key([trials[i]['parameters']['command'],
                                        *trials[i]['parameters']['parameters']]), *trials[i]['coordinates'])
    chosen = min(pool, key=key)
    strongest = max(scored, key=lambda i: trials[i]['audit'][metric]) if scored else chosen
    gentle = min(eligible, key=lambda i: (*base.gentle_key(
        [trials[i]['parameters']['command'], *trials[i]['parameters']['parameters']]), *trials[i]['coordinates']))
    return chosen, list(dict.fromkeys((chosen, strongest, gentle)))


def local_panel(source, candidates, regions, output, *, title='Parent / selected / other representatives; identical [0,1] display'):
    from PIL import Image, ImageDraw
    arrays = [base.pixels(source), *[base.pixels(path) for path in candidates]]
    width, cell = 240*len(arrays), 180
    canvas = Image.new('RGB', (width, 28+cell*len(regions)), '#101418')
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), title, fill='white')
    for i, region in enumerate(regions):
        x0, y0, x1, y1 = region['rect']
        for j, array in enumerate(arrays):
            cut = array[:, array.shape[1]-y1:array.shape[1]-y0, x0:x1]
            if len(cut) == 1:
                cut = np.repeat(cut, 3, axis=0)
            display = Image.fromarray(np.uint8(np.clip(cut.transpose(1, 2, 0)[::-1], 0, 1)*255))
            display.thumbnail((224, 150))
            canvas.paste(display, (j*240+8, 48+i*cell))
            draw.text((j*240+8, 30+i*cell), region['id'], fill='white')
    canvas.save(output)


def solve(session, payload, request, source, scientific_source, full, regions, request_file, run_id, deadline, *, derived=False, shared_attempt=None):
    if payload.get('execution_policy') and payload['execution_policy'].get('stretch') != POLICY:
        base.fail('Protected solving requires a new v22 session')
    from deep_sky_siril_stretch_profiles import validate_body_chain
    if shared_attempt is None or request.get('strategy', {}).get('variant') != 'rgb':
        validate_body_chain(payload, request['operators'])
    full_diagnostic = (bool(full) or derived) and full_is_diagnostic(payload)
    noise_diagnostic = noise_is_diagnostic(payload)
    highlight_budget = base.optimization_enabled(payload)
    validate(request, regions, full=bool(full), full_diagnostic=full_diagnostic)
    from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST
    proposal = request['schema'] == PROPOSAL_REQUEST
    from deep_sky_siril_stretch_preflight import effects_enabled, ghs_effects
    effects = proposal and effects_enabled(payload)
    reject_nonfinite = proposal or request['schema'] == PROFILE_REQUEST
    statistics(base.pixels(source), regions)
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not runtime.get('fingerprint') or not fingerprint_matches(runtime['fingerprint']):
        base.fail('Frozen Siril runtime is missing or changed')
    directory = base.session_path(session, f'reports/{run_id}', allowed_roots=('reports',))
    from deep_sky_siril_stretch_trials import enabled as archive_enabled, directory as archive_directory
    if archive_enabled(payload) and archive_directory(session, run_id).exists():
        base.fail('Trial artifacts already exist; use a new run ID')
    if any(p.exists() or p.is_symlink() for p in (directory/'stretch-parameters.json', directory/'parameter-trials')):
        base.fail('Solve evidence already exists; use a new run ID')
    directory.mkdir(exist_ok=True)
    trials_dir = directory/'parameter-trials'
    trials_dir.mkdir()
    bindings = {'request': base.record(session, request_file), 'regions': request['regions'],
                'source': {**request['source'], 'size': scientific_source.stat().st_size},
                'runtime': runtime, 'implementation': implementation(),
                'profiles': fingerprint(PROFILE_PATH, include_path=False)}
    if full:
        bindings['full_source'] = base.record(session, full)
    if derived:
        bindings['derived_source'] = base.record(session, source)
    report = {'schema': DERIVED if derived else REPORT, 'policy': POLICY, 'run_id': run_id,
              'status': 'search_exhausted', 'bindings': bindings, 'request': request,
              'transfer_chain': [], 'steps': [], 'requires_visual_review': True}
    if full and full_diagnostic:
        report['full_replay_role'] = 'diagnostic'
    if proposal:
        report['generation'] = PROPOSAL_POLICY
        report['status'] = 'ineligible'
    def unchanged():
        base.source_file(session, payload, request['source'])
        for key in ('request', 'regions', 'derived_source', 'full_source'):
            if key in bindings:
                base.bound_file(session, bindings[key], ('artifacts',) if key == 'full_source' else ('reports',))
        if (implementation() != bindings['implementation'] or not fingerprint_matches(runtime['fingerprint'])
            or fingerprint(PROFILE_PATH, include_path=False) != bindings['profiles']):
            base.fail('Stretch implementation, morphology reference or runtime changed during solve')
    try:
        with tempfile.TemporaryDirectory(prefix='starun-stretch-v2-') as raw:
            oracle = base.NativeTrials(session, runtime, Path(raw), trials_dir, deadline)
            oracle.previews = True
            if proposal and not derived and shared_attempt is None:
                oracle.scientific_attempt = (session, payload, run_id, request)
            current, current_full = source, full
            for index, step in enumerate(request['operators']):
                subject_rows = subject_constraints(step['constraints'], regions, full_diagnostic=full_diagnostic)
                data = base.pixels(current)
                reference = load_json(base.bound_file(session, request['regions']))['reference']
                if data.shape[1:] != (reference['height'], reference['width']):
                    base.fail('Derived stretch geometry differs from bound regions')
                full_data = base.pixels(current_full) if full else None
                if full and full_data.shape != data.shape:
                    base.fail('Full/starless geometry differs')
                before = statistics(data, regions)
                black = base.safe_blackpoint([data]+([full_data] if full and not full_diagnostic else []), headroom=highlight_budget)
                landmarks = {k: before[v['region']][v['statistic']] for k, v in step.get('landmarks', {}).items()}
                if landmarks and not 0 <= landmarks['LP'] <= landmarks['SP'] <= landmarks['HP'] <= 1:
                    base.fail('Current-domain LP/SP/HP bounds are invalid')
                method = step['method']
                bounds = {'mtf': [(-16., 16.)], 'asinh': [(0., math.log(1000))],
                          'ght': [(float(np.finfo(np.float32).eps), 10.), (0., 15.)],
                          'autostretch': [(-8., -2.8), (.08, .18)]}[method]
                trace = {'method': method, 'purpose': step['purpose'], 'input_sha256': sha256_file(current),
                         'input_domain': 'linear' if index == 0 else 'nonlinear_intermediate',
                         'input_statistics': before, 'trials': []}
                report['steps'].append(trace)
                trial_start, seen, files = oracle.count, set(), []
                limit = (128-(2 if full else 1))//(2 if full else 1)

                def evaluate(coordinates):
                    if coordinates in seen or len(seen) >= limit:
                        return
                    seen.add(coordinates)
                    tokens = proposal_tokens(step, black, landmarks) if proposal else base.tokens_for(method, coordinates, black, landmarks)
                    parameter = {'command': tokens[0], 'parameters': tokens[1:]}
                    output, evidence = oracle.run(current, [parameter])
                    result = trial_audit(data, output, regions, subject_rows, request['targets'],
                                         reject_nonfinite=reject_nonfinite, noise_diagnostic=noise_diagnostic, highlight_budget=highlight_budget)
                    row = {'coordinates': coordinates, 'parameters': parameter, 'audit': result, 'evidence': evidence}
                    full_output = None
                    if full:
                        full_output, full_evidence = oracle.run(current_full, [parameter])
                        constraints = [c for c in step['constraints'] if next(
                            r['role'] for r in regions if r['id'] == c['region']) == 'stars']
                        row['full_audit'] = trial_audit(full_data, full_output, regions, constraints,
                                                       request['targets'], stellar_only=True, reject_nonfinite=reject_nonfinite, highlight_budget=highlight_budget)
                        evidence['full_source'] = full_evidence
                    trace['trials'].append(row)
                    files.append((output, full_output))

                if proposal:
                    evaluate(factor_values(step))
                else:
                    gentlest = {'mtf': (0.,), 'asinh': (0.,), 'ght': (float(np.finfo(np.float32).eps), 0.),
                                'autostretch': (-8., .08)}[method]
                    if reject_nonfinite and method == 'ght':
                        gentlest = (.001, 0.)
                    evaluate(gentlest)
                    if method == 'mtf':
                        for constraint in subject_rows:
                            if constraint['statistic'] == 'median':
                                x = (before[constraint['region']]['median']-black)/(1-black)
                                y = (constraint['min']+constraint['max'])/2
                                if 0 < x < 1 and 0 < y < 1:
                                    mid = x*(y-1)/(2*x*y-y-x)
                                    if 0 < mid < 1:
                                        evaluate((min(16., max(-16., math.log(mid/(1-mid)))),))
                    search_bounds = bounds
                    for level in range(4):
                        count = 17 if len(bounds) == 1 else (7 if level == 0 else 5)
                        grids = [np.linspace(lo, hi, count).tolist() for lo, hi in search_bounds]
                        if reject_nonfinite and method == 'ght' and level == 0:
                            # Nonlinear prefixes need low-D samples that a uniform 0..10 grid misses.
                            grids = [[.001, .01, .1, .6, 2., 5., 10.], [0., .5, 1., 3., 6., 10., 15.]]
                        for coordinates in sorted(itertools.product(*grids), key=lambda x: base.gentle_key(
                                base.tokens_for(method, x, black, landmarks))):
                            evaluate(coordinates)
                        if len(seen) >= limit:
                            break
                        chosen, _ = select(trace['trials'], full_diagnostic=full_diagnostic, noise_diagnostic=noise_diagnostic)
                        if chosen is None:
                            chosen = min(range(len(trace['trials'])), key=lambda i: (
                                'nonfinite_count' in trace['trials'][i]['audit'] or 'nonfinite_count' in trace['trials'][i].get('full_audit', {}),
                                len(trace['trials'][i]['audit']['reasons'])+(0 if full_diagnostic else len(trace['trials'][i].get('full_audit', {}).get('reasons', []))),
                                base.violation([c for c in trace['trials'][i]['audit']['constraints']
                                                if c not in trace['trials'][i]['audit'].get('noise_warnings', [])])))
                        center = trace['trials'][chosen]['coordinates']
                        search_bounds = [(max(original[0], x-(hi-lo)/(count-1)), min(original[1], x+(hi-lo)/(count-1)))
                                         for x, (lo, hi), original in zip(center, search_bounds, bounds)]
                chosen, representatives = select(trace['trials'], full_diagnostic=full_diagnostic, noise_diagnostic=noise_diagnostic)
                trace.update(evaluation_count=len(seen), native_evaluation_count=oracle.count-trial_start,
                             selected_trial=chosen, representative_trials=representatives)
                if chosen is None and not proposal:
                    report['reason'] = f'No protected candidate found for operator {index+1}'
                    break
                if chosen is None:
                    representatives = [0]
                    trace['representative_trials'] = representatives
                if all('nonfinite_count' not in trace['trials'][i]['audit'] for i in representatives):
                    panel = trials_dir/f'step-{index+1}-fixed-rois.png'
                    local_panel(current, [files[i][0] for i in representatives], regions, panel)
                    trace['local_panel'] = base.record(session, panel)
                for trial_index in representatives:
                    row = trace['trials'][trial_index]
                    row['retained'] = {}
                    for role, path in zip(('candidate', 'full'), files[trial_index]):
                        if path is not None:
                            for suffix in ('.fit', '.jpg'):
                                original = path.with_suffix(suffix)
                                target = trials_dir/f'step-{index+1}-{trial_index+1}-{role}{suffix}'
                                shutil.copyfile(original, target)
                                row['retained'][role+suffix] = base.record(session, target)
                            if highlight_budget and 'nonfinite_count' not in row['audit' if role == 'candidate' else 'full_audit']:
                                after = base.pixels(path)
                                local = data if role == 'candidate' else full_data
                                origin = base.pixels(source if role == 'candidate' else full)
                                for label, before_pixels in (('highlight', local), ('cumulative-highlight', origin)):
                                    if index == 0 and label == 'cumulative-highlight':
                                        continue
                                    overlay = highlight_overlay(before_pixels, after, path.with_suffix('.jpg'))
                                    if overlay is not None:
                                        target = trials_dir/f'step-{index+1}-{trial_index+1}-{role}-{label}.png'
                                        with target.open('xb') as stream:
                                            stream.write(overlay)
                                        row['retained'][role+'-'+label+'.png'] = base.record(session, target)
                if chosen is None:
                    if effects and method == 'ght' and 'statistics' in trace['trials'][0]['audit']:
                        origin = bindings.get('derived_source',request['source']) if index == 0 else report['steps'][index-1]['selected']['.fit']
                        trace['effect_diagnostics'] = ghs_effects(before,trace['trials'][0]['audit']['statistics'],regions,
                            request['targets'],origin,trace['trials'][0]['retained']['candidate.fit'])
                    report['reason'] = f'Agent proposal fails frozen protection at operator {index+1}; parent retained'
                    break
                selected = trace['trials'][chosen]['parameters']
                trace['selected_parameters'] = selected
                report['transfer_chain'].append(selected)
                current, evidence = oracle.run(source, report['transfer_chain'])
                trace['selected_replay'] = evidence
                trace['prefix_audit'] = audit(base.pixels(source), base.pixels(current), regions,
                                              subject_rows, request['targets'], noise_diagnostic=noise_diagnostic, highlight_budget=highlight_budget)
                if full:
                    current_full, evidence = oracle.run(full, report['transfer_chain'])
                    trace['selected_full_replay'] = evidence
                    constraints = [c for c in step['constraints'] if next(
                        r['role'] for r in regions if r['id'] == c['region']) == 'stars']
                    trace['full_prefix_audit'] = audit(base.pixels(full), base.pixels(current_full), regions,
                                                       constraints, request['targets'], stellar_only=True, highlight_budget=highlight_budget)
                for role, path in (('selected', current), ('selected-full', current_full if full else None)):
                    if path is not None:
                        trace[role] = {}
                        for suffix in ('.fit', '.jpg'):
                            target = trials_dir/f'step-{index+1}-{role}{suffix}'
                            shutil.copyfile(path.with_suffix(suffix), target)
                            trace[role][suffix] = base.record(session, target)
                trace['constraints'] = trace['prefix_audit']['constraints']
                if effects and method == 'ght':
                    origin = bindings.get('derived_source',request['source']) if index == 0 else report['steps'][index-1]['selected']['.fit']
                    trace['effect_diagnostics'] = ghs_effects(before,trace['prefix_audit']['statistics'],regions,
                        request['targets'],origin,trace['selected']['.fit'])
                trace['native_evaluation_count'] = oracle.count-trial_start
                if not trace['prefix_audit']['eligible'] or full and not full_diagnostic and not trace['full_prefix_audit']['eligible']:
                    base.fail('Complete prefix replay fails frozen protection')
                assert trace['native_evaluation_count'] <= 128
            else:
                report.update(status='solved', final_constraints=trace['constraints'],
                              final_clipping=trace['prefix_audit']['clipping'], final_sha256=sha256_file(current))
            unchanged()
            if time.monotonic() > deadline:
                raise TimeoutError('Stretch solve timed out')
    except (base.ContractError, OSError, ValueError, TimeoutError) as exc:
        report.update(status='timed_out' if isinstance(exc, TimeoutError) else 'failed', reason=str(exc))
    try:
        unchanged()
    except (base.ContractError, OSError, ValueError) as exc:
        report.update(status='failed', reason=str(exc))
    if archive_enabled(payload):
        from deep_sky_siril_stretch_trials import create
        try:
            report['trial_artifacts'] = create(session, report)
        except (base.ContractError, OSError, ValueError) as exc:
            report.update(status='failed', reason='Stage 7 trial archiving failed: '+str(exc))
    path = directory/'stretch-parameters.json'
    with path.open('x') as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write('\n')
    return {'status': report['status'], 'parameters_path': str(path), 'report': report}


def verify(session, report, source, regions, *, incomplete=False):
    try:
        _verify(session, report, source, regions, incomplete=incomplete)
        from deep_sky_siril_stretch_trials import enabled as archive_enabled, verify as verify_archive
        if archive_enabled(load_json(session/'session.json')):
            verify_archive(session, report)
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        base.fail(f'Malformed protected stretch evidence: {type(exc).__name__}')


def _verify(session, report, source, regions, *, incomplete=False):
    from deep_sky_siril_artifacts import diagnose_siril_log
    bindings = report['bindings']
    if (report.get('policy') != POLICY or not implementation_matches(session, bindings.get('implementation'))
        or bindings.get('profiles') != profiles_fingerprint(session)):
        base.fail('Protected stretch implementation or morphology reference changed')
    full = base.bound_file(session, bindings['full_source'], ('artifacts',)) if 'full_source' in bindings else None
    proposal = report['request']['schema'] == PROPOSAL_REQUEST
    if report.get('generation') != (PROPOSAL_POLICY if proposal else None):
        base.fail('Parameter generation mode differs from its request')
    payload = load_json(session/'session.json')
    from deep_sky_siril_stretch_preflight import effects_enabled, ghs_effects
    effects = proposal and effects_enabled(payload)
    full_diagnostic = (bool(full) or 'derived_source' in bindings) and full_is_diagnostic(payload)
    noise_diagnostic = noise_is_diagnostic(payload)
    highlight_budget = base.optimization_enabled(payload)
    from deep_sky_siril_stretch_profiles import routing_required
    strict = routing_required(payload)

    def native_script(evidence, parent, chain):
        from deep_sky_siril_processing import script_commands, transfer_chain
        text = base.bound_file(session, evidence['script']).read_text()
        commands = script_commands(text)
        loads = [row for row in commands if row[0] == 'load']
        saves = [row for row in commands if row[0] == 'save']
        allowed = base.METHODS | {'requires', 'set32bits', 'load', 'save', 'savejpg', 'icc_assign', 'close'}
        if (any(row[0] not in allowed for row in commands) or len(loads) != 1 or len(saves) != 1
            or Path(loads[0][1]).resolve() != parent.resolve()
            or transfer_chain(text, Path(saves[0][1]).resolve()) != chain):
            base.fail('Native scientific evidence source or operator chain differs from its proposal')
        return Path(saves[0][1]).resolve()
    if report.get('full_replay_role') != ('diagnostic' if full and full_diagnostic else None):
        base.fail('Full replay role differs from the frozen starless policy')
    validate(report['request'], regions, full=bool(full), full_diagnostic=full_diagnostic)
    previous, previous_full = source, full
    previous_native, previous_native_full = source, full
    for index, trace in enumerate(report['steps']):
        if proposal:
            step = report['request']['operators'][index]
            before = statistics(base.pixels(previous), regions)
            black = base.safe_blackpoint([base.pixels(previous)]+([base.pixels(previous_full)] if full and not full_diagnostic else []), headroom=highlight_budget)
            landmarks = {k: before[v['region']][v['statistic']] for k, v in step.get('landmarks', {}).items()}
            expected = proposal_tokens(step, black, landmarks)
            if (len(trace['trials']) != 1 or trace['input_statistics'] != before
                or trace['trials'][0]['parameters'] != {'command': expected[0], 'parameters': expected[1:]}
                or tuple(trace['trials'][0]['coordinates']) != factor_values(step)):
                base.fail('Agent proposal factors, current-domain anchors or trial count changed')
        chosen, representatives = select(trace['trials'], full_diagnostic=full_diagnostic, noise_diagnostic=noise_diagnostic)
        failed_proposal = incomplete and proposal and chosen is None
        if failed_proposal:
            representatives = [0]
        expected_count = (len(trace['trials']) + (0 if failed_proposal else 1)) * (2 if full else 1)
        if (trace.get('selected_trial') != chosen or trace.get('representative_trials') != representatives
            or chosen is None and not failed_proposal
            or chosen is not None and trace['selected_parameters'] != trace['trials'][chosen]['parameters']
            or trace['evaluation_count'] != len(trace['trials'])
            or trace['native_evaluation_count'] != expected_count or expected_count > 128):
            base.fail('Protected trial selection or evaluation budget changed')
        constraints = report['request']['operators'][index]['constraints']
        subject_rows = subject_constraints(constraints, regions, full_diagnostic=full_diagnostic)
        for trial_index in representatives:
            row = trace['trials'][trial_index]
            for binding in row['retained'].values():
                base.bound_file(session, binding)
            value = base.pixels(base.bound_file(session, row['retained']['candidate.fit']))
            if sha256_file(base.bound_file(session, row['retained']['candidate.fit'])) != row['evidence']['output_sha256']:
                base.fail('Retained candidate differs from native output fingerprint')
            if audit(base.pixels(previous), value, regions, subject_rows, report['request']['targets'],
                     noise_diagnostic=noise_diagnostic, highlight_budget=highlight_budget) != row['audit']:
                base.fail('Retained candidate differs from its regional measurements')
            if highlight_budget:
                for label, origin in (('highlight', previous), ('cumulative-highlight', source)):
                    if index == 0 and label == 'cumulative-highlight':
                        continue
                    key = 'candidate-'+label+'.png'
                    expected_overlay = highlight_overlay(base.pixels(origin), value, base.bound_file(session, row['retained']['candidate.jpg']))
                    if (expected_overlay is None) != (key not in row['retained']) or expected_overlay is not None and base.bound_file(session, row['retained'][key]).read_bytes() != expected_overlay:
                        base.fail('Candidate highlight location evidence changed')
            if full:
                stars = [c for c in constraints if next(r['role'] for r in regions if r['id'] == c['region']) == 'stars']
                value = base.pixels(base.bound_file(session, row['retained']['full.fit']))
                if sha256_file(base.bound_file(session, row['retained']['full.fit'])) != row['evidence']['full_source']['output_sha256']:
                    base.fail('Retained stellar image differs from native output fingerprint')
                if audit(base.pixels(previous_full), value, regions, stars, report['request']['targets'], stellar_only=True, highlight_budget=highlight_budget) != row['full_audit']:
                    base.fail('Retained stellar trial differs from its measurements')
                if highlight_budget:
                    for label, origin in (('highlight', previous_full), ('cumulative-highlight', full)):
                        if index == 0 and label == 'cumulative-highlight':
                            continue
                        key = 'full-'+label+'.png'
                        expected_overlay = highlight_overlay(base.pixels(origin), value, base.bound_file(session, row['retained']['full.jpg']))
                        if (expected_overlay is None) != (key not in row['retained']) or expected_overlay is not None and base.bound_file(session, row['retained'][key]).read_bytes() != expected_overlay:
                            base.fail('Full-source highlight location evidence changed')
        for row in trace['trials']:
            if strict:
                if trace['input_sha256'] != sha256_file(previous):
                    base.fail('Native scientific evidence input fingerprint changed')
                native_script(row['evidence'], previous_native, [row['parameters']])
                if full:
                    native_script(row['evidence']['full_source'], previous_native_full, [row['parameters']])
            for value in (row['audit'], row.get('full_audit', {})):
                if 'nonfinite_count' in value:
                    from deep_sky_siril_stretch_profiles import REQUEST as PROFILE_REQUEST
                    if (report['request']['schema'] not in {PROFILE_REQUEST, PROPOSAL_REQUEST} or type(value['nonfinite_count']) is not int
                        or value['nonfinite_count'] <= 0 or value != {
                            'eligible': False, 'reasons': ['nonfinite_native_trial'], 'constraints': [],
                            'separation': None, 'sky_hf': None, 'nonfinite_count': value['nonfinite_count']}):
                        base.fail('Numerically invalid trial cannot become an eligible candidate')
            for evidence in (row['evidence'], row['evidence'].get('full_source', {})):
                if evidence:
                    log = base.bound_file(session, evidence['log']).read_text()
                    if diagnose_siril_log(log, exit_code=0, timed_out=False, execution_valid=True)['status'] == 'failed':
                        base.fail('Protected trial log contains a native error')
        base.bound_file(session, trace['local_panel'])
        if failed_proposal:
            if trace is not report['steps'][-1] or report['status'] != 'ineligible':
                base.fail('Failed proposal evidence must stop at its first ineligible operator')
            if effects and step['method'] == 'ght':
                origin = bindings.get('derived_source',report['request']['source']) if index == 0 else report['steps'][index-1]['selected']['.fit']
                expected = ghs_effects(before,trace['trials'][0]['audit']['statistics'],regions,
                    report['request']['targets'],origin,trace['trials'][0]['retained']['candidate.fit'])
                if trace.get('effect_diagnostics') != expected:
                    base.fail('GHS effect diagnostics changed')
            return
        if strict:
            chain = [step['selected_parameters'] for step in report['steps'][:index+1]]
            previous_native = native_script(trace['selected_replay'], source, chain)
            if sha256_file(base.bound_file(session, trace['selected']['.fit'])) != trace['selected_replay']['output_sha256']:
                base.fail('Selected prefix differs from its native scientific output')
            if full:
                previous_native_full = native_script(trace['selected_full_replay'], full, chain)
        for role, origin, key in (('selected', source, 'prefix_audit'), ('selected-full', full, 'full_prefix_audit')):
            if origin is None:
                continue
            for binding in trace[role].values():
                base.bound_file(session, binding)
            output = base.bound_file(session, trace[role]['.fit'])
            rows = subject_rows if role == 'selected' else [c for c in constraints if next(
                r['role'] for r in regions if r['id'] == c['region']) == 'stars']
            actual = audit(base.pixels(origin), base.pixels(output), regions, rows, report['request']['targets'],
                           stellar_only=role != 'selected', noise_diagnostic=noise_diagnostic and role == 'selected', highlight_budget=highlight_budget)
            if actual != trace[key] or not actual['eligible'] and (role == 'selected' or not full_diagnostic):
                base.fail('Selected prefix protection changed')
            if role == 'selected':
                if effects and step['method'] == 'ght':
                    origin = bindings.get('derived_source',report['request']['source']) if index == 0 else report['steps'][index-1]['selected']['.fit']
                    expected = ghs_effects(before,actual['statistics'],regions,report['request']['targets'],origin,trace['selected']['.fit'])
                    if trace.get('effect_diagnostics') != expected:
                        base.fail('GHS effect diagnostics changed')
                previous = output
            else:
                previous_full = output


def measure_output(session, source, primary, report):
    payload = load_json(session/'session.json')
    regions = load_json(base.bound_file(session, report['request']['regions']))['regions']
    rows = subject_constraints(report['request']['operators'][-1]['constraints'], regions,
        full_diagnostic=bool(report['bindings'].get('full_source') or report['bindings'].get('derived_source'))
            and full_is_diagnostic(payload))
    actual = audit(base.pixels(source), base.pixels(primary), regions,
                   rows, report['request']['targets'], noise_diagnostic=noise_is_diagnostic(payload), highlight_budget=base.optimization_enabled(payload))
    expected = base.pixels(base.bound_file(session, report['steps'][-1]['selected']['.fit']))
    data = base.pixels(primary)
    if not actual['eligible'] or data.shape != expected.shape or np.max(np.abs(data-expected)) > 1e-7:
        base.fail('Formal protected stretch differs from the selected native prefix')
    from deep_sky_siril_artifacts import read_fits_header
    before, after = read_fits_header(source), read_fits_header(primary)
    from deep_sky_siril_processing import complete_wcs, wcs_matrix
    if complete_wcs(source):
        if not complete_wcs(primary):
            base.fail('Protected stretch lost the source WCS')
        keys = ['CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2']
        keys += [key for key in before if key.startswith(('A_', 'B_', 'AP_', 'BP_')) or key == 'EQUINOX']
        same_values = all(key in after and math.isclose(base.number(before[key]), base.number(after[key]),
                         rel_tol=1e-12, abs_tol=1e-12) for key in keys)
        same_matrix = all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15)
                          for a, b in zip(wcs_matrix(before), wcs_matrix(after)))
        same_axes = all(str(before.get(key, 'deg')) == str(after.get(key, 'deg')) for key in
                        ('CTYPE1', 'CTYPE2', 'CUNIT1', 'CUNIT2'))
        if not (same_values and same_matrix and same_axes):
            base.fail('Protected stretch changed the source WCS')
    result = {'status': 'passed', 'policy': POLICY, 'audit': actual, 'requires_visual_review': True}
    if report.get('generation'):
        result['generation'] = report['generation']
    return result


def review_paths(session, binding):
    report = load_json(base.bound_file(session, binding['parameters']))
    if report.get('schema') not in {REPORT, DERIVED, 'starun-siril.stretch-parameters.v3'}:
        return {}
    paths = {binding['parameters']['path']: base.bound_file(session, binding['parameters'])}
    trace = report['steps'][-1]
    for item in [trace['local_panel'], trace['selected']['.jpg'],
                 *([trace['selected-full']['.jpg']] if 'selected-full' in trace else [])]:
        paths[item['path']] = base.bound_file(session, item)
    for step in report['steps']:
        for trial in step['trials']:
            for role, item in trial.get('retained', {}).items():
                if role.endswith('-highlight.png'):
                    paths[item['path']] = base.bound_file(session, item)
    if 'comparison' in binding:
        from deep_sky_siril_stretch_profiles import review_paths as comparison_paths
        paths.update(comparison_paths(session, binding['comparison'], binding['parameters']))
    for comparison in binding.get('comparisons', []):
        from deep_sky_siril_stretch_profiles import review_paths as comparison_paths
        paths.update(comparison_paths(session, comparison, binding['parameters']))
    return paths


def protection_binding(session, source, text, *, verify_parent=True):
    rows = [line.split(':', 1)[1].strip() for line in text.splitlines()
            if line.strip().startswith('# stretch-protection:')]
    if not rows:
        return None
    if len(rows) != 1:
        base.fail('Duplicate final-stretch protection declarations')
    declaration = base.strict_json_bytes(rows[0].encode(), document='stretch protection declaration')
    path = base.bound_file(session, declaration)
    request = base.strict_json_bytes(path.read_bytes(), document='final stretch protection')
    required = {'schema', 'source', 'full_source', 'regions', 'profile', 'targets', 'core_absence', 'constraints'}
    if not isinstance(request, dict) or set(request) != required or request['schema'] != 'starun-siril.stretch-protection.v2':
        base.fail('Invalid final-stretch protection request')
    payload = load_json(session/'session.json')
    if base.source_file(session, payload, request['source']) != source:
        base.fail('Final-stretch protection belongs to another scientific source')
    full = base.bound_file(session, request['full_source'], ('artifacts',)) if request['full_source'] else None
    if request['full_source'] is not None and not isinstance(request['full_source'], dict):
        base.fail('full_source must be a real binding or null')
    if verify_parent:
        from deep_sky_siril_session import effective_session_payload
        if base.accepted_parent(session, effective_session_payload(session, payload), source) != full:
            base.fail('Final-stretch full source differs from accepted separation')
    region_path = base.bound_file(session, request['regions'])
    from deep_sky_siril_metrics import measure_regions
    if measure_regions(source, None, region_path)['status'] != 'measured':
        base.fail('Final-stretch regions are not geometry-bound scientific measurements')
    regions = load_json(region_path)['regions']
    roles = {r['id']: r['role'] for r in regions}
    constraints = request['constraints']
    if not isinstance(constraints, list) or not 1 <= len(constraints) <= 64:
        base.fail('Final-stretch protection needs explicit regional constraints')
    for row in constraints:
        if (not isinstance(row, dict) or set(row) != {'region', 'statistic', 'min', 'max'}
            or not isinstance(row['region'], str) or not isinstance(row['statistic'], str)
            or row['region'] not in roles or row['statistic'] not in base.STATISTICS | EXTRA_STATISTICS
            or base.number(row['min']) > base.number(row['max'])
            or row['statistic'] == 'hf_sigma2' and roles[row['region']] != 'background'):
            base.fail('Invalid final-stretch regional constraint')
    full_diagnostic = bool(full) and full_is_diagnostic(payload)
    validate(request, regions, full=bool(full), full_diagnostic=full_diagnostic)
    statistics(base.pixels(source), regions)
    return {'request': base.record(session, path), 'regions': request['regions'],
            'full_source': base.record(session, full) if full else None,
            'implementation': implementation(session), 'profiles': profiles_fingerprint(session)}


def measure_protection(session, source, primary, binding, full_control=None):
    payload = load_json(session/'session.json')
    if not implementation_matches(session, binding['implementation']) or binding['profiles'] != profiles_fingerprint(session):
        base.fail('Final-stretch protection implementation changed')
    request = load_json(base.bound_file(session, binding['request']))
    regions = load_json(base.bound_file(session, binding['regions']))['regions']
    full_diagnostic = bool(binding['full_source']) and full_is_diagnostic(payload)
    rows = subject_constraints(request['constraints'], regions, full_diagnostic=full_diagnostic)
    result = audit(base.pixels(source), base.pixels(primary), regions, rows, request['targets'],
                   noise_diagnostic=noise_is_diagnostic(payload), highlight_budget=base.optimization_enabled(payload))
    if not result['eligible']:
        base.fail('Final HDR blend fails protected regional or endpoint constraints')
    full = None
    if binding['full_source']:
        if full_control is None:
            base.fail('Protected HDR needs the same-mask full-source replay')
        origin = base.bound_file(session, binding['full_source'], ('artifacts',))
        rows = [c for c in request['constraints'] if next(r['role'] for r in regions if r['id'] == c['region']) == 'stars']
        full = audit(base.pixels(origin), base.pixels(full_control), regions, rows, request['targets'], stellar_only=True, highlight_budget=base.optimization_enabled(payload))
        if not full_diagnostic and not full['eligible']:
            base.fail('HDR full-source replay fails stellar protection')
    result = {'candidate': result, 'full_replay': full, 'requires_visual_review': True}
    if full_diagnostic:
        result['full_replay_role'] = 'diagnostic'
    return result

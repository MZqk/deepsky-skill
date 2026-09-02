# StarNet 2.5 migration design

Status: design only; not part of the standalone v1 runtime or release ZIP.

## 1. Boundary and target

Standalone v1 remains bound to Siril `>=1.4.4,<1.5` and the native `starnet`
command. This document does not authorize changing v1 sessions, command policy,
probe output, SSF validation, or Stage 6 behavior.

The target is a future standalone v2 for Siril `>=1.6,<1.7`. Siril's pinned
1.4.4 manual states that the native C interface is deprecated, cannot support
StarNet 2.5, and is removed in Siril 1.6. The selected replacement is
`pyscript StarNet.py`, based on the official
[Siril scripts repository](https://gitlab.com/free-astro/siril-scripts/-/tree/main/StarNetAstro).

If the requirements below are not met, v2 disables `stars.separate`. It must not
silently call the legacy command, an unpinned script, or a different star-removal
backend.

## 2. Source and distribution decision

The v2 implementation will use an audited, pinned StarNet Python adapter derived
from the official `StarNetAstro/StarNet.py`, not a script dynamically selected
from Siril's user script directories.

Before implementation, freeze all of the following in a source lock:

- upstream repository URL and exact commit;
- upstream relative path, byte SHA-256, declared version, and SPDX identifier;
- local patch series and its SHA-256;
- resulting adapter byte SHA-256;
- required Siril Python packages and tested versions;
- supported StarNet CLI and model versions.

The local patch is limited to deterministic integration: remove update checks,
downloads and package installation, expose stable non-interactive errors, and
retain the upstream pixel algorithm. It must not add enhancement choices,
parameter inference, SSF generation, or a processing Recipe.

The adapter is a separately licensed component. Its upstream and modified
source, GPL notice, copyright notices, source lock, and patch provenance must be
distributed together in v2. The proprietary StarNet 2.5 executable and model
remain external and are never included in the Skill. v2 remains
`publishable=false` until human legal review confirms the exact component scope
and the target platform's mixed/path-scoped license support.

## 3. v2 runtime contract

The public commands remain `probe`, `init`, `run`, and `finalize`; the migration
does not add an installer command.

`probe` discovers without writing or networking and reports independent records
for:

- the pinned bundled adapter and its source-lock identity;
- the user-installed StarNet 2.5 CLI executable;
- the matching model or weights;
- the Siril Python runtime and every required import.

Each file record contains an absolute canonical path, size, SHA-256, and the
available version evidence. Compatibility is true only when every component is
present, version-compatible, fingerprinted, and importable without installation.
Missing dependencies return a specific unavailable reason and preserve the
with-stars parent.

`init` freezes these records in the v2 session. `run`, replay, and `finalize`
recheck the complete set; any path, byte, version, source-lock, or dependency
drift fails closed.

The future v2 command policy for `stars.separate` will:

- add `pyscript` and bind it only to the pinned StarNet adapter;
- remove authorization for the native `starnet` command and `core.starnet_*`
  settings;
- permit only single-image linear processing;
- reject sequence mode, upsampling, GUI mode, alternate scripts, and unbounded
  stride values;
- require explicit `--exe` and `--weights` values equal to the frozen probe;
- default to stride `256` and request the `subtract` mask so the star layer is
  the additive `source - starless` layer used by Stage 9.

The intended non-interactive invocation is equivalent to:

```text
pyscript /pinned/StarNet.py --exe /frozen/starnet2 --weights /frozen/model --linear --stride 256 --masks subtract
```

The exact argv is validated against the pinned upstream revision before v2 is
implemented. The Agent still authors the stage SSF and same-stem provenance;
Python does not choose applicability, stride, stages, or downstream treatment.

## 4. Output normalization and evidence

Stage 6 first freezes a known-basename linear parent. The adapter's known
`starless_` and `subtract_mask_` files are temporary outputs only. The SSF loads
them and saves them into explicit session outputs for the starless image and
additive star layer. The executor rejects any other new filesystem entry.

Both normalized outputs must:

- remain within the session and match declared expected outputs;
- be newly created without overwriting input or existing artifacts;
- reopen in a fresh Siril process;
- match the source geometry and channel count;
- contain only finite statistics;
- retain complete source, script, executable, model, adapter, and parameter
  fingerprints in the run receipt.

Temporary upstream-named files are removed only after both normalized outputs
and the receipt are durable. Failed or interrupted runs retain the scene for
review and cannot become a parent source.

## 5. Offline and side-effect gates

The migration is blocked until the audited adapter proves all of the following:

- no URL access, update feed, telemetry, subprocess installer, `pip`, or package
  resolver path remains reachable;
- all Python dependencies are already installed in Siril's Python runtime;
- a clean temporary home/cache/config environment produces no writes outside
  the explicit session;
- repeated runs with identical frozen inputs create the same declared file set;
- missing CLI, model, dependency, or incompatible version fails before pixel
  processing begins.

Required failure classes are
`starnet25_script_unapproved`, `starnet25_dependency_unavailable`,
`starnet25_tool_incompatible`, `starnet25_runtime_drift`,
`starnet25_unexpected_write`, and `starnet25_output_contract_failed`.

If star separation was optional, failure preserves the original with-stars
parent and records a limitation. If the user explicitly required starless or
separate star control, the result is at most `partial_success`; an unsafe or
unverifiable output is `review_required` and is never delivered as formal.

## 6. Implementation and acceptance gates

Future implementation proceeds as a standalone v2 change in this order:

1. Pin upstream source, patch set, licenses, dependencies, and external tool
   compatibility; complete legal review of the planned distribution shape.
2. Add v2 probe/session fields and policy/validator support without reading v1
   sessions.
3. Add negative tests for network/install paths, unexpected writes, alternate
   scripts, unpinned hashes, drift, implicit output escape, and dependency
   absence.
4. Add small linear FITS tests for starless/star-layer geometry, finite values,
   fresh-process reopen, and additive reconstruction.
5. Run representative dense-star, nebula, galaxy, and bright-core images through
   Stage 6 visual review, checking target leakage, dark holes, halos, residuals,
   grid artifacts, and reconstruction error.
6. Enable v2 `stars.separate` only after every technical, visual, provenance,
   offline, and legal gate passes. Otherwise ship v2 with the protocol disabled.

No part of this design grants permission to download StarNet, alter the current
v1 release whitelist, tag, upload, or publish a Skill.

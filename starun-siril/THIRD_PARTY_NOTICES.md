# Third-party notices

## Siril 1.4.4 documentation

`references/siril-manual/` is an isolated, version-locked documentation
component derived from the Siril documentation project:

- Project: `free-astro/siril-doc`
- Source: <https://gitlab.com/free-astro/siril-doc>
- Documentation version: `1.4.4`
- Source commit: `1550a31d325276124fe961368477c90d49df804b`
- Concluded component license: `NOASSERTION` pending human review
- Siril documentation license file:
  `references/siril-manual/LICENSE.GPL-3.0.txt` (`GPL-3.0-only`)
- MuniPack-derived photometry excerpt: Siril names GNU FDL but does not state
  its version. The component records
  `LicenseRef-MuniPack-GNU-FDL-version-unspecified` and includes GNU's exact
  GFDL 1.2 text as the historically supported, inferred candidate
  `GFDL-1.2-no-invariants-only` in
  `references/siril-manual/LICENSE.GFDL-1.2.txt`.
- Component notices: `references/siril-manual/NOTICE.md` and
  `references/siril-manual/MODIFICATIONS.md`

The proprietary license in the top-level `LICENSE.md` does not apply to this
component. The upstream RST sources and selected referenced images are kept
separate from the generated catalogs and Chinese-English search aliases. The
component manifest records the exact source, selection, derivation, and hashes.

This notice records provenance and packaging boundaries; it is not a legal
opinion. The 0.1.0 release tool contains a source-level closed gate, so an
unsigned external JSON cannot make the aggregate package publishable. Public
distribution remains blocked pending human scope/version review and independent
confirmation that the target platform supports mixed, path-scoped licensing,
plus adoption of a verifiable trusted human-authorization mechanism.

## StarNet Python bridge

`scripts/siril_starnet.py` is derived from free-astro/siril-scripts,
`StarNetAstro/StarNet.py`, commit `f6cc0feae8f0f686f1b83f18758eae1ca27392a7`.
Copyright 2012-2026 team free-astro, 2026 StarNetAstro contributors, and 2026
MZqk for integration modifications. License: GPL-3.0-or-later; complete text
is in `references/LICENSE.StarNet-GPL-3.0.txt`. The proprietary top-level license
does not apply to this adapter. `references/starnet-source.json` records original
and modified hashes, preserved functions, and changes. CLI/model binaries are
external and are not distributed in this package.

`scripts/siril_starnet_linear.py` is also GPL-3.0-or-later and reuses the
frozen legacy validator and runtime probe. `references/starnet-linear-source.json`
binds both adapter hashes and documents the native StarNet 2.6.2 FITS handoff.
Native CLI/model binaries remain external and are not redistributed.

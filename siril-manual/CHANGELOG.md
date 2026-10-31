# Changelog

## [0.1.0] - 2026-10-30

- Extract the pinned Siril `1.4.4` (`1550a31d325276124fe961368477c90d49df804b`) offline documentation corpus and read-only query engine from `starun-siril` into a standalone companion skill.
- Relocate the static documentation corpus under `assets/siril-manual/` in accordance with Agent Skills directory conventions and strip 24 non-CLI `.png` images (~10.0 MiB reduction).
- Support standalone offline verification (`--verify-bundle`), bilingual `zh-CN`/`en` search, exact `--command` lookup, and `include` / `csv-table` resolved `--read` queries with zero runtime dependencies outside the Python standard library.

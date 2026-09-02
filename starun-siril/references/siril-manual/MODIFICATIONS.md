# Modifications and selection statement

- Every bundled `source/doc/**/*.rst` file is copied byte-for-byte from the
  pinned upstream commit.  Safe in-document `include`, `literalinclude`,
  `download`, and `csv-table :file:` dependencies that resolve within `doc/`
  are also copied byte-for-byte.
- The Siril documentation project's upstream license is copied byte-for-byte
  and renamed `LICENSE.GPL-3.0.txt`.
- `LICENSE.GFDL-1.2.txt` is copied byte-for-byte from GNU's fixed GFDL 1.2
  license URL as the inferred candidate text for the MuniPack-derived excerpt in
  `source/doc/photometry/general.rst`.  Siril's RST does not state the GFDL
  version, so metadata remains `NOASSERTION` and public release remains blocked
  pending human legal review.
- Only the PNG files listed by `image-selection.json` are bundled.  The list is
  curated for command-line deep-sky processing; unselected images, generated
  HTML, themes, videos, animations, and external resources are omitted.
- `catalog.json`, `commands.json`, `sections.jsonl`, `aliases.zh-en.json`,
  `image-selection.json`, `files.json`, `manifest.json`, this file, and
  `NOTICE.md` are generated or authored by the `deep-sky-siril` maintainers.
- The Chinese aliases are retrieval aids, not translations of the official
  manual and not additional Siril execution authorization.

The upstream RST is authoritative for documented Siril behavior.  The Skill's
separate command policy remains authoritative for commands it may execute.

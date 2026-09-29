# astrometry.solve

## Applicability and evidence

Stage 4, linear FITS before color calibration. Use only native Siril local Gaia. A complete finite existing WCS is preserved with a load/save-only script and receipt status `preserved_existing_solution`. Without WCS and `DEEP_SKY_SIRIL_GAIA_ASTRO_FILE`, skip explicitly; no successful solve is claimed. The astrometric catalogue file is distinct from the photometric catalogue directory. No dependencies or catalogues are downloaded.

When an unsolved master was stacked from subframes that possess valid WCS or coordinates, use `sync_fits_header.py` to synchronize headers from the source subframe/reference before running Stage 4 (see [astrometry-source-preservation](../astrometry-source-preservation.md)), enabling the `preserved_existing_solution` path without downloading external catalogues.

Coordinates and sampling must be finite file metadata (`RA`, `DEC`, `FOCALLEN`, `XPIXSZ`). User overrides require `# astrometry-evidence: {"source":"user","evidence":"exact user evidence","ra":98.3,"dec":4.7,"focal":160,"pixelsize":2.9}` and matching `platesolve -catalog=localgaia -noflip -nocrop 98.3,4.7 -focal=160 -pixelsize=2.9`. Values and the evidence must also be justified in provenance; do not infer them from filenames.

## SSF knowledge and skeleton

This is the primary protocol reference; consult the frozen `platesolve/set` manual for variations and retain lookup evidence. Bind the exact probed catalogue path.

```ssf
requires 1.4.4 1.5.0
set32bits
set core.catalogue_gaia_astro=/abs/gaia_astrometric.dat
load "/abs/current-parent.fit"
platesolve -catalog=localgaia -noflip -nocrop
save "/abs/session/artifacts/040-solved" -chksum
stat main
savejpg "/abs/session/previews/040-solved" 95
close
```

For an existing solution, omit `set` and `platesolve`. Declare the scientific output as primary. Success requires complete finite WCS, unchanged dimensions, exact pixel orientation and values; preserved WCS coefficients must be unchanged. Actual newly solved local-catalogue acceptance remains pending installed catalogue validation. Review output against the source; astrometry alone does not establish truthful channel roles or permit calibration of unknown channels.

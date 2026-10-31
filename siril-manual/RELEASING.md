# Releasing siril-manual

## Local verification

Run repository governance, bundle verification, and the isolated test suite from the monorepo root before tagging:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate_repository.py --skill siril-manual
PYTHONDONTWRITEBYTECODE=1 python3 -B siril-manual/scripts/build_siril_manual.py --check
PYTHONDONTWRITEBYTECODE=1 python3 -B siril-manual/scripts/query_siril_manual.py --verify-bundle
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -p no:cacheprovider siril-manual/tests
```

## Release boundary and tag convention

`assets/siril-manual/` carries upstream Siril `1.4.4` documentation (`GPL-3.0-only` plus an unversioned GNU FDL MuniPack excerpt), so its concluded license remains `NOASSERTION` pending human legal review. Do not publish aggregate packages to external registries until mixed/path-scoped licensing review is completed.

When authorized, use namespaced tags of the form `siril-manual/vX.Y.Z`.

# structure.local-contrast

Stage 8, only a reviewed nonlinear starless primary descendant of an accepted separation. Skip if no trusted starless branch exists. Choose four ordered mask boundaries from the actual current luminance: `0 <= low_start < low_end <= high_start < high_end <= 1`. Record them in the SSF comment and provenance. First candidate uses cliplimit 1.5, grid 8, blend 0.20 and Gaussian sigma 2.

## SSF knowledge and skeleton

This primary reference defines the validated algebra. Consult frozen `split/pm/clahe/gauss/rgbcomp` syntax before constructing a variation. Declare every generated FITS via `--expect`, and the RGB composite via `--primary-output`. Python verifies this literal operation sequence and never generates SSF or alters pixels. Boundary examples below are illustrative; replace them together with mask denominators based on actual evidence. Decimal formatting may vary, but operation order and formulas must match.

```ssf
requires 1.4.4 1.5.0
set32bits
# local-contrast: {"low_start": 0.03, "low_end": 0.08, "high_start": 0.65, "high_end": 0.90}
load "/abs/current-starless.fit"
split "/abs/session/artifacts/080-r" "/abs/session/artifacts/080-g" "/abs/session/artifacts/080-b"
pm "0.2126 * $artifacts/080-r$ + 0.7152 * $artifacts/080-g$ + 0.0722 * $artifacts/080-b$"
save "/abs/session/artifacts/080-l" -chksum
clahe 1.5 8
save "/abs/session/artifacts/080-l-enhanced" -chksum
pm "(min(1, max(0, ($artifacts/080-l$ - 0.03) / 0.05))^2 * (3 - 2 * min(1, max(0, ($artifacts/080-l$ - 0.03) / 0.05)))) * (1 - min(1, max(0, ($artifacts/080-l$ - 0.65) / 0.25))^2 * (3 - 2 * min(1, max(0, ($artifacts/080-l$ - 0.65) / 0.25))))"
gauss 2
save "/abs/session/artifacts/080-mask" -chksum
pm "1 + 0.20 * $artifacts/080-mask$ * ($artifacts/080-l-enhanced$ - $artifacts/080-l$) / max($artifacts/080-l$, 0.000001)"
save "/abs/session/artifacts/080-gain" -chksum
pm "$artifacts/080-r$ * $artifacts/080-gain$"
save "/abs/session/artifacts/080-r-enhanced" -chksum
pm "$artifacts/080-g$ * $artifacts/080-gain$"
save "/abs/session/artifacts/080-g-enhanced" -chksum
pm "$artifacts/080-b$ * $artifacts/080-gain$"
save "/abs/session/artifacts/080-b-enhanced" -chksum
rgbcomp "/abs/session/artifacts/080-r-enhanced.fit" "/abs/session/artifacts/080-g-enhanced.fit" "/abs/session/artifacts/080-b-enhanced.fit" -out=/abs/session/artifacts/080-local-contrast
load "/abs/session/artifacts/080-local-contrast.fit"
stat main
savejpg "/abs/session/previews/080-local-contrast" 95
close
```

Only luminance receives CLAHE. The product of smoothstep dark fade-in and highlight fade-out is blurred, then the same gain applies to all three channels. No independent RGB equalization, clipping, or HDR operation. Inspect noise amplification, fine filaments, broad halos, bright cores and color; reject when no clear improvement exists. Keep all diagnostic layers and the accepted starless parent.

Siril `rgbcomp -out` writes the RGB composite without loading it. Reload the primary before statistics or previews so review materials represent the scientific candidate.

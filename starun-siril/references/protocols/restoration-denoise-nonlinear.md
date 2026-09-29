# restoration.denoise-nonlinear

Stage 10 before final color adjustments. Requires a reviewed nonlinear scientific primary parent. Each candidate is independent; do not reuse linear-only denoise on nonlinear sources.

## SSF knowledge and skeleton

This is the primary reference. The frozen `denoise` manual defines native NL-Bayes modulation; Python only executes and verifies.

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-nonlinear-parent.fit"
denoise -nocosmetic -mod=0.20
stat main
save "/abs/session/artifacts/100-denoise-nonlinear" -chksum
savejpg "/abs/session/previews/100-denoise-nonlinear" 95
close
```

Allow only `0 < mod <= 0.35`; no SOS, DA3D, VST or cosmetic correction. Inspect background, filaments, cores, star profiles and color continuity. Reject without clear visible improvement; retain the accepted parent. Metrics cannot accept visual gates.

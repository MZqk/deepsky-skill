# stars.recompose

Stage 9. Requires accepted separation, explicit stretch and current nonlinear starless reviews. Pass `--separation-run` and `--stretch-run`; every source must share lineage and geometry. Automatic stretch without a complete replayable chain cannot be recomposed.

## Frozen transfer and SSF knowledge

This primary reference defines display-domain subtraction. Replay the ordered explicit MTF/Asinh/GHS chain from the stretch receipt on **both** separation full source I and original starless A. Build `S_d = F(I) - F(A)`; compose `A_prime + strength * S_d`, default strength 1.0, allowed 0.70–1.00. Never stretch the linear residual directly. No `min`, clipping or rescaling may conceal negative residuals.

Declare all four scientific outputs; primary is the last candidate. Paths for `load` and `save` are absolute. PixelMath paths are session-relative without extensions. The example uses a single MTF; substitute exactly the recorded ordered chain on both loads. Consult frozen command manuals for unexpanded chains and retain evidence.

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/separation-full-source.fit"
mtf 0 0.01 1
save "/abs/session/artifacts/090-full-baseline" -chksum
close
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.01 1
save "/abs/session/artifacts/090-original-starless" -chksum
close
pm "$artifacts/090-full-baseline$ - $artifacts/090-original-starless$"
save "/abs/session/artifacts/090-display-stars" -chksum
close
pm "$artifacts/080-local-contrast$ + 1.0 * $artifacts/090-display-stars$"
save "/abs/session/artifacts/090-recomposed" -chksum
stat main
savejpg "/abs/session/previews/090-recomposed" 95
close
```

## Review and fallback

The unenhanced baseline requires per-channel `max_abs(F(A)+S_d-F(I)) <= 1e-5`. Nonfinite pixels, geometry changes or residuals below -1e-5 reject. Closure proves arithmetic only: inspect target leakage in the star layer independently, plus star diameters, color, halos and repeated structures. Failure retains the trustworthy full-stars parent/baseline; no starless final delivery unless explicitly requested.

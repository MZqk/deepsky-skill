# Changelog

## Unreleased — Per-region denoise review materials

- Write one lossless PNG per region (parent, candidate and signed residual at both a common and a per-region scale), magnify small ROIs by an integer nearest-neighbour factor up to 8x, and add native 1:1 512 px slices for regions above 1024 px; keep the existing panel bytes for historical replay.
- Add a full-frame locator, a per-sample highlight index with bounding box, coordinates, parent/candidate values and native star-core comparisons, and a hash manifest for every material.
- Bind the manifest, locator and each region view, slice, residual scale and highlight record into the denoise assessment skeleton; review of prior diagnostics without these materials stays byte-identical and writes nothing.

## Unreleased — Morphology multistage body routes

- Require at least two executed Stage 7 body steps and default ordinary RGB/mono/HDR to prefix feedback; retain explicit batch and the dedicated HOO workflow.
- Retire new standalone baseline/compare routes and one-step completion, preserving internal HDR/HOO transfers and independent Stage 9 Fs.
- Freeze complete outgoing evidence for immutable historical replay/finalization. Keep version 0.3.3 and existing science/review budgets.

## Unreleased — Stage 7/8 display-scale review

- Add a display-only generator that builds a fixed 1024 px long-edge parent/candidate panel from already-rendered JPEG/PNG previews; it rejects scientific formats, never upscales and writes an immutable PNG plus receipt.
- Require a display-scale observation in Stage 7 and Stage 8 review: whether the subject still reads at viewing size and whether the frame collapses into color blotches or flat gray; no threshold, no ROI coordinates, no replacement for 1:1 crops or same-scale panels.
- Merge the display-scale row into the existing `080-comparison-1to1.png` for the 32-material CLAHE comparison flow, retain the panel through finalize pruning, and register the outgoing guidance hashes for historical replay without a policy or implementation change.

## Unreleased — Stage 7 curve amplification diagnostics

- Add a diagnostic-only `curve_amplification` block to every preflight `curve_effects` row: analytic MTF slope at each ROI's per-channel input median plus predicted or measured p99-p01 span gain, with subject/background aggregates and a subject-over-background ratio.
- Keep the block outside every gate: no threshold, no warning, no stopping or adoption change and no attempt-count change; clamped inputs, zero span baselines and unmeasurable denominators are recorded as null with a reason.
- Freeze the outgoing `curve_effect_review_v2` policy, documents and implementation in `references/stretch-curve-amplification-history.json` for immutable historical replay and finalization without migration.

## Unreleased — Stage 7 artifact focus review

- Bind independent review-only focus areas to Stage 7 strategies; allow locked, append-only additions without changing scientific ROIs, transforms or trial accounting.
- Build context and native-resolution comparison panels from existing previews, and require complete Agent-authored artifact observations in prefix feedback and formal review. Preserve existing quality gates and the 32-material limit.
- Retain artifact evidence through pruning and freeze the complete outgoing policy and fingerprints for historical replay/finalization without migration.

## Unreleased — Optional Stage 7 prefix feedback

- Add explicit stepwise initialization, one-step native replay, prefix-bound Agent feedback and completion for RGB/mono and both HDR branches; retain batch defaults and HOO.
- Preserve shared candidate accounting, exclusive successors, frozen current-domain anchors, HDR masks, same-prefix tail comparisons and formal review. Recovery and archive verification remain read-only.
- Freeze the complete outgoing policy and evidence fingerprints for historical replay and finalization without migration.

## Unreleased — Morphology-selected initial HDR

- Allow the Agent to select masked dual HDR in the first Stage 7 proposal for all five morphology profiles, without demonstrating a single-chain conflict.
- Preserve explicit native branches, shared-mask replay, final ROI protection, visual review and the shared three-attempt ledger; bind HDR revisions to their actual native receipts.
- Retain the outgoing policy, documents and implementation fingerprints for immutable historical replay and finalization.

## Unreleased — Stage 4 photometric fit diagnostics

- Parse Siril's photometric fit lines, white-balance gains and star counts from the bound run log into `processing_checks.color_calibration.fit_quality` with `acceptable` / `degraded` / `degenerate` verdicts; logs without fit evidence gain no field.
- Block acceptance of a `degenerate` photometric solution and keep the uncalibrated parent; the imprecise-solution warning stays non-fatal and is mirrored into the color calibration receipt block.
- Require `protection_regions` for PCC/SPCC declarations (neutral may still use `background_regions`) and validate the optional `route_history` ladder record; replay keeps the historical permissive fallback.
- Document the route ladder, the fact that no pre-run catalogue precheck is possible under the Stage 4 command policy, and the new acceptance rule. Old receipts and frozen sessions replay unchanged.

## Unreleased — Stage 7 adaptive display budget

- Permit up to 0.01% newly crossed highlights per channel, retain location overlays and require visual review; preserve historical zero-crossing behavior.
- Reserve shadow headroom and offer read-only MTF inversion on the actual ROI pixels without consuming a scientific attempt.
- Keep the three-attempt ledger and freeze outgoing evidence, policy and implementation fingerprints.
- Allow evidence-bound factor revisions of an existing exception route without treating the unchanged route as a new exception; preserve the prior policy for replay.

## Unreleased — Curve effects and evidence-bound short routes

- Check MTF against actual input-channel pixels and declared brightness/span targets before freezing; keep unknown prefixes pending and float32-boundary cases subject to strict native verification.
- Record and reverify actual GHS prefix changes as diagnostics, without a new benefit gate or attempt.
- Allow evidence-bound initial one/two-step exception routes and subsequent shortening with verified GHS before/after evidence; retain scientific protection, required tail comparisons and immutable historical replay.

## Unreleased — Read-only recovery summaries

- Report the accepted parent, explicit next-stage intent or evaluation hint, blockers and remaining Stage 7 trial budget without writing session state or launching Siril.
- Bind concise continuation/stop records to existing reviews; propose reviewed same-lineage ROI rebinding and verify reusable exact-prefix evidence without reusing stale measurements.
- Prepare fallback routes only after their trigger and preserve historical policies, receipts, reviews and scientific execution restrictions.

## Unreleased — Verified negative tail controls

- Add tail comparison v2 for complete native final trials that miss display goals while preserving shared scientific protection; keep failed images diagnostic and formal selection solved-only.
- Support derived historical comparison recovery without Siril execution, attempt resets or rewriting scientific evidence; preserve v1 comparison replay and exact outgoing policy fingerprints.

## Unreleased — Stage 7 declared morphology

- Prepare native-parent ROI context independently of a profile; require complete primary/companion/background/stellar declarations and processing-goal/profile consistency before native trials.
- Bind and retain context evidence through request verification and finalization; disclose morphology revisions without changing recipes, scientific algorithms, endpoints or attempt budgets.
- Preserve outgoing policy, document/helper and implementation fingerprints; existing sessions and Stage 1 observations remain unchanged.

## Unreleased — Stage 7 whole-chain constraint preflight

- Add read-only preflight with aggregated step constraints and evidence-backed intermediate brightness lower bounds; freeze and verify the report before native execution without extra trials or parameter prediction.
- Clarify that GHS SP is not a brightness fixed point and HP begins a linear protection segment without requiring a value near 1.
- Preserve the outgoing policy and evidence fingerprints. Keep existing sessions, endpoints, attempt budgets, recipes and parameter algorithms unchanged.

## Unreleased — Retained Stage 7 trial artifacts

- Archive actual Stage 7 trial-prefix FITS/JPEG, full-stars controls and ROI panels under artifacts with native source/parameter/status bindings; include HOO/tail trials and native HDR outputs.
- Retain these archives through intermediate cleanup and audit them as evidence without promoting failed trials to scientific parents. Keep the original parameter reports and three-attempt budget.
- Freeze exact outgoing v29 policy/document/helper/implementation bindings. Apply only to new sessions; no existing-session backfill, new algorithm or publication.

## Unreleased — Evidence-bound Stage 7 morphology routes (v29)

- Enforce all five morphology chains through strategy.v2 / goals.v3 and explicit request.v4 factors; require measured failure or reviewed same-source protection conflict for custom exceptions and HDR. Keep HOO body chains bound and the original RGB star contract.
- Preserve immutable first classification, automatic revision/target diffs and one shared three-attempt ledger. Bind both morphology and tail comparisons with repeatable --comparison.
- Add ROI reference assessment v2 with actual controls/panels, identical-pixel contradiction checks and explicit partial tradeoffs; target-only corrections disclose no new image benefit.
- Freeze exact v28 policy, document/helper and implementation fingerprints; preserve historical replay/finalize and existing sessions.

## Unreleased — Stellar stretch selection and Modified Asinh (v28)

- Select the shortest independent stellar curve from the observed color, highlight or contrast problem; retain satisfactory MTF and the original baseline without mandatory finishing steps or automatic parameter selection.
- Authorize explicit human-luminance `modasinh` with finite D/LP/SP/HP and rgbblend only in Stage 9. Preserve paired linear-source replay, signed subtraction, final addition and existing scientific/color/geometry review.
- Freeze the exact outgoing policy, command bytes, documents and implementation for immutable historical replay/finalize. No schema/version change, dependency installation, existing-master rerun or publication.

## Unreleased — Stage 7 documentation layout

- Put the current starting chains, execution sequence and shared three-attempt budget first; keep current ROI protection and full diagnostics in the main body.
- Move historical policy notes, old rankings and explicit search examples into an on-demand appendix. Preserve every existing anchor and fenced example, plus exact outgoing bindings for committed replay/finalize.
- Keep algorithms, command arguments, schemas, recipes and execution policy unchanged; no existing-master rerun or release.

## Unreleased — Stage 7 route revisions and shared trial budget

- Distinguish factor revisions from evidence-based route changes: each submitted proposal remains immutable, while a new same-parent proposal may change operators, order or chain length.
- Share three scientific attempts across candidates, revisions and independent controls. Each MTF/GHS tail solve counts; comparing or reusing exact matching reports adds no trial, and missing mandatory controls require reserved budget.
- Clarify baseline/HDR/HOO accounting, failed attempts and recovery without adding a runtime counter or changing scientific validation. Preserve exact outgoing references for committed replay/finalize; no existing-session rewrite or release.

## Unreleased — GHS CLI D semantics

- Correct the CLI/internal strength distinction: Siril 1.4.4 uses k=expm1(d), with m=1/(exp(d)+1) only for the matching scalar B=1 configuration. Keep the existing D arguments unchanged.
- Remove unsupported linear-scale, native-precision and starting-range claims; document channel/landmark limits and the native rejection of D=0.
- Add real Siril gradient and RGB counterexamples, exact formula-correction coverage and immutable outgoing guidance replay. No algorithm, schema, policy, trial-budget or release change; no existing-master rerun.

## Unreleased — Stage 7 multi-step guidance

- Align the default Stage 7 workflow around one complete morphology-guided chain; prefer the existing Asinh/GHS recipe for observed bright-core/faint-envelope contrast and stop further revisions once the reviewed goals are met.
- Keep baseline as an optional first-MTF anchor seed, require representative faint anchors and regional visibility/color evidence, and document explicit single-step exceptions.
- Reject single-operator requests in the default documentation example before freezing or trials; preserve the existing CLI, algorithms, schemas, recipes and scientific gates.
- Register exact outgoing document fingerprints for immutable committed replay/finalize. No policy/version change, publication or existing-master rerun.

## Preservation workflow

- Added paired red/blue protection evidence and per-region review for background and calibration.
- Added explicit RGB tail comparison, min-preserving local masks and high-saturation protection.
- Added separately frozen star curves with original baseline/catalog comparison and core/halo color diagnostics.
- Registered the complete outgoing policy and fingerprints for immutable historical replay/finalize.

## Unreleased — DeepSNR-first Stage 5

- Prefer installed DeepSNR 1.3.2/model 2 for eligible Stage 5 linear denoise; require explicit, same-parent evidence before NL-Bayes fallback.
- Add v2 backend proposals, frozen runtime/model capabilities and an audited Siril float32 FITS bridge with native linear MTF, explicit modulation, signed diagnostics and reviewed adoption.
- Preserve HOO/OIII v1, Stage 10, scientific precision/WCS gates and complete pre-DeepSNR immutable replay. No dependency installation, version bump or publishing.

## Unreleased — Morphology-default Stage 7 stretch (v26)

- Make the same-parent morphology comparison the default Stage 7 entry: `init-request → solve → compare → generate --comparison`. The reference-driven `baseline` seed remains available as an explicit entry for recovering a safe MTF start, not as the default.
- Keep adoption benefit-first: a single-step control may be adopted when the higher-order chain shows no reviewable structural gain; chain length is still not quality evidence.
- Leave the shipped morphology recipes byte-identical: `emission_nebula` still starts with GHT because the historical fingerprint tables freeze `stretch-profiles.json`. GHS has no blackpoint term and cannot lift the linear sky on its own, so this recipe order remains a known limitation to fix in a version that can introduce a separate profile file; this change does not rewrite the frozen file.
- Give the full-stars route a default two-step `autostretch → GHT` chain, with the single-step autostretch retained as the same-parent control.
- Freeze `guidance=morphology_default_stretch_flow_v26`; v25 and earlier sessions remain replay/finalize-only with unchanged fingerprints. No new CLI, schema or scientific gate.

## Unreleased — test suite deduplication

- Extract the shared version-pinning skeleton, the replay/finalize-without-migration body and the FITS byte builders into tests-only helper modules (`tests/_policy_pins.py`, `tests/_fits_bytes.py`), and merge the mirrored network-policy tests into one parametrized case; tests are outside release-files.txt and never shipped.
- Test-refactor only: no runtime, policy, schema, CLI or fingerprint change; every version-pinning assertion is preserved verbatim in behavior and the full pytest pass remains the sole release gate.

## Unreleased — morphology reference chain wording

- Clarify in the target morphology reference that the five morphology cascades belong to the explicit morphology-comparison route, are optional controls and do not replace the reference-driven baseline stretch or actual regional evidence; link the Stage 7 chain table instead of leaving "five cascades" and "preferred trial candidates" undefined on the reference page.
- Change reference wording only: no new gate, schema, parameter, Stage order or execution-policy label; retain existing scientific gates, review contracts and immutable historical fingerprints.

## Unreleased — Protected Stage 8 finishing

- Add v3 source-exclusion masks without changing v1/v2 recipes or the weakest-ROI texture gate; expose complete texture failure diagnostics.
- Add explicit cyan saturation targets through the existing native D trials and a strict red luminance-MTF variant with common RGB gain, immutable evidence and scientific replay.
- Preserve exact outgoing helper, complete Stage 7 implementation and knowledge/command-policy bindings; retain Agent pixel review and existing star-layer contribution controls.

## Unreleased — Structured Stage 7 adoption evidence

- Make the existing reference baseline the Agent default, with explicit reasons for alternate routes; retain morphology generation and all processing entry points.
- Bind real prefix, morphology, gentle-HDR or monochrome HOO controls in the existing uniform review panel. Require per-ROI reference/detail conclusions and evidence-backed regression tradeoffs through optional review.v1 stretch_assessment on newly marked runs.
- Preserve Agent adoption decisions, scientific gates, numeric recommendation, request versions and policy labels; retain exact outgoing implementation, guidance and review-schema fingerprints for immutable historical replay/finalize.

## Unreleased — Reference-driven Stage 7 visibility

- Derive a safe MTF baseline seed from the accepted Stage 6 starless autostretch reference and a declared structure ROI; retain native trials, safe blackpoints, fixed whitepoint and explicit factors.
- Bind native sRGB reference/candidate views and fixed local panels to new starless stretch receipts and mandatory review; retain full replay, existing HDR/HOO sources and immutable historical bindings.
- Prioritize sufficient visibility, highlight detail and natural transitions over maximal separation or chain completion; use GHS/HDR only when actual structure needs them. Fix parameter loading to retain diagnostic sky-noise overruns under the existing noise policy.

## Unreleased — Stage 7 ROI feedback

- Turn existing guidance into an Agent workflow that separates protected and diagnostic ROIs, diagnoses blocking constraints before proposals, and adjusts current-domain landmarks and display goals without extending operators or chains.
- Reuse existing medians for directional local-pair feedback, with explicit luminance versus per-channel semantics, bounded revisions, compact evidence notes and actual structural/color/core review. Keep numeric ranking, schemas and execution policy unchanged.
- Retain multiple exact outgoing guidance fingerprints per path for committed receipt replay/finalize; preserve earlier palette references and reject stale references for new runs. No historical records are rewritten.

## Unreleased — Explicit palette routes

- Default new CLI and Python sessions to dualband off; retain explicit auto opt-in, source checks and frozen historical values without inheriting the switch in same-master sessions.
- Record evidence-backed natural RGB, real-line SHO/modified-SHO or HOO choices separately from display style; connect calibration, stretching, one-time mapping and stellar/final-color disclosure.
- Preserve existing mapping formulas, native HOO execution, actual pixel review, schemas and execution-policy labels; register exact outgoing knowledge for committed receipt replay/finalize, without authorizing old references for new runs. No dualband Hubble-style variant or external stellar input is added.

## Unreleased — Benefit-first Stage 7 stretch

- Prioritize visible subject structure with an initial Agent proposal and up to two evidence-based revisions; retain one native candidate per operator and immutable comparison rounds.
- Freeze `stretch_noise=diagnostic_only_v1`: record sky MAD/HF overruns without rejecting candidates, and rank protected candidates by the weakest directional subject-to-sky median contrast without noise normalization or gentle tie-breaking. Apply the same semantics to native trials, replay, formal outputs, HOO and HDR.
- Preserve endpoint, source, geometry, core and other regional protection, actual color/structure review, and exact outgoing policy/implementation/document bindings. Defer denoising; do not add Stage 8 processing or weaken its existing gates. Tests are not run for this change.

## Unreleased — Agent-proposed Stage 8 enhancement

- Add v2 requests with one bounded wavelet amplitude, an explicit fine-detail choice and CLAHE blend strength; compare one native candidate per method, retaining six planes and fixed coarse coefficients, cliplimit, grid and mask smoothing.
- Keep v1 trials and rejection rules; allow one evidence-bound fine-off revision at the original proposed amplitude. Preserve exact outgoing helper, complete Stage 7 implementation and document bindings without rewriting historical evidence or changing execution policy.

## Unreleased — Agent-selected saturation gain

- Add optional `--target-gain` and keyword-only `solve(target_gain=...)` for Agent-proposed relative saturation gains; zero skips native trials, and explicit targets enable the same bounded flow for non-vivid styles.
- Keep legacy omitted-target defaults, ROI-derived masks, discrete native D selection, endpoint checks, report schemas and execution policies. Document target reasoning and actual review across Stage 8/10; no continuous D or manual mask controls.

## Unreleased — Agent-selected Stage 9 strength

- Make Agent-guided recomposition pass the final `--strength` explicitly, informed by user preference, subject obstruction, halos and the final blend preview; retain the automatic calculator as a starting reference and the omitted-override default.
- Clarify that lower strength reduces the stellar layer contribution rather than setting stellar diameter. Preserve existing bounds, frozen transfer, review gates and immutable candidate assets; no runtime interface or version change.

## Unreleased — unbound profile disclosure

- Disclose that a v4 proposal without `strategy` is not bound to its declared morphology chain: generation now records `Declared <profile> profile is unbound` or `Explicit custom-profile request` in provenance instead of staying silent.
- State in Stage 7 that `profile` constrains operators only when the request carries `strategy`, and list the unbound route beside v22-compatible in delivery disclosure. No new acceptance gate, version bump or real-master rerun.

## Unreleased — Agent stretch factors v25

- Add explicit v4 Agent factor proposals with one candidate per operator, current-domain anchors, native prefix verification, and retained ineligible diagnostics without automatic retries.
- Make morphology request generation use v2 goals with explicit baseline/phase factors; retain bounded search through explicit old requests and init-request --search. Extend HOO to v3 proposals for all three chains.
- Bind factor/mode/anchor evidence through formal output and review, freeze outgoing v24 policy and implementation/document bytes for read-only history, and retain existing command authorization and RGB recomposition. No publication or real-master rerun.

## Unreleased — starless subject scope v24

- Separate trusted starless v2/v3 subject protection from full-replay stellar diagnostics; allow stronger ROI-driven GHS without pruning on stellar highlights or applying stellar budgets to starless pixels.
- Retain native full replays, finite/geometry/log/fingerprint checks, endpoints and required review materials; keep Stage 9 RGB residual/strength rules and actual composition review.
- Freeze exact v23 execution policy, helper bindings, command bytes and document fingerprints for read-only historical replay/finalize. No version publication or master rerun.

## Unreleased — morphology stretch v23

- Evaluate five morphology-specific Asinh/GHS/MTF cascades against same-parent single-chain controls, without a short-chain preference or fixed scientific parameters.
- Add explicit-goal request construction, verified native chain comparison and comparison-bound formal review evidence.
- Preserve v22 protection/history, signed RGB stellar replay and existing HDR/HOO behavior; no real-master rerun or publication.

## Unreleased — ROI stretch v22

- Add explicit ROI/sky texture protection and bounded near-best display-separation selection for v2 stretch requests.
- Preserve independent HOO budgets, same-mask protected HDR controls, signed RGB stellar replay and immutable v21 history.
- Retain native representative pixels and fixed-region review evidence; no version publication or real-master rerun.


Unreleased — multiscale structure flow v21: compare finite native B-spline wavelet and luminance CLAHE siblings; preserve coarse coefficients, protected common RGB gain and original stellar chains. Bind native layer reconstruction, trials, coarse drift and actual review; retain exact v20 authority and immutable replay. No version bump or release.

Unreleased — dualband HOO flow v20: default to assessing native signed Ha/OIII display estimates on trusted starless OSC branches; freeze independent subject curves and preserve original RGB stellar residuals. Bind native trials, palette, regional reviews and immutable v19 replay. No matrix unmixing, version bump or release.

Unreleased — vivid color flow v19: default new sessions to vivid; add source-bound masked native GHS saturation curves and bounded ROI parameter trials, retain actual visual review and complete v18 immutable replay. No version bump or release.

Unreleased — benefit-first flow v18: actively resolve Stage 4 calibration evidence and WCS prerequisites; test eligible nonidentity neutral candidates and allow tiny verified gains. Adopt technically valid Stage 5 candidates after review finds no material degradation, including near-unchanged results and minor local costs. Let finite above-one linear samples reach native StarNet unchanged with a clipping advisory, following the official 2.6.2 input behavior; preserve its warnings and all integrity/visual checks. Retain complete v17 guidance/policy history and existing CLI/receipt schemas. Version remains 0.3.3; no release.

Unreleased — prepared Stage 4 flow v17: settle the calibration route and accepted pre-color parent before authoring, freeze reviewed neutral sky/subject regions and matching display parameters, and derive paths and invocation arguments once. Record preflight and retry evidence through existing reports and review receipts. Preserve complete v16 policy/reference history; CLI, schemas, scientific operators, thresholds and canonical SSF blocks are unchanged. Version remains 0.3.3; no release.

Unreleased — assessed Stage 3 flow v16: require source-bound control proposals, local review and sampling assessment for every linear full-processing input, followed by a native candidate when eligible. Document reduced sample targets, RBF and degree 1–4 selection, bounded same-parent revisions and evidence-backed fallback. Preserve complete v15 policy/reference history; CLI, schemas, scientific operators, thresholds and canonical SSF blocks are unchanged. Version remains 0.3.3; no release.

Unreleased — serial Agent flow v15: retire the independent batch runner and its diagnostic-packet module; process multiple masters sequentially through the existing single-task Agent loop, with independent sessions and actual reviews. Preserve complete v14 policy/reference history for immutable replay/finalize; scientific operators, thresholds and native adapters are unchanged. Version remains 0.3.3; no release.

Unreleased — reviewed batch flow v14: replace implicit geometry/background actions with source-bound immutable decisions and diagnostic packets; remove manufactured wrapper reviews; add native StarNet range/size preflight with reviewed full-stars partial-success delivery; require current-parent stretch requests and explicit prepared HDR; support fresh session roots and preserve complete v13 historical verification. No version bump or release.

## 2026-10-14 — Assessed Stage 5 restoration

- Assess linear denoising and a conservative RL candidate by default; adoption remains an actual-review decision.
- Support scientifically bound FITS/XISF geometry and units, constrained float32 promotion, WCS preservation and matched previews.
- Require both batch restoration decisions before separation/stretch, with exclusive publication, source chaining and immutable resume.
- Freeze restoration v1 and retain complete native-neutral policy/command/reference history without changing PSF algorithms or old records.

## 2026-10-14 — Native dualband background neutralization

- Prefer evaluating unit-gain native neutral for confirmed linear dualband OSC, independently of optional user-selected PCC/SPCC.
- Support safe FITS/XISF scientific ROIs, constrained float32 identity promotion, unchanged formula tolerance and matching-transfer parent/candidate previews.
- Add explicit bound batch Stage 4 use-review/skip decisions before separation/stretch; retain exact outgoing policies, command bytes and historical evidence.
- Limit preservation claims to background-subtracted RGB signals during neutralization, with actual sky and visual review still required.

## 2026-10-13 — Protected manual background samples

- Freeze protected_regions_v1 sampling policy; reject complete manual/automatic sample footprints and sky controls intersecting declared foreground protection.
- Bind the sampling label through static validation, postprocessing and replay; retain complete outgoing DWARF policy/knowledge and historical confirmation/recovery/finalize rules.
- Add evidence-qualified NGC 6888 manual sampling guidance with a 1.5-times starting shell box, unchanged middle-sky coverage and no low-order or vignetting safety guarantee.

## 2026-10-13 — DWARF explicit filter provenance

- Recognize complete DWARF mini, II/2 and 3/III header models and sourced selected-filter mappings; retain unknown selections and generic II Duo-Band evidence.
- Keep identical parsers in both skills and freeze filter_metadata=dwarf_explicit_filters_v1 with exact outgoing policy/knowledge history.
- Preserve linear/channel-role and PCC/SPCC evidence gates and historical confirmation/recovery/review/replay/finalize verification.

## 2026-10-13 — Fixed recomposition review materials and PCC guidance

- Freeze fixed_materials_v1 review policy; bind the same Stage 9 roles on warning and clean runs, including validated original or recovered metrics.
- Retain complete v10 policy/reference history, old review/finalize rules, and read-only confirmation/recovery verification.
- Qualify large-galaxy PCC guidance using the task 2 log and official deviation definition, without changing calibration gates or warning severity.

## 2026-10-13 — Native Stage 4 XISF scientific inputs

- Read Image 0 FITSKeywords for native astrometry/PCC/SPCC input metadata and freeze sourced filter/channel evidence.
- Verify scientific units, bottom-origin pixels, geometry and WCS against FITS outputs; keep non-unit float bounds and FITS-only neutral/ROI paths explicit.
- Freeze guidance v10 and retain exact complete v9 policy/reference history, including read-only confirmation replay and same-master evidence recovery.

## 2026-10-13 — Evidence-bound input confirmation

- Add `confirm-input` after accepted inspection to resolve every unknown input field in the same session from explicit source declarations.
- Preserve frozen sessions and historical reviews; bind supplemental confirmation to the manifest, subsequent runs, automatic same-master recovery and final audit.
- Keep histogram diagnostics separate from linear identity, and support exact pre-upgrade inspection references without granting new work to legacy policies.

## 2026-10-13 — Scientific XISF Stage 3 background processing

- Read supported FITS/XISF science in one bound bottom-origin coordinate frame for S3 sampling and held-out measurements.
- Add an audited scientific sample bridge with native float32 identity verification; preserve legacy bridge bytes and prohibit fallback.
- Freeze background v4 and guidance v9 with exact complete v8 policy/reference history; retain explicit batch review and FITS-only public ROI/neutralization.

## 2026-10-12 — XISF scientific input metrics

- Read XISF Image 0 for full-image candidate/parent statistics with declared sample range, layout,
  byte order and supported lossless payload validation; retain negative, above-range and nonfinite values.
- Surface decode failures through supplemental report warnings without changing scientific receipts.
- Freeze guidance v8 and preserve complete v7 policy/reference history; keep ROI FITS-only.


## 2026-10-12 — Signed recomposition and global contrast review

- Treat signed negative-energy ratios and low global display contrast as bound visual-review warnings while retaining other hard gates.
- Verify the actual final additive composition and record paired raw endpoints; enforce complete warning review materials and five passing gates.
- Freeze subject_roi_residual_v2, paired_fits_jpeg_v2 and stage_recompose_review_v7 with exact v6 history, paired delivery checks and immutable evidence after pruning.
- Preserve StarNet bridges, source locks and existing sessions; new M81 acceptance uses a separate lineage without redoing Stage 1-5.

## 2026-10-12 — Native input precision and failed separation isolation

- Promote both native Stage 6 parent loads with constrained fmul 1, retaining float32 identity and scientific gates.
- Verify bridge receipts after every separation invocation; isolate failed declared artifacts before receipt commit and expose structured failure evidence.
- Freeze stage_starnet_precision_v6 with exact v5 history; preserve legacy bridge/lock bytes and all historical sessions. Batch failures return nonzero while independent targets continue.
- Rebuild saved adaptive-stretch manual query evidence under its exact frozen v5 command policy during historical replay; reject unknown authority fingerprints and forged query contents.

## 2026-10-11 — Native linear StarNet separation

- Route frozen StarNet 2.6.2 through an independently locked float32 FITS bridge with explicit native linear pre-stretch; preserve the legacy bridge and source lock bytes.
- Bind input/output pixels, argv and raw logs in internal v2 receipts; retain signed Siril residuals and existing visual/recomposition gates.
- Freeze versioned_linear_bridge_v2 and stage_starnet_linear_v5 with exact historical guidance snapshots for immutable replay/finalize; retain pending review evidence and exclude protocols from release packages.

## 2026-10-11 — Evidence-bound stage limits

- Separate Stage 3–5 algorithm premises, recommended settings and technical gates; remove plane-safety guarantees and disclose RGB Lab luminance restoration.
- Require sourced standard RGB PCC compatibility or an explicit user display experiment; freeze stage_evidence_limits_v4 with exact v3 immutable replay/finalize history.
- Delegate background processing to the Agent and make batch continuation consume real reviews, preserving committed and pending materials on resume.

## 2026-10-10 — Direct stage provenance

- Remove the sixteen protocol identity documents and bind each SSF directly to its primary stage plus required supporting stages.
- Freeze stage_primary_references_v3 and retain exact v1/v2 historical reference fingerprints for immutable replay/finalize when the retired directory is wholly absent.
- Update stage routing, Agent guidance, release inventories and regression checks without changing scientific scripts or acceptance thresholds.

## 2026-10-10 — Stage-owned protocol bodies

- Move applicability, parameter rules, SSF skeletons and protocol-specific reviews into the ten stage guides; retain protocol identity paths for provenance compatibility.
- Keep shared tone adjustment in Stage 10 and local saturation in Stage 8; route direct lookups to canonical stage sections.
- Require stage-body SHA-256 bindings for new guidance sessions and preserve exact historical guidance fingerprints for immutable replay/finalize.

## [0.3.3] - 2026-10-08 — Paired scientific FITS delivery

- Export a float32 scientific FITS and its JPEG in delivery.render, with immutable roles, native reopen and read-only pixel/WCS verification.
- Finalize reference.jpg, final.jpg and final.fit together before cleanup; preserve paired review, interrupted-copy recovery and scientific file hashes after pruning.
- Freeze paired_fits_jpeg_v1 for new sessions and retain historical two-JPEG replay/finalize without backfilling.


## [0.3.2] - 2026-10-08 — PSF spatial diagnostics

- Add hash-bound native-coordinate 3x3 coverage and regional matched FWHM, roundness and periodic direction reports without spatial quality thresholds.
- Freeze native_star_samples_v2 with ellipticity-weighted axial means and canonical kernel axes; retain v1 computation and immutable replay/finalize.
- Require both reports and parent/candidate pixel review for v2 adoption; report I/O failure preserves completed science and keeps the candidate pending review.

## [0.3.1] - 2026-10-08 — Masked local saturation

- Add an optional color.finish variant for reviewed nonlinear RGB FITS starless branches, with source-derived dark/highlight protection, native saturation and audited PixelMath blending.
- Bind scientific mask/blend checks and parent/mask/candidate review materials; preserve existing global color finishing and immutable historical replay/finalize.

## 2026-10-07 — Online by default, explicit offline opt-in

- Start every Siril run online and treat `init --offline` as the explicit tightening switch: that session passes `--offline` to every run and still rejects any `-catalog=gaia` at static validation. Freeze the rule as `online_default_gaia_catalog_explicit_v3`.
- Keep the catalogue an explicit per-script decision under both modes. `platesolve/pcc/spcc` still require exactly one `-catalog=gaia|localgaia`, local and remote catalogues still cannot be mixed, plate solving is still confined to `astrometry.solve`, and catalogue commands are still confined to `astrometry.solve` and `color.calibrate`. This is required because the pinned manual defaults `pcc` to remote NOMAD and lets `platesolve` choose a catalogue from the field of view, so an omitted `-catalog` would reintroduce implicit networking.
- Branch the classifier on the policy a session froze so v1 and v2 receipts keep replaying under the offline-first rule they were written with; record the resolved label and `effective_offline` from that same rule. Preserve every command-policy gate, the delivery/initfile repository switches, the credential-free child environment and `PIP_NO_INDEX=1`.
- Register the outgoing reference fingerprints before editing so sessions frozen against them still verify. No new CLI flag, no change to review schemas, acceptance thresholds or native command authorization.

## 2026-10-05 — Benign runtime log re-classification

- Add fail_closed_v3, which records context-proven runtime noise (post-success pipe flush, nl-bayes copy-fits notice, JPEG EXIF notice) as `benign` with a `benign_count`, keeping the exact line, number and text in the receipt while no longer setting the run-wide status. Only a fatal finding can fail a run, and a real warning such as an imprecise photometric fit still surfaces on its own.
- Keep every fail_closed_v2 detection rule in force under v3 so a catalogue HTTP failure, a script failure and any unclassified runtime error stay fatal. Canonicalize v3 onto v2 for replay comparison so existing receipts still verify, while v1 remains distinguishable.

## 2026-10-05 — Versioned policy history, compact receipts, review-check

- Replace the chain of per-feature execution-policy constants with one ordered history table tested by content, so a new feature appends a single entry instead of renaming a constant and a twelve-member membership tuple.
- Stop inlining per-star PSF rows in the run receipt; keep the measured summary and bind the rows by count to the hash-bound star tables, reducing a rejected deconvolution receipt from 674 KB to 4 KB while older inline receipts still replay and finalize.
- Add the read-only `review-check` command to derive a review skeleton with real fingerprints and to validate an existing review before finalize. It never writes a review, adds no gate and changes no session state.

## 2026-10-05 — Astrometry outcome status and remote failure ladder

- Record `solve_requested_local_gaia`/`solve_requested_remote_gaia` during static validation and promote to `solved_*` only after the declared output is written and re-measured, so a failed run can no longer publish a solve status for a missing artifact.
- Classify a remote catalogue failure as transient, persistent or no-match, and allow one bounded same-parameter retry, a frozen local catalogue, or explicit coordinates before falling back to colour work. No new CLI flags, review fields or acceptance thresholds.

## 2026-10-05 — Subject visibility and finishing benefit

- Require direct subject visibility, fixed target/sky evidence and explicit noise tradeoffs in the existing stretch, tone and delivery review gates; avoid default global darkening and keep diagnostic autostretch separate from science.
- Freeze subject_visibility_v1 while preserving masked_dual_v1 and exact prior policies/reference fingerprints for immutable replay/finalize. Keep CLI, review schemas, numerical gates and native operation authorization unchanged.

## 2026-10-05 — Native masked dual HDR stretch

- Add evidence-triggered explicit dual transfers with smooth luminance masks and native mono/RGB mixing; retain the single-curve default and existing revision limit.
- Freeze both chains and the shared mask, require four direct review previews, and replay the complete spatial transfer for signed additive star recomposition without changing closure/negative-residual gates.
- Freeze masked_dual_v1 and preserve exact pre-HDR policies/reference fingerprints for immutable receipt replay and finalize. Verify native Siril 1.4.4 arithmetic, mask copies, and paths containing spaces.

## 2026-10-05 — Neutral held-out benefit and paired safety

- Bind separate fit/validation sky regions; retain regions.v1 and pure additive/explicit white-reference boundaries.
- Preserve independent benefit, paired endpoint transitions and fixed ROI signal evidence in immutable receipts, supplemental metrics and recovery. Numerical diagnostics remain uncertain; no new empirical thresholds.
- Freeze heldout_benefit_safety_v1, require bound actual review materials, and preserve historical replay/finalize with exact pre-change reference fingerprints.

## 2026-10-04 — Native PSF sample provenance and matched FWHM

- Bind full-precision native models, saturation exclusions and sample-derived native manual PSFs through a fixed SirilPy bridge. Preserve the explicit native-stars fallback when evidence is unavailable; Python never writes PSF pixels.
- Measure only bidirectionally unique star pairs within 1.0 pixel using identical detection settings after reloading the scientific candidate. Retain uncertainty and failed-run evidence without adding acceptance gates or changing RL/TV/kernel defaults.
- Freeze native_star_samples_v1 and preserve previous policies and exact reference/command-policy fingerprints for historical receipt replay and finalize.

## 2026-10-04 — Subject ROI and signed recomposition diagnostics

- Add optional hash-bound parent/starless/star-layer ROI measurements and matching native MTF comparison previews after scientific snapshots. Keep existing acceptance gates and default processing steps.
- Measure all recomposition channels before rejection; retain complete failure evidence, residual locations and an 8×8 negative-energy grid without changing thresholds or clipping pixels.
- Freeze subject_roi_residual_v1 and preserve previous session receipt replay/finalize with exact trusted reference fingerprints.

## 2026-10-04 — Log diagnostics v2 and unknown channel gate

- Classify wrapped Siril errors, script/progress failures and catalogue HTTP failures without relaxing contextual warning exceptions. Freeze fail_closed_v2 for new sessions and preserve v1 receipt replay/finalize.
- Stop after input.inspect when input_state or channel_mode is unknown; reject candidate finalization while allowing known channel modes without channel_map.

## 2026-10-04 — Background sampling and native precision v3

- Preserve held-out material acceptance; add exact native footprints and one bounded sparse-star recovery after insufficient strict coverage.
- Add source-only 75×75 validation ROI proposals, hard foreground rectangles, and native float32 identity evidence before fitting.
- Freeze heldout_material_gate_v3; retain immutable v1/v2 receipt replay and finalize.
## Unreleased

- Remove the unintegrated stretch-advisor experiment and its exclusive tests; retain all runtime scripts and manual/release maintenance tools.
- Replace Header/Stage 4 guarantees with sourced geometry, Header completeness, coordinate closure and sky-correspondence boundaries. Clarify native Siril/Gaia requirements in helper notes; preserve diagnostic fields, classifications and verify exit-code semantics.
- Describe the current PSF bridge and fallback without historical authorization or anecdotal measurements. Freeze evidence_bound_guidance_v1 and retain the complete progressive_disclosure_v1 policy and exact previous reference fingerprints for immutable replay/finalize.

- Reduce the skill entrypoint to task routing, shared invariants and the execution/review loop. Keep stage ordering in its sole stage map and turn the protocol index into a unique operation lookup; move missing input, review and delivery guidance into existing references.
- Freeze progressive_disclosure_v1 guidance in new sessions and retain the exact previous policy and document fingerprints for immutable replay/finalize. Keep CLI, scientific operations, SSF and public review/selection schemas unchanged.

- Add heldout_material_gate_v2: pixel-derived chromatic benefits, span warnings, paired new-shadow boundary protection and explicit skip isolation. Require identical-MTF parent/candidate visual reviews; preserve v1 evidence for replay/finalize without upgrading sessions.

- Freeze held-out background ROI evidence before fitting, prevent overlapping 25x25 samples at every count, and add sampler `--exclude-regions`.
- Require paired PCG64 block-bootstrap luminance/native-channel spatial improvement; bind read-only background validation reports and block failed or uncertain candidates from reviews, lineage and delivery. Preserve pre-gate sessions for immutable replay/finalize only.

- Use a 20-column aspect-preserving candidate grid, detection-only robust trends, complete 25-pixel sample protection and deterministic spatial selection; retain the 256-point cap and require middle-sky coverage. Add separate sampling diagnostics and per-line density without changing the strict contract or neutralization defaults.
- Default to a reviewed separation candidate for full galaxy and diffuse-nebula processing; retain containing-star priority for stellar subjects and clarify that final star preservation permits intermediate separation. Align Stage 6 with the implemented validation and fallback contract.
- Add automatic RGB background ROI contracts and background-only neutralization for confirmed dualband OSC, preserving unit gains, scientific signal differences and native verification.
- Add audited PCC display fallback for confirmed dualband OSC when narrowband SPCC is unavailable or rejected, preserving the uncalibrated-parent and fit-quality gates.
- Accept parameter-bound manufacturer passbands and documented native OSC RGB for narrowband SPCC without requiring HOO extraction.
- Measure same-scale StarNet channel retention and raw-zero changes without automatic pixel repair or inferred spectral roles.
- Label nonlinear display values with native sRGB assignment and disable heuristic ICC loading to prevent repeat Gamma on FITS reopen/export.
- Require accepted nonlinear RGB parents, one bounded native color operation, and paired direct previews for finishing reviews and delivery.
- Require explicit tone/color evaluation notes; record imprecise PCC fits as nonfatal quality warnings and allow acceptance only after actual image review, retaining the precision limitation.
- Clarify native background samples per row, helper target sample totals and existing-contract sample counts.
- Added native online Gaia astrometry with explicit offline boundaries and optional local catalogues.
- Added offline SPCC response discovery, complete spectral evidence checks, and audited native background/reference neutralization.
- Documented independent SPCC/PCC/neutral fallback from the same uncalibrated parent.


本文件记录 `starun-siril` 的独立版本变更。

## Unreleased

- Add reviewed nonlinear MTF/GHS tone adjustment before and after star recomposition, with separate tone-chain auditing and unchanged stretch/quality gates.

## [0.3.0] - 2026-10-01

- Migrate StarNet 2.5–2.6.2 to a pinned offline Python bridge while retaining Siril 1.4.4 and contract 1.
- Run conservative linear denoising before deconvolution and regenerate PSF from the actual parent.
- Add evidence-based stretch selection and explicit, hash-bound FITS region measurements.
- Freeze actual Python dependencies and keep legacy execution-policy sessions read-only.
- Use verified external Python for helpers and Siril-launched pyscript for adapters; document macOS launch constraints.

## [0.2.0] - 2026-09-29

- Make scientific metrics and automatic background sampling fail closed; remove inferred image domains and fabricated visual passes.
- Add native GHS, offline local Gaia astrometry, protected luminance CLAHE and nonlinear denoise protocols.
- Add Source FITS Header Preservation protocol and sync_fits_header.py tool for lossless WCS and prior metadata injection with pixel invariance verification.
- Bind primary scientific outputs, explicit transfer chains, reviewed starless branches and display-domain recomposition closure.
- Preserve standalone contract 1 and immutable session history; complete native and C50 acceptance with documented fallbacks.

## [0.1.1] - 2026-09-24

- 例行补丁升级与元数据规范维护

## [0.1.0] - 2026-08-29

- 建立 standalone contract v1：公开命令收敛为 `probe`、`init`、`run`、`finalize`。
- 增加由 session 冻结的 `siril|strict` 容器验证模式；默认以真实 Siril 前向重开为准，strict 先做标准库完整容器检查再核对 Siril 观察。
- 离线 Siril 1.4.4 手册完整随包分发，但改为命令或参数不确定时按需查询。
- 明确 `review_required` 只保留候选与审计证据，不产生正式 reference/final；仅 `success|partial_success` 可正式交付。
- 保留精确白名单、确定性 ZIP、第三方组件完整性与不可公开发布许可门禁，同时移除运行时工具安装接口。

- Stage 9 generation requires an explicit accepted nonlinear starless source, shares verified lineage checks with execution, and preserves native display-domain residual recomposition with independent NumPy closure validation.

## Stage 8 纹理门与候选高频审查

新增冻结 local_contrast=texture_hf_review_v1：HDR 实际谱系与 CLAHE 互斥，人工当前源 ROI 的 texture_snr <1.7 在执行前阻断；实际 Siril 候选多尺度高频幅度 >1.5x 告警并强制材料与像素审查。保留旧 v13 策略和文档指纹，历史记录只读回放及交付。比值不是物理信噪比或真实纹理证明。


Pre-color chroma denoise: add a native RGB bilateral variant with locked weighted display brightness, joint color-edge protection, endpoint-safe common weights, paired protected color probes and immutable review/replay evidence. No release or version bump.

### Stage 9 independent contour

- Separate stellar contribution from optional local-peak MTF contour contraction, retaining signed residuals and common RGB weights.
- Add same-contribution controls, independent review evidence and immutable outgoing-session replay. Version remains 0.3.3.

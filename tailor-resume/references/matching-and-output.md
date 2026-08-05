# JD matching and output policy

Use this reference for every job-specific application.

## Contents

- [Requirement model](#requirement-model)
- [Behavior-level decoding](#behavior-level-decoding)
- [Evidence states](#evidence-states)
- [Evidence depth](#evidence-depth)
- [Selection priorities](#selection-priorities)
- [Keyword policy](#keyword-policy)
- [Resume writing](#resume-writing)
- [Compression calibration](#compression-calibration)
- [Chronology rules](#chronology-rules)
- [Page-cap calculation](#page-cap-calculation)
- [Application directory](#application-directory)
- [Match report](#match-report)
- [Delivery gate](#delivery-gate)

## Requirement model

Extract requirements without silently strengthening or weakening them:

- **Hard requirement**: explicitly mandatory eligibility, credential,
  location, schedule, language, clearance, or experience threshold.
- **Core responsibility**: work the hired person is expected to perform.
- **Preferred qualification**: explicitly optional or advantageous evidence.

Also capture the role level, business domain, recurring nouns and verbs, and
observable success outcomes.

## Behavior-level decoding

Use two-layer decoding when a JD responsibility is too abstract to match
directly:

1. Convert the abstract responsibility into observable behavior categories.
   For example, "drive project delivery" may include requirements analysis,
   planning, stakeholder alignment, risk handling, decisions, and review.
2. Convert each relevant behavior into the actions and evidence that would
   demonstrate it: what the candidate did, for whom or with whom, at what scope,
   and with what verified outcome or artifact.
3. Match those evidence needs against the career master. Record missing details
   as `none` or `needs-confirmation`; never assume the candidate performed every
   behavior implied by the JD.

Use the decoded behavior chain to identify high-impact clarification questions
and to order evidence. Do not copy the entire analytical chain into the resume.

## Evidence states

Classify each requirement:

- `strong`: one or more direct, specific, verified examples.
- `partial`: adjacent or narrower verified evidence; explain the limitation.
- `none`: no supporting fact.
- `needs-confirmation`: a supplied fact may support it, but a material detail
  is ambiguous.

Point to a career-master heading or quote a concise current-task fact. Do not
use an invented evidence ID if the source has none.

## Evidence depth

Assess depth using only supplied facts:

- **Action:** the personally performed decision, analysis, build, coordination,
  or delivery work.
- **Scope or result:** a verified metric, scale, constraint, output, or outcome.
- **Stakeholders:** verified reporting lines, users, customers, teams, or
  decision-makers that clarify ownership.
- **Artifact:** a safe-to-disclose deliverable such as a process, specification,
  analysis, system, or training material.

These are possible evidence layers, not mandatory fields. Never invent a metric,
stakeholder, or artifact to make a bullet appear complete. Do not expose
confidential artifact names, links, customer details, or internal information.

## Selection priorities

Rank candidate content by:

1. direct relevance to a hard requirement or core responsibility;
2. strength and specificity of verified outcome;
3. recency;
4. scope appropriate to the target seniority;
5. coverage not already provided elsewhere in the resume.

Use older evidence when it is materially stronger or more relevant than newer
evidence. Avoid repeating the same proof in the summary, skills, and multiple
bullets.

## Keyword policy

- Use the JD's exact term when the career fact uses that term or supports a
  genuinely equivalent concept.
- Preserve a more precise career term when the JD uses a broad category.
- Never add a technology, credential, role level, industry, or number solely
  because it appears in the JD.
- Never add invisible text, keyword dumps, or misleading parenthetical lists.

## Resume writing

- Make each bullet concise and evidence-led.
- Prefer action + scope/context + verified result.
- Use compressed STAR as an editing check rather than a visible four-part
  formula. When verified, show two or three meaningful steps in the behavior
  chain—such as initiation, stakeholder alignment, decision, delivery, and
  review—without turning the bullet into a process diary.
- Use active voice without adding ownership not present in the source.
- Keep dates, tense, punctuation, and number formatting consistent.
- Avoid first-person pronouns, generic adjectives, mission statements, and
  unsupported superlatives.
- Keep official names intact. Translate surrounding descriptions to the JD
  language; retain an official title and add a supported functional translation
  in parentheses when useful.
- Do not hide an unsupported hard requirement behind vague wording.

## Compression calibration

When the strongest content is still over budget or the correct detail level is
unclear, compare three working versions:

1. A lean version containing only indispensable evidence.
2. A full version preserving all relevant verified detail.
3. A balanced version between those boundaries.

Use the comparison to identify facts that cannot be removed and wording that
adds no evidence. Treat these as working drafts, not default deliverables. The
actual PDF page count and page cap remain authoritative, and no version may add
or strengthen a fact merely to sound more complete.

## Chronology rules

- Order formal roles in reverse chronology.
- Keep the last ten years intelligible.
- For a weakly related recent role, preserve company, title, and dates rather
  than creating an unexplained gap.
- If the most recent role lasted at least 12 months, keep one scope statement
  and two to four substantive bullets. Use four to six bullets when highly
  relevant and space permits.
- Omit older irrelevant roles. Restore older relevant roles when they provide
  stronger evidence.

## Page-cap calculation

Use verified, de-duplicated full-time tenure:

| Total verified experience | Final-render cap |
| --- | ---: |
| 0-3 years inclusive | 1 page |
| >3-5 years inclusive | 2 pages |
| >5-10 years inclusive | 3 pages |
| >10 years | 3 pages by default |

Use four pages only for an executive/research profile or an explicit request.
The renderer's Markdown estimate is advisory; the actual PDF count is
authoritative.

If over the cap, remove duplication and weak evidence before compressing
history. Never reduce the renderer's 10.5 pt body font, 18 mm margins, or 1.08
line spacing to force a fit.

## Application directory

Use:

```text
applications/YYYY-MM-DD-company-role/
```

Store:

- `jd.md`
- `tailored-resume.md`
- `tailored-resume.docx`
- `tailored-resume.pdf`
- `match-report.md`

Avoid overwriting an existing directory. Add `-2`, `-3`, and so on when needed.

## Match report

Do not place an arbitrary overall percentage at the top. Include:

1. target role, company, JD input/source, and extraction limitations;
2. page-cap tenure calculation;
3. hard requirements, core responsibilities, and preferred qualifications;
4. evidence state and source pointer for each requirement;
5. selected evidence and selection rationale;
6. materially omitted evidence and omission rationale;
7. hard gaps and unresolved questions;
8. current-task facts used but not yet written to the career master;
9. render results: estimated pages, actual PDF pages, parity result, and visual
   QA status.

If the user explicitly requests a numeric score, define weights and arithmetic
before reporting it. Keep the evidence matrix regardless of the score.

## Delivery gate

Deliver only when:

- all reader-facing claims trace to verified facts;
- Markdown, DOCX, and PDF contain the same content;
- PDF page count is within the cap;
- every rendered page has been visually inspected;
- no clipping, overlap, missing glyphs, placeholders, or internal notes remain;
- `match-report.md` accurately records gaps and QA status.

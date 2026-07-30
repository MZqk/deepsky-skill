# JD matching and output policy

Use this reference for every job-specific application.

## Requirement model

Extract requirements without silently strengthening or weakening them:

- **Hard requirement**: explicitly mandatory eligibility, credential,
  location, schedule, language, clearance, or experience threshold.
- **Core responsibility**: work the hired person is expected to perform.
- **Preferred qualification**: explicitly optional or advantageous evidence.

Also capture the role level, business domain, recurring nouns and verbs, and
observable success outcomes.

## Evidence states

Classify each requirement:

- `strong`: one or more direct, specific, verified examples.
- `partial`: adjacent or narrower verified evidence; explain the limitation.
- `none`: no supporting fact.
- `needs-confirmation`: a supplied fact may support it, but a material detail
  is ambiguous.

Point to a career-master heading or quote a concise current-task fact. Do not
use an invented evidence ID if the source has none.

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
- Use active voice without adding ownership not present in the source.
- Keep dates, tense, punctuation, and number formatting consistent.
- Avoid first-person pronouns, generic adjectives, mission statements, and
  unsupported superlatives.
- Keep official names intact. Translate surrounding descriptions to the JD
  language; retain an official title and add a supported functional translation
  in parentheses when useful.
- Do not hide an unsupported hard requirement behind vague wording.

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

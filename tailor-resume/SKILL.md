---
name: tailor-resume
description: Build and maintain a verified career-master.md, analyze a job description, select the strongest relevant evidence, and generate a factual ATS-friendly tailored resume in Markdown, DOCX, and PDF plus a match report. Use when the user asks to create, tailor, rewrite, or optimize a resume/CV for a specific JD; organize or update a career history; or compare verified experience against job requirements.
---

# Tailor Resume

Create a job-specific resume from verified career facts. Keep the reusable Skill
separate from the user's private career data.

## Non-negotiable rules

1. Treat `career-master.md` and facts explicitly supplied by the user in the
   current task as the only sources of personal-experience claims.
2. Never invent or inflate titles, dates, employers, technologies,
   responsibilities, credentials, team size, revenue, percentages, or outcomes.
3. Use JD terminology only when a verified fact is genuinely equivalent.
   Never keyword-stuff, hide keywords, or imply unsupported years of experience.
4. Preserve official employer and title names. Add a parenthetical functional
   descriptor only when verified facts support it.
5. Treat every fact already stored in `career-master.md` as displayable,
   including written monetary values and metrics, unless the user explicitly
   marks it as non-public.
6. Use newly supplied facts in the current application immediately. Show the
   exact normalized change proposed for `career-master.md` and wait for
   confirmation before writing it permanently.
7. Do not use company research, general knowledge, or the JD itself as evidence
   about the user's experience.
8. When a critical fact is ambiguous, ask one high-impact question at a time.
   If the user asks for a draft first, generate a conservative draft and record
   unresolved gaps in the match report.

## Choose the workflow

- **Build or update the career master**: Normalize raw career material into the
  private fact source.
- **Tailor to a JD**: Analyze one JD and generate the complete application
  bundle from an existing career master.
- **Update and tailor**: Use explicit new facts for the current application,
  propose a career-master patch, and continue tailoring without waiting for the
  permanent write.

Read [career-master-schema.md](references/career-master-schema.md) when building,
updating, or interpreting the fact source. Read
[matching-and-output.md](references/matching-and-output.md) before analyzing a
JD or generating deliverables.

## Resolve private paths

1. Use a `career-master.md` path explicitly supplied by the user.
2. Otherwise, search only the current workspace for that exact filename.
3. If exactly one file is found, confirm it is the intended fact source when
   there is any ambiguity.
4. If none or multiple are found, ask which private working directory to use.
5. Never create or store the real career master inside this Skill directory.
6. When creating a new master, copy
   [career-master.template.md](assets/career-master.template.md) into the chosen
   private working directory as `career-master.md`.

Place application bundles beside the private master by default:

```text
<private-workspace>/
├── career-master.md
└── applications/
    └── YYYY-MM-DD-company-role/
```

Use a filesystem-safe company/role slug. If the destination already exists,
append a numeric suffix instead of overwriting it.

## Build or update `career-master.md`

1. Accept pasted notes and readable local TXT, Markdown, DOCX, PDF, or image
   files.
2. Preserve explicit facts while normalizing them into the template sections.
   Do not turn vague statements into precise claims.
3. Record exact dates when available. Preserve lower-precision dates as
   lower-precision rather than guessing a day or month.
4. Keep responsibilities, projects, actions, outcomes, metrics, and technologies
   distinguishable enough to support later evidence selection.
5. Include contact details, education, certifications, languages, links,
   publications, open source, and other resume material only when supplied.
6. Omit empty optional fields from generated resumes.
7. Present a concise patch preview before changing an existing master. Apply
   only confirmed changes.

## Acquire and preserve the JD

1. Accept pasted text, a local TXT/Markdown/DOCX/PDF file, an image or
   screenshot, or a recruiting-page URL.
2. Extract the complete visible JD, including qualifications and location or
   employment constraints.
3. If a URL is inaccessible, login-protected, incomplete, or unstable, explain
   what is missing and request pasted text or a file. Never reconstruct the JD
   from the job title.
4. Save the normalized JD as `jd.md` in the application directory. Preserve the
   wording of requirements; clearly label any extraction uncertainty.
5. Do not perform external company research unless the user explicitly asks.
   Even then, keep external context out of personal-experience claims.

## Map requirements to evidence

1. Split the JD into hard requirements, core responsibilities, and preferred
   qualifications.
2. For every requirement, locate the strongest fact in the career master or in
   explicit current-task additions.
3. Classify evidence as `strong`, `partial`, `none`, or `needs-confirmation`.
4. Point to the relevant career-master heading or current-task fact in the
   match report.
5. Ask only about missing facts that could materially change the application.
6. Warn about unsupported hard requirements, but still generate the best
   truthful resume unless the user requested a fit assessment first.
7. Do not emit an arbitrary overall match percentage. Generate a numeric score
   only when explicitly requested, and disclose its formula.

## Select and write resume content

1. Follow the primary language of the JD. Generate a second language only when
   explicitly requested.
2. Write a three-to-four-line professional summary using only verified years,
   domains, scope, and outcomes.
3. Include a compact core-skills section containing only evidenced skills.
4. Order formal employment in reverse chronology. Reorder projects and bullets
   within a role by JD relevance.
5. Keep the recent ten-year formal-employment timeline intelligible. Compress a
   weakly related role to basic company/title/date information instead of
   creating an unexplained gap. Omit older irrelevant roles; restore older
   highly relevant evidence when useful.
6. If the most recent formal role lasted at least 12 months, retain one scope
   statement and two to four substantive bullets even when it is weakly related.
   Use four to six bullets when it is highly relevant, subject to the page cap.
7. Prefer action, scope, and verified outcome over generic responsibility lists.
   Remove duplicated evidence across the summary, skills, and experience.
8. Omit photo, age, gender, marital status, government ID, current salary, and
   expected salary by default. Include only non-empty basic contact fields and
   relevant professional links.
9. Omit empty education, certification, language, project, publication, or
   open-source sections.
10. Keep the resume single-column and ATS-readable. Do not use sidebars, text
    boxes, icons, skill bars, multi-column layout, or layout tables.

Use the language-appropriate Markdown starting point:

- [tailored-resume.zh.template.md](assets/tailored-resume.zh.template.md)
- [tailored-resume.en.template.md](assets/tailored-resume.en.template.md)

## Enforce the page budget

Calculate verified total full-time work experience and apply this final-render
cap:

- 0 through 3 years, inclusive: 1 page
- More than 3 through 5 years, inclusive: 2 pages
- More than 5 through 10 years, inclusive: 3 pages
- More than 10 years: 3 pages by default
- Allow 4 pages only for an executive/research profile or an explicit user
  request

Estimate the Markdown against the fixed A4 template, then use the actual PDF
page count as the authority. If over the cap, revise content in this order:

1. Remove duplicated or low-evidence bullets.
2. Shorten verbose bullets without dropping facts.
3. Compress older weakly related roles.
4. Remove optional low-relevance sections.

Do not solve overflow by shrinking body text below 10.5 pt, reducing margins
below 18 mm, tightening line spacing below 1.08, or hiding text.

## Create the application bundle

Create this set by default:

```text
applications/YYYY-MM-DD-company-role/
├── jd.md
├── tailored-resume.md
├── tailored-resume.docx
├── tailored-resume.pdf
└── match-report.md
```

Build `tailored-resume.md` first. Generate DOCX and PDF from that same Markdown
source with [render_resume.py](scripts/render_resume.py). Resolve the bundled
workspace Python runtime when available; otherwise use a Python environment
with `python-docx` and `pypdf`, plus `soffice` and `pdftoppm`.

```bash
python <skill-directory>/scripts/render_resume.py \
  <application-directory>/tailored-resume.md \
  --docx <application-directory>/tailored-resume.docx \
  --pdf <application-directory>/tailored-resume.pdf \
  --qa-dir <temporary-qa-directory> \
  --target-pages <page-cap>
```

The renderer uses an A4 single-column layout with 18 mm margins, 10.5 pt body
text, restrained heading sizes, and 1.08 line spacing. Treat a nonzero exit as
unfinished work:

- Fix missing dependencies or conversion errors.
- If the page cap is exceeded, edit the Markdown content and render again.
- If text parity fails, inspect and correct the conversion before delivery.

## Verify all outputs

1. Confirm the Markdown contains no placeholders, unsupported claims, analysis
   notes, or hidden keyword lists.
2. Render every PDF page to PNG through the renderer and visually inspect every
   page at readable zoom.
3. Check clipping, overlap, missing glyphs, awkward page breaks, font
   substitution, excess whitespace, and inconsistent headings.
4. Confirm the Markdown, DOCX, and PDF have the same reader-facing content.
5. Confirm the actual PDF page count is within the cap.
6. Keep QA images outside the application bundle and remove them after the
   latest inspection passes.
7. Never claim DOCX/PDF generation or visual QA succeeded when it did not.

## Write `match-report.md`

Follow [match-report.template.md](assets/match-report.template.md). Include:

- role and JD-source summary;
- requirement-to-evidence matrix;
- selected experience and why it was selected;
- materially omitted experience and why it was omitted;
- hard gaps, partial evidence, and remaining questions;
- proposed but uncommitted career-master additions;
- final Markdown estimate, actual PDF page count, page cap, and visual-QA status.

Keep analysis out of the resume itself. Deliver the clean resume files and the
separate report.

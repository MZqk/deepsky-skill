# Career master schema

Use this reference when creating, updating, or interpreting
`career-master.md`.

## Purpose

Treat the career master as the user's private, human-editable, canonical fact
source. It is not a long resume and does not need to fit a page limit. Preserve
useful detail so later applications can select evidence without inventing it.

## Canonical sections

Use only sections for which facts exist:

1. **Basic information**: name, location, phone, email, and professional links.
2. **Career overview**: optional user-supplied positioning or domain context.
3. **Work experience**: one reverse-chronological section per formal role.
4. **Independent projects**: work not nested under an employer.
5. **Education**.
6. **Certifications and licenses**.
7. **Languages**.
8. **Publications, patents, open source, talks, or awards**.
9. **Explicit exclusions**: facts the user does not want disclosed.

Do not add empty decorative sections to a generated resume. Empty template
fields may remain in a newly initialized career master until the user fills
them.

## Work-experience record

For each formal role, preserve:

- employer's official name;
- official title;
- start and end date at the precision supplied;
- location and employment type when supplied;
- a plain scope statement;
- responsibilities that explain ownership;
- projects or initiatives;
- actions personally taken;
- verified outcomes and metrics;
- technologies, methods, domains, and stakeholders explicitly involved.

Keep projects under the role where they occurred. Create a separate project
section only when the project was independent or the employer relationship is
unknown.

## Fact normalization

- Preserve exact numbers, currencies, units, denominators, and time windows.
- Preserve uncertainty. For example, keep "约 20%" as approximate rather than
  changing it to "20%".
- Do not infer causation from sequence. "Launched X; revenue later rose" is not
  automatically "X increased revenue".
- Do not convert team participation into leadership.
- Do not convert tool exposure into proficiency or years of experience.
- Do not merge facts from different employers or projects.
- Do not translate an internal title into a different official title. Add a
  supported functional descriptor only in parentheses.
- Calculate a derived value only from explicit inputs, show the calculation in
  the proposed patch, and obtain confirmation before storing it as a fact.

## New information

Facts explicitly stated by the user in the current task may be used immediately
for that application. Before updating the master:

1. Show the destination heading.
2. Show the exact Markdown addition or replacement.
3. Distinguish user-provided facts from editorial wording.
4. Ask for confirmation.
5. Apply only the confirmed patch.

Never write an inference, JD phrase, external-company fact, or model-generated
metric into the career master.

## Missing information

Ask about missing information only when it materially affects the current
workflow. Ask one question at a time. Useful high-impact questions include:

- exact employment dates needed to calculate tenure or explain chronology;
- whether the user personally owned or merely contributed to an outcome;
- the measurement window and baseline for a metric;
- whether a named technology was actually used in the cited work;
- whether a credential is current;
- whether an unclear title is official or only functional.

If the user requests a draft before answering, use a conservative statement and
record the uncertainty in `match-report.md`.

## Total work years

Calculate page-cap tenure from verified full-time formal-employment intervals.
Merge overlapping intervals rather than double-counting them. Do not count
education, side projects, internships, or part-time work unless the user
explicitly directs otherwise. Preserve the calculation in the match report.

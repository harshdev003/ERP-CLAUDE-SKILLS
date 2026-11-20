# Adversarial pass

Read this file in Step 6 lens 3 when the PR adds or changes a **route**, a **database access rule** or an **SSR page** in an ERP service repo. Skip it, and say nothing about it, when the diff has none of those surfaces.

The rules themselves live in `~/ERP-CLAUDE-SKILLS/rules/services/security.md` (read in Step 0); its rule IDs (A1, A2, B1, B2, C, D1) are the "Checklist" codes below. This file lists the probes only; cite the security.md rule, not this file, in a finding.

## How to run it

For each new or changed surface, read the code path a request takes (middleware, handler, query, rule) and write down what each probe below would get back. Compare that with the expected answer.

- **Each probe the change gets wrong is a Critical finding**, anchored on the line that decides the answer, citing the checklist rule in the table (e.g. "Checklist B2 — ownership").
- A probe that doesn't apply to the surface (no id in the path, no body, no SSR) is skipped, not reported.
- **A surface that is public on purpose** (a public product catalogue, a public status page) answers probes 1–3 with its public answer, which is not a finding. Check instead that it returns only allow-listed fields (Checklist A2) and that probes 4–8 still hold. A surface counts as public on purpose only with positive evidence: it sits in an existing public router or a sibling of one, it returns an explicit field allow-list, or the linked ticket asks for a public surface. A missing `authenticate`, or a database rule that allows every read, is never that evidence on its own: it is exactly what probes 1–3 catch, so flag it. When in doubt, treat the surface as private.
- One finding per wrong probe. When several probes fail for the same missing check, write one finding that names them all.

## Probes

| # | Probe | Expected answer | Checklist rule |
|---|---|---|---|
| 1 | No token | 401 (404 on a public route that doesn't exist; the public answer on a public surface) | B1 |
| 2 | Anonymous / guest token | The same answer as no login, for anything that isn't public | B1 (guest sessions count as authenticated) |
| 3 | Another user's token | 404, and no write (on a public surface: the public read, and still no write) | B2 |
| 4 | `{}` body | 400 or no rows, never every row | B2 (filter rule) |
| 5 | Missing or invalid id, or a tenant id (e.g. `companyId` / `orgId`) in the wrong shape | Same as probe 4 | B2 (filter rule) |
| 6 | Each other HTTP method on the path | 401 or 405 | B1 |
| 7 | An id taken from a public page or another response | Must not grant a read | A2 |
| 8 | SSR page with an id that doesn't exist | `notFound` from the server, no data in `__NEXT_DATA__` | C |

For a database access rule (row-level security, ORM scope), read probes 1–3 and 7 as reads and writes of the rows it guards.

## Why

In a multi-tenant ERP the worst bugs are a check that is missing — one company seeing another's invoices, stock or payroll. A review that reads the diff line by line misses them; these probes catch them.

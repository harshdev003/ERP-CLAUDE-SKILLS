# Resource map

The country × category lines a reviewer checks a CS draft against (SKILL.md Step 3, `review_rules.md` step 4). The triage skill already picked these when it drafted the email; this copy is here so the review can confirm the draft carries the right lines **for the person's country** — the one check the reviewer leaves to Claude.

> **The country tables, "Coverage gaps" and "Global backstops" below are a byte-identical copy** of `eng:investigate-memory-ingestion-guardrail`'s `references/resource_map.md` (the canonical copy). Edit a phone number there first and propagate it to every copy in the same PR — `tests/test_resource_map_copies.py` fails when they drift. Only this header and the final section are specific to this skill.

## How the review uses it

1. Take the country from the triage reply's UID line (`country:` or `suspected country:`).
2. Find the row for `(country, category)`. The category is on the same line.
3. Flag the draft when it carries a line from **another** country's row (a US number for someone in the UK), when a line it needs for the category is missing, or when a line does not fit the category.
4. A country not in the tables: the draft should carry only the global backstop; the triage reply already asks the reviewer to localize, and so does this check.

**Emergency numbers by country:** US / CA `911` · UK `999` (or `112`) · IE `112` / `999` · AU `000` · NZ `111`. A number from another country's row does not connect: 988 and 911 do not work from the UK, Ireland, Australia or New Zealand.

## Verification cadence

Phone numbers and URLs change. Re-verify **quarterly** against the named organisations' public websites. Don't trust any line unconfirmed in the last 12 months. `(country, category)` cells with no confirmed specialized line are listed under "Coverage gaps" below rather than in the tables (so a placeholder can't leak into a draft) — promote a line into the table once verified.

---

## 🇺🇸 United States

| Category | Resources |
|---|---|
| 1 Suicide / self-harm | 988 Suicide & Crisis Lifeline — call, text, or chat 988 / 988lifeline.org<br>Crisis Text Line — text HOME to 741741 |
| 2 Harm to others | 988 Suicide & Crisis Lifeline — call or text 988 (also supports thoughts of harming others) / 988lifeline.org<br>Crisis Text Line — text HOME to 741741 |
| 2a Harm to others — target is a partner, ex or family member (add-on to row 2) | National Domestic Violence Hotline — 1-800-799-7233 / text START to 88788 / thehotline.org (also supports people worried about their own behavior) |
| 3 Abuse / DV / coercive control / stalking / trafficking / sexual violence | National Domestic Violence Hotline — 1-800-799-7233 (call) / text START to 88788 / thehotline.org<br>RAINN National Sexual Assault Hotline — 1-800-656-4673 / rainn.org<br>National Human Trafficking Hotline — 1-888-373-7888 / text 233733<br>loveisrespect (young adults) — 1-866-331-9474 / loveisrespect.org |
| 4 Child / elder / dependent abuse or neglect | Childhelp National Child Abuse Hotline — 1-800-422-4453 / childhelphotline.org<br>Eldercare Locator (routes to Adult Protective Services) — 1-800-677-1116 / eldercare.acl.gov |
| 5 Medical emergency / overdose / poisoning / substance | Poison Control — 1-800-222-1222 (poisoning, overdose, dangerous reactions) / poison.org<br>SAMHSA National Helpline — 1-800-662-4357 / findtreatment.gov *(substance-use treatment referral — `(ongoing care)`)* |
| 6 Psychosis / mania / disorientation w/ safety concern | 988 Suicide & Crisis Lifeline — call or text 988 / 988lifeline.org<br>NAMI HelpLine — 1-800-950-6264 / nami.org *(mental-health information & support — `(ongoing care)`)* |
| 7 Immediate basic-safety (housing, food, water, medication) | 211 (United Way) — call 211 / 211.org (housing, shelter, food, utilities, basic needs)<br>National DV Hotline shelter locator (when DV-linked) — 1-800-799-7233 |

Ongoing-care directory for categories 5/6 (and any non-acute case): **psychologytoday.com/us/therapists** and **openpathcollective.org** (sliding scale). Add as the second line, never the first, never on an acute category-1/2 or imminent-danger email.

## 🇨🇦 Canada

| Category | Resources |
|---|---|
| 1 Suicide / self-harm | 9-8-8 Suicide Crisis Helpline — call or text 988 / 988.ca |
| 2 Harm to others | 9-8-8 Suicide Crisis Helpline — call or text 988 / 988.ca |
| 2a Harm to others — target is a partner, ex or family member (add-on to row 2) | 211 — call 211 / 211.ca (routes to local family-violence and counselling services) |
| 3 Abuse / DV / coercive control / stalking / trafficking / sexual violence | 211 — call 211 (routes to local domestic-violence shelters & services) / 211.ca<br>ShelterSafe — sheltersafe.ca (find a shelter by location)<br>Canadian Human Trafficking Hotline — 1-833-900-1010 |
| 4 Child / elder / dependent abuse or neglect | Kids Help Phone (under 20) — 1-800-668-6868 / text CONNECT to 686868<br>Provincial child-welfare / adult-protection line (211 will route) — call 211 |
| 5 Medical emergency / overdose / poisoning / substance | Poison Control — 1-844-764-7669<br>Wellness Together Canada — 1-866-585-0445 / wellnesstogether.ca *(mental-health & substance support — `(ongoing care)`)* |
| 6 Psychosis / mania / disorientation w/ safety concern | 9-8-8 Suicide Crisis Helpline — call or text 988 / 988.ca<br>Provincial mental-health line (211 will route) — call 211 *(ongoing care)* |
| 7 Immediate basic-safety | 211 Canada — call 211 / 211.ca |

## 🇬🇧 United Kingdom

| Category | Resources |
|---|---|
| 1 Suicide / self-harm | Samaritans — 116 123 (free, 24/7) / samaritans.org<br>Shout — text SHOUT to 85258 |
| 2 Harm to others | Samaritans — 116 123 / samaritans.org<br>NHS 111 (option 2 — mental health) |
| 2a Harm to others — target is a partner, ex or family member (add-on to row 2) | Respect Phoneline — 0808 802 4040 / respectphoneline.org.uk (for people worried about their own behaviour toward a partner or family member; weekdays) |
| 3 Abuse / DV / coercive control / stalking / trafficking / sexual violence | National Domestic Abuse Helpline (England) — 0808 2000 247 / nationaldahelpline.org.uk<br>Rape Crisis England & Wales — 0808 500 2222 / rapecrisis.org.uk<br>Modern Slavery & Exploitation Helpline — 08000 121 700<br>(Scotland DA & Forced Marriage — 0800 027 1234 · Live Fear Free Wales — 0808 80 10 800 · Women's Aid NI — 0808 802 1414) |
| 4 Child / elder / dependent abuse or neglect | NSPCC — 0808 800 5000 / nspcc.org.uk (child)<br>Hourglass (elder abuse) — 0808 808 8141 / wearehourglass.org |
| 5 Medical emergency / overdose / poisoning / substance | NHS 111 (poisoning / urgent medical advice)<br>FRANK — 0300 1236600 / talktofrank.com *(drugs & substance support — `(ongoing care)`)* |
| 6 Psychosis / mania / disorientation w/ safety concern | Samaritans — 116 123 / samaritans.org<br>NHS 111 (option 2 — mental health) *(ongoing care)* |
| 7 Immediate basic-safety | Shelter — 0808 800 4444 / shelter.org.uk<br>Refuge (when DV-linked) — 0808 2000 247 |

## 🇦🇺 Australia

| Category | Resources |
|---|---|
| 1 Suicide / self-harm | Lifeline — 13 11 14 (call) / text 0477 13 11 14 / lifeline.org.au<br>Beyond Blue — 1300 22 4636 / beyondblue.org.au |
| 2 Harm to others | Lifeline — 13 11 14 / lifeline.org.au |
| 2a Harm to others — target is a partner, ex or family member (add-on to row 2) | Men's Referral Service — 1300 766 491 / ntv.org.au (24/7; for men worried about their own behaviour) — when the person has not said they are a man, use 1800RESPECT — 1800 737 732 / 1800respect.org.au instead |
| 3 Abuse / DV / coercive control / stalking / trafficking / sexual violence | 1800RESPECT — 1800 737 732 / 1800respect.org.au (DV + sexual assault)<br>Australian Federal Police human-trafficking line — 131 237 (131 AFP) |
| 4 Child / elder / dependent abuse or neglect | Kids Helpline (5–25) — 1800 55 1800 / kidshelpline.com.au<br>1800ELDERHelp (elder abuse) — 1800 353 374 |
| 5 Medical emergency / overdose / poisoning / substance | Poisons Information Centre — 13 11 26<br>National Alcohol and Other Drug Hotline — 1800 250 015 *(ongoing care)* |
| 6 Psychosis / mania / disorientation w/ safety concern | Lifeline — 13 11 14 / lifeline.org.au<br>Beyond Blue — 1300 22 4636 / beyondblue.org.au *(ongoing care)* |
| 7 Immediate basic-safety | Ask Izzy — askizzy.org.au (housing, food, support search)<br>1800RESPECT shelter referral (when DV-linked) — 1800 737 732 |

## 🇳🇿 New Zealand

| Category | Resources |
|---|---|
| 1 Suicide / self-harm | 1737 Need to Talk — call or text 1737 / 1737.org.nz<br>Lifeline Aotearoa — 0800 543 354 |
| 2 Harm to others | 1737 Need to Talk — call or text 1737 / 1737.org.nz |
| 2a Harm to others — target is a partner, ex or family member (add-on to row 2) | Are You OK Family Violence Information Line — 0800 456 450 / areyouok.org.nz |
| 3 Abuse / DV / coercive control / stalking / trafficking / sexual violence | Women's Refuge Crisisline — 0800 733 843 / womensrefuge.org.nz<br>Safe to Talk (sexual harm) — 0800 044 334 / text 4334 / safetotalk.nz |
| 4 Child / elder / dependent abuse or neglect | Oranga Tamariki (call if a child is at risk) — 0508 326 459<br>Elder Abuse Response Service — 0800 32 668 65 |
| 5 Medical emergency / overdose / poisoning / substance | National Poisons Centre — 0800 764 766<br>Alcohol Drug Helpline — 0800 787 797 *(ongoing care)* |
| 6 Psychosis / mania / disorientation w/ safety concern | 1737 Need to Talk — call or text 1737 / 1737.org.nz *(ongoing care)* |
| 7 Immediate basic-safety | Work and Income housing — workandincome.govt.nz<br>Women's Refuge (when DV-linked) — 0800 733 843 |

## 🇮🇪 Ireland

| Category | Resources |
|---|---|
| 1 Suicide / self-harm | Samaritans Ireland — 116 123 (free, 24/7) / samaritans.org<br>Pieta — 1800 247 247 (call) / text HELP to 51444 / pieta.ie |
| 2 Harm to others | Samaritans Ireland — 116 123 / samaritans.org |
| 3 Abuse / DV / coercive control / stalking / trafficking / sexual violence | Women's Aid 24hr National Freephone Helpline — 1800 341 900 / womensaid.ie<br>Dublin Rape Crisis Centre 24hr National Helpline — 1800 778 888 / drcc.ie |
| 4 Child / elder / dependent abuse or neglect | Childline (ISPCC) — 1800 666 666 / childline.ie (child) |
| 5 Medical emergency / overdose / poisoning / substance | National Poisons Information Centre — 01 809 2166<br>HSE Drugs and Alcohol Helpline — 1800 459 459 *(ongoing care)* |
| 6 Psychosis / mania / disorientation w/ safety concern | Samaritans Ireland — 116 123 / samaritans.org *(ongoing care)* |
| 7 Immediate basic-safety | Threshold — 1800 454 454 / threshold.ie |

---

## Coverage gaps (no verified specialized line — use the country's related same-category lines first, else global backstops; reviewer localizes)

These `(country, category)` cells had no specialized national line confirmed at authoring time. They are listed here rather than in the tables so a placeholder can never be copied into an email as if it were a real line. For a case that maps to one of these, first use the country's **related lines from the same category** if the table has them (e.g. a trafficking case in NZ/IE → that country's DV / sexual-violence lines, which are dial-able national lines), then its category-1 crisis line if appropriate; only if the table offers nothing fitting, draft with the global backstops (as for a country not in the map). Either way, flag for a reviewer to add the missing specialized line.

**Route the fallback to the right kind of help.** The "nothing fits → global backstop" step does **not** mean every gap lands on Befrienders. Befrienders is an *emotional-support* directory, so it only fits categories whose need is emotional (1, 2, 6, and interim support for 3/4). A **physical medical emergency (category 5)** must route to **medical** guidance — the prompt-medical-care `{resource_list}` line defined under "Global backstops" (physician / urgent care / telehealth), with the emergency-services / ER body line as the dial-able action — never to Befrienders, which would understate the medical urgency. **Basic-safety (category 7)** leads with the practical-help `{resource_list}` line defined there (local social services / community assistance / shelter-food resource), again with the emergency-services body line as the dial-able action. See the US category-5 gap below for the worked case.

- **New Zealand — category 3 (trafficking specifically):** DV and sexual-harm lines are in the table; a dedicated trafficking line is unconfirmed.
- **Ireland — category 3 (trafficking specifically):** DV and sexual-violence lines are in the table; a dedicated trafficking line is unconfirmed.
- **Ireland — category 2a (harm to a partner, ex or family member):** no national line for people worried about their own behaviour is confirmed (Women's Aid serves people experiencing abuse). Use row 2 only and flag for the reviewer.
- **Every unmapped country — category 2a:** no global directory of lines for people worried about their own behaviour exists. Use the category-2 global backstop and flag for the reviewer.
- **Ireland — category 4 (elder / dependent adult):** the table's child line (Childline) does not cover elder/dependent abuse; an Irish elder-safeguarding line is unconfirmed.
- **United States — category 5 (general medical emergency):** the table's category-5 lines are Poison Control (poisoning) and SAMHSA (substance-use) only; there is **no free national medical-triage line** for a general medical emergency (dehydration, uncontrolled vomiting, acute physical symptoms). This is a **medical** gap, so it does **not** fall through to the Befrienders global backstop (emotional support ≠ medical care). Draft with prompt-medical-care guidance — the user's physician, urgent care, or a telehealth service — and the template body's emergency-services / ER line as the acute escalation; flag for the reviewer to add the user's own care channel if known. Do not force Poison Control or SAMHSA. *(No dial-able national line exists to promote; the guidance line itself is the fix.)*

Re-verify and promote a line into the tables once confirmed (see Verification cadence).

## Global backstops (country not in the table)

When the country isn't in the table, still **draft** the email. For the **emotional-need categories** — suicide/self-harm, harm-to-others, psychosis (1, 2, 6) and interim support for abuse/DV and child/elder/dependent (3, 4) — use **Befrienders Worldwide** (free, confidential crisis/support helplines listed by country at befrienders.org) as the lead resource: it keeps `{resource_list}` non-empty (so the intro grammar and closing hold) and routes to local lines. It's a directory, not a single dial-able number, so the *dial-able immediate action* is the template body's "call your local emergency services" line. For a **physical medical emergency (5)** or **basic-safety (7)**, Befrienders is not the lead — the `{resource_list}` line is the medical-care / practical-help guidance described in those bullets below (which keeps the list non-empty on its own). Flag in the Step 6 reply that the resource is global so a reviewer can add the country-correct specialized line before CS sends:

- **Suicide / self-harm / harm-to-others / psychosis (1, 2, 6)** — Befrienders fits directly; no extra line needed beyond the reviewer-localize flag.
- **Sexual violence / DV / trafficking (3), child / elder / dependent (4)** — Befrienders is interim emotional support only; flag that a reviewer must add the country's DV / sexual-violence / anti-trafficking / child-protection line (UN Women maintains a partial DV directory at unwomen.org).
- **Medical / overdose / substance (5), basic-safety (7)** — Befrienders is general emotional support only; it is **not** a medical or basic-needs resource, so it is never the `{resource_list}` line here. The single global line is concrete, actionable guidance (not a hotline), and it does **not** duplicate the template body's emergency-services line:
  - **Physical medical emergency / overdose / poisoning / substance (5):** `* Prompt medical care — please get checked by a medical professional: contact your doctor, an urgent-care clinic, or a telehealth service, or go to the nearest emergency room if symptoms are severe or worsening.` (General across the whole category — dehydration, overdose, poisoning, substance, or other acute medical concern; don't narrow the wording to one presentation.)
  - **Basic-safety (7):** `* Local support — reach out to local social services, a community assistance line, or a nearby shelter/food resource for help with shelter, food, or utilities.`

  This keeps `{resource_list}` non-empty on its own (the emergency-services body line remains the dial-able immediate action). Flag that a reviewer should add the country's poison-control / substance / housing / basic-needs line before CS sends.
- **Universal emergency line** — already covered by the template's "call your local emergency services" backstop. Do not duplicate.

Never substitute a mapped country's national line (a US/UK/etc. number) for an unmapped country — a wrong-country hotline is worse than the global backstop.

---

## Mapping — reviewer notes

The category axis matches `review_rules.md`'s topic triages: 1 → Suicide / self-harm, 3 → Relationship, 4 → Child / elder, 5 → Substance (and medical), 2 / 6 / 7 → Other categories. When the US list in `review_rules.md` and this file disagree, this file wins — it is the copy the dial-able lines are tested in.

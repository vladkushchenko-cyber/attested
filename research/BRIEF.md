# BRIEF — Oncology Diagnostics Intelligence prototype

You are working on a clickable HTML prototype of an internal Medical Affairs application.
Read this whole file before doing anything. Everything here is established fact from a real
intake session and from live source testing already completed. Do not re-derive it.

---

## 1. The users (from the intake session, 30 Oct 2026)

**Diana — Head of Early Cancer Detection & Screening, Global Medical Affairs.** The primary
user. Verbatim intake points:

- She is **not interested in selling a medicine**. She is interested in *identifying people
  with early-stage cancer*. Her unit of work is an **"intended use"**, e.g. *"I want to
  monitor patients with small cell lung cancer, probably via liquid biopsy."*
- Her workflow: define intended use → find candidate assays → **shortlist the top 2–3
  companies** → meet them to get technical specs → propose pilots, or produce a comparison
  report for external key experts (KEEs) implementing a test.
- **Her stated pain, verbatim:** *"I literally have to go into each of them to find what the
  specificity, what the sensitivity is, basically go in and take it out."* She wants a
  **table across records**, not a list of records to open one by one.
- She asks for queries like: *"give me all multi-cancer early detection tests with
  specificity of at least X and sensitivity of Y for lung cancer."*
- She filters on **operational** specs too, not just performance — her example was *a test
  that only requires 2 mL of plasma*.
- She uses it **episodically, not daily**: *"I don't use it on a daily basis."* Bursts around
  a new intended use or a congress.
- She explicitly asked for **agent-assisted entry**: *"somebody at ESMO said, Diana, I met
  this really great technology — can we add them to the database?"* Including from a
  **photo of a poster** taken at a congress booth.
- She does **not** want endless novelty: *"it's not that we're consistently wanting to find
  new technologies... it's more of the update of already existing tools."* A high-school
  science project is explicitly out of scope.
- On funding data: *"We don't need this much data on this. We just want to know, are they
  good on their funding or are they like baby stage."* → a simple signal, not an analysis.
- Research & trials content = **must have**. Event feeds / corporate announcements =
  *"nice to know"*. Detailed funding analysis = **not** a must.
- She is on parental leave from Nov 2026, back end of March 2027. **David LeDuc** covers.
- **Ester** (diagnostics subject expert, available to end of 2026) does the deep category
  landscapes and advisory-board prep. She judges assays on: sensitivity, specificity, PPV,
  cancer types targeted, material (tissue/blood/urine), number and type of trials,
  population, randomised or not, retrospective or prospective, **analytically vs clinically
  validated**.
- **James** curates an existing Airtable of assays+organisations and wants automated updates.
- Target date: **early May 2027** (one month before ASCO).

**Design consequences you must honour:**
- The app must be legible to someone returning after months away (episodic use, handover
  to David). No tacit knowledge required.
- Cross-record comparison is the core value, not record browsing.
- Every number must be traceable to a source, or it is worse than useless to these users.
- An explicit "no data for this indication" state is required. These users would rather see
  nothing than a plausible wrong number.

---

## 2. The maturity model — the diagnostics ladder (REPLACES trial phase)

Trial phase does not work for diagnostics: two-thirds of relevant studies have no phase
value at all. We use a 5-rung ladder instead:

| Rung | Name | Meaning |
|---|---|---|
| **R1** | Analytical validation | limit of detection, precision, reproducibility |
| **R2** | Clinical validation | sensitivity/specificity/PPV/NPV vs a reference standard |
| **R3** | Clinical utility | does using it change management or outcomes |
| **R4** | Regulatory | FDA PMA/510(k)/De Novo, Health Canada licence, or declared LDT |
| **R5** | Reimbursement & adoption | coverage decision, guideline inclusion, HTA, funding |

**CRITICAL two-mode rule, proven empirically:**
- **R1–R3 are EXTRACTED from text** (congress abstracts, papers, trial records).
- **R4–R5 are ASSERTED BY SOURCE PROVENANCE.** A record in openFDA PMA or Health Canada
  MDALL *is* R4. A record from Ontario Health HTA *is* R5. No text classification.
- Why: R4/R5 vocabulary inside research abstracts is ~85% false positive. "FDA-approved"
  in an abstract nearly always describes the *therapy*, not the assay. "uptake" meant
  *Fc-mediated cellular uptake*. "CMS" meant *screening eligibility criteria*. "NCCN" meant
  *panel naming*.
- **Store the evidence rung and the document rung separately.** An HTA recommendation is an
  R5 document carrying R2 evidence. Both are true and both are useful.

**Entity attribution is the binding constraint.** Only **5–14%** of rung-classified
abstracts name an identifiable assay product. ~75% describe a method generically
("a methylation-based classifier") with no product to attach to. Therefore:

- **The primary entity on the portfolio view is the MODALITY CLASS, not the product.**
- Product-level trails are a **second layer**, available for ~21 named assays only.
- Radial position = **mean rung**. The trail over time = **the rung-mix shift**, NOT
  activity volume. Volume-sizing renders emerging modalities invisible (fragmentomics has
  n=5 but a clear maturing signal).

---

## 3. The data — REAL, already extracted

`prototype_data.json` sits beside this brief. Everything in it traces to a live pull from
Crossref (ASCO 2023/24/25 + AACR 2025 abstracts), ClinicalTrials.gov v2, openFDA
device/pma, Health Canada MDALL, and the Ontario Health HTA recommendation PDF. **Do not
invent data. Do not add placeholder assays or fabricated percentages.** If a field is null,
the UI must show the "not reported" state — that is a feature, not a gap to paper over.

Top-level keys and counts:

- `meta` — provenance, corpus sizes, ladder definitions, the attribution caveat
- `evidence` (859) — congress abstracts with: `rung`, `rungs[]`, `entities[]`, `perf[]`
  (extracted performance strings), `n`, `sample`, `use`, `nct`, `quote`, `url` (DOI link),
  `src` ("ASCO 2025" etc.), `year`
- `regulatory` (25) — openFDA PMA: `tradeName`, `applicant`, `date`, `panel`,
  `supplementKind` ("administrative" | "clinical / indication"), `quote`, `url`
- `mdall` (56) — Health Canada: `tradeName`, `applicant`, `country`, `date`, `riskClass`,
  `licenceType`
- `reimbursement` (1) — the Ontario Health record. Fully populated: sensitivity 68%,
  specificity 86%, PPV 89%, NPV 61%, GRADE Moderate, recommended for public funding as a
  **triage test**, with two verbatim quotes and the PDF link
- `trials` (18) — ClinicalTrials.gov: `status`, `studyType`, `phase`, `n`, `sponsor`,
  `start`, `rung`, `entities[]`, `url`
- `classes` (9) — modality classes with per-year `{n, R1, R2, R3, mean}` for 2023/2024/2025,
  plus `meanRung`, `delta`, `trend` ("maturing" | "stable" | "regressing")
- `assays` (21) — named products with `trail` (rung per congress), `rungsSeen[]`, `maxRung`,
  `regUS[]`, `regCA[]`, `applicants[]`, `headlinePerf`, `headlineQuote`, `headlineSrc`,
  `headlineId`, `evidenceCount`, `trialCount`

Real examples you can rely on:
- **PD-L1 IHC 22C3 pharmDx** — R1–R4, trail R3/R2/R2/R2, FDA P150013 (+3 supplements),
  3 Health Canada licences, Agilent
- **cobas EGFR Mutation Test v2** — R3 + R4, concordance 77.7%, FDA P120019, Roche, HC licensed
- **Idylla EGFR** — R2 + R4, sensitivity 73.7%, Biocartis, **Health Canada licensed but no
  FDA PMA** (a genuinely interesting asymmetry worth surfacing)
- **cfDNA methylation** — mean rung 1.90 → 2.27, **maturing**, R1 share 30%→0%
- **AI / digital pathology** — pinned at **100% R2 across all three years**, mean 2.00,
  stable. Plenty of clinical validation, zero clinical utility evidence. This is a real
  adoption bottleneck and should be visible.
- **PD-L1 IHC** — mean 2.13 → 2.11, stable. A mature, established technology, correctly flat.

Known data-quality facts to surface honestly in the UI, not hide:
- openFDA PMA supplements: **12 of 25 are administrative** ("relocation of the quality
  control laboratories", "modification of manufacturing equipment", "replacement of the
  fermenters", "reduce environmental monitoring testing"). The UI must let the user filter
  these out, and must not count them as approval events.
  **Compute this figure at runtime from `supplementKind`; never hard-code it.** An earlier
  draft of this brief said 11, from a narrower classifier. The shipped data says 12.
- Health Canada MDALL does naive substring matching: `device_name=MET` returns 3,166
  records, mostly metatarsal bone plates. Gene-symbol search needs an allowlist.
- **FoundationOne, Guardant, Oncotype and Signatera have ZERO active Canadian device
  licences** — they run as lab-developed tests. Absence in MDALL does **not** mean
  unavailable in Canada. The UI must say this where it would otherwise mislead.
- Per-class-per-year n is 2–30. Trends are directional, not statistically robust. Say so.
- Numeric performance parsing, measured on the shipped data: **210** records carry at least
  one numeric claim; **58** carry BOTH a sensitivity and a specificity figure parsed strictly
  from `perf[]` (stored in `claims.sens` / `claims.spec`); widening the window and including
  `quote` reaches **69**. Use the strict 58 for the threshold filter. **34** records carry
  `claimAmbiguous: true` because more than one distinct sensitivity figure appears; never
  quote a single number from those without showing the verbatim string beside it.
- `evidence[]` has NO tumour field. The corpus is lung-only, so tumour type is constant and
  **cannot be an axis**. Use the pre-computed `gapMaps.useBySample` and `gapMaps.useByRung`.
- `workedExamples[]` holds four real pre-computed intended uses: the deep-but-thin case
  (92 matching, 4 with both figures, 43 at clinical utility), the rich case (94 matching,
  24 with both), the zero-performance case (40 matching, 0 with any numeric figure, which
  must route the user to the regulatory layer) and the true empty case (0 matching).
  These are the landing screen's worked examples.
- `findings[]` holds four verified headline findings. The strongest: **all 10 liquid-biopsy
  products have zero regulatory anchor in either jurisdiction, while 9 of 10 tissue
  companion diagnostics have one.** `assays[].family` carries the liquid/tissue split.
- `reviewQueue[]` holds 10 real human-in-the-loop items across four kinds: field-update (3),
  entity-attribution (3, deliberately low confidence, the 87% problem), congress-capture (2,
  the poster-photo path) and declined (2, administrative supplements the agent refuses to
  treat as approval events).
- `findings[]`, `reviewQueue[]`, `gapMaps`, `workedExamples[]` and `claims` were all added
  AFTER the design panel ran. Wire them in. They are not optional decoration.

---

## 4. Visual system — NON-NEGOTIABLE (validated, do not re-pick colours)

Define these as CSS custom properties and reference by role. Both modes required.

```
/* light */                          /* dark */
--surface-1:      #fcfcfb;           #1a1a19
--page:           #f9f9f7;           #0d0d0d
--text-primary:   #0b0b0b;           #f0efec
--text-secondary: #52514e;           #c3c2b7
--text-muted:     #898781;           #898781
--grid:           #e1e0d9;           #2c2c2a
--axis:           #c3c2b7;           #383835
--border:         rgba(11,11,11,.10);rgba(255,255,255,.10)
```

**Ladder ramp (ORDINAL, one hue, validator-PASSED in both modes — use exactly these):**
```
light:  R1 #86b6ef  R2 #5598e7  R3 #2a78d6  R4 #1c5cab  R5 #104281
dark:   R1 #cde2fb  R2 #9ec5f4  R3 #5598e7  R4 #2a78d6  R5 #184f95
```
A 3-segment stack (R1/R2/R3) uses light `#86b6ef,#2a78d6,#184f95`, dark
`#9ec5f4,#3987e5,#184f95` — also validator-PASSED.

**Status palette (fixed, never themed, always with icon + text label — never colour alone):**
`good #0ca30c` · `warning #fab219` · `serious #ec835a` · `critical #d03b3b`

**Hard rules from the dataviz method:**
- The ladder is **ordinal magnitude → one blue hue, light→dark**. Never a rainbow, never a
  categorical hue per rung.
- **No dual-axis charts, ever.** Two measures of different scale → two charts.
- Colour follows the entity, never its rank. Filtering must not repaint survivors.
- Categorical hues only if genuinely needed, in fixed order, never cycled. Scatter/radar
  forms cap at **3 categorical series** — so on the radar, do **not** colour dots by
  modality type. Colour by rung (sequential) and **direct-label every dot**.
- Thin marks; 2px lines; ≥8px markers; 4px rounded data-ends anchored to baseline; 2px
  surface gap between stacked segments; recessive grid and axes.
- A legend is present for ≥2 series (none for a single series — the title names it); ≤4
  series are also direct-labelled so identity is never colour-alone.
- **Hover layer by default**: crosshair+tooltip on line/area, per-mark tooltip on
  bar/dot/cell. Hit targets larger than the mark. Filters in one row above the charts.
- A **table view must exist** for every chart.
- Text wears text tokens, never the series colour.
- Typeface: `system-ui, -apple-system, "Segoe UI", sans-serif` throughout, including large
  numbers. `font-variant-numeric: tabular-nums` only in aligned columns (tables, axis ticks).
- Selective direct labels — never a number on every point.

---

## 5. Build constraints

- **Plain HTML + CSS + vanilla JS. No build step, no npm, no CDN, no frameworks.** Must open
  by double-clicking the file.
- `fetch()` of a local JSON file fails under `file://`. So the data must be **inlined as a
  JS object literal** in a shared `data.js` loaded with a plain `<script>` tag.
- Multi-page prototype: real `<a href>` navigation between sibling `.html` files in the same
  directory, sharing `app.css` and `data.js`.
- It is a **prototype**: interactions that matter (filter, sort, select, open a record,
  switch chart/table view, theme toggle) must genuinely work in JS. Non-core affordances may
  be visually present but inert — if inert, mark them so a reviewer is not misled.
- Accessibility: keyboard-reachable controls, visible focus rings, `aria-label` on icon-only
  controls, semantic `<table>` for tables, `prefers-color-scheme` honoured plus a manual
  theme toggle that wins both ways.
- No em-dashes in UI copy. British spelling in prose ("tumour", "licence", "organisation").

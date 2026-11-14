# BUILD SPEC — Oncology Diagnostics Intelligence prototype

Read `BRIEF.md` first (users, ladder, data, visual rules). This file is the agreed design.
It is the synthesis of three independent IA proposals plus a judging pass. Follow it exactly;
where it is silent, follow the brief; where both are silent, choose the simpler thing.

**Winning skeleton: task-first** (the intended use is a saved object that resolves into one
cross-record table). Grafted in: the evidence-trust claim ledger and disclosure patterns, and
the portfolio radar and white-space maps.

---

## 0. What is already built — do not rebuild, do not restyle

These exist in `/tmp/dx-prototype/` and are verified by render test:

| File | What it gives you |
|---|---|
| `app.css` | All tokens, light + dark, and every component primitive: `.panel`, `.filter-row`, `.field`, `.btn`, `.toggle`, `.rung` chips, `.rung-scale`, `.status`, `.tag`, `.table-wrap`/`table.data`, `.nd` (not-reported), `.empty-state`, `.tile`, `.ev-card`, `.quote`, `.fieldlist`, `.mixbar`, `.hm`, `.legend`, `.ramp-legend`, `.tip`, `.note`, `.proto-flag`, `.app-header`, `.app-nav`, `footer.app-foot` |
| `charts.js` | `DXChart.ladderRadar`, `.rungMix`, `.assayTrail`, `.heatmap`, `.table`, `.viewSwitch`, `.themeToggle`, `.bindTip`, `.rungColour` |
| `data.js` | `window.DX` — the real dataset |

**Never** write a new colour value. **Never** write a second tooltip or theme implementation.
If a primitive is missing, add it to `app.css` with a comment saying which screen needed it.

Load order on every page:
```html
<link rel="stylesheet" href="app.css">
...
<script src="data.js"></script>
<script src="charts.js"></script>
<script>/* page script */</script>
```

## 1. Shared chrome — byte-identical on all six pages

```html
<a class="skip-link" href="#main">Skip to content</a>
<header class="app-header">
  <div class="app-header-top">
    <span class="app-mark"><span class="dot"></span>Oncology Diagnostics Intelligence</span>
    <span class="app-scope">Lung cancer · <span id="scopeFresh"></span></span>
    <span class="app-header-right">
      <span class="proto-flag">Prototype</span>
      <button class="btn btn-sm" id="themeBtn">Dark</button>
    </span>
  </div>
  <nav class="app-nav" aria-label="Main">
    <a href="index.html">Intended uses</a>
    <a href="compare.html">Compare</a>
    <a href="portfolio.html">Landscape</a>
    <a href="anchors.html">Regulatory &amp; funding</a>
    <a href="review.html">Review queue <span id="navQ"></span></a>
  </nav>
</header>
<main id="main"> ... </main>
```
- Set `aria-current="page"` on the current tab.
- `DXChart.themeToggle(document.getElementById('themeBtn'))` on every page.
- `#scopeFresh` = computed: `DX.meta.corpus` summed + " evidence records · sources last pulled Nov 2026".
- `#navQ` = count of `DX.reviewQueue` items not yet actioned in this session.
- Footer on every page, with the provenance line and the prototype caveat.

## 2. Screens

### 2.1 `index.html` — Intended use console (landing)

Purpose: Diana names an intended use; she sees the honest denominator before any table; a
cold arrival (David) learns what this is from worked examples.

1. **Page head.** H1 "Intended uses". One line: what the database covers and how current it is.
2. **Saved intended uses** (`.panel`). List from `localStorage['dx.intendedUses']`. Each row:
   name, facet summary, floors, last-run hit count, "Open in Compare" link that round-trips
   the definition through the query string. Seed with the four `DX.workedExamples` on first
   visit so the screen is never empty. Delete per row.
3. **Define an intended use** (`.panel`, the primary action). Fields:
   - Clinical use (select, from the four distinct `evidence[].use` values + "any")
   - Sample material (select: blood / plasma, tissue, urine, not stated, any) — **keep urine
     selectable even though it yields zero records**; it triggers the designed empty state
   - Minimum evidence rung (select: any / R1+ / R2+ / R3) — this is Ester's
     analytically-versus-clinically-validated criterion, named as such in the help text
   - Minimum sensitivity, minimum specificity (number inputs, 0–100, blank = off)
   - Minimum cohort size (number, blank = off), using `evidence[].nNum`
   - A visibly **disabled** fieldset "Operational specs" containing a disabled
     "required plasma volume (mL)" input, with the reason printed inline: *"No indexed source
     reports sample volume. This stays on screen because it is a stated requirement; it is
     routed into the vendor question list instead."* Use `.proto-flag` to mark it inert.
   - Name field + "Save and run" (`.btn-primary`), "Run without saving" (`.btn`).
4. **Coverage pre-flight** (`.grid-4` of `.tile`), recomputed live on every field change,
   BEFORE the user navigates. Five tiles:
   `matching records` · `report any performance` · `sensitivity as a number` ·
   `specificity as a number` · `both as numbers`.
   Under them, one `.note`: when `both` is under 10, say so plainly — *"A threshold query will
   rank N records. The wider evidence base is M records; rank on clinical utility and cohort
   size instead."* This is the screen's whole reason for existing: a small honest answer must
   read as rigour, not as a broken app.
5. **Worked examples** (`.grid-2`). One card per `DX.workedExamples` entry: label, note, the
   real counts as a small inline table, the `route` sentence, and a button that loads those
   facets into the form. WE-C and WE-D are the instructive ones; do not hide them.
6. **Verified findings** (`.panel`). One row per `DX.findings`: `.status` chip from
   `severity` (icon + text label, never colour alone), headline, detail, and `evidence` as
   the provenance line.

### 2.2 `compare.html` — Cross-record comparison

Purpose: the one table. Diana must never open a record to read a number.

1. **Filter row** (`.filter-row`), initialised from the query string. Same facets as the
   console plus: source (ASCO 2023/2024/2025, AACR 2025, any), "named products only"
   checkbox, "hide ambiguous claims" checkbox. "Reset", and "Save as intended use".
2. **Result summary line**: "N records · M with both figures · K attributable to a product".
3. **The comparison table** (`table.data` inside `.table-wrap`). Columns:
   `Record` (title, truncated, links to `record.html?id=`) · `Product` (or the
   `.nd` state "no product named") · `Rung` (`.rung` chip) · `Sensitivity` (num) ·
   `Specificity` (num) · `PPV` (num) · `NPV` (num) · `AUC` (num) · `Cohort n` (num) ·
   `Material` · `Clinical use` · `Source`.
   - Numeric columns from `claims` / `nNum`, right-aligned, `tabular-nums`, **sortable**
     (click header, `aria-sort`, blanks always sort last regardless of direction).
   - Missing values use `<span class="nd" title="...">not reported</span>`. Never 0, never "—"
     alone, never an invented value.
   - `claimAmbiguous` rows show a `.status status-warning` icon beside the figure.
   - **In-row expansion**: a chevron button per row toggles a detail `<tr>` showing the
     verbatim `perf[]` strings and the `quote`, with the resolvable `url`. This is the literal
     fix for "I literally have to go into each of them" — traceability without navigation.
4. **Sensitivity against specificity scatter**, chart/table toggled with `DXChart.viewSwitch`.
   Only the records with both figures. One series, so no legend box; dots coloured by rung
   (sequential), direct-label only the named products. **Two axes of the same unit (%) is
   fine; this is not a dual-scale chart.** Hover gives title + both figures + n.
   If fewer than 3 points, hide the chart and say why.
5. **Shortlist tray**, capped at 3 with a swap prompt on the fourth, persisted to
   localStorage. Each entry: product or record, the figures, and the rung. "Open vendor brief".
6. **No-data state** (`.empty-state`): when the result is zero, recompute **which single
   filter caused it** by relaxing each in turn, and offer that specific relaxation with its
   real boundary value. Never show a nearest-match number as if it answered the query.

### 2.3 `portfolio.html` — Modality landscape

1. **Scope strip** (`.grid-4` tiles): classes tracked, evidence records classified, mean rung
   across classes, classes maturing.
2. **Ladder radar**: `DXChart.ladderRadar(host, DX.classes, {onSelect})`. Chart/table toggle
   with the radar-as-table (class, mean 2023, mean 2025, n 2025, trend).
3. **Rung mix**: `DXChart.rungMix(host, DX.classes, year)` with a year selector 2023/2024/2025.
4. **Movement table**: class, mean 2023, mean 2024, mean 2025, delta, trend `.status` chip, n.
   Sortable by delta. This is where "maturing" is auditable rather than asserted.
5. **White space**: two heatmaps via `DXChart.heatmap` from `DX.gapMaps.useByRung` and
   `DX.gapMaps.useBySample`. Every cell numbered. Keep the "not stated" column as a real
   column. Caption must state that tumour type cannot be an axis because the corpus is lung-only.
6. **Findings callouts** for F2 (AI pathology pinned at R2) and F3 (cfDNA methylation maturing).
7. **Sample-size honesty band** (`.note note-caveat`): print the real min and max per-class-year
   n computed from the data, and say direction is interpretable but magnitude is not.

### 2.4 `anchors.html` — Regulatory and funding

1. **Tiles**: FDA PMA records, of which administrative (**computed from `supplementKind`,
   not hard-coded**), Health Canada licences, reimbursement precedents (1).
2. **Finding F1 as the headline** (`.note` with `.status status-serious`): the liquid-biopsy
   versus tissue-CDx asymmetry, with the counts computed at runtime from `assays[].family`
   and the `regUS`/`regCA` arrays.
3. **Jurisdiction asymmetry table**: product, family, US anchor, Canada anchor, max rung.
   Where both are absent for a liquid-biopsy product, the cell reads
   `<span class="nd" title="Runs as a laboratory-developed test; absence from a regulator's database does not mean unavailable">LDT</span>`.
4. **Approval events**: PMA records with a toggle "hide administrative supplements"
   (default ON). Each row: PMA id, trade name, applicant, date, panel, and the verbatim
   `quote`. Administrative rows, when shown, are visually recessive and labelled.
5. **Health Canada panel**: licence list with risk class and type, plus a `.note` on the
   substring-matching trap (`device_name=MET` returns 3,166 records, mostly bone plates).
6. **The single R5 precedent** (`.ev-card`): the full Ontario Health record with both
   verbatim quotes, the rung pair (R5 document / R2 evidence) and the PDF link.
7. **Trials lane**: `DX.trials` table — NCT, title, status, study type, phase, n, sponsor.
   Research and trials is a stated must-have, so this surface is required. Add a tile showing
   the phase-futility counts computed from the data (how many have no usable phase, how many
   no extractable rung, how many observational); this is the evidence for using the ladder
   instead of phase.

### 2.5 `review.html` — Review queue (human in the loop)

Purpose: Diana's agent-assisted entry, and the rule that no record changes without a human.

1. **Tiles**: pending, by kind (4 kinds), mean confidence.
2. **Filter row**: kind, minimum confidence, assay.
3. **Queue list**, one `.ev-card` per `DX.reviewQueue` item:
   - header: kind badge, confidence as a number plus a `.rung-scale`-style meter, assay or
     "no product named"
   - `proposal`, then the `agentNote` as the agent's stated reasoning
   - the verbatim `quote` in a `.quote` block with the resolvable `url` and `sourceId`
   - proposed rung chip and cohort size
   - actions: **Approve**, **Edit**, **Reject** — these must genuinely work in JS, moving the
     item to an actioned state, updating the tiles and `#navQ`, and appending to a visible
     session audit list. No persistence beyond the session is needed.
   - `entity-attribution` items (confidence 0.38) must read as correctly-routed-to-a-human,
     not as a failure. Add a line explaining this is the 87% case.
   - `declined` items are shown as already-declined with the rule that declined them, and
     cannot be approved.
4. A `.note note-proto` stating the ingestion pipeline and OCR are not built; these ten items
   are real records drawn from the indexed corpus, standing in for what the agent would propose.

### 2.6 `record.html` — Source record

Universal resolver: `?id=` matches against `evidence[].id` (DOI), `trials[].id` (NCT),
`regulatory[].id` (PMA), `mdall[].id`, `reimbursement[].id`. If no id, show a short index
of example records of each type.

1. **Header**: title, source, year, and **two rung chips** — "evidence rung" and, where the
   source type asserts one, "document rung" — with a one-line explanation of the difference.
2. **Parsed against verbatim**: a two-column table, parsed `claims` value beside the exact
   `perf[]` string it came from. Where they could disagree, say so.
3. **Verbatim quote** in `.quote` with the resolvable link.
4. **"Why is this R2?"** disclosure (`<details>`): list the ladder vocabulary that fired for
   each rung in `rungs[]`, and state plainly that R4 and R5 are never inferred from text.
5. **Fields** (`.fieldlist`): material, clinical use, cohort size, NCT if present, entities.
6. **Related**: other records sharing an entity or clinical use, each with its figures inline
   so the user still never has to open one to compare.
7. **Back to compare** preserving the previous filter state.

## 2.7 AMENDMENT — verified versus machine-read claims (supersedes earlier wording)

A hand audit of 20 parsed sensitivity/specificity pairs found **10 correct, 7 wrong and 3 that
should have been refused**: metric inversions, figures stolen from an adjacent metric
(accuracy, precision, PPV), confidence-interval bounds leaking in as values, and range
statements split instead of refused. Measured machine accuracy on paired figures is **50%**.

This is load-bearing for the design, not a footnote. A numeric threshold filter over
machine-read values would mislead Diana about half the time, which is the exact failure the
app exists to prevent. Therefore:

1. Every record carries `claimVerified` (boolean). 20 records are verified; 7 of those carry
   `claimMachine` (what the parser said) alongside the corrected `claims`, and 3 carry
   `claimRefused`. The rest are machine-read.
2. **Sensitivity and specificity threshold filters rank ONLY on `claimVerified: true`
   records.** There are 16 such records with a paired figure. The filter must say so.
3. Machine-read values still appear in the comparison table, but:
   - marked with a `.status status-warning` chip reading "machine-read" (icon + label),
   - never sortable into the top of a threshold-filtered result,
   - always accompanied by the verbatim sentence via the in-row expansion,
   - with a "Send to review" action that pushes the record into the review queue.
4. Where `claimCorrected` is true, the record detail shows **both** the machine value and the
   corrected value, side by side, labelled. This is the most persuasive thing in the whole
   prototype: it shows the tool catching itself.
5. The coverage pre-flight gains a sixth tile: `verified pairs` beside `both as numbers`.
6. `DX.meta.claimAccuracy` holds the audit result. Print it, do not restate it from memory.
7. Finding F4 is now `severity: critical` and belongs above the fold on `index.html`.

## 3. Rules that apply to every screen

1. **Compute every figure at runtime from `window.DX`.** No hard-coded counts anywhere, in
   copy or in markup. If a number appears in a sentence, it is interpolated.
2. **Never invent a value.** Absent is `.nd` with a `title` explaining why it is absent.
3. **No record-at-a-time in the primary journey.** Any comparison surface shows figures inline.
4. Every chart has a table view. Every chart has a hover layer. No chart has two scales.
5. Identity never by colour alone: `.status` always carries an icon and a text label; series
   are direct-labelled or legended.
6. Keyboard reachable, visible focus, `aria-sort` on sortable headers, `aria-live` on result
   counts, semantic `<table>`, `<caption>` on every table.
7. Inert affordances carry `.proto-flag` and a printed reason. Nothing fake looks real.
8. British spelling in prose. No em-dashes in UI copy.
9. Do not edit `data.js`. Do not edit another screen's file.

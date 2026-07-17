# Parsing the Senate's Money: A Socio-Technical Audit

*An assessment of the `senate_parser` rebuild — its design choices, constraints,
failures, and what they mean for anyone using the data it produces.*

This document is about the **approach**, not the spending. It is written for a
future maintainer, a skeptical data editor, or anyone deciding how much to
trust a CSV this pipeline produced. It is deliberately more critical than a
README: the point is to record not just what works, but what almost didn't,
what only works because a human keeps checking, and where the design quietly
transfers risk to the reader.

---

## 1. The problem, honestly stated

The U.S. Senate publishes its detailed expenditures as ~2,000–3,000-page PDFs,
twice a year, generated from at least two different report-generation systems
across fifteen years. The documents are *printed reports*, not data: columns
are visual conventions, not fields; totals are typeset, not computed for you;
and the layout changes between eras, between offices within one document, and
occasionally mid-word ("TRAVEL AND TRANSP" / "ORTATION OF PERSONS").

The central design realization — and the single most consequential decision —
was that **the reports validate themselves**. Every office/account block prints
its own subtotals. If parsed rows don't sum to the printed figure, something is
wrong: either the parse or the source. Everything else in this project is
scaffolding around that one idea.

The second realization came later and was more uncomfortable: **sometimes the
source's own arithmetic is wrong**, and a pipeline that treats printed totals
as ground truth will imprison correct data forever. Distinguishing "we
misparsed" from "the Senate's total disagrees with the Senate's own itemization"
became the hardest and most valuable part of the system.

## 2. Architecture and the choices that mattered

### Coordinates, not text layout

The original pipeline used `pdftotext -layout` and regexes. That failed subtly:
reflowed text desynchronizes columns, so an amount could silently attach to the
wrong row — wrong *data* that looks plausible. The rebuild extracts word-level
x/y coordinates (via `natural_pdf`/`pypdfium2`) and reconstructs the table
geometrically. This is slower and much more code, but it fails *loudly*
(reconciliation catches misassignment) instead of quietly.

**Judgment:** this was the right call, and nothing in the project works without
it. But it bought precision at the cost of fragility to layout drift — every
new template era has required real calibration work (days, not hours), and
there is no reason to believe the 112th–113th or 115th–116th eras will be free.

### Blocks as the unit of everything

Pages are segmented into office/account blocks at each banner page before any
row parsing. Rows never cross blocks; subtotals belong to exactly one block;
bioguide attribution happens per block. This made cross-page continuations and
senator identification tractable, and it gave reconciliation a natural scope.

### Reconciliation as a gate, not a report

A segment whose rows don't sum to its printed subtotal doesn't just get logged
— its rows are **quarantined** out of the published CSV. This is the pipeline's
most opinionated stance: *wrong-but-plausible data is worse than missing data*,
because a journalist can see a gap but cannot see a silently corrupted amount.

The gate's granularity changed over the project's life, and each change was a
usability decision:

1. **Whole-block quarantine** (initial): one bad segment held back everything
   in the block. Safe, but at its worst it held $187.6M across 7 reports —
   including ~5,100 rows that were individually perfect ("innocent bystanders").
   A journalist totaling an affected senator's spending would silently
   undercount and never know why.
2. **Per-segment quarantine**: rows are routed by their own segment's outcome.
3. **Second-opinion release** (see §3): even failing segments publish if an
   independent check proves the rows faithful.

**Judgment:** the progression was correct but the initial conservatism lasted
too long — the $187.6M sat quarantined through multiple published iterations
before anyone interrogated *why* segments were failing. The answer (below)
turned out to be mostly "they weren't."

### Per-row validation status: pushing the verdict to the reader

Every published row carries `validation_status` (`ok` / `warn` / `unchecked` /
`source_mismatch`) and `category` (its covering printed subtotal). This is the
project's most important interface decision: instead of a binary
published/withheld split, consumers can filter to their own risk tolerance.

It is also a quiet **transfer of responsibility**. A `source_mismatch` row is a
faithful transcription of a line whose printed category total disagrees with
its own itemization — but that nuance lives in a README most users will not
read. Anyone who sums a `source_mismatch` category and compares against the
Senate's printed figure will get a discrepancy this pipeline knew about and
tagged, but could not make impossible. The status taxonomy has also grown to
nine-plus values across artifacts (`ok`, `warn`, `fail`, `unchecked`,
`source_mismatch`, `no_records`, `zero_records`, `component`,
`banner_missing`, plus verdicts `cross_year` / `inconclusive` /
`parser_suspect`). Each one earned its existence, but collectively they demand
real study to use correctly. That's a cost, and it's paid by the reader.

## 3. The validation stack (defense in depth, honestly assessed)

The system has six semi-independent layers. Their *independence* is the point —
each catches a class of failure the others structurally cannot.

| Layer | Catches | Cannot catch |
|---|---|---|
| Printed-subtotal reconciliation | dropped/duplicated/misassigned **amounts** | wrong payee/description with correct amount; source-side errors |
| Second-opinion re-sum (naive, classifier-free) | whether a failing segment is our bug or the source's | anything on passing segments; shares column geometry with parser |
| Banner cross-check (advisory) | whole-block gaps incl. trailing unchecked rows | anything, bindingly — it never gates |
| Golden-page fixture tests (96) | regressions of every previously-found bug | bugs never seen before |
| Full-report snapshots (7 reports, run twice for determinism) | any drift in counts/dollars/statuses | field-level corruption that conserves totals |
| Manual sampling (`verify_sample.py`) | field-level correctness a human can see | anything at scale; only samples 12–30 rows/run |

Two hard-won lessons about this stack:

**A "second opinion" is only worth having if it can disagree.** The re-sum
deliberately bypasses the record classifier (the historically buggy layer) and
uses only raw column geometry. When it sides with the parser against the
printed total → `source_mismatch`, publish. When it sides with the printed
total against the parser → `parser_suspect`, quarantine and alarm. That second
outcome has now fired twice, and **both times it was a real parser bug** (the
tight-group dropped-amount bug, §5). The design earned its keep. But note the
honest limitation: it shares column calibration with the parser, so a
calibration error fools both. It is independent of the *classifier*, not of
the *geometry*.

**Reconciliation is blind to everything that isn't a dollar.** The most
instructive near-miss of the project (§5, the wrapped-payee regression) passed
reconciliation perfectly — every total conserved — while mangling payee names
and inventing amount-less fragments. It was caught only because a column-level
diff against prior output was standard practice. Green reconciliation is
necessary, nowhere near sufficient.

## 4. Constraints that shaped everything

- **The source is the constraint.** Fiscal-year-boundary reports list travel
  rows they don't count; payroll adjustments are counted in one funding year
  but itemized in a *sibling year's* roster (verified penny-exact:
  ±$102,388.98 across Cantwell's FY2015/FY2016 blocks); some segments' printed
  totals simply don't match their own printed rows (Blackburn's intern roster:
  rows sum $24,774.31, printed total $24,745.43, banner agrees with the
  total). No amount of parsing skill fixes these; the design response was
  *classification and disclosure* rather than correction.
- **Era heterogeneity.** The 112th–114th template differs not just in geometry
  (two header layouts inside one document; page labels printed twice, once as
  "B -1,000" *inside the amount column*) but in **semantics**: all subtotals
  print at the end of a block, and payroll category lines *partition* the
  roster rather than bounding row runs. The pipeline ended up with two
  calibration modes and two reconciliation modes, selected by congress number.
  That's a fork in the code that every future change must consider twice.
- **Cross-document invisibility.** ~$150M across the three 114th reports sits
  quarantined as `inconclusive` largely because the offsetting entries live in
  the *adjacent report's* PDF. A single-document pipeline structurally cannot
  verify them. This is the largest known gap, and it is a scope decision, not
  an accident.
- **Tooling bugs are data bugs.** `natural_pdf` reports flatly wrong page
  dimensions for every content page of exactly one report (117sdoc8), which
  corrupted highlight rendering in the QA tool while leaving word coordinates
  correct. The fix (cross-check against `pypdfium2`, refuse to highlight when
  they disagree) is emblematic of the project's posture: never guess, degrade
  loudly.

## 5. Failures — the instructive ones

Recorded here because each one produced a rule.

1. **The $187.6M false alarm.** 868 of 924 "failing" checks were the
   reconciliation logic forgetting that printed lump-sum categories (PERSONNEL
   BENEFITS etc.) legitimately have no rows. 830 of them were explained *to
   the penny* by that one omission. The lesson: before building recovery
   machinery for failures, prove the failures are real. A day of arithmetic
   on the reconciliation report reframed an entire milestone.
2. **The wrapped-payee regression (worst near-miss).** A fix for merged
   records ("a doc number starts a new record") was validated by tests and by
   reconciliation — and shredded legitimate records whose doc number prints on
   their *second* visual line, turning "GOVERNMENT RETIREMENT & BENEFITS,
   INC." into a payee named "INC." across all seven modern reports. Totals
   conserved; snapshots caught count drift; only a field-level diff revealed
   the mangling. The corrected invariant ("at most one doc number per record —
   split at the *second*") is now a fixture test. Rule: every invariant needs
   its counterexample hunted before shipping, and field-level diffs are not
   optional ceremony.
3. **The first 114th-era run published 10% and quarantined 90%.** Eight full
   iterations were needed to get to ~80–90% published. Each iteration's
   failure was diagnosed to a penny-exact cause before code changed — which
   is the only reason eight iterations converged instead of oscillating. The
   criticism worth recording: the *scale* of era difference was
   underestimated at the start ("give me the commands" became a multi-day
   calibration project), and that misestimate should inform planning for the
   112th–113th and 115th–116th eras.
4. **The dropped-second-amount bug.** Old-template multi-line expenses pack
   ~3.7pt apart — inside the row-grouping threshold — and the classifier's
   "first amount wins" silently discarded every later dollar line in a merged
   group. Caught not by tests, not by reconciliation noticing at the time,
   but by the second-opinion verifier flagging three segments as
   `parser_suspect` (~$600 total). Small money; important precedent — this is
   the failure mode (silent per-row loss) the whole stack was built to
   surface, and it worked.
5. **QA tooling had its own bug tail.** The sampling tool's empty-stratum
   detection was dead code (derived "all strata" from observed data, which is
   never empty by construction); its highlight padding bled onto neighboring
   rows on tight-packed old pages; its page-label predicate drifted out of
   sync with the production one it deliberately duplicates. Verification code
   deserves verification. It got it — but only because the same adversarial
   standard was applied there as to the pipeline.

## 6. How the choices shape use

For someone actually consuming `senate_data_cleaned.csv`:

- **The modern era (117th Congress on) is the strong claim.** 100% of parsed
  dollars publish; 99.9%+ reconcile to the penny against printed subtotals;
  quarantine files are empty. Cite freely, with the standard caveats.
- **The 114th era is a qualified claim.** 78–90% of dollars publish (mostly
  `ok`, the rest `source_mismatch` with documented semantics); 10–22% sits in
  quarantine, now consisting *entirely* of segments the machinery could
  neither confirm nor refute — dominated by cross-report payroll residuals.
  If you sum an office's payroll in these years, you are summing the
  *published subset*, and the quarantine file is where the rest of the story
  lives. This is disclosed, but disclosure in a README is a weak instrument.
- **`source_mismatch` means "trust the rows, not the printed total."** The
  rows are machine-verified transcriptions; the Senate's own category total
  disagrees with its own itemization. Sum the rows yourself and you will match
  the rows — not necessarily the Senate's figure. Both are honestly derived;
  they genuinely differ at the source.
- **Bioguide IDs are maintained, not solved.** Matching runs 96–100% only
  because a hand-curated alias table absorbs formal-vs-nickname mismatches
  (A. MITCHELL MCCONNELL, JR. → Mitch McConnell) and a funding-year-overlap
  rule handles senators whose offices spend after they leave. New name
  variants will appear; `unmatched_senators.csv` is the tripwire, and someone
  has to look at it.
- **Provenance is real.** Every run writes a manifest (PDF SHA-256, git
  commit, page range, tolerances, counts) and every check is in
  `reconciliation_report.csv`. A published number can be traced to its inputs.
  This matters more than any single accuracy claim.

The socio- half of socio-technical: this system assumes a human in the loop.
The maintainer runs commands, reads reconciliation deltas, hand-traces blocks
against the PDF, fills in (or, so far, mostly *doesn't* fill in) the
`reviewer_verdict` column that would turn spot-check images into a citable
audit trail. The strongest regression net — the full-report snapshot suite —
requires local PDFs and ~35 minutes, so **CI runs only the fast fixture suite**;
the deepest checks run only when a person chooses to run them. The design is
honest about this dependence, but it is a dependence.

## 7. Standing debts

- **Cross-report reconciliation** (net payroll residuals across adjacent
  reports) — the single highest-value next milestone; would convert most of
  the remaining ~$150M of `inconclusive` into either releases or real findings.
- **Eras not yet attempted:** 112th–113th (old template, presumably; only
  legacy-pipeline output exists) and 115th–116th (unknown template; not even
  downloaded). Budget for calibration, not just commands.
- **Known-but-unfixed:** payee/date column bleed on some old-template pages
  (fields, not dollars); banner cross-check flags ~a third of blocks
  (meaningful signal drowning in known source behaviors — it needs
  suppression rules or it will be ignored, which is worse than not existing);
  `REPORTS` volume tables duplicated across three files with "keep in sync"
  comments doing load-bearing work.
- **Process debt:** large stretches of this work existed only in an
  uncommitted working tree for days at a time, and the manual-verification
  verdict log remains empty. Both are cheap to fix and expensive to regret.

## 8. What I'd tell the next maintainer

1. **Evidence before code, always.** Every durable fix in this project began
   with a penny-exact hand-trace against the PDF. Every regression began with
   a plausible rule adopted without hunting its counterexample.
2. **Conserve dollars as an invariant.** Published + quarantined must not
   change when routing or tagging changes ($3,061,383,318, to the dollar,
   across the biggest refactor). It is the cheapest powerful check you have.
3. **One bug, one fixture.** The 96-test suite is an inventory of every way
   this source has surprised us. It is the project's real documentation.
4. **Don't trust green.** Reconciliation conserves dollars while fields rot;
   snapshots freeze counts while payees mangle. Diff fields, render pages,
   look with your eyes — the machine checks buy you the *time* to do that on
   what matters, they don't do it for you.
5. **The source is allowed to be wrong.** Build for classification and
   disclosure, not correction. The moment the pipeline "fixes" the Senate's
   arithmetic is the moment its output stops being defensible.

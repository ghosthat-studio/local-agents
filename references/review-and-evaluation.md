# Review, evaluation and human review gates

Read this when an agent's output needs checking — by another model, by measurement, or by a
person — and when you are deciding how a batch of many inputs (files, documents, records) is
analysed.

## Contents
- 1. Independent review, not self-review
- 2. Per-item passes and the cross-item integration pass
- 3. Measuring accuracy: stratify and sample, do not average
- 4. Human review gates: route the uncertain, not everything

## 1. Independent review, not self-review

A model asked to review work it just produced, in the same context, reviews its own reasoning
as well as its output — and it finds that reasoning persuasive, because it is the reasoning
that produced the output. Same-context self-review mostly confirms. It catches typos, not
wrong premises.

Review with a **separate instance**:
- It gets the artifact, the source it was made from, and the criteria — **not** the author's
  reasoning, notes or conversation. The point is a reader who has to be convinced by the work
  itself.
- It reports findings in structure (item, problem, evidence, severity) so they can be checked
  and acted on.
- On local hardware this can be the same model in a fresh context; a different model (or a
  larger one for review only) is better where available, because it does not share the same
  blind spots. Anthropic's own evaluation guidance makes the same point about grading: use a
  different model to grade than the one that generated.

Give the reviewer a bounded job. Told to "find problems", a reviewer assumes there must be some
and argues itself into findings (or, on a small context, runs out of room debating fields that do
not matter). A per-item checklist — "does this field match the source: yes / no / cannot tell,
with the evidence" — over only the fields that matter keeps it honest and inside its budget.

Multi-pass review of one artifact — first for correctness, then for completeness, then against
the source — works better as separate focused passes than as one pass told to check everything,
especially on small-context models where "everything" does not fit.

## 2. Per-item passes and the cross-item integration pass

When the input is many files or documents, there are two different questions:

- **What is in each item** — its facts, defects, fields. Best answered one item at a time:
  every item gets the same depth and its own context, nothing crowds anything else out, and the
  work fits a small context and runs in parallel.
- **How the items relate** — contradictions between documents, a function whose callers assume
  something it does not do, totals that must agree across statements, duplicate records. No
  per-item pass can see these.

So run both:
1. **Per-item pass:** each item alone, producing a structured summary — its facts, its
   interfaces (what it exposes, what it assumes, identifiers, amounts), and its local findings.
2. **Integration pass:** over the per-item summaries (not the raw items), looking only for
   cross-item problems: inconsistencies, broken assumptions, mismatched totals, gaps.
3. **Targeted follow-up:** when the integration pass suspects a problem, it pulls the specific
   items (or excerpts) involved and checks against the source.

When everything fits comfortably in one context and the items are few, a single pass can see
the interactions directly — but attention thins as the context fills, so past a handful of
substantial items the two-pass structure is more reliable, not just more economical. The
per-item summary format is the contract between the passes: design it so the integration pass
has what it needs (interfaces and cross-references), and keep sources so it can go back.

## 3. Measuring accuracy: stratify and sample, do not average

An aggregate accuracy figure hides where the system fails. 97% overall can be 99.5% on clean
typed invoices and 60% on handwritten receipts; if receipts are 5% of volume, the average barely
moves while every receipt is suspect. The same happens across fields (a rarely present field
extracted badly) and over time (a new supplier's layout).

- **Stratify** by what drives difficulty: document type and source, layout or supplier, scan
  quality, language, and **field** — measure accuracy per field, not per document.
- **Sample randomly within each stratum**, and **oversample rare strata** so each has enough
  items to say something (report the sample size and the uncertainty with every figure — 20 of
  20 correct is not the same claim as 2,000 of 2,000).
- **Check against ground truth** made by someone who did not see the model's answer.
- **Keep sampling in production**, including from items the system accepted automatically;
  otherwise the auto-accepted path is never measured, and that is where silent errors live.
- **Calibrate confidence before you use it:** within each stratum, compare the model's stated
  confidence with its measured accuracy. A model that says 0.95 on everything gives a threshold
  nothing to work with; confidence becomes useful only once "0.9" has been shown to mean ~90%
  in that stratum.

## 4. Human review gates: route the uncertain, not everything

A review gate that sends everything to a person turns the automation into a queue: people stop
reading carefully, and the system saves nothing. Route only the items where a person adds
information:

- fields with low confidence — **calibrated** confidence, per section 3
- values extracted from ambiguous, illegible or contradictory sources (two documents disagree;
  the same field appears twice with different values)
- document types or strata whose measured accuracy is below the bar
- fields with more than one reasonable interpretation (an `unclear` enum, an ambiguous date
  format, a policy case the rules do not settle)
- reconciliation mismatches that survived a targeted re-extraction
- validation failures that survived their retries

Plus a small **random sample of auto-accepted items**, so the accepted path stays measured.

Watch the reviewers too. People checking a pre-filled value that is usually right drift into
approving it. Plant a small share of known-wrong items (removed before anything is acted on) to
measure how often reviewers miss errors; for critical fields such as amounts and bank details,
have the reviewer re-key the value rather than confirm it; and cap session length.

Make each routed item quick to decide: show the value, the source excerpt it came from, why it
was routed, and the alternatives. Feed the decisions back: corrections become ground truth for
section 3, and a stratum whose accuracy rises can route less.

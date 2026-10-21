# Structured output, extraction and reconciliation

Read this when an agent returns data that code will use: tool arguments, extracted fields,
tables, ledgers, decisions. Getting valid syntax is a solved problem; getting true content is
not, and the two need different tools.

## Contents
- 1. Force the syntax — it is solvable, so solve it
- 2. Separate thinking from the structured answer when they compete
- 3. Design the schema so the model is never pushed to invent
- 4. Validate meaning in code, and retry only what a retry can fix
- 5. Reconcile tables and ledgers against the source
- 6. A worked example
- 7. Structure does not make it true

## 1. Force the syntax — it is solvable, so solve it

Malformed JSON is not something to catch with parsing and retries; constrain generation so it
cannot happen. Every serious local engine can:

| engine | how |
|---|---|
| Ollama | `"format": <JSON schema>` on `/api/chat` or `/api/generate` |
| vLLM | `response_format: {"type": "json_schema", ...}` (structured outputs / guided decoding); `tool_choice` naming a tool, or `"required"` |
| llama.cpp server | `response_format` with a JSON schema, `json_schema`, or a GBNF `grammar` |
| MLX / LM Studio | JSON-schema structured output where supported; otherwise use llama.cpp |
| Claude API (for comparison) | structured outputs / `strict: true` tools — constrained decoding, documented as guaranteeing schema-valid output |

Two ways to use it:
- **A tool as the output schema.** Define a tool (e.g. `record_invoice`) whose parameters are
  the fields you want, and force the model to call it (`tool_choice` naming it). The arguments
  are the structured result. This fits agents that already use tools.
- **Schema-constrained content.** Pass the schema as the response format. This fits one-shot
  extraction steps.

**Show the model the schema too.** Constrained decoding enforces the shape but does not tell the
model what the fields mean. On Ollama, a model given only `format` spent ~6,800 thinking tokens
guessing the fields and was cut off; describing the fields in the prompt and adding one worked
example answer brought it to ~1,100. Put the schema (or a filled-in example) in the prompt as well
as in the constraint.

Know the limits of each engine's schema support (recursive schemas, `$ref`, numeric and string
constraints are often unsupported or ignored) — test the schema on the engine, and enforce
anything unsupported in code (section 4). With reasoning models, thinking is generated
separately and the constraint applies to the answer (verified on Ollama 0.34 with qwen3.5);
check this on your engine rather than assuming it.

Parsing free text with a regex to find the answer is the thing this replaces.

## 2. Separate thinking from the structured answer when they compete

A tight schema can crowd out reasoning on hard inputs — the model spends its effort fitting the
format. When a task needs real analysis (reading a messy scan, reconciling conflicting sources,
judging a policy case), split it:

1. **Work step:** the model reads, reasons and writes notes freely (or uses tools).
2. **Extraction step:** a separate call, constrained to the schema, turns those notes and the
   source into the structured record.

Reasoning models do the first part inside their thinking, which is often enough; add the
explicit two-step split when output quality drops as the schema grows, or when the work step
itself needs tools.

## 3. Design the schema so the model is never pushed to invent

A schema is an instruction. A required field says "there is always a value here"; when the
source does not have one, the model is being asked to produce one anyway, and it will. Required
fields for data that can be absent are an anti-pattern: they turn a gap into a fabrication.

- **Require only what is always present** — usually the structural containers (the list of
  line items) and the fields the source always has. Make the rest optional or nullable.
- **Make absence expressible.** `"type": ["number", "null"]` with a description saying *null
  when not printed*. Absence is information the downstream step needs.
- **Give enums an escape.** Add `"other"` (with a free-text detail field) and `"unclear"` to
  every classification enum. Without them, an item that fits no category is forced into the
  nearest wrong one, and the error looks confident.
- **Describe every field** — what it is, where it comes from, what to do when it is missing.
  Descriptions are read; they are the cheapest prompt you have.
- **Keep "as printed" and "derived" apart.** `stated_total` (copied from the document) and a
  total you compute are different facts; so are a currency code that is printed and one inferred
  from a `$` sign.
- **Ask for provenance** where it matters: the quote or location each value came from. It makes
  checking cheap (section 4) and hallucination visible.
- **Ask for field-level confidence** only if you will calibrate it (see
  `review-and-evaluation.md`); uncalibrated confidence is decoration.

## 4. Validate meaning in code, and retry only what a retry can fix

Constrained decoding guarantees the shape. Code checks the meaning:

- types and ranges beyond what the engine enforced; formats (dates, ids, currency codes)
- cross-field rules (line total = quantity × unit price; end date after start date)
- references against real state (the account exists, the order belongs to the customer)
- provenance: the quoted text really appears in the source at the cited place

When a check fails, send the **specific** failure back to the model — "line 3: quantity 3 ×
unit price 4.00 = 12.00 but line_total is 14.50" — and let it re-read the source. That works when
the model misread or mis-copied something.

A retry cannot help when the information is not in the source. Retrying then pressures the model
to produce a value, which is how a validation loop manufactures fabrications. Distinguish the two:
if the failure is "missing" or "contradicts itself in the source", record null / `unclear`, attach
the reason, and route it (section 5, and human review in `review-and-evaluation.md`). Bound
retries with a count so a permanent failure surfaces instead of looping.

## 5. Reconcile tables and ledgers against the source

For any table with totals — invoices, statements, ledgers, timesheets — reconcile every level,
in code, and never let the model "fix" a total to make it balance.

1. **Extract lines and printed totals separately.** Each line as printed; each subtotal and the
   grand total as printed.
2. **Recompute in code:** each line (quantity × price = line total), each subtotal (sum of its
   lines), the grand total (subtotals + tax + fees − discounts). For ledgers: opening balance +
   movements = closing balance on every row, and total debits = total credits.
3. **Compare line by line and total by total**, with a tolerance for rounding stated explicitly.
4. **On a mismatch, look for the cause before flagging:**
   - a line missed or read twice (the most common)
   - a sign error (a discount or credit read as positive)
   - tax or fees shown as inclusive vs added
   - a digit misread (OCR: 1/7, 5/6, 0/8), or a column misaligned
   - a total that covers a different set of lines than you think (page subtotals)
   Do a targeted re-extraction of the region involved, with the specific mismatch stated.
5. **If it still does not reconcile, flag it — do not force it.** Record both numbers, the
   difference, the lines involved and the suspected cause, and route it for review. A document
   that genuinely does not add up is a finding; silently adjusting a line to match the total
   destroys it.

## 6. A worked example

Two real runs on qwen3.5:9b via Ollama, schema enforced by `format`, on the invoice *"3 widgets
at $4.00, shipping $2.50, total $14.50"*:

**Run 1 — bare schema, no descriptions.** Perfectly valid JSON, and three semantic errors: the
shipping line dropped, the printed total omitted (an optional field nobody described), and the
currency set to "USD", inferred from the `$`.

**Run 2 — the schema from section 3:** field descriptions, every charge including shipping as a
line, `stated_total` required but nullable, currency only when printed, a `kind` enum with
`other`/`unclear`, and a prompt explaining that the team reconciles every line. The shipping line
and the stated total came through and no currency was invented — but the model put $4.00 as the
widgets' *line total*, left the unit price null and folded the quantity into the description.

Reconciliation caught it immediately: lines sum to 6.50, stated total 14.50, mismatch. The next
step is the targeted re-extraction from section 5 ("the lines sum to 6.50 but the printed total
is 14.50; re-read the widgets line: quantity, unit price and line total as printed"), and if it
still does not balance, a flag with both numbers.

The lesson from both runs: each fix removed one class of error and the model found another.
Schema design reduces fabrication; reconciliation catches what arithmetic can check; the rest is
only found by sampling against ground truth (section 7).

## 7. Structure does not make it true

Constrained decoding, schemas and validation remove whole classes of error, but a field can be
well-formed, pass every check, and still be wrong: a plausible date from the wrong part of the
page, a vendor name from the letterhead of a forwarded document. Those are only found by
measuring against ground truth — stratified, randomly sampled checks — described in
`review-and-evaluation.md`. Plan for them from the start.

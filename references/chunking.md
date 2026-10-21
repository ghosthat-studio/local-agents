# Chunking and rolling history: details and pitfalls

Step 3 of the skill covers the approach: read whole if it fits, let the agent pull, push
chunks only when everything must be covered, and roll history into a state record. This file
has the working detail for when you implement them.

## Counting tokens

- vLLM: `POST /tokenize` with `{"model": ..., "prompt": ...}` → `count`
- llama.cpp: `POST /tokenize` with `{"content": ...}` → `tokens`
- Ollama: send the text with `"options": {"num_predict": 1}` and read `prompt_eval_count`
  (use the same `num_ctx` the agent uses, or the model reloads). This only works for text that
  fits: an over-long text is truncated before counting and reports ~half the context (below).
  Measure a large document in pieces that fit and add them up, or use the model's tokenizer
  offline (`transformers` / `tokenizers` with the model's tokenizer files).

Characters ÷ 4 is a rough English average and fails on code, tables and non-English text.
Measure once per kind of input and keep the ratio if calling the tokenizer each time is costly.

Ollama truncates an over-long prompt silently — and not to the limit. Measured on Ollama 0.34
at `num_ctx 8192`: a 5,685-token prompt went through whole, but every prompt over the limit came
back with `prompt_eval_count` = 4,098, about half the context. So a guard that checks "is the
count at the limit?" never fires. Compare `prompt_eval_count` with the size you measured before
sending; if it is smaller, input was lost. Better still, measure first and never send more than
the budget.

## Letting the agent pull

Tools that work well for small-context agents:
- `outline()` — section titles with ids and sizes, so it can see the shape first
- `search(query)` — matching passages with their section id, a few lines each, capped in
  total length so one search cannot flood the context
- `read(section_id, start, length)` — a bounded range
- `note(text)` — write to a scratchpad that survives history rolls

Return sizes are the thing to bound, not the number of calls: a search that returns 20k
tokens into an 8k context is the failure, not the fifth search.

## Pushing chunks

- **Size** chunks to the input budget from step 3, measured in tokens, leaving room for the
  running notes block.
- **Split** at the largest structural boundary that fits (chapter → section → paragraph →
  sentence). Never split inside a table row, a code block or a sentence.
- **Overlap** by a paragraph or a few lines so a fact that straddles a boundary is seen whole
  at least once.
- **Carry** a running notes block forward: established facts with their source, open
  questions, identifiers seen, and anything that *changes* an earlier fact (later sections
  often supersede earlier ones — keep the latest and say so).
- **Reduce** over the extracted notes only. If the notes themselves outgrow the budget,
  reduce hierarchically (notes of notes), keeping sources.
- **Say what was not found.** "Not in the document" is an answer; make the extraction step
  record absence explicitly so the reduce step does not invent it.

## Rolling history

- **Trigger** on measured prompt size (e.g. 70% of running context), not on turn count.
- **Pin** outside the rolled region: the system prompt, the task, constraints and rulings the
  user gave, and anything the harness must keep exactly (ids, amounts).
- **Write the roll as state**, not narrative: decisions with reasons, items done, items left,
  open questions, values that later steps depend on.
- **Keep recent turns verbatim** — the model's last few tool calls and results.
- **Log every roll** with the before/after token counts, and keep the full history on disk so
  a person can see what the model no longer can.

## What goes wrong

- Truncation with no log line: the agent forgets, and nothing says why.
- Chunks sized to the harness's assumed context when the server serves less.
- A reduce prompt that receives the raw chunks again and overflows.
- A constraint given in turn one, rolled into a summary as "some rules apply", then broken.
- Retrieval that returns plausible but off-topic text, which the model then trusts. Check
  that retrieved material is about the query, and treat "nothing relevant found" as its own
  result.

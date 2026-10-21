---
name: local-agents
description: Guides building, configuring, running and debugging agents on locally hosted models (Ollama, vLLM, llama.cpp, LM Studio, MLX, opencode, any local OpenAI-compatible endpoint) - measuring the stack and memory, choosing engine and model, sizing context, chunking and rolling history, stop-reason loops, tools, prompts, forced structured output, extraction and reconciliation, coding and review agents, stratified evaluation, human review routing, and rehearsal on the real model. Use when writing or changing a local agent's loop, prompt, tools, guards, timeouts, context or memory settings; when a local agent is slow, stops early, returns empty or truncated output, or underperforms; when fitting models into GPU or unified memory; or when building an extraction, coding, review or batch-analysis agent on a local model - even if the user never says "agent".
license: MIT
---

# Building local agents

A model on the user's own hardware is usually more capable than the harness around it lets
it be. The work of building a local agent is mostly giving the model what it actually has —
its real context, the time it needs, tools to find things out, reasons instead of orders —
and putting limits where they protect something real. This guide walks through that in the
order you would build it. Each step says what to do, which limits belong there, and what
tends to go wrong.

If you are fixing an agent that already exists rather than building one, go to
"Diagnosing an agent that underperforms" at the end; it follows the same steps.

Copy this checklist into your response and check items off as you go:

```
Local agent build:
- [ ] 1. Measure the stack: room, running/trained context, speed, device; other clients of the box
- [ ] 2. Decide what the model decides; name the kind of agent and read its reference
- [ ] 3. Size the context; choose read-whole / pull / push; plan history rolling
- [ ] 4. Loop ends on the stop reason; limits are backstops; no generation timeout; thinking on
- [ ] 5. Tools to find things out; guards by consequence where actions execute
- [ ] 6. Prompt as a briefing: job, good output, situations table, failure modes, reasons
- [ ] 7. Forced structure; schema that allows absence; meaning checked in code
- [ ] 8. Rehearse on the real model in a sandbox; each hard path once; report what ran
```

## Step 1 — Measure the stack

Before designing anything, find out what the model really has. Context size, which port
serves what, and whether the model is on the GPU are guessed wrong often enough to cost hours.
Run the probe (Python 3.9+, standard library only; exits 1 if it finds a problem):

```bash
python3 <skill-dir>/scripts/check_local_stack.py            # localhost, usual ports
python3 <skill-dir>/scripts/check_local_stack.py --host <ip> --ports 8081,11434
python3 <skill-dir>/scripts/check_local_stack.py --json     # machine-readable
```

It is read-only, runs on Linux and macOS, and reports, per server: what is loaded, how much
is on the GPU, the running context and the context the model was trained for. It compares the
opencode config with what is actually served, and it reports memory: GPU memory, what is free
for models after keeping room for the OS, which services or containers hold GPU memory, page
cache and swap, and on a Mac the GPU wired-memory limit. Write down:

- **room for models** (GPU-usable memory free now)
- **running context** (what the server will accept per request)
- **trained context** (what the model can use if you ask for it)
- **speed** (tokens/s; measure one real call, or estimate it from `references/choosing.md`)
- **device** (fully on GPU, split, or CPU)

**Find everything else that depends on this box** before planning changes to it: which
services, cron jobs and agents call each server (service units, crontabs, the server's own
request log), and how often. Server-wide settings — models kept loaded, parallel slots,
context length — apply to every client, and stopping a shared server stops all of them.

Before calling something missing or down, rule out a server that moved (another port, or
bound only to one interface such as a Tailscale address) and a model name that is an alias
(llama-swap answers to aliases that `/v1/models` does not list).

**Check the model fits before you build around it.** Needed = weights + KV cache at the
context you want + a few GiB of runtime overhead; compare that with the room the probe
reports. If it does not fit, offer the user the options — a smaller context, a smaller
quantisation, a smaller or mixture-of-experts model, or freeing memory — with what each costs.
Freeing memory changes a shared machine (stopping or shrinking other servers, raising the
GPU wired limit on a Mac), so lay out what each option stops or slows and let the user choose.
Do not script an option that stops a shared server until the user has picked it.
`references/choosing.md` has the sizes, the speed estimate and the exact commands.

**Keep models on the GPU.** Engines fall back to CPU quietly — a boot race with the driver, a
GPU already full, too few layers offloaded — and the result looks like a weak or hung model.
CPU inference is a choice for the user to make with the numbers in front of them
(`references/choosing.md` §3), never something discovered afterwards.

**If you are choosing the engine, the model or the harness** — or the current ones do not
fit — read `references/choosing.md`: hardware bandwidth and memory, what fits and how fast it
will run, which engine suits which job, how to pick a model, and which kind of harness.
If several servers share the box's memory, read `references/memory.md` too.

## Step 2 — Decide what the model decides

Write down, for this agent, which choices belong to the model and which to code.

- **The model decides** what to look at next, what something means, which action fits, when
  it has enough to answer, and when it is finished.
- **Code does** the mechanical, deterministic and irreversible parts: fetching, executing an
  approved action, writing state, enforcing permissions.

If code ends up deciding every step and the model is called once in the middle, you have
built a pipeline. That is sometimes the right thing — but call it a pipeline, and do not
expect it to use the model's judgment. An agent is a loop in which the model chooses the next
action from what it observes.

**Name the kind of agent**, because each kind has its own patterns and its own ways of going
wrong — read the reference for it before designing:

| kind | what it does | read |
|---|---|---|
| acting agent | uses tools to change things (triage, ops, support) | steps 4-5; guards by consequence |
| extraction agent | turns documents, tables or records into structured data | `references/structured-output.md` |
| coding agent | reads, edits and runs code; fixes, features, migrations | `references/coding-agents.md` |
| review agent | checks work — code, documents, another agent's output | `references/review-and-evaluation.md` §1-2; for code, `references/coding-agents.md` §7 |
| batch / many-item agent | the same analysis across many files or records, plus what connects them | `references/review-and-evaluation.md` §2; step 3 |
| long-running / conversational agent | many turns with growing history | step 3 (history) |

Many agents are more than one kind (a support agent that extracts, then acts); take the
patterns from each. Systems of several cooperating agents are a separate topic, beyond this
skill.

## Step 3 — Size the context, and plan for input that does not fit

**Set the running context on purpose.** Use the numbers from step 1. Where memory allows,
run close to what the model was trained for; where it does not, choose the largest size the
memory budget supports and write down why. Match the harness to the server exactly: a harness
that assumes less wastes the model, and one that assumes more gets requests refused.

**Budget the window per call:**

    available for input = running context
                          − system prompt − tool definitions
                          − reserve for thinking (reasoning models: often thousands)
                          − reserve for the answer

Count tokens with the server's tokenizer, not characters (`/tokenize` on vLLM and
llama.cpp; `prompt_eval_count` from a one-token Ollama call). Tool definitions are resent
every turn and can be thousands of tokens on their own.

**Choose how input that does not fit will be handled** — before writing the loop, because it
shapes the loop:

1. **Read it whole** if it fits. Chunking something the model could read whole loses the
   connections between its parts for nothing.
2. **Let the agent pull** — the usual best answer for large inputs. Give it tools to list,
   search and read ranges (sections, files, rows, an index), keep the material outside the
   context, and let it decide where to look. It reads what is relevant, and its judgment
   about where to look is the capability you want.
3. **Push chunks** when the whole input genuinely has to be covered (summaries, audits):
   split on structure (sections, functions, messages), overlap a little at the edges, carry a
   short running-notes block from chunk to chunk, extract per chunk, then combine in a final
   pass that sees only the extracted notes. Keep where each fact came from.

**Plan how history is managed** for anything that runs more than a few turns. When the
prompt passes a threshold you choose (say 70% of the running context), roll older turns into
a state record — what was decided and why, what is still open, identifiers in play — and keep
recent turns verbatim. Keep the task, the constraints and the user's rulings outside the part
that gets rolled, so they are never summarised away. Log every roll and every trim: silent
truncation is the failure nobody sees. Detail and pitfalls: `references/chunking.md`.

## Step 4 — Write the loop around the stop reason

The model's stop reason is what ends a turn and what ends the task. Every limit in the loop is
a backstop behind it, not a replacement for it.

**Handle every stop reason explicitly.** On OpenAI-compatible servers (`finish_reason`;
Ollama's `done_reason`):

| stop reason | meaning | what the loop does |
|---|---|---|
| `tool_calls` | the model wants to act | run the tools, append results, continue |
| `stop` | the model finished its turn | if the task is done, finish; if work is visibly left (an item with no decision), say what is missing and continue once |
| `length` | cut off by the output budget | not an answer. Raise the budget or ask it to continue; never accept truncated content as the result |
| anything else / error | unfinished | record it as unfinished with the reason and partial output |

On the Anthropic API the same roles are `tool_use`, `end_turn` and `max_tokens`. A reasoning
model with a small output budget can spend all of it thinking and return empty `content` with
`length` — so set the output budget with the thinking reserve from step 3, and read the stop
reason before reading the content.

**Iteration limits are backstops, and there are good places for them:**

- **Max turns** against a genuine infinite loop — the model repeating the same call, or two
  tools bouncing forever. Set it well above what a real task needs (measure a few real runs),
  and when it is hit, record the run as *unfinished, here is where it got to* — and make it
  resumable. Hitting the backstop is a finding to look at, not a normal way to end.
- **Retry counts** on transient failures: a connection refused while the server restarts, a
  tool that failed with an error, a malformed tool call handed back to the model to fix.
  Bound these so a permanent failure surfaces instead of spinning.
- **No-progress detection**: if the model says it is done while work is left, ask once; a
  second stop with no progress is recorded as unfinished rather than asked again forever.
- **Cost or wall-clock budgets** at the harness level when the user wants one, checked
  between turns and reported as unfinished when reached.

What does not work: a fixed count as the *primary* end (`for step in range(10)`), treating the
cap as success, or deciding the model is done by looking for a word like DONE in its text.

**Timeouts belong on everything except the model's generation.** A local model thinking at
30 tokens a second, or loading onto the GPU, is slow, not broken; stopping it throws the work
away and reports a failure that never happened. So:

- **Use timeouts** on connecting to a server, on health probes ("is anything listening?"), on
  remote APIs, and on tools that call out to other systems (a `gh` call, a shell command, a
  web fetch) — those can hang for reasons that have nothing to do with the model.
- **Distinguish connect from read.** A short connect timeout on the model server is fine; it
  catches a server that is down. The read — the generation — gets none.
- **If something must not wait on the model** (a heartbeat, a watchdog, a user-facing reply),
  decouple it: send the heartbeat before the model call, or run the model call separately.
  Do not time the model out to protect it.
- Thinking stays on — see the next part.

**Turning thinking off is not the first thing to reach for.** With a reasoning model, the
thinking is where the work happens; switching it off (`think: false`, a no-think template,
reasoning effort at minimum) trades the model's judgment for speed, the same trade as a
timeout. It is tempting to reach for it early, whenever a symptom looks like "too much
thinking". Each of those symptoms has a better first answer:

| symptom | the tempting fix | do this first |
|---|---|---|
| empty `content`, stop reason `length` | turn thinking off | raise the output budget to include the thinking reserve (step 3); read the stop reason before the content |
| the call is slow | turn thinking off | decouple whatever cannot wait (step 4, timeouts); measure whether the model is on the GPU; choose a faster or MoE model for this job |
| thinking loops or rambles on one input | turn thinking off | make the task unambiguous: show the exact output shape with a worked example, say what code will check so the model need not re-check it, split the task; a rambling model is usually an unclear prompt |
| a helper call (summarising history, counting tokens, warming up) "doesn't need it" | turn thinking off for helpers | leave it on — the summary decides what the agent remembers; for counting and warm-up, use a call that generates nothing (`num_predict: 1`) or the tokenizer instead of a model answer |
| structured output fights with thinking | turn thinking off | constrained decoding applies to the answer, not the thinking (check on your engine); show the schema in the prompt; split work and extraction steps (`references/structured-output.md`) |
| the context fills with thinking | turn thinking off | budget for it; roll history; pull instead of push (step 3) |

It becomes a reasonable option only after those, for a call where the model genuinely has
nothing to decide — and then as a measured choice the user hears about, with the comparison
(accuracy and time with and without thinking on real cases), not a default in the harness.
In a harness, never make "thinking off" the fallback path that runs silently when a call is
slow or long; that is a quality drop nobody sees.

**Case:** an invoice extractor on qwen3.5:9b spent 7,550 thinking tokens on one small invoice,
filled its 8k context and returned nothing — it was looping over whether to wrap the answer in
markdown and re-checking values. Giving it the exact output shape, saying "no fences", and
telling it that code checks the arithmetic brought the same call to about 2,500 tokens with
thinking on. Another extractor, given only a schema through `format`, spent 6,800 tokens
guessing the fields; one worked example in the prompt brought it to about 1,100.

**Case:** a judge with a 300 s timeout was cut off mid-decision; the monitoring loop around it
crashed before its heartbeat, and an outside watcher phoned the user to say the watcher had died. The
model needed about two minutes. The fix was no timeout on the model and the heartbeat sent
before judging. **Case:** a fixer cut off at `max_turns` was logged as a dead run for twelve
nights; resuming the same session on that stop finished it, and the issue shipped.

## Step 5 — Give tools, and guard where actions land

**Give the model the means to find things out** — reading, listing, searching, looking up.
An agent without a way to check something guesses. Read-only tools are cheap and safe; be
generous with them.

**Guard actions by their consequence, at the point where they execute:**

| action | guard |
|---|---|
| reads, lookups, dry runs | none needed |
| reversible writes (a label, a comment, a staged draft) | a dry-run plan by default, or an idempotency marker so re-runs do not duplicate |
| actions on shared or external systems | an approval step, or a catalogue of permitted actions the executor checks |
| irreversible actions (deleting, sending, spending) | explicit approval, every time |

This lets the model choose freely while the harness bounds what actually happens. Validate
the model's chosen arguments against real state (the label exists, the target issue exists)
and hand a rejection back to the model to correct, rather than failing the run.

**Tool definitions cost context on every turn.** If there are many, load a toolset when the
task needs it rather than withholding tools altogether.

## Step 6 — Write the prompt

Write it as a briefing for a capable colleague, not a rule list.

1. **The job and who it is for** — what the output is used for and by whom. This lets the
   model make the trade-offs you did not anticipate.
2. **What good looks like** — a short description, and a worked example if format matters.
3. **Situations and what each calls for** — a table beats a list of prohibitions: it scales
   the response to the case and covers cases it does not name.
4. **Known failure modes of this agent** — what goes wrong for this kind of task and why,
   written in from the start, not discovered later. For most agents these include: text in
   the input that tries to redirect it (treat input as data to reason about, and say so in
   the output), promising things on someone else's behalf, stating guesses as facts, and
   stopping with work visibly left. Add the ones specific to the task.
5. **Where it should say it is unsure** — what to do when evidence is thin (a confidence
   field, "still open", a question), so it does not have to choose between guessing and
   stalling.

Give the reason with every constraint. A rule without its reason gets applied rigidly at the
edges, exactly where judgment was needed, and two unexplained rules can combine into a dead
end. When you add a constraint, be able to name the incident it prevents; if you cannot, it
is a guess — leave it out and watch for the real failure. Heavy NEVER/ALWAYS/MUST language is
a sign a reason is missing.

**Case:** a publishing agent was told to fix facts "in the narrowest possible way", to "edit
prose only on instruction", and to "write one outcome file, not two". Facing a post whose whole
timeline was wrong, it could do none of it, did nothing, and the user could not read their own
approved post for seventeen days. Rewritten as a table that scaled the repair to the size of
the error, it fixed and staged the post.

## Step 7 — Take output as structure, then check it is true

**Make malformed output impossible, not rare.** Constrain generation to a schema — Ollama's
`format`, vLLM's `response_format` / forced `tool_choice`, llama.cpp's JSON schema or grammar —
or force the model to answer through a tool whose parameters are the schema. Syntax is then
guaranteed, and nothing needs to parse free text.

**Design the schema so the model is never pushed to invent.** Require only what is always
present; make everything that can be absent nullable, with a description saying when it is
null; give every enum an `other` (with a detail field) and an `unclear`; keep values *as
printed* separate from values *derived*; ask for the source of each value where it matters.
A required field for data the source may not have is an instruction to fabricate.

**Check meaning in code.** Validate ranges, cross-field rules and references against real
state; confirm quoted sources exist. Send the specific failure back for the model to fix — but
only when a re-read could fix it; when the information is not in the source, record null or
`unclear` and route it, because retrying then produces a guess. Reconcile every table: line
items against printed totals, running balances row by row, and flag a total that will not
balance rather than adjusting a line to match.

**Structure does not make it true.** A well-formed, validated, reconciled field can still be
wrong. Plan the measurement in step 8 for that. Details, engine specifics and a worked example:
`references/structured-output.md`.

**Do not decide what the model meant by matching its text** — a regex, a `startswith`, a search
for a keyword fires on text that merely mentions the thing and misses the same thing said
another way. When the harness needs to recognise something it produced earlier, have it write
a marker it controls and look for that.

## Step 8 — Rehearse on the real model, and feed what you find back in

An agent is not built until it has run on the model and hardware it will use, in the
environment it will run in (the same service manager, the same memory pressure — a model that
is fast in your shell can land on the CPU under the real scheduler).

1. **Build a small set of cases with known right answers**, including the hard paths, and
   make sure each hard path actually executes:
   - input larger than the running context (force it with a smaller `num_ctx` if you must)
     so chunking or pulling really runs — and a fact that spans a chunk boundary
   - a run long enough that history rolls over at least once
   - a `length` stop, a malformed tool call, a tool that fails
   - adversarial input for the known failure modes from step 6
   - a slow turn — let it run

   **Budget the user's time.** Time one real call, estimate a full run, and tell the user.
   Force each hard path **once, in the smallest test that triggers it** — one call with a tiny
   output budget for `length`, a low roll threshold on one item for history, one oversized
   input for truncation. A path test that fires dozens of times is a whole run in disguise.
   Whole runs (an hour is common on a small local model) confirm the finished agent; plan one
   or two, not a rehearsal loop. An agent that runs in 9 minutes but took 80 to build spent
   most of it on rehearsal the final run never needed.
2. **Rehearse against a sandbox, with the real model.** The agent under test will use its
   tools for real — and while you are still testing it, you do not want those calls to change
   anything that matters: posting comments on a live repository, writing to the production
   database, emailing a customer. So point its tools at an isolated copy of each system: a
   scratch repository or fork, a staging database or site, a service's test mode, a container
   or worktree holding a copy of the files it edits. The agent meets real behaviour — errors,
   pagination, rate limits, odd data — and nothing it does has consequences.

   Where no sandbox exists, or when you need a particular failure on demand (a tool error,
   malformed data, a permission refusal), give the agent a stand-in tool that returns
   realistic data and declines to make changes.

   Keep the real model either way. The model is what you are testing; a mocked reply cannot
   show how it actually thinks, stops or misreads.
3. **Read what the model did, not just the result.** When it goes wrong, ask whether the
   harness took something away (context, time, a tool, a reason) before concluding the model
   cannot do it. Fix the harness or the prompt, add the failure to the prompt's known failure
   modes with its reason, and rerun. Make every fix general: if it works only because the
   prompt now mentions the rehearsal case's own values or names, or a detector was written for
   the exact bugs in the test, it has fitted the test rather than improved the agent.
4. **Exercise every path you built, or remove it.** If the design avoids a path (fresh
   context per item, so history never rolls), either force it once in rehearsal or delete the
   code for it. A path that has never run is a bug waiting for the first input that needs it.
5. **Ship the output of the final run.** After fixing something, rerun and deliver that run's
   results, not an earlier run's.
6. **Report what ran and what did not**: which paths executed on the real model, which were
   only checked offline, measured speed per call. "Written but not exercised" is a legitimate
   result; presenting it as tested is not.

**Have it reviewed by a fresh instance, not by itself.** The context that produced the work
believes its own reasoning. A separate instance, given the output, the source and the criteria
but not the author's reasoning, finds what self-review confirms.

**Measure accuracy by stratum, not on average.** For anything that runs in production —
especially extraction — sample randomly within strata (document type, source quality, field),
oversample the rare ones, and report each with its sample size. Calibrate the model's
confidence against measured accuracy before using it for anything. Keep sampling after launch,
including items that were accepted automatically.

**Send people only what they add to.** A human review gate that receives everything stops being
read. Route low-calibrated-confidence fields, ambiguous or contradictory sources, weak strata,
fields with several readings, and unresolved reconciliation or validation failures — plus a
small random sample of the rest. Details: `references/review-and-evaluation.md`.

When choosing between models or settings, run the same cases several times each (small
models at non-zero temperature vary) and record correctness, latency and confidence.

## Diagnosing an agent that underperforms

Walk the same steps in the same order; most problems turn up in the first three, and none of
those require reading the model's output.

1. **Stack** — is it serving what the harness thinks, where it thinks, on the GPU? Is the
   server's prefix cache reusing the conversation turn to turn? A low hit rate means each
   turn is prefilled from scratch, usually because the harness and the chat template
   disagree (`references/coding-agents.md` §9). (step 1)
2. **Context** — running vs trained vs harness assumption; what is actually being sent; is
   input silently truncated? (step 3)
3. **Loop** — does it end on the stop reason? Is `length` treated as an answer? Is a cap or a
   timeout ending the work? (step 4)
4. **Tools** — could it find out what it needed? (step 5)
5. **Prompt** — rules without reasons, contradictions, missing failure modes? (step 6)
   Is thinking switched off anywhere — the main call, a helper, a fallback path? (step 4)
6. **Acceptance** — is text matching deciding what counts? (step 7)

Only then is "the model isn't good enough" the likely explanation — and it is worth testing
before concluding: the same task through a stronger model with the same harness, or the same
model through a leaner harness, shows which one was holding it back.

## Files in this skill

Read a reference when its step calls for it; run the script, do not read it.

| file | read when |
|---|---|
| `references/choosing.md` | choosing engine, model or harness; memory fit, speed estimate, GPU vs CPU, freeing memory (step 1) |
| `references/memory.md` | KV cache, what each server reserves, unified memory, two servers on one box (step 1) |
| `references/chunking.md` | token counting, pulling vs pushing chunks, rolling history, truncation (step 3) |
| `references/coding-agents.md` | coding and code-review agents; Claude Code on a local model; a coordinator with local workers and real data (step 2) |
| `references/structured-output.md` | forced syntax, schemas that allow absence, validation, reconciliation (step 7) |
| `references/review-and-evaluation.md` | independent review, per-item and integration passes, stratified sampling, human review gates (step 8) |
| `scripts/check_local_stack.py` | **run** it: read-only probe of servers, context, GPU placement, memory and opencode config (step 1) |

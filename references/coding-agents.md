# Coding agents and code-review agents

Read this when the agent reads, writes, fixes or reviews code. Coding agents have an advantage
most agents lack: the code can be *run*. Tests, type checkers, linters and the program itself
give facts, not opinions, about whether the work is right — build the agent around them.

## Contents
- 1. Use an existing harness, or build one
- 2. The tools a coding agent needs
- 3. How the agent edits files
- 4. Finding its way around a repository with a small context
- 5. The loop: edit, run the checks, read the result
- 6. Sandbox and git: making mistakes cheap
- 7. Code-review agents
- 8. Rehearsing a coding agent
- 9. Claude Code on a local model, and a coordinator with local workers

## 1. Use an existing harness, or build one

For interactive coding — the user asks, the agent edits their working copy — an existing coding
harness pointed at the local model (opencode and similar) is usually the right answer: file
tools, edit application, command running and permissions are done. The work is configuration:
- the provider's context and output limits match what the server serves (step 1 of the skill)
- the harness's request timeout is off or very long for a slow local model
- the model does tool calling reliably in that harness's format — test it on a real edit
- the server's context is large enough for the harness's system prompt and tool definitions,
  which can take thousands of tokens before any code is read

Build your own loop when the coding work is unattended or specialised — a nightly fixer, a
migration across many repositories, a pipeline step — and you need control of the stop
conditions, guards and reporting. The rest of this file applies to both: it is what to check
in a harness you adopt and what to build into one you write.

## 2. The tools a coding agent needs

- **Read** a file or a line range. Ranges matter on small contexts: whole large files do not fit.
- **Search**: text search across the repository (ripgrep-style) with file and line in the
  results, and symbol search (definitions, references) where available.
- **List / outline**: the directory tree, and a file's outline (classes, functions, signatures)
  without its bodies.
- **Edit** (section 3).
- **Run a command**: tests, the type checker, the linter, the formatter, the program itself —
  with the output returned (bounded in length; long logs summarised to the failures).
- **Git**: status, diff, and commit in the sandbox (section 6).

Be generous with read, search and run: they are how the agent finds out instead of guessing.
Guard the ones with consequences — pushing, deploying, touching anything outside the sandbox —
by blast radius, as in step 5 of the skill.

## 3. How the agent edits files

How edits are expressed decides how often they fail to apply.

- **Search-and-replace blocks** — the exact existing text and its replacement — are the most
  reliable format for local models. The harness checks the search text appears exactly once;
  if not, it returns the specific failure *with the actual surrounding lines* so the model can
  correct it, rather than a bare "no match".
- **Unified diffs** with line numbers are fragile for small models: they get the numbers and
  context lines wrong. Accept them only with a lenient applier, or not at all.
- **Whole-file rewrites** are fine for small new files; for existing large files they cost
  output budget and silently drop code the model did not reproduce. If you allow them, diff the
  result against the original and flag large deletions.

After every edit, show the model the resulting diff; it catches its own misapplied edits there.

## 4. Finding its way around a repository with a small context

A repository never fits. Treat it as input that does not fit (step 3 of the skill) and let the
agent pull:
- start from an outline: the tree, then outlines of the relevant files — signatures and
  docstrings, not bodies
- search for the symbols involved, and read only the ranges that matter
- keep a short working-notes block (files touched, what each change was for, what is still to
  do) outside the rolled history, so a long session does not forget its own edits
- for work across many files, do it file by file with the notes carrying the cross-file facts
  (what a changed function now takes and returns), then run the whole test suite to catch what
  the notes missed

## 5. The loop: edit, run the checks, read the result

The loop is: understand → edit → run the checks → read the output → repeat, and it ends when
**the checks pass and the model says it is done** — both. The model saying it is done while a
test fails is not done; tests passing while the model has work left is not done either.

- **Run the real checks**: the project's tests, the type checker, the linter, and the program
  on a real input where that is cheap. Their output is ground truth; give it to the model
  verbatim (trimmed to the failures), not paraphrased.
- **If there are no tests for the change, write one first** that fails for the right reason,
  then make it pass. It turns "I think this fixes it" into a fact.
- **A backstop on turns** for a loop that is not converging — the same test failing the same way
  several times — recorded as unfinished with the last failure attached, not as done.
- **Do not let the agent weaken the check** to pass it: deleting or skipping a failing test,
  loosening an assertion, adding a type-ignore. Detect these in the diff (a test file changed
  alongside the code, a skip marker added) and route them for review; sometimes they are right,
  but never silently.

## 6. Sandbox and git: making mistakes cheap

- **Work in an isolated copy**: a git worktree or branch for code changes, a container when the
  agent runs commands that could affect the machine. The agent can then act freely inside it.
- **Git is the undo**: commit (or at least snapshot) at each working state, so a bad edit is a
  revert, not a reconstruction.
- **The boundary is where changes leave the sandbox**: pushing, opening a PR, merging,
  deploying. Guard those by consequence — automatic for low-risk (a draft PR on a branch), an
  approval for anything that reaches users.
- **Secrets**: the sandbox holds only the credentials the task needs.

## 7. Code-review agents

A review agent finds what is wrong with a change. Split the work by what each part is good at:

- **Facts come from tools.** Run the language's standard analysis — linters, type checkers,
  the tests, and a static analyser where one exists (for Python: `ruff` or `pylint`, `mypy`,
  `pytest`) — and give the results to the model. They find wrong argument counts, unclosed
  resources, unused parameters, type errors and failing behaviour on any change, reliably.
  Use the general tools; do not write detectors for the particular bugs you expect to find in
  the change in front of you — they catch this change's bugs and nothing else, and the model
  learns to lean on them.
- **Judgment comes from the model.** What no linter sees: a value in milliseconds treated as
  seconds, two "centralised" constants that disagree, a change that does not do what the
  description says, a missing case in the logic, a design that will not hold. Give the model
  the diff, the files it touches, the tool results and the change description.
- **Treat the author's description as claims to check**, not as context. The reviewer should
  not inherit the author's reasoning (see `review-and-evaluation.md` §1); each claim ("all
  callers updated", "tests pass") is verified against the code and the tool results.
- **Per-file, then cross-file** (`review-and-evaluation.md` §2): each changed file reviewed in
  its own context with the files it calls and is called by; then an integration pass over the
  per-file summaries — signatures, units, shared constants, assumptions — for the cross-file
  bugs.
- **Verify each finding** in a fresh context against the source, with the quoted evidence
  checked in code, before it is reported.
- **Deduplicate by structure, not by asking the model to compare everything.** Key findings by
  file, line range and the root cause the model names; merge identical keys in code, and use
  the model only to judge small groups of near-matches. Asking a small-context model to group
  dozens of findings in one call runs it out of context and merges things that differ.
- **Severity by consequence**: breaks at runtime / wrong results / maintainability / style.
  A reviewer that marks everything critical gives the human nothing to prioritise.

## 8. Rehearsing a coding agent

Rehearse (step 8 of the skill) on a real repository copy in a sandbox, with tasks that have a
known right answer — a failing test to fix, a planted bug to find, a small feature with an
acceptance test. Include the hard paths once each: an edit whose search text does not match,
a test that keeps failing, a file too large to read whole, a change that needs several files.
Fixes found in rehearsal must be general: if a prompt change works only because it now
mentions the rehearsal repository's own names or bugs, it has fitted the test, not improved
the agent.

## 9. Claude Code on a local model, and a coordinator with local workers

Claude Code runs against any server that speaks the Anthropic Messages API, vLLM included, and
it is a strong unattended coding harness for a local model: file tools, edit application,
sessions you can resume, sandboxing. What to get right:

- **Point every model slot at the local model** (`ANTHROPIC_MODEL`, the default Opus, Sonnet
  and Haiku slots, the small/fast and subagent slots), or background calls quietly go to the
  hosted API. Send the key as `ANTHROPIC_AUTH_TOKEN` (a Bearer token, which vLLM checks);
  `ANTHROPIC_API_KEY` sends `x-api-key`, gets a 401, and the client retries forever. Build the
  run's environment from nothing but a named profile, so a shell export or a settings file
  can't switch half a run to another backend, and after the run read which model actually
  answered from the stream (`--output-format stream-json`), not from the configuration.
- **The client's request timeout covers only the wait for the first byte**, and a request
  queued behind other work can wait longer than its 10 minutes. Raise `API_TIMEOUT_MS` to
  the largest value a timer holds (2147483647); a streaming answer is never cut by it.
- **Check the prefix cache is reusing the conversation.** Each turn of an agent resends the
  whole conversation, so the server's prefix cache is most of the speed. Claude Code sends
  short `system` messages mid-conversation (token counts, environment notes). A chat template
  that accepts a system message only at the top makes vLLM's Anthropic adapter merge them
  into the top system prompt instead, so the prompt changes right after the system and tool
  block on every turn and the whole conversation is prefilled again. Measure: vLLM's
  `/metrics` prefix-cache hits ÷ queries, or replay two consecutive recorded requests and
  compare the cached token counts. Fix: a chat template (`--chat-template`) that renders a
  mid-conversation system message in place, for example as a user turn wrapped in a tag.
  **Case:** a qwen worker on two DGX Sparks spent 1–3 hours per bug, about 98% of it waiting
  on the server; cache reuse was about 15%. With the template it was 82% on a fresh session,
  and the same kind of case took 13 minutes instead of 189 (with the next point too).
- **Tune speculative decoding by measurement.** A model with built-in draft heads (MTP)
  guesses tokens ahead; more guesses is not faster. Measure one stream and several at once
  at each setting. **Case:** qwen3.8 flash, tensor-parallel over two Sparks: 2 guesses was
  best (41–46 tok/s for one stream, 114 tok/s total for four); 3 was slower than 2.
- **Run workers in parallel.** A local server generates several streams for little more than
  the cost of one (here 4 streams ≈ 114 tok/s total against ≈ 40 for one), and tokens cost
  nothing, so independent cases should run at once.

### Give the worker the real data the bug is about

A local coding model in a sandbox sees only the code. **Case:** replaying eleven past bugs, a
qwen worker fixed eight from the report alone. All three misses had the answer in real data it
could not see (run records, email threads, a live tender list): it invented test data shaped
like its own guess, its fix passed its own tests, and one fix made things worse with a rule
much broader than the report. When a coordinator (here Claude) that can see the live system
first found the relevant records, copied them into the worker's case folder, and wrote a brief
saying what the data showed and what "fixed" means on it, all three were fixed.

- The brief says what was **verified** in the data, separately from what is suspected, and
  names the traps: what would look right on invented data and be wrong on the real data.
- **The copied data is private and must not reach a commit.** One worker turned exported
  emails into fixtures and committed 72 real snippets. Tell the worker to rebuild the data's
  shape with invented people and text, and also check mechanically: refuse a diff whose added
  lines (above a minimum length, and not already in the code) appear in the case's data, or
  that adds an email address found there.
- Split agents by context, not by role: the coordinator holds the case (history, the
  person's rulings, the data); the worker holds the code; a fresh reviewer sees the result,
  the brief and the diff, not the coordinator's reasoning.

### Sandboxing the worker, and proving it

- **A scratch HOME stops accidents, not escapes.** A worker with the real HOME smoke-tested a
  script that found the user's mail login and emailed them a false alarm. A scratch HOME hides
  logins found through `$HOME`; an absolute path still reaches them, so commands also need an
  OS sandbox (Claude Code's bubblewrap sandbox: no network, reads limited to the work folder,
  token variables removed from the command environment).
- **Judge a sandbox test by what is on disk afterwards**, never by a command's exit code or
  the model's account: inside bubblewrap the real HOME is an empty throwaway view, so a write
  there exits 0 and writes nothing. A sandbox that blocks every command also lets a run
  "finish" with nothing done; test that a command did run.
- **Clone, don't worktree**: a git worktree's `.git` points back into the main checkout,
  outside the sandbox.
- **Don't ask a hosted model to run a sandbox-escape probe.** Its safety classifier may flag
  it, and a client can then move the session to another model (Claude Code announces this as a
  `model_refusal_fallback` event and continues on an older model). Run the probe on the local
  model; record any announced fallback as such rather than failing the run as a stray model.


# Evals

Three tasks used to develop the skill, each run with and without it on a real local model and
graded against the assertions in `evals.json`.

| id | task | fixtures |
|---|---|---|
| 1 | Build an invoice-extraction agent that must not invent values and must route what needs a human | `fixtures/invoices/` (8 invoices, each with a planted difficulty) |
| 2 | Build a local code-review agent, including cross-file bugs and false claims in the PR description | `fixtures/pr/` (a small Python package and its PR description) |
| 3 | Design accuracy measurement and human review for an extraction agent going to production | none (a design document) |

All fixtures are synthetic: the companies, invoices and code were written for these evals.

## Running one

1. Start a local model. The prompts assume `qwen3.5:9b` in Ollama with `num_ctx 8192`; any
   small local model works if you change the prompt to match. A small model and a small context
   are deliberate: they are where the failure modes the skill addresses show up.
2. Copy `fixtures/` into a scratch working directory, so the agent cannot read `evals.json`
   and its answer key.
3. In that directory, give Claude the eval's `prompt` twice, in two clean sessions: once with
   the skill installed and once without it (for Claude Code, a session with skills disabled
   or the skill folder moved aside).
4. Grade each run against the eval's `assertions`. Each assertion is pass or fail; read the
   agent Claude built and its saved log, not only its output files, because several
   assertions are about how the agent works (forced structure, no generation timeout,
   reconciliation in code).

Grade blind if you can: a second person, or a fresh Claude session given the assertions and
the run's files but not told which run had the skill.

The answer key is in the assertions. For example, INV-04's lines sum to 875 against a printed
total of 925, which an agent should flag, not fix. Don't put it where the agent under test can
read it.

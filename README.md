# local-agents

A Claude skill for building, running and debugging agents on **locally hosted models**:
Ollama, vLLM, llama.cpp, LM Studio, MLX, or anything behind a local OpenAI-compatible
endpoint.

> Read the write-up: **[Claude Is Selling Your Local Open Source Models Short](https://ghosthatstudio.com/blog/claude-is-selling-your-local-models-short/)**. Product page: [ghosthatstudio.com/toolbox/local-agents](https://ghosthatstudio.com/toolbox/local-agents/)

## Why

A model on your own hardware is usually more capable than the harness around it lets it be.
When Claude builds a local agent, it tends to protect the harness from the model instead of
using it. Typical results:

- **Context left unused.** The harness gets less context than the model has: Ollama's 4,096-token
  default, or a harness configured for a smaller window than the server serves.
- **Timeouts on slow work.** A model thinking at 30 tokens a second is slow, not hung. Cutting it
  off throws the work away and reports a failure that never happened.
- **Iteration caps as the end condition.** The run ends after N steps instead of on the model's stop
  reason, and a `length` stop with empty content is accepted as an answer.
- **Thinking switched off** as the first fix for anything that looks slow or long.
- **Rules without reasons.** Prompts that work on the expected case and deadlock on the first
  unexpected one.
- **Text-matching on the model's output.** A regex or keyword search decides what the model
  "meant".
- **Silent CPU fallback.** A model that quietly ends up on the CPU looks like a weak model.

This skill teaches Claude the other way round. Give the model what it actually has. Let it
finish on its own signal. Explain the reason behind each constraint instead of only giving the
order. Guard actions by their consequences rather than by matching words. Then rehearse on the
real model before calling the agent done.

## Install

### Claude Code: plugin (recommended)

The skill is listed in the [Ghost Hat Studio plugin catalog](https://github.com/ghosthat-studio/marketplace).
In a Claude Code session, add the catalog, then install the skill from it:

```
/plugin marketplace add ghosthat-studio/marketplace
/plugin install local-agents@ghosthat-studio
```

If this machine has no SSH key for GitHub, start Claude Code with
`CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` so the catalog clones over HTTPS.

To update later, run `/plugin marketplace update ghosthat-studio`, or turn on auto-update for
the catalog under **Marketplaces** in `/plugin`.

### Claude Code: as a plain skill folder

For you, in every project:

```bash
git clone https://github.com/ghosthat-studio/local-agents.git ~/.claude/skills/local-agents
```

For one project only (commit it so collaborators get it too):

```bash
git clone https://github.com/ghosthat-studio/local-agents.git .claude/skills/local-agents
```

Update with `git -C ~/.claude/skills/local-agents pull`.

### Claude.ai and the Claude desktop app

1. Download `local-agents-<version>.zip` from the
   [latest release](https://github.com/ghosthat-studio/local-agents/releases/latest).
2. In Claude, open the skills settings (**Customize → Skills**) and upload the zip.
3. Turn the skill on.

Uploaded skills need code execution enabled. The probe script in this skill checks servers
on your network, so on claude.ai, where Claude runs in a cloud sandbox, use the skill for its
guidance. Use it from Claude Code to probe your own machines.

### Claude API

Upload the same zip with the Skills API (`POST /v1/skills`) and reference it from a Messages
request's `container`. See [Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide).

## Using it

The skill loads on its own whenever you build or debug an agent on a local model, fit models
into GPU or unified memory, or build an extraction, coding or review agent that runs locally.
You do not have to name it. Some prompts that bring it in:

- *"Build an agent that triages our support inbox using the qwen model in Ollama on this box."*
- *"My local agent stops after a few turns and returns empty answers. Why?"*
- *"Will a 70B model fit on this machine next to the vLLM server that's already running?"*
- *"Set up Claude Code to run against our local vLLM server."*

You can also invoke it directly: `/local-agents` as a plain skill, or
`/local-agents:local-agents` when it is installed as a plugin.

### The stack probe

`scripts/check_local_stack.py` is a read-only probe. Run it on any machine with a local
inference server. It needs Python 3.9+ and uses only the standard library. It reports:

- what each server is serving
- its running and trained context
- whether the model is on the GPU
- memory headroom (Linux and macOS, including unified memory)
- mismatches between an opencode config and what is actually served

```bash
python3 ~/.claude/skills/local-agents/scripts/check_local_stack.py
python3 ~/.claude/skills/local-agents/scripts/check_local_stack.py --host <ip> --ports 8081,11434
python3 ~/.claude/skills/local-agents/scripts/check_local_stack.py --json
```

The probe exits 1 if it finds a problem.

## What's inside

`SKILL.md` is an eight-step build guide, plus a diagnosis path for agents that underperform:

1. **Measure the stack.** What is served, at what context, on which device, and whether the
   model fits.
2. **Decide what the model decides,** and name the kind of agent.
3. **Size the context** and plan for input that does not fit.
4. **Write the loop around the stop reason.** Limits are backstops, there is no timeout on
   generation, and thinking stays on.
5. **Give tools,** and guard actions by their consequences.
6. **Write the prompt** as a briefing, with reasons and known failure modes.
7. **Take output as structure,** then check it is true.
8. **Rehearse on the real model** in a sandbox, and measure accuracy by stratum.

Detail lives in `references/`, which Claude reads only when a step calls for it:

| file | covers |
|---|---|
| `references/choosing.md` | engine, model and harness choice; memory and bandwidth; speed estimates; freeing memory on Linux and macOS |
| `references/memory.md` | KV cache, what each server reserves, unified memory, two servers on one box |
| `references/chunking.md` | token counting, pulling vs pushing chunks, rolling history, silent truncation |
| `references/structured-output.md` | forced syntax, schemas that do not invite fabrication, validation, reconciling totals |
| `references/review-and-evaluation.md` | independent review, per-item and integration passes, stratified sampling, human review gates |
| `references/coding-agents.md` | coding and code-review agents; Claude Code on a local model; a coordinator with local workers |

## Evaluation

The guide was developed by running the same tasks with and without the skill on real local
models and grading the results. `evals/` has the prompts, the assertions and the synthetic
fixtures, so you can rerun them on your own hardware. See [evals/README.md](evals/README.md).

## Requirements

- Claude Code, Claude.ai / the desktop app, or the Claude API.
- For the probe: Python 3.9+ on Linux or macOS. Windows is untested.

## Contributing

Issues and pull requests are welcome. Reports from real runs are especially useful: a failure
the skill did not prevent, or a server or engine behaviour it gets wrong. Include the engine and
version, the model, and what the probe reported.

## License

MIT. See [LICENSE](LICENSE).

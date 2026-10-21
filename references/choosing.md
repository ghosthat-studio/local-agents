# Choosing the engine, the model and the harness

Read this when setting up a new agent or box, or when the measurements from step 1 show the
current choice does not fit. The order matters: the hardware decides what memory and
bandwidth you have; memory decides which models fit; bandwidth decides how fast they run;
and the task decides which harness to put around them.

## Contents
- 1. Know the hardware: memory and bandwidth
- 2. Estimate what fits and how fast it runs
- 3. GPU first; CPU only as a last resort, explained
- 4. Choosing the engine
- 5. Choosing the model
- 6. Choosing the harness
- 7. Making more memory available (Linux and macOS)

## 1. Know the hardware: memory and bandwidth

Two numbers decide nearly everything: **how much memory the GPU can use** and **how fast it can
read it**. Generation is usually limited by memory bandwidth, not compute: every generated
token reads the model's active weights once.

| hardware | GPU-usable memory | bandwidth (approx.) |
|---|---|---|
| NVIDIA GB10 (DGX Spark) | ~120 GiB unified, shared with the OS | 273 GB/s |
| Apple M3 Ultra | up to the wired limit of unified memory (up to 512 GB) | 819 GB/s |
| Apple M4 Max | up to the wired limit (up to 128 GB) | 546 GB/s |
| AMD Ryzen AI Max+ 395 (Strix Halo) | up to ~96-112 GB unified, set in BIOS/kernel | 256 GB/s |
| RTX 4090 | 24 GB VRAM | ~1,000 GB/s |
| RTX 5090 | 32 GB VRAM | ~1,800 GB/s |
| Desktop CPU, dual-channel DDR5 | system RAM | ~60-90 GB/s |
| Server CPU, 8-12 channel DDR5 | system RAM | ~300-500 GB/s |

Use the probe (`scripts/check_local_stack.py`) for what is actually free right now; the table
is for planning (figures as of 2026; check the vendor's spec sheet for hardware not listed). Discrete GPUs have their own VRAM; on unified-memory machines (GB10, Apple
Silicon, Strix Halo) the GPU's budget *is* system memory minus what the OS and everything else
is using.

## 2. Estimate what fits and how fast it runs

**Weights:** parameters × bits per weight ÷ 8, plus ~5-10% overhead.

| format | bits/weight (approx.) | 70B model | 30B model | 8B model |
|---|---|---|---|---|
| BF16 / FP16 | 16 | 140 GB | 60 GB | 16 GB |
| FP8 / Q8_0 | 8-8.5 | 70-75 GB | 30-32 GB | 8.5 GB |
| Q6_K | ~6.6 | 58 GB | 25 GB | 6.6 GB |
| Q4_K_M / AWQ-4bit | ~4.5-4.8 | 40-42 GB | 18 GB | 4.8 GB |

**KV cache:** grows with context × concurrent sequences; see `memory.md` for the formula, and
read the real figure from the server's startup log.

**Total needed** = weights + KV cache at the context you want + a few GiB of runtime overhead.
Compare it with the room the probe reports. If it does not fit, the options are, in order:
a smaller context, a smaller quantisation, a smaller or MoE model, freeing memory (section 7).
Do not let it spill onto the CPU silently.

**Speed, roughly:** tokens/s ≈ bandwidth ÷ bytes read per token, and bytes per token ≈ the
*active* parameters × bytes per weight. Real numbers land at about 50-80% of this ceiling.

- Dense 70B at FP8 on a GB10: 273 ÷ 70 ≈ 3.9 → about 3 tok/s.
- MoE with ~3B active at Q8 on a GB10: 273 ÷ 3.2 ≈ 85 → about 40-60 tok/s.
- Dense 8B at Q4 on dual-channel DDR5 CPU: 80 ÷ 4.8 ≈ 17 → about 8-12 tok/s.
- Dense 70B at Q4 on dual-channel DDR5 CPU: 80 ÷ 42 ≈ 1.9 → about 1 tok/s.

This is why mixture-of-experts models suit bandwidth-limited machines: a large total model
with few active parameters gives big-model quality at small-model speed, as long as all of it
fits in memory. Prompt processing (prefill) is compute-bound and is much slower on CPU than
on GPU, so long prompts cost far more there than these numbers suggest.

Tell the user the estimate before they commit to a model. A 2,000-token answer at 3 tok/s is
eleven minutes; that may be fine for an overnight job and useless for interactive coding.

## 3. GPU first; CPU only as a last resort, explained

Run models on the GPU. Check that they actually are — Ollama's `size_vram`, vLLM and
llama.cpp startup logs, `nvidia-smi` process lists — because engines fall back to CPU quietly
(a boot race with the driver, a GPU already full, too few layers offloaded).

CPU inference is a legitimate choice when there is genuinely no GPU option: no GPU, the model
cannot fit in GPU memory even after section 7, or the GPU is committed to something more
important. When you propose it, give the user the numbers so they can decide:

- the estimated speed from section 2 — often single-digit tokens per second, and ~1 tok/s for
  large dense models on desktop memory
- prefill cost: a long prompt can take minutes before the first token
- partial offload (`-ngl` in llama.cpp, Ollama's automatic split) runs at roughly the speed of
  the CPU part — splitting a model 80/20 does not give 80% of GPU speed
- the alternatives: a smaller or MoE model that fits on the GPU, a shorter context, a remote
  model for this task

Then let them choose. Never discover CPU placement after the fact and present the slowness as
the model's.

## 4. Choosing the engine

| engine | good for | watch for |
|---|---|---|
| **Ollama** | quick setup, many small models, model management, Apple Silicon and NVIDIA | picks context and GPU split automatically — set context explicitly; reloads when a request's `num_ctx` differs; spills to CPU quietly |
| **llama.cpp `llama-server`** | GGUF models with precise control (`-c`, `-ngl`, `-np`), CPU+GPU splits, multi-host RPC | `-c` is split across `-np` slots; you manage the model files |
| **llama-swap** | one endpoint in front of several llama-server/vLLM backends, loading on demand with aliases and TTLs | `/v1/models` lists primary ids, not aliases; backends start cold |
| **vLLM** | safetensors/FP8/AWQ models, concurrency, tool-call parsers, tensor parallel across GPUs or hosts | reserves memory up front (`--gpu-memory-utilization`); startup can take minutes; checks free memory at start |
| **SGLang** | similar to vLLM; strong prefix caching and constrained decoding | same memory behaviour as vLLM |
| **MLX / mlx-lm, LM Studio** | Apple Silicon native; LM Studio adds a GUI and serves both GGUF and MLX | bound by the macOS GPU wired limit (section 7) |

Rules of thumb: one user and many models → Ollama or llama-swap over llama.cpp. One big model,
concurrency or multi-GPU → vLLM or SGLang. Mac → MLX or llama.cpp (Ollama and LM Studio wrap
them). Whatever you pick, confirm the model's **tool-calling format** is supported by that
engine (vLLM needs the right `--tool-call-parser`; GGUF chat templates must include tools).

## 5. Choosing the model

1. **Fit** — weights + KV at the context you need must fit the room from the probe (section 2).
2. **Speed** — estimate tokens/s for the use: interactive coding wants tens of tok/s; a
   nightly batch job can live with a few.
3. **Context** — the trained context should cover what the agent needs (step 3 of the skill);
   check the model card, not the default the server picked.
4. **Tools and thinking** — for agents, the model must do tool calling reliably. Reasoning
   models think before answering: budget output tokens for it, and keep it on.
5. **Quantisation** — Q4_K_M and up are usually close to full quality for large models; small
   models (under ~8B) lose more at low bit widths. Prefer a larger model at Q4 to a smaller one
   at Q8 when both fit.
6. **Test it on the task** — run the agent's rehearsal cases (step 8) on the candidates; the
   winner on benchmarks is not always the winner on your cases.

## 6. Choosing the harness

| harness | use when | check |
|---|---|---|
| **A coding TUI** (opencode and similar) pointed at the local endpoint | interactive coding and exploration by the user | its context/output limits match the server; its provider request timeout is off or generous for slow local models |
| **Your own loop** on an OpenAI-compatible client | an agent with specific tools, guards and state | you control stop reasons, retries and history — the rest of this skill applies directly |
| **An agent framework** | you want its tool plumbing, memory or multi-agent patterns | read its defaults: many ship iteration caps as the end condition, request timeouts, and small context assumptions — set them per this skill |
| **A pipeline** (code decides each step, the model does one job) | the flow really is fixed | call it a pipeline; give the model's step its full context and no timeout |

## 7. Making more memory available (Linux and macOS)

Start from the probe: it reports room for models, which processes hold GPU memory and which
service or container owns each one. Offer the user the options that apply, with what each
frees and what it costs — these change shared machines, so the user chooses.

### Linux (systemd)

- **Stop what is not needed.** For each GPU process the probe attributes to a unit:
  `systemctl --user stop <unit>` (or `sudo systemctl stop <unit>` for system units), and
  `disable` as well if it should not return at boot. A retired server that still starts at boot
  takes its memory first and makes the intended one fail.
- **Unload idle models.** `ollama stop <model>`; or set `OLLAMA_KEEP_ALIVE` so idle models
  unload, and `OLLAMA_MAX_LOADED_MODELS=1` so only one is resident.
- **Shrink what a server reserves.** In the server's unit, via `systemctl edit <unit>` (a
  drop-in, so the change survives upgrades):
  - vLLM: lower `--gpu-memory-utilization`, `--max-model-len` or `--max-num-seqs`; use
    `--kv-cache-dtype fp8` to halve the KV cache.
  - Ollama: `Environment=OLLAMA_FLASH_ATTENTION=1` and `Environment=OLLAMA_KV_CACHE_TYPE=q8_0`
    (halves the KV cache; needs flash attention), `OLLAMA_NUM_PARALLEL=1` (each parallel slot
    has its own cache), `OLLAMA_CONTEXT_LENGTH` to pin the context. A snap install
    (`snap.ollama.*`) takes drop-ins the same way; check `snap get ollama` too.
  - Then `systemctl daemon-reload` and restart the unit.
- **Make the server wait for the GPU at boot** (a drop-in with an `ExecStartPre` that waits
  for `nvidia-smi` to answer), so it never starts on CPU.
- **Drop the page cache** once before starting a large server on unified memory:
  `sync && echo 3 | sudo tee /proc/sys/vm/drop_caches`. Harmless; the cache refills.
- **AMD unified-memory APUs** (e.g. Strix Halo): the GPU's share is set in firmware (UMA size)
  and kernel parameters for the GTT/TTM limit; check the distribution's and ROCm's current
  guidance before changing them.

### macOS (Apple Silicon)

The GPU may only wire a fraction of unified memory by default (roughly two-thirds to
three-quarters). To let a large model load fully on the GPU:

    sudo sysctl iogpu.wired_limit_mb=<MB>

Leave about 8-16 GB for macOS itself (e.g. on a 128 GB machine, ~114000). The setting resets
at reboot; to persist it, install a LaunchDaemon, for example
`/Library/LaunchDaemons/local.iogpu-wired-limit.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.iogpu-wired-limit</string>
  <key>ProgramArguments</key>
  <array><string>/usr/sbin/sysctl</string><string>iogpu.wired_limit_mb=114000</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
```

then `sudo launchctl bootstrap system /Library/LaunchDaemons/local.iogpu-wired-limit.plist`.
On macOS Ventura the key was `debug.iogpu.wired_limit` (in bytes). Setting it too high starves
macOS and makes the whole machine swap — leave the headroom.

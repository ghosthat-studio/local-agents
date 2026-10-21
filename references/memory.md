# Memory, context and configuration on a local box

Read this when fitting models onto a machine, when two inference servers share one, when a
model is slower than it should be, or when context and memory settings disagree.

## Contents
- What actually consumes memory
- Estimating KV cache
- What each server reserves, and how to read it
- Unified memory machines
- Two servers on one box
- Checklist

## What actually consumes memory

A served model needs **weights** (fixed by size and quantisation), **KV cache** (grows with
context length × concurrent sequences), and **activations and runtime overhead** (smaller,
but spikes during prefill). Context is not free: doubling the context roughly doubles the KV
cache for every sequence that uses it.

## Estimating KV cache

Per sequence, per token:

    bytes ≈ 2 (K and V) × layers × kv_heads × head_dim × bytes_per_element

Multiply by context length and by the number of sequences served at once. `kv_heads` is the
number of key/value heads (smaller than attention heads with grouped-query attention);
`bytes_per_element` is 2 for fp16/bf16 and 1 for an fp8 cache. Hybrid and linear-attention
models (Mamba-style layers) keep only a small fixed state for those layers, so their KV
cache is much smaller than the formula suggests for a pure transformer.

You rarely need the formula, because the servers report the answer:

- **vLLM** logs it at startup: `GPU KV cache size: N tokens` and
  `Maximum concurrency for <max_model_len> tokens per request: Nx`. That is the real
  budget; trust it over estimates.
- **llama.cpp** prints `llama_kv_cache: size = … MiB` at load, and `/props` reports `n_ctx`.
- **Ollama** prints the same llama.cpp lines in its service log; `/api/ps` reports what is
  loaded, its `size`, `size_vram` and running `context_length`.

## What each server reserves, and how to read it

| server | what decides memory | where to look |
|---|---|---|
| vLLM | `--gpu-memory-utilization` reserves that fraction of GPU memory **up front** for weights plus KV cache; `--max-model-len` caps context per request; `--max-num-seqs` caps concurrency | startup log; `/v1/models` → `max_model_len` |
| llama.cpp | `-c` total context, **split across `-np` parallel slots** (each request gets `c/np`); `-ngl` layers on GPU | `/props`; load log |
| Ollama | `num_ctx` per request or `OLLAMA_CONTEXT_LENGTH` on the server; picks a default from free VRAM if unset; `OLLAMA_NUM_PARALLEL` multiplies the cache | `/api/ps` (`size_vram`, `context_length`); service log line `inference compute` shows the device it chose |

Traps worth naming:
- **vLLM's startup check uses the utilisation fraction, whatever else you set.** At start it
  refuses to run unless *free* memory is at least `gpu_memory_utilization × total` ("Free
  memory on device … is less than desired GPU memory utilization"). Setting the KV cache in
  bytes (`--kv-cache-memory-bytes`) does not exempt it, and the default is 0.9. With another
  server already holding memory, set the fraction to what is really free at start — lower,
  not left at the default.
- **Ollama reloads when the requested context changes.** A request whose `num_ctx` differs
  from the loaded model's context unloads and reloads it. Two clients asking for different
  sizes make every alternate call a cold load — slow enough to look like an outage. Pin the
  context on the server (`OLLAMA_CONTEXT_LENGTH`) and have clients either send the same value
  or none.
- **The Ollama snap drops some settings.** Its launcher (`/snap/ollama/current/bin/snap_launcher.sh`)
  reads `snap get ollama flash-attention` but never exports it, so `snap set ollama
  flash-attention=1` alone does nothing — and a q8_0 KV cache quietly falls back to full size
  without flash attention. A systemd drop-in setting `OLLAMA_FLASH_ATTENTION=1` alone is not
  enough either: the launcher overwrites the variable with the (empty) snap value. Set both,
  then confirm in the server log what took effect. The general rule: after changing any server
  setting, read back what the server says it is running with; do not trust the setting.
- **llama.cpp's slot split.** `-c 32768 -np 4` gives each request 8,192 tokens, not 32,768.
- **Ollama's device is decided once, at start.** If it starts before the GPU driver is
  ready it logs `library=cpu`, reports `total_vram="0 B"`, and runs everything on CPU with
  a small default context until restarted. A service drop-in that waits for `nvidia-smi` to
  answer before starting prevents it.

## Unified memory machines

On unified-memory systems (NVIDIA GB10 / DGX Spark, Apple Silicon) the GPU and CPU share
one pool. `nvidia-smi` may show memory as `[N/A]`; the system's available memory is the
GPU's budget too. Consequences:
- A vLLM `--gpu-memory-utilization 0.7` on a 121 GiB box claims ~85 GiB of *system* RAM.
- "Out of GPU memory" and "out of RAM" are the same event.
- Page cache counts: a box that just read 60 GB of weights may report little free memory
  that is actually reclaimable (`MemAvailable` is the honest number, not `MemFree`).

## Two servers on one box

Serving a large model in vLLM and a small one in Ollama on the same machine works when the
budget is planned: vLLM's reservation is taken first and is not given back, so whatever
starts second gets what is left. Decide the split deliberately — lower vLLM's utilisation or
its `max_model_len` to leave room — and check after both are up that the second one really
is on the GPU. The failure is quiet: the second server falls back to CPU and simply becomes
very slow.

Also check that retired servers are **disabled**, not just stopped. A leftover unit that
starts at boot takes its memory first, and the intended server then fails with "free memory
is less than desired".

## Checklist

1. Run `scripts/check_local_stack.py` — running context, device, harness mismatches.
2. Read each server's startup log for the device and the KV cache it actually got.
3. Add up: weights + KV cache × concurrency for every server, against total (unified) or
   GPU memory.
4. Confirm the harness's context and output limits fit what is served, with room for the
   prompt and for thinking.
5. After a reboot, check again: boot order changes what each server gets.

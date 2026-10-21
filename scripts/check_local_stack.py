#!/usr/bin/env python3
"""What the local model stack ACTUALLY serves, against what the harness ASSUMES.

Asks each inference server directly - Ollama, vLLM, llama.cpp - for the models it has
loaded, their real context windows, and whether they are on the GPU; reads harness configs
(opencode) and flags every mismatch. Read-only. Stdlib only.

Why it exists: on 2026-09-27 opencode was configured for a model at 127.0.0.1:8080 with a
131,072-token context. Nothing listened on 8080 - the model had moved to vLLM on 8081 - and
vLLM served 65,536. Separately, Ollama lost a boot race with the NVIDIA driver, found no GPU,
and ran on CPU with a 4,096-token default context. Each took hours to find by hand and each
is one HTTP request to see.

Usage:
  check_local_stack.py                      # probe localhost on the usual ports
  check_local_stack.py --host 192.0.2.10 --ports 8081,11434
  check_local_stack.py --opencode ~/.config/opencode/opencode.json
  check_local_stack.py --json               # machine-readable

Exits 1 if any PROBLEM was found, 0 otherwise. Needs Python 3.9+.

The short HTTP timeouts here are on PROBES (is anything listening?), never on generation.
Local models themselves get no timeouts.
"""
import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

if sys.version_info < (3, 9):
    sys.exit("check_local_stack.py needs Python 3.9+ (found %d.%d)" % sys.version_info[:2])

# Default ports: Ollama 11434, vLLM 8000, llama.cpp / llama-swap 8080, a second server 8081,
# LM Studio 1234, common custom/Flask-style wrappers 5000, SGLang 30000.
PORTS = [11434, 8000, 8080, 8081, 1234, 5000, 30000]
GET_TIMEOUT = 4    # seconds; a listening local server answers metadata calls in milliseconds
POST_TIMEOUT = 10  # /api/show can read model files from disk on first call

OLLAMA_DEFAULT_CTX = 4096  # Ollama's fallback context when nothing sets num_ctx; a running
                           # context at or below this on a model trained for more is the default,
                           # not a choice
STARVED_FRACTION = 4       # running context under trained/4 is worth a warning
HARNESS_UNDERUSE = 2       # harness assuming under half the served context is starving the model
REASONING_MIN_OUTPUT = 4096  # below this, thinking alone can use the whole output budget
OS_RESERVE_GIB = 8         # left for the OS, desktop and page tables when estimating room (Linux)
MAC_OS_RESERVE_GIB = 12    # suggested headroom for macOS when raising the wired limit (8-16 GiB)
MAC_DEFAULT_WIRED = 0.7    # approx. fraction of RAM the GPU may wire by default (2/3 to 3/4)

findings = []  # (level, where, message)


def note(level, where, msg):
    findings.append((level, where, msg))


def get(url, timeout=GET_TIMEOUT):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def post(url, payload, timeout=POST_TIMEOUT):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def probe_ollama(base, served):
    code, ps = get(base + "/api/ps")
    if code != 200 or not isinstance(ps, dict):
        return False
    code, tags = get(base + "/api/tags")
    names = [m.get("name") for m in (tags or {}).get("models", [])] if isinstance(tags, dict) else []
    loaded = ps.get("models", [])
    note("OK", base, "Ollama: %d model(s) installed, %d loaded" % (len(names), len(loaded)))
    for m in loaded:
        name = m.get("name")
        size, vram = m.get("size", 0), m.get("size_vram", 0)
        ctx = m.get("context_length")
        where = "%s %s" % (base, name)
        if size and vram == 0:
            note("PROBLEM", where, "loaded with 0 bytes on the GPU - running on CPU. Usually a "
                 "boot race with the GPU driver or a GPU already full; restart Ollama once "
                 "`nvidia-smi` answers, and make it wait for the GPU at boot.")
        elif size and vram < size:
            note("WARN", where, "only %.0f%% on the GPU (%.1f of %.1f GiB) - the rest is on CPU "
                 "and every token pays for it" % (100 * vram / size, vram / 2**30, size / 2**30))
        else:
            note("OK", where, "fully on the GPU (%.1f GiB)" % (vram / 2**30))
        info = post(base + "/api/show", {"model": name}) or {}
        mi = info.get("model_info") or {}
        trained = next((v for k, v in mi.items() if k.endswith(".context_length")), None)
        if ctx:
            served[name] = {"ctx": ctx, "kind": "ollama", "base": base}
            msg = "running context %d tokens" % ctx
            if trained:
                msg += " (model trained for %d)" % trained
            level = "OK"
            if ctx <= OLLAMA_DEFAULT_CTX and (trained or 0) > 2 * OLLAMA_DEFAULT_CTX:
                level = "PROBLEM"
                msg += " - this is Ollama's small default, not the model's capacity. Set " \
                       "num_ctx per request or OLLAMA_CONTEXT_LENGTH on the server."
            elif trained and ctx < trained // STARVED_FRACTION:
                level = "WARN"
                msg += " - under a quarter of what the model supports; check that is deliberate"
            note(level, where, msg)
    for n in names:
        if n not in served:
            served.setdefault(n, {"ctx": None, "kind": "ollama", "base": base})
    return True


def probe_openai(base, served):
    code, models = get(base + "/v1/models")
    if code != 200 or not isinstance(models, dict):
        return False
    data = models.get("data") or []
    owned = {m.get("owned_by") for m in data}
    kind = "vllm" if "vllm" in owned else "openai-compatible"
    code, props = get(base + "/props")
    if code == 200 and isinstance(props, dict) and "default_generation_settings" in props:
        kind = "llama.cpp"
    for m in data:
        mid = m.get("id")
        ctx = m.get("max_model_len")
        if kind == "llama.cpp":
            ctx = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx")
        served[mid] = {"ctx": ctx, "kind": kind, "base": base}
        note("OK", "%s %s" % (base, mid), ("%s serving, context %d tokens" % (kind, ctx)) if ctx else "%s serving, context not reported" % kind)
    if kind == "llama.cpp" and isinstance(props, dict):
        slots = props.get("total_slots")
        if slots and slots > 1:
            note("WARN", base, "llama.cpp splits its context across %d parallel slots - each "
                 "request gets n_ctx / %d unless the server was started with a larger -c" % (slots, slots))
    return True


def check_opencode(path, served, host):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        note("PROBLEM", path, "could not be read: %s" % e)
        return
    # opencode merges several layers; this probe reads only the global file.
    others = []
    d = os.getcwd()
    while True:
        for n in ("opencode.json", "opencode.jsonc", ".opencode/opencode.json", ".opencode/opencode.jsonc"):
            f = os.path.join(d, n)
            if os.path.isfile(f) and os.path.realpath(f) != os.path.realpath(os.path.expanduser(path)):
                others.append(f)
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    if others:
        note("WARN", "opencode config layers", "checked %s only; these also apply and can override "
             "it: %s" % (path, ", ".join(others)))
    for pname, p in (cfg.get("provider") or {}).items():
        base = ((p.get("options") or {}).get("baseURL") or "").rstrip("/")
        where = "opencode provider %r" % pname
        if not base:
            continue
        root = base[:-3] if base.endswith("/v1") else base
        code, models = get(root + "/v1/models")
        if code != 200:
            note("PROBLEM", where, "baseURL %s does not answer (%s). opencode will report an "
                 "API error, not a missing server. Before calling the server down, check whether "
                 "it moved: another port, or bound to a different interface (e.g. only its "
                 "Tailscale address)." % (base, code or "nothing listening"))
            continue
        ids = [m.get("id") for m in (models or {}).get("data", [])]
        # Ollama resolves names case-insensitively and treats "x" as "x:latest";
        # vLLM and llama.cpp match the id exactly.
        ollama = get(root + "/api/version")[0] == 200
        norm = (lambda n: (n or "").lower().removesuffix(":latest")) if ollama else (lambda n: n)
        known = {norm(i) for i in ids}
        # llama-swap answers to aliases that /v1/models does not list, so absence
        # from the list is not proof the name fails. It exposes /running.
        swap = get(root + "/running")[0] == 200
        for mid, m in (p.get("models") or {}).items():
            w = "%s model %r" % (where, mid)
            if norm(mid) not in known:
                if swap:
                    note("WARN", w, "not a primary id at %s (llama-swap; lists %s). It may be an "
                         "alias - check the aliases in the llama-swap config before calling it "
                         "missing." % (base, ", ".join(ids) or "none"))
                else:
                    note("PROBLEM", w, "configured but not served at %s (served: %s)" % (base, ", ".join(ids) or "none"))
                continue
            lim = m.get("limit") or {}
            want, out = lim.get("context"), lim.get("output")
            got = next((v.get("ctx") for k, v in served.items() if norm(k) == norm(mid)), None)
            if not got:
                for mm in (models or {}).get("data", []):
                    if mm.get("id") == mid:
                        got = mm.get("max_model_len")
            if want and got and want > got:
                note("PROBLEM", w, "harness assumes %d tokens of context; the server serves %d. "
                     "Long sessions will be refused outright." % (want, got))
            elif want and got and want < got // HARNESS_UNDERUSE:
                note("WARN", w, "harness uses %d of the %d the server serves - the agent is "
                     "being starved of context it has" % (want, got))
            elif want and got:
                note("OK", w, "context %d fits the served %d" % (want, got))
            elif want:
                note("WARN", w, "harness assumes %d tokens of context, but the server did not report "
                     "a running context for this model (not loaded, or not probed) - load it and "
                     "re-run, or check by hand" % want)
            if want and out and out >= want:
                note("PROBLEM", w, "output limit %d >= context %d - leaves no room for the prompt" % (out, want))
            if m.get("reasoning") and out and out < REASONING_MIN_OUTPUT:
                note("WARN", w, "reasoning model with output limit %d - thinking can use the whole "
                     "budget and return empty content" % out)


def _unit_of(pid):
    """The systemd unit or container a process belongs to, from its cgroup path."""
    try:
        with open("/proc/%s/cgroup" % pid) as f:
            path = f.read().strip().split(":")[-1]
    except Exception:
        return None
    parts = [x for x in path.split("/") if x]
    for x in reversed(parts):
        if x.startswith("docker-") and x.endswith(".scope"):
            return "container %s" % x[7:19]
        if x.endswith(".service") and not x.startswith("user@"):
            return ("user unit " if "user@" in path else "system unit ") + x
    return None


def _linux_memory():
    with open("/proc/meminfo") as f:
        mi = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in f if ":" in l}
    total, avail = mi["MemTotal"], mi.get("MemAvailable", 0)
    cache = mi.get("Cached", 0) + mi.get("Buffers", 0)
    swap = mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)
    note("OK", "memory", "%.0f GiB total, %.0f GiB available, %.0f GiB page cache, %.1f GiB swap in use"
         % (total / 2**30, avail / 2**30, cache / 2**30, swap / 2**30))
    gpus, unified = [], False
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True)
        for line in r.stdout.strip().splitlines():
            name, tot, used = [x.strip() for x in line.rsplit(",", 2)]
            if "N/A" in tot:
                unified = True
                note("OK", "gpu", "%s - unified memory: GPU and CPU share the RAM above, so "
                     "system memory *is* the GPU's budget" % name)
            else:
                gpus.append((name, int(tot) * 2**20, int(used) * 2**20))
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True)
        for line in r.stdout.strip().splitlines():
            pid, rest = line.split(",", 1)
            proc, used = rest.rsplit(",", 1)
            pid, proc, used = pid.strip(), proc.strip(), used.strip()
            owner = _unit_of(pid)
            mem = "" if "N/A" in used else " %.1f GiB," % (int(used) / 1024)
            note("OK", "gpu process", "pid %s %s,%s %s" % (pid, proc, mem, owner or "not a service"))
    elif not shutil.which("rocm-smi"):
        note("WARN", "gpu", "no nvidia-smi or rocm-smi - if this box has no usable GPU, models "
             "will run on CPU; see references/choosing.md for what that costs")
    for name, tot, used in gpus:
        free = tot - used
        note("OK", "gpu free", "%s: %.1f of %.1f GiB free for models (%.1f GiB in use)"
             % (name, free / 2**30, tot / 2**30, used / 2**30))
    if unified or not gpus:
        room = max(0, avail - OS_RESERVE_GIB * 2**30)
        note("OK", "room for models", "~%.0f GiB (available %.0f GiB minus %d GiB kept for the OS)"
             % (room / 2**30, avail / 2**30, OS_RESERVE_GIB))
        if unified and cache > 8 * 2**30 and cache > 0.2 * total:
            note("WARN", "page cache", "%.0f GiB of page cache. It is reclaimable, but on unified memory the GPU's "
                 "free figure may not count it, so vLLM's startup memory check can refuse to start. Drop it once before "
                 "starting a large server: `sync && echo 3 | sudo tee /proc/sys/vm/drop_caches`"
                 % (cache / 2**30))
    if swap > 2**30:
        note("WARN", "swap", "%.1f GiB in swap - memory is oversubscribed; a model or KV cache paged "
             "to disk runs far slower. Free memory (stop idle servers, unload models) before "
             "adding load." % (swap / 2**30))


def _mac_memory():
    def sysctl(k):
        r = subprocess.run(["sysctl", "-n", k], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    total = int(sysctl("hw.memsize") or 0)
    r = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = int(re.search(r"page size of (\d+)", r).group(1)) if "page size" in r else 16384
    pages = {m.group(1): int(m.group(2)) for m in re.finditer(r'^"?(Pages [^:"]+)"?:\s+(\d+)', r, re.M)}
    avail = page * sum(pages.get(k, 0) for k in
                       ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
    limit = sysctl("iogpu.wired_limit_mb")
    chip = sysctl("machdep.cpu.brand_string") or "Mac"
    note("OK", "memory", "%s, %.0f GiB unified memory, ~%.0f GiB available now"
         % (chip, total / 2**30, avail / 2**30))
    if limit is None:
        note("OK", "gpu limit", "iogpu.wired_limit_mb not present (older macOS or Intel). On "
             "Ventura the key was debug.iogpu.wired_limit, in bytes.")
        return
    limit = int(limit)
    if limit == 0:
        note("WARN", "gpu limit", "GPU wired-memory limit is the macOS default (roughly two-thirds "
             "to three-quarters of RAM). A model bigger than that falls back to CPU or fails to "
             "load. To raise it, leaving ~8-16 GiB for macOS: `sudo sysctl "
             "iogpu.wired_limit_mb=%d` - resets at reboot; persist it with a LaunchDaemon."
             % int(total / 2**20 - MAC_OS_RESERVE_GIB * 1024))
    else:
        note("OK", "gpu limit", "GPU may wire up to %.0f GiB (iogpu.wired_limit_mb=%d); %.0f GiB "
             "left for macOS" % (limit / 1024, limit, total / 2**30 - limit / 1024))
    room = min(avail, (limit or total * MAC_DEFAULT_WIRED / 2**20) * 2**20)
    note("OK", "room for models", "~%.0f GiB" % (room / 2**30))


def check_memory():
    try:
        if sys.platform == "darwin":
            _mac_memory()
        else:
            _linux_memory()
    except Exception as e:
        note("WARN", "memory", "could not read memory: %s" % e)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ports", default=",".join(map(str, PORTS)))
    ap.add_argument("--opencode", default="~/.config/opencode/opencode.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    served = {}
    found = []
    for port in [int(p) for p in a.ports.split(",") if p]:
        base = "http://%s:%d" % (a.host, port)
        if probe_ollama(base, served) or probe_openai(base, served):
            found.append(base)
    if not found:
        note("PROBLEM", a.host, "no inference server answered on ports %s" % a.ports)
    check_opencode(a.opencode, served, a.host)
    check_memory()
    if a.json:
        print(json.dumps([{"level": l, "where": w, "message": m} for l, w, m in findings], indent=1))
    else:
        order = {"PROBLEM": 0, "WARN": 1, "OK": 2}
        for l, w, m in sorted(findings, key=lambda x: order[x[0]]):
            print("%-8s %s\n         %s" % (l, w, m))
        n = sum(1 for l, _, _ in findings if l == "PROBLEM")
        print("\n%d problem(s), %d warning(s)" % (n, sum(1 for l, _, _ in findings if l == "WARN")))
    return 1 if any(l == "PROBLEM" for l, _, _ in findings) else 0


if __name__ == "__main__":
    sys.exit(main())

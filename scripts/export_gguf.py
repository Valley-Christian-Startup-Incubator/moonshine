#!/usr/bin/env python3
"""Build a portable Ollama package (GGUF) for a finished train job.

    export_gguf.py <job_id> [--hf-dir DIR] [--force]

Output: results/<id>/ollama-gguf.zip containing
    model.gguf   Q4_K_M quantized model (llama.cpp)
    Modelfile    FROM ./model.gguf + Qwen3 chat template (thinking off by default)
    README.txt   Windows / Mac / Linux install steps
Status for the web page: results/<id>/gguf_status.json
    {"state": "running"|"done"|"failed", "step", "message", "error", "pid", ...}

Pipeline: full-precision HF safetensors (mlx_lm.fuse --dequantize from the
student base + adapters, or --hf-dir to reuse one) -> convert_hf_to_gguf.py
(f16) -> llama-quantize Q4_K_M -> zip. Intermediates live in
results/<id>/gguf-tmp/ and are always deleted.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402

LLAMACPP_HOME = os.environ.get("MOONSHINE_LLAMACPP", os.path.join(mc.DISTILL_HOME, "llamacpp"))
CONVERT_PY = os.path.join(LLAMACPP_HOME, "venv", "bin", "python")
CONVERT_SCRIPT = os.path.join(LLAMACPP_HOME, "src", "convert_hf_to_gguf.py")
QUANTIZE = shutil.which("llama-quantize") or "/opt/homebrew/bin/llama-quantize"
MLX_BIN = os.path.dirname(sys.executable)
ZIP_NAME = "ollama-gguf.zip"
STATUS_NAME = "gguf_status.json"
QUANT = "Q4_K_M"

# Ollama's official qwen3 template (`ollama show qwen3 --modelfile`), with one
# change: when the caller does not set think at all, the reply starts with an
# empty <think></think> block, i.e. no thinking. That is how Moonshine
# evaluates the trained model (enable_thinking=False). think:false and
# `/set nothink` behave exactly like the official model; think:true / `/set
# think` still turn thinking on.
TEMPLATE = r'''{{- $lastUserIdx := -1 -}}
{{- range $idx, $msg := .Messages -}}
{{- if eq $msg.Role "user" }}{{ $lastUserIdx = $idx }}{{ end -}}
{{- end }}
{{- if or .System .Tools }}<|im_start|>system
{{ if .System }}
{{ .System }}
{{- end }}
{{- if .Tools }}

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{{- range .Tools }}
{"type": "function", "function": {{ .Function }}}
{{- end }}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>
{{- end -}}
<|im_end|>
{{ end }}
{{- range $i, $_ := .Messages }}
{{- $last := eq (len (slice $.Messages $i)) 1 -}}
{{- if eq .Role "user" }}<|im_start|>user
{{ .Content }}
{{- if and $.IsThinkSet (eq $i $lastUserIdx) }}
   {{- if $.Think -}}
      {{- " "}}/think
   {{- else -}}
      {{- " "}}/no_think
   {{- end -}}
{{- end }}<|im_end|>
{{ else if eq .Role "assistant" }}<|im_start|>assistant
{{ if (and $.IsThinkSet (and .Thinking (or $last (gt $i $lastUserIdx)))) -}}
<think>{{ .Thinking }}</think>
{{ end -}}
{{ if .Content }}{{ .Content }}
{{- else if .ToolCalls }}<tool_call>
{{ range .ToolCalls }}{"name": "{{ .Function.Name }}", "arguments": {{ .Function.Arguments }}}
{{ end }}</tool_call>
{{- end }}{{ if not $last }}<|im_end|>
{{ end }}
{{- else if eq .Role "tool" }}<|im_start|>user
<tool_response>
{{ .Content }}
</tool_response><|im_end|>
{{ end }}
{{- if and (ne .Role "assistant") $last }}<|im_start|>assistant
{{ if not (and $.IsThinkSet $.Think) -}}
<think>

</think>

{{ end -}}
{{ end }}
{{- end }}'''

MODELFILE = '''# Moonshine fine-tuned model ({label}), job {job_id}
# Build: ollama create {name} -f Modelfile
FROM ./model.gguf
TEMPLATE """{template}"""
PARAMETER temperature 0.6
PARAMETER top_k 20
PARAMETER top_p 0.95
PARAMETER repeat_penalty 1
PARAMETER num_ctx 8192
PARAMETER stop <|im_start|>
PARAMETER stop <|im_end|>
'''

README = '''Moonshine model for Ollama ({label}, fine-tuned by {owner}, job {job_id})
=====================================================================

Files: model.gguf ({quant} quantized, {size}), Modelfile, README.txt.
Works with Ollama on Windows, macOS and Linux. Install Ollama first: https://ollama.com/download

Windows (PowerShell)
  1. Right-click {zipname} > Extract All... > Extract.
  2. Open the extracted folder (the one containing Modelfile), click the address bar,
     type powershell and press Enter. A PowerShell window opens in that folder.
  3. ollama create {name} -f Modelfile
  4. ollama run {name}

macOS / Linux (Terminal)
  1. cd ~/Downloads
     unzip {zipname} -d {name}
     (on a Mac, double-clicking the .zip in Finder also works; then cd into the new folder)
  2. cd {name}
  3. ollama create {name} -f Modelfile
  4. ollama run {name}

Thinking is off by default (short, direct answers, the way the model was trained and tested).
To let it think first: type /set think in `ollama run`, or send "think": true in the API.
/set nothink (or "think": false) turns it off again.
After step 3 you can delete the folder; Ollama keeps its own copy. Remove the model with: ollama rm {name}
'''


class Exporter:
    def __init__(self, job_id: str, hf_dir: str | None):
        self.id = job_id
        self.rd = os.path.join(mc.RESULTS_DIR, job_id)
        if not os.path.isdir(self.rd):
            raise SystemExit(f"no results folder for job {job_id}")
        self.meta = mc.job_meta(job_id)
        if self.meta.get("type") != "train":
            raise SystemExit(f"job {job_id} is not a train job")
        self.p = self.meta.get("params", {})
        self.student = self.p.get("STUDENT", "qwen3-0.6b")
        info = mc.STUDENTS.get(self.student, {"label": self.student, "dir": ""})
        self.label = info["label"]
        self.base = os.path.join(mc.MLX_MODELS_DIR, info["dir"]) if info["dir"] else ""
        self.hf_dir = hf_dir
        self.tmp = os.path.join(self.rd, "gguf-tmp")
        self.status_path = os.path.join(self.rd, STATUS_NAME)
        self.status: dict = {}
        self.t0 = time.time()
        state = mc.read_json(os.path.join(self.rd, "state.json"), {}) or {}
        owner = self.meta.get("ownerName") or "student"
        self.owner = owner
        # same name the web download uses: <owner>-<id>-ollama-gguf.zip (download/+server.ts)
        safe = lambda v: re.sub(r"^\.+$", "_", re.sub(r"[^A-Za-z0-9._-]", "_", v))[:180] or "result"  # noqa: E731
        self.download_name = f"{safe(owner.lower())}-{safe(job_id)}-{ZIP_NAME}"
        self.name = state.get("ollama_tag") or f"moonshine-{mc.slugify(owner)}-{job_id.lower()}"

    # ------------------------------------------------------------- status
    def set(self, **kw):
        self.status.update(kw, updated_at=mc.now_iso())
        mc.write_json_atomic(self.status_path, self.status)

    def step(self, step: str, message: str):
        mc.log(f"[gguf] {message}")
        self.set(state="running", step=step, message=message)

    def run_cmd(self, cmd, cwd=None):
        mc.log("$ " + " ".join(cmd))
        tail: list[str] = []
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                cwd=cwd, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        try:
            for line in proc.stdout:
                line = line.rstrip("\n").split("\r")[-1]
                if not line.strip():
                    continue
                print(line, flush=True)
                tail = (tail + [line])[-15:]
            proc.wait()
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"{os.path.basename(cmd[0])} {os.path.basename(cmd[1]) if len(cmd) > 1 else ''} "
                               f"failed (exit {proc.returncode}): " + " | ".join(tail[-4:]))

    # ------------------------------------------------------------- steps
    def get_hf(self) -> str:
        if self.hf_dir and os.path.exists(os.path.join(self.hf_dir, "config.json")):
            mc.log(f"[gguf] reusing full-precision model at {self.hf_dir}")
            return self.hf_dir
        out = os.path.join(self.tmp, "hf")
        adapters = os.path.join(self.rd, "adapters")
        fused = os.path.join(self.rd, "fused-model")
        self.step("dequantize", "Rebuilding the full-precision model (base + adapters)")
        if self.base and os.path.exists(os.path.join(self.base, "config.json")) and \
                os.path.exists(os.path.join(adapters, "adapters.safetensors")):
            self.run_cmd([os.path.join(MLX_BIN, "mlx_lm.fuse"), "--model", self.base, "--adapter-path", adapters,
                          "--save-path", out, "--dequantize"])
        elif os.path.exists(os.path.join(fused, "config.json")):
            self.run_cmd([os.path.join(MLX_BIN, "mlx_lm.convert"), "--hf-path", fused, "--mlx-path", out,
                          "--dequantize"])
        else:
            raise RuntimeError("neither adapters + base model nor fused-model/ found")
        return out

    def run(self):
        for exe in (CONVERT_PY, CONVERT_SCRIPT, QUANTIZE):
            if not os.path.exists(exe):
                raise RuntimeError(f"GGUF tooling missing: {exe}")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp)
        hf = self.get_hf()
        # the chat template lives in our Modelfile; convert still wants tokenizer files
        f16 = os.path.join(self.tmp, "model-f16.gguf")
        self.step("convert", "Converting to GGUF (f16)")
        self.run_cmd([CONVERT_PY, CONVERT_SCRIPT, hf, "--outtype", "f16", "--outfile", f16])
        if hf.startswith(self.tmp):
            shutil.rmtree(hf, ignore_errors=True)
        q = os.path.join(self.tmp, "model.gguf")
        self.step("quantize", f"Quantizing to {QUANT}")
        self.run_cmd([QUANTIZE, f16, q, QUANT])
        os.remove(f16)
        size = os.path.getsize(q)

        self.step("package", "Writing Modelfile and README, zipping")
        modelfile = MODELFILE.format(label=self.label, job_id=self.id, name=self.name, template=TEMPLATE)
        readme = README.format(label=self.label, owner=self.owner, job_id=self.id, quant=QUANT,
                               size=f"{size / 1e9:.1f} GB", name=self.name, zipname=self.download_name)
        readme = readme.replace("\n", "\r\n")  # readable in Windows Notepad too
        part = os.path.join(self.rd, ZIP_NAME + ".part")
        with zipfile.ZipFile(part, "w", allowZip64=True) as z:
            z.writestr("Modelfile", modelfile, compress_type=zipfile.ZIP_DEFLATED)
            z.writestr("README.txt", readme, compress_type=zipfile.ZIP_DEFLATED)
            z.write(q, "model.gguf", compress_type=zipfile.ZIP_STORED)  # already compressed
        with zipfile.ZipFile(part) as z:
            info = {i.filename: i.file_size for i in z.infolist()}
        if sorted(info) != ["Modelfile", "README.txt", "model.gguf"] or info["model.gguf"] != size:
            raise RuntimeError(f"zip check failed: {info}")
        os.replace(part, os.path.join(self.rd, ZIP_NAME))
        shutil.rmtree(self.tmp, ignore_errors=True)
        zsize = os.path.getsize(os.path.join(self.rd, ZIP_NAME))
        secs = round(time.time() - self.t0)
        self.set(state="done", step="done", message=f"Ready: {zsize / 1e9:.2f} GB ({QUANT}) in {mc.fmt_duration(secs)}",
                 bytes=zsize, gguf_bytes=size, seconds=secs, quant=QUANT, model_name=self.name,
                 finished_at=mc.now_iso(), pid=None, error=None)
        mc.log(f"[gguf] {self.status['message']} -> {os.path.join(self.rd, ZIP_NAME)}")


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--hf-dir", help="reuse an existing full-precision HF model folder")
    ap.add_argument("--force", action="store_true", help="rebuild even if the zip exists")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", a.job_id):
        raise SystemExit("bad job id")
    rd = os.path.join(mc.RESULTS_DIR, a.job_id)
    status_path = os.path.join(rd, STATUS_NAME)
    old = mc.read_json(status_path, {}) or {}
    if old.get("state") == "running" and old.get("pid") != os.getpid() and pid_alive(old.get("pid")):
        print(f"export already running (pid {old['pid']})")
        return 0
    if os.path.exists(os.path.join(rd, ZIP_NAME)) and not a.force:
        print("ollama-gguf.zip already exists (use --force to rebuild)")
        return 0
    def on_term(signum, frame):
        raise SystemExit(f"stopped by signal {signum}")
    signal.signal(signal.SIGTERM, on_term)
    ex = Exporter(a.job_id, a.hf_dir)
    ex.set(state="running", step="start", message="Starting", pid=os.getpid(), started_at=mc.now_iso(),
           error=None, finished_at=None)
    try:
        ex.run()
        return 0
    except BaseException as e:  # noqa: BLE001 - record everything, including Ctrl-C / SIGTERM
        msg = str(e) or e.__class__.__name__
        mc.log(f"[gguf] FAILED: {msg}")
        ex.set(state="failed", message="Build failed", error=msg[:1000], pid=None, finished_at=mc.now_iso())
        shutil.rmtree(ex.tmp, ignore_errors=True)
        try:
            os.remove(os.path.join(rd, ZIP_NAME + ".part"))
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())

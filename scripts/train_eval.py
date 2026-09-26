#!/usr/bin/env python3
"""Train job: split -> LoRA train -> evaluate -> fuse -> register in Ollama.

Every phase is resumable. State lives in results/<id>/state.json; per-item
evaluation results are appended to jsonl files so a pause/crash never loses
more than one item (or, for training, more than SAVE_EVERY steps).

Usage: train_eval.py --job-id ID
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402

VENV_BIN = os.path.dirname(sys.executable)
PHASES = ["split", "train", "loss", "answer_base", "answer_trained", "grade_base", "grade_trained",
          "report", "fuse", "register"]
PHASE_LABELS = {
    "split": "Splitting data 80/20", "train": "Training", "loss": "Measuring validation loss",
    "answer_base": "Untrained student answering held-out questions",
    "answer_trained": "Trained student answering held-out questions",
    "grade_base": "Teacher grading untrained answers", "grade_trained": "Teacher grading trained answers",
    "report": "Writing evaluation report", "fuse": "Fusing adapters into a 4-bit model",
    "register": "Registering the model in Ollama",
}

GRADER_SYSTEM = """You are a strict, fair grader of practice-question answers. You compare a STUDENT ANSWER with a REFERENCE ANSWER written by an expert.

Rubric:
- "correct": true only if the student's FINAL answer matches the reference's final answer: the same choice letter, the same action/decision, or the same value (allow trivial rounding and wording differences). If the student gives no clear final answer, gives several conflicting answers, or the final answer differs, "correct" is false. Do not give credit for good reasoning that ends in the wrong answer.
- "reasoning_score" (integer 1-5) rates the student's worked reasoning:
  5 = correct, complete and clearly leads to the final answer
  4 = correct with small gaps or minor imprecision
  3 = partly correct; a noticeable error or missing step
  2 = mostly wrong or confused, with some relevant content
  1 = missing, irrelevant, incoherent, or contradicts itself
- "explanation": one short sentence justifying the grade.
Judge only against the reference answer and sound domain knowledge. Ignore style and length. Output only the JSON object."""

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "correct": {"type": "boolean"},
        "reasoning_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "explanation": {"type": "string"},
    },
    "required": ["correct", "reasoning_score", "explanation"],
}


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse_json_obj(text: str) -> dict:
    """Ollama's MLX engine ignores `format`, so accept fenced / wrapped JSON."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError(f"no JSON object in grader reply: {text[:120]!r}")
    return json.loads(m.group(0))


class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.meta = mc.job_meta(job_id)
        self.p = self.meta.get("params", {})
        self.rd = mc.results_dir(job_id)
        self.state_path = os.path.join(self.rd, "state.json")
        self.state = mc.read_json(self.state_path, {}) or {}
        self.state.setdefault("done_phases", [])
        self.state.setdefault("rates", {})
        self.prog = mc.Progress(job_id, PHASES)
        student = self.p.get("STUDENT", "qwen3-0.6b")
        if student not in mc.STUDENTS:
            raise SystemExit(f"Unknown student model {student!r}")
        self.student = student
        self.model_path = os.path.join(mc.MLX_MODELS_DIR, mc.STUDENTS[student]["dir"])
        if not os.path.exists(os.path.join(self.model_path, "config.json")):
            raise SystemExit(f"Student model folder missing: {self.model_path}")
        self.data_dir = os.path.join(self.rd, "data")
        self.eval_dir = os.path.join(self.rd, "eval")
        self.adapter_dir = os.path.join(self.rd, "adapters")
        self.ckpt_dir = os.path.join(self.adapter_dir, "checkpoints")
        for d in (self.data_dir, self.eval_dir, self.adapter_dir, self.ckpt_dir):
            os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------ helpers
    def save_state(self):
        with mc.critical():
            mc.write_json_atomic(self.state_path, self.state)

    def done(self, phase):
        return phase in self.state["done_phases"]

    def mark(self, phase):
        if phase not in self.state["done_phases"]:
            self.state["done_phases"].append(phase)
        self.save_state()
        self.prog.finish_phase()

    def param(self, key, default, cast=float):
        v = self.p.get(key)
        if v in (None, "", 0, "0", "auto"):
            return default
        try:
            return cast(v)
        except (TypeError, ValueError):
            return default

    def eta_total(self, current_eta):
        """Best-effort overall remaining time from measured per-item rates."""
        n_valid = self.state.get("n_eval") or 0
        r = self.state["rates"]
        est = current_eta or 0
        cur = self.prog.data.get("phase")
        pending = [p for p in PHASES if not self.done(p) and p != cur]
        for p in pending:
            if p == "train":
                est += (self.state.get("iters_total") or 0) * r.get("train", 0.5)
            elif p.startswith("answer"):
                est += n_valid * r.get("answer_base", r.get("answer_trained", 2.0))
            elif p.startswith("grade"):
                est += n_valid * r.get("grade_base", r.get("grade_trained", 3.0))
            elif p == "loss":
                est += 60
            elif p in ("fuse", "register"):
                est += 120
        return int(est)

    def update(self, done, **extra):
        self.prog.update(done, **extra)
        self.prog.data["eta_total_sec"] = self.eta_total(self.prog.data.get("eta_sec"))

    def stream(self, cmd, on_line=None, cwd=None):
        mc.log("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                cwd=cwd, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        try:
            last = None
            for line in proc.stdout:
                segs = [x for x in ANSI.sub("", line).rstrip("\n").split("\r") if x.strip()]
                clean = (segs[-1] + "\n") if segs else ""
                if not clean.strip() or clean == last or any(ch in clean for ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
                    continue
                last = clean
                sys.stdout.write(clean if clean.endswith("\n") else clean + "\n")
                sys.stdout.flush()
                line = clean
                if on_line:
                    on_line(line)
            proc.wait()
        except BaseException:
            try:
                proc.terminate()
                proc.wait(timeout=20)
            except Exception:  # noqa: BLE001
                proc.kill()
            raise
        return proc.returncode

    # ------------------------------------------------------------ phases
    def phase_split(self):
        src = self.meta["inputFile"]
        pairs = mc.read_pairs(src)
        if len(pairs) < 5:
            raise SystemExit(f"Need at least 5 valid {{prompt, completion}} lines; found {len(pairs)} in the upload.")
        groups: dict[str, list] = {}
        for pr in pairs:
            groups.setdefault(mc.norm_prompt(pr["prompt"]), []).append(pr)
        keys = sorted(groups)
        random.Random(42).shuffle(keys)
        target_valid = max(1, round(0.2 * len(pairs)))
        valid, train = [], []
        for k in keys:
            (valid if len(valid) < target_valid else train).extend(groups[k])
        if not train:
            raise SystemExit("Not enough distinct prompts to make a training split.")
        for name, rows in (("train", train), ("valid", valid)):
            with open(os.path.join(self.data_dir, f"{name}.jsonl"), "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.makedirs(os.path.join(self.rd, "eval_data"), exist_ok=True)
        shutil.copy(os.path.join(self.data_dir, "valid.jsonl"), os.path.join(self.rd, "eval_data", "test.jsonl"))
        self.state.update({"n_total": len(pairs), "n_train": len(train), "n_valid": len(valid),
                           "n_duplicate_prompt_groups": sum(1 for g in groups.values() if len(g) > 1)})
        mc.log(f"Split {len(pairs)} pairs -> train {len(train)} / valid {len(valid)} (seed 42, duplicates kept together)")

    def train_config(self):
        n_train, n_valid = self.state["n_train"], self.state["n_valid"]
        bs = int(self.param("BATCH_SIZE", 4, int))
        bs = max(1, min(bs, n_train, n_valid))
        epochs = self.param("EPOCHS", 2.0)
        auto_iters = int(min(3000, max(20, math.ceil(epochs * n_train / bs))))
        iters = int(self.param("ITERS", auto_iters, int))
        cfg = {
            "batch_size": bs, "iters": iters, "auto_iters": auto_iters, "epochs": epochs,
            "learning_rate": self.param("LEARNING_RATE", 1e-4),
            "num_layers": int(self.param("NUM_LAYERS", 16, int)),
            "max_seq_length": int(self.param("MAX_SEQ_LENGTH", 2048, int)),
            "save_every": int(self.param("SAVE_EVERY", 100, int)),
        }
        cfg["steps_per_eval"] = max(10, iters // 4)
        cfg["val_batches"] = min(25, max(1, n_valid // bs))
        return cfg

    def _normalize_checkpoints(self):
        """Move mlx_lm's per-segment numbered checkpoints to absolute step numbers."""
        off = self.state.get("segment_offset", 0)
        with mc.critical():
            for f in glob.glob(os.path.join(self.adapter_dir, "*_adapters.safetensors")):
                n = int(os.path.basename(f).split("_")[0])
                shutil.move(f, os.path.join(self.ckpt_dir, f"{off + n:07d}_adapters.safetensors"))
            ckpts = sorted(glob.glob(os.path.join(self.ckpt_dir, "*_adapters.safetensors")))
            for f in ckpts[:-3]:  # keep the 3 most recent checkpoints
                os.remove(f)

    def _latest_checkpoint(self):
        best, path = 0, None
        for f in glob.glob(os.path.join(self.ckpt_dir, "*_adapters.safetensors")):
            n = int(os.path.basename(f).split("_")[0])
            if n > best:
                best, path = n, f
        return best, path

    def phase_train(self):
        cfg = self.state.get("train_cfg") or self.train_config()
        self.state["train_cfg"] = cfg
        self.state["iters_total"] = cfg["iters"]
        self.save_state()
        self._normalize_checkpoints()
        done_iters, ckpt = self._latest_checkpoint()
        total = cfg["iters"]
        self.prog.start_phase("train", total, done_iters, "steps", PHASE_LABELS["train"])
        if done_iters >= total and os.path.exists(os.path.join(self.adapter_dir, "adapters.safetensors")):
            mc.log("Training already complete (checkpoint at final step).")
            return
        remaining = total - done_iters
        self.state["segment_offset"] = done_iters
        self.save_state()
        cmd = [os.path.join(VENV_BIN, "mlx_lm.lora"), "--model", self.model_path, "--train",
               "--data", self.data_dir, "--adapter-path", self.adapter_dir, "--mask-prompt",
               "--iters", str(remaining), "--batch-size", str(cfg["batch_size"]),
               "--learning-rate", str(cfg["learning_rate"]), "--num-layers", str(cfg["num_layers"]),
               "--max-seq-length", str(cfg["max_seq_length"]), "--save-every", str(cfg["save_every"]),
               "--steps-per-eval", str(cfg["steps_per_eval"]), "--val-batches", str(cfg["val_batches"]),
               "--steps-per-report", "10", "--seed", "42"]
        if ckpt:
            mc.log(f"Resuming training from step {done_iters} ({os.path.basename(ckpt)})")
            cmd += ["--resume-adapter-file", ckpt]
        t0 = time.time()
        losses = self.state.setdefault("train_losses", [])

        def on_line(line):
            m = re.search(r"Iter (\d+): Train loss ([\d.]+)", line)
            if m:
                it = done_iters + int(m.group(1))
                losses.append([it, float(m.group(2))])
                self.update(it, train_loss=float(m.group(2)))
                if it > done_iters:
                    self.state["rates"]["train"] = (time.time() - t0) / (it - done_iters)
            m = re.search(r"Iter (\d+): Val loss ([\d.]+)", line)
            if m:
                self.state.setdefault("val_losses", []).append([done_iters + int(m.group(1)), float(m.group(2))])
                self.prog.set_extra(val_loss=float(m.group(2)))

        rc = self.stream(cmd, on_line)
        self._normalize_checkpoints()
        if rc != 0:
            raise SystemExit(f"mlx_lm.lora failed with exit code {rc}")
        # final weights are adapters/adapters.safetensors; record a final checkpoint too
        shutil.copy(os.path.join(self.adapter_dir, "adapters.safetensors"),
                    os.path.join(self.ckpt_dir, f"{total:07d}_adapters.safetensors"))
        self.update(total)

    def phase_loss(self):
        cfg = self.state["train_cfg"]
        self.prog.start_phase("loss", 2, len(self.state.get("loss", {})), "runs", PHASE_LABELS["loss"])
        res = self.state.setdefault("loss", {})
        for i, (key, adapter) in enumerate((("base", ""), ("trained", self.adapter_dir))):
            if key in res:
                continue
            out = {}

            def on_line(line, out=out):
                m = re.search(r"Test loss (\d+(?:\.\d+)?), Test ppl (\d+(?:\.\d+)?|inf)", line)
                if m:
                    out["loss"], out["ppl"] = float(m.group(1)), float(m.group(2))

            cmd = [os.path.join(VENV_BIN, "mlx_lm.lora"), "--model", self.model_path, "--test",
                   "--data", os.path.join(self.rd, "eval_data"), "--adapter-path", adapter, "--mask-prompt",
                   "--batch-size", str(cfg["batch_size"]), "--test-batches", "-1",
                   "--max-seq-length", str(cfg["max_seq_length"])]
            rc = self.stream(cmd, on_line)
            if rc != 0 or "loss" not in out:
                raise SystemExit(f"Validation loss run ({key}) failed")
            res[key] = out
            self.save_state()
            self.update(i + 1)
        mc.log(f"Validation loss: untrained {res['base']['loss']:.3f} (ppl {res['base']['ppl']:.2f}) -> "
               f"trained {res['trained']['loss']:.3f} (ppl {res['trained']['ppl']:.2f})")

    def eval_items(self):
        rows = mc.read_pairs(os.path.join(self.data_dir, "valid.jsonl"))
        cap = int(self.param("EVAL_MAX_ITEMS", 0, int))
        if cap > 0:
            rows = rows[:cap]
        self.state["n_eval"] = len(rows)
        return rows

    @staticmethod
    def _read_done(path):
        done = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                        done[o["i"]] = o
                    except (json.JSONDecodeError, KeyError):
                        pass
        return done

    def phase_answer(self, which):
        phase = f"answer_{which}"
        rows = self.eval_items()
        out_path = os.path.join(self.eval_dir, f"answers_{which}.jsonl")
        done = self._read_done(out_path)
        todo = [i for i in range(len(rows)) if i not in done]
        self.prog.start_phase(phase, len(rows), len(done), "answers", PHASE_LABELS[phase])
        if not todo:
            return
        from mlx_lm import load, generate  # heavy import only when needed
        try:
            from mlx_lm import batch_generate
        except ImportError:
            batch_generate = None
        mc.log(f"Loading {self.student} ({which}) for answering {len(todo)} prompts")
        if which == "trained":
            model, tok = load(self.model_path, adapter_path=self.adapter_dir)
        else:
            model, tok = load(self.model_path)
        max_tokens = int(self.param("MAX_ANSWER_TOKENS", 768, int))
        chunk = int(self.param("ANSWER_BATCH", 8, int))

        def encode(p):
            try:
                return tok.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True,
                                               enable_thinking=False)
            except TypeError:
                return tok.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True)

        t0, n_done_here = time.time(), 0
        for s in range(0, len(todo), chunk):
            mc.check_stop()
            idx = todo[s:s + chunk]
            prompts = [encode(rows[i]["prompt"]) for i in idx]
            texts = None
            if batch_generate is not None:
                try:
                    texts = batch_generate(model, tok, prompts, max_tokens=max_tokens).texts
                except mc.StopRequested:
                    raise
                except Exception as e:  # noqa: BLE001
                    mc.log(f"batch_generate unavailable ({e}); falling back to one-at-a-time")
                    batch_generate = None
            if texts is None:
                texts = [generate(model, tok, prompt=p, max_tokens=max_tokens) for p in prompts]
            with mc.critical():
                with open(out_path, "a", encoding="utf-8") as f:
                    for i, t in zip(idx, texts):
                        t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
                        f.write(json.dumps({"i": i, "answer": t}, ensure_ascii=False) + "\n")
            n_done_here += len(idx)
            self.state["rates"][phase] = (time.time() - t0) / n_done_here
            self.update(len(done) + n_done_here)
            self.save_state()
        del model
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    def phase_grade(self, which):
        phase = f"grade_{which}"
        rows = self.eval_items()
        answers = self._read_done(os.path.join(self.eval_dir, f"answers_{which}.jsonl"))
        out_path = os.path.join(self.eval_dir, f"grades_{which}.jsonl")
        done = self._read_done(out_path)
        todo = [i for i in range(len(rows)) if i not in done]
        teacher = self.state.get("teacher") or mc.pick_teacher()
        self.state["teacher"] = teacher
        self.prog.start_phase(phase, len(rows), len(done), "grades", PHASE_LABELS[phase])
        t0, n_here = time.time(), 0
        for i in todo:
            mc.check_stop()
            ans = (answers.get(i) or {}).get("answer", "")
            user = (f"QUESTION:\n{rows[i]['prompt']}\n\nREFERENCE ANSWER:\n{rows[i]['completion']}\n\n"
                    f"STUDENT ANSWER:\n{ans[:4000] if ans else '(no answer)'}")
            g = None
            for attempt in range(3):
                try:
                    r = mc.teacher_chat(teacher, [{"role": "system", "content": GRADER_SYSTEM},
                                                  {"role": "user", "content": user}],
                                        temperature=0.0 if attempt == 0 else 0.3, num_predict=400, num_ctx=8192, fmt=GRADE_SCHEMA, timeout=600)
                    g = parse_json_obj(r["message"].get("content", ""))
                    g = {"correct": bool(g["correct"]), "reasoning_score": max(1, min(5, int(g["reasoning_score"]))),
                         "explanation": str(g.get("explanation", ""))[:400]}
                    break
                except mc.StopRequested:
                    raise
                except Exception as e:  # noqa: BLE001
                    mc.log(f"grade item {i} attempt {attempt + 1} failed: {e}")
                    time.sleep(3)
            if g is None:
                g = {"correct": False, "reasoning_score": 1, "explanation": "grader failed", "grader_error": True}
            with mc.critical():
                with open(out_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"i": i, **g}, ensure_ascii=False) + "\n")
            n_here += 1
            self.state["rates"][phase] = (time.time() - t0) / n_here
            self.update(len(done) + n_here)
            if n_here % 10 == 0:
                self.save_state()
        self.save_state()

    def phase_report(self):
        rows = self.eval_items()
        self.prog.start_phase("report", 1, 0, "steps", PHASE_LABELS["report"])
        ans = {w: self._read_done(os.path.join(self.eval_dir, f"answers_{w}.jsonl")) for w in ("base", "trained")}
        grd = {w: self._read_done(os.path.join(self.eval_dir, f"grades_{w}.jsonl")) for w in ("base", "trained")}

        def summary(w):
            g = [grd[w][i] for i in range(len(rows)) if i in grd[w]]
            n = len(g)
            return {"graded": n, "correct": sum(1 for x in g if x["correct"]),
                    "accuracy": round(sum(1 for x in g if x["correct"]) / n, 4) if n else None,
                    "avg_reasoning_score": round(sum(x["reasoning_score"] for x in g) / n, 3) if n else None,
                    "grader_errors": sum(1 for x in g if x.get("grader_error"))}

        base, trained = summary("base"), summary("trained")
        # examples: prefer items that flipped from wrong to right, then others
        order = sorted(range(len(rows)), key=lambda i: (
            -(int(grd["trained"].get(i, {}).get("correct", False)) - int(grd["base"].get(i, {}).get("correct", False))), i))
        examples = []
        for i in order[:5]:
            examples.append({"prompt": rows[i]["prompt"], "reference": rows[i]["completion"],
                             "untrained_answer": ans["base"].get(i, {}).get("answer", ""),
                             "untrained_grade": grd["base"].get(i),
                             "trained_answer": ans["trained"].get(i, {}).get("answer", ""),
                             "trained_grade": grd["trained"].get(i)})
        cfg = self.state.get("train_cfg", {})
        report = {
            "job_id": self.id, "owner": self.meta.get("ownerName"), "student": self.student,
            "student_path": self.model_path, "teacher_grader": self.state.get("teacher"),
            "data": {k: self.state.get(k) for k in ("n_total", "n_train", "n_valid", "n_eval")},
            "training": {k: cfg.get(k) for k in ("iters", "batch_size", "learning_rate", "num_layers", "epochs")},
            "final_train_loss": (self.state.get("train_losses") or [[None, None]])[-1][1],
            "validation_loss": self.state.get("loss"),
            "untrained": base, "trained": trained,
            "accuracy_change": (round(trained["accuracy"] - base["accuracy"], 4)
                                if base["accuracy"] is not None and trained["accuracy"] is not None else None),
            "examples": examples, "created_at": mc.now_iso(),
        }
        mc.write_json_atomic(os.path.join(self.rd, "eval_report.json"), report)

        def pct(x):
            return "n/a" if x is None else f"{100 * x:.1f}%"

        L = self.state.get("loss", {})
        md = [f"# Evaluation report: {mc.STUDENTS[self.student]['label']} ({self.meta.get('ownerName') or ''})", "",
              f"- Job: `{self.id}`",
              f"- Data: {report['data']['n_total']} pairs -> {report['data']['n_train']} train / "
              f"{report['data']['n_valid']} held out (all {report['data']['n_eval']} held-out prompts evaluated)",
              f"- Training: {cfg.get('iters')} steps, batch {cfg.get('batch_size')}, learning rate {cfg.get('learning_rate')}",
              f"- Grader: {report['teacher_grader']}", "",
              "| | Untrained | Trained |", "|---|---|---|",
              f"| Accuracy (final answer correct) | {pct(base['accuracy'])} ({base['correct']}/{base['graded']}) | "
              f"{pct(trained['accuracy'])} ({trained['correct']}/{trained['graded']}) |",
              f"| Average reasoning score (1-5) | {base['avg_reasoning_score']} | {trained['avg_reasoning_score']} |",
              f"| Validation loss | {L.get('base', {}).get('loss')} | {L.get('trained', {}).get('loss')} |",
              f"| Validation perplexity | {L.get('base', {}).get('ppl')} | {L.get('trained', {}).get('ppl')} |", "",
              "## Examples", ""]
        for n, ex in enumerate(examples, 1):
            def g(x):
                return "not graded" if not x else f"{'correct' if x['correct'] else 'incorrect'}, reasoning {x['reasoning_score']}/5"
            md += [f"### Example {n}", "", "**Prompt**", "", "```", ex["prompt"], "```", "",
                   "**Reference answer**", "", "```", ex["reference"], "```", "",
                   f"**Untrained answer** ({g(ex['untrained_grade'])})", "", "```", ex["untrained_answer"][:2000], "```", "",
                   f"**Trained answer** ({g(ex['trained_grade'])})", "", "```", ex["trained_answer"][:2000], "```", ""]
        with open(os.path.join(self.rd, "eval_report.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        self.update(1)
        mc.log(f"Eval: accuracy {pct(base['accuracy'])} -> {pct(trained['accuracy'])}; "
               f"reasoning {base['avg_reasoning_score']} -> {trained['avg_reasoning_score']}")

    def phase_fuse(self):
        self.prog.start_phase("fuse", 2, 0, "steps", PHASE_LABELS["fuse"])
        fused = os.path.join(self.rd, "fused-model")
        if not os.path.exists(os.path.join(fused, "config.json")):
            shutil.rmtree(fused, ignore_errors=True)
            rc = self.stream([os.path.join(VENV_BIN, "mlx_lm.fuse"), "--model", self.model_path,
                              "--adapter-path", self.adapter_dir, "--save-path", fused])
            if rc != 0:
                raise SystemExit("mlx_lm.fuse failed")
        self.update(1)
        deq = os.path.join(self.rd, "ollama-import-tmp")
        if not os.path.exists(os.path.join(deq, "config.json")) and not self.done("register"):
            shutil.rmtree(deq, ignore_errors=True)
            rc = self.stream([os.path.join(VENV_BIN, "mlx_lm.fuse"), "--model", self.model_path,
                              "--adapter-path", self.adapter_dir, "--save-path", deq, "--dequantize"])
            if rc != 0:
                raise SystemExit("mlx_lm.fuse --dequantize failed")
        self.update(2)

    def phase_register(self):
        self.prog.start_phase("register", 1, 0, "steps", PHASE_LABELS["register"])
        existing = {m["name"] for m in mc.ollama_request("/api/tags", timeout=30).get("models", [])}
        tag = self.state.get("ollama_tag")
        if tag and (tag in existing or f"{tag}:latest" in existing):
            mc.log(f"Already registered as {tag}")
        else:
            name = self.p.get("MODEL_NAME") or self.meta.get("ownerName") or "model"
            base = f"{mc.STUDENTS[self.student]['tag_prefix']}-{mc.slugify(name)}-{datetime.now():%Y%m%d}"
            tag, k = base, 1
            while tag in existing or f"{tag}:latest" in existing:
                k += 1
                tag = f"{base}-{k}"
            self.state["ollama_tag"] = tag
            self.save_state()
            deq = os.path.join(self.rd, "ollama-import-tmp")
            with open(os.path.join(deq, "Modelfile"), "w") as f:
                f.write("FROM .\nRENDERER qwen3.5\nPARSER qwen3.5\nPARAMETER temperature 0.6\n"
                        "PARAMETER top_k 20\nPARAMETER top_p 0.95\n")
            ollama = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
            rc = self.stream([ollama, "create", "--experimental", "-q", "int4", tag, "-f", "Modelfile"], cwd=deq)
            if rc != 0:
                raise SystemExit(f"ollama create failed for {tag}")
            with mc.critical():
                reg = mc.read_json(mc.OLLAMA_REGISTRY, []) or []
                reg = [r for r in reg if r.get("tag") != tag]
                reg.append({"tag": tag, "owner": self.meta.get("ownerName"), "jobId": self.id,
                            "student": self.student, "createdAt": mc.now_iso()})
                mc.write_json_atomic(mc.OLLAMA_REGISTRY, reg)
        # smoke test the registered model
        try:
            r = mc.teacher_chat(tag, [{"role": "user", "content": "Reply with the single word: ready"}],
                                temperature=0, num_predict=20, num_ctx=2048, timeout=300)
            mc.log(f"Ollama smoke test ({tag}): {r['message']['content'][:80]!r}")
            self.state["ollama_smoke"] = r["message"]["content"][:200]
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"Registered {tag} but it failed to answer: {e}")
        shutil.rmtree(os.path.join(self.rd, "ollama-import-tmp"), ignore_errors=True)
        self.prog.set_extra(ollama_tag=tag)
        self.update(1)
        mc.log(f"Model available in Ollama as: {tag}")

    def run(self):
        mc.log(f"Train job {self.id}: student={self.student} input={self.meta.get('inputFile')}")
        steps = {
            "split": self.phase_split, "train": self.phase_train, "loss": self.phase_loss,
            "answer_base": lambda: self.phase_answer("base"), "answer_trained": lambda: self.phase_answer("trained"),
            "grade_base": lambda: self.phase_grade("base"), "grade_trained": lambda: self.phase_grade("trained"),
            "report": self.phase_report, "fuse": self.phase_fuse, "register": self.phase_register,
        }
        for ph in PHASES:
            if self.done(ph):
                continue
            mc.check_stop()
            mc.log(f"== Phase {PHASES.index(ph) + 1}/{len(PHASES)}: {PHASE_LABELS[ph]}")
            if ph == "split":
                self.prog.start_phase("split", 1, 0, "steps", PHASE_LABELS["split"])
            steps[ph]()
            self.mark(ph)
        self.prog.data["eta_total_sec"] = 0
        self.prog.set_extra(output_path=os.path.join(self.rd, "eval_report.md"))
        mc.log("All phases complete.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    args = ap.parse_args()
    mc.run_main(args.job_id, lambda: Job(args.job_id).run())


if __name__ == "__main__":
    main()

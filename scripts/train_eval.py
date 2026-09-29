#!/usr/bin/env python3
"""Train job: split -> LoRA train -> evaluate -> fuse -> register in Ollama -> GGUF download -> Teacher advice.

Every phase is resumable. State lives in results/<id>/state.json; per-item
evaluation results are appended to jsonl files so a pause/crash never loses
more than one item (or, for training, more than SAVE_EVERY steps).

Training defaults: 2 passes over the data, or 1 pass when there are more than
BIG_SET training items (EPOCHS / ITERS overrides always win). Best checkpoint:
adapters are saved every SAVE_EVERY steps (auto: about 10 saves per run) and the
validation loss is measured at every save on a fixed held-out subset
(data_lora/valid.jsonl, up to 25 batches, the same items every time). mlx_lm
measures "Iter N: Val loss" just BEFORE update N but saves checkpoint N just
AFTER it, so those numbers only shortlist candidates: after training the 3 best
candidates plus the final checkpoint are re-scored exactly (mlx_lm.lora --test
on the same subset), and the checkpoint with the lowest exact loss becomes
adapters/adapters.safetensors (the final weights are kept as
adapters/final_adapters.safetensors), so evaluation, fuse, Ollama and the GGUF
download all use it. The choice is logged and stored in state.json
"best_checkpoint" and eval_report.json.

Usage: train_eval.py --job-id ID
       train_eval.py --advice-only ID   (re-run only the Teacher analysis of missed questions)
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

try:  # grounded grading is optional: never let it break training
    import doc_sources as ds  # noqa: E402
except Exception:  # noqa: BLE001
    ds = None

VENV_BIN = os.path.dirname(sys.executable)
PHASES = ["split", "train", "loss", "answer_base", "answer_trained", "grade_base", "grade_trained",
          "report", "fuse", "register", "gguf", "advice"]
PHASE_LABELS = {
    "split": "Splitting data 80/20", "train": "Training", "loss": "Measuring validation loss",
    "answer_base": "Untrained student answering held-out questions",
    "answer_trained": "Trained student answering held-out questions",
    "grade_base": "Teacher grading untrained answers", "grade_trained": "Teacher grading trained answers",
    "report": "Writing evaluation report", "fuse": "Fusing adapters into a 4-bit model",
    "register": "Registering the model in Ollama",
    "gguf": "Building the Ollama download (GGUF)",
    "advice": "Teacher analyzing missed questions",
}

ADVICE_MAX_ITEMS = 40
ADVICE_MAX_CHARS = 110000  # advice prompt incl. the whole documents must fit in a 32k-token context
BIG_SET = 1000          # more training items than this: default 1 epoch instead of 2
VAL_MAX_BATCHES = 25    # size of the fixed validation subset used during training (batches)
ADVICE_SYSTEM = """You are an expert machine-learning coach helping a high-school student improve a small language model (the Student) that was fine-tuned on question/answer pairs written by a larger Teacher model. You are shown the evaluation summary and a sample of held-out questions the TRAINED Student got wrong, with the answer key (expected answer), the Student's answer and the grader's note.

Write a short, concrete analysis in Markdown with exactly these two sections:

## Patterns in the misses
2 to 4 bullets. Each names one pattern (a topic, a question type, a format or final-answer mistake, or answer-key items that look wrong or ambiguous) and cites 1-3 example item numbers like (#12, #31). Say how many of the sampled misses it roughly covers.

## Next steps to raise the score
3 to 5 numbered, specific actions (for example: which topics to generate more questions on and roughly how many, how to change the question-generation prompt, which answer-key items to fix or drop, training settings to change, uploading source documents). Tie each to a pattern or to the summary numbers.

Rules: be specific to THIS data; no generic filler; under 350 words total; plain Markdown (headings, bullets, **bold**), no tables, no code blocks. If an answer key looks wrong, say so plainly.
When SOURCE DOCUMENTS are included, they are the whole official material: check facts against them before calling anything invented. Never call a fact a hallucination if the SOURCE DOCUMENTS state it; a SOURCE SECTION is only one part of the documents, so a fact missing from it may still be correct."""

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

GROUNDED_GRADER_ADDENDUM = """

This question was written from an official SOURCE PASSAGE, which is included below the question. Grade against the SOURCE PASSAGE: the student's final answer is correct only if it agrees with what the source says (exact numbers, limits, rule numbers and conditions matter). If the reference answer and the source disagree, trust the source. Do not reward facts that contradict the source, and do not penalize correct facts from the source that the reference left out."""

ALL_DOCS_GRADER_ADDENDUM = """

This question was written from official SOURCE DOCUMENTS, included in full (the section the question was written from is repeated after the student answer). Grade against the SOURCE DOCUMENTS: the student's final answer is correct only if it agrees with what the documents say (exact numbers, limits, names, codes and conditions matter). A fact is correct if ANY of the documents states it, even when it is not in the first section. If the reference answer and the documents disagree, trust the documents. Do not reward facts that contradict the documents, and do not penalize correct facts from the documents that the reference left out."""

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
            elif p in ("fuse", "register", "gguf"):
                est += 120
            elif p == "advice":
                est += 90
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
        epochs_default = 1.0 if n_train > BIG_SET else 2.0
        epochs = self.param("EPOCHS", epochs_default)
        auto_iters = int(min(3000, max(20, math.ceil(epochs * n_train / bs))))
        iters = int(self.param("ITERS", auto_iters, int))
        save_every = int(self.param("SAVE_EVERY", max(5, min(100, math.ceil(iters / 10))), int))
        save_every = max(1, min(save_every, iters))
        cfg = {
            "batch_size": bs, "iters": iters, "auto_iters": auto_iters, "epochs": epochs,
            "epochs_default": epochs_default, "epochs_user": self.p.get("EPOCHS") not in (None, "", 0, "0", "auto"),
            "iters_user": self.p.get("ITERS") not in (None, "", 0, "0", "auto"),
            "learning_rate": self.param("LEARNING_RATE", 1e-4),
            "num_layers": int(self.param("NUM_LAYERS", 16, int)),
            "max_seq_length": int(self.param("MAX_SEQ_LENGTH", 2048, int)),
            "save_every": save_every,
            # validation loss at every save, on the same fixed subset each time -> comparable across checkpoints
            "steps_per_eval": save_every,
            "val_items": max(bs, min(n_valid, VAL_MAX_BATCHES * bs) // bs * bs),
            "val_batches": -1,
            "select_best": True,
        }
        return cfg

    def lora_data_dir(self, cfg):
        """mlx_lm --data folder: all training items + a fixed validation subset (same items at every eval)."""
        if not cfg.get("select_best"):
            return self.data_dir
        d = os.path.join(self.rd, "data_lora")
        os.makedirs(d, exist_ok=True)
        shutil.copy(os.path.join(self.data_dir, "train.jsonl"), os.path.join(d, "train.jsonl"))
        with open(os.path.join(self.data_dir, "valid.jsonl"), encoding="utf-8") as f:
            rows = [line for line in f if line.strip()][:cfg["val_items"]]
        with open(os.path.join(d, "valid.jsonl"), "w", encoding="utf-8") as f:
            f.writelines(rows)
        return d

    def _normalize_checkpoints(self):
        """Move mlx_lm's per-segment numbered checkpoints to absolute step numbers."""
        off = self.state.get("segment_offset", 0)
        with mc.critical():
            for f in glob.glob(os.path.join(self.adapter_dir, "*_adapters.safetensors")):
                n = int(os.path.basename(f).split("_")[0])
                shutil.move(f, os.path.join(self.ckpt_dir, f"{off + n:07d}_adapters.safetensors"))
            ckpts = sorted(glob.glob(os.path.join(self.ckpt_dir, "*_adapters.safetensors")))
            keep = set(ckpts[-3:])  # the 3 most recent checkpoints (resume) ...
            for _, step in self._ranked_steps(ckpts)[:3]:  # ... plus the 3 best-scoring candidates so far
                keep.add(os.path.join(self.ckpt_dir, f"{step:07d}_adapters.safetensors"))
            for f in ckpts:
                if f not in keep:
                    os.remove(f)

    def _val_by_step(self):
        return {int(st): float(v) for st, v in (self.state.get("val_losses") or []) if st and int(st) > 1}

    def _ranked_steps(self, ckpt_paths):
        """[(approx val_loss, step)] of saved checkpoints, best first. Approximate: mlx_lm reports
        "Iter N: Val loss" just before update N, while checkpoint N is saved just after it."""
        if not (self.state.get("train_cfg") or {}).get("select_best"):
            return []
        vl = self._val_by_step()
        cands = []
        for f in ckpt_paths:
            n = int(os.path.basename(f).split("_")[0])
            if n in vl:
                cands.append((vl[n], -n))
        return [(loss, -neg) for loss, neg in sorted(cands)]

    def _score_adapter(self, ckpt_path, cfg):
        """Exact loss of one checkpoint on the fixed validation subset (mlx_lm.lora --test)."""
        tmp = os.path.join(self.adapter_dir, "score_tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        shutil.copy(os.path.join(self.adapter_dir, "adapter_config.json"), os.path.join(tmp, "adapter_config.json"))
        os.symlink(ckpt_path, os.path.join(tmp, "adapters.safetensors"))
        test_dir = os.path.join(self.rd, "data_lora_test")
        os.makedirs(test_dir, exist_ok=True)
        shutil.copy(os.path.join(self.rd, "data_lora", "valid.jsonl"), os.path.join(test_dir, "test.jsonl"))
        out = {}

        def on_line(line):
            m = re.search(r"Test loss (\d+(?:\.\d+)?|nan|inf)", line)
            if m:
                out["loss"] = float(m.group(1))

        try:
            rc = self.stream([os.path.join(VENV_BIN, "mlx_lm.lora"), "--model", self.model_path, "--test",
                              "--data", test_dir, "--adapter-path", tmp, "--mask-prompt",
                              "--batch-size", str(cfg["batch_size"]), "--test-batches", "-1",
                              "--max-seq-length", str(cfg["max_seq_length"])], on_line)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        loss = out.get("loss")
        if rc != 0 or loss is None or not math.isfinite(loss):
            return None
        return loss

    def select_best_checkpoint(self):
        """Make the lowest-validation-loss checkpoint the model used by eval, fuse, Ollama and GGUF."""
        cfg = self.state.get("train_cfg") or {}
        if not cfg.get("select_best"):
            return
        total = cfg["iters"]
        ckpts = sorted(glob.glob(os.path.join(self.ckpt_dir, "*_adapters.safetensors")))
        final_path = os.path.join(self.ckpt_dir, f"{total:07d}_adapters.safetensors")
        vl = self._val_by_step()
        ranked = self._ranked_steps(ckpts)
        final_copy = os.path.join(self.adapter_dir, "final_adapters.safetensors")
        # exact re-score of the shortlisted candidates + the final checkpoint (cached in state for resume)
        scores = {int(k): v for k, v in (self.state.get("checkpoint_scores") or {}).items()}
        cand_steps = [st for _, st in ranked[:3]] + ([total] if os.path.exists(final_path) else [])
        cand_steps = list(dict.fromkeys(cand_steps))
        if cand_steps:
            mc.log(f"Best checkpoint: re-scoring steps {', '.join(map(str, cand_steps))} on the {cfg.get('val_items')} "
                   "validation items")
        for st in cand_steps:
            if st in scores:
                continue
            sc = self._score_adapter(os.path.join(self.ckpt_dir, f"{st:07d}_adapters.safetensors"), cfg)
            scores[st] = sc
            self.state["checkpoint_scores"] = {str(k): v for k, v in scores.items()}
            self.save_state()
        exact = [(v, -k) for k, v in scores.items() if v is not None and k in cand_steps]
        best = None
        if exact:
            loss, neg = min(exact)
            best = (loss, -neg)
        with mc.critical():
            if os.path.exists(final_path) and not os.path.exists(final_copy):
                shutil.copy(final_path, final_copy)
            info = {"final_step": total, "final_val_loss": scores.get(total), "save_every": cfg.get("save_every"),
                    "scores": {str(k): v for k, v in sorted(scores.items())}, "approx_val": {str(k): v for k, v in sorted(vl.items())},
                    "checkpoints_scored": sum(1 for f in ckpts if int(os.path.basename(f).split("_")[0]) in vl),
                    "val_items": cfg.get("val_items")}
            if best is None or not os.path.exists(final_path):
                if os.path.exists(final_path):
                    shutil.copy(final_path, os.path.join(self.adapter_dir, "adapters.safetensors"))
                info.update({"step": total, "val_loss": scores.get(total), "used": "final",
                             "note": "no scored checkpoints; using the final weights"})
            else:
                loss, step = best
                src = os.path.join(self.ckpt_dir, f"{step:07d}_adapters.safetensors")
                shutil.copy(src, os.path.join(self.adapter_dir, "adapters.safetensors"))
                info.update({"step": step, "val_loss": loss, "used": "final" if step == total else "best"})
            self.state["best_checkpoint"] = info
            self.save_state()
        if info["used"] == "best":
            mc.log(f"Best checkpoint: step {info['step']} of {total} (validation loss {info['val_loss']:.3f}; "
                   f"final step {info['final_val_loss'] if info['final_val_loss'] is None else round(info['final_val_loss'], 3)}). "
                   "Using it for evaluation, fuse, Ollama and the GGUF download; final weights kept as "
                   "adapters/final_adapters.safetensors.")
        else:
            mc.log(f"Best checkpoint: the final step {total} had the lowest validation loss"
                   + (f" ({info['val_loss']:.3f})" if info.get("val_loss") is not None else "") + "; using the final weights.")
        self.prog.set_extra(best_step=info["step"], best_val_loss=info.get("val_loss"), final_step=total)

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
            if "best_checkpoint" not in self.state:
                self.select_best_checkpoint()
            return
        remaining = total - done_iters
        self.state["segment_offset"] = done_iters
        self.save_state()
        if cfg.get("select_best"):
            mc.log(f"Settings: {total} steps ({cfg['epochs']} epoch(s)"
                   + ("" if cfg.get("epochs_user") or cfg.get("iters_user") else
                      f", default for {self.state['n_train']} training items") + f"), batch {cfg['batch_size']}; "
                   f"checkpoint + validation loss every {cfg['save_every']} steps on {cfg['val_items']} held-out items")
        cmd = [os.path.join(VENV_BIN, "mlx_lm.lora"), "--model", self.model_path, "--train",
               "--data", self.lora_data_dir(cfg), "--adapter-path", self.adapter_dir, "--mask-prompt",
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
                self.save_state()  # keep val losses across a pause: they pick the best checkpoint

        rc = self.stream(cmd, on_line)
        self._normalize_checkpoints()
        if rc != 0:
            raise SystemExit(f"mlx_lm.lora failed with exit code {rc}")
        # final weights are adapters/adapters.safetensors; record a final checkpoint too
        shutil.copy(os.path.join(self.adapter_dir, "adapters.safetensors"),
                    os.path.join(self.ckpt_dir, f"{total:07d}_adapters.safetensors"))
        self.select_best_checkpoint()
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

    def grounding(self, rows):
        """Source passages for held-out items that came from a grounded Generate Questions job (else None)."""
        if getattr(self, "_grounding", None) is not None and len(self._grounding) == len(rows):
            return self._grounding
        passages = [None] * len(rows)
        if ds is not None and self.p.get("GROUNDED_GRADING", "1") not in ("0", 0, "off", "false"):
            try:
                passages, info = ds.lookup_sources([r["prompt"] for r in rows], exclude_job=self.id)
                self._all_docs = info.get("all_docs") or [None] * len(rows)
                if info["matched"]:
                    mc.log(f"Grounded grading: {info['matched']}/{info['total']} held-out questions matched to source "
                           f"document sections (jobs: {', '.join(info['jobs'])}); the teacher grades those against the source.")
                else:
                    mc.log("Grounded grading: no held-out questions matched a source document; grading against the reference only.")
                self.state["grounded_grading"] = {"matched": info["matched"], "total": info["total"],
                                                  "jobs": list(info["jobs"]),
                                                  "all_docs_fallback": info.get("all_docs_matched", 0)}
            except Exception as e:  # noqa: BLE001
                mc.log(f"Grounded grading lookup failed ({e}); grading against the reference only.")
                passages = [None] * len(rows)
        self._grounding = passages
        if len(getattr(self, "_all_docs", None) or []) != len(rows):
            self._all_docs = [None] * len(rows)
        return passages

    def all_docs(self, rows):
        """Whole-document text per held-out item (for the grading fallback), aligned with grounding(rows)."""
        self.grounding(rows)
        return self._all_docs

    def _grade_call(self, teacher, row, ans, passage, docs=None):
        user, system, ctx = self.grade_message(row, ans, passage, docs)
        for attempt in range(3):
            try:
                r = mc.teacher_chat(teacher, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                    temperature=0.0 if attempt == 0 else 0.3, num_predict=400, num_ctx=ctx,
                                    fmt=GRADE_SCHEMA, timeout=600)
                g = parse_json_obj(r["message"].get("content", ""))
                return {"correct": bool(g["correct"]), "reasoning_score": max(1, min(5, int(g["reasoning_score"]))),
                        "explanation": str(g.get("explanation", ""))[:400]}
            except mc.StopRequested:
                raise
            except Exception as e:  # noqa: BLE001
                mc.log(f"grade attempt {attempt + 1} failed: {e}")
                time.sleep(3)
        return None

    def grade_item(self, teacher, row, ans, passage, docs):
        """Grade against the matched section; a miss is re-graded once against all the uploaded documents (when they
        are short enough to show whole), so a correct fact from another document is not marked wrong."""
        g = self._grade_call(teacher, row, ans, passage)
        if g is None:
            return {"correct": False, "reasoning_score": 1, "explanation": "grader failed", "grader_error": True}
        if passage and docs and not g["correct"]:
            g2 = self._grade_call(teacher, row, ans, passage, docs)
            if g2 is not None:
                g2["all_docs_checked"] = True
                if g2["correct"]:
                    g2["regraded"] = "all_docs"
                    g2["section_explanation"] = g["explanation"]
                return g2
        return g

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
        passages = self.grounding(rows) if todo else [None] * len(rows)
        docs = self.all_docs(rows) if todo else [None] * len(rows)
        t0, n_here = time.time(), 0
        for i in todo:
            mc.check_stop()
            ans = (answers.get(i) or {}).get("answer", "")
            g = self.grade_item(teacher, rows[i], ans, passages[i], docs[i])
            with mc.critical():
                with open(out_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"i": i, **g, **({"grounded": True} if passages[i] else {})}, ensure_ascii=False) + "\n")
            n_here += 1
            self.state["rates"][phase] = (time.time() - t0) / n_here
            self.update(len(done) + n_here)
            if n_here % 10 == 0:
                self.save_state()
        self.save_state()

    @staticmethod
    def grade_message(row, ans, passage, docs=None):
        """(user message, system prompt, num_ctx) for one grading call."""
        user = (f"QUESTION:\n{row['prompt']}\n\nREFERENCE ANSWER:\n{row['completion']}\n\n"
                f"STUDENT ANSWER:\n{ans[:4000] if ans else '(no answer)'}")
        if not passage:
            return user, GRADER_SYSTEM, 8192
        if docs:
            # the documents go in the system prompt (identical for every item) so the Teacher can reuse its cached
            # prefix; only the question, answers and matched section change from call to call
            system = (GRADER_SYSTEM + ALL_DOCS_GRADER_ADDENDUM
                      + "\n\nSOURCE DOCUMENTS (all official documents uploaded for this subject)\n" + docs)
            user = f"{user}\n\nSECTION THE QUESTION WAS WRITTEN FROM:\n{passage}"
            return user, system, 32768
        user = f"{user}\n\nSOURCE PASSAGE (official document this question was written from):\n{passage}"
        return user, GRADER_SYSTEM + GROUNDED_GRADER_ADDENDUM, 16384

    def train_loss_at_checkpoint(self):
        """Last reported train loss at or before the kept checkpoint (compare with the kept model's val loss)."""
        bc = self.state.get("best_checkpoint") or {}
        pts = [l for st, l in (self.state.get("train_losses") or []) if bc.get("step") is None or st <= bc["step"]]
        return pts[-1] if pts else None

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
        regraded = sum(1 for w in ("base", "trained") for x in grd[w].values() if x.get("regraded"))
        base["regraded_all_docs"] = sum(1 for x in grd["base"].values() if x.get("regraded"))
        trained["regraded_all_docs"] = sum(1 for x in grd["trained"].values() if x.get("regraded"))
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
            "grounded_grading": self.state.get("grounded_grading"),
            "best_checkpoint": self.state.get("best_checkpoint"),
            "train_loss_at_checkpoint": self.train_loss_at_checkpoint(),
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
              *([f"- Checkpoint used: step {bc['step']} of {bc['final_step']}"
                 + (f" (lowest validation loss {bc['val_loss']:.3f}" if bc.get('val_loss') is not None else " (")
                 + (f"; final step {bc['final_val_loss']:.3f})" if bc.get('used') == 'best' and bc.get('final_val_loss') is not None else ")")]
                if (bc := self.state.get("best_checkpoint")) else []),
              f"- Grader: {report['teacher_grader']}"
              + (f" (graded against source documents for {report['grounded_grading']['matched']} of "
                 f"{report['grounded_grading']['total']} questions"
                 + (f"; misses re-checked against all uploaded documents, {regraded} changed to correct"
                    if (report['grounded_grading'].get('all_docs_fallback')) else "") + ")"
                 if (report.get("grounded_grading") or {}).get("matched") else ""), "",
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
        # ollama-import-tmp (the dequantized model) is reused by phase_gguf, which deletes it
        self.prog.set_extra(ollama_tag=tag)
        self.update(1)
        mc.log(f"Model available in Ollama as: {tag}")

    def phase_gguf(self):
        """Portable Ollama package (ollama-gguf.zip). Best effort: a failure is recorded in
        gguf_status.json / state.json and never fails the train job."""
        self.prog.start_phase("gguf", 1, 0, "steps", PHASE_LABELS["gguf"])
        deq = os.path.join(self.rd, "ollama-import-tmp")
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_gguf.py"),
               self.id, "--force"]
        if os.path.exists(os.path.join(deq, "config.json")):
            cmd += ["--hf-dir", deq]
        try:
            rc = self.stream(cmd)
            if rc != 0:
                raise RuntimeError(f"export_gguf.py exited with code {rc}")
            self.state.pop("gguf_error", None)
        except mc.StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            st = mc.read_json(os.path.join(self.rd, "gguf_status.json"), {}) or {}
            if st.get("state") != "failed":
                mc.write_json_atomic(os.path.join(self.rd, "gguf_status.json"),
                                     {**st, "state": "failed", "message": "Build failed", "error": msg[:1000],
                                      "pid": None, "finished_at": mc.now_iso()})
            self.state["gguf_error"] = (st.get("error") or msg)[:1000]
            mc.log(f"WARNING: Ollama GGUF download was not built ({self.state['gguf_error'][:300]}). "
                   "The trained model is fine; the fused MLX model and Ollama tag are still available.")
        shutil.rmtree(deq, ignore_errors=True)
        self.update(1)

    # ------------------------------------------------------------ advice
    def build_advice(self) -> dict:
        """Ask the Teacher to analyze the held-out questions the trained Student got wrong.
        Writes advice.json + advice.md. Raises on failure (callers decide how to handle it)."""
        rows = self.eval_items()
        ans = self._read_done(os.path.join(self.eval_dir, "answers_trained.jsonl"))
        grd = {w: self._read_done(os.path.join(self.eval_dir, f"grades_{w}.jsonl")) for w in ("base", "trained")}
        wrong = [i for i in range(len(rows))
                 if i in grd["trained"] and not grd["trained"][i].get("correct") and not grd["trained"][i].get("grader_error")]
        report = mc.read_json(os.path.join(self.rd, "eval_report.json"), {}) or {}
        out = {"job_id": self.id, "n_eval": len(rows), "n_wrong": len(wrong), "n_sampled": 0,
               "teacher": None, "markdown": None, "error": None, "created_at": mc.now_iso()}
        if not wrong:
            out["markdown"] = ("## Patterns in the misses\n- The trained Student answered every graded held-out question "
                               "correctly, so there are no misses to analyze.\n")
            return self._save_advice(out)
        # regressions (untrained right, trained wrong) first, then the rest; spread evenly, deterministic
        regress = [i for i in wrong if grd["base"].get(i, {}).get("correct")]
        rest = [i for i in wrong if i not in set(regress)]
        rng = random.Random(7)
        pick = regress[:ADVICE_MAX_ITEMS // 2]
        pick += sorted(rng.sample(rest, min(len(rest), ADVICE_MAX_ITEMS - len(pick))))
        if len(pick) < ADVICE_MAX_ITEMS:
            pick += [i for i in regress if i not in set(pick)][:ADVICE_MAX_ITEMS - len(pick)]
        pick = sorted(pick)
        grounded = bool((self.state.get("grounded_grading") or {}).get("matched"))
        passages = self.grounding(rows) if grounded else [None] * len(rows)

        def clip(t, n, tail=False):
            t = re.sub(r"\s+\n", "\n", str(t or "").strip())
            if len(t) <= n:
                return t
            return ("…" + t[-n:]) if tail else (t[:n] + "…")

        items = []
        for k, i in enumerate(pick, 1):
            g = grd["trained"][i]
            block = [f"### Item #{i}" + (" (untrained Student had this RIGHT)" if i in regress else ""),
                     f"QUESTION: {clip(rows[i]['prompt'], 600)}",
                     f"EXPECTED ANSWER (answer key): {clip(rows[i]['completion'], 400, tail=True)}",
                     f"STUDENT ANSWER: {clip((ans.get(i) or {}).get('answer', ''), 400, tail=True) or '(no answer)'}",
                     f"GRADER NOTE: {clip(g.get('explanation'), 220)}"]
            if passages[i]:
                block.append(f"SOURCE SECTION: {clip(passages[i], 500)}")
            items.append("\n".join(block))
        U, T = report.get("untrained") or {}, report.get("trained") or {}
        L = report.get("validation_loss") or self.state.get("loss") or {}
        cfg = self.state.get("train_cfg") or {}

        def pct(x):
            return "n/a" if x is None else f"{100 * x:.1f}%"

        summary = "\n".join([
            f"Student model: {mc.STUDENTS[self.student]['label']}",
            f"Training items: {self.state.get('n_train')}; held-out items evaluated: {len(rows)}",
            f"Final answer correct: untrained {pct(U.get('accuracy'))} -> trained {pct(T.get('accuracy'))}",
            f"Average reasoning score (1-5): untrained {U.get('avg_reasoning_score')} -> trained {T.get('avg_reasoning_score')}",
            f"Validation loss: untrained {(L.get('base') or {}).get('loss')} -> trained {(L.get('trained') or {}).get('loss')}; "
            f"final train loss {report.get('final_train_loss')}",
            f"Training settings: {cfg.get('iters')} steps, {cfg.get('epochs')} epochs, batch {cfg.get('batch_size')}, "
            f"learning rate {cfg.get('learning_rate')}, {cfg.get('num_layers')} LoRA layers",
            f"Checkpoint used: step {(self.state.get('best_checkpoint') or {}).get('step', cfg.get('iters'))} of {cfg.get('iters')} "
            f"(the one with the lowest validation loss during training)",
            f"Questions grounded in source documents: {'yes' if grounded else 'no'}",
            f"The trained Student got {len(wrong)} of {len(rows)} held-out questions wrong "
            f"({len(regress)} of those the untrained Student had right). {len(pick)} are shown below.",
        ])
        user = f"EVALUATION SUMMARY\n{summary}\n\nMISSED QUESTIONS (sample)\n\n" + "\n\n".join(items)
        docs_all = next((d for d in (self.all_docs(rows) if grounded else []) if d), None)
        if docs_all and len(user) + len(docs_all) <= ADVICE_MAX_CHARS:
            user += ("\n\nSOURCE DOCUMENTS (the whole official material the questions were written from; check facts "
                     "here before calling anything invented)\n" + docs_all)
        teacher = self.state.get("teacher") or report.get("teacher_grader") or mc.pick_teacher()
        ctx = 40960 if len(user) > 90000 else 32768 if len(user) > 40000 else 24576 if len(user) > 24000 else 16384
        mc.log(f"Advice: asking {teacher} to analyze {len(pick)} of {len(wrong)} missed questions "
               f"({len(user):,} chars, num_ctx {ctx})")
        t0 = time.time()
        text = ""
        for attempt in range(2):
            r = mc.teacher_chat(teacher, [{"role": "system", "content": ADVICE_SYSTEM}, {"role": "user", "content": user}],
                                temperature=0.3, num_predict=1400, num_ctx=ctx, timeout=1200, think=False)
            text = re.sub(r"<think>.*?</think>", "", r["message"].get("content", "") or "", flags=re.S).strip()
            if len(text) > 80:
                break
            mc.log(f"Advice attempt {attempt + 1}: reply too short ({len(text)} chars); retrying")
        if len(text) <= 80:
            raise RuntimeError(f"Teacher reply was empty or too short: {text[:120]!r}")
        out.update({"teacher": teacher, "n_sampled": len(pick), "n_regressions": len(regress), "items": pick,
                    "markdown": text, "seconds": round(time.time() - t0, 1), "created_at": mc.now_iso()})
        mc.log(f"Advice written in {out['seconds']:.0f}s ({len(text)} chars)")
        return self._save_advice(out)

    def _save_advice(self, out: dict) -> dict:
        mc.write_json_atomic(os.path.join(self.rd, "advice.json"), out)
        if out.get("markdown"):
            with open(os.path.join(self.rd, "advice.md"), "w", encoding="utf-8") as f:
                f.write("# Teacher's analysis of missed questions\n\n" + out["markdown"].strip() + "\n")
        return out

    def phase_advice(self):
        """Best effort: a failure is recorded in advice.json / state.json and never fails the job."""
        self.prog.start_phase("advice", 1, 0, "steps", PHASE_LABELS["advice"])
        try:
            self.build_advice()
            self.state.pop("advice_error", None)
        except mc.StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"[:1000]
            self.state["advice_error"] = msg
            try:
                mc.write_json_atomic(os.path.join(self.rd, "advice.json"),
                                     {"job_id": self.id, "markdown": None, "error": msg, "created_at": mc.now_iso()})
            except Exception:  # noqa: BLE001
                pass
            mc.log(f"WARNING: Teacher analysis of missed questions failed ({msg[:300]}). The job is otherwise complete.")
        self.update(1)

    @staticmethod
    def _write_grades(path, grades):
        with mc.critical():
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for i in sorted(grades):
                    f.write(json.dumps({"i": i, **{k: v for k, v in grades[i].items() if k != "i"}},
                                       ensure_ascii=False) + "\n")
            os.replace(tmp, path)

    def regrade(self) -> dict:
        """--regrade: re-check the misses of a finished job against all uploaded documents, then rewrite the report and
        the advice. Does not touch the job's status; progress/phase display is left as it was."""
        rows = self.eval_items()
        passages, docs = self.grounding(rows), self.all_docs(rows)
        teacher = self.state.get("teacher") or mc.pick_teacher()
        changed = {}
        for w in ("trained", "base"):
            path = os.path.join(self.eval_dir, f"grades_{w}.jsonl")
            answers = self._read_done(os.path.join(self.eval_dir, f"answers_{w}.jsonl"))
            grades = self._read_done(path)
            n = 0
            for i, g in grades.items():
                if g.get("correct") or g.get("grader_error") or g.get("all_docs_checked") or not (passages[i] and docs[i]):
                    continue
                g2 = self._grade_call(teacher, rows[i], (answers.get(i) or {}).get("answer", ""), passages[i], docs[i])
                if g2 is None:
                    continue
                g2["all_docs_checked"] = True
                if g2["correct"]:
                    g2["regraded"] = "all_docs"
                    g2["section_explanation"] = g.get("explanation")
                    n += 1
                else:
                    g2 = {**g, "all_docs_checked": True}
                grades[i] = {**({"grounded": True} if g.get("grounded") else {}), **g2}
                self._write_grades(path, grades)  # after every item, so an interrupted regrade resumes
            self._write_grades(path, grades)
            changed[w] = sum(1 for x in grades.values() if x.get("regraded"))
            mc.log(f"Regrade ({w}): {changed[w]} miss(es) changed to correct after checking all uploaded documents")
        self.save_state()

        class _NoProgress:  # keep the finished job's progress display untouched
            data: dict = {}

            def start_phase(self, *a, **k):
                pass

            def update(self, *a, **k):
                pass

            def finish_phase(self, *a, **k):
                pass

            def set_extra(self, *a, **k):
                pass

        self.prog = _NoProgress()
        self.phase_report()
        return changed

    def run(self):
        mc.log(f"Train job {self.id}: student={self.student} input={self.meta.get('inputFile')}")
        steps = {
            "split": self.phase_split, "train": self.phase_train, "loss": self.phase_loss,
            "answer_base": lambda: self.phase_answer("base"), "answer_trained": lambda: self.phase_answer("trained"),
            "grade_base": lambda: self.phase_grade("base"), "grade_trained": lambda: self.phase_grade("trained"),
            "report": self.phase_report, "fuse": self.phase_fuse, "register": self.phase_register,
            "gguf": self.phase_gguf, "advice": self.phase_advice,
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
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--job-id")
    g.add_argument("--advice-only", metavar="JOB_ID",
                   help="Only (re)write advice.json for a finished train job; does not touch progress or status.")
    g.add_argument("--regrade", metavar="JOB_ID",
                   help="Re-check a finished train job's misses against all uploaded documents, then rewrite the "
                        "evaluation report and advice; does not touch status.")
    args = ap.parse_args()
    if args.regrade:
        job = Job(args.regrade)
        if not os.path.exists(os.path.join(job.rd, "eval", "grades_trained.jsonl")):
            raise SystemExit("This job has no graded evaluation yet.")
        changed = job.regrade()
        out = job.build_advice()
        print(json.dumps({"changed_to_correct": changed, "advice_error": out.get("error")}))
        return
    if args.advice_only:
        job = Job(args.advice_only)
        if not os.path.exists(os.path.join(job.rd, "eval", "grades_trained.jsonl")):
            raise SystemExit("This job has no graded evaluation yet.")
        out = job.build_advice()
        print(json.dumps({k: v for k, v in out.items() if k not in ("markdown", "items")}))
        print(out.get("markdown") or "")
        return
    mc.run_main(args.job_id, lambda: Job(args.job_id).run())


if __name__ == "__main__":
    main()

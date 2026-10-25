"""Local language model worker, run as its own process: `python -m classifiles.llm_worker --model …`.

Started by the service for the second-opinion step of a scan and stopped right after, so all GPU
memory is released. It talks over stdin/stdout, one JSON object per line (no network port):

    -> {"id": 1, "task": "choose", "prompt": "...", "options": ["A", "B", "C"]}
    <- {"id": 1, "probs": {"A": 0.81, "B": 0.12, "C": 0.07}}

"choose" reads the model's probabilities for the option letters as the next token after the
prompt: one forward pass, no text generation, so the answer is always one of the options
(SemIf-style). Later tasks (e.g. naming folders or files) can be added next to it.

Everything runs locally: weights are downloaded once from Hugging Face (nothing is uploaded),
then loaded offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

SYSTEM = ("You sort files into folders. You are given a file and up to three candidate folders, "
          "each with examples of files already stored there. Answer with the letter of the folder "
          "the file belongs in, and nothing else.")


def _load(model_name: str, gpu_fraction: float, threads: int):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        torch.cuda.set_per_process_memory_fraction(gpu_fraction)
    kwargs = {"dtype": torch.bfloat16} if device == "cuda" else {}
    try:  # offline first; download only if the model isn't cached yet
        tok = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(model_name, local_files_only=True, **kwargs)
    except OSError:
        tok = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name, **kwargs)
    os.environ["HF_HUB_OFFLINE"] = "1"
    model.to(device).eval()
    return tok, model, device


def _letter_ids(tok, letter: str) -> list[int]:
    """Token ids that start the answer `letter` ("A", " A"), whichever exist as single tokens."""
    ids = set()
    for variant in (letter, " " + letter):
        enc = tok.encode(variant, add_special_tokens=False)
        if len(enc) == 1:
            ids.add(enc[0])
    return sorted(ids)


def _chat(tok, prompt: str) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    try:
        return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:  # templates without the thinking switch
        return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def choose(tok, model, device, prompt: str, options: list[str], max_tokens: int = 2048) -> dict[str, float]:
    import torch

    text = _chat(tok, prompt)
    inputs = tok(text, return_tensors="pt", truncation=True, max_length=max_tokens).to(device)
    with torch.inference_mode():
        logits = model(**inputs).logits[0, -1].float()
    logp = torch.log_softmax(logits, dim=-1)
    scores = []
    for opt in options:
        ids = _letter_ids(tok, opt)
        scores.append(torch.logsumexp(logp[ids], dim=0) if ids else torch.tensor(-1e9))
    probs = torch.softmax(torch.stack(scores), dim=0).tolist()
    return {opt: round(p, 5) for opt, p in zip(options, probs)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--gpu-fraction", type=float, default=0.7)
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args(argv)
    try:
        os.nice(10)
    except OSError:
        pass
    out = sys.stdout
    try:
        tok, model, device = _load(args.model, args.gpu_fraction, args.threads)
    except Exception as e:  # noqa: BLE001 - reported to the parent
        out.write(json.dumps({"ready": False, "error": str(e)[:500]}) + "\n")
        out.flush()
        return
    info = {"ready": True, "model": args.model, "device": device}
    if device == "cuda":
        import torch

        info["gpu_gb"] = round(torch.cuda.memory_allocated() / 1e9, 2)
    out.write(json.dumps(info) + "\n")
    out.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if req.get("task") == "shutdown":
                break
            if req.get("task") == "choose":
                res = {"id": req.get("id"), "probs": choose(tok, model, device, req["prompt"], req["options"])}
            else:
                res = {"id": req.get("id"), "error": f"unknown task {req.get('task')!r}"}
        except Exception as e:  # noqa: BLE001 - one bad request must not stop the worker
            res = {"id": req.get("id") if isinstance(req, dict) else None, "error": str(e)[:500]}
        out.write(json.dumps(res) + "\n")
        out.flush()


if __name__ == "__main__":
    main()

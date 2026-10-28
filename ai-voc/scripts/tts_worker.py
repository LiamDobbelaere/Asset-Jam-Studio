"""Persistent Qwen3-TTS worker for the studio web UI: keeps the model loaded between lines.

    python -I scripts/tts_worker.py

Protocol: one JSON object per line on stdin, one JSON object per line on stdout. Everything the
libraries print goes to stderr, so stdout stays a clean channel.

Requests:
    {"id": "...", "text": "...", "voice": "liam", "mood": "upset"|null, "mood_timbre": false,
     "language": "English", "seed": 3|null, "temperature": 0.9|null, "top_k": ..., "top_p": ...,
     "repetition_penalty": ..., "out": "D:/.../file.wav", "model": null|"path", "speaker": null|"name"}
    {"cmd": "unload"}      free the GPU, keep the process
    {"cmd": "quit"}

Responses: {"id", "event": "loading"|"generating"|"done"|"error", ...}; {"event": "ready"} at start.
"""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import load_tts, log, reference_for, voice_dir  # noqa: E402

_real_stdout = sys.stdout
sys.stdout = sys.stderr   # keep library chatter off the protocol channel


def send(obj):
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


class Worker:
    def __init__(self):
        self.model = None
        self.model_path = None
        self.dtype = None
        self.prompts = {}   # (ref, ref_text, timbre_ref) -> voice clone prompt

    def ensure_model(self, req):
        path = req.get("model") or None
        dtype = req.get("dtype") or "float16"
        if self.model is not None and (self.model_path, self.dtype) == (path, dtype):
            return
        self.unload()
        send({"id": req.get("id"), "event": "loading", "model": path or "Qwen3-TTS-12Hz-1.7B-Base"})
        self.model = load_tts(path, dtype)
        self.model_path, self.dtype = path, dtype

    def unload(self):
        if self.model is None:
            return
        import torch
        self.model = None
        self.prompts.clear()
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        log("tts worker: model unloaded")

    def clone_prompt(self, ref, ref_text, timbre_ref):
        key = (ref, ref_text, timbre_ref)
        if key not in self.prompts:
            prompt = self.model.create_voice_clone_prompt(ref_audio=ref, ref_text=ref_text)
            if timbre_ref:
                neutral = self.model.create_voice_clone_prompt(ref_audio=timbre_ref, x_vector_only_mode=True)
                prompt[0].ref_spk_embedding = neutral[0].ref_spk_embedding
            self.prompts[key] = prompt
        return self.prompts[key]

    def handle(self, req):
        import soundfile as sf
        rid = req.get("id")
        text = req["text"]
        voice = req["voice"]
        mood = req.get("mood") or None
        speaker = req.get("speaker") or None
        ref = ref_text = timbre_ref = None
        if not speaker:
            if req.get("ref"):
                ref, ref_text = req["ref"], req.get("ref_text")
                if not ref_text:
                    rt = Path(ref).with_suffix(".txt")
                    if not rt.exists():
                        raise ValueError("ref_text is required with a custom reference clip")
                    ref_text = rt.read_text(encoding="utf-8").strip()
            else:
                vd = voice_dir(voice)
                ref, ref_text = reference_for(vd, mood)
                if mood and not req.get("mood_timbre"):
                    timbre_ref, _ = reference_for(vd)
        self.ensure_model(req)
        import torch
        if req.get("seed") is not None:
            torch.manual_seed(int(req["seed"]))
        gen = {k: req[k] for k in ("temperature", "top_k", "top_p", "repetition_penalty", "max_new_tokens")
               if req.get(k) is not None}
        send({"id": rid, "event": "generating", "reference": ref, "timbre": timbre_ref})
        t0 = time.time()
        torch.cuda.reset_peak_memory_stats()
        lang = req.get("language") or "English"
        if speaker:
            wavs, sr = self.model.generate_custom_voice(text=[text], language=[lang], speaker=[speaker], **gen)
        else:
            prompt = self.clone_prompt(ref, ref_text, timbre_ref)
            wavs, sr = self.model.generate_voice_clone(text=[text], language=[lang], voice_clone_prompt=prompt, **gen)
        dt = time.time() - t0
        out = Path(req["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, wavs[0], sr)
        dur = len(wavs[0]) / sr
        send({"id": rid, "event": "done", "path": str(out), "duration": round(dur, 2), "seconds": round(dt, 1),
              "realtime": round(dur / max(dt, 1e-6), 2), "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)})

    def loop(self):
        send({"event": "ready"})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                send({"event": "error", "error": "bad json"})
                continue
            cmd = req.get("cmd")
            if cmd == "quit":
                break
            if cmd == "unload":
                self.unload()
                send({"event": "unloaded"})
                continue
            if cmd == "ping":
                send({"event": "pong", "loaded": self.model is not None})
                continue
            try:
                self.handle(req)
            except (SystemExit, Exception) as e:   # voice_dir/reference_for raise SystemExit with a helpful message
                traceback.print_exc()
                send({"id": req.get("id"), "event": "error", "error": str(e) or e.__class__.__name__})


if __name__ == "__main__":
    Worker().loop()

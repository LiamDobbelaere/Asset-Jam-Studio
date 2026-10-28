"""The ai-voc side: voice/mood discovery and the persistent TTS worker process."""
import json
import os
import wave

from .paths import AIVOC, VOC_OUTPUT, VOICES, VOC_PY, file_url
from .procworker import LineWorker

WORKER_SCRIPT = os.path.join(AIVOC, "scripts", "tts_worker.py")

# What the README documents as steering the delivery, and what is still untested (todo/ai-voc-improvements.md).
TEXT_TIPS = {
    "documented": [
        {"mark": "...", "effect": "a pause and a trailing-off, hesitant delivery"},
        {"mark": "!", "effect": "emphasis and energy on the sentence"},
        {"mark": "?", "effect": "rising, questioning intonation"},
        {"mark": "Oh! Hey, Sam.", "effect": "short interjections and names with commas give natural beats"},
        {"mark": "--mood", "effect": "the biggest lever: the emotion comes from the mood reference clip, not from words"},
        {"mark": "--seed", "effect": "same text + same seed = same take; try a few seeds and keep the best"},
        {"mark": "temperature 0.7", "effect": "steadier, flatter read (default 0.9); raise for more variation"},
    ],
    "untested": [
        {"mark": "?!", "effect": "surprised question"},
        {"mark": "—", "effect": "an abrupt cut-off mid sentence"},
        {"mark": "CAPS", "effect": "shouting"},
        {"mark": "nooo, yyyears", "effect": "stretched letters for drawn-out words"},
        {"mark": "ugh, hmm, huh", "effect": "stage-direction sounds written as words"},
    ],
    "note": "Qwen3-TTS-Base has no 'say it sadly' instruction input; it continues the reference clip, so punctuation "
            "and the mood reference are the only levers. Keep lines as full sentences; a lone 'URGH' gives it nothing to continue.",
}


def wav_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return None


def list_voices():
    voices = []
    if not os.path.isdir(VOICES):
        return voices
    for name in sorted(os.listdir(VOICES)):
        d = os.path.join(VOICES, name)
        ref = os.path.join(d, "ref.wav")
        if not os.path.isfile(ref):
            continue
        v = {"name": name, "ref_url": file_url(ref), "ref_text": "", "ref_duration": wav_duration(ref),
             "clips": 0, "clip_seconds": 0, "moods": []}
        txt = os.path.join(d, "ref.txt")
        if os.path.isfile(txt):
            v["ref_text"] = open(txt, encoding="utf-8").read().strip()
        cj = os.path.join(d, "clips.jsonl")
        if os.path.isfile(cj):
            rows = [json.loads(l) for l in open(cj, encoding="utf-8") if l.strip()]
            v["clips"] = len(rows)
            v["clip_seconds"] = round(sum(r.get("duration", 0) for r in rows))
        for fn in sorted(os.listdir(d)):
            if fn.startswith("ref_") and fn.endswith(".wav"):
                mood = fn[4:-4]
                mt = os.path.join(d, f"ref_{mood}.txt")
                if os.path.isfile(mt):
                    v["moods"].append({"name": mood, "text": open(mt, encoding="utf-8").read().strip(),
                                       "url": file_url(os.path.join(d, fn)),
                                       "duration": wav_duration(os.path.join(d, fn))})
        # fine-tuned checkpoints: output/<voice>-ft*/ with a config.json
        v["finetuned"] = []
        if os.path.isdir(VOC_OUTPUT):
            for fn in sorted(os.listdir(VOC_OUTPUT)):
                p = os.path.join(VOC_OUTPUT, fn)
                if fn.startswith(name + "-ft") and os.path.isdir(p) and os.path.isfile(os.path.join(p, "config.json")):
                    v["finetuned"].append({"name": fn, "path": p})
        voices.append(v)
    return voices


def _voice_path(name):
    if not name or any(c in name for c in "/\\."):
        raise ValueError("bad voice name")
    d = os.path.join(VOICES, name)
    if not os.path.isdir(d):
        raise FileNotFoundError(f"no voice '{name}'")
    return d


def voice_clips(name):
    """Every clip of a prepared voice with its transcript and a playable URL, plus the reference texts."""
    d = _voice_path(name)
    rows = []
    cj = os.path.join(d, "clips.jsonl")
    if os.path.isfile(cj):
        for i, l in enumerate(l for l in open(cj, encoding="utf-8") if l.strip()):
            r = json.loads(l)
            wav = os.path.join(d, r.get("audio", ""))
            rows.append({"index": i, "number": os.path.splitext(os.path.basename(r.get("audio", "")))[0], "text": r.get("text", ""),
                         "duration": r.get("duration"), "source": r.get("source", ""),
                         "url": file_url(wav) if os.path.isfile(wav) else None})
    refs = {}
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".txt") and (fn == "ref.txt" or fn.startswith("ref_")):
            refs[fn[:-4]] = open(os.path.join(d, fn), encoding="utf-8").read().strip()
    return {"name": name, "clips": rows, "refs": refs, "has_clips": bool(rows) and all(r["url"] for r in rows)}


def save_clip_texts(name, texts):
    """texts: {index: new text}. Rewrites clips.jsonl keeping every other field."""
    d = _voice_path(name)
    cj = os.path.join(d, "clips.jsonl")
    rows = [json.loads(l) for l in open(cj, encoding="utf-8") if l.strip()]
    changed = 0
    for k, v in texts.items():
        i = int(k)
        if 0 <= i < len(rows) and rows[i].get("text") != v:
            rows[i]["text"] = v
            changed += 1
    with open(cj, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return changed


def save_ref_text(name, stem, text):
    d = _voice_path(name)
    if not (stem == "ref" or (stem.startswith("ref_") and stem[4:].isalnum())):
        raise ValueError("bad reference name")
    if not os.path.isfile(os.path.join(d, stem + ".wav")):
        raise FileNotFoundError(f"{stem}.wav does not exist")
    with open(os.path.join(d, stem + ".txt"), "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")


class TTSWorker(LineWorker):
    """Owns the tts_worker.py subprocess. One request at a time (the studio worker is single-threaded)."""

    def __init__(self):
        super().__init__("voice", VOC_PY, WORKER_SCRIPT, AIVOC, "Qwen3-TTS")

    def request(self, req, on_event, should_cancel=lambda: False, timeout=1800):
        """Send one generation request; relays loading/generating events; returns the done message."""
        def on_message(msg):
            ev = msg.get("event")
            if ev == "log":
                on_event("log", message=msg.get("message", ""))
            elif ev == "loading":
                on_event("loading", model=msg.get("model"))
            elif ev == "generating":
                self.loaded = True
                on_event("generating", reference=msg.get("reference"), timbre=msg.get("timbre"))
            elif ev == "done":
                return msg
            return None
        return super().request(req, on_message, should_cancel, timeout, on_tick=lambda s: on_event("tick", seconds=s))


tts = TTSWorker()

"""Shared helpers for the ai-voc scripts (paths, model loading, audio I/O)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
VOICES = ROOT / "voices"
OUTPUT = ROOT / "output"

TTS_MODEL = MODELS / "Qwen3-TTS-12Hz-1.7B-Base"
TTS_TOKENIZER = MODELS / "Qwen3-TTS-Tokenizer-12Hz"
ASR_MODEL = MODELS / "whisper-large-v3-turbo"

# Qwen3-TTS-12Hz decodes to 24 kHz; the fine-tuning data is kept at the same rate.
SAMPLE_RATE = 24000


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def voice_dir(name):
    d = VOICES / name
    if not (d / "ref.wav").exists():
        avail = sorted(p.name for p in VOICES.iterdir() if (p / "ref.wav").exists()) if VOICES.exists() else []
        raise SystemExit(f"voice '{name}' not prepared (no {d / 'ref.wav'}). Prepared voices: {avail or 'none'}. "
                         f"Run prep_voice.bat <folder-with-clips> --name {name}")
    return d


def list_moods(vd):
    """Mood references available for a voice: voices/<name>/ref_<mood>.wav + .txt."""
    return sorted(p.stem[4:] for p in vd.glob("ref_*.wav") if p.with_suffix(".txt").exists())


def reference_for(vd, mood=None):
    """(ref wav path, transcript) for a voice: ref.wav, or ref_<mood>.wav when a mood is given."""
    stem = f"ref_{mood}" if mood else "ref"
    wav, txt = vd / f"{stem}.wav", vd / f"{stem}.txt"
    if not (wav.exists() and txt.exists()):
        moods = list_moods(vd)
        raise SystemExit(f"voice '{vd.name}' has no mood '{mood}' (missing {wav.name}/{txt.name}). "
                         f"Available moods: {', '.join(moods) if moods else 'none'}. "
                         f"Add one with: prep_voice.bat <folder> --name {vd.name} --mood {mood} --ref \"clip name\" ...")
    return str(wav), txt.read_text(encoding="utf-8").strip()


def load_tts(model_path=None, dtype="float16"):
    """Load Qwen3-TTS on the GPU. fp16 by default because RTX 20-series (Turing) has no bf16."""
    import torch
    from qwen_tts import Qwen3TTSModel

    path = str(model_path or TTS_MODEL)
    dt = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[dtype]
    log(f"loading {path} ({dtype}) ...")
    return Qwen3TTSModel.from_pretrained(path, device_map="cuda:0", dtype=dt)

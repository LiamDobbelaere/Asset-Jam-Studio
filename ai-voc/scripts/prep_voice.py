"""Turn a folder of an actor's voice lines into a cloning reference + a fine-tuning set.

    python scripts/prep_voice.py C:/recordings/liam --name liam [--ref-seconds 12] [--max-ref-clips 3]
    python scripts/prep_voice.py C:/recordings/liam --name liam --mood upset --ref "annoyed 1" "annoyed 2"

Writes voices/<name>/:
    clips/NNN.wav   every input line as 24 kHz mono wav, silence-trimmed, peak-normalised
    clips.jsonl     {"audio", "text", "duration"} per clip, transcribed with Whisper (edit text by hand if wrong)
    ref.wav/ref.txt the cloning reference: the clearest clips concatenated up to --ref-seconds, with transcript
    ref_<mood>.wav/.txt  with --mood: a reference for one emotion, built from the named clips (speak.py --mood)

Qwen3-TTS clones from ref.wav + ref.txt (in-context mode), so a good ref is the single most
important input. 6-15 s of clean, expressive, typical-register speech works best.
"""
import argparse
import re
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import ASR_MODEL, SAMPLE_RATE, VOICES, log, read_jsonl, write_jsonl

AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


def load_clip(path):
    y, _ = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    y, _ = librosa.effects.trim(y, top_db=35)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y / peak * 0.9
    return y.astype(np.float32)


def transcribe_all(wavs, language):
    import torch
    from transformers import pipeline

    log(f"transcribing {len(wavs)} clips with {ASR_MODEL.name} ...")
    asr = pipeline("automatic-speech-recognition", model=str(ASR_MODEL), dtype=torch.float16, device="cuda:0")
    kw = {"language": language, "task": "transcribe"}
    texts = []
    for i, y in enumerate(wavs):
        out = asr({"raw": y, "sampling_rate": SAMPLE_RATE}, generate_kwargs=kw)
        t = re.sub(r"\s+", " ", out["text"]).strip()
        t = re.sub(r"(.)\1{4,}", r"\1\1\1", t)  # Whisper runs away on hums/screams ("mmmmmm..."): keep 3
        texts.append(t)
        log(f"  [{i:03d}] {t}")
    del asr
    torch.cuda.empty_cache()
    return texts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", help="folder with the actor's audio files (optional with --mood when the 24 kHz clips exist)")
    ap.add_argument("--name", required=True, help="voice name, e.g. liam")
    ap.add_argument("--language", default="en", help="Whisper language code of the clips (default en)")
    ap.add_argument("--ref-seconds", type=float, default=12.0, help="target length of ref.wav")
    ap.add_argument("--max-ref-clips", type=int, default=3, help="how many clips may be concatenated into ref.wav")
    ap.add_argument("--ref", nargs="*", help="use exactly these clips for the reference: substrings of the source file names, or clip numbers like 023")
    ap.add_argument("--mood", help="only build ref_<mood>.wav/.txt from the --ref clips (voice must already be prepared)")
    args = ap.parse_args()

    src = Path(args.folder) if args.folder else None
    if src is not None:
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in AUDIO_EXT)
        if not files:
            raise SystemExit(f"no audio files in {src}")
    elif not args.mood:
        raise SystemExit("give the folder with the actor's audio files")
    out = VOICES / args.name
    clips_dir = out / "clips"
    stem = "ref"

    if args.mood:
        # mood reference: reuse the prepared clips + transcripts, no transcription needed
        if not re.fullmatch(r"[a-z0-9]+", args.mood):
            raise SystemExit("--mood must be lowercase letters/digits, e.g. upset, happy, tired")
        if not args.ref:
            raise SystemExit("--mood needs --ref with the clip name(s) spoken in that mood")
        if not (out / "clips.jsonl").exists():
            raise SystemExit(f"prepare the voice first: prep_voice.bat <folder> --name {args.name}")
        rows = read_jsonl(out / "clips.jsonl")
        files = [(src / r["source"]) if src else Path(r["source"]) for r in rows]
        if not all((out / r["audio"]).exists() for r in rows):
            if src is None:
                raise SystemExit("the 24 kHz clips are missing; give the source folder so they can be rebuilt")
            log("24 kHz clips missing, re-converting from the source folder")
            clips_dir.mkdir(parents=True, exist_ok=True)
            for r in rows:
                sf.write(out / r["audio"], load_clip(src / r["source"]), SAMPLE_RATE)
        wavs = [sf.read(out / r["audio"], dtype="float32")[0] for r in rows]
        stem = f"ref_{args.mood}"
    else:
        clips_dir.mkdir(parents=True, exist_ok=True)
        log(f"loading {len(files)} clips from {src}")
        wavs = [load_clip(p) for p in files]
        texts = transcribe_all(wavs, args.language)

        rows = []
        for i, (p, y, t) in enumerate(zip(files, wavs, texts)):
            wp = clips_dir / f"{i:03d}.wav"
            sf.write(wp, y, SAMPLE_RATE)
            rows.append({"audio": str(wp.relative_to(out)).replace("\\", "/"), "text": t,
                         "duration": round(len(y) / SAMPLE_RATE, 2), "source": p.name})
        write_jsonl(out / "clips.jsonl", rows)

    # pick reference clips: user-chosen, else the longest clips between 2.5 s and ref-seconds that transcribed to a sentence
    if args.ref:
        # match substrings of the source file name, or a clip number like "023" (clips/023.wav)
        chosen = [i for i, p in enumerate(files)
                  if any(s.lower() in p.name.lower() or s == Path(rows[i]["audio"]).stem for s in args.ref)]
        if not chosen:
            raise SystemExit(f"--ref matched no files among {[p.name for p in files]}")
    else:
        cands = [i for i, r in enumerate(rows) if 2.5 <= r["duration"] <= args.ref_seconds and len(r["text"].split()) >= 4]
        cands.sort(key=lambda i: rows[i]["duration"], reverse=True)
        chosen, total = [], 0.0
        for i in cands:
            if len(chosen) >= args.max_ref_clips or total + rows[i]["duration"] > args.ref_seconds:
                continue
            chosen.append(i)
            total += rows[i]["duration"]
        if not chosen:
            chosen = [max(range(len(rows)), key=lambda i: rows[i]["duration"])]
        chosen.sort()
    gap = np.zeros(int(0.35 * SAMPLE_RATE), dtype=np.float32)
    parts = []
    for i in chosen:
        parts += [wavs[i], gap]
    ref = np.concatenate(parts[:-1])
    ref_text = " ".join(rows[i]["text"] for i in chosen)
    sf.write(out / f"{stem}.wav", ref, SAMPLE_RATE)
    (out / f"{stem}.txt").write_text(ref_text + "\n", encoding="utf-8")
    log(f"{stem}.wav: {len(ref) / SAMPLE_RATE:.1f} s from {[files[i].name for i in chosen]}")
    log(f"{stem}.txt: {ref_text}")
    if len(ref) / SAMPLE_RATE < 3:
        log("warning: under 3 s of reference; the clone will be unstable. Add a full sentence or two.")
    if args.mood:
        log(f"mood '{args.mood}' ready for voice '{args.name}': speak.bat \"...\" --voice {args.name} --mood {args.mood}")
    else:
        log(f"voice '{args.name}' ready: {len(rows)} clips, {sum(r['duration'] for r in rows):.0f} s total -> {out}")


if __name__ == "__main__":
    main()

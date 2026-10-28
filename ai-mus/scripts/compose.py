"""Generate game music with ACE-Step 1.5 (text -> 48 kHz stereo audio, instrumental by default).

    python scripts/compose.py "calm piano and soft strings, a quiet village at dusk" --duration 45 --loop
    python scripts/compose.py "8-bit chiptune boss battle, fast, tense" --bpm 170 --takes 3 --seed 7
    python scripts/compose.py "indie folk, acoustic guitar, warm" --lyrics lyrics.txt --language en

Options:
    --duration S        target length in seconds (10-600, default 60); the model may round it
    --loop              seamless loop: the last --crossfade seconds are blended into the start and cut off
    --takes N           N takes with consecutive seeds (seed, seed+1, ...), or random seeds without --seed
    --lyrics FILE|TEXT  sung lyrics ([Verse]/[Chorus] tags welcome); switches the track to vocal
    --bpm / --key / --timesig   pin tempo (30-300), key ("A minor", "F# major"), time signature (2,3,4,6)
    --no-think          skip the planner LM (faster, less structured; the DiT only sees the caption)
    --ref FILE          reference audio for style/timbre (text2music + reference)
    --format wav|flac|ogg
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import (DEFAULT_SHIFT, DEFAULT_STEPS, DIT_MODEL, FORMATS, LM_MODEL, OUTPUT, Engine, log, make_loop,  # noqa: E402
                    slug, trim_silence, unique_path, write_audio)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caption", help="what the music should sound like: genre, instruments, mood, tempo words")
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--crossfade", type=float, default=2.0, help="loop crossfade in seconds (default 2)")
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--lyrics", help="text file with lyrics, or the lyrics themselves")
    ap.add_argument("--language", default="en", help="vocal language code for lyrics (en, de, fr, ja, ...)")
    ap.add_argument("--bpm", type=int)
    ap.add_argument("--key", default="", help='e.g. "A minor", "F# major"')
    ap.add_argument("--timesig", default="", help="2, 3, 4 or 6")
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--lm-temperature", type=float, default=0.85)
    ap.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    ap.add_argument("--shift", type=float, default=DEFAULT_SHIFT)
    ap.add_argument("--ref", help="reference audio file (style transfer)")
    ap.add_argument("--format", default="wav", choices=FORMATS)
    ap.add_argument("--out", help="output file (single take) or folder (several takes); default output/<slug>.wav")
    ap.add_argument("--name", help="file name stem instead of a slug of the caption")
    ap.add_argument("--dit", default=DIT_MODEL)
    ap.add_argument("--lm", default=LM_MODEL, help="planner LM folder in models/ ('' for none)")
    ap.add_argument("--no-offload", action="store_true", help="keep VAE and text encoder on the GPU (needs more VRAM)")
    ap.add_argument("--json", action="store_true", help="print a JSON line per written file")
    args = ap.parse_args()

    lyrics = ""
    if args.lyrics:
        p = Path(args.lyrics)
        lyrics = p.read_text(encoding="utf-8") if p.is_file() else args.lyrics
    instrumental = not lyrics.strip()
    takes = max(1, min(args.takes, 16))
    think = not args.no_think and bool(args.lm)

    eng = Engine()
    eng.load(args.dit, (args.lm or None) if think else None, offload=not args.no_offload,
             progress=lambda kind, what, *a: log(f"{kind}: {what}"))

    stem = args.name or slug(args.caption)
    if args.out:
        out = Path(args.out)
        folder, stem = (out, stem) if takes > 1 or out.suffix == "" else (out.parent, out.stem)
    else:
        folder = OUTPUT
    written = []
    for i in range(takes):
        seed = (args.seed + i) if args.seed is not None else None
        t0 = time.time()
        last = [""]

        def progress(kind, what, ratio=None):
            if kind == "progress" and what != last[0]:
                last[0] = what
                log(f"  {int((ratio or 0) * 100):3d}%  {what}")

        r = eng.generate(args.caption, lyrics=lyrics, instrumental=instrumental, duration=args.duration, seed=seed,
                         bpm=args.bpm, keyscale=args.key, timesignature=args.timesig, think=think, steps=args.steps,
                         shift=args.shift, reference_audio=args.ref, vocal_language=args.language,
                         lm_temperature=args.lm_temperature, progress=progress)
        audio = trim_silence(r["audio"], r["sr"])
        if args.loop:
            audio = make_loop(audio, r["sr"], args.crossfade)
        base = stem if takes == 1 else f"{stem}_{i + 1}"
        path = write_audio(unique_path(folder, base, args.format), audio, r["sr"], args.format)
        dur = audio.shape[1] / r["sr"]
        import torch
        info = {"path": str(path), "seed": r["seed"], "duration": round(dur, 2), "seconds": round(time.time() - t0, 1),
                "loop": bool(args.loop), "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2), **r["meta"]}
        written.append(info)
        log(f"take {i + 1}/{takes}: {dur:.1f} s in {info['seconds']} s, seed {r['seed']} -> {path}")
        if args.json:
            print(json.dumps(info), flush=True)
    if not args.json:
        for w in written:
            print(w["path"])


if __name__ == "__main__":
    main()

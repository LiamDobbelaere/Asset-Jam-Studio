"""Generate game sound effects with MOSS-SoundEffect v2.0 (text -> 48 kHz mono audio, up to 30 s).

    python scripts/sfx.py "a huge explosion with debris falling" --duration 4
    python scripts/sfx.py "a single pistol gunshot in a small room" --duration 1.5 --takes 4
    python scripts/sfx.py "steady rain on a tin roof" --duration 20 --loop

Options:
    --duration S        length in seconds (0.5-30, default 4); the prompt is told the length too
    --takes N           N takes with consecutive seeds (seed, seed+1, ...), or random seeds without --seed
    --loop              seamless loop for ambiences: the last --crossfade seconds are blended into the start
    --steps N           solver steps (default 50; upstream uses 100, little audible difference)
    --cfg X             guidance (default 4.0); higher follows the prompt more literally
    --negative TEXT     what to steer away from ("music, speech")
    --no-trim           keep leading/trailing silence
    --highpass HZ       low-cut against sub-bass drift (default 30; 0 = off)
    --peak DB           peak-normalize to DB dBFS (default -1); --peak none keeps the raw level
    --format wav|flac|ogg
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import (DEFAULT_CFG, DEFAULT_SHIFT, DEFAULT_STEPS, FORMATS, MAX_SECONDS, OUTPUT, Engine, finish, log,  # noqa: E402
                    slug, unique_path, write_audio)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="what the sound is: source, action, space, character (\"a heavy wooden door creaks open slowly\")")
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--crossfade", type=float, default=1.0, help="loop crossfade in seconds (default 1)")
    ap.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    ap.add_argument("--cfg", type=float, default=DEFAULT_CFG)
    ap.add_argument("--shift", type=float, default=DEFAULT_SHIFT)
    ap.add_argument("--negative", default="")
    ap.add_argument("--no-trim", action="store_true")
    ap.add_argument("--highpass", type=float, default=30.0)
    ap.add_argument("--peak", default="-1", help="peak level in dBFS, or 'none'")
    ap.add_argument("--format", default="wav", choices=FORMATS)
    ap.add_argument("--out", help="output file (single take) or folder (several takes); default output/<slug>.wav")
    ap.add_argument("--name", help="file name stem instead of a slug of the prompt")
    ap.add_argument("--no-offload", action="store_true", help="keep the text encoder on the GPU (~6.5 GB)")
    ap.add_argument("--json", action="store_true", help="print a JSON line per written file")
    args = ap.parse_args()

    if not 0.5 <= args.duration <= MAX_SECONDS:
        ap.error(f"--duration must be between 0.5 and {MAX_SECONDS:g} s")
    takes = max(1, min(args.takes, 16))
    peak = None if str(args.peak).lower() == "none" else float(args.peak)

    eng = Engine()
    eng.load(offload=not args.no_offload, progress=lambda kind, what, *a: log(f"{kind}: {what}"))

    stem = args.name or slug(args.prompt)
    if args.out:
        out = Path(args.out)
        folder, stem = (out, stem) if takes > 1 or out.suffix == "" else (out.parent, out.stem)
    else:
        folder = OUTPUT
    import torch
    written = []
    for i in range(takes):
        seed = (args.seed + i) if args.seed is not None else None
        t0 = time.time()
        torch.cuda.reset_peak_memory_stats()
        r = eng.generate(args.prompt, duration=args.duration, seed=seed, steps=args.steps, cfg=args.cfg,
                         shift=args.shift, negative=args.negative, exact=args.loop)
        audio = finish(r["audio"], r["sr"], loop=args.loop, crossfade=args.crossfade, trim=not args.no_trim, peak_db=peak,
                       hp_hz=args.highpass)
        base = stem if takes == 1 else f"{stem}_{i + 1}"
        path = write_audio(unique_path(folder, base, args.format), audio, r["sr"], args.format)
        dur = audio.shape[1] / r["sr"]
        info = {"path": str(path), "seed": r["seed"], "duration": round(dur, 2), "seconds": round(time.time() - t0, 1),
                "loop": bool(args.loop), "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
        written.append(info)
        log(f"take {i + 1}/{takes}: {dur:.2f} s in {info['seconds']} s, seed {r['seed']} -> {path}")
        if args.json:
            print(json.dumps(info), flush=True)
    if not args.json:
        for w in written:
            print(w["path"])


if __name__ == "__main__":
    main()

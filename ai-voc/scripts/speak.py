"""Generate a dialogue line in a prepared actor's voice (zero-shot clone with Qwen3-TTS).

    python scripts/speak.py "I missed the last train home." --voice liam [--out output/liam_train.wav]
    python scripts/speak.py --voice liam --lines lines.txt           # one utterance per text line
    python scripts/speak.py "..." --voice liam --model output/liam-ft-epoch2 --speaker liam

Options that change the delivery:
    --mood upset                      delivery from voices/<voice>/ref_upset.wav, timbre still from ref.wav
    --mood upset --mood-timbre        take the timbre from the mood clip as well
    --ref path.wav --ref-text "..."   clone from a specific clip instead of voices/<voice>/ref.wav
    --temperature 0.9 --top-k 50 --top-p 1.0 --repetition-penalty 1.05   sampling (lower temperature = steadier)
    --seed N                          reproducible output
    --language English                Auto, Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian
"""
import argparse
import re
import sys
import time
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import OUTPUT, load_tts, log, reference_for, voice_dir


def slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:n] or "line"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="the line to say")
    ap.add_argument("--voice", required=True, help="prepared voice name (voices/<name>); with --speaker only used for file names")
    ap.add_argument("--lines", help="text file, one line per utterance")
    ap.add_argument("--out", help="output wav (single text) or output folder (with --lines)")
    ap.add_argument("--mood", help="use voices/<voice>/ref_<mood>.wav for the delivery (e.g. upset, happy); timbre stays from ref.wav")
    ap.add_argument("--mood-timbre", action="store_true", help="with --mood: take the timbre from the mood clip too (old behaviour)")
    ap.add_argument("--ref", help="reference clip to clone from (default voices/<voice>/ref.wav)")
    ap.add_argument("--ref-text", help="transcript of --ref (default voices/<voice>/ref.txt)")
    ap.add_argument("--language", default="English")
    ap.add_argument("--model", help="model folder (default models/Qwen3-TTS-12Hz-1.7B-Base); a fine-tuned checkpoint works too")
    ap.add_argument("--speaker", help="use a fine-tuned speaker name instead of reference-clip cloning")
    ap.add_argument("--dtype", default="float16", choices=["float16", "bfloat16", "float32"])
    ap.add_argument("--seed", type=int)
    ap.add_argument("--temperature", type=float)
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--top-p", type=float)
    ap.add_argument("--repetition-penalty", type=float)
    ap.add_argument("--max-new-tokens", type=int)
    args = ap.parse_args()

    if args.lines:
        texts = [l.strip() for l in Path(args.lines).read_text(encoding="utf-8").splitlines() if l.strip()]
    elif args.text:
        texts = [args.text]
    else:
        ap.error("give a text or --lines file")

    # resolve the reference before loading the model so a wrong voice/mood fails in a second, not a minute
    ref = ref_text = timbre_ref = None
    if not args.speaker:
        if args.ref:
            ref, ref_text = args.ref, args.ref_text
            if not ref_text:
                rt = Path(args.ref).with_suffix(".txt")
                if not rt.exists():
                    raise SystemExit("--ref-text is required with --ref (or put a .txt next to the clip)")
                ref_text = rt.read_text(encoding="utf-8").strip()
        else:
            ref, ref_text = reference_for(voice_dir(args.voice), args.mood)
            if args.mood and not args.mood_timbre:
                # delivery comes from the mood clip, the voice itself from the neutral reference
                timbre_ref, _ = reference_for(voice_dir(args.voice))
        log(f"reference: {ref}" + (f"  (timbre from {timbre_ref})" if timbre_ref else ""))

    import torch
    if args.seed is not None:
        torch.manual_seed(args.seed)

    model = load_tts(args.model, args.dtype)
    gen = {k: v for k, v in dict(temperature=args.temperature, top_k=args.top_k, top_p=args.top_p,
                                 repetition_penalty=args.repetition_penalty, max_new_tokens=args.max_new_tokens).items()
           if v is not None}
    langs = [args.language] * len(texts)

    t0 = time.time()
    if args.speaker:
        wavs, sr = model.generate_custom_voice(text=texts, language=langs, speaker=[args.speaker] * len(texts), **gen)
    else:
        prompt = model.create_voice_clone_prompt(ref_audio=ref, ref_text=ref_text)
        if timbre_ref:
            # The prompt carries two things: the clip's codec tokens + transcript (the model continues
            # these, so pacing and emotion come from here) and a speaker embedding (timbre). A mood clip
            # would move both; keep the timbre pinned to the neutral reference so the actor stays the same.
            neutral = model.create_voice_clone_prompt(ref_audio=timbre_ref, x_vector_only_mode=True)
            prompt[0].ref_spk_embedding = neutral[0].ref_spk_embedding
        wavs, sr = model.generate_voice_clone(text=texts, language=langs, voice_clone_prompt=prompt, **gen)
    dt = time.time() - t0

    total = 0.0
    if len(texts) == 1:
        out = Path(args.out) if args.out else OUTPUT / f"{args.voice}_{slug(texts[0])}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, wavs[0], sr)
        total = len(wavs[0]) / sr
        print(out)
    else:
        folder = Path(args.out) if args.out else OUTPUT / args.voice
        folder.mkdir(parents=True, exist_ok=True)
        for i, (t, w) in enumerate(zip(texts, wavs)):
            p = folder / f"{i:03d}_{slug(t)}.wav"
            sf.write(p, w, sr)
            total += len(w) / sr
            print(p)
    log(f"{len(texts)} line(s), {total:.1f} s of audio in {dt:.1f} s ({total / max(dt, 1e-6):.2f}x realtime), "
        f"peak VRAM {torch.cuda.max_memory_allocated() / 2**30:.1f} GB")


if __name__ == "__main__":
    main()

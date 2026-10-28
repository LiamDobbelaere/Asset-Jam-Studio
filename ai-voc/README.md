# ai-voc: game dialogue in a cloned voice (local, RTX 2080 / 8 GB)

Text → speech in the voice of an actor, cloned from a few seconds of their recorded lines.
Only clone voices you have permission to use: your own, or an actor who agreed to it. Runs fully local on the 2080 in fp16, no compiled CUDA extensions, no cloud.

| Stage | Model | License | Notes |
|---|---|---|---|
| Reference transcription | Whisper large-v3-turbo | MIT | only in `prep_voice`, once per actor |
| Text + reference → speech | **Qwen3-TTS-12Hz-1.7B-Base** | Apache-2.0 | zero-shot clone from 3–15 s of audio, 10 languages, 24 kHz output |
| Speech codec | Qwen3-TTS-Tokenizer-12Hz | Apache-2.0 | used by the model, and by `finetune` to tokenise clips |
| Optional per-actor fine-tune | same model + LoRA (peft) | Apache-2.0 | official single-speaker recipe, adapted to 8 GB |

Why this model: it is the strongest cloning model with weights you may ship in a commercial game.
Chatterbox-Turbo (MIT, 350M) is the lighter alternative and has native `[laugh]`-style tags, but
its clone fidelity measured lower in the comparisons found and it has no fine-tuning route.
F5-TTS, Fish/OpenAudio, XTTS-v2, Voxtral and IndexTTS have non-commercial weights; Step-Audio-EditX
and Ming-3.0-tts are Linux/bf16-first and need more VRAM. Qwen3-TTS ships bf16 weights, but the
RTX 20-series has no bf16, so everything here loads fp16; verified on the 2080: no NaNs, Whisper
transcribes the output back exactly, and the speaker embedding of generated lines sits as close to
the actor's reference as the actor's own held-out lines do.

## Setup on a fresh machine

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Creates `.venv` with uv (Python 3.12, torch cu130), installs `requirements.txt` and downloads
~6.5 GB of weights into `models/`. `-SkipModels` skips the download. `ffmpeg` must be on PATH for
mp3 reference clips. Ignore the "SoX could not be found" warning at startup: it comes from the
unused 25 Hz tokenizer in the `qwen-tts` package.

## Layout

```
setup.ps1                 one-time install (venv, packages, models)
requirements.txt          pinned Python packages
prep_voice.bat <folder> --name <actor>   actor's clips -> voices/<actor>/ (reference + transcripts)
speak.bat "line" --voice <actor>         text -> wav in that voice
finetune.bat --voice <actor>             optional LoRA fine-tune -> output/<actor>-ft/
scripts/prep_voice.py     transcribes every clip with Whisper, picks and builds the reference
scripts/speak.py          the TTS CLI (single line or --lines file, zero-shot or fine-tuned speaker)
scripts/finetune.py       LoRA single-speaker fine-tune that fits in 8 GB
scripts/tts_worker.py     persistent worker process the studio talks to (keeps the model loaded between lines)
scripts/download_models.py
scripts/common.py         paths and model loading
voices/<actor>/ref.wav    cloning reference (clean clips concatenated, ~12 s)       committed
voices/<actor>/ref.txt    its transcript                                             committed
voices/<actor>/clips.jsonl  every clip with Whisper transcript + duration           committed, edit by hand
voices/<actor>/clips/     24 kHz mono wavs of every clip (fine-tune data)           ignored, rebuilt by prep_voice
voices/<actor>/codes.jsonl  cached codec tokens for finetune.py                     ignored
output/                   generated wavs and fine-tuned checkpoints                 ignored
models/                   weights (~6.5 GB)                                         ignored
```

The repo ships one example voice, `liam` (the author's own voice, included only as an example; do not use
it to impersonate the author). Your own voices go next to it in `voices/` and stay out of git (see `.gitignore`).
Recording a new voice: 8–15 separate files, one sentence each, 3–10 s, in the speaker's normal register,
quiet room, no music, same mic distance throughout.

## Usage

```bash
prep_voice.bat C:\recordings\sam --name sam
```

Transcribes the clips (prints them; fix mistakes in `voices/sam/clips.jsonl` and `ref.txt`), writes
the 24 kHz clips, and builds `ref.wav` from up to three of the longest clean clips (2.5–12 s each).
To choose the reference yourself: `--ref "weather" "train"` (substrings of file
names). The reference is what the model imitates, so pick the actor's typical register, not a
scream or a whisper, with no music or other voices.

```bash
speak.bat "I missed the last train home." --voice liam
speak.bat "I missed the last train home." --voice liam --seed 3 --out output\liam_train.wav
speak.bat --voice sam --lines sam_lines.txt --out output\sam
```

Prints the written path(s). Output is 24 kHz mono wav; ~3 s of compute per second of audio on the
2080, 4.5 GB VRAM. Useful knobs:

| Flag | Effect |
|---|---|
| `--seed N` | reproducible take; try a few seeds and keep the best |
| `--temperature 0.7` | steadier, flatter delivery (default 0.9); raise for more variation |
| `--mood upset` | clone from `voices/<actor>/ref_upset.wav` instead of `ref.wav` (see Moods) |
| `--ref clip.wav --ref-text "..."` | clone from a specific clip, e.g. an angry line to get an angry read |
| `--language German` | the text's language; the voice carries over (reference stays English) |
| `--model models\Qwen3-TTS-12Hz-0.6B-Base` | smaller/faster model if downloaded (lower quality) |

Delivery and emotion follow the reference clip and the punctuation of the text ("...", "!", "?").
The model has no "say it sadly" instruction input (that exists only in Qwen's preset-speaker
variants, which cannot clone), so emotion is steered by the reference.

### Moods

A mood is a second reference, `voices/<actor>/ref_<mood>.wav` + `.txt`, built from clips of the
actor already in that state:

```bash
prep_voice.bat C:\recordings\sam --name sam --mood upset --ref "annoyed 1" "annoyed 2" "annoyed 3"
speak.bat "Great. Another crash." --voice sam --mood upset
```

`--ref` takes substrings of the clip file names or clip numbers (`023` = `clips/023.wav`); the voice
must already be prepared (the clips and transcripts are reused, nothing is re-transcribed).
`speak.bat` with a mood the actor does not have stops immediately and lists the moods that exist.

A reference carries two things: its codec tokens + transcript, which the model continues (pacing,
emotion), and a speaker embedding (timbre). With `--mood`, only the first comes from the mood clip;
the timbre is taken from the neutral `ref.wav`, so a mood reference cut from a different session
or recording chain does not change who is speaking. Measured on one actor: the upset take with the
pinned timbre sits as close to the neutral reference as a neutral take does (0.966 vs 0.969),
where the all-from-mood-clip version drifted to 0.959. `--mood-timbre` restores the old behaviour. Use full sentences spoken in the mood, 4–12 s in
total: in-context cloning continues the reference, so a one-second "URGH" alone gives the model
nothing about how the actor talks and tends to produce shouting or garbage. A yell can be one of
two or three clips in a mood reference, never the only one.

## Fine-tuning (optional)

Zero-shot cloning is already speaker-accurate; fine-tune only when an actor's timbre or pacing is
consistently off, and A/B against zero-shot before adopting it.

```bash
finetune.bat --voice sam --epochs 8
speak.bat "Good morning, everyone." --voice sam --model output\sam-ft --speaker sam
```

Trains LoRA adapters (rank 16, ~19 M params) on the talker and code predictor in fp16 autocast with
gradient checkpointing (5.2 GB VRAM, ~20 s per epoch on 150 s of speech), merges them and writes a
standard Qwen3-TTS `custom_voice` checkpoint (~4.4 GB incl. the codec) with the actor as speaker `<voice>`.

Measured on one actor (62 clips, 150 s), same three unseen lines, speaker-embedding cosine to their reference:

| | similarity | Whisper round-trip |
|---|---|---|
| zero-shot (`speak.bat` default) | 0.97–0.98 | all three lines exact |
| LoRA 8 epochs | 0.95–0.97 | all three exact |
| LoRA 24 epochs | 0.95–0.97 | one line garbled (train loss ~0: memorised the clips) |

So with a few minutes of audio per actor, fine-tuning does not beat zero-shot and over-training
breaks intelligibility. Keep 8 epochs or fewer, and expect fine-tuning to pay off only with
substantially more material (tens of minutes). Short sets (under ~30 s) should stay zero-shot.

## Notes

- Qwen3-TTS is ~0.3x realtime here. `faster-qwen3-tts` (CUDA graphs) exists if batch generation
  of a whole script becomes the bottleneck; not installed.
- Source clips can be any sample rate and channel count (44.1 kHz stereo mp3 is fine); `prep_voice` converts them to 24 kHz
  mono, trims silence and peak-normalises. Keep originals; everything in `voices/` is derived.
- Non-speech clips (hums, screams) transcribe oddly; `prep_voice` collapses run-away repeats
  ("mmmmmm…" → "mmm"). Delete lines from `clips.jsonl` you do not want in the fine-tune set.

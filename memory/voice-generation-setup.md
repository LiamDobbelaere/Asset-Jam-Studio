---
name: voice-generation-setup
description: "ai-voc (D:\\Git\\3d-model-gen\\ai-voc) uses Qwen3-TTS-12Hz-1.7B-Base in fp16 for actor voice cloning; why it was chosen over Chatterbox/F5/Fish, and the fp16/8 GB gotchas"
metadata:
  type: project
---

ai-voc (set up 2026-10-26) = Qwen3-TTS-12Hz-1.7B-Base (Apache-2.0) zero-shot voice cloning for
the user's game dialogue; actor references live in a private folder outside the repo (44.1 kHz stereo mp3,
2–8 s lines; 6–74 clips per actor). Whisper large-v3-turbo
transcribes refs; LoRA (peft) fine-tune adapted from the official sft_12hz.py recipe.

**Why:** needs commercial-safe weights (F5-TTS, Fish/OpenAudio, XTTS-v2, Voxtral, IndexTTS are
non-commercial); Qwen3-TTS measured best clone fidelity and has an official fine-tune path.
Chatterbox-Turbo (MIT) is the fallback. Step-Audio-EditX / Ming-3.0-tts are Linux + bf16 + bigger.

**How to apply:** always load with dtype=float16 (Turing has no bf16; fp16 verified clean on the
2080, 4.5 GB VRAM, ~0.3x realtime). Qwen3-TTS pip 0.1.1 warns about SoX at import: harmless
(only the unused 25 Hz tokenizer). Full-parameter fine-tune needs ~30 GB; the LoRA version uses
~7.8 GB at batch 2, so nothing else may be on the GPU while it runs. Scripts run with `python -I`,
so each inserts its own folder into sys.path. See [[hardware-and-license-constraints]].

**Fine-tune verdict (2026-10-26):** LoRA on the main actor's 150 s gave no speaker-similarity gain over
zero-shot (0.95–0.97 vs 0.97–0.98); 24 epochs memorised the clips and garbled a line. Zero-shot
(speak.bat without --model) is the default; fine-tune only with much more data, ≤8 epochs.
Emotion: no instruct input on the Base model; use mood-specific --ref clips + punctuation.

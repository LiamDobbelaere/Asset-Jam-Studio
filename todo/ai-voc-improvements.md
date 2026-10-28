# ai-voc: improvement ideas

State on 2026-10-26: zero-shot cloning with Qwen3-TTS-12Hz-1.7B-Base works and is speaker-accurate
on neutral lines, but (1) there is not enough "acting" control, and (2) the `upset` mood reference
produces a noticeably different-sounding voice for the same actor. Ideas below, roughly in order of
expected payoff per hour.

## 1. Why the mood reference changes the voice, and cheap fixes

In-context cloning feeds the model two things from the reference: a speaker embedding (x-vector,
timbre) and the reference's codec tokens + transcript (which it continues, so prosody and emotion
come from here). Both are taken from the *same* clip. A mood reference therefore swaps the timbre
too, and with short, compressed, possibly mixed-actor clips the x-vector moves a lot.

- [x] **Split the two sources.** Done in `speak.py`: with `--mood` the speaker embedding comes
      from the neutral `ref.wav`, only codec tokens + transcript from the mood clip. Similarity to
      the neutral reference went from 0.959 (old) to 0.966 (neutral take: 0.969). `--mood-timbre`
      keeps the old behaviour. Still to judge by ear whether the emotion survives the swap.
- [ ] **Audit the clips for the actor switch.** Add `scripts/check_voice.py`: cosine similarity of
      every `clips/NNN.wav` against `ref.wav` using `model.speaker_encoder` (the code is already in
      the session's QC scripts), print sorted, flag outliers. Then rebuild `ref.wav` and
      `ref_upset.wav` from one actor only, and drop the other actor's lines from `clips.jsonl`.
      Same tool answers "is this mood ref the same voice as the neutral ref" before generating.
- [ ] **Longer, cleaner references.** All refs come from 2–3 s mp3 lines glued together. Record
      (or cut from the longest takes) one 20–30 s continuous reference per actor per mood at
      48 kHz wav. Continuous speech gives the model real pacing, not a sequence of fragments.
- [ ] **Best-of-N takes.** `--takes 5`: generate N seeds, score each by speaker similarity to the
      neutral ref and by Whisper word error rate, keep the best, print the scores. Removes most
      "that take drifted" cases without any model change.

## 2. More acting

The Base model has no instruction input; the levers are the reference, the text and sampling.

- [ ] **Scene-continuity reference.** For a conversation, use the previously generated line (audio
      + its text) as the reference for the next one, so a scene keeps one delivery instead of
      resetting to `ref.wav` each line. Cheap, already supported by `--ref` + `--ref-text`; wire it
      into `--lines` as `--chain`.
- [ ] **Punctuation and stress cheat sheet.** Systematically test what the model reacts to:
      "...", "—", "!", "?!", CAPS, repeated letters ("nooo"), stage-direction words ("ugh,").
      Write the findings into the README so line authors can act through text.
- [ ] **Mood tags via fine-tuning.** Prefix training transcripts with a tag ("[upset] I really can't
      do this anymore.") and LoRA-train on all actors with labelled moods. The text channel will carry
      the tag at inference. Needs tens of minutes of labelled lines; with the 150 s we have it will
      memorise (see README fine-tune table).
- [ ] **Second engine for tagged paralinguistics.** Chatterbox-Turbo (MIT) has inline `[laugh]`,
      `[sigh]`, `[chuckle]` tags and an `exaggeration` knob. It cannot fine-tune and its clone
      fidelity measured lower, but for laughs/sighs/breaths it may beat Qwen. Could run as
      `speak.bat --engine chatterbox`, same `voices/` layout.
- [ ] **Emotion editing as a post-pass.** Step-Audio-EditX (Apache-2.0 code, weights license
      unconfirmed) edits emotion/style of *existing* audio: generate with Qwen for timbre, then
      "make this angrier". Linux + vLLM + 12 GB in the docs (AWQ 4-bit ~8–10 GB) so it would
      need WSL2 and may not fit the 2080. Verify the weights license before investing.
- [ ] **IndexTTS-2** explicitly disentangles timbre from emotion (timbre from one clip, emotion
      from another, or an 8-dim emotion vector). Exactly problem (1). Its weights are not
      commercial-clean as far as found; re-check, it would be the best technical fit.

## 3. Quality of the source material

- [ ] Re-export the references from the original project files instead of mp3 if they exist
      (the original set was 44.1 kHz stereo mp3 of 2–8 s lines).
- [ ] Loudness-normalise to a target LUFS instead of peak-normalise; the codec is sensitive to level.
- [ ] Trim less aggressively: `librosa.effects.trim(top_db=35)` may cut soft starts ("Sam, he...").

## 4. Speed

- [ ] `faster-qwen3-tts` (CUDA graphs) claims real-time on consumer GPUs; we are at ~0.3x.
- [ ] Batch all lines of a scene in one `generate_voice_clone` call (the API takes lists); the CLI
      already builds lists, check whether batch >1 is actually faster on 8 GB.
- [ ] Try the 0.6B Base model for drafts; keep 1.7B for finals.

## 5. Pipeline

- [ ] `lines.csv` with columns `actor, mood, text, file` → one command renders a whole scene into
      Unity-ready names.
- [ ] Add a small listening page (HTML) that lists takes per line with play buttons, so picking the
      best take is a 2-minute job.

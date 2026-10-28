"""Fine-tune Qwen3-TTS on one prepared voice with LoRA so it fits an 8 GB GPU.

    python scripts/finetune.py --voice liam [--epochs 8] [--lr 1e-4] [--rank 16] [--batch-size 2] [--grad-accum 4]

Reads voices/<voice>/clips.jsonl (made by prep_voice.py; fix any wrong transcripts there first) and
voices/<voice>/ref.wav, encodes the clips to codec tokens once (cached in voices/<voice>/codes.jsonl),
trains LoRA adapters on the talker + code predictor in fp16 autocast with gradient checkpointing,
then merges the adapters and writes a normal Qwen3-TTS "custom_voice" checkpoint to
output/<voice>-ft/ that speak.py can load:

    python scripts/speak.py "Hello." --voice liam --model output/liam-ft --speaker liam

This is the official single-speaker recipe (finetuning/sft_12hz.py in QwenLM/Qwen3-TTS, Apache-2.0)
with full-parameter AdamW swapped for LoRA; the full recipe needs ~30 GB for the 1.7B model.
Zero-shot cloning (speak.py without --model) is usually already good; fine-tune when an actor's
timbre or delivery is consistently off, and compare against zero-shot output before adopting it.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import librosa
import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import OUTPUT, SAMPLE_RATE, TTS_MODEL, TTS_TOKENIZER, log, read_jsonl, voice_dir, write_jsonl

SPK_ID = 3000  # row of the codec embedding table that the official recipe uses for the new speaker


def encode_codes(vd, rows):
    """Encode each clip to 12 Hz codec tokens with the Qwen3-TTS tokenizer (cached)."""
    cache = vd / "codes.jsonl"
    if cache.exists():
        cached = read_jsonl(cache)
        if [c["audio"] for c in cached] == [r["audio"] for r in rows] and all(c["text"] == r["text"] for c, r in zip(cached, rows)):
            log(f"using cached codes {cache}")
            return cached
    from qwen_tts import Qwen3TTSTokenizer

    log("encoding clips to codec tokens ...")
    tok = Qwen3TTSTokenizer.from_pretrained(str(TTS_TOKENIZER), device_map="cuda:0")
    out = []
    for i in range(0, len(rows), 16):
        batch = rows[i:i + 16]
        enc = tok.encode([str(vd / r["audio"]) for r in batch])
        for code, r in zip(enc.audio_codes, batch):
            out.append({**r, "audio_codes": code.cpu().tolist()})
    del tok
    torch.cuda.empty_cache()
    write_jsonl(cache, out)
    return out


def ref_mel(path):
    from qwen_tts.core.models.modeling_qwen3_tts import mel_spectrogram

    y, _ = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    return mel_spectrogram(torch.from_numpy(y.astype(np.float32)).unsqueeze(0), n_fft=1024, num_mels=128,
                           sampling_rate=SAMPLE_RATE, hop_size=256, win_size=1024, fmin=0, fmax=12000).transpose(1, 2)


class ClipDataset(Dataset):
    """Same layout as the official finetuning/dataset.py: text channel + codec channel + masks."""

    def __init__(self, rows, processor, config):
        self.rows, self.processor, self.config = rows, processor, config

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        text = f"<|im_start|>assistant\n{r['text']}<|im_end|>\n<|im_start|>assistant\n"
        ids = self.processor(text=text, return_tensors="pt", padding=True)["input_ids"]
        ids = ids.unsqueeze(0) if ids.dim() == 1 else ids
        return {"text_ids": ids[:, :-5], "audio_codes": torch.tensor(r["audio_codes"], dtype=torch.long)}

    def collate(self, batch):
        cfg, tc = self.config, self.config.talker_config
        t = max(b["text_ids"].shape[1] + b["audio_codes"].shape[0] for b in batch) + 8
        n = len(batch)
        input_ids = torch.zeros((n, t, 2), dtype=torch.long)
        codec_ids = torch.zeros((n, t, 16), dtype=torch.long)
        text_mask = torch.zeros((n, t), dtype=torch.bool)
        codec_emb_mask = torch.zeros((n, t), dtype=torch.bool)
        codec_mask = torch.zeros((n, t), dtype=torch.bool)
        attn = torch.zeros((n, t), dtype=torch.long)
        labels = torch.full((n, t), -100, dtype=torch.long)
        for i, b in enumerate(batch):
            ti, codes = b["text_ids"], b["audio_codes"]
            tl, cl = ti.shape[1], codes.shape[0]
            s = 8 + tl - 1  # first codec position
            input_ids[i, :3, 0] = ti[0, :3]
            input_ids[i, 3:7, 0] = cfg.tts_pad_token_id
            input_ids[i, 7, 0] = cfg.tts_bos_token_id
            input_ids[i, 8:8 + tl - 3, 0] = ti[0, 3:]
            input_ids[i, 8 + tl - 3, 0] = cfg.tts_eos_token_id
            input_ids[i, 8 + tl - 2:8 + tl + cl, 0] = cfg.tts_pad_token_id
            text_mask[i, :8 + tl + cl] = True
            input_ids[i, 3:8, 1] = torch.tensor([tc.codec_nothink_id, tc.codec_think_bos_id, tc.codec_think_eos_id, 0, tc.codec_pad_id])
            input_ids[i, 8:8 + tl - 2, 1] = tc.codec_pad_id
            input_ids[i, 8 + tl - 2, 1] = tc.codec_bos_id
            input_ids[i, s:s + cl, 1] = codes[:, 0]
            input_ids[i, s + cl, 1] = tc.codec_eos_token_id
            labels[i, s:s + cl] = codes[:, 0]
            labels[i, s + cl] = tc.codec_eos_token_id
            codec_ids[i, s:s + cl, :] = codes
            codec_emb_mask[i, 3:8 + tl + cl] = True
            codec_emb_mask[i, 6] = False  # speaker embedding slot
            codec_mask[i, s:s + cl] = True
            attn[i, :8 + tl + cl] = True
        return dict(input_ids=input_ids, codec_ids=codec_ids, text_embedding_mask=text_mask.unsqueeze(-1),
                    codec_embedding_mask=codec_emb_mask.unsqueeze(-1), codec_mask=codec_mask, attention_mask=attn, codec_0_labels=labels)


def step_loss(model, talker, batch, speaker_embedding):
    """talker: the unwrapped Qwen3TTSTalkerForConditionalGeneration (LoRA layers are injected in place)."""
    dev = model.device
    b = {k: v.to(dev) for k, v in batch.items()}
    text_emb = talker.model.text_embedding(b["input_ids"][:, :, 0]) * b["text_embedding_mask"]
    codec_emb = talker.model.codec_embedding(b["input_ids"][:, :, 1]) * b["codec_embedding_mask"]
    codec_emb[:, 6, :] = speaker_embedding.to(codec_emb.dtype)
    x = text_emb + codec_emb
    for i in range(1, 16):
        e = talker.code_predictor.get_input_embeddings()[i - 1](b["codec_ids"][:, :, i])
        x = x + e * b["codec_mask"].unsqueeze(-1)
    out = talker(inputs_embeds=x[:, :-1, :], attention_mask=b["attention_mask"][:, :-1],
                       labels=b["codec_0_labels"][:, 1:], output_hidden_states=True)
    hidden = out.hidden_states[0][-1][b["codec_mask"][:, :-1]]
    _, sub_loss = talker.forward_sub_talker_finetune(b["codec_ids"][b["codec_mask"]], hidden)
    return out.loss + 0.3 * sub_loss, out.loss, sub_loss


def save_checkpoint(model, src_dir, out_dir, speaker_name, speaker_embedding):
    """Write a custom_voice checkpoint the way the official recipe does (speaker at codec row SPK_ID)."""
    from safetensors.torch import save_file

    out_dir.mkdir(parents=True, exist_ok=True)
    for p in Path(src_dir).iterdir():
        if p.is_dir():  # speech_tokenizer/ (the codec, ~650 MB) must sit next to the weights
            shutil.copytree(p, out_dir / p.name, dirs_exist_ok=True)
        elif p.suffix != ".safetensors" and p.name != "config.json":
            shutil.copy2(p, out_dir / p.name)
    cfg = json.loads((Path(src_dir) / "config.json").read_text(encoding="utf-8"))
    cfg["tts_model_type"] = "custom_voice"
    cfg.setdefault("talker_config", {})
    cfg["talker_config"]["spk_id"] = {speaker_name: SPK_ID}
    cfg["talker_config"]["spk_is_dialect"] = {speaker_name: False}
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    sd = {k: v.detach().to("cpu").contiguous() for k, v in model.state_dict().items() if not k.startswith("speaker_encoder")}
    w = sd["talker.model.codec_embedding.weight"]
    w[SPK_ID] = speaker_embedding.detach().to("cpu").to(w.dtype)
    save_file(sd, str(out_dir / "model.safetensors"), metadata={"format": "pt"})
    log(f"saved {out_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--model", help="base model folder (default models/Qwen3-TTS-12Hz-1.7B-Base)")
    ap.add_argument("--out", help="checkpoint folder (default output/<voice>-ft)")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16, help="LoRA rank")
    ap.add_argument("--alpha", type=int, default=32, help="LoRA alpha")
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--min-seconds", type=float, default=1.0, help="skip clips shorter than this")
    ap.add_argument("--save-every", type=int, default=0, help="also save a checkpoint every N epochs (each is ~4.4 GB)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    vd = voice_dir(args.voice)
    src = Path(args.model) if args.model else TTS_MODEL
    out_dir = Path(args.out) if args.out else OUTPUT / f"{args.voice}-ft"
    rows = [r for r in read_jsonl(vd / "clips.jsonl") if r["duration"] >= args.min_seconds and r["text"].strip()]
    log(f"{len(rows)} clips, {sum(r['duration'] for r in rows):.0f} s of speech")
    rows = encode_codes(vd, rows)

    from peft import LoraConfig, get_peft_model
    from qwen_tts import Qwen3TTSModel

    log(f"loading {src} (fp16) ...")
    tts = Qwen3TTSModel.from_pretrained(str(src), device_map="cuda:0", dtype=torch.float16)
    model = tts.model
    model.config.use_cache = False
    for p in model.parameters():
        p.requires_grad_(False)
    with torch.no_grad():
        speaker_embedding = model.speaker_encoder(ref_mel(vd / "ref.wav").to(model.device).half())[0]

    lcfg = LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05, bias="none",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    talker = model.talker  # unwrapped talker; LoRA layers are injected into it in place
    model.talker = get_peft_model(talker, lcfg)
    model.talker.print_trainable_parameters()
    talker.model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

    ds = ClipDataset(rows, tts.processor, model.config)
    dl = DataLoader(ds, batch_size=args.batch_size, shuffle=True, collate_fn=ds.collate)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
    total_updates = max(1, args.epochs * len(dl) // args.grad_accum)
    lr_lambda = lambda u: min(1.0, (u + 1) / 10) * max(0.05, 1 - u / total_updates)   # noqa: E731
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lr_lambda)
    scaler = torch.amp.GradScaler("cuda")

    model.train()
    t0, upd = time.time(), 0
    for epoch in range(args.epochs):
        losses = []
        for step, batch in enumerate(dl):
            with torch.autocast("cuda", dtype=torch.float16):
                loss, l0, ls = step_loss(model, talker, batch, speaker_embedding)
            scaler.scale(loss / args.grad_accum).backward()
            losses.append(float(loss))
            if (step + 1) % args.grad_accum == 0 or step + 1 == len(dl):
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad(set_to_none=True)
                sched.step()
                upd += 1
        log(f"epoch {epoch + 1}/{args.epochs}  loss {np.mean(losses):.3f}  (codec0 {float(l0):.3f}, sub {float(ls):.3f})  "
            f"{time.time() - t0:.0f} s  peak VRAM {torch.cuda.max_memory_allocated() / 2**30:.1f} GB")
        if args.save_every and (epoch + 1) % args.save_every == 0 and epoch + 1 < args.epochs:
            model.talker = model.talker.merge_and_unload()  # merges in place; re-wrap afterwards to keep training
            save_checkpoint(model, src, out_dir.with_name(f"{out_dir.name}-epoch{epoch + 1}"), args.voice, speaker_embedding)
            model.talker = get_peft_model(talker, lcfg)
            talker.model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
            params = [p for p in model.parameters() if p.requires_grad]
            opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
            done = upd
            sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda u, d=done: lr_lambda(u + d))   # continue the warm-up/decay curve

    model.eval()
    model.talker = model.talker.merge_and_unload()
    save_checkpoint(model, src, out_dir, args.voice, speaker_embedding)
    log(f"done. Try: speak.bat \"Good morning, everyone.\" --voice {args.voice} --model {out_dir} --speaker {args.voice}")


if __name__ == "__main__":
    main()

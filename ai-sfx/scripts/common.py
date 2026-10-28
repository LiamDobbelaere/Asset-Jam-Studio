"""Shared helpers for the ai-sfx scripts: paths, MOSS-SoundEffect v2.0 loading on an 8 GB Turing card, audio output."""
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
OUTPUT = ROOT / "output"

MODEL = "MOSS-SoundEffect-v2.0"         # 1.3B DiT + Qwen3-1.7B text encoder + DAC VAE, Apache-2.0
SAMPLE_RATE = 48000                     # the DAC VAE decodes to 48 kHz mono
MAX_SECONDS = 30.0                      # the DiT always denoises a 30 s latent; shorter sounds are cropped from it
FORMATS = ("wav", "flac", "ogg")        # written with libsndfile; wav is 16-bit PCM
DEFAULT_STEPS = 50
DEFAULT_CFG = 4.0
DEFAULT_SHIFT = 5.0
# Turing has no native bf16: the DiT runs in fp16 (compute-bound, 2 passes per step with CFG). The text encoder
# runs once per sound, so it stays in emulated bf16 like the ai-mus planner (Qwen3 activations can overflow fp16).
DIT_DTYPE = os.environ.get("AISFX_DIT_DTYPE", "float16")
TE_DTYPE = os.environ.get("AISFX_TE_DTYPE", "bfloat16")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def setup_env():
    # the DiT forward is wrapped in torch.compile + Triton CUDA graphs upstream; no Triton on Windows/sm_75
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")        # never go online at load time; setup.ps1 downloads
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:n] or "sfx"


def unique_path(folder, base, ext):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{base}.{ext}"
    i = 2
    while p.exists():
        p = folder / f"{base}_{i}.{ext}"
        i += 1
    return p


class Engine:
    """MOSS-SoundEffect v2.0, loaded once and reused.

    Upstream loads everything in one dtype on one device (bf16, ~6.5 GB resident with the DiT kept in fp32).
    Here the DiT is cast to fp16 and stays on the GPU with the small DAC VAE (~2.9 GB); the 3.4 GB Qwen3
    text encoder only visits the GPU to encode the prompt and goes back to the CPU before denoising
    (`offload=False` keeps it resident: ~6.5 GB, a second faster).
    """

    def __init__(self):
        self.pipe = None
        self.offload = True

    def load(self, offload=True, progress=None):
        if self.pipe is not None:
            return
        setup_env()
        import torch
        from moss_soundeffect_v2.diffsynth.pipelines.wan_audio import WanAudioPipeline

        model_dir = MODELS / MODEL
        if not (model_dir / "model_index.json").is_file():
            raise SystemExit(f"model not found: {model_dir}. Run setup.ps1.")
        if progress:
            progress("loading", MODEL)
        t0 = time.time()
        pipe = WanAudioPipeline.from_pretrained(str(model_dir), device="cpu", torch_dtype=getattr(torch, TE_DTYPE))
        dit_dtype = getattr(torch, DIT_DTYPE)
        # Module.to(dtype) also casts complex buffers and drops their imaginary part: keep the RoPE tables
        rope = {n: b for n, b in pipe.dit.named_buffers() if b.is_complex()}
        pipe.dit.to(dtype=dit_dtype)
        for n, b in rope.items():
            pipe.dit.register_buffer(n, b, persistent=False)
        pipe.torch_dtype = dit_dtype     # dtype of the noise / latents
        pipe.device = "cuda"
        pipe.dit.to("cuda")
        self.offload = offload
        if not offload:
            pipe.text_encoder.to("cuda")
            pipe.vae.to("cuda")

        def load_models_to_device(names=()):
            # Upstream moves whole sub-models only when vram management is on. Here the DiT stays resident and the
            # text encoder (3.4 GB) and VAE (1.5 GB, fp32) take turns next to it: all three together peak at 8.1 GB
            # and the caching allocator keeps it reserved, so every DiT step then runs from shared system memory
            # (14 s per step instead of a fraction of a second). empty_cache() hands the freed blocks back.
            if not self.offload:
                return
            for name in ("text_encoder", "vae"):
                getattr(pipe, name).to("cuda" if name in names else "cpu")
            torch.cuda.empty_cache()
        pipe.load_models_to_device = load_models_to_device
        self.pipe = pipe
        log(f"{MODEL} loaded in {time.time() - t0:.0f} s (DiT {DIT_DTYPE}, text encoder {TE_DTYPE}"
            f"{', offloaded' if offload else ''})")

    def unload(self):
        if self.pipe is None:
            return
        import gc
        import torch
        self.pipe = None
        gc.collect()
        torch.cuda.empty_cache()

    def generate(self, prompt, duration=4.0, seed=None, steps=DEFAULT_STEPS, cfg=DEFAULT_CFG, shift=DEFAULT_SHIFT,
                 negative="", exact=False, progress=None):
        """Returns {"audio": float32 [channels, samples], "sr", "seed"}; progress(kind, what, ratio).

        The model keeps to the length in the prompt only roughly: a "1.5 s" gunshot can start at 1.2 s with its echo
        running to 1.7 s, and the canvas after that is silent. So the crop leaves a margin (half the length, at least
        1 s) and trim_silence() decides where the sound ends; `exact` crops at `duration` (loops)."""
        import random

        import torch
        duration = round(min(max(float(duration), 0.5), MAX_SECONDS), 1)
        seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)

        def steps_iter(timesteps):
            n = len(timesteps)
            for i, t in enumerate(timesteps):
                if progress:
                    progress("progress", f"step {i + 1}/{n}", i / n)
                yield t
            if progress:
                progress("progress", "decoding", 1.0)

        with torch.no_grad(), torch.autocast("cuda", dtype=self.pipe.torch_dtype):
            audio = self.pipe(prompt=f"{prompt.strip()} duration: {duration:.1f}s", negative_prompt=negative or "",
                              seed=seed, cfg_scale=float(cfg), sigma_shift=float(shift), num_inference_steps=int(steps),
                              num_samples=int(SAMPLE_RATE * MAX_SECONDS), num_channels=1, progress_bar_cmd=steps_iter)
        crop = duration if exact else min(MAX_SECONDS, duration + max(1.0, duration / 2))
        audio = audio[0, :, : int(SAMPLE_RATE * crop)].float().cpu().numpy()
        if not (audio == audio).all():
            raise RuntimeError(f"the model produced NaNs in {DIT_DTYPE}; set AISFX_DIT_DTYPE=float32 and retry")
        return {"audio": audio, "sr": SAMPLE_RATE, "seed": seed}


def trim_silence(audio, sr, head_db=-40.0, tail_db=-50.0, keep=0.01):
    """Drop leading/trailing near-silence, relative to the peak: the start is cut tighter (a one-shot must sound on
    the frame it is triggered; the model often leaves a low rumble before the hit), the tail looser (reverb)."""
    import numpy as np
    env = np.abs(audio).max(axis=0)
    peak = float(env.max()) if env.size else 0.0
    if peak < 1e-6:
        return audio
    head = np.nonzero(env > peak * 10 ** (head_db / 20))[0]
    tail = np.nonzero(env > peak * 10 ** (tail_db / 20))[0]
    a = max(0, head[0] - int(keep * sr))
    b = min(audio.shape[1], tail[-1] + 1 + int(keep * sr))
    return audio[:, a:b]


def highpass(audio, sr, hz=30.0):
    """Remove the sub-bass drift the DAC decoder leaves in (visible as a slow wander of the waveform; it eats
    headroom and does nothing on game speakers). Zero-phase, so transients keep their shape."""
    if not hz:
        return audio
    from scipy.signal import butter, sosfiltfilt
    sos = butter(2, hz, btype="highpass", fs=sr, output="sos")
    return sosfiltfilt(sos, audio, axis=1).astype("float32")


def fade_out(audio, sr, seconds=0.05):
    """Short fade at the end so a cropped sound never ends on a click."""
    import numpy as np
    n = min(int(seconds * sr), audio.shape[1])
    if n > 1:
        audio = audio.copy()
        audio[:, -n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return audio


def normalize(audio, peak_db=-1.0):
    """Peak-normalize to `peak_db` dBFS (the model's levels vary a lot between prompts)."""
    import numpy as np
    peak = float(np.abs(audio).max())
    return audio if peak < 1e-6 else audio * (10 ** (peak_db / 20) / peak)


def make_loop(audio, sr, crossfade=1.0):
    """Make an ambience loop seamlessly: the last `crossfade` seconds are blended into the first ones
    (equal-power), and that tail is cut off. Returns the shortened [channels, samples] array."""
    import numpy as np
    n = audio.shape[1]
    f = int(min(crossfade, max(0.1, n / sr / 4)) * sr)
    if f <= 0 or n <= 2 * f:
        return audio
    head, tail = audio[:, :f], audio[:, n - f:]
    t = np.linspace(0.0, 1.0, f, dtype=np.float32)
    win_in, win_out = np.sqrt(t), np.sqrt(1.0 - t)
    out = audio[:, : n - f].copy()
    out[:, :f] = head * win_in + tail * win_out
    return out


def finish(audio, sr, loop=False, crossfade=1.0, trim=True, peak_db=-1.0, hp_hz=30.0):
    """The post-processing every take gets: high-pass, trim, loop or fade-out, normalize."""
    audio = highpass(audio, sr, hp_hz)
    if trim and not loop:
        audio = trim_silence(audio, sr)
    audio = make_loop(audio, sr, crossfade) if loop else fade_out(audio, sr)
    return normalize(audio, peak_db) if peak_db is not None else audio


def write_audio(path, audio, sr, fmt="wav"):
    import numpy as np
    import soundfile as sf
    fmt = (fmt or "wav").lower()
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    data = np.clip(audio.T, -1.0, 1.0)
    path = Path(path).with_suffix("." + fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt in ("wav", "flac"):
        sf.write(str(path), data, sr, subtype="PCM_16")
        return path
    # OGG goes through ffmpeg: libsndfile 1.2.2's Vorbis encoder kills the whole process on Windows (see ai-mus)
    import shutil
    import subprocess
    import tempfile
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to write ogg (soundfile's Vorbis encoder crashes); use --format wav or install ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "take.wav")
        sf.write(wav, data, sr, subtype="PCM_16")
        r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", wav, "-c:a", "libvorbis", "-q:a", "6", str(path)],
                           capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not write " + str(path) + ": " + r.stderr[-500:])
    return path

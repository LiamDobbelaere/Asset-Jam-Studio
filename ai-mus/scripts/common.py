"""Shared helpers for the ai-mus scripts: paths, ACE-Step 1.5 loading on an 8 GB Turing card, audio output."""
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACESTEP = ROOT / "ACE-Step-1.5"
MODELS = ROOT / "models"
OUTPUT = ROOT / "output"

DIT_MODEL = "acestep-v15-turbo"        # 2B distilled DiT: 8 steps, no CFG, ~4.7 GB in fp16
LM_MODEL = "acestep-5Hz-lm-0.6B"       # the planner LM that fits next to the DiT in 8 GB (1.7B needs 12 GB+)
SAMPLE_RATE = 48000                     # the VAE decodes to 48 kHz stereo
FORMATS = ("wav", "flac", "ogg")        # written with libsndfile; wav is 16-bit PCM
DEFAULT_STEPS = 8
DEFAULT_SHIFT = 3.0                     # upstream's recommended timestep shift for the turbo model
LM_DTYPE = os.environ.get("AIMUS_LM_DTYPE", "bfloat16")   # see Engine.load


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def setup_env():
    """Point ACE-Step at ai-mus/models and keep its caches inside ai-mus/."""
    os.environ.setdefault("ACESTEP_CHECKPOINTS_DIR", str(MODELS))
    os.environ.setdefault("ACESTEP_PROJECT_ROOT", str(ROOT))
    os.environ.setdefault("ACESTEP_TMPDIR", str(ROOT / ".cache" / "tmp"))
    os.environ.setdefault("ACESTEP_DISABLE_TQDM", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")        # never go online at load time; setup.ps1 downloads
    if str(ACESTEP) not in sys.path:
        sys.path.insert(0, str(ACESTEP))


def slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:n] or "track"


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
    """ACE-Step 1.5 DiT + optional planner LM, loaded once and reused.

    On the RTX 2080 ACE-Step itself picks float16 + eager attention (no bf16 on Turing). Every model
    is offloaded to the CPU between its phases, the 4.7 GB DiT included: with the DiT resident, the VAE
    decode finds under 0.5 GB free and ACE-Step silently runs it on the CPU (20+ minutes for a 64 s
    track instead of seconds). Moving the DiT in and out costs a few seconds per track.
    """

    def __init__(self):
        self.dit = None
        self.llm = None
        self.lm_name = None
        self.dit_name = None

    def load(self, dit_name=DIT_MODEL, lm_name=LM_MODEL, offload=True, quantization=None, progress=None):
        setup_env()
        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler

        if self.dit is None or self.dit_name != dit_name:
            if not (MODELS / dit_name).is_dir():
                raise SystemExit(f"DiT model not found: {MODELS / dit_name}. Run setup.ps1.")
            if progress:
                progress("loading", f"{dit_name} (DiT)")
            t0 = time.time()
            self.dit = AceStepHandler()
            msg, ok = self.dit.initialize_service(project_root=str(ROOT), config_path=dit_name, device="cuda",
                                                 use_flash_attention=False, compile_model=False,
                                                 offload_to_cpu=offload, offload_dit_to_cpu=offload,
                                                 quantization=quantization)
            if not ok:
                self.dit = None
                raise RuntimeError(f"ACE-Step DiT failed to load: {msg}")
            self.dit_name = dit_name
            self._fp32_lyric_encoder()
            log(f"DiT {dit_name} loaded in {time.time() - t0:.0f}s (dtype {self.dit.dtype})")
        if lm_name and (self.llm is None or self.lm_name != lm_name):
            if not (MODELS / lm_name).is_dir():
                raise SystemExit(f"LM model not found: {MODELS / lm_name}. Run setup.ps1.")
            if progress:
                progress("loading", f"{lm_name} (planner LM)")
            t0 = time.time()
            self.unload_lm()
            self.llm = LLMHandler()
            import torch
            # Planner dtype on Turing: fp16 overflows in Qwen3's activations (NaN probabilities in multinomial),
            # fp32 is 2x slower and its 2.4 GB pushes the VAE decode onto the CPU; bf16 is emulated but memory-bound
            # decoding still runs ~9 tok/s and it is numerically safe. It is moved to the CPU between phases so the
            # DiT and VAE get the GPU back.
            dt = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}[LM_DTYPE]
            msg, ok = self.llm.initialize(checkpoint_dir=str(MODELS), lm_model_path=lm_name, backend="pt", device="cuda",
                                          offload_to_cpu=True, dtype=dt)
            if not ok:
                self.llm = None
                raise RuntimeError(f"ACE-Step LM failed to load: {msg}")
            self.lm_name = lm_name
            log(f"LM {lm_name} loaded in {time.time() - t0:.0f}s")
        if not lm_name and self.llm is None:
            self.llm = LLMHandler()   # an uninitialised handler; generate_music skips it when thinking=False

    def _fp32_lyric_encoder(self):
        """With sung lyrics the DiT's lyric encoder overflows fp16 (its MLP in layer 7 sees activations ~9e3
        and down_proj produces inf, then every latent is NaN; instrumentals only pass "[Instrumental]" so they
        are fine). The encoder is ~0.1 GB, so it runs in fp32 and its output is cast back for the DiT."""
        import torch
        model = getattr(self.dit, "model", None)
        enc = getattr(getattr(model, "encoder", None), "lyric_encoder", None)
        if enc is None or self.dit.dtype != torch.float16 or getattr(enc, "_aimus_fp32", False):
            return
        enc.float()
        orig = enc.forward

        def forward(*a, **k):
            # The offload context re-casts the whole DiT to fp16 each time it comes back to the GPU,
            # which would undo enc.float() above; put the encoder back in fp32 before every call.
            if next(enc.parameters()).dtype != torch.float32:
                enc.float()
            k = {n: (v.float() if torch.is_tensor(v) and v.is_floating_point() else v) for n, v in k.items()}
            a = tuple(v.float() if torch.is_tensor(v) and v.is_floating_point() else v for v in a)
            out = orig(*a, **k)
            hs = getattr(out, "last_hidden_state", None)
            if hs is not None:
                out.last_hidden_state = hs.to(torch.float16)
            return out

        enc.forward = forward
        enc._aimus_fp32 = True
        log("lyric encoder kept in fp32 (fp16 overflows with sung lyrics on Turing)")

    def unload_lm(self):
        if self.llm is not None:
            try:
                self.llm.unload()
            except Exception:
                pass
        self.llm, self.lm_name = None, None

    def unload(self):
        self.unload_lm()
        self.dit, self.dit_name = None, None
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def generate(self, caption, lyrics="", instrumental=True, duration=60.0, seed=None, bpm=None, keyscale="",
                 timesignature="", think=True, steps=DEFAULT_STEPS, shift=DEFAULT_SHIFT, reference_audio=None,
                 vocal_language="en", lm_temperature=0.85, progress=None):
        """One track. Returns {"audio": float32 [channels, samples] numpy, "sr", "seed", "meta"}."""
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        think = bool(think and self.lm_name)
        params = GenerationParams(
            caption=caption or "", lyrics=lyrics or "", instrumental=bool(instrumental),
            vocal_language=vocal_language if not instrumental else "unknown",
            bpm=int(bpm) if bpm else None, keyscale=keyscale or "", timesignature=str(timesignature or ""),
            duration=float(duration) if duration and duration > 0 else -1.0,
            inference_steps=int(steps), shift=float(shift), seed=int(seed) if seed is not None else -1,
            reference_audio=reference_audio or None, thinking=think, lm_temperature=float(lm_temperature),
            use_cot_metas=think, use_cot_caption=False, use_cot_language=False, use_cot_lyrics=False,   # the caption is the user's, not the LM's
        )
        config = GenerationConfig(batch_size=1, use_random_seed=seed is None,
                                  seeds=[int(seed)] if seed is not None else None, audio_format="wav")

        def cb(ratio=0.0, desc="", *a, **k):
            if progress:
                try:
                    progress("progress", desc or "", float(ratio or 0))
                except Exception:
                    pass

        result = generate_music(self.dit, self.llm, params, config, save_dir=None, progress=cb)
        if not result.success or not result.audios:
            raise RuntimeError(result.error or result.status_message or "generation failed")
        a = result.audios[0]
        tensor = a.get("tensor")
        if tensor is None:
            raise RuntimeError("no audio tensor in the result")
        audio = tensor.detach().float().cpu().numpy()
        if audio.ndim == 1:
            audio = audio[None, :]
        meta = {"seed": a.get("params", {}).get("seed")}
        lm = (result.extra_outputs or {}).get("lm_metadata") or {}
        for k in ("bpm", "keyscale", "timesignature", "duration", "caption"):
            if lm.get(k) not in (None, "", -1):
                meta["lm_" + k] = lm[k]
        tc = (result.extra_outputs or {}).get("time_costs") or {}
        if tc:
            meta["time_costs"] = {k: round(v, 1) for k, v in tc.items() if isinstance(v, (int, float))}
        return {"audio": audio, "sr": a.get("sample_rate", SAMPLE_RATE), "seed": meta["seed"], "meta": meta}


def make_loop(audio, sr, crossfade=2.0):
    """Make a track loop seamlessly: the last `crossfade` seconds are blended into the first ones
    (equal-power), and that tail is cut off. Returns the shortened [channels, samples] array."""
    import numpy as np
    n = audio.shape[1]
    f = int(min(crossfade, max(0.5, n / sr / 4)) * sr)
    if f <= 0 or n <= 2 * f:
        return audio
    head, tail = audio[:, :f], audio[:, n - f:]
    t = np.linspace(0.0, 1.0, f, dtype=np.float32)
    win_in, win_out = np.sqrt(t), np.sqrt(1.0 - t)
    out = audio[:, : n - f].copy()
    out[:, :f] = head * win_in + tail * win_out
    return out


def trim_silence(audio, sr, threshold=1e-3, keep=0.05):
    """Drop leading/trailing digital silence (the model sometimes pads a little); keeps `keep` seconds."""
    import numpy as np
    env = np.abs(audio).max(axis=0)
    idx = np.nonzero(env > threshold)[0]
    if idx.size == 0:
        return audio
    k = int(keep * sr)
    a, b = max(0, idx[0] - k), min(audio.shape[1], idx[-1] + 1 + k)
    return audio[:, a:b]


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
    # OGG goes through ffmpeg: libsndfile 1.2.2's Vorbis encoder kills the whole process on Windows
    # (no exception, a 4 KB file and exit code 127), even on 12 s of noise.
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

"""Kimodo text-to-motion for ai-anim: prompt -> SOMA-77 BVH (T-pose rest, 30 fps) + Kimodo .npz.

    python motion.py "a person walks forward slowly" --seconds 4 --out output/clips/Walk [--seed N]
    python motion.py --npz some_motion.npz --out output/clips/Walk        # just convert an .npz to BVH

The model is NVIDIA Kimodo-SOMA-RP (NVIDIA Open Model License, commercial use allowed), 282M parameters.
Its text encoder is LLM2Vec on Llama-3-8B-Instruct, which needs Hugging Face access to the gated Llama
repo (`hf auth login`). The encoder runs on the CPU (TEXT_ENCODER_DEVICE=cpu, ~16 GB RAM) so the 8 GB
card only holds the denoiser. Foot-skate post-processing needs Kimodo's compiled MotionCorrection
extension; it is skipped when that is not built (post_processing=False).

`generate()` is also used by anim_worker.py, which keeps the models loaded between requests.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KIMODO = os.path.join(ROOT, "kimodo")
if KIMODO not in sys.path:
    sys.path.insert(0, KIMODO)
os.environ.setdefault("TEXT_ENCODER_DEVICE", "cpu")
os.environ.setdefault("TEXT_ENCODER_MODE", "local")
os.environ.setdefault("LOCAL_CACHE", "True")

import numpy as np  # noqa: E402
import torch  # noqa: E402

MODEL = "kimodo-soma-rp"
FPS = 30
_model = None


def load(log=print):
    global _model
    if _model is None:
        from kimodo import load_model
        t0 = time.time()
        log("loading Kimodo-SOMA-RP + the Llama-3-8B text encoder (CPU)…")
        _model, _ = load_model(MODEL, device="cuda" if torch.cuda.is_available() else "cpu", return_resolved_name=True)
        log(f"Kimodo loaded in {time.time() - t0:.0f}s")
    return _model


def unload():
    global _model
    _model = None
    import gc
    gc.collect()
    torch.cuda.empty_cache()


def npz_to_bvh(npz_path, bvh_path):
    """Kimodo .npz (30- or 77-joint, with or without local rotations) -> T-pose SOMA-77 BVH."""
    from kimodo.exports.bvh import save_motion_bvh
    from kimodo.skeleton.registry import build_skeleton
    from kimodo.skeleton.transforms import global_rots_to_local_rots
    d = np.load(npz_path)
    nj = d["posed_joints"].shape[1]
    skel = build_skeleton(nj)
    pj = torch.from_numpy(d["posed_joints"]).float()
    root = pj[:, skel.root_idx]
    if "local_rot_mats" in d.files:
        local = torch.from_numpy(d["local_rot_mats"]).float()
    else:
        local = global_rots_to_local_rots(torch.from_numpy(d["global_rot_mats"]).float(), skel)
    if nj == 30:
        local = skel.output_to_SOMASkeleton77({"local_rot_mats": local, "root_positions": root})["local_rot_mats"]
        skel = skel.somaskel77
    save_motion_bvh(bvh_path, local, root, skeleton=skel, fps=FPS, standard_tpose=True)
    return bvh_path


def generate(prompts, seconds, out_stem, seed=None, steps=100, log=print, progress=None):
    """prompts: one string or a list (played in sequence). Writes <out_stem>.npz and .bvh; returns their paths."""
    from kimodo.exports.motion_io import save_kimodo_npz
    from kimodo.tools import seed_everything
    model = load(log)
    if isinstance(prompts, str):
        prompts = [prompts]
    prompts = [p.strip().rstrip(".") + "." for p in prompts if p.strip()]
    if isinstance(seconds, (int, float)):
        seconds = [float(seconds)] * len(prompts)
    num_frames = [max(int(s * model.fps), model.fps) for s in seconds]
    if seed is not None:
        seed_everything(int(seed))
    t0 = time.time()
    log(f"generating {sum(num_frames)} frames for {prompts} (seed {seed}, {steps} steps)")

    def bar(it, *a, **k):
        it = list(it)
        for i, x in enumerate(it):
            if progress:
                progress(i + 1, len(it))
            yield x

    out = model(prompts, num_frames, num_denoising_steps=steps, num_samples=1, multi_prompt=True,
                num_transition_frames=5, post_processing=False, return_numpy=True, progress_bar=bar)
    single = {k: (v[0] if hasattr(v, "shape") and len(v.shape) > 0 and v.shape[0] == 1 else v) for k, v in out.items()}
    os.makedirs(os.path.dirname(os.path.abspath(out_stem)), exist_ok=True)
    npz_path = out_stem + ".npz"
    save_kimodo_npz(npz_path, single)
    bvh_path = npz_to_bvh(npz_path, out_stem + ".bvh")
    log(f"motion written to {bvh_path} ({time.time() - t0:.0f}s)")
    return npz_path, bvh_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", help="what the person does; several sentences separated by ' | ' play in sequence")
    ap.add_argument("--seconds", type=float, default=4.0, help="length of the clip (or of each sentence)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--steps", type=int, default=100, help="diffusion steps (default 100)")
    ap.add_argument("--npz", help="convert this Kimodo .npz to BVH instead of generating")
    ap.add_argument("--out", required=True, help="output stem (writes <out>.npz and <out>.bvh)")
    a = ap.parse_args()
    if a.npz:
        print("wrote", npz_to_bvh(a.npz, a.out + ".bvh"))
        return
    if not a.prompt:
        ap.error("give a prompt or --npz")
    prompts = [p for p in a.prompt.split("|")]
    generate(prompts, a.seconds, a.out, a.seed, a.steps)


if __name__ == "__main__":
    main()

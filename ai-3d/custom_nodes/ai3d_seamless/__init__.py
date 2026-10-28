"""Seamless texture nodes for Z-Image-Turbo (and other DiT models).

SeamlessTileModel: rolls the latent by a per-step pseudo-random offset inside the sampler, so no fixed
seam can form; the roll amplitude tapers with the noise level. Adapted from Oliver Crosby's
ComfyUI-Universal-Seamless-Tiles (MIT, https://github.com/OliverCrosby/ComfyUI-Universal-Seamless-Tiles).
Circular Conv2d padding, the classic trick, does nothing for DiT models because they patchify
with a non-overlapping conv.

CircularVAEDecode: decodes with every Conv2d of the VAE decoder in circular padding mode so the
image's edges are continuous, then restores the VAE. The upstream pack deep-copied the VAE for this,
which fails on ComfyUI's pinned host buffers; patching in place for one decode needs no copy.

Installed by setup.ps1 into ComfyUI/custom_nodes/ai3d_seamless.
"""
import torch

TILING_MODES = ["enable", "x_only", "y_only", "disable"]


def _axes(tiling):
    return tiling in ("enable", "x_only"), tiling in ("enable", "y_only")


def _step_offsets(seed, sigma, height, width, tile_x, tile_y, scale, patch):
    # keyed on the sigma value so cond and uncond passes of one step share the offset;
    # offsets are multiples of the DiT patch size so the patch grid never shifts
    key = (int(seed) & 0x7FFFFFFF) ^ (int(sigma * 100000.0) & 0x7FFFFFFF)
    gen = torch.Generator().manual_seed(key)
    dy = int(round(scale * torch.randint(0, max(1, height), (1,), generator=gen).item())) // patch * patch if tile_y else 0
    dx = int(round(scale * torch.randint(0, max(1, width), (1,), generator=gen).item())) // patch * patch if tile_x else 0
    return dy, dx


def _tiling_wrapper(seed, tiling, patch):
    tile_x, tile_y = _axes(tiling)
    state = {"max_sigma": 0.0}

    def wrapper(apply_model, params):
        inp, timestep, c = params["input"], params["timestep"], params["c"]
        sigma = float(timestep.flatten().max().item())
        state["max_sigma"] = max(state["max_sigma"], sigma)
        # full-range rolls at high noise lay out tileable structure, the detail steps barely roll
        scale = sigma / state["max_sigma"] if state["max_sigma"] > 0 else 1.0
        dy, dx = _step_offsets(seed, sigma, inp.shape[-2], inp.shape[-1], tile_x, tile_y, scale, patch)
        if dy == 0 and dx == 0:
            return apply_model(inp, timestep, **c)
        dims = (-2, -1)
        c_rolled = dict(c)
        if torch.is_tensor(c.get("c_concat")):
            c_rolled["c_concat"] = torch.roll(c["c_concat"], shifts=(dy, dx), dims=dims)
        out = apply_model(torch.roll(inp, shifts=(dy, dx), dims=dims), timestep, **c_rolled)
        return torch.roll(out, shifts=(-dy, -dx), dims=dims)

    return wrapper


class SeamlessTileModel:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "tiling": (TILING_MODES,),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
            "patch": ("INT", {"default": 2, "min": 1, "max": 8,
                              "tooltip": "roll offsets are multiples of this many latent pixels (2 = Z-Image / Flux patch size)"}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "run"
    CATEGORY = "ai3d"
    DESCRIPTION = "Rolls the latent by a different offset every sampling step so the result tiles. Pair with CircularVAEDecode."

    def run(self, model, tiling, seed, patch):
        m = model.clone()
        if tiling != "disable":
            m.set_model_unet_function_wrapper(_tiling_wrapper(seed, tiling, patch))
        return (m,)


class CircularVAEDecode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"samples": ("LATENT",), "vae": ("VAE",)}}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "decode"
    CATEGORY = "ai3d"
    DESCRIPTION = "VAE decode with circular (wrap-around) convolution padding, so the decoded edges continue into each other."

    def decode(self, vae, samples):
        convs = [m for m in vae.first_stage_model.modules() if isinstance(m, torch.nn.Conv2d) and m.padding != (0, 0)]
        modes = [m.padding_mode for m in convs]
        for m in convs:
            m.padding_mode = "circular"
        try:
            images = vae.decode(samples["samples"])
        finally:
            for m, mode in zip(convs, modes):
                m.padding_mode = mode
        if len(images.shape) == 5:
            images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])
        return (images,)


NODE_CLASS_MAPPINGS = {"SeamlessTileModel": SeamlessTileModel, "CircularVAEDecode": CircularVAEDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"SeamlessTileModel": "Seamless Tile Model (ai3d)", "CircularVAEDecode": "Circular VAE Decode (ai3d)"}

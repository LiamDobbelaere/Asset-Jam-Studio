"""First + last frame conditioning for Wan 2.2 TI2V-5B.

ComfyUI's built-in Wan22ImageToVideoLatent only takes a start image. The 5B model is conditioned by
in-painting the latent (noise_mask = 0 where the content is known), so an end frame is the same trick at the
other end of the latent: encode it, drop it into the last latent frame and clear its mask. That is what the
14B WanFirstLastFrameToVideo node does with its concat mask; here it is done for the 5B latent layout
(48 channels, 4x temporal / 16x spatial compression).

Installed by ai-vid/setup.ps1 into ai-3d/ComfyUI/custom_nodes/aivid_wan.
"""
import torch

import comfy.latent_formats
import comfy.model_management
import comfy.utils
import nodes


class AIVidWan22FirstLastFrameToVideoLatent:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vae": ("VAE",),
                "width": ("INT", {"default": 832, "min": 32, "max": nodes.MAX_RESOLUTION, "step": 32}),
                "height": ("INT", {"default": 480, "min": 32, "max": nodes.MAX_RESOLUTION, "step": 32}),
                "length": ("INT", {"default": 73, "min": 1, "max": nodes.MAX_RESOLUTION, "step": 4}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
            },
            "optional": {
                "start_image": ("IMAGE",),
                "end_image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "run"
    CATEGORY = "aivid"
    DESCRIPTION = "Wan 2.2 5B latent with an optional first and/or last frame pinned (noise-mask in-painting)."

    def run(self, vae, width, height, length, batch_size, start_image=None, end_image=None):
        dev = comfy.model_management.intermediate_device()
        t = ((length - 1) // 4) + 1
        latent = torch.zeros([1, 48, t, height // 16, width // 16], device=dev)
        if start_image is None and end_image is None:
            return ({"samples": latent},)
        mask = torch.ones([1, 1, t, latent.shape[-2], latent.shape[-1]], device=dev)

        def encode(img, n):
            img = comfy.utils.common_upscale(img[:n].movedim(-1, 1), width, height, "bilinear", "center").movedim(1, -1)
            return vae.encode(img)

        if start_image is not None:
            s = encode(start_image, length)
            latent[:, :, :s.shape[-3]] = s
            mask[:, :, :s.shape[-3]] = 0.0
        if end_image is not None:
            e = encode(end_image[-1:], 1)            # one picture -> one latent frame
            latent[:, :, -e.shape[-3]:] = e
            mask[:, :, -e.shape[-3]:] = 0.0

        fmt = comfy.latent_formats.Wan22()
        latent = fmt.process_out(latent) * mask + latent * (1.0 - mask)
        return ({"samples": latent.repeat((batch_size,) + (1,) * (latent.ndim - 1)),
                 "noise_mask": mask.repeat((batch_size,) + (1,) * (mask.ndim - 1))},)


NODE_CLASS_MAPPINGS = {"AIVidWan22FirstLastFrameToVideoLatent": AIVidWan22FirstLastFrameToVideoLatent}
NODE_DISPLAY_NAME_MAPPINGS = {"AIVidWan22FirstLastFrameToVideoLatent": "Wan 2.2 5B First+Last Frame to Video (aivid)"}

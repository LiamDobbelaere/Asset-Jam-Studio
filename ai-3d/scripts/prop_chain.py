"""The steps of the modelled-prop chain that the studio's prop flow and style_eval.py share, so both run the same:
which reference props a style shows (prop_refs.py) and how the reference picture is drawn.

    picture: Z-Image with the style's picture words (prop_style.picture_prompt). A style with "picture_from":
    "restyle" then has FLUX.2 klein redraw that picture in the art style of its most similar own prop (image 2), so
    the picture carries the artist's palette and painting while Z-Image keeps the object right. "refs" draws the new
    prop from the reference props alone; klein then copies them too closely (asked for a wheelie bin with the artist's
    pedal bin as the sample, it drew the pedal bin; a stool came out as their kitchen chair), so it is not the default.
    "lora" draws it with klein from text alone through the style's "picture_lora", a LoRA trained on the artist's own
    props (style_lora_data.py --picture), captioned the same way as its training pictures (PICTURE_CAPTION).
"""
import uuid

import generate as gen3d
import paint
import prop_refs
import prop_style

PICTURE_CAPTION = ("{prompt}, a 3D game prop with a hand-painted texture, three-quarter view from slightly above, plain "
                   "light grey background")   # = style_lora_data.py PICTURE_CAPTION, the LoRA's training captions
KLEIN_RESTYLE = ("Redraw image 1 in exactly the art style of image 2: the same simple low-poly modelling, hand-painted "
                 "texture, colour palette, shading and outlines as image 2. Keep the object, its shape, its parts and its "
                 "design from image 1: {prompt}. Image 2 is a different object, only its style matters. Plain light grey "
                 "background.")
KLEIN_PICTURE = ("Draw a new 3D game prop in exactly the art style of {images}: the same simple low-poly modelling, "
                 "the same hand-painted texture, colours, shading and outlines, the same rendering and light. The new "
                 "prop: {prompt}. Only this one object, not the object{s} in {images}. Centred, whole object in view, "
                 "three-quarter view from slightly above, plain light grey background, no shadows.")


def refs_for(style, prompt, n=3):
    """The style's reference props most like the prompt ([] for a style without a library)."""
    st = prop_style.load(style) if isinstance(style, str) or style is None else style
    return prop_refs.pick(st, prompt, n) if st and st.get("refs") else []


def picture(prompt, style, seed, refs=(), raw=False, upload=paint._cli_upload, run_api=paint._cli_run, prefix="paint/picture"):
    """Draw the reference picture; returns its path."""
    st = prop_style.load(style) if isinstance(style, str) or style is None else style
    mode = st.get("picture_from") if st and not raw else None
    if mode == "lora" and st.get("picture_lora"):
        api = paint.klein_t2i_api(PICTURE_CAPTION.format(prompt=prompt), seed, prefix, lora=st["picture_lora"])
        files = run_api(api)
        if not files:
            raise RuntimeError("ComfyUI returned no reference picture")
        return files[0]
    if not refs:
        mode = None
    if mode == "refs":
        tag = uuid.uuid4().hex[:6]
        names = [upload(prop_refs.front_render(st, r), f"picref_{tag}_{i}.png") for i, r in enumerate(refs[:2])]
        images = "image 1" if len(names) == 1 else "images 1 and 2"
        api = paint.klein_picture_api(names, KLEIN_PICTURE.format(images=images, prompt=prompt, s="s" if len(names) > 1 else ""),
                                      seed, prefix, lora=st.get("picture_lora"))
    else:
        text, raw = prop_style.picture_prompt(prompt, st, raw)
        api, _ = gen3d.build_api(text, "gameasset", raw, seed, image_only=True)
        if mode == "restyle":
            drawn = run_api(api)
            if not drawn:
                raise RuntimeError("ComfyUI returned no reference picture")
            tag = uuid.uuid4().hex[:6]
            names = [upload(drawn[0], f"picz_{tag}.png"), upload(prop_refs.front_render(st, refs[0]), f"picref_{tag}.png")]
            api = paint.klein_picture_api(names, KLEIN_RESTYLE.format(prompt=prompt), seed, prefix, lora=st.get("picture_lora"))
    files = run_api(api)
    if not files:
        raise RuntimeError("ComfyUI returned no reference picture")
    return files[0]

"""Runs inside a windowed Blender with the StableGen add-on (its generate operator is modal and needs a window):
paints the model from its reference picture through ComfyUI, saves the scene and quits. paint.py starts it.

    blender --python paint_drive.py -- model.glb picture.png camera.json "prompt" workdir

prompt is "shared | front-only details | plain wording for the other views". Details the picture shows (a logo,
drawers) go on the picture's camera only, so they are not repeated on every side; the plain wording must describe
the whole prop (a top view told "plain cardboard sleeve" paints the lid as cardboard).
Cameras: the picture's own, from the silhouette fit, then a ring at eye level and one from above; PAINT_LOW=1 adds
two low views for props with undersides (chair stretchers, table frames). Elsewhere they invent a second logo
copied from the IP-Adapter picture.
Env: PAINT_SERVER (127.0.0.1:8188), PAINT_CHECKPOINT, PAINT_SEED, PAINT_IPA (IP-Adapter strength, 0.8),
PAINT_DISCARD (degrees, 65: the preset's 80 smears front details round the sides).
Writes workdir/<name>_sg.blend and workdir/<name>_drive.json ({status, error, seconds}).
"""
import json
import math
import os
import sys
import time
import traceback

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
glb, pic, camf, prompt, workdir = argv[:5]
name = os.path.splitext(os.path.basename(glb))[0]
shared, front, plain = ([p.strip() for p in prompt.split("|")] + ["", ""])[:3]
CAMS = ((60, 10, "side view"), (120, 15, "back side view"), (180, 15, "back view"), (240, 15, "back side view"),
        (300, 10, "side view"), (30, 60, "top view"))
if os.environ.get("PAINT_LOW"):
    CAMS += ((90, -25, "low view from below"), (270, -25, "low view from below"))
ADDON = "bl_ext.user_default.stablegen"
os.makedirs(workdir, exist_ok=True)
RESULT = os.path.join(workdir, name + "_drive.json")
state = {}


def log(*a):
    print("[paint]", " ".join(str(x) for x in a), flush=True)


def finish(status, error=""):
    json.dump({"status": status, "error": error, "seconds": round(time.time() - state.get("t0", time.time()))},
              open(RESULT, "w"))
    bpy.ops.wm.quit_blender()


def view3d_override():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == "VIEW_3D":
            region = [r for r in area.regions if r.type == "WINDOW"][0]
            return dict(window=win, screen=win.screen, area=area, region=region)
    raise RuntimeError("no 3D view")


def add_cam(cam_name, pos, target, fov_deg=30):
    cd = bpy.data.cameras.new(cam_name)
    cd.angle = math.radians(fov_deg)
    co = bpy.data.objects.new(cam_name, cd)
    bpy.context.scene.collection.objects.link(co)
    co.location = pos
    co.rotation_euler = (Vector(target) - Vector(pos)).to_track_quat("-Z", "Y").to_euler()
    return co


def setup():
    try:
        prefs = bpy.context.preferences
        prefs.system.use_online_access = True
        if ADDON not in prefs.addons:
            raise RuntimeError("the StableGen add-on is not installed in this Blender profile: run ai-3d/setup_paint.ps1")
        ap = prefs.addons[ADDON].preferences
        ap.server_address = os.environ.get("PAINT_SERVER", "127.0.0.1:8188")
        ap.output_dir = workdir
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.ops.import_scene.gltf(filepath=glb)
        obj = [o for o in bpy.data.objects if o.type == "MESH"][0]
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        # StableGen replaces the materials; paint_bake.py needs each part's modelled colour for --lock
        cols = {}
        for m in obj.data.materials:
            bsdf = m.node_tree.nodes.get("Principled BSDF") if m and m.node_tree else None
            if bsdf:
                cols[m.name.split(".")[0]] = list(bsdf.inputs["Base Color"].default_value[:3])
        obj["paint_part_colors"] = json.dumps(cols)
        sc = bpy.context.scene
        sc.render.resolution_x = sc.render.resolution_y = 1024

        # the picture's camera direction (glTF frame -> Blender: (x, y, z) -> (x, -z, y)), all cameras at 30 deg FOV
        # with the prop filling the frame
        cam = json.load(open(camf))
        p0 = Vector((cam["pos"][0], -cam["pos"][2], cam["pos"][1]))
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        c, r = (lo + hi) / 2, (hi - lo).length / 2
        d0 = (p0 - c).normalized()
        dist = r / math.sin(math.radians(15)) * 1.05
        add_cam("Cam0_picture", c + d0 * dist, c)
        view_prompts = {"Cam0_picture": "front view" + (", " + front if front else "")}
        az0 = math.atan2(d0.y, d0.x)
        for i, (daz, el, what) in enumerate(CAMS):
            a, e = az0 + math.radians(daz), math.radians(el)
            add_cam(f"Cam{i + 1}", c + Vector((math.cos(a) * math.cos(e), math.sin(a) * math.cos(e), math.sin(e))) * dist, c)
            view_prompts[f"Cam{i + 1}"] = what + (", " + plain if plain else "")
        sc.camera_prompts.clear()
        for k, v in view_prompts.items():
            it = sc.camera_prompts.add()
            it.name, it.prompt = k, v
        sc.use_camera_prompts = True
        sc.camera = bpy.data.objects["Cam0_picture"]

        # ARCHITECTURE: SDXL + depth ControlNet + Lightning LoRA, each view generated separately, the most
        # straight-on camera wins per point (flat details are not blurred across views)
        sc.stablegen_preset = "ARCHITECTURE"
        bpy.ops.stablegen.apply_preset()
        sc.model_name = os.environ.get("PAINT_CHECKPOINT", "RealVisXL_V5.0_fp16.safetensors")
        if os.environ.get("PAINT_SEED"):
            sc.seed = int(os.environ["PAINT_SEED"])
        sc.comfyui_prompt = shared
        sc.comfyui_negative_prompt = ("photo, photorealistic, text, watermark, harsh shadows, specular highlights, "
                                      "reflections, dirt, noise, blurry")
        sc.fallback_color = (1.0, 0.0, 1.0)   # magenta: paint_bake.py fills it from the neighbouring texels
        sc.ipadapter_strength = float(os.environ.get("PAINT_IPA", "0.8"))
        sc.discard_factor = float(os.environ.get("PAINT_DISCARD", "65"))
        sc.use_ipadapter = True
        sc.ipadapter_image = pic
        log("cameras", len(view_prompts), "seed", sc.seed, "checkpoint", sc.model_name)
        with bpy.context.temp_override(**view3d_override()):
            res = bpy.ops.object.test_stable("EXEC_DEFAULT")
        log("generate", res)
        state["t0"] = time.time()
        bpy.app.timers.register(watch, first_interval=5)
    except Exception:
        log(traceback.format_exc())
        finish("error", traceback.format_exc(limit=3))
    return None


def watch():
    sc = bpy.context.scene
    from bl_ext.user_default.stablegen.texturing.generator import ComfyUIGenerate
    if (ComfyUIGenerate._is_running or sc.generation_status == "running") and time.time() - state["t0"] < 3600:
        return 5
    err = getattr(sc, "sg_last_gen_error_message", "") or ""
    log("finished", sc.generation_status, err, f"{time.time() - state['t0']:.0f}s")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(workdir, name + "_sg.blend"))
    finish("error" if err else "done", err)
    return None


bpy.app.timers.register(setup, first_interval=2)

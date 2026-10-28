"""Text -> textured 3D model from the command line.

Usage:
    generate.bat "an office desk chair"                       low-poly game asset (default preset)
    generate.bat "a wooden treasure chest" --preset detailed  full-detail PBR model
    generate.bat "a water cooler" --shape round --size 1.6 --name WaterCooler

Requires the ComfyUI server to be running (start_comfyui.bat). Loads the API-format prompt for the
preset, patches prompt / seed / options, queues it, waits, and prints the output paths. With the
gameasset preset it also converts the GLB to <name>.fbx + <name>Diffuse.png with Blender.

build_api() and glb_to_fbx() are also used by the studio web UI (../../studio).
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188")
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
OUT_DIR = os.path.join(ROOT, "ComfyUI", "output")

PRESETS = {
    "gameasset": {
        "workflow": "text_to_3d_gameasset_api.json",
        "style": ", low-poly stylized 3D game prop, smooth rounded forms with soft bevelled edges, clean hand-painted "
                 "flat colors with soft shading, slight toon look, no fine surface detail, only this one object and nothing else, centered, "
                 "full object in view, three-quarter view, plain light grey studio background, no shadows",
        "resolution": 1024, "texture": 1024,
    },
    "character": {
        # a person / creature to rig and animate with ai-anim: front view, T-pose, so Make-It-Animatable gets a
        # canonical shape and the limbs are separated from the body
        "workflow": "text_to_3d_gameasset_api.json",
        "style": ", full body 3D game character, standing upright in a T-pose with both arms stretched out horizontally "
                 "to the sides, legs slightly apart, seen from the front, symmetrical, low-poly stylized with smooth rounded "
                 "forms, clean hand-painted flat colors with soft shading, slight toon look, only this one character and "
                 "nothing else, centered, whole figure in view from head to feet, plain light grey studio background, no shadows",
        "resolution": 1024, "texture": 1024, "tris": 4000,
    },
    "detailed": {
        "workflow": "text_to_3d_api.json",
        "style": ", single object, 3D game asset, centered, full object in view, three-quarter view, "
                 "plain light grey studio background, soft even lighting, no shadows, high detail",
        "resolution": 1536, "texture": 4096,
    },
}

# Triangle budget of the QEM decimation and the planar-dissolve angle Blender applies afterwards (glb_to_fbx.py).
# Small parts (castor wheels, armrest pads, a robot's feet) are the first thing QEM collapses, whatever the feature
# weights: at 1500 triangles an office chair has no wheels, at 3000 it has. Flat panels then give most of their
# triangles back in the dissolve (a filing cabinet decimated to 3000 lands near 1200), so the budget goes where it is
# needed. Round props have nothing to dissolve and no small parts, so they keep a smaller budget (todo/ai-3d-improvements.md).
TRIS = {"hard": 3000, "round": 1500}
DISSOLVE = {"hard": 10.0, "round": 0.0}
# Voxel resolution of the remesh the decimator works from. 512 (+15 s) keeps taps, ribs and collars separate where
# 256 melts them into one lump (water cooler A/B against the `detailed` mesh). Perforated parts are the exception:
# at 512 the grid resolves the holes of a mesh chair back and the decimator stalls on them, so use --remesh 256 there.
REMESH = {"hard": 512, "round": 384}
# Diffuse = base colour x AO, then a blur for the painted look. On hard-surface props the 2 px blur smears the
# handles and panel lines the normal map restores and AO 0.45 darkens every recess into a smudge; 0 / 0.3 reads
# crisper on the filing cabinet A/B. Round props keep the softer painted look.
DIFFUSE = {"hard": {"blur": 0, "ao": 0.3}, "round": {"blur": 2, "ao": 0.45}}

# How the dense Pixal3D / TRELLIS.2 surface becomes the low-poly mesh. "soft" is the original pipeline, which
# came out melted: 20 Taubin passes on the remesh and midpoint edge collapses round every corner off (todo/ai-3d.md).
# "sharp" keeps the creases: QEF dual-contouring vertices on the remesh, 2 smoothing passes only to take the
# voxel staircase out, and QEM-optimal vertex placement with line quadrics pinned on feature edges, so a box stays
# a box at 1500 triangles.
EDGES = {
    "soft": {"remesh_smooth": 20, "remesh_qef": False, "decimate": "midpoint"},
    "sharp": {"remesh_smooth": 2, "remesh_qef": True, "decimate": "qem", "feature_weight": 50.0, "feature_angle": 30.0},
}
VIEWPORT_STATE = {"image": "", "mask": "", "normal": "", "recording": "", "model_3d_info": [],
                  "camera_info": {"position": {"x": 10, "y": 10, "z": 10}, "target": {"x": 0, "y": 0, "z": 0}, "zoom": 1,
                                  "cameraType": "perspective", "fov": 35, "aspect": 1, "near": 0.01, "far": 10000}}


def get(path):
    with urllib.request.urlopen(SERVER + path, timeout=60) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(SERVER + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def find(api, class_type, title_contains=None):
    for k, v in api.items():
        if v["class_type"] == class_type and (title_contains is None or title_contains.lower() in v["_meta"]["title"].lower()):
            return k
    raise SystemExit(f"node {class_type} not found in workflow")


def prune_to(api, keep_ids):
    keep = set(keep_ids)
    changed = True
    while changed:
        changed = False
        for k in list(keep):
            for v in api[k]["inputs"].values():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in keep:
                    keep.add(v[0])
                    changed = True
    return {k: v for k, v in api.items() if k in keep}


def asset_name(prompt, fallback="Prop"):
    return "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", prompt))[:40] or fallback


def build_api(prompt, preset="gameasset", raw=False, seed=None, model="pixal3d", resolution=None, texture=None,
              tris=None, shape="hard", image_only=False, reference_image=None, edges="sharp", normalmap=True,
              remesh=None, save_highpoly=False, normal_from="remesh", blur=None, ao=None):
    """Patch the preset's API workflow. Returns (api, info).

    reference_image: name of a picture already uploaded to ComfyUI's input folder; the text-to-image stage is
    then replaced by that picture (the prompt still drives nothing else, Pixal3D/TRELLIS work from the image).
    edges: "sharp" (default) or "soft", see EDGES. normalmap: bake a tangent-space normal map from the remeshed
    high-poly into the low-poly's UVs (gameasset/character). remesh: voxel resolution of the remesh (default 512,
    round 384; 256 for perforated parts, see REMESH). save_highpoly: also write the remeshed high-poly mesh as 3d/highpoly_*.glb. normal_from: which
    high-poly the normal map is baked from, "remesh" (the remeshed shell) or "raw" (the shape VAE's own mesh,
    before the remesh: keeps the finest detail but also its voxel staircase). blur: radius in px of the blur on
    the diffuse (0 = none); ao: how much the baked AO darkens the diffuse (0..1). Both default per shape, see DIFFUSE.
    """
    p = PRESETS[preset]
    api = json.load(open(os.path.join(ROOT, "workflows", p["workflow"]), encoding="utf-8"))
    text = prompt if raw else prompt + p["style"]
    api[find(api, "CLIPTextEncode", "PROMPT")]["inputs"]["text"] = text
    if seed is None:
        seed = random.randint(0, 2**31)
    api[find(api, "KSampler", "Text to image")]["inputs"]["seed"] = seed
    api[find(api, "PrimitiveBoolean")]["inputs"]["value"] = (model == "trellis2")
    resolution = resolution or p["resolution"]
    texture = texture or p["texture"]
    api[find(api, "Trellis2UpsampleStage")]["inputs"]["target_resolution"] = resolution
    api[find(api, "PrimitiveInt", "Texture")]["inputs"]["value"] = texture
    tris = tris or p.get("tris") or TRIS[shape]
    if preset in ("gameasset", "character"):
        e = EDGES[edges]
        dec = api[find(api, "DecimateMesh")]["inputs"]
        rem = api[find(api, "RemeshMesh")]["inputs"]
        dec["target_face_count"] = tris
        rem["smooth_iters"] = e["remesh_smooth"]
        rem["sign_mode.qef"] = e["remesh_qef"]
        for k in [k for k in dec if k.startswith("placement_mode.")]:
            del dec[k]
        dec["placement_mode"] = e["decimate"]
        if e["decimate"] == "qem":
            dec["placement_mode.line_quadric_weight"] = e.get("line_weight", 0.0)
            dec["placement_mode.feature_edge_quadric_weight"] = e.get("feature_weight", 0.0)
            dec["placement_mode.feature_edge_min_dihedral_deg"] = e.get("feature_angle", 30.0)
            dec["placement_mode.clamp_v_to_edge"] = True
        if shape == "round":
            for k, v in api.items():
                if v["class_type"] == "MeshSmoothNormals":
                    v["inputs"]["crease_angle"] = 180.0
            rem["sign_mode.qef"] = False   # QEF makes jagged creases on organic surfaces
        rem["resolution"] = remesh or REMESH[shape]
        blur = DIFFUSE[shape]["blur"] if blur is None else blur
        ao = DIFFUSE[shape]["ao"] if ao is None else ao
        blend_id, blur_id = find(api, "ImageBlend"), find(api, "ImageBlur")
        api[blend_id]["inputs"]["blend_factor"] = ao
        if blur > 0:
            api[blur_id]["inputs"]["blur_radius"] = int(blur)
        else:   # take the blur node out of the chain (its radius cannot be 0)
            for v in api.values():
                for k, inp in v["inputs"].items():
                    if inp == [blur_id, 0]:
                        v["inputs"][k] = [blend_id, 0]
            del api[blur_id]
        remesh_id, unwrap_id = find(api, "RemeshMesh"), find(api, "UnwrapMesh")
        high_id = find(api, "GetMeshInfo") if normal_from == "raw" else remesh_id   # GetMeshInfo passes the VAE mesh through
        if normalmap:
            api["9010"] = {"class_type": "BakeNormalMapFromMesh", "_meta": {"title": "Bake normal map (high -> low)"},
                           "inputs": {"low_poly": [unwrap_id, 0], "high_poly": [high_id, 0], "resolution": texture,
                                      "cage_distance": 0.05, "ignore_backfaces": True}}
            api[find(api, "ApplyTextureToMesh")]["inputs"]["normal_map"] = ["9010", 0]
            api["9011"] = {"class_type": "SaveImage", "_meta": {"title": "Normal map (PNG)"},
                           "inputs": {"filename_prefix": "text_to_3d/normal", "images": ["9010", 0]}}
        if save_highpoly:
            api["9020"] = {"class_type": "MeshToFile3D", "_meta": {"title": "High poly (remeshed)"}, "inputs": {"mesh": [remesh_id, 0]}}
            api["9021"] = {"class_type": "Save3DAdvanced", "_meta": {"title": "Save high poly"},
                           "inputs": {"model_3d": ["9020", 0], "filename_prefix": "3d/highpoly", "viewport_state": VIEWPORT_STATE,
                                      "width": 1024, "height": 1024}}
    if reference_image:
        decode = find(api, "VAEDecode")
        api["9001"] = {"class_type": "LoadImage", "_meta": {"title": "Reference image (uploaded)"},
                       "inputs": {"image": reference_image}}
        for v in api.values():
            for k, inp in v["inputs"].items():
                if inp == [decode, 0]:
                    v["inputs"][k] = ["9001", 0]
    if image_only:
        api = prune_to(api, [find(api, "SaveImage", "Reference")])
    info = {"preset": preset, "seed": seed, "model": model, "resolution": resolution, "texture": texture, "text": text}
    if preset in ("gameasset", "character"):
        info.update(tris=tris, shape=shape, edges=edges, normalmap=bool(normalmap), normal_from=normal_from,
                    remesh=remesh or REMESH[shape], blur=blur, ao=ao)
    return api, info


def glb_to_fbx(glb, out_dir, name, size, level=True, dissolve=0.0, crease=60.0):
    """Convert a GLB to <out_dir>/<name>.fbx + <name>Diffuse.png with Blender. Returns (fbx, png, stats).

    With level=True (default) the mesh is first set flat on the ground and the GLB is rewritten in
    that pose too; see scripts/level_mesh.py. dissolve > 0 merges coplanar triangles within that many
    degrees (inside UV islands) before export, see glb_to_fbx.py; crease is the hard-edge angle used when
    the normals are recomputed after the dissolve."""
    if not os.path.exists(BLENDER):
        raise RuntimeError(f"Blender not found at {BLENDER}; set the BLENDER env var to convert to FBX")
    cmd = [BLENDER, "-b", "--python", os.path.join(ROOT, "scripts", "glb_to_fbx.py"), "--",
           glb, out_dir, name, str(size), "level" if level else "nolevel", f"dissolve={dissolve}", f"crease={crease}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    line = next((l for l in r.stdout.splitlines() if l.startswith("GLB2FBX")), None)
    if not line:
        raise RuntimeError("FBX conversion failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    fbx = os.path.join(out_dir, name + ".fbx")
    stats = line[len("GLB2FBX "):].strip()
    if stats.startswith(fbx):
        stats = stats[len(fbx):].strip()
    return fbx, os.path.join(out_dir, name + "Diffuse.png"), stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="describe ONE object; the preset appends its style hints")
    ap.add_argument("--preset", choices=list(PRESETS), default="gameasset")
    ap.add_argument("--raw", action="store_true", help="use the prompt verbatim, no style hints")
    ap.add_argument("--seed", type=int, default=None, help="seed for the reference image (default random)")
    ap.add_argument("--model", choices=["pixal3d", "trellis2"], default="pixal3d")
    ap.add_argument("--resolution", type=int, default=None, help="shape voxel resolution 1024..2048 (preset default)")
    ap.add_argument("--texture", type=int, default=None, help="texture atlas size (preset default)")
    ap.add_argument("--tris", type=int, default=None, help="gameasset: triangle budget of the decimation (default 3000, round 1500, "
                                                            "character 4000); the planar dissolve then takes flat panels back down")
    ap.add_argument("--shape", choices=["hard", "round"], default="hard",
                    help="gameasset: 'round' for bottles, pots, balls: fully smooth shading, no hard-edge split, "
                         "finer remesh and a 1500-triangle default so curves don't facet")
    ap.add_argument("--edges", choices=list(EDGES), default="sharp",
                    help="gameasset: sharp (default) keeps corners and creases through remesh + decimation; soft is the old "
                         "rounded-off pipeline")
    ap.add_argument("--no-normalmap", action="store_true", help="gameasset: skip baking the high-poly normal map into the GLB")
    ap.add_argument("--remesh", type=int, default=None,
                    help="gameasset: remesh voxel resolution (default 512, round 384); 256 for perforated parts such as a mesh "
                         "chair back, where 512 resolves the holes and the decimator stalls on them")
    ap.add_argument("--save-highpoly", action="store_true", help="gameasset: also save the remeshed high-poly GLB")
    ap.add_argument("--normal-from", choices=["remesh", "raw"], default="remesh",
                    help="gameasset: bake the normal map from the remeshed shell (default) or from the raw shape-VAE mesh")
    ap.add_argument("--blur", type=int, default=None, help="gameasset: blur radius on the diffuse in px (default 0, round 2)")
    ap.add_argument("--ao", type=float, default=None, help="gameasset: AO strength multiplied into the diffuse (default 0.3, round 0.45)")
    ap.add_argument("--dissolve", type=float, default=None,
                    help="gameasset: after decimation merge coplanar triangles within this many degrees in Blender (default 10, "
                         "round 0; 0 = off), so flat panels give their triangles back; FBX and GLB are written with the reduced mesh")
    ap.add_argument("--name", default=None, help="gameasset: asset name for the FBX/PNG (default from prompt)")
    ap.add_argument("--size", type=float, default=1.0, help="gameasset: longest dimension in metres for the FBX")
    ap.add_argument("--no-level", action="store_true", help="keep the camera-frame tilt instead of setting the mesh flat")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets"), help="gameasset: folder for FBX + PNG")
    ap.add_argument("--no-fbx", action="store_true", help="gameasset: skip the Blender FBX conversion")
    ap.add_argument("--image-only", action="store_true", help="only generate the reference image")
    ap.add_argument("--dump", help="write the patched API prompt JSON here and exit")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()

    api, info = build_api(a.prompt, a.preset, a.raw, a.seed, a.model, a.resolution, a.texture, a.tris, a.shape,
                          a.image_only, edges=a.edges, normalmap=not a.no_normalmap, remesh=a.remesh,
                          save_highpoly=a.save_highpoly, normal_from=a.normal_from, blur=a.blur, ao=a.ao)
    if a.dump:
        json.dump(api, open(a.dump, "w", encoding="utf-8"), indent=1)
        print("wrote", a.dump)
        return

    res = post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})
    if "prompt_id" not in res:
        print(json.dumps(res, indent=1))
        sys.exit(1)
    pid = res["prompt_id"]
    print(f"queued {pid}  preset={a.preset} seed={info['seed']} model={a.model} res={info['resolution']} "
          f"tex={info['texture']}" + (f" tris={info['tris']} shape={a.shape} edges={a.edges} normalmap={not a.no_normalmap}"
                                      if a.preset in ("gameasset", "character") else ""))
    t0 = time.time()
    last = ""
    while time.time() - t0 < a.timeout:
        hist = get(f"/history/{pid}")
        if pid in hist:
            break
        q = get("/queue")
        msg = f"  running={len(q.get('queue_running', []))} pending={len(q.get('queue_pending', []))} {time.time() - t0:.0f}s"
        if msg != last:
            print(msg, end="\r", flush=True)
            last = msg
        time.sleep(3)
    else:
        print("timed out")
        sys.exit(3)

    h = hist[pid]
    st = h.get("status", {})
    if st.get("status_str") == "error":
        for m in st.get("messages", []):
            if m[0] == "execution_error":
                print("\nERROR in node", m[1].get("node_type"), ":", m[1].get("exception_message"))
        sys.exit(2)
    print(f"\ndone in {time.time() - t0:.0f}s")
    glb = None
    for out in h.get("outputs", {}).values():
        for img in out.get("images", []):
            if img.get("type") == "output":
                print("  image:", os.path.join(OUT_DIR, img.get("subfolder", ""), img["filename"]))
        for item in out.get("3d", []) or []:
            if isinstance(item, dict) and item.get("filename", "").endswith(".glb") and item.get("subfolder") == "3d":
                glb = os.path.join(OUT_DIR, "3d", item["filename"])
                print("  model:", glb)
    if a.image_only:
        return
    if glb is None:
        files = sorted(glob.glob(os.path.join(OUT_DIR, "3d", "*.glb")), key=os.path.getmtime)
        glb = files[-1] if files else None
    if a.preset in ("gameasset", "character") and glb and not a.no_fbx:
        name = a.name or asset_name(a.prompt)
        try:
            dissolve = DISSOLVE[a.shape] if a.dissolve is None else a.dissolve
            fbx, png, stats = glb_to_fbx(glb, a.out, name, a.size, level=not a.no_level, dissolve=dissolve,
                                         crease=180.0 if a.shape == "round" else 60.0)
        except RuntimeError as e:
            print("  " + str(e))
            sys.exit(4)
        print("  fbx:", fbx, stats)
        print("  texture:", png)


if __name__ == "__main__":
    main()

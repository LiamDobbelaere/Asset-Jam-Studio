"""Find the camera the reference picture was taken from, by matching the model's silhouette to the picture's mask.

    python paint_fit.py model.glb mask.png out.json

Grid search over yaw / elevation / field of view, then a finer pass around the best; scale and offset in the image
come from the bounding boxes, with separate x / y scale so small proportion errors in the model don't bend the
camera angle. The model's front is +Z in the glTF frame (Y up), so only views from the front half are tried.
"""
import json
import math
import sys

import numpy as np
import trimesh
from PIL import Image, ImageDraw


def fit(glb, mask_path):
    mesh = trimesh.load(glb, force="mesh")
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces)
    lo, hi = V.min(0), V.max(0)
    C = (lo + hi) / 2
    R = np.linalg.norm(hi - lo) / 2

    M = np.asarray(Image.open(mask_path).convert("L"), float) / 255 > 0.5
    H, W = M.shape
    S = 256
    Ms = np.asarray(Image.fromarray(M.astype(np.uint8) * 255).resize((S, S * H // W)), float) > 127
    ys, xs = np.nonzero(M)
    if not len(xs):
        raise RuntimeError(f"the mask {mask_path} is empty: background removal found no object in the picture")
    mb = np.array([xs.min(), ys.min(), xs.max(), ys.max()], float)

    def camera(yaw, elev, fov):
        d = np.array([math.sin(yaw) * math.cos(elev), math.sin(elev), math.cos(yaw) * math.cos(elev)])
        pos = C + d * R / math.sin(fov / 2)
        fwd = -d
        right = np.cross(fwd, [0, 1, 0])
        right /= np.linalg.norm(right)
        return pos, right, np.cross(right, fwd), fwd

    def iou(params):
        pos, right, up, fwd = cam = camera(*params)
        P = V - pos
        z = P @ fwd
        uv = np.stack([P @ right / z, P @ up / z], 1)
        ub = np.array([uv[:, 0].min(), -uv[:, 1].max(), uv[:, 0].max(), -uv[:, 1].min()])
        f = ((mb[2] - mb[0]) / (ub[2] - ub[0]), (mb[3] - mb[1]) / (ub[3] - ub[1]))
        cx = (mb[0] + mb[2]) / 2 - f[0] * (ub[0] + ub[2]) / 2
        cy = (mb[1] + mb[3]) / 2 - f[1] * (ub[1] + ub[3]) / 2
        px = (f[0] * uv[:, 0] + cx) * S / W
        py = (-f[1] * uv[:, 1] + cy) * S / W
        img = Image.new("L", (S, Ms.shape[0]), 0)
        dr = ImageDraw.Draw(img)
        for t in F:
            dr.polygon([(px[i], py[i]) for i in t], fill=255)
        sil = np.asarray(img) > 127
        return (sil & Ms).sum() / max((sil | Ms).sum(), 1), (cam, f, cx, cy)

    best = (-1, None, None)
    for yaw in np.radians(np.arange(-100, 101, 10)):
        for elev in np.radians(np.arange(0, 31, 5)):
            for fov in np.radians([12, 25, 40, 55]):
                s, info = iou((yaw, elev, fov))
                if s > best[0]:
                    best = (s, (yaw, elev, fov), info)
    y0, e0, f0 = best[1]
    for yaw in y0 + np.radians(np.arange(-6, 7, 2)):
        for elev in e0 + np.radians(np.arange(-4, 5, 2)):
            elev = min(max(0.0, elev), np.radians(30))
            for fov in [max(0.1, f0 + np.radians(k)) for k in (-8, -4, 0, 4, 8)]:
                s, info = iou((yaw, elev, fov))
                if s > best[0]:
                    best = (s, (yaw, elev, fov), info)
    s, (yaw, elev, fov), (cam, f, cx, cy) = best
    pos, right, up, fwd = cam
    return {"iou": s, "yaw": math.degrees(yaw), "elev": math.degrees(elev), "fov": math.degrees(fov),
            "pos": pos.tolist(), "right": right.tolist(), "up": up.tolist(), "fwd": fwd.tolist(),
            "fx": f[0], "fy": f[1], "cx": cx, "cy": cy, "W": W, "H": H}


if __name__ == "__main__":
    glb, mask, out = sys.argv[1:4]
    r = fit(glb, mask)
    json.dump(r, open(out, "w"), indent=1)
    print(f"FIT iou={r['iou']:.3f} yaw={r['yaw']:.0f} elev={r['elev']:.0f} fov={r['fov']:.0f} stretch={r['fy'] / r['fx']:.2f}")

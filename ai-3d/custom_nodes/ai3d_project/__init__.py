"""Project the reference picture onto a Pixal3D mesh as its base colour.

Pixal3D is pixel-aligned: it builds the object in the frame of a fixed camera (straight in front at eye level, the
FoV from MoGe), so every point of its surface lands exactly on its pixel in the cropped reference picture. The voxel
texture bake (BakeTextureFromVoxel) only gets the colours through a 1024 voxel grid and comes out soft; the picture
itself is a crisp 1024 painting. This node rasterizes the mesh's UV atlas, puts each texel on the dense surface,
projects it into the picture through the Pixal3D camera and takes the picture's colour where the texel is visible
and faces the camera, the baked colour elsewhere. The baked colours are first matched to the picture so the hidden
side does not change hue at the boundary.
"""
import math

import numpy as np
import torch
import torch.nn.functional as F

import comfy.model_management
from comfy_api.latest import ComfyExtension, IO
from comfy_extras.nodes_mesh_postprocess import (_back_project_positions, _interp_vertex_attr, _normalize_uvs_to_unit,
                                                 _rasterize_uv_barycentric, _seam_fill)
from comfy_extras.nodes_save_3d import get_mesh_batch_item


def _project(points, fov, res):
    """Pixal3D single-view camera in the mesh frame (Y up, camera on +Z looking at -Z, the object's grid spanning
    -0.5..0.5 exactly fills the picture at depth 0): -> pixel x, pixel y (row, top = 0), depth."""
    t = math.tan(fov / 2.0)
    depth = 0.5 / t - points[..., 2]
    f = res / 2.0 / t
    return f * points[..., 0] / depth + res / 2.0, -f * points[..., 1] / depth + res / 2.0, depth


def _depth_buffer(verts, faces, fov, res):
    """Nearest depth per pixel of the mesh seen from the Pixal3D camera (inf where empty), tiled like the UV
    rasterizer but keeping the closest triangle."""
    px, py, d = _project(verts, fov, res)
    tri_x, tri_y, tri_d = px[faces], py[faces], d[faces]            # [F,3]
    x0, x1, x2 = tri_x.unbind(1)
    y0, y1, y2 = tri_y.unbind(1)
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    ok = denom.abs() > 1e-12
    xmin = tri_x.min(1).values.floor().clamp(0, res - 1).long(); xmax = tri_x.max(1).values.ceil().clamp(0, res - 1).long()
    ymin = tri_y.min(1).values.floor().clamp(0, res - 1).long(); ymax = tri_y.max(1).values.ceil().clamp(0, res - 1).long()
    zbuf = torch.full((res, res), float("inf"), device=verts.device)
    TILE = 32
    for ty in range(0, res, TILE):
        for tx in range(0, res, TILE):
            idx = torch.nonzero(ok & (xmin < tx + TILE) & (xmax >= tx) & (ymin < ty + TILE) & (ymax >= ty), as_tuple=True)[0]
            if idx.numel() == 0:
                continue
            ys = torch.arange(ty, min(ty + TILE, res), device=verts.device, dtype=torch.float32) + 0.5
            xs = torch.arange(tx, min(tx + TILE, res), device=verts.device, dtype=torch.float32) + 0.5
            yy, xx = torch.meshgrid(ys, xs, indexing="ij")
            for c in range(0, idx.numel(), 2048):   # bound memory on dense tiles
                i = idx[c:c + 2048]
                sx0, sy0, sx1, sy1, sx2, sy2 = (a[i][:, None, None] for a in (x0, y0, x1, y1, x2, y2))
                den = denom[i][:, None, None]
                b0 = ((sy1 - sy2) * (xx - sx2) + (sx2 - sx1) * (yy - sy2)) / den
                b1 = ((sy2 - sy0) * (xx - sx2) + (sx0 - sx2) * (yy - sy2)) / den
                b2 = 1.0 - b0 - b1
                inside = (b0 >= -1e-6) & (b1 >= -1e-6) & (b2 >= -1e-6)
                dd = tri_d[i]
                z = b0 * dd[:, 0, None, None] + b1 * dd[:, 1, None, None] + b2 * dd[:, 2, None, None]
                z = torch.where(inside, z, torch.full_like(z, float("inf"))).min(0).values
                tile = zbuf[ty:ty + TILE, tx:tx + TILE]
                torch.minimum(tile, z, out=tile)
    return zbuf


def _sample(img, px, py):
    """Bilinear sample of img [H,W,C] at pixel coords -> [N,C]."""
    H, W = img.shape[:2]
    grid = torch.stack([px / W * 2 - 1, py / H * 2 - 1], -1).view(1, -1, 1, 2)
    return F.grid_sample(img.permute(2, 0, 1)[None], grid, mode="bilinear", padding_mode="border",
                         align_corners=False)[0, :, :, 0].T


def _match_colors(baked, picture, sure, pairs=4096, sigma=0.1, chunk=16384):
    """Shift every baked colour by the bake->picture difference seen on texels that had a similar baked colour.
    One global transfer tints a multi-colour prop (the red body of a cooler turned its white lid pink); keying the
    shift on the baked colour keeps whites white while a near-black bake still becomes the picture's charcoal.
    The shift is a Gaussian-kernel average over all sampled pairs, so it varies smoothly with the colour: taking
    the k nearest pairs instead made neighbouring texels jump between corrections and mottled the hidden side."""
    idx = torch.nonzero(sure, as_tuple=True)[0]
    if idx.numel() < 100:
        return baked
    idx = idx[torch.randperm(idx.numel(), device=idx.device)[:pairs]]
    keys, shift = baked[idx], picture[idx] - baked[idx]
    mean_shift = shift.mean(0)
    out = torch.empty_like(baked)
    for c in range(0, baked.shape[0], chunk):
        q = baked[c:c + chunk]
        wt = torch.exp(-torch.cdist(q, keys) ** 2 / (2 * sigma ** 2))
        total = wt.sum(1, keepdim=True)
        local = (wt @ shift) / total.clamp_min(1e-12)
        # colours the camera never saw fall back to the average shift
        trust = (total / 5.0).clamp(0, 1)
        out[c:c + chunk] = (q + trust * local + (1 - trust) * mean_shift).clamp(0, 1)
    return out


def project_texture(verts, faces, uvs, ref, image, mask, baked, fov, size, facing_lo, facing_hi, depth_tol, match):
    dev = comfy.model_management.get_torch_device()
    v_np = verts.cpu().numpy().astype(np.float32)
    f_np = faces.cpu().numpy().astype(np.int64)
    uv_np = _normalize_uvs_to_unit(uvs.cpu().numpy().astype(np.float32), True, log_prefix="[ProjectReferenceTexture] ")
    face_idx, bary, cov = _rasterize_uv_barycentric(f_np.astype(np.uint32), uv_np, size)
    v = torch.from_numpy(v_np).to(dev)
    f = torch.from_numpy(f_np).to(dev)
    pos = _interp_vertex_attr(v, f, face_idx, bary, cov)
    if ref is not None:   # the texel's point on the dense surface, so detail lands where the picture has it
        pos = torch.from_numpy(_back_project_positions(pos.cpu().numpy(), cov.cpu().numpy(), ref[0], ref[1])).to(dev)

    fn = torch.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]], dim=1)
    fn = fn / fn.norm(dim=1, keepdim=True).clamp_min(1e-12)
    cam = torch.tensor([0.0, 0.0, 0.5 / math.tan(fov / 2.0)], device=dev)
    p = pos[cov]
    n = fn[face_idx[cov]]
    view = cam - p
    facing = (n * view).sum(1) / view.norm(dim=1).clamp_min(1e-12)

    res = image.shape[0]
    zbuf = _depth_buffer(v, f, fov, res)
    px, py, d = _project(p, fov, res)
    xi = px.long().clamp(0, res - 1); yi = py.long().clamp(0, res - 1)
    # the dense surface sits a little off the low-poly one, and texels at a crease see the neighbouring face,
    # so compare against the nearest depth in a 3x3 neighbourhood with a tolerance
    zmax = -F.max_pool2d(-zbuf.where(torch.isfinite(zbuf), torch.full_like(zbuf, 1e3))[None, None], 3, 1, 1)[0, 0]
    visible = d <= zmax[yi, xi] + depth_tol

    img = image.to(dev, torch.float32)
    m = mask.to(dev, torch.float32)
    m = -F.max_pool2d(-m[None, None], 5, 1, 2)[0, 0]   # erode 2 px: the black crop background bleeds at the outline
    w = ((facing - facing_lo) / (facing_hi - facing_lo)).clamp(0, 1)
    w = w * visible.float() * _sample(m[..., None], px, py)[:, 0].clamp(0, 1)
    proj = _sample(img, px, py)

    base = baked.to(dev, torch.float32).clone()
    old = base[cov]
    if match:
        old = _match_colors(old, proj, w > 0.9)
    base[cov] = old * (1 - w[:, None]) + proj * w[:, None]
    out = _seam_fill(base.cpu().numpy(), cov.cpu().numpy())
    cover = torch.zeros(cov.shape, device=dev)
    cover[cov] = w
    return torch.from_numpy(np.ascontiguousarray(out)).float(), cover.cpu()


class ProjectReferenceTexture(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ProjectReferenceTexture",
            display_name="Project Reference Picture onto Mesh (ai3d)",
            category="3d/texturing",
            description="Base colour from the reference picture itself where the surface faces the Pixal3D camera, "
                        "the voxel bake elsewhere. Pixal3D meshes only (TRELLIS.2 is not pixel-aligned).",
            inputs=[
                IO.Mesh.Input("mesh", tooltip="UV-unwrapped low-poly mesh, still in the Pixal3D frame (before any levelling)."),
                IO.Image.Input("image", tooltip="The cropped picture fed to Pixal3D Conditioning."),
                IO.Image.Input("mask", tooltip="The subject mask cropped exactly like the picture (white = object)."),
                IO.Image.Input("base_color", tooltip="The voxel bake (BakeTextureFromVoxel) on the same UVs."),
                IO.Float.Input("fov", default=49.13, min=1.0, max=170.0, step=0.01, tooltip="Same FoV as Pixal3D Conditioning."),
                IO.Mesh.Input("reference_mesh", optional=True, tooltip="Dense pre-decimation mesh (as for the voxel bake)."),
                IO.Float.Input("facing_lo", default=0.2, min=0.0, max=1.0, step=0.01,
                               tooltip="cos(angle to the camera) below which the bake is used: the picture smears on grazing faces."),
                IO.Float.Input("facing_hi", default=0.5, min=0.0, max=1.0, step=0.01,
                               tooltip="cos(angle to the camera) above which the picture is used fully."),
                IO.Float.Input("depth_tolerance", default=0.02, min=0.0, max=0.2, step=0.001,
                               tooltip="How far behind the visible surface (in object units, the object is ~1) still counts as visible."),
                IO.Boolean.Input("match_colors", default=True, tooltip="Shift the bake's colours towards the picture's first."),
            ],
            outputs=[IO.Image.Output(display_name="base_color"), IO.Mask.Output(display_name="coverage")],
        )

    @classmethod
    def execute(cls, mesh, image, mask, base_color, fov, reference_mesh=None, facing_lo=0.2, facing_hi=0.5,
                depth_tolerance=0.02, match_colors=True) -> IO.NodeOutput:
        if mesh.uvs is None:
            raise ValueError("ProjectReferenceTexture: the mesh has no UVs; unwrap it first.")
        outs, covers = [], []
        for i in range(int(mesh.vertices.shape[0])):
            v, f, *_ = get_mesh_batch_item(mesh, i)
            ref = None
            if reference_mesh is not None:
                rv, rf, *_ = get_mesh_batch_item(reference_mesh, min(i, reference_mesh.vertices.shape[0] - 1))
                ref = (rv, rf)
            size = base_color.shape[1]
            t, c = project_texture(v, f, mesh.uvs[i, :v.shape[0]], ref, image[min(i, image.shape[0] - 1), ..., :3],
                                   mask[min(i, mask.shape[0] - 1), ..., 0], base_color[min(i, base_color.shape[0] - 1)],
                                   math.radians(fov), size, facing_lo, facing_hi, depth_tolerance, match_colors)
            outs.append(t); covers.append(c)
        return IO.NodeOutput(torch.stack(outs), torch.stack(covers))


class Extension(ComfyExtension):
    async def get_node_list(self):
        return [ProjectReferenceTexture]


async def comfy_entrypoint():
    return Extension()

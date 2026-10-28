"""Make-It-Animatable inference: mesh -> Mixamo skeleton joints, skinning weights and a to-rest pose.

    python rig_infer.py <in.glb|obj|fbx> <out.npz> [--no-fingers] [--normals]

Runs the four MIA networks (coarse joints, joints+tails, blend weights, pose) on 32k surface samples and
writes an .npz that scripts/blender_rig.py turns into a rigged GLB/FBX inside Blender:
    verts (N,3)             the mesh vertices in MIA's normalised, hips-aligned, Y-up space
    joints / joints_tail    (52,3) bone heads / tails in that space (at the INPUT pose)
    bw (N,52)               blend weights per vertex
    pose (52,4,4)           per-bone rigid transforms that move the input pose to the Mixamo T-pose
    bone_names (52,)        "mixamorig:Hips", ...
    parents (52,)           parent index, -1 for Hips
    global_transform (4,4)  column-vector matrix mapping original mesh coordinates -> normalised space

Model code: https://github.com/jasongzy/Make-It-Animatable (MIT). Weights: output/best/new/*.pth (2.2 GB).
"""
import argparse
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MIA = os.path.join(ROOT, "mia")
sys.path.insert(0, HERE)
import mia_compat  # noqa: E402

mia_compat.setup(MIA)
from pytorch3d.transforms import Transform3d  # noqa: E402  (vendored)
from model import PCAE  # noqa: E402
from util.dataset_mixamo import BONES_IDX_DICT, JOINTS_NUM, KINEMATIC_TREE, MIXAMO_PREFIX, get_hips_transform  # noqa: E402
from util.utils import fix_random, get_normalize_transform, pose_rot_to_global, sample_mesh  # noqa: E402

N = 32768
HANDS_RESAMPLE_RATIO = 0.5
WEIGHTS = os.path.join(MIA, "output", "best", "new")
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")

_models = None


def load_models(device):
    global _models
    if _models is not None:
        return _models
    t0 = time.time()
    common = dict(N=N, input_normal=False, deterministic=True)
    hier = dict(hierarchical_ratio=HANDS_RESAMPLE_RATIO)
    bw = PCAE(**common, **hier, output_dim=JOINTS_NUM).load(os.path.join(WEIGHTS, "bw.pth")).to(device).eval()
    bw_normal = PCAE(N=N, input_normal=True, input_attention=True, deterministic=True, **hier, output_dim=JOINTS_NUM)
    bw_normal.load(os.path.join(WEIGHTS, "bw_normal.pth")).to(device).eval()
    joints = PCAE(**common, **hier, output_dim=JOINTS_NUM, kinematic_tree=KINEMATIC_TREE, predict_bw=False,
                  predict_joints=True, predict_joints_tail=True, joints_attn_causal=True)
    joints.load(os.path.join(WEIGHTS, "joints.pth")).to(device).eval()
    coarse = PCAE(**common, output_dim=JOINTS_NUM, predict_bw=False, predict_joints=True, predict_joints_tail=True)
    coarse.load(os.path.join(WEIGHTS, "joints_coarse.pth")).to(device).eval()
    pose = PCAE(**common, **hier, output_dim=JOINTS_NUM, kinematic_tree=KINEMATIC_TREE, predict_bw=False,
                predict_pose_trans=True, pose_mode="ortho6d", pose_input_joints=True, pose_attn_causal=True)
    pose.load(os.path.join(WEIGHTS, "pose.pth")).to(device).eval()
    _models = dict(bw=bw, bw_normal=bw_normal, joints=joints, coarse=coarse, pose=pose)
    print(f"MIA models loaded in {time.time() - t0:.1f}s", file=sys.stderr)
    return _models


def unload_models():
    global _models
    _models = None
    import gc
    gc.collect()
    torch.cuda.empty_cache()


# --- blend-weight clean-up, condensed from MIA app.py (no rabbit-ear / tail bones) -----------------------

def _conflict_mask(dominant_idx, fn1, fn2):
    idx1 = torch.tensor([v for k, v in BONES_IDX_DICT.items() if fn1(k)]).to(dominant_idx)
    idx2 = torch.tensor([v for k, v in BONES_IDX_DICT.items() if fn2(k)]).to(dominant_idx)
    mask2 = torch.isin(torch.arange(JOINTS_NUM, device=dominant_idx.device), idx2)
    m = torch.isin(dominant_idx, idx1).unsqueeze(-1).expand([*dominant_idx.shape, JOINTS_NUM]).clone()
    m[:, :, ~mask2] = False
    return m


def bw_post_process(bw, above_head_mask=None, no_fingers=False):
    hands = {"Left", "Right"}
    if above_head_mask is not None:   # everything above the head bone's tail belongs to the head
        m = above_head_mask.unsqueeze(-1).tile(bw.shape[-1])
        head_only = torch.zeros_like(m)
        head_only[..., BONES_IDX_DICT[f"{MIXAMO_PREFIX}Head"]] = True
        bw[m & head_only] = 1e5
    if no_fingers:
        dominant = torch.argmax(bw, dim=-1)
        for hand in hands:
            bw[_conflict_mask(dominant, lambda k: hand in k and any(x in k for x in FINGERS),
                              lambda k: k.endswith(f"{hand}Hand"))] = 1e5
        bw[_conflict_mask(dominant, lambda k: True, lambda k: any(x in k for x in FINGERS))] = 0
    dominant = torch.argmax(bw, dim=-1)
    if not no_fingers:
        for hand in hands:
            other = next(iter(hands - {hand}))
            bw[_conflict_mask(dominant, lambda k: hand in k, lambda k: other in k)] = 0
            for finger in FINGERS:
                others = set(FINGERS) - {finger}
                bw[_conflict_mask(dominant, lambda k: hand in k and finger in k,
                                  lambda k: hand in k and any(x in k for x in others))] = 0
    arm = lambda s: (f"{s}Arm", f"{s}ForeArm", f"{s}Hand") + tuple(f"{s}Hand{f}{i}" for f in FINGERS for i in (1, 2, 3))
    conflict_sets = (
        {("Neck", "Head"), ("LeftLeg", "LeftFoot", "LeftToeBase"), ("RightLeg", "RightFoot", "RightToeBase"),
         arm("Left"), arm("Right")},
        {("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftShoulder", "RightShoulder", "RightUpLeg", "RightLeg",
          "RightFoot", "RightToeBase", "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase"),
         arm("Right")[1:] + arm("Left")[1:]},
        {("Neck", "Head"), ("RightFoot", "RightToeBase", "LeftFoot", "LeftToeBase")},
    )
    for parts in conflict_sets:
        for part in parts:
            others = parts - {part}
            bw[_conflict_mask(dominant, lambda k: any(x in k for x in part),
                              lambda k: any(any(x in k for x in p) for p in others))] = 0
    bw = bw / (bw.sum(dim=-1, keepdim=True) + 1e-10)
    thresholds = torch.topk(bw, k=4, dim=-1, sorted=True).values[..., -1:]
    bw[bw < thresholds] = 0
    return bw


# --- the pipeline ---------------------------------------------------------------------------------------

@torch.no_grad()
def rig(mesh_path, out_path, no_fingers=False, use_normals=False, device=None, log=print):
    device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    fix_random()
    models = load_models(device)
    t0 = time.time()

    mesh = trimesh.load(mesh_path, force="mesh")
    if isinstance(mesh, trimesh.PointCloud) or mesh.faces.shape[0] == 0:
        raise ValueError("input must be a triangle mesh")
    verts = torch.from_numpy(np.asarray(mesh.vertices, dtype=np.float32)).unsqueeze(0)
    verts_normal = torch.from_numpy(np.asarray(mesh.vertex_normals, dtype=np.float32)).unsqueeze(0)
    faces = np.asarray(mesh.faces)
    pts = torch.from_numpy(sample_mesh(mesh, N, get_normals=True).astype(np.float32)).unsqueeze(0)
    pts, pts_normal = torch.chunk(pts, 2, dim=-1)
    log(f"mesh: {verts.shape[1]} vertices, {faces.shape[0]} faces")

    # 1. coarse joints on the normalised shape, then rotate so the hips face +Z (MIA's canonical frame)
    norm = get_normalize_transform(pts, keep_ratio=True, recenter=True)
    pts, verts = norm.transform_points(pts), norm.transform_points(verts)
    joints = models["coarse"](pts.to(device)).joints.cpu()
    joints, joints_tail = joints[..., :3], joints[..., 3:]
    j = lambda n: joints[:, BONES_IDX_DICT[f"{MIXAMO_PREFIX}{n}"]]
    rotate = Transform3d(matrix=get_hips_transform(j("Hips"), j("RightUpLeg"), j("LeftUpLeg")).transpose(-1, -2))
    global_transform = norm.compose(rotate)
    pts, verts = rotate.transform_points(pts), rotate.transform_points(verts)
    pts_normal = F.normalize(rotate.transform_normals(pts_normal), dim=-1)
    verts_normal = F.normalize(rotate.transform_normals(verts_normal), dim=-1)
    mesh.vertices = verts.squeeze(0).numpy()
    log(f"coarse joints + alignment done ({time.time() - t0:.1f}s)")

    # 2. resample with extra points around the hands (the fine models expect this layout)
    tails = rotate.transform_points(joints_tail).squeeze(0).numpy()
    hands = [tails[BONES_IDX_DICT[f"{MIXAMO_PREFIX}LeftHand"]], tails[BONES_IDX_DICT[f"{MIXAMO_PREFIX}RightHand"]]]
    pts = sample_mesh(mesh, N, get_normals=True, attn_ratio=HANDS_RESAMPLE_RATIO, attn_centers=hands).astype(np.float32)
    pts = torch.from_numpy(pts).unsqueeze(0)
    pts, pts_normal = torch.chunk(pts, 2, dim=-1)

    # 3. second normalisation (no recentering) and the fine models
    norm2 = get_normalize_transform(pts, keep_ratio=True, recenter=False)
    pts, verts = norm2.transform_points(pts), norm2.transform_points(verts)
    global_transform = global_transform.compose(norm2)
    pts_d, pts_n_d = pts.to(device), pts_normal.to(device)
    bw = []
    for v, vn in zip(torch.split(verts, 100000, dim=-2), torch.split(verts_normal, 100000, dim=-2)):
        v, vn = v.to(device), vn.to(device)
        b = models["bw"](pts_d, v).bw
        if use_normals:
            bn = models["bw_normal"](torch.cat([pts_d, pts_n_d], -1), torch.cat([v, vn], -1)).bw
            m = _conflict_mask(torch.argmax(b, -1), lambda k: True, lambda k: any(x in k for x in ("Spine", "Shoulder", "Arm")))
            bn[m] = b[m]
            b = bn
        bw.append(b)
    bw = torch.cat(bw, dim=-2).cpu()
    joints = models["joints"](pts_d).joints
    pose = models["pose"](pts_d, joints=joints.clone()).pose_trans
    joints, pose = joints.cpu(), pose.cpu()
    log(f"weights, joints and pose predicted ({time.time() - t0:.1f}s)")

    # 4. clean the weights, convert the pose to global 4x4 transforms (input pose -> T-pose)
    head_tail_y = joints[:, BONES_IDX_DICT[f"{MIXAMO_PREFIX}Head"], 4]
    bw = bw_post_process(bw, above_head_mask=verts[..., 1] >= head_tail_y, no_fingers=no_fingers)
    parents = torch.tensor(KINEMATIC_TREE.parent_indices)
    pose, _ = pose_rot_to_global(pose, joints[..., :3], parents)
    pose[..., 0, :, :] = torch.eye(4)

    m = global_transform.get_matrix()[0].numpy().T   # pytorch3d row-vector convention -> column vectors
    names = np.array(list(BONES_IDX_DICT.keys()))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    np.savez(out_path, verts=verts.squeeze(0).numpy(), faces=faces, joints=joints[0, :, :3].numpy(),
             joints_tail=joints[0, :, 3:].numpy(), bw=bw.squeeze(0).numpy(), pose=pose.squeeze(0).numpy(),
             bone_names=names, parents=parents.numpy(), global_transform=m, source=os.path.abspath(mesh_path))
    log(f"rig written to {out_path} ({time.time() - t0:.1f}s total)")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mesh")
    ap.add_argument("out")
    ap.add_argument("--no-fingers", action="store_true", help="fold the finger weights into the hands")
    ap.add_argument("--normals", action="store_true", help="also use the normal-aware weight model (slower)")
    a = ap.parse_args()
    rig(a.mesh, a.out, a.no_fingers, a.normals)


if __name__ == "__main__":
    main()

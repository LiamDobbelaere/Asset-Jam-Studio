"""Find the rotation that sets a generated mesh flat on the ground.

Image-to-3D reconstructs in the reference camera's frame, so a prop drawn in three-quarter
view comes out pitched by the camera elevation and turned by its azimuth. Most game props are
box-like, so the upright pose is the one whose axis-aligned bounding box is smallest. We search
yaw freely but keep pitch and roll inside +/- MAX_TILT degrees, which rules out "solutions"
that lie the object on its side, and among near-equal volumes we take the smallest rotation.

Coordinates are Z-up (Blender). Only numpy is needed, so this also runs outside Blender.
"""
import math

import numpy as np

MAX_TILT = 45.0        # degrees of pitch / roll the solver may apply
COARSE_STEP = 2.5
FINE_STEP = 0.25
TIE_TOLERANCE = 0.015  # scores within 1.5 % of the best count as equal
TILT_COST = 0.5        # a tilt of MAX_TILT must shrink the box by this fraction to be worth it


def rotation_matrices(yaw, pitch, roll):
    """(N,) degree arrays -> (N, 3, 3) matrices applying roll (X), then pitch (Y), then yaw (Z)."""
    cy, sy = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    cp, sp = np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
    cr, sr = np.cos(np.radians(roll)), np.sin(np.radians(roll))
    n = len(yaw)
    rz = np.zeros((n, 3, 3)); rz[:, 0, 0] = cy; rz[:, 0, 1] = -sy; rz[:, 1, 0] = sy; rz[:, 1, 1] = cy; rz[:, 2, 2] = 1
    ry = np.zeros((n, 3, 3)); ry[:, 0, 0] = cp; ry[:, 0, 2] = sp; ry[:, 2, 0] = -sp; ry[:, 2, 2] = cp; ry[:, 1, 1] = 1
    rx = np.zeros((n, 3, 3)); rx[:, 1, 1] = cr; rx[:, 1, 2] = -sr; rx[:, 2, 1] = sr; rx[:, 2, 2] = cr; rx[:, 0, 0] = 1
    return rz @ ry @ rx


def bbox_volumes(points, mats):
    """AABB volume of `points` (M, 3) under each rotation in `mats` (N, 3, 3) -> (N,)."""
    rotated = np.einsum("nij,mj->nmi", mats, points)
    extent = rotated.max(axis=1) - rotated.min(axis=1)
    return extent.prod(axis=1)


def evaluate(points, yaw, pitch, roll, chunk=4096):
    vols = np.empty(len(yaw))
    for i in range(0, len(yaw), chunk):
        s = slice(i, i + chunk)
        vols[s] = bbox_volumes(points, rotation_matrices(yaw[s], pitch[s], roll[s]))
    return vols


def total_tilt(pitch, roll):
    """Angle in degrees between the original up axis and the rotated one (yaw does not move it)."""
    cos_t = np.cos(np.radians(pitch)) * np.cos(np.radians(roll))
    return np.degrees(np.arccos(np.clip(cos_t, -1, 1)))


def pick(yaw, pitch, roll, vols):
    """Index of the smallest rotation among the near-best scores that keep up roughly up.

    The score is the box volume with a quadratic tilt penalty: furniture pitched by a camera's
    ~10-20 degrees shrinks its box by a third or more and is corrected, while a figure with a leg
    forward and arms out, whose box gets slightly smaller lying down, stays upright."""
    tilt = total_tilt(pitch, roll)
    allowed = tilt <= MAX_TILT
    score = vols * (1 + TILT_COST * (tilt / MAX_TILT) ** 2)
    best = score[allowed].min()
    ok = allowed & (score <= best * (1 + TIE_TOLERANCE))
    # Yaw has 90-degree symmetry for a box; fold it so "least turn" is well defined.
    yaw_turn = np.abs((yaw + 45) % 90 - 45)
    effort = tilt * 4 + yaw_turn
    effort[~ok] = np.inf
    return int(np.argmin(effort))


def solve(points):
    """-> (yaw, pitch, roll) in degrees, and the volume ratio after / before."""
    pts = np.asarray(points, dtype=np.float64)
    pts = pts - pts.mean(axis=0)
    before = bbox_volumes(pts, np.eye(3)[None])[0]

    tilt = np.arange(-MAX_TILT, MAX_TILT + 1e-6, COARSE_STEP)
    yaws = np.arange(-45, 45, COARSE_STEP)
    y, p, r = np.meshgrid(yaws, tilt, tilt, indexing="ij")
    y, p, r = y.ravel(), p.ravel(), r.ravel()
    i = pick(y, p, r, evaluate(pts, y, p, r))
    cy, cp, cr = y[i], p[i], r[i]

    span = np.arange(-COARSE_STEP, COARSE_STEP + 1e-6, FINE_STEP)
    y, p, r = np.meshgrid(cy + span, cp + span, cr + span, indexing="ij")
    y, p, r = y.ravel(), p.ravel(), r.ravel()
    p = np.clip(p, -MAX_TILT, MAX_TILT); r = np.clip(r, -MAX_TILT, MAX_TILT)
    vols = evaluate(pts, y, p, r)
    i = pick(y, p, r, vols)
    return (float(y[i]), float(p[i]), float(r[i])), float(vols[i] / before)


def hull_points(points):
    """Cheap stand-in for a convex hull: the extreme points along many directions."""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) <= 600:
        return pts
    rng = np.random.default_rng(0)
    dirs = rng.normal(size=(400, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    idx = np.unique(np.concatenate([(pts @ dirs.T).argmax(axis=0), (pts @ dirs.T).argmin(axis=0)]))
    return pts[idx]


def euler_to_matrix(yaw, pitch, roll):
    return rotation_matrices(np.array([yaw]), np.array([pitch]), np.array([roll]))[0]


if __name__ == "__main__":
    # Self-test: a 2 x 1 x 0.5 box, knocked over by a known rotation, must come back level.
    box = np.array([[x, y, z] for x in (-1, 1) for y in (-.5, .5) for z in (-.25, .25)], dtype=float)
    truth = (31.0, -34.0, 12.0)
    knocked = box @ euler_to_matrix(*truth).T
    (yaw, pitch, roll), ratio = solve(knocked)
    fixed = knocked @ euler_to_matrix(yaw, pitch, roll).T
    ext = fixed.max(axis=0) - fixed.min(axis=0)
    print(f"applied {truth} -> solver {yaw:.2f}, {pitch:.2f}, {roll:.2f}  volume x{ratio:.3f}  extent {np.round(ext, 3)}")
    assert ratio < 0.3 and abs(ext[2] - 0.5) < 0.02, "box did not come back level with its flat side down"
    print("ok")

"""Import Make-It-Animatable's model code without Blender's `bpy` and without the gated Mixamo template FBX.

MIA's `util/dataset_mixamo.py` imports bpy at module level and reads `data/Mixamo/bones.fbx` (a gated
Hugging Face dataset) at import time only to learn the Mixamo bone hierarchy. That hierarchy is public
knowledge, so this module registers stub `bpy` / `bmesh` / `mathutils` modules, loads `dataset_mixamo`
with the template-reading line replaced by a hierarchy built from MIXAMO_PARENTS, and adds the vendored
`pytorch3d.transforms` and `torch_cluster.fps` replacements to sys.path.

Call `setup(mia_dir)` once before importing `model` / `util.utils`.
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")

# child -> parent (without the "mixamorig:" prefix); the standard Mixamo skeleton MIA was trained on
MIXAMO_PARENTS = {
    "Hips": None, "Spine": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Neck": "Spine2", "Head": "Neck",
    "LeftShoulder": "Spine2", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm", "LeftHand": "LeftForeArm",
    "RightShoulder": "Spine2", "RightArm": "RightShoulder", "RightForeArm": "RightArm", "RightHand": "RightForeArm",
    "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg", "LeftToeBase": "LeftFoot",
    "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg", "RightToeBase": "RightFoot",
}
for _side in ("Left", "Right"):
    for _finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
        MIXAMO_PARENTS[f"{_side}Hand{_finger}1"] = f"{_side}Hand"
        MIXAMO_PARENTS[f"{_side}Hand{_finger}2"] = f"{_side}Hand{_finger}1"
        MIXAMO_PARENTS[f"{_side}Hand{_finger}3"] = f"{_side}Hand{_finger}2"


def _stub_blender_modules():
    if "bpy" in sys.modules:
        return
    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    for name in ("Action", "Armature", "Context", "Mesh", "Object"):
        setattr(bpy_types, name, type(name, (), {}))
    bpy.types = bpy_types
    bpy.ops = bpy.data = bpy.context = None
    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = bpy_types
    sys.modules["bmesh"] = types.ModuleType("bmesh")
    sys.modules["mathutils"] = types.ModuleType("mathutils")


def _build_tree_source():
    return '''
def _build_kinematic_tree(bones_idx_dict, parents):
    template_joints = tuple(bones_idx_dict.keys())
    children_of = {}
    for name in bones_idx_dict:
        p = parents[name[len(MIXAMO_PREFIX):]]
        children_of.setdefault(None if p is None else MIXAMO_PREFIX + p, []).append(name)
    def make(name, parent):   # Joint is a frozen dataclass: parent is set at construction, children appended
        j = Joint(name, index=bones_idx_dict[name], parent=parent, children=[], template_joints=template_joints)
        for c in children_of.get(name, []):
            j.children.append(make(c, j))
        return j
    return make(MIXAMO_PREFIX + "Hips", None)
KINEMATIC_TREE = _build_kinematic_tree(BONES_IDX_DICT, MIXAMO_PARENTS)
'''


def setup(mia_dir):
    """Make `model`, `models_ae`, `util.utils` and `util.dataset_mixamo` importable from mia_dir."""
    if VENDOR not in sys.path:
        sys.path.insert(0, VENDOR)
    if mia_dir not in sys.path:
        sys.path.insert(0, mia_dir)
    _stub_blender_modules()
    if "util.dataset_mixamo" in sys.modules:
        return
    import util  # noqa: F401  (mia/util/__init__.py)
    path = os.path.join(mia_dir, "util", "dataset_mixamo.py")
    src = open(path, encoding="utf-8").read()
    src = src.replace("from util import blender_utils\n", "import types as _t; blender_utils = _t.SimpleNamespace(Object=object)\n")
    src = src.replace("from util.blender_utils import bpy as bpy\n", "bpy = None\n")
    marker = "KINEMATIC_TREE = get_kinematic_tree(TEMPLATE_PATH, BONES_IDX_DICT)\n"
    assert marker in src, "dataset_mixamo.py changed; update mia_compat.py"
    src = src.replace(marker, "from mia_compat import MIXAMO_PARENTS\n" + _build_tree_source())
    spec = importlib.util.spec_from_file_location("util.dataset_mixamo", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["util.dataset_mixamo"] = mod
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod

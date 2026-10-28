"""Converts an API-format ComfyUI graph into the frontend's workflow format (positions, typed slots, links) so the
same graph can be saved as a sidebar workflow. Used by build_texture_workflow.py and build_sprite_workflow.py.
Needs the ComfyUI server (reads /object_info for slot types)."""
import json
import os
import urllib.request

SERVER = os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188")


def build_ui(api, keep, columns, groups, wf_id, bypass_ids=(), bypass_types=()):
    info = json.load(urllib.request.urlopen(SERVER + "/object_info"))
    rows = {}
    ui_nodes, links = [], []
    link_id = 0
    for nid in keep:
        n = api[nid]
        ct = n["class_type"]
        spec = info[ct]["input"]
        in_names = list(spec.get("required", {})) + list(spec.get("optional", {}))
        inputs, widgets = [], []
        for name in in_names:
            val = n["inputs"].get(name)
            sdef = (spec.get("required", {}).get(name) or spec["optional"][name])
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                src, slot = val
                link_id += 1
                typ = info[api[src]["class_type"]]["output"][slot]
                links.append([link_id, int(src), slot, int(nid), len(inputs), typ])
                inputs.append({"name": name, "type": typ, "link": link_id})
            else:
                typ = sdef[0] if isinstance(sdef[0], str) else "COMBO"
                if name not in n["inputs"] and typ not in ("COMBO", "INT", "FLOAT", "STRING", "BOOLEAN"):
                    inputs.append({"name": name, "type": typ, "link": None})   # unconnected optional socket
                    continue
                inputs.append({"name": name, "type": typ, "widget": {"name": name}, "link": None})
                widgets.append(val)
                if name in ("seed", "noise_seed"):
                    widgets.append("fixed")
        outs = [{"name": on, "type": ot, "links": []} for on, ot in zip(info[ct]["output_name"], info[ct]["output"])]
        col = columns[nid]
        row = rows.get(col, 0)
        rows[col] = row + 1
        mode = 4 if (nid in bypass_ids or ct in bypass_types) else 0
        ui_nodes.append({"id": int(nid), "type": ct, "pos": [col * 420, row * 260],
                         "size": [380, 220 if ct in ("CLIPTextEncode", "KSampler", "LoadImage") else 120],
                         "flags": {}, "order": keep.index(nid), "mode": mode,
                         "inputs": inputs, "outputs": outs,
                         "properties": {"Node name for S&R": ct}, "widgets_values": widgets, "title": n["_meta"]["title"]})
    by_id = {n["id"]: n for n in ui_nodes}
    for lid, src, slot, dst, dslot, typ in links:
        by_id[src]["outputs"][slot]["links"].append(lid)
    return {"id": wf_id, "revision": 0, "last_node_id": max(int(k) for k in api), "last_link_id": link_id,
            "nodes": ui_nodes, "links": links, "groups": groups, "config": {}, "extra": {}, "version": 0.4}

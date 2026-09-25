# -*- coding: utf-8 -*-
"""生成「图生提示词」示例工作流（ComfyUI UI 格式 .json）

节点链路：
    LoadImage → HammPromptImageToPrompt → HammPromptPreview

直接读取 HammPromptImageToPrompt / HammPromptPreview 的真实 INPUT_TYPES，
保证 widgets_values 顺序、连线 slot 与代码完全一致。

用法：
    python examples/gen_image2prompt.py
生成：examples/image_to_prompt_workflow.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import py.nodes as nodes          # noqa: E402
import py.hamm_core as C           # noqa: E402

WIDGET_TYPES = {"STRING", "INT", "FLOAT", "BOOLEAN"}


def is_widget(typ):
    if isinstance(typ, str):
        return typ in WIDGET_TYPES
    if isinstance(typ, list):          # combo
        return True
    return False


def default_value(typ, cfg):
    if isinstance(typ, list):          # combo
        if isinstance(cfg, dict) and "default" in cfg:
            return cfg["default"]
        return typ[0]
    cfg = cfg or {}
    if typ == "STRING":
        return cfg.get("default", "")
    if typ == "INT":
        return cfg.get("default", 0)
    if typ == "FLOAT":
        return cfg.get("default", 0.0)
    if typ == "BOOLEAN":
        return cfg.get("default", False)
    return ""


def build_node(class_type, it, title=None, inputs_defaults=None, position=(0, 0)):
    inputs_defaults = inputs_defaults or {}
    req = it.get("required", {})
    opt = it.get("optional", {})
    fields = []
    for name, spec in req.items():
        fields.append((name, spec, True))
    for name, spec in opt.items():
        fields.append((name, spec, False))

    widgets = []
    node_inputs = []
    for slot_idx, (name, spec, _is_req) in enumerate(fields):
        typ = spec[0]
        cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        node_inputs.append({
            "name": name,
            "type": typ if isinstance(typ, str) else "COMBO",
            "link": None,
            "slot_index": slot_idx,
        })
        if is_widget(typ):
            val = inputs_defaults.get(name, default_value(typ, cfg))
            widgets.append(val)
    return {
        "type": class_type,
        "inputs": node_inputs,
        "widgets": widgets,
        "_slot_of": {name: i for i, (name, _, _) in enumerate(fields)},
        "title": title,
        "pos": position,
    }


# ---------------------------------------------------------------- 构造节点
# 1) LoadImage（ComfyUI 自带）
load_img = {
    "type": "LoadImage",
    "inputs": [{"name": "image", "type": "STRING", "link": None, "slot_index": 0}],
    "widgets": [""],
    "_slot_of": {"image": 0},
    "title": "LoadImage（上传参考图）",
    "pos": [40, 220],
    "outputs": [
        {"name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": []},
        {"name": "MASK", "type": "MASK", "slot_index": 1, "links": []},
    ],
}

# 2) HammPromptImageToPrompt
it = nodes.HammPromptImageToPrompt.INPUT_TYPES()
override = {
    "llm_mode": C.LLMConfig.LOCAL,
    "local_vision_model": "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
    "local_vision_mmproj": "",
    "keep_loaded": False,
}
img2prompt = build_node("HammPromptImageToPrompt", it,
                        title="Hamm 图生提示词（本地 VLM）",
                        inputs_defaults=override,
                        position=[420, 160])
img2prompt["outputs"] = [
    {"name": "prompt", "type": "STRING", "slot_index": 0, "links": []},
]

# 3) HammPromptPreview
prev = build_node("HammPromptPreview", nodes.HammPromptPreview.INPUT_TYPES(),
                  title="Hamm 提示词预览",
                  position=[860, 220])
prev["outputs"] = [
    {"name": "text", "type": "STRING", "slot_index": 0, "links": []},
]

# ---------------------------------------------------------------- 连线
# LoadImage.IMAGE(0) -> HammPromptImageToPrompt.image(0)
img_slot = img2prompt["_slot_of"]["image"]
link1 = [1, 1, 0, 2, img_slot, "IMAGE"]
# HammPromptImageToPrompt.prompt(0) -> Preview.positive(0)
pos_slot = prev["_slot_of"]["positive"]
link2 = [2, 2, 0, 3, pos_slot, "STRING"]

links = [link1, link2]

load_img["outputs"][0]["links"] = [1]
img2prompt["inputs"][img_slot]["link"] = 1
img2prompt["outputs"][0]["links"] = [2]
prev["inputs"][pos_slot]["link"] = 2


# ---------------------------------------------------------------- 组装 UI 格式
def to_ui_node(raw, nid):
    return {
        "id": nid,
        "type": raw["type"],
        "pos": list(raw["pos"]),
        "size": [320, 220],
        "flags": {},
        "order": nid,
        "mode": 0,
        "inputs": raw["inputs"],
        "outputs": raw.get("outputs", []),
        "title": raw.get("title"),
        "properties": {"Node name for S&R": raw["type"]},
        "widgets_values": raw["widgets"],
    }


ui_nodes = [
    to_ui_node(load_img, 1),
    to_ui_node(img2prompt, 2),
    to_ui_node(prev, 3),
]

workflow = {
    "last_node_id": 3,
    "last_link_id": 2,
    "nodes": ui_nodes,
    "links": links,
    "groups": [],
    "config": {},
    "extra": {},
    "version": 0.4,
}

out_path = os.path.join(HERE, "image_to_prompt_workflow.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)

print("WROTE", out_path)
print("nodes:", [n["type"] for n in ui_nodes])
print("links:", links)
print("HammPromptImageToPrompt widgets_values count:", len(img2prompt["widgets"]))

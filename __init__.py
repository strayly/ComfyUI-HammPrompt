# -*- coding: utf-8 -*-
"""ComfyUI-HammPrompt —— 中文写提示词 / 英文给模型 的 ComfyUI 节点套装

文生图：HammPromptImage、HammPromptStoryboard
视频：HammPromptVideo（对齐 MiniMax H3 官方 prompt 规范）
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _register_llm_dir():
    """把 ComfyUI/models/LLM 注册进 folder_paths（官方 v0.33 尚未注册该目录）"""
    root = _HERE               # .../custom_nodes/ComfyUI-HammPrompt
    for _ in range(2):          # -> custom_nodes -> ComfyUI
        root = os.path.dirname(root)
    p = os.path.join(root, "models", "LLM")
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    try:
        import folder_paths
        folder_paths.add_model_folder_path("LLM", p)
    except Exception:
        pass
    return p


_register_llm_dir()

def _numeric_defaults(mappings):
    """数值控件的出厂默认值（时长这类），给前端「重置」按钮用。

    下拉的默认值前端能自己从 options.values 推（「不指定」→ 带「默认」的项 → 首项），
    数值控件推不出来 —— 新前端数字 widget 的 options 里只有 min/max/step，没有 default，
    照 min 重置等于把时长打回下限（实测 5s 会被打回 2s）。所以由后端给一份，
    前端只读，规则仍然只有一处（节点自己的 INPUT_TYPES）。
    """
    out = {}
    for name, cls in (mappings or {}).items():
        if not (isinstance(name, str) and name.startswith("HammPrompt")):
            continue
        try:
            it = cls.INPUT_TYPES()
        except Exception:
            continue
        vals = {}
        for sect in ("required", "optional"):
            for k, spec in (it.get(sect) or {}).items():
                if not (isinstance(spec, (list, tuple)) and spec):
                    continue
                if spec[0] not in ("FLOAT", "INT"):
                    continue
                opt = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if isinstance(opt.get("default"), (int, float)):
                    vals[k] = opt["default"]
        if vals:
            out[name] = vals
    return out


def _register_desc_route():
    """给前端结构化预览框提供「中文考据描述」。

    框里显示的词必须和喂给大模型的词一致，所以描述只有一份数据源（模板 JSON），
    后端拿来拼 brief、前端拿去展开显示，都走这里。
    """
    try:
        from server import PromptServer
        from aiohttp import web
        from .py import hamm_core as C

        @PromptServer.instance.routes.get("/hammprompt/desc")
        async def _hammprompt_desc(request):
            out = {}
            for group, cats in (("t2i", ("era", "style", "scene", "action",
                                          "shot", "lighting", "mood", "quality")),
                                ("video", ("mode", "style", "shot_size",
                                           "camera_motion", "transition"))):
                try:
                    out[group] = {c: C.desc_all(c, group) for c in cats}
                except Exception as e:
                    print("[HammPrompt] desc 路由 %s 失败：%s" % (group, e))
            # 年代的简洁写法（唐 -> 唐代）：前端显示用，规则只在 hamm_core 里维护一份
            for group in ("t2i", "video"):
                try:
                    out[group + "_era_display"] = C.era_display_all(group)
                except Exception as e:
                    print("[HammPrompt] era_display 路由 %s 失败：%s" % (group, e))
            # 数值控件的出厂默认值（前端「重置」按钮用）
            try:
                from .py.nodes import NODE_CLASS_MAPPINGS
                out["defaults"] = _numeric_defaults(NODE_CLASS_MAPPINGS)
            except Exception as e:
                print("[HammPrompt] defaults 路由失败：%s" % e)
            return web.json_response(out)
    except Exception as e:
        print("[HammPrompt] desc 路由未注册（不影响使用）：%s" % e)


_register_desc_route()

from .py.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # noqa: E402,F401

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

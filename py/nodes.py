# -*- coding: utf-8 -*-
"""HammPrompt 节点：本地模型加载器 / 文生图提示词 / 视频提示词 / 分镜批量 / 中文提示词预览"""

import json
import os
import re

from . import hamm_core as C
from . import hamm_local_llm as L

DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.7-flash"

HAMM_LLM = "HAMM_LLM"


def _local_widgets():
    """本地模型相关参数（三个提示词节点共用）

    默认值策略：**新建节点直接可用**。
    - 机器上装了本地模型 → `llm_mode` 默认「本地模型（推荐）」、`local_model` 默认挑一个
      （优先 Qwen，中文扩写最合适）；否则退回「关（纯离线词典）」+ 占位项。
    - 注意：这只影响**新建节点**的初始值；已有工作流保存过的值不会被覆盖。
    """
    choices = L.model_choices()
    real = [m for m in choices if not m.startswith("（") and not m.startswith("(")]
    picked = ""
    for m in real:
        if "qwen" in m.lower():
            picked = m
            break
    if not picked and real:
        picked = real[0]

    return {
        "llm_mode": (C.LLMConfig.MODES,
                     {"default": C.LLMConfig.LOCAL if picked else C.LLMConfig.OFF}),
        "local_model": (choices, {"default": picked or "（不启用本地模型）"}),
        "local_gpu_layers": ("INT", {"default": -1, "min": -1, "max": 500,
                                     "tooltip": "llama.cpp GPU 卸载层数，-1 = 全部卸载到显卡"}),
        "local_ctx": ("INT", {"default": 4096, "min": 512, "max": 32768}),
        "local_max_tokens": ("INT", {"default": 512, "min": 64, "max": 4096,
                                     "tooltip": "智能扩写输出上限。扩写会写 60~110 词，建议 ≥512"}),
        "local_temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05}),
        "keep_loaded": ("BOOLEAN", {
            "default": False,
            "tooltip": "关=每次用完立刻卸载，把显存还给生图（推荐）；开=常驻显存，连续多镜更快但占显存"}),
    }


def _api_widgets():
    return {
        "llm_model": ("STRING", {"default": DEFAULT_MODEL, "multiline": False}),
        "llm_base_url": ("STRING", {"default": DEFAULT_URL, "multiline": False}),
        "llm_api_key": ("STRING", {"default": "", "multiline": False}),
    }


def _resolve_llm(local_llm, llm_mode, local_model,
                 local_gpu_layers, local_ctx, local_max_tokens, local_temperature,
                 keep_loaded, llm_base_url, llm_model, llm_api_key,
                 local_vision_model="", local_vision_mmproj=""):
    """接了加载器节点就用加载器，否则按节点自身参数构造。

    local_vision_model 是「读图」用的本地 VLM（独立于文本扩写模型）；
    文本模型走 local_model，视觉模型走 local_vision_model，两套权重分开。
    """
    if local_llm is not None and getattr(local_llm, "enabled", False):
        return local_llm
    if llm_mode == C.LLMConfig.OFF:
        return None
    path, backend = L.resolve_model(local_model)
    vpath, vbackend = "", "gguf"
    if local_vision_model and not local_vision_model.startswith("（") and not local_vision_model.startswith("("):
        vpath, vbackend = L.resolve_model(local_vision_model)
    if (local_vision_mmproj or "").strip() and not os.path.exists(local_vision_mmproj.strip()):
        raise RuntimeError(
            "指定的 local_vision_mmproj 文件不存在：%s\n"
            "请改成留空（自动同目录探测）或填写正确的 mmproj 路径。" % local_vision_mmproj.strip())
    vmmproj = (local_vision_mmproj or "").strip() or (L.auto_mmproj(vpath) if vpath else "")
    return C.LLMConfig(
        mode=llm_mode, backend=backend, path=path,
        gpu_layers=local_gpu_layers, ctx=local_ctx,
        max_tokens=local_max_tokens, temperature=local_temperature,
        keep_loaded=keep_loaded,
        base_url=llm_base_url, model=llm_model, api_key=llm_api_key,
        vision_path=vpath, vision_mmproj=vmmproj, vision_backend=vbackend)


# ---------------------------------------------------------------- 要素清单文本
# 这里拼出来的文本是「按元素名分行的选择清单」，会显示在预览节点的文本框里，
# 也是「一键复制给豆包优化」的正文。
#
# 显示规则（用户明确要求）：
#   1. 标签和值里的括号注释一律不显示 —— 主体（自填）→ 主体、通用高清（推荐）→ 通用高清
#   2. 值为空的行整行不显示（不留「（空）」占位）
#   3. 不再附带「—— 诊断 ——」块（引擎 / 输出语言 / 扩写状态），诊断只打到控制台
_NOTE_WORDS = ("自填", "可选", "空", "推荐")
_NOTE_RE = re.compile(r"（(?:%s)）" % "|".join(_NOTE_WORDS))


def _clean_label(text):
    return _NOTE_RE.sub("", str(text or "")).strip()


def _clean_value(text):
    s = str(text or "").strip()
    s = re.sub(r"（推荐）\s*$", "", s).strip()
    if s == "不指定":
        return ""                     # 「不指定」= 没选，行直接消失（与空值同等）
    return s


def _log_status(where, status_msg):
    """诊断信息不进 UI（用户要求），只打到 ComfyUI 控制台，排查时看这里"""
    if status_msg:
        print("[HammPrompt] %s 扩写状态：%s" % (where, status_msg))


# ---------------------------------------------------------------- 结构化提示词回填
# 节点上的 elements_text 文本框（前端随输入/下拉实时刷新，用户可手改）随队列传回后端。
# 这里把「名称：值」逐行解析回来，覆盖对应参数 —— 这样「运行时按修改后的词扩写」
# 就是字面意义的：词典替换和 LLM 扩写吃的 brief 完全来自这份文本。
# 键必须与前端 UPSTREAM_ROWS 的中文标签完全一致（前端 hammprompt_preview.js）。
_ELEMENTS_FIELDS = {
    "HammPromptImage": {
        # 新键名（2026-09-18 起）：年代排第一行且改叫「时代背景」，额外词改叫「额外说明」
        "时代背景": "era", "额外说明": "extra_positive",
        # 旧键名保留：手改过结构化框的老工作流里存的还是这两个词，认不出就会整行丢
        "年代": "era", "补充正面": "extra_positive",
        "主体": "subject", "风格": "style", "地点": "scene", "场景": "scene",
        "动作": "action",
        "景别": "shot", "光线": "lighting", "氛围": "mood", "画质": "quality",
    },
    "HammPromptVideo": {
        "生成模式": "mode", "时长": "duration", "主体": "subject", "动作": "action",
        "场景": "scene", "风格": "style", "景别": "shot_size", "运镜": "camera_motion",
        "幅度": "amplitude", "速度": "speed", "转场": "transition",
        "分镜（每行一镜）": "shots_zh", "台词": "dialogue", "台词语言": "dialogue_lang",
        "环境音": "soundscape", "配乐": "music",
    },
    "HammPromptStoryboard": {
        "分镜（每行一镜）": "shots_zh", "固定角色前缀": "role_prefix",
        "固定风格后缀": "style_suffix",
    },
}

_FLOAT_FIELDS = {"duration"}


def _elements_widget():
    """三个提示词节点共用的「结构化提示词」文本框（放 optional 末尾，不打乱旧工作流）"""
    return ("STRING", {"multiline": True, "default": "",
                       "tooltip": "结构化提示词：随上方输入/选择实时变化；可手改，运行时按这份内容扩写"})


_FREE_TEXT_FIELDS = {
    "subject", "extra_positive", "extra_negative", "shots_zh", "dialogue",
    "soundscape", "music", "role_prefix", "style_suffix",
}


def _strip_paren(v):
    """「清（清制长袍…）」 -> 「清」。

    结构化框里的下拉值会带考据描述（框里看到的 = 喂给 LLM 的），
    但回写参数时必须还原成能在模板里查到的选项名，否则英文片段会丢失。
    """
    s = re.split(r"[（(]", v, 1)[0].strip(" ,，")
    return s or v


def apply_elements_overrides(kind, elements_text, params):
    """把结构化提示词文本解析回参数字典。仅覆盖文本里出现的行，其余参数保持原值。"""
    text = (elements_text or "").strip()
    if not text:
        return params
    fields = _ELEMENTS_FIELDS.get(kind) or {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "：" in line:
            k, _, v = line.partition("：")
        elif ":" in line:
            k, _, v = line.partition(":")
        else:
            continue
        k = _clean_label(k.strip())
        v = _clean_value(v.strip())
        if not k or not v or k not in fields:
            continue
        name = fields[k]
        if name == "shots_zh":
            v = v.replace(" / ", "\n")       # 分镜多行在框里折成「 / 」显示，拆回来
        if name in _FLOAT_FIELDS:
            try:
                v = float(re.sub(r"[^0-9.]", "", v))
            except ValueError:
                continue
        # 下拉类字段：值可能带考据描述括号（框里显示的完整词），还原成选项名
        if name == "era":
            # 年代走两步：先剥考据括号（旧文本「唐（人物与场景符合唐代背景）」→「唐」），
            # 再把框里的简洁写法（唐代）反查回选项名（唐）。少这一步，value("era", "唐代")
            # 在模板里查不到，英文的年代片段会整段丢掉。
            v = C.era_to_zh(_strip_paren(v))
        elif name not in _FREE_TEXT_FIELDS:
            v = _strip_paren(v)
        params[name] = v
    return params


# ---------------------------------------------------------------- 加载器
class HammLocalLLM:
    """加载本地 LLM（GGUF / transformers），供提示词节点复用，避免每镜重复加载"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (L.model_choices(), {"default": "（不启用本地模型）"}),
            },
            "optional": {
                "gpu_layers": ("INT", {"default": -1, "min": -1, "max": 500}),
                "ctx": ("INT", {"default": 4096, "min": 512, "max": 32768}),
                "max_tokens": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05}),
                "keep_loaded": ("BOOLEAN", {"default": True,
                                            "tooltip": "开=常驻显存供多个节点复用；关=每次用完即卸"}),
            },
        }

    RETURN_TYPES = (HAMM_LLM,)
    RETURN_NAMES = ("local_llm",)
    FUNCTION = "load"
    CATEGORY = "HammPrompt/本地模型"

    def load(self, model, gpu_layers=-1, ctx=4096,
             max_tokens=512, temperature=0.7, keep_loaded=True):
        path, backend = L.resolve_model(model)
        if not path:
            raise RuntimeError("未选择本地模型：把 GGUF 或 HF 模型放到 ComfyUI/models/LLM 后刷新页面")
        cfg = C.LLMConfig(mode=C.LLMConfig.LOCAL, backend=backend, path=path,
                          gpu_layers=gpu_layers, ctx=ctx, max_tokens=max_tokens,
                          temperature=temperature, keep_loaded=keep_loaded)
        if keep_loaded:  # 预热：真正加载一次，后面各节点直接复用
            L.get_session(backend, path, gpu_layers, ctx)
        return (cfg,)


class HammUnloadLLM:
    """手动卸载本地模型，把显存还给生图（接在链路末端或单独跑一次）"""

    @classmethod
    def INPUT_TYPES(cls):
        return {"optional": {"local_llm": (HAMM_LLM,), "any": ("*",)}}

    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "unload"
    CATEGORY = "HammPrompt/本地模型"
    OUTPUT_NODE = True          # 没有下游也要执行，否则卸载根本不会触发

    def unload(self, local_llm=None, any=None):
        L.unload_all()
        return {"ui": {"text": ["已卸载本地模型，显存已归还 ComfyUI"]}, "result": (any,)}


# ---------------------------------------------------------------- 文生图
class HammPromptImage:
    """中文输入 + 模板选择 -> 中文提示词（本地模型 / 在线 API 智能扩写）

    只出一个口：positive（中文正向，直接喂给吃中文的生图模型）。
    早期版本的 positive 是英文、另有一个 prompt 口放中文，对吃中文的模型
    英文口永远空挂，已合并。
    反向（negative）输出口已下线：本节点从不产生负向词，留着只会多一个
    恒为空的连线点，需要负向词请直接在下游 CLIPTextEncode 里写。
    """

    @classmethod
    def INPUT_TYPES(cls):
        opt = lambda c: C.options(c, "t2i")
        o = {"required": {"subject": ("STRING", {"multiline": True, "default": "白衣汉服少女"})}}
        o["optional"] = {
            "style": (opt("style"),),
            "scene": (opt("scene"),),
            "action": (opt("action"),),
            "shot": (opt("shot"),),
            "lighting": (opt("lighting"),),
            "mood": (opt("mood"),),
            "quality": (opt("quality"),),
            "era": (opt("era"),),                 # 年代插在画质之后 = 控件索引 8
            "extra_positive": ("STRING", {"multiline": True, "default": ""}),
            # negative_preset / extra_negative 已下线（用户要求去掉反向提示词相关项）：
            # 控件从 INPUT_TYPES 里删除 = 控件索引 9 / 11 消失，后面全部左移两格。
            # 前端 hammprompt_preview.js 的 dropLegacyNegativeSlots / fixNegativeShift
            # 负责把旧工作流 widgets_values 里的这两槽删掉，否则整体错位。
            # build() 仍保留这两个形参（默认空串），兼容旧的 API 调用与缓存 prompt。
            # 结构化提示词紧跟输入选择，运行时按这份内容扩写
            # （output_lang 已移除：只保留中文扩写，english 输出口保留兼容旧连线）
            "elements_text": _elements_widget(),
            "local_llm": (HAMM_LLM,),
            **_local_widgets(),
            **_api_widgets(),
        }
        return o

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("positive",)
    FUNCTION = "build"
    CATEGORY = "HammPrompt/文生图"

    def build(self, subject, style="", scene="", action="", shot="", lighting="", mood="",
              quality="", negative_preset="", extra_positive="", extra_negative="",
              output_lang="英文（推荐）", local_llm=None, llm_mode=C.LLMConfig.OFF,
              local_model="（不启用本地模型）", local_gpu_layers=-1,
              local_ctx=4096, local_max_tokens=512, local_temperature=0.7,
              keep_loaded=False, llm_model=DEFAULT_MODEL, llm_base_url=DEFAULT_URL,
              llm_api_key="", elements_text="", era="不指定"):
        # negative_preset / extra_negative 控件已下线：节点上不再暴露，也没人传值，
        # 形参留给旧的 API 调用者（不传即空串）。negative 输出口保留但恒为空 ——
        # 下游要负向词就自己在 CLIPTextEncode 里写。
        # 结构化提示词里改过的行覆盖对应参数：扩写按改后的内容来
        p = apply_elements_overrides("HammPromptImage", elements_text, {
            "subject": subject, "style": style, "scene": scene, "action": action,
            "shot": shot, "lighting": lighting, "mood": mood, "quality": quality,
            "era": era, "extra_positive": extra_positive,
        })

        cfg = _resolve_llm(local_llm, llm_mode, local_model,
                           local_gpu_layers, local_ctx, local_max_tokens,
                           local_temperature, keep_loaded,
                           llm_base_url, llm_model, llm_api_key)
        status = {}

        subject = p["subject"]; style = p["style"]; scene = p["scene"]
        action = p["action"]; shot = p["shot"]; lighting = p["lighting"]
        mood = p["mood"]; quality = p["quality"]; era = p["era"]
        extra_positive = p["extra_positive"]
        pos, neg, en, zh = C.build_t2i(
            subject, style, scene, action, shot, lighting, mood, quality,
            extra_positive, extra_negative, negative_preset, output_lang,
            llm=cfg, status=status, era=era)
        _log_status("文生图", status.get("vision", ""))
        _log_status("文生图", status.get("llm", ""))
        # 只有一个输出口：positive 直接给中文段。
        # 原先 positive(英文) + prompt(中文) 是两个语言版本，对吃中文的模型
        # （Qwen-Image / Z-Image 等）英文口永远是空挂的，合并成一个。
        # zh 为空（极端情况）时退回 pos，保证不会吐出空串。
        # negative 输出口已下线（本节点恒不产生负向词，接口参数仍保留兼容旧调用）。
        return {"result": (zh or pos,)}


# ---------------------------------------------------------------- 图生提示词（独立节点）
class HammPromptImageToPrompt:
    """上传参考图 → 本地 VLM / 在线视觉模型识别 → 输出中文提示词文本。

    与 `HammPromptImage` 完全解耦：
    - `HammPromptImage` 继续负责「中文要素 → 扩写 → 提示词」；
    - 本节点只负责「图 → 文本描述」，输出可直接接 `HammPromptPreview.positive`
      或任何吃 STRING 的输入口。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "参考图：自动识别成一段中文提示词"}),
            },
            "optional": {
                "llm_mode": (C.LLMConfig.MODES, {"default": C.LLMConfig.LOCAL}),
                "local_vision_model": (L.model_choices(),
                                       {"default": "（不启用本地模型）",
                                        "tooltip": "本地 VLM（Qwen2.5-VL GGUF），读图用"}),
                "local_vision_mmproj": ("STRING", {"default": "", "multiline": False,
                                        "tooltip": "VLM 的 mmproj 路径；留空自动在模型同目录找 mmproj*.gguf"}),
                "local_gpu_layers": ("INT", {"default": -1, "min": -1, "max": 500,
                                             "tooltip": "llama.cpp GPU 卸载层数，-1 = 全部卸载到显卡"}),
                "local_ctx": ("INT", {"default": 4096, "min": 512, "max": 32768}),
                "local_max_tokens": ("INT", {"default": 512, "min": 64, "max": 4096,
                                             "tooltip": "视觉模型输出上限"}),
                "local_temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05}),
                "keep_loaded": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "关=用完卸载；开=常驻显存，连续多图更快但占显存"}),
                "llm_model": ("STRING", {"default": DEFAULT_MODEL, "multiline": False}),
                "llm_base_url": ("STRING", {"default": DEFAULT_URL, "multiline": False}),
                "llm_api_key": ("STRING", {"default": "", "multiline": False}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "describe"
    CATEGORY = "HammPrompt/工具"

    def describe(self, image, llm_mode=C.LLMConfig.LOCAL,
                 local_vision_model="（不启用本地模型）", local_vision_mmproj="",
                 local_gpu_layers=-1, local_ctx=4096, local_max_tokens=512,
                 local_temperature=0.7, keep_loaded=False,
                 llm_model=DEFAULT_MODEL, llm_base_url=DEFAULT_URL, llm_api_key=""):
        if image is None:
            _log_status("图生提示词", "未收到图片")
            return ("",)

        cfg = _resolve_llm(None, llm_mode, "（不启用本地模型）",
                           local_gpu_layers, local_ctx, local_max_tokens,
                           local_temperature, keep_loaded,
                           llm_base_url, llm_model, llm_api_key,
                           local_vision_model, local_vision_mmproj)
        if cfg is None:
            msg = "⚠️ LLM 未启用：请选择本地 VLM 或在线 API 视觉模型"
            _log_status("图生提示词", msg)
            return (msg,)

        try:
            img_b64 = C.tensor_to_base64(image)
            fields = C.vision_describe(cfg, img_b64, group="t2i",
                                       max_tokens=512, temperature=0.4)
            parts = [fields.get(k, "") for k in (
                "subject", "style", "scene", "action", "shot",
                "lighting", "mood", "quality", "era", "extra_positive")]
            text = "，".join(p for p in parts if p)
            used = [k for k in (
                "subject", "style", "scene", "action", "shot",
                "lighting", "mood", "quality", "era") if fields.get(k)]
            _log_status("图生提示词", "✓ 识别要素：%s" % "、".join(used) if used else "✓ 已生成提示词")
            return (text or fields.get("subject", ""),)
        except Exception as e:
            msg = "⚠️ 图片识别失败：%s" % str(e)[:120]
            _log_status("图生提示词", msg)
            return (msg,)


# ---------------------------------------------------------------- 视频
class HammPromptVideo:
    """MiniMax H3 官方格式：T2VA / I2VA / FL2VA / L2VA + 运镜 + 分镜 + 声音"""

    @classmethod
    def INPUT_TYPES(cls):
        o = lambda c: C.options(c, "video")
        return {
            "required": {
                "mode": (o("mode"),),
                "duration": ("FLOAT", {"default": 5.0, "min": 2.0, "max": 15.0, "step": 0.5}),
                "subject": ("STRING", {"multiline": True, "default": "白衣女子"}),
                "action": ("STRING", {"multiline": True, "default": "撑伞走过石桥"}),
            },
            "optional": {
                "scene": ("STRING", {"multiline": True, "default": "江南古镇"}),
                "style": (o("style"),),
                "shot_size": (o("shot_size"),),
                "camera_motion": (o("camera_motion"),),
                "amplitude": (o("amplitude"),),
                "speed": (o("speed"),),
                "transition": (o("transition"),),
                "shots_zh": ("STRING", {"multiline": True, "default": "",
                                        "placeholder": "每行一镜，留空则用上面的主体/动作/场景生成单镜"}),
                "dialogue": ("STRING", {"multiline": True, "default": ""}),
                "dialogue_lang": (o("dialogue_lang"),),
                "soundscape": (o("soundscape"),),
                "music": (o("music"),),
                # 结构化提示词紧跟内容输入（配乐下面），运行时按这份内容扩写
                "elements_text": _elements_widget(),
                "local_llm": (HAMM_LLM,),
                **_local_widgets(),
                **_api_widgets(),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("H3_prompt", "prompt")
    FUNCTION = "build"
    CATEGORY = "HammPrompt/视频"

    def build(self, mode, duration, subject, action, scene="", style="", shot_size="",
              camera_motion="", amplitude="", speed="", transition="", shots_zh="",
              dialogue="", dialogue_lang="", soundscape="", music="",
              output_lang="英文（推荐）", local_llm=None, llm_mode=C.LLMConfig.OFF,
              local_model="（不启用本地模型）", local_gpu_layers=-1,
              local_ctx=4096, local_max_tokens=512, local_temperature=0.7,
              keep_loaded=False, llm_model=DEFAULT_MODEL, llm_base_url=DEFAULT_URL,
              llm_api_key="", elements_text=""):
        p = apply_elements_overrides("HammPromptVideo", elements_text, {
            "mode": mode, "duration": duration, "subject": subject, "action": action,
            "scene": scene, "style": style, "shot_size": shot_size,
            "camera_motion": camera_motion, "amplitude": amplitude, "speed": speed,
            "transition": transition, "shots_zh": shots_zh, "dialogue": dialogue,
            "dialogue_lang": dialogue_lang, "soundscape": soundscape, "music": music,
        })
        mode = p["mode"]; duration = p["duration"]; subject = p["subject"]
        action = p["action"]; scene = p["scene"]; style = p["style"]
        shot_size = p["shot_size"]; camera_motion = p["camera_motion"]
        amplitude = p["amplitude"]; speed = p["speed"]; transition = p["transition"]
        shots_zh = p["shots_zh"]; dialogue = p["dialogue"]
        dialogue_lang = p["dialogue_lang"]; soundscape = p["soundscape"]; music = p["music"]

        cfg = _resolve_llm(local_llm, llm_mode, local_model,
                           local_gpu_layers, local_ctx, local_max_tokens,
                           local_temperature, keep_loaded,
                           llm_base_url, llm_model, llm_api_key)
        status = {}
        out, en, zh = C.build_video(
            mode, duration, style, subject, action, scene, shot_size,
            camera_motion, amplitude, speed, transition, shots_zh,
            dialogue, dialogue_lang, soundscape, music,
            output_lang, llm=cfg, status=status)
        _log_status("视频", status.get("llm", ""))
        return {"result": (out, zh)}


# ---------------------------------------------------------------- 分镜批量
class HammPromptStoryboard:
    """中文分镜批量：每行一镜 -> 每行一条完整英文提示词，可接 easy promptLine 一次跑完"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "shots_zh": ("STRING", {"multiline": True, "default": "站在石桥尽头\n撑伞缓步走过石桥\n侧身回眸"}),
            },
            "optional": {
                "role_prefix": ("STRING", {"multiline": True, "default": "白衣汉服少女，黑长发"}),
                "style_suffix": ("STRING", {"multiline": True, "default": "cinematic still, consistent art style, soft natural lighting, high detail"}),
                # 结构化提示词紧跟内容输入（固定风格后缀下面），运行时按这份内容扩写
                "elements_text": _elements_widget(),
                "local_llm": (HAMM_LLM,),
                **_local_widgets(),
                **_api_widgets(),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt_lines", "prompt")
    FUNCTION = "build"
    CATEGORY = "HammPrompt/文生图"

    def build(self, shots_zh, role_prefix="", style_suffix="", output_lang="英文（推荐）",
              local_llm=None, llm_mode=C.LLMConfig.OFF,
              local_model="（不启用本地模型）", local_gpu_layers=-1,
              local_ctx=4096, local_max_tokens=512, local_temperature=0.7,
              keep_loaded=False, llm_model=DEFAULT_MODEL, llm_base_url=DEFAULT_URL,
              llm_api_key="", elements_text=""):
        p = apply_elements_overrides("HammPromptStoryboard", elements_text, {
            "shots_zh": shots_zh, "role_prefix": role_prefix, "style_suffix": style_suffix,
        })
        shots_zh = p["shots_zh"]; role_prefix = p["role_prefix"]; style_suffix = p["style_suffix"]

        cfg = _resolve_llm(local_llm, llm_mode, local_model,
                           local_gpu_layers, local_ctx, local_max_tokens,
                           local_temperature, keep_loaded,
                           llm_base_url, llm_model, llm_api_key)
        lines = [l.strip() for l in (shots_zh or "").split("\n") if l.strip()]
        prefix = C.zh2en(role_prefix)
        suffix = style_suffix.strip()

        # 批量跑 LLM 时强制常驻显存：否则每镜都要重新加载一次模型（几十秒 × N）
        forced_keep = False
        if cfg is not None and cfg.enabled and not cfg.keep_loaded:
            cfg.keep_loaded = True
            forced_keep = True

        out_lines, zh_lines = [], []
        ok_n = 0
        fail_msg = ""
        status_msg = C.LLM_STATUS_OFF
        try:
            for i, l in enumerate(lines):
                en = C.zh2en(l)
                zh_line = l
                if cfg is not None and cfg.enabled:
                    try:
                        got_zh, got_en = C.split_bilingual(
                            cfg.run(C.SYS_T2I, C.USER_T2I_SHOT % (prefix or "(none)", l),
                                    max_tokens=768))
                        en_ok = bool(got_en) and C._llm_output_ok(got_en, "英文（推荐）")
                        zh_ok = bool(got_zh)
                        if en_ok:
                            en = got_en
                        if zh_ok:
                            zh_line = got_zh     # 中文也换成扩写版
                        if en_ok or zh_ok:
                            ok_n += 1
                        elif not fail_msg:
                            fail_msg = "⚠ 第 %d 镜输出不合格，已回退离线拼装" % (i + 1)
                    except Exception as e:
                        en = C.zh2en(l)
                        if not fail_msg:
                            fail_msg = "✗ 第 %d 镜失败：%s" % (i + 1, str(e)[:110])
                en = C.merge([prefix, en.strip().rstrip(" .,;，。"), suffix])
                out_lines.append(en)
                zh_lines.append("SC%03d | %s" % (i + 1, zh_line))
        finally:
            if forced_keep:
                cfg.keep_loaded = False
                cfg.release()

        if cfg is not None and cfg.enabled:
            status_msg = "✓ 已智能扩写 %d/%d 镜" % (ok_n, len(lines))
            if fail_msg:
                status_msg += "　" + fail_msg

        text = "\n".join(out_lines)
        zh = "\n".join(zh_lines)
        if output_lang == "中文":
            out = zh
        elif output_lang == "中英对照":
            out = "中文：\n%s\n\nEnglish：\n%s" % (zh, text)
        else:
            out = text
        _log_status("分镜批量", status_msg)
        return {"result": (out, zh)}


# ---------------------------------------------------------------- 文生图 / 图生图 切换
class HammImageMode:
    """文生图 / 图生图 一键切换（HammPrompt 包内置，纯后端、不依赖第三方节点）。

    选「文生图」→ 输出 latent_txt2img（通常接 EmptyLatentImage 的 LATENT）。
    选「图生图」→ 输出 latent_img2img（通常接 LoadImage → VAEEncode 的 LATENT）。

    未选的那一路在图里可以一直挂着不接：ComfyUI 只执行「连到输出」的祖先节点，
    不接的那路不会被触发，所以两条线并存互不干扰、也不会因没图而报错。
    """

    MODES = ["文生图", "图生图"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (cls.MODES, {"default": cls.MODES[0]}),
            },
            "optional": {
                "latent_txt2img": ("LATENT",),
                "latent_img2img": ("LATENT",),
            },
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("latent",)
    FUNCTION = "pick"
    CATEGORY = "HammPrompt/工具"

    def pick(self, mode, latent_txt2img=None, latent_img2img=None):
        if mode == self.MODES[1]:  # 图生图
            if latent_img2img is None:
                raise RuntimeError(
                    "Hamm 生图模式＝图生图：请把 LoadImage → VAEEncode 的 LATENT "
                    "接到本节点的 latent_img2img 口")
            return (latent_img2img,)
        # 文生图
        if latent_txt2img is None:
            raise RuntimeError(
                "Hamm 生图模式＝文生图：请把 EmptyLatentImage 的 LATENT "
                "接到本节点的 latent_txt2img 口")
        return (latent_txt2img,)


# ---------------------------------------------------------------- 中文预览节点
class HammPromptPreview:
    """独立预览面板：正向提示词（可编辑文本框）。

    框里的值会随队列透传给下游；不连输入时可直接在框内手写，
    连了输入则显示上游送来的值（并可在此基础上编辑）。
    （英文显示框、反向提示词框已移除 —— 只保留大模型扩写后的中文正向提示词。）
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("STRING", {"multiline": True, "default": "",
                                        "placeholder": "接提示词节点的 positive 口"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "preview"
    CATEGORY = "HammPrompt/工具"
    OUTPUT_NODE = True

    def preview(self, positive):
        return {
            "ui": {
                "hamm_preview_cn": [positive or "(无)"],
            },
            "result": (positive,),
        }


# ---------------------------------------------------------------- 执行开关
RUN_SWITCH = ["开：继续生成图片", "关：只生成提示词（后面全部跳过）"]

# ExecutionBlocker 是 ComfyUI 官方的「拦截下游执行」机制：某个输出口返回它，
# 所有依赖这个输出的下游节点会被标记为 blocked（界面显示被跳过），上游照常执行。
# 比 raise 中断优雅：不会打断队列里其它无关的执行分支。
# 注意本机版本它放在 comfy_execution.graph_utils（旧版在 execution，两处都试）。
try:
    from comfy_execution.graph_utils import ExecutionBlocker as _ExecBlocker
except Exception:
    try:
        from comfy_execution.execution import ExecutionBlocker as _ExecBlocker
    except Exception:
        _ExecBlocker = None
try:
    from comfy.model_management import InterruptProcessingException as _Interrupt
except Exception:
    _Interrupt = None


class HammPromptGate:
    """执行开关：串在提示词节点和 CLIP 编码之间。

    开 = 原样透传，正常出图；
    关 = 输出 ExecutionBlocker，下游（CLIP 编码 / KSampler / 保存图片）全部被跳过，
    队列只运行到提示词生成为止 —— 省掉采样时间，专心调提示词。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "run": (RUN_SWITCH, {"default": RUN_SWITCH[0]}),
                "positive": ("STRING", {"forceInput": True}),
            },
            "optional": {
                "negative": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "build"
    CATEGORY = "HammPrompt/工具"

    def build(self, run, positive="", negative=""):
        if run == RUN_SWITCH[1]:
            msg = "Hamm 开关：关（只生成提示词，图片部分已跳过）"
            if _ExecBlocker is not None:
                b = _ExecBlocker(msg)
                return (b, b)
            if _Interrupt is not None:
                print("[HammPrompt] " + msg)
                raise _Interrupt()
            raise RuntimeError(msg)
        return (positive, negative)


NODE_CLASS_MAPPINGS = {
    "HammLocalLLM": HammLocalLLM,
    "HammUnloadLLM": HammUnloadLLM,
    "HammPromptImage": HammPromptImage,
    "HammPromptImageToPrompt": HammPromptImageToPrompt,
    "HammPromptVideo": HammPromptVideo,
    "HammPromptStoryboard": HammPromptStoryboard,
    "HammPromptPreview": HammPromptPreview,
    "HammPromptGate": HammPromptGate,
    "HammImageMode": HammImageMode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HammLocalLLM": "Hamm 本地模型 (GGUF/HF)",
    "HammUnloadLLM": "Hamm 卸载本地模型 (释放显存)",
    "HammPromptImage": "Hamm 文生图提示词",
    "HammPromptImageToPrompt": "Hamm 图生提示词",
    "HammPromptVideo": "Hamm 视频提示词 (MiniMax H3)",
    "HammPromptStoryboard": "Hamm 分镜批量",
    "HammPromptPreview": "Hamm 提示词预览（中文）",
    "HammPromptGate": "Hamm 执行开关（关=只出提示词）",
    "HammImageMode": "Hamm 生图模式切换（文生图/图生图）",
}

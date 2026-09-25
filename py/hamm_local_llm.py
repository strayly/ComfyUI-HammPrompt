# -*- coding: utf-8 -*-
"""HammPrompt 本地 LLM 后端：llama.cpp (GGUF) / transformers (HF)

设计要点
--------
1. 惰性 import：torch / llama_cpp / transformers 都在真正用到时才导入，
   模块导入零成本，不会因为缺依赖导致整个节点包挂掉。
2. 模型目录：复用 ComfyUI 官方的 ``models/LLM``（也兼容 ``models/llm``），
   用户下载完直接丢进去就能在下拉里看到。
3. 显存：默认「用完即卸」（keep_loaded=False）——本地 LLM 常驻会跟生图抢
   显存，16GB 卡上这点很致命。想连续多镜复用再打开 keep_loaded。
4. 卸载走 torch.cuda.empty_cache() + comfy.model_management.soft_empty_cache()，
   把显存真正还给 ComfyUI。
"""

import gc
import json
import os
import re
import time

# ---------------------------------------------------------------- 目录

def llm_dirs():
    """ComfyUI 的 LLM 模型目录（含子目录）"""
    dirs = []
    try:
        import folder_paths
        for key in ("LLM", "llm"):
            try:
                for p in folder_paths.get_folder_paths(key):
                    if p and os.path.isdir(p) and p not in dirs:
                        dirs.append(p)
            except Exception:
                pass
    except Exception:
        pass
    if not dirs:  # 兜底：custom_nodes/ComfyUI-HammPrompt/py/../../.. = ComfyUI 根
        here = os.path.abspath(__file__)
        for _ in range(4):
            here = os.path.dirname(here)
        guess = os.path.join(here, "models", "LLM")
        if os.path.isdir(guess):
            dirs.append(guess)
    return dirs


_SCAN_CACHE = {"t": 0.0, "gguf": [], "hf": []}
_SCAN_TTL = 10.0


def _in_blobs(p):
    """HF 缓存里的 blobs 软链目录不参与展示"""
    return "blobs" in p.replace("\\", "/").split("/")


def scan_models(force=False):
    """扫描本地可用模型，返回 (gguf列表, hf列表)。结果缓存 10 秒。"""
    now = time.time()
    if not force and now - _SCAN_CACHE["t"] < _SCAN_TTL:
        return _SCAN_CACHE["gguf"], _SCAN_CACHE["hf"]

    gguf, hf = [], []
    for root in llm_dirs():
        for r, ds, fs in os.walk(root):
            if r.count(os.sep) - root.count(os.sep) > 3:
                ds[:] = []
            for f in fs:
                if not f.lower().endswith(".gguf"):
                    continue
                low = f.lower()
                # mmproj / 视觉塔不是文本模型，排除
                if low.startswith("mmproj") or "mmproj" in low:
                    continue
                p = os.path.join(r, f)
                rel = os.path.relpath(p, root).replace("\\", "/")
                try:
                    gb = os.path.getsize(p) / (1024 ** 3)
                except Exception:
                    gb = 0.0
                gguf.append({"path": p, "label": "%s  (%.2fGB)" % (rel, gb), "gb": gb})
            # HF 模型：目录里有 config.json
            if "config.json" in fs and not _in_blobs(r):
                hf.append(_probe_hf(r, root))
        # models--org--name 缓存格式：展开到 snapshots/<hash>
        for d in list(ds):
            if d.startswith("models--"):
                base = os.path.join(r, d, "snapshots")
                if not os.path.isdir(base):
                    continue
                for snap in sorted(os.listdir(base)):
                    sp = os.path.join(base, snap)
                    if os.path.isfile(os.path.join(sp, "config.json")):
                        hf.append(_probe_hf(sp, root))
                        break

    # 视觉/多模态模型（Qwen-VL 之类）对纯文本没用，排后面
    gguf.sort(key=lambda x: x["label"].lower())
    hf = [h for h in hf if h]
    hf.sort(key=lambda x: x["label"].lower())
    _SCAN_CACHE.update({"t": now, "gguf": gguf, "hf": hf})
    return gguf, hf


def _probe_hf(path, root):
    """读 config.json 判断是不是可用的因果语言模型"""
    try:
        cfg = json.load(open(os.path.join(path, "config.json"), "r", encoding="utf-8"))
    except Exception:
        return None
    arch = " ".join(cfg.get("architectures", []) or [])
    model_type = (cfg.get("model_type") or "").lower()
    if "ForCausalLM" not in arch and "CausalLM" not in arch:
        if model_type not in ("llama", "qwen2", "qwen3", "gemma", "phi", "smollm", "mistral", "gpt2"):
            return None
    name = os.path.basename(path.rstrip("\\/"))
    parent = os.path.basename(os.path.dirname(path.rstrip("\\/")))
    if len(name) in (40, 64) and parent == "snapshots":  # hash 目录，取上层名字
        parts = os.path.relpath(path, root).replace("\\", "/").split("/")
        name = parts[0].replace("models--", "").replace("--", "/") if parts else name
    elif name.isdigit() or len(name) == 40:
        parts = os.path.relpath(path, root).replace("\\", "/").split("/")
        name = parts[0].replace("models--", "").replace("--", "/") if parts else name
    return {"path": path, "label": name, "model_type": model_type}


def _backend_of(path):
    return "gguf" if str(path).lower().endswith(".gguf") else "hf"


def resolve_model(choice, custom=""):
    """把下拉值 / 自定义路径解析成 (绝对路径, backend)。

    choice 存的是相对 models/LLM 的路径，这样 UI 里不会显示一长串绝对路径。
    """
    raw = (custom or "").strip()
    if raw:
        if os.path.exists(raw):
            return raw, _backend_of(raw)
        for d in llm_dirs():
            cand = os.path.join(d, raw)
            if os.path.exists(cand):
                return cand, _backend_of(cand)
        return raw, _backend_of(raw)
    if not choice or choice.startswith("（") or choice.startswith("("):
        return "", "gguf"
    if os.path.exists(choice):
        return choice, _backend_of(choice)
    for d in llm_dirs():
        cand = os.path.join(d, choice)
        if os.path.exists(cand):
            return cand, _backend_of(cand)
    return choice, _backend_of(choice)


def model_choices():
    """给节点下拉用：[(显示/值)]，值为相对路径"""
    gguf, hf = scan_models()
    out = ["（不启用本地模型）"]
    roots = llm_dirs()
    for m in gguf:
        rel = m["path"]
        for d in roots:
            if m["path"].startswith(d):
                rel = os.path.relpath(m["path"], d).replace("\\", "/")
                break
        out.append(rel)
    seen = set(out)
    for m in hf:
        rel = m["path"]
        for d in roots:
            if m["path"].startswith(d):
                rel = os.path.relpath(m["path"], d).replace("\\", "/")
                break
        if rel not in seen:
            out.append(rel)
            seen.add(rel)
    return out


# ---------------------------------------------------------------- VLM chat handler 选择

_VISION_HANDLER_MAP = (
    # (文件名特征小写子串, handler 类名, 额外构造参数)
    ("qwen2.5-vl", "Qwen25VLChatHandler", {"image_min_tokens": 1024}),
    ("qwen2_5_vl", "Qwen25VLChatHandler", {"image_min_tokens": 1024}),
    ("qwen3-vl", "Qwen3VLChatHandler", {"image_min_tokens": 1024}),
    ("qwen3_vl", "Qwen3VLChatHandler", {"image_min_tokens": 1024}),
    ("minicpm-v-2_6", "MiniCPMv26ChatHandler", {}),
    ("minicpmv2.6", "MiniCPMv26ChatHandler", {}),
    ("minicpm-v2.6", "MiniCPMv26ChatHandler", {}),
    ("moondream", "MoondreamChatHandler", {}),
    ("llava-v1.6", "Llava16ChatHandler", {}),
    ("llava-v1.5", "Llava15ChatHandler", {}),
    ("llava1.6", "Llava16ChatHandler", {}),
    ("llava1.5", "Llava15ChatHandler", {}),
)


def _vision_chat_handler(model_path, mmproj_path, verbose=False):
    """根据模型文件名选一个专门的 VLM chat handler。

    llama.cpp 0.3.48 的 GenericMTMDChatHandler 对很多 VLM 的 chat template 识别不好
    （例如 Qwen2.5-VL GGUF 常被识别成普通文本模型），导致 content list 传不进去、
    图片实际上没被看到。这里按文件名前缀强制用对应 handler，并把 mmproj 交给 handler。
    文件名匹配不到的模型走 None → 让 llama.cpp 自己自动选 handler。
    """
    low = (model_path or "").lower()
    import llama_cpp.llama_chat_format as lcf
    for token, cls_name, extra_kw in _VISION_HANDLER_MAP:
        if token in low:
            cls = getattr(lcf, cls_name, None)
            if cls is None:
                continue
            kw = dict(mmproj_path=mmproj_path, verbose=verbose, use_gpu=True)
            kw.update(extra_kw)
            try:
                return cls(**kw)
            except Exception as e:
                raise RuntimeError(
                    "创建 %s 失败：%s\n"
                    "请确认 mmproj 与主模型配套且来自同一仓库。" % (cls_name, str(e)[:200]))
    return None


# ---------------------------------------------------------------- 会话

class Session(object):
    """统一封装 llama.cpp / transformers 两种后端"""

    def __init__(self, backend, path, gpu_layers=-1, ctx=4096, mmproj=None):
        self.backend = backend
        self.path = path
        self.gpu_layers = gpu_layers
        self.ctx = ctx
        self.mmproj = mmproj          # 视觉模型的 mmproj（图片塔）路径；None = 纯文本模型
        self.obj = None
        self.tok = None
        self._load()

    # ---- 加载
    def _load(self):
        if self.backend == "gguf":
            from llama_cpp import Llama
            kw = dict(model_path=self.path,
                      n_ctx=int(self.ctx),
                      n_gpu_layers=int(self.gpu_layers),
                      n_batch=512,
                      verbose=False)
            if self.mmproj:          # 视觉模型：把 mmproj（图片塔）一起加载
                # 很多 VLM 的 GGUF 内嵌 chat template 不是 VL 版，llama.cpp 会误用
                # GenericMTMDChatHandler，导致图片根本没被看到。按文件名强制用专用 handler。
                handler = _vision_chat_handler(self.path, self.mmproj, verbose=False)
                if handler is not None:
                    kw["chat_handler"] = handler
                else:
                    kw["mmproj_path"] = self.mmproj
            try:
                self.obj = Llama(**kw)
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if "failed to load" in low or "could not load" in low or "error loading" in low:
                    if self.mmproj:
                        raise RuntimeError(
                            "加载视觉模型失败：%s\n"
                            "常见原因：① mmproj 与模型不配套（必须同仓库同名下载）；"
                            "② 当前 llama.cpp 版本不支持该 VLM（ComfyUI 自带 llama-cpp-python 0.3.48，"
                            "对 Qwen2.5-VL 等新版视觉模型可能不兼容）；"
                            "③ 显存/内存不足。\n"
                            "解决：换用 LLaVA-v1.6 / MiniCPM-V-2.6 / moondream2 等成熟 VLM GGUF，"
                            "或把 llm_mode 切到「在线 API」用视觉模型（如 glm-4v-flash）。" % msg[:200])
                    else:
                        raise RuntimeError(
                            "加载本地模型失败：%s\n"
                            "提示：可尝试 n_gpu_layers=0 纯 CPU 加载，或确认该 GGUF 与当前 llama.cpp 兼容。" % msg[:200])
                raise
        else:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            try:                       # 别让权重加载进度条刷屏 ComfyUI 控制台
                from transformers.utils import logging as hf_logging
                hf_logging.disable_progress_bar()
            except Exception:
                pass
            self.tok = AutoTokenizer.from_pretrained(self.path, trust_remote_code=True)
            kw = dict(trust_remote_code=True, low_cpu_mem_usage=True)
            try:
                self.obj = AutoModelForCausalLM.from_pretrained(
                    self.path, dtype="auto", device_map="auto", **kw)
            except TypeError:  # 旧版 transformers
                self.obj = AutoModelForCausalLM.from_pretrained(
                    self.path, torch_dtype="auto", device_map="auto", **kw)
            if getattr(self.obj, "generation_config", None) is not None:
                try:
                    self.obj.generation_config.pad_token_id = self.obj.generation_config.eos_token_id
                except Exception:
                    pass

    # ---- 生成
    def chat(self, system, user, max_tokens=384, temperature=0.7, top_p=0.9):
        msgs = [{"role": "system", "content": system or ""},
                {"role": "user", "content": user}]

        if self.backend == "gguf":
            try:
                r = self.obj.create_chat_completion(
                    messages=msgs, max_tokens=int(max_tokens),
                    temperature=float(temperature), top_p=float(top_p))
                return (r["choices"][0]["message"]["content"] or "").strip()
            except Exception:
                # 没有内置 chat template 的 GGUF：退化成纯 completion
                prompt = "%s\n\n%s\n\n" % (system or "", user)
                r = self.obj(prompt, max_tokens=int(max_tokens),
                             temperature=float(temperature), top_p=float(top_p),
                             stop=["</s>", "<|im_end|>", "\n\n\n"])
                return (r["choices"][0]["text"] or "").strip()

        import torch
        enc = None
        try:
            res = self.tok.apply_chat_template(
                msgs, add_generation_prompt=True, return_tensors="pt", tokenize=True)
            if hasattr(res, "input_ids"):      # transformers>=4.34 返回 BatchEncoding
                enc, ids = res, res["input_ids"]
            else:
                ids = res
        except Exception:                       # 无 chat template 的模型，退化成纯文本
            enc = self.tok("%s\n\n%s\n" % (system or "", user), return_tensors="pt")
            ids = enc["input_ids"]
        try:
            if enc is not None:
                enc = enc.to(self.obj.device)
                ids = enc["input_ids"]
            else:
                ids = ids.to(self.obj.device)
        except Exception:
            pass
        do_sample = float(temperature) > 0.01
        kw = dict(max_new_tokens=int(max_tokens), do_sample=do_sample,
                  pad_token_id=getattr(self.tok, "eos_token_id", None))
        if do_sample:
            kw.update(temperature=float(temperature), top_p=float(top_p))
        with torch.no_grad():
            out = self.obj.generate(ids, **kw)
        cut = ids.shape[-1]
        seq = out[0]
        if seq.dim() > 1:
            seq = seq[0]
        return self.tok.decode(seq[cut:], skip_special_tokens=True).strip()

    # ---- 视觉（多模态）：把图片 base64 连同文本发给 VLM
    def chat_vision(self, system, user, image_b64, max_tokens=512, temperature=0.4, top_p=0.9):
        if self.backend != "gguf":
            raise RuntimeError("本地视觉暂仅支持 GGUF（llama.cpp + mmproj）后端")
        if not self.mmproj:
            raise RuntimeError("视觉模型未加载 mmproj（图片塔），无法看图；"
                               "请为 VLM 准备对应的 mmproj 文件并放到模型同目录")
        msgs = [
            {"role": "system", "content": system or ""},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,%s" % image_b64}},
                {"type": "text", "text": user},
            ]},
        ]
        try:
            r = self.obj.create_chat_completion(
                messages=msgs, max_tokens=int(max_tokens),
                temperature=float(temperature), top_p=float(top_p))
            return (r["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            raise RuntimeError("本地视觉推理失败：%s" % str(e)[:140])

    # ---- 释放
    def close(self):
        try:
            if self.obj is not None and hasattr(self.obj, "close"):
                self.obj.close()
        except Exception:
            pass
        self.obj = None
        self.tok = None


# ---------------------------------------------------------------- 缓存管理

_CURRENT = {"key": None, "sess": None}


def _soft_empty_cache():
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    try:
        import comfy.model_management as mm
        mm.soft_empty_cache()
    except Exception:
        pass


def unload_all():
    """卸载当前模型并归还显存"""
    s = _CURRENT.get("sess")
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
    _CURRENT["key"] = None
    _CURRENT["sess"] = None
    _soft_empty_cache()


def get_session(backend, path, gpu_layers=-1, ctx=4096, mmproj=None):
    """按 key 复用会话；换了模型就先卸载旧的（mmproj 不同也算不同模型）"""
    key = (backend, path, int(gpu_layers), int(ctx), mmproj or "")
    if _CURRENT["key"] == key and _CURRENT["sess"] is not None:
        return _CURRENT["sess"]
    unload_all()
    sess = Session(backend, path, gpu_layers, ctx, mmproj=mmproj)
    _CURRENT["key"] = key
    _CURRENT["sess"] = sess
    return sess


# ---------------------------------------------------------------- mmproj 探测
def _token_set(name):
    """把文件名拆成可用于匹配的 token（字母数字段 + 数字段）"""
    import re as _re
    base = os.path.splitext(os.path.basename(name or ""))[0].lower()
    # 同时保留完整词段和去掉连下划线后的连续字母数字
    tokens = set(_re.findall(r"[a-z0-9]+", base))
    return tokens


def _mmproj_score(model_path, mmproj_path):
    """mmproj 与主模型的匹配分数：共享 token 越多、且同名 f16 越优先"""
    model_tok = _token_set(model_path)
    proj_tok = _token_set(mmproj_path)
    shared = model_tok & proj_tok
    low = mmproj_path.lower()
    score = len(shared) * 10
    if "f16" in low or "fp16" in low:
        score += 5
    elif "q4" in low:
        score += 2
    return score


def auto_mmproj(model_path, strict=True):
    """在 VLM 同目录里自动找一个 mmproj（图片塔）.gguf。

    会优先挑选文件名里包含「主模型同名 token」的 mmproj（例如主模型是
    Qwen2.5-VL-7B-Instruct-q4_k_m.gguf，会优先匹配 Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf）。
    strict=True 时找不到直接抛错，把缺少 mmproj 的原因和下载方向告诉用户。
    这样用户把 VLM GGUF 和它配套的 mmproj 放一起，就不需要手动填 mmproj 路径。
    """
    if not model_path:
        if strict:
            raise RuntimeError("未选择本地视觉模型（local_vision_model）")
        return ""
    d = os.path.dirname(model_path)
    if not d or not os.path.isdir(d):
        if strict:
            raise RuntimeError("本地视觉模型目录不存在：%s" % model_path)
        return ""
    cands = []
    try:
        for f in os.listdir(d):
            low = f.lower()
            if low.endswith(".gguf") and "mmproj" in low:
                cands.append(os.path.join(d, f))
    except Exception:
        pass
    if not cands:
        if strict:
            raise RuntimeError(
                "未找到与视觉模型配套的 mmproj*.gguf 文件。\n"
                "本地 VLM 必须成对下载：① 主模型 .gguf  ② mmproj（图片塔）.gguf，"
                "两者要来自同一仓库、同一版本。\n"
                "请把 mmproj 文件放到模型同目录：%s\n"
                "例如：huggingface.co/Mungert/Qwen2.5-VL-7B-Instruct-GGUF 下载 "
                "Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf。\n"
                "不想折腾本地模型可把 llm_mode 切到「在线 API」用视觉模型。" % d)
        return ""
    # 按与主模型的匹配度排序，同名系列排最前
    cands.sort(key=lambda p: _mmproj_score(model_path, p), reverse=True)
    return cands[0]


# ---------------------------------------------------------------- 高阶调用

SYS_TRANSLATE = (
    "You are a translation engine for image/video generation prompts. "
    "Translate the Chinese text into natural, concrete English describing only "
    "what is visible: subject, appearance, action, environment, lighting, camera. "
    "Comma separated phrases, no sentences, no explanations, no quotes. "
    "Output ONLY the English translation."
)

SYS_EXPAND = (
    "You are an expert prompt engineer for image diffusion models. "
    "Rewrite the user's brief into ONE dense English prompt: subject first, then appearance, "
    "action, environment, lighting, shot type, then style and quality tags. "
    "Use concrete visible nouns, comma separated, no sentences, no explanations, no quotes, "
    "no line breaks. Output ONLY the final English prompt."
)

SYS_BILINGUAL = (
    "You are a bilingual prompt engineer. Given the user's Chinese brief, output exactly two "
    "lines and nothing else, in this strict format:\n"
    "EN: <one dense English prompt, comma separated visible details only>\n"
    "ZH: <the same prompt in natural Chinese>\n"
    "No explanations, no extra lines, no quotes."
)


def _parse_bilingual(text):
    """解析 EN:/ZH: 两段；解析失败返回 (None, None)"""
    en = zh = None
    for line in (text or "").splitlines():
        s = line.strip()
        if s.upper().startswith("EN:"):
            en = s[3:].strip()
        elif s.upper().startswith("ZH:"):
            zh = s[3:].strip()
        elif s.startswith("英文"):
            en = s.split(":", 1)[-1].split("：", 1)[-1].strip()
        elif s.startswith("中文"):
            zh = s.split(":", 1)[-1].split("：", 1)[-1].strip()
    if en and zh:
        return en, zh
    return (en or None), (zh or None)


def run(backend, path, system, user, gpu_layers=-1, ctx=4096,
        max_tokens=384, temperature=0.7, top_p=0.9, keep_loaded=False):
    """跑一次本地推理；keep_loaded=False 时跑完立刻卸，把显存还给生图"""
    sess = get_session(backend, path, gpu_layers, ctx)
    try:
        return sess.chat(system, user, max_tokens, temperature, top_p)
    finally:
        if not keep_loaded:
            unload_all()


def translate(backend, path, text, gpu_layers=-1, ctx=4096, max_tokens=256,
              temperature=0.3, keep_loaded=False):
    """本地模型翻译：中文 -> 英文提示词"""
    return run(backend, path, SYS_TRANSLATE,
               "Translate to English prompt text:\n%s" % text,
               gpu_layers, ctx, max_tokens, temperature, 0.9, keep_loaded)


def bilingual(backend, path, brief, gpu_layers=-1, ctx=4096, max_tokens=512,
              temperature=0.7, keep_loaded=False):
    """本地模型一次产出中英对照，返回 (en, zh)，失败返回 (None, None)"""
    txt = run(backend, path, SYS_BILINGUAL, brief,
              gpu_layers, ctx, max_tokens, temperature, 0.9, keep_loaded)
    en, zh = _parse_bilingual(txt)
    # 弱模型常把中文原样复读，EN 段必须是干净的英文，否则判定失败让上层回退
    if not en or zh_ratio(en) > 0.05:
        return None, None
    if not zh or zh_ratio(zh) < 0.05:
        zh = None
    return en, zh


def run_vision(backend, path, mmproj, system, user, image_b64, gpu_layers=-1, ctx=4096,
               max_tokens=512, temperature=0.4, keep_loaded=False):
    """跑一次本地视觉推理（GGUF + mmproj）。

    mmproj 为空时由调用方先 auto_mmproj 探测；这里直接用它加载。
    keep_loaded=False 时跑完立刻卸，把显存还给生图。
    """
    sess = get_session(backend, path, gpu_layers, ctx, mmproj=mmproj)
    try:
        return sess.chat_vision(system, user, image_b64, max_tokens, temperature)
    finally:
        if not keep_loaded:
            unload_all()

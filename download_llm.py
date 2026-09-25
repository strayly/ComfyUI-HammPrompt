# -*- coding: utf-8 -*-
"""下载本地 LLM（GGUF）到 ComfyUI/models/LLM

用法（用 ComfyUI 自带的 python 跑，它才有 huggingface_hub）：

    "E:\\soft\\ComfyUI_windows_portable\\python_embeded\\python.exe" download_llm.py

常用参数：
    --preset qwen3-4b      # 默认，约 2.5GB，中文最强的小体积选择
    --preset qwen3-1.7b    # 约 1.1GB，更快更省显存
    --mirror               # 走 hf-mirror.com（国内下载更快）
    --repo  org/name --file xxx.gguf   # 自定义仓库
    --dir   D:\\path\\to\\models\\LLM      # 自定义存放目录
"""

import argparse
import os
import sys

PRESETS = {
    # 名称: (repo, file, 说明)
    "qwen3-4b": ("bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF",
                 "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
                 "2.5GB | 中文强、指令跟随好 | RTX 16GB 推荐"),
    "qwen3-4b-unsloth": ("unsloth/Qwen3-4B-Instruct-2507-GGUF",
                         "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
                         "2.5GB | 同上，unsloth 版本"),
    "qwen3-8b": ("bartowski/Qwen_Qwen3-8B-GGUF",
                 "Qwen3-8B-Q4_K_M.gguf",
                 "4.9GB | 更强但更慢，显存够再选"),
}

CANDIDATE_DIRS = [
    os.environ.get("COMFYUI_PATH", ""),
    r"E:\soft\ComfyUI_windows_portable\ComfyUI",
    r"D:\soft\ComfyUI_windows_portable\ComfyUI",
    r"C:\ComfyUI",
]


def find_llm_dir(explicit=""):
    if explicit:
        os.makedirs(explicit, exist_ok=True)
        return explicit
    for base in CANDIDATE_DIRS:
        if not base:
            continue
        p = os.path.join(base, "models", "LLM")
        if os.path.isdir(os.path.join(base, "main.py")) or os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            return p
    print("找不到 ComfyUI 目录，请用 --dir 指定 models/LLM 的路径")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="qwen3-4b", choices=list(PRESETS))
    ap.add_argument("--repo", default="")
    ap.add_argument("--file", default="")
    ap.add_argument("--dir", default="")
    ap.add_argument("--mirror", action="store_true", help="走 hf-mirror.com")
    a = ap.parse_args()

    if a.mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    if a.repo and a.file:
        repo, fname, note = a.repo, a.file, "自定义"
    else:
        repo, fname, note = PRESETS[a.preset]

    dst = find_llm_dir(a.dir)
    print("仓库 : %s" % repo)
    print("文件 : %s  (%s)" % (fname, note))
    print("镜像 : %s" % os.environ.get("HF_ENDPOINT", "huggingface.co"))
    print("目标 : %s" % dst)
    print("-" * 60)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("当前 python 没有 huggingface_hub。请用 ComfyUI 自带的 python：")
        print(r'  "E:\soft\ComfyUI_windows_portable\python_embeded\python.exe" %s' % __file__)
        sys.exit(1)

    path = hf_hub_download(repo_id=repo, filename=fname, local_dir=dst)
    print("-" * 60)
    print("下载完成：%s" % path)
    print("重启 ComfyUI 后，在 Hamm 节点的 local_model 下拉里就能选到它。")


if __name__ == "__main__":
    main()

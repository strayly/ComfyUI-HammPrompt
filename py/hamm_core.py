# -*- coding: utf-8 -*-
"""HammPrompt core: 模板加载 / 中英映射 / LLM 智能扩写 / 提示词拼装"""

import base64
import io
import json
import os
import re
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_CACHE = {}


def load(name):
    if name not in _CACHE:
        with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
            _CACHE[name] = json.load(f)
    return _CACHE[name]


def options(cat, group="t2i"):
    """返回某个模板类别的中文选项列表"""
    data = load("t2i_templates.json" if group == "t2i" else "video_templates.json")
    return [x["zh"] for x in data[cat]]


def value(cat, zh, group="t2i"):
    """中文选项 -> 英文片段"""
    if not zh:
        return ""
    data = load("t2i_templates.json" if group == "t2i" else "video_templates.json")
    for x in data[cat]:
        if x["zh"] == zh:
            return x["en"]
    return ""


def desc(cat, zh, group="t2i"):
    """中文选项 -> 中文考据描述（服装/妆容/环境限定）。

    这份描述是「结构化预览框」和「喂给大模型的 brief」共用的唯一数据源：
    框里看到的词 = 真正送进 LLM 的词。没有 desc 的类别返回空串（降级为只显示选项名）。
    """
    if not zh:
        return ""
    data = load("t2i_templates.json" if group == "t2i" else "video_templates.json")
    for x in data[cat]:
        if x["zh"] == zh:
            return x.get("desc", "")
    return ""


def desc_all(cat, group="t2i"):
    """返回某类别的 {中文选项: 中文描述}，供前端预览框展开用"""
    data = load("t2i_templates.json" if group == "t2i" else "video_templates.json")
    out = {}
    for x in data.get(cat, []):
        if x.get("desc"):
            out[x["zh"]] = x["desc"]
    return out


# ---------------------------------------------------------------- 年代的单行写法
# 结构化框里年代这一行标签已经叫「时代背景」，再写「人物与场景符合唐代背景」整句就是重复。
# 显示只用简洁写法（唐 -> 唐代、50年代 -> 1950年代），这份写法和选项名的对应关系
# 由下面两个函数一处维护：前端拿去显示，后端拿去反查（否则 value("era", ...) 查不到
# 选项，英文片段会整段丢掉 —— _strip_paren 当初就是为这个坑加的）。
_ERA_HEAD = "人物与场景符合"
_ERA_TAIL = "背景"


def _era_templates(group="t2i"):
    data = load("t2i_templates.json" if group == "t2i" else "video_templates.json")
    return data.get("era", []) or []


def era_display(zh, group="t2i"):
    """年代选项名 -> 结构化框里的简洁写法。没有 desc 时原样返回选项名。"""
    if not zh:
        return ""
    d = desc("era", zh, group)
    if not d:
        return zh
    s = d[len(_ERA_HEAD):] if d.startswith(_ERA_HEAD) else d
    if s.endswith(_ERA_TAIL):
        s = s[: -len(_ERA_TAIL)]
    return s.strip() or zh


def era_display_all(group="t2i"):
    """返回 {选项名: 简洁写法}，供前端显示（前端不再自己拼规则，避免两边跑偏）"""
    out = {}
    for x in _era_templates(group):
        zh = x.get("zh")
        if zh and zh != "不指定":
            out[zh] = era_display(zh, group)
    return out


def era_to_zh(text, group="t2i"):
    """把结构化框里的简洁写法（唐代）反查回选项名（唐）。

    认不出来就原样返回 —— 宁可让拼装少一段，也不能把「唐代」当选项名丢进模板查询。
    """
    t = (text or "").strip()
    if not t:
        return t
    names = [x["zh"] for x in _era_templates(group) if x.get("zh")]
    if t in names:
        return t
    for zh in names:
        if era_display(zh, group) == t:
            return zh
    return t


# ---------------------------------------------------------------- zh -> en
# 自由文本里的常见中文词，按长度优先替换。覆盖不到的中文会原样保留，
# 此时建议打开 LLM 智能扩写。
ZH2EN = {
    # 人物 / 身份
    "少女": "a young woman", "女子": "a woman", "青年女子": "a young woman",
    "少年": "a young man", "男子": "a man", "中年男子": "a middle-aged man",
    "老者": "an elderly man", "老人": "an old man", "孩童": "a child",
    "仕女": "a court lady", "仙子": "a celestial maiden", "剑客": "a swordsman",
    "侠客": "a wandering swordsman", "书生": "a young scholar", "将军": "a general",
    "皇帝": "an emperor", "宫女": "a palace maid", "僧人": "a monk",
    "道士": "a Taoist priest", "船夫": "a boatman", "商人": "a merchant",
    "舞者": "a dancer", "琴师": "a guqin player", "猎人": "a hunter",
    "猫": "a cat", "白猫": "a white cat", "鹤": "a crane", "鹿": "a deer",
    "马": "a horse", "鱼": "a fish", "蝴蝶": "a butterfly", "鸟": "a bird",
    # 外貌 / 服装
    "长发": "long black hair", "黑长发": "long black hair", "短发": "short hair",
    "白发": "white hair", "丸子头": "hair in a topknot bun", "发髻": "hair in an updo bun",
    "发带": "a hair ribbon", "玉簪": "a jade hairpin", "木簪": "a wooden hairpin",
    "步摇": "a dangling hair ornament", "面纱": "a thin veil",
    "汉服": "hanfu", "白色汉服": "a white hanfu", "青色汉服": "a cyan hanfu",
    "红色汉服": "a red hanfu", "古装": "traditional Chinese costume",
    "唐装": "Tang style costume", "旗袍": "a qipao", "长裙": "a long dress",
    "广袖": "wide flowing sleeves", "披风": "a cloak", "斗篷": "a hooded cloak",
    "刺绣": "embroidered patterns", "云纹": "cloud motif embroidery",
    "盔甲": "armor", "布鞋": "cloth shoes", "玉佩": "a jade pendant",
    "油纸伞": "an oil-paper umbrella", "纸伞": "a paper umbrella",
    "折扇": "a folding fan", "团扇": "a round silk fan", "灯笼": "a paper lantern",
    "古琴": "a guqin", "笛子": "a bamboo flute", "长剑": "a long sword",
    "书卷": "a scroll", "书信": "a letter", "茶具": "a tea set",
    "酒壶": "a wine pot", "香炉": "an incense burner",
    # 动作
    "站立": "standing", "静立": "standing still", "行走": "walking",
    "缓步": "walking slowly", "奔跑": "running", "回眸": "glancing back",
    "回头": "turning the head back", "低头": "lowering the head",
    "抬头": "lifting the head", "微笑": "smiling faintly", "浅笑": "with a faint smile",
    "大笑": "laughing", "哭泣": "crying", "凝望": "gazing steadily",
    "远望": "gazing into the distance", "闭眼": "with eyes closed",
    "睁眼": "opening the eyes", "抚琴": "playing a guqin",
    "写字": "writing with a brush", "斟茶": "pouring tea", "品茶": "sipping tea",
    "舞剑": "performing a sword dance", "旋转": "spinning", "挥手": "waving a hand",
    "作揖": "bowing with hands folded", "撑伞": "holding an umbrella",
    "撑船": "poling a boat", "拾起": "picking up", "放下": "putting down",
    "坐下": "sitting down", "倚靠": "leaning against", "躺": "lying down",
    "跳舞": "dancing", "练剑": "practicing sword forms", "看书": "reading a book",
    "写信": "writing a letter", "梳妆": "doing her makeup", "照镜子": "looking into a mirror",
    "转身": "turning around", "走近": "walking closer", "走远": "walking away",
    "伸手": "extending one hand", "抚摸": "stroking gently", "抱": "holding",
    "举": "raising", "举起": "lifting up", "投掷": "throwing",
    "跳跃": "jumping", "飞跃": "leaping through the air", "打斗": "fighting",
    "对视": "looking into each other's eyes", "牵手": "holding hands",
    # 场景 / 环境
    "江南": "a Jiangnan water town", "古镇": "an ancient town", "水乡": "a canal town",
    "石桥": "a stone bridge", "拱桥": "an arched stone bridge", "长巷": "a narrow alley",
    "庭院": "a courtyard", "回廊": "a covered corridor", "亭子": "a pavilion",
    "竹林": "a bamboo forest", "枫林": "a maple forest", "桃林": "a peach grove",
    "梅园": "a plum blossom garden", "荷塘": "a lotus pond", "湖边": "by a lake",
    "河边": "by a river", "海边": "by the sea", "山间": "among mountains",
    "山顶": "on a mountain summit", "悬崖": "on a cliff", "云海": "a sea of clouds",
    "沙漠": "a desert", "雪地": "a snowfield", "麦田": "a wheat field",
    "花田": "a flower field", "草原": "a grassland", "森林": "a forest",
    "皇宫": "an imperial palace", "大殿": "a palace hall", "寺庙": "a temple",
    "书斋": "a scholar's study", "茶馆": "a teahouse", "酒楼": "a tavern",
    "市集": "a bustling market street", "码头": "a dock", "船上": "on a boat",
    "城楼": "a city gate tower", "城墙": "a city wall", "街道": "a street",
    "室内": "indoors", "窗边": "by a window", "屋顶": "on a rooftop",
    "城市": "a modern city", "夜晚": "at night", "街头": "on a street corner",
    "天台": "on a rooftop", "地铁": "a subway carriage", "咖啡馆": "a cafe",
    "图书馆": "a library", "废弃工厂": "an abandoned factory", "废墟": "ruins",
    # 光线 / 天气 / 时段
    "清晨": "early morning", "早晨": "morning", "正午": "midday",
    "黄昏": "dusk", "傍晚": "at dusk", "夜晚": "at night", "深夜": "late at night",
    "阴天": "overcast", "雨天": "on a rainy day", "下雨": "rain falling",
    "小雨": "light rain", "暴雨": "heavy rain", "雪": "snow falling",
    "下雪": "snow falling", "雾": "thin mist", "薄雾": "thin mist",
    "浓雾": "dense fog", "晴天": "clear weather", "多云": "cloudy",
    "阳光": "sunlight", "暖光": "warm light", "冷光": "cool light",
    "逆光": "backlight", "侧光": "side light", "柔光": "soft light",
    "月光": "moonlight", "烛光": "candlelight", "灯光": "lantern glow",
    "霓虹": "neon light", "聚光灯": "spotlight", "逆光轮廓": "rim light",
    # 情绪 / 氛围
    "孤独": "lonely", "宁静": "serene", "忧伤": "melancholic",
    "喜悦": "joyful", "愤怒": "angry", "惊讶": "surprised",
    "温柔": "gentle", "冷峻": "stern and cold", "神秘": "mysterious",
    "庄严": "solemn", "浪漫": "romantic", "紧张": "tense",
    "治愈": "healing and warm", "空灵": "ethereal", "壮观": "grand and epic",
    "怀旧": "nostalgic", "禅意": "zen", "烟火气": "lively everyday life",
    # 画质 / 镜头
    "高清": "high detail", "超清": "ultra detailed", "写实": "photorealistic",
    "电影感": "cinematic", "胶片": "film grain", "浅景深": "shallow depth of field",
    "锐利": "sharp focus", "柔和": "soft rendering", "细腻": "fine texture",
    "特写": "close-up", "近景": "medium close-up", "中景": "medium shot",
    "远景": "wide shot", "全景": "full body shot", "大远景": "extreme wide shot",
    "仰拍": "low angle shot", "俯拍": "high angle shot", "平视": "eye level",
    "侧面": "side profile", "背影": "from behind", "剪影": "silhouette",
    "景深": "depth of field", "广角": "wide angle lens", "长焦": "telephoto lens",
    # 颜色 / 虚词 / 常用补词
    "白衣": "in white", "青衣": "in cyan", "红衣": "in red", "黑衣": "in black",
    "黄衣": "in yellow", "紫衣": "in purple", "绿衣": "in green",
    "走过": "walking across", "走向": "walking toward", "靠近": "approaching",
    "等待": "waiting", "等人": "waiting for someone", "缓缓": "slowly",
    "慢慢": "slowly", "突然": "suddenly", "轻轻": "gently", "静静": "quietly",
    "渐渐": "gradually", "她": "she", "他": "he", "它": "it",
    "乌篷船": "a black-awning wooden boat", "小船": "a small wooden boat",
    "船": "a boat", "桥头": "the end of the bridge", "桥上": "on the bridge",
    "水面": "the water surface", "望向": "looking toward", "看向": "looking at",
    "露出": "breaking into", "撑着": "holding up", "撑": "holding up",
    "一个": "a", "一位": "a", "一名": "a", "古代": "ancient",
    "中国": "Chinese", "古风": "Chinese classical style", "现代": "modern",
    "画面": "the frame", "镜头": "the shot", "背景": "the background",
    "前景": "the foreground", "侧面光": "side light",
    "站": "standing", "抬起头": "lifting the head", "走": "walking",
    "跑": "running", "看": "looking", "说": "saying", "笑": "smiling",
    "听": "listening", "站": "standing", "风": "wind", "雨滴": "raindrops",
    # 颜色
    "白色": "white", "黑色": "black", "红色": "red", "青色": "cyan",
    "蓝色": "blue", "绿色": "green", "金色": "gold", "紫色": "purple",
    "粉色": "pink", "灰色": "grey", "米色": "beige", "墨色": "ink black",
}

# 人物类词条：命中后会被提到最前面，避免出现 "white hanfu a young woman" 这种粘连
PERSON_KEYS = {
    "少女", "青年女子", "女子", "少年", "男子", "中年男子", "老者", "老人", "孩童",
    "仕女", "仙子", "剑客", "侠客", "书生", "将军", "皇帝", "宫女", "僧人", "道士",
    "船夫", "商人", "舞者", "琴师", "猎人",
    "猫", "白猫", "鹤", "鹿", "马", "鱼", "蝴蝶", "鸟",
}

_ZH_KEYS = sorted(ZH2EN.keys(), key=len, reverse=True)
_HAS_ZH = re.compile(r"[\u4e00-\u9fff]")


def zh2en(text):
    """词典级中译英：人物类提前，其余按长度优先替换，未命中的中文原样保留"""
    if not text or not _HAS_ZH.search(text):
        return (text or "").strip()
    out = text
    # 0) 中文标点先归一成英文标点，否则英文提示词里会夹着「，」
    for a, b in (("，", ", "), ("、", ", "), ("；", "; "), ("：", ": "),
                 ("。", ". "), ("！", "! "), ("？", "? "), ("（", " ("), ("）", ") ")):
        out = out.replace(a, b)
    # 1) 先把「人物」抽出来放最前
    person = ""
    for k in sorted(PERSON_KEYS, key=len, reverse=True):
        if k in out:
            person = ZH2EN.get(k, k)
            out = out.replace(k, " ", 1)
            break
    # 2) 其余逐条替换（两侧留空格，避免中英粘连）
    for k in _ZH_KEYS:
        if k in out:
            out = out.replace(k, " %s " % ZH2EN[k])
    # 3) 去掉中文虚词残留
    for p in PARTICLES:
        out = out.replace(p, " ")
    out = re.sub(r"\s+", " ", out).strip(" ,，、")
    out = re.sub(r"\s+([,;.!?])", r"\1", out)   # 标点前不留空格
    if person:
        out = (person + " " + out).strip(" ,")
    out = re.sub(r"\s+", " ", out).strip(" ,，;.")
    return out


PARTICLES = ["的", "了", "着", "和", "与", "在", "中", "里", "上", "下", "把", "被", "是", "有"]


def leftover_zh(s):
    """返回仍未翻译的中文片段，用于提醒用户开 LLM"""
    if not s:
        return ""
    segs = re.findall(r"[\u4e00-\u9fff]+", s)
    return "、".join(sorted(set(segs)))


# ---------------------------------------------------------------- LLM
def llm_chat(base_url, model, api_key, system, user, timeout=90, temperature=0.8,
             image_b64=None):
    """OpenAI 兼容 /chat/completions，零第三方依赖。

    传 image_b64（PNG base64）即走多模态：user 消息变成 [text, image_url] 数组，
    兼容 OpenAI / Zhipu(GLM-4V) / 多数 OpenAI 兼容视觉端点。
    """
    key = api_key or os.environ.get("LLM_API_KEY", "")
    if not key:
        raise RuntimeError("未填 API Key（节点 llm_api_key 或环境变量 LLM_API_KEY）")
    url = base_url.rstrip("/") + "/chat/completions"
    user_content = user
    if image_b64:
        user_content = [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,%s" % image_b64}},
        ]
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


# 关键：这里必须明确要求「扩写 / 补全细节」，否则小模型只会把 brief 逐词直译一遍，
# 用户感受到的就是「开了 LLM 但提示词没变丰富」。
# 同时要求模型一次返回「中文扩写 + 英文扩写」两段：中文那版是给用户看/直接用的，
# 以前只返回英文，用户就会觉得「中文提示词根本没变」。
SYS_T2I = (
    "You are a senior prompt engineer for image diffusion models (Flux / Qwen-Image / SDXL). "
    "The user gives a brief of named fields like subject=... | era=... | scene=... | "
    "action=... | shot=... | lighting=... | mood=... | style=... (empty fields are "
    "omitted).\n"
    "EXPAND that brief into a RICHER version in BOTH Chinese and English. Expanding means "
    "adding concrete plausible visual detail, not reordering the same words.\n"
    "Add:\n"
    "- subject: age, hairstyle, fabric and pattern of the costume, facial expression, "
    "hand pose, props\n"
    "- environment: foreground / midground / background layers, weather, time of day, "
    "surface textures\n"
    "- lighting: direction, hard or soft, colour temperature, rim light, haze or dust in "
    "the air\n"
    "- camera: lens focal length, aperture, depth of field, camera angle, composition\n"
    "Rules: keep every element of the brief and never contradict it; do not invent a "
    "different person or a different place; do NOT mention golden hour, sunset, sunrise, "
    "neon or any light source the brief did not ask for; no text, no watermark, no logo, "
    "no signature.\n"
    "ERA RULE (very important when the era field has a value): treat it as a historical "
    "period tag and do a period-accurate research pass. Hairstyle and coiffure, makeup, "
    "the actual garment types worn in THAT period (use their proper names), architecture, "
    "furniture, props and street scene must ALL belong to that single era. Never mix "
    "garments from different dynasties, never add modern items, and never mistake later "
    "garments for the era (e.g. the qipao/cheongsam is a Republican-era garment, Ming "
    "women wore aoqun, Song women wore beizi). State the period-accurate details "
    "explicitly in the output rather than just naming the dynasty. BUT the era tag only "
    "constrains people (costume, hairstyle, makeup) and period flavour of props - it "
    "must NOT override the scene field: if the brief sets a location (wilderness, "
    "forest, indoor, beach...), keep that location as-is and adapt the era details to "
    "it; never invent era-specific buildings or landmarks the brief did not ask for.\n"
    "MEDIUM RULE (very important): style, mood and era tags describe the THEME, wardrobe "
    "and setting only - the result must always read as a photorealistic live-action "
    "photograph of real people in a real place, unless the quality tag explicitly names a "
    "non-photographic medium (painting / illustration / ink wash / 3D render). Never add "
    "words like painting, artwork, ink wash, guohua, anime, illustration, 画, 国画, 水墨, "
    "仕女图, 山水 to the output; historical eras (Tang, Song, Ming...) must be shown "
    "through period-accurate architecture, costume and props inside a real-world "
    "photographic scene.\n"
    "ZH: 80-140 汉字，用「，」分隔的短语，不要整句、不要分点、不要解释。\n"
    "EN: 70-110 words, comma-separated phrases, no full sentences, no bullet points.\n"
    "Reply in EXACTLY this format and nothing else:\n"
    "[ZH]\n<Chinese prompt>\n[EN]\n<English prompt>"
)

USER_T2I = ("Brief (subject | scene | action | camera | lighting | mood | style | extra):\n"
            "%s\n\nExpand it into the rich English image prompt now.")

# 分镜批量用：多一个「固定角色」占位（注意占位符数量必须和传参一致，否则 % 格式化会炸）
USER_T2I_SHOT = ("Keep this fixed character throughout, never change their identity or "
                 "costume: %s\nShot content: %s\n\n"
                 "Expand it into the rich English image prompt now.")

SYS_VIDEO = (
    "You are a senior prompt engineer for MiniMax H3 video generation. "
    "The user gives a short Chinese brief. Write the official H3 format with these exact "
    "fields: integrated_multimodal_description, overall_soundscape, non_diegetic_music. "
    "EXPAND the brief rather than translating it word for word: describe how the action "
    "evolves over time, what the subject's body and clothing do, layered environment "
    "detail, and lighting changes. "
    "Inside the description write timeline shots as [Shot 1] ... [Shot 2] At 00:0X.000, ... "
    "Describe camera motion as a natural action: 'The camera pushes in with small amplitude "
    "at slow speed ...'. Keep the requested total duration. "
    "Output English only, no explanations."
)

# ---------------- 兜底：模型只吐了英文、没给中文段时，单独补跑一次纯中文扩写
# 视频分支尤其常见：H3 正文很长，模型一口气写完就超了 max_tokens，
# 根本没机会写 [ZH] 段。这时宁可多花一次短调用，也要把中文扩写拿到手。
SYS_ZH_ONLY = (
    "你是一名中文图像提示词工程师。用户给你一份简短的中文要素清单，"
    "请把它扩写成一段更丰富、更具体的中文提示词：补全人物外貌与服饰、环境的前中后景层次、"
    "光线的方向与色温、镜头的焦段与构图等可见细节。\n"
    "要求：保留原始要素，不得改变人物与地点；不要添加用户没要求的特殊光源"
    "（金色时刻、日落、霓虹等）；不要分点、不要解释、不要引号；不要出现"
    "「这张图」「画面中」之类的说明文字。\n"
    "用「，」分隔的短语，长度 80-160 汉字。只输出这一段中文。"
)

SYS_ZH_ONLY_VIDEO = (
    "你是一名中文视频提示词工程师。用户给你一份简短的视频要素清单，"
    "请把它扩写成一段更丰富、更具体的中文视频描述：写清动作如何随时间发展、"
    "人物身体与衣物的变化、环境的前中后景层次、光线与色温的变化，"
    "并保留原来的运镜方式与分镜顺序。\n"
    "要求：保留原始要素，不得改变人物与地点；不要添加用户没要求的特殊光源；"
    "不要分点、不要解释、不要引号。\n"
    "用「，」分隔的短语，长度 100-180 汉字。只输出这一段中文。"
)


def _expand_zh(cfg, brief, video=False, max_tokens=448):
    """单独补一次中文扩写。失败返回空串，绝不抛异常打断出图。"""
    try:
        got = clean_llm_prompt(cfg.run(
            SYS_ZH_ONLY_VIDEO if video else SYS_ZH_ONLY,
            "要素：\n%s\n\n请输出扩写后的中文提示词。" % brief,
            max_tokens=max_tokens, temperature=0.6))
        if got and _HAS_ZH.search(got):
            return _one_line_zh(got)
    except Exception:
        pass
    return ""


# ---------------- LLM 状态文案（结构预览里会显示，方便一眼确认扩写到底跑没跑）
LLM_STATUS_OFF = "－ 未启用（纯离线词典拼装）"
LLM_STATUS_OK = "✓ 已智能扩写"


def clean_llm_prompt(text):
    """清掉模型常见的包装：```围栏、引号、EN: 前缀、多余空行"""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    for pref in ("EN:", "EN：", "English:", "English：", "Prompt:", "Prompt：",
                 "最终提示词：", "提示词：", "英文提示词："):
        if s.upper().startswith(pref.upper()):
            s = s[len(pref):].strip()
            break
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("\"", "'", "“", "‘"):
        s = s[1:-1].strip()
    return s.strip()


_ZH_HEAD = re.compile(r"^\s*(中文|汉化|ZH|Chinese)\s*[：:]\s*", re.I)
_EN_HEAD = re.compile(r"^\s*(英文|English|EN)\s*[：:]\s*", re.I)


def _one_line_zh(s):
    """中文扩写块压成一行：模型爱换行/分点，中文提示词里换行和「- 」都是噪声"""
    s = re.sub(r"[\r\n]+", "，", s or "")
    s = re.sub(r"(^|，)\s*[-*·•]\s*", r"\1", s)     # 去掉行首的列表符号
    s = re.sub(r"^[\s，,、;；]+", "", s)
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip("，,。；;、 \t")


def split_bilingual(text):
    """解析模型返回的 [ZH] / [EN] 双语块，返回 (zh, en)。

    容错：模型没按格式输出时，把整段当成英文、中文留空，
    让上层决定要不要回退离线拼装。
    """
    if not text:
        return "", ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s).strip()
    m = re.search(r"\[\s*EN\s*\]\s*[：:]?", s, re.I)
    if not m:
        return "", clean_llm_prompt(s)
    zh_raw, en_raw = s[:m.start()], s[m.end():]
    zh_raw = re.sub(r"\[\s*ZH\s*\]\s*[：:]?", "", zh_raw, flags=re.I)
    zh_raw = re.sub(r"^\s*\[\s*ZH\s*\]?\s*$", "", zh_raw, flags=re.I | re.M)
    zh_raw = _ZH_HEAD.sub("", zh_raw)
    en_raw = _EN_HEAD.sub("", en_raw)
    return _one_line_zh(clean_llm_prompt(zh_raw)), clean_llm_prompt(en_raw)


# ---------------------------------------------------------------- 图生提示词（视觉）
# 把 ComfyUI 的 IMAGE 张量（torch.Tensor / numpy，shape (B,H,W,3)，0~1）转成 base64 PNG。
# 取首帧；ComfyUI 运行时一定有 PIL，所以这里直接 lazy import。
def tensor_to_base64(image):
    import numpy as np
    if hasattr(image, "detach"):
        try:
            image = image.detach().cpu().numpy()
        except Exception:
            image = image.cpu().numpy()
    arr = np.asarray(image, dtype="float32")
    if arr.ndim == 4:
        arr = arr[0]
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype("uint8")
    from PIL import Image
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# 视觉模型把图片识别成结构化要素：每行「中文标签：内容」，再解析回字段键。
# 同时接受中文/英文标签，兼容国内外视觉模型。
_VISION_LABEL_MAP = {
    "主体": "subject", "subject": "subject", "人物": "subject",
    "风格": "style", "style": "style",
    "场景": "scene", "scene": "scene", "环境": "scene",
    "动作": "action", "action": "action", "行为": "action",
    "景别": "shot", "shot": "shot", "镜头": "shot",
    "光线": "lighting", "lighting": "lighting", "光照": "lighting",
    "氛围": "mood", "mood": "mood", "情绪": "mood",
    "画质": "quality", "quality": "quality",
    "年代": "era", "era": "era", "时期": "era",
}


def _parse_vision_fields(text):
    """解析视觉模型返回的「标签：内容」行，返回 {字段键: 内容}"""
    out = {}
    if not text:
        return out
    for line in (text or "").split("\n"):
        s = line.strip()
        if not s:
            continue
        if "：" in s:
            k, _, v = s.partition("：")
        elif ":" in s:
            k, _, v = s.partition(":")
        else:
            continue
        k = k.strip().lower()
        v = v.strip().strip("，,。;；").strip()
        if not k or not v:
            continue
        key = _VISION_LABEL_MAP.get(k)
        if key and key not in out:
            out[key] = v
    return out


SYS_VISION_T2I = (
    "你是一名中文图像提示词工程师，擅长把图片转成用于 AI 生图的要素清单。"
    "请仔细观察图片，用中文输出以下要素（图片里没有的就省略该行，不要编造）：\n"
    "主体：\n风格：\n场景：\n动作：\n景别：\n光线：\n氛围：\n画质：\n年代：\n"
    "要求：每行一个，格式严格为「中文标签：内容」；内容用「，」分隔的短语；"
    "不要解释、不要整句描述、不要引号；人物要写清年龄/发型/服饰/表情/道具；"
    "只描述画面可见内容，不要脑补画外信息。"
)

SYS_VISION_VIDEO = (
    "你是一名中文视频提示词工程师。请仔细观察图片，用中文输出以下要素（图片里没有的就省略该行）：\n"
    "主体：\n风格：\n场景：\n动作：\n运镜：\n光线：\n氛围：\n画质：\n年代：\n"
    "要求：每行一个，格式严格为「中文标签：内容」；内容用「，」分隔的短语；"
    "不要解释、不要整句、不要引号；只描述画面可见内容。"
)


def vision_describe(cfg, image_b64, group="t2i", max_tokens=512, temperature=0.4):
    """把图片丢给视觉模型，返回解析后的 {字段键: 内容} 字典"""
    sys = SYS_VISION_VIDEO if group == "video" else SYS_VISION_T2I
    raw = cfg.run_vision(sys, "请描述这张图片，按格式生成要素清单。",
                         image_b64, max_tokens, temperature)
    return _parse_vision_fields(raw)


# ---------------------------------------------------------------- LLM 调度
class LLMConfig(object):
    """统一的 LLM 调度配置：关 / 仅本地 / 仅 API"""

    OFF = "关（纯离线词典）"
    LOCAL = "本地模型（推荐）"
    API = "在线 API"

    MODES = [OFF, LOCAL, API]

    def __init__(self, mode=OFF, backend="gguf", path="", gpu_layers=-1, ctx=4096,
                 max_tokens=384, temperature=0.7, keep_loaded=False,
                 base_url="", model="", api_key="", timeout=90,
                 vision_path="", vision_mmproj="", vision_backend="gguf"):
        self.mode = mode or self.OFF
        self.backend = backend            # "gguf" | "hf"
        self.path = path                  # 本地文本模型路径
        self.gpu_layers = gpu_layers      # llama.cpp GPU offload 层数，-1 = 全部
        self.ctx = ctx
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.keep_loaded = keep_loaded    # True = 常驻显存（多镜连续用更快，但占显存）
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        # 本地视觉模型（VLM）：读图用，独立于文本模型（文本模型看不了图）
        self.vision_path = vision_path          # VLM GGUF 路径
        self.vision_mmproj = vision_mmproj      # mmproj（图片塔）路径；空 = 自动探测
        self.vision_backend = vision_backend

    @property
    def enabled(self):
        return self.mode != self.OFF

    def can_local(self):
        return self.mode == self.LOCAL and bool(self.path)

    def can_api(self):
        return self.mode == self.API and bool(self.base_url and self.model)

    def can_local_vision(self):
        """配置了本地 VLM（LLM 未关 + 选了视觉模型）即可本地看图"""
        return self.mode != self.OFF and bool(self.vision_path)

    def _order(self):
        if self.mode == self.LOCAL:
            return ["local"]
        if self.mode == self.API:
            return ["api"]
        return []

    def run(self, system, user, max_tokens=None, temperature=None):
        """按优先级跑一次推理，成功返回文本，全失败抛 RuntimeError（带上各路原因）"""
        if not self.enabled:
            raise RuntimeError("LLM 未启用")
        temp = self.temperature if temperature is None else temperature
        errs = []
        for kind in self._order():
            if kind == "local":
                if not self.can_local():
                    errs.append("本地：未选择模型")
                    continue
                try:
                    try:
                        from . import hamm_local_llm as L
                    except ImportError:
                        import hamm_local_llm as L
                    return L.run(self.backend, self.path, system, user,
                                 self.gpu_layers, self.ctx,
                                 int(max_tokens or self.max_tokens), temp, 0.9,
                                 self.keep_loaded)
                except Exception as e:
                    errs.append("本地：%s" % str(e)[:110])
            else:
                if not self.can_api():
                    errs.append("API：未填 base_url/model")
                    continue
                try:
                    return llm_chat(self.base_url, self.model, self.api_key,
                                    system, user, self.timeout, temp)
                except Exception as e:
                    errs.append("API：%s" % str(e)[:110])
        raise RuntimeError(" ; ".join(errs))

    def run_vision(self, system, user, image_b64, max_tokens=None, temperature=None):
        """多模态调用：把图片（base64 PNG）连同文本发给视觉模型。

        **本地优先**：配了本地 VLM（local_vision_model + mmproj）就走 llama.cpp 本地推理，
        完全离线、不耗 API 额度；本地失败直接报错（不静默回退 API，避免偷偷烧额度）。
        没配本地 VLM 时，才走「在线 API」视觉模型（需 llm_mode=在线 API）。
        """
        if not self.enabled:
            raise RuntimeError(
                "LLM 未启用：开「图生提示词」请把 llm_mode 设为「本地模型（推荐）」，"
                "并选择 local_vision_model（本地 VLM，如 Qwen2.5-VL GGUF + mmproj），"
                "或填在线 API 视觉模型")
        temp = self.temperature if temperature is None else temperature
        # 1) 本地视觉模型优先（配了就本地，失败直接报，不回退 API）
        if self.can_local_vision():
            try:
                try:
                    from . import hamm_local_llm as L
                except ImportError:
                    import hamm_local_llm as L
                return L.run_vision(self.vision_backend, self.vision_path, self.vision_mmproj,
                                    system, user, image_b64,
                                    self.gpu_layers, self.ctx,
                                    int(max_tokens or 512), temp, self.keep_loaded)
            except Exception as e:
                raise RuntimeError("本地视觉：%s" % str(e)[:140])
        # 2) 没配本地 VLM -> 在线 API（需 mode=在线 API 且填了端点）
        if self.can_api():
            try:
                return llm_chat(self.base_url, self.model, self.api_key,
                                system, user, self.timeout, temp, image_b64=image_b64)
            except Exception as e:
                raise RuntimeError("视觉 API 调用失败：%s" % str(e)[:140])
        raise RuntimeError(
            "未配置可用视觉模型：请把 llm_mode 设为「本地模型（推荐）」并选择 "
            "local_vision_model（本地 VLM + mmproj），或切「在线 API」并填视觉模型")

    def release(self):
        """显式释放本地模型显存"""
        try:
            try:
                from . import hamm_local_llm as L
            except ImportError:
                import hamm_local_llm as L
            L.unload_all()
        except Exception:
            pass


def _llm_output_ok(text, output_lang):
    """校验 LLM 结果能不能用。

    英文/中英对照模式下，如果输出里还有成段中文，说明这个模型没听懂指令
    （小模型常见：直接把中文复读一遍）。这种情况宁可用回词典结果，
    也不要把带中文的「英文提示词」喂给生图模型。
    """
    if not text or not text.strip():
        return False
    if output_lang == "中文":
        return True
    segs = re.findall(r"[\u4e00-\u9fff]+", text)
    return len("".join(segs)) < 6


def _llm_or_none(llm, use_llm, base_url, model, api_key):
    """兼容旧调用：没传 LLMConfig 但 use_llm=True 时，退化成在线 API"""
    if llm is not None:
        return llm
    if use_llm:
        return LLMConfig(mode=LLMConfig.API, base_url=base_url,
                         model=model, api_key=api_key)
    return None


def nz(v):
    """「不指定」= 没选（下拉的占位值）：按空处理。

    前端结构化提示词框早就把这种行整行丢了，后端必须对齐 —— 否则中文拼装 /
    LLM brief 里会冒出一串「不指定，不指定…」（用户实测：全未指定时提示词里 5 个）。
    英文路本来没事（value() 查表取 en，「不指定」对应空串），只有中文拼装和 brief 会泄露。
    """
    s = (v or "").strip()
    return "" if s == "不指定" else s


def merge(parts, sep=", "):
    return sep.join([p.strip(" ,，") for p in parts
                     if p and p.strip(" ,，") and p.strip(" ,，") != "不指定"])


def append_missing(base, extra):
    """把 extra 里 base 还没提到的片段补到末尾，避免和模型已经写过的画质词重复"""
    b = (base or "").strip().rstrip(" .,;，。")
    if not extra:
        return b
    low = b.lower()
    add = []
    for piece in str(extra).split(","):
        p = piece.strip()
        if not p or p.lower() in low:
            continue
        add.append(p)
    return merge([b] + add) if add else b


# ---------------------------------------------------------------- 文生图
def build_t2i(subject, style, scene, action, shot, lighting, mood, quality,
              extra_pos, extra_neg, neg_preset, output_lang,
              use_llm=False, base_url="", model="", api_key="", llm=None, status=None,
              era=""):
    """返回 (out_pos, out_neg, en, zh)。

    status 是可选的 dict，函数会往里写 status["llm"]，供结构预览显示扩写到底跑没跑。
    注意：LLM 失败绝不能把报错文字混进 en —— 那段英文会直接喂给生图模型。
    """
    cfg = _llm_or_none(llm, use_llm, base_url, model, api_key)
    st = status if status is not None else {}
    # 「不指定」= 没选：统一按空处理（brief / 中文拼装都不能留这个词）
    (subject, style, scene, action, shot, lighting, mood, quality,
     extra_pos, extra_neg, neg_preset, era) = [
        nz(x) for x in (subject, style, scene, action, shot, lighting, mood,
                        quality, extra_pos, extra_neg, neg_preset, era)]
    # 年代带考据描述一起喂给 LLM：框里看到的词 = 送进模型的词（ERA RULE 才接得住）
    era_brief = era
    if era:
        d = desc("era", era)
        if d:
            era_brief = "%s（%s）" % (era, d)
    # 命名格式 brief：LLM 必须知道每个值的语义（尤其 era），裸值平铺会被忽略
    _named = [("subject", subject), ("era", era_brief), ("scene", scene),
              ("action", action), ("shot", shot), ("lighting", lighting),
              ("mood", mood), ("style", style), ("extra", extra_pos)]
    brief = " | ".join("%s=%s" % (k, v) for k, v in _named if v)

    en_parts = [
        zh2en(subject),
        value("era", era),
        value("scene", scene),
        value("action", action),
        value("shot", shot),
        value("lighting", lighting),
        value("mood", mood),
        value("style", style),
        value("quality", quality),
        zh2en(extra_pos),
    ]
    en = merge(en_parts)
    zh = merge([p for p in [subject, era, scene, action, lighting, mood] if p] + [style, quality])

    if cfg and cfg.enabled:
        try:
            got_zh, got_en = split_bilingual(
                cfg.run(SYS_T2I, USER_T2I % brief, max_tokens=768))
            en_ok = bool(got_en) and _llm_output_ok(got_en, "英文（推荐）")
            zh_ok = bool(got_zh) and bool(_HAS_ZH.search(got_zh))
            if not zh_ok:                       # 模型只给了英文：单独补一次中文扩写
                more = _expand_zh(cfg, brief)
                if more:
                    got_zh, zh_ok = more, True
            if en_ok:
                en = append_missing(got_en, value("quality", quality))
            if zh_ok:
                zh = got_zh          # 中文也换成扩写版，而不是原始下拉词拼接
            if en_ok and zh_ok:
                st["llm"] = LLM_STATUS_OK
            elif en_ok or zh_ok:
                st["llm"] = "✓ 已智能扩写（仅%s段）" % ("英文" if en_ok else "中文")
            else:
                st["llm"] = "⚠ 模型输出不合格，已回退离线拼装"
        except Exception as e:  # LLM 失败不阻断出图，只记录状态
            st["llm"] = "✗ 失败：%s" % str(e)[:130]
    else:
        st["llm"] = LLM_STATUS_OFF

    negative = merge([value("negative", neg_preset), zh2en(extra_neg)])

    if output_lang == "中文":
        out_pos, out_neg = (zh or en), negative
    elif output_lang == "中英对照":
        out_pos = (zh + "\n" + en) if zh else en
        out_neg = negative
    else:
        out_pos, out_neg = en, negative

    return out_pos, out_neg, en, zh


# ---------------------------------------------------------------- 视频
def _fmt_ss(sec):
    return "%.2f" % float(sec)


def build_video(mode, duration, style, subject, action, scene, shot_size,
                camera, amplitude, speed, transition, shots_zh,
                dialogue, dlg_lang, soundscape, music,
                output_lang, use_llm=False, base_url="", model="", api_key="", llm=None,
                status=None):
    cfg = _llm_or_none(llm, use_llm, base_url, model, api_key)
    st = status if status is not None else {}
    # 同上：视频模板里也有「不指定」占位项，brief / 中文拼装都不能留
    (mode, style, subject, action, scene, shot_size, camera, amplitude, speed,
     transition, dialogue, soundscape, music) = [
        nz(x) for x in (mode, style, subject, action, scene, shot_size, camera,
                        amplitude, speed, transition, dialogue, soundscape, music)]
    vb = " | ".join([x for x in [mode, "duration=%ss" % duration, style, subject, action, scene,
                                 shot_size, camera, shots_zh, dialogue, soundscape, music] if x])
    v = lambda cat, zh: value(cat, zh, group="video")

    # 运镜短语：官方要求写成自然语句 "The camera pushes in with small amplitude at slow speed ..."
    cam = v("camera_motion", camera)
    cam_phrase = ""
    if cam:
        cam_phrase = "The camera %s" % merge([cam, v("amplitude", amplitude), v("speed", speed)], sep=" ")
    elif amplitude and v("amplitude", amplitude):
        cam_phrase = "The camera moves %s." % v("amplitude", amplitude)

    style_en = v("style", style) or "Live-action, cinematic"

    # 分镜：每行一镜
    lines = [l.strip() for l in (shots_zh or "").split("\n") if l.strip()]
    shots_en = []
    if lines:
        for i, l in enumerate(lines):
            head = "[Shot %d] " % (i + 1)
            if i > 0:
                # 均分切点
                cut = duration * i / max(1, len(lines))
                head = "[Shot %d] At 00:%06.3f, %s " % (i + 1, cut, v("transition", transition) or "the camera cuts to")
            shots_en.append(head + zh2en(l))
    else:
        body = merge([zh2en(subject), zh2en(action), zh2en(scene)], sep=", ")
        first = "[Shot 1] %s, %s %s. %s." % (
            style_en, v("shot_size", shot_size) or "a medium shot frames", body, cam_phrase)
        shots_en.append(first.replace(" ,", ",").replace("..", "."))

    if dialogue:
        lang = v("dialogue_lang", dlg_lang) or "[Chinese]"
        shots_en[0] += ' The subject (S1) says: <d>%s %s</d>' % (lang, dialogue)

    imd = " ".join(shots_en)
    snd = v("soundscape", soundscape) or zh2en(soundscape) or "Ambient sound consistent with the scene."
    mus = v("music", music) or zh2en(music) or "N/A"

    head = ""
    if mode == "I2VA":
        head = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    elif mode == "FL2VA":
        head = ("How the reference pictures align with the target video — "
                "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
                "Picture 2 (from Shot %d) aligns with the %s-second mark of the target video."
                % (len(shots_en), _fmt_ss(duration)))
    elif mode == "L2VA":
        head = ("How the reference pictures align with the target video — "
                "<Picture 1> (from [Shot %d]) aligns with the %s-second mark of the target video."
                % (len(shots_en), _fmt_ss(duration)))

    en = (head + "\n\n" if head else "")
    en += "integrated_multimodal_description: %s\n\noverall_soundscape: %s\n\nnon_diegetic_music: %s" % (imd, snd, mus)

    got_zh_text = ""
    if cfg and cfg.enabled:
        try:
            got_zh, got_en = split_bilingual(cfg.run(
                SYS_VIDEO,
                "Brief (mode | duration | style | subject | action | scene | camera | "
                "dialogue | soundscape | music):\n%s\n\nExpand it into the official H3 "
                "prompt now." % vb, max_tokens=1024))   # H3 正文本身就 400+ 词，512 会截断
            en_ok = bool(got_en) and _llm_output_ok(got_en, "英文（推荐）")
            zh_ok = bool(got_zh) and bool(_HAS_ZH.search(got_zh))
            if not zh_ok:                       # 模型一口气写完 H3 就没配额写中文段了
                more = _expand_zh(cfg, vb, video=True)
                if more:
                    got_zh, zh_ok = more, True
            if en_ok:
                en = got_en
            if zh_ok:
                got_zh_text = got_zh
            if en_ok and zh_ok:
                st["llm"] = LLM_STATUS_OK
            elif en_ok or zh_ok:
                st["llm"] = "✓ 已智能扩写（仅%s段）" % ("英文" if en_ok else "中文")
            else:
                st["llm"] = "⚠ 模型输出不合格，已回退模板拼装"
        except Exception as e:
            st["llm"] = "✗ 失败：%s" % str(e)[:130]
    else:
        st["llm"] = LLM_STATUS_OFF

    zh = got_zh_text or merge([subject, action, scene, camera, style], sep=" / ")
    if output_lang == "中文":
        out = zh + "\n\n" + en
    elif output_lang == "中英对照":
        out = "中文：%s\n\nEnglish：\n%s" % (zh, en)
    else:
        out = en
    return out, en, zh

# ComfyUI-HammPrompt

中文写提示词、直接喂模型的 ComfyUI 节点套装。两套系统：文生图（中文）/ 视频（MiniMax H3，英文）。

## 节点

| 节点 | 分类 | 作用 |
|---|---|---|
| `Hamm 本地模型 (GGUF/HF)` (HammLocalLLM) | HammPrompt/本地模型 | 加载本地 LLM 并常驻，供下面几个节点复用（避免每镜重复加载） |
| `Hamm 卸载本地模型` (HammUnloadLLM) | HammPrompt/本地模型 | 手动释放本地模型显存，还给生图 |
| `Hamm 文生图提示词` (HammPromptImage) | HammPrompt/文生图 | 中文主题 + 风格/地点/动作/景别/光线/氛围/画质/**年代**下拉 → 输出 positive（**中文**正向，单口） |
| `Hamm 分镜批量` (HammPromptStoryboard) | HammPrompt/文生图 | 每行一句中文分镜 → 输出 prompt_lines / prompt（接 easy promptLine 可一次跑 N 镜） |
| `Hamm 视频提示词` (HammPromptVideo) | HammPrompt/视频 | MiniMax H3 官方格式 → 输出 H3_prompt / prompt |
| `Hamm 提示词预览（中文）` (HammPromptPreview) | HammPrompt/工具 | 独立预览面板：正向提示词可编辑文本框，零连线也能用 |
| `Hamm 生图模式切换（文生图/图生图）` (HammImageMode) | HammPrompt/工具 | 下拉切「文生图 / 图生图」，决定 KSampler 吃 EmptyLatentImage 还是 VAEEncode 出的图 latent；纯后端节点，不依赖第三方 |

提示词节点自身不显示多余文字，保持参数区干净 —— 但节点上有一个「结构化提示词」文本框（见下）。

### 结构化提示词（在提示词节点上，实时联动）

三个提示词节点（文生图 / 视频 / 分镜批量）上都有「结构化提示词」文本框，
绿色标题条，**紧跟在输入选择后面**（文生图在「额外说明」下面；
视频在「配乐」下面；分镜在「固定风格后缀」下面）：

- **字段顺序（2026-09-18 起）**：文生图把**「时代背景」放在第一行**（先交代时代，再人物 / 风格 /
  场景…），最后一行是**「额外说明」**（原来的「补充正面」）。原「年代」这一行的标签也跟着改名
- **年代只写简洁写法**：`时代背景：唐代`，不再写 `年代：唐（人物与场景符合唐代背景）`——
  标签本身已经叫「时代背景」，那半句考据是重复的。写法表在 `hamm_core.era_display` 一处维护
  （唐→唐代、50年代→1950年代、魏晋→魏晋南北朝时代…），后端 `era_to_zh` 负责反查回选项名，
  **所以英文年代片段不会被丢**（少这一步，`value("era", "唐代")` 在模板里查不到）

- **实时联动**：改上面任何一个输入 / 下拉，这份文本立刻刷新（`名称：值` 逐行，空项不显示、不带括号注释）。
  刷新走的是纯前端事件钩子，**不产生任何网络请求**，切下拉下一帧就能看到
- **选「不指定」= 没选**：该要素整行消失（前后端一致），不会出现「风格：不指定」这种噪声行。
  这个「前后端一致」是硬约束：结构化框、最终中文提示词、以及喂给 LLM 的 brief 三处
  都不能出现「不指定」（2026-09-17 修：后端 `merge()` 只过滤空串没过滤占位词，
  全未指定时提示词里会冒出一串「不指定」）
- **可以手改**：直接在框里改字，改完**直接运行 —— 词典替换和 LLM 扩写都按改后的内容来**
  （后端会把这份文本逐行解析回参数再扩写，不是摆设）
- **注意**：改了上面的下拉，框会跟着实时刷新（覆盖手改）；想固定自己写的版本，
  改完就别再动上面的选择，或把内容复制出去
- **复制按钮**：结构化框正下方，点一下 = 复制「**优化指令 + 要素清单**」（含手改）。
  指令在前、以「待优化词如下：」收尾，清单紧跟其后 —— 整段直接粘进豆包 / DeepSeek 就是一个完整请求
- **重置按钮**：同一行最右侧，一键把**本节点的元素选项**还原成未选状态 ——
  下拉回「不指定」（该组没有「不指定」的，回想带「默认」的那项、再没有就回首项，
  如画质回「通用高清（推荐）」、转场回「硬切（默认）」），文本框清空，时长回 5s；
  结构化框同步刷成重置后的内容（顺手清掉手改标记）。
  **技术设置不动**：`llm_mode` / 本地模型 / API key 等一律保留 —— 重置风格不该把模型选择也清掉。
  按钮上会回一句「已重置 N 项」；本来就是空的则回「已是初始」。
  三个提示词节点（文生图 / 视频 / 分镜批量）都有，重置范围按各自的元素字段来

> 旧版预览节点上的复制按钮已移到提示词节点上；预览节点现在是单框显示面板
> （只留正向提示词），老工作流仍然兼容。
> 提示词节点默认宽度 520（可拉宽，切下拉不会再跳动）。
>
> **反向提示词口已全部下线（2026-09-18）**：`Hamm 文生图提示词` 的 `negative` 输出口、
> `Hamm 提示词预览` 的 `negative` 输入口与输出口一起删掉了 —— 本套节点从不产生负向词，
> 留着只是两个恒为空的连线点。需要负向词就在下游 `CLIPTextEncode` 里自己写。
> 加载旧工作流时前端会自动把这几个废弃口的残线清掉（`reconcileOutputs` /
> `reconcilePreviewInputs`），不需要手动改。
> `Hamm 执行开关` 的 `negative` 口**保留**（它是通用透传口，可以接你自己写的负向词）。

> **英文功能已下架（2026-09-16）**：三个提示词节点的 `output_lang` 下拉、`english` /
> `structure` 输出口、预览节点的英文框全部移除，只保留中文扩写链路。
>
> **文生图的 `positive` / `prompt` 两个口已合并（2026-09-16）**：原来 `positive` 是英文、
> `prompt` 是中文，是两个语言版本。对吃中文的模型（Qwen-Image / Z-Image 等）英文口永远
> 空挂，所以现在**只出两个口**：`positive` 直接就是中文正向，喂给下游即可；`prompt` 口删除。
> 预览节点的输入口也跟着从 `prompt` 改名成 `positive`。
>
> 视频节点的 `H3_prompt` **不并**（MiniMax H3 官方格式只吃英文，必须留着），
> 分镜的 `prompt_lines` 同理。
>
> 加载旧工作流时前端会自动迁移：删掉语言槽值、按名字（含改名别名）把连线挪到新槽位、
> 清掉废弃口的残线。具体包括：旧的 `image.prompt → preview.prompt` 这根线会自动接到
> `image.positive → preview.positive` 上，一条不丢；旧 `english` / `structure` 才清掉。

**预览节点可以零连线使用**：拖出来放在旁边，什么都不接，跑一次 Queue 它就会自动显示
最近执行的提示词节点的结果（前端全局监听执行事件）。想显式绑定（多个提示词节点
各配一个预览、或固定看某一个）再连线：

```
Hamm 文生图提示词.positive ──→ Hamm 提示词预览.positive
```

连了线的口以后端送来的值为准；没连的口自动走全局捕获。

预览节点只有**一个框：正向提示词**（显示并可直接编辑最终喂给模型/下游的中文提示词）。

> **框是节点上真实的多行文本框**（不是画在画布上的），上方有彩色标题条：
> 文字被框住、超长在框内滚动，**永远不会溢出节点边界**；可以手改，
> 改过之后重新运行**不会被自动结果冲掉**，清空再运行即可恢复自动。

复制按钮在**提示词节点的结构化框正下方**（悬停有 tooltip，旁边有灰色提示语「复制到豆包 / DeepSeek 优化」），
点一下剪贴板里是**一句优化指令 + 要素清单** —— **指令在前、待优化词在后**，
整段直接粘进对话框就是一个完整请求，不用再手动补一句：
刻意只带你的选择、不带我们已写好的扩写结果，免得把豆包带偏：

```
请按下面的元素格式帮我优化提示词：保留已选的人物、风格、场景、动作、光线等要素不变，补全镜头、材质、光影层次等细节，只输出一段可直接使用的中文提示词，不要解释、不要分点说明，待优化词如下：
时代背景：唐代
主体：一个年轻女子
风格：写实电影感
地点：荷花池畔
动作：轻提裙摆
景别：近景特写
光线：柔和自然光
画质：通用高清
额外说明：中国古典风格
```

粘贴到豆包 / DeepSeek / ChatGPT 即可。指令句会自动识别类型（文生图 / 文生视频 / 分镜批量），
文生图那版要中文（`positive` 现在就是中文），视频 / 分镜那版要英文（H3 官方格式）。

同一行的最右侧是**重置按钮**：把节点上的元素选项一键还原成「未选」——
下拉回「不指定」（没有这一项的回想带「默认」的首项 / 首项）、文本框清空、时长回 5s，
结构化框跟着刷新；`llm_mode`、模型路径、API key 这些技术设置**不会被动**。
想把一堆选过的要素清干净重新挑，点它就行。

**要素清单里刻意不放的东西**：负向词（预设与补充）、以及「类型 / 输出语言 / 智能扩写 / 扩写状态」这类诊断行。
负向不属于正向提示词结构；诊断会干扰豆包判断。想要诊断信息，看 ComfyUI 控制台里的
`[HammPrompt] 文生图 扩写状态：…` 一行。

### 执行开关：调提示词时不出图

`Hamm 执行开关（关=只出提示词）` 串在提示词节点和 CLIP 编码之间：

```
Hamm 文生图提示词.positive ──→ Hamm 执行开关.positive ──→ 正向 CLIPTextEncode
（负向就直接在 CLIPTextEncode 里写；开关上的 negative 口保留，需要透传时再接）
```

- **开：继续生成图片** = 原样透传，正常出图
- **关：只生成提示词** = 输出 ComfyUI 官方的 `ExecutionBlocker`，下游（CLIP 编码 / KSampler /
  保存图片）全部显示为「已跳过」，队列只运行到提示词生成为止 —— 调提示词时省掉采样时间
- 预览节点不受影响照常更新（它是独立输出节点）；切回「开」再跑一次就出图


## 文生图 / 图生图 一键切换

示例工作流 `hammprompt-preview-example.json` 已经把图生图分支预搭好了，由一个 `Hamm 生图模式切换`
节点控制。**默认就是文生图**，开箱即用、不会因没图报错。

链路：

```
文生图：EmptyLatentImage ──→ Hamm 生图模式切换.latent_txt2img ─┐
                                                            ├─→ KSampler.latent_image
图生图：LoadImage → VAEEncode ──→ Hamm 生图模式切换.latent_img2img ┘
```

- 开关选「文生图」→ 输出 `latent_txt2img`（走 EmptyLatentImage 的纯噪声 latent）
- 开关选「图生图」→ 输出 `latent_img2img`（走 LoadImage → VAEEncode 的图 latent）

**关键点**：ComfyUI 只执行「连到输出」的祖先节点。图生图分支的 `VAEEncode` 输出**默认没接进开关**，
所以文生图模式下 `LoadImage` / `VAEEncode` 不会被触发，也不会因为没上传图而报错。

### 怎么切到图生图

1. 在 `LoadImage` 节点上传一张参考图（决定了出图尺寸）
2. 把 `VAEEncode` 的 `LATENT` 输出口，连到 `Hamm 生图模式切换` 的 `Latent(图生图)` 口
3. 开关 `模式` 下拉切到「图生图」
4. 把 `KSampler` 的 `denoise` 调到 < 1.0：想大改画面设 0.5~0.7，想保原构图只换风格/细节设 0.3 左右

> `denoise` 是 KSampler 自带滑块，开关节点不管它——图生图必须自己调低，否则等于文生图重画整张。
> 想切回文生图，把开关切回「文生图」即可（VAEEncode 那根线留着不碍事，文生图模式下它不会执行）。


## 图生提示词（上传图片识别）

新增独立节点 **`Hamm 图生提示词`** (`HammPromptImageToPrompt`)，与原来的 `Hamm 文生图提示词` 完全解耦：

- `Hamm 图生提示词`：只负责「图片 → 中文提示词文本」。
- `Hamm 文生图提示词`：继续负责「中文要素 → 本地/在线扩写 → 提示词」。

这样你可以直接把识别结果接到 `Hamm 提示词预览` 看文本，也可以接到 `Hamm 文生图提示词.subject`
再做一轮扩写，老工作流不受影响。

链路（直接看识别结果）：

```
LoadImage.IMAGE ──→ Hamm 图生提示词.image
                          │
                          ▼
                  Hamm 提示词预览.positive
```

链路（识别后再扩写）：

```
LoadImage.IMAGE ──→ Hamm 图生提示词.prompt ──→ Hamm 文生图提示词.subject
                                                   │
                                                   ▼
                                         Hamm 提示词预览.positive
```

使用前提（重要）：

看图需要**视觉模型**。`Hamm 图生提示词` 节点默认**本地优先**：

- **本地 VLM（推荐，完全离线）**：`llm_mode` 设 `本地模型（推荐）`，`local_vision_model`
  下拉选一个视觉模型 GGUF（如 `Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf`），`local_vision_mmproj`
  留空即自动在同目录找 `mmproj*.gguf`（图片塔）。需要你自己下载这两样丢进 `ComfyUI/models/LLM`。
- **在线 API（兜底）**：没配本地 VLM、但 `llm_mode` 设 `在线 API` 并填了视觉模型
  （如智谱 `glm-4v-flash`、OpenAI `gpt-4o-mini`），就走在线视觉接口。
- 配了本地 VLM 时**绝不静默回退 API**——本地失败直接报错，避免偷偷烧额度；想用在线就别选本地 VLM。

下载建议（本地 VLM）：

| 模型 | 主模型大小 | mmproj | 说明 |
|------|-----------|--------|------|
| **MiniCPM-V-2_6-GGUF**（推荐） | Q4_K_M 约 4.7GB | `mmproj-model-f16.gguf` | llama.cpp 支持最成熟，中文 OCR 强，错误少 |
| **moondream2-20250414-gguf** | f16 约 2.6GB | `moondream2-mmproj-f16-20250414.gguf` | 极小、极快，适合先跑通链路 |
| **Qwen2.5-VL-7B-Instruct-GGUF** | Q4_K_M 约 5GB | `Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf` | 能力强，但对 llama.cpp 版本敏感，容易加载失败 |

- 模型 + mmproj 必须**来自同一仓库、同一版本**，随便混搭一定报错。
- 两个文件都丢进 `ComfyUI/models/LLM`，`local_vision_mmproj` 留空即可自动探测。

常见报错：

- `未找到与视觉模型配套的 mmproj*.gguf` → 缺 mmproj，按上表下载配套图片塔。
- `Failed to load llama context` / `加载视觉模型失败` → 当前 llama.cpp 版本不支持该 VLM。
  建议换成 **MiniCPM-V-2_6** 或 **moondream2**；或把 `llm_mode` 切到「在线 API」用视觉模型。

行为细节：

- 取图片**第一帧**转 PNG 发给视觉模型。
- 输出是把识别到的「主体 / 风格 / 场景 / 动作 / 景别 / 光线 / 氛围 / 画质 / 年代」拼成的一段中文提示词，
  直接进预览框或下游 STRING 输入口。
- 调用失败（没开 LLM / 没配本地 VLM / mmproj 缺失 / 接口异常 / key 错）会输出一段带 ⚠️ 的提示文本，
  节点不崩，原因打到控制台：`[HammPrompt] 图生提示词 扩写状态：✗ 图片识别失败：…`


## 装法

整个目录放到 `ComfyUI/custom_nodes/ComfyUI-HammPrompt/`，重启 ComfyUI。
离线模式零第三方依赖；本地模型用的是 ComfyUI 自带的 `llama-cpp-python` 和 `transformers`，不用额外装。

## 智能扩写：本地模型

`llm_mode` 三档：

| 选项 | 行为 |
|---|---|
| `关（纯离线词典）` | 内置约 300 条中文词典 + 模板拼装，零延迟零显存 |
| `本地模型（推荐）` | 直接用本地 GGUF / HF 模型，**不需要 Ollama、不联网** |
| `在线 API` | OpenAI 兼容接口（智谱 / DeepSeek / Kimi / 本地 vLLM） |

**「扩写」是真的扩写，而且中英各一份**（文生图现在只输出中文那份，视频 / 分镜用英文那份）：
系统提示词要求模型一次返回 `[ZH]` / `[EN]` 两段，
两段都要**补**而不是复述——补主体细节（年龄 / 发型 / 面料花纹 / 表情 / 手部与道具）、
分层环境（前中后景 / 天气 / 时段 / 材质）、光线（方向 / 软硬 / 色温 / 轮廓光 / 空气感）、
镜头（焦段 / 光圈 / 景深 / 角度 / 构图），并要求不得与原 brief 矛盾、
不得乱加没要求的光源（golden hour 之类）。

**模型只吐了英文怎么办**：视频那路尤其常见——H3 正文本身 400+ 词，模型一口气写完就没有额度写中文段了。
这时节点会自动**补跑一次纯中文扩写**（短调用），所以中文栏永远有扩写版。

实测（RTX 5060 Ti + Qwen3-4B-Instruct-2507-Q4_K_M）：

| | 词典拼装 | 开本地模型的扩写结果 | 耗时 |
|---|---|---|---|
| 文生图 英文 | ~30 词 | **119~132 词** | 6.7s |
| 文生图 中文 | ~22 字 | **201~208 字** | （同上，一次调用） |
| H3 视频 英文 | 模板 | 完整 H3 三段式 | 10.0s（含中文补跑） |
| 分镜 中文 | 原文 | **每镜 90+ 字扩写** | 5.1s / 2 镜 |

`llm_mode` 切换时参数区会自动显隐：选本地模型显示 `local_*` 一组；选在线 API 隐藏本地模型、显示「API 设置」分组（model / url / key）。
LLM 失败**不会**把报错文字混进正文（那段文本会直接喂给生图 / 视频模型），
失败原因只打到 ComfyUI 控制台：`[HammPrompt] 文生图 扩写状态：✗ 失败：…`

### 本地模型放哪

`ComfyUI/models/LLM/`（节点包会自动注册这个目录）。支持两种格式：

- **GGUF** → 走 `llama-cpp-python`（ComfyUI 便携版已自带 0.3.48 + CUDA），GPU 卸载
- **HF 格式**（含 `config.json` 的目录，含 `models--org--name` 缓存目录）→ 走 `transformers`

放进去后刷新页面，`local_model` 下拉里就能选到。

### 下载推荐（RTX 5060 Ti 16GB）

```bash
"E:\soft\ComfyUI_windows_portable\python_embeded\python.exe" download_llm.py
"E:\soft\ComfyUI_windows_portable\python_embeded\python.exe" download_llm.py --mirror   # 国内走镜像
```

默认下 `Qwen3-4B-Instruct-2507-Q4_K_M`（约 2.5GB，中文强、指令跟随稳）。
其它：`--preset qwen3-8b`（4.9GB）、`--repo org/name --file x.gguf` 自定义。

### 显存怎么管

`keep_loaded` **默认关** —— 每次扩写完立刻卸载，显存还给生图。
16GB 卡上这点很关键：LLM 常驻会跟生图模型抢显存。

连续跑多镜想省加载时间，就把 `keep_loaded` 打开（或接 `Hamm 本地模型` 加载器节点复用），
跑完接一个 `Hamm 卸载本地模型` 节点释放。

**`Hamm 分镜批量` 例外**：节点内部会在批量循环期间强制常驻模型，循环结束再自动卸载。
否则 N 个分镜就要重新加载 N 次模型（每次几十秒），实测 3 镜从 ~25 秒降到 8 秒。

## 关键参数

- `local_max_tokens`：同一次推理的总输出上限，**默认 512**。
  节点内部会给文生图 / 视频 / 分镜分别留足额度（768 / 1024 / 768），
  不用手动调；调太低最直接的症状就是 H3 正文被从中间截断。
- `local_temperature`：`0.7` 比较稳；调高更发散但容易跑偏。
- LLM 走 OpenAI 兼容协议，默认智谱 `glm-4.7-flash`（免费层）。改 `llm_base_url` / `llm_model` 可换 DeepSeek、Kimi、本地 vLLM。
- Key 填在节点 `llm_api_key`，或设环境变量 `LLM_API_KEY`（节点留空时自动读）。

## 词典模式的能力边界

内置约 300 条中文词条（人物 / 服饰 / 动作 / 场景 / 光线 / 情绪 / 镜头 / 画质 / 颜色）。
**没命中的中文会原样残留**，此时走英文的那几路（视频 / 分镜）提示词里会夹着汉字 —— 说明该开 LLM 了。
看 ComfyUI 控制台的 `[HammPrompt] … 扩写状态：` 一行就知道当前是哪种情况。

提示词里出现中文标点也不用管，`zh2en` 会先把「，、；：。！？（）」归一成英文标点再替换词。

## 视频节点对齐 MiniMax H3 官方规范

字段与运镜词汇取自官方 `VIDEO_PROMPT_WRITING_GUIDE_base_en.md`（本机
`custom_nodes/ComfyUI-Fantastic-MiniMaxH3-PromptBuilder/web/video-prompt-writing-guide.html`）：

- 模式：`T2VA` 文生视频 / `I2VA` 首帧生视频 / `FL2VA` 首尾帧 / `L2VA` 尾帧
- 三段式输出：`integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music`
- 运镜：13 种官方 motion type（Push In、Pan、Truck、Tilt、Pedestal、Arc、Tracking、Static、Shake、POV、Roll…）
  + amplitude（small / large）+ speed（slow / fast），写成自然语句 `The camera pushes in with small amplitude at slow speed ...`
- 风格：官方 7 种（Cinematic / Live-action / 2D-animated / 3D CG / Claymation / Watercolor / Vintage film）
  + 15 种扩展（中国古风真人、国风动画、水墨、仙侠、武侠、赛博朋克…）
- 分镜：`shots_zh` 每行一镜，自动编号 `[Shot N]` 并按总时长均分切点

## 模板容量

`data/t2i_templates.json`：风格 42 / 地点 39 / 动作 40 / 景别 13 / 光线 24 / 氛围 20 / 画质 12 / 年代 21 / 负向 8
`data/video_templates.json`：模式 4 / 风格 22 / 运镜 21 / 幅度 3 / 速度 3 / 景别 13 / 转场 8 / 环境音 15 / 配乐 14

改这两个 JSON 就能加模板，重启生效，不用动代码。

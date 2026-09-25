// HammPrompt 前端：
// 1) HammPromptPreview 独立预览节点：
//    ① 中文扩写提示词 = 真实的多行文本框（DOM widget，可编辑）
//    用文本框而不是 canvas 绘制：文字被框住、超长在框内滚动，绝不溢出节点边界
//    （旧版的「已选要素」「英文提示词」框与复制按钮均已移除/搬家）
// 2) 零连线模式：4 个输入口全不连也能用 —— 全局监听 executed 事件，
//    抓住最近执行的提示词节点送出的数据自动喂进来（显式连线的口仍优先）
// 3) 按 llm_mode 动态显隐 local_* / llm_* 参数
import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import { api } from "../../scripts/api.js";

// 版本号：F12 控制台会打印，用来确认浏览器加载的是不是最新版（没硬刷新时会是旧号）
const HAMM_JS_VERSION = "v29-preview-refresh";
console.log("[HammPrompt] web js loaded:", HAMM_JS_VERSION);

const LINE_H = 15;
const TITLE_H = 20;          // 每个文本框上方的标题条高度
// 框架给 DOM widget 的容器上还留了一段间距（实测 10px，两种缩放比例下一致），
// 所以一个框占的完整高度 = 内容 + 标题条 + 这段间距（否则最后一个框会压到按钮）。
const CONTAINER_PAD = 10;
const BOX_TOP_PAD = TITLE_H + CONTAINER_PAD;

// 预览节点的显示区：只剩「正向提示词」一个文本框。
// 它与后端 INPUT_TYPES 里的 positive 同名绑定，改动会被序列化并透传给下游；
// 前端负责标签、配色、自适应高度和标题条上的复制按钮。
// 注：原 ①「已选要素」已合并进提示词节点；英文框已移除；② 反向提示词框
//     也按用户要求去掉了（后端 negative 输入/输出口仍在，值照常透传，只是不再显示框）。
const BOX_DEFS = [
  { name: "positive", label: "正向提示词输入框（生成的扩展词显示在这里，同时直接作为提示词喂给大模型）", maxLines: 24, color: "#d4a574" },
];
// 旧版 ① 框的名字：旧工作流里可能还留着这个控件，加载时要识别并收起
const RETIRED_EL_BOX = "elements_text";
const EL_WIDGET = RETIRED_EL_BOX;                 // 兼容旧引用（复制按钮挂到提示词节点用）
const BOX_NAMES = BOX_DEFS.map((d) => d.name);


// llm_mode 三档（必须与后端 C.LLMConfig.MODES 完全一致）
const MODE_OFF = "关（纯离线词典）";
const MODE_LOCAL = "本地模型（推荐）";
const MODE_API = "在线 API";
// 已下线的旧选项：旧工作流里可能还存着，必须改写掉，
// 否则 indexOf 模糊匹配会让「本地」和「API」同时命中 → 两组控件一起显示。
const RETIRED_MODES = ["本地优先→API兜底"];

// 参数分组：按 llm_mode 决定显隐
const LOCAL_W = ["local_model", "local_gpu_layers", "local_ctx",
                 "local_max_tokens", "local_temperature", "keep_loaded"];
const API_W = ["llm_model", "llm_base_url", "llm_api_key"];

const API_LABELS = {
  llm_model: "模型名称",
  llm_base_url: "API 地址",
  llm_api_key: "API KEY",
};

// 某些前端版本在控件被隐藏时会把数值保存成 NaN/undefined/0，重新显示时兜底回默认值
const FALLBACKS = {
  local_gpu_layers: -1,
  local_ctx: 4096,
  local_max_tokens: 512,
  local_temperature: 0.7,
};

function fixValue(w) {
  if (w.type === "hidden") return;
  const fallback = w.options?.default ?? FALLBACKS[w.name];
  if (fallback === undefined) return;
  // 只修复数值控件（INT/FLOAT），字符串类控件不碰，避免覆盖用户输入的 url/model/key
  const isNum = w.type === "INT" || w.type === "FLOAT" || w.type === "number";
  if (!isNum) return;

  let v = w.value;
  // 坏字符串统一转成 NaN
  if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    if (s === "" || s === "nan" || s === "undefined" || s === "null") {
      v = NaN;
    } else {
      const n = Number(v);
      v = isNaN(n) ? NaN : n;
    }
  }

  let invalid = false;
  if (v === undefined || v === null || (typeof v === "number" && isNaN(v))) {
    invalid = true;
  } else if (typeof v === "number") {
    const min = w.options?.min;
    const max = w.options?.max;
    if ((min !== undefined && v < min) || (max !== undefined && v > max)) {
      invalid = true;
    }
  } else {
    invalid = true;
  }

  if (invalid) {
    w.value = fallback;
  }
}

// 隐藏/显示控件：新版 ComfyUI 前端中 STRING 是 DOM widget，
// 只改 type 不会真正隐藏 DOM 元素，必须同步改 inputEl/element/display/_state。
function applyHidden(node, w, hide) {
  if (hide) {
    if (w.hammOrigType === undefined) {
      w.hammOrigType = w.type;
      w.hammOrigCS = w.computeSize;
    }
    w.hidden = true;
    if (w.inputEl) w.inputEl.style.display = "none";
    if (w.element) w.element.style.display = "none";
    w.type = "hidden";
    w.computeSize = () => [0, -4];
    w.computedHeight = 0;
    if (w._state) { w._state.hidden = true; w._state.type = "hidden"; w._state.computedHeight = 0; }
  } else if (w.hammOrigType !== undefined) {
    w.hidden = false;
    if (w.inputEl) w.inputEl.style.display = "";
    if (w.element) w.element.style.display = "";
    w.type = w.hammOrigType;
    w.computeSize = w.hammOrigCS || (() => [Math.max(160, node.size[0] - 16), 26]);
    // 关键：高度必须当场补上，不能留空等下一帧重算。
    // DOM 覆盖层（DomWidgets.vue 的 updateWidgets）是这样定位的：
    //   state.pos  = 节点坐标 + w.y
    //   state.size = w.computedHeight ?? 50        ← 留空就按 50px 画
    // 刚恢复显示的控件这一帧里 w.y 还是「隐藏时的旧值」、computedHeight 已删，
    // 于是被画在错误位置、错误高度，下一帧才归位 —— 就是肉眼看到的「切换模式时闪一下错位」。
    // 这里按框架 _arrangeWidgets 的同一算法（computeSize()[1] + 4）当场补好。
    let ch = 30;
    try {
      const s = w.computeSize(node.size[0]);
      ch = (s && s[1] ? s[1] : 26) + 4;
    } catch (e) { /* 拿不到就用兜底 30 */ }
    w.computedHeight = ch;
    if (w._state) { w._state.hidden = false; w._state.type = w.type; w._state.computedHeight = ch; }
    delete w.hammOrigType;
    delete w.hammOrigCS;
  }
}

// 读取 llm_mode 并把已下线的旧值改写掉（返回规范化后的模式字符串）
function readMode(node) {
  const w = node.widgets && node.widgets.find((x) => x.name === "llm_mode");
  if (!w) return "";
  const v = String(w.value || "");
  if (RETIRED_MODES.indexOf(v) >= 0) {
    w.value = MODE_LOCAL;
    return MODE_LOCAL;
  }
  return v;
}

function migrateApiWidgetValues(node, info) {
  // 旧顺序：llm_base_url / llm_model / llm_api_key
  // 新顺序：llm_model / llm_base_url / llm_api_key
  // 加载旧工作流时把错位值换回来。
  // 错位特征：模型位置(idxModel)装的是 URL，URL 位置(idxUrl)装的是模型名。
  const vals = info && info.widgets_values;
  if (!Array.isArray(vals) || !node.widgets) return;
  const idxModel = node.widgets.findIndex((w) => w.name === "llm_model");
  const idxUrl = node.widgets.findIndex((w) => w.name === "llm_base_url");
  if (idxModel < 0 || idxUrl < 0) return;
  const modelVal = vals[idxModel];
  const urlVal = vals[idxUrl];
  if (typeof modelVal === "string" && modelVal.startsWith("http") &&
      (typeof urlVal !== "string" || !urlVal.startsWith("http"))) {
    vals[idxModel] = urlVal;
    vals[idxUrl] = modelVal;
  }
}

function labelizeApiWidgets(node) {
  if (!node.widgets) return;
  for (const w of node.widgets) {
    if (API_LABELS[w.name] != null) {
      w.label = API_LABELS[w.name];
    }
  }
}

// 加载旧工作流后，base onConfigure 已把保存值填进控件，
// 此时直接检查「模型名称」控件是否误装了 URL，是就与「API 地址」互换。
function swapApiIfMisplaced(node) {
  if (!node.widgets) return;
  const mw = node.widgets.find((x) => x.name === "llm_model");
  const uw = node.widgets.find((x) => x.name === "llm_base_url");
  if (!mw || !uw) return;
  const mv = String(mw.value || "");
  const uv = String(uw.value || "");
  if (mv.startsWith("http") && !uv.startsWith("http")) {
    mw.value = uv;
    uw.value = mv;
  }
}

function drawApiHeader(node, ctx) {
  // 在线 API 模式下在 url/model/key 三项上方画一个分组标题
  if (readMode(node) !== MODE_API) return;
  const w = node.widgets && node.widgets.find((x) => x.name === "llm_base_url");
  if (!w || w.last_y == null) return;
  const y = w.last_y - 24;
  if (y < 20) return;
  ctx.save();
  ctx.fillStyle = "rgba(36,38,44,0.92)";
  ctx.fillRect(8, y, node.size[0] - 16, 20);
  ctx.font = "12px sans-serif";
  ctx.fillStyle = "#b0b3b8";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("—— API 设置 ——", node.size[0] / 2, y + 10);
  ctx.restore();
}

function isPreviewNodeOf(node) {
  return node && (node.type === "HammPromptPreview" || node.comfyClass === "HammPromptPreview");
}

// ================================================================ 零连线模式
// 预览节点的输入口（positive/negative）全都不连也能用：
// 全局监听 api 的 executed 事件，抓住最近执行的提示词节点送出的预览数据，
// 直接喂给画布上没连线的预览节点。显式连线的口仍然优先（后端送来的值不被覆盖）。
let hammLastPayload = null;   // { nodeId, srcType, en, cn, neg, struct, ts }

function nodeHasLinkedInputs(node) {
  return !!(node.inputs && node.inputs.some((i) => i && i.link != null));
}

function installExecutedCapture() {
  if (app.hammExecCaptureInstalled || !api || !api.addEventListener) return;
  app.hammExecCaptureInstalled = true;
  api.addEventListener("executed", (e) => {
    const d = e.detail || {};
    const out = d.output || {};
    if (!out || (out.hamm_preview_en === undefined && out.hamm_preview_cn === undefined)) return;
    const g = app.graph;
    const src = (g && g.getNodeById && d.node != null) ? g.getNodeById(d.node) : null;
    hammLastPayload = {
      nodeId: d.node,
      srcType: src ? src.type : null,
      en: out.hamm_preview_en ? out.hamm_preview_en[0] : undefined,
      cn: out.hamm_preview_cn ? out.hamm_preview_cn[0] : undefined,
      neg: out.hamm_preview_neg ? out.hamm_preview_neg[0] : undefined,
      struct: out.hamm_preview_struct ? out.hamm_preview_struct[0] : undefined,
      ts: Date.now(),
    };
    // 预览节点可能先于上游执行（OUTPUT_NODE 相互独立），事件驱动最稳：
    // 数据一到就把画布上没连线的预览节点全部刷新
    if (!g || !g._nodes) return;
    for (const n of g._nodes) {
      if (!isPreviewNodeOf(n) || nodeHasLinkedInputs(n)) continue;
      applyAutoPayload(n, hammLastPayload);
    }
  });
}

function applyAutoPayload(node, p) {
  if (!p) return;
  if (p.en !== undefined) node.hammPreviewEN = p.en;
  if (p.cn !== undefined) node.hammPreviewCN = p.cn;
  if (p.neg !== undefined) node.hammPreviewNEG = p.neg;
  if (p.struct !== undefined) node.hammPreviewSTRUCT = p.struct;
  node.hammAutoSrc = p.nodeId;
  fillPreviewBoxes(node);
}

// 三个文本框统一填充：没手改过就跟着自动结果走，手改了就保留他的版本；
// 想恢复自动生成：把该框清空再运行一次。
//
// 2026-09-25 修正：HammPromptPreview（右侧预览面板）每次运行都要强制刷新为最新结果，
// 不能保留旧工作流残留的文本或手改标记——否则用户看到的是上一次/不相关的提示词。
function fillPreviewBoxes(node) {
  ensureAllBoxes(node);
  forceBoxVisible(node);
  const isPreview = isPreviewNodeOf(node);
  const fill = (def, text) => {
    if (typeof text !== "string") return;   // 这次没送这个值，保持原样别清空
    const w = node.hammBoxes && node.hammBoxes[def.name];
    if (!w) return;
    const cur = String(w.value || "");
    // preview 节点是结果展示面板，运行后必须显示当前上游/本次运行的提示词；
    // 提示词节点上的结构化框仍保留「手改优先」的语义。
    if (isPreview || !boxEdited(node, def) || !cur) {
      if (cur !== text) {
        w.value = text;
        // DOM 文本框必须同步改 inputEl.value，否则只改对象属性不刷新显示
        if (w.inputEl && w.inputEl.value !== text) w.inputEl.value = text;
        if (w.element && w.element.value !== text) w.element.value = text;
      }
      if (node.properties && node.properties.hammEdited) {
        node.properties.hammEdited[def.name] = false;
      }
    }
  };
  // 按名字取数据源，加/减框（如已下线的 ② 反向框）都不用改这里
  const SRC = { positive: node.hammPreviewCN, negative: node.hammPreviewNEG };
  for (const def of BOX_DEFS) fill(def, SRC[def.name]);
  layoutPreviewNode(node);
}

// 「控件区从哪开始」这个量必须精确给对，否则两种错法各占一半：
//
//   前端 _arrangeWidgets 里  控件起点 y = widgets_start_y ?? (槽位区高度 + 2)
//   前端 computeSize 里      节点高 = max(槽位区高度, 控件栈高度 + widgets_start_y)
//
// 也就是说 widgets_start_y 是**控件栈的绝对起点**（相对节点 body），不是「额外偏移」。
//   · 给 null  → 框架自己按槽位区算，本来是对的，但某些路径下会被反复加高（见 git 历史）；
//   · 给 0     → 控件从 body 顶部开始，直接盖住右侧 outputs 那一列
//                （实测 HammPromptImage 输出口在 body y=0..40，
//                 而 subject 控件 y=0..58，标签被框住 —— 用户报的「文本框盖住字」）。
//
// 正解：按槽位行数把起点算出来，再加上框架兜底用的那 2px 间距。
// computeSize 那边是 max()，所以这个值不会让节点高度自增长。
const SLOT_ROW_H = 20;   // LiteGraph.NODE_SLOT_HEIGHT

// 真实（非控件）输入口与输出口各占一行，取较多的那一侧
function slotAreaHeight(node) {
  const rowsIn = (node.inputs || []).filter((i) => i && !(i.widget && i.widget.name)).length;
  const rowsOut = (node.outputs || []).length;
  return Math.max(rowsIn, rowsOut) * SLOT_ROW_H;
}

function ensureWidgetsStartY(node) {
  const want = slotAreaHeight(node) + 2;
  if (node.widgets_start_y === want) return;
  node.widgets_start_y = want;
  if (node.graph && node.graph.setDirtyCanvas) node.graph.setDirtyCanvas(true, true);
}

// 真输入口（local_llm 这种 HAMM_LLM 连线口）不参与前端的控件口重排：
// _arrangeWidgetInputSlots 只摆「有同名控件」的口，剩下的 pos 永远不会被刷新，
// 会停在节点创建时那个错位置上（实测 local_llm.pos = [10, 440]，
// 正好压在 elements_text 那一行 —— 标签从结构化提示词框底下露出来，看着像被框盖住）。
// 这里把这类口按行摆到 body 顶部的槽位带里（左列，和右侧输出口同一排）。
// pos 是**节点内**坐标，boundingRect 由前端的 _measureSlot 从它推出来。
function placeRealInputSlots(node) {
  if (!node.inputs || !node.inputs.length) return;
  let row = 0;
  let moved = false;
  for (const inp of node.inputs) {
    if (!inp || (inp.widget && inp.widget.name)) continue;   // 控件口交给前端摆
    const want = [SLOT_ROW_H * 0.5, SLOT_ROW_H * 0.5 + row * SLOT_ROW_H];
    row += 1;
    if (!inp.pos || inp.pos[0] !== want[0] || inp.pos[1] !== want[1]) {
      inp.pos = want;
      moved = true;
    }
  }
  if (!moved) return;
  if (typeof node.arrange === "function") {
    try { node.arrange(); } catch (e) { /* 某些前端版本没有 arrange，忽略 */ }
  }
  if (node.graph && node.graph.setDirtyCanvas) node.graph.setDirtyCanvas(false, true);
}

// 节点显示名改过两次：旧 title 是按当时的值存进 JSON 的，不会跟着
// NODE_DISPLAY_NAME_MAPPINGS 变。这里做等价替换：还是旧默认名就顺手改成新的
// （用户自定义过就不动）。
const RETIRED_TITLES = {
  "Hamm 提示词预览（中英对照）": "Hamm 提示词预览（中文）",
  "Hamm 提示词预览（要素 / 中文 / 英文）": "Hamm 提示词预览（中文）",
  "Hamm 提示词预览（中文 / 英文）": "Hamm 提示词预览（中文）",
  "Hamm 分镜批量 (中文→英文)": "Hamm 分镜批量",
};

function migrateTitle(node) {
  if (!node || !node.title) return;
  const nt = RETIRED_TITLES[node.title];
  if (nt) node.title = nt;
}

// 预览节点的输入全部由连线喂进来，后端那几个多行文本控件留着只会撑满节点、
// 把画布上画的预览和复制按钮盖住（DOM 元素永远在 canvas 之上）。
// 所以进场就把它们全收起 —— 唯独我们挂的三个显示文本框要留着。
function hideAllWidgets(node) {
  if (!node.widgets) return;
  for (const w of node.widgets) {
    if (BOX_NAMES.indexOf(w.name) >= 0) continue;
    applyHidden(node, w, true);
  }
}

// 文本框的像素高度 = 起步高度（BOX_MIN_LINES 行）+ 内容行数（按节点宽度换行）× 行高，
// 行数封顶 maxLines 后框内滚动。
//
// 在自动高度之上再叠一层「用户用底部把手拖出来的额外高度」：
// 存 node.properties 里随工作流保存，内容重排也不会把它冲掉；
// 双击把手复位成 0，回到纯自动高度。
// 注：v24 起删掉标题条上的「自动」按钮 —— 框天生就能拖，不需要那个按钮。
function boxExtraH(node, def) {
  const m = node.properties && node.properties.hammBoxExtra;
  const v = m ? Number(m[def.name]) : 0;
  return Number.isFinite(v) && v > 0 ? v : 0;
}

function setBoxExtra(node, def, px) {
  const v = Math.max(0, Math.min(4000, Math.round(px)));
  node.properties = node.properties || {};
  node.properties.hammBoxExtra = node.properties.hammBoxExtra || {};
  node.properties.hammBoxExtra[def.name] = v;
}

// 空框（或内容很少）时的起步高度：给足行数，开箱就是一个够大的框。
// 用户觉得还不够高，直接拖把手加高即可（拖出来的部分另存 hammBoxExtra）。
const BOX_MIN_LINES = 12;

function boxContentHeight(node, def) {
  const w = (node.hammBoxes || {})[def.name];
  const v = w ? String(w.value || "") : "";
  const n = v ? wrap(v, wrapPx(node)).length : 0;
  const auto = Math.max(BOX_MIN_LINES, Math.min(n, def.maxLines)) * LINE_H + 18;
  return auto + boxExtraH(node, def);   // 自动高度 + 用户拖出来的部分
}

// ① 框下面要给「复制按钮行」留高度（提示词节点的结构化框同款）
const COPY_ROW_H = 30;

// 框架分配给这个 widget 的高度 = 文本框本身 + 上方标题区 + 容器自带间距
function boxHeight(node, def) {
  return boxContentHeight(node, def) + BOX_TOP_PAD;
}

// 标题条做成真正的 DOM 元素塞进文本框的容器里（absolute 贴容器顶部）。
// 为什么不在画布上画：canvas 用的 widget.last_y 会滞后一帧（节点重排后仍是旧布局），
// 画出来就会错位、被文本框盖住；挂在 DOM 容器里则永远跟着文本框走。
function ensureBoxTitleEl(node, def, w) {
  const ta = w && (w.inputEl || w.element);
  const p = ta && ta.parentElement;
  if (!p) return;
  if (w.hammTitleEl && w.hammTitleEl.parentElement === p) {
    // v21~v23 的「自动」按钮已下架：框默认就能拖高，不需要这个按钮，顺手清掉
    const oldFit = w.hammTitleEl.querySelector("[data-hamm-fitbtn]");
    if (oldFit) { oldFit.remove(); w.hammFitBtn = null; }
    ensureBoxCopyBtn(node, def, w);   // 容器被框架重建过 → 按钮跟着重挂（幂等）
    return;
  }
  const t = document.createElement("div");
  t.textContent = def.label;
  t.title = def.label;
  t.setAttribute("data-hamm-title", def.name);
  // 右侧留出复制按钮的位置，避免长标题压住按钮（标题自身仍 ellipsis）
  t.style.cssText =
    "position:absolute;left:0;top:0;width:100%;height:" + TITLE_H + "px;" +
    "line-height:" + TITLE_H + "px;padding:0 60px 0 8px;box-sizing:border-box;" +   // 右侧留给「复制」按钮
    "font:bold 12px sans-serif;color:rgba(16,16,20,0.92);background:" + def.color + ";" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" +
    "pointer-events:none;z-index:2;";
  p.appendChild(t);
  w.hammTitleEl = t;
  ensureBoxCopyBtn(node, def, w);
}

// 标题条右侧的小「复制」按钮：一键复制这个框里的全文。
// 挂成 DOM 按钮而不是画在画布上 —— 不占节点高度，也不会和 litegraph 抢鼠标。
// 注意标题条本身是 pointer-events:none（不挡框），所以按钮要单独开 auto。
function ensureBoxCopyBtn(node, def, w) {
  const t = w.hammTitleEl;
  if (!t) return;
  if (w.hammBoxCopyBtn && w.hammBoxCopyBtn.parentElement === t) return;
  const btn = document.createElement("button");
  btn.setAttribute("data-hamm-boxcopy", def.name);
  btn.textContent = "复制";
  btn.title = "复制这个框里的全文（也可以直接在框里拖选、Ctrl+C）";
  btn.style.cssText =
    "position:absolute;right:6px;top:3px;height:15px;line-height:15px;padding:0 8px;" +
    "border:none;border-radius:3px;background:rgba(20,20,26,0.55);color:#fff;" +
    "font:bold 10px sans-serif;cursor:pointer;pointer-events:auto;z-index:3;";
  btn.addEventListener("mouseenter", () => {
    if (btn.textContent === "复制") btn.style.background = "rgba(20,20,26,0.78)";
  });
  btn.addEventListener("mouseleave", () => {
    if (btn.textContent === "复制") btn.style.background = "rgba(20,20,26,0.55)";
  });
  btn.addEventListener("mousedown", (e) => e.stopPropagation());   // 别让画布抢走按下事件
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    e.preventDefault();
    const ta = w.inputEl || w.element;
    const text = String((ta && ta.value) || w.value || "");
    if (!text.trim()) { flashBoxCopyBtn(btn, "空", false); return; }
    copyToClipboard(text).then((ok) => flashBoxCopyBtn(btn, ok ? "已复制 ✓" : "失败", ok));
  });
  t.appendChild(btn);
  w.hammBoxCopyBtn = btn;
}

function flashBoxCopyBtn(btn, txt, ok) {
  btn.textContent = txt;
  btn.style.background = ok ? "#2f7a52" : "#8a3b3b";
  setTimeout(() => {
    btn.textContent = "复制";
    btn.style.background = "rgba(20,20,26,0.55)";
  }, 1500);
}

// ---------------------------------------------------------------- 框底部拖拽把手
// 框的高度本来是「内容几行就几行」（封顶 maxLines，超了框内滚动），所以拉不动。
// 这里在框正下方的空隙里挂一条把手：上下拖动 = 给这个框加/减高度。
// 高度记在 node.properties.hammBoxExtra[def.name]，随工作流保存；双击复位成自动高度。
function relayoutAfterResize(node) {
  applyBoxHeights(node);
  layoutPreviewNode(node);
  if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
}

function ensureBoxGrip(node, def, w, contentH) {
  const ta = w && (w.inputEl || w.element);
  const p = ta && ta.parentElement;
  if (!p) return;
  const top = TITLE_H + contentH;
  if (w.hammGrip && w.hammGrip.parentElement === p) {
    if (w.hammGrip.style.top !== top + "px") w.hammGrip.style.top = top + "px";
    return;
  }
  const g = document.createElement("div");
  g.setAttribute("data-hamm-grip", def.name);
  g.title = "上下拖动可调整框的高度；双击复位为自动高度";
  g.style.cssText =
    "position:absolute;left:0;width:100%;height:9px;" +
    "display:flex;align-items:center;justify-content:center;" +
    "cursor:ns-resize;pointer-events:auto;z-index:3;";
  const bar = document.createElement("div");
  bar.style.cssText =
    "width:54px;height:3px;border-radius:2px;background:rgba(255,255,255,0.45);pointer-events:none;";
  g.appendChild(bar);
  g.addEventListener("mouseenter", () => { bar.style.background = "rgba(255,255,255,0.9)"; });
  g.addEventListener("mouseleave", () => { bar.style.background = "rgba(255,255,255,0.45)"; });
  // 双击复位。用 click 的 detail 判定（第二次 click 的 detail===2），
  // 比只挂 dblclick 稳 —— dblclick 要求两次点击落在同一元素上，
  // 而把手会随框高变化移动，第二次容易落空。
  const resetHeight = (e) => {
    e.stopPropagation();
    setBoxExtra(node, def, 0);
    relayoutAfterResize(node);
  };
  g.addEventListener("click", (e) => { if (e.detail >= 2) resetHeight(e); });
  g.addEventListener("dblclick", (e) => resetHeight(e));   // 双保险（幂等）
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();                       // 别让画布顺手拖节点
    const startY = e.clientY;
    const startExtra = boxExtraH(node, def);
    const scale = (app.canvas && app.canvas.ds && app.canvas.ds.scale) || 1;
    const onMove = (ev) => {
      // 画布缩放要换算，否则缩小时拖 1px 框长 2px
      const dy = (ev.clientY - startY) / (scale || 1);
      setBoxExtra(node, def, startExtra + dy);   // setBoxExtra 内部已 clamp 到 0..4000
      relayoutAfterResize(node);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      // 松手收尾再排一次：拖拽中间几次可能落在画布重绘的两帧之间，
      // 补排一次保证停下来时的框高/节点高和 hammBoxExtra 完全一致。
      relayoutAfterResize(node);
      requestAnimationFrame(() => relayoutAfterResize(node));
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
  p.appendChild(g);
  w.hammGrip = g;
}

// DOM 文本框的实际像素高度/位置由它自己的样式决定（computedHeight 只管节点布局）。
// 用 position:absolute + top 精确让出上方标题区（marginTop 会被容器布局干扰，
// 实测会偏 10px，导致标题条与框错位），标题条由 ensureBoxTitleEl 挂进同一容器。
function applyBoxHeights(node) {
  for (const def of BOX_DEFS) {
    const w = (node.hammBoxes || {})[def.name];
    if (!w) continue;
    const content = boxContentHeight(node, def);
    const alloc = content + BOX_TOP_PAD;
    if (w.computedHeight !== alloc) w.computedHeight = alloc;

    const ta = w.inputEl || w.element;
    if (!ta || !ta.style) continue;
    const s = ta.style;
    if (s.position !== "absolute") s.position = "absolute";
    if (s.top !== TITLE_H + "px") s.top = TITLE_H + "px";
    if (s.left !== "0px") s.left = "0px";
    if (s.width !== "100%") s.width = "100%";
    if (s.height !== content + "px") s.height = content + "px";
    const p = ta.parentElement;
    if (p && p.style && p.style.height !== alloc + "px") p.style.height = alloc + "px";
    // 容器被框架重建过 → 标题元素跟着重挂（幂等）
    if (w.hammTitleEl && w.hammTitleEl.parentElement !== p) w.hammTitleEl = null;
    ensureBoxTitleEl(node, def, w);
    ensureBoxGrip(node, def, w, content);   // 框底边的拖拽把手（调高/调矮）
    // 复制按钮已搬到提示词节点的结构化框下方；旧版本留在预览节点里的按钮清掉
    if (def.name === EL_WIDGET && w.hammCopyRow) {
      w.hammCopyRow.remove();
      delete w.hammCopyRow;
      delete w.hammCopyBtnEl;
    }
  }
}

// ---------------------------------------------------------------- 「重置」元素选项
// 把节点上的元素选项一键还原成「未选」状态：
//   下拉 → 「不指定」（没有这项的用带「默认」的那项，再没有就用第一项）
//   文本框 → 清空
//   数值（时长）→ 回控件默认值
// 范围严格限定在 UPSTREAM_ROWS 列出的元素字段，
// 不碰 llm_mode / local_model / API key 等技术控件 —— 重置风格不该把模型选择也清掉。
function widgetValuesOf(w) {
  const vals = w && w.options && w.options.values;
  if (!Array.isArray(vals) || !vals.length) return null;
  // 带 step 的 FLOAT 会被前端渲染成滑条，options.values 是**数字档位数组** ——
  // 那是滑块刻度，不是下拉选项，不能当 combo 处理（否则时长会被当成选项取首档）。
  if (!vals.some((v) => typeof v === "string")) return null;
  return vals.map((v) => {
    if (typeof v === "string") return v;
    if (v && typeof v === "object") return String(v.value != null ? v.value : v.name != null ? v.name : "");
    return String(v == null ? "" : v);
  });
}

// 数值控件（时长等）的出厂默认值只有后端知道：新前端数字 widget 的 options 里
// 没有 default，只有 min/max/step，照 min 重置等于把时长打回下限。
// 所以后端在 /hammprompt/desc 里顺带给一张 defaults 表，前端只读。
function hammBackendDefault(node, name) {
  const m = (MO_DESC || {}).defaults || {};
  const g = m[node.type] || {};
  const v = g[name];
  return typeof v === "number" ? v : undefined;
}

function defaultElementValue(node, name, w) {
  const vals = widgetValuesOf(w);
  if (vals) {
    const none = vals.find((v) => v.indexOf("不指定") === 0);
    if (none) return none;
    const dflt = vals.find((v) => v.indexOf("默认") >= 0);
    if (dflt) return dflt;
    return vals[0];
  }
  const isNum = typeof w.value === "number" || w.type === "number" || w.type === "slider" ||
                (w.options && typeof w.options.min === "number");
  if (isNum) {
    const bd = hammBackendDefault(node, name);
    if (typeof bd === "number") return bd;
    const d = w.options && w.options.default;
    if (typeof d === "number") return d;
    const mn = w.options && w.options.min;
    return typeof mn === "number" ? mn : 0;
  }
  return "";                                  // 多行文本：重置成空白
}

function resetElementWidgets(node) {
  const defs = UPSTREAM_ROWS[node.type];
  if (!defs || !node.widgets) return 0;
  let changed = 0;
  for (const pair of defs) {
    const w = node.widgets.find((x) => x.name === pair[0]);
    if (!w) continue;
    let nv;
    try { nv = defaultElementValue(node, pair[0], w); } catch (e) { continue; }
    if (nv === undefined) continue;
    if (w.value === nv) continue;
    try { w.value = nv; } catch (e) { continue; }   // 挂过 value setter 的控件会顺带触发刷新
    const el = w.inputEl || w.element;
    if (el && "value" in el && el.value !== nv) el.value = nv;
    if (typeof w.callback === "function") { try { w.callback(nv); } catch (e) { /* 忽略 */ } }
    changed++;
  }
  // 结构化框：清掉「手改」标记并强制重算（哪怕值没变，也要把用户手改的正文还原成自动生成）
  node.properties = node.properties || {};
  node.properties.hammPromptElTouched = false;
  node.hammPromptElSnap = null;
  if (isPromptNodeOf(node)) syncPromptElText(node);
  if (typeof node.arrange === "function") { try { node.arrange(); } catch (e) { /* 忽略 */ } }
  if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
  // 值没变也可能改了手改的正文，所以「有没有动过」按控件算，重置本身总是生效
  return changed;
}

// ① 框正下方的小「复制」按钮 + 灰色提示语，做成 DOM 元素挂进同一容器：
// 点击是原生 button 事件，不用再和 litegraph 抢鼠标；位置永远贴着 ① 框底。
function ensureCopyRowEl(node, w, contentH) {
  const ta = w.inputEl || w.element;
  const p = ta && ta.parentElement;
  if (!p) return;
  const top = TITLE_H + contentH + 2;
  if (w.hammCopyRow && w.hammCopyRow.parentElement === p) {
    if (w.hammCopyRow.style.top !== top + "px") w.hammCopyRow.style.top = top + "px";
    return;
  }
  const row = document.createElement("div");
  row.setAttribute("data-hamm-copyrow", EL_WIDGET);
  row.style.cssText =
    "position:absolute;left:0;width:100%;height:" + COPY_ROW_H + "px;" +
    "display:flex;align-items:center;gap:8px;padding:0 6px;box-sizing:border-box;z-index:2;";
  const btn = document.createElement("button");
  btn.textContent = "复制";
  btn.setAttribute("data-hamm-copybtn", "1");
  btn.title = "复制「优化指令 + 要素清单」，整段可粘贴到豆包 / DeepSeek 让它帮你优化";
  btn.style.cssText =
    "flex:0 0 auto;height:22px;padding:0 16px;border:none;border-radius:5px;" +
    "background:#2b4a6b;color:#eaf2ff;font:bold 12px sans-serif;cursor:pointer;";
  btn.addEventListener("mouseenter", () => { if (btn.textContent === "复制") btn.style.background = "#395d85"; });
  btn.addEventListener("mouseleave", () => { if (btn.textContent === "复制") btn.style.background = "#2b4a6b"; });
  btn.addEventListener("click", (e) => { e.stopPropagation(); doCopy(node); });
  const hint = document.createElement("span");
  hint.setAttribute("data-hamm-copyhint", "1");
  hint.textContent = "复制到豆包 / DeepSeek 优化";
  hint.style.cssText =
    "flex:0 1 auto;min-width:0;font:11px sans-serif;color:rgba(255,255,255,0.55);" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
  // 右侧「重置」：一键把所有元素选项还原成未选状态（下拉回「不指定」、输入框清空）。
  // 仍做成 DOM 按钮 —— 不占节点高度，也不会和 litegraph 抢鼠标事件。
  const rst = document.createElement("button");
  rst.textContent = "重置";
  rst.setAttribute("data-hamm-resetbtn", "1");
  rst.title = "重置所有元素选项：下拉回「不指定」、输入框清空" +
              "（模型 / 模式 / API 等技术设置不受影响）";
  rst.style.cssText = RESET_BTN_CSS;
  rst.addEventListener("mouseenter", () => {
    if (rst.textContent === "重置") rst.style.background = "rgba(255,255,255,0.16)";
  });
  rst.addEventListener("mouseleave", () => {
    if (rst.textContent === "重置") rst.style.background = RESET_BTN_BG;
  });
  rst.addEventListener("mousedown", (e) => e.stopPropagation());
  rst.addEventListener("click", (e) => {
    e.stopPropagation();
    e.preventDefault();
    const n = resetElementWidgets(node);
    flashResetBtn(rst, n);
  });
  row.appendChild(btn);
  row.appendChild(hint);
  row.appendChild(rst);
  p.appendChild(row);
  w.hammCopyRow = row;
  w.hammCopyBtnEl = btn;
  w.hammResetBtnEl = rst;
}

const RESET_BTN_BG = "rgba(255,255,255,0.07)";
const RESET_BTN_BD = "rgba(255,255,255,0.38)";
const RESET_BTN_CSS =
  "flex:0 0 auto;margin-left:auto;height:22px;padding:0 12px;border-radius:5px;" +
  "border:1px solid " + RESET_BTN_BD + ";background:" + RESET_BTN_BG + ";" +
  "color:rgba(235,242,255,0.9);font:bold 12px sans-serif;cursor:pointer;";

// 反馈直接写在按钮上：改了几项就说重置了几项，本来就是空的就说「已是初始」
function flashResetBtn(btn, changed) {
  if (btn.hammResetTimer) clearTimeout(btn.hammResetTimer);
  btn.textContent = changed ? "已重置 " + changed + " 项" : "已是初始";
  btn.style.background = changed ? "#2f7a52" : RESET_BTN_BG;
  btn.style.borderColor = changed ? "#2f7a52" : RESET_BTN_BD;
  btn.hammResetTimer = setTimeout(() => {
    btn.textContent = "重置";
    btn.style.background = RESET_BTN_BG;
    btn.style.borderColor = RESET_BTN_BD;
    btn.hammResetTimer = null;
  }, 1500);
}

// 用户手动编辑过就在 properties 上打个标记（会随工作流保存），
// 这样重新运行不会把他改好的内容冲掉；清空文本框再运行即可恢复自动生成。
function bindBoxInput(node, w, def) {
  if (w.hammBound) return;
  const ta = w.inputEl;
  if (!ta || !ta.addEventListener) return;
  ta.addEventListener("input", () => {
    node.properties = node.properties || {};
    node.properties.hammEdited = node.properties.hammEdited || {};
    node.properties.hammEdited[def.name] = true;
    if (ta.value !== w.value) w.value = ta.value;
  });
  w.hammBound = true;
}

function ensureBoxWidget(node, def) {
  node.hammBoxes = node.hammBoxes || {};
  if (node.hammBoxes[def.name]) {
    bindBoxInput(node, node.hammBoxes[def.name], def);
    return node.hammBoxes[def.name];
  }
  let w = node.widgets && node.widgets.find((x) => x.name === def.name);
  if (!w) {
    try {
      const r = ComfyWidgets.STRING(node, def.name,
        ["STRING", { multiline: true, default: "" }], app);
      w = r && r.widget;
    } catch (e) { w = null; }
  }
  if (!w) w = node.addWidget("multiline", def.name, "", () => {});
  if (!w) return null;
  w.name = def.name;
  w.label = def.label;
  w.value = typeof w.value === "string" ? w.value : "";
  w.computeSize = function (width) {
    return [width != null ? width : Math.max(240, (node.size[0] || 500) - 16),
            boxHeight(node, def)];
  };
  node.hammBoxes[def.name] = w;
  if (def.name === EL_WIDGET) node.hammElWidget = w;
  applyBoxHeights(node);
  bindBoxInput(node, w, def);
  return w;
}

function ensureAllBoxes(node) {
  for (const def of BOX_DEFS) ensureBoxWidget(node, def);
  return node.hammBoxes;
}

function boxEdited(node, def) {
  const m = node.properties && node.properties.hammEdited;
  return !!(m && m[def.name]);
}

// ================================================================ 「连了线也不许藏框」
// 新版前端 DomWidget 的可见性判据（实测运行时源码）：
//     isVisible() { return !this.hidden && !this.computedDisabled && this.node.isWidgetVisible(this) }
// 而 LGraphNode.updateComputedDisabled() 里：
//     widget.computedDisabled = widget.disabled || this.getSlotFromWidget(widget)?.link != null
//
// 即：**只要该控件对应的输入口连了线，框架就把它算成「已失效」→ 直接隐藏**。
// 预览节点的 ① 框，其输入口 positive 恰恰就是「接提示词节点」的那个口，必然连着线，
// 于是框架把 ① 框的 DOM 容器 v-show 成 display:none —— 用户看到的就是
// 「① 框一片空白、② 框（没连线）正常」。这就是「跑完队列看不到预览词」的真正原因：
// 值早就填进去了，是框被框架藏了。
//
// 修法：把 computedDisabled 这一条从判据里摘掉，其余门控原样保留
// （hidden 仍生效；节点折叠 / 离屏仍会隐藏，所以不会出现「离屏浮框」）。
function forceBoxVisible(node) {
  for (const def of BOX_DEFS) {
    const w = (node.hammBoxes || {})[def.name];
    if (!w) continue;
    if (!w.hammPatchedVisible) {
      if (typeof w.isVisible === "function") {
        w.isVisible = function () {
          if (this.hidden) return false;
          const n = this.node;
          if (!n || typeof n.isWidgetVisible !== "function") return true;
          return n.isWidgetVisible(this);
        };
      }
      w.hammPatchedVisible = true;
    }
    // 顺手清掉这个标志：画布层会按它给控件乘 0.5 透明度，清了才不是灰的
    if (w.computedDisabled) w.computedDisabled = false;

    // 二道兜底：万一以后的前端版本判据里又多一条，这里直接把容器从 display:none 拉回来。
    // 只在「节点确实落在画布可视区」时才拉，避免离屏时把框浮在界面上。
    const el = w.inputEl || w.element;
    if (!el) continue;

    // 同一个 disabled 判定还有第二个副作用：框架把 DOM 的 pointer-events 也写成 none。
    // 于是框看得见却点不进去 —— 选不中文本、Ctrl+C 复制不了、长文本也滚不动。
    // （实测：textarea.pointerEvents === "none"；鼠标拖选后 document.activeElement 仍是 CANVAS）
    // 这里只把 textarea 自己恢复成可交互；父容器继续 none，节点拖动行为不受影响
    // （父级 pointer-events:none 不阻止子元素显式设成 auto 后接收事件）。
    if (el.style) {
      if (el.style.getPropertyValue("pointer-events") !== "auto") {
        el.style.setProperty("pointer-events", "auto", "important");
      }
      if (el.style.getPropertyValue("user-select") !== "text") {
        el.style.setProperty("user-select", "text", "important");
        el.style.setProperty("-webkit-user-select", "text", "important");
      }
    }

    const cont = el.parentElement;
    if (cont && cont.style.display === "none" && nodeInViewport(node)) cont.style.display = "";
  }
}

// 节点是否落在画布当前可视区（visible_area 是 [x, y, w, h]）
function nodeInViewport(node) {
  try {
    const c = app.canvas;
    const va = c && c.ds && c.ds.visible_area;
    if (!va) return true;
    const x1 = va[0], y1 = va[1], x2 = va[0] + va[2], y2 = va[1] + va[3];
    const nx1 = node.pos[0], ny1 = node.pos[1];
    const nx2 = nx1 + (node.size ? node.size[0] : 0);
    const ny2 = ny1 + (node.size ? node.size[1] : 0);
    return !(nx1 > x2 || nx2 < x1 || ny1 > y2 || ny2 < y1);
  } catch (e) {
    return true;
  }
}

function syncVisibility(node) {
  if (!node.widgets) return;
  if (isPreviewNodeOf(node)) {
    hideAllWidgets(node);
    return;                     // 预览节点的尺寸由 onExecuted 里的 layout 决定
  }
  const mode = readMode(node);
  // 读不到模式时不乱动：等 callback / onDrawBackground 拿到真实值再说
  if (!mode) return;
  // 严格等值匹配，杜绝「本地优先→API兜底」这类旧值让两组同时命中
  const isLocal = mode === MODE_LOCAL;
  const isApi = mode === MODE_API;
  for (const w of node.widgets) {
    if (API_W.includes(w.name)) {
      applyHidden(node, w, !isApi);
    } else if (w.name === "local_model") {
      // API 模式下不需要选本地模型；本地/关闭模式下保留，方便切换
      applyHidden(node, w, isApi);
    } else if (LOCAL_W.includes(w.name)) {
      applyHidden(node, w, !isLocal);
    }
    fixValue(w);
  }
  // 先把显隐变化落进布局：arrange() 会按新的可见控件集合重新分配每个控件的 y 与
  // computedHeight。少了这一步，被重新显示的控件这一帧还带着隐藏时的旧 y（错位置）
  // 和空高度，而 DOM 覆盖层正是用这两个值定位 → 肉眼就是「切模式时闪一下错位」。
  if (typeof node.arrange === "function") {
    try { node.arrange(); } catch (e) { /* 老版本没有 arrange，靠 applyHidden 里的兜底高度 */ }
  }
  // 宽度只能增不能减！computeSize()[0] 是框架算的「最小宽度」，直接 setSize(cs)
  // 会把用户拉宽的节点压窄 → 结构化文本框按新宽度重新换行 → 行数变化 → 高度跳一行
  // （15px）→ 它下面的控件整列上下跳一下 = 用户看到的「切换模式一瞬间错位」。
  // 压窄还会在下一帧被 applyPromptElBox 的宽度兜底拉回来，造成来回抖两下。
  const cs = node.computeSize();
  node.setSize([Math.max(node.size[0] || 0, cs[0] || 0), cs[1]]);
  if (typeof node.arrange === "function") {
    try { node.arrange(); } catch (e) { /* 忽略 */ }
  }
  node.setDirtyCanvas(true, true);
}

// 新版前端里 STRING 是 DOM widget，元素可能是懒创建的：
// 隐藏那一刻 inputEl/element 还不存在，后面才被补上，于是又冒出来。
// 每帧兜底：只要该控件处于隐藏态，就强制把它的 DOM 元素藏掉。
function enforceDomHidden(node) {
  if (!node.widgets) return;
  for (const w of node.widgets) {
    if (w.type !== "hidden" && !w.hidden) continue;
    if (w.inputEl && w.inputEl.style.display !== "none") w.inputEl.style.display = "none";
    if (w.element && w.element.style.display !== "none") w.element.style.display = "none";
  }
}

// 等宽 12px 下：全角/中日韩按 12px 算，其余按 7.3px。按像素而不是「字符个数」换行，
// 中文长句就不会像以前那样溢出节点右边界。
function wrap(text, maxPx) {
  const lines = [];
  const wOf = (ch) => (/[\u2E80-\uFFEF\uFF00-\uFF60]/.test(ch) ? 12 : 7.3);
  for (const raw of String(text).split("\n")) {
    if (!raw.length) { lines.push(""); continue; }
    let cur = "", curW = 0;
    for (const ch of raw) {
      const cw = wOf(ch);
      if (cur && curW + cw > maxPx) { lines.push(cur); cur = ""; curW = 0; }
      cur += ch;
      curW += cw;
    }
    if (cur) lines.push(cur);
  }
  return lines;
}

function wrapPx(node) {
  return Math.max(180, (node.size[0] || 500) - 26);
}

// ================================================================ 复制功能
// 后端 structure 文本被 【诊断】 分成两段：前半是「用户选了哪些元素」，
// 后半是引擎/状态诊断。复制只取前半段，诊断行只用来排查，不进剪贴板。
const DIAG_MARK = "【诊断】";

// 复制出去的是「一句优化指令 + 按元素名分行的选择清单」，不带扩写后的正文 ——
// 让豆包按这份清单重新优化，而不是被我们已经写好的内容带偏。
//
// 顺序（2026-09-18 用户要求）：**指令在前，待优化词在后**，指令以「待优化词如下：」收尾 ——
// 这样整段粘进豆包 / DeepSeek 就是一个完整请求，不用再手动补一句话。
// 文生图现在只出中文（positive 就是中文段），所以指令要中文；
// 视频/分镜仍走英文（MiniMax H3 官方格式只吃英文）。
const COPY_INSTRUCTION = {
  image: "请按下面的元素格式帮我优化提示词：保留已选的人物、风格、场景、动作、光线等要素不变，" +
         "补全镜头、材质、光影层次等细节，只输出一段可直接使用的中文提示词，" +
         "不要解释、不要分点说明，待优化词如下：",
  video: "请按下面的元素格式帮我优化视频提示词：保留已选的主体、场景、运镜与时长不变，" +
         "补全动作过程与光影变化等细节，严格按 MiniMax H3 官方格式输出" +
         "（integrated_multimodal_description / overall_soundscape / non_diegetic_music），" +
         "只用英文，不要解释，待优化词如下：",
  story: "请按下面的元素格式帮我优化分镜提示词：保持固定角色与统一风格不变，" +
         "逐镜补全镜头、材质、光影层次等细节，每行输出一镜的纯英文提示词，" +
         "不要解释、不要分点说明，待优化词如下：",
};

function splitStructure(text) {
  const s = (text || "").trim();
  if (!s) return { el: "", diag: "" };
  const i = s.indexOf(DIAG_MARK);
  if (i < 0) return { el: s, diag: "" };
  return { el: s.slice(0, i).trim(), diag: s.slice(i + DIAG_MARK.length).trim() };
}

// 空选项（后端写的是「（空）」）在预览和复制里都是噪声，直接丢掉；
// 旧工作流里 structure 还带诊断/负向残留，也一并过滤掉。
const DROP_ROW_RE = /^(负向|负向预设|补充负向|类型|输出语言|智能扩写|扩写状态|来源)\s*[:：]/;

// ---------------------------------------------------------------- 兜底：预览口没连线
// 预览节点可以完全不连线。一旦连了 negative 之类，很多人就不会走「零连线自动抓」，
// 结果 ① 面板只剩半截——看着像 bug。这里直接在画布上顺着连线找到上游
// 提示词节点，读它的控件值，现场拼出同一份要素清单（不依赖后端、不改工作流）。
const UPSTREAM_ROWS = {
  HammPromptImage: [
    // 年代排第一行（用户要求：先交代时代背景，再是人物/风格/场景…）
    // 「年代」-> 「时代背景」；「补充正面」-> 「额外说明」（与复制出去喂给豆包/DeepSeek 的词对齐）
    ["era", "时代背景"],
    ["subject", "主体"], ["style", "风格"], ["scene", "地点"], ["action", "动作"],
    ["shot", "景别"], ["lighting", "光线"], ["mood", "氛围"], ["quality", "画质"],
    ["extra_positive", "额外说明"],
  ],
  HammPromptVideo: [
    ["mode", "生成模式"], ["duration", "时长"], ["subject", "主体"],
    ["action", "动作"], ["scene", "场景"], ["style", "风格"],
    ["shot_size", "景别"], ["camera_motion", "运镜"], ["amplitude", "幅度"],
    ["speed", "速度"], ["transition", "转场"], ["shots_zh", "分镜（每行一镜）"],
    ["dialogue", "台词"], ["soundscape", "环境音"], ["music", "配乐"],
  ],
  HammPromptStoryboard: [
    ["shots_zh", "分镜（每行一镜）"], ["role_prefix", "固定角色前缀"],
    ["style_suffix", "固定风格后缀"],
  ],
};

const KIND_BY_TYPE = {
  HammPromptImage: "文生图",
  HammPromptVideo: "文生视频",
  HammPromptStoryboard: "分镜批量",
};

function findUpstreamNode(node) {
  if (!node || !node.inputs) return null;
  const g = node.graph || (typeof app !== "undefined" && app && app.graph);
  if (!g) return null;
  for (const inp of node.inputs) {
    if (!inp || !inp.link) continue;
    const l = g.links && g.links[inp.link];
    if (!l) continue;
    const src = g.getNodeById ? g.getNodeById(l.origin_id) : null;
    if (src && UPSTREAM_ROWS[src.type]) return src;
  }
  // 零连线兜底：用最近一次执行的提示词节点（全局捕获记下的）
  if (hammLastPayload && hammLastPayload.nodeId != null && g.getNodeById) {
    const src = g.getNodeById(hammLastPayload.nodeId);
    if (src && UPSTREAM_ROWS[src.type]) return src;
  }
  return null;
}

function widgetVal(n, name) {
  const w = n && n.widgets && n.widgets.find((x) => x.name === name);
  if (!w) return "";
  const v = w.value;
  if (v === undefined || v === null || typeof v === "boolean") return "";
  return String(v).trim();
}

// ================================================================ 提示词节点：结构化提示词框
// 用户要求把「结构化提示词」和输入选择合并在一起：它是提示词节点里的
// elements_text 控件（紧跟输入选择，运行时随队列传回后端，按这份内容扩写）。
// 前端负责：标题条 + 复制按钮 + 随上方输入/下拉实时刷新（改过下拉会覆盖手改内容）。
const PROMPT_EL_TITLE = "结构化提示词（随上方选择实时变化 · 可改 · 运行时按这份内容扩写）";
const PROMPT_EL_MAX_LINES = 22;
const PROMPT_EL_COLOR = "#5fae8c";

function isPromptNodeOf(node) {
  return !!(node && node.type && UPSTREAM_ROWS[node.type]);
}

function promptElWidget(node) {
  return node.widgets && node.widgets.find((x) => x.name === "elements_text");
}

// 自己（提示词节点）的要素行 —— 与预览节点读上游用的是同一份映射
// 中文考据描述（模板 desc）：与后端喂给大模型的 brief 共用同一份数据源。
// 结构化框里显示的词 = 送进 LLM 的词，用户在框里改的就是模型吃到的。
let MO_DESC = null;            // {"t2i": {"era": {"清": "..."}}}
function loadMoDesc() {
  if (MO_DESC) return Promise.resolve(MO_DESC);
  return api.fetchApi("/hammprompt/desc").then((r) => r.json()).then((d) => {
    MO_DESC = d || {};
    for (const n of app.graph ? app.graph._nodes || [] : []) {
      if (isPromptNodeOf(n)) syncPromptElText(n);   // 数据到了再刷一次框
    }
    return MO_DESC;
  }).catch(() => { MO_DESC = {}; return MO_DESC; });
}

function descOf(group, name, val) {
  const g = (MO_DESC || {})[group] || {};
  const m = g[name] || {};
  return m[val] || "";
}

// 年代的单行写法（唐 -> 唐代 / 50年代 -> 1950年代）。
// 规则只在后端 hamm_core.era_display 里维护一份，前端只读映射 ——
// 前端自己再写一套正则的话，两边一跑偏，后端就反查不回选项名了。
function eraDisplayVal(group, val) {
  const key = group === "video" ? "video_era_display" : "t2i_era_display";
  const m = (MO_DESC || {})[key] || {};
  return m[val] || "";
}

// 值的最终写法：年代用简洁写法（标签已经叫「时代背景」，
// 再跟上「人物与场景符合唐代背景」整句就是重复）；其余带 desc 的类别仍是「值（考据）」。
// 框里看到的 = 送进 LLM 的词，所以后端必须能反查回参数值（见 nodes.apply_elements_overrides）。
function displayValue(group, name, v) {
  if (name === "era") {
    const d = eraDisplayVal(group, v);
    if (d) return d;
  }
  const d = descOf(group, name, v);
  return d ? v + "（" + d + "）" : v;
}

function selfElementRows(node) {
  const rows = [];
  const group = node.type === "HammPromptVideo" ? "video" : "t2i";
  for (const [name, label] of (UPSTREAM_ROWS[node.type] || [])) {
    let v = widgetVal(node, name);
    if (!v) continue;
    v = v.replace(/（(?:自填|可选|空|推荐)）/g, "").replace(/\s*\n+\s*/g, " / ").trim();
    if (!v || v === "不指定") continue;      // 「不指定」= 没选，行整行消失
    if (name === "duration") v += "s";
    // 年代走简洁写法，其余带考据描述的元素展开成「清（清制长袍…）」—— 这就是喂给模型的词
    v = displayValue(group, name, v);
    rows.push(label + "：" + v);
  }
  return rows;
}

function promptElContentH(node) {
  const w = promptElWidget(node);
  const v = w ? String(w.value || "") : "";
  const n = v ? wrap(v, wrapPx(node)).length : 5;
  return Math.max(5, Math.min(n, PROMPT_EL_MAX_LINES)) * LINE_H + 18;
}

function ensurePromptTitleEl(node, w) {
  const ta = w.inputEl || w.element;
  const p = ta && ta.parentElement;
  if (!p) return;
  if (w.hammTitleEl && w.hammTitleEl.parentElement === p) return;
  const t = document.createElement("div");
  t.textContent = PROMPT_EL_TITLE;
  t.setAttribute("data-hamm-prompt-title", "1");
  t.style.cssText =
    "position:absolute;left:0;top:0;width:100%;height:" + TITLE_H + "px;" +
    "line-height:" + TITLE_H + "px;padding:0 8px;box-sizing:border-box;" +
    "font:bold 12px sans-serif;color:rgba(16,16,20,0.92);background:" + PROMPT_EL_COLOR + ";" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" +
    "pointer-events:none;z-index:2;";
  p.appendChild(t);
  w.hammTitleEl = t;
}

// 每帧幂等：最小宽度 / 标题条 / 复制按钮 / 高度 / 定位（容器被框架重建过就重挂）
function applyPromptElBox(node) {
  const w = promptElWidget(node);
  if (!w) return;
  // 最小宽度兜底：新建节点后 node.computeSize() 会把宽度压回很小，
  // 这里每帧兜住下限（用户拉宽到 520 以上不受影响）
  if (node.size[0] < PROMPT_NODE_W) {
    node.size[0] = PROMPT_NODE_W;
    if (typeof node.arrange === "function") { try { node.arrange(); } catch (e) { /* 忽略 */ } }
  }
  const content = promptElContentH(node);
  const alloc = content + BOX_TOP_PAD + COPY_ROW_H;
  if (w.computedHeight !== alloc) w.computedHeight = alloc;

  const ta = w.inputEl || w.element;
  if (!ta || !ta.style) return;
  const s = ta.style;
  if (s.position !== "absolute") s.position = "absolute";
  if (s.top !== TITLE_H + "px") s.top = TITLE_H + "px";
  if (s.left !== "0px") s.left = "0px";
  if (s.width !== "100%") s.width = "100%";
  if (s.height !== content + "px") s.height = content + "px";
  const p = ta.parentElement;
  if (p && p.style && p.style.height !== alloc + "px") p.style.height = alloc + "px";
  if (w.hammTitleEl && w.hammTitleEl.parentElement !== p) w.hammTitleEl = null;
  ensurePromptTitleEl(node, w);
  ensureCopyRowEl(node, w, content);   // 复制按钮行，贴在结构化框正下方

  if (!w.hammBound) {
    const inp = w.inputEl;
    if (inp && inp.addEventListener) {
      inp.addEventListener("input", () => {
        node.properties = node.properties || {};
        node.properties.hammPromptElTouched = true;   // 仅记录；下拉一变仍会实时刷新
        if (inp.value !== w.value) w.value = inp.value;
      });
      w.hammBound = true;
    }
  }
}

// 旧后端（没重启）没有 elements_text 控件时兜底挂一个，UI 照常联动；
// 重启后后端控件就位，同名不会重复挂。
function setupPromptElWidget(node) {
  let w = promptElWidget(node);
  if (!w) {
    try {
      const r = ComfyWidgets.STRING(node, "elements_text",
        ["STRING", { multiline: true, default: "" }], app);
      w = r && r.widget;
    } catch (e) { w = null; }
    if (!w) return;
    // 别让兜底挂载把已拉宽的节点压回去：宽度只增不减
    const cs = node.computeSize();
    node.setSize([Math.max(node.size[0] || 0, cs[0] || 0), cs[1]]);
  }
  if (w.hammElSetup) return;
  w.hammElSetup = true;
  w.name = "elements_text";
  w.computeSize = function (width) {
    return [width != null ? width : Math.max(240, (node.size[0] || 500) - 16),
            promptElContentH(node) + BOX_TOP_PAD + COPY_ROW_H];
  };
  applyPromptElBox(node);
}

// 实时联动：上方输入/下拉任一变化 → 重新生成结构化文本（覆盖手改，实时优先）
function syncPromptElText(node) {
  if (node.hammConfiguring) return;   // 工作流加载中：elements_text 槽还装着待迁移的旧值，别动
  const w = promptElWidget(node);
  if (!w) return;
  const rows = selfElementRows(node);
  const snap = JSON.stringify(rows);
  if (snap === node.hammPromptElSnap) return;      // 上方没变，什么都不动（保留手改）
  node.hammPromptElSnap = snap;
  const text = rows.join("\n");
  if (String(w.value || "") !== text) {
    w.value = text;
    const inp = w.inputEl;
    if (inp && inp.value !== text) inp.value = text;
    applyPromptElBox(node);
    if (typeof node.arrange === "function") { try { node.arrange(); } catch (e) { /* 忽略 */ } }
    // computeSize 会把宽度打回默认小值（新版前端如此），切个下拉就看见节点先缩后弹。
    // 直接把宽度取下限后再 setSize —— 高度重排，宽度一步到位（不再"先窄后宽"）。
    const cs = node.computeSize();
    node.setSize([Math.max(node.size[0] || 0, cs[0] || 0), cs[1]]);
    node.setDirtyCanvas(true, true);
  }
}

// ---- 实时联动的「快」路径（纯前端，不发任何网络请求）--------------------
// 旧实现只在 onDrawBackground 里做快照比对 —— 新前端改完下拉后画布不一定立刻
// 重绘，表现为「切场景后结构化框慢半拍」。这里直接给每个输入控件的 value 挂
// setter：值一变就在下一帧（rAF）刷新结构化框，不等画布重绘。
// elements_text 本身不挂（它由 syncPromptElText 写入，挂了会自激循环）。
function schedulePromptSync(node) {
  if (node.hammPromptSyncPending) return;
  node.hammPromptSyncPending = true;
  requestAnimationFrame(() => {
    node.hammPromptSyncPending = false;
    if (isPromptNodeOf(node)) syncPromptElText(node);
  });
}

function hookPromptInputs(node) {
  if (!node.widgets) return;
  for (const w of node.widgets) {
    if (w.name === "elements_text" || w.hammSyncHook) continue;
    // 沿原型链找 value 的定义层：新前端（BaseWidget）在原型上是 accessor，
    // 旧前端是实例自带数据属性
    let owner = null, desc = null;
    let o = w;
    while (o && o !== Object.prototype) {
      const d = Object.getOwnPropertyDescriptor(o, "value");
      if (d) { owner = o; desc = d; break; }
      o = Object.getPrototypeOf(o);
    }
    if (!desc || !desc.configurable) continue;          // 动不了，走画布帧兜底
    if (desc.get || desc.set) {
      // accessor：实例上挂委托访问器，写穿到原 setter 并顺带触发同步
      w.hammSyncHook = true;
      Object.defineProperty(w, "value", {
        get() { return desc.get.call(this); },
        set(nv) {
          desc.set.call(this, nv);
          schedulePromptSync(node);
        },
        configurable: true,
        enumerable: true,
      });
    } else if (owner === w) {
      // 实例自带数据属性：包一层
      w.hammSyncHook = true;
      let v = w.value;
      Object.defineProperty(w, "value", {
        get() { return v; },
        set(nv) { v = nv; schedulePromptSync(node); },
        configurable: true,
        enumerable: true,
      });
    }
    // 原型上的共享数据属性不动（改了会影响同类型全部控件），走画布帧兜底
  }
}

// 提示词节点默认宽度：结构化提示词框内容多，太窄会挤成一长条
const PROMPT_NODE_W = 520;

// ---- 旧工作流迁移 -------------------------------------------------------
// 历史格式演进：
//   v10：elements_text 在控件序列末尾 → remapLegacyElementsOrder 搬回原位
//   v11：elements_text 后面还有 output_lang（输出语言下拉）→ 已删除该控件，
//        dropLegacyOutputLang 把保存值里的 language 槽删掉，否则后面全部错位
const LANG_VALUES = ["英文（推荐）", "中文", "中英对照"];

function unshiftLegacyElementsValue(node) {
  if (!node.widgets) return;
  const p = node.widgets.findIndex((x) => x.name === "elements_text");
  if (p < 0) return;
  const last = node.widgets.length - 1;
  if (last <= p) return;
  const atP = String(node.widgets[p].value == null ? "" : node.widgets[p].value);
  const atNext = String(node.widgets[p + 1].value == null ? "" : node.widgets[p + 1].value);
  if (LANG_VALUES.indexOf(atP) < 0 || LANG_VALUES.indexOf(atNext) >= 0) return;
  const moved = node.widgets[last].value;
  for (let i = last; i > p; i--) node.widgets[i].value = node.widgets[i - 1].value;
  node.widgets[p].value = moved;
}

function remapLegacyElementsOrder(node, info) {
  const vals = info && info.widgets_values;
  if (!Array.isArray(vals) || !node.widgets) return;
  const p = node.widgets.findIndex((x) => x.name === "elements_text");
  if (p < 0 || vals.length <= p + 1) return;
  const atP = String(vals[p] == null ? "" : vals[p]);
  const atNext = String(vals[p + 1] == null ? "" : vals[p + 1]);
  if (LANG_VALUES.indexOf(atP) < 0 || LANG_VALUES.indexOf(atNext) >= 0) return;
  const moved = vals.pop();
  vals.splice(p, 0, moved);
}

// v11 → v12：output_lang 控件已删除。旧保存值里它排在 elements_text 后面，
// 不删掉的话 llm_mode 起全部左移错位。签名：elements_text 后一位 ∈ LANG_VALUES
// （llm_mode 的合法值都不在其中，零误报）。
function dropLegacyOutputLang(node, info) {
  const vals = info && info.widgets_values;
  if (!Array.isArray(vals) || !node.widgets) return;
  const p = node.widgets.findIndex((x) => x.name === "elements_text");
  if (p < 0 || vals.length <= p + 1) return;
  const atNext = String(vals[p + 1] == null ? "" : vals[p + 1]);
  if (LANG_VALUES.indexOf(atNext) < 0) return;
  vals.splice(p + 1, 1);
}

// v14：新增「年代」下拉（插在 quality 之后 = 控件索引 8）。旧保存值只有 22 个，
// 不补一个的话从 negative_preset 到 llm_api_key 全部右移一格。
function insertEraValue(node, info) {
  if (!node || node.type !== "HammPromptImage") return;
  const vals = info && info.widgets_values;
  if (!Array.isArray(vals) || vals.length !== 22) return;   // 22 = 加年代前的控件数
  vals.splice(8, 0, "不指定");
}

// v23：删掉 negative_preset（索引 9）和 extra_negative（索引 11）两个控件
// —— 用户要求「关于反向提示词的项都去掉」。旧保存值里这两槽还在，
// 不删掉的话 extra_positive 起全部左移两格（实测：llm_mode 会拿到 local_model
// 的值 → 队列报「llm_mode / local_model 无效输入」）。
// 签名：长度 23 = 含负向两项的旧格式（当前格式删完是 21）。
// 必须排在 insertEraValue 之后：老文件先补 era 凑成 23，再统一删这两槽。
function dropLegacyNegativeSlots(node, info) {
  if (!node || node.type !== "HammPromptImage") return;
  const vals = info && info.widgets_values;
  if (!Array.isArray(vals) || vals.length !== 23) return;
  vals.splice(11, 1);   // extra_negative（先删靠后的，避免索引前移）
  vals.splice(9, 1);    // negative_preset
}

// v15 控件级兜底：insertEraValue 是「改 info 再灌值」的思路，但部分前端版本在
// onConfigure 钩子触发前就已把 widgets_values 灌进控件（时序不可依赖）。
// 配置完成后直接检查 era 的值：不在合法选项表 = 没补位、整体错位了一格
// （实测表现：era=「人物通用」、negative_preset=空 → 队列报这两个无效输入）。
// 用原始 info.widgets_values 在 era 位置插「不指定」后按序列化顺序重灌。
function fixEraShift(node, info) {
  if (!node || node.type !== "HammPromptImage" || !node.widgets) return;
  const era = node.widgets.find((w) => w.name === "era");
  if (!era || !era.options || !Array.isArray(era.options.values)) return;
  if (era.options.values.indexOf(era.value) >= 0) return;   // 值合法 = 没错位
  const vals = info && Array.isArray(info.widgets_values)
    ? info.widgets_values.slice() : null;
  if (!vals) return;
  const ei = node.widgets.indexOf(era);
  if (vals.length < node.widgets.length) vals.splice(ei, 0, "不指定");
  else if (vals.length > node.widgets.length) vals.length = node.widgets.length;
  if (vals.length !== node.widgets.length) {
    console.warn("[HammPrompt] era 值非法但长度对不上，放弃自动修复:",
      "vals=" + vals.length, "widgets=" + node.widgets.length,
      "JS=" + HAMM_JS_VERSION);
    return;
  }
  let j = 0;                                                // 与序列化顺序一致：
  for (const w of node.widgets) {                           // 跳过按钮/不保存值控件
    if (!w || w.type === "button") continue;
    if (w.options && w.options.serialize === false) continue;
    if (j >= vals.length) break;
    w.value = vals[j++];
  }
  console.warn("[HammPrompt] era 槽位错位已自动修复 (" + HAMM_JS_VERSION + ")",
    "era =", era.value);
}

// 同上的控件态兜底（时序无关）：负向两槽没删干净时，llm_mode 会吃到
// local_model 的值 —— llm_mode 的合法选项是固定几个（关 / 本地模型 / 在线 API），
// 值不在表里 = 铁证错位。用原始 info.widgets_values 删掉 9 / 11 两槽后按序列化
// 顺序重灌（跳过 button / serialize===false，与 fixEraShift 同一套规矩）。
function fixNegativeShift(node, info) {
  if (!node || node.type !== "HammPromptImage" || !node.widgets) return;
  const mode = node.widgets.find((w) => w.name === "llm_mode");
  if (!mode || !mode.options || !Array.isArray(mode.options.values)) return;
  if (mode.options.values.indexOf(mode.value) >= 0) return;   // 值合法 = 没错位
  const vals = info && Array.isArray(info.widgets_values)
    ? info.widgets_values.slice() : null;
  if (!vals) return;
  if (vals.length === node.widgets.length + 2) {
    vals.splice(11, 1);   // extra_negative
    vals.splice(9, 1);    // negative_preset
  } else if (vals.length !== node.widgets.length) {
    console.warn("[HammPrompt] llm_mode 值非法但长度对不上，放弃自动修复:",
      "vals=" + vals.length, "widgets=" + node.widgets.length,
      "JS=" + HAMM_JS_VERSION);
    return;
  }
  let j = 0;
  for (const w of node.widgets) {
    if (!w || w.type === "button") continue;
    if (w.options && w.options.serialize === false) continue;
    if (j >= vals.length) break;
    w.value = vals[j++];
  }
  // 直接改 .value 不走新前端 setter，COMBO 的 DOM 下拉框手动同步
  for (const w of node.widgets) {
    const el = w && (w.inputEl || w.element);
    if (el && el.tagName === "SELECT" && String(el.value) !== String(w.value)) {
      el.value = w.value;
    }
  }
  console.warn("[HammPrompt] 负向槽位已删除 + 控件重灌 (" + HAMM_JS_VERSION + ")",
    "llm_mode =", mode.value);
}

// 同上的控件态兜底：base onConfigure 已经把保存值按索引灌进控件后才跑到这里
// （旧文件直接在已打开的画布上重载时可能出现）。widgets[p+1] 是 llm_mode，
// 它装着语言选项 = output_lang 残留 → 从 p+1 起整体左移一格，末位清空。
function spliceOutOutputLangValue(node) {
  if (!node.widgets) return;
  const p = node.widgets.findIndex((x) => x.name === "elements_text");
  if (p < 0 || p + 1 >= node.widgets.length) return;
  const next = node.widgets[p + 1];
  if (!next || next.name !== "llm_mode") return;
  const v = String(next.value == null ? "" : next.value);
  if (LANG_VALUES.indexOf(v) < 0) return;
  for (let i = p + 1; i < node.widgets.length - 1; i++) {
    node.widgets[i].value = node.widgets[i + 1].value;
  }
  node.widgets[node.widgets.length - 1].value = "";
  // 直接改 .value 可能不走新前端的 setter，DOM 下拉框手动同步一下
  for (let i = p + 1; i < node.widgets.length; i++) {
    const el = node.widgets[i] && (node.widgets[i].inputEl || node.widgets[i].element);
    if (el && el.tagName === "SELECT" && String(el.value) !== String(node.widgets[i].value)) {
      el.value = node.widgets[i].value;
    }
  }
}

// ---- 输入/输出口对账（english/structure 等废弃口的自动清理）----------------
// 旧工作流 JSON 里保存了过时的输出口/输入口列表，LiteGraph 加载时照搬，
// 不会跟新节点定义对齐 —— 表现为已删除的 english/structure 口还挂在节点上。
// 这里按「口的名字」迁移幸存连线到新槽位，删掉废弃口及其连线。
//
// 改名别名表：口被改名时（不是删掉），旧名字的连线要接到新名字上，而不是
// 当成废弃口删线。key 是旧名，value 是新名。
//   HammPromptImage：positive 改成吐中文后，原来的 prompt/text 口并进 positive
//   HammPromptPreview：输入口 prompt/text 跟着改名成 positive
const OUT_ALIAS = { HammPromptImage: { prompt: "positive", text: "positive" } };
const IN_ALIAS = { HammPromptPreview: { prompt: "positive", text: "positive" } };

function reconcileOutputs(node, nodeData) {
  if (!nodeData || !Array.isArray(nodeData.output_name) || !node.graph) return;
  const g = node.graph;
  const want = nodeData.output_name;
  const cur = node.outputs || [];
  if (cur.length === want.length && cur.every((o, i) => o && o.name === want[i])) return;
  const alias = OUT_ALIAS[node.type] || {};
  const wantSet = {};
  for (const n of want) wantSet[n] = true;
  // 旧名字 → 新名字：先按原名认，认不出再看别名表，都不中就是真废弃口
  const resolveOut = (name) => {
    if (wantSet[name]) return name;
    const to = alias[name];
    return to && wantSet[to] ? to : null;
  };
  const keep = {};
  for (const o of cur) {
    if (!o || !o.name) continue;
    const key = resolveOut(o.name);
    if (!key) continue;
    if (!keep[key]) keep[key] = [];
    for (const id of (o.links || [])) if (id != null) keep[key].push(id);
  }
  node.outputs = want.map((name, i) => ({
    name,
    type: (nodeData.output || [])[i] || "STRING",
    links: [],
  }));
  const dropped = [];
  for (let i = 0; i < node.outputs.length; i++) {
    for (const id of (keep[node.outputs[i].name] || [])) {
      const l = g.links ? g.links[id] : null;
      if (!l) continue;
      node.outputs[i].links.push(id);
      l.origin_id = node.id;
      l.origin_slot = i;
    }
  }
  for (const o of cur) {
    if (!o || !o.name) continue;
    if (resolveOut(o.name)) continue;      // 已经按名字/别名迁到新槽位
    for (const id of (o.links || [])) dropped.push(id);   // 废弃口的连线
  }
  for (const id of dropped) {
    const l = g.links ? g.links[id] : null;
    if (!l) continue;
    const tn = g.getNodeById ? g.getNodeById(l.target_id) : null;
    if (tn && tn.inputs && tn.inputs[l.target_slot] &&
        tn.inputs[l.target_slot].link === id) {
      tn.inputs[l.target_slot].link = null;
    }
    if (g.links) delete g.links[id];
  }
}

// 输入口对账（text/structure 等废弃口自动清理）。
//
// 关键：提示词节点的 inputs 里每个口都和同名 widget 绑定（input.widget），
// 重建 input 对象会把绑定丢掉 —— 前端于是把「控件输入口」当成普通输入口，
// 在节点顶部画出一列只有名字、没有控件的假口（用户看到的严重错位）。
// 因此这里只做「增删/排序」：留下的口沿用原对象（保住 widget 绑定），
// 只有名字对不上时才新建；名字匹配时本函数是 no-op。
function reconcilePreviewInputs(node, nodeData) {
  if (!nodeData || !nodeData.input || !node.graph) return;
  const g = node.graph;
  const old = node.inputs || [];
  const want = [];
  const collect = (obj) => {
    for (const [name, def] of Object.entries(obj || {})) {
      want.push({ name, type: Array.isArray(def) ? def[0] : "STRING" });
    }
  };
  collect(nodeData.input.required);
  collect(nodeData.input.optional);

  const alias = IN_ALIAS[node.type] || {};
  const wantSet = {};
  for (const w of want) wantSet[w.name] = true;

  // 同一个新名字可能同时对应两个旧口对象：前端按新定义先建好 "positive" 口，
  // 再把旧 JSON 里对不上名字的 "prompt" 口**追加到末尾**（于是它排在新口后面）。
  // 所以不能「先到先得」：按名字（含别名）分组，每组留最靠前的那一个
  // （前端自己认的那个），把其余对象的连线搬过来；搬不下的才清掉。
  const groups = {};
  for (const inp of old) {
    if (!inp || !inp.name) continue;
    let key = inp.name;
    if (!wantSet[key]) {
      const to = alias[key];
      if (to && wantSet[to]) key = to;
    }
    if (!groups[key]) groups[key] = [];
    groups[key].push(inp);
  }
  const strays = [];       // 同组里搬不下的多余连线
  let touched = false;
  const next = want.map((w) => {
    const list = groups[w.name] || [];
    const keep = list[0];
    for (const other of list) {
      if (other === keep) continue;
      if (other.link != null) {
        if (keep && keep.link == null) { keep.link = other.link; touched = true; }
        else strays.push(other.link);
      }
      other.link = null;
    }
    if (keep) {
      if (keep.name !== w.name) {         // 就地改名：widget 绑定挂在这个对象上
        keep.name = w.name;
        touched = true;
      }
      return keep;
    }
    return { name: w.name, type: w.type, link: null };
  });
  const wantNames = want.map((w) => w.name);

  const same = next.length === old.length && next.every((inp, i) => inp === old[i]);
  if (same && !touched) return;

  node.inputs = next;

  // 留下的口：把连线目标槽位同步到新下标
  next.forEach((inp, i) => {
    if (inp.link == null) return;
    const l = g.links ? g.links[inp.link] : null;
    if (l) { l.target_id = node.id; l.target_slot = i; }
  });

  // 被删的口：三处同清（本端 input.link、上游 output.links、graph.links）
  const dropped = strays.slice();
  for (const inp of old) {
    if (inp && inp.link != null && wantNames.indexOf(inp.name) < 0) dropped.push(inp.link);
  }
  for (const id of dropped) {
    const l = g.links ? g.links[id] : null;
    if (!l) continue;
    const sn = g.getNodeById ? g.getNodeById(l.origin_id) : null;
    if (sn && sn.outputs && sn.outputs[l.origin_slot] && sn.outputs[l.origin_slot].links) {
      const arr = sn.outputs[l.origin_slot].links;
      const k = arr.indexOf(id);
      if (k >= 0) arr.splice(k, 1);
    }
    if (g.links) delete g.links[id];
  }
}

function upstreamElementRows(node) {
  const src = findUpstreamNode(node);
  if (!src) return [];
  const group = src.type === "HammPromptVideo" ? "video" : "t2i";
  const rows = [];
  for (const [name, label] of (UPSTREAM_ROWS[src.type] || [])) {
    let v = widgetVal(src, name);
    if (!v) continue;
    v = v.replace(/（(?:自填|可选|空|推荐)）/g, "").replace(/\s*\n+\s*/g, " / ").trim();
    if (!v || v === "不指定") continue;
    if (name === "duration") v += "s";
    v = displayValue(group, name, v);      // 与提示词节点同一套写法（年代 = 简洁写法）
    rows.push(label + "：" + v);
  }
  return rows;
}

// 要素行的统一清洗：去括号注释、去尾空白、丢空值行、丢负向/诊断残留行；
// 「××：不指定」也整行丢（用户要求：没选的要素不显示）
function cleanRows(rows) {
  return String(rows.join ? rows.join("\n") : rows || "")
    .split("\n")
    .map((l) => l.replace(/（(?:自填|可选|空|推荐)）/g, "").replace(/\s+$/, ""))
    .filter((l) => l.trim() && !/（空）\s*$/.test(l) && !/：\s*不指定\s*$/.test(l)
                    && !DROP_ROW_RE.test(l.trim()));
}

// 结构化要素清单：后端 structure 文本 > 上游节点实时读取。
// ① 面板只放「正向要素」——负向（预设 + 补充）不进这块，它本来就不属于提示词正向结构。
function elementRows(node) {
  const { el } = splitStructure(node.hammPreviewSTRUCT);
  let rows = cleanRows(el);
  if (!rows.length) rows = cleanRows(upstreamElementRows(node));
  return rows;
}

function detectKind(node) {
  // 提示词节点自己就是来源，按类型直接判
  if (node && node.type && UPSTREAM_ROWS[node.type]) {
    if (node.type === "HammPromptVideo") return "video";
    if (node.type === "HammPromptStoryboard") return "story";
    return "image";
  }
  const s = (node && node.hammPreviewSTRUCT) || "";
  if (s.indexOf("类型：文生视频") >= 0) return "video";
  if (s.indexOf("类型：分镜") >= 0) return "story";
  if (s.indexOf("类型：文生图") >= 0) return "image";
  if (s.indexOf("生成模式") >= 0) return "video";        // 老结构文本兜底
  if (s.indexOf("分镜数") >= 0) return "story";
  const src = findUpstreamNode(node);
  if (src && src.type === "HammPromptVideo") return "video";
  if (src && src.type === "HammPromptStoryboard") return "story";
  return "image";
}

// 文本框里的当前内容（用户可能手动改过，改了就按他改的复制）。
// 提示词节点 → 读自己的 elements_text（结构化提示词框）；
// 预览节点 → 读 ① 文本框。
function currentElementsText(node) {
  const w = isPromptNodeOf(node)
    ? promptElWidget(node)
    : (node.hammElWidget || (node.widgets && node.widgets.find((x) => x.name === EL_WIDGET)));
  const v = w && typeof w.value === "string" ? w.value.trim() : "";
  if (v) return v;
  return isPromptNodeOf(node)
    ? selfElementRows(node).join("\n")
    : elementRows(node).join("\n");
}

function buildCopyPayload(node) {
  const body = currentElementsText(node);
  const ins = COPY_INSTRUCTION[detectKind(node)] || "";
  // 指令在前、待优化词紧跟其后（指令以「待优化词如下：」收尾），整段可直接粘贴
  return body ? ins + "\n" + body : ins;
}

async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 落到 execCommand 兜底 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}

function doCopy(node) {
  const payload = buildCopyPayload(node);
  copyToClipboard(payload).then((ok) => {
    // 反馈直接写在按钮上（DOM），2 秒后恢复
    const w = isPromptNodeOf(node) ? promptElWidget(node) : node.hammElWidget;
    const btn = w && w.hammCopyBtnEl;
    if (btn) {
      btn.textContent = ok ? "已复制 ✓" : "复制失败";
      btn.style.background = ok ? "#2f7a52" : "#8a3b3b";
      setTimeout(() => {
        btn.textContent = "复制";
        btn.style.background = "#2b4a6b";
      }, 1800);
    }
  });
}

// ================================================================ 预览版面
// ① ② ③ 都是节点上的真实多行文本框（DOM，可编辑、可滚动，绝不溢出节点），
// 复制按钮（DOM）挂在 ① 框正下方。画布上不再画任何东西。
const HEAD_TOP = 32;

function layoutPreviewNode(node) {
  ensureAllBoxes(node);
  forceBoxVisible(node);
  applyBoxHeights(node);
  const w = Math.max(node.size[0] || 0, 500);
  node.size[0] = w;

  let base = HEAD_TOP;
  try { base = node.computeSize()[1] || HEAD_TOP; } catch (e) { /* 保持兜底 */ }
  node.setSize([w, Math.max(HEAD_TOP, base) + 6]);

  // 关键：必须先按新的 widget 高度重排一次，各 widget 的 last_y 才会更新。
  // 否则 last_y 会停留在「内容为空」时的旧布局上，画布上按 last_y 画的标题条就会错位
  // （表现为标题条压在文本框中间或被文本框盖住）。
  if (typeof node.arrange === "function") {
    try { node.arrange(); } catch (e) { /* 某些前端版本没有 arrange，忽略 */ }
  }
  node.setDirtyCanvas(true, true);
}

// 标题条与复制按钮都由 DOM 承载（ensureBoxTitleEl / ensureCopyRowEl），画布上不画按钮。

installExecutedCapture();   // 零连线模式：全局监听提示词节点的执行数据

app.registerExtension({
  name: "HammPrompt.Preview",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!String(nodeData.name).startsWith("HammPrompt")) return;
    const isPreviewNode = nodeData.name === "HammPromptPreview";

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
      const node = this;
      migrateTitle(node);
      ensureWidgetsStartY(node);   // 控件起点让开槽位带，防高度自增长
      placeRealInputSlots(node);   // 真输入口摆回顶部槽位带，别压在控件上
      if (isPreviewNode) {
        hideAllWidgets(node);
        layoutPreviewNode(node);
      }
      setTimeout(() => {
        swapApiIfMisplaced(node);
        labelizeApiWidgets(node);
        node.hammLastMode = readMode(node);
        syncVisibility(node);
        if (isPreviewNode) layoutPreviewNode(node);
        if (isPromptNodeOf(node)) {
          node.size[0] = Math.max(node.size[0] || 0, PROMPT_NODE_W);   // 默认加宽
          setupPromptElWidget(node);   // 结构化提示词框（旧后端没这控件时兜底挂载）
          syncPromptElText(node);      // 初始就按当前选择填好
          hookPromptInputs(node);      // 输入/下拉变化 → 下一帧即时刷新（纯前端）
        }
      }, 0);
      const modeW = this.widgets && this.widgets.find((x) => x.name === "llm_mode");
      if (modeW) {
        const origCb = modeW.callback;
        modeW.callback = function (...a) {
          const rr = origCb ? origCb.apply(this, a) : undefined;
          node.hammLastMode = readMode(node);
          syncVisibility(node);
          return rr;
        };
      }
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      this.hammConfiguring = true;   // 冻结 elements_text 自动填充，保住待迁移的错位值
      migrateApiWidgetValues(this, info);
      remapLegacyElementsOrder(this, info);   // v10：elements_text 在末尾 → 搬回原位
      dropLegacyOutputLang(this, info);       // v11：删掉 output_lang 槽值（控件已移除）
      insertEraValue(this, info);             // v14：补上「年代」槽值（新增控件）
      dropLegacyNegativeSlots(this, info);    // v23：删掉负向两项槽值（控件已移除）
      const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
      migrateTitle(this);
      ensureWidgetsStartY(this);
      placeRealInputSlots(this);
      setTimeout(() => {
        // 端口对账最先跑：english/structure 等废弃口的连线要按名字挪回新槽位
        reconcileOutputs(this, nodeData);
        reconcilePreviewInputs(this, nodeData);
        fixEraShift(this, info);           // v15：era 槽位错位的控件级兜底（时序无关）
        fixNegativeShift(this, info);      // v23：负向两槽删除的控件级兜底（时序无关）
        // 控件值修复必须最先跑：readMode/syncVisibility 都依赖 llm_mode 的正确值
        unshiftLegacyElementsValue(this);  // 旧工作流（v10 格式）：末位元素文本搬回原位
        spliceOutOutputLangValue(this);    // v11 格式：llm_mode 装着语言选项 → 左移修复
        swapApiIfMisplaced(this);
        labelizeApiWidgets(this);
        this.hammLastMode = readMode(this);
        syncVisibility(this);
        if (isPreviewNode) layoutPreviewNode(this);
        this.hammConfiguring = false;        // 迁移完成，恢复实时联动
        if (isPromptNodeOf(this)) {
          setupPromptElWidget(this);
          syncPromptElText(this);            // 加载旧工作流也立即填上
          hookPromptInputs(this);
        }
      }, 0);
      return r;
    };

    // 保险：每帧检测 llm_mode 是否变了（有变化才重新计算显隐），
    // 防止某些前端版本 combo 不触发 callback 导致显隐卡死
    const onDrawBackground = nodeType.prototype.onDrawBackground;
    nodeType.prototype.onDrawBackground = function (ctx) {
      const r = onDrawBackground ? onDrawBackground.apply(this, arguments) : undefined;
      if (this.flags && this.flags.collapsed) return r;
      migrateTitle(this);           // 幂等：title 还是旧默认名就换成新的
      ensureWidgetsStartY(this);    // 幂等：槽位带高度变了（输出口增减）要跟着走
      placeRealInputSlots(this);    // 幂等：真输入口别被前端留在错行上
      if (isPreviewNode) {
        hideAllWidgets(this);       // 幂等；顺手兜住懒创建出来的 DOM 元素
        enforceDomHidden(this);
        const boxes = this.hammBoxes || {};
        for (const def of BOX_DEFS) {
          const w = boxes[def.name];
          if (w) bindBoxInput(this, w, def);   // DOM 是懒创建的
        }
        forceBoxVisible(this);      // 输入口连了线也要让框露出来（框架会把它藏掉）
        applyBoxHeights(this);      // 框高跟着内容行数走（幂等）
        // 节点被拉宽/收窄后重新换行，避免长句溢出右边界
        if (this.hammLastW !== this.size[0]) {
          this.hammLastW = this.size[0];
          layoutPreviewNode(this);
        }
        return r;
      }
      const mode = readMode(this);
      if (mode !== this.hammLastMode) {
        this.hammLastMode = mode;
        syncVisibility(this);
      } else {
        enforceDomHidden(this);
      }
      // 提示词节点：结构化提示词框每帧兜底 + 实时联动（快照没变就是零开销）
      if (isPromptNodeOf(this)) {
        setupPromptElWidget(this);
        applyPromptElBox(this);
        hookPromptInputs(this);     // 幂等；兜住懒创建出来的控件
        syncPromptElText(this);
      }
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted ? onExecuted.apply(this, arguments) : undefined;
      if (message) {
        if (message.hamm_preview_struct) this.hammPreviewSTRUCT = message.hamm_preview_struct[0];
        if (message.hamm_preview_cn) this.hammPreviewCN = message.hamm_preview_cn[0];
        if (message.hamm_preview_en) this.hammPreviewEN = message.hamm_preview_en[0];
        if (message.hamm_preview_neg) this.hammPreviewNEG = message.hamm_preview_neg[0];
      }
      if (!isPreviewNode) return r;
      // 诊断：把后端送来的消息打到控制台，确认是不是收到了空 positive
      console.log("[HammPrompt] preview onExecuted:", this.id, "msg keys:", message ? Object.keys(message) : null,
                  "hammPreviewCN:", this.hammPreviewCN ? this.hammPreviewCN.slice(0, 30) : "(空)");
      // 逐口判断：连了线的口用后端送来的值（上面已写入）；
      // 没连线的口丢掉后端默认值，改用全局捕获的数据 —— 这就是零连线模式
      const hasLink = (name) => !!(this.inputs || []).find(
        (i) => i && i.name === name && i.link != null);
      const p = hammLastPayload;
      if (p) {
        if (!hasLink("positive") && p.cn !== undefined) this.hammPreviewCN = p.cn;
        if (!hasLink("negative") && p.neg !== undefined) this.hammPreviewNEG = p.neg;
        if (!hasLink("structure") && p.struct !== undefined) this.hammPreviewSTRUCT = p.struct;
        this.hammAutoSrc = p.nodeId;
      }
      fillPreviewBoxes(this);
      return r;
    };

    const onDraw = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx) {
      const r = onDraw ? onDraw.apply(this, arguments) : undefined;
      if (this.flags.collapsed) return r;
      drawApiHeader(this, ctx);
      // ①②③ 和复制按钮都是 DOM 元素，画布上不再画任何东西
      return r;
    };
  },
});

// 拉一次考据描述：结构化框要显示「喂给大模型的词」（拿到后会自动刷新已有节点的框）
try { loadMoDesc(); } catch (e) { /* 拿不到就降级为只显示选项名 */ }

#!/usr/bin/env python3
"""把 .kyncode/debug.log 渲染成自包含的 HTML 静态页面。

debug.log 是 kyncode 的 trace 日志，由 ``logging.basicConfig(format="%(asctime)s
%(name)s %(message)s")`` 追加写入。每行结构为：

    <时间戳> <logger 名> <消息>

其中「消息」分两类：

* 结构化 trace（带 JSON）：``llm_response`` / ``tool_call`` / ``permission_check``
  —— 由 agent.py / runtime.py / checker.py 用 ``log.info("xxx %s", json)`` 写出。
* 纯文本：MCP 连接/关闭、httpx 请求、以及偶发的多行 traceback（崩溃续行）。

本脚本把这些行解析成结构化条目，按 ``agent_id``（一次 agent 会话 = 一次对话）
分组，并把 JSON 美化后嵌入一份可离线打开、可搜索/折叠的 HTML。

用法：
    python kyncode/debug_content.py                          # 默认读 .kyncode/debug.log，写到 .kyncode/debug_content.html
    python kyncode/debug_content.py /path/to/debug.log out.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 日志行解析
# ---------------------------------------------------------------------------

# asctime 默认格式：2026-10-11 10:35:23,406
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ")

# 带 JSON 载荷的 trace 事件（消息 = "<事件名> <json>"）
_JSON_EVENTS = ("llm_response", "tool_call", "permission_check")

# 每个事件的展示颜色（CSS 变量名，见模板里的 :root）
_EVENT_STYLE: dict[str, str] = {
    "llm_response": "llm",
    "tool_call": "tool",
    "permission_check": "perm",
    "http": "http",
    "mcp": "mcp",
    "plain": "plain",
}


def _classify(logger: str, event: str | None) -> str:
    """把 logger + 事件名归并成一个视觉类别，用于配色。"""
    if event in _EVENT_STYLE:
        return _EVENT_STYLE[event]
    if "httpx" in logger or logger == "httpx2":
        return "http"
    if "mcp" in logger or "websockets" in logger:
        return "mcp"
    return "plain"


def parse_log(path: Path) -> list[dict[str, Any]]:
    """把 debug.log 解析成有序条目列表。

    多行 traceback 会作为 ``extra`` 附加到上一条，而不是独立成条。
    """
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not _TIMESTAMP_RE.match(line):
            # 续行（traceback）：拼到上一条
            if entries:
                entries[-1].setdefault("extra", []).append(line)
            else:
                entries.append(
                    {"ts": "", "logger": "", "event": "plain", "message": line}
                )
            continue

        ts = line[:23]  # "2026-10-11 10:35:23,406"
        rest = line[24:]
        m = re.match(r"(\S+)\s+(.*)", rest)
        if not m:
            entries.append(
                {"ts": ts, "logger": "", "event": "plain", "message": rest}
            )
            continue

        logger, message = m.group(1), m.group(2)
        event: str | None = None
        payload: dict[str, Any] | None = None
        raw_json: str | None = None

        for ev in _JSON_EVENTS:
            prefix = ev + " "
            if message.startswith(prefix):
                event = ev
                raw_json = message[len(prefix):]
                try:
                    payload = json.loads(raw_json)
                except (json.JSONDecodeError, ValueError):
                    payload = None
                break

        entries.append(
            {
                "ts": ts,
                "logger": logger,
                "event": event,
                "message": message if payload is None else None,
                "json": payload,
                "json_raw": raw_json,
                "kind": _classify(logger, event),
                "extra": [],
            }
        )

    # 给每条补 agent_id（用于分组）：遇到 llm_response 就更新「当前对话」，
    # 其余条目归到当前对话名下；对话切换靠 llm_response 的 agent_id 驱动。
    current: str | None = None
    for entry in entries:
        if entry["event"] == "llm_response" and entry["json"]:
            current = entry["json"].get("agent_id") or current
        entry["agent_id"] = current
    return entries


# ---------------------------------------------------------------------------
# 按「单次对话」分组
# ---------------------------------------------------------------------------

def group_by_conversation(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 agent_id 分组，保留时间顺序；无 agent_id 的启动日志归入 __system__。"""
    groups: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    for entry in entries:
        key = entry["agent_id"] or "__system__"
        if key not in index:
            index[key] = len(groups)
            groups.append(
                {
                    "key": key,
                    "entries": [],
                    "start_ts": None,
                    "end_ts": None,
                }
            )
        g = groups[index[key]]
        g["entries"].append(entry)
        if entry["ts"]:
            if g["start_ts"] is None or entry["ts"] < g["start_ts"]:
                g["start_ts"] = entry["ts"]
            if g["end_ts"] is None or entry["ts"] > g["end_ts"]:
                g["end_ts"] = entry["ts"]

    # 汇总每组的统计
    for g in groups:
        counts: dict[str, int] = {}
        for e in g["entries"]:
            label = e["event"] or e["kind"]
            counts[label] = counts.get(label, 0) + 1
        g["counts"] = counts
        g["entry_count"] = len(g["entries"])
        # 分组标题：优先用第一条 llm_response 的文本摘要
        g["title"] = _group_title(g["entries"])

    return groups


def _group_title(entries: list[dict[str, Any]]) -> str:
    """给对话一个可读标题：第一条 llm_response 的首行文本。"""
    for e in entries:
        if e["event"] == "llm_response" and e["json"]:
            text = (e["json"].get("text") or "").strip()
            if text:
                first_line = text.splitlines()[0]
                return first_line[:80]
    return ""


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KynCode Debug Log</title>
<style>
:root {
  --bg: #16161c;
  --panel: #1e1e26;
  --panel2: #26262f;
  --border: #2f2f3a;
  --text: #d8d8e0;
  --muted: #8b8b98;
  --llm: #875fff;
  --tool: #5a7fd6;
  --perm: #ffb86c;
  --http: #56b6c2;
  --mcp: #6bd08a;
  --plain: #8b8b98;
  --key: #e06c75;
  --str: #98c379;
  --num: #d19a66;
  --bool: #c678dd;
  --null: #c678dd;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Microsoft YaHei", monospace;
}
a { color: var(--llm); }

/* header */
header {
  position: sticky; top: 0; z-index: 10;
  background: linear-gradient(180deg, #1a1a22, #16161c);
  border-bottom: 1px solid var(--border);
  padding: 18px 24px 14px;
}
header h1 { margin: 0 0 6px; font-size: 20px; }
header .sub { color: var(--muted); font-size: 12px; }
.stats { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.stat {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 12px; font-size: 12px;
}
.stat b { color: var(--text); font-size: 16px; }
.stat span { color: var(--muted); margin-left: 4px; }

/* toolbar */
.toolbar {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  padding: 12px 24px; border-bottom: 1px solid var(--border);
}
.toolbar input[type=search] {
  flex: 1; min-width: 200px;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); padding: 7px 12px; outline: none;
}
.toolbar input[type=search]:focus { border-color: var(--llm); }
.toolbar select {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 6px 10px; font-size: 12px; outline: none; cursor: pointer;
}
.toolbar select:focus { border-color: var(--llm); }
.chip {
  background: var(--panel); border: 1px solid var(--border); color: var(--muted);
  border-radius: 999px; padding: 5px 12px; cursor: pointer; font-size: 12px;
  user-select: none;
}
.chip.active { background: var(--llm); border-color: var(--llm); color: #fff; }
.btn {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 6px 12px; cursor: pointer; font-size: 12px;
}
.btn:hover { border-color: var(--llm); }

/* conversation groups */
main { padding: 16px 24px 60px; max-width: 1200px; margin: 0 auto; }
.group {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; margin-bottom: 14px; overflow: hidden;
}
.group-head {
  display: flex; align-items: center; gap: 10px; padding: 12px 16px;
  cursor: pointer; user-select: none;
}
.group-head:hover { background: var(--panel2); }
.group-head .caret { transition: transform .15s; color: var(--muted); }
.group.open .caret { transform: rotate(90deg); }
.group-head .gid { color: var(--llm); font-weight: 600; }
.group-head .gtitle { color: var(--text); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.group-head .gmeta { color: var(--muted); font-size: 11px; white-space: nowrap; }
.badges { display: flex; gap: 5px; margin-left: 8px; }
.badge { font-size: 10px; padding: 1px 7px; border-radius: 999px; }
.badge.llm { background: rgba(135,95,255,.18); color: var(--llm); }
.badge.tool { background: rgba(90,127,214,.18); color: var(--tool); }
.badge.perm { background: rgba(255,184,108,.18); color: var(--perm); }
.badge.http { background: rgba(86,182,194,.18); color: var(--http); }
.badge.mcp { background: rgba(107,208,138,.18); color: var(--mcp); }
.badge.plain { background: rgba(139,139,152,.18); color: var(--plain); }

.group-body { display: none; border-top: 1px solid var(--border); }
.group.open .group-body { display: block; }

/* entry */
.entry { border-bottom: 1px solid var(--border); }
.entry:last-child { border-bottom: none; }
.entry-head {
  display: flex; align-items: flex-start; gap: 10px; padding: 8px 16px;
  cursor: pointer;
}
.entry-head:hover { background: var(--panel2); }
.entry-head .ts { color: var(--muted); font-size: 11px; white-space: nowrap;
  padding-top: 1px; }
.entry-head .tag {
  font-size: 10px; font-weight: 600; padding: 1px 8px; border-radius: 4px;
  white-space: nowrap; letter-spacing: .3px;
}
.tag.llm { background: var(--llm); color: #fff; }
.tag.tool { background: var(--tool); color: #fff; }
.tag.perm { background: var(--perm); color: #2a2a2a; }
.tag.http { background: var(--http); color: #1c2b2c; }
.tag.mcp { background: var(--mcp); color: #1c2b1c; }
.tag.plain { background: var(--panel2); color: var(--muted); border: 1px solid var(--border); }
.entry-head .logger { color: var(--muted); font-size: 11px; white-space: nowrap; }
.entry-head .msg { color: var(--text); flex: 1; word-break: break-all; }
.entry-body { display: none; padding: 0 16px 12px 16px; }
.entry.open .entry-body { display: block; }
.entry.open > .entry-head .msg { display: none; }

/* json block */
pre.json {
  margin: 0; padding: 12px 14px; background: #121216; border: 1px solid var(--border);
  border-radius: 8px; overflow-x: auto; font-size: 12.5px; line-height: 1.5;
}
pre.raw {
  margin: 0; padding: 12px 14px; background: #121216; border: 1px solid var(--border);
  border-radius: 8px; overflow-x: auto; color: var(--plain);
  white-space: pre-wrap; word-break: break-all;
}
.extra { color: #e06c75; }
.json .key { color: var(--key); }
.json .str { color: var(--str); }
.json .num { color: var(--num); }
.json .bool { color: var(--bool); }
.json .null { color: var(--null); }

.empty { color: var(--muted); text-align: center; padding: 40px; }
.hidden { display: none !important; }
</style>
</head>
<body>
<header>
  <h1>KynCode Debug Log</h1>
  <div class="sub" id="source"></div>
  <div class="stats" id="stats"></div>
</header>

<div class="toolbar">
  <input type="search" id="search" placeholder="搜索关键词（时间戳 / logger / 事件 / 内容）…">
  <select id="date-filter" title="按日期筛选"></select>
  <div id="chips"></div>
  <button class="btn" id="expand-all">展开全部</button>
  <button class="btn" id="collapse-all">折叠全部</button>
</div>

<main id="main"></main>

<script>
const DATA = __DATA__;

const KIND_LABEL = { llm: "llm_response", tool: "tool_call", perm: "permission_check",
                     http: "HTTP/MCP", mcp: "MCP", plain: "other" };

// ---- JSON 语法高亮（轻量正则，处理 string/number/bool/null/key）----
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
// 用于 HTML 属性值：多转义引号，避免 data-text 里的 JSON 引号截断属性
function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function highlightJson(s) {
  const safe = esc(s);
  return safe.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    function (m) {
      let cls = "num";
      if (m[0] === '"') cls = /:$/.test(m) ? "key" : "str";
      else if (m === "true" || m === "false") cls = "bool";
      else if (m === "null") cls = "null";
      return '<span class="' + cls + '">' + m + '</span>';
    }
  );
}

// ---- 渲染 ----
const main = document.getElementById("main");
const searchEl = document.getElementById("search");
const chipsEl = document.getElementById("chips");
const dateEl = document.getElementById("date-filter");

function eventKind(e) { return e.kind; }
function eventLabel(e) { return e.event || KIND_LABEL[e.kind] || "log"; }

function renderStats() {
  const s = DATA.meta;
  document.getElementById("source").textContent =
    "源文件 " + s.source + " · 生成于 " + s.generated_at;
  const stats = [
    ["entries", s.total_entries, "条日志"],
    ["groups", s.total_conversations, "次对话"],
    ["lines", s.total_lines, "行原始日志"],
  ];
  const ev = s.event_counts || {};
  const evMap = { llm_response: "llm", tool_call: "tool", permission_check: "perm" };
  for (const [k, v] of Object.entries(ev)) stats.push([k, v, k]);
  document.getElementById("stats").innerHTML = stats.map(([k, v, label]) =>
    '<div class="stat"><b>' + v + '</b><span>' + label + '</span></div>'
  ).join("");
}

function renderChips() {
  const kinds = ["llm", "tool", "perm", "http", "mcp", "plain"];
  const chips = kinds.map(k =>
    '<div class="chip" data-kind="' + k + '">' + KIND_LABEL[k] + '</div>'
  ).join("");
  chipsEl.innerHTML = chips;
  chipsEl.querySelectorAll(".chip").forEach(c => c.addEventListener("click", () => {
    c.classList.toggle("active");
    applyFilters();
  }));
}

function renderDateFilter() {
  const dates = DATA.meta.dates || [];
  dateEl.innerHTML = '<option value="">全部日期</option>' +
    dates.map(d => '<option value="' + d.date + '">' + d.date + ' (' + d.count + ')</option>').join("");
}

function entryHtml(e) {
  const tag = '<span class="tag ' + e.kind + '">' + esc(eventLabel(e)) + '</span>';
  const ts = '<span class="ts">' + esc(e.ts) + '</span>';
  const logger = '<span class="logger">' + esc(e.logger) + '</span>';

  let body = "";
  if (e.json !== null && e.json !== undefined) {
    const pretty = JSON.stringify(e.json, null, 2);
    body = '<pre class="json">' + highlightJson(pretty) + '</pre>';
  } else if (e.message) {
    body = '<pre class="raw">' + esc(e.message) + '</pre>';
  } else if (e.json_raw) {
    // JSON 解析失败，原样展示
    body = '<pre class="raw">' + esc(e.json_raw) + '</pre>';
  }
  if (e.extra && e.extra.length) {
    body += '<pre class="raw extra">' + esc(e.extra.join("\n")) + '</pre>';
  }
  if (!body) body = '<pre class="raw">(empty)</pre>';

  const msg = e.message ? '<span class="msg">' + esc(e.message) + '</span>'
                        : '<span class="msg">' + esc(JSON.stringify(e.json).slice(0, 200)) + '</span>';

  const date = (e.ts || "").slice(0, 10);
  return '<div class="entry" data-kinds="' + e.kind + '" data-date="' + date + '" data-text="' +
    escAttr((e.ts + " " + e.logger + " " + (e.message || e.json_raw || "")).toLowerCase()) +
    '">' +
    '<div class="entry-head">' + ts + tag + logger + msg + '</div>' +
    '<div class="entry-body">' + body + '</div>' +
    '</div>';
}

function groupHtml(g) {
  const isSystem = g.key === "__system__";
  const gid = isSystem ? "系统 / 启动日志" : g.key;
  const title = g.title ? esc(g.title) : '<span style="color:var(--muted)">(无文本)</span>';
  const meta = g.entry_count + " 条 · " + (g.start_ts || "?") +
    (g.end_ts && g.end_ts !== g.start_ts ? " ~ " + g.end_ts.slice(11) : "");
  const badges = Object.entries(g.counts).map(([k, v]) => {
    const cls = (k === "llm_response") ? "llm" : (k === "tool_call") ? "tool"
      : (k === "permission_check") ? "perm" : (g.kindStyle || "plain");
    return '<span class="badge ' + cls + '">' + esc(k) + " " + v + '</span>';
  }).join("");

  const entries = g.entries.map(entryHtml).join("");
  return '<div class="group" data-group>' +
    '<div class="group-head">' +
      '<span class="caret">▶</span>' +
      '<span class="gid">' + esc(gid) + '</span>' +
      '<span class="gtitle">' + title + '</span>' +
      '<div class="badges">' + badges + '</div>' +
      '<span class="gmeta">' + esc(meta) + '</span>' +
    '</div>' +
    '<div class="group-body">' + entries + '</div>' +
    '</div>';
}

function renderGroups() {
  main.innerHTML = DATA.groups.map(groupHtml).join("");
  // 分组折叠
  main.querySelectorAll(".group-head").forEach(h => h.addEventListener("click", () => {
    h.parentElement.classList.toggle("open");
  }));
  // 条目折叠
  main.querySelectorAll(".entry-head").forEach(h => h.addEventListener("click", () => {
    h.parentElement.classList.toggle("open");
  }));
}

function applyFilters() {
  const q = searchEl.value.trim().toLowerCase();
  const selectedDate = dateEl.value;
  const activeKinds = new Set(
    Array.from(chipsEl.querySelectorAll(".chip.active")).map(c => c.dataset.kind)
  );
  const hasKindFilter = activeKinds.size > 0;

  main.querySelectorAll(".group").forEach(g => {
    let visible = 0;
    g.querySelectorAll(".entry").forEach(e => {
      const kindMatch = !hasKindFilter || activeKinds.has(e.dataset.kinds);
      const textMatch = !q || e.dataset.text.includes(q);
      const dateMatch = !selectedDate || e.dataset.date === selectedDate;
      const show = kindMatch && textMatch && dateMatch;
      e.classList.toggle("hidden", !show);
      if (show) visible++;
    });
    g.classList.toggle("hidden", visible === 0);
  });
}

document.getElementById("expand-all").addEventListener("click", () => {
  main.querySelectorAll(".group").forEach(g => g.classList.add("open"));
  main.querySelectorAll(".entry").forEach(e => e.classList.add("open"));
});
document.getElementById("collapse-all").addEventListener("click", () => {
  main.querySelectorAll(".group").forEach(g => g.classList.remove("open"));
  main.querySelectorAll(".entry").forEach(e => e.classList.remove("open"));
});
searchEl.addEventListener("input", applyFilters);
dateEl.addEventListener("change", applyFilters);

renderStats();
renderChips();
renderDateFilter();
renderGroups();
</script>
</body>
</html>
"""


def build_meta(entries: list[dict[str, Any]], source: Path) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    date_counts: dict[str, int] = {}
    for e in entries:
        if e["event"]:
            event_counts[e["event"]] = event_counts.get(e["event"], 0) + 1
        if e["ts"]:
            date_counts[e["ts"][:10]] = date_counts.get(e["ts"][:10], 0) + 1
    # 日期按字典序（即时间序）排列，供筛选下拉使用
    dates = [{"date": d, "count": date_counts[d]} for d in sorted(date_counts)]
    return {
        "source": str(source),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_entries": len(entries),
        "total_conversations": 0,  # 后面回填
        "total_lines": sum(1 for _ in source.open(encoding="utf-8", errors="replace")),
        "event_counts": event_counts,
        "dates": dates,
    }


def render_html(entries: list[dict[str, Any]], source: Path) -> str:
    groups = group_by_conversation(entries)
    meta = build_meta(entries, source)
    meta["total_conversations"] = len(groups)
    data = {"meta": meta, "groups": groups}

    # 安全嵌入 <script>：转义 </ 防止提前闭合
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__DATA__", payload)


def default_paths() -> tuple[Path, Path]:
    """默认输入/输出路径，均相对项目根（本文件上一级目录）。"""
    root = Path(__file__).resolve().parent.parent
    return root / ".kyncode" / "debug.log", root / ".kyncode" / "debug_content.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 kyncode 的 debug.log 渲染成自包含 HTML 静态页面"
    )
    parser.add_argument("log_path", nargs="?", help="debug.log 路径（默认 .kyncode/debug.log）")
    parser.add_argument("output", nargs="?", help="输出 HTML 路径（默认 .kyncode/debug_content.html）")
    args = parser.parse_args(argv)

    default_in, default_out = default_paths()
    log_path = Path(args.log_path) if args.log_path else default_in
    out_path = Path(args.output) if args.output else default_out

    if not log_path.exists():
        print(f"错误：找不到日志文件 {log_path}", file=sys.stderr)
        return 1

    entries = parse_log(log_path)
    html = render_html(entries, log_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    print(f"已生成 {out_path}")
    print(f"  日志条目：{len(entries)}")
    print(f"  对话分组：{len(group_by_conversation(entries))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

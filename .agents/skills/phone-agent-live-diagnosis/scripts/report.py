"""Render the v2 ``summary.json`` (+ evidence stream) into an interactive HTML report.

Per ``outputs/design-council/ROUND2-D1.md`` §4. The report is a single
self-contained HTML file: the design system (primary ``#1E40AF`` / accent
``#F59E0B`` / Fira Code, ``<base target="_blank">``, KPI cards, cause cards,
tab framework) is carried over from the v1 template, but the content is reworked
to the v2 thin-loop dimensions. The overview tab leads with R1's three
first-page blocks:

1. **终局裁定** — the verdict + terminal + finish-gate outcome.
2. **TaskDoc 板** — the terminal task board (goal / route items / facts).
3. **80/20 三件事** — the top recommendations.

Data is embedded as a JSON island in a ``<script type="application/json">`` tag
with ``</``-safe escaping (mirrors the v1 ``__REPORT_DATA__`` scheme), so the
report is offline and never executes the payload. The evidence and summary are
already redacted + base64-free by the diagnostic middleware and the analyzer;
this module adds no secrets and re-escapes nothing sensitive back in.
"""

from __future__ import annotations

import json
from typing import Any


def _escape_report_data(payload: str) -> str:
    """Escape a JSON payload for safe embedding in a ``<script>`` island.

    ``<`` -> ``\\u003c`` neutralizes any ``</script>`` sequence; ``&`` / ``>`` are
    escaped for good measure. Mirrors the v1 report's ``__REPORT_DATA__`` scheme.
    """

    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("</", "<\\/")
    )


def render_html(summary: dict[str, Any], evidence: list[dict[str, Any]] | None = None) -> str:
    """Render ``summary`` (+ optional redacted evidence stream) to an HTML string.

    ``evidence`` is the list of diagnostic events (already redacted / base64-free);
    it powers the timeline and raw-evidence tabs. Both are embedded as a JSON
    island and rendered client-side.
    """

    payload = json.dumps(
        {"summary": summary, "evidence": evidence or []}, ensure_ascii=False
    )
    return HTML_TEMPLATE.replace("__REPORT_DATA__", _escape_report_data(payload))


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <base target="_blank">
  <title>Phone Agent 薄 loop 实机诊断</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');
    :root {
      --primary: #1E40AF;
      --primary-soft: #DBEAFE;
      --accent: #F59E0B;
      --bg: #F1F5F9;
      --panel: #FFFFFF;
      --ink: #0F172A;
      --muted: #64748B;
      --line: #E2E8F0;
      --success: #15803D;
      --failed: #B91C1C;
      --blocked: #B45309;
      --radius: 10px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Fira Sans", system-ui, sans-serif;
      letter-spacing: 0;
    }
    header {
      background: linear-gradient(135deg, #0F172A 0%, #1E293B 60%, #1E3A8A 100%);
      color: white;
      padding: 26px 32px 22px;
      border-bottom: 4px solid var(--accent);
    }
    .header-row {
      display: flex; align-items: flex-start; justify-content: space-between;
      gap: 16px; flex-wrap: wrap; max-width: 1400px;
    }
    h1 { margin: 0 0 10px; font-size: 24px; line-height: 1.2; }
    h2 { margin: 0 0 12px; font-size: 17px; display: flex; align-items: center; gap: 8px; }
    h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--accent); }
    h3 { margin: 0 0 8px; font-size: 14px; }
    .mono, code, pre { font-family: "Fira Code", ui-monospace, monospace; }
    .wrap { word-break: break-all; overflow-wrap: break-word; }
    .subtitle { color: #CBD5E1; max-width: 1000px; font-size: 14px; line-height: 1.5; }
    .verdict-chip {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 8px 16px; border-radius: 999px;
      font: 700 14px "Fira Code"; white-space: nowrap;
      background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.25);
    }
    .verdict-chip.success { background: rgba(21,128,61,.35); border-color: #4ADE80; color: #BBF7D0; }
    .verdict-chip.failed { background: rgba(185,28,28,.35); border-color: #F87171; color: #FECACA; }
    .verdict-chip.takeover { background: rgba(180,83,9,.4); border-color: #FCD34D; color: #FDE68A; }
    .verdict-chip.max_steps { background: rgba(180,83,9,.4); border-color: #FCD34D; color: #FDE68A; }
    .verdict-chip.uncertain { background: rgba(100,116,139,.35); border-color: #CBD5E1; color: #E2E8F0; }
    .shell { padding: 20px 32px 40px; max-width: 1400px; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px 16px;
      box-shadow: 0 1px 3px rgba(15, 23, 42, .06);
    }
    .kpi-label { color: var(--muted); font-size: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: .04em; }
    .kpi-value { font-size: 18px; font-weight: 700; }
    .badge {
      display: inline-flex; align-items: center; min-height: 22px;
      border-radius: 999px; padding: 2px 9px;
      font: 600 12px "Fira Code";
      background: #E0E7FF; color: var(--primary); border: 1px solid #BFDBFE;
    }
    .badge.success { background: #DCFCE7; color: var(--success); border-color: #86EFAC; }
    .badge.completed { background: #DCFCE7; color: var(--success); border-color: #86EFAC; }
    .badge.failed { background: #FEE2E2; color: var(--failed); border-color: #FCA5A5; }
    .badge.blocked { background: #FEF3C7; color: var(--blocked); border-color: #FCD34D; }
    .badge.in_progress { background: #DBEAFE; color: var(--primary); border-color: #93C5FD; }
    .badge.pending { background: #F1F5F9; color: var(--muted); border-color: #CBD5E1; }
    .alert {
      border-radius: 8px; padding: 10px 14px; margin: 0 0 12px;
      font-weight: 700; word-break: break-all; overflow-wrap: break-word;
    }
    .alert.danger { background: #FEE2E2; color: var(--failed); border: 1px solid #FCA5A5; }
    .alert.warn { background: #FEF3C7; color: var(--blocked); border: 1px solid #FCD34D; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 14px 0; }
    button, input, select {
      border: 1px solid var(--line); background: white; color: var(--ink);
      border-radius: 8px; padding: 8px 12px; font: 500 13px "Fira Sans";
    }
    button { cursor: pointer; transition: all .15s; }
    button:hover { border-color: var(--primary); color: var(--primary); }
    button.active { background: var(--primary); border-color: var(--primary); color: white; }
    input { min-width: 260px; }
    .tab { display: none; }
    .tab.active { display: block; animation: fadeIn .18s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
    .grid-2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 10px; vertical-align: top; text-align: left; }
    th { color: #334155; background: #F8FAFC; position: sticky; top: 0; z-index: 1; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
    tr:last-child td { border-bottom: none; }
    /* Root cause / recommendation cards */
    .cause-card {
      background: var(--panel); border: 1px solid var(--line);
      border-left: 5px solid var(--muted);
      border-radius: var(--radius); padding: 16px 18px; margin-bottom: 12px;
      box-shadow: 0 1px 3px rgba(15,23,42,.06);
    }
    .cause-card.sev-P0 { border-left-color: var(--failed); }
    .cause-card.sev-P1 { border-left-color: var(--accent); }
    .cause-card.sev-P2 { border-left-color: var(--primary); }
    .cause-card.sev-Info { border-left-color: var(--success); }
    .cause-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
    .cause-what { font-size: 15px; font-weight: 700; line-height: 1.45; margin: 6px 0 10px; }
    .cause-block { display: grid; grid-template-columns: 72px minmax(0,1fr); gap: 6px 12px; font-size: 13.5px; line-height: 1.6; }
    .cause-label { color: var(--muted); font-weight: 600; white-space: nowrap; }
    .cause-src { margin-top: 10px; padding: 8px 10px; background: #F8FAFC; border: 1px dashed var(--line); border-radius: 6px; font-size: 12.5px; }
    /* Task board */
    .board-goal { font-size: 14px; line-height: 1.6; margin-bottom: 10px; }
    .board-goal .base { font-weight: 700; }
    .facts-list { margin: 8px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.7; }
    /* Timeline */
    .timeline { display: grid; gap: 8px; }
    .event {
      border-left: 4px solid var(--primary); padding: 10px 14px;
      background: white; border-radius: 8px;
      border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line);
    }
    .event.is-error { border-left-color: var(--failed); background: #FFF7F7; }
    .event.is-image { border-left-color: var(--accent); }
    .event-head { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: var(--primary); font-weight: 700; }
    pre {
      margin: 8px 0 0; padding: 12px; background: #0B1220; color: #E2E8F0;
      border-radius: 8px; overflow: auto; max-height: 360px; font-size: 12px;
      white-space: pre-wrap; word-break: break-all;
    }
    .severity-P0 { color: #B91C1C; font-weight: 700; }
    .severity-P1 { color: #B45309; font-weight: 700; }
    .severity-P2 { color: #1E40AF; font-weight: 700; }
    .severity-Info { color: #15803D; font-weight: 700; }
    .muted { color: var(--muted); }
    a { color: var(--primary); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 1100px) {
      .grid-2 { grid-template-columns: 1fr; }
    }
    @media (max-width: 520px) {
      header, .shell { padding-left: 16px; padding-right: 16px; }
      input { min-width: 100%; }
    }
  </style>
</head>
<body>
<script id="report-data" type="application/json">__REPORT_DATA__</script>
<header>
  <div class="header-row">
    <div>
      <h1>Phone Agent 薄 loop 实机诊断</h1>
      <div class="subtitle wrap" id="subtitle"></div>
    </div>
    <div id="verdictChip"></div>
  </div>
</header>
<main class="shell">
  <section class="kpis" id="kpis"></section>
  <nav class="toolbar" id="tabs"></nav>
  <section class="toolbar">
    <input id="search" placeholder="搜索 step / event / tool / file / class">
    <select id="layerFilter"><option value="">全部层级</option></select>
    <select id="severityFilter"><option value="">全部优先级</option></select>
  </section>
  <section id="overview" class="tab active"></section>
  <section id="dimensions" class="tab"></section>
  <section id="timeline" class="tab"></section>
  <section id="source" class="tab"></section>
  <section id="recommendations" class="tab"></section>
  <section id="raw" class="tab"></section>
</main>
<script>
const data = JSON.parse(document.getElementById('report-data').textContent);
const summary = data.summary || {};
const evidence = data.evidence || [];
const state = { tab: 'overview', query: '', layer: '', severity: '' };
const tabs = [
  ['overview', '终局与首页'],
  ['dimensions', '运行维度'],
  ['timeline', '决策时间线'],
  ['source', '源码归因'],
  ['recommendations', '修改建议'],
  ['raw', '原始证据'],
];
const VERDICT_LABEL = { success: '成功', failed: '失败', takeover: '人工接管', max_steps: '步数耗尽', uncertain: '不确定' };
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function json(v) { return esc(JSON.stringify(v, null, 2)); }
function badge(text, cls='') { return `<span class="badge ${cls}">${esc(text)}</span>`; }
function num(v) { return (v ?? v === 0) ? esc(v) : '-'; }
function matches(text) { return !state.query || String(text).toLowerCase().includes(state.query.toLowerCase()); }
function row(k, v) { return `<tr><td class="mono">${esc(k)}</td><td class="wrap">${esc(v ?? '-')}</td></tr>`; }

function render() {
  document.getElementById('subtitle').innerHTML =
    `<strong>${esc(summary.target)}</strong><br><span class="mono wrap">${esc(summary.run_id)} · ${esc(summary.created_at || '')} · ${esc(summary.steps ?? '-')} 步 · ${esc((summary.duration_sec ?? 0))}s</span>`;
  document.getElementById('verdictChip').innerHTML =
    `<span class="verdict-chip ${esc(summary.verdict)}">${esc(VERDICT_LABEL[summary.verdict] || summary.verdict)}</span>`;
  renderKpis();
  renderTabs();
  renderFilters();
  renderOverview();
  renderDimensions();
  renderTimeline();
  renderSource();
  renderRecommendations();
  renderRaw();
}
function renderKpis() {
  const th = summary.tool_health || {};
  const g = summary.grounding || {};
  const v = summary.visual || {};
  const td = summary.taskdoc_final || {};
  const items = [
    ['结论', VERDICT_LABEL[summary.verdict] || summary.verdict || '-'],
    ['步数', num(summary.steps)],
    ['工具错误率', th.total_calls ? Math.round((th.error_rate || 0) * 100) + '%' : '-'],
    ['TaskDoc 终态', td.terminal_state || '-'],
    ['开放项', num(td.open_item_count)],
    ['视觉回流', (v.tool_results_with_image ?? 0) > 0 ? '有(' + v.tool_results_with_image + ')' : '无'],
    ['mark 寻址', `${(g.mark_addressing||{}).by_mark_id ?? 0}/${(g.mark_addressing||{}).by_description ?? 0}`],
  ];
  document.getElementById('kpis').innerHTML = items.map(([k, val]) =>
    `<div class="card"><div class="kpi-label">${k}</div><div class="kpi-value wrap">${esc(val)}</div></div>`).join('');
}
function renderTabs() {
  document.getElementById('tabs').innerHTML = tabs.map(([id, label]) =>
    `<button class="${state.tab === id ? 'active' : ''}" onclick="state.tab='${id}'; selectTab()">${label}</button>`).join('');
}
function selectTab() {
  for (const [id] of tabs) document.getElementById(id).classList.toggle('active', state.tab === id);
  renderTabs();
}
function renderFilters() {
  const layers = [...new Set((summary.findings || []).map(f => f.layer).filter(Boolean))];
  const severities = [...new Set((summary.findings || []).map(f => f.severity).filter(Boolean))];
  const layer = document.getElementById('layerFilter');
  const severity = document.getElementById('severityFilter');
  if (layer.options.length <= 1) layer.innerHTML += layers.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (severity.options.length <= 1) severity.innerHTML += severities.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
}

// ---- first-page block 1: terminal verdict --------------------------------
function renderTerminalBlock() {
  const t = summary.terminal || {};
  const fg = summary.finish_gate || {};
  const rejections = (fg.rejections || []);
  let banner = '';
  if (fg.blocked_by_open_items) {
    banner = `<div class="alert warn">finish 被开放项拦截：路线未闭合。finish gate fail-closed，应先完成/标 blocked（带 reason）或用 update_task_doc 修正路线，绝不放宽 gate。</div>`;
  }
  const openList = (fg.open_items_at_finish || []);
  return `<div class="card">
    <h2>终局裁定</h2>
    ${banner}
    <table>
      ${row('目标', summary.target)}
      ${row('结论', VERDICT_LABEL[summary.verdict] || summary.verdict)}
      ${row('已声明完成', t.finished)}
      ${row('完成摘要', t.finish_summary)}
      ${row('接管原因', t.takeover_reason)}
      ${row('停止原因', t.reason)}
      ${row('finish 尝试', fg.attempted)}
      ${row('finish 被接受', fg.accepted)}
      ${row('被开放项拦截', fg.blocked_by_open_items)}
    </table>
    ${rejections.length ? `<table style="margin-top:10px"><tr><th>step</th><th>class</th><th>message</th></tr>
      ${rejections.map(r => `<tr><td class="mono">${num(r.step)}</td><td class="mono">${badge(r.class,'failed')}</td><td class="wrap">${esc(r.message)}</td></tr>`).join('')}
    </table>` : ''}
    ${openList.length ? `<div class="cause-src mono wrap">finish 时仍开放：${openList.map(esc).join(' · ')}</div>` : ''}
  </div>`;
}

// ---- first-page block 2: task board --------------------------------------
function renderTaskBoardBlock() {
  const td = summary.taskdoc_final || {};
  const c = td.counts || {};
  if (td.terminal_state === 'no_board' || !td.items) {
    return `<div class="card"><h2>TaskDoc 板</h2><div class="muted">无任务板记录（taskdoc 关闭或未写入）。</div></div>`;
  }
  const items = td.items || [];
  const amendments = td.amendments || [];
  const facts = td.facts || [];
  return `<div class="card">
    <h2>TaskDoc 板</h2>
    <div class="board-goal">
      <div><span class="cause-label">目标</span> <span class="base wrap">${esc(td.goal_base)}</span></div>
      ${amendments.length ? `<div><span class="cause-label">补充</span> <span class="wrap">${amendments.map(esc).join('；')}</span></div>` : ''}
    </div>
    <div class="cause-head">
      ${badge('总 ' + (c.total ?? 0))}
      ${badge('完成 ' + (c.completed ?? 0), 'completed')}
      ${badge('进行 ' + (c.in_progress ?? 0), 'in_progress')}
      ${badge('待办 ' + (c.pending ?? 0), 'pending')}
      ${badge('阻塞 ' + (c.blocked ?? 0), 'blocked')}
      ${badge('终态 ' + (td.terminal_state || '-'))}
    </div>
    <table style="margin-top:10px"><tr><th>id</th><th>路线项</th><th>状态</th><th>原因</th></tr>
      ${items.length ? items.map(it => `<tr>
        <td class="mono">${esc(it.id)}</td>
        <td class="wrap">${esc(it.content)}</td>
        <td>${badge(it.status, it.status)}</td>
        <td class="wrap muted">${esc(it.reason || '-')}</td>
      </tr>`).join('') : '<tr><td colspan="4" class="muted">路线为空</td></tr>'}
    </table>
    ${facts.length ? `<h3 style="margin-top:12px">关键事实</h3><ul class="facts-list">${facts.map(f => `<li class="wrap">${esc(f)}</li>`).join('')}</ul>` : ''}
  </div>`;
}

// ---- first-page block 3: 80/20 recommendations ---------------------------
function renderTopThree() {
  const recs = (summary.recommendations || []).slice(0, 3);
  if (!recs.length) return `<div class="card"><h2>80/20 三件事</h2><div class="muted">未发现需要修改的高优先级问题。</div></div>`;
  return `<div class="card"><h2>80/20 三件事</h2>${recs.map(r => `
    <div class="cause-card sev-${esc(r.priority)}">
      <div class="cause-head">${badge(r.id)} ${badge(r.priority)} </div>
      <div class="cause-what">${esc(r.title)}</div>
      <div class="cause-block">
        <span class="cause-label">怎么办</span><span class="wrap">${esc(r.recommendation)}</span>
        <span class="cause-label">验证</span><span class="wrap">${esc(r.verification)}</span>
      </div>
      ${(r.target_files || []).length ? `<div class="cause-src mono wrap">📍 ${(r.target_files||[]).map(f => esc(f.path)).join(' · ')}</div>` : ''}
    </div>`).join('')}</div>`;
}

function renderOverview() {
  const v = summary.visual || {};
  let visualAlert = '';
  if ((v.tool_results_with_image ?? 0) === 0 && (summary.steps ?? 0) > 0) {
    visualAlert = `<div class="alert danger">视觉回流断供：tool_results_with_image=0 —— 工具返回未携带截图，模型在纯文本 marks 摘要上盲操作。核对 _obs.py / actuation.py 是否把截图 image 块随工具返回回流。</div>`;
  }
  document.getElementById('overview').innerHTML = `
    ${visualAlert}
    <div class="grid-2">
      ${renderTerminalBlock()}
      ${renderTaskBoardBlock()}
    </div>
    <div style="margin-top:14px">${renderTopThree()}</div>`;
}

function renderDimensions() {
  const s = summary.stagnation || {};
  const c = summary.context || {};
  const h = summary.hitl || {};
  const th = summary.tool_health || {};
  const g = summary.grounding || {};
  const v = summary.visual || {};
  const m = summary.model || {};
  const byTool = th.by_tool || {};
  document.getElementById('dimensions').innerHTML = `<div class="grid-2">
    <div class="card"><h2>停滞</h2><table>
      ${row('触发轻推', s.nudged)}
      ${row('轻推步', s.nudge_step)}
      ${row('最大观测状态数', s.max_seen_states)}
      ${row('停滞连击峰值', s.stagnant_streak_peak)}
    </table></div>
    <div class="card"><h2>上下文留存</h2><table>
      ${row('峰值消息数', c.peak_message_count)}
      ${row('峰值图像消息', c.peak_image_messages)}
      ${row('累计剪除截图', c.pruned_screen_total)}
      ${row('TaskDoc 每步钉入', c.taskdoc_pinned_every_step)}
      ${row('平均上下文字符', c.avg_context_chars)}
    </table>
    ${c.peak_image_messages > 1 ? '<div class="alert warn">峰值图像消息 &gt; 1：图像剪裁可能失效（应恒为 1）。</div>' : ''}
    </div>
    <div class="card"><h2>HITL 小卡</h2><table>
      ${row('中断次数', h.interrupts)}
      ${row('批准 / 拒绝 / 应答', `${h.approvals ?? 0} / ${h.rejections ?? 0} / ${h.responds ?? 0}`)}
      ${row('ask_user 次数', h.ask_user_count)}
      ${row('take_over 次数', h.take_over_count)}
    </table>
    ${(h.decisions || []).length ? `<table style="margin-top:8px"><tr><th>step</th><th>tool</th><th>决定</th></tr>
      ${(h.decisions||[]).map(d => `<tr><td class="mono">${num(d.step)}</td><td class="mono">${esc(d.tool)}</td><td>${badge(d.decision)}</td></tr>`).join('')}</table>` : ''}
    </div>
    <div class="card"><h2>视觉回流</h2><table>
      ${row('带截图的工具返回', v.tool_results_with_image)}
      ${row('累计截图字节', v.total_image_bytes)}
      ${row('首个截图步', v.first_image_step)}
      ${row('末个截图步', v.last_image_step)}
    </table></div>
    <div class="card"><h2>Grounding</h2><table>
      ${row('by_mark_id / by_description', `${(g.mark_addressing||{}).by_mark_id ?? 0} / ${(g.mark_addressing||{}).by_description ?? 0}`)}
      ${row('解析失败 (ambiguous/stale/no_match)', `${(g.resolve_failures||{}).ambiguous ?? 0} / ${(g.resolve_failures||{}).stale ?? 0} / ${(g.resolve_failures||{}).no_match ?? 0}`)}
      ${row('locate (calls/ok/no_match/err)', `${(g.locate||{}).calls ?? 0} / ${(g.locate||{}).success ?? 0} / ${(g.locate||{}).no_match ?? 0} / ${(g.locate||{}).provider_error ?? 0}`)}
      ${row('launch (resolved/denied/unknown/not_installed/ambiguous)', `${(g.launch||{}).resolved ?? 0} / ${(g.launch||{}).denied ?? 0} / ${(g.launch||{}).unknown ?? 0} / ${(g.launch||{}).not_installed ?? 0} / ${(g.launch||{}).ambiguous ?? 0}`)}
    </table></div>
    <div class="card"><h2>模型</h2><table>
      ${row('调用次数', m.calls)}
      ${row('平均延迟(ms)', m.avg_latency_ms)}
      ${row('p95 延迟(ms)', m.p95_latency_ms)}
      ${row('错误', m.errors)}
    </table></div>
    <div class="card" style="grid-column:1 / -1"><h2>工具健康</h2>
      <div class="cause-head">${badge('调用 ' + (th.total_calls ?? 0))} ${badge('错误 ' + (th.total_errors ?? 0), (th.total_errors||0) ? 'failed' : '')} ${badge('错误率 ' + Math.round((th.error_rate||0)*100) + '%')}</div>
      <table><tr><th>tool</th><th>calls</th><th>ok</th><th>error</th><th>error classes</th><th>avg ms</th><th>p95 ms</th></tr>
        ${Object.keys(byTool).length ? Object.entries(byTool).map(([tool, st]) => `<tr>
          <td class="mono">${esc(tool)}</td>
          <td class="mono">${num(st.calls)}</td>
          <td class="mono">${num(st.ok)}</td>
          <td class="mono ${st.error ? 'severity-P0' : ''}">${num(st.error)}</td>
          <td class="mono wrap">${esc(Object.entries(st.error_classes||{}).map(([k,c]) => `${k}×${c}`).join(', ') || '-')}</td>
          <td class="mono">${num(st.avg_latency_ms)}</td>
          <td class="mono">${num(st.p95_latency_ms)}</td>
        </tr>`).join('') : '<tr><td colspan="7" class="muted">无工具调用</td></tr>'}
      </table>
    </div>
  </div>`;
}

function renderTimeline() {
  const rows = evidence.filter(e => {
    const kind = e.event || '';
    return ['tool_invoke','tool_observation','hitl_decision','stagnation_nudge','taskdoc_snapshot','run_end'].includes(kind)
      && matches(`${e.step} ${kind} ${e.tool || ''} ${JSON.stringify(e)}`);
  });
  const isErr = e => e.event === 'tool_observation' && e.error;
  const isImg = e => e.event === 'tool_observation' && (e.image || {}).present;
  document.getElementById('timeline').innerHTML = `<div class="timeline">${rows.map(e => {
    const head = e.event === 'tool_observation'
      ? `${badge('step ' + (e.step ?? '-'))} ${badge(e.tool || '')} ${badge('obs', isErr(e) ? 'failed' : '')} ${isImg(e) ? badge('📷 screen#' + ((e.image||{}).screen_seq ?? '?')) : ''}`
      : e.event === 'tool_invoke'
      ? `${badge('step ' + (e.step ?? '-'))} ${badge(e.tool || '')} ${badge('invoke')}`
      : `${badge('step ' + (e.step ?? '-'))} ${badge(e.event)}`;
    return `<article class="event ${isErr(e) ? 'is-error' : ''} ${isImg(e) ? 'is-image' : ''}">
      <div class="event-head"><div>${head}</div><span class="mono muted">${esc((e.result_text && typeof e.result_text === 'object') ? '截断' : '')}</span></div>
      <details><summary>event</summary><pre>${json(e)}</pre></details>
    </article>`;
  }).join('') || '<div class="card">没有匹配的事件。</div>'}</div>`;
}

function renderFiles(files) {
  return (files || []).map(file => {
    const anchors = (file.anchors || []).slice(0, 3).map(a => `${a.symbol}:${a.line}`).join(', ');
    const missing = file.exists === false ? ' <span class="severity-P0">(缺失)</span>' : '';
    return `<div class="mono wrap">${esc(file.path)}${missing}${anchors ? '<br><span class="muted">' + esc(anchors) + '</span>' : ''}</div>`;
  }).join('');
}
function filteredFindings() {
  return (summary.findings || []).filter(f => {
    const text = JSON.stringify(f);
    return (!state.layer || f.layer === state.layer) && (!state.severity || f.severity === state.severity) && matches(text);
  });
}
function renderSource() {
  const rows = filteredFindings();
  document.getElementById('source').innerHTML = `<div class="card"><h2>源码归因（v2 source map）</h2><table>
    <tr><th>优先级</th><th>层级</th><th>现象</th><th>次数</th><th>源码位置</th><th>建议 / 验证</th></tr>
    ${rows.length ? rows.map(f => `<tr>
      <td class="severity-${esc(f.severity)}">${esc(f.severity)}</td>
      <td class="mono">${esc(f.layer)}</td>
      <td class="wrap">${esc(f.title)}<br><span class="muted mono">${esc((f.examples||[]).slice(0,1).join(''))}</span></td>
      <td class="mono">${num(f.count)}</td>
      <td>${renderFiles(f.files || [])}</td>
      <td class="wrap">${esc(f.suggestion)}<br><span class="muted">${esc(f.verify)}</span></td>
    </tr>`).join('') : '<tr><td colspan="6" class="muted">当前筛选下无源码归因条目。</td></tr>'}
  </table></div>`;
}
function renderRecommendations() {
  const rows = (summary.recommendations || []).filter(r => matches(JSON.stringify(r)) && (!state.severity || r.priority === state.severity));
  document.getElementById('recommendations').innerHTML = `<div class="card"><h2>修改建议</h2><table>
    <tr><th>ID</th><th>优先级</th><th>建议</th><th>目标文件</th><th>验证方式</th></tr>
    ${rows.length ? rows.map(r => `<tr>
      <td class="mono">${esc(r.id)}</td>
      <td class="severity-${esc(r.priority)}">${esc(r.priority)}</td>
      <td class="wrap"><strong>${esc(r.title)}</strong><br>${esc(r.recommendation)}</td>
      <td>${renderFiles(r.target_files || [])}</td>
      <td class="wrap">${esc(r.verification)}</td>
    </tr>`).join('') : '<tr><td colspan="5" class="muted">无建议条目。</td></tr>'}
  </table></div>`;
}
function renderRaw() {
  document.getElementById('raw').innerHTML = `<div class="grid-2">
    <div class="card"><h2>Summary JSON</h2><pre>${json(summary)}</pre></div>
    <div class="card"><h2>Evidence 事件（前 200）</h2><pre>${json(evidence.slice(0, 200))}</pre></div>
  </div>`;
}
document.getElementById('search').addEventListener('input', e => { state.query = e.target.value; renderTimeline(); renderSource(); renderRecommendations(); });
document.getElementById('layerFilter').addEventListener('change', e => { state.layer = e.target.value; renderSource(); });
document.getElementById('severityFilter').addEventListener('change', e => { state.severity = e.target.value; renderSource(); renderRecommendations(); });
render();
</script>
</body>
</html>
"""


__all__ = ["render_html", "HTML_TEMPLATE"]

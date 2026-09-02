"""Render the v2 ``summary.json`` (+ evidence stream) into an interactive HTML report.

Per ``outputs/design-council/ROUND2-D1.md`` §4, **rebuilt for A5** (local-first,
full-fidelity, step-by-step replay). The report is a single self-contained HTML
file whose reader is the **device owner on their own machine**:

* **Full fidelity** — the default artifacts are unredacted; the report shows the
  real model thinking, tool args, tool results, task board, and terminal text.
* **Screenshots on disk** — the diagnostic middleware lands each screenshot at
  ``<run_dir>/screenshots/screen-<seq>.png`` and records a relative ``image.path``.
  The report renders the *real screenshot* (``<img src="screenshots/…">``, click
  to open full-size) beside each step. The old "never store an image" constraint
  is intentionally gone — it belonged to the redacted-share world.
* **Step-by-step replay is the core** — every step is: screenshot thumbnail +
  full model thinking + tool call & args + full result + latency.

The design system is carried over from the v1 template (primary ``#1E40AF`` /
accent ``#F59E0B`` / Fira Code, ``<base target="_blank">``, KPI cards, cause
cards, long-path wrapping). Data is embedded as a ``</``-safe JSON island so the
report is offline and never executes its payload.

Sharing: an explicit ``--share`` export (``run_diagnosis.py``) deep-redacts the
summary + evidence and drops every ``image.path`` before calling this same
renderer, producing ``report-share.html`` with no screenshot references and no
sensitive text. This module itself adds nothing back and re-escapes nothing.
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


def render_html(
    summary: dict[str, Any], evidence: list[dict[str, Any]] | None = None
) -> str:
    """Render ``summary`` (+ optional evidence stream) to an HTML string.

    ``summary`` carries the analyzed dimensions **and** the per-step ``replay``
    list the step-by-step view renders; ``evidence`` powers the raw-events tab.
    Both are embedded as a JSON island and rendered client-side. Screenshots are
    referenced by the relative ``image.path`` the middleware recorded, so the
    report must sit in the run dir (next to ``screenshots/``) to show them.
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
    .badge.error { background: #FEE2E2; color: var(--failed); border-color: #FCA5A5; }
    .badge.blocked { background: #FEF3C7; color: var(--blocked); border-color: #FCD34D; }
    .badge.in_progress { background: #DBEAFE; color: var(--primary); border-color: #93C5FD; }
    .badge.pending { background: #F1F5F9; color: var(--muted); border-color: #CBD5E1; }
    .badge.accent { background: #FEF3C7; color: var(--blocked); border-color: #FCD34D; }
    .alert {
      border-radius: 8px; padding: 10px 14px; margin: 0 0 12px;
      font-weight: 700; word-break: break-all; overflow-wrap: break-word;
    }
    .alert.danger { background: #FEE2E2; color: var(--failed); border: 1px solid #FCA5A5; }
    .alert.warn { background: #FEF3C7; color: var(--blocked); border: 1px solid #FCD34D; }
    .alert.info { background: #DBEAFE; color: var(--primary); border: 1px solid #93C5FD; font-weight: 500; }
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
    .evi-note { color: var(--success); font-size: 12px; margin-top: 3px; }
    /* Step replay */
    .step {
      background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
      margin-bottom: 16px; box-shadow: 0 1px 3px rgba(15,23,42,.06); overflow: hidden;
    }
    .step-head {
      display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
      padding: 12px 16px; background: #F8FAFC; border-bottom: 1px solid var(--line);
    }
    .step-no { font: 700 15px "Fira Code"; color: var(--primary); }
    .step-body { display: grid; grid-template-columns: 300px minmax(0,1fr); gap: 16px; padding: 16px; }
    .shot-col { display: flex; flex-direction: column; gap: 6px; }
    .shot {
      display: block; width: 100%; border: 1px solid var(--line); border-radius: 8px;
      background: #0B1220; object-fit: contain; max-height: 520px;
    }
    .shot-missing {
      display: flex; align-items: center; justify-content: center; min-height: 160px;
      border: 1px dashed var(--line); border-radius: 8px; color: var(--muted);
      font-size: 12.5px; text-align: center; padding: 12px;
    }
    .shot-cap { font-size: 11.5px; color: var(--muted); text-align: center; }
    .think {
      background: #FFFBEB; border: 1px solid #FCD34D; border-radius: 8px;
      padding: 10px 12px; font-size: 13.5px; line-height: 1.6; white-space: pre-wrap;
      word-break: break-word; margin-bottom: 12px;
    }
    .think.empty { background: #F8FAFC; border-color: var(--line); color: var(--muted); }
    .toolcall { border: 1px solid var(--line); border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
    .toolcall.is-error { border-color: #FCA5A5; }
    .toolcall-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 12px; background: #F8FAFC; }
    .toolcall-body { padding: 10px 12px; }
    .kv { font-size: 12.5px; color: var(--muted); margin-bottom: 4px; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: var(--primary); font-weight: 700; }
    pre {
      margin: 8px 0 0; padding: 12px; background: #0B1220; color: #E2E8F0;
      border-radius: 8px; overflow: auto; max-height: 360px; font-size: 12px;
      white-space: pre-wrap; word-break: break-all;
    }
    pre.result { max-height: 320px; }
    .severity-P0 { color: #B91C1C; font-weight: 700; }
    .severity-P1 { color: #B45309; font-weight: 700; }
    .severity-P2 { color: #1E40AF; font-weight: 700; }
    .severity-Info { color: #15803D; font-weight: 700; }
    .muted { color: var(--muted); }
    .bar { height: 8px; border-radius: 4px; background: var(--primary-soft); overflow: hidden; }
    .bar > span { display: block; height: 100%; background: var(--primary); }
    .chip-cloud { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .dimension-stack { display: grid; gap: 14px; margin-top: 14px; }
    a { color: var(--primary); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 1100px) {
      .grid-2 { grid-template-columns: 1fr; }
      .step-body { grid-template-columns: 1fr; }
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
  <section id="overview" class="tab active"></section>
  <section id="replay" class="tab"></section>
  <section id="problems" class="tab"></section>
  <section id="dimensions" class="tab"></section>
  <section id="source" class="tab"></section>
  <section id="raw" class="tab"></section>
</main>
<script>
const data = JSON.parse(document.getElementById('report-data').textContent);
const summary = data.summary || {};
const evidence = data.evidence || [];
const state = { tab: 'overview' };
const tabs = [
  ['overview', '概览与终局'],
  ['replay', '逐步回放'],
  ['problems', '问题分析'],
  ['dimensions', '性能与维度'],
  ['source', '源码归因'],
  ['raw', '原始文件'],
];
const VERDICT_LABEL = { success: '成功', failed: '失败', takeover: '人工接管', max_steps: '步数耗尽', uncertain: '不确定' };
const CLASS_LABEL = {
  success: 'OK', observation: '观测', obs_capture_failed: '再观测失败',
  addressing_conflict: '寻址冲突', addressing_missing: '缺寻址', stale_mark: 'stale mark',
  ambiguous_resolve: '描述歧义', locate_no_match: '未定位', locate_provider_error: '定位失败',
  bad_coords: '坐标非法', bad_direction: '方向非法', ambiguous_app: 'app 歧义',
  launch_denied: '启动被拒', app_not_installed: '未安装', launch_failed: '启动失败', unknown_app: '未知 app',
  taskdoc_input_invalid: '任务板输入无效', taskdoc_validation_failed: '任务板校验失败',
  taskdoc_ok: '任务板已更新', finish_no_evidence: 'finish 无证据',
  finish_blocked_open_items: 'finish 被拦截', finish_ok: 'finish 通过',
  ask_user: '询问用户', takeover_requested: '请求接管', unknown: '未分类',
};
const ERROR_CLASSES = new Set([
  'obs_capture_failed','addressing_conflict','addressing_missing','stale_mark','ambiguous_resolve',
  'locate_no_match','locate_provider_error','bad_coords','bad_direction','ambiguous_app',
  'launch_denied','app_not_installed','launch_failed','unknown_app','taskdoc_input_invalid','taskdoc_validation_failed',
  'finish_no_evidence','finish_blocked_open_items',
]);
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function json(v) { return esc(JSON.stringify(v, null, 2)); }
function badge(text, cls='') { return `<span class="badge ${cls}">${esc(text)}</span>`; }
function num(v) { return (v || v === 0) ? esc(v) : '-'; }
function row(k, v) { return `<tr><td class="mono">${esc(k)}</td><td class="wrap">${esc(v ?? '-')}</td></tr>`; }
function classBadge(cls) {
  const label = CLASS_LABEL[cls] || cls || '未分类';
  return badge(label, ERROR_CLASSES.has(cls) ? 'error' : (cls === 'success' || cls === 'finish_ok' || cls === 'taskdoc_ok' ? 'success' : ''));
}

function render() {
  const m = summary.model || {};
  const usage = m.token_usage || {};
  const tokenStr = usage.total_tokens ? `${usage.total_tokens} tok` : '—';
  document.getElementById('subtitle').innerHTML =
    `<strong>${esc(summary.target)}</strong><br><span class="mono wrap">${esc(summary.run_id)} · ${esc(summary.created_at || '')} · ${esc(summary.steps ?? '-')} 步 · ${esc(summary.duration_sec ?? 0)}s · ${esc(tokenStr)}</span>`;
  document.getElementById('verdictChip').innerHTML =
    `<span class="verdict-chip ${esc(summary.verdict)}">${esc(VERDICT_LABEL[summary.verdict] || summary.verdict)}</span>`;
  renderKpis();
  renderTabs();
  renderOverview();
  renderReplay();
  renderProblems();
  renderDimensions();
  renderSource();
  renderRaw();
}
function renderKpis() {
  const th = summary.tool_health || {};
  const v = summary.visual || {};
  const td = summary.taskdoc_final || {};
  const m = summary.model || {};
  const usage = m.token_usage || {};
  const cfg = ((evidence.find(e => e.event === 'run_start') || {}).config_digest) || {};
  const isDry = (summary.command || []).includes('dry-run');
  const device = cfg.device_id || (isDry ? 'dry-run' : (cfg.grounding_provider || '-'));
  const items = [
    ['结论', VERDICT_LABEL[summary.verdict] || summary.verdict || '-'],
    ['步数', num(summary.steps)],
    ['耗时', (summary.duration_sec ?? 0) + 's'],
    ['Token 用量', usage.total_tokens ? num(usage.total_tokens) : '—'],
    ['工具错误率', th.total_calls ? Math.round((th.error_rate || 0) * 100) + '%' : '-'],
    ['TaskDoc 终态', td.terminal_state || '-'],
    ['截图落盘', (v.tool_results_with_image ?? 0) > 0 ? '有(' + v.tool_results_with_image + ')' : '无'],
    ['设备', device],
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

// ---- overview block 1: header + terminal verdict -------------------------
function renderTerminalBlock() {
  const t = summary.terminal || {};
  const fg = summary.finish_gate || {};
  const m = summary.model || {};
  const usage = m.token_usage || {};
  const cfg = ((evidence.find(e => e.event === 'run_start') || {}).config_digest) || {};
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
      ${row('耗时 / 步数', `${summary.duration_sec ?? 0}s / ${summary.steps ?? '-'} 步`)}
      ${row('Token 用量', usage.total_tokens ? `${usage.total_tokens}（入 ${usage.input_tokens ?? '?'} / 出 ${usage.output_tokens ?? '?'}）` : '模型未上报')}
      ${row('模型 / provider', `${cfg.model_name ?? '-'} / ${cfg.grounding_provider ?? '-'}`)}
      ${row('设备', cfg.device_id ?? '-')}
      ${row('已声明完成', t.finished)}
      ${row('完成摘要', t.finish_summary)}
      ${row('接管原因', t.takeover_reason)}
      ${row('停止原因', t.reason)}
      ${row('finish 尝试 / 被接受', `${fg.attempted} / ${fg.accepted}`)}
    </table>
    ${rejections.length ? `<table style="margin-top:10px"><tr><th>step</th><th>class</th><th>message</th></tr>
      ${rejections.map(r => `<tr><td class="mono">${num(r.step)}</td><td>${classBadge(r.class)}</td><td class="wrap">${esc(r.message)}</td></tr>`).join('')}
    </table>` : ''}
    ${openList.length ? `<div class="cause-src mono wrap">finish 时仍开放：${openList.map(esc).join(' · ')}</div>` : ''}
  </div>`;
}

// ---- overview block 2: task board (terminal card) ------------------------
function renderTaskBoardBlock() {
  const td = summary.taskdoc_final || {};
  const c = td.counts || {};
  if (td.terminal_state === 'no_board' || !td.items) {
    return `<div class="card"><h2>任务板终态</h2><div class="muted">无任务板记录（taskdoc 关闭或未写入）。</div></div>`;
  }
  const items = td.items || [];
  const amendments = td.amendments || [];
  const facts = td.facts || [];
  return `<div class="card">
    <h2>任务板终态</h2>
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
    <table style="margin-top:10px"><tr><th>id</th><th>路线项（带证据）</th><th>状态</th><th>原因</th></tr>
      ${items.length ? items.map(it => `<tr>
        <td class="mono">${esc(it.id)}</td>
        <td class="wrap">${esc(it.content)}${it.evidence_note ? `<div class="evi-note wrap">✓ ${esc(it.evidence_note)}</div>` : ''}</td>
        <td>${badge(it.status, it.status)}</td>
        <td class="wrap muted">${esc(it.reason || '-')}</td>
      </tr>`).join('') : '<tr><td colspan="4" class="muted">路线为空</td></tr>'}
    </table>
    ${facts.length ? `<h3 style="margin-top:12px">关键事实</h3><ul class="facts-list">${facts.map(f => `<li class="wrap">${esc(f)}</li>`).join('')}</ul>` : ''}
  </div>`;
}

// ---- overview block 3: 80/20 recommendations -----------------------------
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

function renderResolverOverview() {
  const r = summary.resolver || {};
  const decisions = r.decision_counts || {};
  const embeddings = r.embedding_launch_hits || [];
  return `<div class="card"><h2>解析</h2><table>
    ${row('launch 解析次数', r.total_attempts ?? 0)}
    ${row('resolved / ambiguous / unknown', `${decisions.resolved ?? 0} / ${decisions.ambiguous ?? 0} / ${decisions.unknown ?? 0}`)}
    ${row('embedding 启动命中', embeddings.length)}
    ${row('歧义后恢复', (r.ambiguous_recoveries || []).length)}
  </table>${(r.total_attempts ?? 0) === 0 ? '<div class="muted" style="margin-top:8px">无 resolution_attempt 事件（旧 run 或本 run 未 launch）。</div>' : ''}</div>`;
}

function renderMemoryOverview() {
  const memory = summary.memory || {};
  const rag = memory.memory_rag || {};
  const episode = memory.episode || {};
  const hit = rag.candidate_count ? (rag.hit ? '命中' : '未命中') : '无候选';
  return `<div class="card"><h2>记忆</h2><table>
    ${row('memory_rag 模式 / 状态', `${rag.mode ?? '-'} / ${rag.status ?? '-'}`)}
    ${row('召回候选 / 实际启动包', `${rag.candidate_count ?? 0} / ${(rag.actual_launch_packages || []).length}`)}
    ${row('逐 run 命中', hit)}
    ${row('别名生命周期事件', (memory.alias_events || []).length)}
    ${row('注入教训', (episode.injected_lessons || []).length)}
    ${row('产出物', episode.deliverable_path || '-')}
  </table></div>`;
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
    <div class="grid-2" style="margin-top:14px">
      ${renderResolverOverview()}
      ${renderMemoryOverview()}
    </div>
    <div style="margin-top:14px">${renderTopThree()}</div>`;
}

// ---- replay (core): screenshot + thinking + tool calls + result ----------
function renderShot(image) {
  if (image && image.path) {
    const cap = image.screen_seq != null ? ('screen#' + image.screen_seq) : '截图';
    return `<div class="shot-col">
      <a href="${esc(image.path)}"><img class="shot" src="${esc(image.path)}" alt="${esc(cap)}" loading="lazy"></a>
      <div class="shot-cap">${esc(cap)} · 点击看大图</div>
    </div>`;
  }
  if (image && image.present) {
    return `<div class="shot-col"><div class="shot-missing">截图已回流但未落盘引用<br>（share 副本或旧版本）</div></div>`;
  }
  return `<div class="shot-col"><div class="shot-missing">本步无截图<br>（工具未回流图像）</div></div>`;
}
function renderToolCall(tc) {
  const isErr = !!tc.error || ERROR_CLASSES.has(tc.class);
  const argStr = tc.args && Object.keys(tc.args || {}).length ? json(tc.args) : '{}';
  const truncNote = tc.result_truncated ? ' <span class="muted">(超 4000 字已截断)</span>' : '';
  return `<div class="toolcall ${isErr ? 'is-error' : ''}">
    <div class="toolcall-head">
      ${badge(tc.tool || '?', isErr ? 'error' : '')}
      ${classBadge(tc.class)}
      ${tc.latency_ms != null ? badge(tc.latency_ms + 'ms') : ''}
      ${(tc.image && tc.image.screen_seq != null) ? badge('screen#' + tc.image.screen_seq, 'accent') : ''}
    </div>
    <div class="toolcall-body">
      <div class="kv">参数</div><pre>${argStr}</pre>
      <div class="kv" style="margin-top:8px">结果${truncNote}</div>
      <pre class="result">${esc(tc.result_text || (tc.error ? ('⚠ ' + tc.error) : '（空返回）'))}</pre>
    </div>
  </div>`;
}
function renderStep(s) {
  const think = (s.thinking || '').trim();
  const calls = s.tool_calls || [];
  // The screenshot to show for the step: the first tool call that produced one.
  const shotImg = (calls.find(c => c.image && (c.image.path || c.image.present)) || {}).image || null;
  const ctx = s.context || {};
  const modelCalls = (s.model_tool_calls || []).map(mc => badge(mc.name || '?')).join(' ');
  const hitl = s.hitl || [];
  return `<div class="step">
    <div class="step-head">
      <span class="step-no">STEP ${esc(s.step ?? '?')}</span>
      ${modelCalls || '<span class="muted">（无工具调用）</span>'}
      ${ctx.image_message_count != null ? badge('图消息 ' + ctx.image_message_count) : ''}
      ${ctx.pruned_screen_count ? badge('剪除 ' + ctx.pruned_screen_count) : ''}
      ${ctx.context_chars != null ? badge(ctx.context_chars + ' 字') : ''}
      ${ctx.taskdoc_present ? badge('TaskDoc✓', 'success') : ''}
    </div>
    <div class="step-body">
      ${renderShot(shotImg)}
      <div class="detail-col">
        <div class="kv">模型思考</div>
        <div class="think ${think ? '' : 'empty'}">${think ? esc(think) : '（本步模型未输出文本思考）'}</div>
        ${calls.length ? calls.map(renderToolCall).join('') : '<div class="muted">本步没有工具调用（可能是终局消息）。</div>'}
        ${hitl.length ? `<div class="alert info">HITL：${hitl.map(h => esc((h.decision || '') + ' — ' + (h.requested_action || ''))).join('；')}</div>` : ''}
      </div>
    </div>
  </div>`;
}
function renderReplay() {
  const replay = summary.replay || [];
  const host = document.getElementById('replay');
  if (!replay.length) {
    host.innerHTML = `<div class="card"><h2>逐步回放</h2><div class="muted">无回放数据（evidence 未记录 model_response/tool 事件）。</div></div>`;
    return;
  }
  host.innerHTML = `<div class="alert info">逐步回放：每步 = 真实截图（点击看大图）+ 模型思考全文 + 工具调用与参数 + 结果全文 + 延迟。截图落盘在 <span class="mono">screenshots/</span>，报告需与其同目录。</div>
    ${replay.map(renderStep).join('')}`;
}

// ---- problems: taxonomy errors + stagnation + finish + hitl --------------
function renderProblems() {
  const th = summary.tool_health || {};
  const byTool = th.by_tool || {};
  const s = summary.stagnation || {};
  const fg = summary.finish_gate || {};
  const h = summary.hitl || {};
  // Aggregate error classes across tools.
  const errAgg = {};
  for (const st of Object.values(byTool)) {
    for (const [cls, n] of Object.entries(st.error_classes || {})) errAgg[cls] = (errAgg[cls] || 0) + n;
  }
  const errRows = Object.entries(errAgg).sort((a,b) => b[1]-a[1]);
  const stagAlert = s.nudged
    ? `<div class="alert warn">停滞轻推已触发（step ${num(s.nudge_step)}，停滞连击峰值 ${num(s.stagnant_streak_peak)}）——模型可能在原地打转。</div>`
    : `<div class="alert info">未触发停滞轻推；停滞连击峰值 ${num(s.stagnant_streak_peak)}（上升但未触发属正常探索）。</div>`;
  document.getElementById('problems').innerHTML = `
    <div class="card">
      <h2>错误分类统计（taxonomy）</h2>
      ${errRows.length ? `<table><tr><th>class</th><th>次数</th></tr>
        ${errRows.map(([cls, n]) => `<tr><td>${classBadge(cls)}</td><td class="mono">${n}</td></tr>`).join('')}</table>`
        : '<div class="muted">全程无被分类为错误的工具返回。</div>'}
    </div>
    <div class="card" style="margin-top:14px"><h2>停滞</h2>${stagAlert}
      <table>
        ${row('触发轻推', s.nudged)}
        ${row('轻推步', s.nudge_step)}
        ${row('最大观测状态数', s.max_seen_states)}
        ${row('停滞连击峰值', s.stagnant_streak_peak)}
      </table>
    </div>
    <div class="card" style="margin-top:14px"><h2>完成门（finish gate）</h2>
      <table>
        ${row('尝试 finish', fg.attempted)}
        ${row('被接受', fg.accepted)}
        ${row('被开放项拦截', fg.blocked_by_open_items)}
      </table>
      ${(fg.rejections || []).length ? `<h3 style="margin-top:10px">被拒历史</h3><table><tr><th>step</th><th>class</th><th>message</th></tr>
        ${(fg.rejections||[]).map(r => `<tr><td class="mono">${num(r.step)}</td><td>${classBadge(r.class)}</td><td class="wrap">${esc(r.message)}</td></tr>`).join('')}</table>`
        : '<div class="muted" style="margin-top:8px">无 finish 被拒记录。</div>'}
    </div>
    <div class="card" style="margin-top:14px"><h2>HITL 事件</h2>
      <table>
        ${row('中断次数', h.interrupts)}
        ${row('批准 / 拒绝 / 应答', `${h.approvals ?? 0} / ${h.rejections ?? 0} / ${h.responds ?? 0}`)}
        ${row('ask_user / take_over', `${h.ask_user_count ?? 0} / ${h.take_over_count ?? 0}`)}
      </table>
      ${(h.decisions || []).length ? `<table style="margin-top:8px"><tr><th>step</th><th>tool</th><th>决定</th></tr>
        ${(h.decisions||[]).map(d => `<tr><td class="mono">${num(d.step)}</td><td class="mono">${esc(d.tool)}</td><td>${badge(d.decision)}</td></tr>`).join('')}</table>` : ''}
    </div>`;
}

// ---- dimensions: perf distribution + tool/grounding/context/model --------
function renderDimensions() {
  const c = summary.context || {};
  const th = summary.tool_health || {};
  const g = summary.grounding || {};
  const v = summary.visual || {};
  const m = summary.model || {};
  const usage = m.token_usage || {};
  const resolver = summary.resolver || {};
  const memory = summary.memory || {};
  const rag = memory.memory_rag || {};
  const episode = memory.episode || {};
  const capabilities = summary.capabilities || {};
  const byTool = th.by_tool || {};
  const maxLat = Math.max(1, ...Object.values(byTool).map(st => st.p95_latency_ms || 0));
  const routeStats = resolver.route_stats || {};
  const routeRows = ['exact','lexical','pinyin','embedding'];
  const capItems = capabilities.items || [];
  const ragVerdict = (rag.candidate_count ?? 0) === 0 ? 'no candidates' : (rag.hit ? 'run hit' : 'run miss');
  document.getElementById('dimensions').innerHTML = `<div class="grid-2">
    <div class="card"><h2>模型</h2><table>
      ${row('调用次数', m.calls)}
      ${row('输入 token', usage.input_tokens)}
      ${row('输出 token', usage.output_tokens)}
      ${row('总 token', usage.total_tokens)}
    </table><div class="muted" style="margin-top:6px;font-size:12px">模型逐步延迟见 traces/&lt;run_id&gt;.jsonl（P0 #6 生产 trace）。</div></div>
    <div class="card"><h2>上下文卫生</h2><table>
      ${row('峰值消息数', c.peak_message_count)}
      ${row('峰值图像消息', c.peak_image_messages)}
      ${row('累计剪除截图', c.pruned_screen_total)}
      ${row('TaskDoc 每步钉入', c.taskdoc_pinned_every_step)}
      ${row('平均上下文字符', c.avg_context_chars)}
    </table>
    ${c.peak_image_messages > 1 ? '<div class="alert warn">峰值图像消息 &gt; 1：图像剪裁可能失效（应恒为 1）。</div>' : ''}
    </div>
    <div class="card"><h2>视觉回流</h2><table>
      ${row('带截图的工具返回', v.tool_results_with_image)}
      ${row('累计截图字节', v.total_image_bytes)}
      ${row('首个 / 末个截图步', `${v.first_image_step ?? '-'} / ${v.last_image_step ?? '-'}`)}
    </table></div>
    <div class="card"><h2>Grounding</h2><table>
      ${row('by_mark_id / by_description', `${(g.mark_addressing||{}).by_mark_id ?? 0} / ${(g.mark_addressing||{}).by_description ?? 0}`)}
      ${row('解析失败 (ambiguous/stale/no_match)', `${(g.resolve_failures||{}).ambiguous ?? 0} / ${(g.resolve_failures||{}).stale ?? 0} / ${(g.resolve_failures||{}).no_match ?? 0}`)}
      ${row('locate (calls/ok/no_match/err)', `${(g.locate||{}).calls ?? 0} / ${(g.locate||{}).success ?? 0} / ${(g.locate||{}).no_match ?? 0} / ${(g.locate||{}).provider_error ?? 0}`)}
    </table></div>
    <div class="card" style="grid-column:1 / -1"><h2>工具健康与延迟分布</h2>
      <div class="cause-head">${badge('调用 ' + (th.total_calls ?? 0))} ${badge('错误 ' + (th.total_errors ?? 0), (th.total_errors||0) ? 'failed' : '')} ${badge('错误率 ' + Math.round((th.error_rate||0)*100) + '%')}</div>
      <table><tr><th>tool</th><th>calls</th><th>ok</th><th>error</th><th>error classes</th><th>avg ms</th><th>p95 ms</th><th>p95 分布</th></tr>
        ${Object.keys(byTool).length ? Object.entries(byTool).map(([tool, st]) => `<tr>
          <td class="mono">${esc(tool)}</td>
          <td class="mono">${num(st.calls)}</td>
          <td class="mono">${num(st.ok)}</td>
          <td class="mono ${st.error ? 'severity-P0' : ''}">${num(st.error)}</td>
          <td class="mono wrap">${esc(Object.entries(st.error_classes||{}).map(([k,cc]) => `${k}×${cc}`).join(', ') || '-')}</td>
          <td class="mono">${num(st.avg_latency_ms)}</td>
          <td class="mono">${num(st.p95_latency_ms)}</td>
          <td style="min-width:120px"><div class="bar"><span style="width:${Math.round(100*(st.p95_latency_ms||0)/maxLat)}%"></span></div></td>
        </tr>`).join('') : '<tr><td colspan="8" class="muted">无工具调用</td></tr>'}
      </table>
    </div>
  </div>
  <div class="dimension-stack">
    <div class="card"><h2>应用名解析路分布</h2>
      <table><tr><th>route</th><th>attempts</th><th>resolved</th><th>launch ok</th><th>解析率</th><th>启动率</th></tr>
        ${routeRows.map(route => { const st = routeStats[route] || {}; return `<tr>
          <td>${badge(route, route === 'embedding' ? 'accent' : '')}</td>
          <td class="mono">${num(st.attempts ?? 0)}</td><td class="mono">${num(st.resolved ?? 0)}</td>
          <td class="mono">${num(st.successful_launches ?? 0)}</td>
          <td class="mono">${Math.round((st.resolution_rate || 0) * 100)}%</td>
          <td class="mono">${Math.round((st.launch_success_rate || 0) * 100)}%</td>
        </tr>`; }).join('')}
      </table>
      ${(resolver.attempts || []).length ? `<table style="margin-top:12px"><tr><th># / step</th><th>mention</th><th>route / type</th><th>top1 / rank_score / margin</th><th>decision</th><th>basis</th><th>launch</th></tr>
        ${(resolver.attempts || []).map(a => `<tr><td class="mono">${num(a.launch_index)} / ${num(a.step)}</td>
          <td class="wrap">${esc(a.mention)}</td><td>${badge(a.route || '-')} ${badge(a.match_type || '-')}</td>
          <td class="mono wrap">${esc(a.top1_package || '-')} · ${num(a.top1_score)} · ${num(a.margin)}</td>
          <td>${badge(a.decision || '-')}</td><td class="mono wrap">${esc(a.decision_basis || '-')}<br><span class="muted">${esc(a.reason || '')}</span></td>
          <td class="mono wrap">${a.launch_succeeded ? '✓ ' + esc(a.launched_package) : '-'}</td></tr>`).join('')}
      </table>` : '<div class="muted" style="margin-top:8px">无解析尝试。</div>'}
    </div>
    <div class="card"><h2>召回候选 vs 实际启动</h2>
      <div class="cause-head">${badge('mode ' + (rag.mode || '-'))} ${badge('候选 ' + (rag.candidate_count ?? 0))} ${badge(ragVerdict, rag.hit ? 'success' : ((rag.candidate_count||0) ? 'error' : ''))}</div>
      <div class="mono wrap muted">实际启动：${esc((rag.actual_launch_packages || []).join(', ') || '-')} · 命中：${esc((rag.matched_packages || []).join(', ') || '-')}</div>
      <table style="margin-top:10px"><tr><th>rank</th><th>namespace / ref</th><th>score</th><th>候选包</th><th>命中</th></tr>
        ${(rag.candidates || []).length ? (rag.candidates || []).map(cand => `<tr>
          <td class="mono">${num(cand.rank)}</td><td class="mono wrap">${esc(cand.namespace || '-')}<br><span class="muted">${esc(cand.ref_id || '-')}</span></td>
          <td class="mono">${num(cand.score)}</td><td class="mono wrap">${esc((cand.packages || []).join(', ') || '-')}</td>
          <td>${badge(cand.hit ? 'hit' : 'miss', cand.hit ? 'success' : 'pending')}</td></tr>`).join('') : '<tr><td colspan="5" class="muted">无 run_start memory_rag 候选。</td></tr>'}
      </table>
    </div>
    <div class="card"><h2>别名生命周期与 run 产物</h2>
      <table><tr><th>op</th><th>kind</th><th>term</th><th>package 变更</th><th>ts</th></tr>
        ${(memory.alias_events || []).length ? (memory.alias_events || []).map(event => `<tr>
          <td>${badge(event.op || '-')}</td><td class="mono">${esc(event.kind || '-')}</td><td class="wrap">${esc(event.term || '-')}</td>
          <td class="mono wrap">${esc(event.old_package || event.package || '-')} ${event.new_package ? '→ ' + esc(event.new_package) : ''}</td>
          <td class="mono wrap">${esc(event.ts || '-')}</td></tr>`).join('') : '<tr><td colspan="5" class="muted">本 run 无 learned / overwritten / user 写入事件。</td></tr>'}
      </table>
      <div class="cause-src"><strong>injected_lessons</strong> <span class="mono wrap">${esc((episode.injected_lessons || []).join(', ') || '-')}</span><br>
        <strong>deliverable_path</strong> <span class="mono wrap">${esc(episode.deliverable_path || '-')}</span></div>
    </div>
    <div class="card"><h2>能力挂载快照</h2>
      <div class="chip-cloud">${capItems.length ? capItems.map(cap => badge(`${cap.cap_id}:${cap.mode}/${cap.state}`, cap.state === 'active' ? 'success' : (cap.state === 'pending' ? 'blocked' : 'pending'))).join('') : '<span class="muted">无 capability_snapshot（旧 run 或 trace 缺失）。</span>'}</div>
      ${capItems.some(cap => (cap.missing_deps || []).length) ? `<table style="margin-top:10px"><tr><th>cap_id</th><th>missing deps</th></tr>${capItems.filter(cap => (cap.missing_deps || []).length).map(cap => `<tr><td class="mono">${esc(cap.cap_id)}</td><td class="mono wrap">${esc(cap.missing_deps.join(', '))}</td></tr>`).join('')}</table>` : ''}
      <div class="mono wrap muted" style="margin-top:10px">memory generation: ${esc(JSON.stringify(capabilities.memory_generation ?? null))}</div>
    </div>
  </div>`;
}

// ---- source attribution ---------------------------------------------------
function renderFiles(files) {
  return (files || []).map(file => {
    const anchors = (file.anchors || []).slice(0, 3).map(a => `${a.symbol}:${a.line}`).join(', ');
    const missing = file.exists === false ? ' <span class="severity-P0">(缺失)</span>' : '';
    return `<div class="mono wrap">${esc(file.path)}${missing}${anchors ? '<br><span class="muted">' + esc(anchors) + '</span>' : ''}</div>`;
  }).join('');
}
function renderSource() {
  const rows = summary.findings || [];
  document.getElementById('source').innerHTML = `<div class="card"><h2>源码归因（v2 source map）</h2><table>
    <tr><th>优先级</th><th>层级</th><th>现象</th><th>次数</th><th>源码位置</th><th>建议 / 验证</th></tr>
    ${rows.length ? rows.map(f => `<tr>
      <td class="severity-${esc(f.severity)}">${esc(f.severity)}</td>
      <td class="mono">${esc(f.layer)}</td>
      <td class="wrap">${esc(f.title)}<br><span class="muted mono">${esc((f.examples||[]).slice(0,1).join(''))}</span></td>
      <td class="mono">${num(f.count)}</td>
      <td>${renderFiles(f.files || [])}</td>
      <td class="wrap">${esc(f.suggestion)}<br><span class="muted">${esc(f.verify)}</span></td>
    </tr>`).join('') : '<tr><td colspan="6" class="muted">未发现被归因的错误类别。</td></tr>'}
  </table></div>
  <div class="card" style="margin-top:14px"><h2>全部修改建议</h2><table>
    <tr><th>ID</th><th>优先级</th><th>建议</th><th>目标文件</th><th>验证</th></tr>
    ${(summary.recommendations || []).length ? (summary.recommendations||[]).map(r => `<tr>
      <td class="mono">${esc(r.id)}</td>
      <td class="severity-${esc(r.priority)}">${esc(r.priority)}</td>
      <td class="wrap"><strong>${esc(r.title)}</strong><br>${esc(r.recommendation)}</td>
      <td>${renderFiles(r.target_files || [])}</td>
      <td class="wrap">${esc(r.verification)}</td>
    </tr>`).join('') : '<tr><td colspan="5" class="muted">无建议。</td></tr>'}
  </table></div>`;
}

// ---- raw file links + json -----------------------------------------------
function renderRaw() {
  const a = summary.artifacts || {};
  const links = [
    ['summary.json', a.summary || 'summary.json'],
    ['evidence.jsonl', a.evidence || summary.evidence_stream || 'evidence.jsonl'],
    ['traces/', summary.trace || 'traces'],
    ['run_dir', summary.run_dir || '.'],
  ];
  const notes = summary.notes || [];
  document.getElementById('raw').innerHTML = `
    ${notes.length ? `<div class="alert info">${notes.map(esc).join('<br>')}</div>` : ''}
    <div class="card"><h2>原始文件链接</h2>
      <table><tr><th>文件</th><th>路径</th></tr>
        ${links.map(([k, p]) => `<tr><td class="mono">${esc(k)}</td><td class="wrap"><a href="${esc(p)}">${esc(p)}</a></td></tr>`).join('')}
      </table>
      <div class="muted" style="margin-top:8px;font-size:12px">截图位于 <span class="mono">screenshots/screen-&lt;seq&gt;.png</span>；本报告需与该目录同级方能显示缩略图。</div>
    </div>
    <div class="grid-2" style="margin-top:14px">
      <div class="card"><h2>Summary JSON</h2><pre>${json(summary)}</pre></div>
      <div class="card"><h2>Evidence 事件（前 200）</h2><pre>${json(evidence.slice(0, 200))}</pre></div>
    </div>`;
}
render();
</script>
</body>
</html>
"""


__all__ = ["render_html", "HTML_TEMPLATE"]

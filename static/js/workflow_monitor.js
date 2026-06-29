let _workflowLoaded = false;
let _workflowRenderSeq = 0;

if (window.mermaid) {
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "loose", flowchart: { useMaxWidth: false } });
}

function _wfSanitizeLabel(text) {
    return String(text).replace(/"/g, "'").replace(/[\[\]]/g, "");
}

function _wfBuildMermaid(nodes, edges) {
    const lines = ["graph LR"];
    nodes.forEach(n => {
        lines.push(`  ${n.id}["${_wfSanitizeLabel(n.label)}"]:::${n.status}`);
    });
    edges.forEach(e => {
        lines.push(`  ${e.from} --> ${e.to}`);
    });
    lines.push("classDef green fill:#1b5e20,stroke:#43a047,color:#e8f5e9;");
    lines.push("classDef amber fill:#8a6d00,stroke:#ffb300,color:#fff8e1;");
    lines.push("classDef red fill:#7f1d1d,stroke:#ef5350,color:#ffebee;");
    lines.push("classDef disabled fill:#2a2a2a,stroke:#555,color:#888;");
    lines.push("classDef external fill:#004d54,stroke:#00bcd4,color:#b2ebf2;");
    lines.push("classDef manual fill:#4a148c,stroke:#ab47bc,color:#f3e5f5;");
    return lines.join("\n");
}

function _wfRenderConflicts(conflicts) {
    const container = document.getElementById("workflow-conflicts");
    if (!conflicts.length) {
        container.innerHTML = '<p class="text-muted text-sm">No conflicts detected.</p>';
        return;
    }
    const order = { critical: 0, warning: 1, info: 2 };
    conflicts.sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
    container.innerHTML = conflicts.map(c => `
        <div class="wf-conflict wf-conflict-${c.severity}">
            <span class="wf-conflict-type">${c.type.replace(/_/g, " ")}</span>
            <span class="wf-conflict-msg">${c.message}</span>
        </div>`).join("");
}

async function loadWorkflowMonitor() {
    const container = document.getElementById("workflow-graph-container");
    if (container) container.innerHTML = '<p class="text-muted text-sm">Loading workflow graph…</p>';
    try {
        const resp = await fetch("/api/workflow-monitor/status");
        const data = await resp.json();
        if (data.status !== "success") throw new Error(data.message || "request failed");

        _wfRenderConflicts(data.conflicts || []);

        if (!container) { _workflowLoaded = true; return; }

        if (!window.mermaid) {
            container.innerHTML = '<p class="text-red text-sm">Mermaid library not loaded — the graph bundle has not been fetched yet.</p>';
            return;
        }
        const def = _wfBuildMermaid(data.nodes || [], data.edges || []);
        const { svg } = await mermaid.render(`wfGraph${++_workflowRenderSeq}`, def);
        container.innerHTML = svg;
        _workflowLoaded = true;
    } catch (e) {
        if (container) container.innerHTML = `<p class="text-red text-sm">Failed to load workflow graph: ${e.message}</p>`;
    }
}

async function openWorkflowGraphTab() {
    const win = window.open("", "_blank");
    if (!win) return;
    win.document.write('<!doctype html><title>Workflow Monitor</title><body style="margin:0;background:#0e0e0e;color:#aaa;font-family:sans-serif;padding:16px;">Loading…</body>');
    try {
        const resp = await fetch("/api/workflow-monitor/status");
        const data = await resp.json();
        if (data.status !== "success") throw new Error(data.message || "request failed");
        const def = _wfBuildMermaid(data.nodes || [], data.edges || []);
        const { svg } = await mermaid.render(`wfGraphTab${++_workflowRenderSeq}`, def);
        const nodeJson = JSON.stringify(data.nodes || []).replace(/<\/script>/gi, "<\\/script>");
        const base = location.origin;
        const ts = new Date().toLocaleString();
        win.document.open();
        win.document.write(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Workflow Monitor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#0e0e0e;color:#ccc;font-family:system-ui,sans-serif;overflow:hidden}
#wf-header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:10px 16px;background:#111;border-bottom:1px solid #222;flex-shrink:0}
#wf-header h1{font-size:15px;font-weight:600;color:#e0e0e0;margin:0}
#wf-header p{font-size:11px;color:#888;margin:2px 0 0}
.wf-hdr-right{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.wf-legend{display:flex;align-items:center;gap:10px;font-size:11px}
.wf-legend span{display:flex;align-items:center;gap:4px}
.wf-dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.wf-dot-green{background:#43a047}.wf-dot-amber{background:#ffb300}.wf-dot-red{background:#ef5350}.wf-dot-grey{background:#555}.wf-dot-cyan{background:#00bcd4}.wf-dot-purple{background:#ab47bc}
.wf-zoom{display:flex;align-items:center;gap:6px}
.wf-zoom button{background:#222;border:1px solid #444;color:#ccc;padding:3px 9px;border-radius:4px;cursor:pointer;font-size:13px}
.wf-zoom button:hover{background:#333}
#wf-zoom-pct{font-size:11px;color:#888;min-width:36px;text-align:right}
#wf-ts{font-size:10px;color:#666}
#wf-body{display:flex;height:calc(100vh - 52px);overflow:hidden}
#wf-canvas{flex:1;overflow:hidden;cursor:grab;position:relative;background:#0e0e0e}
#wf-canvas.dragging{cursor:grabbing}
#wf-svg-wrap{position:absolute;top:0;left:0;transform-origin:0 0;will-change:transform}
#wf-svg-wrap svg{display:block}
#wf-panel{width:320px;flex-shrink:0;background:#111;border-left:1px solid #222;overflow-y:auto;padding:14px;display:none}
#wf-panel.open{display:block}
.wf-pnl-title{font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:10px;padding-right:24px;line-height:1.3}
.wf-pnl-close{position:absolute;top:14px;right:14px;background:none;border:none;color:#888;font-size:18px;cursor:pointer;line-height:1}
.wf-pnl-close:hover{color:#ccc}
.wf-pnl-row{display:flex;align-items:flex-start;gap:8px;font-size:12px;margin-bottom:7px;line-height:1.4}
.wf-pnl-key{color:#888;min-width:80px;flex-shrink:0}
.wf-pnl-val{color:#ccc;word-break:break-word}
.wf-status-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px;flex-shrink:0;margin-top:3px}
.wf-pnl-chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
.wf-chip{background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:1px 7px;font-size:11px;color:#aaa}
.wf-pnl-divider{border:none;border-top:1px solid #222;margin:10px 0}
.wf-pnl-btn{display:block;width:100%;margin-top:12px;padding:7px;background:#004d54;border:1px solid #00bcd4;color:#b2ebf2;border-radius:4px;cursor:pointer;font-size:12px;text-align:center}
.wf-pnl-btn:hover{background:#005f6b}
</style>
</head>
<body>
<div id="wf-header">
  <div>
    <h1>Workflow Monitor — Dependency Graph</h1>
    <p>Scheduled job dependency flow. Single-click a node for details; double-click to jump to its Settings panel.</p>
  </div>
  <div class="wf-hdr-right">
    <div class="wf-legend">
      <span><span class="wf-dot wf-dot-green"></span>Recent</span>
      <span><span class="wf-dot wf-dot-amber"></span>Stale</span>
      <span><span class="wf-dot wf-dot-red"></span>Failed</span>
      <span><span class="wf-dot wf-dot-grey"></span>Disabled</span>
      <span><span class="wf-dot wf-dot-cyan"></span>External</span>
      <span><span class="wf-dot wf-dot-purple"></span>Manual entry</span>
    </div>
    <div class="wf-zoom">
      <button onclick="wfZoomIn()">+</button>
      <button onclick="wfZoomOut()">−</button>
      <button onclick="wfFit()">Fit</button>
      <span id="wf-zoom-pct">100%</span>
    </div>
    <span id="wf-ts">Updated: ${ts}</span>
  </div>
</div>
<div id="wf-body">
  <div id="wf-canvas">
    <div id="wf-svg-wrap">${svg}</div>
  </div>
  <div id="wf-panel">
    <button class="wf-pnl-close" onclick="wfClosePanel()">&#215;</button>
    <div id="wf-pnl-content"></div>
  </div>
</div>
<script>
var _wfNodes = ${nodeJson};
<\/script>
<script src="${base}/static/js/workflow_tab.js"><\/script>
</body>
</html>`);
        win.document.close();
    } catch (e) {
        if (win.document.body) win.document.body.innerHTML = `<p style="color:#ef5350;padding:16px">Failed to render graph: ${e.message}</p>`;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const card = document.getElementById("workflow-monitor-card");
    if (!card) return;
    card.addEventListener("toggle", () => {
        if (card.open && !_workflowLoaded) loadWorkflowMonitor();
    });
});

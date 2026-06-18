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
    win.document.write('<!doctype html><title>Workflow Graph</title><body style="margin:0;background:#0e0e0e;color:#aaa;font-family:sans-serif;padding:16px;">Loading…</body>');
    try {
        const resp = await fetch("/api/workflow-monitor/status");
        const data = await resp.json();
        if (data.status !== "success") throw new Error(data.message || "request failed");
        const def = _wfBuildMermaid(data.nodes || [], data.edges || []);
        const { svg } = await mermaid.render(`wfGraphTab${++_workflowRenderSeq}`, def);
        win.document.open();
        win.document.write(`<!doctype html><html><head><title>Workflow Graph</title><meta charset="utf-8"><style>html,body{margin:0;background:#0e0e0e;}svg{display:block;margin:auto;}</style></head><body>${svg}</body></html>`);
        win.document.close();
    } catch (e) {
        win.document.body.innerHTML = `<p style="color:#ef5350">Failed to render graph: ${e.message}</p>`;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const card = document.getElementById("workflow-monitor-card");
    if (!card) return;
    card.addEventListener("toggle", () => {
        if (card.open && !_workflowLoaded) loadWorkflowMonitor();
    });
});

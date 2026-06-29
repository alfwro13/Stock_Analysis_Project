(function () {
    var nodeMap = {};
    (_wfNodes || []).forEach(function (n) { nodeMap[n.id] = n; });

    var canvas = document.getElementById("wf-canvas");
    var wrap = document.getElementById("wf-svg-wrap");
    var panel = document.getElementById("wf-panel");
    var pnlContent = document.getElementById("wf-pnl-content");

    var state = { scale: 1, tx: 0, ty: 0 };
    var drag = null;
    var clickTimer = null;

    function applyTransform() {
        wrap.style.transform = "translate(" + state.tx + "px," + state.ty + "px) scale(" + state.scale + ")";
        document.getElementById("wf-zoom-pct").textContent = Math.round(state.scale * 100) + "%";
    }

    function clampScale(s) { return Math.min(4, Math.max(0.1, s)); }

    window.wfZoomIn = function () { state.scale = clampScale(state.scale * 1.2); applyTransform(); };
    window.wfZoomOut = function () { state.scale = clampScale(state.scale / 1.2); applyTransform(); };
    window.wfFit = function () { state.scale = 1; state.tx = 0; state.ty = 0; applyTransform(); };

    canvas.addEventListener("wheel", function (e) {
        e.preventDefault();
        var factor = e.deltaY < 0 ? 1.1 : 0.9;
        var rect = canvas.getBoundingClientRect();
        var mx = e.clientX - rect.left;
        var my = e.clientY - rect.top;
        var prevScale = state.scale;
        state.scale = clampScale(prevScale * factor);
        state.tx = mx - (mx - state.tx) * (state.scale / prevScale);
        state.ty = my - (my - state.ty) * (state.scale / prevScale);
        applyTransform();
    }, { passive: false });

    canvas.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        drag = { sx: e.clientX - state.tx, sy: e.clientY - state.ty };
        canvas.classList.add("dragging");
    });
    window.addEventListener("mousemove", function (e) {
        if (!drag) return;
        state.tx = e.clientX - drag.sx;
        state.ty = e.clientY - drag.sy;
        applyTransform();
    });
    window.addEventListener("mouseup", function () {
        drag = null;
        canvas.classList.remove("dragging");
    });

    function resolveNodeId(el) {
        var g = el.closest ? el.closest("g.node") : null;
        if (!g) return null;
        var raw = g.id || "";
        var m = raw.match(/^flowchart-(.+)-\d+$/);
        if (!m) return null;
        var candidate = m[1];
        if (nodeMap[candidate]) return candidate;
        return null;
    }

    var statusColors = { green: "#43a047", amber: "#ffb300", red: "#ef5350", disabled: "#555", external: "#00bcd4", manual: "#ab47bc" };

    function formatDuration(sec) {
        if (!sec) return "—";
        if (sec < 60) return Math.round(sec) + "s";
        return Math.floor(sec / 60) + "m " + Math.round(sec % 60) + "s";
    }

    function chips(arr) {
        if (!arr || !arr.length) return '<span class="wf-pnl-val">—</span>';
        return '<div class="wf-pnl-chips">' + arr.map(function (a) { return '<span class="wf-chip">' + a + '</span>'; }).join("") + "</div>";
    }

    function showPanel(id) {
        var n = nodeMap[id];
        if (!n) return;
        var color = statusColors[n.status] || "#888";
        var rows = [
            ["Status", '<span class="wf-status-dot" style="background:' + color + '"></span>' + (n.status_reason || n.status)],
            ["Category", n.category],
            ["Engine", n.engine],
            ["Last run", n.last_run || "—"],
            ["Avg duration", formatDuration(n.avg_duration_sec)],
            ["Next run", n.next_run || "—"],
            ["Produces", chips(n.produces)],
            ["Consumes", chips(n.consumes)],
        ];
        var html = '<div class="wf-pnl-title">' + n.label + "</div>";
        rows.forEach(function (r) {
            html += '<div class="wf-pnl-row"><span class="wf-pnl-key">' + r[0] + '</span><span class="wf-pnl-val">' + r[1] + "</span></div>";
        });
        if (n.settings_anchor) {
            html += '<hr class="wf-pnl-divider"><button class="wf-pnl-btn" onclick="wfNavigateToSettings(\'' + n.settings_anchor + '\')">Open in Settings ↗</button>';
        }
        pnlContent.innerHTML = html;
        panel.classList.add("open");
    }

    window.wfClosePanel = function () { panel.classList.remove("open"); };

    window.wfNavigateToSettings = function (anchor) {
        if (!anchor) return;
        try {
            if (window.opener && window.opener.location.pathname === "/settings") {
                var el = window.opener.document.getElementById(anchor);
                if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth" }); return; }
            }
            if (window.opener) { window.opener.location.href = "/settings#" + anchor; return; }
        } catch (_) {}
        window.open("/settings#" + anchor, "_blank");
    };

    wrap.addEventListener("click", function (e) {
        var id = resolveNodeId(e.target);
        if (!id) return;
        if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; return; }
        clickTimer = setTimeout(function () { clickTimer = null; showPanel(id); }, 220);
    });

    wrap.addEventListener("dblclick", function (e) {
        if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
        var id = resolveNodeId(e.target);
        if (!id) return;
        var n = nodeMap[id];
        if (n && n.settings_anchor) wfNavigateToSettings(n.settings_anchor);
        else showPanel(id);
    });

    applyTransform();
}());

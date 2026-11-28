(function () {
    "use strict";

    const COLORS = {
        planned: "#3d8bfd",
        plannedProblem: "#ef5350",
        overlap: "#ff8a65",
        whisker: "#ff7043",
        margin: "#ffb300",
        idle: "rgba(255, 224, 130, 0.45)",
        overrun: "rgba(239, 83, 80, 0.55)",
        actual: "#43a047",
        failed: "#ef5350",
        band: "rgba(108, 117, 125, 0.75)",
        tight: "#ffb300",
        violation: "#ef5350",
        ok: "rgba(170, 170, 170, 0.6)",
        grid: "#2a2a2a",
        day: "#555",
        now: "#ef5350",
        past: "rgba(255, 255, 255, 0.035)",
        noHistory: "rgba(120, 120, 120, 0.12)",
    };
    const MOBILE_BP = 768;
    const ROW_PX = 30;

    const state = { start: null, view: "day", data: null, rows: [], selected: null, hideFrequent: false };
    const chartEl = document.getElementById("jstChart");
    const statusEl = document.getElementById("jstStatus");

    function toMs(stamp) {
        const [d, t] = stamp.split(" ");
        const [y, mo, da] = d.split("-").map(Number);
        const [h, mi, s] = (t || "00:00:00").split(":").map(Number);
        return Date.UTC(y, mo - 1, da, h, mi, s || 0);
    }

    function fromMs(ms) {
        return new Date(ms).toISOString().slice(0, 19).replace("T", " ");
    }

    function fmtDur(sec) {
        if (sec === null || sec === undefined) return "unknown";
        if (sec < 90) return `${Math.round(sec)} s`;
        const min = sec / 60;
        if (min < 90) return `${min.toFixed(1)} min`;
        return `${Math.floor(min / 60)} h ${Math.round(min % 60)} min`;
    }

    function hm(stamp) {
        return stamp ? stamp.slice(5, 16) : "—";
    }

    function chartHeight() {
        const rows = Math.max(state.rows.length, 1);
        const h = rows * ROW_PX + 140;
        return window.innerWidth < MOBILE_BP ? Math.max(400, h) : Math.max(320, h);
    }

    function setStatus(text) {
        statusEl.textContent = text || "";
    }

    function jobsById() {
        const map = {};
        (state.data.jobs || []).forEach((j) => { map[j.id] = j; });
        return map;
    }

    function problemOf(dep) {
        if (dep.backwards || dep.overlap === "violation" || dep.recent_overrun) return "violation";
        if (dep.overlap === "tight" || dep.overlap === "unknown") return "tight";
        return "ok";
    }

    function depMessage(dep, jobs) {
        const p = (jobs[dep.from] || {}).label || dep.from;
        const c = (jobs[dep.to] || {}).label || dep.to;
        const parts = [];
        if (dep.backwards) parts.push(`${c} runs before ${p}, its upstream producer, so it uses the previous cycle's output.`);
        if (dep.overlap === "violation") parts.push(`${c} depends on ${p}, but starts ${dep.gap_min} min after it, before ${p} is expected to complete.`);
        if (dep.overlap === "tight") parts.push(`${c} starts inside ${p}'s ${dep.margin_min}-min safety margin (gap ${dep.gap_min} min).`);
        if (dep.overlap === "unknown") parts.push(`${p}'s typical run time is not yet known; ${c} starts ${dep.gap_min} min after it.`);
        if (dep.recent_overrun) parts.push(`${p}'s longest recent run would still be running when ${c} starts.`);
        if (!parts.length) parts.push(`${c} starts ${dep.gap_min === null ? "?" : dep.gap_min} min after ${p}; order and buffer are fine.`);
        return parts.join(" ");
    }

    function buildRows() {
        const d = state.data;
        const rows = [];
        for (let lane = 0; lane < Math.max(d.lanes, 1); lane++) {
            rows.push({ key: `lane:${lane}`, text: lane === 0 ? "Scheduled jobs" : `Scheduled jobs (${lane + 1})` });
        }
        if (!state.hideFrequent) {
            d.jobs.filter((j) => j.row === "frequent").forEach((j) => {
                rows.push({ key: `job:${j.id}`, text: j.label, job: j.id });
            });
        }
        state.rows = rows;
        const index = {};
        rows.forEach((r, i) => { index[r.key] = i; });
        return index;
    }

    function laneForRun(run, blocksByJob) {
        const list = blocksByJob[run.job_id] || [];
        if (!list.length) return null;
        const t = toMs(run.start);
        let best = list[0], bestGap = Infinity;
        list.forEach((b) => {
            const gap = Math.abs(toMs(b.start) - t);
            if (gap < bestGap) { best = b; bestGap = gap; }
        });
        return best.lane;
    }

    function barTrace(name, items, opts) {
        return Object.assign({
            type: "bar",
            orientation: "h",
            name,
            y: items.map((i) => i.y),
            base: items.map((i) => i.start),
            x: items.map((i) => Math.max(toMs(i.end) - toMs(i.start), 1000)),
            hovertext: items.map((i) => i.hover),
            hovertemplate: "%{hovertext}<extra></extra>",
            customdata: items.map((i) => i.custom || null),
            showlegend: false,
        }, opts);
    }

    function buildFigure() {
        const d = state.data;
        const jobs = jobsById();
        const rowIndex = buildRows();
        const blocksByJob = {};
        d.blocks.forEach((b) => { (blocksByJob[b.job_id] = blocksByJob[b.job_id] || []).push(b); });

        const problemConsumers = {};
        d.dependencies.forEach((dep) => {
            if (problemOf(dep) !== "ok") {
                const key = `${dep.to}|${dep.consumer_start}`;
                (problemConsumers[key] = problemConsumers[key] || []).push(dep);
            }
        });

        const planned = d.blocks.map((b, idx) => {
            const job = jobs[b.job_id] || {};
            const deps = problemConsumers[`${b.job_id}|${b.start}`] || [];
            const lines = [
                `<b>${escapeHtml(job.label || b.job_id)}</b>`,
                `Scheduled start: ${escapeHtml(b.start)}`,
                `Expected end: ${escapeHtml(b.end)} (${escapeHtml(fmtDur(job.avg_duration_sec))})`,
            ];
            if (job.recent_max_sec) lines.push(`Recent longest run: ${escapeHtml(fmtDur(job.recent_max_sec))}`);
            if (b.overlap_with.length) lines.push(`Overlaps: ${escapeHtml(b.overlap_with.map((id) => (jobs[id] || {}).label || id).join(", "))}`);
            deps.forEach((dep) => lines.push(`&#9888; ${escapeHtml(depMessage(dep, jobs))}`));
            return {
                y: rowIndex[`lane:${b.lane}`], start: b.start, end: b.end, hover: lines.join("<br>"),
                custom: { kind: "block", idx }, problem: deps.length > 0, overlap: b.overlap_with.length > 0,
            };
        });

        const traces = [];
        traces.push(barTrace("Planned run", planned, {
            width: 0.55,
            offset: -0.4,
            marker: {
                color: planned.map((p) => (p.problem ? COLORS.plannedProblem : COLORS.planned)),
                line: { color: planned.map((p) => (p.overlap ? COLORS.overlap : "rgba(0,0,0,0)")), width: 2 },
            },
            textposition: "inside",
            insidetextanchor: "start",
            constraintext: "none",
            textfont: { color: "#fff" },
            text: planned.map(() => ""),
        }));

        const wx = [], wy = [], wt = [];
        d.blocks.forEach((b) => {
            if (!b.whisker_end) return;
            const job = jobs[b.job_id] || {};
            const y = rowIndex[`lane:${b.lane}`] - 0.125;
            const text = escapeHtml(`${job.label || b.job_id}: recent longest run ${fmtDur(job.recent_max_sec)}, would end ${b.whisker_end}`);
            wx.push(b.end, b.whisker_end, null);
            wy.push(y, y, null);
            wt.push(text, text, null);
        });
        traces.push({
            type: "scatter", mode: "lines", name: "Recent longest run", x: wx, y: wy, hovertext: wt,
            hovertemplate: "%{hovertext}<extra></extra>", line: { color: COLORS.whisker, width: 3 }, showlegend: false,
        });

        const buffers = {};
        d.dependencies.forEach((dep) => {
            if (!dep.producer_end || dep.overlap === "violation" || dep.backwards) return;
            const key = `${dep.from}|${dep.producer_start}`;
            if (!buffers[key] || toMs(dep.consumer_start) < toMs(buffers[key].consumer_start)) buffers[key] = dep;
        });
        const margins = [], idles = [], overruns = [];
        Object.values(buffers).forEach((dep) => {
            const block = (blocksByJob[dep.from] || []).find((b) => b.start === dep.producer_start);
            if (!block) return;
            const y = rowIndex[`lane:${block.lane}`];
            const pEnd = toMs(dep.producer_end), cStart = toMs(dep.consumer_start);
            const marginEnd = Math.min(pEnd + dep.margin_min * 60000, cStart);
            const label = escapeHtml(`${(jobs[dep.from] || {}).label} → ${(jobs[dep.to] || {}).label}`);
            if (cStart <= pEnd) {
                overruns.push({ y, start: dep.consumer_start, end: dep.producer_end, hover: `${label}<br>No buffer: next job starts before expected end` });
                return;
            }
            margins.push({ y, start: dep.producer_end, end: fromMs(marginEnd), hover: `${label}<br>Safety margin: ${escapeHtml(fmtDur((marginEnd - pEnd) / 1000))}` });
            if (cStart > marginEnd) {
                idles.push({ y, start: fromMs(marginEnd), end: dep.consumer_start, hover: `${label}<br>Idle buffer: ${escapeHtml(fmtDur((cStart - marginEnd) / 1000))} (total gap ${escapeHtml(fmtDur((cStart - pEnd) / 1000))})` });
            }
        });
        traces.push(barTrace("Safety margin", margins, { width: 0.2, offset: -0.225, marker: { color: COLORS.margin } }));
        traces.push(barTrace("Idle buffer", idles, { width: 0.2, offset: -0.225, marker: { color: COLORS.idle } }));
        traces.push(barTrace("No buffer", overruns, { width: 0.2, offset: -0.225, marker: { color: COLORS.overrun } }));

        if (!state.hideFrequent) {
            const bands = d.bands.filter((b) => rowIndex[`job:${b.job_id}`] !== undefined).map((b) => ({
                y: rowIndex[`job:${b.job_id}`], start: b.start, end: b.end,
                hover: `<b>${escapeHtml((jobs[b.job_id] || {}).label || b.job_id)}</b><br>${escapeHtml(String(b.fires))} runs, ${escapeHtml(hm(b.start))} – ${escapeHtml(hm(b.end))}<br>Typical run: ${escapeHtml(fmtDur((jobs[b.job_id] || {}).avg_duration_sec))}`,
                custom: { kind: "job", job: b.job_id },
            }));
            traces.push(barTrace("High-frequency band", bands, { width: 0.55, offset: -0.4, marker: { color: COLORS.band } }));
        }

        const runs = [];
        d.runs.forEach((r) => {
            const job = jobs[r.job_id] || {};
            let y;
            if (job.row === "scheduled") {
                const lane = laneForRun(r, blocksByJob);
                y = lane === null ? undefined : rowIndex[`lane:${lane}`];
            } else {
                y = rowIndex[`job:${r.job_id}`];
            }
            if (y === undefined) return;
            runs.push({
                y, start: r.start, end: r.end, status: r.status,
                hover: `<b>${escapeHtml(job.label || r.job_id)}</b> (actual, ${escapeHtml(r.trigger)})<br>${escapeHtml(r.start)} → ${escapeHtml(r.end)}<br>Duration: ${escapeHtml(fmtDur(r.duration_sec))} · ${escapeHtml(r.status)}`,
                custom: { kind: "job", job: r.job_id },
            });
        });
        traces.push(barTrace("Actual run", runs, {
            width: 0.22, offset: 0.17,
            marker: { color: runs.map((r) => (r.status === "error" ? COLORS.failed : COLORS.actual)) },
        }));

        const warnX = [], warnY = [], warnT = [];
        Object.entries(problemConsumers).forEach(([key, deps]) => {
            const [jobId, start] = key.split("|");
            const block = (blocksByJob[jobId] || []).find((b) => b.start === start);
            if (!block) return;
            warnX.push(start);
            warnY.push(rowIndex[`lane:${block.lane}`] - 0.125);
            warnT.push(deps.map((dep) => escapeHtml(depMessage(dep, jobs))).join("<br>"));
        });
        traces.push({
            type: "scatter", mode: "text", name: "Dependency warning", x: warnX, y: warnY, text: warnX.map(() => "⚠"),
            textposition: "middle left", textfont: { color: COLORS.violation, size: 14 }, hovertext: warnT,
            hovertemplate: "%{hovertext}<extra></extra>", showlegend: false,
        });

        return { traces, annotations: buildArrows(rowIndex, blocksByJob, jobs), shapes: buildShapes(rowIndex, jobs) };
    }

    function buildArrows(rowIndex, blocksByJob, jobs) {
        const notes = [];
        state.data.dependencies.forEach((dep) => {
            const cls = problemOf(dep);
            const selected = state.selected && (dep.from === state.selected || dep.to === state.selected);
            if (cls === "ok" && !selected) return;
            if (!dep.producer_start) return;
            const pBlock = (blocksByJob[dep.from] || []).find((b) => b.start === dep.producer_start);
            const cBlock = (blocksByJob[dep.to] || []).find((b) => b.start === dep.consumer_start);
            if (!pBlock || !cBlock) return;
            notes.push({
                x: dep.consumer_start, y: rowIndex[`lane:${cBlock.lane}`] - 0.125,
                ax: dep.producer_end || dep.producer_start, ay: rowIndex[`lane:${pBlock.lane}`] - 0.125,
                xref: "x", yref: "y", axref: "x", ayref: "y",
                showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: cls === "ok" ? 1 : 2,
                arrowcolor: COLORS[cls], text: "", hovertext: escapeHtml(depMessage(dep, jobs)),
                captureevents: true,
            });
        });
        return notes;
    }

    function buildShapes(rowIndex, jobs) {
        const d = state.data;
        const shapes = [];
        const startMs = toMs(d.window.start), endMs = toMs(d.window.end), nowMs = toMs(d.window.now);
        for (let t = startMs; t <= endMs; t += 86400000) {
            shapes.push({ type: "line", xref: "x", yref: "paper", x0: fromMs(t), x1: fromMs(t), y0: 0, y1: 1, line: { color: COLORS.day, width: 1 } });
        }
        if (nowMs > startMs) {
            shapes.push({ type: "rect", xref: "x", yref: "paper", x0: d.window.start, x1: fromMs(Math.min(nowMs, endMs)), y0: 0, y1: 1, fillcolor: COLORS.past, line: { width: 0 }, layer: "below" });
        }
        if (nowMs >= startMs && nowMs <= endMs) {
            shapes.push({ type: "line", xref: "x", yref: "paper", x0: d.window.now, x1: d.window.now, y0: 0, y1: 1, line: { color: COLORS.now, width: 2, dash: "dot" } });
        }
        d.jobs.forEach((j) => {
            const y = rowIndex[`job:${j.id}`];
            if (!j.history_from || y === undefined || toMs(j.history_from) <= startMs) return;
            shapes.push({ type: "rect", xref: "x", yref: "y", x0: d.window.start, x1: j.history_from, y0: y - 0.5, y1: y + 0.5, fillcolor: COLORS.noHistory, line: { width: 0 }, layer: "below" });
        });
        return shapes;
    }

    function viewRange() {
        const d = state.data;
        const startMs = toMs(d.window.start), endMs = toMs(d.window.end), nowMs = toMs(d.window.now);
        const anchor = nowMs >= startMs && nowMs <= endMs ? nowMs : startMs;
        if (state.view === "week") return [d.window.start, d.window.end];
        if (state.view === "6h") return [fromMs(anchor - 3 * 3600000), fromMs(anchor + 3 * 3600000)];
        const dayStart = startMs + Math.floor((anchor - startMs) / 86400000) * 86400000;
        return [fromMs(dayStart), fromMs(dayStart + 86400000)];
    }

    function updateLabels() {
        if (!chartEl._fullLayout || !state.data) return;
        const xa = chartEl._fullLayout.xaxis;
        const span = xa.r2l(xa.range[1]) - xa.r2l(xa.range[0]);
        const pxPerMs = xa._length / Math.max(span, 1);
        const jobs = jobsById();
        const text = state.data.blocks.map((b) => {
            const label = (jobs[b.job_id] || {}).label || b.job_id;
            const px = (toMs(b.end) - toMs(b.start)) * pxPerMs;
            if (px < 40) return "";
            const fit = Math.floor(px / 7);
            return escapeHtml(label.length <= fit ? label : `${label.slice(0, Math.max(fit - 1, 1))}…`);
        });
        Plotly.restyle(chartEl, { text: [text] }, [0]);
    }

    function applyHeight(height) {
        chartEl.style.height = `${height}px`;
        const c = chartEl.querySelector(".plot-container");
        if (c) c.style.height = `${height}px`;
    }

    let eventsBound = false;
    function bindEvents() {
        if (eventsBound) return;
        eventsBound = true;
        chartEl.on("plotly_relayout", updateLabels);
        chartEl.on("plotly_click", (ev) => {
            const pt = ev.points && ev.points[0];
            const custom = pt && pt.customdata;
            if (!custom) return;
            if (custom.kind === "block") {
                const block = state.data.blocks[custom.idx];
                showPanel(block.job_id, block);
            } else if (custom.kind === "job") {
                showPanel(custom.job, null);
            }
        });
        chartEl.on("plotly_clickannotation", (ev) => {
            const text = ev.annotation && ev.annotation.hovertext;
            if (text) setStatus(new DOMParser().parseFromString(text, "text/html").documentElement.textContent);
        });
    }

    function render() {
        const fig = buildFigure();
        const height = chartHeight();
        const layout = {
            title: { text: "Scheduled jobs and expected run windows", x: 0.5, xanchor: "center" },
            height,
            barmode: "overlay",
            dragmode: "pan",
            hovermode: "closest",
            paper_bgcolor: "rgba(0,0,0,0)",
            plot_bgcolor: "rgba(0,0,0,0)",
            font: { color: "#ccc" },
            margin: { l: 10, r: 20, t: 50, b: 60 },
            xaxis: {
                type: "date", range: viewRange(), showgrid: true, gridcolor: COLORS.grid, side: "top",
                tickformatstops: [
                    { dtickrange: [null, 86400000], value: "%H:%M\n%a %d %b" },
                    { dtickrange: [86400000, null], value: "%a %d %b" },
                ],
            },
            yaxis: {
                automargin: true, showgrid: false, zeroline: false, fixedrange: true,
                tickvals: state.rows.map((_, i) => i), ticktext: state.rows.map((r) => escapeHtml(r.text)),
                range: [state.rows.length - 0.5, -0.5],
            },
            shapes: fig.shapes,
            annotations: fig.annotations,
            showlegend: false,
            uirevision: `${state.start}|${state.view}`,
        };
        Plotly.react(chartEl, fig.traces, layout, { responsive: true, scrollZoom: true, displaylogo: false }).then(() => {
            applyHeight(height);
            bindEvents();
            updateLabels();
        });
    }

    function addRow(container, key, value) {
        const row = document.createElement("div");
        row.className = "jst-pnl-row";
        const k = document.createElement("span");
        k.className = "jst-pnl-key";
        k.textContent = key;
        const v = document.createElement("span");
        v.className = "jst-pnl-val";
        v.textContent = value;
        row.append(k, v);
        container.appendChild(row);
    }

    function showPanel(jobId, block) {
        const jobs = jobsById();
        const job = jobs[jobId];
        if (!job) return;
        state.selected = jobId;
        const panel = document.getElementById("jstPanel");
        const content = document.getElementById("jstPanelContent");
        content.replaceChildren();
        const title = document.createElement("div");
        title.className = "jst-pnl-title";
        title.textContent = job.label;
        content.appendChild(title);
        addRow(content, "Status", `${job.running ? "running" : job.status_reason}`);
        addRow(content, "Row", job.row === "scheduled" ? "Scheduled jobs" : `High-frequency (up to ${job.max_fires_per_day} runs a day)`);
        if (block) {
            addRow(content, "Scheduled start", block.start);
            addRow(content, "Expected end", block.end);
        }
        addRow(content, "Expected duration", fmtDur(job.avg_duration_sec));
        addRow(content, "Recent longest run", fmtDur(job.recent_max_sec));
        const runs = state.data.runs.filter((r) => r.job_id === jobId);
        if (runs.length) {
            const last = runs[0];
            addRow(content, "Latest run in view", `${last.start} → ${last.end} (${last.status})`);
        }
        if (job.history_from) addRow(content, "History from", job.history_from);
        addRow(content, "Upstream", job.upstream.length ? job.upstream.join(", ") : "none");
        addRow(content, "Downstream", job.downstream.length ? job.downstream.join(", ") : "none");
        const issues = state.data.dependencies.filter((dep) => (dep.to === jobId || dep.from === jobId) && problemOf(dep) !== "ok");
        const seen = new Set();
        issues.forEach((dep) => {
            const msg = depMessage(dep, jobs);
            if (seen.has(msg)) return;
            seen.add(msg);
            const p = document.createElement("p");
            p.className = `jst-pnl-issue jst-pnl-${problemOf(dep)}`;
            p.textContent = `⚠ ${msg}`;
            content.appendChild(p);
        });
        if (job.settings_anchor) {
            const a = document.createElement("a");
            a.className = "btn btn-sm btn-outline-info mt-2";
            a.href = `/settings#${encodeURIComponent(job.settings_anchor)}`;
            a.target = "_blank";
            a.rel = "noopener";
            a.textContent = "Open in Settings";
            content.appendChild(a);
        }
        panel.classList.remove("d-none");
        render();
    }

    function closePanel() {
        state.selected = null;
        document.getElementById("jstPanel").classList.add("d-none");
        render();
    }

    async function load() {
        setStatus("Loading…");
        const params = new URLSearchParams({ days: "7" });
        if (state.start) params.set("start", state.start);
        try {
            const resp = await fetch(`/api/workflow-monitor/timeline?${params}`);
            const data = await resp.json();
            if (data.status !== "success") throw new Error(data.message || "Request failed.");
            state.data = data;
            state.start = data.window.start.slice(0, 10);
            document.getElementById("jstTz").textContent = data.timezone;
            const problems = data.dependencies.filter((dep) => problemOf(dep) === "violation").length;
            setStatus(`Week of ${state.start}: ${data.jobs.length} jobs, ${problems} dependency problem(s) in view.`);
            render();
        } catch (e) {
            setStatus("Failed to load the Job Scheduling Timeline.");
        }
    }

    function shiftWeek(days) {
        const ms = toMs(`${state.start} 00:00:00`) + days * 86400000;
        state.start = fromMs(ms).slice(0, 10);
        load();
    }

    document.querySelectorAll("[data-jst-view]").forEach((btn) => {
        btn.addEventListener("click", () => {
            state.view = btn.dataset.jstView;
            if (state.data) Plotly.relayout(chartEl, { "xaxis.range": viewRange() });
        });
    });
    document.getElementById("jstPrev").addEventListener("click", () => shiftWeek(-7));
    document.getElementById("jstNext").addEventListener("click", () => shiftWeek(7));
    document.getElementById("jstNow").addEventListener("click", () => { state.start = null; load(); });
    document.getElementById("jstRefresh").addEventListener("click", load);
    document.getElementById("jstPanelClose").addEventListener("click", closePanel);
    document.getElementById("jstHideFrequent").addEventListener("change", (e) => {
        state.hideFrequent = e.target.checked;
        if (state.data) render();
    });
    window.addEventListener("resize", () => {
        if (!state.data) return;
        const height = chartHeight();
        Plotly.relayout(chartEl, { height }).then(() => applyHeight(height));
    });

    load();
})();

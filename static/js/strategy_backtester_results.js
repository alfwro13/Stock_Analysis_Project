var SB_STATUS_LABELS = {
    executed: "Executed", held: "Held", superseded: "Replaced before it executed",
    not_executed: "Not executed (data ended)", pending: "Pending",
};

function sbSummaryRow(label, s, extras, className) {
    var m = s.metrics;
    var dd = m ? m.drawdown_analytics.max_drawdown : null;
    var cells = [
        "<td>" + label + "</td>",
        '<td class="text-end">' + sbMoney(s.final_value) + "</td>",
        '<td class="text-end">' + sbPct(s.total_return) + "</td>",
        '<td class="text-end">' + sbPct(extras.gross) + "</td>",
        '<td class="text-end">' + sbPct(s.annualized_return) + "</td>",
        '<td class="text-end">' + sbPct(s.volatility) + "</td>",
        '<td class="text-end">' + sbNum(s.sharpe_ratio) + "</td>",
        '<td class="text-end">' + sbPct(dd) + "</td>",
        '<td class="text-end">' + sbNum(m ? m.risk_adjusted_ratios.sortino_ratio : null) + "</td>",
        '<td class="text-end">' + sbNum(m ? m.risk_adjusted_ratios.calmar_ratio : null) + "</td>",
        '<td class="text-end">' + (extras.costs === null ? "—" : sbMoney(extras.costs)) + "</td>",
        '<td class="text-end">' + (extras.trades === null ? "—" : extras.trades) + "</td>",
        '<td class="text-end">' + sbPct(extras.turnover, 1) + "</td>",
        '<td class="text-end">' + sbPct(extras.exposure, 1) + "</td>",
    ];
    return '<tr class="' + (className || "") + '">' + cells.join("") + "</tr>";
}

function sbRenderSummary(result) {
    var rows = result.strategies.map(function (item, i) {
        var label = '<span class="fw-semibold" style="color:' + SB_COLORS[i % SB_COLORS.length] + '">&#9632;</span> ' + escapeHtml(item.label);
        if (!item.available) {
            return "<tr><td>" + escapeHtml(item.label) + '</td><td colspan="13" class="text-muted small">Unavailable: ' + escapeHtml(item.reason || "") + "</td></tr>";
        }
        var s = item.summary;
        return sbSummaryRow(label, s, {
            gross: s.gross_total_return, costs: s.costs_total, trades: s.trade_count,
            turnover: s.turnover_per_year, exposure: s.average_exposure,
        });
    });
    if (result.benchmark) {
        rows.push(sbSummaryRow("Benchmark (" + escapeHtml(result.benchmark.label) + ")", result.benchmark.summary,
            { gross: null, costs: null, trades: null, turnover: null, exposure: null }, "table-secondary"));
    }
    sbEl("sb-summary-tbody").innerHTML = rows.join("");
}

function sbBaseLayout(title, yTitle, suffix) {
    return {
        title: { text: title, x: 0.5, xanchor: "center" },
        template: "plotly_dark", height: sbChartHeight(),
        margin: { l: 60, r: 20, t: 50, b: 80 },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "center", x: 0.5 },
        paper_bgcolor: "#1e1e1e", plot_bgcolor: "#1e1e1e", font: { color: "#ccc" },
        xaxis: { automargin: true, gridcolor: "#333" },
        yaxis: { title: yTitle, ticksuffix: suffix || "", automargin: true, gridcolor: "#333" },
    };
}

var SB_PLOT_CONFIG = { responsive: true, displaylogo: false };

function sbRenderCharts(result) {
    var equity = [];
    var drawdown = [];
    result.strategies.forEach(function (item, i) {
        if (!item.available) return;
        var color = SB_COLORS[i % SB_COLORS.length];
        equity.push({ x: result.dates, y: item.equity, mode: "lines", name: item.label, line: { color: color, width: 2 }, hovertemplate: "%{y:,.0f}<extra>" + item.label + "</extra>" });
        drawdown.push({ x: result.dates, y: item.drawdown.map(function (v) { return v * 100; }), mode: "lines", name: item.label, line: { color: color, width: 1.5 }, hovertemplate: "%{y:.1f}%<extra>" + item.label + "</extra>" });
    });
    if (result.benchmark) {
        var dash = { dash: "dash", color: "#ffffff", width: 1.5 };
        equity.push({ x: result.dates, y: result.benchmark.equity, mode: "lines", name: "Benchmark (" + result.benchmark.label + ")", line: dash });
        drawdown.push({ x: result.dates, y: result.benchmark.drawdown.map(function (v) { return v * 100; }), mode: "lines", name: "Benchmark (" + result.benchmark.label + ")", line: dash });
    }
    Plotly.react(sbEl("sb-equity-chart"), equity, sbBaseLayout("Portfolio Value After Costs", "Value"), SB_PLOT_CONFIG);
    Plotly.react(sbEl("sb-drawdown-chart"), drawdown, sbBaseLayout("Drawdown From Previous High", "Drawdown", "%"), SB_PLOT_CONFIG);
}

function sbRenderAllocation(runId, strategyId) {
    var el = sbEl("sb-alloc-chart");
    sbFetchJson("/api/strategy-backtester/runs/" + runId + "/allocations?strategy=" + encodeURIComponent(strategyId)).then(function (data) {
        if (data.status !== "success") { el.innerHTML = "<p class='text-muted'>" + escapeHtml(data.message || "") + "</p>"; return; }
        var traces = Object.keys(data.weights).map(function (ticker, i) {
            return {
                x: data.dates, y: data.weights[ticker].map(function (v) { return v === null ? null : v * 100; }), name: ticker,
                mode: "lines", stackgroup: "one", line: { width: 0.5, color: SB_COLORS[i % SB_COLORS.length] }, hovertemplate: "%{y:.1f}%<extra>" + ticker + "</extra>",
            };
        });
        traces.push({
            x: data.dates, y: data.cash.map(function (v) { return v === null ? null : v * 100; }), name: "Cash",
            mode: "lines", stackgroup: "one", line: { width: 0.5, color: "#777" }, hovertemplate: "%{y:.1f}%<extra>Cash</extra>",
        });
        var layout = sbBaseLayout("Share of the Portfolio in Each Holding", "Share", "%");
        layout.yaxis.range = [0, 100];
        Plotly.react(el, traces, layout, SB_PLOT_CONFIG);
    }).catch(function () {});
}

function sbMixText(target, cashWeight) {
    if (!target) return "—";
    var parts = Object.keys(target).filter(function (t) { return target[t] > 0.0005; })
        .map(function (t) { return escapeHtml(t) + " " + sbPct(target[t], 1); });
    if (cashWeight > 0.0005) parts.push("Cash " + sbPct(cashWeight, 1));
    return parts.join(" · ");
}

function sbDateCell(date, utc) {
    if (!date) return "—";
    return utc ? '<abbr title="' + escapeHtml(utc) + ' UTC">' + escapeHtml(date) + "</abbr>" : escapeHtml(date);
}

function sbRenderStrategyDetail() {
    var result = sbState.result;
    var item = result.strategies.find(function (s) { return s.id === sbEl("sb-detail-strategy").value; });
    if (!item || !item.available) return;
    sbEl("sb-detail-help").textContent = sbState.meta.strategies.find(function (s) { return s.id === item.id; }).description;
    sbEl("sb-decisions-tbody").innerHTML = item.decisions.map(function (d) {
        return "<tr><td>" + sbDateCell(d.decision_date, d.decided_at_utc) + "</td><td>" + sbDateCell(d.executed_date, d.executed_at_utc) + "</td>"
            + "<td>" + escapeHtml(SB_STATUS_LABELS[d.status] || d.status) + "</td><td>" + escapeHtml(d.reason || "") + "</td>"
            + '<td class="small">' + sbMixText(d.target, d.cash_weight) + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="text-muted">No decisions.</td></tr>';
    sbEl("sb-trades-note").textContent = item.transactions_total > item.transactions.length
        ? "Showing the first " + item.transactions.length + " of " + item.transactions_total + " trades."
        : item.transactions_total + " trades.";
    sbEl("sb-trades-tbody").innerHTML = item.transactions.map(function (t) {
        var cost = t.commission + t.spread + t.slippage;
        return "<tr><td>" + sbDateCell(t.date, t.executed_at_utc) + "</td><td>" + escapeHtml(t.ticker) + "</td>"
            + '<td class="' + (t.side === "buy" ? "text-success" : "text-danger") + '">' + t.side + "</td>"
            + '<td class="text-end">' + sbNum(t.units, 3) + '</td><td class="text-end">' + sbNum(t.price) + '</td>'
            + '<td class="text-end">' + sbMoney(t.notional) + '</td><td class="text-end">' + sbNum(cost) + "</td></tr>";
    }).join("") || '<tr><td colspan="7" class="text-muted">No trades.</td></tr>';
    sbRenderAllocation(sbState.runId, item.id);
}

function sbRenderData(run, result) {
    var inputs = run.inputs;
    var rows = Object.keys(inputs.sources).map(function (t) { return [t, inputs.sources[t]]; });
    if (inputs.benchmark_source) rows.push(["Benchmark " + inputs.benchmark, inputs.benchmark_source]);
    sbEl("sb-data-tbody").innerHTML = rows.map(function (r) {
        return "<tr><td>" + escapeHtml(r[0]) + "</td><td>" + (r[1].source === "extended" ? "Extended" : "Standard") + "</td><td>" + r[1].start + "</td><td>" + r[1].end + '</td><td class="text-end">' + r[1].sessions + "</td></tr>";
    }).join("");
    var window_ = result.shared_window;
    sbEl("sb-data-note").textContent = "All tickers share " + window_.start + " to " + window_.end + " (" + window_.sessions + " sessions); the test itself covers "
        + result.period.start + " to " + result.period.end + " (" + result.period.sessions + " sessions) because the rolling strategies need their lookback first. Input fingerprint " + inputs.digest.slice(0, 12) + ".";
}

function sbRenderRun(detail) {
    var run = detail.run, result = detail.result;
    sbState.runId = run.id;
    sbState.result = result;
    var config = run.config;
    sbShow("sb-placeholder", false);
    sbShow("sb-results", true);
    sbEl("sb-results-meta").textContent = "Fixed-current-basket test of " + run.basket.tickers.length + " tickers in " + run.basket.currency + " (" + run.basket.label + "). "
        + "Decisions use only completed closes and trade at the next eligible close. Costs: " + (config.commission_bps + config.spread_bps + config.slippage_bps) + " bps per side. "
        + "Cash earns " + sbPct(config.cash_rate, 1) + " per year; the benchmark pays the same costs on its one purchase. Run " + run.id + ", " + run.created_at + ".";
    var warnings = result.warnings.slice();
    sbShow("sb-warning", warnings.length > 0);
    sbEl("sb-warning").textContent = warnings.join(" ");
    var incomplete = sbEl("sb-incomplete");
    incomplete.classList.toggle("d-none", !result.incomplete);
    if (result.incomplete) {
        var shown = result.issues.slice(0, 5).map(function (i) { return i.ticker + " " + i.date; }).join(", ");
        incomplete.textContent = "Incomplete: " + result.issues_total + " expected price(s) were missing (" + shown + (result.issues_total > 5 ? ", …" : "")
            + "). The previous price was carried for valuation only and no trade used it, but every result below is affected. Fix the data (for example with Repair Data) and run again.";
    }
    sbRenderSummary(result);
    sbRenderCharts(result);
    var select = sbEl("sb-detail-strategy");
    select.innerHTML = result.strategies.filter(function (s) { return s.available; }).map(function (s) {
        return '<option value="' + s.id + '">' + escapeHtml(s.label) + "</option>";
    }).join("");
    sbRenderStrategyDetail();
    sbRenderData(run, result);
}

function sbLoadRuns() {
    sbFetchJson("/api/strategy-backtester/runs").then(function (data) {
        var rows = (data.runs || []).map(function (r) {
            var badge = r.state === "completed" ? "bg-success" : (r.state === "failed" ? "bg-danger" : "bg-secondary");
            var period = r.period ? r.period.start + " → " + r.period.end : "—";
            return "<tr><td>" + escapeHtml(r.created_at || "") + '<div class="text-muted small">' + escapeHtml(r.id) + "</div></td>"
                + "<td>" + escapeHtml(r.basket) + '<div class="text-muted small">' + r.tickers + " tickers · " + escapeHtml(String(r.currency)) + " · " + r.strategies + " strategies</div></td>"
                + "<td>" + period + (r.incomplete ? ' <span class="badge bg-danger">Incomplete</span>' : "") + "</td>"
                + '<td><span class="badge ' + badge + '">' + r.state + "</span>" + (r.error ? '<div class="text-muted small">' + escapeHtml(r.error) + "</div>" : "") + "</td>"
                + '<td class="text-end text-nowrap">'
                + (r.state === "completed" ? '<button type="button" class="btn btn-outline-primary btn-sm me-1" data-sb-open="' + r.id + '">Open</button>' : "")
                + (r.state === "queued" || r.state === "running" ? "" : '<button type="button" class="btn btn-outline-danger btn-sm" data-sb-delete="' + r.id + '">Delete</button>')
                + "</td></tr>";
        });
        sbEl("sb-runs-tbody").innerHTML = rows.join("") || '<tr><td colspan="5" class="text-muted">No saved runs yet.</td></tr>';
    }).catch(function () {});
}

function sbOpenRun(runId) {
    sbFetchJson("/api/strategy-backtester/runs/" + runId).then(function (data) {
        if (data.status === "success" && data.run.state === "completed") {
            sbShow("sb-incomplete", false);
            sbRenderRun(data);
            window.scrollTo({ top: 0, behavior: "smooth" });
        }
    }).catch(function () {});
}

function sbDeleteRun(runId) {
    if (!window.confirm("Delete this saved run and its files?")) return;
    sbFetchJson("/api/strategy-backtester/runs/" + runId, { method: "DELETE" }).then(function (data) {
        if (data.status !== "success") sbShowError(data.message || "Could not delete the run.");
        if (sbState.runId === runId) {
            sbShow("sb-results", false);
            sbShow("sb-placeholder", true);
            sbState.runId = null;
        }
        sbLoadRuns();
    }).catch(function (err) { sbShowError(err.message); });
}

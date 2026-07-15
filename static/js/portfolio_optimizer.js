var selectedAccountId = "all";

function _fmt_pct(v) {
    if (v === null || v === undefined) return "—";
    return (v * 100).toFixed(2) + "%";
}

function _showWarning(text) {
    var el = document.getElementById("po-warning");
    el.textContent = text;
    el.classList.remove("d-none");
}

function _hideWarning() {
    document.getElementById("po-warning").classList.add("d-none");
}

function _poChartHeight() {
    // Mobile can't go below 400 — static/css/styles.css forces a 400px min-height
    // on .js-plotly-plot under 768px; a smaller value here leaves the chart pinned
    // short inside a taller, CSS-floored container.
    return window.innerWidth < 768 ? 400 : 420;
}

var _PO_CHART_OPTS = { getHeight: _poChartHeight };

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, _PO_CHART_OPTS);
}

window.addEventListener("resize", function () {
    ChartFullscreen.relayoutForCurrentState("po-chart-outer", _PO_CHART_OPTS);
});

function renderWeightsTable(weights) {
    var tbody = document.getElementById("po-weights-tbody");
    tbody.innerHTML = weights.map(function (w) {
        var badges = "";
        if (w.is_new_addition) badges += ' <span class="badge bg-info">New</span>';
        if (w.is_short) badges += ' <span class="badge bg-danger">Negative</span>';
        return "<tr>"
            + "<td>" + w.symbol + badges + "<div class='text-muted small'>" + w.name + "</div></td>"
            + "<td>" + _fmt_pct(w.current_weight) + "</td>"
            + "<td>" + _fmt_pct(w.suggested_weight_mv) + "</td>"
            + "<td>" + _fmt_pct(w.suggested_weight_ms) + "</td>"
            + "</tr>";
    }).join("");
}

function renderFrontierChart(frontier) {
    var el = document.getElementById("po-chart");
    if (!frontier) {
        el.innerHTML = "<p class='text-muted'>Efficient frontier unavailable for this candidate set.</p>";
        return;
    }
    var traces = [
        {
            x: frontier.points.map(function (p) { return p.volatility * 100; }),
            y: frontier.points.map(function (p) { return p.return * 100; }),
            mode: "lines", name: "Efficient Frontier",
            line: { color: "#b366ff", width: 2 },
            hovertemplate: "Vol %{x:.2f}%, Return %{y:.2f}%<extra></extra>",
        },
        {
            x: [frontier.min_variance.volatility * 100], y: [frontier.min_variance.return * 100],
            mode: "markers", name: "Min-Variance",
            marker: { color: "#00ffcc", size: 11, symbol: "diamond" },
        },
        {
            x: [frontier.max_sharpe.volatility * 100], y: [frontier.max_sharpe.return * 100],
            mode: "markers", name: "Max-Sharpe",
            marker: { color: "#ffaa00", size: 11, symbol: "star" },
        },
    ];
    var layout = {
        title: { text: "Efficient Frontier", x: 0.5, xanchor: "center" },
        template: "plotly_dark", height: _poChartHeight(),
        margin: { l: 60, r: 20, t: 50, b: 60 },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "center", x: 0.5 },
        paper_bgcolor: "#1e1e1e", plot_bgcolor: "#1e1e1e", font: { color: "#ccc" },
        xaxis: { title: "Annualised Volatility %", ticksuffix: "%", automargin: true, gridcolor: "#333" },
        yaxis: { title: "Annualised Return %", ticksuffix: "%", automargin: true, gridcolor: "#333" },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
}

function _selectedCandidateTickers() {
    return Array.from(document.querySelectorAll("#po-candidates-list input[type=checkbox]:checked"))
        .map(function (cb) { return cb.value; });
}

function runOptimization() {
    var btn = document.getElementById("po-run-btn");
    var errEl = document.getElementById("po-error");
    errEl.classList.add("d-none");
    _hideWarning();

    var tickers = _selectedCandidateTickers();
    if (tickers.length < 2) {
        errEl.textContent = "Select at least 2 candidate tickers.";
        errEl.classList.remove("d-none");
        return;
    }

    btn.disabled = true;
    btn.textContent = "Running…";

    fetch("/api/portfolio-optimizer/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: selectedAccountId, include_tickers: tickers }),
    })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success") {
                errEl.textContent = data.message || "Optimization failed.";
                errEl.classList.remove("d-none");
                return;
            }
            if (data.data_warnings && data.data_warnings.length) {
                _showWarning(data.data_warnings.join(" "));
            }
            if (!data.weights) {
                document.getElementById("po-results").classList.add("d-none");
                document.getElementById("po-placeholder").classList.remove("d-none");
                return;
            }
            document.getElementById("po-placeholder").classList.add("d-none");
            document.getElementById("po-results").classList.remove("d-none");
            renderWeightsTable(data.weights);
            renderFrontierChart(data.efficient_frontier);
        })
        .catch(function (err) {
            errEl.textContent = "Request failed: " + err.message;
            errEl.classList.remove("d-none");
        })
        .finally(function () {
            btn.disabled = false;
            btn.textContent = "▶ Run Optimization";
        });
}

function _renderCandidatesList(candidates) {
    var container = document.getElementById("po-candidates-list");
    container.innerHTML = candidates.map(function (c) {
        var badge = c.held
            ? '<span class="badge bg-secondary">Held</span>'
            : '<span class="badge border border-secondary text-secondary">Watchlist</span>';
        return '<div class="form-check po-candidate-row">'
            + '<input class="form-check-input po-candidate-checkbox" type="checkbox" value="' + c.symbol + '"'
            + (c.held ? " checked" : "") + ' data-held="' + c.held + '">'
            + '<label class="form-check-label small">' + c.symbol + " " + badge + "</label>"
            + "</div>";
    }).join("");
}

function loadCandidates(accountId) {
    document.getElementById("po-candidates-list").innerHTML = "";
    fetch("/api/portfolio-optimizer/candidates?account_id=" + encodeURIComponent(accountId))
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success") {
                _showWarning(data.message || "Failed to load candidate tickers.");
                return;
            }
            _renderCandidatesList(data.candidates);
        })
        .catch(function () {});
}

function _setActiveTile(btn, accountId) {
    document.querySelectorAll(".mc-account-tile").forEach(function (t) {
        t.classList.remove("mc-account-tile--active");
    });
    btn.classList.add("mc-account-tile--active");
    selectedAccountId = accountId;
    document.getElementById("po-results").classList.add("d-none");
    document.getElementById("po-placeholder").classList.remove("d-none");
    loadCandidates(accountId);
}

function loadAccounts() {
    fetch("/api/portfolio-optimizer/accounts")
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success" || !data.accounts || !data.accounts.length) return;
            var bar = document.getElementById("po-accounts-bar");
            var container = document.getElementById("po-accounts-tiles");

            var tiles = data.accounts.map(function (acc) {
                return { id: acc.id, name: acc.name, value: acc.value };
            });
            tiles.push({ id: "all", name: "Global (All Accounts)", value: data.total, isTotal: true });

            tiles.forEach(function (tile) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className = "mc-account-tile";
                btn.innerHTML = '<div class="mc-account-tile-name">' + tile.name + "</div>";
                btn.addEventListener("click", function () { _setActiveTile(btn, tile.id); });
                if (tile.isTotal) {
                    btn.classList.add("mc-account-tile--active");
                }
                container.appendChild(btn);
            });

            bar.classList.remove("d-none");
            loadCandidates(selectedAccountId);
        })
        .catch(function () {});
}

function initPage() {
    loadAccounts();
    document.getElementById("po-run-btn").addEventListener("click", runOptimization);
    document.getElementById("po-select-all-watchlist").addEventListener("change", function (e) {
        document.querySelectorAll("#po-candidates-list .po-candidate-checkbox").forEach(function (cb) {
            if (cb.dataset.held !== "true") cb.checked = e.target.checked;
        });
    });
}

document.addEventListener("DOMContentLoaded", initPage);

function _fmt_gbp(value) {
    if (value === null || value === undefined) return "—";
    if (value >= 1e6) return "£" + (value / 1e6).toFixed(2) + "M";
    if (value >= 1e3) return "£" + (value / 1e3).toFixed(1) + "K";
    return "£" + Math.round(value).toLocaleString();
}

function renderChart(data) {
    const years = Array.from({ length: data.horizon_years + 1 }, (_, i) => i);
    const p = data.percentiles;
    const pr = data.percentiles_real;

    const nominalColour = "rgba(99,110,250,1)";
    const realColour = "rgba(239,85,59,1)";

    const traces = [
        {
            x: years, y: p.p5, name: "P5 (nominal)", line: { color: "transparent" },
            showlegend: false, hoverinfo: "skip",
        },
        {
            x: years, y: p.p25, name: "P5–P25 band", fill: "tonexty",
            fillcolor: "rgba(99,110,250,0.12)", line: { color: "transparent" },
            showlegend: false, hoverinfo: "skip",
        },
        {
            x: years, y: p.p75, name: "P25–P75 band", fill: "tonexty",
            fillcolor: "rgba(99,110,250,0.28)", line: { color: "transparent" },
            showlegend: false, hoverinfo: "skip",
        },
        {
            x: years, y: p.p95, name: "P75–P95 band", fill: "tonexty",
            fillcolor: "rgba(99,110,250,0.12)", line: { color: "transparent" },
            showlegend: false, hoverinfo: "skip",
        },
        {
            x: years, y: p.p50, name: "Median (nominal)",
            line: { color: nominalColour, width: 2.5 },
            mode: "lines", hovertemplate: "Year %{x}: £%{y:,.0f}<extra>Median</extra>",
        },
        {
            x: years, y: pr.p5, name: "P5 (real)", line: { color: "transparent" },
            showlegend: false, hoverinfo: "skip",
        },
        {
            x: years, y: pr.p95, name: "P95 (real)", fill: "tonexty",
            fillcolor: "rgba(239,85,59,0.08)", line: { color: "transparent" },
            showlegend: false, hoverinfo: "skip",
        },
        {
            x: years, y: pr.p50, name: "Median (real)",
            line: { color: realColour, width: 2, dash: "dot" },
            mode: "lines", hovertemplate: "Year %{x}: £%{y:,.0f}<extra>Median (real)</extra>",
        },
    ];

    const target = parseFloat(document.getElementById("mc-target").value) || 0;
    const shapes = [];
    const annotations = [];
    if (target > 0) {
        shapes.push({
            type: "line", x0: 0, x1: data.horizon_years, y0: target, y1: target,
            line: { color: "rgba(255,200,0,0.7)", dash: "dash", width: 1.5 },
        });
        annotations.push({
            x: data.horizon_years, y: target, xanchor: "right", yanchor: "bottom",
            text: "Target: " + _fmt_gbp(target), showarrow: false,
            font: { color: "rgba(255,200,0,0.9)", size: 11 },
        });
    }

    const layout = {
        template: "plotly_dark",
        height: 480,
        xaxis: { title: "Year", tickmode: "linear", dtick: 5 },
        yaxis: { title: "£", tickprefix: "£", tickformat: ",.0f" },
        title: { text: "Portfolio Wealth Projection — 1,000 Simulations", x: 0.5, xanchor: "center" },
        margin: { t: 60, b: 50, l: 80, r: 20 },
        legend: { orientation: "h", y: -0.15, x: 0.5, xanchor: "center" },
        hovermode: "x unified",
        shapes: shapes,
        annotations: annotations,
    };

    const el = document.getElementById("mc-chart");
    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
}

function runSimulation(e) {
    e.preventDefault();
    const btn = document.getElementById("mc-run-btn");
    const errEl = document.getElementById("mc-error");
    errEl.classList.add("d-none");

    const pv = parseFloat(document.getElementById("mc-pv").value);
    if (!pv || pv <= 0) {
        errEl.textContent = "Portfolio value must be greater than 0.";
        errEl.classList.remove("d-none");
        return;
    }

    const horizonEl = document.querySelector("input[name='mc-horizon']:checked");
    if (!horizonEl) return;

    const rawDrifts = {
        "Global Equity ETF": parseFloat(document.getElementById("mc-drift-global").value),
        "UK Equity": parseFloat(document.getElementById("mc-drift-uk").value),
        "Bond/Fixed Income": parseFloat(document.getElementById("mc-drift-bond").value),
    };
    const drift_overrides = {};
    for (const [k, v] of Object.entries(rawDrifts)) {
        if (!isNaN(v)) drift_overrides[k] = v;
    }

    const payload = {
        portfolio_value: pv,
        monthly_contribution: parseFloat(document.getElementById("mc-contrib").value) || 0,
        horizon_years: parseInt(horizonEl.value),
        target_wealth: parseFloat(document.getElementById("mc-target").value) || 0,
        drift_overrides: drift_overrides,
        inflation_pct: parseFloat(document.getElementById("mc-inflation").value) || 0,
    };

    btn.disabled = true;
    btn.textContent = "Running…";

    fetch("/api/monte-carlo/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success") {
                errEl.textContent = data.message || "Simulation failed.";
                errEl.classList.remove("d-none");
                return;
            }

            document.getElementById("mc-placeholder").classList.add("d-none");
            const chartEl = document.getElementById("mc-chart");
            chartEl.classList.remove("d-none");
            renderChart(data);

            document.getElementById("mc-median-final").textContent = _fmt_gbp(data.median_final);
            document.getElementById("mc-p5-final").textContent = _fmt_gbp(data.p5_final);
            const prob = data.probability_of_success;
            document.getElementById("mc-prob-success").textContent =
                prob !== null ? (prob * 100).toFixed(1) + "%" : "N/A (no target set)";
            document.getElementById("mc-summary").classList.remove("d-none");
        })
        .catch(function (err) {
            errEl.textContent = "Request failed: " + err.message;
            errEl.classList.remove("d-none");
        })
        .finally(function () {
            btn.disabled = false;
            btn.textContent = "▶ Run Simulation";
        });
}

function initPage() {
    const drifts = window.MC_DEFAULTS.drifts;
    document.getElementById("mc-drift-global").value = drifts["Global Equity ETF"];
    document.getElementById("mc-drift-uk").value = drifts["UK Equity"];
    document.getElementById("mc-drift-bond").value = drifts["Bond/Fixed Income"];
    document.getElementById("mc-inflation").value = window.MC_DEFAULTS.inflation;

    fetch("/api/xray")
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data && data.portfolio_total_value > 0) {
                document.getElementById("mc-pv").value = Math.round(data.portfolio_total_value);
            }
        })
        .catch(function () {});

    document.getElementById("mc-form").addEventListener("submit", runSimulation);
}

document.addEventListener("DOMContentLoaded", initPage);

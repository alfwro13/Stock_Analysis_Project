(function () {
    "use strict";

    const PERIOD_LABELS = { ytd: "YTD", "1y": "1 Year", "2y": "2 Years", lifetime: "Lifetime" };

    function pct(v) {
        if (v == null) return "—";
        const sign = v >= 0 ? "+" : "";
        return sign + v.toFixed(2) + "%";
    }

    function gbp(v) {
        if (v == null) return "—";
        return "£" + v.toLocaleString("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    }

    function valClass(v) {
        if (v == null) return "";
        return v >= 0 ? "positive-val" : "negative-val";
    }

    function weightedAvg(data, field) {
        const exposures = data.map(d => d.gbp_exposure || 0);
        const total = exposures.reduce((a, b) => a + b, 0);
        if (total === 0) {
            const vals = data.map(d => d[field]).filter(v => v != null);
            if (!vals.length) return null;
            return vals.reduce((a, b) => a + b, 0) / vals.length;
        }
        let weighted = 0;
        data.forEach((d, i) => {
            if (d[field] != null) weighted += d[field] * (exposures[i] / total);
        });
        return weighted;
    }

    function renderSummary(data) {
        const totalExposure = data.reduce((s, d) => s + (d.gbp_exposure || 0), 0);
        const avgEquity = weightedAvg(data, "equity_pct");
        const avgFx = weightedAvg(data, "fx_pct");
        const avgGbp = weightedAvg(data, "total_gbp_pct");

        document.getElementById("fxd-total-exposure").textContent = gbp(totalExposure || null);

        const eqEl = document.getElementById("fxd-avg-equity");
        eqEl.textContent = pct(avgEquity);
        eqEl.className = "xray-metric-value " + valClass(avgEquity);

        const fxEl = document.getElementById("fxd-fx-effect");
        fxEl.textContent = pct(avgFx);
        fxEl.className = "xray-metric-value " + valClass(avgFx);

        const gbpEl = document.getElementById("fxd-avg-gbp");
        gbpEl.textContent = pct(avgGbp);
        gbpEl.className = "xray-metric-value " + valClass(avgGbp);
    }

    function renderTable(data, period) {
        const fromHeader = document.querySelector("#fxd-table thead th:last-child");
        if (fromHeader) {
            fromHeader.textContent = period === "lifetime" ? "Earliest Buy" : "From";
            fromHeader.title = period === "lifetime"
                ? "Date of the earliest BUY trade used to derive the weighted-average purchase FX rate."
                : "Date of the first trading day in the selected period used as the reference price.";
        }
        const tbody = document.getElementById("fxd-tbody");
        tbody.innerHTML = "";
        data.forEach(function (d) {
            const dateVal = d.earliest_buy || d.ref_date || "—";
            const tr = document.createElement("tr");
            tr.innerHTML =
                "<td><a href=\"/stock/" + d.ticker + "\">" + d.ticker + "</a></td>" +
                "<td class=\"" + valClass(d.equity_pct) + "\">" + pct(d.equity_pct) + "</td>" +
                "<td class=\"" + valClass(d.fx_pct) + "\">" + pct(d.fx_pct) + "</td>" +
                "<td class=\"" + valClass(d.total_gbp_pct) + "\">" + pct(d.total_gbp_pct) + "</td>" +
                "<td>" + gbp(d.gbp_exposure) + "</td>" +
                "<td class=\"text-muted small\">" + dateVal + "</td>";
            tbody.appendChild(tr);
        });
    }

    function renderChart(data, period) {
        const el = document.getElementById("fxd-chart-wrapper");
        if (!data.length) { el.innerHTML = ""; return; }

        const tickers = data.map(d => d.ticker);
        const equityVals = data.map(d => d.equity_pct != null ? parseFloat(d.equity_pct.toFixed(2)) : 0);
        const fxVals = data.map(d => d.fx_pct != null ? parseFloat(d.fx_pct.toFixed(2)) : 0);

        const equityColors = equityVals.map(v => v >= 0 ? "rgba(0,200,83,0.8)" : "rgba(244,67,54,0.8)");
        const fxColors = fxVals.map(v => v >= 0 ? "rgba(100,181,246,0.8)" : "rgba(255,152,0,0.8)");

        const traceEquity = {
            x: tickers, y: equityVals, name: "Equity (USD)",
            type: "bar", marker: { color: equityColors },
        };
        const traceFx = {
            x: tickers, y: fxVals, name: "FX Effect",
            type: "bar", marker: { color: fxColors },
        };

        const layout = {
            barmode: "relative",
            paper_bgcolor: "#111", plot_bgcolor: "#111",
            font: { color: "#ccc", size: 12 },
            margin: { t: 10, b: 60, l: 50, r: 10 },
            xaxis: { tickfont: { size: 11 }, gridcolor: "#222" },
            yaxis: { ticksuffix: "%", gridcolor: "#2a2a2a", zerolinecolor: "#444" },
            legend: { orientation: "h", y: -0.25 },
            showlegend: true,
        };
        const config = { displayModeBar: false, responsive: true };
        Plotly.react(el, [traceEquity, traceFx], layout, config);
    }

    function render(data, period) {
        const empty = document.getElementById("fxd-empty");
        const table = document.getElementById("fxd-table");
        const chart = document.getElementById("fxd-chart-wrapper");
        const summary = document.getElementById("fxd-summary");
        if (!data || !data.length) {
            empty.style.display = "";
            table.style.display = "none";
            chart.style.display = "none";
            summary.style.display = "none";
            return;
        }
        empty.style.display = "none";
        table.style.display = "";
        chart.style.display = "";
        summary.style.display = "";
        renderSummary(data);
        renderTable(data, period);
        renderChart(data, period);
    }

    function setPeriodButtons(active) {
        document.querySelectorAll(".fxd-period-btn").forEach(function (btn) {
            const isActive = btn.dataset.period === active;
            btn.classList.toggle("btn-primary", isActive);
            btn.classList.toggle("btn-outline-secondary", !isActive);
        });
    }

    function fetchPeriod(period) {
        setPeriodButtons(period);
        fetch("/api/fx-drag?period=" + period)
            .then(function (r) { return r.json(); })
            .then(function (json) { render(json.data || [], period); })
            .catch(function (e) { console.error("FX drag fetch failed:", e); });
    }

    document.querySelectorAll(".fxd-period-btn").forEach(function (btn) {
        btn.addEventListener("click", function () { fetchPeriod(btn.dataset.period); });
    });

    render(window.FXD_INITIAL || [], window.FXD_PERIOD || "ytd");
})();

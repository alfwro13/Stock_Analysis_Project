var sbState = {
    accountId: "all", meta: null, candidates: [], currencies: [], shortlists: [],
    runId: null, result: null, pollTimer: null, historyTimer: null, prefill: null,
};

var SB_EXPLAIN_KEY = "sb-show-explanations";
var SB_PERIOD_WORD = { monthly: "month", quarterly: "quarter", annual: "year" };

var SB_COLORS = ["#00ffcc", "#ffaa00", "#4da6ff", "#b366ff", "#ff6b6b", "#7bd88f", "#ff9ff3"];
var SB_CHART_WRAPPERS = ["sb-equity-outer", "sb-drawdown-outer", "sb-alloc-outer"];

function sbPct(v, digits) {
    if (v === null || v === undefined) return "—";
    return (v * 100).toFixed(digits === undefined ? 2 : digits) + "%";
}

function sbNum(v, digits) {
    if (v === null || v === undefined) return "—";
    return Number(v).toFixed(digits === undefined ? 2 : digits);
}

function sbMoney(v) {
    if (v === null || v === undefined) return "—";
    return Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function sbEl(id) { return document.getElementById(id); }

function sbShow(id, visible) { sbEl(id).classList.toggle("d-none", !visible); }

function sbShowError(text) {
    var el = sbEl("sb-error");
    el.textContent = text || "";
    el.classList.toggle("d-none", !text);
}

function sbChartHeight() {
    return window.innerWidth < 768 ? 400 : 420;
}

var SB_CHART_OPTS = { getHeight: sbChartHeight };

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, SB_CHART_OPTS);
}

window.addEventListener("resize", function () {
    SB_CHART_WRAPPERS.forEach(function (id) { ChartFullscreen.relayoutForCurrentState(id, SB_CHART_OPTS); });
});

function sbBasketType() {
    return document.querySelector("input[name=sb-basket]:checked").value;
}

function sbHistoryMode() {
    return document.querySelector("input[name=sb-history]:checked").value;
}

function sbFetchJson(url, options) {
    return fetch(url, options).then(function (resp) {
        return resp.json().then(function (body) {
            if (resp.status === 422) throw new Error("Some of the settings are out of range.");
            return body;
        });
    });
}

function sbApplyDefaults(defaults) {
    sbEl("sb-cadence").value = defaults.cadence;
    sbEl("sb-capital").value = defaults.initial_capital;
    sbEl("sb-lookback").value = defaults.lookback;
    sbEl("sb-band").value = defaults.band_pp;
    sbEl("sb-max-weight").value = defaults.max_weight * 100;
    sbEl("sb-cash-reserve").value = defaults.cash_reserve * 100;
    sbEl("sb-cash-rate").value = defaults.cash_rate * 100;
    sbEl("sb-cost-preset").value = defaults.cost_preset;
    sbSyncCostInputs();
}

function sbSyncCostInputs() {
    var presetKey = sbEl("sb-cost-preset").value;
    var preset = sbState.meta.cost_presets[presetKey];
    ["commission", "spread", "slippage"].forEach(function (name) {
        var input = sbEl("sb-" + name);
        input.disabled = presetKey !== "custom";
        if (preset) input.value = preset[name + "_bps"];
    });
}

function sbRenderMeta(meta) {
    sbState.meta = meta;
    sbEl("sb-strategies").innerHTML = meta.strategies.map(function (s) {
        return '<div class="form-check">'
            + '<input class="form-check-input sb-strategy-checkbox" type="checkbox" value="' + escapeHtml(s.id) + '" id="sb-strat-' + escapeHtml(s.id) + '" checked>'
            + '<label class="form-check-label small" for="sb-strat-' + escapeHtml(s.id) + '">' + escapeHtml(s.label) + "</label>"
            + '<div class="sb-strategy-explanation d-none" data-sb-strategy="' + escapeHtml(s.id) + '"></div>'
            + "</div>";
    }).join("");
    sbEl("sb-cadence").innerHTML = meta.cadences.map(function (c) {
        return '<option value="' + escapeHtml(c) + '">' + escapeHtml(c.charAt(0).toUpperCase() + c.slice(1)) + "</option>";
    }).join("");
    var presets = Object.keys(meta.cost_presets).map(function (key) {
        return '<option value="' + key + '">' + escapeHtml(meta.cost_presets[key].label) + "</option>";
    });
    presets.push('<option value="custom">Custom</option>');
    sbEl("sb-cost-preset").innerHTML = presets.join("");
    sbApplyDefaults(meta.defaults);
    sbApplyPrefillRules();
    sbRenderExplanations();
    sbEl("sb-history-note").textContent = "Extended history is downloaded once for the chosen tickers into its own cache. A run uses it only while it is up to date, and always shows the dates it actually covered.";
}

function sbExplanationParams() {
    var n = sbSelectedTickers().length;
    var percentField = function (id) { return String(parseFloat(sbEl(id).value)); };
    return {
        n: n >= 1 ? String(n) : "N",
        each_pct: n >= 1 ? String(parseFloat((100 / n).toFixed(3))) : "100/N",
        period: SB_PERIOD_WORD[sbEl("sb-cadence").value] || "period",
        lookback: String(parseInt(sbEl("sb-lookback").value, 10)),
        band_pp: percentField("sb-band"),
        max_weight_pct: percentField("sb-max-weight"),
        cash_reserve_pct: percentField("sb-cash-reserve"),
    };
}

function sbRenderExplanations() {
    var visible = sbEl("sb-show-explanations").checked;
    var params = sbExplanationParams();
    sbState.meta.strategies.forEach(function (s) {
        var box = document.querySelector('.sb-strategy-explanation[data-sb-strategy="' + s.id + '"]');
        box.classList.toggle("d-none", !visible);
        if (!visible) return;
        box.innerHTML = strategyStepsHtml(s.steps, params);
    });
}

function sbApplyPrefillRules() {
    var prefill = sbState.prefill;
    if (!prefill) return;
    if (prefill.maxWeight !== null) sbEl("sb-max-weight").value = prefill.maxWeight * 100;
    if (prefill.cashReserve !== null) sbEl("sb-cash-reserve").value = prefill.cashReserve * 100;
    if (sbState.meta.strategies.some(function (s) { return s.id === prefill.strategy; })) {
        document.querySelectorAll(".sb-strategy-checkbox").forEach(function (cb) { cb.checked = cb.value === prefill.strategy; });
    }
}

function sbApplyPrefillTickers() {
    var prefill = sbState.prefill;
    if (!prefill || !prefill.tickers.length) return;
    var wanted = new Set(prefill.tickers);
    document.querySelectorAll("#sb-candidates-list input[type=checkbox]").forEach(function (cb) {
        if (!cb.disabled) cb.checked = wanted.has(cb.value);
    });
    prefill.tickers = [];
}

function sbSelectedTickers() {
    return Array.from(document.querySelectorAll("#sb-candidates-list input[type=checkbox]:checked"))
        .map(function (cb) { return cb.value; });
}

function sbReadPercent(id, min, max, label) {
    var value = parseFloat(sbEl(id).value);
    if (!isFinite(value) || value < min || value > max) throw new Error(label);
    return value / 100;
}

function sbReadNumber(id, min, max, label) {
    var value = parseFloat(sbEl(id).value);
    if (!isFinite(value) || value < min || value > max) throw new Error(label);
    return value;
}

function sbBuildPayload() {
    var strategies = Array.from(document.querySelectorAll(".sb-strategy-checkbox:checked")).map(function (cb) { return cb.value; });
    if (!strategies.length) throw new Error("Tick at least one strategy.");
    var tickers = sbSelectedTickers();
    if (tickers.length < sbState.meta.limits.min_tickers) throw new Error("Select at least " + sbState.meta.limits.min_tickers + " tickers.");
    if (tickers.length > sbState.meta.limits.max_tickers) throw new Error("Select at most " + sbState.meta.limits.max_tickers + " tickers.");
    var payload = {
        basket_type: sbBasketType(), account_id: sbState.accountId, include_tickers: tickers,
        currency: sbConverting() || ["null", "Unknown"].indexOf(sbSelectedCurrency()) >= 0 ? null : sbSelectedCurrency(),
        convert_currency: sbConverting(), strategies: strategies,
        cadence: sbEl("sb-cadence").value,
        lookback: Math.round(sbReadNumber("sb-lookback", 20, 756, "Training Lookback must be between 20 and 756 sessions.")),
        initial_capital: sbReadNumber("sb-capital", 1, 1e9, "Starting Capital must be at least 1."),
        band_pp: sbReadNumber("sb-band", 0.5, 50, "Tolerance Band must be between 0.5 and 50 pp."),
        max_weight: sbReadPercent("sb-max-weight", 0.5, 100, "Weight Cap must be between 0.5% and 100%."),
        cash_reserve: sbReadPercent("sb-cash-reserve", 0, 99, "Cash Reserve must be between 0% and 99%."),
        cash_rate: sbReadPercent("sb-cash-rate", -5, 25, "Cash Return must be between -5% and 25% per year."),
        cost_preset: sbEl("sb-cost-preset").value,
        commission_bps: sbReadNumber("sb-commission", 0, 500, "Commission must be between 0 and 500 bps."),
        spread_bps: sbReadNumber("sb-spread", 0, 500, "Spread must be between 0 and 500 bps."),
        slippage_bps: sbReadNumber("sb-slippage", 0, 500, "Slippage must be between 0 and 500 bps."),
        benchmark: sbEl("sb-benchmark").value.trim() || "auto", history: sbHistoryMode(),
    };
    if (payload.basket_type === "shortlist") {
        var basket = sbSelectedShortlist();
        if (!basket) throw new Error("No Stable Shortlist snapshot is available yet.");
        payload.shortlist_signal = basket.signal_type;
        payload.shortlist_scope = basket.scope;
        payload.include_tickers = [];
    }
    return payload;
}

function sbSetBusy(busy, text) {
    var btn = sbEl("sb-run-btn");
    btn.disabled = busy;
    btn.textContent = busy ? "Running…" : "▶ Run Backtest";
    sbShow("sb-progress", busy);
    sbEl("sb-progress").classList.toggle("d-flex", busy);
    if (busy) {
        sbEl("sb-progress-text").textContent = text || "Running…";
        sbShow("sb-placeholder", false);
        sbShow("sb-results", false);
    }
}

function sbRun() {
    sbShowError("");
    sbShow("sb-warning", false);
    sbShow("sb-incomplete", false);
    var payload;
    try { payload = sbBuildPayload(); } catch (e) { sbShowError(e.message); return; }
    sbSetBusy(true, "Queued…");
    sbFetchJson("/api/strategy-backtester/run", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }).then(function (data) {
        if (data.status !== "success") {
            sbSetBusy(false);
            sbShow("sb-placeholder", true);
            sbShowError(data.message || "The backtest could not be started.");
            return;
        }
        sbPollRun(data.run_id, 0);
    }).catch(function (err) {
        sbSetBusy(false);
        sbShow("sb-placeholder", true);
        sbShowError("Request failed: " + err.message);
    });
}

function sbPollRun(runId, attempts) {
    clearTimeout(sbState.pollTimer);
    sbFetchJson("/api/strategy-backtester/runs/" + runId).then(function (data) {
        if (data.status !== "success") throw new Error(data.message || "Run not found.");
        var state = data.run.state;
        if (state === "completed") {
            sbSetBusy(false);
            sbRenderRun(data);
            sbLoadRuns();
        } else if (state === "failed") {
            sbSetBusy(false);
            sbShow("sb-placeholder", true);
            sbShowError(data.run.error || "The backtest failed.");
            sbLoadRuns();
        } else if (attempts > 400) {
            throw new Error("The run is taking too long; check Saved Runs later.");
        } else {
            sbEl("sb-progress-text").textContent = state === "queued" ? "Queued…" : "Loading prices and simulating…";
            sbState.pollTimer = setTimeout(function () { sbPollRun(runId, attempts + 1); }, 1500);
        }
    }).catch(function (err) {
        sbSetBusy(false);
        sbShow("sb-placeholder", true);
        sbShowError(err.message);
    });
}

function sbOnBasketChange() {
    sbShow("sb-account-section", sbBasketType() === "account");
    sbShow("sb-shortlist-section", sbBasketType() === "shortlist");
    sbRenderBasket();
}

function sbInit() {
    sbState.prefill = readStrategyHandoff();
    if (sbState.prefill) sbState.prefill.convert = true;
    try { sbEl("sb-show-explanations").checked = localStorage.getItem(SB_EXPLAIN_KEY) === "1"; } catch (e) {}
    sbFetchJson("/api/strategy-backtester/meta").then(function (meta) {
        sbRenderMeta(meta);
        sbLoadAccounts();
        sbLoadShortlists();
        sbLoadRuns();
    }).catch(function (err) { sbShowError(err.message); });

    document.querySelectorAll("input[name=sb-basket]").forEach(function (el) { el.addEventListener("change", sbOnBasketChange); });
    document.querySelectorAll("input[name=sb-history]").forEach(function (el) { el.addEventListener("change", sbRefreshHistoryStatus); });
    sbEl("sb-currency").addEventListener("change", function () {
        if (sbBasketType() === "account") sbRenderAccountCandidates(); else sbRenderShortlistCandidates();
        sbRefreshHistoryStatus();
    });
    sbEl("sb-shortlist-select").addEventListener("change", sbRenderBasket);
    sbEl("sb-candidates-list").addEventListener("change", function () {
        sbRefreshHistoryStatus();
        sbRenderExplanations();
    });
    sbEl("sb-show-explanations").addEventListener("change", function () {
        try { localStorage.setItem(SB_EXPLAIN_KEY, sbEl("sb-show-explanations").checked ? "1" : "0"); } catch (e) {}
        sbRenderExplanations();
    });
    ["sb-cadence", "sb-lookback", "sb-band", "sb-max-weight", "sb-cash-reserve"].forEach(function (id) {
        sbEl(id).addEventListener("input", sbRenderExplanations);
    });
    sbEl("sb-benchmark").addEventListener("change", sbRefreshHistoryStatus);
    sbEl("sb-cost-preset").addEventListener("change", sbSyncCostInputs);
    sbEl("sb-detail-strategy").addEventListener("change", sbRenderStrategyDetail);
    sbEl("sb-prepare-btn").addEventListener("click", sbPrepareHistory);
    sbEl("sb-run-btn").addEventListener("click", sbRun);
    sbEl("sb-runs-tbody").addEventListener("click", function (event) {
        var open = event.target.closest("[data-sb-open]");
        var del = event.target.closest("[data-sb-delete]");
        if (open) sbOpenRun(open.dataset.sbOpen);
        if (del) sbDeleteRun(del.dataset.sbDelete);
    });
}

document.addEventListener("DOMContentLoaded", sbInit);

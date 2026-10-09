var sbState = {
    accountId: "all", meta: null, candidates: [], currencies: [], shortlists: [],
    runId: null, result: null, pollTimer: null, historyTimer: null,
};

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
            + '<label class="form-check-label small" for="sb-strat-' + escapeHtml(s.id) + '"><abbr title="' + escapeHtml(s.description) + '">' + escapeHtml(s.label) + "</abbr></label>"
            + "</div>";
    }).join("");
    sbEl("sb-cadence").innerHTML = meta.cadences.map(function (c) {
        return '<option value="' + c + '">' + c.charAt(0).toUpperCase() + c.slice(1) + "</option>";
    }).join("");
    var presets = Object.keys(meta.cost_presets).map(function (key) {
        return '<option value="' + key + '">' + escapeHtml(meta.cost_presets[key].label) + "</option>";
    });
    presets.push('<option value="custom">Custom</option>');
    sbEl("sb-cost-preset").innerHTML = presets.join("");
    sbApplyDefaults(meta.defaults);
    sbEl("sb-history-note").textContent = "Extended history is downloaded once for the chosen tickers into its own cache. A run uses it only while it is up to date, and always shows the dates it actually covered.";
}

function sbSelectedTickers() {
    return Array.from(document.querySelectorAll("#sb-candidates-list input[type=checkbox]:checked"))
        .map(function (cb) { return cb.value; });
}

var SB_CONVERT = "__convert__";

function sbSelectedCurrency() {
    return sbEl("sb-currency").value;
}

function sbConverting() {
    return sbSelectedCurrency() === SB_CONVERT;
}

function sbReportingCurrency() {
    return sbConverting() ? sbState.meta.base_currency : sbSelectedCurrency();
}

function sbHistoryLabel(history) {
    if (!history || !history.standard) return "no history";
    var label = history.standard.sessions + "d";
    if (history.extended) label += history.extended_usable ? " / " + history.extended.sessions + "d ext." : " / ext. stale";
    return label;
}

function sbCandidateRow(c, locked) {
    var id = "sb-cand-" + escapeHtml(c.symbol);
    return '<div class="form-check po-candidate-row">'
        + '<input class="form-check-input" type="checkbox" value="' + escapeHtml(c.symbol) + '" id="' + id + '"'
        + (c.held || locked ? " checked" : "") + (locked ? " disabled" : "") + ' data-held="' + c.held + '">'
        + '<label class="form-check-label small po-candidate-label" for="' + id + '">'
        + '<span class="po-candidate-symbol">' + escapeHtml(c.symbol) + "</span>"
        + '<span class="po-candidate-name">' + (c.name && c.name !== c.symbol ? escapeHtml(c.name) : "") + "</span>"
        + '<span class="po-candidate-days">' + sbHistoryLabel(c.history) + "</span>"
        + "</label></div>";
}

function sbRenderAccountCandidates() {
    var currency = sbSelectedCurrency();
    var inBucket = sbState.candidates.filter(function (c) { return sbConverting() || String(c.currency) === currency; });
    var held = inBucket.filter(function (c) { return c.held; });
    var watch = inBucket.filter(function (c) { return !c.held; });
    var html = "";
    if (held.length) html += '<div class="po-candidates-group-label">Your Holdings (' + held.length + ")</div>" + held.map(function (c) { return sbCandidateRow(c, false); }).join("");
    if (watch.length) html += '<div class="po-candidates-group-label">Watchlist — tick to include (' + watch.length + ")</div>" + watch.map(function (c) { return sbCandidateRow(c, false); }).join("");
    sbEl("sb-candidates-list").innerHTML = html;
    var others = sbState.candidates.length - inBucket.length;
    sbEl("sb-candidates-count").textContent = held.length + " held, " + watch.length + " on your Watchlist"
        + (sbConverting() ? ", all converted to " + sbReportingCurrency() : " in " + currency)
        + (others ? ". " + others + " in other currencies are hidden." : ".")
        + " The figures on the right are days of price history (standard / extended).";
}

function sbSelectedShortlist() {
    var value = sbEl("sb-shortlist-select").value;
    return sbState.shortlists.find(function (b) { return b.signal_type + "|" + b.scope === value; });
}

function sbRenderShortlistCandidates() {
    var basket = sbSelectedShortlist();
    if (!basket) {
        sbEl("sb-candidates-list").innerHTML = "";
        sbEl("sb-candidates-count").textContent = "";
        return;
    }
    var currency = sbSelectedCurrency();
    var members = basket.members.filter(function (m) { return sbConverting() || (m.currency || "Unknown") === currency; });
    sbEl("sb-candidates-list").innerHTML = members.map(function (m) {
        return sbCandidateRow({ symbol: m.symbol, name: m.name, held: true, history: null }, true);
    }).join("");
    sbEl("sb-candidates-count").textContent = sbConverting()
        ? members.length + " members, all converted to " + sbReportingCurrency() + "."
        : members.length + " of " + basket.members.length + " members are quoted in " + currency + ".";
    sbEl("sb-shortlist-note").textContent = "Members of the snapshot taken " + basket.decision_ts + " UTC. The list is fixed at that moment, so this tests today's members over the past.";
}

function sbRefreshCurrencyOptions(options) {
    var select = sbEl("sb-currency");
    options = options.concat([{ value: SB_CONVERT, label: "All currencies — converted to " + sbState.meta.base_currency }]);
    var previous = select.value;
    select.innerHTML = options.map(function (o) {
        return '<option value="' + escapeHtml(String(o.value)) + '">' + escapeHtml(o.label) + "</option>";
    }).join("");
    if (options.some(function (o) { return String(o.value) === previous; })) select.value = previous;
}

function sbRenderBasket() {
    if (sbBasketType() === "account") {
        sbRefreshCurrencyOptions(sbState.currencies.map(function (c) {
            return { value: c.currency, label: (c.currency || "Unknown") + " — " + c.held + " held, " + c.watchlist + " watchlist" };
        }));
        sbRenderAccountCandidates();
    } else {
        var basket = sbSelectedShortlist();
        var buckets = basket ? Object.keys(basket.by_currency) : [];
        sbRefreshCurrencyOptions(buckets.map(function (b) { return { value: b, label: b + " — " + basket.by_currency[b].length + " members" }; }));
        sbRenderShortlistCandidates();
    }
    sbSyncStrategyAvailability();
    sbRefreshHistoryStatus();
}

function sbSyncStrategyAvailability() {
    var box = document.getElementById("sb-strat-current_weights");
    if (!box) return;
    var isShortlist = sbBasketType() === "shortlist";
    box.disabled = isShortlist;
    if (isShortlist) box.checked = false;
}

function sbLoadCandidates(accountId) {
    sbEl("sb-candidates-list").innerHTML = "";
    sbFetchJson("/api/strategy-backtester/candidates?account_id=" + encodeURIComponent(accountId))
        .then(function (data) {
            if (data.status !== "success") {
                sbShowError(data.message || "Failed to load tickers.");
                return;
            }
            sbState.candidates = data.candidates;
            sbState.currencies = data.currencies;
            sbRenderBasket();
        })
        .catch(function (err) { sbShowError(err.message); });
}

function sbLoadShortlists() {
    sbFetchJson("/api/strategy-backtester/shortlists").then(function (data) {
        sbState.shortlists = data.baskets || [];
        sbEl("sb-shortlist-select").innerHTML = sbState.shortlists.length
            ? sbState.shortlists.map(function (b) {
                return '<option value="' + b.signal_type + "|" + b.scope + '">' + escapeHtml(b.label) + " — " + b.scope + "</option>";
            }).join("")
            : '<option value="">No shortlist has a snapshot yet</option>';
        if (sbBasketType() === "shortlist") sbRenderBasket();
    }).catch(function () {});
}

function sbSetActiveTile(btn, accountId) {
    document.querySelectorAll(".mc-account-tile").forEach(function (t) { t.classList.remove("mc-account-tile--active"); });
    btn.classList.add("mc-account-tile--active");
    sbState.accountId = accountId;
    sbLoadCandidates(accountId);
}

function sbLoadAccounts() {
    sbFetchJson("/api/strategy-backtester/accounts").then(function (data) {
        if (data.status !== "success" || !data.accounts || !data.accounts.length) return;
        var container = sbEl("sb-accounts-tiles");
        var tiles = data.accounts.map(function (acc) { return { id: acc.id, name: acc.name }; });
        tiles.push({ id: "all", name: "Global (All Accounts)", isTotal: true });
        tiles.forEach(function (tile) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "mc-account-tile";
            btn.innerHTML = '<div class="mc-account-tile-name">' + escapeHtml(tile.name) + "</div>";
            btn.addEventListener("click", function () { sbSetActiveTile(btn, tile.id); });
            if (tile.isTotal) btn.classList.add("mc-account-tile--active");
            container.appendChild(btn);
        });
        sbShow("sb-accounts-bar", true);
        sbLoadCandidates(sbState.accountId);
    }).catch(function () {});
}

function sbHistoryTickers() {
    var tickers = sbSelectedTickers();
    var benchmark = sbEl("sb-benchmark").value.trim();
    if (benchmark.toLowerCase() === "auto") benchmark = sbState.meta.default_benchmarks[sbReportingCurrency()] || "";
    if (benchmark && benchmark.toLowerCase() !== "none" && tickers.indexOf(benchmark) === -1) tickers.push(benchmark);
    return tickers;
}

function sbRenderHistoryStatus(statuses) {
    var tickers = Object.keys(statuses);
    var preparing = false;
    sbEl("sb-history-status").innerHTML = tickers.map(function (t) {
        var s = statuses[t];
        var text;
        if (s.state === "preparing") { text = "preparing…"; preparing = true; }
        else if (s.state === "failed" && !s.extended) text = "download failed";
        else if (!s.extended) text = "not prepared";
        else text = s.extended.start + " → " + s.extended.end + " (" + s.extended.sessions + "d)" + (s.extended_usable ? "" : " — out of date");
        return "<div>" + escapeHtml(t) + ": " + text + "</div>";
    }).join("");
    clearTimeout(sbState.historyTimer);
    if (preparing) sbState.historyTimer = setTimeout(sbRefreshHistoryStatus, 3000);
}

function sbRefreshHistoryStatus() {
    var show = sbHistoryMode() === "extended";
    sbShow("sb-history-box", show);
    if (!show) return;
    var tickers = sbHistoryTickers();
    if (!tickers.length) { sbEl("sb-history-status").textContent = ""; return; }
    sbFetchJson("/api/strategy-backtester/history-status?tickers=" + encodeURIComponent(tickers.join(",")))
        .then(function (data) { if (data.status === "success") sbRenderHistoryStatus(data.tickers); })
        .catch(function () {});
}

function sbPrepareHistory() {
    var tickers = sbHistoryTickers();
    sbShowError("");
    if (!tickers.length) { sbShowError("Select tickers first."); return; }
    sbFetchJson("/api/strategy-backtester/prepare-history", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tickers: tickers, convert_currency: sbConverting() }),
    }).then(function (data) {
        if (data.status !== "success") { sbShowError(data.message || "Could not start the download."); return; }
        sbRefreshHistoryStatus();
        setTimeout(sbRefreshHistoryStatus, 1000);
    }).catch(function (err) { sbShowError(err.message); });
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
    sbEl("sb-candidates-list").addEventListener("change", sbRefreshHistoryStatus);
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

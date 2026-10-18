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
        + '<span class="po-candidate-days">' + escapeHtml(sbHistoryLabel(c.history)) + "</span>"
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
    sbApplyPrefillTickers();
    sbRenderExplanations();
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
        sbRenderExplanations();
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
    sbRenderExplanations();
}

function sbRefreshCurrencyOptions(options) {
    var select = sbEl("sb-currency");
    options = options.concat([{ value: SB_CONVERT, label: "All currencies — converted to " + sbState.meta.base_currency }]);
    var previous = select.value;
    if (sbState.prefill && sbState.prefill.convert) {
        previous = SB_CONVERT;
        sbState.prefill.convert = false;
    }
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
    sbEl("sb-candidates-list").innerHTML = '<p class="text-muted small mb-0">Loading tickers…</p>';
    sbFetchJson("/api/strategy-backtester/candidates?account_id=" + encodeURIComponent(accountId))
        .then(function (data) {
            if (data.status !== "success") {
                sbEl("sb-candidates-list").innerHTML = '<p class="text-muted small mb-0">Could not load tickers.</p>';
                sbShowError(data.message || "Failed to load tickers.");
                return;
            }
            sbState.candidates = data.candidates;
            sbState.currencies = data.currencies;
            sbRenderBasket();
        })
        .catch(function (err) {
            sbEl("sb-candidates-list").innerHTML = '<p class="text-muted small mb-0">Could not load tickers.</p>';
            sbShowError(err.message);
        });
}

function sbLoadShortlists() {
    sbFetchJson("/api/strategy-backtester/shortlists").then(function (data) {
        sbState.shortlists = data.baskets || [];
        sbEl("sb-shortlist-select").innerHTML = sbState.shortlists.length
            ? sbState.shortlists.map(function (b) {
                return '<option value="' + escapeHtml(b.signal_type + "|" + b.scope) + '">' + escapeHtml(b.label + " — " + b.scope) + "</option>";
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
        if (data.status !== "success" || !data.accounts || !data.accounts.length) {
            sbEl("sb-candidates-list").innerHTML = '<p class="text-muted small mb-0">No accounts with holdings found.</p>';
            return;
        }
        var container = sbEl("sb-accounts-tiles");
        var tiles = data.accounts.map(function (acc) { return { id: acc.id, name: acc.name }; });
        tiles.push({ id: "all", name: "Global (All Accounts)", isTotal: true });
        var wantedId = sbState.prefill && tiles.some(function (t) { return t.id === sbState.prefill.account; }) ? sbState.prefill.account : "all";
        tiles.forEach(function (tile) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "mc-account-tile";
            btn.innerHTML = '<div class="mc-account-tile-name">' + escapeHtml(tile.name) + "</div>";
            btn.addEventListener("click", function () { sbSetActiveTile(btn, tile.id); });
            if (tile.id === wantedId) btn.classList.add("mc-account-tile--active");
            container.appendChild(btn);
        });
        sbState.accountId = wantedId;
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
        else text = escapeHtml(s.extended.start + " → " + s.extended.end + " (" + s.extended.sessions + "d)") + (s.extended_usable ? "" : " — out of date");
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

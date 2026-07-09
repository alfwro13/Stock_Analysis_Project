var selectedAccountId = "all";

var METRIC_GROUPS = [
    {
        containerId: "pt-cards-risk_adjusted_ratios",
        metrics: [
            { key: "sortino_ratio", label: "Sortino Ratio", fmt: "num",
              tip: "Like the Sharpe ratio, but only penalises downside volatility — upside swings don't count against it." },
            { key: "calmar_ratio", label: "Calmar Ratio", fmt: "num",
              tip: "Annualised return divided by the maximum drawdown — return earned per unit of the worst peak-to-trough loss." },
            { key: "omega_ratio", label: "Omega Ratio", fmt: "num",
              tip: "Ratio of total gains to total losses relative to the risk-free rate. Above 1 means gains outweighed losses." },
            { key: "profit_factor", label: "Profit Factor", fmt: "num",
              tip: "Sum of all positive daily returns divided by the absolute sum of all negative daily returns." },
        ],
    },
    {
        containerId: "pt-cards-drawdown_analytics",
        metrics: [
            { key: "max_drawdown", label: "Max Drawdown", fmt: "pct",
              tip: "The largest peak-to-trough decline in portfolio value over the cached history, computed from daily returns." },
            { key: "longest_drawdown_days", label: "Longest Drawdown", fmt: "days",
              tip: "The longest continuous stretch the portfolio spent below a previous high." },
            { key: "time_underwater_days", label: "Time Underwater", fmt: "days",
              tip: "Days since the portfolio last reached a new high." },
            { key: "ulcer_index", label: "Ulcer Index", fmt: "num",
              tip: "Measures the depth and duration of drawdowns combined — higher values mean deeper, longer-lasting declines." },
        ],
    },
    {
        containerId: "pt-cards-distribution_tail_stats",
        metrics: [
            { key: "best_day", label: "Best Day", fmt: "pct", tip: "The single best daily return in the cached history." },
            { key: "worst_day", label: "Worst Day", fmt: "pct", tip: "The single worst daily return in the cached history." },
            { key: "best_month", label: "Best Month", fmt: "pct", tip: "The best calendar month's compounded return in the cached history." },
            { key: "worst_month", label: "Worst Month", fmt: "pct", tip: "The worst calendar month's compounded return in the cached history." },
            { key: "tail_ratio", label: "Tail Ratio", fmt: "num",
              tip: "Ratio of the 95th-percentile daily gain to the 5th-percentile daily loss — above 1 means the best days outsized the worst." },
        ],
    },
    {
        containerId: "pt-cards-win_loss_stats",
        metrics: [
            { key: "win_rate", label: "Win Rate", fmt: "pct", tip: "Percentage of cached trading days with a positive return." },
            { key: "avg_win", label: "Average Win", fmt: "pct", tip: "Mean daily return on winning days." },
            { key: "avg_loss", label: "Average Loss", fmt: "pct", tip: "Mean daily return on losing days." },
            { key: "payoff_ratio", label: "Win/Loss Ratio", fmt: "num",
              tip: "Average win divided by the absolute average loss — the payoff ratio." },
            { key: "max_consecutive_wins", label: "Max Consecutive Wins", fmt: "days",
              tip: "The longest streak of consecutive positive-return days." },
            { key: "max_consecutive_losses", label: "Max Consecutive Losses", fmt: "days",
              tip: "The longest streak of consecutive negative-return days." },
        ],
    },
];

function _fmt_gbp(value) {
    if (value === null || value === undefined) return "—";
    if (value >= 1e6) return "£" + (value / 1e6).toFixed(2) + "M";
    if (value >= 1e3) return "£" + (value / 1e3).toFixed(1) + "K";
    return "£" + Math.round(value).toLocaleString();
}

function _num(v, dec) {
    if (v === null || v === undefined) return "—";
    return v.toFixed(dec !== undefined ? dec : 2);
}

function _pct(v, dec) {
    if (v === null || v === undefined) return "—";
    return (v * 100).toFixed(dec !== undefined ? dec : 2) + "%";
}

function _days(v) {
    if (v === null || v === undefined) return "—";
    return v + (v === 1 ? " day" : " days");
}

function _fmtMetric(v, fmt) {
    if (fmt === "pct") return _pct(v);
    if (fmt === "days") return _days(v);
    return _num(v);
}

function _card(label, value, tipText) {
    var labelHtml = tipText
        ? '<abbr title="' + tipText.replace(/"/g, "&quot;") + '">' + label + "</abbr>"
        : label;
    return '<div class="xray-metric-card">'
        + '<div class="xray-metric-label">' + labelHtml + "</div>"
        + '<div class="xray-metric-value">' + value + "</div>"
        + "</div>";
}

function renderPlaceholderCards() {
    METRIC_GROUPS.forEach(function (group) {
        var container = document.getElementById(group.containerId);
        var html = "";
        group.metrics.forEach(function (m) {
            html += _card(m.label, "—", m.tip);
        });
        container.innerHTML = html;
    });
}

function _setActiveTile(btn, accountId) {
    document.querySelectorAll(".mc-account-tile").forEach(function (t) {
        t.classList.remove("mc-account-tile--active");
    });
    btn.classList.add("mc-account-tile--active");
    selectedAccountId = accountId;
}

function loadAccounts() {
    fetch("/api/performance-analytics/accounts")
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success" || !data.accounts || !data.accounts.length) return;
            var bar = document.getElementById("pt-accounts-bar");
            var container = document.getElementById("pt-accounts-tiles");

            var tiles = data.accounts.map(function (acc) {
                return { id: acc.id, name: acc.name, value: acc.value };
            });
            tiles.push({ id: "all", name: "Global (All Accounts)", value: data.total, isTotal: true });

            tiles.forEach(function (tile) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className = "mc-account-tile";
                btn.innerHTML =
                    '<div class="mc-account-tile-name">' + tile.name + "</div>"
                    + '<div class="mc-account-tile-value">' + _fmt_gbp(tile.value) + "</div>";
                btn.addEventListener("click", function () { _setActiveTile(btn, tile.id); });
                if (tile.isTotal) {
                    btn.classList.add("mc-account-tile--active");
                }
                container.appendChild(btn);
            });

            bar.classList.remove("d-none");
        })
        .catch(function () {});
}

function initPage() {
    renderPlaceholderCards();
    loadAccounts();
}

document.addEventListener("DOMContentLoaded", initPage);

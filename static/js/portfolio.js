// ── Position sizing cell renderer ─────────────────────────────────────────────
function renderPositionSizing() {
    document.querySelectorAll('.ps-cell').forEach(function (cell) {
        var row = cell.closest('tr');
        var entryPrice = parseFloat(cell.dataset.entryPrice);
        var atrPctRaw  = cell.dataset.atrPct;
        var atrPct     = atrPctRaw === '' ? null : parseFloat(atrPctRaw);
        var currency   = cell.dataset.currency || 'USD';

        var result = window.PositionSizing.calculateForRow(entryPrice, atrPct, currency);
        var sharesCell = row ? row.querySelector('[data-col-key="shares"]') : null;
        var stopCell   = row ? row.querySelector('[data-col-key="stop_price"]') : null;
        var riskCell   = row ? row.querySelector('[data-col-key="risk_amount"]') : null;

        if (result.positionValue != null && result.shares != null && result.shares > 0) {
            cell.textContent = window.PositionSizing.formatCurrency(result.positionValue, window.BASE_CURRENCY);
            cell.setAttribute('data-sort', result.positionValue);

            if (sharesCell) {
                sharesCell.textContent = result.shares.toLocaleString();
                sharesCell.setAttribute('data-sort', result.shares);
            }
            if (stopCell) {
                stopCell.textContent = window.PositionSizing.formatCurrency(result.stopPrice, currency);
                stopCell.setAttribute('data-sort', result.stopPrice);
            }
            if (riskCell) {
                riskCell.textContent = window.PositionSizing.formatCurrency(result.riskAmount, window.BASE_CURRENCY);
                riskCell.setAttribute('data-sort', result.riskAmount);
            }
        } else {
            cell.textContent = '—';
            if (sharesCell) sharesCell.textContent = '—';
            if (stopCell) stopCell.textContent = '—';
            if (riskCell) riskCell.textContent = '—';
        }
    });
}

document.addEventListener('DOMContentLoaded', renderPositionSizing);

// ── X-ray mode state ──────────────────────────────────────────────────────────
window._xrayMode = false;
window._xraySummaryOriginal = null;

function _captureSummaryOriginal() {
    var costEl = document.getElementById('summary-cost-val');
    var mvEl   = document.getElementById('summary-mv-val');
    var pnlEl  = document.getElementById('summary-pnl-val');
    if (!costEl) return;
    window._xraySummaryOriginal = {
        cost:        costEl.textContent,
        mv:          mvEl  ? mvEl.textContent  : '',
        pnl:         pnlEl ? pnlEl.textContent : '',
        pnlPositive: pnlEl ? pnlEl.classList.contains('pnl-positive') : true
    };
}

function _restoreSummaryOriginal() {
    var orig = window._xraySummaryOriginal;
    if (!orig) return;
    var costEl = document.getElementById('summary-cost-val');
    var mvEl   = document.getElementById('summary-mv-val');
    var pnlEl  = document.getElementById('summary-pnl-val');
    if (costEl) costEl.textContent = orig.cost;
    if (mvEl)   mvEl.textContent   = orig.mv;
    if (pnlEl) {
        pnlEl.textContent = orig.pnl;
        pnlEl.className   = 'summary-value-large ' + (orig.pnlPositive ? 'pnl-positive' : 'pnl-negative');
    }
}

function _updateSummaryRow(data) {
    var costEl = document.getElementById('summary-cost-val');
    var mvEl   = document.getElementById('summary-mv-val');
    var pnlEl  = document.getElementById('summary-pnl-val');
    if (!costEl) return;
    var cur    = data.base_currency || 'GBP';
    var sym    = ({ GBP: '£', USD: '$', EUR: '€' })[cur] || (cur + ' ');
    var fmtAmt = function (v) {
        return sym + (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ' + cur;
    };
    var mv   = data.portfolio_total_value     || 0;
    var cost = data.portfolio_total_investment || 0;
    var pnl  = mv - cost;
    var pct  = cost > 0 ? (pnl / cost * 100) : 0;
    costEl.textContent = fmtAmt(cost);
    if (mvEl)  mvEl.textContent  = fmtAmt(mv);
    if (pnlEl) {
        pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmtAmt(pnl) + ' (' + pct.toFixed(2) + '%)';
        pnlEl.className   = 'summary-value-large ' + (pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
    }
}

function toggleXray() {
    if (!window._xrayMode) { _enterXrayMode(); } else { _exitXrayMode(); }
}

function _getTableContainer() {
    return document.getElementById('dataTable_wrapper') || document.getElementById('dataTable');
}

function _enterXrayMode() {
    if (typeof _exitHeatmapMode === 'function' && window._heatmapMode) _exitHeatmapMode();
    _captureSummaryOriginal();
    window._xrayMode = true;
    var ctrl = document.querySelector('.controls-container');
    var tbl  = _getTableContainer();
    if (ctrl) ctrl.style.display = 'none';
    if (tbl)  tbl.style.display  = 'none';
    document.getElementById('xray-panel').style.display   = 'block';
    document.getElementById('xray-loading').style.display = 'flex';
    document.getElementById('xray-content').style.display = 'none';
    var lnk = document.getElementById('xray-link');
    if (lnk) lnk.innerHTML = '&larr; Back to Portfolio';
    _loadXray(document.getElementById('accountContextSelector').value);
}

function _exitXrayMode() {
    window._xrayMode = false;
    var ctrl = document.querySelector('.controls-container');
    var tbl  = _getTableContainer();
    if (ctrl) ctrl.style.display = '';
    if (tbl)  tbl.style.display  = '';
    document.getElementById('xray-panel').style.display = 'none';
    var lnk = document.getElementById('xray-link');
    if (lnk) lnk.innerHTML = '&#128302; X-ray';
    _restoreSummaryOriginal();
}

function switchAccountContext() {
    var selectedId = document.getElementById('accountContextSelector').value;
    if (window._xrayMode) { _loadXray(selectedId); }
    else { window.location.href = '/portfolio?account_id=' + encodeURIComponent(selectedId); }
}

// ── Show After Market Data toggle (display-only — never affects P&L/totals) ───────────────────
function toggleExtendedHours(checked) {
    window.SHOW_EXTENDED_HOURS = checked;
    document.cookie = 'portfolio_show_extended=' + (checked ? 'true' : 'false') + ';path=/;max-age=31536000';
    document.querySelectorAll('.extended-hours-cell').forEach(function (el) {
        el.classList.toggle('d-none', !checked);
    });
    if (window._heatmapMode) {
        var panel = document.getElementById('heatmap-panel');
        if (panel) _buildHeatmap(panel);
    }
}

// ── Live Unrealized P&L recompute (keeps summary row + Global Value/P&L columns
// in sync with the same live-price poll that already refreshes Price/Change) ──
function _baseCurrencySymbol() {
    var cur = window.BASE_CURRENCY || 'GBP';
    return ({ GBP: '£', USD: '$', EUR: '€' })[cur] || (cur + ' ');
}

function _updateRowPnl(rowEl, rawPrice) {
    var shares   = parseFloat(rowEl.dataset.shares);
    var costBase = parseFloat(rowEl.dataset.costBase);
    var fxRate   = parseFloat(rowEl.dataset.fxRate);
    var price    = parseFloat(rawPrice);
    if (!shares || !isFinite(fxRate) || !isFinite(price)) return;

    var valueBase = shares * price * fxRate;
    rowEl.setAttribute('data-value-base', valueBase);

    var sym  = _baseCurrencySymbol();
    var gvEl = document.getElementById('gv-' + rowEl.dataset.ticker);
    if (gvEl) {
        gvEl.textContent = sym + valueBase.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        var gvCell = gvEl.closest('td');
        if (gvCell) gvCell.setAttribute('data-sort', valueBase);
    }

    if (!isFinite(costBase)) return;
    var gpnlEl = document.getElementById('gpnl-' + rowEl.dataset.ticker);
    if (!gpnlEl) return;
    var pnl  = valueBase - costBase;
    var pct  = costBase ? (pnl / costBase * 100) : null;
    var sign = pnl >= 0 ? '+' : '';
    var cls  = pnl >= 0 ? 'trend-up' : 'trend-down';
    var html = '<span class="' + cls + '">' + sign + sym + pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '</span>';
    if (pct !== null) {
        html += '<br><small class="cell-sub">' + sign + pct.toFixed(2) + '%</small>';
    }
    gpnlEl.innerHTML = html;
    var gpnlCell = gpnlEl.closest('td');
    if (gpnlCell) gpnlCell.setAttribute('data-sort', pnl);
}
window._updateRowPnl = _updateRowPnl;

function _recomputePortfolioSummary() {
    var rows = window._portfolioTable
        ? window._portfolioTable.rows().nodes()
        : document.querySelectorAll('#dataTable tbody tr');
    var totalValue = 0, totalCost = 0, any = false;
    Array.prototype.forEach.call(rows, function (rowEl) {
        if (rowEl.classList.contains('child')) return;
        var v = parseFloat(rowEl.dataset.valueBase);
        var c = parseFloat(rowEl.dataset.costBase);
        if (isFinite(v) && isFinite(c) && c > 0) {
            totalValue += v;
            totalCost += c;
            any = true;
        }
    });
    if (!any) return;
    _updateSummaryRow({
        base_currency: window.BASE_CURRENCY,
        portfolio_total_value: totalValue,
        portfolio_total_investment: totalCost
    });
}
window._recomputePortfolioSummary = _recomputePortfolioSummary;

function _loadXray(accountId) {
    var loadEl = document.getElementById('xray-loading');
    var contEl = document.getElementById('xray-content');
    if (loadEl) { loadEl.style.display = 'flex'; loadEl.innerHTML = '<div class="xray-spinner"></div>Computing X-ray analysis&hellip;'; }
    if (contEl) contEl.style.display = 'none';

    Promise.all([
        fetch('/api/xray?account_id=' + encodeURIComponent(accountId)).then(function (r) { return r.json(); }),
        fetch('/api/macro-regime-allocation').then(function (r) { return r.json(); }).catch(function () { return null; })
    ])
        .then(function (results) {
            var data      = results[0];
            var macroData = results[1];
            if (data.error) {
                if (loadEl) loadEl.innerHTML = '<span class="xray-error-msg">&#9888; ' + data.error + '</span>';
                return;
            }
            _renderXray(data, macroData);
            _updateSummaryRow(data);
            if (loadEl) loadEl.style.display = 'none';
            if (contEl) contEl.style.display = 'block';
            requestAnimationFrame(function () { window.dispatchEvent(new Event('resize')); });
        })
        .catch(function () {
            if (loadEl) loadEl.innerHTML = '<span class="xray-error-msg">&#9888; Failed to load X-ray data. Try again or check server logs.</span>';
        });
}

// ── Render helpers ────────────────────────────────────────────────────────────
function _abbr(label, tip) {
    return tip ? '<abbr title="' + tip.replace(/"/g, '&quot;') + '">' + label + '</abbr>' : label;
}

function _card(label, value, tipText, colorClass) {
    var labelHtml = tipText
        ? '<abbr title="' + tipText.replace(/"/g, '&quot;') + '">' + label + '</abbr>'
        : label;
    return '<div class="xray-metric-card">'
        + '<div class="xray-metric-label">' + labelHtml + '</div>'
        + '<div class="xray-metric-value' + (colorClass ? ' ' + colorClass : '') + '">' + value + '</div>'
        + '</div>';
}

function _pct(v, dec) {
    if (v === null || v === undefined) return 'N/A';
    return (v * 100).toFixed(dec !== undefined ? dec : 1) + '%';
}

function _num(v, dec) {
    if (v === null || v === undefined) return 'N/A';
    return v.toFixed(dec !== undefined ? dec : 2);
}

function _baseLayout(overrides) {
    var base = {
        paper_bgcolor: '#1e1e1e', plot_bgcolor: '#1e1e1e',
        font: { color: '#e0e0e0', family: 'system-ui,-apple-system,sans-serif', size: 12 },
        margin: { t: 30, r: 20, b: 40, l: 20 },
        showlegend: false,
        autosize: true
    };
    return Object.assign(base, overrides || {});
}
var _PC = { displayModeBar: false, responsive: true };

// ── Cash status toggle (localStorage) ────────────────────────────────────────
function _setCashStatus(val) {
    localStorage.setItem('macro_cash_ok', val);
    var btnYes = document.getElementById('ma-cash-btn-yes');
    var btnNo  = document.getElementById('ma-cash-btn-no');
    if (btnYes) btnYes.className = 'ma-cash-btn' + (val === 'yes' ? ' active'    : '');
    if (btnNo)  btnNo.className  = 'ma-cash-btn' + (val === 'no'  ? ' active-no' : '');
    var deltaEl = document.getElementById('ma-cash-delta-text');
    if (deltaEl && window._macroAllocData && window._macroAllocData.fmtAmt) {
        var d   = window._macroAllocData;
        var fmt = d.fmtAmt;
        if (val === 'yes') {
            deltaEl.className   = 'ma-cash-delta-ok';
            deltaEl.textContent = 'On target ✓';
        } else {
            var isShort = d.cashDelta < 0;
            deltaEl.className   = isShort ? 'ma-cash-delta-short' : 'ma-cash-delta-ok';
            deltaEl.textContent = fmt(Math.abs(d.cashDelta)) + (isShort ? ' SHORT' : ' SURPLUS');
        }
    }
    if (window._macroAllocData) {
        var c = window._macroAllocData.current
            ? Object.assign({}, window._macroAllocData.current)
            : null;
        if (c && val === 'yes' && window._macroAllocData.ideal) {
            c.cash = window._macroAllocData.ideal.cash || 0;
        } else if (c) {
            c.cash = window._macroAllocData.originalCash;
        }
        _plotMacroAlloc('xray-ma-alloc-chart', window._macroAllocData.ideal, c);
    }
}

// ── Macro regime section ──────────────────────────────────────────────────────
function _renderMacroSection(macroData, xrayData) {
    var REGIME_ORDER  = ['Risk-On', 'Recovery', 'Late Cycle', 'Stagflation', 'Contraction'];
    var REGIME_COLORS = { 'Risk-On': '#00ffcc', 'Recovery': '#4499ff', 'Late Cycle': '#ffcc00', 'Stagflation': '#ff9900', 'Contraction': '#ff4444' };
    var REGIME_DESC   = {
        'Risk-On':     'Positive yield curve, contained inflation, tight spreads. Equities favoured.',
        'Recovery':    'Cycle transitioning from contraction. Rebuilding equity exposure as spreads tighten.',
        'Late Cycle':  'Flattening curve with rising inflation. Reduce equity overweights; add bonds and real assets.',
        'Stagflation': 'High inflation meets financial stress or negative real yields. Hard assets and cash over bonds.',
        'Contraction': 'Inverted curve and recession-mode macro. Defensive tilt: long bonds, reduce equity risk.'
    };
    var ASSET_ORDER  = ['equities', 'bonds', 'commodities'];
    var ASSET_LABELS = { equities: 'Equities', bonds: 'Bonds', commodities: 'Commodities' };

    if (!macroData || macroData.status === 'no_data') {
        return '<div class="xray-section">'
            + '<h3 class="xray-section-title">&#127759; Macro Regime &amp; Portfolio Alignment</h3>'
            + '<p class="ma-note">Run the Macro Data Engine in <a href="/settings#macro" style="color:#00ffcc">Settings &rarr; Macroeconomic Data</a> to populate regime data.</p>'
            + '</div>';
    }

    var cur   = (xrayData && xrayData.base_currency) || 'GBP';
    var sym   = ({ GBP: '£', USD: '$', EUR: '€' })[cur] || (cur + ' ');
    var label = macroData.regime_label || 'Risk-On';
    var color = REGIME_COLORS[label] || '#aaaaaa';
    var sigs  = macroData.key_signals || {};
    var ideal = macroData.ideal_allocation || {};
    var curr  = macroData.current_allocation;
    var deltas  = macroData.rebalance_deltas;
    var ranges  = macroData.regime_ranges || {};
    var score   = macroData.alignment_score;
    var history = macroData.regime_history || [];

    var fN = function (v, dp) { return (v == null) ? '—' : parseFloat(v).toFixed(dp !== undefined ? dp : 2); };
    var threatColor = function (lvl) { return lvl === 'RED' ? '#ff4444' : lvl === 'YELLOW' ? '#ffcc00' : '#00ffcc'; };

    var html = '<div class="xray-section">'
        + '<h3 class="xray-section-title">&#127759; Macro Regime &amp; Portfolio Alignment</h3>';

    html += '<div class="ma-traffic-strip">';
    REGIME_ORDER.forEach(function (r) {
        var rc = REGIME_COLORS[r] || '#aaa';
        html += '<div class="ma-traffic-box' + (r === label ? ' active' : '') + '" style="color:' + rc + '">' + r + '</div>';
    });
    html += '</div>';

    html += '<div class="ma-regime-banner" style="border-color:' + color + '">'
        + '<div><span class="ma-regime-badge" style="background:' + color + ';color:#111">' + label + '</span>'
        + '<span class="ma-regime-date">as of ' + (macroData.regime_date || '—') + '</span></div>'
        + '<div class="ma-regime-desc">' + (REGIME_DESC[label] || '') + '</div>'
        + '<div class="ma-threat-row">'
        + '<span>US yield threat: <strong style="color:' + threatColor(macroData.us_threat_level) + '">' + (macroData.us_threat_level || '—') + '</strong></span>'
        + '&nbsp;|&nbsp;<span>UK yield threat: <strong style="color:' + threatColor(macroData.uk_threat_level) + '">' + (macroData.uk_threat_level || '—') + '</strong></span>'
        + (macroData.yield_curve_inverted ? '&nbsp;|&nbsp;<span style="color:#ff4444">&#9888; Inverted ' + macroData.days_inverted + ' day' + (macroData.days_inverted !== 1 ? 's' : '') + '</span>' : '')
        + '</div></div>';

    var sigDefs = [
        { label: '10y–2y Yield Curve', v: sigs.us_yield_curve,
          disp: sigs.us_yield_curve != null ? (sigs.us_yield_curve > 0 ? '+' : '') + fN(sigs.us_yield_curve) + '%' : '—',
          note: macroData.yield_curve_inverted ? 'Inverted' : 'Normal',
          color: macroData.yield_curve_inverted ? '#ff4444' : '#00ffcc',
          abbr: '10-year minus 2-year US Treasury spread. Negative = inverted. A sustained inversion has preceded every US recession since 1970.' },
        { label: 'US CPI Inflation', v: sigs.us_cpi_inflation,
          disp: sigs.us_cpi_inflation != null ? fN(sigs.us_cpi_inflation, 1) + '%' : '—',
          note: sigs.us_cpi_inflation == null ? '—' : sigs.us_cpi_inflation > 4 ? 'High' : sigs.us_cpi_inflation > 3 ? 'Elevated' : 'Contained',
          color: sigs.us_cpi_inflation > 4 ? '#ff9900' : sigs.us_cpi_inflation > 3 ? '#ffcc00' : '#00ffcc',
          abbr: 'US Consumer Price Index YoY change from FRED. Above 4% = high inflationary pressure.' },
        { label: 'US HY Credit Spread', v: sigs.us_high_yield_spread,
          disp: sigs.us_high_yield_spread != null ? fN(sigs.us_high_yield_spread, 0) + ' bps' : '—',
          note: sigs.us_high_yield_spread == null ? '—' : sigs.us_high_yield_spread > 600 ? 'Blown out' : sigs.us_high_yield_spread > 400 ? 'Widening' : 'Tight',
          color: sigs.us_high_yield_spread > 600 ? '#ff4444' : sigs.us_high_yield_spread > 400 ? '#ffcc00' : '#00ffcc',
          abbr: 'US High-Yield OAS spread. Above 600 bps = financial distress.' },
        { label: 'Real Yield (10y TIPS)', v: sigs.us_real_yield_10y,
          disp: sigs.us_real_yield_10y != null ? (sigs.us_real_yield_10y > 0 ? '+' : '') + fN(sigs.us_real_yield_10y) + '%' : null,
          note: sigs.us_real_yield_10y == null ? null : sigs.us_real_yield_10y < 0 ? 'Negative — stagflation' : 'Positive',
          color: sigs.us_real_yield_10y != null && sigs.us_real_yield_10y < 0 ? '#ff9900' : '#00ffcc',
          abbr: '10-year TIPS yield. Negative = bonds cannot keep pace with inflation — classic stagflation signal.' }
    ];
    var sigHtml = '<div class="ma-signals-grid">';
    sigDefs.forEach(function (c) {
        if (c.disp == null) return;
        sigHtml += '<div class="ma-signal-card">'
            + '<div class="ma-signal-label"><abbr title="' + c.abbr + '">' + c.label + '</abbr></div>'
            + '<div class="ma-signal-value" style="color:' + c.color + '">' + c.disp + '</div>'
            + '<div class="ma-signal-note" style="color:' + c.color + '">' + c.note + '</div>'
            + '</div>';
    });
    sigHtml += '</div>';

    var gaugeColor = score == null ? '#666' : score >= 75 ? '#00ffcc' : score >= 50 ? '#ffcc00' : '#ff4444';
    var scoreHtml = score != null
        ? '<div class="ma-score-value" style="color:' + gaugeColor + '">' + score + '<span class="ma-score-unit">/100</span></div>'
          + '<div class="ma-score-label">Portfolio alignment to regime ideal</div>'
        : '<div class="ma-score-na">' + (macroData.portfolio_note || 'Portfolio data unavailable.') + '</div>';

    html += '<div class="ma-two-col">'
        + '<div class="ma-panel"><h3 class="ma-panel-title">Driving Signals</h3>' + sigHtml + '</div>'
        + '<div class="ma-panel ma-score-panel"><h3 class="ma-panel-title">Alignment Score</h3>' + scoreHtml + '</div>'
        + '</div>';

    html += '<div class="ma-panel"><h3 class="ma-panel-title">Asset Class Allocation: Current vs Regime Ideal</h3>'
        + '<div id="xray-ma-alloc-chart" class="ma-chart-area"></div>';

    var investedValue = 0;
    if (xrayData && xrayData.holdings) { xrayData.holdings.forEach(function (h) { investedValue += (h.value || 0); }); }
    var totalValue   = (xrayData && xrayData.portfolio_total_value) || 0;
    var cashHeld     = Math.max(0, totalValue - investedValue);
    var idealCashPct = ideal.cash || 0;
    var idealCashAmt = totalValue * idealCashPct / 100;
    var cashDelta    = cashHeld - idealCashAmt;
    var fmtAmt       = function (v) { return sym + Math.round(v).toLocaleString(); };
    var cashOkVal    = localStorage.getItem('macro_cash_ok');

    html += '<div class="ma-cash-card"><div class="ma-cash-row"><span><strong>Cash Reserve</strong></span></div>';
    if (totalValue > 0) {
        html += '<div class="ma-cash-row"><span>You hold:</span><span>' + fmtAmt(cashHeld) + ' (' + (cashHeld / totalValue * 100).toFixed(1) + '%)</span></div>'
            + '<div class="ma-cash-row"><span>Regime target:</span><span>' + fmtAmt(idealCashAmt) + ' (' + idealCashPct.toFixed(1) + '%)</span></div>'
            + '<div class="ma-cash-row"><span>You are:</span><span id="ma-cash-delta-text" class="' + (cashDelta < 0 ? 'ma-cash-delta-short' : 'ma-cash-delta-ok') + '">'
            + fmtAmt(Math.abs(cashDelta)) + (cashDelta < 0 ? ' SHORT' : ' SURPLUS') + '</span></div>';
    } else {
        html += '<div class="ma-cash-row"><span style="color:#888">Portfolio value unavailable — connect Ghostfolio to see cash guidance.</span></div>';
    }
    html += '<div style="margin-top:10px;display:flex;gap:8px;">'
        + '<button class="ma-cash-btn' + (cashOkVal === 'yes' ? ' active'    : '') + '" onclick="_setCashStatus(\'yes\')" id="ma-cash-btn-yes">&#10003; I\'m on target</button>'
        + '<button class="ma-cash-btn' + (cashOkVal === 'no'  ? ' active-no' : '') + '" onclick="_setCashStatus(\'no\')"  id="ma-cash-btn-no">&#10007; I\'m short on cash</button>'
        + '</div></div>';

    if (curr) {
        var tRows = ASSET_ORDER.map(function (k) {
            var delta  = deltas ? deltas[k] : null;
            var lo     = ranges[k] ? ranges[k][0] : null;
            var hi     = ranges[k] ? ranges[k][1] : null;
            var action = delta == null ? '—' : Math.abs(delta) < 2 ? 'On Target' : delta > 0 ? 'Underweight' : 'Overweight';
            var ac     = action === 'On Target' ? '#00ffcc' : action === 'Underweight' ? '#4499ff' : '#ffcc00';
            return '<tr><td>' + ASSET_LABELS[k] + '</td>'
                + '<td>' + (curr[k] != null ? curr[k].toFixed(1) + '%' : '—') + '</td>'
                + '<td>' + (lo != null ? lo.toFixed(1) + '% – ' + hi.toFixed(1) + '%' : '—') + '</td>'
                + '<td>' + (delta != null ? (delta > 0 ? '+' : '') + delta.toFixed(1) + '%' : '—') + '</td>'
                + '<td style="color:' + ac + '">' + action + '</td></tr>';
        }).join('');
        html += '<table class="ma-alloc-table"><thead><tr><th>Asset Class</th><th>Current</th><th>Regime Range</th><th>Delta</th><th>Action</th></tr></thead>'
            + '<tbody>' + tRows + '</tbody></table>';
    } else if (macroData.portfolio_note) {
        html += '<p class="ma-note">' + macroData.portfolio_note + '</p>';
    }
    html += '</div>';

    if (history.length > 1) {
        html += '<div class="ma-panel"><h3 class="ma-panel-title">Regime History (last 90 days)</h3>'
            + '<div id="xray-ma-history-chart" class="ma-chart-area"></div></div>';
    }

    html += '</div>';
    window._macroAllocData = {
        ideal: ideal,
        current: curr,
        originalCash: curr ? (curr.cash || 0) : 0,
        cashDelta: cashDelta,
        idealCashAmt: idealCashAmt,
        fmtAmt: fmtAmt
    };
    return { html: html, history: history, ideal: ideal, current: curr };
}

// ── Full X-ray render ─────────────────────────────────────────────────────────
function _renderXray(data, macroData) {
    var rm   = data.risk_metrics || {};
    var conc = data.concentration || {};
    var inc  = data.income || {};
    var tips = data.tooltips || {};
    var cur  = data.base_currency || 'GBP';
    var sym  = ({ GBP: '£', USD: '$', EUR: '€' })[cur] || (cur + ' ');

    var macroResult = _renderMacroSection(macroData, data);
    var html = macroResult.html;

    if (data.data_warnings && data.data_warnings.length) {
        html += '<div class="xray-warnings">';
        data.data_warnings.forEach(function (w) { html += '<div class="xray-warning-item">&#9888; ' + w + '</div>'; });
        html += '</div>';
    }

    var bv = rm.portfolio_beta, bc = '';
    if (bv !== null && bv !== undefined) bc = bv > 1.5 ? 'xray-val-red' : bv > 1.1 ? 'xray-val-amber' : '';
    var hv = conc.hhi, hc = '';
    if (hv !== undefined) hc = hv > 0.25 ? 'xray-val-red' : hv > 0.15 ? 'xray-val-amber' : 'xray-val-green';
    var t5v = conc.top5_weight, t5c = '';
    if (t5v !== undefined) t5c = t5v > 0.5 ? 'xray-val-red' : t5v > 0.35 ? 'xray-val-amber' : '';
    var ddv = rm.max_drawdown, ddc = '';
    if (ddv !== null && ddv !== undefined) ddc = ddv < -0.25 ? 'xray-val-red' : ddv < -0.15 ? 'xray-val-amber' : '';
    var varAbs = (rm.var_95_1d !== null && rm.var_95_1d !== undefined)
        ? sym + Math.abs(rm.var_95_1d).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })
        : 'N/A';
    var acv = rm.avg_pairwise_correlation, acc = '';
    if (acv !== null && acv !== undefined) acc = acv > 0.7 ? 'xray-val-red' : acv > 0.5 ? 'xray-val-amber' : 'xray-val-green';

    html += '<div class="xray-cards-row">';
    html += _card('Portfolio Beta',   bv !== null && bv !== undefined ? _num(bv, 2) : 'N/A', tips.beta,            bc);
    html += _card('Ann. Volatility',  _pct(rm.annualized_vol, 1),                             tips.vol,             '');
    html += _card('Max Drawdown',     ddv !== null && ddv !== undefined ? _pct(ddv, 1) : 'N/A', tips.max_drawdown, ddc);
    html += _card('VaR 95% (1-day)',  varAbs,                                                 tips.var,             '');
    html += _card('HHI Score',        hv !== undefined ? _num(hv, 3) : 'N/A',                 tips.hhi,             hc);
    html += _card('Top-5 Weight',     t5v !== undefined ? _pct(t5v, 1) : 'N/A',               tips.top5,            t5c);
    html += _card('Avg Correlation',  acv !== null && acv !== undefined ? _num(acv, 3) : 'N/A', tips.avg_correlation, acc);
    html += '</div>';

    var hasExtended = rm.sharpe_ratio !== null && rm.sharpe_ratio !== undefined;
    if (hasExtended) {
        var sv = rm.sharpe_ratio, sc = '';
        if (sv !== null && sv !== undefined) sc = sv > 1 ? 'xray-val-green' : sv > 0 ? 'xray-val-amber' : 'xray-val-red';
        var hvarAbs = (rm.historical_var_95_1d !== null && rm.historical_var_95_1d !== undefined)
            ? sym + rm.historical_var_95_1d.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : 'N/A';
        var cvarAbs = (rm.cvar_95_1d !== null && rm.cvar_95_1d !== undefined)
            ? sym + rm.cvar_95_1d.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : 'N/A';
        var skv = rm.skewness, skc = '';
        if (skv !== null && skv !== undefined) skc = skv < -0.5 ? 'xray-val-red' : skv < 0 ? 'xray-val-amber' : '';
        html += '<div class="xray-cards-row xray-cards-row-secondary">';
        html += _card('Sharpe Ratio',    sv !== null && sv !== undefined ? _num(sv, 2) : 'N/A', tips.sharpe_ratio,   sc);
        html += _card('Calmar Ratio',    rm.calmar_ratio !== null && rm.calmar_ratio !== undefined ? _num(rm.calmar_ratio, 2) : 'N/A', tips.calmar_ratio, '');
        html += _card('Hist. VaR 95%',   hvarAbs,                                               tips.historical_var, '');
        html += _card('CVaR 95%',        cvarAbs,                                               tips.cvar,           '');
        html += _card('Tracking Error',  _pct(rm.tracking_error, 1),                            tips.tracking_error, '');
        html += _card('Return Skewness', skv !== null && skv !== undefined ? _num(skv, 2) : 'N/A', tips.skewness,   skc);
        html += '</div>';
    }

    var hasFX = data.fx_exposure && data.fx_exposure.length > 0;
    html += '<div class="xray-section">'
        + '<h3 class="xray-section-title">' + _abbr('Allocation Overview', tips.weights_note) + '</h3>'
        + '<div class="' + (hasFX ? 'xray-row-2x2' : 'xray-row-3col') + '">'
        + '<div class="xray-chart-box"><div class="xray-chart-label">' + _abbr('Instrument Type', tips.instrument_type) + '</div><div id="xray-c-assetclass" class="xray-chart-el"></div></div>'
        + '<div class="xray-chart-box"><div class="xray-chart-label">' + _abbr('True Sector Exposure', tips.sector_lookthrough) + ' <span class="xray-badge">look-through</span></div><div id="xray-c-sector" class="xray-chart-el"></div></div>'
        + '<div class="xray-chart-box"><div class="xray-chart-label">Geographic Exposure</div><div id="xray-c-geo" class="xray-chart-el"></div></div>'
        + (hasFX ? '<div class="xray-chart-box"><div class="xray-chart-label">' + _abbr('FX / Currency Exposure', tips.fx_exposure) + '</div><div id="xray-c-fx" class="xray-chart-el"></div></div>' : '')
        + '</div></div>';

    html += '<div class="xray-section">'
        + '<h3 class="xray-section-title">Position Concentration</h3>'
        + '<div id="xray-c-concentration" class="xray-chart-el"></div>'
        + '</div>';

    var mrcHoldings = (data.holdings || []).filter(function (h) { return h.marginal_risk_contribution != null; });
    if (mrcHoldings.length >= 2) {
        html += '<div class="xray-section">'
            + '<h3 class="xray-section-title">' + _abbr('Risk Contribution per Holding', tips.marginal_risk_contribution) + '</h3>'
            + '<div id="xray-c-mrc" class="xray-chart-el"></div>'
            + '</div>';
    }

    var cm = data.correlation_matrix || {};
    if (cm.tickers && cm.tickers.length >= 2) {
        html += '<div class="xray-section">'
            + '<h3 class="xray-section-title">' + _abbr('Correlation Matrix', tips.avg_correlation) + '</h3>'
            + '<div class="xray-corr-guide">'
            + '<strong>How to read this chart:</strong> Each cell shows the correlation between two holdings '
            + '&mdash; how closely they move together over the past year. '
            + 'A value near <strong>+1</strong> means the pair rises and falls in lockstep: holding both adds no diversification benefit, just doubled exposure. '
            + 'A value near <strong>0</strong> means the two holdings are essentially independent &mdash; what happens to one does not predict the other. '
            + 'A value near <strong>&minus;1</strong> means they move in opposite directions, acting as a natural hedge. '
            + 'The diagonal is always <strong>1.0</strong> (a holding is perfectly correlated with itself). '
            + '<br><br>'
            + '<strong>What to watch for:</strong> Red clusters between ETFs that are supposed to diversify you often reveal they hold the same underlying stocks '
            + '(e.g. a global tracker and a developed-market ETF). A fully red matrix means your portfolio reacts to market shocks as a single unit &mdash; when one falls, everything falls. '
            + 'Teal (low or negative correlation) holdings are your genuine shock absorbers.'
            + '<div class="xray-corr-legend">'
            + '<div class="xray-corr-legend-item"><div class="xray-corr-swatch" style="background:#ff4d4d"></div>Near +1 &mdash; move together, concentrated risk</div>'
            + '<div class="xray-corr-legend-item"><div class="xray-corr-swatch" style="background:#2c2c2c;border:1px solid #444"></div>Near 0 &mdash; independent, genuine diversification</div>'
            + '<div class="xray-corr-legend-item"><div class="xray-corr-swatch" style="background:#00ffcc"></div>Near &minus;1 &mdash; inverse movement, natural hedge</div>'
            + '</div>'
            + '</div>'
            + '<div id="xray-c-corr" class="xray-chart-el"></div>'
            + '</div>';
    }

    html += '<div class="xray-section"><h3 class="xray-section-title">Income &amp; Unrealised P&amp;L</h3>'
        + '<div class="xray-row-2col">'
        + '<div class="xray-income-cards">'
        + '<div class="xray-income-card"><div class="xray-income-label">' + _abbr('Weighted Dividend Yield', tips.dividend_yield) + '</div>'
        + '<div class="xray-income-value">' + _pct(inc.weighted_dividend_yield, 2) + '</div></div>'
        + '<div class="xray-income-card"><div class="xray-income-label">' + _abbr('Projected Annual Income', tips.projected_income) + '</div>'
        + '<div class="xray-income-value">' + sym + (inc.projected_annual_income || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + '</div></div>'
        + '</div>'
        + '<div id="xray-c-pnl" class="xray-chart-el"></div>'
        + '</div></div>';

    var recs = data.recommendations || {};
    var recGroups = [
        { key: 'market_development',   label: 'Market Development',    icon: '🌍' },
        { key: 'regional_clusters',    label: 'Regional Clusters',     icon: '🗺️' },
        { key: 'country_concentration', label: 'Country Concentration', icon: '🏳️' },
        { key: 'sector',               label: 'Sector Exposure',        icon: '📊' },
        { key: 'asset_class',          label: 'Asset Class',           icon: '🧩' },
        { key: 'concentration',        label: 'Concentration',         icon: '⚖️' },
        { key: 'risk_metrics',         label: 'Risk Metrics',          icon: '📉' },
        { key: 'income',               label: 'Income',                icon: '💰' }
    ];
    var statusBadge = {
        'exceeds': '<span class="xray-rec-badge xray-rec-exceeds">Exceeds</span>',
        'below':   '<span class="xray-rec-badge xray-rec-below">Below target</span>',
        'within':  '<span class="xray-rec-badge xray-rec-within">Within range ✓</span>',
        'ok':      '<span class="xray-rec-badge xray-rec-within">OK ✓</span>'
    };
    var hasAnyRec = recGroups.some(function (g) { return (recs[g.key] || []).length > 0; });
    if (hasAnyRec) {
        html += '<div class="xray-section"><h3 class="xray-section-title">Portfolio Recommendations</h3>';
        html += '<p class="xray-rec-intro">Comparison of your current allocations against the configured MSCI ACWI-based targets. '
              + 'Targets can be customised in <a href="/settings" style="color:#00bcd4;">Settings → X-Ray Allocation Targets</a>.</p>';
        recGroups.forEach(function (g) {
            var items = recs[g.key] || [];
            if (!items.length) return;
            html += '<details class="xray-rec-group" open>'
                  + '<summary class="xray-rec-group-summary">' + g.icon + ' ' + g.label
                  + ' <span class="xray-rec-count">' + items.length + '</span></summary>'
                  + '<div class="xray-rec-list">';
            items.forEach(function (item) {
                var badge = statusBadge[item.status] || '';
                html += '<div class="xray-rec-item xray-rec-item-' + item.status + '">'
                      + badge
                      + '<span class="xray-rec-msg">' + item.message + '</span>'
                      + '</div>';
            });
            html += '</div></details>';
        });
        html += '</div>';
    }

    if (rm.cache_date) {
        html += '<div class="xray-footer">Risk metrics as of ' + rm.cache_date
            + ' &middot; benchmark: ' + rm.benchmark
            + ' &middot; ' + _abbr('lookback: ' + rm.lookback_days + ' days', tips.benchmark)
            + ' &middot; all % = invested capital, cash excluded</div>';
    }

    document.getElementById('xray-content').innerHTML = html;

    _plotDonut('xray-c-assetclass', data.asset_class_allocation, 'By Type');
    var sectorData = data.sector_allocation.slice(0, 10);
    if (data.sector_allocation.length > 10) {
        var rest = data.sector_allocation.slice(10).reduce(function (s, x) { return s + x.weight; }, 0);
        sectorData = sectorData.concat([{ name: 'Other', weight: rest }]);
    }
    _plotDonut('xray-c-sector', sectorData, 'Sectors');
    _plotDonut('xray-c-geo', data.geographic_allocation, 'Regions');
    if (hasFX) {
        _plotDonut('xray-c-fx', data.fx_exposure.map(function (e) { return { name: e.currency, weight: e.weight }; }), 'Currency');
    }
    _plotConcentration('xray-c-concentration', data.holdings, conc);
    if (mrcHoldings.length >= 2) { _plotMRC('xray-c-mrc', data.holdings); }
    if (cm.tickers && cm.tickers.length >= 2) { _plotCorrHeatmap('xray-c-corr', cm); }
    _plotPnL('xray-c-pnl', data.holdings, sym);

    if (macroData && macroData.status === 'ok') {
        _plotMacroAlloc('xray-ma-alloc-chart', macroData.ideal_allocation, macroData.current_allocation);
        if (macroResult.history && macroResult.history.length > 1) {
            _plotMacroHistory('xray-ma-history-chart', macroResult.history);
        }
        var savedCash = localStorage.getItem('macro_cash_ok');
        if (savedCash) { _setCashStatus(savedCash); }
    }
}

// ── Chart renderers ───────────────────────────────────────────────────────────
var _DC = ['#4da6ff', '#00ffcc', '#ffd700', '#ff6b6b', '#bb86fc', '#ff9800', '#2196f3', '#4caf50', '#f44336', '#9c27b0', '#607d8b'];

function _plotDonut(elId, alloc, centerText) {
    if (!alloc || !alloc.length) return;
    Plotly.newPlot(elId, [{
        type: 'pie', hole: 0.55,
        values: alloc.map(function (a) { return a.weight; }),
        labels: alloc.map(function (a) { return a.name; }),
        textinfo: 'none',
        hovertemplate: '<b>%{label}</b><br>%{percent}<extra></extra>',
        marker: { colors: _DC }
    }], _baseLayout({
        height: 260,
        margin: { t: 10, r: 20, b: 30, l: 20 },
        showlegend: true,
        legend: { font: { size: 11, color: '#aaa' }, orientation: 'h', y: -0.15, x: 0.5, xanchor: 'center' },
        annotations: [{ text: centerText, showarrow: false, font: { color: '#888', size: 11 } }]
    }), _PC);
}

function _plotConcentration(elId, holdings, conc) {
    var top    = holdings.slice(0, 20);
    var labels = top.map(function (h) { return h.symbol; }).reverse();
    var values = top.map(function (h) { return h.weight * 100; }).reverse();
    var colors = values.map(function (v) { return v > 20 ? '#ff4d4d' : v > 10 ? '#ffaa00' : '#4da6ff'; });
    var maxV   = Math.max.apply(null, values);
    Plotly.newPlot(elId, [{
        type: 'bar', orientation: 'h',
        x: values, y: labels,
        marker: { color: colors },
        text: values.map(function (v) { return v.toFixed(1) + '%'; }),
        textposition: 'outside',
        hovertemplate: '<b>%{y}</b>: %{x:.1f}%<extra></extra>',
        cliponaxis: false
    }], _baseLayout({
        height: Math.max(320, top.length * 30 + 100),
        margin: { t: 20, r: 70, b: 40, l: 80 },
        xaxis: { color: '#888', tickformat: '.0f', ticksuffix: '%', range: [0, Math.min(maxV * 1.25, 100)] },
        yaxis: { color: '#aaa', automargin: true },
        shapes: [
            { type: 'line', x0: 10, x1: 10, y0: 0, y1: 1, yref: 'paper', line: { color: '#ffaa00', width: 1, dash: 'dot' } },
            { type: 'line', x0: 20, x1: 20, y0: 0, y1: 1, yref: 'paper', line: { color: '#ff4d4d', width: 1, dash: 'dot' } }
        ]
    }), _PC);
}

function _plotPnL(elId, holdings, curSym) {
    var withPnL = holdings.filter(function (h) { return h.gross_perf !== 0; });
    if (!withPnL.length) return;
    withPnL.sort(function (a, b) { return b.gross_perf - a.gross_perf; });
    Plotly.newPlot(elId, [{
        type: 'bar',
        x: withPnL.map(function (h) { return h.symbol; }),
        y: withPnL.map(function (h) { return h.gross_perf; }),
        marker: { color: withPnL.map(function (h) { return h.gross_perf >= 0 ? '#00ffcc' : '#ff4d4d'; }) },
        text: withPnL.map(function (h) {
            var p = h.gross_perf_pct;
            return p !== undefined ? (h.gross_perf >= 0 ? '+' : '') + p.toFixed(1) + '%' : '';
        }),
        textposition: 'outside',
        hovertemplate: '<b>%{x}</b><br>P&L: ' + curSym + '%{y:,.0f}<extra></extra>',
        cliponaxis: false
    }], _baseLayout({
        height: 260,
        margin: { t: 30, r: 20, b: 60, l: 80 },
        xaxis: { color: '#aaa', tickangle: -45, automargin: true },
        yaxis: { color: '#888', automargin: true, tickformat: ',.0f' },
        bargap: 0.3
    }), _PC);
}

function _plotMRC(elId, holdings) {
    var mrcH = holdings.filter(function (h) { return h.marginal_risk_contribution != null; });
    if (!mrcH.length) return;
    mrcH.sort(function (a, b) { return b.marginal_risk_contribution - a.marginal_risk_contribution; });
    var top    = mrcH.slice(0, 20).reverse();
    var mrcPct = top.map(function (h) { return h.marginal_risk_contribution * 100; });
    Plotly.newPlot(elId, [{
        type: 'bar', orientation: 'h',
        x: mrcPct,
        y: top.map(function (h) { return h.symbol; }),
        marker: { color: mrcPct.map(function (v) { return v > 5 ? '#ff4d4d' : v > 3 ? '#ffaa00' : '#bb86fc'; }) },
        text: mrcPct.map(function (v) { return v.toFixed(2) + '%'; }),
        textposition: 'outside',
        customdata: top.map(function (h) { return (h.weight * 100).toFixed(1); }),
        hovertemplate: '<b>%{y}</b><br>Risk contribution: %{x:.2f}%<br>Portfolio weight: %{customdata}%<extra></extra>',
        cliponaxis: false
    }], _baseLayout({
        height: Math.max(300, top.length * 28 + 100),
        margin: { t: 20, r: 80, b: 40, l: 80 },
        xaxis: { color: '#888', tickformat: '.1f', ticksuffix: '%', title: { text: '% of total portfolio volatility', font: { size: 11, color: '#666' } } },
        yaxis: { color: '#aaa', automargin: true }
    }), _PC);
}

function _plotCorrHeatmap(elId, corrData) {
    var tickers = corrData.tickers;
    var matrix  = corrData.matrix;
    if (!tickers || tickers.length < 2 || !matrix) return;
    var textVals = matrix.map(function (row) {
        return row.map(function (v) { return (v !== null && v !== undefined) ? v.toFixed(2) : ''; });
    });
    Plotly.newPlot(elId, [{
        type: 'heatmap',
        z: matrix, x: tickers, y: tickers,
        colorscale: [[0, '#00ffcc'], [0.5, '#1e1e1e'], [1, '#ff4d4d']],
        zmin: -1, zmax: 1,
        text: textVals,
        texttemplate: '%{text}',
        textfont: { size: 10, color: '#e0e0e0' },
        hovertemplate: '<b>%{x} × %{y}</b><br>Correlation: %{z:.3f}<extra></extra>',
        showscale: true,
        colorbar: {
            tickvals: [-1, -0.5, 0, 0.5, 1],
            ticktext: ['-1', '-0.5', '0', '+0.5', '+1'],
            thickness: 12, len: 0.8,
            tickfont: { color: '#888', size: 10 },
            title: { text: 'r', font: { color: '#888', size: 11 } }
        }
    }], _baseLayout({
        height: Math.max(320, tickers.length * 40 + 120),
        margin: { t: 20, r: 100, b: 100, l: 80 },
        xaxis: { color: '#aaa', tickangle: -45, automargin: true },
        yaxis: { color: '#aaa', automargin: true, autorange: 'reversed' }
    }), _PC);
}

function _plotMacroAlloc(elId, ideal, current) {
    if (!ideal || !document.getElementById(elId)) return;
    var ASSET_ORDER  = ['equities', 'bonds', 'commodities', 'cash'];
    var ASSET_LABELS = ['Equities', 'Bonds', 'Commodities', 'Cash'];
    var idealVals    = ASSET_ORDER.map(function (k) { return ideal[k] || 0; });
    var traces = [{
        name: 'Ideal (regime target)',
        x: ASSET_LABELS, y: idealVals,
        type: 'bar',
        marker: { color: '#00ffcc', opacity: 0.85 }
    }];
    if (current) {
        traces.push({
            name: 'Current portfolio',
            x: ASSET_LABELS,
            y: ASSET_ORDER.map(function (k) { return current[k] || 0; }),
            type: 'bar',
            marker: { color: '#4499ff', opacity: 0.85 }
        });
    }
    Plotly.newPlot(elId, traces, _baseLayout({
        barmode: 'group', height: 220,
        margin: { t: 20, b: 60, l: 50, r: 20 },
        showlegend: true,
        legend: { orientation: 'h', y: -0.3, font: { size: 11 } },
        yaxis: { title: { text: '%', font: { size: 11 } }, range: [0, 100] }
    }), _PC);
}

function _plotMacroHistory(elId, history) {
    if (!history || history.length < 2 || !document.getElementById(elId)) return;
    var REGIME_COLORS = { 'Risk-On': '#00ffcc', 'Recovery': '#4499ff', 'Late Cycle': '#ffcc00', 'Stagflation': '#ff9900', 'Contraction': '#ff4444' };
    var dates  = history.map(function (r) { return r.date; }).reverse();
    var labels = history.map(function (r) { return r.regime_label; }).reverse();
    Plotly.newPlot(elId, [{
        x: dates, y: labels, type: 'scatter', mode: 'markers',
        marker: { color: labels.map(function (l) { return REGIME_COLORS[l] || '#aaa'; }), size: 8 }
    }], _baseLayout({
        height: 180,
        margin: { t: 10, b: 40, l: 110, r: 20 },
        xaxis: { color: '#aaa' },
        yaxis: { color: '#aaa', categoryorder: 'array',
                 categoryarray: ['Contraction', 'Stagflation', 'Late Cycle', 'Recovery', 'Risk-On'] },
        showlegend: false
    }), _PC);
}

// ── DataTables init ───────────────────────────────────────────────────────────
$(document).ready(function () {
    var allCols = window.PORTFOLIO_COLUMNS || [];
    var colPrefs = window.PORTFOLIO_COLUMN_PREFS || { hidden_core_columns: [], shown_optional_columns: [] };
    var hiddenIndices = [];
    allCols.forEach(function (col, idx) {
        if (!ColumnPicker.resolveVisible(col.key, allCols, colPrefs)) hiddenIndices.push(idx);
    });

    var table = $('#dataTable').DataTable({
        responsive: true,
        pageLength: 50,
        lengthMenu: [[10, 25, 50, 100, 250, -1], [10, 25, 50, 100, 250, 'All']],
        deferRender: true,
        dom: 'lrtip',
        order: [],
        initComplete: function () {
            try { if (localStorage.getItem('portfolio_heatmap_active')) _enterHeatmapMode(); } catch(e) {}
            if (window.AUTO_XRAY) toggleXray();
        },
        columnDefs: [
            { responsivePriority: 1, targets: [0, 2, 5] },
            { responsivePriority: 2, targets: [3, 4, 20] },
            { responsivePriority: 3, targets: [18, 19] },
            { responsivePriority: 4, targets: [16] },
            { responsivePriority: 5, targets: [13, 14, 15] },
            { responsivePriority: 6, targets: [6, 7, 17] },
            { responsivePriority: 7, targets: [10, 11, 12] },
            { responsivePriority: 8, targets: [1, 8, 9] },
            { targets: hiddenIndices, visible: false }
        ]
    });
    window._portfolioTable = table;

    var picker = ColumnPicker.init({
        table: table,
        scope: 'portfolio',
        allColumns: allCols,
        prefs: colPrefs,
        menuId: 'columnPickerMenu'
    });

    var advFilter = AdvancedFilter.init({
        table: table,
        scope: 'portfolio',
        allColumns: allCols,
        modalId: 'advFilterModal',
        bodyId: 'advFilterBody',
        anchorId: 'dataTable_length',
        buttonClass: 'btn btn-sm btn-outline-secondary ms-2'
    });

    ColumnPicker.initViewsMenu(picker, {
        scope: 'portfolio',
        menuId: 'viewsPickerMenu',
        views: window.PORTFOLIO_VIEWS,
        getExtraViewData: function () { return { filter: advFilter.getCurrentFilter() }; },
        onApplyView: function (view) { advFilter.applyFilter(view.filter || []); }
    });

    applyStickyTheadOffset();
    window.addEventListener('resize', applyStickyTheadOffset);

    // Tap anywhere on a row (except the ticker link or the expand triangle itself)
    // to expand/collapse the responsive child row.
    $('#dataTable tbody').on('click', 'tr:not(.child)', function (e) {
        if ($(e.target).closest('a').length) return;
        if ($(e.target).closest('.dtr-control').length) return;
        $(this).find('.dtr-control').trigger('click');
    });

    $('#customSearchInput').on('keyup', function () {
        table.search(this.value).draw();
    });

    $('#signalFilter').on('change', function () {
        var val = $(this).val();
        if (val === 'ALL') { table.column(20).search('').draw(); }
        else { table.column(20).search('^' + val + '$', true, false).draw(); }
    });

    $('#tagFilter').on('change', function () {
        var val = $(this).val();
        if (val === 'ALL') { table.column(19).search('').draw(); }
        else { table.column(19).search(exactTagSearchPattern(val), true, false).draw(); }
    });

    ChangePeriod.init({
        table: table,
        cookieName: 'portfolio_change_period',
        globalVar: 'PORTFOLIO_CHANGE_PERIOD',
        onChange: function () {
            if (window._heatmapMode) {
                var panel = document.getElementById('heatmap-panel');
                if (panel) _buildHeatmap(panel);
            }
        }
    });
});

(function () {

    // ─── Dip Reset Time ───────────────────────────────────────────────────────────
    function formatDipResetLocalTime() {
        try {
            const now = new Date();
            const nyDate = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York' }).format(now);
            const [y, m, d] = nyDate.split('-').map(Number);
            // 16:05 ET can be 20:05 UTC (EDT) or 21:05 UTC (EST)
            for (const utcHour of [20, 21]) {
                const candidate = new Date(Date.UTC(y, m - 1, d, utcHour, 5));
                const nyHour = parseInt(
                    new Intl.DateTimeFormat('en', {
                        timeZone: 'America/New_York', hour: 'numeric', hour12: false
                    }).format(candidate)
                );
                if (nyHour === 16) {
                    return candidate.toLocaleTimeString([], {
                        hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
                    });
                }
            }
        } catch (e) {}
        return '16:05 ET';
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.dip-reset-time').forEach(function (el) {
            el.textContent = formatDipResetLocalTime();
        });
    });

    // ─── Dip Radar ────────────────────────────────────────────────────────────────
    async function toggleDipRadar(enable) {
        const ticker = window.STOCK_TICKER;
        const endpoint = enable ? '/api/intraday-monitor/add' : '/api/intraday-monitor/remove';
        const daysEl = document.getElementById('dip-radar-days');
        const days = daysEl ? (parseInt(daysEl.value, 10) || 1) : 1;
        try {
            await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(enable ? { ticker: ticker, days: days } : { ticker: ticker })
            });
        } catch (e) { console.error('DipRadar toggle error:', e); }
        if (daysEl) daysEl.disabled = enable;
        const resultDiv = document.getElementById('dip-radar-result');
        resultDiv.style.display = enable ? 'block' : 'none';
        if (enable) {
            document.getElementById('dip-score-display').textContent = 'Waiting for first scan (runs every 2 minutes during market hours)...';
            pollDipRadar();
        }
    }

    async function pollDipRadar() {
        const ticker = window.STOCK_TICKER;
        try {
            const resp = await fetch('/api/intraday-monitor/analysis/' + ticker);
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data) return;

            const score  = data.reversal_score || 0;
            const clr    = score >= 65 ? '#00cc66' : score >= 40 ? '#ffaa00' : '#ff4d4d';
            const label  = score >= 65 ? '&#x2705; BOTTOMING ZONE' : score >= 40 ? '&#x26A0;&#xFE0F; WATCHING' : '&mdash; Not triggered';

            const rsi        = data.rsi;
            const rsiFired   = rsi != null && rsi < 30;
            const rsiPts     = rsi != null ? (rsi < 25 ? 30 : rsi < 30 ? 15 : 0) : 0;
            const rsiSub     = rsi != null ? (rsi < 25 ? 'Extreme oversold' : rsi < 30 ? 'Oversold' : 'Neutral') : '';

            const price      = data.current_price;
            const bbFired    = data.bb_lower != null && price < data.bb_lower;
            const vwapFired  = data.vwap_lower != null && price < data.vwap_lower;
            const volFired   = data.vol_climax === true;

            var TIP_RSI  = 'Relative Strength Index (14-period, 1-min bars). Below 30 = oversold (+15 pts). Below 25 = extreme oversold (+30 pts).';
            var TIP_BB   = 'Lower Bollinger Band (20-bar SMA minus 2.5 standard deviations). Dip Radar uses 2.5σ instead of the standard 2σ so only truly extreme sell-offs score. Price below this line is expected less than 1.2% of the time. +25 pts.';
            var TIP_VWAP = 'Volume Weighted Average Price — the intraday fair-value benchmark used by institutional desks. Trading 2.5σ below VWAP signals an extreme discount to where the majority of today\'s volume traded. +20 pts.';
            var TIP_VOL  = 'Volume Capitulation: the current down-candle\'s volume exceeds the 20-bar rolling average by 3+ standard deviations. The footprint of forced selling (margin calls, stop-loss cascades) exhausting supply. +25 pts.';

            function tile(lbl, val, pts, fired, sub, tooltip) {
                var bg  = fired ? 'rgba(0,204,102,.1)'      : 'rgba(255,255,255,.04)';
                var bdr = fired ? '1px solid rgba(0,204,102,.35)' : '1px solid rgba(255,255,255,.1)';
                var ic  = fired ? '&#x2705;' : '&#x274C;';
                var pc  = fired ? '#00cc66' : '#555';
                var labelHtml = tooltip
                    ? '<abbr title="' + tooltip.replace(/"/g, '&quot;') + '">' + lbl + '</abbr>'
                    : lbl;
                return '<div style="background:' + bg + ';border:' + bdr + ';border-radius:6px;padding:9px 10px;min-width:0;">' +
                    '<div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px;">' + labelHtml + '</div>' +
                    '<div style="color:#e8e8e8;font-size:13px;font-weight:bold;margin-bottom:1px;">' + (val != null ? val : '&mdash;') + '</div>' +
                    '<div style="color:' + pc + ';font-size:11px;">' + ic + ' ' + pts + ' pts</div>' +
                    (sub ? '<div style="color:#666;font-size:10px;margin-top:2px;">' + sub + '</div>' : '') +
                    '</div>';
            }

            document.getElementById('dip-score-display').innerHTML =
                '<div style="margin-bottom:10px;">' +
                  '<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:5px;">' +
                    '<span style="color:#999;font-size:11px;text-transform:uppercase;letter-spacing:.5px;">Reversal Score</span>' +
                    '<span style="color:' + clr + ';font-size:17px;font-weight:bold;">' + score +
                      '<span style="font-size:11px;color:#555;"> / 100</span></span>' +
                  '</div>' +
                  '<div style="background:#1c1c1c;border-radius:4px;height:7px;overflow:hidden;">' +
                    '<div style="background:' + clr + ';width:' + score + '%;height:100%;border-radius:4px;"></div>' +
                  '</div>' +
                  '<div style="text-align:right;margin-top:3px;font-size:11px;color:' + clr + ';">' + label + '</div>' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(78px,1fr));gap:7px;margin-bottom:9px;">' +
                  tile('RSI 14',    rsi != null ? rsi.toFixed(1) : null, rsiPts, rsiFired, rsiSub, TIP_RSI) +
                  tile('BB Lower',  data.bb_lower  != null ? '$' + data.bb_lower.toFixed(2)  : null, bbFired   ? 25 : 0, bbFired,   bbFired   ? 'Price &lt; 2.5&sigma; band' : '', TIP_BB) +
                  tile('VWAP &minus;2.5&sigma;', data.vwap_lower != null ? '$' + data.vwap_lower.toFixed(2) : null, vwapFired ? 20 : 0, vwapFired, vwapFired ? 'VWAP deviation' : '', TIP_VWAP) +
                  tile('Vol Climax', volFired ? 'YES' : (data.vol_climax != null ? 'No' : null), volFired ? 25 : 0, volFired, volFired ? 'High-vol down-bar' : '', TIP_VOL) +
                '</div>' +
                '<div style="color:#555;font-size:11px;border-top:1px solid #222;padding-top:6px;">' +
                  'Price: <span style="color:#bbb;">' + (price != null ? price : '&mdash;') + '</span>' +
                  (data.vwap != null ? ' &nbsp;&middot;&nbsp; VWAP: <span style="color:#bbb;">' + data.vwap + '</span>' : '') +
                  ' &nbsp;&middot;&nbsp; Scan: <span style="color:#bbb;">' + (data.scan_ts || '&mdash;') + '</span>' +
                '</div>';
        } catch (e) { console.error('DipRadar poll error:', e); }
    }

    window.toggleDipRadar = toggleDipRadar;

    if (window.IS_DIP_MONITORED) {
        pollDipRadar();
        setInterval(pollDipRadar, 120000);
    }

    // ─── Watchlist Toggle ─────────────────────────────────────────────────────────
    var isInWatchlist = window.IS_IN_WATCHLIST;

    async function toggleWatchlist() {
        const ticker = window.STOCK_TICKER;
        const star = document.getElementById('watchlist-star');
        star.classList.add('syncing');
        const endpoint = isInWatchlist ? '/api/watchlist/remove' : '/api/watchlist/add';
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: ticker })
            });
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                isInWatchlist = !isInWatchlist;
                star.innerText = isInWatchlist ? '★' : '☆';
                const msg = document.createElement('span');
                msg.innerText = " Syncing...";
                msg.className = "star-sync-msg";
                star.parentNode.appendChild(msg);
                setTimeout(function () { msg.remove(); }, 2000);
            } else {
                alert("Failed to update watchlist: " + (data.message || "Unknown error"));
            }
        } catch (e) {
            alert("Network error updating watchlist.");
        }
        star.classList.remove('syncing');
    }

    window.toggleWatchlist = toggleWatchlist;

    // ─── Intraday Auto-Refresh ────────────────────────────────────────────────────
    var _intradayBusy = false;
    var _intradayTimer = null;

    async function _refreshIntradayChart() {
        const ticker = window.STOCK_TICKER;
        if (_intradayBusy) return;
        _intradayBusy = true;
        try {
            const resp = await fetch('/api/intraday-chart/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: ticker })
            });
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data || !data.html) return;
            const wrapper = document.getElementById('intraday-wrapper');
            if (!wrapper) return;
            const btn = wrapper.querySelector('.fullscreen-btn');
            Array.from(wrapper.children).forEach(function (child) {
                if (child !== btn) child.remove();
            });
            const tmp = document.createElement('div');
            tmp.innerHTML = data.html;
            Array.from(tmp.childNodes).forEach(function (node) {
                if (node.nodeName !== 'SCRIPT') {
                    wrapper.appendChild(node.cloneNode(true));
                }
            });
            tmp.querySelectorAll('script').forEach(function (oldScript) {
                const s = document.createElement('script');
                Array.from(oldScript.attributes).forEach(function (attr) {
                    s.setAttribute(attr.name, attr.value);
                });
                s.textContent = oldScript.textContent;
                wrapper.appendChild(s);
            });
            // The freshly re-rendered chart comes back at its server-default height —
            // if the wrapper is mid-fullscreen, re-apply that state (see toggleFullscreen).
            if (wrapper.classList.contains('is-fullscreen')) {
                ChartFullscreen.relayoutForCurrentState('intraday-wrapper', _stockChartOpts('intraday-wrapper'));
            }
        } catch (e) {
            // silently ignore — next tick will retry
        } finally {
            _intradayBusy = false;
        }
    }

    function startIntradayAutoRefresh() {
        if (!window.ENABLE_LIVE_ASSETS) return;
        if (window.STOCK_QUOTE_TYPE === 'MUTUALFUND') return;
        const intervalMs = (window.REFRESH_RATE_MS && window.REFRESH_RATE_MS > 0)
            ? window.REFRESH_RATE_MS : 60000;
        _refreshIntradayChart();
        _intradayTimer = setInterval(function () {
            resetCountdown();
            _refreshIntradayChart();
        }, intervalMs);
    }

    // ─── Refresh Status Countdown ─────────────────────────────────────────────────
    var _countdownSecs = 0;
    var _countdownTick = null;

    function updateCountdownDisplay() {
        const el = document.getElementById('refresh-status');
        if (!el) return;
        const m = Math.floor(_countdownSecs / 60);
        const s = _countdownSecs % 60;
        el.innerHTML = '<span class="pulse-dot pulse-dot-live"></span> Next update in ' + m + ':' + String(s).padStart(2, '0');
    }

    function resetCountdown() {
        _countdownSecs = Math.round((window.REFRESH_RATE_MS || 60000) / 1000);
        updateCountdownDisplay();
    }

    function initRefreshStatus() {
        const el = document.getElementById('refresh-status');
        if (!el) return;
        if (!window.ENABLE_LIVE_ASSETS) {
            el.innerHTML = '<span class="pulse-dot offline"></span> Manual updates only';
            return;
        }
        resetCountdown();
        _countdownTick = setInterval(function () {
            _countdownSecs = Math.max(0, _countdownSecs - 1);
            updateCountdownDisplay();
        }, 1000);
    }

    startIntradayAutoRefresh();
    document.addEventListener('DOMContentLoaded', initRefreshStatus);

    // ─── Single Asset Refresh ─────────────────────────────────────────────────────
    async function refreshSingleData() {
        const ticker = window.STOCK_TICKER;
        _intradayBusy = true;
        if (_intradayTimer) clearInterval(_intradayTimer);

        const btn = document.getElementById('refreshDataBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="btn-icon spin-icon">&#8635;</span> Crunching...';

        try {
            const response = await fetch('/api/data/refresh-single', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: ticker })
            });
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                window.location.reload();
            } else {
                alert("Failed to refresh data: " + (data.message || "Unknown error"));
                btn.disabled = false;
                btn.innerHTML = '<span class="btn-icon">&#8635;</span> Refresh';
                _intradayBusy = false;
                startIntradayAutoRefresh();
            }
        } catch (e) {
            alert("Network error refreshing data.");
            btn.disabled = false;
            btn.innerHTML = '<span class="btn-icon">&#8635;</span> Refresh';
            _intradayBusy = false;
            startIntradayAutoRefresh();
        }
    }

    window.refreshSingleData = refreshSingleData;

    // ─── Fullscreen Toggle ────────────────────────────────────────────────────────
    // These three charts are server-rendered (visuals.py's fig.to_html()), so
    // config.responsive never actually reacts to container size changes (rotation,
    // fullscreen) — width/height must be relayout'd explicitly, per AGENTS.md rule 18.
    var _STOCK_CHART_IDS = ['intraday-wrapper', 'macro-wrapper', 'anomaly-wrapper'];
    var _chartDefaultHeights = {};

    function _captureChartDefaultHeights() {
        _STOCK_CHART_IDS.forEach(function (id) {
            var wrapper = document.getElementById(id);
            var plotEl = wrapper && wrapper.querySelector('.js-plotly-plot');
            if (plotEl && plotEl.layout) _chartDefaultHeights[id] = plotEl.layout.height;
        });
    }

    // On desktop the chart wrapper lives inside .detail-right-column which has
    // position:sticky. Sticky elements form a CSS stacking context with z-index:auto
    // (paint step 7), so Bootstrap's navbar (z-index:1020, step 8) always paints on
    // top — making the fullscreen chart partially obscured. Fix: hide the navbar for
    // the duration of fullscreen mode. Also elevate the sticky column's z-index so the
    // fixed wrapper is above any other root-level stacking contexts.
    function _stockChartOpts(wrapperId) {
        return {
            forceWidth: true,
            getHeight: function () { return _chartDefaultHeights[wrapperId]; },
            onEnter: function (wrapper) {
                var navbar = document.querySelector('.app-navbar');
                var stickyCol = wrapper.closest('.detail-right-column');
                if (navbar) navbar.style.display = 'none';
                if (stickyCol) stickyCol.style.zIndex = '10000';
            },
            onExit: function (wrapper) {
                var navbar = document.querySelector('.app-navbar');
                var stickyCol = wrapper.closest('.detail-right-column');
                if (navbar) navbar.style.display = '';
                if (stickyCol) stickyCol.style.zIndex = '';
            },
        };
    }

    function toggleFullscreen(wrapperId) {
        ChartFullscreen.toggle(wrapperId, _stockChartOpts(wrapperId));
    }

    window.toggleFullscreen = toggleFullscreen;

    document.addEventListener('DOMContentLoaded', _captureChartDefaultHeights);

    window.addEventListener('resize', function () {
        _STOCK_CHART_IDS.forEach(function (id) {
            ChartFullscreen.relayoutForCurrentState(id, _stockChartOpts(id));
        });
    });

    // ─── Position Sizing ──────────────────────────────────────────────────────────
    function recalc() {
        const ticker        = window.STOCK_TICKER;
        const nativeCurrency = window.STOCK_CURRENCY;
        const baseCurrency  = window.BASE_CURRENCY;
        const atrPct        = window.STOCK_ATR_PCT;

        function getCurrentPrice() {
            const priceEl = document.getElementById("price-" + ticker);
            if (priceEl) {
                const txt = priceEl.textContent.replace(/[^0-9.\-]/g, "");
                let val = parseFloat(txt);
                if (!isNaN(val) && val > 0) {
                    if (nativeCurrency === 'GBp') val = val * 100.0;
                    return val;
                }
            }
            return window.STOCK_CURRENT_PRICE || null;
        }

        const accountValue = parseFloat(document.getElementById("ps-account-value").value);
        const riskPct      = parseFloat(document.getElementById("ps-risk-pct").value);
        const stopMultiple = parseFloat(document.getElementById("ps-stop-multiple").value);
        const entryPrice   = getCurrentPrice();
        const fxRate       = window.FX_RATES[nativeCurrency] || 1.0;

        const result = window.PositionSizing.calculate({
            accountValue:  accountValue,
            entryPrice:    entryPrice,
            atrPct:        atrPct,
            fxRateToBase:  fxRate,
            riskPct:       riskPct,
            stopMultiple:  stopMultiple,
        });

        const fmt = window.PositionSizing.formatCurrency;

        document.getElementById("ps-current-price").textContent =
            entryPrice ? fmt(entryPrice, nativeCurrency) : "—";
        document.getElementById("ps-atr").textContent =
            atrPct ? (atrPct * 100).toFixed(2) + "%" +
                    (entryPrice ? " (" + fmt(entryPrice * atrPct, nativeCurrency) + ")" : "")
                : "—";
        document.getElementById("ps-stop-loss").textContent =
            result.stopPrice != null ? fmt(result.stopPrice, nativeCurrency) : "—";
        document.getElementById("ps-risk-per-share").textContent =
            result.riskPerShareNative != null ? fmt(result.riskPerShareNative, nativeCurrency) : "—";
        document.getElementById("ps-shares").textContent =
            result.shares != null ? result.shares.toLocaleString() + " shares" : "—";
        document.getElementById("ps-position-value").textContent =
            result.positionValue != null ? fmt(result.positionValue, baseCurrency) : "—";
        document.getElementById("ps-at-risk").textContent =
            result.riskAmount != null ? fmt(result.riskAmount, baseCurrency) : "—";
    }

    document.addEventListener("DOMContentLoaded", function () {
        var ids = ["ps-account-value", "ps-risk-pct", "ps-stop-multiple"];
        ids.forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener("input", recalc);
        });
        recalc();
    });

})();

function startNameEdit() {
    document.getElementById('name-edit-input').value =
        document.getElementById('company-name-display').textContent.trim();
    document.getElementById('name-edit-form').hidden = false;
    document.getElementById('name-edit-btn').hidden = true;
    document.getElementById('name-edit-input').focus();
}

function cancelNameEdit() {
    document.getElementById('name-edit-form').hidden = true;
    document.getElementById('name-edit-btn').hidden = false;
}

function toggleSetTargetsPanel() {
    var panel = document.getElementById('setTargetsPanel');
    if (panel) panel.classList.toggle('d-none');
}

function toggleTargetAllAccountsMode() {
    var allRow = document.getElementById('targetAllAccountsRow');
    var perAccountRows = document.getElementById('targetPerAccountRows');
    var useAll = document.getElementById('targetSetForAll').checked;
    if (allRow) allRow.classList.toggle('d-none', !useAll);
    if (perAccountRows) perAccountRows.classList.toggle('d-none', useAll);
}

function _targetInputValue(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    var raw = el.value.trim();
    if (raw === '') return null;
    var num = parseFloat(raw);
    if (isNaN(num) || num <= 0) return null;
    if (window.STOCK_CURRENCY === 'GBp') num = num * 100.0;
    return num;
}

async function _postHoldingPriceLimit(accountId, ticker, lowLimit, highLimit) {
    const response = await fetch('/api/accounts/holding-price-limit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            account_id: accountId,
            ticker: ticker,
            low_limit: lowLimit,
            high_limit: highLimit
        })
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || 'Failed to save target.');
    }
    return data;
}

function renderCurrentTargetsDisplay(limitsByAccount) {
    var container = document.getElementById('currentTargetsDisplay');
    if (!container) return;
    var currency = window.STOCK_CURRENCY;
    var fmt = window.PositionSizing.formatCurrency;
    var rows = (window.TARGET_ACCOUNTS || [])
        .map(function (acc) {
            var limits = limitsByAccount[acc.account_id] || {};
            if (limits.low_limit == null && limits.high_limit == null) return null;
            var low = limits.low_limit != null ? fmt(limits.low_limit, currency) : 'Not set';
            var high = limits.high_limit != null ? fmt(limits.high_limit, currency) : 'Not set';
            return '<div class="sub-account-row"><div class="sub-account-name">' +
                escapeHtml(acc.name) + '</div><div>Low: <strong>' + low +
                '</strong> &nbsp;|&nbsp; High: <strong>' + high + '</strong></div></div>';
        })
        .filter(function (html) { return html !== null; });
    container.innerHTML = rows.length
        ? '<h3 class="text-sm-caps">Your Targets</h3>' + rows.join('')
        : '';
}

async function saveTargets() {
    const statusEl = document.getElementById('setTargetsStatus');
    const ticker = window.STOCK_TICKER;
    const useAll = document.getElementById('targetSetForAll').checked;
    statusEl.textContent = 'Saving...';

    const limitsByAccount = Object.assign({}, window.HOLDING_PRICE_LIMITS || {});
    const failedAccounts = [];
    for (const acc of window.TARGET_ACCOUNTS) {
        const low = useAll ? _targetInputValue('target-low-all') : _targetInputValue('target-low-' + acc.account_id);
        const high = useAll ? _targetInputValue('target-high-all') : _targetInputValue('target-high-' + acc.account_id);
        try {
            await _postHoldingPriceLimit(acc.account_id, ticker, low, high);
            limitsByAccount[acc.account_id] = { low_limit: low, high_limit: high };
        } catch (e) {
            failedAccounts.push(acc.name);
        }
    }
    window.HOLDING_PRICE_LIMITS = limitsByAccount;
    renderCurrentTargetsDisplay(limitsByAccount);

    if (failedAccounts.length) {
        statusEl.textContent = 'Failed to save for: ' + failedAccounts.join(', ');
    } else {
        statusEl.textContent = 'Saved.';
        setTimeout(function () { statusEl.textContent = ''; }, 3000);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    if (window.TARGET_ACCOUNTS && window.TARGET_ACCOUNTS.length) {
        renderCurrentTargetsDisplay(window.HOLDING_PRICE_LIMITS || {});
    }
});

function saveNameOverride(reset) {
    var name = reset ? '' : document.getElementById('name-edit-input').value.trim();
    fetch('/api/ticker/' + window.STOCK_TICKER + '/name-override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: name })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'success') {
            location.reload();
        }
    });
}

function toggleAddNotePanel() {
    var panel = document.getElementById('addNotePanel');
    if (panel) panel.classList.toggle('d-none');
}

function saveTickerNote() {
    var statusEl = document.getElementById('addNoteStatus');
    var noteText = document.getElementById('newNoteTextarea').value.trim();
    if (!noteText) return;
    statusEl.textContent = 'Saving...';
    fetch('/api/ticker/' + window.STOCK_TICKER + '/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note_text: noteText })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'success') {
            location.reload();
        } else {
            statusEl.textContent = data.message || 'Failed to save note.';
        }
    })
    .catch(function() { statusEl.textContent = 'Network error saving note.'; });
}

function toggleEditNotePanel(noteId) {
    var view = document.getElementById('note-view-' + noteId);
    var panel = document.getElementById('note-edit-' + noteId);
    if (view) view.classList.toggle('d-none');
    if (panel) panel.classList.toggle('d-none');
}

function saveEditedNote(noteId) {
    var noteText = document.getElementById('note-edit-textarea-' + noteId).value.trim();
    if (!noteText) return;
    fetch('/api/ticker/' + window.STOCK_TICKER + '/notes/' + noteId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note_text: noteText })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'success') {
            location.reload();
        } else {
            alert(data.message || 'Failed to save note.');
        }
    });
}

function deleteTickerNote(noteId) {
    if (!confirm('Delete this note? This cannot be undone.')) return;
    fetch('/api/ticker/' + window.STOCK_TICKER + '/notes/' + noteId, { method: 'DELETE' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'success') {
            location.reload();
        } else {
            alert(data.message || 'Failed to delete note.');
        }
    });
}

function toggleNotesSection() {
    var body = document.getElementById('notesSectionBody');
    var icon = document.getElementById('notesToggleIcon');
    var visible = !body.classList.contains('d-none');
    if (visible) {
        body.classList.add('d-none');
        icon.innerHTML = '&#9654;';
    } else {
        body.classList.remove('d-none');
        icon.innerHTML = '&#9660;';
    }
}

function toggleNoteTruncate(noteId) {
    var body = document.getElementById('note-view-' + noteId);
    var link = document.getElementById('note-toggle-link-' + noteId);
    var expanded = body.classList.toggle('note-expanded');
    link.textContent = expanded ? 'Show less' : 'Show more';
}

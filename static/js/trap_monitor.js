(function () {
    var POLL_MS = 60000;
    var countdown = POLL_MS / 1000;
    var countdownTimer = null;

    // ── Shared state: populated by each fetch, narrative builds once all three arrive ──
    var _state = { hmm: null, stress: null, trap: null };

    // ── Scope filter: applies to the table and the alert strip; the situation card's
    // narrative/severity and the lifecycle arc highlighting stay market-wide by design ──
    var _tmAllRows = [];
    var _tmPortfolioTickers = new Set();
    var _tmWatchlistTickers = new Set();
    var _tmActiveScope = 'portfolio';

    function _tmInScope(ticker) {
        if (_tmActiveScope === 'portfolio') return _tmPortfolioTickers.has(ticker);
        if (_tmActiveScope === 'watchlist') return _tmWatchlistTickers.has(ticker);
        return true;
    }

    function _tmFilteredRows() {
        return _tmAllRows.filter(function (r) { return _tmInScope(r.ticker); });
    }

    // ── Style maps ──────────────────────────────────────────────────────────────────────
    var PHASE_STYLE = {
        'ACTIVE_SELLOFF':      { bg: 'rgba(255,77,77,.15)',   color: '#ff4d4d',  border: '#ff4d4d',  label: 'ACTIVE SELLOFF' },
        'BULL_TRAP_RISK':      { bg: 'rgba(255,170,0,.12)',   color: '#ffaa00',  border: '#ffaa00',  label: 'BULL TRAP RISK' },
        'CAPITULATION_FORMING':{ bg: 'rgba(255,136,136,.12)', color: '#ff8888',  border: '#ff8888',  label: 'CAPITULATION' },
        'BEAR_TRAP_RISK':      { bg: 'rgba(77,166,255,.12)',  color: '#4da6ff',  border: '#4da6ff',  label: 'BEAR TRAP RISK' },
        'ACCUMULATION':        { bg: 'rgba(0,255,204,.1)',    color: '#00ffcc',  border: '#00ffcc',  label: 'ACCUMULATION' },
        'CAUTION':             { bg: 'rgba(255,204,0,.1)',    color: '#ffcc00',  border: '#ffcc00',  label: 'CAUTION' },
        'NEUTRAL':             { bg: 'rgba(128,128,128,.1)',  color: '#888',     border: '#555',     label: 'NEUTRAL' },
    };
    var HMM_PILL_STYLE = {
        'Bull':  { bg: 'rgba(0,255,0,.15)',   color: '#00ff00', border: '#00cc00' },
        'Chop':  { bg: 'rgba(255,170,0,.15)', color: '#ffaa00', border: '#cc8800' },
        'Crash': { bg: 'rgba(255,77,77,.18)', color: '#ff4d4d', border: '#cc2222' },
    };
    var BULL_STYLE  = { 'SEVERE_TRAP_RISK': { color: '#ff4d4d', label: 'SEVERE' }, 'ELEVATED_RISK': { color: '#ffaa00', label: 'ELEVATED' }, 'ACTIVE_SELLOFF': { color: '#ff4d4d', label: 'SELLOFF' }, 'SAFE': { color: '#444', label: '—' } };
    var BEAR_STYLE  = { 'CONFIRMED_BEAR_TRAP': { color: '#4da6ff', label: 'CONFIRMED' }, 'POSSIBLE_BEAR_TRAP': { color: '#88aaff', label: 'POSSIBLE' }, 'SAFE': { color: '#444', label: '—' } };
    var CAP_STYLE   = { 'CAPITULATION_FORMING': { color: '#ff8888', label: 'FORMING' }, 'WATCH': { color: '#ffcc00', label: 'WATCH' }, 'NONE': { color: '#444', label: '—' } };
    var WYK_STYLE   = { 'ACCUMULATION_PHASE': { color: '#00ffcc', label: 'ACTIVE' }, 'SQUEEZE_FORMING': { color: '#00ccaa', label: 'SQUEEZE' }, 'NONE': { color: '#444', label: '—' } };

    var MSI_FEATURE_LABELS = { vix_level: 'VIX', vix_ma_ratio: 'VIX/MA', hyg_return: 'HYG', tnx_change: '10Y Δ', spy_vol_zscore: 'Vol Z', spy_return: 'SPY' };

    // ── Utility ─────────────────────────────────────────────────────────────────────────
    function pill(style, label) {
        return '<span style="display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:700;background:' + style.bg + ';color:' + style.color + ';border:1px solid ' + style.border + ';">' + label + '</span>';
    }
    function signalCell(styleMap, level) {
        var s = styleMap[level] || { color: '#444', label: level || '—' };
        return '<span style="color:' + s.color + ';font-weight:600;font-size:11px;">' + s.label + '</span>';
    }
    function emaColor(val) {
        if (val == null) return '#888';
        return val < -5 ? '#ff4d4d' : val < 0 ? '#ffaa00' : val > 3 ? '#00ff00' : '#aaa';
    }
    function rsiColor(val) {
        if (val == null) return '#888';
        return val < 25 ? '#ff4d4d' : val < 35 ? '#ffaa00' : val > 70 ? '#00ccff' : '#aaa';
    }
    function msiColor(s) {
        return s >= 0.75 ? '#ff4d4d' : s >= 0.50 ? '#ffaa00' : '#00cc44';
    }
    function msiLabel(s) {
        return s >= 0.75 ? 'STRESS ALERT' : s >= 0.50 ? 'ELEVATED' : 'NORMAL';
    }
    function msiFeatureColor(k, v) {
        if (v == null) return '#888';
        if (k === 'vix_level')      return v > 30 ? '#ff4d4d' : v > 20 ? '#ffaa00' : '#4caf50';
        if (k === 'vix_ma_ratio')   return v > 1.5 ? '#ff4d4d' : v > 1.2 ? '#ffaa00' : '#aaa';
        if (k === 'hyg_return')     return v < -1.0 ? '#ff4d4d' : v < -0.3 ? '#ffaa00' : '#aaa';
        if (k === 'tnx_change')     return Math.abs(v) > 0.1 ? '#ffaa00' : '#aaa';
        if (k === 'spy_vol_zscore') return v > 2 ? '#ff4d4d' : v > 1 ? '#ffaa00' : '#aaa';
        if (k === 'spy_return')     return v < -1.5 ? '#ff4d4d' : v < -0.5 ? '#ffaa00' : '#aaa';
        return '#aaa';
    }
    function msiFeatureDisplay(k, v) {
        if (v == null) return '—';
        if (k === 'hyg_return' || k === 'spy_return') return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
        if (k === 'vix_ma_ratio') return v.toFixed(2) + '×';
        if (k === 'tnx_change') return (v >= 0 ? '+' : '') + v.toFixed(2);
        return v.toFixed(1);
    }

    // ── Dominant phase helper ────────────────────────────────────────────────────────────
    var ALERT_PHASES = ['ACTIVE_SELLOFF', 'BULL_TRAP_RISK', 'CAPITULATION_FORMING', 'BEAR_TRAP_RISK', 'ACCUMULATION'];
    function dominantPhase(rows) {
        var counts = {};
        (rows || []).forEach(function (r) {
            if (ALERT_PHASES.indexOf(r.phase) >= 0) counts[r.phase] = (counts[r.phase] || 0) + 1;
        });
        var best = null, bestN = 0;
        Object.keys(counts).forEach(function (p) { if (counts[p] > bestN) { best = p; bestN = counts[p]; } });
        return best;
    }
    function alertCount(rows) {
        return (rows || []).filter(function (r) { return ALERT_PHASES.indexOf(r.phase) >= 0; }).length;
    }

    // ── Narrative engine ─────────────────────────────────────────────────────────────────
    //
    // Severity levels: 0=calm(green) 1=watch(blue) 2=caution(amber) 3=danger(red)
    var SEV_COLORS  = ['#00cc44', '#4da6ff', '#ffaa00', '#ff4d4d'];
    var SEV_LABELS  = ['CALM', 'WATCH', 'CAUTION', 'DANGER'];
    var SEV_BG      = ['rgba(0,204,68,.06)', 'rgba(77,166,255,.06)', 'rgba(255,170,0,.07)', 'rgba(255,77,77,.08)'];

    function buildSituation(hmm, stress, trapRows) {
        var hmmLabel  = hmm  ? hmm.label  : null;
        var stressVal = stress ? stress.score : 0;
        var dom       = dominantPhase(trapRows);
        var alerts    = alertCount(trapRows);
        var stressAlert    = stressVal >= 0.75;
        var stressElevated = stressVal >= 0.50;
        var hmmCrash  = hmmLabel === 'Crash';
        var hmmChop   = hmmLabel === 'Chop';
        var hmmBull   = hmmLabel === 'Bull';

        var sev, title, narrative, watch;

        // Priority waterfall — most severe first
        if (hmmCrash && stressAlert) {
            sev = 3;
            title = 'Systemic crisis — both market structure and macro are broken';
            narrative = 'The broad market is in a confirmed Crash regime (HMM) and the macro stress model is simultaneously in alert territory. '
                + 'VIX is spiking, credit spreads are widening, and the joint distribution of macro signals is in its most anomalous 5% of the past two years. '
                + 'This combination historically accompanies the most damaging drawdown phases. Defensive positioning is strongly warranted.';
            watch = ['HYG (credit ETF) daily return — accelerating spread widening is the most dangerous leading signal',
                     'VIX term structure — backwardation (near-term VIX above long-term) signals acute fear',
                     'Watch for capitulation volume signatures in the ticker table — that would mark the final flush'];

        } else if (hmmCrash && stressElevated) {
            sev = 3;
            title = 'Crash regime with rising macro stress';
            narrative = 'SPY is in a confirmed Crash regime and macro conditions are deteriorating but not yet at full-alert levels. '
                + 'The stress model is reading elevated readings across VIX and/or credit. This is a worsening picture that warrants reduced risk exposure. '
                + 'The key question is whether macro stress continues to build toward a full systemic event.';
            watch = ['Stress score trajectory — two consecutive days above 0.75 would trigger the systemic alert',
                     'HYG and 10Y yield — if both are moving adversely simultaneously, credit is the driver',
                     'An HMM transition back to Chop would be the first sign of stabilisation'];

        } else if (hmmCrash && dom === 'BULL_TRAP_RISK') {
            sev = 3;
            title = 'Bull Trap risk inside a crash regime — high danger';
            narrative = 'SPY volatility is in crash territory and individual stocks are bouncing on low volume — the most dangerous pattern on this page. '
                + 'A Bull Trap forming within a confirmed crash regime typically precedes a secondary, often sharper, leg down. '
                + 'The bounce lacks institutional conviction: down-day volume significantly exceeds up-day volume. '
                + 'Crucially, macro conditions are not yet acutely stressed, suggesting this is a market-structure correction rather than a credit event — which means recovery is possible, but not yet signalled.';
            watch = ['Volume on the next up-day — any increase would be the first sign of genuine buying interest',
                     'VIX level — needs to start falling to confirm crash regime is easing',
                     'Stress score — if it rises above 0.50, the macro backdrop is worsening into this weakness'];

        } else if (hmmCrash && dom === 'CAPITULATION_FORMING') {
            sev = 2;
            title = 'Possible true bottom — capitulation forming in a crash regime';
            narrative = 'The market is in a confirmed crash regime and multiple monitored tickers are showing capitulation volume signatures: panic-selling climaxes with RSI below 30. '
                + 'When this occurs while the macro plumbing is intact (credit spreads fine, VIX not spiking to extremes), it is often the final flush before institutional absorption establishes a genuine bottom. '
                + 'This is not a buy signal yet — confirmation requires the volume to dry up in subsequent sessions.';
            watch = ['Wick patterns — a candle closing in the upper half of its range signals absorption',
                     'Volume in subsequent sessions — the Wyckoff pattern requires volume to contract after the flush',
                     'Stress score — if it remains below 0.50, credit is not in distress and recovery is more likely'];

        } else if (hmmCrash && dom === 'ACCUMULATION') {
            sev = 2;
            title = 'Crash regime, but bases are quietly forming';
            narrative = 'SPY volatility is elevated (crash regime) while individual tickers are entering Wyckoff accumulation patterns: Bollinger Bands squeezing, ATR contracting, volume drying up. '
                + 'Base-building in a crash regime historically precedes the fastest recoveries — institutions absorb supply quietly before the breakout. '
                + 'Macro conditions are not acutely stressed, which is supportive of a genuine base forming.';
            watch = ['Volume-confirmed breakout above the squeeze range — this is the entry signal',
                     'HMM — a transition to Chop is the first confirmation of regime stabilisation',
                     'Stress score — keep it below 0.50 to maintain confidence in the base'];

        } else if (hmmCrash) {
            sev = 2;
            title = 'Crash regime — elevated volatility, mixed ticker signals';
            narrative = 'SPY is in a confirmed crash regime with high realised volatility. Individual tickers show a mixed picture with no single dominant lifecycle phase. '
                + 'Macro conditions remain relatively calm, suggesting this is a market-structure correction rather than a macro-driven crisis. '
                + 'The range of outcomes from here is wide — monitor for a dominant pattern to emerge in the ticker table.';
            watch = ['Watch for the ticker table to converge on a single phase — that would clarify the next move',
                     'VIX — a sustained decline below 20 would signal regime transition is beginning',
                     'Any rise in the stress score would indicate the correction is feeding into macro'];

        } else if (stressAlert && !hmmCrash) {
            sev = 3;
            title = 'Macro stress alert — market structure hasn\'t broken yet';
            narrative = 'The macro stress model is detecting statistically abnormal conditions: the joint reading of VIX, credit spreads, yield volatility, and SPY dynamics is in its most anomalous 5% of the past two years. '
                + 'Crucially, SPY itself has not yet transitioned into a Crash regime — meaning the macro warning is leading the market reaction, not confirming it. '
                + 'Historically, macro stress signals that precede a crash regime transition give a short but actionable window to reduce exposure.';
            watch = ['HMM state — a transition to Crash would confirm the macro stress is feeding into equities',
                     'HYG daily return — the most sensitive real-time credit indicator',
                     'Individual ticker phases — any widespread ACTIVE_SELLOFF signals would confirm broadening'];

        } else if (hmmChop && dom === 'BULL_TRAP_RISK') {
            sev = 1;
            title = 'Sector correction — caution on low-volume bounces';
            narrative = 'The broad market (HMM) is in Chop — no clear directional trend, elevated realised volatility, but no confirmed breakdown. '
                + 'Individual stocks are bouncing after a sell-off, but the bounce volume is significantly below sell-off volume. '
                + 'This is the classic Bull Trap setup: smart money is not buying the dip. '
                + 'Critically, the macro plumbing is intact — VIX is calm, credit spreads are tight, yields are stable. This is a sector-specific or stock-specific event, not a systemic crisis.';
            watch = ['Volume on the next up-session — a meaningful increase would signal genuine demand returning',
                     'HMM state — a transition to Crash would deepen this picture significantly',
                     'Stress score — if it rises above 0.50, the macro backdrop is joining the weakness'];

        } else if (hmmChop && dom === 'CAPITULATION_FORMING') {
            sev = 1;
            title = 'Capitulation signatures in a choppy market — possible local bottom';
            narrative = 'The broad market is in a Chop regime with no clear trend, while some tickers are hitting capitulation volume: extreme selling climaxes with oversold RSI. '
                + 'Capitulation during a Chop regime (rather than full Crash) often produces faster recoveries — the broader tape has not broken down, providing a supportive backdrop. '
                + 'Macro conditions are calm, which further reduces the risk of a more serious leg down.';
            watch = ['Wick patterns and closing prices to confirm institutional absorption',
                     'Volume dry-up in subsequent sessions — the prerequisite for a Wyckoff base'];

        } else if (hmmChop && dom === 'ACCUMULATION') {
            sev = 1;
            title = 'Accumulation bases forming — pre-breakout setup in choppy market';
            narrative = 'Individual tickers are forming Wyckoff accumulation bases (tight Bollinger squeeze, contracting ATR) while the broader market drifts in a Chop regime. '
                + 'Base-building typically precedes a directional breakout. The neutral macro backdrop provides no headwind. '
                + 'The open question is whether the breakout comes to the upside or downside — HMM direction will be the confirming signal.';
            watch = ['Volume surge on breakout above the squeeze range — this is the entry signal',
                     'SPY — an HMM transition to Bull would provide the strongest confirmation'];

        } else if (hmmChop && dom === 'BEAR_TRAP_RISK') {
            sev = 1;
            title = 'False breakdown — short sellers may be caught offside';
            narrative = 'The market is in a Chop regime and some tickers are showing Bear Trap patterns: brief support breaks on low volume that immediately recover. '
                + 'This is a classic short-seller trap — momentum traders who broke support are now holding losing positions as price reverses. '
                + 'The macro environment is calm and the broader market has not broken down, supporting a continued recovery.';
            watch = ['Volume on the recovery — increasing volume confirms the trap has sprung',
                     'SPY support levels — if SPY also holds cleanly, the tape is supportive'];

        } else if (hmmChop && dom === 'ACTIVE_SELLOFF') {
            sev = 2;
            title = 'Active selling pressure in a choppy market';
            narrative = 'Multiple tickers are in an active selloff phase while the broad market regime is choppy rather than trending. '
                + 'The selloff is concentrated in specific names rather than being market-wide — SPY has not broken down and macro conditions are intact. '
                + 'However, concentrated selling that starts in a few names can broaden if the HMM transitions toward Crash.';
            watch = ['Whether the selling is spreading to other sectors or remaining contained',
                     'Stress score — any rise toward 0.50 would indicate the macro backdrop is worsening',
                     'HMM state — watch for transition to Crash as the key escalation signal'];

        } else if (hmmChop) {
            sev = 0;
            title = 'Directionless market — no dominant signal, elevated vol';
            narrative = 'The market is in a Chop regime: realised volatility is elevated but no clear directional trend has emerged. '
                + 'No trap, capitulation, or accumulation patterns are dominating the ticker table. '
                + 'Macro conditions are calm. This is a holding pattern — the market is digesting recent moves without committing to a direction.';
            watch = ['Volume-confirmed range breakout — up or down — would signal the next trend',
                     'Any Bull Trap patterns forming after a local pullback would suggest resolution is downward'];

        } else if (hmmBull && dom === 'BULL_TRAP_RISK') {
            sev = 0;
            title = 'Healthy market with pockets of weakness';
            narrative = 'The broad market is in a Bull regime, but some individual tickers are showing Bull Trap patterns after local sell-offs. '
                + 'This is selective weakness within an overall healthy market — likely sector rotation or stock-specific news. '
                + 'The macro backdrop and broad SPY trend remain constructive. Individual stock weakness is normal in a bull market.';
            watch = ['Whether the weakness stays contained to specific names or begins broadening',
                     'SPY relative strength — if the index itself starts lagging, regime transition may be approaching'];

        } else if (hmmBull && dom === 'ACCUMULATION') {
            sev = 0;
            title = 'Bull market with base-building — setup phase';
            narrative = 'The broad market is in a Bull regime and individual tickers are forming Wyckoff accumulation bases. '
                + 'This is the textbook setup: a healthy macro environment supporting quiet institutional accumulation before the next leg. '
                + 'No macro stress is present.';
            watch = ['Volume-confirmed breakouts from the squeeze range — the entry signal',
                     'RSI crossing above 50 on the breakout confirms momentum is following price'];

        } else if (hmmBull) {
            sev = 0;
            title = 'Clear conditions — bull market, no active traps';
            narrative = 'The broad market is in a Bull regime with low realised volatility. No trap, capitulation, or lifecycle signals are active across the monitored tickers. '
                + 'Macro conditions are calm. Risk-on positioning is supported by the current environment.';
            watch = ['Complacency: a sudden VIX spike or HYG drop would be the first macro warning',
                     'Any Bull Trap patterns emerging after a local pullback would signal a crack in the uptrend'];

        } else {
            // Data not loaded yet or ambiguous
            return;
        }

        // ── Render the situation card ────────────────────────────────────────────────────
        var card  = document.getElementById('situation-card');
        var color = SEV_COLORS[sev];
        var bg    = SEV_BG[sev];

        card.style.borderLeftColor = color;
        card.style.background = bg;

        document.getElementById('sit-severity-label').style.color   = color;
        document.getElementById('sit-severity-label').textContent    = SEV_LABELS[sev];
        document.getElementById('sit-title').textContent             = title;
        document.getElementById('sit-narrative').textContent         = narrative;

        var watchHtml = '<strong style="color:#666;font-size:11px;text-transform:uppercase;letter-spacing:0.8px;">Watch for:</strong><ul style="margin:6px 0 0 0;padding-left:18px;">';
        watch.forEach(function (w) { watchHtml += '<li style="margin-bottom:4px;">' + w + '</li>'; });
        watchHtml += '</ul>';
        document.getElementById('sit-watch').innerHTML = watchHtml;

        // Compact "you are here" arc in the header
        var arcPhases = [
            { key: 'ACTIVE_SELLOFF',       label: 'Selloff',   col: '#ff4d4d' },
            { key: 'BULL_TRAP_RISK',        label: 'Bull Trap', col: '#ffaa00' },
            { key: 'CAPITULATION_FORMING',  label: 'Cap',       col: '#ff8888' },
            { key: 'ACCUMULATION',          label: 'Base',      col: '#00ffcc' },
            { key: 'NEUTRAL',               label: 'Recovery',  col: '#00ff00' },
        ];
        var arcHtml = '';
        arcPhases.forEach(function (p, i) {
            var active = dom === p.key;
            var opacity = active ? '1' : '0.28';
            var border  = active ? ('2px solid ' + p.col) : '1px solid rgba(255,255,255,.08)';
            var bg2     = active ? ('rgba(' + hexToRgb(p.col) + ',.18)') : 'transparent';
            arcHtml += '<span style="font-size:10px;padding:3px 8px;border-radius:12px;border:' + border + ';color:' + p.col + ';opacity:' + opacity + ';white-space:nowrap;background:' + bg2 + ';">' + p.label + '</span>';
            if (i < arcPhases.length - 1) arcHtml += '<span style="color:#333;font-size:11px;">&#8594;</span>';
        });
        document.getElementById('sit-arc').innerHTML = arcHtml;

        card.classList.remove('d-none');
    }

    function hexToRgb(hex) {
        var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
        return r + ',' + g + ',' + b;
    }

    function tryRenderSituation() {
        if (_state.hmm === undefined || _state.stress === undefined || _state.trap === undefined) return;
        buildSituation(_state.hmm, _state.stress, _state.trap);
        renderStressStrip(_state.stress);
        renderHmmStrip(_state.hmm);
        renderTrapStrip(_state.trap);
    }

    // ── Signal strip renderers ────────────────────────────────────────────────────────────
    function renderHmmStrip(hmm) {
        if (!hmm) return;
        var sty = HMM_PILL_STYLE[hmm.label] || {};
        var p = document.getElementById('strip-hmm-pill');
        p.textContent = hmm.label.toUpperCase();
        p.style.background  = sty.bg || '';
        p.style.color       = sty.color || '';
        p.style.borderColor = sty.border || '';
        document.getElementById('strip-hmm-conf').textContent = Math.round(hmm.probability * 100) + '% conf.';
        var chgEl = document.getElementById('strip-hmm-change');
        if (hmm.last_change && hmm.last_change.date) {
            chgEl.textContent = 'Changed from ' + hmm.last_change.from_label + ' on ' + hmm.last_change.date;
        } else {
            chgEl.textContent = 'as of ' + (hmm.as_of || '—');
        }
    }

    function renderStressStrip(stress) {
        if (!stress) return;
        var s = stress.score;
        var col = msiColor(s);
        document.getElementById('strip-stress-score').textContent = s.toFixed(2);
        document.getElementById('strip-stress-score').style.color = col;
        document.getElementById('strip-stress-fill').style.width  = Math.round(s * 100) + '%';
        document.getElementById('strip-stress-fill').style.background = col;
        var lbl = document.getElementById('strip-stress-label');
        lbl.textContent = msiLabel(s);
        lbl.style.color = col;

        var feats = stress.features || {};
        var keys  = ['vix_level', 'vix_ma_ratio', 'hyg_return', 'tnx_change', 'spy_vol_zscore', 'spy_return'];
        var html  = '';
        keys.forEach(function (k) {
            var v  = feats[k] != null ? feats[k] : null;
            var fc = msiFeatureColor(k, v);
            html += '<span style="font-size:10px;white-space:nowrap;">' +
                '<span style="color:#444;">' + MSI_FEATURE_LABELS[k] + ' </span>' +
                '<span style="color:' + fc + ';font-family:monospace;">' + msiFeatureDisplay(k, v) + '</span></span>';
        });
        document.getElementById('strip-features').innerHTML = html;
    }

    function renderTrapStrip(rows) {
        var el = document.getElementById('strip-trap-summary');
        if (!rows || rows.length === 0) { el.innerHTML = '<span style="color:#555;">No scan data</span>'; return; }
        var n   = alertCount(rows);
        var dom = dominantPhase(rows);
        if (n === 0) {
            el.innerHTML = '<span style="color:#4caf50;font-weight:600;">' + rows.length + ' tickers</span><span style="color:#555;"> — no active alerts</span>';
        } else {
            var ps = dom ? (PHASE_STYLE[dom] || PHASE_STYLE['NEUTRAL']) : PHASE_STYLE['NEUTRAL'];
            el.innerHTML = '<span style="color:#ffaa00;font-weight:700;">' + n + ' ticker' + (n > 1 ? 's' : '') + ' flagged</span>' +
                ' <span style="font-size:11px;color:#666;">dominant: </span>' +
                '<span style="color:' + ps.color + ';font-weight:600;">' + (ps.label || '—') + '</span>';
        }
    }

    // ── Table rendering ───────────────────────────────────────────────────────────────────
    function renderTable(rows) {
        var tbody = document.getElementById('trap-tbody');
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center p-4 text-muted">No scan data yet. Click "Run Scan Now" or enable the scheduled job in Settings.</td></tr>';
            document.getElementById('ticker-count').textContent = '';
            return;
        }
        document.getElementById('ticker-count').textContent = '(' + rows.length + ' tickers)';
        var html = '', latestTs = '';
        rows.forEach(function (r) {
            var ps  = PHASE_STYLE[r.phase] || PHASE_STYLE['NEUTRAL'];
            var emaVal   = r.ema_distance != null ? r.ema_distance.toFixed(1) + '%' : '—';
            var rsiVal   = r.rsi != null ? r.rsi.toFixed(0) : '—';
            var tsDisplay = r.scan_ts ? r.scan_ts.replace('T', ' ').slice(0, 16) : '—';
            if (r.scan_ts && r.scan_ts > latestTs) latestTs = r.scan_ts;
            var volNote = r.bull_trap_vol_ratio != null ? ' <span style="color:#666;font-size:10px;">(ratio ' + r.bull_trap_vol_ratio.toFixed(2) + ')</span>' : '';
            html += '<tr>' +
                '<td style="padding:8px 10px;"><a href="/stock/' + r.ticker + '" style="color:#4da6ff;font-weight:600;text-decoration:none;">' + r.ticker + '</a></td>' +
                '<td style="text-align:center;padding:8px 10px;">' + pill(ps, ps.label) + '</td>' +
                '<td style="text-align:center;padding:8px 10px;">' + signalCell(BULL_STYLE, r.bull_trap_level) + volNote + '</td>' +
                '<td style="text-align:center;padding:8px 10px;">' + signalCell(BEAR_STYLE, r.bear_trap_level) + '</td>' +
                '<td style="text-align:center;padding:8px 10px;">' + signalCell(CAP_STYLE,  r.cap_level) + '</td>' +
                '<td style="text-align:center;padding:8px 10px;">' + signalCell(WYK_STYLE,  r.wyckoff_level) + '</td>' +
                '<td style="text-align:center;padding:8px 6px;color:' + emaColor(r.ema_distance) + ';font-family:monospace;">' + emaVal + '</td>' +
                '<td style="text-align:center;padding:8px 6px;color:' + rsiColor(r.rsi) + ';font-family:monospace;">' + rsiVal + '</td>' +
                '<td style="text-align:right;padding:8px 10px;color:#444;font-size:10px;font-family:monospace;">' + tsDisplay + '</td>' +
                '</tr>';
        });
        tbody.innerHTML = html;
        if (latestTs) document.getElementById('last-scan-ts').textContent = latestTs.replace('T', ' ').slice(0, 16) + ' UTC';
    }

    function renderAlertStrip(rows) {
        var strip = document.getElementById('alert-strip');
        var alerts = (rows || []).filter(function (r) { return ALERT_PHASES.indexOf(r.phase) >= 0; });
        if (alerts.length === 0) { strip.innerHTML = ''; return; }
        var html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">';
        alerts.forEach(function (r) {
            var ps    = PHASE_STYLE[r.phase] || PHASE_STYLE['NEUTRAL'];
            var notes = r.bull_trap_notes || r.bear_trap_notes || r.cap_notes || r.wyckoff_notes || '';
            html += '<div style="background:' + ps.bg + ';border:1px solid ' + ps.border + ';border-radius:8px;padding:10px 14px;max-width:340px;">' +
                '<div style="color:' + ps.color + ';font-weight:700;font-size:12px;margin-bottom:4px;">' + r.ticker + ' — ' + ps.label + '</div>' +
                '<div style="color:#aaa;font-size:11px;line-height:1.5;">' + (notes.length > 120 ? notes.slice(0, 120) + '…' : notes) + '</div>' +
                '</div>';
        });
        html += '</div>';
        strip.innerHTML = html;
    }

    function highlightArcStep(rows) {
        var phases = (rows || []).map(function (r) { return r.phase; });
        document.querySelectorAll('.arc-step').forEach(function (el) {
            var active = phases.indexOf(el.getAttribute('data-phase')) >= 0;
            el.style.opacity   = active ? '1' : '0.35';
            el.style.transform = active ? 'scale(1.04)' : '';
        });
    }

    // ── Fetchers ──────────────────────────────────────────────────────────────────────────
    function fetchRegime() {
        fetch('/api/market-regime/current')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || data.status !== 'success' || !data.current) { _state.hmm = null; tryRenderSituation(); return; }
                _state.hmm = {
                    label:       data.current.label,
                    probability: data.current.probability,
                    as_of:       data.current.as_of,
                    last_change: data.last_change,
                };
                tryRenderSituation();
            })
            .catch(function () { _state.hmm = null; tryRenderSituation(); });
    }

    function fetchStress() {
        fetch('/api/market-stress')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || data.status !== 'success' || !data.current) { _state.stress = null; tryRenderSituation(); return; }
                _state.stress = data.current;
                tryRenderSituation();
            })
            .catch(function () { _state.stress = null; tryRenderSituation(); });
    }

    function fetchResults() {
        fetch('/api/trap-monitor/results')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'success' && data.results) {
                    _state.trap = data.results;
                    _tmAllRows = data.results;
                    _tmPortfolioTickers = new Set(data.portfolio_tickers || []);
                    _tmWatchlistTickers = new Set(data.watchlist_tickers || []);
                    renderTable(_tmFilteredRows());
                    renderAlertStrip(_tmFilteredRows());
                    highlightArcStep(data.results);
                    tryRenderSituation();
                }
            })
            .catch(function (e) { console.warn('trap-monitor fetch failed:', e); });
    }

    function fetchAccuracy() {
        fetch('/api/trap-monitor/accuracy')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status !== 'success') return;
                var badge = document.getElementById('trap-acc-overall-badge');
                var body  = document.getElementById('trap-accuracy-body');
                var ov    = data.overall || {};
                var PHASE_LABELS = {
                    'BULL_TRAP_RISK':       'Bull Trap Risk',
                    'CAPITULATION_FORMING': 'Capitulation',
                    'BEAR_TRAP_RISK':       'Bear Trap Risk',
                    'ACCUMULATION':         'Accumulation',
                    'ACTIVE_SELLOFF':       'Active Selloff',
                };
                function accCell(acc, resolved) {
                    if (resolved === 0 || resolved == null) return '<span class="text-muted acc-val">Pending</span>';
                    var cls = acc >= 60 ? 'text-green' : acc >= 50 ? 'text-warning' : 'text-red';
                    var bar = '<span class="acc-bar" style="width:' + Math.round(acc * 0.5) + 'px;background:currentColor;"></span>';
                    return '<span class="' + cls + ' acc-val">' + bar + acc + '%</span>';
                }
                var ov14 = ov.resolved_14d || 0;
                var ov30 = ov.resolved_30d || 0;
                var ovAcc = ov14 > 0 ? ov.accuracy_14d : (ov30 > 0 ? ov.accuracy_30d : null);
                if (ovAcc != null) {
                    var cls = ovAcc >= 60 ? 'text-green' : ovAcc >= 50 ? 'text-warning' : 'text-red';
                    badge.className = 'trap-accuracy-badge ' + cls;
                    badge.textContent = 'Overall ' + ovAcc + '% (14d)';
                } else {
                    badge.textContent = 'Pending data';
                }
                var rows = (data.phases || []).map(function (p) {
                    return '<tr><td><span class="phase-label">' + (PHASE_LABELS[p.phase] || p.phase) + '</span></td>' +
                           '<td class="acc-val">' + (p.total || 0) + '</td>' +
                           '<td>' + accCell(p.accuracy_14d, p.resolved_14d) + '</td>' +
                           '<td class="acc-val text-muted">' + (p.resolved_14d || 0) + '</td>' +
                           '<td>' + accCell(p.accuracy_30d, p.resolved_30d) + '</td>' +
                           '<td class="acc-val text-muted">' + (p.resolved_30d || 0) + '</td></tr>';
                }).join('');
                body.innerHTML = '<table class="trap-accuracy-table">' +
                    '<thead><tr><th>Phase</th><th>Calls</th><th>14d Accuracy</th><th>14d Resolved</th><th>30d Accuracy</th><th>30d Resolved</th></tr></thead>' +
                    '<tbody>' + rows + '</tbody>' +
                    '<tfoot><tr class="trap-accuracy-overall-row">' +
                    '<td>Overall</td><td class="acc-val">' + (ov.total || 0) + '</td>' +
                    '<td>' + accCell(ov.accuracy_14d, ov14) + '</td><td class="acc-val text-muted">' + ov14 + '</td>' +
                    '<td>' + accCell(ov.accuracy_30d, ov30) + '</td><td class="acc-val text-muted">' + ov30 + '</td>' +
                    '</tr></tfoot></table>';
            })
            .catch(function () {});
    }

    // ── Countdown & scan ─────────────────────────────────────────────────────────────────
    function startCountdown() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdown = POLL_MS / 1000;
        countdownTimer = setInterval(function () {
            countdown = Math.max(0, countdown - 1);
            var el = document.getElementById('tm-countdown');
            if (el) el.textContent = countdown;
            if (countdown === 0) { fetchResults(); countdown = POLL_MS / 1000; }
        }, 1000);
    }

    window.runScanNow = function () {
        var btn = document.getElementById('run-now-btn');
        var status = document.getElementById('run-status');
        btn.disabled = true; btn.textContent = 'Scanning…'; status.textContent = '';
        fetch('/api/trap-monitor/run', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                status.textContent = data.message || 'Scan triggered.';
                setTimeout(function () {
                    fetchResults();
                    btn.disabled = false; btn.textContent = '▶ Run Scan Now'; status.textContent = 'Done.';
                    startCountdown();
                }, 4000);
            })
            .catch(function () { btn.disabled = false; btn.textContent = '▶ Run Scan Now'; status.textContent = 'Error — check notifications.'; });
    };

    document.getElementById('tm-scope-filter').addEventListener('change', function (e) {
        _tmActiveScope = e.target.value;
        renderTable(_tmFilteredRows());
        renderAlertStrip(_tmFilteredRows());
    });

    // Mark state as "not arrived yet" using undefined so tryRenderSituation knows to wait
    _state.hmm    = undefined;
    _state.stress = undefined;
    _state.trap   = undefined;

    fetchResults();
    fetchRegime();
    fetchStress();
    fetchAccuracy();
    startCountdown();
})();

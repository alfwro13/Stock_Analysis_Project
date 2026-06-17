(function () {
    var baseCurrency = window.STRESS_BASE_CURRENCY || 'GBP';
    var selectedScenarioId = 'gfc_2008';

    var currencySymbol = baseCurrency === 'GBP' ? '£'
        : baseCurrency === 'USD' ? '$'
        : baseCurrency === 'EUR' ? '€'
        : baseCurrency;

    function fmt(val) {
        var abs = Math.abs(val);
        if (abs >= 1000000) return currencySymbol + (val / 1000000).toFixed(2) + 'M';
        if (abs >= 1000) return currencySymbol + (abs >= 10000
            ? Math.round(val).toLocaleString()
            : val.toLocaleString(undefined, { maximumFractionDigits: 0 })
        );
        return currencySymbol + val.toFixed(2);
    }

    window.selectScenario = function (id) {
        selectedScenarioId = id;
        document.querySelectorAll('.stress-scenario-card').forEach(function (el) {
            el.classList.toggle('selected', el.getAttribute('data-id') === id);
        });
        var customRow = document.getElementById('custom-row');
        if (id === 'custom') {
            customRow.classList.remove('d-none');
        } else {
            customRow.classList.add('d-none');
        }
    };

    window.updateCustomPreview = function (val) {
        var n = parseFloat(val);
        var el = document.getElementById('custom-drop-preview');
        if (isNaN(n)) { el.textContent = '−?%'; return; }
        el.textContent = (n >= 0 ? '+' : '') + n + '%';
        el.style.color = n >= 0 ? '#4caf50' : '#ff4d4d';
    };

    window.runStressTest = function () {
        var btn    = document.getElementById('run-btn');
        var status = document.getElementById('run-status');

        var accountId = 'all';
        var accountSelect = document.getElementById('account-select');
        if (accountSelect) accountId = accountSelect.value;

        var customDrop = null;
        if (selectedScenarioId === 'custom') {
            var raw = parseFloat(document.getElementById('custom-drop-input').value);
            if (isNaN(raw) || raw >= 0) {
                status.textContent = 'Enter a negative drop % for the custom scenario.';
                status.style.color = '#ff4d4d';
                return;
            }
            customDrop = raw / 100;
        }

        btn.disabled = true;
        btn.textContent = 'Running…';
        status.textContent = 'Fetching portfolio data…';
        status.style.color = '#888';
        document.getElementById('results-panel').classList.add('d-none');

        fetch('/api/stress-test/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                account_id: accountId,
                scenario_id: selectedScenarioId,
                custom_drop: customDrop,
            }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            btn.disabled = false;
            btn.textContent = '▶ Run Stress Test';
            if (data.status !== 'success') {
                status.textContent = 'Error: ' + (data.message || 'Unknown error');
                status.style.color = '#ff4d4d';
                return;
            }
            status.textContent = 'Done.';
            status.style.color = '#00ffcc';
            renderResults(data.result);
        })
        .catch(function (e) {
            btn.disabled = false;
            btn.textContent = '▶ Run Stress Test';
            status.textContent = 'Network error — check server logs.';
            status.style.color = '#ff4d4d';
            console.error(e);
        });
    };

    function renderResults(r) {
        var panel = document.getElementById('results-panel');
        var sc    = r.scenario;
        var loss  = r.estimated_loss;
        var isGain = loss >= 0;

        var headlineEl = document.getElementById('result-loss-headline');
        headlineEl.textContent = (isGain ? '+' : '') + fmt(loss);
        headlineEl.className = 'stress-result-headline' + (isGain ? ' gain' : '');

        var pctEl = document.getElementById('result-loss-pct');
        pctEl.textContent = (isGain ? '+' : '') + r.estimated_loss_pct.toFixed(1) + '% of portfolio';
        pctEl.className = 'stress-result-pct' + (isGain ? ' gain' : '');

        document.getElementById('result-portfolio-value').textContent =
            fmt(r.portfolio_value) + ' ' + r.portfolio_currency;
        document.getElementById('result-scenario-name').textContent = sc.name || r.scenario_id;

        var recRow = document.getElementById('result-recovery-row');
        if (sc.recovery_months || sc.duration_days) {
            recRow.classList.remove('d-none');
            document.getElementById('result-recovery').textContent =
                sc.recovery_months ? sc.recovery_months + ' months (historical avg)' : 'unknown';
            document.getElementById('result-duration').textContent =
                sc.duration_days
                    ? sc.duration_days + ' trading days ('
                        + Math.round(sc.duration_days / 21) + ' months)'
                    : 'unknown';
        } else {
            recRow.classList.add('d-none');
        }

        var warnDiv  = document.getElementById('result-warnings');
        var warnList = document.getElementById('result-warnings-list');
        if (r.data_warnings && r.data_warnings.length > 0) {
            warnList.innerHTML = r.data_warnings.map(function (w) {
                return '<li>' + w + '</li>';
            }).join('');
            warnDiv.classList.remove('d-none');
        } else {
            warnDiv.classList.add('d-none');
        }

        renderSectorBars(r.sector_impact, r.portfolio_value);
        renderHoldingsTable(r.holdings, r.portfolio_currency);

        panel.classList.remove('d-none');
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function renderSectorBars(sectors, portfolioValue) {
        var container = document.getElementById('sector-bars');
        if (!sectors || sectors.length === 0) {
            container.innerHTML = '<span style="color:#555;font-size:12px;">No sector data available.</span>';
            return;
        }

        var maxAbs = Math.max.apply(null, sectors.map(function (s) { return Math.abs(s.estimated_loss); })) || 1;

        var html = '';
        sectors.forEach(function (s) {
            var isGain   = s.estimated_loss >= 0;
            var barPct   = Math.min(100, Math.abs(s.estimated_loss) / maxAbs * 100);
            var barColor = isGain ? '#4caf50' : '#ff4d4d';
            var lossStr  = (isGain ? '+' : '') + fmt(s.estimated_loss);
            var lossColor = isGain ? '#4caf50' : '#ff8888';
            var weightStr = (s.weight * 100).toFixed(1) + '%';

            html += '<div class="stress-sector-bar-wrap">'
                + '<div class="stress-sector-label">'
                + '<span style="color:#555;">' + weightStr + '</span> '
                + s.sector
                + '</div>'
                + '<div class="stress-sector-bar-track">'
                + '<div class="stress-sector-bar-fill" style="width:' + barPct + '%;background:' + barColor + ';"></div>'
                + '</div>'
                + '<div class="stress-sector-loss-amt" style="color:' + lossColor + ';">' + lossStr + '</div>'
                + '</div>';
        });
        container.innerHTML = html;
    }

    function renderHoldingsTable(holdings, currency) {
        var tbody = document.getElementById('holdings-tbody');
        if (!holdings || holdings.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;color:#555;">No holdings data.</td></tr>';
            return;
        }

        var html = '';
        holdings.forEach(function (h, idx) {
            var isGain = h.estimated_loss >= 0;
            var dropColor = h.estimated_drop_pct >= 0 ? '#4caf50'
                : h.estimated_drop_pct < -30 ? '#ff4d4d'
                : h.estimated_drop_pct < -15 ? '#ffaa00'
                : '#aaa';
            var lossColor = isGain ? '#4caf50'
                : idx < 3 ? '#ff4d4d'
                : '#ff8888';
            var rowBg = idx < 3 && !isGain ? 'background:rgba(255,77,77,.04);' : '';

            var multColor = h.sector_multiplier > 1.3 ? '#ff8888'
                : h.sector_multiplier > 1.0 ? '#ffaa00'
                : h.sector_multiplier < 0 ? '#4caf50'
                : '#888';

            html += '<tr style="border-bottom:1px solid #1a1a1a;' + rowBg + '" '
                + 'onmouseover="this.style.background=\'rgba(255,255,255,.03)\'" '
                + 'onmouseout="this.style.background=\'' + (idx < 3 && !isGain ? 'rgba(255,77,77,.04)' : '') + '\'">'
                + '<td style="padding:7px 10px;font-weight:700;">'
                + '<a href="/stock/' + h.symbol + '" style="color:#4da6ff;text-decoration:none;">' + h.symbol + '</a>'
                + '</td>'
                + '<td style="padding:7px 10px;color:#ccc;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + h.name + '</td>'
                + '<td style="text-align:right;padding:7px 8px;color:#888;font-family:monospace;">' + (h.weight * 100).toFixed(1) + '%</td>'
                + '<td style="text-align:right;padding:7px 8px;color:#aaa;font-family:monospace;">' + h.beta.toFixed(2) + '</td>'
                + '<td style="padding:7px 8px;color:#777;font-size:11px;">' + h.sector + '</td>'
                + '<td style="text-align:right;padding:7px 8px;font-family:monospace;color:' + multColor + ';">' + h.sector_multiplier.toFixed(2) + '×</td>'
                + '<td style="text-align:right;padding:7px 8px;font-family:monospace;color:' + dropColor + ';font-weight:700;">'
                + (h.estimated_drop_pct >= 0 ? '+' : '') + h.estimated_drop_pct.toFixed(1) + '%'
                + '</td>'
                + '<td style="text-align:right;padding:7px 10px;font-family:monospace;font-weight:700;color:' + lossColor + ';">'
                + (isGain ? '+' : '') + fmt(h.estimated_loss)
                + '</td>'
                + '</tr>';
        });
        tbody.innerHTML = html;
    }
})();

function _phiTierClass(tier) {
    if (tier === 'RED') return 'risk-summary-red';
    if (tier === 'YELLOW') return 'risk-summary-yellow';
    return 'risk-summary-green';
}

function _phiTierIcon(tier) {
    if (tier === 'RED') return '🚨';
    if (tier === 'YELLOW') return '⚠️';
    return '✅';
}

function _phiMetricClass(tier) {
    if (tier === 'RED') return 'metric-poor';
    if (tier === 'YELLOW') return 'metric-neutral';
    return 'metric-excellent';
}

function _phiRenderScopes(scopes) {
    const el = document.getElementById('phi-scopes');
    if (!scopes.length) {
        el.innerHTML = '<p class="text-muted">No Portfolio Heat Index data yet — run a scan to populate this page.</p>';
        return;
    }
    el.innerHTML = scopes.map(s => `
        <div class="sentiment-widget risk-summary-row ${_phiTierClass(s.tier)}">
            <h3 class="risk-summary-header ${s.tier.toLowerCase()}">${_phiTierIcon(s.tier)} ${escapeHtml(s.scope_label)}: ${s.phi_score}/100</h3>
            <p class="risk-summary-text">
                ${(s.breakdown || []).map(line => escapeHtml(line)).join('<br>')}
                <br><span class="text-muted text-xs">Last updated: ${escapeHtml(s.last_updated)}</span>
            </p>
        </div>
    `).join('');
}

function _phiRenderTickers(tickers) {
    const tbody = document.getElementById('phi-ticker-tbody');
    if (!tickers.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-muted">No per-ticker risk contributions yet.</td></tr>';
        return;
    }
    tbody.innerHTML = tickers.map(t => `
        <tr>
            <td><a href="/stock/${encodeURIComponent(t.ticker)}">${escapeHtml(t.ticker)}</a></td>
            <td class="${_phiMetricClass(t.risk_tier)}">${escapeHtml(t.risk_tier)}</td>
            <td>${t.risk_score}</td>
            <td>${t.marginal_var_contribution_pct != null ? t.marginal_var_contribution_pct + '%' : 'N/A'}</td>
            <td>${t.max_pairwise_correlation != null ? t.max_pairwise_correlation : 'N/A'}</td>
            <td>${t.stop_distance_pct != null ? t.stop_distance_pct + '%' : 'N/A'}</td>
        </tr>
    `).join('');
}

async function loadPortfolioHeatIndex() {
    try {
        const resp = await fetch('/api/risk-orchestrator/status');
        const data = await resp.json();
        if (data.status === 'success') {
            _phiRenderScopes(data.scopes || []);
            _phiRenderTickers(data.tickers || []);
        }
    } catch (e) {
        document.getElementById('phi-scopes').innerHTML = `<p class="msg-error">Failed to load: ${escapeHtml(e.message)}</p>`;
    }
}

async function runRiskOrchestratorScanNow() {
    const btn = document.getElementById('phi-run-now-btn');
    const statusEl = document.getElementById('phi-run-status');
    btn.disabled = true;
    btn.innerText = '⏳ Scanning...';
    try {
        const resp = await fetch('/api/risk-orchestrator/run', { method: 'POST' });
        const data = await resp.json();
        statusEl.textContent = data.message || '';
        setTimeout(loadPortfolioHeatIndex, 3000);
    } catch (e) {
        statusEl.textContent = `Failed: ${e.message}`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = '▶ Run Scan Now'; }, 3000);
    }
}

document.addEventListener('DOMContentLoaded', loadPortfolioHeatIndex);

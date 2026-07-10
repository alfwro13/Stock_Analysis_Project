(function () {
    const TICKER = window.INDEX_TICKER;
    const refreshRate = window.INDEX_REFRESH_RATE_MS || 60000;
    let interval;

    async function fetchPrice() {
        try {
            const res = await fetch('/api/market-pulse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tickers: [] })
            });
            const data = await res.json();
            if (!res.ok || data.status !== 'success') return;

            const all = [...(data.data.indexes || []), ...(data.data.assets || [])];
            const item = all.find(d => d.ticker === TICKER);
            if (!item) return;

            const priceEl = document.getElementById('pulse-price');
            const changeEl = document.getElementById('pulse-change');
            const changePtsEl = document.getElementById('pulse-change-pts');
            const trendClass = item.is_stale ? 'stale-text' : (item.is_positive ? 'trend-up' : 'trend-down');

            if (priceEl) {
                priceEl.innerText = Number(item.price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                priceEl.className = trendClass;
            }
            if (changeEl) {
                const sign = item.is_positive ? '+' : '';
                changeEl.innerText = `${sign}${Number(item.change_pct).toFixed(2)}%`;
                changeEl.className = trendClass;
            }
            if (changePtsEl) {
                const sign = item.change_pts >= 0 ? '+' : '';
                changePtsEl.innerText = `${sign}${Number(item.change_pts).toFixed(2)}`;
                changePtsEl.className = trendClass;
            }
        } catch (e) { }
    }

    document.addEventListener('DOMContentLoaded', function () {
        fetchPrice();
        interval = setInterval(fetchPrice, refreshRate);
    });

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            clearInterval(interval);
        } else {
            fetchPrice();
            interval = setInterval(fetchPrice, refreshRate);
        }
    });

    window.refreshIndexData = async function () {
        const btn = document.getElementById('refreshDataBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="btn-icon spin-icon">&#8635;</span> Crunching&hellip;';
        try {
            const response = await fetch('/api/index/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: TICKER })
            });
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                window.location.reload();
            } else {
                alert('Failed to refresh data: ' + (data.message || 'Unknown error'));
                btn.disabled = false;
                btn.innerHTML = '<span class="btn-icon">&#8635;</span> Refresh';
            }
        } catch (e) {
            alert('Network error refreshing data.');
            btn.disabled = false;
            btn.innerHTML = '<span class="btn-icon">&#8635;</span> Refresh';
        }
    };

    // Both charts are server-rendered (visuals.py's fig.to_html()), so config.responsive
    // never actually reacts to container size changes (rotation, fullscreen) — width/height
    // must be relayout'd explicitly, per AGENTS.md rule 18.
    const CHART_WRAPPER_IDS = ['intraday-wrapper', 'macro-wrapper'];
    const _chartDefaultHeights = {};

    function _captureChartDefaultHeights() {
        CHART_WRAPPER_IDS.forEach(function (id) {
            const wrapper = document.getElementById(id);
            const plotEl = wrapper && wrapper.querySelector('.js-plotly-plot');
            if (plotEl && plotEl.layout) _chartDefaultHeights[id] = plotEl.layout.height;
        });
    }

    function _indexChartOpts(wrapperId) {
        return { forceWidth: true, getHeight: function () { return _chartDefaultHeights[wrapperId]; } };
    }

    window.toggleFullscreen = function (wrapperId) {
        ChartFullscreen.toggle(wrapperId, _indexChartOpts(wrapperId));
    };

    document.addEventListener('DOMContentLoaded', _captureChartDefaultHeights);

    window.addEventListener('resize', function () {
        CHART_WRAPPER_IDS.forEach(function (id) {
            ChartFullscreen.relayoutForCurrentState(id, _indexChartOpts(id));
        });
    });
})();

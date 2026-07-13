let marketsPollInterval;
let marketsTickInterval;
let marketsLastFetchTime = null;
let marketsCurrentView = window.MARKETS_DEFAULT_VIEW || 'dynamic';
const MARKETS_REFRESH_RATE_MS = (window.REFRESH_RATE_MS || 60000);

const MARKETS_REGION_LABELS = {
    'US': 'United States',
    'Europe': 'Europe',
    'Asia': 'Asia-Pacific',
    'Commodities_FX': 'Commodities & FX',
};

const MARKETS_CURRENCY_PREFIX = { 'USD': '$', 'GBP': '£', 'EUR': '€' };

function marketsStateBadgeHTML(state) {
    const map = {
        open: ['markets-state-open', 'Open'],
        partial: ['markets-state-partial', 'Some Open'],
        pre: ['markets-state-pre', 'Pre-Market'],
        post: ['markets-state-post', 'Post-Market'],
        closed: ['markets-state-closed', 'Closed'],
    };
    const entry = map[state] || map.closed;
    return `<span class="sent-badge ${entry[0]}">${entry[1]}</span>`;
}

function marketsSentimentBadgeHTML(sentimentScore) {
    if (sentimentScore === undefined || sentimentScore === null) return '';
    const sScore = parseFloat(sentimentScore);
    let sClass, sText;
    if (sScore > 0.6) { sClass = 'sent-euphoria'; sText = 'Euphoria'; }
    else if (sScore > 0.2) { sClass = 'sent-bullish'; sText = 'Bullish'; }
    else if (sScore >= -0.2) { sClass = 'sent-neutral'; sText = 'Neutral'; }
    else if (sScore > -0.6) { sClass = 'sent-bearish'; sText = 'Bearish'; }
    else { sClass = 'sent-fear'; sText = 'Extreme Fear'; }
    return `<div class="mt-10"><span class="sent-badge ${sClass}">${sText} (${sScore.toFixed(3)})</span></div>`;
}

function marketsSparklineSVG(points, isPositive) {
    if (!points || points.length < 2) return '';
    const width = 100, height = 28;
    const prices = points.map(p => p[1]);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = (max - min) || 1;
    const stepX = width / (points.length - 1);
    const coords = points.map((p, i) => {
        const x = i * stepX;
        const y = height - ((p[1] - min) / range) * height;
        return x.toFixed(2) + ',' + y.toFixed(2);
    }).join(' ');
    const cls = isPositive ? 'positive' : 'negative';
    return `<svg class="macro-sparkline ${cls}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><polyline points="${coords}"></polyline></svg>`;
}

function marketsCardVisualState(isPositive, invertColor, isForex, isStale, marketOpen) {
    // marketOpen === null means "no exchange-session concept applies" (e.g. a futures contract,
    // which trades near-continuously) — color by data freshness only, never grey for "closed".
    if (marketOpen === false) return { cardClass: 'markets-closed-card', changeClass: '', stale: true };
    if (isStale) return { cardClass: 'markets-stale-data-card', changeClass: '', stale: true };
    if (invertColor) return { cardClass: isPositive ? 'negative' : 'positive', changeClass: isPositive ? 'negative' : 'positive', stale: false };
    if (isForex) return { cardClass: 'chart-wrapper-accent-cyan', changeClass: 'text-accent-cyan', stale: false };
    return { cardClass: isPositive ? 'positive' : 'negative', changeClass: isPositive ? 'positive' : 'negative', stale: false };
}

function marketsCardHTML(ticker, displayName, price, changePct, currency, sparkline, sentimentScore, badgeText, visual) {
    const sign = changePct >= 0 ? '+' : '';
    const prefix = MARKETS_CURRENCY_PREFIX[currency] || '';
    const formattedPrice = prefix + Number(price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const formattedChange = Number(changePct).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const staleText = visual.stale ? 'stale-text' : '';
    const badgeHTML = badgeText ? `<div class="mt-10"><span class="sent-badge sent-neutral">${badgeText}</span></div>` : '';
    const sentimentHTML = sentimentScore !== undefined ? marketsSentimentBadgeHTML(sentimentScore) : '';

    return `
        <a href="/index/${encodeURIComponent(ticker)}" class="macro-card-link" data-ticker="${ticker}">
            <div class="macro-card ${visual.cardClass}">
                <div class="macro-title ${staleText}">${displayName}</div>
                ${marketsSparklineSVG(sparkline, changePct >= 0)}
                <div class="macro-price ${staleText}">${formattedPrice}</div>
                <div class="macro-change ${visual.changeClass} ${staleText}">${sign}${formattedChange}%</div>
                ${badgeHTML}
                ${sentimentHTML}
            </div>
        </a>
    `;
}

function marketsTileHTML(tile) {
    const isForex = tile.asset_type === 'FX';

    if (tile.dual_instrument) {
        // Spot and future are always shown side by side (not auto-swapped into one tile) so it's
        // never ambiguous which instrument a price belongs to.
        const spot = tile.dual_instrument.spot;
        const future = tile.dual_instrument.future;
        const spotVisual = marketsCardVisualState(spot.is_positive, tile.invert_color, isForex, spot.is_stale, tile.market_state === 'open');
        const futureVisual = marketsCardVisualState(future.is_positive, tile.invert_color, isForex, future.is_stale, null);
        return (
            marketsCardHTML(spot.ticker, spot.display_name, spot.price, spot.change_pct, tile.currency, spot.sparkline, tile.sentiment_score, 'Index', spotVisual) +
            marketsCardHTML(future.ticker, future.display_name, future.price, future.change_pct, tile.currency, future.sparkline, undefined, 'Futures', futureVisual)
        );
    }

    const marketOpen = tile.market_state === 'open';
    const visual = marketsCardVisualState(tile.is_positive, tile.invert_color, isForex, tile.stale_data, marketOpen);
    return marketsCardHTML(tile.ticker, tile.display_name, tile.price, tile.change_pct, tile.currency, tile.sparkline, tile.sentiment_score, null, visual);
}

function marketsRegionSectionHTML(region) {
    const label = MARKETS_REGION_LABELS[region.region] || region.region;
    const tilesHTML = region.tiles.map(marketsTileHTML).join('');
    // Commodities & FX trade near-continuously with no single exchange session (see
    // markets_engine.get_region_state) — an Open/Closed badge there would misrepresent a
    // status that was never actually checked, so the badge is only shown for the three
    // regions whose state is derived from real exchange hours.
    const badgeHTML = region.region === 'Commodities_FX' ? '' : marketsStateBadgeHTML(region.state);
    return `
        <div class="markets-region-section">
            <div class="markets-region-header">
                <h3>${label}</h3>
                ${badgeHTML}
            </div>
            <div class="markets-tile-grid">${tilesHTML}</div>
        </div>
    `;
}

function formatMarketsAge(ms) {
    const totalSeconds = Math.max(Math.floor(ms / 1000), 0);
    if (totalSeconds < 60) return totalSeconds + 's ago';
    const minutes = Math.floor(totalSeconds / 60);
    if (minutes < 60) return minutes + 'm ago';
    const hours = Math.floor(minutes / 60);
    return hours + 'h ago';
}

function updateMarketsLastUpdatedText() {
    const el = document.getElementById('markets-last-updated');
    if (!el) return;
    el.innerText = marketsLastFetchTime === null ? '' : ('· Last updated ' + formatMarketsAge(Date.now() - marketsLastFetchTime));
}

async function fetchMarketsData() {
    try {
        const response = await fetch('/api/markets?view=' + encodeURIComponent(marketsCurrentView));
        const result = await response.json();

        const dot = document.getElementById('markets-pulse-dot');
        const text = document.getElementById('markets-pulse-text');

        if (response.ok && result.status === 'success') {
            const container = document.getElementById('markets-regions-container');
            if (container) {
                container.innerHTML = result.data.regions.map(marketsRegionSectionHTML).join('');
            }
            marketsLastFetchTime = Date.now();
            updateMarketsLastUpdatedText();
            const anyStale = result.data.regions.some(r => r.tiles.some(t => t.stale_data));
            if (dot && text) {
                dot.classList.remove('offline');
                if (anyStale) {
                    dot.classList.remove('pulse-dot-live');
                    dot.classList.add('pulse-dot-stale');
                    text.innerText = 'Market data refresh delayed. Retrying...';
                } else {
                    dot.classList.remove('pulse-dot-stale');
                    dot.classList.add('pulse-dot-live');
                    text.innerText = 'Live Markets Data Active';
                }
            }
        } else if (dot) {
            dot.classList.add('offline');
            if (text) text.innerText = 'Market data unavailable.';
        }
    } catch (error) {
        const dot = document.getElementById('markets-pulse-dot');
        if (dot) dot.classList.add('offline');
    }
}

function setMarketsView(view) {
    marketsCurrentView = view;
    document.cookie = 'markets_view=' + view + ';path=/;max-age=31536000';
    document.querySelectorAll('.change-period-btn[data-view]').forEach(function (btn) {
        const isActive = btn.dataset.view === view;
        btn.classList.toggle('btn-primary', isActive);
        btn.classList.toggle('btn-outline-secondary', !isActive);
    });
    fetchMarketsData();
}

document.addEventListener('DOMContentLoaded', function () {
    setMarketsView(marketsCurrentView);
    document.querySelectorAll('.change-period-btn[data-view]').forEach(function (btn) {
        btn.addEventListener('click', function () { setMarketsView(this.dataset.view); });
    });
    marketsPollInterval = setInterval(fetchMarketsData, MARKETS_REFRESH_RATE_MS);
    marketsTickInterval = setInterval(updateMarketsLastUpdatedText, 1000);
});

let marketPulseInterval;
let isFastPolling = false;
let fastPollDelayMs = 3000;
const REFRESH_RATE_MS = (window.REFRESH_RATE_MS || 60000);
const FAST_POLL_BASE_MS = 3000;

async function fetchMarketPulse() {
    try {
        let requestedTickers = [];
        if (window.ENABLE_LIVE_ASSETS) {
            const rows = document.querySelectorAll('.live-asset-row');
            rows.forEach(row => {
                const ticker = row.getAttribute('data-ticker');
                if (ticker) requestedTickers.push(ticker);
            });
        }

        const response = await fetch('/api/market-pulse', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ tickers: requestedTickers })
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            const indexes = result.data.indexes || [];
            const assets = result.data.assets || [];

            let anyStale = false;
            let pulseDot = null;
            let pulseText = null;

            if (window.SHOW_INDEXES) {
                renderMacroCards(indexes);
                pulseDot = document.getElementById('pulse-dot');
                pulseText = document.getElementById('pulse-text');
            }

            // Track if any element is stale to update UI status correctly
            indexes.forEach(idx => { if(idx.is_stale) anyStale = true; });
            assets.forEach(asset => { if(asset.is_stale) anyStale = true; });

            if (window.SHOW_INDEXES) {
                if (pulseDot && pulseText) {
                    pulseDot.classList.remove('offline');
                    if (anyStale) {
                        pulseText.innerText = "Market data refresh delayed. Retrying...";
                        pulseDot.classList.remove('pulse-dot-live');
                        pulseDot.classList.add('pulse-dot-stale');
                    } else {
                        pulseText.innerText = "Live Market Pulse Active";
                        pulseDot.classList.remove('pulse-dot-stale');
                        pulseDot.classList.add('pulse-dot-live');
                    }
                }
            }

            updateAssetPrices(assets);

            if (window._heatmapMode) {
                var panel = document.getElementById('heatmap-panel');
                if (panel) _buildHeatmap(panel);
            }

            // --- DYNAMIC POLLING LOGIC ---
            if (anyStale && !isFastPolling) {
                // Enter fast-polling mode while backend is fetching
                fastPollDelayMs = FAST_POLL_BASE_MS;
                clearInterval(marketPulseInterval);
                marketPulseInterval = setInterval(fetchMarketPulse, fastPollDelayMs);
                isFastPolling = true;
            } else if (anyStale && isFastPolling) {
                // Still stale — back off with doubling delay capped at REFRESH_RATE_MS
                fastPollDelayMs = Math.min(fastPollDelayMs * 2, REFRESH_RATE_MS);
                clearInterval(marketPulseInterval);
                marketPulseInterval = setInterval(fetchMarketPulse, fastPollDelayMs);
            } else if (!anyStale && isFastPolling) {
                // Background fetch completed — return to standard slow refresh rate
                clearInterval(marketPulseInterval);
                marketPulseInterval = setInterval(fetchMarketPulse, REFRESH_RATE_MS);
                isFastPolling = false;
                fastPollDelayMs = FAST_POLL_BASE_MS;
            }

        } else {
            handlePulseError();
        }
    } catch (error) {
        handlePulseError();
    }
}

function renderMacroCards(data) {
    const container = document.getElementById('macro-cards-container');
    if (!container) return;

    container.innerHTML = '';

    // On mobile, force layout via inline styles — CSS overrides of display:contents are
    // unreliable in Safari/WebKit. Mobile always shows a sub-filter of what desktop shows
    // (is_pulse_mobile rows only, first MARKET_PULSE_MOBILE_COUNT of them, server order
    // preserved) — never an independently-ranked list.
    const isMobile = window.innerWidth <= 768;
    const mobileCount = window.MARKET_PULSE_MOBILE_COUNT || 8;
    let mobileVisibleTickers = null;
    if (isMobile) {
        mobileVisibleTickers = new Set(
            data.filter(index => index.is_pulse_mobile !== false).slice(0, mobileCount).map(index => index.ticker)
        );
    }

    data.forEach(index => {
        const isPos = index.is_positive;
        let cardClass = '';
        let changeClass = '';
        const sign = isPos ? '+' : '';

        // Polarity: registry-driven invert_color (yields/dollar/oil — rising = risk-off) and
        // asset_type === 'FX' (neutral styling for currency pairs), replacing the old hardcoded
        // per-ticker arrays so a newly-added registry ticker gets correct styling automatically.
        const isForex = index.asset_type === 'FX';

        if (index.invert_color) {
            // Inverted polarity: Surging yields/dollar = Red (Danger), Dropping = Green (Safe)
            cardClass = isPos ? 'negative' : 'positive';
            changeClass = isPos ? 'negative' : 'positive';
        } else if (isForex) {
            // Neutral polarity for Forex
            cardClass = 'chart-wrapper-accent-cyan';
            changeClass = 'text-accent-cyan';
        } else {
            // Standard polarity for equities (Up = Green, Down = Red)
            cardClass = isPos ? 'positive' : 'negative';
            changeClass = isPos ? 'positive' : 'negative';
        }

        // Assign transparent gray classes if the DB data is older than refresh rate
        const staleClass = index.is_stale ? 'stale-card' : '';
        const staleText = index.is_stale ? 'stale-text' : '';

        // Map Sentiment Badges dynamically via JS
        let sentBadgeHTML = '';
        if (index.sentiment_score !== undefined && index.sentiment_score !== null) {
            let sScore = parseFloat(index.sentiment_score);
            let sClass = '';
            let sText = '';
            if (sScore > 0.6) { sClass = 'sent-euphoria'; sText = 'Euphoria'; }
            else if (sScore > 0.2) { sClass = 'sent-bullish'; sText = 'Bullish'; }
            else if (sScore >= -0.2) { sClass = 'sent-neutral'; sText = 'Neutral'; }
            else if (sScore > -0.6) { sClass = 'sent-bearish'; sText = 'Bearish'; }
            else { sClass = 'sent-fear'; sText = 'Extreme Fear'; }

            sentBadgeHTML = `<div class="mt-10"><span class="sent-badge ${sClass}">${sText} (${sScore.toFixed(3)})</span></div>`;
        }

        let formattedPrice = Number(index.price).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        if (index.asset_type === 'Commodity' && index.currency === 'USD') formattedPrice = '$' + formattedPrice;
        const formattedChange = Number(index.change_pct).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});

        let linkStyle = '';
        if (isMobile) {
            linkStyle = mobileVisibleTickers.has(index.ticker)
                ? 'display:flex;flex:0 0 calc(25% - 3px);max-width:calc(25% - 3px)'
                : 'display:none';
        }

        const cardHTML = `
            <a href="/index/${encodeURIComponent(index.ticker)}" class="macro-card-link" data-ticker="${index.ticker}"${linkStyle ? ` style="${linkStyle}"` : ''}>
                <div class="macro-card ${cardClass} ${staleClass}">
                    <div class="macro-title ${staleText}">${index.name}</div>
                    <div class="macro-price ${staleText}">${formattedPrice}</div>
                    <div class="macro-change ${changeClass} ${staleText}">
                        ${sign}${formattedChange}%
                    </div>
                    ${sentBadgeHTML}
                </div>
            </a>
        `;
        container.innerHTML += cardHTML;
    });
}

function updateAssetPrices(assets) {
    assets.forEach(asset => {
        const priceEl = document.getElementById('price-' + asset.ticker);
        const changeEl = document.getElementById('change-' + asset.ticker);

        if (priceEl && changeEl) {
            // Extract currency from the DOM row to format correctly
            const rowEl = priceEl.closest('.live-asset-row');
            const currencyCode = rowEl ? rowEl.getAttribute('data-currency') : 'USD';

            let numPrice = parseFloat(asset.price);
            let symbol = '$';

            // Live GBp (Pence) to GBP conversion
            if (currencyCode === 'GBp') {
                numPrice = numPrice / 100.0;
                symbol = '£';
            } else if (currencyCode === 'GBP') {
                symbol = '£';
            } else if (currencyCode === 'EUR') {
                symbol = '€';
            } else if (currencyCode && currencyCode !== 'USD') {
                symbol = '';
            }

            let formattedPrice = symbol + numPrice.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            if (currencyCode && !['USD', 'GBP', 'GBp', 'EUR'].includes(currencyCode)) {
                formattedPrice += ' ' + currencyCode;
            }

            priceEl.innerText = formattedPrice;

            const extEl = document.getElementById('extended-' + asset.ticker);
            if (extEl) {
                if (asset.extended_session && window.SHOW_EXTENDED_HOURS) {
                    let extPrice = parseFloat(asset.extended_price);
                    if (currencyCode === 'GBp') extPrice = extPrice / 100.0;
                    const extLabel = asset.extended_session === 'pre' ? 'Pre-Market' : 'After Hours';
                    const extChangePct = Number(asset.extended_change_pct);
                    const extSign = extChangePct >= 0 ? '+' : '';
                    extEl.innerText = '(' + extLabel + ': ' + symbol + extPrice.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ' + extSign + extChangePct.toFixed(2) + '%)';
                    extEl.classList.remove('d-none');
                } else {
                    extEl.innerText = '';
                    extEl.classList.add('d-none');
                }
            }

            // 1D change_pct/is_positive is always the value this poll response carries,
            // regardless of which Change Period button is currently selected on the
            // Portfolio page — keep it fresh on the row so switching back to 1D doesn't
            // need a fresh fetch.
            if (rowEl) {
                rowEl.setAttribute('data-live-price', asset.price);
                rowEl.setAttribute('data-day1-change-pct', asset.change_pct);
                rowEl.setAttribute('data-day1-is-positive', asset.is_positive ? '1' : '0');
            }

            const activePeriod = window.PORTFOLIO_CHANGE_PERIOD || window.WATCHLIST_CHANGE_PERIOD;
            let isPositive = asset.is_positive;

            if (rowEl && activePeriod && activePeriod !== '1d' && typeof window._applyChangeCell === 'function') {
                const pct = window._pctFromAnchor(asset.price, rowEl.dataset['close' + activePeriod]);
                isPositive = pct !== null && pct >= 0;
                window._applyChangeCell(rowEl, pct, isPositive, asset.is_stale);
            } else {
                // 1D and Stock Detail (no Change Period control there) — unchanged rendering.
                const formattedChange = Number(asset.change_pct).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
                const sign = asset.is_positive ? '+' : '';
                changeEl.innerText = `${sign}${formattedChange}%`;
                if (rowEl) rowEl.setAttribute('data-change-pct', asset.change_pct);
                changeEl.className = asset.is_stale ? 'stale-text' : (asset.is_positive ? 'trend-up' : 'trend-down');
            }

            priceEl.className = asset.is_stale ? 'stale-text' : (isPositive ? 'trend-up' : 'trend-down');

            if (rowEl && typeof window._updateRowPnl === 'function') {
                window._updateRowPnl(rowEl, asset.price);
            }
        }
    });

    if (typeof window._recomputePortfolioSummary === 'function') {
        window._recomputePortfolioSummary();
    }
}

function handlePulseError() {
    if (window.SHOW_INDEXES) {
        const pulseDot = document.getElementById('pulse-dot');
        if (pulseDot) {
            pulseDot.classList.add('offline');
            document.getElementById('pulse-text').innerText = "Market Pulse Offline (Retrying...)";
        }
    }
}

function startPulseEngine() {
    fetchMarketPulse();
    // Start with whichever interval state we are currently in
    marketPulseInterval = setInterval(fetchMarketPulse, isFastPolling ? fastPollDelayMs : REFRESH_RATE_MS);
}

function stopPulseEngine() {
    clearInterval(marketPulseInterval);
    if (window.SHOW_INDEXES) {
        const pulseDot = document.getElementById('pulse-dot');
        if (pulseDot) {
            pulseDot.classList.add('offline');
            document.getElementById('pulse-text').innerText = "Market Pulse Paused (Tab Inactive)";
        }
    }
}

document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        stopPulseEngine();
    } else {
        startPulseEngine();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    if (!document.hidden) {
        startPulseEngine();
    }
});

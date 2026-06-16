document.addEventListener("DOMContentLoaded", async function() {
    // Render the markdown briefing
    const rawMarkdown = window.MARKDOWN_CONTENT;
    document.getElementById('markdown-render-area').innerHTML = marked.parse(rawMarkdown);

    // Signal Explorer table
    let allRows = [];
    let sortCol = 'composite_score';
    let sortAsc = false;
    let currentPage = 1;
    const PAGE_SIZE = 10;

    const TOOLTIPS = {
        ticker:          null,
        company_name:    null,
        composite_score: 'Composite score from −100 to +100. Combines moving averages, RSI health, volume, candlestick patterns, VCP breakout, relative strength, and fundamentals. ≥40 = Strong Buy, ≥20 = Bullish, <0 = Caution.',
        overall_signal:  'Signal label derived from the composite score: Strong Buy / Bullish / Neutral / Bearish / Strong Sell / Toxic.',
        quality_grade:   'Fundamental quality grade. A = ROE >15%, low debt, fair PE/PEG. B = ROE >10%, manageable debt. C = Average or missing data. D = Loss-making (ROE <0%) or dangerously leveraged (D/E >2×). Avoid D-grade entries.',
        rsi_14:          'Relative Strength Index (14-day). Measures momentum on a 0–100 scale. Below 30 = oversold / potential reversal. Above 70 = overbought / distribution risk. 40–60 = healthy trend zone — ideal for long-term entries.',
        week52_pct:      'Position within the 52-week price range. 0% = at the 52-week low, 100% = at the 52-week high. Values above 70% indicate the stock is trading near its highs (breakout territory).',
        atr_pct:         'Average True Range as a % of price (14-day). Measures daily price volatility. <1.5% = calm/low-risk. 1.5–3% = normal. >3% = highly volatile — wider stop-losses required. Long-term entry setups target <2.5%.',
        earnings_days:   'Days until the next scheduled earnings release. Entering a long-term position just before earnings adds significant gap risk. Highlighted in orange when ≤7 days away.',
    };

    const COLS = [
        { key: 'ticker',          label: 'Ticker',    render: r => `<a class="se-ticker-link" href="/stock/${encodeURIComponent(r.ticker||'')}">${r.ticker||''}</a>` },
        { key: 'company_name',    label: 'Name',      render: r => `<span style="color:#bbb">${(r.company_name||'').slice(0,28)}</span>` },
        { key: 'composite_score', label: 'Score',     render: r => scoreBadge(r.composite_score) },
        { key: 'overall_signal',  label: 'Signal',    render: r => `<span style="color:#ccc;font-size:0.8rem">${r.overall_signal||'—'}</span>` },
        { key: 'quality_grade',   label: 'Quality',   render: r => qualBadge(r.quality_grade) },
        { key: 'rsi_14',          label: 'RSI',       render: r => fmt1(r.rsi_14) },
        { key: 'week52_pct',      label: '52W%',      render: r => r.week52_pct != null ? `${(r.week52_pct*100).toFixed(0)}%` : '—' },
        { key: 'atr_pct',         label: 'ATR%',      render: r => r.atr_pct != null ? `${(r.atr_pct*100).toFixed(1)}%` : '—' },
        { key: 'earnings_days',   label: 'Earnings',  render: r => earningsCell(r.earnings_days) },
    ];

    function scoreBadge(v) {
        if (v == null) return '<span style="color:#666">—</span>';
        const cls = v >= 40 ? 'se-score-strongbuy' : v >= 20 ? 'se-score-bullish' : v >= 0 ? 'se-score-neutral' : v >= -30 ? 'se-score-bearish' : 'se-score-sell';
        return `<span class="se-badge ${cls}">${v}</span>`;
    }
    function qualBadge(g) {
        if (!g) return '—';
        const cls = {A:'se-qual-a',B:'se-qual-b',C:'se-qual-c',D:'se-qual-d'}[g] || 'se-qual-c';
        return `<span class="se-badge ${cls}">${g}</span>`;
    }
    function earningsCell(d) {
        if (d == null) return '<span style="color:#555">—</span>';
        return `<span ${d <= 7 ? 'class="se-earnings-warn"' : 'style="color:#ccc"'}>${d}d</span>`;
    }
    function fmt1(v) { return v != null ? parseFloat(v).toFixed(1) : '—'; }
    function qualOrder(g) { return {A:0,B:1,C:2,D:3}[g] ?? 4; }

    function sortValue(row, col) {
        const v = row[col];
        if (col === 'quality_grade') return qualOrder(v);
        if (typeof v === 'string') return v.toLowerCase();
        return v ?? -Infinity;
    }

    function applyFilters() {
        const text  = document.getElementById('se-text-filter').value.toLowerCase();
        const minSc = parseFloat(document.getElementById('se-score-filter').value);
        return allRows.filter(r => {
            if (text && !(r.ticker||'').toLowerCase().includes(text) && !(r.company_name||'').toLowerCase().includes(text)) return false;
            if (!isNaN(minSc) && (r.composite_score == null || r.composite_score < minSc)) return false;
            return true;
        });
    }

    function renderTable() {
        const filtered = applyFilters();
        filtered.sort((a, b) => {
            const va = sortValue(a, sortCol), vb = sortValue(b, sortCol);
            if (va < vb) return sortAsc ? -1 : 1;
            if (va > vb) return sortAsc ? 1 : -1;
            return 0;
        });

        const container = document.getElementById('se-table-container');
        if (!filtered.length) { container.innerHTML = '<p style="color:#888;padding:1rem">No rows match the current filters.</p>'; return; }

        const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
        if (currentPage > totalPages) currentPage = totalPages;
        const pageRows = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

        const table = document.createElement('table');
        const thead = document.createElement('thead');
        const hrow  = document.createElement('tr');
        COLS.forEach(col => {
            const th = document.createElement('th');
            th.textContent = col.label;
            if (col.key === sortCol) th.innerHTML += ` <span id="se-sort-indicator">${sortAsc ? '▲' : '▼'}</span>`;
            if (TOOLTIPS[col.key]) th.dataset.tip = TOOLTIPS[col.key];
            th.addEventListener('click', () => {
                if (sortCol === col.key) sortAsc = !sortAsc;
                else { sortCol = col.key; sortAsc = col.key !== 'composite_score'; }
                currentPage = 1;
                renderTable();
            });
            hrow.appendChild(th);
        });
        thead.appendChild(hrow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        pageRows.forEach(row => {
            const tr = document.createElement('tr');
            COLS.forEach(col => {
                const td = document.createElement('td');
                td.innerHTML = col.render(row);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);

        const start = (currentPage - 1) * PAGE_SIZE + 1;
        const end   = Math.min(currentPage * PAGE_SIZE, filtered.length);
        const summary = document.createElement('p');
        summary.style.cssText = 'color:#888;font-size:0.8rem;margin-bottom:0.5rem';
        summary.textContent = `Showing ${start}–${end} of ${filtered.length} signals (${allRows.length} total)`;

        const pagebar = document.createElement('div');
        pagebar.className = 'se-pagebar';
        const btnPrev = document.createElement('button');
        btnPrev.textContent = '← Prev';
        btnPrev.disabled = currentPage === 1;
        btnPrev.addEventListener('click', () => { currentPage--; renderTable(); });

        const pageInfo = document.createElement('span');
        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;

        const btnNext = document.createElement('button');
        btnNext.textContent = 'Next →';
        btnNext.disabled = currentPage === totalPages;
        btnNext.addEventListener('click', () => { currentPage++; renderTable(); });

        pagebar.appendChild(btnPrev);
        pagebar.appendChild(pageInfo);
        pagebar.appendChild(btnNext);

        container.innerHTML = '';
        container.appendChild(summary);
        container.appendChild(table);
        container.appendChild(pagebar);
    }

    document.getElementById('se-text-filter').addEventListener('input', () => { currentPage = 1; renderTable(); });
    document.getElementById('se-score-filter').addEventListener('input', () => { currentPage = 1; renderTable(); });
    document.getElementById('se-reset').addEventListener('click', () => {
        document.getElementById('se-text-filter').value = '';
        document.getElementById('se-score-filter').value = '';
        currentPage = 1;
        renderTable();
    });

    try {
        const resp = await fetch('/api/screener-data');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const json = await resp.json();
        allRows = json.data || [];
        renderTable();
    } catch (e) {
        document.getElementById('se-table-container').innerHTML = `<p style="color:#e57373">Failed to load signal data: ${e.message}</p>`;
    }
});

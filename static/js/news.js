let allArticles = [];
let currentSource = 'portfolio';
let selectedId = null;

const FS_MIN = 1, FS_MAX = 7, FS_DEFAULT = 3;

function relTime(ts) {
    const diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 60)    return `${diff}s ago`;
    if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

function sourceBadge(src) {
    const label = {portfolio: 'Portfolio', watchlist: 'Watchlist', both: 'Both'};
    return `<span class="art-source-badge art-source-${src}">${label[src] || src}</span>`;
}

function sentimentBadge(label, score) {
    if (!label) return '';
    const icon = label === 'positive' ? '▲' : label === 'negative' ? '▼' : '●';
    const pct = score != null ? ` ${Math.round(Math.abs(score) * 100)}%` : '';
    return `<span class="sent-badge sent-${label}" title="Sentiment: ${label}${pct}">${icon}${pct}</span>`;
}

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function looksLikeHeading(text) {
    const t = text.trim();
    if (t.length < 5 || t.length > 80) return false;
    if (/[.,;!?]$/.test(t)) return false;
    const words = t.split(/\s+/);
    if (words.length > 10) return false;
    const capWords = words.filter(w => /^[A-Z0-9]/.test(w)).length;
    return capWords / words.length >= 0.7;
}

function formatArticleBody(text, ticker) {
    if (!text) return '';
    const blocks = text.replace(/\r\n/g, '\n').split(/\n+/);
    let tickerRe = null;
    if (ticker) {
        const esc = ticker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        tickerRe = new RegExp('(?<![A-Za-z])' + esc + '(?![A-Za-z])', 'g');
    }
    return blocks.map(block => {
        block = block.trim();
        if (!block) return '';
        const safe = escHtml(block);
        const lit = tickerRe ? safe.replace(tickerRe, m => `<span class="reader-ticker">${m}</span>`) : safe;
        if (looksLikeHeading(block)) {
            return `<h3 class="reader-h3">${lit}</h3>`;
        }
        return `<p class="reader-p">${lit.replace(/\n/g, '<br>')}</p>`;
    }).join('');
}

function applyFontSize(fs) {
    document.getElementById('articleReader').dataset.fs = fs;
    document.getElementById('fontDecBtn').disabled = (fs <= FS_MIN);
    document.getElementById('fontIncBtn').disabled = (fs >= FS_MAX);
}

function changeFontSize(delta) {
    const current = parseInt(document.getElementById('articleReader').dataset.fs || FS_DEFAULT, 10);
    const next = Math.min(FS_MAX, Math.max(FS_MIN, current + delta));
    localStorage.setItem('news-reader-fs', next);
    applyFontSize(next);
}

function renderList(articles) {
    const inner = document.getElementById('articleListInner');
    const empty = document.getElementById('noArticles');
    if (!articles.length) {
        inner.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    inner.innerHTML = articles.map(a => `
        <div class="article-item${a.id === selectedId ? ' selected' : ''}"
             id="art-${a.id}" onclick="selectArticle(${a.id})">
            <div class="art-headline">${escHtml(a.headline)}</div>
            <div class="art-meta">
                <span class="art-ticker-badge">${escHtml(a.ticker)}</span>
                ${sourceBadge(a.source_list)}
                ${sentimentBadge(a.sentiment_label, a.sentiment_score)}
                <span>${escHtml(a.publisher || '')}</span>
                <span>${relTime(a.published_at)}</span>
            </div>
        </div>
    `).join('');
}

function filterArticles() {
    const q = document.getElementById('newsSearchInput').value.toLowerCase();
    const filtered = allArticles.filter(a => {
        const matchSrc = currentSource === 'all' || a.source_list === currentSource || a.source_list === 'both';
        const matchQ = !q
            || a.headline.toLowerCase().includes(q)
            || (a.ticker || '').toLowerCase().includes(q)
            || (a.company_name || '').toLowerCase().includes(q)
            || (a.publisher || '').toLowerCase().includes(q);
        return matchSrc && matchQ;
    });
    renderList(filtered);
}

function onSearch() {
    const q = document.getElementById('newsSearchInput').value;
    document.getElementById('searchClearBtn').style.display = q ? 'block' : 'none';
    filterArticles();
}

function clearSearch() {
    const input = document.getElementById('newsSearchInput');
    input.value = '';
    document.getElementById('searchClearBtn').style.display = 'none';
    filterArticles();
    input.focus();
}

function setSource(src) {
    currentSource = src;
    document.querySelectorAll('.btn-filter').forEach(b => b.classList.toggle('active', b.dataset.source === src));
    filterArticles();
}

function selectArticle(id) {
    selectedId = id;
    const a = allArticles.find(x => x.id === id);
    if (!a) return;

    document.querySelectorAll('.article-item').forEach(el => el.classList.remove('selected'));
    const item = document.getElementById('art-' + id);
    if (item) item.classList.add('selected');

    document.getElementById('readerPlaceholder').style.display = 'none';
    const reader = document.getElementById('readerContent');
    reader.style.display = 'block';

    document.getElementById('articleReader').scrollTop = 0;

    const pubDate = new Date(a.published_at * 1000).toLocaleString();
    const bodyHtml = a.body_fetched && a.full_text
        ? `<div class="reader-body">${formatArticleBody(a.full_text, a.ticker)}</div>`
        : `<div class="reader-body summary-fallback">${formatArticleBody(a.summary || 'No summary available.', a.ticker)}<p class="reader-p"><em style="font-size:0.85em;color:#666;">(Full text unavailable — open original for the complete article)</em></p></div>`;

    const headlineHtml = a.url
        ? `<a href="${escHtml(a.url)}" target="_blank" rel="noopener" class="reader-headline-link">${escHtml(a.headline)}</a>`
        : escHtml(a.headline);
    reader.innerHTML = `
        <h2>${headlineHtml}</h2>
        <div class="reader-meta">
            <span class="art-ticker-badge">${escHtml(a.ticker)}${a.company_name ? ' · ' + escHtml(a.company_name) : ''}</span>
            ${sourceBadge(a.source_list)}
            ${sentimentBadge(a.sentiment_label, a.sentiment_score)}
            <span>${escHtml(a.publisher || '')}</span>
            <span>${pubDate}</span>
        </div>
        ${bodyHtml}
    `;
}

async function loadArticles() {
    try {
        const resp = await fetch('/api/news-feed?source=all&limit=200');
        const data = await resp.json();
        allArticles = data.articles || [];
        document.getElementById('lastUpdated').textContent =
            `${allArticles.length} articles · ${new Date().toLocaleTimeString()}`;
        filterArticles();
    } catch (e) {
        console.error('Failed to load news feed:', e);
    }
}

async function fetchNow() {
    const btn = document.getElementById('fetchNowBtn');
    btn.disabled = true;
    btn.textContent = '⏳ Fetching...';
    try {
        await fetch('/api/news-feed/run-now', { method: 'POST' });
        btn.textContent = '✓ Queued';
        setTimeout(() => {
            btn.textContent = '▶ Fetch Now';
            btn.disabled = false;
            loadArticles();
        }, 3000);
    } catch (e) {
        btn.textContent = '✗ Error';
        setTimeout(() => { btn.textContent = '▶ Fetch Now'; btn.disabled = false; }, 3000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const stored = parseInt(localStorage.getItem('news-reader-fs'), 10);
    const fs = (stored >= FS_MIN && stored <= FS_MAX) ? stored : FS_DEFAULT;
    applyFontSize(fs);
    loadArticles();
});

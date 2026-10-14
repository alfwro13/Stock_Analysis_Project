"""
tests/test_front_end_escaping.py  ── FRONT-END ESCAPING AND LINK-SCHEME CHECKS

Third-party text (screener names, sectors, news fields) and operator-entered names
(accounts, market registry, workflow nodes) must reach the DOM entity-escaped, and
Yahoo/news links must be http(s) only (SEC 11/27 S5-2, SEC 20/27 S5-8 and S5-9).
The JS checks execute the shipped static/js files in Node and skip when it is missing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


_S5_2_PAGES = [
    "/market-screener", "/quality-compounders", "/garp-tenbaggers", "/quality-on-sale",
    "/sector-trends", "/relative-strength-leaders", "/bubble-radar", "/forensic-screener",
    "/stress-test", "/news",
]


def _s5_2_run_node(script, *args):
    import shutil
    import subprocess

    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for the front-end escaping checks')
    result = subprocess.run([node, '-e', script, *args], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def _s5_2_js(name):
    return str(Path(__file__).resolve().parents[1] / 'static/js' / name)


def test_utils_js_is_loaded_by_base_template_only():
    templates = Path(__file__).resolve().parents[1] / 'templates'
    assert (templates / 'base.html').read_text().count('/static/js/utils.js') == 1
    duplicates = [
        str(p.relative_to(templates)) for p in templates.rglob('*.html')
        if p.name != 'base.html' and '/static/js/utils.js' in p.read_text()
    ]
    assert duplicates == []


@pytest.mark.pages
@pytest.mark.parametrize("path", _S5_2_PAGES)
def test_pages_load_utils_js_once_before_their_own_script(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.text.count('/static/js/utils.js') == 1


def test_news_js_uses_the_shared_escape_helper():
    source = (Path(__file__).resolve().parents[1] / 'static/js/news.js').read_text()
    assert 'escHtml' not in source
    assert 'escapeHtml(' in source


_S5_2_DATATABLE_SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const spec = JSON.parse(process.argv[1]);
const payload = '<img src=x onerror=alert(1)>';
class Option { constructor(text, value) { this.text = text; this.value = value; } }
const appended = [];
let tableConfig = null;
function chain() {
    return new Proxy(function () {}, {
        get(_, key) {
            if (key === 'DataTable') return cfg => { tableConfig = cfg; return chain(); };
            if (key === 'append') return (...args) => { appended.push(...args); return chain(); };
            if (key === 'ready') return fn => { fn(); return chain(); };
            return () => chain();
        },
        apply() { return chain(); },
    });
}
const $ = function () { return chain(); };
$.fn = {dataTable: {ext: {search: [], errMode: ''}, render: {number: () => () => ''}}};
const context = {
    $, Option, URLSearchParams, document: {}, window: {location: {search: ''}},
};
vm.createContext(context);
for (const file of process.argv.slice(2)) vm.runInContext(fs.readFileSync(file, 'utf8'), context);
assert.ok(tableConfig, 'DataTable was not constructed');
const col = name => {
    const c = tableConfig.columns.find(x => x.data === name);
    assert.ok(c && c.render, 'missing render for ' + name);
    return c;
};
for (const name of spec.text) {
    const shown = col(name).render(payload, 'display', {});
    assert.ok(!shown.includes('<img'), name + ': ' + shown);
    assert.ok(shown.includes('&lt;img'), name + ': ' + shown);
    assert.equal(col(name).render(payload, 'sort', {}), payload, name + ' sort value');
    assert.equal(col(name).render(payload, 'filter', {}), payload, name + ' filter value');
}
for (const name of spec.links) {
    const shown = col(name).render(payload, 'display', {sector: payload});
    assert.ok(!shown.includes('<img'), name + ': ' + shown);
    assert.ok(shown.includes('&lt;img'), name + ': ' + shown);
}
for (const name of spec.currency) {
    const shown = col(name).render(1.5, 'display', {currency: payload});
    assert.ok(!shown.includes('<img'), name + ': ' + shown);
    assert.equal(col(name).render(1.5, 'sort', {currency: payload}), 1.5);
}
for (const name of spec.rowSector) {
    const shown = col(name).render(1.5, 'display', {sector: payload});
    assert.ok(!shown.includes('<img'), name + ': ' + shown);
}
if (spec.url) {
    const shown = col('freetrade_url').render('https://x" onmouseover="alert(1)', 'display', {});
    assert.ok(!shown.includes('" onmouseover'), shown);
    assert.ok(shown.includes('&quot;'), shown);
}
const values = [payload, 'Technology'];
const each = cb => values.forEach(cb);
const api = {column: () => ({data: () => ({unique: () => ({sort: () => ({each})})})})};
tableConfig.initComplete.call({api: () => api});
assert.ok(appended.some(a => a instanceof Option && a.text === payload && a.value === payload));
assert.ok(!appended.some(a => typeof a === 'string' && a.includes(payload)));
"""


@pytest.mark.parametrize("script_name,spec", [
    ("market_screener.js", {
        "text": ["company_name", "freetrade_subtitle", "sector", "exchange"],
        "links": ["ticker"], "currency": ["close_price", "sma_50", "sma_200"],
        "rowSector": [], "url": True,
    }),
    ("quality_compounders.js", {
        "text": ["company_name", "sector"], "links": ["ticker"],
        "currency": ["close_price"], "rowSector": [], "url": False,
    }),
    ("quality_on_sale.js", {
        "text": ["company_name", "sector"], "links": ["ticker"],
        "currency": ["close_price"], "rowSector": [], "url": False,
    }),
    ("garp_tenbaggers.js", {
        "text": ["company_name", "sector"], "links": ["ticker"],
        "currency": ["close_price"], "rowSector": [], "url": False,
    }),
    ("relative_strength_leaders.js", {
        "text": ["company_name", "sector", "country"], "links": ["ticker"],
        "currency": ["close_price"], "rowSector": [], "url": False,
    }),
    ("sector_trends.js", {
        "text": [], "links": ["sector"], "currency": [],
        "rowSector": ["pct_above_50d", "pct_bullish_cross"], "url": False,
    }),
])
def test_screener_datatables_escape_third_party_text_for_display_only(script_name, spec):
    import json

    _s5_2_run_node(
        _S5_2_DATATABLE_SCRIPT, json.dumps(spec), _s5_2_js('utils.js'), _s5_2_js(script_name),
    )


_S5_2_DOM_PRELUDE = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const payload = '<img src=x onerror=alert(1)>';
const els = {};
const listeners = {};
function el(id) {
    if (!els[id]) {
        const handlers = {};
        els[id] = {
            id, innerHTML: '', textContent: '', value: 'all', disabled: false, style: {}, dataset: {}, handlers,
            classList: {add() {}, remove() {}, toggle() {}},
            addEventListener(type, fn) { handlers[type] = fn; },
            scrollIntoView() {},
        };
    }
    return els[id];
}
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
function load(context, files) {
    vm.createContext(context);
    for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context);
}
"""

_S5_2_FORENSIC_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const context = {
    window: {FORENSIC_CFG: {}}, alert() {}, setTimeout, console,
    document: {
        getElementById: el,
        addEventListener(type, fn) { listeners[type] = fn; },
    },
    fetch: async () => ({json: async () => ({results: [{
        ticker: payload, company_name: payload, sector: payload, source: 'portfolio',
        forensic_last_updated: payload, piotroski_f_score: 5, altman_z_score: 2.0, beneish_m_score: -2.0,
    }]})}),
};
load(context, process.argv.slice(1));
(async () => {
    listeners.DOMContentLoaded();
    await settle();
    const html = els['forensic-tbody'].innerHTML;
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), html);
    assert.ok(!html.includes('<img'), html);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""

_S5_2_BUBBLE_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const fetched = [];
const context = {
    window: {BUBBLE_RADAR_CFG: {watchThreshold: 30, flagThreshold: 60}}, setTimeout,
    document: {
        getElementById: el, querySelectorAll: () => [],
        addEventListener(type, fn) { listeners[type] = fn; },
    },
    fetch: async url => {
        fetched.push(url);
        const body = url.endsWith('/data')
            ? {results: [{ticker: payload, company_name: payload, flag: 'bubble', bubble_score: 70,
                sma_ext_pct: 1, rsi_avg_20d: 1, ps_ratio: 1, peg_ratio: 1, scan_date: payload}]}
            : {result: {flag: 'watch', bubble_score: 50, scan_date: payload,
                metric_scores: {a: {label: payload, value: 1, score: 2}}}};
        return {json: async () => body};
    },
};
load(context, process.argv.slice(1));
(async () => {
    listeners.DOMContentLoaded();
    await settle();
    const rows = els['br-flagged-body'].innerHTML;
    assert.ok(!rows.includes('<img'), rows);
    assert.ok(!rows.includes('onclick'), rows);
    assert.ok(rows.includes('&lt;img src=x onerror=alert(1)&gt;'), rows);
    els['br-flagged-body'].handlers.click({target: {closest: () => ({dataset: {ticker: payload}})}});
    await settle();
    assert.equal(fetched[fetched.length - 1], '/api/bubble-radar/ticker/' + encodeURIComponent(payload));
    const detail = els['br-detail-body'].innerHTML;
    assert.ok(!detail.includes('<img'), detail);
    assert.ok(detail.includes('&lt;img'), detail);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""

_S5_2_STRESS_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const context = {
    window: {STRESS_BASE_CURRENCY: 'GBP'}, console,
    document: {getElementById: el, querySelectorAll: () => []},
    fetch: async () => ({json: async () => ({status: 'success', result: {
        scenario: {name: 'GFC'}, scenario_id: 'gfc_2008', estimated_loss: -100, estimated_loss_pct: -5,
        portfolio_value: 2000, portfolio_currency: 'GBP', data_warnings: [payload],
        sector_impact: [{sector: payload, estimated_loss: -50, weight: 0.5}],
        holdings: [{symbol: payload, name: payload, sector: payload, weight: 0.5, beta: 1,
            sector_multiplier: 1.2, estimated_drop_pct: -10, estimated_loss: -50}],
    }})}),
};
load(context, process.argv.slice(1));
(async () => {
    context.window.runStressTest();
    await settle();
    for (const id of ['result-warnings-list', 'sector-bars', 'holdings-tbody']) {
        const html = els[id].innerHTML;
        assert.ok(html.length > 0, id);
        assert.ok(!html.includes('<img'), id + ': ' + html);
        assert.ok(html.includes('&lt;img'), id + ': ' + html);
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


@pytest.mark.parametrize("script,file", [
    (_S5_2_FORENSIC_SCRIPT, 'forensic_screener.js'),
    (_S5_2_BUBBLE_SCRIPT, 'bubble_radar.js'),
    (_S5_2_STRESS_SCRIPT, 'stress_test.js'),
], ids=['forensic_screener', 'bubble_radar', 'stress_test'])
def test_dom_built_screener_tables_escape_third_party_text(script, file):
    _s5_2_run_node(script, _s5_2_js('utils.js'), _s5_2_js(file))


def test_format_currency_never_echoes_a_malformed_currency_code():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const payload = '<img src=x onerror=alert(1)>';
assert.equal(context.formatCurrency(1234.5, payload), '1,234.50');
assert.equal(context.formatCurrency(1234.5, 'USD'), '$1,234.50');
assert.equal(context.formatCurrency(12345, 'GBp'), '\u00a3123.45');
assert.equal(context.formatCurrency(1234.5, ''), '1,234.50');
assert.equal(context.formatCurrency(null, payload), '\u2014');
"""
    _s5_2_run_node(script, _s5_2_js('utils.js'))


_S5_8_UTILS_SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {URL};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
assert.equal(context.safeHttpUrl('https://example.com/a'), 'https://example.com/a');
assert.equal(context.safeHttpUrl(' http://example.com '), 'http://example.com/');
for (const bad of [
    'javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', '\x01javascript:alert(1)',
    'data:text/html,x', 'vbscript:x', '/relative', '//example.com', 'example.com', '', null, undefined,
]) {
    assert.equal(context.safeHttpUrl(bad), '', JSON.stringify(bad));
}
"""


def test_safe_http_url_js_keeps_only_absolute_http_links():
    _s5_2_run_node(_S5_8_UTILS_SCRIPT, _s5_2_js('utils.js'))


@pytest.mark.parametrize("website,expected", [
    ("https://www.example.com", "https://www.example.com"),
    ("http://example.com/about", "http://example.com/about"),
    ("javascript:alert(1)", None),
    ("JAVASCRIPT:alert(1)", None),
    ("data:text/html,<script>alert(1)</script>", None),
    ("//example.com", None),
    ("example.com", None),
    ("https://", None),
    (None, None),
])
def test_fundamentals_extra_website_is_a_web_link_or_none(tmp_path, monkeypatch, website, expected):
    import json

    import page_helpers

    (tmp_path / "ZZWEB.json").write_text(json.dumps({"website": website}))
    monkeypatch.setattr(page_helpers, "FUNDAMENTALS_DIR", tmp_path)
    assert page_helpers._load_fundamentals_extra("ZZWEB")["website"] == expected


_S5_8_NEWS_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const context = {
    window: {}, URL, console,
    document: {
        getElementById: el, querySelectorAll: () => [],
        addEventListener(type, fn) { listeners[type] = fn; },
    },
};
load(context, process.argv.slice(1));
const show = (url, extra = {}) => {
    vm.runInContext('allArticles = ' + JSON.stringify([{
        id: 1, url, headline: 'Headline', ticker: 'AAA', source_list: 'portfolio',
        sentiment_label: 'positive', sentiment_score: 0.5, publisher: 'P', published_at: 1,
        summary: 'S', body_fetched: 0, ...extra,
    }]), context);
    context.selectArticle(1);
    return els.readerContent.innerHTML;
};
assert.ok(show('https://example.com/a').includes('href="https://example.com/a"'));
for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,x', '/relative', '']) {
    const html = show(bad);
    assert.ok(!html.includes('<a '), bad + ': ' + html);
    assert.ok(html.includes('Headline'), html);
}
const html = show('https://example.com/', {source_list: payload, sentiment_label: payload});
assert.ok(!html.includes('<img'), html);
assert.ok(html.includes('&lt;img'), html);
"""


def test_news_reader_link_needs_http_scheme_and_escapes_badges():
    _s5_2_run_node(_S5_8_NEWS_SCRIPT, _s5_2_js('utils.js'), _s5_2_js('news.js'))


_S5_9_WORKFLOW_MONITOR_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const hostile = '</script><script>alert(1)</script><!--';
const docs = [];
const context = {
    window: {open: () => ({document: {
        write(text) { docs.push(text); }, open() {}, close() {}, body: null,
    }})},
    console, URL, location: {origin: 'https://dash.example'},
    fetch: async () => ({json: async () => ({status: 'success', conflicts: [], edges: [], nodes: [
        {id: 'job_1', label: hostile + payload, status: 'green'},
    ]})}),
    mermaid: {initialize() {}, render: async () => ({svg: '<svg></svg>'})},
    document: {getElementById: el, addEventListener() {}},
};
load(context, process.argv.slice(1));
(async () => {
    context._wfRenderConflicts([{severity: payload, type: payload + '_x', message: payload + ' failed'}]);
    const conflicts = els['workflow-conflicts'].innerHTML;
    assert.ok(!conflicts.includes('<img'), conflicts);
    assert.ok(conflicts.includes('&lt;img'), conflicts);

    const label = context._wfSanitizeLabel(payload + ' Fund #1; A&B');
    assert.ok(!/[<>&]/.test(label), label);
    assert.ok(!label.includes('#1;'), label);

    await context.openWorkflowGraphTab();
    const doc = docs.find(text => text.includes('var _wfNodes'));
    assert.ok(doc, 'graph tab document was not written');
    assert.ok(!doc.includes('alert(1)</script>'), 'node data closed the inline script');
    assert.ok(!doc.includes('<!--<'), 'node data opened an HTML comment');
    assert.ok(doc.includes('\\u003c/script>'), 'node data was not unicode-escaped');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""

_S5_9_WORKFLOW_TAB_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const opened = [];
const anchor = "x'\"><img src=x>";
const context = {
    addEventListener() {}, open: (...args) => opened.push(args),
    _wfNodes: [{
        id: 'n1', label: payload, status: 'green', status_reason: payload, category: payload,
        engine: payload, last_run: payload, next_run: payload, avg_duration_sec: 5,
        produces: [payload], consumes: [payload], settings_anchor: anchor,
    }],
    setTimeout, clearTimeout, console,
    document: {getElementById: el},
};
context.window = context;
load(context, process.argv.slice(1));
(async () => {
    els['wf-svg-wrap'].handlers.click({target: {closest: () => ({id: 'flowchart-n1-5'})}});
    await new Promise(resolve => setTimeout(resolve, 300));
    const html = els['wf-pnl-content'].innerHTML;
    assert.ok(!html.includes('<img'), html);
    assert.ok(html.includes('&lt;img'), html);
    assert.ok(!html.includes('onclick'), html);
    assert.ok(html.includes('data-anchor="x&#39;&quot;&gt;&lt;img src=x&gt;"'), html);
    els['wf-pnl-content'].handlers.click({target: {closest: () => ({dataset: {anchor: 'abc'}})}});
    assert.deepEqual(opened, [['/settings#abc', '_blank']]);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


def test_workflow_monitor_escapes_account_names_in_labels_conflicts_and_graph_tab():
    _s5_2_run_node(_S5_9_WORKFLOW_MONITOR_SCRIPT, _s5_2_js('utils.js'), _s5_2_js('workflow_monitor.js'))


def test_workflow_tab_panel_escapes_node_text_and_has_no_inline_handler():
    _s5_2_run_node(_S5_9_WORKFLOW_TAB_SCRIPT, _s5_2_js('utils.js'), _s5_2_js('workflow_tab.js'))


_S5_9_ACCOUNT_TILES_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const tiles = [];
const context = {
    window: {addEventListener() {}}, console, setTimeout,
    document: {
        getElementById: el, querySelectorAll: () => [], addEventListener() {},
        createElement: () => ({
            innerHTML: '', className: '', dataset: {}, classList: {add() {}, remove() {}},
            addEventListener() {},
        }),
    },
    fetch: async () => ({json: async () => ({
        status: 'success', total: 3, accounts: [{id: 1, name: payload, value: 3}],
        candidates: [],
    })}),
};
els['mc-accounts-tiles'] = {appendChild: node => tiles.push(node), classList: {add() {}, remove() {}}};
els['pt-accounts-tiles'] = els['mc-accounts-tiles'];
els['po-accounts-tiles'] = els['mc-accounts-tiles'];
load(context, process.argv.slice(1));
(async () => {
    context.loadAccounts();
    await settle();
    assert.ok(tiles.length >= 2, 'no account tiles were rendered');
    for (const tile of tiles) {
        assert.ok(!tile.innerHTML.includes('<img'), tile.innerHTML);
    }
    assert.ok(tiles[0].innerHTML.includes('&lt;img'), tiles[0].innerHTML);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


@pytest.mark.parametrize("script_name", ["monte_carlo.js", "performance_analytics.js", "portfolio_optimizer.js"])
def test_account_tiles_escape_operator_entered_account_names(script_name):
    _s5_2_run_node(_S5_9_ACCOUNT_TILES_SCRIPT, _s5_2_js('utils.js'), _s5_2_js(script_name))


_S5_9_MARKETS_SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const payload = '<img src=x onerror=alert(1)>';
const context = {
    window: {innerWidth: 1400}, console,
    document: {getElementById: () => null, querySelectorAll: () => [], addEventListener() {}},
    setInterval() {}, fetch: async () => ({}),
};
vm.createContext(context);
for (const file of process.argv.slice(1)) vm.runInContext(fs.readFileSync(file, 'utf8'), context);
const base = {
    ticker: payload, display_name: payload, name: payload, asset_type: 'Index', price: 1,
    change_pct: 1, currency: 'USD', sparkline: [], sentiment_score: 0.1, is_positive: true,
    invert_color: false, stale_data: false, is_stale: false, market_state: 'open',
};
const check = (html, label) => {
    assert.ok(!html.includes('<img'), label + ': ' + html);
    assert.ok(html.includes('&lt;img'), label + ': ' + html);
    assert.ok(html.includes('data-ticker="&lt;img'), label + ': ' + html);
};
if (context.marketsRegionSectionHTML) {
    const dual = {...base, region: 'Europe', dual_instrument: {spot: base, future: base}};
    check(context.marketsRegionSectionHTML({region: payload, state: 'open', tiles: [base, dual]}), 'markets');
} else {
    const container = {innerHTML: ''};
    context.document.getElementById = () => container;
    context.renderMacroCards([base]);
    check(container.innerHTML, 'macro cards');
}
"""


@pytest.mark.parametrize("script_name", ["markets.js", "macro_cards.js"])
def test_market_cards_escape_registry_names_and_tickers(script_name):
    _s5_2_run_node(_S5_9_MARKETS_SCRIPT, _s5_2_js('utils.js'), _s5_2_js(script_name))


_S5_9_SETTINGS_MARKETS_SCRIPT = _S5_2_DOM_PRELUDE + r"""
const confirmed = [];
const context = {
    window: {MARKET_EXCHANGE_LIST: ['NYSE']}, console, setTimeout,
    confirm: message => { confirmed.push(message); return false; },
    document: {getElementById: el, addEventListener(type, fn) { listeners[type] = fn; }},
    fetch: async () => ({json: async () => ({registry: [{
        ticker: payload, display_name: payload, region: payload, asset_type: payload,
        exchange: payload, currency: payload, future_ticker: payload, future_display_name: payload,
        enabled: true,
    }]})}),
};
load(context, process.argv.slice(1));
(async () => {
    listeners.DOMContentLoaded();
    await settle();
    const html = els['markets-registry-list'].innerHTML;
    assert.ok(html.includes('&lt;img'), html);
    assert.ok(!html.includes('<img'), html);
    assert.ok(!html.includes('onclick'), html);
    els['markets-registry-list'].handlers.click({target: {closest: () => ({dataset: {regAction: 'delete', ticker: payload}})}});
    assert.equal(confirmed.length, 1);
    assert.ok(confirmed[0].includes(payload), confirmed[0]);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


def test_markets_registry_editor_escapes_rows_and_uses_delegated_handlers():
    _s5_2_run_node(_S5_9_SETTINGS_MARKETS_SCRIPT, _s5_2_js('utils.js'), _s5_2_js('settings_markets.js'))

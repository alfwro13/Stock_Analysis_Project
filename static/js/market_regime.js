(function () {
    var HMM_COLORS = { Bull: '#00ff00', Chop: '#ffaa00', Crash: '#ff4d4d' };
    var HMM_BG    = { Bull: 'rgba(0,255,0,.08)', Chop: 'rgba(255,170,0,.10)', Crash: 'rgba(255,77,77,.13)' };
    var HMM_PILL  = {
        Bull:  { bg: 'rgba(0,255,0,.15)',      color: '#00ff00', border: '#00cc00' },
        Chop:  { bg: 'rgba(255,170,0,.15)',     color: '#ffaa00', border: '#cc8800' },
        Crash: { bg: 'rgba(255,77,77,.18)',     color: '#ff4d4d', border: '#cc2222' },
    };

    function renderCurrentBanner(data) {
        var cur = data.current;
        if (!cur) return;
        var pill   = document.getElementById('cur-state-pill');
        var conf   = document.getElementById('cur-confidence');
        var asOf   = document.getElementById('cur-as-of');
        var banner = document.getElementById('current-state-banner');
        var sty = HMM_PILL[cur.label] || {};
        pill.textContent = cur.label.toUpperCase();
        pill.style.background   = sty.bg || '';
        pill.style.color        = sty.color || '';
        pill.style.borderColor  = sty.border || '';
        conf.textContent = 'confidence ' + Math.round((cur.probability || 0) * 100) + '%';
        asOf.textContent = cur.as_of || '—';
        banner.classList.remove('d-none');
        if (data.last_change) {
            var chg = data.last_change;
            document.getElementById('cur-change-text').textContent =
                chg.from_label + ' → ' + chg.to_label + ' on ' + chg.date;
            document.getElementById('cur-change-block').classList.remove('d-none');
        }
    }

    function renderChart(history) {
        var placeholder = document.getElementById('regime-chart-placeholder');
        var chartDiv    = document.getElementById('regime-chart');
        if (!history || history.length === 0) { return; }
        placeholder.classList.add('d-none');
        chartDiv.classList.remove('d-none');

        var dates  = history.map(function (h) { return h.date; });
        var states = history.map(function (h) { return h.state; });
        var probs  = history.map(function (h) { return Math.round((h.probability || 0) * 100); });
        var labels = history.map(function (h) { return h.label; });

        var shapes = [];
        var i = 0;
        while (i < history.length) {
            var runLabel = labels[i];
            var j = i;
            while (j < history.length && labels[j] === runLabel) { j++; }
            shapes.push({
                type: 'rect', xref: 'x', yref: 'paper',
                x0: dates[i], x1: dates[j - 1],
                y0: 0, y1: 1,
                fillcolor: HMM_BG[runLabel] || 'rgba(128,128,128,.05)',
                line: { width: 0 },
                layer: 'below',
            });
            i = j;
        }

        var stateTrace = {
            x: dates,
            y: states,
            mode: 'lines',
            line: { color: '#00ffcc', width: 1.5, shape: 'hv' },
            name: 'State (0=Bull 1=Chop 2=Crash)',
            hovertemplate: '%{x}<br>State: %{y} (' + '%{text}' + ')<extra></extra>',
            text: labels,
        };

        var probTrace = {
            x: dates,
            y: probs,
            mode: 'lines',
            line: { color: '#4da6ff', width: 1, dash: 'dot' },
            name: 'Confidence %',
            yaxis: 'y2',
            hovertemplate: '%{x}<br>Conf: %{y}%<extra></extra>',
            opacity: 0.6,
        };

        var layout = {
            paper_bgcolor: 'transparent',
            plot_bgcolor:  'transparent',
            margin: { t: 10, r: 60, b: 30, l: 40 },
            xaxis: {
                color: '#555', gridcolor: '#222', linecolor: '#333',
                type: 'date',
            },
            yaxis: {
                color: '#555', gridcolor: '#222',
                tickvals: [0, 1, 2], ticktext: ['Bull', 'Chop', 'Crash'],
                range: [-0.3, 2.5],
            },
            yaxis2: {
                color: '#4da6ff', overlaying: 'y', side: 'right',
                range: [0, 105], showgrid: false,
                ticksuffix: '%',
            },
            legend: { x: 0, y: 1.02, orientation: 'h', font: { size: 10, color: '#888' } },
            shapes: shapes,
            font: { family: 'monospace', size: 11, color: '#888' },
        };

        Plotly.newPlot('regime-chart', [stateTrace, probTrace], layout, { responsive: true, displayModeBar: false });
    }

    function renderTransitionMatrix(matrix) {
        var tbody  = document.getElementById('transition-tbody');
        var table  = document.getElementById('transition-table');
        var ph     = document.getElementById('transition-placeholder');
        if (!matrix || matrix.length === 0) { return; }
        tbody.innerHTML = '';
        var labels = ['Bull', 'Chop', 'Crash'];
        for (var r = 0; r < labels.length; r++) {
            var tr = document.createElement('tr');
            var fromLabel = labels[r];
            var td0 = document.createElement('td');
            td0.style.color = HMM_COLORS[fromLabel];
            td0.style.fontWeight = '600';
            td0.textContent = fromLabel;
            tr.appendChild(td0);
            for (var c = 0; c < labels.length; c++) {
                var val = matrix[r] ? (matrix[r][c] || 0) : 0;
                var pct = Math.round(val * 100);
                var td = document.createElement('td');
                td.className = 'text-center';
                if (r === c) {
                    td.style.color = HMM_COLORS[labels[c]];
                    td.style.fontWeight = '700';
                } else {
                    td.style.color = '#666';
                }
                td.textContent = pct + '%';
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }
        ph.classList.add('d-none');
        table.classList.remove('d-none');
    }

    function renderStats(stats) {
        var tbody  = document.getElementById('stats-tbody');
        var table  = document.getElementById('stats-table');
        var ph     = document.getElementById('stats-placeholder');
        if (!stats) { return; }
        tbody.innerHTML = '';
        var labels = ['Bull', 'Chop', 'Crash'];
        for (var i = 0; i < labels.length; i++) {
            var lbl = labels[i];
            var s = stats[lbl];
            if (!s) { continue; }
            var tr = document.createElement('tr');
            var ret = s.mean_daily_return !== null
                ? (s.mean_daily_return >= 0 ? '+' : '') + (s.mean_daily_return * 100).toFixed(3) + '%'
                : '—';
            var retColor = s.mean_daily_return !== null
                ? (s.mean_daily_return >= 0 ? '#00ff00' : '#ff4d4d')
                : '#555';
            var vol = s.mean_vol !== null ? s.mean_vol.toFixed(1) + '%' : '—';
            var td0 = document.createElement('td');
            td0.style.color = HMM_COLORS[lbl];
            td0.style.fontWeight = '600';
            td0.textContent = lbl;
            tr.appendChild(td0);
            var td1 = document.createElement('td');
            td1.className = 'text-center';
            td1.textContent = s.days || 0;
            tr.appendChild(td1);
            var td2 = document.createElement('td');
            td2.className = 'text-center';
            td2.style.color = retColor;
            td2.textContent = ret;
            tr.appendChild(td2);
            var td3 = document.createElement('td');
            td3.className = 'text-center text-muted';
            td3.textContent = vol;
            tr.appendChild(td3);
            tbody.appendChild(tr);
        }
        ph.classList.add('d-none');
        table.classList.remove('d-none');
    }

    function loadData() {
        fetch('/api/market-regime')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || data.status !== 'success') { return; }
                renderCurrentBanner(data);
                renderChart(data.history);
                renderTransitionMatrix(data.transition_matrix);
                renderStats(data.regime_stats);
                var lu = document.getElementById('last-updated');
                if (data.current && data.current.as_of) {
                    lu.textContent = 'Last calculated: ' + data.current.as_of;
                }
            })
            .catch(function () {});
    }

    window.runNow = function () {
        var btn    = document.getElementById('run-now-btn');
        var status = document.getElementById('run-status');
        btn.disabled = true;
        btn.textContent = 'Running…';
        status.textContent = '';
        fetch('/api/market-regime/run', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                status.textContent = data.message || 'Triggered.';
                setTimeout(function () {
                    loadData();
                    btn.disabled = false;
                    btn.textContent = '▶ Run Now';
                    status.textContent = 'Done.';
                }, 6000);
            })
            .catch(function () {
                btn.disabled = false;
                btn.textContent = '▶ Run Now';
                status.textContent = 'Error — check notifications.';
            });
    };

    loadData();
})();

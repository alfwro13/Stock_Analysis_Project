(function () {
    'use strict';

    const output = document.getElementById('lv-output');
    const statusEl = document.getElementById('lv-status');
    const lineCountEl = document.getElementById('lv-line-count');
    const searchInput = document.getElementById('lv-search');
    const autoscrollChk = document.getElementById('lv-autoscroll');
    const clearBtn = document.getElementById('lv-clear-btn');
    const loadFullBtn = document.getElementById('lv-loadfull-btn');
    const levelCheckboxes = Array.from(document.querySelectorAll('.lv-level-chk'));

    // Log format: "YYYY-MM-DD HH:MM:SS,mmm - module.name - LEVEL - message"
    const LINE_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[^ ]*) - ([^ ]+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$/;
    const LEVEL_STORAGE_KEY = 'lv_active_levels';

    function loadStoredLevels() {
        try {
            const raw = localStorage.getItem(LEVEL_STORAGE_KEY);
            const arr = raw ? JSON.parse(raw) : null;
            return Array.isArray(arr) ? arr : null;
        } catch (_) {
            return null;
        }
    }

    function saveActiveLevels() {
        try {
            localStorage.setItem(LEVEL_STORAGE_KEY, JSON.stringify(Array.from(activeLevels)));
        } catch (_) {}
    }

    const storedLevels = loadStoredLevels();
    if (storedLevels) {
        levelCheckboxes.forEach(chk => {
            chk.checked = storedLevels.includes(chk.value);
        });
    }

    let totalLines = 0;
    let activeSearch = '';
    let activeLevels = new Set(levelCheckboxes.filter(c => c.checked).map(c => c.value));

    function parseLevel(raw) {
        const m = LINE_RE.exec(raw);
        return m ? m[3] : null;
    }

    function buildRow(raw) {
        const m = LINE_RE.exec(raw);
        const row = document.createElement('div');
        row.className = 'lv-row';
        row.dataset.raw = raw;

        if (m) {
            const level = m[3];
            row.dataset.level = level;
            row.classList.add('lv-row-' + level.toLowerCase());

            const ts = document.createElement('span');
            ts.className = 'lv-ts';
            ts.textContent = m[1];

            const mod = document.createElement('span');
            mod.className = 'lv-mod';
            mod.textContent = m[2];

            const lvl = document.createElement('span');
            lvl.className = 'lv-lvl lv-lvl-' + level.toLowerCase();
            lvl.textContent = level;

            const msg = document.createElement('span');
            msg.className = 'lv-msg';
            msg.textContent = m[4];

            row.append(ts, mod, lvl, msg);
        } else {
            row.classList.add('lv-row-unknown');
            row.textContent = raw;
        }
        return row;
    }

    function rowVisible(row) {
        const level = row.dataset.level || 'UNKNOWN';
        if (level !== 'UNKNOWN' && !activeLevels.has(level)) return false;
        if (activeSearch && !row.dataset.raw.toLowerCase().includes(activeSearch)) return false;
        return true;
    }

    function applyFilters() {
        const rows = output.querySelectorAll('.lv-row');
        rows.forEach(row => {
            row.style.display = rowVisible(row) ? '' : 'none';
        });
        scrollIfAuto();
    }

    function appendRow(raw) {
        if (!raw.trim()) return;
        const row = buildRow(raw);
        output.appendChild(row);
        totalLines++;
        lineCountEl.textContent = totalLines + ' lines';
        if (!rowVisible(row)) {
            row.style.display = 'none';
        }
        scrollIfAuto();
    }

    function scrollIfAuto() {
        if (autoscrollChk.checked) {
            output.scrollTop = output.scrollHeight;
        }
    }

    function setStatus(text, isError) {
        statusEl.textContent = text;
        statusEl.className = isError ? 'lv-status-err' : '';
    }

    // Initial tail load
    function loadTail() {
        if (!window.LV_LOGGING_ENABLED) {
            setStatus('File logging is disabled — no log to display.', true);
            return;
        }
        setStatus('Loading last 500 lines…');
        fetch('/api/logs/tail?lines=500')
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'success') {
                    setStatus('Error: ' + (data.message || 'unknown'), true);
                    return;
                }
                data.lines.forEach(appendRow);
                startStream();
            })
            .catch(err => {
                setStatus('Failed to load log: ' + err, true);
            });
    }

    // Full-file load (replaces the in-view buffer; the live stream keeps tailing independently)
    function loadFull() {
        if (!window.LV_LOGGING_ENABLED) return;
        setStatus('Loading full file…');
        fetch('/api/logs/tail?full=true')
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'success') {
                    setStatus('Error: ' + (data.message || 'unknown'), true);
                    return;
                }
                output.innerHTML = '';
                totalLines = 0;
                data.lines.forEach(appendRow);
                setStatus('Live — streaming new lines…');
            })
            .catch(err => {
                setStatus('Failed to load log: ' + err, true);
            });
    }

    // SSE live stream
    function startStream() {
        if (!window.LV_LOGGING_ENABLED) return;
        setStatus('Live — streaming new lines…');
        const es = new EventSource('/api/logs/stream');

        es.onmessage = function (e) {
            try {
                const line = JSON.parse(e.data);
                if (line && typeof line === 'string') {
                    appendRow(line);
                } else if (line && line.error) {
                    setStatus('Stream error: ' + line.error, true);
                    es.close();
                }
            } catch (_) {
                appendRow(e.data);
            }
        };

        es.onerror = function () {
            setStatus('Stream disconnected — retrying…', true);
        };

        es.onopen = function () {
            setStatus('Live — streaming new lines…');
        };
    }

    // Filter event handlers
    levelCheckboxes.forEach(chk => {
        chk.addEventListener('change', () => {
            activeLevels = new Set(
                levelCheckboxes.filter(c => c.checked).map(c => c.value)
            );
            saveActiveLevels();
            applyFilters();
        });
    });

    searchInput.addEventListener('input', () => {
        activeSearch = searchInput.value.trim().toLowerCase();
        applyFilters();
    });

    clearBtn.addEventListener('click', () => {
        output.innerHTML = '';
        totalLines = 0;
        lineCountEl.textContent = '';
    });

    loadFullBtn.addEventListener('click', loadFull);

    loadTail();
}());

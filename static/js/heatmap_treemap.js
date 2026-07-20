window.HeatmapTreemap = (function () {
    var MIN_VALUE = 0.05;

    function squarify(items, x, y, w, h) {
        var total = items.reduce(function (s, d) { return s + d.value; }, 0);
        if (!total || !w || !h) return [];
        var norm = items.map(function (d) { return { v: d.value / total, item: d }; });
        norm.sort(function (a, b) { return b.v - a.v; });
        var rects = [];
        squarifySection(norm, x, y, w, h, rects);
        return rects;
    }

    function squarifySection(items, x, y, w, h, rects) {
        if (!items.length) return;
        if (items.length === 1) {
            rects.push({ x: x, y: y, w: w, h: h, item: items[0].item });
            return;
        }
        var s = items.reduce(function (a, b) { return a + b.v; }, 0);
        var row = [items[0]];
        var rowSum = items[0].v;
        for (var i = 1; i < items.length; i++) {
            var newSum = rowSum + items[i].v;
            if (worstAspect(row, rowSum, s, w, h) >= worstAspect(row.concat([items[i]]), newSum, s, w, h)) {
                row.push(items[i]);
                rowSum = newSum;
            } else {
                break;
            }
        }
        var cursor;
        if (w >= h) {
            var stripW = (rowSum / s) * w;
            cursor = y;
            row.forEach(function (d) {
                var tileH = (d.v / rowSum) * h;
                rects.push({ x: x, y: cursor, w: stripW, h: tileH, item: d.item });
                cursor += tileH;
            });
            squarifySection(items.slice(row.length), x + stripW, y, w - stripW, h, rects);
        } else {
            var stripH = (rowSum / s) * h;
            cursor = x;
            row.forEach(function (d) {
                var tileW = (d.v / rowSum) * w;
                rects.push({ x: cursor, y: y, w: tileW, h: stripH, item: d.item });
                cursor += tileW;
            });
            squarifySection(items.slice(row.length), x, y + stripH, w, h - stripH, rects);
        }
    }

    function worstAspect(row, rowSum, total, w, h) {
        var rowArea = (rowSum / total) * (w * h);
        var shortSide = Math.min(w, h);
        var rowLen = rowArea / shortSide;
        if (!rowLen) return Infinity;
        var worst = 0;
        row.forEach(function (d) {
            var tileArea = (d.v / total) * (w * h);
            var tileSide = tileArea / rowLen;
            if (!tileSide) return;
            var ratio = Math.max(rowLen / tileSide, tileSide / rowLen);
            if (ratio > worst) worst = ratio;
        });
        return worst;
    }

    var EXTENDED_MIN_SIDE = 55;

    function _extendedFull(d) {
        var priceStr = formatCurrency(d.extendedPrice, d.currency);
        var sign = d.extendedChangePct >= 0 ? '+' : '';
        var label = d.extendedSession === 'pre' ? 'Pre-Market' : 'After Hours';
        return '(' + label + ': ' + priceStr + ' ' + sign + d.extendedChangePct.toFixed(2) + '%)';
    }

    function _extendedCompact(d) {
        var sign = d.extendedChangePct >= 0 ? '+' : '';
        var label = d.extendedSession === 'pre' ? 'Pre' : 'After';
        return '(' + label + ': ' + sign + d.extendedChangePct.toFixed(2) + '%)';
    }

    function render(panel, items, showExtended) {
        var data = items
            .map(function (d) {
                return {
                    ticker: d.ticker || '',
                    change: d.change,
                    value: Math.max(Math.abs(d.change), MIN_VALUE),
                    currency: d.currency || '',
                    extendedSession: d.extendedSession || '',
                    extendedPrice: d.extendedPrice,
                    extendedChangePct: d.extendedChangePct
                };
            })
            .filter(function (d) { return d.ticker && isFinite(d.change); });

        if (!data.length) {
            panel.innerHTML = '<p class="heatmap-empty">No data to display.</p>';
            return;
        }

        var W = panel.offsetWidth;
        var H = panel.offsetHeight;
        if (!W || !H) return;

        var tileRects = squarify(data, 0, 0, W, H);
        var html = '';
        tileRects.forEach(function (r) {
            var d = r.item;
            var cls = d.change > 0.001 ? 'heatmap-tile-gain'
                    : d.change < -0.001 ? 'heatmap-tile-loss'
                    : 'heatmap-tile-flat';
            var sign = d.change > 0 ? '+' : '';
            var label = sign + d.change.toFixed(2) + '%';
            var minSide = Math.min(r.w, r.h);
            var hasExtended = !!(showExtended && d.extendedSession && isFinite(d.extendedChangePct));
            var href = '/stock/' + encodeURIComponent(d.ticker) + (window.EMBED_MODE ? '?embed=true' + (window.EMBED_TOKEN ? '&embed_token=' + encodeURIComponent(window.EMBED_TOKEN) : '') : '');
            var title = hasExtended && minSide < EXTENDED_MIN_SIDE ? ' title="' + _extendedFull(d).replace(/"/g, '&quot;') + '"' : '';
            html += '<a href="' + href + '" class="heatmap-tile ' + cls + '" style="left:' + Math.round(r.x) + 'px;top:' + Math.round(r.y) + 'px;width:' + Math.round(r.w) + 'px;height:' + Math.round(r.h) + 'px;"' + title + '>'
                  + (minSide >= 20 ? '<span class="heatmap-tile-ticker">' + d.ticker + '</span>' : '')
                  + (minSide >= 35 ? '<span class="heatmap-tile-change">' + label + '</span>' : '')
                  + (hasExtended && minSide >= EXTENDED_MIN_SIDE ? '<span class="heatmap-tile-extended">' + _extendedCompact(d) + '</span>' : '')
                  + '</a>';
        });
        panel.innerHTML = html;
    }

    return { render: render };
})();

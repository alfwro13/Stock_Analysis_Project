// Shared fullscreen toggle + relayout core for Plotly chart wrappers, per AGENTS.md Rule 18.
// Two structural variants: single wrapper (the .fullscreen-btn and the chart share one div,
// pass only outerWrapperId) or outer/inner wrapper (the button lives on an outer div that gets
// .is-fullscreen; the chart lives in a separate descendant div, pass innerWrapperId too).
// Two rendering variants: server-rendered (visuals*.py fig.to_html()) charts don't react to
// config.responsive on container resize and need width+height relayout'd explicitly
// (opts.forceWidth = true); JS-rendered (Plotly.react()/newPlot()) charts already resize width
// via config.responsive and only need height relayout'd (opts.forceWidth = false/omitted).

window.ChartFullscreen = (function () {

    function relayoutPlot(plotEl, height, forceWidth, extraProps) {
        if (!plotEl || !window.Plotly || !height) return;
        var update = Object.assign({ height: height }, extraProps || {});
        if (forceWidth) update.width = plotEl.getBoundingClientRect().width;
        Plotly.relayout(plotEl, update);
        // A height-shrinking relayout doesn't reliably resize .plot-container/the outer
        // div on its own — force both explicitly (see AGENTS.md rule 18).
        plotEl.style.height = height + 'px';
        var container = plotEl.querySelector('.plot-container');
        if (container) container.style.height = height + 'px';
    }

    function resolvePlotEl(innerWrapper) {
        if (!innerWrapper) return null;
        return innerWrapper.classList.contains('js-plotly-plot')
            ? innerWrapper
            : innerWrapper.querySelector('.js-plotly-plot');
    }

    function resolveHeight(opts, plotEl, isFullscreen) {
        if (isFullscreen) return window.innerHeight - 120;
        return typeof opts.getHeight === 'function' ? opts.getHeight(plotEl, false) : opts.getHeight;
    }

    function toggle(outerWrapperId, opts) {
        opts = opts || {};
        var innerWrapperId = opts.innerWrapperId || outerWrapperId;
        var wrapper = document.getElementById(outerWrapperId);
        var innerWrapper = document.getElementById(innerWrapperId);
        if (!wrapper || !innerWrapper) return;
        var plotEl = resolvePlotEl(innerWrapper);
        var isFullscreen = wrapper.classList.contains('is-fullscreen');
        var btn = wrapper.querySelector('.fullscreen-btn');
        var willBeFullscreen = !isFullscreen;

        if (isFullscreen) {
            wrapper.classList.remove('is-fullscreen');
            if (btn) btn.innerHTML = opts.closedLabel || '&#9638; Fullscreen';
            if (opts.onExit) opts.onExit(wrapper);
        } else {
            wrapper.classList.add('is-fullscreen');
            if (btn) btn.innerHTML = opts.openLabel || '&#10006; Exit Fullscreen';
            if (opts.onEnter) opts.onEnter(wrapper);
        }
        var height = resolveHeight(opts, plotEl, willBeFullscreen);
        var extraProps = opts.getExtraProps ? opts.getExtraProps(willBeFullscreen, plotEl) : null;
        relayoutPlot(plotEl, height, !!opts.forceWidth, extraProps);
        window.dispatchEvent(new Event('resize'));
    }

    // Re-applies the wrapper's current fullscreen/closed state — for window resize handlers.
    function relayoutForCurrentState(outerWrapperId, opts) {
        opts = opts || {};
        var innerWrapperId = opts.innerWrapperId || outerWrapperId;
        var wrapper = document.getElementById(outerWrapperId);
        var innerWrapper = document.getElementById(innerWrapperId);
        if (!wrapper || !innerWrapper) return;
        var plotEl = resolvePlotEl(innerWrapper);
        var isFullscreen = wrapper.classList.contains('is-fullscreen');
        var height = resolveHeight(opts, plotEl, isFullscreen);
        var extraProps = opts.getExtraProps ? opts.getExtraProps(isFullscreen, plotEl) : null;
        relayoutPlot(plotEl, height, !!opts.forceWidth, extraProps);
    }

    return { toggle: toggle, relayoutForCurrentState: relayoutForCurrentState, relayoutPlot: relayoutPlot };
})();

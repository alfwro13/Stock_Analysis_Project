function _houseChartHeight() {
    // Mobile can't go below 400 — static/css/styles.css forces a 400px min-height
    // on .js-plotly-plot under 768px (for the Macro chart); a smaller value here
    // leaves the chart pinned short inside a taller, CSS-floored container.
    return window.innerWidth < 768 ? 400 : 350;
}

function toggleFullscreen(wrapperId) {
    const wrapper = document.getElementById(wrapperId);
    if (!wrapper) return;
    const isFullscreen = wrapper.classList.contains('is-fullscreen');
    const btn = wrapper.querySelector('.fullscreen-btn');
    const plotEl = wrapper.querySelector('.js-plotly-plot');
    if (isFullscreen) {
        wrapper.classList.remove('is-fullscreen');
        if (btn) btn.innerHTML = '&#9638; Fullscreen';
        if (plotEl && window.Plotly) Plotly.relayout(plotEl, { height: _houseChartHeight() });
    } else {
        wrapper.classList.add('is-fullscreen');
        if (btn) btn.innerHTML = '&#10006; Exit Fullscreen';
        if (plotEl && window.Plotly) Plotly.relayout(plotEl, { height: window.innerHeight - 120 });
    }
    window.dispatchEvent(new Event('resize'));
}

document.addEventListener('DOMContentLoaded', () => {
    const plotEl = document.querySelector('#house-chart-wrapper .js-plotly-plot');
    if (plotEl && window.Plotly) Plotly.relayout(plotEl, { height: _houseChartHeight() });
});

window.onTransactionChanged = function () {
    location.reload();
};

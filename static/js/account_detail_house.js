function toggleFullscreen(wrapperId) {
    const el = document.getElementById(wrapperId);
    if (!el) return;
    if (!document.fullscreenElement) {
        el.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen();
    }
}

window.onTransactionChanged = function () {
    location.reload();
};

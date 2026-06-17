$(document).ready(function() {
    $('#usCalendarTable').DataTable({
        deferRender: true,
        responsive: true,
        order: [[0, 'asc']],
        pageLength: 10,
        lengthChange: false,
        searching: false,
        info: false
    });

    $('#ukCalendarTable').DataTable({
        deferRender: true,
        responsive: true,
        order: [[0, 'asc']],
        pageLength: 10,
        lengthChange: false,
        searching: false,
        info: false
    });
});

function toggleFullscreen(wrapperId) {
    const elem = document.getElementById(wrapperId);
    if (!elem) return;

    if (!document.fullscreenElement && !document.mozFullScreenElement &&
        !document.webkitFullscreenElement && !document.msFullscreenElement) {
        if (elem.requestFullscreen) {
            elem.requestFullscreen();
        } else if (elem.msRequestFullscreen) {
            elem.msRequestFullscreen();
        } else if (elem.mozRequestFullScreen) {
            elem.mozRequestFullScreen();
        } else if (elem.webkitRequestFullscreen) {
            elem.webkitRequestFullscreen(Element.ALLOW_KEYBOARD_INPUT);
        }
        elem.classList.add('is-fullscreen');
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen();
        } else if (document.msExitFullscreen) {
            document.msExitFullscreen();
        } else if (document.mozCancelFullScreen) {
            document.mozCancelFullScreen();
        } else if (document.webkitExitFullscreen) {
            document.webkitExitFullscreen();
        }
        elem.classList.remove('is-fullscreen');
    }
}

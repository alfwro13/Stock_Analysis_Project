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

function _initAccountDetailTable(id, priorities) {
    const el = document.getElementById(id);
    if (!el) return null;
    return $(el).DataTable({
        responsive: true,
        pageLength: 25,
        columnDefs: priorities.map((targets, priority) => ({ responsivePriority: priority + 1, targets })),
    });
}

function filterActivitiesByType(type) {
    if (!window._activitiesTable) return;
    window._activitiesTable.column(1).search(type ? `^${type}$` : '', true, false).draw();
}

document.addEventListener('DOMContentLoaded', () => {
    (window.ACCOUNT_TRANSACTIONS || []).forEach(t => { _txnCache[t.id] = t; });
    _initAccountDetailTable('holdingsTable', [[0], [1], [6], [4], [5], [7], [8], [2], [3]]);
    _initAccountDetailTable('closedTable', [[0], [1], [5], [3], [4], [6], [7], [2]]);
    window._activitiesTable = _initAccountDetailTable('activitiesTable', [[0], [1], [8], [7], [2], [3], [4], [5], [6]]);
    _initAccountDetailTable('cashTable', [[0], [2], [3], [1]]);
});

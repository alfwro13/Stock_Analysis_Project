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
    if (!el) return;
    $(el).DataTable({
        responsive: true,
        pageLength: 25,
        columnDefs: priorities.map((targets, priority) => ({ responsivePriority: priority + 1, targets })),
    });
}

document.addEventListener('DOMContentLoaded', () => {
    (window.ACCOUNT_TRANSACTIONS || []).forEach(t => { _txnCache[t.id] = t; });
    _initAccountDetailTable('holdingsTable', [[0], [1], [5], [3], [4], [6], [7], [2]]);
    _initAccountDetailTable('closedTable', [[0], [1], [4], [2], [3], [5], [6]]);
    _initAccountDetailTable('activitiesTable', [[0], [1], [7], [2], [3], [4], [5], [6]]);
    _initAccountDetailTable('cashTable', [[0], [1]]);
});

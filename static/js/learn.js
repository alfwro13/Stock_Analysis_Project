let learnSessionCards = [];
let learnSessionIndex = 0;
let learnSessionResults = { good: 0, hard: 0, fail: 0 };
let learnRevealed = false;

async function learnLoadOverview() {
    const response = await fetch('/api/learn/overview');
    const result = await response.json();
    if (!response.ok || result.status !== 'success') return;

    document.getElementById('learnDueBadge').textContent = `Due: ${result.due_count}`;
    document.getElementById('learnWeakBadge').textContent = `Weak: ${result.weak_terms.length}`;
    document.getElementById('learnLearnedBadge').textContent = `Learned: ${result.total_learned}`;

    const levelsEl = document.getElementById('learnLevels');
    levelsEl.innerHTML = result.levels.map(learnLevelRowHTML).join('');

    const startBtn = document.getElementById('learnStartBtn');
    const nothingToStudy = result.due_count === 0 && !result.levels.some(l => l.unlocked && l.studied < l.total);
    document.getElementById('learnEmptyState').style.display = nothingToStudy ? 'block' : 'none';
    startBtn.disabled = nothingToStudy;
}

function learnLevelRowHTML(level) {
    const pct = level.total > 0 ? Math.round((level.studied / level.total) * 100) : 0;
    const lockedClass = level.unlocked ? '' : 'learn-level-locked';
    return `
        <div class="learn-level-row ${lockedClass}">
            <div class="d-flex justify-content-between">
                <span>${level.unlocked ? '' : '🔒 '}${escapeHtml(level.title)}</span>
                <span class="text-muted">${level.studied}/${level.total} studied &middot; ${level.learned} learned</span>
            </div>
            <div class="progress" style="height: 6px;">
                <div class="progress-bar" role="progressbar" style="width: ${pct}%"></div>
            </div>
        </div>
    `;
}

async function learnStartSession() {
    const response = await fetch('/api/learn/session', { method: 'POST' });
    const result = await response.json();
    if (!response.ok || result.status !== 'success' || result.cards.length === 0) return;

    learnSessionCards = result.cards;
    learnSessionIndex = 0;
    learnSessionResults = { good: 0, hard: 0, fail: 0 };

    document.getElementById('learnDashboard').style.display = 'none';
    document.getElementById('learnSession').style.display = 'block';
    document.getElementById('learnSummary').style.display = 'none';
    learnRenderCard();
}

function learnRenderCard() {
    const card = learnSessionCards[learnSessionIndex];
    document.getElementById('learnSessionProgress').textContent =
        `${learnSessionIndex + 1} / ${learnSessionCards.length}`;
    learnRevealed = false;

    const cardEl = document.getElementById('learnCard');
    if (card.mode === 'mcq') {
        cardEl.innerHTML = `
            <div class="learn-term-title">${escapeHtml(card.term_title)}</div>
            <p>${escapeHtml(card.question)}</p>
            <div class="learn-options">
                ${card.options.map(opt => `<button type="button" class="btn btn-outline-secondary learn-option-btn" data-answer="${escapeHtml(opt)}">${escapeHtml(opt)}</button>`).join('')}
            </div>
        `;
        cardEl.querySelectorAll('.learn-option-btn').forEach(btn => {
            btn.addEventListener('click', () => learnAnswerMCQ(card, btn));
        });
    } else {
        cardEl.innerHTML = `
            <div class="learn-term-title">${escapeHtml(card.term_title)}</div>
            <p>${escapeHtml(card.question)}</p>
            <button type="button" class="btn btn-outline-primary" id="learnRevealBtn">Reveal Answer</button>
            <div id="learnRecallAnswer" class="mt-3" style="display:none;">
                <p><strong>${escapeHtml(card.answer)}</strong></p>
                <div class="learn-grade-buttons">
                    <button type="button" class="btn btn-outline-danger" data-grade="fail">Didn't know</button>
                    <button type="button" class="btn btn-outline-warning" data-grade="hard">Fuzzy</button>
                    <button type="button" class="btn btn-outline-success" data-grade="good">Knew it</button>
                </div>
            </div>
        `;
        document.getElementById('learnRevealBtn').addEventListener('click', () => {
            learnRevealed = true;
            document.getElementById('learnRecallAnswer').style.display = 'block';
            document.getElementById('learnRevealBtn').style.display = 'none';
        });
        cardEl.querySelectorAll('[data-grade]').forEach(btn => {
            btn.addEventListener('click', () => learnSubmitAnswer(card.term_key, btn.dataset.grade));
        });
    }
}

function learnAnswerMCQ(card, clickedBtn) {
    const cardEl = document.getElementById('learnCard');
    cardEl.querySelectorAll('.learn-option-btn').forEach(btn => {
        btn.disabled = true;
        if (btn.dataset.answer === card.answer) btn.classList.add('learn-option-correct');
    });
    const correct = clickedBtn.dataset.answer === card.answer;
    if (!correct) clickedBtn.classList.add('learn-option-incorrect');
    setTimeout(() => learnSubmitAnswer(card.term_key, correct ? 'good' : 'fail'), 700);
}

async function learnSubmitAnswer(termKey, grade) {
    learnSessionResults[grade] += 1;
    await fetch('/api/learn/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term_key: termKey, grade }),
    });

    learnSessionIndex += 1;
    if (learnSessionIndex >= learnSessionCards.length) {
        learnShowSummary();
    } else {
        learnRenderCard();
    }
}

function learnShowSummary() {
    document.getElementById('learnSession').style.display = 'none';
    document.getElementById('learnSummary').style.display = 'block';
    document.getElementById('learnSummaryStats').innerHTML = `
        <p>Knew it: ${learnSessionResults.good} &middot; Fuzzy: ${learnSessionResults.hard} &middot; Didn't know: ${learnSessionResults.fail}</p>
    `;
}

function learnBackToDashboard() {
    document.getElementById('learnSummary').style.display = 'none';
    document.getElementById('learnSession').style.display = 'none';
    document.getElementById('learnDashboard').style.display = 'block';
    learnLoadOverview();
}

document.addEventListener('DOMContentLoaded', () => {
    learnLoadOverview();
    document.getElementById('learnStartBtn').addEventListener('click', learnStartSession);
    document.getElementById('learnBackBtn').addEventListener('click', learnBackToDashboard);
    document.getElementById('learnAgainBtn').addEventListener('click', learnStartSession);
});

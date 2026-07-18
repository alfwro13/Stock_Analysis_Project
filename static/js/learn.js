let learnSessionCards = [];
let learnSessionIndex = 0;
let learnSessionResults = { good: 0, hard: 0, fail: 0 };

async function learnLoadOverview() {
    const response = await fetch('/api/learn/overview');
    const result = await response.json();
    if (!response.ok || result.status !== 'success') return;

    document.getElementById('learnDueBadge').textContent = `Due: ${result.due_count}`;
    document.getElementById('learnWeakBadge').textContent = `Weak: ${result.weak_terms.length}`;
    document.getElementById('learnLearnedBadge').textContent = `Learned: ${result.total_learned}`;

    const unlockAll = document.getElementById('learnUnlockAll').checked;
    const levelsEl = document.getElementById('learnLevels');
    levelsEl.innerHTML = result.levels.map(level => learnLevelRowHTML(level, unlockAll)).join('');
    levelsEl.querySelectorAll('.learn-level-clickable').forEach(row => {
        row.addEventListener('click', () => learnStartSession(row.dataset.sectionId, parseInt(row.dataset.total, 10)));
    });

    const startBtn = document.getElementById('learnStartBtn');
    const nothingToStudy = result.due_count === 0 && !result.levels.some(l => l.unlocked && l.studied < l.total);
    document.getElementById('learnEmptyState').style.display = nothingToStudy ? 'block' : 'none';
    startBtn.disabled = nothingToStudy;
}

function learnLevelRowHTML(level, unlockAll) {
    const clickable = level.unlocked || unlockAll;
    const pct = level.total > 0 ? Math.round((level.studied / level.total) * 100) : 0;
    const lockedClass = clickable ? '' : 'learn-level-locked';
    const clickableClass = clickable ? 'learn-level-clickable' : '';
    return `
        <div class="learn-level-row ${lockedClass} ${clickableClass}" data-section-id="${escapeHtml(level.section_id)}" data-total="${level.total}">
            <div class="d-flex justify-content-between">
                <span>${clickable ? '' : '🔒 '}${escapeHtml(level.title)}</span>
                <span class="text-muted">${level.studied}/${level.total} studied &middot; ${level.learned} learned</span>
            </div>
            <div class="progress" style="height: 6px;">
                <div class="progress-bar" role="progressbar" style="width: ${pct}%"></div>
            </div>
        </div>
    `;
}

function learnSetBackLink(inLearningMode) {
    const link = document.getElementById('learnBackLink');
    if (inLearningMode) {
        link.textContent = '← Back to Dashboard';
        link.href = '/glossary/learn';
        link.onclick = (e) => {
            e.preventDefault();
            learnBackToDashboard();
        };
    } else {
        link.textContent = '← Back to Glossary';
        link.href = '/glossary';
        link.onclick = null;
    }
}

async function learnStartSession(sectionId, size) {
    const params = new URLSearchParams();
    params.set('size', size || 10);
    if (sectionId) params.set('section_id', sectionId);

    const response = await fetch(`/api/learn/session?${params.toString()}`, { method: 'POST' });
    const result = await response.json();
    if (!response.ok || result.status !== 'success' || result.cards.length === 0) return;

    learnSessionCards = result.cards;
    learnSessionIndex = 0;
    learnSessionResults = { good: 0, hard: 0, fail: 0 };

    document.getElementById('learnDashboard').style.display = 'none';
    document.getElementById('learnSession').style.display = 'block';
    document.getElementById('learnSummary').style.display = 'none';
    learnSetBackLink(true);
    learnRenderCard();
}

function learnRenderCard() {
    const card = learnSessionCards[learnSessionIndex];
    document.getElementById('learnSessionProgress').textContent =
        `${learnSessionIndex + 1} / ${learnSessionCards.length}`;

    const cardEl = document.getElementById('learnCard');
    const candleBlock = card.candle_html || '';
    if (card.mode === 'mcq') {
        cardEl.innerHTML = `
            <div class="learn-term-title">${escapeHtml(card.term_title)}</div>
            <p>${escapeHtml(card.question)}</p>
            ${candleBlock}
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
            ${candleBlock}
            <button type="button" class="btn btn-outline-primary" id="learnRevealBtn">Reveal Answer</button>
            <div id="learnRecallAnswer" class="mt-3" style="display:none;">
                <p><strong>${escapeHtml(card.answer)}</strong></p>
                <div class="learn-explanation">${card.explanation}</div>
                <div class="learn-grade-buttons">
                    <button type="button" class="btn btn-outline-danger" data-grade="fail">Didn't know</button>
                    <button type="button" class="btn btn-outline-warning" data-grade="hard">Fuzzy</button>
                    <button type="button" class="btn btn-outline-success" data-grade="good">Knew it</button>
                </div>
            </div>
        `;
        document.getElementById('learnRevealBtn').addEventListener('click', () => {
            document.getElementById('learnRecallAnswer').style.display = 'block';
            document.getElementById('learnRevealBtn').style.display = 'none';
        });
        cardEl.querySelectorAll('[data-grade]').forEach(btn => {
            btn.addEventListener('click', () => learnSubmitAnswer(card.term_key, btn.dataset.grade));
        });
    }
}

async function learnAnswerMCQ(card, clickedBtn) {
    const cardEl = document.getElementById('learnCard');
    cardEl.querySelectorAll('.learn-option-btn').forEach(btn => {
        btn.disabled = true;
        if (btn.dataset.answer === card.answer) btn.classList.add('learn-option-correct');
    });
    const correct = clickedBtn.dataset.answer === card.answer;
    if (!correct) clickedBtn.classList.add('learn-option-incorrect');

    const grade = correct ? 'good' : 'fail';
    learnSessionResults[grade] += 1;
    await fetch('/api/learn/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term_key: card.term_key, grade }),
    });

    const feedback = document.createElement('div');
    feedback.className = 'learn-feedback mt-3';
    feedback.innerHTML = `
        <p class="mb-1 ${correct ? 'text-success' : 'text-danger'}"><strong>${correct ? 'Correct!' : 'Not quite.'}</strong></p>
        <p class="mb-2">${escapeHtml(card.answer)}</p>
        <div class="learn-explanation">${card.explanation}</div>
        <button type="button" class="btn btn-primary mt-2" id="learnNextBtn">Next</button>
    `;
    cardEl.appendChild(feedback);
    document.getElementById('learnNextBtn').addEventListener('click', learnAdvance);
}

function learnAdvance() {
    learnSessionIndex += 1;
    if (learnSessionIndex >= learnSessionCards.length) {
        learnShowSummary();
    } else {
        learnRenderCard();
    }
}

async function learnSubmitAnswer(termKey, grade) {
    learnSessionResults[grade] += 1;
    await fetch('/api/learn/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term_key: termKey, grade }),
    });
    learnAdvance();
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
    learnSetBackLink(false);
    learnLoadOverview();
}

async function learnToggleUnlockAll(enabled) {
    await fetch('/api/learn/unlock-all-preference', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
    });
    learnLoadOverview();
}

document.addEventListener('DOMContentLoaded', () => {
    learnLoadOverview();
    document.getElementById('learnStartBtn').addEventListener('click', () => learnStartSession());
    document.getElementById('learnBackBtn').addEventListener('click', learnBackToDashboard);
    document.getElementById('learnAgainBtn').addEventListener('click', () => learnStartSession());
    document.getElementById('learnUnlockAll').addEventListener('change', (e) => learnToggleUnlockAll(e.target.checked));
});

const GLOSSARY_FS_MIN = 1, GLOSSARY_FS_MAX = 7, GLOSSARY_FS_DEFAULT = 3;
const GLOSSARY_FS_SIZES = ['', '0.76em', '0.84em', '0.92em', '1.02em', '1.12em', '1.24em', '1.38em'];

function applyGlossaryFontSize(fs) {
    const el = document.getElementById('glossaryContent');
    el.dataset.fs = fs;
    el.style.setProperty('--glossary-fs', GLOSSARY_FS_SIZES[fs]);
    document.getElementById('glossaryFontDec').disabled = (fs <= GLOSSARY_FS_MIN);
    document.getElementById('glossaryFontInc').disabled = (fs >= GLOSSARY_FS_MAX);
}

function changeGlossaryFontSize(delta) {
    const current = parseInt(document.getElementById('glossaryContent').dataset.fs || GLOSSARY_FS_DEFAULT, 10);
    const next = Math.min(GLOSSARY_FS_MAX, Math.max(GLOSSARY_FS_MIN, current + delta));
    localStorage.setItem('glossary-fs', next);
    applyGlossaryFontSize(next);
}

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('glossarySearch');
    const clearBtn = document.getElementById('searchClearBtn');
    const noResults = document.getElementById('noResults');
    const termSections = document.querySelectorAll('.glossary-section:not(.doc-section)');
    const docSections = document.querySelectorAll('.doc-section');

    const stored = parseInt(localStorage.getItem('glossary-fs'), 10);
    const initFs = (stored >= GLOSSARY_FS_MIN && stored <= GLOSSARY_FS_MAX) ? stored : GLOSSARY_FS_DEFAULT;
    applyGlossaryFontSize(initFs);

    document.getElementById('glossaryFontDec').addEventListener('click', () => changeGlossaryFontSize(-1));
    document.getElementById('glossaryFontInc').addEventListener('click', () => changeGlossaryFontSize(1));

    function expandSection(section) {
        const collapseEl = section.querySelector('.accordion-collapse');
        const btn = section.querySelector('.accordion-button');
        if (collapseEl) collapseEl.classList.add('show');
        if (btn) { btn.classList.remove('collapsed'); btn.setAttribute('aria-expanded', 'true'); }
    }

    function runSearch(query) {
        clearBtn.style.display = query ? 'block' : 'none';
        let sectionsFound = 0;

        termSections.forEach(section => {
            const sectionTerms = section.querySelectorAll('.term-box');
            let matchInSection = false;

            sectionTerms.forEach(term => {
                const title = term.querySelector('.term-title').innerText.toLowerCase();
                const content = term.innerText.toLowerCase();

                if (title.includes(query) || content.includes(query)) {
                    term.style.display = 'block';
                    term.classList.add('search-match');
                    matchInSection = true;
                } else {
                    term.style.display = 'none';
                    term.classList.remove('search-match');
                }
            });

            if (matchInSection || query === '') {
                section.style.display = 'block';
                if (query !== '') expandSection(section);
                sectionsFound++;
            } else {
                section.style.display = 'none';
            }
        });

        docSections.forEach(section => {
            if (query === '') {
                section.style.display = 'block';
                section.classList.remove('doc-section-match');
            } else {
                const text = section.innerText.toLowerCase();
                if (text.includes(query)) {
                    section.style.display = 'block';
                    expandSection(section);
                    section.classList.add('doc-section-match');
                    sectionsFound++;
                } else {
                    section.style.display = 'none';
                    section.classList.remove('doc-section-match');
                }
            }
        });

        const divider = document.getElementById('asset-docs-divider');
        if (divider) {
            if (query === '') {
                divider.style.display = 'block';
            } else {
                const anyDocVisible = [...docSections].some(s => s.style.display !== 'none');
                divider.style.display = anyDocVisible ? 'block' : 'none';
            }
        }

        noResults.style.display = (sectionsFound === 0 && query !== '') ? 'block' : 'none';
    }

    searchInput.addEventListener('input', function () {
        runSearch(this.value.toLowerCase().trim());
    });

    clearBtn.addEventListener('click', () => {
        searchInput.value = '';
        searchInput.focus();
        runSearch('');
    });
});

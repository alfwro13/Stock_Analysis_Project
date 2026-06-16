document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('glossarySearch');
    const noResults = document.getElementById('noResults');
    const termSections = document.querySelectorAll('.settings-card:not(.doc-section)');
    const docSections = document.querySelectorAll('.doc-section');

    searchInput.addEventListener('input', function () {
        const query = this.value.toLowerCase().trim();
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
                if (query !== '') section.setAttribute('open', '');
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
                    section.setAttribute('open', '');
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
    });
});

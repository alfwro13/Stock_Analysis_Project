let currentChainData = {};
let strategyLegs = [];
let underlyingPrice = 0;

async function fetchChain() {
    const ticker = document.getElementById('tickerInput').value.toUpperCase();
    if (!ticker) return;

    document.getElementById('currentPriceDisplay').innerText = 'Fetching...';

    try {
        const response = await fetch(`/api/options/chain/${ticker}`);
        const data = await response.json();

        if (data.error) {
            alert(data.error);
            document.getElementById('currentPriceDisplay').innerText = '';
            return;
        }

        currentChainData = data;
        underlyingPrice = data.current_price;

        document.getElementById('currentPriceDisplay').innerText = `Current Price: $${underlyingPrice.toFixed(2)}`;

        const expSelect = document.getElementById('expSelect');
        expSelect.innerHTML = '';
        data.expirations.forEach(exp => {
            const opt = document.createElement('option');
            opt.value = exp;
            opt.innerText = exp;
            expSelect.appendChild(opt);
        });
        expSelect.classList.remove('d-none');
        expSelect.classList.add('d-inline-block');

        renderTables();

    } catch (err) {
        alert('Failed to fetch options chain.');
    }
}

function renderTables() {
    const exp = document.getElementById('expSelect').value;
    if (!exp || !currentChainData.chains) return;

    const chain = currentChainData.chains[exp];

    const buildRows = (type, data) => {
        let html = '';
        data.forEach(opt => {
            let mid = opt.lastPrice;
            if (opt.bid > 0 && opt.ask > 0) { mid = (opt.bid + opt.ask) / 2; }

            html += `<tr>
                <td><strong>${opt.strike.toFixed(2)}</strong></td>
                <td>${opt.lastPrice.toFixed(2)}</td>
                <td>${opt.bid.toFixed(2)}</td>
                <td>${opt.ask.toFixed(2)}</td>
                <td><button class="btn-add" onclick="addLeg('${type}', ${opt.strike}, ${mid})">+</button></td>
            </tr>`;
        });
        return html;
    };

    document.querySelector('#callsTable tbody').innerHTML = buildRows('call', chain.calls);
    document.querySelector('#putsTable tbody').innerHTML = buildRows('put', chain.puts);
}

function addLeg(type, strike, premium) {
    strategyLegs.push({
        id: Date.now(),
        type: type,
        strike: strike,
        premium: premium,
        position: 'long',
        quantity: 1
    });
    renderLegs();
    calculatePayoff();
}

function removeLeg(id) {
    strategyLegs = strategyLegs.filter(leg => leg.id !== id);
    renderLegs();
    calculatePayoff();
}

function togglePosition(id) {
    const leg = strategyLegs.find(l => l.id === id);
    if (leg) { leg.position = leg.position === 'long' ? 'short' : 'long'; }
    renderLegs();
    calculatePayoff();
}

function updateQuantity(id, val) {
    const leg = strategyLegs.find(l => l.id === id);
    if (leg) { leg.quantity = parseInt(val) || 1; }
    calculatePayoff();
}

function renderLegs() {
    const container = document.getElementById('strategyLegs');
    container.innerHTML = '';

    strategyLegs.forEach(leg => {
        const isLong = leg.position === 'long';
        const tagClass = isLong ? 'leg-long' : 'leg-short';
        const btnClass = isLong ? 'btn-add-long' : 'btn-add-short';

        container.innerHTML += `
            <div class="leg-item ${tagClass}">
                <div class="flex-1">
                    <button class="btn-add ${btnClass}" onclick="togglePosition(${leg.id})">${leg.position.toUpperCase()}</button>
                    <span class="leg-strike-text">${leg.strike} ${leg.type.toUpperCase()}</span>
                </div>
                <div class="flex-1">
                    <input type="number" value="${leg.quantity}" min="1" class="leg-qty-input" onchange="updateQuantity(${leg.id}, this.value)">
                    <span class="leg-premium-text">@ $${leg.premium.toFixed(2)}</span>
                </div>
                <button class="btn-remove" onclick="removeLeg(${leg.id})">X</button>
            </div>
        `;
    });
}

async function calculatePayoff() {
    if (strategyLegs.length === 0) {
        Plotly.purge('payoff-chart');
        return;
    }

    const payload = {
        current_price: underlyingPrice,
        legs: strategyLegs
    };

    const response = await fetch('/api/options/payoff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    const data = await response.json();
    plotChart(data.prices, data.payoffs);
}

function plotChart(prices, payoffs) {
    const posPayoffs = payoffs.map(y => y >= 0 ? y : null);
    const negPayoffs = payoffs.map(y => y <= 0 ? y : null);

    const tracePos = {
        x: prices, y: posPayoffs,
        mode: 'lines', name: 'Profit',
        line: { color: '#00ff00', width: 3 },
        fill: 'tozeroy', fillcolor: 'rgba(0, 255, 0, 0.1)'
    };

    const traceNeg = {
        x: prices, y: negPayoffs,
        mode: 'lines', name: 'Loss',
        line: { color: '#ff4d4d', width: 3 },
        fill: 'tozeroy', fillcolor: 'rgba(255, 77, 77, 0.1)'
    };

    const layout = {
        title: 'Strategy Payoff at Expiration',
        template: 'plotly_dark',
        paper_bgcolor: '#121212',
        plot_bgcolor: '#121212',
        margin: { l: 40, r: 20, t: 40, b: 30 },
        xaxis: { title: 'Underlying Price at Expiration' },
        yaxis: { title: 'Net P&L ($)', zeroline: true, zerolinecolor: '#888', zerolinewidth: 2 },
        showlegend: false,
        hovermode: 'x unified'
    };

    Plotly.newPlot('payoff-chart', [tracePos, traceNeg], layout, { responsive: true, displayModeBar: false });
}

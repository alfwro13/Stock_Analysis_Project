"""Curated study cards for the Glossary Learning feature.

Each entry mirrors one glossary term-box (templates/glossary/_*.html). term_title
must match the glossary <span class="term-title"> text exactly (entity-decoded,
whitespace-normalized) -- tests/test_glossary_learn_seed.py enforces this.

`explanation` is the term-box's own prose (HTML), reused verbatim so the Learn
quiz can show the real material instead of a bare answer string; `candle_html`
is the term-box's rendered candlestick markup, present only on cards that have one.

Adding a new glossary term? Add its term-box to the relevant templates/glossary/_*.html
partial, then add a matching entry here (section_id must be an existing LEVELS entry,
or a new one). See assets/glossary_learning.md for the full checklist.
"""

from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD

_PREDICTION_THRESHOLD_PCT = int(PREDICTION_RETURN_THRESHOLD * 100)  # matches page_routes.py's glossary context

LEVELS = [
    ("market-fundamentals", "Market Fundamentals"),
    ("candlesticks", "Candlestick Anatomy"),
    ("technicals", "Technical Analysis"),
    ("fundamentals", "Company Valuation"),
    ("strategies", "Trading Strategies"),
    ("behavioral-finance", "Investor Psychology"),
    ("machine-learning", "AI & Risk Metrics"),
    ("earnings-vol", "Earnings Volatility"),
    ("dip-radar", "Dip Radar"),
    ("bubble-radar", "Bubble Radar"),
    ("pairs-spread-monitor", "Pairs Spread Monitor"),
    ("forensic-screener", "Forensic Screener"),
    ("fx-drag", "FX Drag Analyzer"),
    ("performance-analytics", "Portfolio Tearsheet"),
    ("portfolio-optimizer", "Portfolio Optimizer"),
    ("stress-tester", "Stress Tester"),
    ("etf-predictor", "ETF Predictor"),
    ("sovereign-debt-auction", "Sovereign Debt Auction"),
    ("accounts", "Built-in Accounts"),
    ("backup-recovery", "Backup & Recovery"),
    ("security-access", "Security & Access"),
    ("workflow-monitor", "Workflow Monitor"),
    ("notification-routing", "Notification Routing"),
    ("methodology", "System Methodology"),
]

CARDS = [
    # --- market-fundamentals ---
    {
        "term_key": "stocks-and-shares",
        "section_id": "market-fundamentals",
        "term_title": "Stocks & Shares — What You Actually Own",
        "question": "What does owning a share of a company actually give you?",
        "answer": "A proportional slice of ownership in the company, including a claim on its assets",
        "distractors": [
            "A guaranteed fixed interest payment from the company each year",
            "The right to demand a refund of your investment at any time",
            "A management role in how the company is run day-to-day",
        ],
        "explanation": """<p>A <strong>stock</strong> (also called a <strong>share</strong>) represents a tiny slice of ownership in a company. When a company like Apple or Tesco wants to raise money to grow, it can divide itself into millions of tiny ownership pieces and sell them to the public on a stock exchange. Each piece is a share. If you own 100 shares of a company that has issued 1,000,000 shares in total, you own 0.01% of that company.</p>
<p>This matters because: as the company earns more profit and grows in value, each share becomes worth more. If the company struggles, each share is worth less. As a shareholder you also have a legal claim on a proportional slice of the company's remaining assets if it ever winds down — you rank below creditors (the people it owes money to), but above having nothing.</p>
<p>Shares are traded on stock exchanges — the New York Stock Exchange (NYSE), NASDAQ, and the London Stock Exchange (LSE) are the largest. During market hours, anyone can buy or sell shares at the current market price. The price moves every second based on the balance of buyers and sellers.</p>""",
    },
    {
        "term_key": "bonds-govt-corporate-iou",
        "section_id": "market-fundamentals",
        "term_title": "Bonds — Government and Corporate IOUs",
        "question": "Why do bond prices fall when interest rates rise?",
        "answer": "A bond's fixed coupon becomes less attractive compared to new bonds paying the higher prevailing rate",
        "distractors": [
            "Governments automatically reduce the face value of existing bonds when rates rise",
            "Rising rates increase the coupon paid on already-issued bonds, so buyers pay more upfront",
            "Bond maturities shorten automatically when interest rates increase",
        ],
        "explanation": """<p>A <strong>bond</strong> is a loan you make to a government or company. Instead of asking a bank, the borrower issues bonds and sells them to thousands of investors. In return, they promise to pay you a fixed annual interest payment (called the <strong>coupon</strong>) and return your original money (the <strong>face value</strong> or <strong>principal</strong>) when the bond matures — typically in 2, 5, 10, or 30 years.</p>
<p>Example: You buy a UK government bond (called a <strong>Gilt</strong>) with a £1,000 face value, 5% coupon, and 10-year maturity. You receive £50 per year for 10 years, then get your £1,000 back. Simple. The complication comes when you want to sell the bond before it matures — other buyers will only pay what makes the yield competitive with current interest rates. If rates rise, your old 5% bond is less attractive, so its market price falls. If rates fall, your bond is more attractive, so its market price rises. This is why <strong>bond prices and interest rates move in opposite directions</strong> — one of the most important relationships in all of finance.</p>
<p>Government bonds (UK Gilts, US Treasuries) are considered nearly risk-free because governments can raise taxes to repay debt. Corporate bonds carry more risk because companies can go bankrupt, so they must offer higher interest rates to attract buyers.</p>""",
    },
    {
        "term_key": "etfs-exchange-traded-funds",
        "section_id": "market-fundamentals",
        "term_title": "ETFs — Exchange-Traded Funds",
        "question": "What is the main advantage of a passive ETF over an actively managed fund?",
        "answer": "It tracks an index automatically, keeping fees far lower than most actively managed funds that fail to beat the index anyway",
        "distractors": [
            "It guarantees a fixed annual return regardless of market conditions",
            "It only holds a single company, reducing complexity",
            "It cannot be bought or sold during market hours, reducing volatility",
        ],
        "explanation": """<p>An <strong>ETF</strong> is a single security you can buy on a stock exchange that holds a <em>basket</em> of other assets inside it — stocks, bonds, commodities, or a mix. When you buy one share of the FTSE 100 ETF (ticker: ISF.L), you instantly own a proportional slice of all 100 largest UK companies at once, without buying 100 separate stocks.</p>
<p>ETFs exist for almost everything: the S&P 500 index (SPY), global small-cap stocks, government bonds, gold, oil, technology companies, dividend-focused stocks, and hundreds more. Most are <strong>passive</strong> — they simply track an index automatically — which keeps fees extremely low (often 0.03%–0.20% per year). This is the key advantage over actively managed funds, which charge 1–2% per year for a fund manager to pick stocks, most of whom fail to beat the index anyway.</p>
<p>ETFs trade like stocks (you can buy and sell any time during market hours), but they don't have the single-company risk of an individual stock. If one company in the basket collapses, it barely moves the ETF. This built-in <strong>diversification</strong> is why ETFs are considered suitable as core long-term holdings.</p>""",
    },
    {
        "term_key": "market-capitalisation",
        "section_id": "market-fundamentals",
        "term_title": "Market Capitalisation (Market Cap)",
        "question": "How is a company's market capitalisation calculated?",
        "answer": "Share price multiplied by the total number of outstanding shares",
        "distractors": [
            "Total annual revenue minus total operating costs",
            "The book value of all the company's assets minus liabilities",
            "Share price multiplied by average daily trading volume",
        ],
        "explanation": """<p>Market cap is the total market value of a company's outstanding shares, calculated as: <strong>share price × total number of shares</strong>. It tells you how much it would cost to buy the entire company at today's price.</p>
<p>Companies are broadly grouped by size:</p>
<p>Market cap does NOT tell you whether a company is profitable, how much debt it has, or whether it is cheap or expensive. A high market cap just means the market currently values it highly. Whether that valuation is justified is a separate question (which this app's fundamental analysis attempts to answer).</p>""",
    },
    {
        "term_key": "markets-page-region-tile-status",
        "section_id": "market-fundamentals",
        "term_title": "Markets Page — Region & Tile Status",
        "question": "What does a tile with a diagonal-stripe overlay on the Markets page indicate?",
        "answer": "The tile's market should be open but its price data hasn't refreshed as expected — worth a manual check",
        "distractors": [
            "The instrument has been permanently delisted",
            "The tile's region status is \"Some Open\"",
            "The price shown is from a currency-converted future contract",
        ],
        "explanation": """<p>The <strong>Markets</strong> page (global indexes, commodities, and FX) groups tiles by region — Europe, US, Asia, and Commodities &amp; FX — and gives each region a status badge: <strong>Open</strong> (every exchange in that region is currently trading), <strong>Some Open</strong> (at least one is trading but not all — e.g. Hong Kong is still open while Tokyo has already closed for the day), <strong>Pre-Market</strong>, or <strong>Closed</strong>.</p>
<p>Individual tiles are judged separately from their region badge, since a region can be "Some Open" while any one tile within it is fully live. A tile whose own market is closed is shown greyed out with its last available price — this is expected, not an error. If a tile's market <em>should</em> be open but its price hasn't refreshed as expected, it's shown greyed out with a diagonal-stripe overlay — a distinct signal that the data itself may be stuck, worth a manual refresh or a check of the background fetch.</p>""",
    },
    {
        "term_key": "bull-bear-markets",
        "section_id": "market-fundamentals",
        "term_title": "Bull Markets & Bear Markets",
        "question": "Why does managing downside risk matter more than chasing returns in a bear market?",
        "answer": "A 50% decline requires a 100% subsequent gain just to break even",
        "distractors": [
            "Bear markets always last longer than bull markets",
            "Regulators require reduced position sizes during declines of 20% or more",
            "Bull markets are defined as periods with lower trading volume than bear markets",
        ],
        "explanation": """<p>A <strong>bull market</strong> is a sustained period of rising prices — typically defined as a 20% rise from a recent low. Investor confidence is high, economies are growing, and most stocks go up. The US stock market was in a bull market for most of 2009–2022.</p>
<p>A <strong>bear market</strong> is the opposite — a sustained decline of 20% or more from a recent peak. These are periods of falling confidence, economic contraction, or financial crises. The COVID crash of March 2020 and the 2022 inflation-driven selloff are recent examples.</p>
<p>The terms come from the way each animal attacks: a bull thrusts its horns <em>upward</em>; a bear swipes its claws <em>downward</em>.</p>
<p>Critically: bear markets feel much worse than they look on a chart. A 50% decline requires a 100% subsequent gain just to break even. This asymmetry is why managing downside risk — using tools like VaR, stop-losses, and regime detection — matters so much more than chasing returns.</p>""",
    },
    {
        "term_key": "bid-ask-spread",
        "section_id": "market-fundamentals",
        "term_title": "Bid, Ask & Spread — The Hidden Cost of Every Trade",
        "question": "If you buy at the ask and immediately sell at the bid, what have you lost?",
        "answer": "The spread — the gap between the bid and ask price — before the stock even moves",
        "distractors": [
            "Nothing, since bid and ask are always identical for liquid stocks",
            "A regulatory transaction tax charged only on same-day trades",
            "The stock's average daily volatility",
        ],
        "explanation": """<p>When you look up a stock price, you see two numbers: the <strong>bid</strong> and the <strong>ask</strong> (sometimes called the <strong>offer</strong>).</p>
<p>Example: Bid = £9.98, Ask = £10.02, Spread = £0.04 (4 pence). If you buy immediately (a "market order"), you pay £10.02. If you then immediately sell, you get £9.98. You've lost 4 pence per share before the stock even moves — that's the spread cost. On a 1,000-share trade, that's a £40 immediate loss.</p>
<p>Spreads are tiny on large, heavily traded stocks (often 0.01–0.05%) but can be very wide on small-cap, thinly traded, or illiquid stocks (sometimes 1–5% or more). A wide spread means it is expensive to enter and exit positions, which changes how you should think about sizing and stop-losses.</p>
<p>A <strong>limit order</strong> lets you set the maximum price you'll pay (buy limit) or minimum price you'll accept (sell limit), protecting you from unfavourable fills — but you risk not getting filled if the price moves away.</p>""",
    },
    {
        "term_key": "options-calls-puts",
        "section_id": "market-fundamentals",
        "term_title": "Options — Calls & Puts",
        "question": "What does buying a call option give you the right to do?",
        "answer": "Buy 100 shares at the strike price before expiry",
        "distractors": [
            "Sell 100 shares at the strike price before expiry",
            "Receive a guaranteed dividend from the underlying stock",
            "Vote on company matters in place of the shares themselves",
        ],
        "explanation": """<p>An <strong>option</strong> is a contract that gives you the <em>right, but not the obligation</em> to buy or sell a specific stock at a specific price (the <strong>strike price</strong>) before a specific date (the <strong>expiry</strong>). You pay an upfront fee called the <strong>premium</strong> to own this right.</p>
<p>There are two types:</p>
<p>Options let traders express a view on direction with limited downside (just the premium paid), or protect an existing portfolio against a drop. They are also used to generate income (selling options to collect premiums). However, they expire worthless if your forecast is wrong — option buyers lose more often than they win, but can win big when they do.</p>
<p><strong>Implied Volatility (IV)</strong> is the key variable in option pricing — it represents the market's expectation of how much the stock will move before expiry. High IV means expensive options; low IV means cheap options. This app's Earnings Volatility engine compares IV-implied moves to historical actual moves to find mispricing opportunities around earnings.</p>""",
    },
    {
        "term_key": "dividends-dividend-yield",
        "section_id": "market-fundamentals",
        "term_title": "Dividends & Dividend Yield",
        "question": "Why can a rising dividend yield be a warning sign rather than good news?",
        "answer": "A rising yield often means the share price has fallen, which can signal the dividend is about to be cut",
        "distractors": [
            "A rising yield always means the company has just raised its dividend payment",
            "Dividend yield only rises when a company issues new shares",
            "A rising yield means the company has stopped paying corporate tax",
        ],
        "explanation": """<p>A <strong>dividend</strong> is a cash payment a company makes to its shareholders, typically from its profits. Not all companies pay dividends — fast-growing companies (like many tech companies) reinvest all profits back into the business instead. Mature, cash-generative companies (utilities, banks, consumer staples) tend to pay regular dividends, often quarterly.</p>
<p>The <strong>dividend yield</strong> is the annual dividend payment divided by the current share price, expressed as a percentage. If a stock is at £10 and pays £0.50 per year in dividends, the yield is 5%. A high yield sounds attractive, but beware: a rising yield often means the <em>share price has fallen</em>, which can signal the dividend is about to be cut. A "dividend trap" is when a company looks like it's offering 8–10% yield but then cuts its dividend as profits deteriorate.</p>
<p>When a stock goes <strong>ex-dividend</strong> (ex-div), buyers on that day and after are no longer entitled to the upcoming dividend. The share price usually drops by roughly the dividend amount on ex-div day — this is mechanical, not a sign of company trouble.</p>
<p>The <strong>payout ratio</strong> (dividends paid ÷ net income) shows how much of a company's profit is being handed back to shareholders versus retained for growth. Below 60% generally leaves comfortable room for the dividend to be sustained and even grown; above 80% leaves little buffer if profits dip even slightly. Checking the payout ratio against <em>free cash flow</em> rather than earnings is more reliable, since it's cash — not accounting profit — that actually funds the payment. A <strong>Dividend Aristocrat</strong> is a company that has raised its dividend for at least 25 consecutive years — a track record that, on its own, says a lot about the durability of the underlying business through multiple economic cycles.</p>""",
    },
    {
        "term_key": "stock-market-indices",
        "section_id": "market-fundamentals",
        "term_title": "Stock Market Indices — S&P 500, FTSE 100, and Others",
        "question": "Why does the S&P 500 move more from Apple, Microsoft, and NVIDIA than from smaller constituents?",
        "answer": "The index is weighted by market capitalisation, so larger companies have more influence",
        "distractors": [
            "Those three companies are excluded from the FTSE 100 for tax reasons",
            "The index only includes each company's most recent IPO price",
            "Smaller companies are removed from the index calculation during volatile periods",
        ],
        "explanation": """<p>An <strong>index</strong> is a calculated number that tracks the combined performance of a specific group of stocks. It gives you a single number to answer "how did the market do today?" without having to check every stock individually.</p>
<p>Index performance is tracked as a <strong>total return</strong> (including reinvested dividends) or as a <strong>price return</strong> (price movement only). Most quoted index numbers are price return — the actual investor return is higher once dividends are included.</p>""",
    },
    {
        "term_key": "dynamic-view-follow-the-sun",
        "section_id": "market-fundamentals",
        "term_title": "Dynamic View — \"Follow the Sun\" Ordering",
        "question": "What does Dynamic View do on the Markets page?",
        "answer": "Reorders regional sections by which region's trading session is currently most relevant",
        "distractors": [
            "Permanently hides regions whose markets are closed",
            "Converts all prices into the user's home currency",
            "Randomises tile order to reduce visual bias toward any one region",
        ],
        "explanation": """<p>The <a href="/markets">Markets page</a> can order its regional sections (Europe, US, Asia-Pacific, Commodities &amp; FX) two ways. <strong>Static view</strong> is a fixed order — Europe, then US, then Asia, then Commodities &amp; FX — that never changes. <strong>Dynamic view</strong> (the default) reorders those sections by which region's trading session is most relevant right now: at 5am UK time, Asia-Pacific ranks first while Europe (still closed) sits below it; by mid-morning UK time, Europe ranks first as its markets open; around UK lunchtime, the US moves to the top as New York opens. Commodities &amp; FX trade near-continuously, so in dynamic view that section always sits directly beneath whichever region currently ranks first, rather than being buried at the bottom.</p>
<p>The same dynamic-view concept is also available for the Market Pulse tiles shown on Portfolio, Watchlist, and Stock Detail pages (Settings → Markets &amp; Market Pulse) — off by default, so Market Pulse keeps its familiar fixed tile set unless you turn it on.</p>""",
    },
    {
        "term_key": "session-driven-region-ordering",
        "section_id": "market-fundamentals",
        "term_title": "Session-Driven Region Ordering",
        "question": "When two regions are open at the same time, which one ranks first in Dynamic View?",
        "answer": "The region that opened most recently",
        "distractors": [
            "The region with the largest number of tracked tickers",
            "Whichever region is alphabetically first",
            "The region with the highest total market capitalisation",
        ],
        "explanation": """<p>Dynamic view's ordering isn't based on a fixed clock schedule — it's derived from whether each region's underlying exchanges are actually <strong>open</strong>, in <strong>pre-market</strong>, or <strong>closed</strong> right now, so it stays correct across Daylight Saving Time changes and (for NYSE/LSE) market holidays. A region with any open exchange always ranks above a region that's merely opening soon, which in turn ranks above one that's fully closed. When two regions are open at the same time — for example Europe and Asia during their morning overlap, or Europe and the US during the afternoon overlap — the region that opened <strong>most recently</strong> ranks first, since that's the session investors are most likely to be watching right at that moment.</p>""",
    },
    {
        "term_key": "spot-futures-tiles",
        "section_id": "market-fundamentals",
        "term_title": "Spot/Futures Tiles",
        "question": "How does the Markets page show an index's cash and futures prices, compared to the Market Pulse widget?",
        "answer": "The Markets page shows both as two adjacent tiles, while Market Pulse shows only one, auto-swapping to futures when the cash market is closed",
        "distractors": [
            "Both surfaces always show only the futures price, never the cash price",
            "The Markets page hides the futures tile entirely until the cash market closes",
            "Market Pulse shows both tiles side by side, the same as the Markets page",
        ],
        "explanation": """<p>Some major indexes — the S&amp;P 500, Nasdaq 100, Dow Jones, Russell 2000, and Nikkei 225 — trade two ways: a <strong>cash/spot</strong> price only available while that exchange's regular session is open, and a <strong>futures</strong> contract that trades almost continuously, including outside regular hours. The Markets page shows both as two adjacent tiles, labeled "Index" and "Futures", so it's never ambiguous which instrument a price belongs to — the futures tile is colored purely by how fresh its own data is (it has no "market closed" state of its own, since it trades near-continuously), while the index tile still greys out while its cash market is shut. A "Hide Futures" checkbox in the United States section header (saved in a cookie, so it stays hidden on your next visit) drops the four US futures tiles (S&amp;P 500, Nasdaq 100, Dow, Russell 2000) if you'd rather only see the cash indexes — it has no effect on the Nikkei 225 futures tile in the Asia section. The compact Market Pulse widget (Portfolio/Watchlist/Stock Detail) is different: it shows only one of the two, automatically swapping to the futures price and label whenever the cash market is closed or in pre-market, since it has room for a single summary tile per index rather than a pair.</p>""",
    },
    {
        "term_key": "futures-contracts",
        "section_id": "market-fundamentals",
        "term_title": "Futures Contracts — Obligations, Not Options",
        "question": "What is the key difference between a futures contract and an options contract?",
        "answer": "A futures contract is a binding obligation for both parties, while an option only gives the holder the right, not the obligation, to act",
        "distractors": [
            "Futures can only be traded on weekends, options only on weekdays",
            "Options always cost more upfront than an equivalent futures contract",
            "Futures have no expiration date, unlike options",
        ],
        "explanation": """<p>A <strong>futures contract</strong> — like the ones shown alongside spot indexes in the Spot/Futures Tiles above — is an agreement to buy or sell an asset at a fixed price on a specific future date. The critical difference from an option: a future is a binding <em>obligation</em> for both sides, not a right one side can choose to walk away from. Futures are exchange-traded and standardised (unlike a private, customised <strong>forward contract</strong>), and gains/losses are settled daily against the account (<strong>mark-to-market</strong>) rather than only at expiry.</p>
<p>Futures trade on margin — a relatively small deposit controls a much larger contract value, so price moves are amplified into much larger percentage gains or losses on the capital actually posted. <strong>Hedgers</strong> use futures to lock in a price and offset a real, existing exposure (an airline locking in fuel costs); <strong>speculators</strong> use them purely to bet on price direction without ever wanting the underlying asset delivered; <strong>arbitrageurs</strong> trade the gap between the futures price and the spot price itself.</p>
<p>The relationship between the futures price and the expected future spot price has a name: <strong>contango</strong> is when futures trade above the current spot price (normal for most financial futures, reflecting the cost of carrying the position over time), <strong>backwardation</strong> is the reverse. This matters most for anyone repeatedly "rolling" a futures position forward as each contract nears expiry — in contango, each roll is systematically a small loss, which is why a futures-based ETF can underperform the spot index it's meant to track over long periods even when the spot index itself is flat.</p>""",
    },
    {
        "term_key": "short-selling-leverage",
        "section_id": "market-fundamentals",
        "term_title": "Short Selling & Leverage",
        "question": "Why is a short seller's potential loss described as theoretically unlimited?",
        "answer": "There is no ceiling on how high a share price can rise before the short seller is forced to buy it back",
        "distractors": [
            "Short sellers are required to hold the position for at least one year",
            "Brokers charge an unlimited fee for every short position",
            "Short selling is only available on stocks with unlimited trading volume",
        ],
        "explanation": """<p><strong>Short selling</strong> bets on a price decline by reversing the usual buy-then-sell order: borrow shares (via your broker), sell them immediately at today's price, then later buy them back to return to the lender — profiting if the buyback price is lower than the original sale price. Unlike a normal long position, where the most you can lose is what you paid, a short position's loss is theoretically unlimited, because there's no ceiling on how high a share price can rise before you're forced to buy it back.</p>
<p>A <strong>short squeeze</strong> is what happens when a heavily-shorted stock starts rising: short sellers facing mounting losses rush to buy back shares to close their positions, and that forced buying itself pushes the price higher still, forcing yet more shorts to cover — a self-reinforcing spiral upward. This is the mechanic behind every Bull Trap and Bear Trap pattern described in the Trading Strategies section: both are, at their core, a batch of short sellers or recent buyers being forced out of a crowded position.</p>
<p><strong>Leverage</strong> — trading with borrowed money via a margin account — magnifies gains and losses in direct proportion to how much is borrowed. Margin accounts require maintaining minimum equity (typically an initial requirement around 50%, with a maintenance floor around 25%); if the account's equity falls below that floor, a <strong>margin call</strong> forces you to deposit more cash or have the position liquidated automatically, often at the worst possible moment in a fast decline.</p>""",
    },
    {
        "term_key": "what-is-a-portfolio",
        "section_id": "market-fundamentals",
        "term_title": "What Is a Portfolio?",
        "question": "Why does diversification often fail during a market crash, exactly when you need it most?",
        "answer": "During crashes, most asset correlations converge toward 1.0 as panic selling hits everything simultaneously",
        "distractors": [
            "Diversification rules are suspended by regulators during a declared market crash",
            "Crashes only affect single-asset portfolios, not diversified ones",
            "Correlation between assets always reaches exactly 0 during a crash",
        ],
        "explanation": """<p>A <strong>portfolio</strong> is your complete collection of investments — every stock, ETF, bond, and cash position you hold. The concept of portfolio management is about more than picking good individual stocks; it is about how all your holdings interact with each other to produce a combined risk and return profile.</p>
<p><strong>Diversification</strong> is the central principle: spreading holdings across different companies, sectors, geographies, and asset classes so that a single bad event does not devastate everything at once. When oil companies fall because of an oversupply shock, utility companies or gold holdings might hold steady or rise. This non-correlation between assets is what makes a diversified portfolio less volatile than any individual holding within it. As a rule of thumb, around 15–20 well-spread stocks eliminates most of the risk that's specific to individual companies (the remaining risk is the market-wide risk no amount of stock-picking diversifies away); beyond that, adding more holdings keeps diversifying but with rapidly diminishing returns. Diversifying by <strong>factor</strong> — blending value, growth, momentum, quality, and small/large-cap exposure — is a deeper layer of the same idea, since these factors also tend to take turns outperforming and underperforming each other.</p>
<p><strong>Correlation</strong> is the measure of how closely two assets move together. A correlation of +1.0 means they move in perfect lockstep; −1.0 means they move in exact opposites; 0 means they are independent. Truly low-correlation assets are rare — during market crashes, most correlations converge toward 1.0 as panic selling hits everything. This is the "diversification breaks down when you need it most" problem, which this app's Stress Tester and X-ray engine are specifically designed to illuminate.</p>""",
    },
    {
        "term_key": "modern-portfolio-theory",
        "section_id": "market-fundamentals",
        "term_title": "Modern Portfolio Theory — Risk-Return Tradeoff",
        "question": "What does it mean for a portfolio to sit 'below' the Efficient Frontier?",
        "answer": "It's taking on more risk than necessary for its expected return, or getting less return than possible for its risk level — free improvement is available",
        "distractors": [
            "The portfolio contains only bonds and no equities",
            "The portfolio has underperformed the S&P 500 for at least one calendar year",
            "The portfolio's total value has fallen below its original cost basis",
        ],
        "explanation": """<p><strong>Modern Portfolio Theory (MPT)</strong> is the foundational idea behind treating a portfolio as a single system rather than a pile of separate bets: total portfolio risk isn't just the average of each holding's own volatility, it also depends heavily on how those holdings move <em>relative to each other</em> (their correlation, see "What Is a Portfolio?" above). Because of this, combining assets that don't move in lockstep can lower the portfolio's overall risk without necessarily lowering its expected return — sometimes called diversification's "free lunch," since you're not giving anything up to get it.</p>
<p>Plotting every possible portfolio's risk against its expected return traces out the <strong>Efficient Frontier</strong> — the set of portfolios offering the best possible return for a given level of risk (or the lowest risk for a given return). Any portfolio sitting below that frontier is leaving free return on the table for the risk it's taking. This is exactly what this app's Portfolio Optimizer tool computes directly from your own holdings — see its own glossary section for the Min-Variance and Max-Sharpe portfolios it derives from the frontier.</p>
<p>MPT distinguishes <strong>strategic allocation</strong> (your long-term target mix across asset classes, driven by goals and risk tolerance — the single biggest driver of long-run returns) from <strong>tactical allocation</strong> (smaller, shorter-term tilts away from that target to exploit a specific view). The theory's well-known weakness: it assumes correlations and expected returns are stable and that markets behave "normally" — assumptions that hold reasonably well in calm periods and break down precisely during the crises where risk management matters most.</p>""",
    },
    {
        "term_key": "portfolio-rebalancing",
        "section_id": "market-fundamentals",
        "term_title": "Portfolio Rebalancing",
        "question": "Why does a portfolio drift away from its target allocation even without you making any trades?",
        "answer": "Winning positions grow as a share of the portfolio and losing ones shrink, purely through market price movement",
        "distractors": [
            "Brokers automatically adjust allocations every quarter without notice",
            "Target allocations are legally required to change every year",
            "Dividends are automatically reinvested into a different asset class",
        ],
        "explanation": """<p>Over time, winning positions grow as a share of a portfolio and losing ones shrink, so even a portfolio built to a careful target allocation drifts away from it purely through market movement — a 60/40 stock/bond split can silently become 75/25 after a strong few years for stocks. <strong>Rebalancing</strong> is periodically selling some of what's grown and buying more of what's shrunk to bring the mix back to target, which mechanically enforces a "sell high, buy low" discipline regardless of what the market is doing at the time.</p>
<p>Two common triggers: <strong>calendar-based</strong> rebalancing (review and reset on a fixed schedule — monthly, quarterly, annually) is simple and predictable, while <strong>threshold-based</strong> rebalancing (reset only once an allocation has drifted beyond a set band, e.g. ±5% from target) trades that predictability for reacting only when drift has become meaningful, generating less unnecessary trading.</p>
<p>In a taxable account, rebalancing isn't free — selling an appreciated position can trigger a taxable capital gain, so many investors tolerate a wider drift band in taxable accounts than in tax-advantaged ones, or prefer to rebalance by directing new contributions toward the underweight asset rather than selling the overweight one outright.</p>""",
    },
    {
        "term_key": "position-sizing",
        "section_id": "market-fundamentals",
        "term_title": "Position Sizing — How Much to Risk Per Trade",
        "question": "Why does this app's Position Sizing tool give a volatile stock a smaller position than a calm one, for the same risk percentage?",
        "answer": "Because sizing is volatility-adjusted (ATR-based) — a wider stop distance on a volatile stock means fewer shares are needed to risk the same fixed amount of money",
        "distractors": [
            "Volatile stocks are excluded from position sizing entirely",
            "The tool always assigns exactly the same number of shares regardless of price",
            "Volatile stocks require a higher percentage of the account to be risked",
        ],
        "explanation": """<p><strong>Position sizing</strong> answers a different question from "should I buy this stock" — it answers "how many shares." Two positions in the same stock can have wildly different risk depending purely on size: a £500 position and a £5,000 position in an identical stock that falls 10% lose £50 and £500 respectively, from the exact same decision. Sizing, not stock-picking, is often the single biggest lever an investor actually controls trade-by-trade.</p>
<p>This app's own Position Sizing tool (Settings → Position Sizing Defaults) uses a <strong>fixed-fractional, volatility-adjusted</strong> method: you set a fixed percentage of your account to risk per trade (e.g. 1%), and the number of shares is sized so that if the stock falls to its <a href="#technicals">ATR Stop-Loss</a>, the loss equals exactly that fixed percentage — a volatile stock (wide ATR) gets a smaller position, a calm stock (narrow ATR) gets a larger one, for the same amount of pounds at risk either way. This is a deliberately more conservative, easier-to-reason-about relative of the famous <strong>Kelly Criterion</strong> (<code>f = (bp − q) / b</code>, sizing a bet as a fraction of capital based on your edge and win probability) — Kelly's theoretically "optimal" full-size bet is aggressive enough that most practitioners only ever risk a fraction of it ("half-Kelly" or smaller), because Kelly assumes your edge estimate is exactly correct, and real-world edge estimates rarely are.</p>
<p>The practical benefit of any fixed-fractional approach: no single trade, even a full stop-out, can meaningfully damage the account, which is what actually lets you survive a losing streak long enough for a real edge (if you have one) to show up in the results.</p>""",
    },
    {
        "term_key": "value-investing-margin-of-safety",
        "section_id": "market-fundamentals",
        "term_title": "Value Investing — Margin of Safety",
        "question": "What is the purpose of Benjamin Graham's 'margin of safety' concept?",
        "answer": "Buying well below estimated intrinsic value so that being somewhat wrong, or an unexpected setback, doesn't turn into a loss",
        "distractors": [
            "Guaranteeing a fixed minimum annual return on every purchase",
            "Ensuring a stock's price never falls below its purchase price",
            "A legal requirement brokers must disclose before executing a trade",
        ],
        "explanation": """<p><strong>Value investing</strong> means buying a stock for meaningfully less than what the underlying business is actually worth, then waiting for the market to notice. The central idea, popularised by Benjamin Graham, is the <strong>margin of safety</strong>: only buy when the price sits well below your estimate of intrinsic value — often 30–50% below — so that being somewhat wrong about that estimate, or an unexpected setback, doesn't turn into a loss. Graham imagined the market as "Mr. Market," a manic-depressive business partner who quotes you a wildly different price every day; a value investor's job is to buy from him when he's fearful and sell to him when he's euphoric, not to trust his mood as information about what the business is actually worth.</p>
<p>Warren Buffett extended Graham's purely numbers-driven screen (low P/E, low P/B, years of consistent earnings) toward business quality: his preference shifted to "a wonderful company at a fair price" over "a mediocre company at a bargain price," placing heavy weight on durable competitive advantages (see Economic Moats) and staying within his own circle of competence.</p>
<p>The central risk is a <strong>value trap</strong> — a stock that looks statistically cheap but is cheap because the business is genuinely deteriorating, not because the market is being irrational. Distinguishing "temporarily out of favour" from "structurally broken" is the hard part of value investing, and is exactly why this app's Financial Safety and Forensic Screener checks exist alongside pure valuation metrics like PEG.</p>""",
    },
    {
        "term_key": "growth-investing",
        "section_id": "market-fundamentals",
        "term_title": "Growth Investing",
        "question": "Why are growth stocks more sensitive to rising interest rates than value stocks?",
        "answer": "More of their valuation comes from profits expected far in the future, and higher rates discount those distant profits more heavily",
        "distractors": [
            "Growth stocks are legally required to hold more cash reserves",
            "Growth companies always carry more short-term debt than value companies",
            "Interest rates only affect companies that pay a dividend",
        ],
        "explanation": """<p>Where value investing pays a low price for the business as it is today, <strong>growth investing</strong> pays a premium price for what the business is expected to become — accepting a high P/E (often 30–50+) in exchange for revenue growing well above the market average, a large addressable market still to capture, and a business model that scales (margins improve as revenue grows, rather than staying flat).</p>
<p>The trade-off is asymmetric risk: because so much of a growth stock's valuation rests on future growth actually materialising, even a modest earnings miss or a slowdown in growth rate can trigger an outsized price decline — the market isn't just repricing one disappointing quarter, it's repricing the whole growth story. Growth stocks are also more sensitive to interest rates than value stocks, since a larger share of their value comes from profits far in the future, and higher rates discount those distant profits more heavily.</p>
<p>Value and growth aren't mutually exclusive philosophies so much as two ends of a spectrum — many real portfolios (and this app's own Quality Compounder / GARP Tenbagger report-screen tags) deliberately blend elements of both, looking for growth that isn't yet fully priced in rather than picking one camp exclusively.</p>""",
    },
    {
        "term_key": "common-vs-preferred-stock",
        "section_id": "market-fundamentals",
        "term_title": "Common Stock vs Preferred Stock",
        "question": "What do preferred shareholders typically get in exchange for giving up voting rights?",
        "answer": "A fixed dividend that must be paid before common shareholders, and priority over common stock in a liquidation",
        "distractors": [
            "A guarantee that the share price can never fall below its issue price",
            "Automatic conversion into bonds if the company's share price declines",
            "The right to override the board of directors on major decisions",
        ],
        "explanation": """<p>Almost every share this app tracks is <strong>common stock</strong> — the standard form of ownership described above: voting rights on major company decisions, a dividend only if and when the board declares one, and the lowest-ranking claim on assets if the company is wound up (behind every creditor and behind preferred shareholders).</p>
<p><strong>Preferred stock</strong> is a hybrid instrument, part share and part bond. It pays a fixed dividend that must be paid before common shareholders receive anything, and it sits ahead of common stock (though behind bonds and other debt) in a liquidation. In exchange for that priority, preferred shares usually carry no voting rights, and many are <strong>callable</strong> — the company can buy them back at a set price — which caps how much their price can rise even if the business does very well.</p>
<p>The practical takeaway: common stock gives you unlimited upside and the highest risk; preferred stock trades some of that upside for a bond-like, more predictable income stream and a safer place in the capital structure.</p>""",
    },
    {
        "term_key": "order-types-market-limit-stop",
        "section_id": "market-fundamentals",
        "term_title": "Order Types — Market, Limit & Stop",
        "question": "What is the main trade-off of using a limit order instead of a market order?",
        "answer": "You get control over the execution price, but risk the order never filling if the market doesn't reach your price",
        "distractors": [
            "Limit orders always execute faster than market orders",
            "Limit orders guarantee the best price available anywhere in the market",
            "Limit orders can only be used to sell, never to buy",
        ],
        "explanation": """<p>A <strong>market order</strong> says "buy/sell right now, at whatever the current price is." It fills almost instantly during trading hours but gives you no control over the exact price — on a fast-moving or illiquid stock, the fill can be noticeably worse than the price you saw a second earlier.</p>
<p>A <strong>limit order</strong> says "buy/sell, but only at this price or better." A buy limit only fills at your price or lower; a sell limit only fills at your price or higher. You get certainty on price, at the cost of certainty on execution — if the market never reaches your limit, the order simply never fills.</p>
<p>A <strong>stop order</strong> (stop-loss) sits dormant until the price touches a trigger level, at which point it converts into a market order. It's the standard way to cap a loss automatically without watching the screen — set a stop below your entry, and a sharp decline sells you out at (approximately) that level rather than continuing to ride the position down.</p>""",
    },
    {
        "term_key": "market-regulation-investor-protection",
        "section_id": "market-fundamentals",
        "term_title": "Market Regulation & Investor Protection",
        "question": "What do investor protection schemes like the US SIPC or UK FSCS actually cover?",
        "answer": "Losses if your broker/custodian fails financially — not losses from your investments simply falling in value",
        "distractors": [
            "Any loss on an investment, including normal market declines",
            "Only losses caused by a company you're invested in going bankrupt",
            "Losses from poor investment decisions made on the advice of a regulated broker",
        ],
        "explanation": """<p>Stock markets are regulated to keep them fair and to make sure investors get accurate information. In the US, the <strong>Securities and Exchange Commission (SEC)</strong> is the primary regulator — it requires public companies to file regular disclosures (an annual <strong>10-K</strong>, quarterly <strong>10-Q</strong>s, and an <strong>8-K</strong> within days of any material event) and enforces rules against fraud and insider trading. <strong>FINRA</strong>, a self-regulatory body, oversees the brokers and dealers who execute trades. In the UK, the equivalent role is split between the <strong>Financial Conduct Authority (FCA)</strong>, which regulates firms and market conduct, and the London Stock Exchange's own listing rules for disclosure.</p>
<p>If a broker itself fails financially, investor protection schemes step in — the US <strong>SIPC</strong> covers up to $500,000 per customer, and the UK <strong>FSCS</strong> covers up to £85,000. Crucially, neither scheme protects you against your investments simply losing value — they only protect against the broker/custodian going bust while holding your assets.</p>
<p>None of this eliminates investment risk — a company can still fail, and its share price can still go to zero — but it does mean a public company can't legally hide its financial condition from you indefinitely, and gives you recourse if a broker mishandles your account.</p>""",
    },
    {
        "term_key": "compound-growth",
        "section_id": "market-fundamentals",
        "term_title": "Compound Growth — Why Time Is Your Biggest Edge",
        "question": "Why does starting to invest earlier often beat investing larger amounts later?",
        "answer": "Earlier money has more years for its returns to generate their own returns, so the compounding base keeps growing for longer",
        "distractors": [
            "Stock markets always perform better in the years further in the past",
            "Brokers charge lower fees to investors with a longer account history",
            "Tax rules only apply to investments held for less than 20 years",
        ],
        "explanation": """<p><strong>Compounding</strong> is what happens when investment returns start earning returns of their own. £1,000 growing at 5% a year under <em>simple</em> interest earns a flat £50 every year. Under <em>compound</em> growth, year one still earns £50 — but year two earns 5% of £1,050 (£52.50), year three earns 5% of £1,102.50, and so on. The gap between simple and compound growth is small at first and enormous after two or three decades, because the base amount doing the compounding keeps growing.</p>
<p>Two variables drive the outcome: <strong>time</strong> and <strong>rate of return</strong>. Time matters more than most people expect — someone who invests £200/month starting at 25 can end up with more at retirement than someone investing double that (£400/month) starting at 45, purely because the earlier money had more years to compound. Rate matters too: £1,000 held for 30 years grows to roughly £4,300 at 5%, but to roughly £17,400 at 10% — a doubling of the rate more than quadruples the outcome over that horizon.</p>
<p>Two things quietly work against compounding: <strong>fees</strong> (even a 1% annual fee, compounded over decades, removes a surprisingly large chunk of the final total) and <strong>inflation</strong> (a return that doesn't beat inflation isn't growing real purchasing power, however good the headline number looks). Reinvesting dividends and interest — rather than withdrawing them — is what actually keeps the compounding engine running.</p>""",
    },
    {
        "term_key": "risk-tolerance",
        "section_id": "market-fundamentals",
        "term_title": "Risk Tolerance — How Much Volatility Can You Stomach?",
        "question": "Why can someone's financial capacity for risk and their psychological willingness to take risk disagree?",
        "answer": "Financial capacity depends on objective factors like time horizon and income stability, while willingness is an emotional reaction to seeing losses — the two don't always move together",
        "distractors": [
            "They never disagree — financial capacity and psychological willingness are the same thing by definition",
            "Financial capacity is fixed at birth while willingness changes every year automatically",
            "Only professional investors have a psychological willingness component at all",
        ],
        "explanation": """<p><strong>Risk tolerance</strong> is how much portfolio volatility and drawdown you can handle without abandoning your plan — a mix of psychological willingness (can you emotionally sit through a 30% decline without panic-selling?) and financial capacity (can you actually afford one, given your time horizon, income stability, and emergency savings?). The two don't always agree: someone with a long time horizon and stable income may have high financial capacity for risk but low emotional tolerance for watching their balance swing, or vice versa. Planning around the lower of the two is usually the safer default.</p>
<p>Risk tolerance sits on a spectrum, from <strong>risk-averse</strong> (capital preservation first — cash, short-dated bonds) through <strong>conservative</strong> and <strong>moderate</strong> (a blended stock/bond split, often used as a default balanced allocation) to <strong>aggressive</strong> and <strong>speculative</strong> (equity-heavy or concentrated positions accepting large swings for higher expected return). A longer time horizon, stable income, and a solid emergency fund generally support sitting further along the aggressive end, since there's less chance of being forced to sell at a bad moment.</p>
<p>A simple practical check some investors use: picture your current allocation losing a third of its value in a matter of weeks — if that scenario would make you sell everything (crystallising the loss) rather than sit tight, your actual risk tolerance is lower than your portfolio currently assumes, regardless of what a questionnaire might have said beforehand.</p>""",
    },
    # --- candlesticks ---
    {
        "term_key": "reading-a-candlestick-basics",
        "section_id": "candlesticks",
        "term_title": "Reading a Candlestick — The Basics",
        "question": "What does a long lower wick on a candlestick suggest?",
        "answer": "Sellers pushed the price down sharply intraday, but buyers forcefully rejected that low before the close",
        "distractors": [
            "The stock traded at the same price for the entire session",
            "The company announced a stock split during that session",
            "Trading volume was below the 20-day average for that session",
        ],
        "explanation": """<p>Every bar on a price chart represents a single time period (one day for daily charts). A <strong>candlestick</strong> encodes four data points in a single visual element: the <strong>open</strong> (price at the start of the period), the <strong>close</strong> (price at the end), the <strong>high</strong> (highest price reached), and the <strong>low</strong> (lowest price reached).</p>
<p>The thick rectangular <strong>body</strong> spans from open to close. If the close was <em>above</em> the open, the body is green (or white in older charts) — a bullish candle, the price went up. If the close was <em>below</em> the open, the body is red (or black) — a bearish candle, the price went down.</p>
<p>The thin lines extending above and below the body are called <strong>wicks</strong> (or shadows). The upper wick shows how high the price went during the period before it fell back to the close. The lower wick shows how low it went before buyers pushed it back up. Long wicks are informative: a long lower wick means sellers pushed the price down sharply intraday but buyers forcefully rejected that low before the period closed — a sign of buying pressure.</p>
<p>Candlestick patterns are combinations of one, two, or three consecutive candles whose shapes and relative positions tell a story about the battle between buyers and sellers. <strong>Bullish</strong> patterns suggest buyers are taking control; <strong>bearish</strong> patterns suggest sellers are. They are most reliable at significant price levels (prior support/resistance, Bollinger Band extremes) and after a sustained trend, not in the middle of a ranging market.</p>""",
    },
    {
        "term_key": "hammer-bullish-rejection",
        "section_id": "candlesticks",
        "term_title": "🔨 The Hammer (Bullish Rejection)",
        "question": "What makes a Hammer pattern more significant?",
        "answer": "It appears after a multi-day downtrend, with above-average volume and next-day follow-through above its body",
        "distractors": [
            "It appears in the middle of a sideways, rangebound market with no trend",
            "It has a long upper wick and a small body near the bottom of its range",
            "It occurs on unusually low trading volume with no confirmation needed",
        ],
        "explanation": """<p>The Hammer is a single-candle pattern with a small body near the top of its range and a lower wick at least twice the length of the body. It appears after a decline. The interpretation: at some point during the session, sellers took control and drove the price significantly lower — but then buyers overwhelmed them and pushed the price back up to near the open. This intraday rejection of the lows is a real-time signal that demand is entering at these prices.</p>
<p>A Hammer is more significant when: (1) it appears after a multi-day downtrend, not a random single-day dip; (2) the volume on the Hammer day is above average, confirming the buying was genuine and broad; (3) the next day opens and closes above the Hammer's body, providing follow-through confirmation. A Hammer with no volume and no follow-through is much weaker.</p>
<p>The <strong>Inverted Hammer</strong> is the mirror image — small body at the bottom, long upper wick — and carries similar but slightly weaker bullish implications when it appears after a decline.</p>""",
        "candle_html": """<div class="candle-display">
                    <div class="candle-group">
                        <div class="candle">
                            <div class="wick h-100 t-0"></div>
                            <div class="body-green h-30 t-10"></div>
                        </div>
                    </div>
                    <div class="candle-explanation">Sellers drove price down hard during the session, but institutional buyers rejected the lows and pushed it back up before the close. The tiny body near the top and long lower wick is the rejection signal.</div>
                </div>""",
    },
    {
        "term_key": "bullish-engulfing",
        "section_id": "candlesticks",
        "term_title": "🐂 Bullish Engulfing",
        "question": "What defines a Bullish Engulfing pattern?",
        "answer": "Day 2's green body completely swallows Day 1's red body after opening lower",
        "distractors": [
            "Two consecutive candles of identical size and colour",
            "A single candle with a very long upper wick and small body",
            "Day 2 gaps up and closes without any wicks at all",
        ],
        "explanation": """<p>The <strong>Bullish Engulfing</strong> is a two-candle pattern. Day 1 is a red (bearish) candle continuing the existing downtrend. Day 2 opens <em>below</em> Day 1's close (a gap down, making the opening look even weaker), then reverses so powerfully that it closes <em>above</em> Day 1's open — the green body "engulfs" the entire red body.</p>
<p>This pattern is powerful because of what it reveals about the balance of buyers and sellers. Day 1 shows sellers in control. Day 2 opens even lower, suggesting sellers are still pressing the advantage — but then buyers reverse the entire move and add more. The sellers who aggressively pushed it down on both days are now trapped in losing short positions, and many of them will cover (buy) when it keeps rising, adding fuel to the rally.</p>
<p>Strength factors: the larger the engulfing body relative to Day 1, the more powerful the reversal signal. Volume should be significantly higher on Day 2 than Day 1. The pattern is most significant at a well-established support level, a Bollinger Band lower boundary, or after a long declining trend.</p>""",
        "candle_html": """<div class="candle-display">
                    <div class="candle-group">
                        <div class="candle">
                            <div class="wick h-60 t-20"></div>
                            <div class="body-red h-40 t-30"></div>
                        </div>
                        <div class="candle">
                            <div class="wick h-100 t-0"></div>
                            <div class="body-green h-80 t-10"></div>
                        </div>
                    </div>
                    <div class="candle-explanation">Day 2's green body completely swallows Day 1's red body — buyers so aggressively overwhelmed sellers that they erased the prior day's entire decline and more.</div>
                </div>""",
    },
    {
        "term_key": "morning-star-3-candle",
        "section_id": "candlesticks",
        "term_title": "🌅 Morning Star (3-Candle)",
        "question": "What is the sequence of the three candles in a Morning Star pattern?",
        "answer": "A large red body (panic), a small indecision candle like a Doji, then a large green body closing above Day 1's midpoint",
        "distractors": [
            "Three consecutive large green candles each closing at a new high",
            "A Doji, followed by two large red candles",
            "A large green candle followed by two small red candles of decreasing size",
        ],
        "explanation": """<p>The <strong>Morning Star</strong> is the highest-conviction three-candle bullish reversal pattern in Japanese candlestick analysis. It requires three specific candles in sequence, each playing a role in the transition from panic to recovery.</p>
<p><strong>Day 1 (The Panic):</strong> A large red body closes near the low of its range, representing aggressive selling and no sign of recovery. The trend appears fully intact.</p>
<p><strong>Day 2 (The Indecision):</strong> A very small body — often a Doji (where open and close are nearly equal) — that gaps down from Day 1's close. The tiny body reveals that sellers could not extend the decline and buyers stepped in at the lows. Neither side is winning; the market is frozen with indecision at the bottom of the move.</p>
<p><strong>Day 3 (The Reversal):</strong> A large green body that gaps up from Day 2 and closes above the midpoint of Day 1's body. This is the confirmation: buyers are firmly in control, overwhelmed sellers completely, and the downtrend has reversed.</p>
<p>The three-day sequence tells a coherent story: panic (D1), exhaustion/transition (D2), reversal (D3). This narrative coherence is why the Morning Star is considered "the crown jewel of bottom-fishing" — it not only signals a reversal but describes the psychology of how it happened.</p>""",
    },
    {
        "term_key": "three-white-soldiers-3-candle",
        "section_id": "candlesticks",
        "term_title": "🪖 Three White Soldiers (3-Candle)",
        "question": "What distinguishes Three White Soldiers from a simple three-day rally?",
        "answer": "Each candle opens within the prior candle's body and closes near its session high, showing sustained buying rather than gap-driven moves",
        "distractors": [
            "Each candle must gap up significantly above the prior day's close",
            "The pattern requires declining volume across all three sessions",
            "Each candle must have a long lower wick",
        ],
        "explanation": """<p>The <strong>Three White Soldiers</strong> pattern is three consecutive large bullish candles where: each opens within (or above) the prior candle's body, and each closes near its session high (short upper wicks). This structure indicates sustained buying across three full sessions — not a single frenzied day followed by profit-taking.</p>
<p>The key distinction from a simple three-day rally: the <em>open within the prior body</em> requirement. If Day 2 opens far above Day 1's close (a large gap), it could signal exhaustion and overnight buyers who might sell during the day. When each day opens modestly and then buys throughout the session to close near the high, it demonstrates consistent, methodical institutional accumulation rather than retail-driven gap-and-fade behaviour.</p>
<p>The pattern is most significant after a prolonged decline or at an important technical support level. After a long trend higher, Three White Soldiers can be an <em>overbought</em> warning rather than a continuation signal — context matters.</p>""",
        "candle_html": """<div class="candle-display">
                    <div class="candle-group">
                        <div class="candle">
                            <div class="wick h-20 t-0"></div>
                            <div class="body-green h-50 t-20"></div>
                            <div class="wick h-10 t-70"></div>
                        </div>
                        <div class="candle">
                            <div class="wick h-15 t-0"></div>
                            <div class="body-green h-65 t-15"></div>
                            <div class="wick h-10 t-80"></div>
                        </div>
                        <div class="candle">
                            <div class="wick h-10 t-0"></div>
                            <div class="body-green h-80 t-10"></div>
                            <div class="wick h-10 t-90"></div>
                        </div>
                    </div>
                    <div class="candle-explanation">Three consecutive strong green candles, each opening within the prior body and closing at or near its high. Sustained institutional buying — not a one-day spike.</div>
                </div>""",
    },
    {
        "term_key": "harami-cross",
        "section_id": "candlesticks",
        "term_title": "🌱 Bullish Harami Cross / 🕸️ Bearish Harami Cross (2-Candle)",
        "question": "What makes the Harami Cross variant stronger than a regular Harami?",
        "answer": "The second candle is a Doji, showing the prior trend was completely stalled with no net movement",
        "distractors": [
            "The second candle must be larger than the first candle's entire body",
            "It requires three additional candles of confirmation before it counts",
            "The pattern only occurs on weekly charts, never daily",
        ],
        "explanation": """<p>The word <strong>Harami</strong> means "pregnant" in Japanese — the pattern is a large candle "carrying" a small one inside it, like a mother carrying a child. The Harami Cross is the strongest variant: the second candle is a <strong>Doji</strong> (the open and close are nearly identical, producing a cross shape rather than a body), and it fits entirely within the range of the prior large candle's body.</p>
<p>A <strong>Doji</strong> by itself signals complete indecision — the session opened, went up and down, and ended where it started. When this Doji forms inside the previous large candle's body, the message is amplified: the dominant trend (represented by the large candle) has been so thoroughly halted that the entire session produced no net movement whatsoever. The battle between buyers and sellers produced a draw.</p>
<p>For the <strong>Bullish Harami Cross</strong>: the large candle is red (downtrend), the Doji forms inside it. The powerful selling of Day 1 has been completely stalled — sellers could not extend the move even a single point. This stalling often precedes a reversal.</p>
<p>For the <strong>Bearish Harami Cross</strong>: the large candle is green (uptrend), the Doji forms inside it. The buying of Day 1 has been completely stalled — buyers who drove a strong session could not maintain any momentum into Day 2. This can precede a top.</p>
<p>The Harami Cross is weaker than the Engulfing or Morning Star patterns — it shows the trend has paused, not definitively reversed. Always wait for Day 3 confirmation before acting on it.</p>""",
    },
    {
        "term_key": "piercing-line-2-candle",
        "section_id": "candlesticks",
        "term_title": "🗡️ Piercing Line (2-Candle)",
        "question": "What threshold must Day 2 close above for a Piercing Line pattern?",
        "answer": "The midpoint of Day 1's red candle body",
        "distractors": [
            "Day 1's opening price exactly",
            "The 200-day moving average",
            "Day 1's highest intraday wick",
        ],
        "explanation": """<p>The <strong>Piercing Line</strong> is a two-candle bullish reversal pattern. Day 1 is a bearish candle (red) that continues the downtrend. Day 2 opens below Day 1's close — initially looking like the weakness is extending — but then buyers step in and push it back up, closing above the midpoint of Day 1's body (i.e., the Day 2 close "pierces" halfway or more into Day 1's red body).</p>
<p>The key threshold is the <em>midpoint</em> of Day 1's body. Closing just above Day 1's low is not meaningful; closing above the midpoint shows that buyers covered more than half of the prior day's selling — that is real counter-trend force. If Day 2 instead closed above <em>all</em> of Day 1's body, it would be a full Bullish Engulfing (a stronger signal).</p>
<p>The Piercing Line is the weakest of the three major two-candle bullish patterns (Engulfing being the strongest). It requires confirmation on Day 3 to be trusted as a reversal signal rather than a temporary bounce within a continuing decline.</p>""",
        "candle_html": """<div class="candle-display">
                    <div class="candle-group">
                        <div class="candle">
                            <div class="wick h-60 t-10"></div>
                            <div class="body-red h-50 t-30"></div>
                        </div>
                        <div class="candle">
                            <div class="wick h-20 t-0"></div>
                            <div class="body-green h-60 t-20"></div>
                            <div class="wick h-20 t-80"></div>
                        </div>
                    </div>
                    <div class="candle-explanation">Day 2 opens below Day 1's close (weakness) but fights back above Day 1's midpoint — buyers are clearly defending the low. A weaker signal than Engulfing but still meaningful buyer interest.</div>
                </div>""",
    },
    # --- technicals ---
    {
        "term_key": "moving-averages",
        "section_id": "technicals",
        "term_title": "Moving Averages (5D, 10D, 21D, 50D, 200D)",
        "question": "What is a 'Golden Cross'?",
        "answer": "When the 50-day moving average crosses above the 200-day moving average",
        "distractors": [
            "When price crosses above the 21-day EMA for the first time",
            "When the 5-day MA crosses below the 10-day MA",
            "When RSI crosses above 70 while price is above the 200-day MA",
        ],
        "explanation": """<p>A <strong>Moving Average (MA)</strong> smooths out day-to-day price noise to reveal the underlying trend direction. It is computed by averaging the closing price over the last N days, then recalculating that average each new trading day as the oldest data point drops out and the newest comes in — the "window" moves forward through time.</p>
<p>Different time periods serve different purposes:</p>
<p>This app uses <strong>EMA</strong> (Exponential Moving Average) for most short-period calculations, not SMA (Simple Moving Average). EMA gives more weight to recent prices, making it more responsive to current price action. SMA weights all days equally and is smoother but lags more. The 200-day is typically calculated as a simple SMA because its role is to represent the long-run structural trend, not to be reactive.</p>""",
    },
    {
        "term_key": "change-period",
        "section_id": "technicals",
        "term_title": "Change Period (1D, 5D, 1M, 6M, YTD, 1Y)",
        "question": "What does the YTD Change Period compare the live price against?",
        "answer": "The last close of the previous calendar year",
        "distractors": [
            "The close exactly 5 trading sessions ago",
            "The all-time high price for that ticker",
            "The average price over the past 200 trading days",
        ],
        "explanation": """<p>The Portfolio page's <strong>Change</strong> column and heatmap can be switched between six lookback windows, matching what Yahoo Finance itself shows for the same ranges. <strong>1D</strong> stays driven by the live intraday price feed exactly as before. <strong>5D</strong> compares the live price to the close 5 trading sessions ago. <strong>1M / 6M / 1Y</strong> compare the live price to the close on or before 1/6/12 calendar months ago (snapped to the nearest earlier trading day when the exact date falls on a weekend or holiday). <strong>YTD</strong> compares the live price to the last close of the previous calendar year.</p>
<p>All six buttons stay live during market hours — only the reference close is fixed per period; the live price side of the comparison keeps updating with the rest of the page. Switching periods updates the table column and the heatmap together, and the last-selected period is remembered on your next visit.</p>""",
    },
    {
        "term_key": "rsi-relative-strength-index",
        "section_id": "technicals",
        "term_title": "RSI (Relative Strength Index)",
        "question": "What does RSI Divergence signal when price makes a new high but RSI makes a lower high?",
        "answer": "The new price high was achieved on declining momentum — a warning the uptrend may be weakening",
        "distractors": [
            "The stock is guaranteed to reverse within the next trading session",
            "Trading volume has definitely increased on the new high",
            "The company is about to announce an earnings beat",
        ],
        "explanation": """<p>The <strong>Relative Strength Index</strong> measures the <em>velocity</em> of recent price changes — how quickly and decisively a stock has been rising or falling over the last 14 trading days. It is displayed as a number between 0 and 100. Developed by J. Welles Wilder in 1978, it remains one of the most widely used momentum indicators in the world.</p>
<p>The calculation: RSI compares the average gain on up-days to the average loss on down-days over 14 periods. If a stock went up on 12 of the last 14 days with large gains, the RSI will be very high (near 100). If it fell on 12 of 14 days with large losses, the RSI will be very low (near 0).</p>
<p>The classic interpretation:</p>
<p><strong>RSI Divergence</strong> is one of the most reliable signals: when the stock price makes a new high but RSI makes a lower high, it means the latest price high was achieved on declining momentum — a warning that the uptrend is weakening even before the price turns. Similarly, a price making a new low while RSI makes a higher low signals that the selling is losing momentum — a potential bottom forming.</p>""",
    },
    {
        "term_key": "atr-stop-loss",
        "section_id": "technicals",
        "term_title": "ATR Stop-Loss (Average True Range)",
        "question": "Why use ATR rather than a fixed percentage to set a stop-loss?",
        "answer": "ATR makes the stop adaptive to the stock's own typical volatility, rather than applying the same fixed percentage to every position",
        "distractors": [
            "ATR guarantees the stop-loss will never be triggered by normal price action",
            "ATR is only usable for stocks with beta above 1.5",
            "ATR removes the need to ever review a position once it's set",
        ],
        "explanation": """<p>The <strong>Average True Range (ATR)</strong> measures a stock's average daily price range — how much it typically moves from high to low in a single session. It accounts for overnight gaps by using "true range" (the greatest of: today's high minus today's low, today's high minus yesterday's close, yesterday's close minus today's low). ATR is purely a volatility measure — it tells you how much a stock moves, not which direction.</p>
<p>A high ATR means the stock is volatile and makes big daily moves — stop-losses need to be wider to avoid being triggered by normal noise. A low ATR means the stock is calm — tighter stops are viable. Using ATR to set stop-losses makes them <em>adaptive</em> to the stock's own behaviour rather than applying the same fixed percentage to every position.</p>
<p>The <strong>ATR Stop-Loss</strong> in this app is dynamically set between <strong>1.8× and 3.0× ATRs below the current price</strong>. The multiplier adjusts based on the ATR Stability Ratio (how consistent the ATR has been recently — a more stable ATR allows a tighter multiple) and widens automatically during high-risk macro events. This line moves upward as the stock rises, "trailing" the price to lock in profits while still leaving room for normal daily fluctuations.</p>
<p>The boundary has a concrete meaning: as long as the stock stays above the ATR stop, any pullback is within its normal volatility envelope — a genuine dip to consider buying. If the stock closes below the ATR stop, it has moved outside its normal volatility range — the structural trend has broken, and the position should be reviewed.</p>""",
    },
    {
        "term_key": "volume-profile-poc",
        "section_id": "technicals",
        "term_title": "Volume Profile & POC (Point of Control)",
        "question": "What is the Point of Control (POC) in a Volume Profile?",
        "answer": "The single price level with the highest accumulated trading volume over the period",
        "distractors": [
            "The current live trading price at this exact moment",
            "The average of the day's high and low prices",
            "The price level with the lowest trading volume, signalling weak interest",
        ],
        "explanation": """<p>Most price charts show volume as a bar at the bottom of the chart aligned with time — you can see that Monday had more trading than Tuesday. A <strong>Volume Profile</strong> turns this on its side: instead of volume-by-time, it shows volume-by-price. It builds a histogram of the last 180 days of trading, showing at which price levels the most shares changed hands.</p>
<p>The <strong>Point of Control (POC)</strong> is the single price level with the highest accumulated volume over the period — the price where the most shares changed hands. This level has special significance: because so many transactions occurred here, many investors have cost bases near the POC. They tend to defend it (buying when price approaches from above) and it can act as a ceiling (those who bought here and broke even sell to get out when price returns from below). Institutions also re-enter near the POC because it represents collective market agreement on fair value.</p>
<p>The <strong>Value Area</strong> spans from the Value Area Low (VAL) to the Value Area High (VAH) and contains 70% of all volume — it is the "normal" price range where most activity occurred. Prices inside the value area are considered fairly valued; prices outside it signal potential imbalance.</p>
<p><strong>High Volume Nodes (HVNs)</strong> are local peaks in the profile — price bands where lots of trading occurred. They act as support from above and resistance from below. <strong>Low Volume Nodes (LVNs)</strong> are thin areas where very little trading happened — price tends to slice through LVNs quickly because there are few participants with positions near those levels who would push back.</p>""",
    },
    {
        "term_key": "keltner-channel-z-score",
        "section_id": "technicals",
        "term_title": "Keltner Channel Z-Score",
        "question": "What does a Keltner Channel Z-Score of -2 mean?",
        "answer": "The current close is two ATRs below the 21-day EMA — a notable oversold pullback within the channel",
        "distractors": [
            "The stock has fallen exactly 2% below its opening price today",
            "RSI has been below 30 for two consecutive trading sessions",
            "The stock's beta relative to the market is -2.0",
        ],
        "explanation": """<p>A <strong>Keltner Channel</strong> is a volatility envelope drawn around a central moving average (EMA of 21 days). Unlike Bollinger Bands (which use price standard deviation), Keltner Channels use <strong>ATR</strong> (Average True Range) to set the band width. This makes them more stable in trending markets because ATR measures actual price range rather than statistical deviation of closes.</p>
<p>The <strong>Z-Score</strong> measures how many ATRs the current close is above or below the 21-day EMA: <code>Z = (Close − EMA21) / ATR14</code>. A score of 0 means the price is exactly at its 21-day trend centre. A score of −2 means it is two ATRs below the trend — a notable oversold pullback within the channel. A score of −3 or lower is very rare and signals an extreme extension.</p>
<p>In a healthy uptrend, price typically oscillates between Z = −1.5 and Z = +2. When Z drops to −2 or below while the stock is still above its 200-day SMA (confirming the long-term uptrend is intact), the pullback has overshot the normal range — a mean-reversion opportunity. When Z exceeds +3 alongside RSI above 75, the price is extended to the upside and a correction is likely.</p>""",
    },
    {
        "term_key": "ml-quantile-price-bands",
        "section_id": "technicals",
        "term_title": "ML Quantile Price Bands (Q10 / Q90)",
        "question": "What does the Q90 band represent?",
        "answer": "In 90% of comparable historical situations, the stock reached or exceeded this price within the next 10 trading days — a data-driven optimistic target",
        "distractors": [
            "A guaranteed price ceiling the stock cannot exceed",
            "The stock's price exactly 90 trading days from today",
            "The 90th percentile of the stock's historical trading volume",
        ],
        "explanation": """<p>Standard ML models output a single probability ("65% chance of rising &gt;3%"). The <strong>Quantile Price Bands</strong> go further: instead of predicting the most likely outcome, they predict the <em>distribution</em> of outcomes — specifically, where the stock is likely to be in the worst-case and best-case scenarios over the next 10 trading days.</p>
<p>Two <strong>XGBoost Quantile Regressors</strong> are trained on the same 18 features as the confidence score model, but with different objectives. The <strong>Q10 model</strong> is trained to predict the 10th percentile of outcomes — the level that only 10% of similar historical setups fell below over 10 days. The <strong>Q90 model</strong> predicts the 90th percentile — the level that only 10% of similar setups exceeded. This gives you a statistically grounded range rather than a single point forecast.</p>
<p><strong>Q10 Band (Floor):</strong> In 90% of comparable historical situations, the stock did not fall below this price over the following 10 trading days. This is a data-driven worst-case support level — useful for placing limit buy orders or stop-loss levels. It is not a guarantee, but a probabilistic boundary based on the model's training data.</p>
<p><strong>Q90 Band (Ceiling):</strong> In 90% of comparable historical situations, the stock reached or exceeded this price within the following 10 trading days. This is a data-driven optimistic target — useful for setting take-profit levels. When shown on the portfolio page, this is displayed as the ML exit target alongside the Volume Profile Exit Zone.</p>
<p>Having both bands simultaneously is much more useful than a single confidence score: you can see not just whether the model thinks the stock will go up, but approximately where it expects the floor and ceiling to be over the next two weeks — information you need to decide on position sizing and order placement.</p>""",
    },
    {
        "term_key": "what-is-technical-analysis",
        "section_id": "technicals",
        "term_title": "What Is Technical Analysis?",
        "question": "What are the three core assumptions technical analysis rests on?",
        "answer": "Price already reflects known information, prices move in identifiable trends, and chart patterns recur because human reactions to fear and greed repeat",
        "distractors": [
            "That company earnings are irrelevant to a stock's future price under all circumstances",
            "That every stock's price is completely random and cannot be studied usefully",
            "That only the last single day of trading data matters for any forecast",
        ],
        "explanation": """<p><strong>Technical analysis</strong> studies a stock's own price and volume history to judge where it might go next, rather than studying the business behind it. It rests on three assumptions: that the current price already reflects everything publicly known about the company (so the chart itself is a summary of collective opinion); that prices move in identifiable trends rather than randomly; and that recurring chart patterns work because human reactions to fear and greed repeat fairly consistently across different stocks and eras.</p>
<p>It's a complement to fundamental analysis, not a replacement for it — fundamentals help answer <em>what</em> to own, technicals help answer <em>when</em> to enter, exit, or size a position. Every indicator in this section (moving averages, RSI, ATR, volume profile, and the rest) is a different lens on the same two raw inputs: price and volume.</p>""",
    },
    {
        "term_key": "stock-chart-types",
        "section_id": "technicals",
        "term_title": "Stock Chart Types — Line, Bar & Candlestick",
        "question": "What is the main advantage of a candlestick chart over a simple line chart?",
        "answer": "It shows the open, high, low, and close for each period, not just the closing price, revealing intraday and session-by-session detail",
        "distractors": [
            "It uses a completely different price scale that is more accurate",
            "It can only be used for stocks priced above $100",
            "It automatically removes days with low trading volume from the chart",
        ],
        "explanation": """<p>Every price chart plots time along the bottom (horizontal) axis and price up the left (vertical) axis — the differences between chart types are purely about how much detail each bar of time shows. A <strong>line chart</strong> is the simplest: it plots only the closing price each period and connects the dots, which is easy to read for a long-term trend but hides everything that happened intraday.</p>
<p>A <strong>bar chart (OHLC)</strong> shows all four data points for each period — open, high, low, close — as a single vertical tick with small horizontal notches for the open (left) and close (right). A <strong>candlestick chart</strong> encodes the exact same four numbers, open/high/low/close, but as a filled body (open-to-close) with thin wicks above and below (the session's high and low) — see the Candlestick Anatomy section for the full detail on reading one. Candlesticks are the most widely used format because the coloured body makes it instantly obvious, at a glance across many bars, whether each session closed up or down.</p>
<p>The chosen <strong>timeframe</strong> (minutes, days, weeks, years) changes the story a chart tells — a stock can look like a strong uptrend on a 1-year chart and a choppy range on a 1-day chart. Always match the timeframe to your actual holding period; a day-trader's 5-minute chart is close to meaningless for someone holding a position for years, and vice versa.</p>""",
    },
    {
        "term_key": "support-and-resistance",
        "section_id": "technicals",
        "term_title": "Support & Resistance",
        "question": "What often happens to a resistance level after price breaks decisively above it?",
        "answer": "It frequently flips and becomes a new support level, since the same trader psychology that defended it now works in the opposite direction",
        "distractors": [
            "It permanently disappears and never affects price again",
            "It automatically becomes the new all-time high for the stock",
            "It converts into a dividend payment date for the company",
        ],
        "explanation": """<p><strong>Support</strong> is a price level where buying pressure has historically been strong enough to halt or reverse a decline — a "floor." <strong>Resistance</strong> is the mirror image, a price level where selling pressure has historically capped an advance — a "ceiling." Both exist because of market memory: traders who watched a stock bounce from £40 before tend to buy again if it revisits £40, and traders who watched it stall at £60 before tend to sell into that level again.</p>
<p>These levels are best thought of as <em>zones</em>, not exact lines — price rarely reverses at the precise same tick twice. They're identified from prior swing highs/lows, from trendlines connecting a series of highs or lows, and from widely-watched moving averages (the 50-day and 200-day are common dynamic support/resistance levels in their own right, see Moving Averages below).</p>
<p>A <strong>breakout</strong> is price decisively moving above resistance; a <strong>breakdown</strong> is price decisively moving below support — both are more meaningful when accompanied by above-average volume, confirming genuine participation rather than a low-conviction wobble. A broken level frequently <strong>flips role</strong>: former resistance often becomes new support once price has broken above it (and vice versa on the downside), because the same trader psychology that defended the level before now works in the opposite direction.</p>""",
    },
    {
        "term_key": "momentum-investing-trend-following",
        "section_id": "technicals",
        "term_title": "Momentum Investing & Trend Following",
        "question": "What is the main risk of momentum-based trading strategies?",
        "answer": "A momentum crash — because many participants chase the same crowded trend, the eventual reversal can be sharp and violent",
        "distractors": [
            "Momentum strategies are illegal to use in most stock markets",
            "Momentum only works on bonds, never on individual stocks",
            "Momentum strategies always require holding a position for at least 10 years",
        ],
        "explanation": """<p><strong>Momentum</strong> is the observation that stocks which have performed well recently tend to keep performing well for a while longer, and vice versa for losers — the opposite of what a purely efficient market would predict, but one of the most persistently documented patterns in market history. Every indicator in this section (moving averages, RSI, MACD, the Stochastic Oscillator) is, at its core, a different way of measuring the same underlying thing: is this stock's momentum currently positive or negative, and how strong is it?</p>
<p>The behavioural explanation for why momentum persists rather than getting arbitraged away instantly: investors <strong>under-react</strong> to new information at first (it takes time for good or bad news to fully sink in and be repriced), large institutions tend to build or unwind big positions gradually rather than all at once (creating sustained directional flow), and <strong>herding</strong> — investors following the crowd rather than acting independently — reinforces whichever direction is already moving.</p>
<p>The key risk is a <strong>momentum crash</strong>: because many participants are chasing the same trend using similar signals, momentum trades can become crowded, and when the trend finally exhausts itself the unwind can be fast and violent — sharper than the drift up ever was. This is exactly why this app pairs momentum indicators (RSI, MACD) with volatility-aware position sizing (ATR Stop-Loss) rather than trading momentum signals on their own.</p>""",
    },
    {
        "term_key": "stochastic-oscillator",
        "section_id": "technicals",
        "term_title": "Stochastic Oscillator",
        "question": "What does the Stochastic Oscillator's %K line actually measure?",
        "answer": "Where the current close sits relative to the high-low trading range of the last 14 periods",
        "distractors": [
            "The average trading volume over the last 14 periods",
            "The percentage change in price since the previous single session",
            "The number of consecutive up-days in the last 14 periods",
        ],
        "explanation": """<p>Where RSI measures the speed of recent gains vs losses, the <strong>Stochastic Oscillator</strong> measures something slightly different: where the current close sits relative to the high-low range of the last 14 periods. The main line, <strong>%K</strong>, is <code>(Close − Lowest Low) ÷ (Highest High − Lowest Low) × 100</code> — a value of 90 means the close is near the top of its recent range, 10 means it's near the bottom. <strong>%D</strong> is a 3-period moving average of %K, plotted alongside it as a signal line.</p>
<p>Readings above 80 are considered overbought, below 20 oversold — similar thresholds to RSI's 70/30, just on a different underlying calculation. The most common trading signal is a <strong>%K/%D crossover</strong> while both lines are in extreme territory: %K crossing above %D below the 20 line is read as a potential bullish turn, %K crossing below %D above the 80 line as a potential bearish turn. Like RSI, it can stay pinned at an extreme through a strong trend, so it's best combined with a trend filter (such as the 50-day or 200-day moving average) rather than traded in isolation.</p>""",
    },
    {
        "term_key": "bollinger-bands",
        "section_id": "technicals",
        "term_title": "Bollinger Bands",
        "question": "How do Bollinger Bands differ from the Keltner Channel used elsewhere in this app?",
        "answer": "Bollinger Bands set their width using the statistical standard deviation of recent closing prices, while Keltner Channels use ATR (actual bar-to-bar range)",
        "distractors": [
            "Bollinger Bands can only be applied to ETFs, not individual stocks",
            "Bollinger Bands never widen or narrow — their width is fixed",
            "Keltner Channels require options data while Bollinger Bands do not",
        ],
        "explanation": """<p><strong>Bollinger Bands</strong> are the other widely-used volatility envelope alongside the Keltner Channel above, and it's worth knowing how they differ. A middle band (typically a 20-day simple moving average) sits between an upper and lower band, each set 2 <strong>standard deviations</strong> of recent closing prices away from that middle band. Because the bands are built from statistical dispersion of price rather than ATR, they widen and narrow purely based on how scattered recent closes have been.</p>
<p>A <strong>band squeeze</strong> — the upper and lower bands pulling close together — signals unusually low volatility and often precedes a sharp directional move once volatility returns, though it says nothing about which direction that move will be. Price touching or briefly poking outside a band isn't automatically a reversal signal; in a strong trend, price can "walk the band," repeatedly touching the upper band on the way up (or the lower band on the way down) for an extended period.</p>
<p>The practical difference from the Keltner Channel used elsewhere in this app: Bollinger Bands react to how volatile prices have <em>statistically</em> been, while ATR-based bands react to how far price has <em>actually</em> travelled bar-to-bar — ATR tends to stay steadier through a strong trend, which is why this app's own mean-reversion signals are built on the Keltner Channel rather than Bollinger Bands.</p>""",
    },
    {
        "term_key": "classic-chart-patterns",
        "section_id": "technicals",
        "term_title": "Classic Chart Patterns — Reversal & Continuation",
        "question": "What confirms a Head and Shoulders pattern as complete?",
        "answer": "Price breaking below the neckline joining the two troughs between the three peaks",
        "distractors": [
            "The formation of a third shoulder identical in height to the head",
            "Trading volume falling to zero for three consecutive sessions",
            "The stock's RSI reading exactly 50 at the pattern's midpoint",
        ],
        "explanation": """<p>Beyond the single- and multi-candle patterns in the Candlestick Anatomy section, technical analysts also look for larger shapes that form over many sessions or weeks. <strong>Reversal patterns</strong> signal an existing trend is ending: a <strong>Head and Shoulders</strong> (three peaks, the middle one highest) marks a top and completes on a break below the "neckline" joining the two troughs between the peaks; its mirror image, the <strong>Inverse Head and Shoulders</strong>, marks a bottom. A <strong>Double Top</strong> (two roughly equal peaks) or <strong>Double Bottom</strong> (two roughly equal troughs) is a simpler, more common version of the same idea — confirmed when price breaks the level between the two extremes.</p>
<p><strong>Continuation patterns</strong> signal a pause within an ongoing trend before it resumes. A <strong>triangle</strong> forms as the trading range narrows — an <strong>ascending triangle</strong> (flat resistance, rising support) leans bullish, a <strong>descending triangle</strong> (falling resistance, flat support) leans bearish, and a <strong>symmetrical triangle</strong> (both lines converging) is directionally neutral until it breaks. A <strong>flag</strong> or <strong>pennant</strong> is a brief, tight consolidation right after a sharp move (the "pole"), usually resolving in the same direction the pole was heading. A <strong>Cup and Handle</strong> is a longer, U-shaped consolidation (the cup) followed by a shallow pullback (the handle) before a bullish breakout.</p>
<p>Across all of these, the same two confirmation rules that apply to candlestick patterns apply here too: volume should expand on the actual breakout (confirming real participation, not a false move), and the pattern is more trustworthy at a well-established support/resistance level or after a sustained trend than in the middle of a directionless range.</p>""",
    },
    # --- fundamentals ---
    {
        "term_key": "what-is-fundamental-valuation",
        "section_id": "fundamentals",
        "term_title": "What Fundamental Valuation Means",
        "question": "How does fundamental analysis differ from technical analysis?",
        "answer": "Fundamental analysis asks what a company is actually worth based on financial performance, rather than looking at price charts and momentum",
        "distractors": [
            "Fundamental analysis only applies to bonds, never to individual stocks",
            "Fundamental analysis relies exclusively on candlestick patterns",
            "Fundamental analysis and technical analysis always produce identical buy/sell signals",
        ],
        "explanation": """<p><strong>Fundamental analysis</strong> asks: what is this company actually worth, based on its financial performance and business quality? It is the opposite of technical analysis, which looks at price charts and momentum. A fundamentally focused investor might buy a stock when its price falls far below its estimated intrinsic value, regardless of what the chart looks like — and sell when the price rises far above it.</p>
<p>The two metrics used in this section — PEG ratio and financial safety indicators — are among the most widely used fundamental filters. They are simple enough to apply consistently across thousands of stocks, yet powerful enough to separate genuinely cheap stocks from cheap-looking ones that are heading into trouble.</p>""",
    },
    {
        "term_key": "peter-lynch-peg-ratio",
        "section_id": "fundamentals",
        "term_title": "Peter Lynch Fair Value — PEG Ratio",
        "question": "What does a PEG ratio of 1.0 represent under Peter Lynch's framework?",
        "answer": "\"Fair value\" — you are paying exactly in line with the company's earnings growth rate",
        "distractors": [
            "The stock is definitely overvalued regardless of growth",
            "The company has zero earnings growth",
            "The P/E ratio exceeds 30",
        ],
        "explanation": """<p>The <strong>P/E ratio</strong> (Price-to-Earnings) is the most widely-known valuation metric: it measures how many pounds (or dollars) you are paying for each pound of annual earnings. A P/E of 20 means you are paying 20× the current year's earnings for the stock. Higher P/E = more expensive, usually because investors expect faster growth. Lower P/E = cheaper, usually reflecting lower expected growth or higher risk.</p>
<p>The problem with a standalone P/E ratio: it ignores growth. A company with a P/E of 30 growing earnings at 30% per year is potentially much cheaper than a company with a P/E of 15 growing earnings at 3% per year. You are paying proportionally for the future, not just the present. P/E also only makes sense within a sector — capital-intensive industries (utilities, banks) structurally trade on lower P/E multiples than asset-light, high-growth ones (software), so comparing across sectors rather than against peers or the company's own history is a common mistake. It's undefined or meaningless for a loss-making company, since a negative or near-zero earnings figure produces a nonsensical ratio.</p>
<p>Peter Lynch — the legendary Fidelity Magellan Fund manager who averaged 29% annual returns — popularised the <strong>PEG ratio</strong> as a simple adjustment: divide the P/E by the earnings growth rate. A PEG of 1.0 means you're paying exactly in line with growth — Lynch considered this "fair value." Below 1.0 means you might be getting growth at a discount; above 2.0 means you are significantly overpaying relative to the growth rate.</p>
<p>This app's implementation uses <em>Trailing P/E</em> (based on last 12 months' actual earnings, not analyst forecasts) and <em>Year-over-Year earnings growth</em> (actual reported, not estimates). This makes it less susceptible to overly optimistic analyst projections.</p>""",
    },
    {
        "term_key": "financial-safety-debt-liquidity",
        "section_id": "fundamentals",
        "term_title": "Financial Safety — Debt & Liquidity",
        "question": "What does a Current Ratio below 1.0 indicate?",
        "answer": "The company cannot cover its near-term obligations with its near-term resources",
        "distractors": [
            "The company has no debt on its balance sheet",
            "The company's dividend yield exceeds its P/E ratio",
            "The company is guaranteed to file for bankruptcy within a year",
        ],
        "explanation": """<p>A company can look like a bargain on earnings-based metrics while quietly building up dangerous levels of debt. When interest rates are low, debt is cheap — but as rates rise (as dramatically happened in 2022–2023), companies with high debt burdens suddenly face a much larger annual interest expense. Companies that could comfortably service debt at 2% interest rates sometimes cannot at 5–6%.</p>
<p>The <strong>Debt-to-Equity ratio (D/E)</strong> compares total liabilities to shareholders' equity — the amount of the company funded by debt versus the amount funded by owners. A D/E above 2.0 means the company has more than twice as much debt as equity — for every £1 of owner-funded value, creditors have lent £2. In a rising-rate or recession environment, this level of leverage creates meaningful bankruptcy risk. Sectors like utilities and real estate naturally carry more debt (because assets are stable and cash flows are predictable); for technology or consumer companies, a D/E above 2.0 is more alarming.</p>
<p>The <strong>Current Ratio</strong> (current assets / current liabilities) measures whether a company can cover its near-term obligations with its near-term resources. A current ratio below 1.0 means it cannot — it would need to refinance debt, sell assets, or raise new equity to meet its obligations over the next 12 months. A ratio above 1.5 is generally considered healthy.</p>
<p>Together, these two metrics form a quick "financial safety check" — a company can be fundamentally cheap on earnings but a value trap if it has dangerous debt levels. The combination of cheap valuation and deteriorating financial safety is one of the most dangerous setups in investing.</p>""",
    },
    {
        "term_key": "quality-grade",
        "section_id": "fundamentals",
        "term_title": "Quality Grade (A–D)",
        "question": "What combination of factors earns a stock an 'A' Quality Grade?",
        "answer": "High ROE (>15%), low debt (<50% D/E), and a reasonable valuation (P/E <25 or PEG <1.5)",
        "distractors": [
            "The highest dividend yield in its sector",
            "The largest market capitalisation in its index",
            "Zero analyst coverage, indicating an under-the-radar opportunity",
        ],
        "explanation": """<p>A single-letter grade shown on the Watchlist page, combining ROE, debt-to-equity, and valuation (P/E or PEG) into one quick quality signal. <strong>A</strong> = high ROE (&gt;15%), low debt (&lt;50% D/E), and reasonable valuation (P/E &lt;25 or PEG &lt;1.5). <strong>B</strong> = solid ROE (&gt;10%), moderate debt (&lt;100% D/E), and a P/E below 35. <strong>D</strong> = loss-making (negative ROE) or over-leveraged (D/E &gt;200%). Everything else is graded <strong>C</strong>.</p>""",
    },
    {
        "term_key": "watchlist-report-screen-tags",
        "section_id": "fundamentals",
        "term_title": "Watchlist Report-Screen Tags",
        "question": "What does the 'Quality on Sale' tag indicate?",
        "answer": "The stock is within 15% of its 52-week low despite having solid fundamentals",
        "distractors": [
            "The stock has just cut its dividend payment",
            "The stock is trading at an all-time high with strong momentum",
            "The stock has failed the Beneish M-Score manipulation check",
        ],
        "explanation": """<p>The Watchlist page tags each ticker with any Market Reports screen it currently qualifies for, so you don't need to cross-reference the Market Reports page manually. <strong>Quality Compounder</strong>: ROE &gt;15%, low debt, steady growth, reasonable P/E. <strong>Quality on Sale</strong>: within 15% of its 52-week low despite solid fundamentals. <strong>GARP Tenbagger</strong>: low PEG (&le;1.0) with strong growth, Peter Lynch style. <strong>Mean Reversion Setup</strong>: oversold RSI within a longer-term uptrend. <strong>Dividend Harvest</strong>: solid yield combined with a healthy composite score.</p>""",
    },
    {
        "term_key": "earnings-per-share-eps",
        "section_id": "fundamentals",
        "term_title": "Earnings Per Share (EPS)",
        "question": "Why can a share buyback raise EPS without the underlying business actually improving?",
        "answer": "EPS is profit divided by share count, so shrinking the share count mechanically raises the result even if total profit stays flat",
        "distractors": [
            "Buybacks are legally required to be accompanied by a matching profit increase",
            "Buybacks directly increase a company's net income figure",
            "Buybacks only affect the balance sheet, never the EPS calculation",
        ],
        "explanation": """<p><strong>EPS</strong> is the slice of a company's profit that belongs to each individual share: <code>(Net Income − Preferred Dividends) ÷ Weighted Average Shares Outstanding</code>. It's the "per share" building block that the P/E ratio (share price ÷ EPS) and most other valuation metrics are built on — without it, you can only compare companies' total profits, not how much of that profit each of your shares actually represents.</p>
<p>There are two common versions. <strong>Basic EPS</strong> uses the shares currently outstanding. <strong>Diluted EPS</strong> is the more conservative figure — it assumes every option, warrant, and convertible bond that could turn into new shares actually does, spreading the same profit over a larger share count. Diluted EPS is normally slightly lower, and it's the more honest number when a company has issued a lot of stock-based compensation.</p>
<p>EPS growth is a genuinely useful trend signal, but it can be flattered without the underlying business improving at all: a large <strong>share buyback</strong> shrinks the share count and mechanically raises EPS even if total profit is flat, and one-off accounting items can inflate a single quarter's net income. Read EPS trends alongside revenue growth and cash flow, not in isolation.</p>""",
    },
    {
        "term_key": "reading-a-balance-sheet",
        "section_id": "fundamentals",
        "term_title": "Reading a Balance Sheet — Assets, Liabilities & Equity",
        "question": "Why does a balance sheet always balance, by definition?",
        "answer": "Everything a company holds (assets) was funded either by borrowing (liabilities) or by shareholders' money and retained profit (equity), so Assets = Liabilities + Equity",
        "distractors": [
            "Regulators manually adjust the figures each quarter to force a balance",
            "It only balances for companies that are currently profitable",
            "Non-current assets are excluded from the calculation entirely",
        ],
        "explanation": """<p>Where the income statement covers a period (a quarter, a year), the <strong>balance sheet</strong> is a snapshot at a single point in time — everything the company owns, owes, and the owners' remaining stake, all on one date. It always balances by definition: <code>Assets = Liabilities + Equity</code>, because everything the company holds was funded either by borrowing (liabilities) or by shareholders' money and retained profit (equity).</p>
<p><strong>Assets</strong> split into current (cash, receivables, inventory — convertible to cash within a year) and non-current (property, equipment, intangibles like patents and goodwill). <strong>Liabilities</strong> split the same way: current (accounts payable, short-term loans, due within a year) and non-current (long-term debt). <strong>Equity</strong> is what's left over — share capital raised from investors plus retained earnings accumulated over the company's life, minus any shares the company has bought back.</p>
<p>Two ratios read directly off the balance sheet do most of the work: the <strong>Current Ratio</strong> (current assets ÷ current liabilities) checks whether near-term obligations are covered by near-term resources, and <strong>Debt-to-Equity</strong> (covered above) checks how leveraged the company is. Both feed directly into the Financial Safety check above — this term-box is the "why" behind that metric, the balance sheet is the "where" it comes from.</p>""",
    },
    {
        "term_key": "cash-flow-statement",
        "section_id": "fundamentals",
        "term_title": "The Cash Flow Statement — Where the Real Cash Went",
        "question": "Why do many investors trust Free Cash Flow more than net income?",
        "answer": "It strips out non-cash accounting items and timing effects, tracking only actual cash the business generated after capital spending — much harder to flatter with accounting choices",
        "distractors": [
            "Free Cash Flow is always a larger number than net income",
            "Free Cash Flow is required by regulators to be audited twice",
            "Free Cash Flow excludes revenue from the calculation entirely",
        ],
        "explanation": """<p>Net income (from the income statement) is an accounting figure — it includes non-cash items like depreciation and can be shifted by the timing of invoices and payments. The <strong>cash flow statement</strong> strips all of that out and tracks only actual cash moving in and out of the business, split into three buckets: <strong>operating</strong> (cash generated by the core business — the one that matters most), <strong>investing</strong> (capital spent on or raised from long-term assets, e.g. buying equipment or making acquisitions), and <strong>financing</strong> (cash raised or returned via debt, share issuance, buybacks, and dividends).</p>
<p><strong>Free Cash Flow (FCF)</strong> — operating cash flow minus capital expenditure — is what's genuinely left over to pay down debt, pay dividends, buy back shares, or reinvest, after keeping the business running. It's one of the hardest numbers to fake, which is why many investors trust it more than net income.</p>
<p>A useful sanity check: compare net income to operating cash flow over several years. If net income is consistently higher than operating cash flow, profits are being booked faster than cash is actually being collected — a classic early warning sign worth investigating, and one of the signals the Forensic Screener's accounting-quality checks are built around.</p>""",
    },
    {
        "term_key": "profitability-efficiency-ratios",
        "section_id": "fundamentals",
        "term_title": "Profitability & Efficiency Ratios — ROA, ROIC & Margins",
        "question": "Why is ROA useful alongside ROE, even though both measure profitability?",
        "answer": "ROA measures profit against total assets regardless of financing, so it can't be inflated simply by taking on more debt the way ROE can",
        "distractors": [
            "ROA and ROE always produce identical results for any company",
            "ROA only applies to companies with no debt at all",
            "ROA is calculated using next year's forecast earnings instead of actual results",
        ],
        "explanation": """<p>Beyond ROE (used in the Quality Grade below), a few other ratios round out the profitability picture. <strong>Return on Assets (ROA)</strong> (net income ÷ average total assets) shows how efficiently the company turns its total asset base into profit, regardless of how much of that base is debt-funded — useful alongside ROE because a company can pump up ROE just by taking on more debt, without actually running the business any better. <strong>Return on Invested Capital (ROIC)</strong> goes further, comparing after-tax operating profit to the total capital (debt + equity) invested in the business; a company earning ROIC consistently above its cost of capital is genuinely creating value, not just growing for growth's sake.</p>
<p><strong>Gross margin</strong> ((revenue − cost of goods sold) ÷ revenue) reflects pricing power and production efficiency. <strong>Operating margin</strong> (operating income ÷ revenue) reflects cost control after operating expenses. <strong>Net margin</strong> (net income ÷ revenue) is the final, all-in profitability figure. Comparing all three over time reveals where a company's profitability is actually coming from — rising gross margin but falling operating margin, for instance, means the cost problem is in overheads, not production.</p>""",
    },
    {
        "term_key": "valuation-methods-dcf-comps-ddm",
        "section_id": "fundamentals",
        "term_title": "Valuation Methods — DCF, Comparables & Dividend Discount",
        "question": "What is the main weakness of a Discounted Cash Flow (DCF) valuation?",
        "answer": "The result is extremely sensitive to the growth and discount rate assumptions, so a small change in either can swing the estimated value significantly",
        "distractors": [
            "DCF can only be applied to companies that pay a dividend",
            "DCF ignores the company's future cash flows entirely",
            "DCF always produces a lower value than comparable company analysis",
        ],
        "explanation": """<p>A <strong>Discounted Cash Flow (DCF)</strong> model estimates intrinsic value directly: forecast the company's free cash flow for several years, add a terminal value for everything beyond that, then discount it all back to today's money using a discount rate (typically the weighted average cost of capital). It's the most theoretically rigorous method, but the output is extremely sensitive to the growth and discount rate assumptions — a small change in either can swing the answer by a large margin, so a DCF is only as good as its inputs.</p>
<p><strong>Comparable company analysis ("comps")</strong> is the pragmatic alternative: instead of forecasting anything, value the company using the multiples (P/E, EV/EBITDA, P/S) that similar publicly traded peers currently trade at. It's fast and reflects real current market pricing, but it inherits whatever the market currently believes — including bubbles, if the whole peer group is overvalued together.</p>
<p>The <strong>Dividend Discount Model (DDM)</strong> values a stock purely as the present value of its expected future dividends — in its simplest form (the Gordon Growth Model), <code>Value = Next Year's Dividend ÷ (Discount Rate − Growth Rate)</code>. It only really works for mature, stable dividend payers (utilities, established banks); it has nothing useful to say about a non-dividend-paying growth company.</p>""",
    },
    {
        "term_key": "economic-moats",
        "section_id": "fundamentals",
        "term_title": "Economic Moats — Sustainable Competitive Advantage",
        "question": "What does a 'switching cost' moat actually protect a company from?",
        "answer": "Customers leaving for a cheaper competitor, because the disruption or expense of switching outweighs the potential savings",
        "distractors": [
            "Government regulators imposing new taxes on the industry",
            "Currency exchange rate fluctuations affecting overseas revenue",
            "Employees leaving to join a competing company",
        ],
        "explanation": """<p>An <strong>economic moat</strong> is whatever protects a company's profits from being competed away — the reason it can keep earning high returns on capital for years without a rival simply copying it and undercutting the price. Warren Buffett popularised the term as a castle-and-moat metaphor: the wider the moat, the harder it is for competitors to storm the castle.</p>
<p>Four common sources of moat: <strong>network effects</strong> (the product gets more valuable as more people use it — a card payment network, a marketplace); <strong>switching costs</strong> (leaving is expensive or disruptive enough that customers stay even when a cheaper option exists — enterprise software, a bank you've used for years); <strong>cost advantages</strong> (structurally lower costs from scale or a proprietary process let the company underprice rivals and still be profitable); and <strong>intangible assets</strong> (a strong brand, patents, or a regulatory licence that a competitor can't simply replicate).</p>
<p>A company with a genuine moat can sustain a high ROIC (see the Profitability & Efficiency Ratios box above) for far longer than one without — which is exactly why moat and ROIC tend to be discussed together: ROIC is the measurable symptom, the moat is the underlying cause.</p>""",
    },
    # --- strategies ---
    {
        "term_key": "macd-reversal",
        "section_id": "strategies",
        "term_title": "⚡ MACD Reversal",
        "question": "What does it mean when the MACD line crosses above the Signal line?",
        "answer": "Short-term momentum is turning upward — the pace of a recent decline has slowed",
        "distractors": [
            "The stock has definitively bottomed and will not fall further",
            "Trading volume has tripled compared to the 20-day average",
            "The company has just reported positive earnings",
        ],
        "explanation": """<p><strong>MACD</strong> (Moving Average Convergence Divergence) is a momentum indicator built from the difference between two exponential moving averages of price — typically the 12-day EMA minus the 26-day EMA. This difference (the MACD line) is then compared against its own 9-day EMA (the Signal line). When the MACD line crosses above the Signal line, it signals that short-term momentum is turning upward — a reversal of the recent downtrend.</p>
<p>The strategy tag <strong>⚡ MACD Reversal</strong> is used for <em>bottom-fishing</em>: identifying stocks that have been in a downtrend where the selling momentum has mathematically exhausted itself. The crossover doesn't mean the decline is definitely over — it means the pace of decline has slowed and the early momentum of a recovery is detectable.</p>
<p>This is most useful after a significant dip in an otherwise healthy stock — not for a stock in a multi-year structural decline. The distinction between "healthy pullback before the uptrend resumes" and "start of a much larger fall" is what the system's full multi-factor scoring is designed to help navigate.</p>""",
    },
    {
        "term_key": "vcp-breakout",
        "section_id": "strategies",
        "term_title": "🔥 VCP Breakout (Volatility Contraction Pattern)",
        "question": "What does the 'coiling' price action in a VCP represent?",
        "answer": "Weak holders being shaken out on shrinking dips while institutional buyers quietly absorb supply",
        "distractors": [
            "A company preparing to announce a stock buyback",
            "Retail investors panic-selling ahead of an earnings report",
            "Random noise with no informational content",
        ],
        "explanation": """<p>The <strong>Volatility Contraction Pattern</strong> was popularised by trader Mark Minervini and describes a recurring price structure seen before major breakouts. After a strong upward move, a stock enters a period of controlled consolidation where the price trades in progressively tighter ranges — each correction is smaller in amplitude and the volume steadily dries up. This "coiling" action reflects a specific market dynamic: the remaining weak, nervous holders are being shaken out on each dip, while strong, patient institutional buyers are quietly absorbing the supply without driving the price higher.</p>
<p>Eventually, the supply of sellers is exhausted. When institutional buyers then step in more aggressively, even a small new wave of demand causes an outsized price move because there is very little stock available at current prices. This is the breakout — and because it was preceded by volume dry-up, it tends to be explosive and sustained.</p>
<p>The pattern visually looks like a funnel narrowing over time: big swings early in the base, tiny swings at the end, then a sudden expansion of range on a high-volume breakout day.</p>""",
    },
    {
        "term_key": "bull-trap-dead-cat-bounce",
        "section_id": "strategies",
        "term_title": "🎭 Bull Trap / Dead Cat Bounce",
        "question": "What is the key volume tell that distinguishes a Bull Trap from a genuine recovery?",
        "answer": "A bull trap's bounce occurs on low volume, driven by short-covering rather than new committed buyers",
        "distractors": [
            "A bull trap always occurs on higher volume than the original decline",
            "Bull traps only happen in stocks with negative beta",
            "The price must close above the 200-day moving average to qualify",
        ],
        "explanation": """<p>A <strong>Bull Trap</strong> is one of the most frustrating setups in markets: a stock falls sharply, then bounces convincingly — appearing to have found its bottom. Retail buyers who missed the selloff rush in to "buy the dip", expecting the recovery to continue. Instead, the bounce fails and a second, often sharper leg down follows. The optimistic buyers are left "trapped" in a position that immediately moved against them.</p>
<p>The <strong>Dead Cat Bounce</strong> is the colourful Wall Street nickname for this pattern, derived from the dark observation that "even a dead cat will bounce if it falls from high enough." The bounce looks real — price is going up — but it is driven by short-term mechanics (short sellers covering their positions, creating artificial demand) rather than genuine investors who believe in the stock's future.</p>
<p>The key diagnostic: volume behaviour. In a genuine recovery, prices rise on <em>high</em> volume as new committed buyers step in. In a bull trap, prices rise on <em>low</em> volume because there are no new believers — just short-covering. The 20-day EMA is also often used as a ceiling test: if the bounce fails to close above the 20-EMA, it has not recaptured a key institutional reference level.</p>""",
    },
    {
        "term_key": "bear-trap-false-breakdown",
        "section_id": "strategies",
        "term_title": "🪤 Bear Trap / False Breakdown",
        "question": "What signals a Bear Trap rather than a genuine breakdown below support?",
        "answer": "The breakdown occurs on relatively low volume, and price closes back above the broken support level the same day",
        "distractors": [
            "The stock gaps down more than 10% on the breakdown day",
            "Short interest falls to zero immediately after the breakdown",
            "The breakdown is confirmed by RSI rising above 70",
        ],
        "explanation": """<p>The <strong>Bear Trap</strong> is the mirror-image of the Bull Trap. Instead of a false recovery, it is a false breakdown. A stock falls through an important support level — the lower Bollinger Band, a key swing low, a round number — triggering stop-loss orders and attracting short sellers who expect the decline to continue. Then the price violently reverses back above the broken level, "trapping" the shorts in a losing position.</p>
<p>Bear traps occur because support levels are visible to everyone. Professional traders and algorithms know exactly where stop orders cluster and where short sellers will pile in after a support break. A brief engineered breakdown — sometimes just an intraday move that closes back above support — can flush out the nervous holders, collect their stop-loss sales at low prices, and set up a sharp move higher when the trap springs.</p>
<p>The tell is volume: a genuine breakdown below support typically involves <em>high</em> volume as sellers accelerate into the move. A bear trap shows relatively low volume on the breakdown — not enough sellers to sustain the move below support — followed by a recovery close back above it.</p>""",
    },
    {
        "term_key": "capitulation-final-flush",
        "section_id": "strategies",
        "term_title": "💥 Capitulation (The Final Flush)",
        "question": "Why is capitulation often bullish in its aftermath?",
        "answer": "Forced sellers have been flushed out, leaving mostly long-term holders and new buyers, so even modest demand can cause a sharp bounce",
        "distractors": [
            "Capitulation guarantees the stock will return to its prior all-time high within a week",
            "Capitulation only happens when a company reports a profit warning",
            "Trading is automatically halted for the rest of the session",
        ],
        "explanation": """<p><strong>Capitulation</strong> is the moment in a decline when the last reluctant holders give up and sell at any price. Before capitulation, you have orderly selling — investors selling in stages as the stock declines, with hope that it will recover. Capitulation is the end of that hope: forced selling (margin calls, stop-losses triggering, panic) floods the market simultaneously, creating a volume spike and an often violent, fast price drop.</p>
<p>Paradoxically, capitulation is bullish in its aftermath. After capitulation, there are almost no more forced sellers left. The only remaining participants are long-term investors who survived the panic (they weren't selling), and new buyers who see value at depressed prices. With selling pressure exhausted, even modest buying demand can cause a sharp bounce.</p>
<p>The statistical signature is specific: volume more than 3 standard deviations above the 20-day average (an extreme spike), RSI below 30 (extreme oversold), but critically, the day's candle closes in the <em>upper half of its range</em> — the long lower wick shows that institutions absorbed the panic selling, buying everything that desperate sellers were throwing at the market. This close in the upper range is the "absorption" signal — potential indication of the true bottom.</p>""",
    },
    {
        "term_key": "wyckoff-accumulation-phase",
        "section_id": "strategies",
        "term_title": "🏗️ Wyckoff Accumulation Phase",
        "question": "Why does Smart Money accumulate shares slowly during the Wyckoff Accumulation Phase?",
        "answer": "Large institutions can't buy millions of shares without moving the price significantly, so they buy slowly to avoid revealing their hand",
        "distractors": [
            "Regulations require institutional purchases to be spread over at least six months",
            "Slow accumulation avoids capital gains tax on the eventual sale",
            "It allows the company to issue new shares at a discount",
        ],
        "explanation": """<p>Richard Wyckoff was a 1930s stock market analyst who described the market as a battleground between large professional operators ("Smart Money") and the uninformed public. His most important observation: after a capitulation bottom, Smart Money doesn't immediately push prices higher. Instead, it quietly accumulates shares over weeks or months in a "trading range" where prices move sideways in a narrow band with declining volume. This is the <strong>Accumulation Phase</strong>.</p>
<p>Why so quiet? Large institutions can't buy millions of shares without moving the price significantly. So they buy slowly — absorbing supply whenever sellers emerge, without revealing their hand. The price goes nowhere (frustrating everyone watching), volume dries up (fewer and fewer sellers are left), and volatility contracts. This specific pattern — Bollinger Band squeeze, declining ATR, drying volume — is the quantitative fingerprint of accumulation in progress.</p>
<p>When the absorption is complete, a <strong>breakout</strong> occurs: high-volume expansion through the upper band of the range, confirming that institutional buying has accelerated and the accumulation phase is over. This breakout is the entry signal — ideally caught as close to the pivot point as possible.</p>""",
    },
    {
        "term_key": "trap-phase-history-accuracy",
        "section_id": "strategies",
        "term_title": "📊 Trap Phase History & Prediction Accuracy",
        "question": "Why does the system require at least 5 resolved predictions per phase before showing an accuracy percentage?",
        "answer": "To avoid statistically meaningless figures from very small sample sizes",
        "distractors": [
            "Because regulations require a minimum sample size for financial claims",
            "Because fewer than 5 predictions would take too long to compute",
            "Because the Trap Monitor only scans 5 tickers per day",
        ],
        "explanation": """<p>Each time the Trap Monitor scan runs (typically once per day), the detected lifecycle phase for every ticker is saved to a persistent database table. This creates a timestamped history: "On 2024-10-15, ticker XYZ was classified as Wyckoff Accumulation."</p>
<p>A background job then looks up the actual closing price of each ticker 14 and 30 calendar days after each phase was detected, and compares it to the price on the detection date. If the price moved in the direction predicted by the phase classification (lower for Bull Trap, higher for Capitulation / Accumulation / Bear Trap), the prediction is marked "correct." This builds an accuracy record for each phase label based on real outcomes — not backtested simulations.</p>
<p>At least 5 resolved predictions per phase are required before an accuracy percentage is shown, to avoid statistically meaningless figures from very small samples. The accuracy chart on the Trap Monitor page is updated daily as new resolutions come in.</p>""",
    },
    {
        "term_key": "market-leader",
        "section_id": "strategies",
        "term_title": "👑 Market Leader",
        "question": "What does a positive Relative Strength (RS) reading indicate about a stock?",
        "answer": "The stock is performing better than the S&P 500 benchmark over the defined period",
        "distractors": [
            "The stock has the largest market capitalisation in its sector",
            "The stock pays the highest dividend yield among its peers",
            "The stock's beta is exactly 1.0",
        ],
        "explanation": """<p>A <strong>Market Leader</strong> tag is assigned to a stock that shows positive <strong>Relative Strength (RS)</strong> compared to the S&amp;P 500. Relative Strength measures how much better (or worse) a stock has performed compared to the market benchmark over a defined period.</p>
<p>The concept, popularised by William O'Neil and his CANSLIM method, is straightforward: if you're going to own individual stocks rather than just the index, you want stocks that are outperforming the market — not just rising because a rising tide is lifting all boats. A Market Leader rises faster than the index in bull markets and falls less (or continues rising) when the market corrects. That relative strength reflects genuine investor demand and institutional accumulation.</p>
<p>Practically: during a market rally, owning market leaders means your portfolio outperforms. During a correction, market leaders often signal their strength by refusing to fall as much as the broad market. When even market leaders start declining at the rate of the index, it is often an early warning that the rally is faltering.</p>""",
    },
    # --- behavioral-finance ---
    {
        "term_key": "cognitive-biases-investing",
        "section_id": "behavioral-finance",
        "term_title": "Cognitive Biases That Sabotage Investors",
        "question": "What is the 'disposition effect'?",
        "answer": "Selling winning positions too early to lock in gains, while holding losing positions far too long hoping they'll recover",
        "distractors": [
            "Only buying stocks that have already doubled in price",
            "A legal requirement to disclose all trades within 24 hours",
            "Only trading stocks within your own home country",
        ],
        "explanation": """<p>Every metric and signal elsewhere in this glossary assumes you'll actually act on what it tells you — in practice, a handful of well-documented mental shortcuts quietly push investors toward the opposite of what the data suggests. <strong>Loss aversion</strong> is the root of several of them: the pain of losing £1,000 feels stronger than the pleasure of gaining £1,000, which alone explains a lot of otherwise irrational behaviour.</p>
<p><strong>Confirmation bias</strong> is seeking out information that supports a position you already hold and dismissing anything that contradicts it — the reason a losing thesis can survive long after the original evidence for it has stopped being true. The <strong>disposition effect</strong> is its natural companion: selling winners too early to "lock in" the good feeling, while holding losers far too long hoping they'll "get back to even" — the exact opposite of the discipline this app's ATR Stop-Loss and Position Targets are designed to enforce mechanically.</p>
<p><strong>Anchoring</strong> is over-weighting the first number you saw — your purchase price, a 52-week high, an analyst target — when deciding what to do next, even though the market has no memory of what you paid. <strong>Overconfidence</strong> and the closely related <strong>illusion of control</strong> lead to believing you can reliably pick winners or time entries/exits, which in practice shows up as excessive trading and undersized attention to risk management. <strong>Recency bias</strong> and <strong>herding</strong> — extrapolating whatever has happened lately, and following what everyone else is doing rather than your own analysis — are what turn an ordinary trend into a crowded, fragile one (see Market Sentiment Cycles below).</p>""",
    },
    {
        "term_key": "market-sentiment-cycles",
        "section_id": "behavioral-finance",
        "term_title": "Market Sentiment Cycles — Fear, Greed & Capitulation",
        "question": "Why do extreme sentiment readings function as contrarian indicators?",
        "answer": "When nearly everyone already agrees on a direction, there are very few investors left to keep pushing the price further that way",
        "distractors": [
            "Sentiment indicators are always wrong, so the opposite is guaranteed to happen",
            "Extreme sentiment readings are illegal for brokers to publish",
            "Sentiment has no relationship to price extremes whatsoever",
        ],
        "explanation": """<p>Aggregate investor mood swings between extremes far more than fundamentals actually change day to day, and that swing is itself a tradeable signal. Near market tops, <strong>excessive optimism</strong> shows up as complacency, rising margin debt, and widespread public participation from inexperienced investors chasing recent gains — the exact conditions this app's Bubble Radar tool screens for. Near market bottoms, <strong>excessive pessimism</strong> shows up as panic selling, heavy media coverage of losses, and capitulation (see the Capitulation term-box in Trading Strategies) — conditions the Dip Radar and Trap Monitor are built to detect.</p>
<p>A full speculative <strong>mania</strong> tends to follow a recognisable arc: a genuine development creates real opportunity (displacement), attracts increasing capital as early gains prove out (boom), then draws in the general public purely because prices are rising (euphoria) — at which point the buying is driven by the price action itself rather than anything fundamental, right before the reversal.</p>
<p>Because sentiment extremes are measurable, they double as <strong>contrarian indicators</strong>: survey-based gauges (like the CNN Fear &amp; Greed Index this app's Market Sentiment page already tracks), volatility-based gauges (the VIX), and positioning data (elevated margin debt, skewed put/call ratios) all tend to cluster at genuine price extremes. This doesn't mean sentiment extremes mark the exact top or bottom — only that when nearly everyone already agrees on a direction, there are very few investors left to push the price further that way.</p>""",
    },
    # --- machine-learning ---
    {
        "term_key": "ml-confidence-score",
        "section_id": "machine-learning",
        "term_title": "ML Confidence Score",
        "question": "Why does the system use a Soft-Voting Ensemble of XGBoost and Random Forest rather than a single model?",
        "answer": "The two models have different strengths — XGBoost learns non-linear interactions well, Random Forest avoids overfitting to recent noise — so averaging their estimates is more robust",
        "distractors": [
            "Soft-voting always produces a higher score than either model alone",
            "XGBoost cannot process technical indicators, only fundamental data",
            "Random Forest is used only as a fallback when XGBoost fails to run",
        ],
        "explanation": f"""<p>Every stock in the universe receives a <strong>ML Confidence Score</strong> — a number between 0% and 100% that represents how likely a machine learning model thinks the stock will gain more than {_PREDICTION_THRESHOLD_PCT}% over the next {PREDICTION_HORIZON_DAYS} trading days (roughly two calendar weeks), measured from the close on the day of the scan to the close {PREDICTION_HORIZON_DAYS} days later.</p>
<p>The model is a <strong>Soft-Voting Ensemble</strong>, which means it combines two separate models — XGBoost and Random Forest — and averages their probability estimates rather than taking a hard yes/no vote. This makes it more robust because the two models have different strengths: XGBoost is better at learning non-linear interactions between features; Random Forest is better at avoiding overfitting to recent noise.</p>
<p><strong>XGBoost</strong> (eXtreme Gradient Boosting) builds a sequence of small decision trees where each tree learns from the mistakes of the previous ones — a technique called gradient boosting. <strong>Random Forest</strong> builds hundreds of independent decision trees on random subsets of data and features, then averages their predictions. Neither model "knows" finance — they learn statistical patterns from 18 technical and fundamental features (RSI, MACD, volume ratios, SMA distances, etc.) across thousands of past observations.</p>
<p>A score of 75% does not mean "this stock will definitely go up 3% in 10 days." It means: of all historical situations where the model saw this same combination of signals, the stock gained more than {_PREDICTION_THRESHOLD_PCT}% in the next {PREDICTION_HORIZON_DAYS} days about 75% of the time. Markets are inherently unpredictable — even a perfect model would be wrong 25–30% of the time just due to randomness.</p>""",
    },
    {
        "term_key": "quant-algo-trading-backtesting-overfitting",
        "section_id": "machine-learning",
        "term_title": "Quantitative & Algorithmic Trading — Backtesting & Overfitting",
        "question": "Why is out-of-sample testing important when validating a quantitative trading rule?",
        "answer": "It tests the rule against data it was never tuned against, guarding against overfitting to patterns that were only real by chance in the historical data used to build it",
        "distractors": [
            "It's a legal requirement before any strategy can be published",
            "It guarantees the strategy will be profitable in live trading",
            "It only matters for strategies that trade options, not stocks",
        ],
        "explanation": """<p><strong>Quantitative investing</strong> is what this whole section is an example of: replacing individual stock-picking judgement with systematic, rules-based signals derived from data — <strong>factor investing</strong> (value, momentum, quality, size, low-volatility — each a return driver identified by academic research) is the classic version, and this app's ML Confidence Score, Isolation Forest anomaly detection, and Market Regime classification are all more modern extensions of the same idea.</p>
<p>Any quantitative strategy lives or dies on <strong>backtesting</strong> — testing the rule against historical data before trusting it with real money. The central danger is <strong>overfitting</strong> (also called data mining): with enough trial and error across enough historical data, it's always possible to find a rule that would have worked brilliantly in the past purely by chance, with no real predictive power going forward. Guarding against this means testing on data the rule was never tuned against (<strong>out-of-sample testing</strong>) and re-testing sequentially forward through time rather than all at once (<strong>walk-forward analysis</strong>) — this app's own ETF Predictor and Bubble Radar accuracy tracking (comparing predictions to what actually happened afterward) exists for exactly this reason: a model is only trustworthy once it's been checked against outcomes it couldn't have seen in advance.</p>
<p>A second real-world risk is <strong>factor crowding</strong>: once a genuine edge becomes widely known and many investors chase the same signal simultaneously, the returns to that signal shrink or the strategy becomes prone to sudden, correlated unwinds (a variant of the momentum crash described in the Technical Analysis section) — a reminder that even a statistically sound signal is not a permanent, risk-free edge.</p>""",
    },
    {
        "term_key": "value-at-risk-95",
        "section_id": "machine-learning",
        "term_title": "Value at Risk — 1-day, 95% (Historical Simulation)",
        "question": "What does a 1-day 95% VaR of 12% mean for a position?",
        "answer": "There is a 95% probability the position will not lose more than 12% in a single trading day",
        "distractors": [
            "The position is guaranteed to lose exactly 12% at some point",
            "The position has a 95% chance of gaining 12% or more the next day",
            "95% of the position's value is held in cash reserves",
        ],
        "explanation": """<p><strong>Value at Risk (VaR)</strong> answers a specific question: "On a bad-but-not-catastrophic day, how much could I lose?" It is expressed as a percentage of position value and is the standard risk metric used by banks, hedge funds, and institutional investors worldwide.</p>
<p>The "95%" means we're looking at the 95th percentile bad day — i.e., the loss level that the worst 5% of days exceeded. On 1 out of every 20 trading days, you can expect to lose <em>more</em> than the VaR figure. On the other 19 days, your loss should stay within it.</p>
<p>This app uses <strong>Historical Simulation</strong> to calculate VaR: it looks at the actual daily returns over the past year, sorts them from worst to best, and takes the 5th percentile (the 95th-worst outcome). This approach captures real market behaviour — including fat tails and crash days — rather than assuming returns follow a neat bell curve.</p>""",
    },
    {
        "term_key": "conditional-var-cvar",
        "section_id": "machine-learning",
        "term_title": "Conditional VaR (CVaR) — Expected Shortfall",
        "question": "What does CVaR add beyond what VaR already tells you?",
        "answer": "The average severity of losses on the days that exceed the VaR threshold, not just the threshold itself",
        "distractors": [
            "CVaR always equals exactly half of the VaR figure",
            "CVaR replaces the need to calculate VaR at all",
            "CVaR measures only gains, never losses",
        ],
        "explanation": """<p>VaR has a well-known blind spot: it tells you where the threshold is, but says nothing about how bad things get once you cross it. A 95% VaR of 12% could mean the worst 5% of days all cluster just below −13% (manageable), or that one of those days was −40% (catastrophic). The VaR number looks the same.</p>
<p><strong>Conditional VaR (CVaR)</strong>, also called <strong>Expected Shortfall (ES)</strong>, solves this by averaging all the losses that exceed the VaR threshold. It answers: "Given that tomorrow is a catastrophic day (one of the worst 5%), how bad will it be on average?"</p>
<p>CVaR is always worse (higher) than VaR. If VaR is 12% and CVaR is 18%, it means: on the bad days that cross the VaR boundary, your average loss is 18%, not 12%. This is the number that tells you the <em>severity</em> of tail risk, not just the frequency. Academic and regulatory risk frameworks (Basel III, FRTB) have been shifting toward CVaR as the primary risk metric precisely because of this.</p>
<p>Practically: a stock with VaR=10% and CVaR=11% has a smooth tail — bad days are all similar severity. A stock with VaR=10% and CVaR=25% has a fat, lumpy tail — most bad days are okay but occasionally there is a violent crash. Same VaR, very different risk profiles.</p>""",
    },
    {
        "term_key": "vader-sentiment-score",
        "section_id": "machine-learning",
        "term_title": "VADER Sentiment Score",
        "question": "What is a key limitation of VADER sentiment scoring?",
        "answer": "It is purely lexical and doesn't deeply understand context — e.g. 'avoided a crash' scores negatively because of the word 'crash'",
        "distractors": [
            "It requires a live internet connection to an external API to function",
            "It cannot process any text longer than a single sentence",
            "It only works on non-English news articles",
        ],
        "explanation": """<p><strong>VADER</strong> (Valence Aware Dictionary and sEntiment Reasoner) is a rule-based Natural Language Processing (NLP) model originally designed for social media text. It reads news headlines and short article summaries and outputs a sentiment score ranging from <strong>−1.0</strong> (extremely negative/fearful) to <strong>+1.0</strong> (extremely positive/euphoric), with 0 being completely neutral.</p>
<p>Unlike machine learning models that need training data, VADER works from a carefully curated lexicon of financial and general language words, each pre-assigned a sentiment strength. Words like "surge", "beat", "record", and "buyback" push the score positive. Words like "crash", "miss", "investigation", "downgrade", and "layoffs" push it negative. It also handles punctuation (!!!) and capitalization (CRASH) as intensity amplifiers.</p>
<p>It is fast and works offline (no external API required), which makes it practical for scanning hundreds of news articles daily. Its limitation is that it is purely lexical — it doesn't understand context deeply. "The company avoided a crash" would be scored negatively because of the word "crash", when the actual meaning is positive. This is why the app uses VADER alongside FinBERT (a purpose-trained financial NLP model) rather than relying on VADER alone.</p>""",
    },
    {
        "term_key": "turbulence-index-volatility-regimes",
        "section_id": "machine-learning",
        "term_title": "Turbulence Index & Volatility Regimes",
        "question": "Why does the Turbulence Index use EWMA rather than a simple moving average?",
        "answer": "EWMA gives more weight to recent data, making it far more responsive to sudden volatility spikes",
        "distractors": [
            "EWMA is required by financial regulators for all volatility calculations",
            "EWMA removes the need to track FTSE data alongside SPY",
            "EWMA always produces a lower reading than a simple average",
        ],
        "explanation": """<p>The <strong>Turbulence Index</strong> is a measure of how chaotic and unpredictable the current market environment is, based on the realized volatility of SPY (the S&P 500 ETF) and FTSE over the past 20 trading days, weighted using <strong>EWMA</strong> (Exponentially Weighted Moving Average).</p>
<p><strong>EWMA</strong> gives more importance to recent data than older data, making it much more responsive to sudden volatility spikes than a simple 20-day average. The decay factor λ=0.94 means yesterday's observation carries roughly 94% of today's weight, last week's data carries about 70%, and data from a month ago carries around 40%. This weighting is a direct copy of the JP Morgan RiskMetrics standard from the 1990s, which became an industry benchmark for this reason.</p>
<p>The index classifies the current environment into three regimes:</p>""",
    },
    {
        "term_key": "market-regime-price-hmm",
        "section_id": "machine-learning",
        "term_title": "Market Regime — Price HMM",
        "question": "What are the three hidden states the Price HMM classifies the market into?",
        "answer": "Bull, Chop, and Crash",
        "distractors": [
            "Risk-On, Late Cycle, and Stagflation",
            "Overbought, Neutral, and Oversold",
            "Accumulation, Markup, and Distribution",
        ],
        "explanation": """<p>A <strong>Hidden Markov Model (HMM)</strong> is a type of statistical model that assumes the world passes through a series of unobserved ("hidden") states, and what we observe (the data) is a noisy signal emitted by whichever hidden state the world is currently in.</p>
<p>Think of it like the weather: you can't directly observe "weather system type" — but you can observe rain, temperature, and wind. An HMM learns the relationship between the observable signals and the hidden states, then infers which state you're most likely in right now.</p>
<p>For markets, the "hidden states" are the true underlying market regimes: Bull, Chop, or Crash. The observable signals are the daily SPY log-returns and 20-day EWMA realized volatility. The HMM is trained on 5 years of SPY data and re-trained every night so it learns from the most recent market behaviour. It then uses the <strong>Viterbi algorithm</strong> to find the single most-likely sequence of regime states across all historical data, and outputs the current state with a confidence probability.</p>""",
    },
    {
        "term_key": "macro-regime-label",
        "section_id": "machine-learning",
        "term_title": "🌍 Macro Regime Label (Yield Curve Allocator)",
        "question": "What has an inverted yield curve historically preceded?",
        "answer": "Every US recession since the 1960s",
        "distractors": [
            "A guaranteed stock market rally within 3 months",
            "A mandatory Federal Reserve rate cut within 30 days",
            "A currency devaluation of the US dollar",
        ],
        "explanation": """<p>While the Price HMM reads market signals, the <strong>Macro Regime Label</strong> reads economic fundamentals: the shape of the yield curve, CPI inflation, high-yield credit spreads, the real 10-year yield, and the HMM hidden state. Together, these paint a picture of where the economic cycle is currently positioned.</p>
<p>The <strong>yield curve</strong> shows interest rates for government bonds at different maturities. Normally it slopes upward — you get paid more for lending for longer (more risk). When short-term rates exceed long-term rates, the curve <strong>inverts</strong> — and this has preceded every US recession since the 1960s. It is considered one of the most powerful economic leading indicators.</p>
<p>Five macro regime labels are defined:</p>""",
    },
    {
        "term_key": "portfolio-regime-alignment-score",
        "section_id": "machine-learning",
        "term_title": "Portfolio Regime Alignment Score",
        "question": "How is the Regime Alignment Score calculated?",
        "answer": "Cosine similarity between your current allocation weights and the ideal weights for the detected regime, scaled to 0-100",
        "distractors": [
            "The percentage of your portfolio held in cash",
            "The difference between your portfolio's beta and 1.0",
            "The number of asset classes currently held",
        ],
        "explanation": """<p>Knowing the macro regime is only useful if your portfolio reflects it. The <strong>Regime Alignment Score</strong> measures how closely your current asset class weights (equities, bonds, commodities, cash) match the historically optimal weights for the detected regime.</p>
<p>It uses <strong>cosine similarity</strong> — a mathematical measure of how "parallel" two vectors are. If your current allocation vector exactly matches the ideal regime allocation, the similarity is 1.0 (= 100 score). If your allocation points in a completely different direction, similarity is 0 (= 0 score). The calculation is: score = cosine_similarity(your weights, ideal weights) × 100.</p>
<p>A score below 50 means your portfolio is meaningfully positioned for a different regime than the one the model detects. This is not a trade recommendation — individual conviction, tax situation, and liquidity needs are not captured here. But it is a prompt to ask: "Am I positioned for today's environment, or yesterday's?"</p>""",
    },
    {
        "term_key": "isolation-forest-anomaly-score",
        "section_id": "machine-learning",
        "term_title": "Isolation Forest Anomaly Score",
        "question": "Why is the Isolation Forest described as 'unsupervised'?",
        "answer": "It needs no labelled data — it learns what 'normal' looks like for each ticker and flags deviations from that baseline",
        "distractors": [
            "It only runs once per year without any retraining",
            "It requires manual labelling of every anomaly before it can operate",
            "It only analyses a single data dimension per ticker",
        ],
        "explanation": """<p>Normal machine learning models learn from labelled data — "these past situations led to a 20% drop, those led to a 5% gain." The <strong>Isolation Forest</strong> is different: it is <em>unsupervised</em>, meaning it needs no labels. Instead of learning what a "bad" situation looks like, it simply learns what <em>normal</em> looks like for each ticker — and then flags anything that deviates significantly from that normal baseline.</p>
<p>The algorithm works by randomly selecting a feature and a random split value, then recursively splitting the data. Anomalous points (those that look unusual) get isolated in fewer splits than normal points — hence the name. An anomaly score is assigned based on how quickly the point was isolated: the faster it is isolated, the more anomalous it is.</p>
<p>The model monitors <strong>six simultaneous dimensions</strong> for each ticker: intraday volume vs. its 20-day average, RSI momentum, daily return percentage, distance from the 50-day SMA, 20-day historical volatility, and beta-adjusted market sensitivity. A volume spike alone is not an anomaly — volume spikes happen. But a volume spike occurring simultaneously with an RSI divergence and an SMA break, in a stock that rarely does any of those things, is anomalous.</p>
<p>Scores range from 0.00 to 1.00:</p>""",
    },
    {
        "term_key": "market-stress-score",
        "section_id": "machine-learning",
        "term_title": "Market Stress Score",
        "question": "How does the Market Stress Score differ from the Isolation Forest Anomaly Score?",
        "answer": "It measures whether the overall market environment is behaving strangely, using six macro features, rather than a single stock's behaviour",
        "distractors": [
            "It is calculated only once a year",
            "It replaces the need for the Price HMM regime classification",
            "It only applies to bond markets, never equities",
        ],
        "explanation": """<p>Where the Isolation Forest Anomaly Score asks "is this individual stock behaving strangely?", the <strong>Market Stress Score</strong> asks "is the market environment itself behaving strangely?" It is a market-wide anomaly detection system, not a per-stock one.</p>
<p>It uses the same Isolation Forest algorithm but trains on six macro market features: the VIX volatility level, the VIX relative to its 20-day average (capturing spikes), the HYG high-yield credit ETF daily return (a proxy for credit market stress), the US 10-year Treasury yield daily change, the SPY volume z-score, and the SPY daily return. These six features capture different dimensions of market health simultaneously.</p>
<p>A score near 1.0 means the joint combination of these six signals is in territory that only occurred in roughly 5% of all trading days over the past two years — the tail of the distribution. This often precedes or coincides with major market dislocations.</p>""",
    },
    {
        "term_key": "monte-carlo-simulation",
        "section_id": "machine-learning",
        "term_title": "Monte Carlo Simulation",
        "question": "Why does the Monte Carlo engine simulate asset returns together using their real correlations, rather than independently?",
        "answer": "So that during a simulated equity crash, bonds behave as they historically have during equity crashes, not independently",
        "distractors": [
            "To guarantee every simulated path shows a positive return",
            "To reduce the number of simulations needed from 1,000 to 10",
            "Because correlated simulation runs faster than independent simulation",
        ],
        "explanation": """<p>No one can predict whether your portfolio will be worth £800,000 or £1,200,000 in 20 years. But we can build a realistic model of the range of possible outcomes — that is what <strong>Monte Carlo simulation</strong> does.</p>
<p>The process: instead of projecting one "expected" future, the model runs 1,000 separate simulated futures. In each simulation, annual returns for each asset are drawn randomly from a probability distribution defined by historical drift (expected return) and volatility (variability around that expected return). The key refinement is that assets are simulated <em>together</em> using their real pairwise correlations (Cholesky decomposition of the live correlation matrix from the X-ray engine) — so in the simulation, if equities crash, bonds behave as they historically have during equity crashes, not independently.</p>
<p>After running all 1,000 simulations, you have 1,000 different "futures." Some are optimistic (strong returns throughout), some are pessimistic (crashes early, slow recovery), most are somewhere in between. By sorting these 1,000 outcomes, you can read off what the best 5%, best 25%, median, worst 25%, and worst 5% outcomes look like. This is the <strong>Percentile Fan</strong> chart.</p>
<p>Monte Carlo is not a crystal ball — if the next 20 years have structural changes (persistently higher inflation, lower equity returns) not captured in recent history, the simulation will be systematically biased. The key insight is that even with realistic assumptions, the range of outcomes is enormous — which is the honest truth about long-horizon investing that point forecasts hide.</p>""",
    },
    {
        "term_key": "drift-assumption",
        "section_id": "machine-learning",
        "term_title": "Drift Assumption",
        "question": "What is the 'drift' in a Monte Carlo simulation?",
        "answer": "The expected annual return before volatility is applied — the centre of gravity around which random outcomes scatter",
        "distractors": [
            "The maximum possible loss the simulation will allow",
            "The number of years simulated",
            "The correlation between two specific assets",
        ],
        "explanation": """<p>In a Monte Carlo simulation, the <strong>drift</strong> (μ, the Greek letter "mu") is the expected annual return before volatility is applied — the "centre of gravity" around which random outcomes scatter. It must be chosen before running the simulation and has a significant effect on outcomes over long horizons.</p>
<p>The defaults in this app reflect long-run historical averages: 7.0% for Global Equity ETFs, 6.5% for UK Equities, 3.5% for Bonds. These are <em>nominal</em> (before inflation) returns. Real (inflation-adjusted) returns are roughly 2% lower. Whether these historical averages will hold over the next 20–30 years is genuinely uncertain.</p>
<p>A key lesson from Monte Carlo: <strong>small changes in drift create huge changes in outcomes over long horizons</strong>. At 7% annual drift, £100,000 grows to a median of ~£387,000 over 20 years. At 5% drift, the median is ~£265,000. That 2% difference compresses by a factor of almost 1.5× — which is why fund charges (which directly reduce your effective drift) matter so much over a lifetime.</p>""",
    },
    {
        "term_key": "percentile-fan",
        "section_id": "machine-learning",
        "term_title": "Percentile Fan",
        "question": "What does a wide Percentile Fan indicate?",
        "answer": "Outcomes are highly dispersed — higher volatility and/or a longer time horizon",
        "distractors": [
            "The simulation only ran 100 paths instead of 1,000",
            "The portfolio is guaranteed to underperform its target",
            "All five percentile lines converge to the same value",
        ],
        "explanation": """<p>The <strong>Percentile Fan</strong> chart shows all 1,000 simulated wealth paths collapsed into five summary lines: the 5th, 25th, 50th (median), 75th, and 95th percentile outcomes. The shaded bands between them represent the range of uncertainty.</p>
<p>How to read it: at any given year on the x-axis, the vertical span of the fan is your uncertainty range. A narrow fan means your portfolio is relatively predictable (lower volatility, shorter horizon); a wide fan means outcomes are highly dispersed (higher volatility, longer horizon). The outer band (P5–P95) captures 90% of all simulated outcomes — the lines above and below represent the extreme 5% tails.</p>
<p>Most people focus on the median line (P50), but the <em>shape</em> of the fan matters just as much. If the P5 line shows your portfolio declining in real terms even in the 20-year scenario, you have either too much volatility, too little expected return, or too aggressive a withdrawal rate for your timeline.</p>""",
    },
    {
        "term_key": "probability-of-success",
        "section_id": "machine-learning",
        "term_title": "Probability of Success",
        "question": "What does a Probability of Success of 0.73 mean?",
        "answer": "730 out of 1,000 simulated futures reached or exceeded the target wealth level",
        "distractors": [
            "The portfolio has a 73% chance of losing money next year",
            "73% of the portfolio is allocated to equities",
            "The simulation ran for exactly 73 years",
        ],
        "explanation": """<p>If you set a target wealth level in the Monte Carlo Simulator (e.g., "I want at least £500,000 in 20 years"), the <strong>Probability of Success</strong> tells you what fraction of the 1,000 simulated futures ended above that target.</p>
<p>A result of 0.73 means 730 out of 1,000 simulated futures reached or exceeded your target — a 73% success rate. Whether 73% is "good enough" is entirely personal. Some investors accept 60% (they're comfortable with the risk and have other income sources). Others want 90%+ before they feel secure. Increasing probability of success requires either a lower target, a longer timeline, more savings contributions, or a higher-return (but higher-risk) portfolio — there is no free lunch.</p>
<p>One important nuance: this metric assumes you do nothing for the entire horizon (no rebalancing, no additional contributions, no withdrawals other than a set rate). In reality, active management of the portfolio — rebalancing, adding contributions during downturns — meaningfully improves real-world outcomes beyond what the simulation shows. The simulation is a conservative baseline.</p>""",
    },
    # --- earnings-vol ---
    {
        "term_key": "what-happens-at-earnings",
        "section_id": "earnings-vol",
        "term_title": "What Happens at Earnings",
        "question": "What determines whether a stock jumps or falls after an earnings announcement?",
        "answer": "Whether the actual reported results beat or missed the consensus analyst estimate",
        "distractors": [
            "Whether the announcement happens before or after market close",
            "The size of the company's total market capitalisation",
            "Whether the CEO personally presents the results",
        ],
        "explanation": """<p>Every quarter, publicly listed companies are required to report their financial results — revenue, profit, and key business metrics. This is called an <strong>earnings announcement</strong> or <strong>earnings release</strong>. The market compares what the company actually reported against what analysts had expected (the <strong>consensus estimate</strong>). If results beat expectations, the stock typically jumps. If they miss, it typically falls — sometimes violently.</p>
<p>The day of an earnings announcement is the single most predictable moment of extreme price volatility in a stock's year. Options traders love earnings because the direction is uncertain but the fact that a big move is coming is near-certain. This predictable volatility creates two things: an opportunity to profit if options are mispriced, and a risk if you are holding a stock into earnings without hedging.</p>""",
    },
    {
        "term_key": "implied-move-atm-straddle",
        "section_id": "earnings-vol",
        "term_title": "Implied Move (ATM Straddle)",
        "question": "What does the cost of an ATM straddle directly tell you?",
        "answer": "The options market's consensus forecast of how big the earnings move will be, in either direction",
        "distractors": [
            "The exact direction the stock will move after earnings",
            "The company's expected revenue growth rate",
            "The dividend that will be paid on the ex-date",
        ],
        "explanation": """<p>Before earnings, options traders price in the expected magnitude of the price move. You can measure this expectation directly by looking at the cost of an <strong>ATM (At-The-Money) Straddle</strong> — buying both a call option and a put option at the current stock price with the nearest expiry after the earnings date.</p>
<p>If you buy both the call and the put, you profit if the stock moves significantly in either direction — up or down doesn't matter. The cost you pay for this strategy (the combined premium of the call + put) directly tells you what the options market expects the stock to move. If the straddle costs 8% of the stock price, the market is implying the stock will move approximately ±8% around earnings.</p>
<p>This is the <strong>Implied Move</strong>: the options market's consensus forecast of how big the earnings reaction will be, expressed as a percentage. It is symmetric — it says nothing about direction, only magnitude.</p>""",
    },
    {
        "term_key": "historical-average-move",
        "section_id": "earnings-vol",
        "term_title": "Historical Average Move",
        "question": "How is the Historical Average Move calculated?",
        "answer": "The average of the absolute percentage price changes on the day after each of the last four earnings announcements",
        "distractors": [
            "The average implied volatility over the past year",
            "The stock's average daily move on any random trading day",
            "The percentage difference between analyst high and low estimates",
        ],
        "explanation": """<p>The options market's implied move is forward-looking (what traders expect to happen). The <strong>Historical Average Move</strong> is backward-looking: it measures the actual absolute percentage price change the stock experienced on the day after each of its last four earnings announcements, then averages them.</p>
<p>For example, if a stock moved +12%, −5%, +9%, and −7% on the four past earnings days, the historical average move is (12+5+9+7)/4 = 8.25%. This is the best empirical estimate of how much this specific stock actually reacts to earnings based on its own history — not what the options market thinks, but what has actually happened.</p>""",
    },
    {
        "term_key": "mathematical-edge-score",
        "section_id": "earnings-vol",
        "term_title": "Mathematical Edge Score",
        "question": "What does a positive Edge Score (Historical > Implied) suggest?",
        "answer": "The options are cheap relative to history — a theoretical edge for an options buyer",
        "distractors": [
            "The stock is guaranteed to beat earnings estimates",
            "The company has increased its dividend",
            "Options trading has been halted for that ticker",
        ],
        "explanation": """<p>The <strong>Edge Score</strong> is the difference between what actually happens (Historical Average Move) and what the options market is charging you to bet on it (Implied Move).</p>
<p><strong>Positive edge</strong> (Historical &gt; Implied) means the options are cheap relative to history — the options market is underestimating how much the stock tends to move at earnings. This is a theoretical edge for an options buyer: you're paying less than the expected value of the move. A straddle buyer profits when the stock moves more than the premium cost — if the stock historically moves 12% but you're only paying 8% implied, history suggests this straddle is cheap.</p>
<p><strong>Negative edge</strong> (Historical &lt; Implied) means the options are expensive — the market is overestimating the expected move. This is a theoretical edge for an options seller: you collect a premium that, based on history, exceeds the typical actual move. Option selling strategies (short straddle, iron condor) are designed to profit when the stock moves less than the implied move suggests.</p>""",
    },
    # --- dip-radar ---
    {
        "term_key": "intraday-mean-reversion",
        "section_id": "dip-radar",
        "term_title": "Intraday Mean Reversion",
        "question": "What condition does Dip Radar require before it considers a snap-back likely?",
        "answer": "Measurable signs of selling exhaustion across multiple independent signals simultaneously, not just one",
        "distractors": [
            "A single day where RSI crosses below 50",
            "The stock must be in a long-term downtrend for over a year",
            "The company must have reported negative earnings that quarter",
        ],
        "explanation": """<p><strong>Mean reversion</strong> is the mathematical tendency for extreme readings to revert toward their average over time. In markets, it manifests intraday when panic selling pushes a stock far below its "fair" intraday value — so far that the selling becomes self-reinforcing (triggering stop-losses, margin calls, and further panic), rather than reflecting any new fundamental information about the company.</p>
<p>This is the principle Dip Radar is built on: when intraday prices overshoot to the downside due to mechanical/emotional selling pressure, and when that selling pressure shows measurable signs of exhaustion (extreme RSI, extreme distance from VWAP, extreme volume spike), a snap-back toward fair value becomes statistically likely. This is not a prediction that the stock will go up for days — it is a shorter-term reversion toward the session's "true" price range.</p>
<p>The model combines four independent measurements of this exhaustion and only fires an alert when the combined evidence is compelling — reducing false positives from any single signal triggering on its own.</p>""",
    },
    {
        "term_key": "reversal-score",
        "section_id": "dip-radar",
        "term_title": "Reversal Score (0–100)",
        "question": "What is the alert threshold for the Dip Radar Reversal Score?",
        "answer": "A score of 65 or above",
        "distractors": [
            "A score of exactly 100 only",
            "A score of 30 or above",
            "Any score above 0",
        ],
        "explanation": """<p>The <strong>Reversal Score</strong> is the composite signal output by the Dip Radar engine. Rather than a simple yes/no alert, it assigns points for each confirmed exhaustion condition — allowing you to assess how many independent signals have aligned simultaneously. More simultaneous signals = higher score = stronger evidence of exhaustion.</p>
<p>A score of 65–80 indicates a likely short-term exhaustion zone. A score of 80–100 indicates potential full capitulation washout — the highest-probability reversal setups the system identifies. The maximum score of 100 requires all four conditions firing simultaneously, which is rare and historically produces the sharpest bounces.</p>""",
    },
    {
        "term_key": "vwap",
        "section_id": "dip-radar",
        "term_title": "VWAP (Volume Weighted Average Price)",
        "question": "How does VWAP differ from a simple average of intraday prices?",
        "answer": "VWAP weights each price level by how much volume traded there, pulling the value toward heavily-traded price levels",
        "distractors": [
            "VWAP only considers the opening and closing prices of the session",
            "VWAP is recalculated only once per week",
            "VWAP ignores volume entirely and uses time-weighting instead",
        ],
        "explanation": """<p>The <strong>VWAP</strong> is the intraday "true fair value" of a stock. Unlike the simple average of intraday prices, VWAP weights each price level by how much volume traded at that level — so if 10 million shares traded between $99.80 and $100.20, and only 500,000 shares traded at $102, the VWAP will be pulled much closer to $100 than $102.</p>
<p>Institutional traders and algorithms use VWAP as the benchmark for execution quality: "Did I buy below VWAP or above it?" If they consistently buy above VWAP, they are paying more than fair intraday value. This is why large orders often try to execute close to the VWAP level — and why price has a strong statistical tendency to oscillate around it during the day.</p>
<p>Dip Radar anchors the VWAP from the market open (09:30 ET / 14:30 BST) and calculates a rolling 30-bar standard deviation band around it. A price trading 2.5σ below VWAP means it has deviated so far below the session's fair value that a statistical pull back is likely — particularly if the deviation is accompanied by a volume spike (capitulation) rather than just drifting lower on thin volume.</p>""",
    },
    {
        "term_key": "bollinger-bands-25-sigma",
        "section_id": "dip-radar",
        "term_title": "Bollinger Bands (2.5σ Extreme Variant)",
        "question": "Why does Dip Radar use a wider 2.5σ threshold instead of the standard 2σ?",
        "answer": "To filter out routine volatility and identify only genuinely extreme dislocations",
        "distractors": [
            "Because 1-minute data cannot support a 2σ calculation",
            "To make alerts fire more frequently throughout the session",
            "Because 2.5σ is the regulatory standard for volatility bands",
        ],
        "explanation": """<p><strong>Bollinger Bands</strong> are a volatility envelope drawn around a simple moving average (SMA). The upper and lower bands are plotted N standard deviations (σ) above and below the SMA, where σ is calculated from the price data over the same period. When volatility expands, the bands widen; when volatility contracts, they tighten. This means the bands automatically adapt to the stock's current behaviour.</p>
<p>In standard daily charting, Bollinger Bands use 20 periods and 2σ. Under a normal distribution, about 95.5% of all data points fall within 2σ of the mean — so a price outside the bands is statistically unusual. On 1-minute data, the distribution is not perfectly normal, but the principle holds: a price breaching the bands signals a statistical extreme.</p>
<p>Dip Radar deliberately uses a <strong>wider 2.5σ threshold</strong> rather than the standard 2σ, to filter out routine volatility and identify only genuinely extreme dislocations. A breach of the 2.5σ lower band on 1-minute data occurs in fewer than 1% of candles under approximately normal conditions. When it does occur, selling pressure has extended the price into statistically rare territory — a condition that historically precedes sharp mean-reversion bounces.</p>""",
    },
    {
        "term_key": "volume-capitulation-climax-volume",
        "section_id": "dip-radar",
        "term_title": "Volume Capitulation (Climax Volume)",
        "question": "What does an extreme volume spike on a down-candle typically indicate?",
        "answer": "Forced sellers (margin calls, stop-losses, panic) have been flushed out simultaneously, often exhausting selling pressure",
        "distractors": [
            "The company has just announced a stock buyback program",
            "Trading has been halted due to a circuit breaker",
            "Institutional buyers are selling to book profits",
        ],
        "explanation": """<p><strong>Capitulation volume</strong> is the single most powerful signal in the Dip Radar algorithm. It detects the specific intraday moment when panicking sellers all attempt to exit simultaneously, producing an extreme volume spike on a down-candle.</p>
<p>Here is why it matters: a sustained decline on normal volume means sellers are steadily exiting — supply and demand are adjusting gradually. But a sudden extreme volume spike on a down-candle means forced sellers (margin call recipients who must sell immediately at any price, stop-loss orders triggering in a cascade, retail panic triggered by a fast-moving price) have all been flushed out at once. After this "capitulation" moment, the category of forced sellers is effectively exhausted — they have already sold. The remaining participants are either long-term holders (not selling) or new buyers attracted by the price level. With forced sellers gone, even modest buying pressure can reverse the move sharply.</p>
<p>The statistical threshold — 3 standard deviations above the 20-candle rolling volume average — is strict. It filters out ordinary busy periods (1–2σ above average) and focuses only on extreme, anomalous volume events. On most trading days, you will not see a single 3σ volume candle. When you do, in conjunction with a sharp price drop, it is one of the clearest short-term exhaustion signals available.</p>""",
    },
    {
        "term_key": "session-scoped-monitoring",
        "section_id": "dip-radar",
        "term_title": "Session-Scoped Monitoring",
        "question": "Why must Dip Radar be manually activated per ticker rather than scanning continuously?",
        "answer": "Continuous scanning of thousands of tickers would generate enormous, mostly noisy alert volumes",
        "distractors": [
            "The exchange only allows 5-minute scans for pre-approved tickers",
            "It reduces the Yahoo Finance data fetch cost to zero",
            "Manual activation is required by financial regulation",
        ],
        "explanation": """<p>Dip Radar is <em>not</em> a persistent background scanner that runs continuously on all stocks. It must be explicitly activated per ticker from the stock detail page. Once activated, it monitors that ticker every 5 minutes during market hours and automatically deactivates at market close (16:05 ET, adjusted for the ticker's exchange).</p>
<p>This design is intentional. Running continuous intraday scans on thousands of tickers simultaneously would generate enormous alert volumes — most of them noise. By requiring manual activation per ticker, the system becomes a precision tool: you identify stocks you want to watch for intraday entry opportunities (perhaps because overnight analysis identified them as interesting setups), activate Dip Radar on them, and then the system monitors for the specific combination of exhaustion signals while you are away from the screen.</p>
<p>The deduplication system (the <code>alert_state</code> table) ensures that once an alert fires at a high score level, the same ticker does not generate repeated alerts for the same signal event — it is armed once per session, fires once when the threshold is crossed, then disarms for that session. This prevents alert fatigue from a single sustained dip generating dozens of notifications.</p>""",
    },
    # --- bubble-radar ---
    {
        "term_key": "what-bubble-radar-detects",
        "section_id": "bubble-radar",
        "term_title": "What Bubble Radar Detects",
        "question": "What does a high Bubble Radar score prompt you to consider?",
        "answer": "Whether you're comfortable holding at these valuation levels and whether you have an exit plan — not a prediction of when it will burst",
        "distractors": [
            "An immediate mandatory sell order",
            "That the stock will definitely crash within 24 hours",
            "That the company's accounting is fraudulent",
        ],
        "explanation": """<p>Financial bubbles are not unusual events — they happen regularly in different assets and sectors. Technology stocks in 2000, US housing in 2007, crypto in 2021, and AI-related stocks in 2023–2025 all exhibited classic bubble characteristics: prices far above any reasonable fundamental anchor, sustained RSI extremes, speculative options activity, and narrow breadth (a few popular stocks rising while most others lag).</p>
<p>The <strong>Bubble Radar</strong> scans your portfolio and watchlist for these specific mathematical signatures. It does not predict when a bubble will burst — bubbles can last far longer than rational analysis suggests they should — but it quantifies how detached from reality a stock's price currently appears across seven independent dimensions simultaneously. A high score should prompt the question "am I comfortable holding this at these valuation levels, and do I have an exit plan?"</p>""",
    },
    {
        "term_key": "bubble-risk-score",
        "section_id": "bubble-radar",
        "term_title": "Bubble Risk Score",
        "question": "Why is the Bubble Risk Score threshold-based rather than percentile-ranked against the market?",
        "answer": "So a score of 80 means the same absolute level of overextension for both a small-cap and a mega-cap stock",
        "distractors": [
            "Because percentile ranking is computationally too expensive to calculate",
            "Because threshold-based scoring only works for US-listed stocks",
            "Because percentile ranking requires real-time options data",
        ],
        "explanation": """<p>A composite 0–100 score built from seven weighted metrics. Each metric contributes a fixed number of points if it breaches its threshold, and the total is capped at 100. The scoring is <em>threshold-based</em> rather than relative (percentile-ranked vs. the market) — this means a score of 80 means the same thing for a small-cap UK stock as for a US mega-cap: both have crossed the same absolute levels of overextension simultaneously across the same set of metrics.</p>
<p>Scores are stored per scan per ticker (the scan cadence — daily or weekly — is configurable in Settings → Bubble Radar), allowing the Bubble Radar page to show: how long the score has been elevated, whether it is rising or falling, and how accurate the system's past "bubble" flags have been at predicting actual price corrections at 4, 8, and 12-week horizons. This accuracy tracking is critical — it tells you whether the model has actually been right in the past and prevents it from being taken on pure faith.</p>""",
    },
    {
        "term_key": "sma-200-extension",
        "section_id": "bubble-radar",
        "term_title": "SMA-200 Extension",
        "question": "Why is the 200-day SMA often treated as a proxy for long-run fair value?",
        "answer": "It moves very slowly and isn't easily distorted by short-term news events",
        "distractors": [
            "It is recalculated every 5 minutes during market hours",
            "It only applies to ETFs, not individual stocks",
            "It always equals the stock's average P/E ratio",
        ],
        "explanation": """<p>The <strong>200-day Simple Moving Average</strong> is the single most widely-watched long-term trend indicator in institutional finance. It represents approximately 200 trading days (~10 months) of average prices, and it moves very slowly — it is not easily distorted by short-term news events. Because of this, the SMA-200 is often used as a proxy for "long-run fair value" or the "institutional cost basis" of a large position built over many months.</p>
<p>When a stock's current price is 40% above its 200-day MA, it means buyers have paid a 40% premium over what the long-run trend suggests is fair value. Historically, very large extensions above the SMA-200 (25%+) have been reliable indicators of overvaluation in individual stocks — not every overextension leads to an immediate collapse, but the odds of a meaningful pullback toward the SMA-200 increase substantially at these levels.</p>""",
    },
    {
        "term_key": "sustained-rsi-high",
        "section_id": "bubble-radar",
        "term_title": "Sustained RSI High",
        "question": "What is unusual and historically correlated with bubbles regarding RSI?",
        "answer": "RSI averaging above 70 over 20 consecutive trading days, not just a single overbought day",
        "distractors": [
            "RSI falling below 30 for a single session",
            "RSI staying exactly at 50 for a month",
            "RSI diverging from price on a single day",
        ],
        "explanation": """<p>A single day with RSI above 70 is a normal overbought reading that occurs regularly in any strong trending stock. What is unusual — and historically correlated with bubbles — is RSI <em>averaging</em> above 70 over 20 consecutive trading days. That requires the stock to be in an almost uninterrupted strong-buying state for a full month with very few down-days disrupting the momentum.</p>
<p>This kind of sustained extreme reading typically reflects a self-reinforcing dynamic: price rises attract attention, attention attracts new buyers, new buyers push price higher, which attracts more attention. This is the definition of speculative momentum rather than fundamental value appreciation. While it can continue for weeks or months, it always eventually exhausts itself — and the reversal is typically sharp.</p>""",
    },
    {
        "term_key": "fcf-yield-vs-real-10-year",
        "section_id": "bubble-radar",
        "term_title": "FCF Yield vs Real 10-Year",
        "question": "What does it mean when a company's FCF yield falls below the real risk-free rate?",
        "answer": "Equity holders are accepting a lower expected return than risk-free government bonds, with far more risk — a textbook bubble indicator",
        "distractors": [
            "The company has zero debt on its balance sheet",
            "The company's dividend has just been increased",
            "The company is guaranteed to be acquired within a year",
        ],
        "explanation": """<p><strong>Free Cash Flow (FCF) Yield</strong> is a fundamental sanity check: how much cash does the company generate relative to how much investors are paying for it? It is calculated as (Free Cash Flow / Market Cap) × 100. A company generating £100m in annual free cash flow with a £2 billion market cap has a 5% FCF yield.</p>
<p>The <strong>Real 10-Year Yield</strong> (FRED series DFII10) is the US 10-year Treasury yield adjusted for inflation — this is the risk-free return available with zero equity risk. When a company's FCF yield falls below the real risk-free rate, equity holders are effectively accepting a lower expected return than they could get by simply holding US government bonds — with far more risk. This "paying more than risk-free for equity" condition is a textbook bubble indicator, most famously observed in the Dot-com era when many tech companies had negative FCF yields (they were burning cash) while government bonds yielded 5%+.</p>""",
    },
    {
        "term_key": "iv-call-skew",
        "section_id": "bubble-radar",
        "term_title": "IV Call Skew",
        "question": "What does it mean when call option implied volatility exceeds put implied volatility?",
        "answer": "The options market is pricing in more upside uncertainty than downside — historically associated with late-stage speculative excess",
        "distractors": [
            "The stock is guaranteed to fall in the near term",
            "The company has just announced a stock split",
            "Trading in the underlying stock has been suspended",
        ],
        "explanation": """<p><strong>Implied Volatility (IV)</strong> is the options market's forecast of how much a stock will move in the future — it is derived from option prices rather than historical price data. Normally, investors are more worried about downside than upside, so put options (bets on a falling price) carry higher IV than call options (bets on a rising price) for the same strike distance from the current price. This is called the "put skew" and is a persistent feature of most stock option markets.</p>
<p>During speculative manias, this relationship reverses: demand for out-of-the-money calls (cheap lottery tickets on further rapid price increases) surges as retail participants and momentum funds pile in. When call IV exceeds put IV — the ratio exceeds 1.0 — the options market is pricing in more upside uncertainty than downside uncertainty. This is historically unusual and is associated with late-stage speculative excess.</p>
<p>The extreme threshold is a ratio of 1.2+: at this level, the market is charging 20% more for upside bets than for equivalent downside protection. This was observed in many meme stocks in 2021 and in AI-related stocks during various momentum peaks in 2023–2025.</p>""",
    },
    {
        "term_key": "spy-vs-rsp-breadth-spread",
        "section_id": "bubble-radar",
        "term_title": "SPY vs RSP Breadth Spread",
        "question": "What does a large positive spread between SPY and RSP (equal-weight) performance indicate?",
        "answer": "A small number of mega-cap stocks are doing all the heavy lifting while the average stock lags — narrow breadth",
        "distractors": [
            "The overall market is falling broadly across all 500 stocks",
            "RSP has been discontinued and no longer trades",
            "All 500 S&P constituents are rising at an identical rate",
        ],
        "explanation": """<p>The S&amp;P 500 is a <strong>cap-weighted</strong> index — larger companies have more influence on the index level. The top 10 stocks in the S&amp;P 500 can represent 35%+ of the index. This means Apple, Microsoft, NVIDIA, Meta, and a handful of others can drag the entire index higher even if most of the 500 stocks are falling or flat.</p>
<p><strong>RSP</strong> (the Invesco S&amp;P 500 Equal Weight ETF) solves this by giving every company in the S&amp;P 500 exactly equal weight (~0.2%), regardless of size. When RSP performance equals SPY performance, all 500 companies are rising together — broad, healthy participation. When SPY significantly outperforms RSP (a large positive spread), a small number of mega-cap stocks are doing all the heavy lifting while the average stock is left behind.</p>
<p>This "narrow breadth" phenomenon has historically been a late-cycle warning signal. The 2023 AI rally is a good example: the S&amp;P 500 rose strongly, but the equal-weight index was nearly flat, because the gains were almost entirely concentrated in the "Magnificent 7" AI-related mega-caps. When the mega-caps eventually corrected in late 2024, the broad market impact was significant because so much index weight was concentrated in just a few names.</p>""",
    },
    {
        "term_key": "bubble-watch-vs-bubble-risk",
        "section_id": "bubble-radar",
        "term_title": "Bubble Watch vs Bubble Risk",
        "question": "What distinguishes 'Bubble Risk' (red flag) from 'Bubble Watch' (yellow flag)?",
        "answer": "Bubble Risk means the composite score has crossed the upper threshold with multiple metrics simultaneously in extreme territory",
        "distractors": [
            "Bubble Risk only applies to non-US stocks",
            "Bubble Watch is a lagging indicator computed after Bubble Risk",
            "Bubble Risk requires manual confirmation from an analyst",
        ],
        "explanation": """<p><strong>Bubble Watch</strong> (yellow flag) means the stock is showing early signs of valuation overextension across some of the seven metrics, but has not yet crossed all the highest-confidence thresholds. Think of it as "elevated, worth monitoring, but not yet alarming."</p>
<p><strong>Bubble Risk</strong> (red flag) means the composite score has crossed the upper threshold and multiple independent metrics are simultaneously in extreme territory. This is the "the lights are flashing" state — not a sell signal, but a strong prompt to ask whether you have an exit strategy and whether your current position size reflects the elevated risk.</p>
<p>Both thresholds are configurable in Settings because investors have different risk tolerances and time horizons. A long-term, diversified investor might set higher thresholds than a more active trader who is more sensitive to short-term valuation extremes.</p>""",
    },
    # --- pairs-spread-monitor ---
    {
        "term_key": "what-pairs-spread-monitor-detects",
        "section_id": "pairs-spread-monitor",
        "term_title": "What Pairs Spread Monitor Detects",
        "question": "How does Pairs Spread Monitor differ from every other alert engine in this app?",
        "answer": "It looks at the relationship between two tickers, not just one ticker in isolation",
        "distractors": [
            "It only works on cryptocurrency pairs",
            "It requires a live options feed to function",
            "It replaces Trap Monitor and Crash/Moonshot rather than complementing them",
        ],
        "explanation": """<p>Every other alert engine in this app — Trap Monitor, Bubble Radar, AI Contagion, Crash/Moonshot — looks at one ticker at a time. <strong>Pairs Spread Monitor</strong> instead looks at the <em>relationship</em> between two tickers that have historically moved together, and flags when that relationship has temporarily broken down.</p>
<p>Two stocks that are usually highly correlated (e.g. two companies in the same sector) can drift apart for a session or two — one rallies on company-specific news while the other lags — before typically converging back toward their historical relationship. This is the classic <strong>pairs trading / statistical arbitrage</strong> setup: a mean-reversion signal that is structurally different from any single-ticker technical or fundamental read.</p>""",
    },
    {
        "term_key": "correlation-threshold-same-currency-pairing",
        "section_id": "pairs-spread-monitor",
        "term_title": "Correlation Threshold & Same-Currency Pairing",
        "question": "Why does Pairs Spread Monitor only pair tickers quoted in the same currency?",
        "answer": "A cross-currency pair's price ratio would also be pulled around by the FX rate, mixing FX noise into the equity-relationship signal",
        "distractors": [
            "Yahoo Finance does not provide cross-currency price data",
            "Different currencies cannot be correlated by definition",
            "It is a regulatory requirement in the UK",
        ],
        "explanation": """<p>Before two tickers are even considered a "pair," their trailing 252-day daily-return <strong>Pearson correlation</strong> must clear a configurable threshold (default 0.7). This filters the scanned ticker universe down to only the pairs that have a genuine, statistically meaningful historical relationship — pairing two unrelated stocks would produce a spread with no real reason to revert.</p>
<p>Pairs are also restricted to <strong>tickers quoted in the same currency</strong> (GBX pence and GBP pounds are treated as the same currency, since both describe UK-listed stocks). A US stock and a UK stock might be genuinely correlated, but their price ratio would also be pulled around by the GBP/USD exchange rate — mixing a real equity-relationship signal with FX noise. Restricting to same-currency pairs keeps the signal clean.</p>""",
    },
    {
        "term_key": "log-spread-z-score",
        "section_id": "pairs-spread-monitor",
        "term_title": "Log-Spread Z-Score",
        "question": "Why does Pairs Spread Monitor use log(price_a) − log(price_b) rather than a raw price difference?",
        "answer": "It's unit-invariant — the measure doesn't depend on whether a ticker is quoted in pence or pounds, or trades at a high or low absolute price",
        "distractors": [
            "It is required for the correlation calculation to work at all",
            "It converts both prices into a common currency automatically",
            "It removes the need for a trailing lookback window",
        ],
        "explanation": """<p>For each correlated pair, the monitor computes the <strong>log-spread</strong> — <code>log(price_a) − log(price_b)</code> — every trading day over the same trailing 252-day window. Using a log ratio rather than a raw price difference means the measure is unit-invariant: it doesn't matter whether either ticker happens to be quoted in pence or pounds, or trades at £5 or £500, only how the two prices move <em>relative</em> to each other.</p>
<p>The <strong>z-score</strong> is how many standard deviations today's log-spread sits from that window's own mean. A z-score of 0 means the pair is trading exactly at its historical relationship; a large positive or negative z-score means one leg has become unusually "rich" or "cheap" relative to the other. An alert fires when the absolute z-score crosses a configurable threshold (default 2.0).</p>""",
    },
    {
        "term_key": "pairs-spread-scope",
        "section_id": "pairs-spread-monitor",
        "term_title": "Scope: Portfolio + Watchlist vs Universe",
        "question": "Why does the Universe scope never trigger a notification, even when a pair crosses the z-score threshold?",
        "answer": "It's a manually-triggered, non-repeating scan, and the alert dedup/cooldown model assumes a recurring scan cadence",
        "distractors": [
            "Universe scope pairs are never statistically significant enough to alert on",
            "Notifications are technically impossible for tickers not in stock_signals",
            "Universe scope only runs once, at first server startup",
        ],
        "explanation": """<p>By default the monitor scans your <strong>Portfolio + Watchlist</strong> — a small, fast scan that runs automatically on a schedule and fires alerts. A <strong>Universe</strong> scope is also available, scanning the full market universe (thousands of tickers) for correlated pairs you don't currently hold or watch.</p>
<p>Universe is <strong>on-demand only</strong> — triggered manually from the report page, never run automatically. A full-universe correlation scan is expensive (many more tickers means many more candidate pairs to check), and firing alerts off a scan the operator didn't ask for and may not repeat again soon would defeat the point of the alert dedup/cooldown model, which assumes a recurring scan cadence. Universe results are shown on the page but never trigger a notification.</p>""",
    },
    # --- forensic-screener ---
    {
        "term_key": "why-forensic-accounting-matters",
        "section_id": "forensic-screener",
        "term_title": "Why Forensic Accounting Matters",
        "question": "What is the core problem the Forensic Screener addresses?",
        "answer": "Some companies exploit the gap between reported numbers and economic reality through aggressive accounting or manipulated accruals",
        "distractors": [
            "Most companies deliberately underreport their true profits",
            "Financial statements are only published once every five years",
            "Price charts already fully capture a company's true financial health",
        ],
        "explanation": """<p>Price charts and momentum signals tell you what the market thinks of a company. Financial statements tell you what the company actually is. The problem: many investors never look at the financial statements, and some companies know this — exploiting the gap between reported numbers and economic reality through creative accounting, aggressive revenue recognition, and manipulated accruals.</p>
<p>The <strong>Forensic Screener</strong> runs three academic models — the Piotroski F-Score, Altman Z-Score, and Beneish M-Score — on annual financial statements for every portfolio and watchlist holding. These models were designed by academics specifically to detect warning signs that financial statements might be deteriorating, distressed, or manipulated. They do not rely on a single ratio but on specific combinations of ratios that historically differentiated healthy companies from distressed or manipulative ones.</p>
<p>The screener runs monthly (annual data changes slowly) and fires Nextcloud alerts when holdings breach distress thresholds. Think of it as a quarterly audit of each holding's financial health.</p>""",
    },
    {
        "term_key": "piotroski-f-score",
        "section_id": "forensic-screener",
        "term_title": "Piotroski F-Score",
        "question": "What does a Piotroski F-Score below 4 suggest?",
        "answer": "Structural decay — the company is deteriorating across multiple financial dimensions simultaneously",
        "distractors": [
            "The company is a guaranteed short-selling opportunity",
            "The company has too much cash on its balance sheet",
            "The company qualifies as a Quality Compounder",
        ],
        "explanation": """<p>Joseph Piotroski's 2000 paper "Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers" introduced a simple but powerful framework: score a company's financial health across 9 specific criteria, one point each, for a maximum score of 9.</p>
<p>The criteria cover three dimensions of financial health:</p>
<p><strong>Profitability (4 points):</strong> Is the company profitable (positive ROA)? Is it generating cash from operations (positive operating cash flow)? Is profitability improving year-over-year (rising ROA)? And critically — are its profits real or paper (is operating cash flow greater than net income, a test for accruals quality)? A company that reports large profits but has lower operating cash flow is booking income it hasn't actually received in cash — a classic accounting red flag.</p>
<p><strong>Leverage &amp; Liquidity (3 points):</strong> Is the company reducing its long-term debt ratio? Is its current ratio (current assets / current liabilities) improving — meaning it can better cover short-term obligations? Has it <em>not</em> issued new shares recently? Issuing new shares dilutes existing shareholders and often signals the company needs cash it can't generate internally.</p>
<p><strong>Operating Efficiency (2 points):</strong> Is gross margin improving (selling products at better prices or lower costs)? Is asset turnover improving (generating more revenue per pound of assets)?</p>""",
    },
    {
        "term_key": "altman-z-score",
        "section_id": "forensic-screener",
        "term_title": "Altman Z-Score",
        "question": "What does an Altman Z-Score below 1.1 suggest?",
        "answer": "The company's financials look statistically similar to companies that filed for bankruptcy within 2 years",
        "distractors": [
            "The company is definitely going to go bankrupt within 2 years",
            "The company has an unusually high dividend yield",
            "The company's Piotroski F-Score is automatically also low",
        ],
        "explanation": """<p>Edward Altman's 1968 model uses financial ratios to predict the probability of corporate bankruptcy within 2 years. While it was originally designed for manufacturing companies, the Z' variant used here was developed for non-manufacturers (service companies, retailers, financial firms) and is more broadly applicable.</p>
<p>The four inputs measure different facets of financial resilience:</p>
<p>Formula: Z' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4</p>""",
    },
    {
        "term_key": "beneish-m-score",
        "section_id": "forensic-screener",
        "term_title": "Beneish M-Score",
        "question": "What does the Beneish M-Score attempt to detect?",
        "answer": "Statistical signatures of earnings manipulation in reported financial statements",
        "distractors": [
            "The probability of bankruptcy within 2 years",
            "Overall financial health across profitability, leverage, and efficiency",
            "The company's dividend sustainability",
        ],
        "explanation": """<p>Messod Beneish's 1999 model was designed to detect statistical signatures of earnings manipulation in reported financial statements — "M" stands for Manipulation. Unlike the Piotroski and Altman scores which assess financial health, the Beneish score asks a more uncomfortable question: are these numbers real?</p>
<p>It uses eight indices, each measuring a specific ratio that has been found to deviate systematically from normal when companies are manipulating their reported earnings:</p>
<p>The model requires two consecutive annual reporting periods and therefore cannot be applied to newly listed companies or ETFs. The detection rate on confirmed manipulators is approximately 76% — not perfect, but powerful as a screening tool.</p>""",
    },
    # --- fx-drag ---
    {
        "term_key": "currency-problem-uk-investors",
        "section_id": "fx-drag",
        "term_title": "The Currency Problem for UK Investors",
        "question": "What two separate factors determine a UK investor's GBP return on a US stock?",
        "answer": "How the stock performed in USD, and how the GBP/USD exchange rate moved over the same period",
        "distractors": [
            "The stock's dividend yield and its P/E ratio",
            "The size of the position and the broker's commission",
            "The stock's beta and its market capitalisation",
        ],
        "explanation": """<p>When a UK investor buys shares in a US company (say, Apple), two things happen: they exchange pounds for dollars to make the purchase, and they now own an asset denominated in US dollars. The return they experience in GBP depends on <em>two separate factors</em>: how the stock performed in USD, and how the GBP/USD exchange rate moved during the holding period.</p>
<p>These two effects can reinforce or cancel each other out. If Apple rises 15% in USD but the pound strengthened 10% against the dollar over the same period, your GBP return is only about 5% — not 15%. Conversely, if the pound weakened 10%, your GBP return would be approximately 25%, even if you had no view on currencies and weren't actively seeking FX exposure.</p>
<p>Most portfolio reporting tools just show the combined GBP return without decomposing it. The <strong>FX Drag Analyzer</strong> splits every US stock position's return into its two components — equity performance in USD and FX effect — so you can understand where your returns actually came from.</p>""",
    },
    {
        "term_key": "fx-drag-term",
        "section_id": "fx-drag",
        "term_title": "FX Drag",
        "question": "When does a UK investor experience negative FX drag (a headwind)?",
        "answer": "When GBP strengthens against USD, making the dollar-denominated asset worth fewer pounds when converted back",
        "distractors": [
            "When GBP weakens against USD",
            "When the stock itself falls in USD terms",
            "When the company pays a dividend in USD",
        ],
        "explanation": """<p>The <strong>FX Drag</strong> (or FX Effect) is the portion of your GBP return that came from exchange rate movement rather than the underlying stock's performance. It can be positive (a tailwind) or negative (a headwind).</p>
<p><strong>Negative FX drag (headwind):</strong> GBP strengthened against USD. Your dollar-denominated asset is now worth fewer pounds when converted back. Example: if the pound moved from $1.20 to $1.30 per pound (+8.3%), a US stock that was flat in USD terms would show a loss of about −8.3% in GBP terms, purely from the exchange rate.</p>
<p><strong>Positive FX drag (tailwind):</strong> GBP weakened against USD. Your dollar-denominated asset is now worth more pounds. The Brexit periods of 2016 and 2022–2023 saw the pound weaken significantly — UK investors holding US stocks received a meaningful FX tailwind that boosted their GBP returns above the stock's actual USD performance.</p>
<p>Understanding FX drag matters for several reasons: it tells you how much of your portfolio return was actually within your control (the equity performance) versus simply being exposed to currency movements you neither sought nor hedged against.</p>""",
    },
    {
        "term_key": "equity-return-usd",
        "section_id": "fx-drag",
        "term_title": "Equity Return (USD)",
        "question": "What does 'Equity Return (USD)' isolate?",
        "answer": "How the company itself performed, independent of what currencies were doing",
        "distractors": [
            "The combined GBP return including currency effects",
            "The dividend yield converted into GBP",
            "The stock's beta relative to the S&P 500",
        ],
        "explanation": """<p>The stock's pure performance in its native currency — US dollars. This is the return you would have seen if you were a US investor with no currency conversion required. It isolates the question: "How did the company itself perform, independent of what currencies were doing?"</p>
<p>Calculated as: <code>(current_price_USD / reference_price_USD − 1) × 100</code>, where the reference price is the stock's closing price on the first available trading day on or after the selected start date (January 1st for YTD calculations).</p>
<p>Comparing this to your Total GBP Return shows you how much the currency added or subtracted from your actual experience as a GBP-based investor.</p>""",
    },
    {
        "term_key": "total-gbp-return",
        "section_id": "fx-drag",
        "term_title": "Total GBP Return",
        "question": "Why is Total GBP Return calculated multiplicatively rather than by simple addition?",
        "answer": "Both the equity return and FX effect compound simultaneously, and the combined effect differs slightly from their sum",
        "distractors": [
            "Because GBP returns must always be rounded to the nearest whole percent",
            "Multiplication is required by UK tax reporting rules",
            "Addition would always overstate the return",
        ],
        "explanation": """<p>The combined GBP return you actually realised — accounting for both the stock's USD performance and the GBP/USD exchange rate movement over the same period.</p>
<p>This is calculated <em>multiplicatively</em>, not by simple addition: <code>(1 + equity_return_USD) × (1 + fx_effect) − 1</code>. The reason for multiplication rather than addition: both effects compound simultaneously, and the combined effect is slightly different from their sum. For example, a +10% equity return and a +5% FX tailwind produces a total return of (1.10 × 1.05) − 1 = 15.5%, not 10 + 5 = 15%.</p>
<p>The small difference (0.5% in this example) is the "cross term" — the interaction between the two effects. It is small at moderate return levels but can become meaningful when both components are large (e.g., a 30% equity return and a 20% FX tailwind).</p>""",
    },
    {
        "term_key": "lifetime-mode",
        "section_id": "fx-drag",
        "term_title": "Lifetime Mode",
        "question": "What exchange rate does Lifetime Mode use for FX Drag calculations?",
        "answer": "The actual exchange rate at which each position was originally purchased, derived from account transaction history",
        "distractors": [
            "The exchange rate on the day the app was installed",
            "A single fixed rate configured in Settings",
            "The average exchange rate over the past 10 years",
        ],
        "explanation": """<p>The standard FX Drag modes (YTD, 1-Year, 2-Year) compare your position against a shared start date. <strong>Lifetime Mode</strong> is more sophisticated: it uses the actual exchange rate at which you originally purchased each position, derived from your account transaction history.</p>
<p>Every Buy transaction records the price in the asset's native currency (USD) alongside its own exchange rate to your base currency (GBP). From these two values, the app can reverse-engineer the implied GBP/USD exchange rate at the time of purchase — without needing a separate historical FX database. If you bought Apple at $180 with a stored exchange rate of 0.8, the implied buy-rate was £1 = $1.25.</p>
<p>This means Lifetime Mode shows you the true FX effect on your actual investment — not a hypothetical position opened at some arbitrary date, but your real cost basis. For long-held positions spanning multiple purchases at different exchange rates, the buy-rate is computed as a weighted average across all Buy transactions, across every built-in account.</p>""",
    },
    # --- performance-analytics ---
    {
        "term_key": "what-portfolio-tearsheet-does",
        "section_id": "performance-analytics",
        "term_title": "What the Portfolio Tearsheet Does",
        "question": "How does the Portfolio Tearsheet relate to the X-ray panel's risk metrics?",
        "answer": "It deliberately covers metrics X-ray doesn't show (Sortino, Omega, drawdown duration) rather than duplicating Sharpe/VaR/CVaR",
        "distractors": [
            "It replaces X-ray entirely with a more advanced version",
            "It requires a separate external quantstats installation to function",
            "It only works for portfolios with fewer than 5 holdings",
        ],
        "explanation": """<p>The <strong>Portfolio Tearsheet</strong> is a native performance-analytics report, covering the same ground as the <strong>quantstats</strong> Python library's metric set — risk-adjusted return ratios, drawdown duration analysis, and win/loss statistics — computed entirely from the app's own cached daily return history, with no external dependency and no static HTML report generator involved.</p>
<p>It deliberately doesn't duplicate what the <a href="/portfolio">Portfolio</a> page's X-ray panel already shows (Sharpe ratio, historical VaR/CVaR, skewness/kurtosis, beta, volatility) — the Tearsheet's cards and charts are all metrics X-ray doesn't cover: Sortino and Omega ratios, drawdown duration and time underwater, the Ulcer Index, tail ratio, and win/loss consistency. Both draw on the same underlying per-ticker cached return series, so a scope's numbers stay consistent between the two views.</p>
<p>Every metric requires at least 30 overlapping cached trading days across the selected scope's holdings — below that, cards show as unavailable with an explanatory warning rather than a misleading partial figure.</p>""",
    },
    {
        "term_key": "sortino-calmar-omega-profit-factor",
        "section_id": "performance-analytics",
        "term_title": "Sortino, Calmar, Omega & Profit Factor",
        "question": "How does the Sortino Ratio differ from the Sharpe Ratio?",
        "answer": "Sortino only penalises downside volatility, so big up-days don't hurt the score",
        "distractors": [
            "Sortino ignores returns entirely and only measures volatility",
            "Sortino is calculated over a fixed 30-day window only",
            "Sortino requires a minimum portfolio size of £100,000",
        ],
        "explanation": """<p><strong>Sortino Ratio</strong> is a Sharpe-ratio variant that only penalises downside volatility — a portfolio with big up-days and small down-days scores well, even if its Sharpe ratio (which penalises all volatility equally) looks mediocre.</p>
<p><strong>Calmar Ratio</strong> divides annualised return by the maximum drawdown — a direct answer to "how much return did I earn per unit of my worst peak-to-trough loss?" Higher is better; a Calmar above 1 means annual return exceeded the worst drawdown.</p>
<p><strong>Omega Ratio</strong> compares the total size of gains above a minimum acceptable return threshold (the risk-free rate) to the total size of losses below it. A value above 1 means gains outweighed losses on a probability-weighted basis, using the entire return distribution rather than just its mean and variance.</p>
<p><strong>Profit Factor</strong> is the simplest of the four: the sum of every positive daily return divided by the absolute sum of every negative daily return. A value of 2.0 means winning days, in total, were twice the size of losing days.</p>""",
    },
    {
        "term_key": "drawdown-duration-ulcer-index",
        "section_id": "performance-analytics",
        "term_title": "Drawdown Duration & the Ulcer Index",
        "question": "What does the Ulcer Index capture that Max Drawdown alone does not?",
        "answer": "How long the portfolio spent underwater, not just how deep the worst decline was",
        "distractors": [
            "The exact date of the portfolio's next drawdown",
            "The correlation between two specific holdings",
            "The dividend yield of the portfolio's largest holding",
        ],
        "explanation": """<p>X-ray's Max Drawdown shows how deep the worst decline was. The Tearsheet adds <em>how long</em> it lasted: <strong>Longest Drawdown</strong> is the longest continuous stretch the portfolio spent below a previous high-water mark, and <strong>Time Underwater</strong> is how many days it's been since the most recent new high — both computed from the full dated drawdown curve, not just its lowest point.</p>
<p>The <strong>Ulcer Index</strong> combines depth and duration into a single number: the root-mean-square of the drawdown curve. Two portfolios can share the same max drawdown, but the one that spent months underwater — rather than snapping back in days — has a higher Ulcer Index, reflecting the greater psychological and practical cost of the decline.</p>""",
    },
    {
        "term_key": "tail-ratio-win-loss-stats",
        "section_id": "performance-analytics",
        "term_title": "Tail Ratio & Win/Loss Stats",
        "question": "What does a Tail Ratio above 1 indicate?",
        "answer": "The portfolio's best days have historically outsized its worst days",
        "distractors": [
            "The portfolio has never had a losing day",
            "The portfolio's win rate is above 90%",
            "The portfolio holds only bonds, no equities",
        ],
        "explanation": """<p><strong>Tail Ratio</strong> compares the size of the best days to the worst: the 95th-percentile daily return divided by the absolute 5th-percentile daily return. A ratio above 1 means the portfolio's best days have historically outsized its worst days.</p>
<p><strong>Win Rate</strong>, <strong>Average Win</strong>/<strong>Average Loss</strong>, and the <strong>Win/Loss (Payoff) Ratio</strong> (average win ÷ absolute average loss) characterise consistency day-to-day, separately from the ratios above that describe risk-adjusted return over the whole period. <strong>Max Consecutive Wins/Losses</strong> shows the longest streaks in either direction — useful context for how "lumpy" the return pattern has been.</p>""",
    },
    {
        "term_key": "the-charts-tearsheet",
        "section_id": "performance-analytics",
        "term_title": "The Charts",
        "question": "What does the Monthly Returns Heatmap in the Portfolio Tearsheet show?",
        "answer": "Every calendar month's compounded return laid out in a year × month grid, useful for spotting seasonal patterns",
        "distractors": [
            "A single number summarising the entire portfolio's lifetime return",
            "The correlation matrix between all portfolio holdings",
            "Only the most recent month's daily returns",
        ],
        "explanation": """<p>The <strong>Underwater Chart</strong> plots the drawdown curve directly — every dip below the water line and how long it took to recover. The <strong>Cumulative Growth</strong> chart indexes the portfolio and its benchmark to a common starting value of 100, making relative performance easy to read regardless of the portfolio's actual size. The <strong>Monthly Returns Heatmap</strong> lays out every calendar month's compounded return in a year × month grid — the tearsheet's signature view for spotting seasonal patterns or a handful of outsized months driving overall performance. A <strong>Daily Return Distribution</strong> histogram, with the mean and 5th-percentile (VaR 95%) marked, rounds out the picture of what a "typical" day actually looked like.</p>""",
    },
    # --- portfolio-optimizer ---
    {
        "term_key": "what-portfolio-optimizer-does",
        "section_id": "portfolio-optimizer",
        "term_title": "What the Portfolio Optimizer Does",
        "question": "What does the Portfolio Optimizer actually do with its suggested weights?",
        "answer": "Shows them for comparison only — this app has no order execution, so nothing rebalances automatically",
        "distractors": [
            "Automatically places buy/sell orders to match the suggested weights",
            "Deletes any holding with a suggested weight below 5%",
            "Requires a brokerage API key to compute the suggestions",
        ],
        "explanation": """<p>In plain terms: it looks at how your tickers have actually moved in the past and works out two alternative ways you could have mixed them — one aimed at the smoothest ride, one aimed at the best return for the bumps involved. It then shows those next to what you actually hold today, so you can see how your real mix compares.</p>
<p>More precisely, the <strong>Portfolio Optimizer</strong> computes two textbook "optimal" allocations for a chosen account scope — <strong>Min-Variance</strong> (the Steadiest Mix) and <strong>Max-Sharpe</strong> (the Best Reward-for-Risk Mix) — using closed-form matrix algebra rather than a numerical optimization library.</p>
<p>It's informational only: this app has no order execution, so nothing is rebalanced automatically. Held tickers are pre-selected candidates; <a href="/watchlist">Watchlist</a> tickers can be opted in to see them suggested as a brand-new position with a nonzero weight.</p>""",
    },
    {
        "term_key": "min-variance-portfolio",
        "section_id": "portfolio-optimizer",
        "term_title": "Min-Variance Portfolio (\"Steadiest Mix\")",
        "question": "How is the Min-Variance (Steadiest Mix) Portfolio's weight vector computed?",
        "answer": "In closed form from the covariance matrix (w ∝ Σ⁻¹ · 1), with no iterative solver",
        "distractors": [
            "By running 1,000 random simulations and picking the smoothest one",
            "By always weighting every candidate ticker equally",
            "By maximizing expected return regardless of risk",
        ],
        "explanation": """<p>The mix of your selected tickers that would have bounced around the <strong>least</strong> historically — the smoothest ride, not necessarily the highest return.</p>
<p>This is a closed-form solution — no iterative solver, no shorting or position-size constraints. It answers "if I only cared about the smoothest possible ride, how would I have weighted these holdings?"</p>""",
    },
    {
        "term_key": "max-sharpe-portfolio",
        "section_id": "portfolio-optimizer",
        "term_title": "Max-Sharpe Portfolio (\"Best Reward-for-Risk Mix\")",
        "question": "What does the Max-Sharpe (Best Reward-for-Risk) Portfolio maximize?",
        "answer": "Historical return per unit of risk — the tangency point on the efficient frontier",
        "distractors": [
            "Total historical return, regardless of volatility",
            "The number of candidate tickers included",
            "Dividend income only",
        ],
        "explanation": """<p>The mix of your selected tickers that would have given the <strong>most return for the amount of bumpiness</strong> involved — the best trade-off between reward and risk, not simply the highest return on its own.</p>
<p>Like the Steadiest Mix, this is closed-form and unconstrained. If a candidate's expected return sits below the risk-free rate, or the spread of expected returns across candidates is too thin, the Optimizer surfaces a warning instead of a misleading number.</p>""",
    },
    {
        "term_key": "efficient-frontier",
        "section_id": "portfolio-optimizer",
        "term_title": "Efficient Frontier (the curve on the chart)",
        "question": "How does the Portfolio Optimizer trace the efficient frontier without a second optimization pass?",
        "answer": "Two-fund separation — every frontier point is a linear combination of the Min-Variance and Max-Sharpe portfolios",
        "distractors": [
            "It re-runs the Monte Carlo Wealth Simulator for each point",
            "It queries a third-party portfolio-optimization API",
            "It only plots the two named portfolios, not a curve",
        ],
        "explanation": """<p>Imagine plotting every possible way of mixing your selected tickers as a dot, with bumpiness on one axis and return on the other. The efficient frontier is the curve connecting the <em>best</em> of those dots — the mixes where you can't get more return without accepting more bumpiness, or less bumpiness without giving up return. Your own portfolio's dot is also shown, so you can see how far it sits from that curve.</p>
<p>The Optimizer traces the curve using <strong>two-fund separation</strong>: every point on the frontier is a linear combination of the Steadiest Mix and the Best Reward-for-Risk Mix, so sweeping a blend factor between (and slightly beyond) the two traces the whole curve without a second optimization pass.</p>""",
    },
    {
        "term_key": "negative-short-weights",
        "section_id": "portfolio-optimizer",
        "term_title": "Negative (Short) Weights — the \"Model says: avoid\" tag",
        "question": "Why can the Portfolio Optimizer show a negative suggested weight?",
        "answer": "The math is unconstrained (no shorting/position-cap rule), so a negative weight is the true closed-form result and is shown as-is",
        "distractors": [
            "It's a display bug and should be reported",
            "It means the ticker will be automatically sold short",
            "Negative weights are always clipped to zero before display",
        ],
        "explanation": """<p>Occasionally the maths comes back wanting a <em>negative</em> amount of a ticker — in plain terms, betting against it rather than owning it. Because the Optimizer is unconstrained — no "no shorting" rule, no per-position cap — this can happen: it reflects the true closed-form math, not a recommendation to actually short a position, and <strong>this app has no order execution and cannot act on it</strong>. Negative weights are always shown as-is, flagged with a badge, and never silently clipped to zero, since clipping would misrepresent the actual result.</p>""",
    },
    # --- stress-tester ---
    {
        "term_key": "what-stress-tester-does",
        "section_id": "stress-tester",
        "term_title": "What the Stress Tester Does",
        "question": "What is the purpose of the Historical Stress Tester?",
        "answer": "To make investors viscerally aware of the downside before it happens, using concrete monetary loss figures",
        "distractors": [
            "To predict the exact date of the next market crash",
            "To automatically sell holdings that fail the stress test",
            "To calculate a portfolio's dividend income under each scenario",
        ],
        "explanation": """<p>The <strong>Historical Stress Tester</strong> answers a question most investors don't ask until it's too late: "If 2008 happened again tomorrow, how much would I lose in pounds?" It takes your current portfolio, applies the historical shock from each of four major market crises, and shows you an estimated monetary loss per holding and by sector.</p>
<p>The four scenarios are: the <strong>Global Financial Crisis 2008–2009</strong> (S&amp;P 500 fell ~56%), the <strong>Dot-com bust 2000–2002</strong> (NASDAQ fell ~78%, S&amp;P fell ~49%), the <strong>COVID-19 crash March 2020</strong> (S&amp;P fell ~34% in 33 days), and the <strong>2022 inflation shock</strong> (S&amp;P fell ~25% as the Fed raised rates at the fastest pace in 40 years).</p>
<p>The purpose is not to predict when a crash will happen — it is to make you viscerally aware of the downside before it happens. Seeing that your portfolio would have lost £47,000 in a 2008-style scenario is different from knowing your "average beta is 1.2." Concrete monetary figures change how investors think about risk.</p>""",
    },
    {
        "term_key": "beta-adjusted-scenario-shock",
        "section_id": "stress-tester",
        "term_title": "Beta-Adjusted Scenario Shock",
        "question": "How is a holding's estimated drop calculated in the Stress Tester?",
        "answer": "Market Crash % × Beta × Sector Multiplier",
        "distractors": [
            "Market Crash % divided by the holding's dividend yield",
            "A fixed 50% drop applied equally to every holding",
            "The holding's own historical maximum drawdown",
        ],
        "explanation": """<p><strong>Beta</strong> is the core concept here. Beta measures how much a specific stock amplifies (or dampens) the broad market's moves. A stock with β=1.0 historically moves exactly in line with the market. A stock with β=1.5 historically falls 50% more than the market on bad days — and rises 50% more on good days. A stock with β=0.5 is half as volatile as the market. Gold or utility stocks can have negative beta (they rise when equities fall, acting as hedges).</p>
<p>The stress tester applies each stock's beta to the historical scenario shock: if the S&amp;P fell 56% in the GFC and your stock has β=1.2, the estimated drop is 56% × 1.2 = 67.2%. This is then multiplied by the sector multiplier to adjust for how your specific sector performed relative to the broad market during that specific crisis.</p>""",
    },
    {
        "term_key": "sector-multiplier",
        "section_id": "stress-tester",
        "term_title": "Sector Multiplier",
        "question": "What does a Sector Multiplier below 1.0 mean for a scenario?",
        "answer": "That sector held up better than the broad index during that historical crisis",
        "distractors": [
            "The sector is guaranteed to rise during any future crash",
            "The sector has no equity holdings, only bonds",
            "The multiplier only applies to non-US sectors",
        ],
        "explanation": """<p>Not all sectors fall equally in a crash. During the Dot-com bust, Technology stocks (the epicentre of the bubble) fell roughly 80% while consumer staples (food, household goods) fell 20% or held steady — people still buy toothpaste in a recession. During the 2022 inflation shock, Energy stocks actually <em>rose</em> significantly (high oil prices were the source of inflation) while growth/tech stocks fell 40–70%.</p>
<p>The <strong>Sector Multiplier</strong> captures these differences: a multiplier above 1.0 means the sector was hit harder than the broad index; below 1.0 means it held up better; a negative multiplier means it actually gained during the crisis (e.g., Energy in 2022 has a negative multiplier for that scenario, meaning your energy holdings would appear as a partial hedge).</p>
<p>Examples from the Dot-com crash: Technology ×2.2 (fell far more than the index), Consumer Defensive ×0.4 (held up much better), Energy ×0.5 (modest decline). These multipliers are fixed per scenario and sector, derived from actual sector ETF performance during those periods.</p>""",
    },
    {
        "term_key": "why-not-historical-replay",
        "section_id": "stress-tester",
        "term_title": "Why Not Historical Replay?",
        "question": "Why can't the Stress Tester simply look up each stock's actual historical return during a past crisis?",
        "answer": "The app only holds 2 years of price history, and many holdings didn't exist or traded differently during those historical crises",
        "distractors": [
            "Historical replay is computationally impossible on modern hardware",
            "Regulators prohibit displaying real historical crash data",
            "Beta and sector data are never available for individual stocks",
        ],
        "explanation": """<p>A natural question: why not just look up what each stock actually returned during the 2008 crisis, rather than using a formula?</p>
<p>Two reasons make that impossible for most portfolios: First, the app only holds 2 years of daily price history per ticker — the GFC, Dot-com, COVID, and 2022 shock are all outside that window for different reasons. Second, many holdings in a modern portfolio — newer ETFs, recently-listed stocks, companies that merged or were acquired — simply did not exist or traded very differently during those historical crises. There is no "actual 2008 price" for a stock that IPO'd in 2021.</p>
<p>The beta-shock parametric model works for <em>any</em> holding regardless of listing date, is transparent about its assumptions, and can be applied consistently across the whole portfolio. Its limitation is that it assumes beta and sector relationships are stable across market regimes — a stock with β=1.5 in calm markets may behave very differently in a genuine crisis when correlations spike and liquidity dries up. Treat the stress test results as directionally informative order-of-magnitude estimates, not precise predictions.</p>""",
    },
    # --- etf-predictor ---
    {
        "term_key": "etf-price-predictor-what-it-solves",
        "section_id": "etf-predictor",
        "term_title": "ETF Price Predictor — What It Solves",
        "question": "What problem does the ETF Price Predictor solve for LSE-listed ETFs holding US stocks?",
        "answer": "The LSE closes hours before US markets, so the ETF's 'last price' can be stale relative to after-hours moves in its US holdings",
        "distractors": [
            "LSE-listed ETFs cannot legally hold any US constituents",
            "ETFs never update their holdings more than once a year",
            "US markets and the LSE always close at exactly the same time",
        ],
        "explanation": """<p>Many ETFs listed on the London Stock Exchange hold US stocks as their primary constituents. The problem: the LSE closes at 16:30 BST, but US markets continue trading until 21:00 BST (16:00 ET). If a major US technology ETF holds Apple, Microsoft, and NVIDIA, and all three release earnings after the LSE closes, the ETF's "last price" reflects the state of those stocks 4.5 hours ago — not their current after-hours value.</p>
<p>The ETF Price Predictor solves this by computing what the ETF's opening price is likely to be the next morning, based on what its constituent holdings did between the LSE close and the current moment. It lets you make more informed decisions about limit orders, whether to buy before or after the open, or whether a pre-market price gap represents opportunity or risk.</p>
<p>The tool is configurable: you define the ETF ticker and up to 20 constituent tickers with their respective weights. The system normalises weights to 100% and runs two prediction engines in sequence.</p>""",
    },
    {
        "term_key": "holdings-engine-primary",
        "section_id": "etf-predictor",
        "term_title": "Holdings Engine (Primary)",
        "question": "How does the Holdings Engine compute a predicted next-day ETF open?",
        "answer": "It weights each constituent's after-hours return by its portfolio weight, applies FX adjustment, then applies that to the last close",
        "distractors": [
            "It always assumes the ETF opens flat regardless of constituent moves",
            "It uses only the single largest constituent's return",
            "It requires at least 15 years of historical ETF data",
        ],
        "explanation": """<p>The <strong>Holdings Engine</strong> is the main prediction method. For each constituent in the configured basket, it measures the price change since the ETF's home-exchange close — the "after-hours signal" for that stock. It then weights each constituent's return by its portfolio weight, sums the weighted returns, and applies any currency adjustment needed (for a GBP-denominated ETF holding USD-denominated stocks, it adjusts for the GBP/USD exchange rate move over the same period).</p>
<p>The result is a single number: the expected basket return since the ETF's last close. That return is then applied to the ETF's last known price to produce a predicted next-day open.</p>
<p>This engine requires at least 3 constituents to have usable data. If fewer are available (market holiday in the US, data provider issues), it falls back to the Regression Engine.</p>""",
    },
    {
        "term_key": "regression-engine-fallback",
        "section_id": "etf-predictor",
        "term_title": "Regression Engine (Fallback)",
        "question": "When does the Regression Engine take over from the Holdings Engine?",
        "answer": "When fewer than 3 constituents have usable data",
        "distractors": [
            "Every single trading day, regardless of data availability",
            "Only when the ETF has more than 20 constituents",
            "When the ETF's ticker contains a currency suffix",
        ],
        "explanation": """<p>When the Holdings Engine cannot run due to insufficient constituent data, the <strong>Regression Engine</strong> takes over. It uses 60+ days of historical data to fit an <strong>OLS (Ordinary Least Squares) regression</strong> model: ETF next-morning return = α + β × average constituent daily return.</p>
<p><strong>OLS regression</strong> is the most fundamental statistical modelling technique: it finds the straight line through historical data that minimises the total squared error. The <strong>beta (β)</strong> here measures how sensitive the ETF's opening move historically has been to a 1% move in its constituent basket. The <strong>alpha (α)</strong> is the baseline drift that isn't explained by constituent moves.</p>
<p>The output includes: the predicted price, the regression beta and R² (a measure of how well the model fits — how much of the ETF's historical variance is explained by constituent moves), and a 95% confidence interval derived from the residuals of the regression. The wider this interval, the less precise the prediction.</p>""",
    },
    {
        "term_key": "bias-corrected-confidence-weighted-blend",
        "section_id": "etf-predictor",
        "term_title": "Bias-Corrected & Confidence-Weighted Blend Predictions",
        "question": "What does the Confidence-Weighted Blend prediction do?",
        "answer": "Combines the Holdings and Regression engines, weighting each by the inverse of its own trailing mean absolute error",
        "distractors": [
            "Always uses the Holdings Engine's result unchanged",
            "Replaces the standard prediction used for Portfolio Impact P&L",
            "Requires manual selection of which engine to trust each day",
        ],
        "explanation": """<p>Two further prices are computed and logged alongside the standard prediction, purely to track which approach ends up closer to reality over time. Neither replaces the standard prediction — Portfolio Impact P&amp;L and every other calculation still use it.</p>
<p>The <strong>Bias-Corrected</strong> price takes the standard prediction and shifts it by the trailing mean signed error (actual minus predicted) over the basket's last 10 resolved predictions of the same type. If this basket has recently under-predicted the move by an average of 1%, the correction adds that 1% back.</p>
<p>The <strong>Confidence-Weighted Blend</strong> combines the Holdings and Regression engines, weighting each by the inverse of its own trailing mean absolute error — the engine that has been more accurate recently gets more say. Until 10 resolved predictions exist for a basket, both variants fall back to a simple average (blend) or don't display at all (bias-correction), since a handful of data points isn't enough to calibrate confidently.</p>""",
    },
    {
        "term_key": "constituent-weights",
        "section_id": "etf-predictor",
        "term_title": "Constituent Weights",
        "question": "What happens if configured constituent weights sum to only 75%?",
        "answer": "The system normalises them to 100% by dividing each weight by 0.75",
        "distractors": [
            "The prediction fails and no output is produced",
            "The missing 25% is assumed to be held in cash",
            "The system rejects the configuration until it sums to exactly 100%",
        ],
        "explanation": """<p>The percentage allocation each ticker represents within the configured ETF prediction basket. Importantly, the weights you configure do not need to exactly match the ETF's actual underlying holdings — they are the weights you want to use for the prediction basket, which may be a simplified approximation of the full holdings.</p>
<p>If you configure Apple at 25%, Microsoft at 20%, NVIDIA at 15%, and others, but these sum to only 75%, the system normalises them to 100% (dividing each by 0.75) so that the weighted returns sum to a meaningful basket return. You always see the normalised weights used in the prediction.</p>
<p>Weights at the time of each prediction are saved as a snapshot with the prediction record. This means that if you later edit the basket configuration, historical accuracy calculations are not corrupted — each past prediction is evaluated against the weights that were actually in effect when it was made.</p>""",
    },
    # --- sovereign-debt-auction ---
    {
        "term_key": "sovereign-debt-auction-monitor",
        "section_id": "sovereign-debt-auction",
        "term_title": "Sovereign Debt Auction Monitor",
        "question": "Why do weak US Treasury auctions matter for stock investors?",
        "answer": "Weak demand raises the government's borrowing rate, which ripples into higher rates economy-wide and reduces the present value of future corporate profits",
        "distractors": [
            "Weak auctions automatically trigger a stock market circuit breaker",
            "Weak auctions only affect companies headquartered in the United States",
            "Treasury auctions have no connection to equity valuations",
        ],
        "explanation": """<p>The US government spends more than it collects in taxes — this difference (the <strong>fiscal deficit</strong>) must be financed by borrowing. To borrow at scale, the US Treasury issues bonds and sells them to investors through regular public auctions. If those auctions go well (many buyers, competitive bids, strong demand), the government can borrow cheaply. If they go badly, it must raise the interest rate it offers to attract enough buyers — and those higher rates ripple through the entire economy, pushing up mortgage rates, car loan rates, and corporate borrowing costs.</p>
<p>This is why Treasury auctions matter for stock investors: a weak auction raises the cost of borrowing economy-wide. Higher interest rates reduce the present value of future corporate profits (because you discount those future earnings at a higher rate), which directly reduces stock valuations — especially growth and technology stocks whose value depends heavily on earnings many years in the future.</p>
<p>The <strong>Sovereign Debt Auction Monitor</strong> watches US Treasury auction results twice daily on weekdays and fires an alert when demand looks materially weaker than recent history. The October 2023 30-year auction is a textbook example: a visible yield tail of ~3 basis points triggered an immediate equity selloff and a sharp rotation out of technology stocks that same afternoon.</p>""",
    },
    {
        "term_key": "us-treasury-auction-how-it-works",
        "section_id": "sovereign-debt-auction",
        "term_title": "US Treasury Auction — How It Works",
        "question": "What is the 'stop-out rate' in a Treasury auction?",
        "answer": "The highest yield accepted — the point at which the auction was fully subscribed",
        "distractors": [
            "The lowest yield rejected by the government",
            "A penalty rate charged to late bidders",
            "The average yield across all maturities auctioned that week",
        ],
        "explanation": """<p>A Treasury auction is a competitive bidding process. The government announces: "We will issue $39 billion of 10-year bonds." Institutional investors (banks, foreign central banks, pension funds) submit sealed bids specifying how many bonds they want and at what yield they require. The government ranks all bids from lowest yield (cheapest for the government) to highest yield (most expensive) and fills the auction starting from the lowest yield until all $39 billion is sold.</p>
<p>The <strong>stop-out rate</strong> (or clearing yield) is the highest yield accepted — the point at which the auction was fully subscribed. Everyone who bid at or below this yield gets filled at the same stop-out rate. This ensures a fair, single-price clearing mechanism.</p>
<p>Auctions happen across many different maturities throughout the week: 3-month and 6-month bills are auctioned weekly; 2-year, 5-year, and 7-year notes are auctioned monthly; 10-year notes and 30-year bonds are auctioned roughly monthly with re-openings. Each maturity has its own investor base and its own "normal" demand dynamics, which is why the monitor compares each auction to the rolling history of auctions of the <em>same</em> maturity.</p>""",
    },
    {
        "term_key": "bid-to-cover-ratio",
        "section_id": "sovereign-debt-auction",
        "term_title": "Bid-to-Cover Ratio",
        "question": "What does a bid-to-cover ratio of 2.5 mean?",
        "answer": "Investors submitted bids worth 2.5 times the value of bonds offered",
        "distractors": [
            "The auction was undersubscribed by 2.5 times",
            "The government had to raise its yield by 2.5 basis points",
            "2.5% of bidders were foreign central banks",
        ],
        "explanation": """<p>The <strong>bid-to-cover ratio</strong> is total demand divided by total supply: (total value of bids submitted) / (total value of bonds offered). A ratio of 2.5 means investors submitted $97.5 billion of bids for a $39 billion auction — demand was 2.5× supply. This is the primary headline number that traders watch in real time.</p>
<p>A <em>high</em> bid-to-cover (e.g., 2.8 for a 10-year auction) signals healthy competition for government bonds — many investors want to lend the government money at this yield. A <em>low</em> ratio (e.g., 2.2) means fewer bids came in relative to the supply, suggesting demand is weak and the government had to raise the yield to attract enough buyers.</p>
<p>What's considered "high" or "low" varies by maturity: 10-year auctions typically attract bid-to-cover ratios around 2.4–2.7. The key is the comparison to recent history — the monitor flags when a ratio falls more than 0.2 below the rolling 6-auction average for that specific maturity, because context matters more than absolute level.</p>""",
    },
    {
        "term_key": "yield-tail",
        "section_id": "sovereign-debt-auction",
        "term_title": "Yield Tail",
        "question": "What does a large yield tail (e.g. +3 basis points) indicate?",
        "answer": "The government had to accept bids at significantly higher yields than the median, suggesting weaker genuine demand",
        "distractors": [
            "The auction attracted more direct bidders than usual",
            "The bond's face value was increased after the auction",
            "The auction happened later in the day than scheduled",
        ],
        "explanation": """<p>The <strong>yield tail</strong> measures the difference between where the majority of bids settled (the <em>median yield</em>) and the highest yield the government had to accept to sell all the bonds (the <em>stop-out yield</em>). It is expressed in <strong>basis points</strong> (bps), where 1 basis point = 0.01%.</p>
<p>In a healthy auction, the median yield and stop-out yield are very close — all serious buyers submitted bids at roughly similar rates, reflecting high confidence in the price. A large tail (e.g., +3 basis points) means the government had to dig deeper into its bid stack and accept bids at significantly higher yields than the midpoint. This indicates the auction attracted fewer genuine voluntary buyers than expected, requiring the most reluctant participants (primary dealers who are legally required to bid) to absorb the extra supply at a premium yield.</p>
<p>Even a tail of 2–3 basis points on a 10-year or 30-year auction is considered noteworthy by bond market professionals because it translates into meaningful additional interest costs on tens of billions of dollars of new debt. The October 2023 30-year auction's ~3bp tail became news instantly and triggered the equity selloff that afternoon.</p>""",
    },
    {
        "term_key": "direct-bidders",
        "section_id": "sovereign-debt-auction",
        "term_title": "Direct Bidders (%)",
        "question": "What does a rising direct bidder percentage across successive auctions suggest?",
        "answer": "Domestic institutions are increasingly competing for Treasuries — a positive demand signal",
        "distractors": [
            "Foreign governments are increasing their Treasury holdings",
            "Primary dealers are absorbing more of the auction supply",
            "The auction's yield tail is widening",
        ],
        "explanation": """<p><strong>Direct bidders</strong> are large institutional investors — pension funds, insurance companies, mutual funds, and asset managers — who participate in Treasury auctions without going through a bank. They bid directly through the Treasury's TreasuryDirect system. Their participation is considered a signal of genuine institutional demand: these are large, long-term, sophisticated investors choosing to allocate to US government debt.</p>
<p>A <em>rising</em> direct bidder percentage over successive auctions is a positive sign — domestic institutions are increasingly competing for Treasuries. A <em>falling</em> percentage suggests institutional interest is waning and they are purchasing less aggressively, which can push the clearing yield higher (meaning more attractive terms had to be offered to fill the auction).</p>""",
    },
    {
        "term_key": "indirect-bidders",
        "section_id": "sovereign-debt-auction",
        "term_title": "Indirect Bidders (%)",
        "question": "Who are 'indirect bidders' in a Treasury auction?",
        "answer": "Primarily foreign central banks, sovereign wealth funds, and overseas government buyers, bidding through primary dealers",
        "distractors": [
            "Domestic pension funds bidding directly through TreasuryDirect",
            "The 24 primary dealer banks obligated to bid at every auction",
            "Individual retail investors buying through their broker",
        ],
        "explanation": """<p><strong>Indirect bidders</strong> are the "foreign" category — primarily foreign central banks, sovereign wealth funds, and overseas government buyers who participate through primary dealers rather than directly. China, Japan, the UK, and oil-producing nations have historically been the largest buyers in this category. Their Treasury purchases are a form of currency recycling: when countries export goods and receive US dollars, they often invest those dollars in US Treasuries to earn a return while holding dollar reserves.</p>
<p>A <em>declining</em> indirect bidder percentage over time is one of the most closely watched early warning signs in global bond markets. It can signal that foreign governments are reducing their appetite for US debt — perhaps because they are diversifying reserves into gold or other currencies, or because geopolitical tensions are reducing their willingness to hold US assets. If foreign demand structurally declines, the US government must either pay higher yields to attract domestic buyers, or rely more heavily on primary dealers (who must bid as a legal obligation, not a choice).</p>""",
    },
    {
        "term_key": "primary-dealers",
        "section_id": "sovereign-debt-auction",
        "term_title": "Primary Dealers (%)",
        "question": "What does it mean when primary dealer absorption rises above 30-35% of an auction?",
        "answer": "Voluntary demand was weak, and the legally obligated dealers had to step in to fill the gap",
        "distractors": [
            "The auction was oversubscribed by voluntary bidders",
            "Foreign central banks bought the entire auction",
            "The bond's maturity was extended",
        ],
        "explanation": """<p><strong>Primary dealers</strong> are approximately 24 large financial institutions (JP Morgan, Goldman Sachs, Bank of America, Barclays, Deutsche Bank, and others) that have a contractual obligation with the Federal Reserve to bid in every US Treasury auction. They act as market-makers for government debt and as buyers of last resort when other demand is insufficient.</p>
<p>In a healthy auction, primary dealers absorb only a modest portion of the supply (15–25%) because voluntary demand from direct and indirect bidders fills most of the auction. When primary dealer absorption rises significantly (above 30–35%), it means voluntary demand was weak and the legally obligated participants had to step in to fill the gap — often at reluctantly high yields. A high primary dealer percentage combined with a large yield tail is the classic "failed auction" pattern that precedes a bond market selloff and equity rotation.</p>
<p>It is also worth noting that primary dealers don't keep this inventory indefinitely — they sell it into the secondary market over subsequent days. A large primary dealer position (called "overhang") can depress bond prices and push yields higher in the days following a weak auction, amplifying the initial impact.</p>""",
    },
    {
        "term_key": "maturity-label",
        "section_id": "sovereign-debt-auction",
        "term_title": "Maturity Label",
        "question": "Why is weak demand for a 30-year Treasury bond generally more concerning than weak demand for a 3-month bill?",
        "answer": "Long-dated weakness signals structural worries about long-run inflation or fiscal sustainability, not just short-term technical noise",
        "distractors": [
            "30-year bonds are auctioned far more frequently than 3-month bills",
            "3-month bills are never purchased by foreign investors",
            "Weak 30-year demand always means bond prices are rising",
        ],
        "explanation": """<p>The <strong>maturity</strong> is the length of time until the bond repays its face value to the holder. Short-dated bills (3M, 6M, 1Y) mature quickly and are primarily influenced by near-term Federal Reserve rate expectations. Medium-term notes (2Y, 5Y, 7Y) reflect expectations about the interest rate path over the next few years. Long-term bonds (10Y, 30Y) reflect long-run expectations about growth, inflation, and fiscal sustainability.</p>
<p>Weak demand for <em>long-dated</em> bonds is generally more concerning than weak demand for short-dated bills. A weak 30-year auction signals that investors are worried about inflation over a 30-year horizon, or about the US government's ability to repay debt that far into the future — structural concerns, not short-term noise. A weak 3-month bill is usually just a technical supply/demand imbalance at the short end and rarely generates significant equity market reactions.</p>
<p>The typical investor hierarchy: foreign central banks prefer long-dated bonds (they hold reserves for stability, not short-term return). Money market funds and banks prefer very short-dated bills (they need liquidity). Insurance companies and pension funds prefer 10–30 year bonds (matching long-duration liabilities). Watching which maturities are weak tells you which investor category is pulling back.</p>""",
    },
    {
        "term_key": "rolling-6-auction-baseline",
        "section_id": "sovereign-debt-auction",
        "term_title": "Rolling 6-Auction Baseline",
        "question": "Why does the monitor compare each auction only to the previous 6 auctions of the same maturity?",
        "answer": "It balances recency with statistical stability — long enough to be stable, short enough to reflect current conditions, and specific to that maturity's own demand pattern",
        "distractors": [
            "Because only 6 auctions of data are ever stored in the database",
            "Because the Federal Reserve mandates a 6-auction comparison window",
            "Because all maturities share an identical baseline regardless of type",
        ],
        "explanation": """<p>Each maturity of Treasury bond has its own "normal" demand dynamics. A bid-to-cover of 2.3 might be completely normal for a 30-year bond (where demand is structurally lower because fewer investors want to commit for 30 years) but alarming for a 2-year note (where short-duration demand is usually much higher). Using a fixed universal threshold for all maturities would generate enormous false-alarm noise.</p>
<p>Instead, the monitor computes the average bid-to-cover ratio and average yield tail for the previous <em>6 auctions of that specific maturity</em>. This rolling baseline adapts to each maturity's own demand patterns and adjusts automatically for structural shifts in the Treasury market over time. An alert only fires when the current auction is meaningfully worse than the recent specific-maturity history — not just lower than some abstract universal standard.</p>
<p>The 6-auction window is deliberately short. Using 12 or 24 auctions would make the baseline too slow to reflect current market conditions. Using only 2 or 3 would make it too volatile and noisy. Six auctions (typically covering 3–6 months for most maturities) provides a balance between recency and statistical stability.</p>""",
    },
    # --- accounts ---
    {
        "term_key": "built-in-accounts-what-it-solves",
        "section_id": "accounts",
        "term_title": "Built-in Accounts — What It Solves",
        "question": "How do Built-in Accounts relate to Ghostfolio?",
        "answer": "They coexist — the Portfolio page merges both sources, summing a ticker held in both into one row",
        "distractors": [
            "Built-in Accounts completely replace and disable Ghostfolio once enabled",
            "Ghostfolio automatically imports all Built-in Account transactions",
            "Built-in Accounts can only be used if Ghostfolio is not configured",
        ],
        "explanation": """<p>Ghostfolio is a great source of live, auto-synced holdings, but it only ever gives you what its own importer captured — it doesn't let you hand-enter a trade, track cash that isn't tied to a position, or keep a brokerage account that you've never connected to Ghostfolio at all. <strong>Built-in Accounts</strong> (<code>/accounts</code>) is a native, database-backed alternative or companion: you create one or more accounts, log every Buy, Sell, Dividend, Interest, Fee, or Cash movement against them, and the app derives holdings, cash balance, and P&amp;L from that ledger — no external service required.</p>
<p>Built-in accounts <strong>coexist</strong> with Ghostfolio rather than replacing it. The Portfolio page merges both sources: a ticker held in both a Ghostfolio account and a built-in account is summed into one row, with both accounts listed against it. If you import the same brokerage account into both systems, that ticker is counted twice — this is a known, documented trade-off rather than an automatic guard.</p>""",
    },
    {
        "term_key": "account-type",
        "section_id": "accounts",
        "term_title": "Account Type",
        "question": "Which account types are aggregated into portfolio-wide ticker views?",
        "answer": "Only Trading accounts — House, Pension, and Watchlist are excluded by design",
        "distractors": [
            "All four account types equally",
            "Only Pension and House accounts",
            "Only the Watchlist account",
        ],
        "explanation": """<p>Every account has a <strong>type</strong>, chosen when creating or editing it: <strong>Trading</strong> (the default — a brokerage-style account whose holdings feed the Portfolio page and X-ray), <strong>House</strong> (tracks a property's value via the Account Price Scraper, below), <strong>Pension</strong> (tracks a pension fund's performance, also via the Account Price Scraper), and <strong>Watchlist</strong> (the system-managed watchlist — see below). Only <strong>Trading</strong> accounts are aggregated into portfolio-wide ticker views — House, Pension, and Watchlist accounts are excluded from that aggregation by design and stay self-contained within <code>/accounts</code>.</p>
<p>Each Trading account tile has its own <strong>X-ray</strong> button, opening the Portfolio X-ray panel scoped to just that account's holdings — no Ghostfolio data mixed in. On the Portfolio page itself, the X-ray account selector's <strong>Global (All Accounts)</strong> option combines every configured source: Ghostfolio (if configured) plus every built-in Trading account, summing the same ticker across both when it appears in more than one. Risk metrics that need a full daily return history (Historical VaR, CVaR, Sharpe/Calmar ratio, tracking error, skewness) work for any scope — Ghostfolio, built-in, or combined — since they're derived from per-ticker cached returns weighted by whatever's currently in scope; they only show as unavailable, with an explanatory warning, if fewer than 30 overlapping cached trading days exist yet (e.g. a holding added since the last nightly risk cache run).</p>
<p>The creation/edit form's <strong>Initial Cash</strong> and <strong>Opening Date</strong> fields relabel themselves to fit the selected type — <strong>Purchase Value</strong>/<strong>Purchase Date</strong> for House, <strong>Opening Balance</strong>/<strong>Opening Balance Date</strong> for Pension — and the account tile on <code>/accounts</code> shows that opening figure under the name instead of the generic "initial cash" wording Trading accounts use — Pension shows "Current Balance: 5000 GBP" (the live valuation), while House shows all three of "Initial Purchase: 300000 GBP", "Current Estimate: 320000 GBP", and "Value gain: 6.67%" (the percentage change of the current estimate over the initial purchase price) on the same line. Pension also gets an optional <strong>Pension Start Date</strong> field for recording when the pension itself began, separate from the Opening Balance Date (when the Opening Balance figure was true) — useful if you're starting to track a pension that already has a balance and want to record how long it's been running; this one is currently just recorded, with no tenure display built from it yet. The optional <strong>Opening Balance Units</strong> field, by contrast, is fully wired in — entering it (alongside the Opening Balance amount) creates a real opening position on save, dated at the Opening Balance Date, so the Holdings table and units-held total reflect a pre-existing balance immediately rather than starting at zero until your first Pay In. Editing either figure later updates that same position rather than creating a second one.</p>""",
    },
    {
        "term_key": "xray-sector-geo-lookthrough",
        "section_id": "accounts",
        "term_title": "X-ray Sector & Geographic Look-Through (ETFs / Funds)",
        "question": "How is a fund's Geographic Exposure approximated in X-ray?",
        "answer": "From the fund's top 10 holdings' known countries, with the resolvable subset's weights rescaled to sum to 100%",
        "distractors": [
            "Directly from Yahoo Finance's official country breakdown for the fund",
            "By assuming all funds are 100% domiciled in their listing country",
            "By evenly splitting the fund's weight across every country in its benchmark index",
        ],
        "explanation": """<p>An ETF or mutual fund has no single sector or country — it's a basket of dozens or hundreds of underlying holdings. Yahoo Finance does provide a complete <strong>sector weightings</strong> breakdown for most funds (e.g. an S&amp;P 500 ETF might be 38% Technology, 12% Financial Services, and so on), which X-ray blends directly into the Sector Exposure chart instead of lumping the whole holding into "Unclassified".</p>
<p>Yahoo has no equivalent country/region breakdown for funds, so <strong>Geographic Exposure</strong> is instead approximated from the fund's own <strong>top 10 holdings</strong>: each underlying stock's known country is looked up, and the resolvable subset's weights are rescaled to sum to 100% of the fund's weight — e.g. if only 4 of the top 10 holdings have a known country, covering 60% of the fund's weight, that 60% is scaled up to represent the full 100%. This is a directional estimate, not an exact figure, and is most reliable for concentrated funds where the top 10 holdings represent most of the fund's value.</p>""",
    },
    {
        "term_key": "auto-top-up-trading",
        "section_id": "accounts",
        "term_title": "Auto Top-up (Trading)",
        "question": "Why doesn't Auto Top-up post a cash deposit the instant its scheduled date arrives?",
        "answer": "Bank credit dates drift around weekends and bank holidays, so a date-only schedule can't be trusted to match the real ledger",
        "distractors": [
            "The app cannot process transactions on weekends for any reason",
            "Auto Top-up requires manual approval from a bank administrator",
            "Cash deposits over £500 require a separate confirmation step",
        ],
        "explanation": """<p>For regular direct-debit deposits into a Trading account, <strong>Auto Top-up</strong> (gear icon next to Edit on the account's tile, or the button on its detail page) records an amount and a schedule — either a fixed <strong>day of the month</strong> (1-31; some months don't have a 29th-31st, those months are simply skipped, the same way a bank's own direct debit would shift or skip) or a <strong>day of the week</strong> (Monday-Friday).</p>
<p>The app deliberately does <strong>not</strong> post a cash deposit the moment the scheduled date arrives — bank credit dates drift around weekends and bank holidays, so a date-only schedule can't be trusted to match the real ledger. Instead, when the schedule fires it creates a <strong>pending</strong> top-up and tags the account <code>[PENDING ACTION]</code> on the Accounts page. Opening the account's detail page surfaces a confirmation banner showing the expected amount/date — both editable, in case the payment landed a day or two late or for a slightly different amount — with <strong>Confirm Payment</strong> (posts a real <code>Cash</code> deposit transaction for the edited amount/date) and <strong>Dismiss</strong> (clears the pending item with no transaction, e.g. a direct debit that failed that month). Multiple unresolved pending top-ups can stack for the same account if several scheduled dates pass unconfirmed — each is resolved independently, oldest first, rather than the newest replacing or blocking earlier ones. Disabling Auto Top-up (or deleting the account) only stops future scheduling; it doesn't retroactively withdraw an already-pending confirmation.</p>""",
    },
    {
        "term_key": "account-price-scraper",
        "section_id": "accounts",
        "term_title": "Account Price Scraper (House / Pension)",
        "question": "How are House and Pension account values priced, since they have no market ticker?",
        "answer": "A generic URL + CSS selector scraper pulls a price from a configured web page on a schedule",
        "distractors": [
            "They are priced using the same ML Confidence Score model as equities",
            "Ghostfolio must be configured to provide their price",
            "They are manually re-priced once per year by the operator",
        ],
        "explanation": """<p>House and Pension accounts have no real market ticker to price against, so instead they're fed by a generic <strong>URL + CSS selector</strong> price scraper — the same approach Ghostfolio's own "manual asset" feature uses. Typically this points at a small static HTML file an operator's own external script (e.g. a cron job scraping a property-valuation site or a pension provider's published unit price) writes once a day — something as simple as <code>&lt;div id="gf-price"&gt;123.45&lt;/div&gt;</code> — but any page works as long as the selector matches an element containing the number. Configure it from the gear icon on the account's tile (or its detail page): <strong>Url</strong>, <strong>Selector</strong>, optional <strong>HTTP Request Headers</strong> (JSON), and a <strong>Daily Run Time</strong> in your own local timezone. <strong>Test</strong> validates the selector against the live page without saving or recording anything; <strong>Scrape Now</strong> runs the saved config immediately and records a real price point — useful both to confirm the scraper works and to backfill an ad-hoc value. Once enabled, a dedicated scheduled job runs it automatically once a day at the configured time. A <strong>Historical Data (CSV)</strong> box on the same modal accepts pasted <code>date;marketPrice</code> rows (semicolon-delimited) to seed price history going further back than the scraper has been running.</p>
<p>For <strong>House</strong>, the latest scraped price <em>is</em> the account's equity value — there's no holdings or transaction concept at all, just a daily price and the resulting value-over-time chart. For <strong>Pension</strong>, the scraped value is a fund <strong>unit price</strong>, used to value a single synthetic holding that the <strong>Pay In</strong> and <strong>Admin Fee</strong> actions (below) build up over time.</p>""",
    },
    {
        "term_key": "pay-in-admin-fee-pension",
        "section_id": "accounts",
        "term_title": "Pay In & Admin Fee (Pension)",
        "question": "What does the Admin Fee action let you enter, depending on what your pension provider shows?",
        "answer": "Either the units remaining after the fee, or the units deducted directly",
        "distractors": [
            "Only a percentage fee rate applied automatically each month",
            "The total historical fees charged since the account was opened",
            "A fixed fee amount set once when the account was created",
        ],
        "explanation": """<p>A Pension account's one holding is built from two dedicated actions on its detail page rather than the generic transaction form. <strong>Pay In</strong> records a contribution: pick a date and the Unit Price field auto-fills from price history for that date (still editable, in case the lookup is missing or you want to override it); enter the amount paid in (in the account's own currency) and a live preview above Save shows exactly how many units that buys before you commit. <strong>Admin Fee</strong> automates the calculation most pension providers don't give you directly: rather than asking for a fee amount in pounds, it offers a choice of two inputs depending on what the provider's portal actually shows — <strong>units remaining after the fee</strong> (the modal computes the delta against units currently held, shown for reference) or, if the provider states it directly, <strong>units deducted</strong> entered as-is. Either way the modal auto-fills the unit price the same way Pay In does and shows a live preview of the units removed and their monetary cost before you save. Both actions still require a manual click and a manual portal reading — only the arithmetic is automated, not the detection of when a fee happened.</p>""",
    },
    {
        "term_key": "pension-benchmarks",
        "section_id": "accounts",
        "term_title": "Pension Benchmarks",
        "question": "What does 'rebasing' a benchmark line to the pension's starting value achieve?",
        "answer": "It lets all lines sit on the same value axis, answering 'what would this account be worth if it tracked this index instead'",
        "distractors": [
            "It converts the benchmark into a percentage-return-only chart",
            "It resets the benchmark's data to zero every calendar year",
            "It removes the need for a UK CPI comparison line",
        ],
        "explanation": """<p>The Pension Value Over Time chart on a Pension account's detail page can overlay comparison lines against the account's own value, added alongside two configurable inputs edited from the <strong>&#127919; Benchmarks</strong> button on the account's tile (<code>/accounts</code>): a fixed <strong>UK CPI + Target</strong> line (UK CPI YoY%, the same series charted on the Market Sentiment page's "UK Price Stability: CPI YoY vs FTSE 100" chart, plus a user-set target — 4% by default) and any number of <strong>ticker benchmarks</strong> (a Yahoo Finance ticker plus a display name), defaulting to <strong>URTH</strong> (MSCI World Index, tracked via the iShares MSCI World ETF) and <strong>VWRL.L</strong> (FTSE All-World Index, tracked via the Vanguard FTSE All-World UCITS ETF). Every line is <strong>rebased</strong> to the pension's own starting value on its first snapshot date, so all lines sit on the same value axis as the Pension Value line itself rather than needing a separate percentage-return chart — a benchmark line answers "what would this account be worth today if it had grown at this rate/tracked this index instead," not "what % did this index return."</p>""",
    },
    {
        "term_key": "uk-treasury-bill-zero-coupon",
        "section_id": "accounts",
        "term_title": "UK Treasury Bill (Zero-Coupon)",
        "question": "Why does each Treasury Bill purchase get its own unique internal ticker?",
        "answer": "So holding several bills at once never blends their discount prices together in the average-cost ledger",
        "distractors": [
            "Because UK regulations require a unique ISIN per purchase",
            "To allow the bill to be sold before maturity like a normal stock",
            "Because Yahoo Finance requires a unique ticker for every instrument",
        ],
        "explanation": """<p>A Trading account's <strong>Buy T-Bill</strong> action records a UK Treasury bill — a zero-coupon instrument bought at a discount to its face (par) value, paying no coupon, that pays back the full face value in cash on a fixed maturity date roughly 28 days later. Freetrade never states face value directly — only the <strong>Amount</strong> (Total Cost) you paid and an <strong>Indicative YTM</strong>, itself just an estimate since the real yield isn't fixed until the Friday DMO tender closes — so enter the <strong>Start Date</strong>, <strong>Amount</strong>, <strong>Indicative YTM</strong>, and <strong>Maturity Date</strong> exactly as shown in the app, and <strong>Face Value</strong> auto-fills as an estimate (Amount + Amount × YTM × days/365) that you can hand-correct if you later learn the exact redemption figure — this is the number the maturity sweep later credits to cash, unchanged from what you saved here. Each purchase gets its own internal ticker behind the scenes, so holding several bills at once — buying a new one each week while an earlier one hasn't matured yet — never blends their discount prices together the way a shared ticker would in the average-cost ledger.</p>
<p>Between purchase and maturity the Holdings table (and Home Assistant) show the bill's value <strong>accreting</strong> in a straight line from the purchase price toward face value, rather than sitting frozen at the discount price. A daily background sweep automatically closes the position on its maturity date, crediting the account's cash balance with the full face value — no manual action needed. <strong>You cannot sell or cancel a Treasury Bill before maturity</strong> — matching the real DMO/broker rules — so the only way to correct a mis-entered bill is to delete it from the Treasury Bills panel, which removes its purchase (and maturity, if it's already matured) transactions together.</p>
<p><strong>Auto-Reinvest is a reminder only</strong>, never an automatic re-purchase: the actual yield on the next weekly issue isn't known until the Friday DMO tender closes, so the app has nothing to act on in advance. When a bill flagged Auto-Reinvest matures, it fires a routed notification prompting you to place your next order through your broker and log it here once it's filled.</p>
<p>Since a bill is often logged before the tender that fixes its real yield has actually happened, a <strong>"Confirm the final YTM"</strong> banner appears on the account page — mirroring Auto Top-up's confirm/dismiss pattern — for any bill whose Start Date has arrived without its YTM being confirmed yet (by then the Friday tender has definitely closed). Enter the real confirmed YTM to recompute Face Value from it, or click <strong>Keep Estimate</strong> to accept the original figure as final with no changes; either way the banner clears. An <strong>Edit</strong> button on every bill's row offers the same recompute-from-YTM (or type the exact Face Value directly) at any time afterward too — including after the bill has already matured, in which case its already-posted cash credit is corrected to match rather than left out of sync.</p>
<p>In X-ray, a Treasury Bill holding is classified as <strong>Cash &amp; Equivalents</strong> — its own bucket in the Asset Class Allocation donut and in the Sector/Geographic Exposure charts — rather than defaulting to Equity, since it has no real Yahoo Finance listing to derive a sector or country from.</p>""",
    },
    {
        "term_key": "watchlist-account",
        "section_id": "accounts",
        "term_title": "Watchlist Account",
        "question": "What makes the Watchlist account different from Trading, House, and Pension accounts?",
        "answer": "Exactly one always exists automatically, holds no transactions, and can't be created, deleted, or converted",
        "distractors": [
            "It is the only account type that supports Buy and Sell transactions",
            "It requires a separate Ghostfolio subscription to function",
            "It is the only account type included in portfolio-wide aggregation",
        ],
        "explanation": """<p>Exactly one <strong>Watchlist</strong>-type account always exists — the system creates it automatically on first boot, and it can't be created, deleted, or converted to/from manually. It holds no transactions, just a flat list of tickers you're following: add one via the star icon on a stock's detail page (<code>/stock/{ticker}</code>), or via the "+ Add Ticker" search on the account's own page. Opening it from <code>/accounts</code> shows a compact table — search, filter by exchange or instrument type, and select rows with checkboxes to delete in bulk — rather than the full transaction-ledger view other account types get. This replaces the previous Ghostfolio-backed watchlist; the full <code>/watchlist</code> page (scores, signals, technicals) reads from the same underlying list.</p>""",
    },
    {
        "term_key": "transaction-types-cash-tracking",
        "section_id": "accounts",
        "term_title": "Transaction Types & Cash Tracking",
        "question": "What does the Transfer transaction type do?",
        "answer": "Moves cash between two of your own built-in accounts, recording a linked debit and credit so both balances stay correct",
        "distractors": [
            "Converts shares from one ticker to another automatically",
            "Records a dividend payment received in a foreign currency",
            "Applies a one-time fee to close an account",
        ],
        "explanation": """<p>Every position and cash movement is a row in the account's transaction ledger. Seven types are supported: <strong>Buy</strong> and <strong>Sell</strong> (move shares and cash), <strong>Dividend</strong> and <strong>Interest</strong> (cash income, no shares), <strong>Fee</strong> (a cash deduction not tied to a trade), <strong>Cash</strong> (a manual deposit — positive amount — or withdrawal — negative amount), and <strong>Transfer</strong> (moves cash between two of your own built-in accounts in one action — pick the destination account and a positive amount; the app records a linked debit on the source account and credit on the destination so both balances stay correct). Every transaction — manually entered or imported from Ghostfolio — affects cash. This is only accurate if your real deposit/withdrawal history is also recorded as Cash/Transfer rows; without that, the calculated balance reflects only the net effect of your trades, not what's actually sitting in the account.</p>
<p>Every Buy/Sell/Dividend transaction also has an optional <strong>ISIN</strong> field next to Ticker — the instrument's International Securities Identification Number, which stays the same even if the ticker symbol later changes or the instrument is delisted. It's purely informational (never validated or looked up), auto-filled by CSV import when the source file provides one, and otherwise left blank.</p>
<p>Transfers are immutable once created — to change one, delete it (which removes both linked legs) and record a new one. Every other transaction carries its own trade <strong>Currency</strong>, picked from a dropdown next to Unit Price (auto-filled from the ticker lookup, but always editable — important since the same exchange can list stocks in more than one currency, e.g. London-listed shares are usually quoted in GBp pence but some are GBP or EUR). The <strong>Exchange Rate</strong> field only appears when the transaction's currency differs from your base currency, and refreshes automatically whenever you change the currency or date — so correcting GBp to GBP also corrects the rate from 0.01 to 1.0 without a separate step; when the currencies match, the field is hidden entirely and a rate of 1.0 is used behind the scenes. The currencies offered in the dropdown are configured in Settings → Core System & Currencies → <strong>Account Currencies</strong> (default: GBP, GBp, USD, EUR). A live "Total" preview above the Save button shows the trade total plus fee, and the resulting cash impact in your base currency, so you can sanity-check the FX math before saving.</p>
<p>The Fee has its own independent <strong>Fee Currency</strong> selector next to it, since a fee isn't always billed in the same currency as the trade — e.g. a broker's FX spread fee shown in your base currency (GBP) on a USD trade. It defaults to the trade currency (matching the ledger's original behaviour) but can be switched separately; a <strong>Fee Exchange Rate</strong> field appears alongside it whenever the fee currency differs from your base currency, auto-filled the same way as the trade's Exchange Rate. Getting this wrong previously meant a fee billed in your base currency got silently run through the trade's own FX rate a second time, over- or under-charging the transaction's cash impact.</p>""",
    },
    {
        "term_key": "holdings-closed-positions-realized-pnl",
        "section_id": "accounts",
        "term_title": "Holdings, Closed Positions & Realized P&L",
        "question": "What method is used to derive holdings from an account's transaction ledger?",
        "answer": "Average-cost — each Buy raises the average cost basis, each Sell realises a gain or loss against it",
        "distractors": [
            "First-in-first-out (FIFO) lot matching",
            "Last-in-first-out (LIFO) lot matching",
            "A fixed cost basis set once at account creation",
        ],
        "explanation": """<p>Holdings aren't stored — they're derived on every read by replaying an account's transactions in chronological order using an <strong>average-cost</strong> method: each Buy raises the position's average cost basis, each Sell realises a gain or loss against that average cost and reduces the remaining shares. A ticker that's been fully sold nets to zero open shares but isn't discarded — it survives in the ledger and appears under <strong>Closed Positions</strong> with its realized P&amp;L, so your full trading history (wins and losses) stays visible even after you've exited a position.</p>""",
    },
    {
        "term_key": "position-targets-low-high",
        "section_id": "accounts",
        "term_title": "Position Targets — Low/High Set Targets",
        "question": "How often can a Position Target notification fire for the same account, ticker, and direction?",
        "answer": "At most once per calendar day, even if the price crosses back and forth over the target repeatedly",
        "distractors": [
            "Every time the price crosses the target, with no limit",
            "Only once ever, for the lifetime of the position",
            "Once per hour during market hours",
        ],
        "explanation": """<p>A standalone <strong>Position Targets</strong> box on the Stock Detail page (independent of the Your Position box — it also appears for a Watchlist-only ticker with no holding at all) lets you click "Set Targets" to set a <strong>Low</strong> (buy-more/watch) and/or <strong>High</strong> (sell/take-profit) price target per built-in account holding that ticker, <strong>plus a separate Watchlist row</strong> if the ticker is on your Watchlist — a ticker held in two accounts and also watchlisted shows three independent rows (Account 1, Account 2, Watchlist), each with its own target. A "Set for all accounts" checkbox applies the same pair to every row shown, watchlist row included. As a starting point, the panel shows the ML Quantile Regression model's current <strong>Q10/Q90 price band</strong> (see ML Quantile Price Bands) for that ticker — a statistical range, not a recommendation, since the model was trained for a general 10-day price distribution rather than as a dedicated target-setting tool. Leaving a field blank, or entering <code>0</code>, clears that target.</p>
<p>Targets are stored in <code>holding_price_limits</code>, keyed by account and ticker — the same table already exposed to Home Assistant as the "Low Limit"/"High Limit" number entities (the Watchlist account is a real built-in account internally, so its row works identically to a Trading account row with no special-casing), so a target set on the web app or in Home Assistant is the same value seen on both. Every 5 minutes during market hours, the intraday scan compares each held or watchlisted ticker's live price against any set target — a <strong>Low</strong> target fires when the price drops to or below it, a <strong>High</strong> target fires when the price rises to or above it — and sends a <strong>Position Target Reached</strong> notification the first time it's crossed. Unlike Crash/Moonshot alerts, this fires <strong>at most once per account, per ticker, per direction, per calendar day</strong> — the price can cross back and forth over the target repeatedly in one session without repeat notifications; the next notification for that target can only fire the following day. It's routed through its own independent notification channel toggles (Settings → Notification Settings → "↳ Position Target Reached", grouped under Crash & Moonshot Alerts) — enabling or disabling it, or its Nextcloud Talk delivery, never affects Crash/Moonshot/Anomaly alerts. A ticker that's watchlisted but not held is scanned only for its own price target — setting a Watchlist target does not add that ticker to Crash/Moonshot/Anomaly detection. Ghostfolio-only holdings (no built-in account) don't currently support Position Targets, since the table is keyed to a built-in account. The Watchlist page (<code>/watchlist</code>) surfaces a Watchlist-row target: once at least one watchlisted ticker has a target set, a <strong>Has Target Set</strong> filter appears that swaps the Piotroski/Altman Z/Beneish M columns for Low Target/High Target columns and filters the table to only rows with a target. Portfolio-page targets are out of scope for that filter, since a ticker's Portfolio row aggregates across every account and a single ticker can hold different targets in different accounts.</p>""",
    },
    {
        "term_key": "account-value-snapshot",
        "section_id": "accounts",
        "term_title": "Account Value Snapshot",
        "question": "What does comparing Total Value against Net Contributions on an account's chart show?",
        "answer": "At a glance, whether the account is ahead of or behind what was actually paid in",
        "distractors": [
            "The account's total dividend income for the year",
            "The exact number of transactions recorded in the ledger",
            "The account's current cash-to-equity ratio only",
        ],
        "explanation": """<p>The value-over-time chart on each account's detail page is fed by a per-day snapshot written to <code>account_value_history</code> — <strong>Total Value</strong> (cash + equity), <strong>Cash</strong>, and <strong>Net Contributions</strong> (cumulative money put in or taken out via Cash and Transfer transactions only — Buy/Sell/Dividend/Interest/Fee don't count, since they're investment activity rather than a deposit or withdrawal). Comparing Total Value against Net Contributions shows at a glance whether the account is ahead of or behind what was actually paid in. A nightly scheduled job refreshes every account once a day, but the chart and Cash Balance History also recompute immediately in the background whenever you add, edit, or delete a transaction, record a transfer, or import from Ghostfolio — you don't have to wait for the next nightly run to see a change reflected. When you first create an account, a one-time backfill runs in the background against cached historical price data so the chart has a meaningful history immediately rather than starting from a single point. The job can also be triggered on demand from the Background Automation Schedulers panel in Settings. The chart's <strong>1M / 1Y / YTD / MAX</strong> range buttons (on Trading accounts only) re-fetch from <code>GET /api/accounts/{id}/value-history?period=...</code>; the selected range is saved in an <code>acct_chart_period</code> browser cookie, so it carries over to every other account you open.</p>""",
    },
    {
        "term_key": "live-return-tiles-mwrr",
        "section_id": "accounts",
        "term_title": "Live Return Tiles & Money-Weighted Rate of Return",
        "question": "Why are period returns on a Trading account shown as a currency amount rather than a percentage?",
        "answer": "Dividing by a small starting value can blow the percentage up into a meaningless figure, while the currency amount stays sane and bounded",
        "distractors": [
            "Percentages are not supported by the underlying database schema",
            "Currency amounts are easier to convert between GBP and USD",
            "Regulators require currency-amount reporting for all UK accounts",
        ],
        "explanation": """<p>A Trading account's detail page shows two live-refreshing tile rows below the summary tiles: <strong>1 Day / 1 Week / 1 Month / 3 Month / 6 Month / 1 Year Return</strong> (in <code>BASE_CURRENCY</code>), and <strong>Unrealized P&amp;L</strong> / <strong>Money-Weighted Rate of Return (MWRR)</strong>. Every period return excludes the effect of deposits and withdrawals made during that window — <code>end value − start value − net contributions during the period</code> — so topping up cash doesn't look like a gain. These are deliberately shown as a currency amount rather than a percentage: dividing by the period's starting value blows up into a huge, meaningless percentage whenever that baseline happens to be small (most commonly a lookback window older than the account itself, which falls back to the earliest available snapshot — near account opening, before real deposits landed, that snapshot's value can be tiny). The currency amount stays sane and bounded regardless. Because the app only keeps one value snapshot per calendar day (<code>account_value_history</code>, see Account Value Snapshot above), "1 Day" means "since last night's snapshot," not a rolling 24 hours; a return whose lookback window predates the account's first snapshot falls back to the earliest one available rather than showing nothing — in currency terms this still reads as a sensible "gain since earliest available data," just mislabeled by the window name if the account isn't actually that old yet.</p>
<p><strong>MWRR</strong> is a single since-inception figure using the <strong>Modified Dietz method</strong> — a closed-form approximation of a true money-weighted (IRR-style) return that weights every deposit/withdrawal by how long it's been invested, without needing an iterative solver. For typical usage this tracks a strict XIRR calculation closely; it can diverge slightly for accounts with very large or irregular cash flows clustered near account inception.</p>
<p>These figures are computed once per 5-minute intraday scan cycle (the same job that refreshes live prices) and persisted to <code>account_performance_cache</code>, so every browser tab viewing the page reads a shared, already-computed value rather than each one re-deriving it from the full transaction history — the page shows the latest persisted figures immediately on load, then polls <code>GET /api/accounts/{id}/live-performance</code> every <code>UI_PREFERENCES.REFRESH_RATE</code> seconds while the tab stays open and visible, matching the existing "Live prices on Stock Detail View" toggle's behavior. If polling is disabled in Settings, the tiles still show the last computed values from page load, they simply stop auto-refreshing.</p>""",
    },
    {
        "term_key": "portfolio-gain-vs-fx",
        "section_id": "accounts",
        "term_title": "Portfolio Gain vs. Portfolio Gain with FX",
        "question": "When do Portfolio Gain and Portfolio Gain with FX show identical figures?",
        "answer": "For a BASE_CURRENCY-native holding, since its exchange rate is always 1.0",
        "distractors": [
            "Only when the portfolio contains exclusively foreign-currency holdings",
            "Only immediately after a Cash Reconciliation",
            "Never — the two figures always differ by design",
        ],
        "explanation": """<p><strong>Portfolio Gain</strong> is the unrealized gain across all open holdings in every Trading account, re-expressed at each holding's own purchase-quantity-weighted-average exchange rate — it isolates how the underlying investments themselves performed, with currency movement since purchase neutralised out. <strong>Portfolio Gain with FX</strong> is the actual gain you'd realise today, at today's live exchange rates, so it also carries whatever FX tailwind or headwind has occurred since each purchase.</p>
<p>A BASE_CURRENCY-native holding (e.g. a GBP stock when your base currency is GBP) always has an exchange rate of 1.0, so the two figures are identical for it automatically. The gap only opens up on foreign-currency holdings, and only widens or narrows as FX rates actually move. Both figures cover open holdings only — not realized gains from positions you've already closed.</p>""",
    },
    {
        "term_key": "twr-vs-twr-with-fx",
        "section_id": "accounts",
        "term_title": "Time-Weighted Return % vs. Time-Weighted Return with FX %",
        "question": "What makes Time-Weighted Return (TWR) different from a simple gain percentage?",
        "answer": "TWR isn't distorted by the timing and size of deposits and withdrawals — it geometrically links returns between snapshots",
        "distractors": [
            "TWR only applies to accounts with no cash transactions",
            "TWR is always higher than a simple gain percentage",
            "TWR requires the account to be at least one year old",
        ],
        "explanation": """<p><strong>Time-Weighted Return (TWR)</strong> is the standard chain-linked return used to judge investment performance independent of the timing and size of your own deposits and withdrawals — unlike a simple gain percentage, it isn't distorted by, say, a large top-up landing right before a rally. It's computed by geometrically linking the return of each period between snapshots, so a sequence of gains and losses compounds correctly rather than being averaged.</p>
<p><strong>Time-Weighted Return %</strong> is FX-neutral — every holding's foreign-currency value is re-expressed at a fixed baseline exchange rate throughout, so the figure reflects only the underlying investment strategy's performance. <strong>Time-Weighted Return with FX %</strong> is the actual, currency-inclusive figure using each day's real exchange rate, so it also reflects how currency movements affected your returns as experienced in your base currency. As with Portfolio Gain above, the two converge to the same value whenever a portfolio holds only BASE_CURRENCY-native positions.</p>""",
    },
    {
        "term_key": "cash-reconciliation",
        "section_id": "accounts",
        "term_title": "Cash Reconciliation",
        "question": "What happens when you use the Reconcile feature to enter your real broker balance?",
        "answer": "If the difference from the computed balance is more than half a penny, the app books a single Cash transaction for exactly that difference",
        "distractors": [
            "The app deletes and re-imports the entire transaction history",
            "The reconciliation only works for USD-denominated accounts",
            "A reconciliation always requires contacting the broker directly",
        ],
        "explanation": """<p>The <strong>Reconcile</strong> button on a Trading account's detail page lets you true up small drift between the app's computed cash balance and what your real broker statement shows — typically FX rounding or a missed fee. You enter the actual balance from your statement (in <code>BASE_CURRENCY</code>, matching the Cash Balance tile); the app computes the difference against <code>accounts_engine.cash_balance()</code> and, if it's more than half a penny, books a single <strong>Cash</strong> transaction for exactly that difference (<code>POST /api/accounts/{id}/reconcile-cash</code>). If the balances already match, nothing is booked. Every reconciliation transaction is tagged <code>is_adjustment</code> in the database, shown with an <strong>Adjustment</strong> badge in the Activities table, and can be isolated at any time via the <strong>Adjustment</strong> option in the "Filter by type" dropdown — so you can always see how many times an account has needed truing up. The tag is set once at creation and isn't cleared if you later edit the transaction's date or amount.</p>""",
    },
    {
        "term_key": "import-from-csv",
        "section_id": "accounts",
        "term_title": "Import from CSV",
        "question": "What happens to a CSV row whose ticker can't be resolved to a known symbol?",
        "answer": "It is skipped outright, and listed individually in the result message and Notifications panel",
        "distractors": [
            "It is imported anyway with a blank ticker field",
            "The entire import file is rejected",
            "It is automatically matched to the closest similarly-named ticker",
        ],
        "explanation": """<p>The <strong>"Import from CSV"</strong> control on the Accounts page loads a GIA/broker-style activity export file directly into a built-in account — useful for backfilling a full trading history from a broker that doesn't connect to Ghostfolio. <code>TOP_UP</code> rows become <strong>Cash</strong> deposits, <code>INTEREST_FROM_CASH</code> becomes <strong>Interest</strong>, <code>ORDER</code> rows become <strong>Buy</strong>/<strong>Sell</strong>, and <code>DIVIDEND</code> rows become <strong>Dividend</strong>. <code>INTERNAL_TRANSFER</code> rows are ignored — record transfers between your own accounts manually instead. The required column layout, and exactly how the GBP exchange rate and fees are derived from the file, are documented in <code>assets/csv_import_format.md</code>.</p>
<p>Unlike Ghostfolio import, a row whose ticker can't be resolved against this app's own ticker cache or Yahoo Finance (typically a delisted or mistyped symbol) is <strong>skipped outright</strong> rather than imported and flagged — there's no real ticker to attach the trade to. Every skipped row — unresolved ticker, no ticker in the file, an unparseable date, or an already-imported duplicate — is listed individually with its date and ticker, both in the result message and in a persistent entry on the <strong>Notifications</strong> panel, so nothing silently goes missing and you can find the exact row in your file after the fact. Re-uploading the same file (or a later export that just appends new rows) is safe — already-imported rows are recognised and skipped, the same dedup convention Ghostfolio import uses.</p>""",
    },
    {
        "term_key": "export-to-csv",
        "section_id": "accounts",
        "term_title": "Export to CSV",
        "question": "Why might re-importing an account's own CSV export fail the importer's column check?",
        "answer": "The export's Fee column stands in for two separate import columns (Stamp Duty and Dividend Withheld Tax) that the ledger doesn't track separately",
        "distractors": [
            "Exported files are encrypted and cannot be re-read by the importer",
            "The export format uses a different date standard than the importer expects",
            "CSV export is only available for Watchlist accounts",
        ],
        "explanation": """<p>The <strong>"Export to CSV"</strong> link on each account card downloads its entire transaction ledger with column headers that deliberately mirror the Import from CSV format, so the file doubles as a practical backup — easy to read against a brokerage statement and close to ready for re-import. <code>Fee</code> stands in for the import format's separate <code>Stamp Duty</code> and <code>Dividend Withheld Tax Amount</code> columns, since the ledger doesn't track those separately — re-importing an export as-is will fail the importer's required-column check until those headers are restored by hand. <strong>Fee</strong> and <strong>Transfer</strong> rows have no equivalent in the GIA <code>Type</code> vocabulary and are written as <code>FEE</code>/<code>TRANSFER</code>, which the importer skips on re-import exactly like it already skips <code>INTERNAL_TRANSFER</code>.</p>
<p>The <strong>Position</strong> column reads <code>closed</code> only once a ticker has been <strong>fully exited</strong> — every share sold, none remaining — and is left blank otherwise, including while a position is only partially sold down. It's also populated on a ticker's Dividend rows, not just its Buy/Sell rows, so a dividend received before a position was later closed out still shows the right status with hindsight.</p>""",
    },
    # --- backup-recovery ---
    {
        "term_key": "automated-backup",
        "section_id": "backup-recovery",
        "term_title": "Automated Backup",
        "question": "What are the three independently-toggled components of a Backup & Recovery archive?",
        "answer": "Data, Models, and Database",
        "distractors": [
            "Logs, Screenshots, and Cache",
            "Users, Sessions, and API Keys",
            "Templates, Static Assets, and Config",
        ],
        "explanation": """<p>Built-in Accounts holds data — the brokerage ledger, account values, and Account Price Scraper history — that previously had no SQLite-resident equivalent, so the app had nothing the operator could lose. <strong>Backup &amp; Recovery</strong> (Settings → Backup &amp; Recovery) archives the three things worth protecting independently: <strong>Data</strong> (the <code>data/</code> directory — Parquet caches, JSON trackers, and everything else except the database file itself), <strong>Models</strong> (trained ML artefacts under <code>models/</code>), and <strong>Database</strong> (<code>data/analysis.db</code>) — each toggled on or off via its own checkbox, so a config that only needs the database backed up doesn't waste space re-archiving gigabytes of cached Parquet history.</p>
<p>The destination is either a <strong>Local Folder</strong> (a path relative to the app's install directory, default <code>backups</code>) or an <strong>NFS Share</strong> (a server IP/FQDN plus export path — the app mounts it with <code>mount -t nfs</code> for the duration of the backup and unmounts afterward). Enable the schedule with the day-of-week checkboxes and a run time in your local timezone, or skip scheduling entirely and use <strong>Run Backup Now</strong> for an on-demand archive. Each run writes a single timestamped <code>backup_YYYYMMDD_HHMMSS.tar.gz</code>; <strong>Retention</strong> controls how many of the most recent archives are kept at the destination — older ones are deleted automatically after each successful run.</p>
<p><strong>NFS Share requires one-time host setup</strong> — mounting a filesystem needs root privilege, which the app itself never has. Run <code>tools/setup_nfs_backup.sh</code> once on the host that runs the app; it installs two narrowly-scoped root-owned wrapper scripts plus a passwordless <code>sudo</code> rule limited to exactly those two scripts, so the app can mount/unmount only its own backup scratch directory and nothing else on the host. See <a href="#doc-nfs-backup-setup">NFS Backup — Server Setup</a> below for the manual steps and what gets installed. <strong>Local Folder</strong> backups need none of this.</p>""",
    },
    {
        "term_key": "recovery",
        "section_id": "backup-recovery",
        "term_title": "Recovery",
        "question": "What should you do after restoring a backup archive?",
        "answer": "Restart the service so every in-memory cache reloads from the restored files",
        "distractors": [
            "Immediately trigger a new backup of the just-restored data",
            "Nothing further is needed — restores apply instantly with no restart",
            "Manually re-enter every transaction from the restored ledger",
        ],
        "explanation": """<p>The Recovery panel lists every archive found at the currently configured destination in a dropdown, newest first. Selecting one and clicking <strong>Restore</strong> extracts it back into place — overwriting the live database, data files, and models with whatever that archive contains (only the components that were included when it was created). This is destructive and requires confirmation; restart the service afterward so every in-memory cache (Yahoo Finance cache, ML model, scheduler state) reloads from the restored files rather than continuing to serve what was in memory before the restore.</p>""",
    },
    # --- security-access ---
    {
        "term_key": "embed-token",
        "section_id": "security-access",
        "term_title": "Embed Token",
        "question": "What access does a leaked Embed Token URL expose?",
        "answer": "Only the read-only embedded view of that specific page — not the API Key's full account access",
        "distractors": [
            "Full administrative access to every API endpoint",
            "The ability to modify account transactions remotely",
            "Access to the server's underlying filesystem",
        ],
        "explanation": """<p>The Portfolio, Watchlist, and Stock Detail pages can be embedded read-only in another tool (e.g. a Home Assistant iframe card) via <code>?embed=true</code>, which hides the navbar and other chrome. Because that embed usually loads the page cross-origin, the normal session cookie is never sent, so the page would otherwise redirect to the login screen every time.</p>
<p>The <strong>Embed Token</strong> (Settings → User Account) is a dedicated secret, separate from the general-purpose API Key, that lets a <code>GET</code> request to one of those pages skip login when both <code>embed=true</code> and a matching <code>&amp;embed_token=&lt;token&gt;</code> are present in the URL. It grants no other access — it does not work on any other page, on any <code>/api/*</code> endpoint, or on any non-<code>GET</code> request — so a leaked embed URL exposes only the read-only embedded view, not the API Key's full account access. Generating a new token immediately invalidates the old one.</p>
<p>Clicking a ticker from an embedded Portfolio or Watchlist page carries the token forward automatically, so drilling into a Stock Detail page from an embedded view stays logged in without needing the token typed into that link separately.</p>""",
    },
    # --- workflow-monitor ---
    {
        "term_key": "workflow-monitor-term",
        "section_id": "workflow-monitor",
        "term_title": "Workflow Monitor",
        "question": "What does 'Manual Account Entry' represent on the Workflow Monitor graph?",
        "answer": "A non-scheduled data source (like hand-entered Built-in Account transactions) that still needs to be shown so downstream jobs' inputs are visible",
        "distractors": [
            "A scheduled job that runs every night at midnight",
            "An error state indicating a failed database connection",
            "A deprecated feature kept only for backward compatibility",
        ],
        "explanation": """<p>The app runs many background jobs automatically: downloading price data, training ML models, scanning for signals, running anomaly detection. These jobs do not run independently — they depend on each other in a specific order. The ML model needs price data to already be downloaded. The Bubble Radar scan needs the overnight quant scan's scores to already exist. If any job in this chain fails silently, everything downstream produces stale or empty results.</p>
<p>The <strong>Workflow Monitor</strong> (found in Settings → Workflow Monitor) makes this entire dependency chain visible as a flow-chart. Each job is a box connected to the jobs that must run before it — you can see at a glance which job feeds which. Each box is colour-coded by its current health, letting you immediately spot where in the pipeline something has gone wrong without trawling through log files.</p>
<p>Not every box on the graph is a scheduled job. Some data originates outside the scheduler entirely — an external source like Yahoo Finance, or a manual process like hand-entering Built-in Account transactions — and those still need to be shown so you can see where a downstream job's input actually comes from. <strong>Manual Account Entry</strong> feeds the <strong>Trading</strong>, <strong>Pension</strong>, and <strong>House</strong> Account boxes, which in turn feed the same downstream jobs as Ghostfolio Sync (for Trading) or the per-account <strong>Account Price Scraper</strong> job (for Pension/House).</p>""",
    },
    {
        "term_key": "traffic-light-status",
        "section_id": "workflow-monitor",
        "term_title": "Traffic-Light Status",
        "question": "What does a Red status mean for a job box on the Workflow Monitor?",
        "answer": "The job failed on its last attempt, or is significantly overdue beyond its normal schedule",
        "distractors": [
            "The job is disabled in Settings",
            "The job is a manual, non-scheduled process",
            "The job ran successfully but slightly ahead of schedule",
        ],
        "explanation": """<p>The colour of each job box in the Workflow Monitor tells you whether that job is running on schedule, and whether its last run succeeded. The system computes "staleness" by comparing when the job last ran to how frequently it is scheduled — if a job runs daily at 18:00 and it is now 20:00 with no record of it running today, it is flagged as overdue.</p>""",
    },
    {
        "term_key": "conflict-detection",
        "section_id": "workflow-monitor",
        "term_title": "Conflict Detection",
        "question": "What is 'backwards ordering' in the Workflow Monitor's conflict detection?",
        "answer": "A job scheduled to run before the upstream job that produces the data it depends on",
        "distractors": [
            "Two jobs scheduled to run at the exact same second",
            "A job that has never been run since installation",
            "A job whose output format has changed since the last release",
        ],
        "explanation": """<p>The scheduler runs multiple jobs concurrently rather than waiting for one to finish before starting the next. This is efficient — overnight there are 20+ jobs to run, and sequential execution would take many hours. But it creates a risk: a downstream job can fire while its upstream data source is still being generated, and silently read incomplete data.</p>
<p>The conflict engine continuously checks for four dangerous situations:</p>
<p>When conflicts are detected, they are shown on the Workflow Monitor with specific labels on the affected job boxes, allowing you to resolve scheduling gaps without reading the application logs.</p>""",
    },
    {
        "term_key": "yahoo-finance-api-usage-detail",
        "section_id": "workflow-monitor",
        "term_title": "Yahoo Finance API Usage Detail",
        "question": "What does an amber circle marker on the Yahoo Finance API Usage chart indicate?",
        "answer": "The yfinance library itself logged an ERROR without the app's own request actually failing, so it doesn't count toward the Errors column",
        "distractors": [
            "A rate-limit (HTTP 429) response was received",
            "The request succeeded with no issues at all",
            "The request was made by a scheduled job rather than manual browsing",
        ],
        "explanation": """<p>The Yahoo Finance API Usage panel in Settings → System Diagnostics shows one row per day (total calls, IPv4/IPv6 split, rate-limit hits, errors, and a separate <strong>yfinance-logged</strong> count). Clicking a row opens a chart in a new tab breaking that day's requests into 15-minute intervals, stacked by which scheduled job was running at the time (or <strong>Manual / On-Demand</strong> for requests triggered by browsing a page rather than a background job). Rate-limited (HTTP 429) or errored requests are marked with a red triangle on the bar for that interval; intervals where the yfinance library itself logged an ERROR (e.g. a ticker with no data for the requested range, a 404 on a quoteSummary module Yahoo doesn't support for that instrument) without our own request actually failing get a separate amber circle marker — these never raise an exception, so they don't count toward the Errors column, but they do explain ERROR-level "yfinance" lines seen in the Log Viewer that the Errors column doesn't otherwise account for. Detail is kept for the past 8 days, matching the summary table's window.</p>""",
    },
    # --- notification-routing ---
    {
        "term_key": "notification-settings-term",
        "section_id": "notification-routing",
        "term_title": "Notification Settings",
        "question": "What does the Notification Settings panel let you control independently for each event type?",
        "answer": "Whether it goes to the log file, the in-app bell icon, and/or a Nextcloud Talk message",
        "distractors": [
            "Only whether the event is logged, with no other channel options",
            "The exact wording of every notification message",
            "Which user account receives the notification",
        ],
        "explanation": """<p>The app generates many types of notifications: a job completed successfully, a job failed, an anomaly was detected, a treasury auction looked weak, a dip radar alert fired. Without organisation, these would flood any single channel and important signals would be buried in routine status messages.</p>
<p>The <strong>Notification Settings</strong> panel (in Settings → Notification Settings) gives you granular control over each individual notification type: you choose independently whether each event goes to the log file, appears in the in-app bell icon, and/or is sent as a Nextcloud Talk message. This means critical alerts (a crash detection, a forensic accounting red flag) can be sent directly to your phone via Nextcloud while routine job status messages stay in the log only.</p>""",
    },
    {
        "term_key": "channels-notification",
        "section_id": "notification-routing",
        "term_title": "Channels",
        "question": "Which notification channel is described as the 'act now' channel?",
        "answer": "Nextcloud Talk, since messages arrive on your phone immediately",
        "distractors": [
            "The log file, since it's checked most frequently",
            "The in-app notification centre bell icon",
            "Email, since it's the most universally supported channel",
        ],
        "explanation": """<p>There are three independent delivery destinations, each togglable per notification source:</p>
<p>The Fear &amp; Greed chart is a special case: it sends a file attachment to Nextcloud Talk via its own upload path, controlled by its own separate enable toggle. The Notification Settings panel handles its log/in-app routing for status events (started, completed, failed).</p>""",
    },
    {
        "term_key": "job-status-vs-alert-sources",
        "section_id": "notification-routing",
        "term_title": "Job Status vs. Alert Sources",
        "question": "What distinguishes 'alert sources' from 'job status events'?",
        "answer": "Alert sources are market-signal events carrying actionable information, appropriate for real-time push delivery",
        "distractors": [
            "Job status events are always higher priority than alert sources",
            "Alert sources only apply to scheduled jobs that run overnight",
            "Job status events cannot be routed to the in-app notification centre",
        ],
        "explanation": """<p>Not all notifications are the same type. The system distinguishes between two categories:</p>
<p><strong>Job status events</strong> are system-level: "Quant scan started", "ML training completed in 3m 42s", "Sentiment scan failed with error." These are operational messages useful for monitoring that the automation is working correctly. They are generally low-urgency and appropriate for the log and in-app channels only.</p>
<p><strong>Alert sources</strong> are market-signal events: "Crash alert: AAPL down 8% intraday", "Dip Radar: NVDA score 87/100", "Anomaly detected: TSLA score 0.84", "Macro yield threat: US 10Y +15bp in 24h." These carry potential actionable information and are appropriate for Nextcloud Talk push delivery where real-time awareness matters.</p>
<p>Because a single scheduled job can produce multiple distinct alert types (the intraday orchestrator produces crash, moonshot, dip radar, anomaly, and macro yield alerts — each a different signal with different urgency), each alert type has its own routing row in the Notification Settings panel. This lets you, for example, push dip radar and crash alerts to your phone while keeping moonshot alerts (lower urgency for your strategy) in-app only.</p>""",
    },
    {
        "term_key": "etf-crash-alert-benchmark-resolution",
        "section_id": "notification-routing",
        "term_title": "ETF Crash-Alert Benchmark Resolution",
        "question": "How does the system pick a benchmark for a crashing ETF's Crash Alert context report?",
        "answer": "It looks at the ETF's top-10 holdings, finds the dominant exchange by weight, and compares against that exchange's headline index",
        "distractors": [
            "It always compares every ETF against the S&P 500 regardless of holdings",
            "It requires the operator to manually assign a benchmark for every ETF",
            "It compares the ETF only against its own 200-day moving average",
        ],
        "explanation": """<p>A Crash Alert's context report compares the crashing ticker's move against a broader-market benchmark, so you can tell an isolated company issue from a market-wide sell-off. For an ordinary stock this is always the S&amp;P 500 — but a globally-diversified ETF may not track the US market at all (e.g. an Asia-Pacific ex-Japan ETF), so comparing it to the S&amp;P 500 would be misleading.</p>
<p>For ETFs, the system instead looks at the ETF's cached top-10 holdings, works out which stock exchange dominates them by weight, and compares against that exchange's own headline index from the <a href="/markets">Markets</a> page's index registry (e.g. Nikkei 225 for a Japan-heavy fund, FTSE 100 for a UK-heavy one). Exchange detection reuses the app's existing exchange registry (the same one behind market hours/timezones), so adding a new exchange's index to the <a href="/markets">Markets</a> page (e.g. KOSPI for Korea) makes it available as a crash-alert benchmark automatically — no separate configuration needed. If no single exchange clearly dominates the holdings, or the dominant one has no index on the Markets page yet, the benchmark comparison is left out of the alert entirely rather than showing a misleading one.</p>
<p>Once a benchmark is identified, the figure shown prefers a live intraday move; if that market is currently closed (e.g. a European evening alert referencing an already-closed Asian index) or hasn't traded long enough yet today, it falls back to that index's last completed session's change — the same figure shown on its <a href="/markets">Markets</a> page tile — labelled "last session" so it's clear the number isn't live right now. The comparison is only left out entirely when the benchmark itself has never been resolved (see above), not merely because its market happens to be shut at alert time.</p>""",
    },
    # --- methodology ---
    {
        "term_key": "quantamental-dual-lens",
        "section_id": "methodology",
        "term_title": "Quantamental \"Dual-Lens\" Architecture",
        "question": "In the Quantamental approach, what role does technical analysis play?",
        "answer": "It decides WHEN to buy, timing entries to coincide with momentum confirmation or a regime signal",
        "distractors": [
            "It decides WHAT to buy, based on balance sheet health",
            "It replaces fundamental analysis entirely",
            "It is used only for tax-loss harvesting decisions",
        ],
        "explanation": """<p>Most investment frameworks are either purely fundamental (Warren Buffett: buy great businesses at fair prices, hold forever) or purely technical (trend-following algorithms: buy what's rising, sell what's falling). Each approach has real weaknesses that the other compensates for.</p>
<p>Pure fundamental analysis answers "what to buy" with precision — but can be early by years. A fundamentally cheap stock can remain cheap for a very long time if market sentiment is against it. Investors who bought excellent businesses in 2007 at seemingly cheap prices were down 40–60% before the thesis proved correct in 2010. The entry timing was wrong, even though the fundamental analysis was right.</p>
<p>Pure technical analysis answers "when to buy" with reasonable timing signals — but buys blindly on momentum, with no check on whether the underlying business is actually healthy. It will buy a stock going up regardless of whether the valuation makes any sense, and exit just because a line on a chart was breached — no matter the fundamental reason.</p>
<p>The <strong>Quantamental</strong> approach combines both lenses: <strong>Fundamentals decide WHAT to buy</strong> (only stocks with healthy balance sheets, improving earnings, and reasonable valuations pass through), and <strong>Technical analysis decides WHEN to buy</strong> (the entry is timed to coincide with momentum confirmation, a volume pattern, a regime signal). The goal is to own the right businesses at the right times with quantified risk controls — rather than relying on either gut feel or pure automation.</p>""",
    },
    {
        "term_key": "hierarchical-signal-purity",
        "section_id": "methodology",
        "term_title": "Hierarchical Signal Purity",
        "question": "When a 2-candle and a 3-candle pattern both appear on the same candles, which takes priority?",
        "answer": "The 3-candle pattern always overrides the 2-candle pattern",
        "distractors": [
            "The pattern that appeared most recently in the app's history",
            "The 2-candle pattern, since it forms faster",
            "Whichever pattern has the higher associated accuracy score that week",
        ],
        "explanation": """<p>When multiple candlestick patterns appear on the same set of candles, which one takes priority? This question matters because different patterns can imply contradictory signals — a two-candle Bullish Harami might appear within a three-candle Morning Star. If both signals are displayed, the system is giving conflicting advice on the same price data.</p>
<p>The <strong>Hierarchical Signal Purity</strong> rule resolves this with a simple priority: <strong>3-candle patterns always override 2-candle patterns</strong>. A Morning Star takes precedence over a Bullish Harami Cross that happens to form within it. The 3-candle pattern provides more information (it requires three specific candles in sequence), is statistically rarer, and is generally considered more reliable. Showing both would be redundant and potentially confusing.</p>
<p>This is one of many places where the system makes explicit design choices about signal conflicts — documenting them here so users understand why they see one signal label rather than multiple. Fewer, higher-quality signals are better than many overlapping signals that cancel each other out.</p>""",
    },
    {
        "term_key": "finbert-nlp-sentiment-engine",
        "section_id": "methodology",
        "term_title": "FinBERT NLP Sentiment Engine",
        "question": "Why are UK Gilt and US Treasury yield headlines excluded from FinBERT sentiment scoring?",
        "answer": "Words like 'surging yields' read as positive language but are economically toxic for equities, which would create false positive sentiment scores",
        "distractors": [
            "Yield data changes too infrequently to score meaningfully",
            "FinBERT cannot process any numerical content in headlines",
            "Yield headlines are not published in English",
        ],
        "explanation": """<p><strong>Natural Language Processing (NLP)</strong> is the branch of artificial intelligence concerned with understanding and processing human language. For financial applications, it is used to automatically read news articles, earnings call transcripts, analyst reports, and social media posts, and extract a sense of whether the content is positive, negative, or neutral about a company or the market.</p>
<p><strong>FinBERT</strong> is a version of Google's BERT language model (Bidirectional Encoder Representations from Transformers) that has been specifically fine-tuned on financial text — earnings call transcripts, financial news, Reuters articles — to understand financial language better than a general-purpose model. It was developed by ProsusAI and is available open-source through HuggingFace. Because it was trained on financial language, it correctly understands financial context: "the company beat expectations" is positive even though "beat" normally has physical connotations, and "the stock fell on strong results" correctly interprets the fall as negative despite the "strong results" positive phrase nearby.</p>
<p>The raw output is a classification (Positive / Negative / Neutral) with a confidence score. This app maps these into five labelled sentiment states for display: <strong>Euphoria</strong>, <strong>Bullish</strong>, <strong>Neutral</strong>, <strong>Bearish</strong>, <strong>Extreme Fear</strong>.</p>
<p>The model runs entirely locally on the server — no data is sent to an external API. It processes news articles in batches nightly, truncating text to the 512-token BERT input limit (roughly 380 words). For very long articles, the sentiment reflects the first 380 words, which is typically the most informative section (headline, key announcement) rather than boilerplate legal language at the end.</p>""",
    },
    {
        "term_key": "glossary-learning-spaced-repetition",
        "section_id": "methodology",
        "term_title": "Glossary Learning (Spaced Repetition)",
        "question": "What determines whether a Glossary Learning card is shown as multiple-choice or a self-graded flip card?",
        "answer": "Its current Leitner box — box 1-2 uses multiple-choice, box 3 and above switches to flip-card recall",
        "distractors": [
            "Whichever mode the operator manually selects at the start of each session",
            "Cards are always shown as multiple-choice regardless of progress",
            "The mode alternates randomly each time the card is due",
        ],
        "explanation": """<p>Reading a glossary entry once rarely makes it stick. <strong>Glossary Learning</strong> (the 🎓 Learn button next to the Glossary page header) turns every term-box in this glossary into a study card, and schedules reviews using a <strong>Leitner box</strong> system — one of the oldest and most studied spaced-repetition methods.</p>
<p>Every term starts in Box 1. Answer correctly and it moves up a box, with the review interval lengthening each time (1 day, then 3, 7, 14, and finally 30 days). Answer incorrectly and it drops straight back to Box 1, regardless of how far it had progressed. New and weaker terms (Box 1–2) are tested with multiple-choice questions; once a term reaches Box 3 it switches to a self-graded flip card ("Didn't know" / "Fuzzy" / "Knew it") — a harder, active-recall exercise reserved for terms you already have some grip on.</p>
<p>Terms are organised into levels matching the glossary's own sections, starting with Market Fundamentals and ending with System Methodology. A level unlocks once you've studied at least 80% of the terms in the level before it, so the course builds up from foundational concepts before introducing the more advanced engines.</p>""",
    },
]

window.PositionSizing = (function () {
    "use strict";

    function calculate(params) {
        const {
            accountValue,
            entryPrice,
            atrPct,
            fxRateToBase = 1.0,
            riskPct = 1.0,
            stopMultiple = 2.0,
        } = params;

        const nullResult = {
            shares: null,
            positionValue: null,
            stopPrice: null,
            riskAmount: null,
            riskPerShareBase: null,
            riskPerShareNative: null,
        };

        if (atrPct == null || atrPct <= 0) return nullResult;
        if (entryPrice == null || entryPrice <= 0) return nullResult;
        if (accountValue == null || accountValue <= 0) return nullResult;
        if (fxRateToBase == null || fxRateToBase <= 0) return nullResult;

        const riskFraction         = riskPct / 100.0;
        const riskCapitalBase      = accountValue * riskFraction;
        const riskPerShareNative   = entryPrice * atrPct * stopMultiple;
        const riskPerShareBase     = riskPerShareNative * fxRateToBase;

        if (riskPerShareBase <= 0) return nullResult;

        const shares             = Math.floor(riskCapitalBase / riskPerShareBase);
        const stopPriceNative    = entryPrice - (entryPrice * atrPct * stopMultiple);
        const positionValueBase  = shares * entryPrice * fxRateToBase;
        const actualRiskBase     = shares * riskPerShareBase;

        return {
            shares: shares,
            positionValue: positionValueBase,
            stopPrice: stopPriceNative,
            riskAmount: actualRiskBase,
            riskPerShareBase: riskPerShareBase,
            riskPerShareNative: riskPerShareNative,
        };
    }

function formatCurrency(amount, currencyCode, locale) {
        if (amount == null || isNaN(amount)) return "—";
        
        if (currencyCode === "GBp") {
            amount = amount / 100.0;
            currencyCode = "GBP";
        }
        
        if (!locale) {
            locale = ({
                "GBP": "en-GB",
                "USD": "en-US",
                "EUR": "de-DE",
                "JPY": "ja-JP",
            })[currencyCode] || "en-GB";
        }
        try {
            return new Intl.NumberFormat(locale, {
                style: "currency",
                currency: currencyCode,
                maximumFractionDigits: 2,
            }).format(amount);
        } catch (e) {
            return currencyCode + " " + amount.toFixed(2);
        }
    }

    function calculateForRow(entryPriceNative, atrPct, currencyNative) {
        const cfg = window.POSITION_SIZING_CONFIG || {};
        const fxRates = window.FX_RATES || {};
        const fxRate = fxRates[currencyNative] != null ? fxRates[currencyNative] : null;

        return calculate({
            accountValue:  cfg.ACCOUNT_VALUE,
            entryPrice:    entryPriceNative,
            atrPct:        atrPct,
            fxRateToBase:  fxRate,
            riskPct:       cfg.RISK_PCT,
            stopMultiple:  cfg.STOP_MULTIPLE,
        });
    }

    return {
        calculate: calculate,
        calculateForRow: calculateForRow,
        formatCurrency: formatCurrency,
    };
})();
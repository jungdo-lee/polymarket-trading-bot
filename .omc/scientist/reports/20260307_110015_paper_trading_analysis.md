# Paper Trading Analysis Report
Generated: 2026-03-07 11:00:15

## Executive Summary

Six trades were executed across ensemble and arbitrage strategies with $9.21 of total capital deployed
(15.7% capital utilization). The sole closed trade (Trade #3) suffered a -38.3% loss ($1.41),
far exceeding the configured 5% stop-loss - indicating the stop-loss did not trigger correctly.
Open positions show net unrealized gain of +$4.12 (sell-slippage adjusted), driven entirely by the
YES leg of the arbitrage pair which is up 94%.

---

## Data Overview

- **Initial Bankroll**: $100.00
- **Current Bankroll**: $82.87
- **Total Trades**: 6 (5 open, 1 closed)
- **Strategies**: ensemble (4 trades), arbitrage (2 trades)
- **Slippage Model**: 2% on BUY fills; sell slippage 2% applied to unrealized value
- **Win/Loss**: 0 wins, 1 loss (open positions not counted)

---

## Per-Trade Breakdown

| # | Status | Strategy | Fill Price | Orig Quote | Size | Cost | Curr Px | Unr PnL (sell-slip) | PnL% |
|---|--------|----------|-----------|-----------|------|------|---------|----------------------|------|
| 1 | OPEN | ensemble | 0.69000 | 0.67647 | 3.4946 | $2.41 | 0.69500 | -$0.03 | -1.29% |
| 2 | OPEN | ensemble | 0.08700 | 0.08529 | 12.4179 | $1.08 | 0.08700 | -$0.02 | -2.00% |
| 3 | CLOSED | ensemble | 0.81050 | 0.79461 | 4.5566 | $3.69 | 0.50000 | -$1.41 (realized) | -38.31% |
| 4 | OPEN | ensemble | 0.62500 | 0.61275 | 4.8325 | $3.02 | 0.62500 | -$0.06 | -2.00% |
| 5 | OPEN | arbitrage | 0.50000 | 0.49020 | 9.2073 | $4.60 | 0.99000 | +$4.33 | +94.04% |
| 6 | OPEN | arbitrage | 0.01000 | 0.00980 | 460.37 | $4.60 | 0.01000 | -$0.09 | -2.00% |

---

## Key Findings

### Finding 1: Stop-Loss Failure on Trade #3

**Loss**: -38.31% ($1.41 on $3.69 deployed)
**Configured stop-loss**: 5% (max allowed loss: $0.18)
**Actual loss vs threshold**: $1.41 vs $0.18 - stop-loss missed by 7.7x

The position was bought at fill price $0.8105 (original quote $0.7946) and closed at $0.50/share.
The stop-loss either was not wired to an active monitor, or the price gapped through the level with
no liquidity to execute the exit at -5%.

### Finding 2: Arbitrage Pair is NOT a True Balanced Arb

YES quote: $0.4902 + NO quote: $0.0098 = **$0.5000** (sum of original quotes)
YES fill:  $0.5000 + NO fill:  $0.0100 = **$0.5100** (sum of fill prices)

Both sums are well below $1.00, confirming this IS a market-level arbitrage opportunity.

However, the position sizes are radically different:
- YES: 9.21 shares @ $0.50 = $4.60 deployed
- NO:  460.37 shares @ $0.01 = $4.60 deployed

**Payoff if YES resolves**: 9.21 * $1.00 = $9.21  ->  profit = $0.00 (breakeven)
**Payoff if NO resolves**: 460.37 * $1.00 = $460.37  ->  profit = +$451.16

This is NOT a balanced hedge. The market implies YES ~49%, NO ~0.98%.
The NO leg is a deeply out-of-the-money cheap hedge, not a symmetric arb.

### Finding 3: Unrealized PnL System Discrepancy

- System reported unrealized PnL: +$4.53
- Recalculated WITHOUT sell slippage: +$4.53 (matches)
- Recalculated WITH 2% sell slippage: +$4.12

The system does not apply sell slippage when computing unrealized PnL.
True exit value is $0.41 lower than reported.

### Finding 4: Ensemble Trades All Sitting at Zero or Negative

Trades #1, #2, #4 have current price = entry fill price, so they sit at exactly -2.00% unrealized
(because buy slippage was paid but no price movement has occurred yet).
Trade #1 has a tiny +0.5 cent favorable move but net -1.29% after slippage.

---

## Portfolio Metrics

| Metric | Value |
|--------|-------|
| Initial bankroll | $100.00 |
| Current bankroll | $82.87 |
| Bankroll change | -$17.13 (-17.13%) |
| Total capital deployed | $19.41 |
| Capital in open positions | $15.72 |
| Capital utilization | 15.72% |
| Realized PnL | -$1.41 |
| Unrealized PnL (sell-slip adj.) | +$4.12 |
| Unrealized PnL (no sell-slip) | +$4.53 |
| Total PnL (sell-slip adj.) | +$2.71 |
| Total PnL (no sell-slip, system) | +$3.11 |
| Win rate | 0.0% |
| Avg EV across trades | 0.2042 |

**Note on bankroll discrepancy**: Bankroll dropped $17.13 but total PnL is only +$3.11.
This is because $15.72 of capital is locked in open positions and counted as spent,
reducing reported current bankroll even though those positions are not yet resolved.

---

## Limitations

- Only 6 trades and 1 closed position - insufficient sample for performance conclusions
- Stop-loss failure on Trade #3 is a critical infrastructure bug that must be fixed
- Arbitrage leg sizes are unbalanced; strategy logic should be reviewed
- No timestamp-to-datetime conversion done; trade timing analysis not performed
- EV values are strategy model outputs and not independently verified
- Current prices for open trades may not reflect real-time market state

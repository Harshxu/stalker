# STALKER — What It Is & How It Works
*A plain-English guide for everyone*

---

## What is STALKER?

STALKER is an automated system that **wakes up every morning before the market opens**, scans 188 Indian stocks, figures out which ones are worth buying today, and emails you the answer.

Think of it as a really fast, emotional-less analyst that does 6 hours of research in 90 seconds — every single day.

---

## The Big Idea

Most people lose money in the stock market because they:
- Buy when they *feel* excited (usually when prices are already high)
- Sell when they *feel* scared (usually when prices are about to recover)

STALKER removes feelings from the equation. It only buys when **numbers say it's safe**. It sits out when the market is dangerous.

> **"The most important job is not making money. It's not losing money."**

---

## How It Decides (7 Steps)

Imagine 188 stocks standing in a queue. STALKER runs them through 7 checkpoints:

### Step 1 — Safety Check 🛡️
*"Is this stock safe to even look at?"*

Instantly rejects stocks that:
- Trade too little volume (hard to sell when you want to)
- Gapped up huge overnight (price already ran, too late to buy)
- Are in a clear downtrend (never buy a falling stock)
- Have incomplete or suspicious data

**~180 stocks get rejected here.**

---

### Step 2 — Market Mood 🌤️ / 🌩️
*"What kind of market day is it?"*

STALKER classifies the whole market into one of 8 moods — from `Bull_Trend` (everything going up) to `Bear_Panic` (everything crashing).

- **Bull mood** → buying is allowed, take setups
- **Bear mood** → buying is blocked, only watchlist (protect cash)

This is the most important decision. A great stock in a bad market is still a bad trade.

---

### Step 3 — Buyer vs Seller Balance 💹
*"Who's winning today — buyers or sellers?"*

Every morning, STALKER measures 5 signals:

| Signal | What it checks |
|--------|---------------|
| **India VIX** | Is the market scared? (high VIX = fear) |
| **Advance/Decline** | How many stocks are up vs down right now |
| **Buying Pressure** | Did stocks close near their high or low today |
| **Money Flow** | Is money flowing in or out of stocks |
| **Volume** | Are traders actually participating or sitting out |

These combine into a **Pulse Score (0–100):**
- 🟢 65+ = buyers in control → entries allowed
- 🟡 55–65 = slight buyer edge → be careful
- ⚪ 45–55 = nobody winning → wait
- 🟠 35–45 = sellers slightly ahead → no new buys
- 🔴 < 35 = sellers dominating → **all BUY signals blocked**

---

### Step 4 — Stock Scoring 🏆
*"Of the survivors, which are actually good?"*

Each stock gets scored by 4 independent models:

- **Momentum** — Is it trending up? Is it stronger than Nifty?
- **Quality** — Is the company actually profitable and growing?
- **Institutional** — Are big funds buying this stock (volume patterns)?
- **Catalyst** — Any upcoming earnings surprise or sector tailwind?

Each model gives a score. They're combined into one **Alpha Score (0–100)**.

In a Bear market, a stock needs **80+ score** to even be considered. In a Bull market, 70+ is enough.

---

### Step 4.5 — Elite Qualification Layer 🥇 (Validation)
*"Is this stock actually a market leader?"*

To prevent buying short-term spikes, survivors are run through the **Elite Qualification Layer**:

1. **Minervini Trend Template** (8 conditions checking long-term trend health). Must pass at least 6/8 conditions to survive.
2. **VCP Detection** (Volatility Contraction Pattern checking for low-risk base consolidation). Grade varies from *None*, *Weak*, *Strong*, to *Elite*.
3. **Leadership Score** (combines Sector/Industry ranks, Institutional activity, and a **Stability Score**). Stability measures what percentage of the last 60 days the stock was in a healthy trend structure.
4. **Multiplier Model** — Multiplies Alpha by VCP Multiplier (1.00x - 1.07x) and Leadership Multiplier (0.95x - 1.05x). This preserves original Alpha ranking while giving true leaders the final priority.

This layer feeds a **Feature Attribution Database** to track how each factor contributes to real trade outcomes (3d, 5d, 10d, 20d returns).

---

### Step 5 — Reality Check 🔍
*"Looks good on paper — but can we actually trade it?"*

Some stocks score well but are impossible to trade cleanly:
- Earnings announcement in 2 days? → Too risky, skip
- Price already gapped 6% at open? → Too late, skip
- Stock at its 52-week high circuit? → No room to exit, skip

---

### Step 6 — Risk Sizing 📏
*"How much of our money should we risk?"*

STALKER tracks how much the system has lost recently. If it's been losing:

| Recent losses | Action |
|--------------|--------|
| < 5% | Full size — all good |
| 5–10% | Half size — be careful |
| 10–15% | Quarter size — something's wrong |
| > 15% | **Stop trading** — protect capital |

---

### Step 7 — Portfolio Building 🧱
*"Do these stocks work well TOGETHER?"*

Even good individual stocks can be bad together. STALKER checks:
- Not too many from the same sector (max 2)
- Not too correlated (if one falls, they all fall)
- Total risk stays under 6% of capital

---

## What You Get Every Morning (8:30 AM)

An email with today's picks showing:

```
1. APOLLOHOSP   → WATCH | Score: 67 | Market: Bear 🐻
2. JSWSTEEL     → WATCH | Score: 43 | VIX: 15.8
...
Pulse: 63/100 (Buyers Slight) — mild buying bias today
```

- **BUY** = All 7 checkpoints passed. Confidence to enter.
- **WATCH** = Good stock, but one checkpoint said "not today." Monitor it.

---

## Daily Schedule

| Time | What happens |
|------|-------------|
| 7:00 AM | Full scan of 188 stocks |
| 8:15 AM | Prices double-checked vs live market |
| 8:30 AM | Email sent to you |
| 9:20 AM | Entry prices locked for P&L tracking |
| 3:35 PM | Closing prices recorded |
| 4:00 PM | End-of-day report emailed — how did today's picks do? |

---

## Why No Picks Some Days?

That's not a bug. That's the system working.

When the market is in `Bear_Trend` (like right now — Nifty below all EMAs, only 24% of stocks above their 50-day average), **not trading IS the right trade.**

Retail traders lose money by forcing trades in bad markets. STALKER doesn't.

---

## Files (For Developers)

| File | Does what |
|------|-----------|
| `screener.py` | Main brain — runs all 7 steps |
| `regime_engine.py` | Market mood classifier |
| `market_pulse.py` | Buyer/seller balance |
| `alpha_engine.py` | Stock scoring (4 models) |
| `leadership_engine.py` | Elite Qualification (Minervini, VCP, Stability) |
| `reality_check.py` | Execution friction checks |
| `risk_manager.py` | Position sizing |
| `portfolio_engine.py` | Portfolio construction |
| `main.py` | Daily scheduler |
| `api_server.py` | Dashboard + API |
| `backtest_engine.py` | Historical performance testing |

---

## Quick Commands

```bash
# Start the daily automation (runs forever)
python main.py --mode run

# Trigger today's scan + email right now
python send_now.py

# Test run on 5 stocks (no email sent)
python screener.py --dry-run
```

---

*STALKER v5.0 — Phase 1 Elite Architecture | June 2026*

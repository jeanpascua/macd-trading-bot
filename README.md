# macd-trading-bot

Automated MACD trading bot. Runs on my homelab server and connects to Interactive Brokers. Checks MACD(12,26,9) signals on daily bars every weekday at 3:55 PM ET and places orders automatically.

Manual trading takes too long. Emotion messes up decisions. This just follows the rules.

## Setup

**Stack:** Python (ib-insync + pandas-ta), running on the Ubuntu server

**IBKR Account:**
- Account type: Cash, Individual, IBKR Pro
- Base currency: CAD
- Products: Stocks, Forex
- Tax treaty: Canada-US (15% withholding on US dividends)

**Funding:** Started with $50 CAD. CAD-only account, bot auto-converts to USD using live forex rate from IBKR.

## How It Works

- IB Gateway runs on the Ubuntu server with Xvfb (needs a GUI to run, virtual display handles it)
- IBC handles automated login (username/password + 2FA via IBKR Mobile)
- Bot checks MACD(12,26,9) signal on daily bars each weekday at 3:55 PM ET (5 minutes before close, so the daily bar is fully formed)
- Buys on bullish crossover (`prev_delta <= 0` and `curr_delta > 0`), sells on bearish crossover
- 5% trailing stop below last close, ratchets up only
- Splits account evenly across tickers, whole shares only (Cash account limitation, no fractional)
- Auto-sells any position not in TICKERS on rebalance (orphan cleanup)
- Discord webhook notifications on run start, fills, errors, and rebalance done

## Infrastructure

- IB Gateway 10.45 on ubuntu-server (192.168.1.79)
- IBC 3.23.0 at `~/ibc` (handles automated login)
- Xvfb on display `:10`, all managed by user systemd services
- Systemd timer fires at 3:55 PM ET Mon-Fri, survives reboots
- API on port 4001
- Bot: `~/trading/macd_bot.py`, service: `macd-bot.service` triggered by `macd-bot.timer`
- Logs: `~/trading/macd-bot.log`

## Tickers

Started with SPY/QQQ/IWM but they're $300-700/share, too expensive for a small account.
Switched to **F (Ford)** and **AAL (American Airlines)** — both cheap and liquid.
Swapped AAL for **PLTR (Palantir)** after backtesting: PLTR +507% return, 1.28 Sharpe vs AAL -1.2% return, 0.26 Sharpe (2020-2024).
PLTR ran up to ~$133/share, unreachable with a ~$72 USD account. Swapped PLTR for **SOFI** (cheap, liquid, similar volatility profile).

Current tickers: **F** and **SOFI**.

When account grows to $300+ USD, switch back to SPY/QQQ/IWM.

## Status

- [x] IBKR account approved
- [x] IB Gateway + IBC + Xvfb running on Ubuntu server
- [x] Systemd timer — fires at 3:55 PM ET Mon-Fri, survives reboots
- [x] MACD(12,26,9) crossover strategy with 5% trailing stop
- [x] vectorbt backtest mirroring live bot logic
- [x] Bot deployed and all bugs fixed
- [x] First live fill confirmed
- [x] Swapped AAL for PLTR based on backtest results
- [x] Auto-sell orphan positions on rebalance
- [x] Swapped PLTR for SOFI (PLTR ran out of reach for small account)
- [x] Discord notifications wired (webhook URL pending)
- [ ] Scale account to $300+ USD, switch to SPY/QQQ/IWM

## Bugs Fixed

**May 6, 2026:**
- **Read-only mode (Error 321):** IBC 3.19.0 didn't handle "API client needs write access" dialog. Fixed by upgrading to IBC 3.23.0.
- **Blind trading blocked:** `AllowBlindTrading=no` in IBC config blocked orders when no real-time market data subscription. Set to `yes`.
- **Fractional shares (Error 10244):** `cashQty` not supported on Cash accounts. Switched to whole share count.
- **Price returning nan:** used `reqMktData` which fails after hours and without subscription. Switched to last close from historical data.
- **CAD/USD mismatch:** account value in CAD divided by USD stock price gave wrong share count. Bot now fetches live USD/CAD rate from IBKR forex and converts.

**May 7, 2026:**
- **Timezone bug:** scheduler hardcoded `13:35 UTC` which breaks when clocks fall back to EST. Replaced `schedule` lib with a `zoneinfo America/New_York` loop, always fires at 9:35 AM ET regardless of DST.
- **ZoneInfoNotFoundError:** `tzdata` package missing from lean-env. Installed it.

**May 11, 2026:**
- **Duplicate sell on orphan (Error 201):** `close_orphans()` placed sell when one already existed, rejected as short sell. Fixed: check open sells before placing.
- **Gateway data farms broken at open:** bot crash loop on market open. Fixed via gateway restart. Root cause: flaky IBKR upstream.

**May 12, 2026:**
- **Service restart loop:** `macd-bot.service` had `Restart=always` + `RestartSec=60` and no timer. Bot exited cleanly after each 4s run, systemd restarted it every minute all day. About 480 connect cycles instead of one daily run. Switched to `Type=oneshot` + `Restart=on-failure`, triggered by `macd-bot.timer` with `OnCalendar=Mon-Fri *-*-* 09:35:00 America/New_York`. Gotcha: timezone goes IN the OnCalendar expression, NOT as a separate `Timezone=` field in `[Timer]` (that field doesn't exist, silently ignored, fires at UTC).
- **Cosmetic ib_insync warning accepted:** `ERROR completed orders request timed out` appears once per run from ib_insync's internal sync. Per [ib_insync#355](https://github.com/erdewit/ib_insync/issues/355), harmless. Bot doesn't read `ib.orders()` completed history. `ib.RequestTimeout` does not control this. Living with one ERROR line per daily run.

**May 13, 2026:**
- **Strategy silent for weeks (~0 real BUY signals across 2250 runs).** Four root causes: (a) bot fired at 9:35 AM ET → daily bar still empty → MACD computed on yesterday's close, always 1 day late; (b) old `delta_pct > 0.0025` tolerance scaled by price made the threshold tighter on cheap stocks (F at $12 needed a $0.03 macd-sig gap, hard to cross consistently); (c) no trailing stop meant winners gave back gains; (d) only `delta%` was logged → undebuggable. Fixes: timer moved to 3:55 PM ET, switched to crossover signal (`prev_delta <= 0` → `curr_delta > 0`), added 5% trailing stop that ratchets up each run, raw `macd`/`sig`/`prev_delta`/`curr_delta` now logged per ticker per run.
- **Boot run at 2 AM ET (market closed):** `macd-bot.service` had `[Install] WantedBy=default.target`, so the service ran once at boot before the timer took over. Fix: removed `[Install]` section, `systemctl --user disable macd-bot.service`. Service is now `static`, fires only via timer. Rule: timer-driven oneshot services must NOT have an `[Install]` section.
- **IBKR Error 10349 (`Order TIF was set to DAY based on order preset`):** every BUY and StopOrder triggered IBKR to cancel and re-submit because `ib_insync.Order.tif` defaults to `''` and the IBKR account preset overrides it to DAY. Orders still filled, but with extra latency and noise per run. Fix: added `ORDER_TIF = 'DAY'` constant and `buy_limit()` / `sell_limit()` / `sell_stop()` helpers that set `.tif` explicitly. All `LimitOrder` / `StopOrder` call sites swapped to use the helpers.

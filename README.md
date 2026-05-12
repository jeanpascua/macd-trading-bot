# macd-trading-bot

Automated MACD trading bot. Runs on my homelab server and connects to Interactive Brokers. Checks MACD(12,26,9) signals on daily bars every weekday at 9:35 AM ET and places orders automatically.

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
- Bot checks MACD(12,26,9) signal on daily bars each weekday at 9:35 AM ET
- Buys when MACD crosses above signal line, sells when it crosses below
- Splits account evenly across tickers, whole shares only (Cash account limitation, no fractional)
- Auto-sells any position not in TICKERS on rebalance (orphan cleanup)

## Infrastructure

- IB Gateway 10.45 on ubuntu-server (192.168.1.79)
- IBC 3.23.0 at `~/ibc` (handles automated login)
- Xvfb on display `:10`, all managed by user systemd services
- Systemd timer fires at 9:35 AM ET Mon-Fri, survives reboots
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
- [x] Systemd timer — fires at 9:35 AM ET Mon-Fri, survives reboots
- [x] MACD(12,26,9) strategy (~47% return over 6 years on QuantConnect backtest)
- [x] vectorbt backtest mirroring live bot logic
- [x] Bot deployed and all bugs fixed
- [x] First live fill confirmed
- [x] Swapped AAL for PLTR based on backtest results
- [x] Auto-sell orphan positions on rebalance
- [x] Swapped PLTR for SOFI (PLTR ran out of reach for small account)
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

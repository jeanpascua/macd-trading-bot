# AI Trading Bot

## The Problem

Manually trading takes time and emotion gets in the way. Most retail traders lose money because they react instead of following a strategy. An automated bot follows rules, not feelings.

## The Idea

A fully automated trading bot that runs on the homelab server, connected to Interactive Brokers, executing a MACD strategy.

## Setup

**Stack:** Python (ib-insync + pandas-ta), running on the Ubuntu server

**IBKR Account:**
- Account type: Cash, Individual, IBKR Pro
- Base currency: CAD
- Products: Stocks, Forex
- Tax treaty: Canada-US (15% withholding on US dividends)
- Account ID: U25601790

**Funding:** $50 CAD to test. Account is CAD-only — bot auto-converts to USD using live forex rate from IBKR.

## How It Works

- IB Gateway runs on the Ubuntu server with Xvfb (virtual display — needs a GUI to run)
- IBC handles automated login (username/password + 2FA via IBKR Mobile)
- Bot checks MACD(12,26,9) signal on daily bars each weekday at 9:35 AM ET
- Buys when MACD crosses above signal line, sells when it crosses below
- Splits account evenly across tickers — whole shares only (no fractional, Cash account limitation)

## Infrastructure

- IB Gateway 10.45 on ubuntu-server (192.168.1.79)
- IBC 3.23.0 at `~/ibc` — handles automated login
- Xvfb on display `:10`, all managed by user systemd services
- API on port 4001
- Bot: `~/trading/macd_bot.py`, service: `macd-bot`
- Logs: `~/trading/macd-bot.log`

## Tickers

Started with SPY/QQQ/IWM but they're $300-700/share — too expensive for $50 CAD.
Switched to **F (Ford)** and **AAL (American Airlines)** — both under $15/share, very liquid.

When account grows to $300+ USD, switch back to SPY/QQQ/IWM.

## Status

- [x] IBKR account approved
- [x] $50 CAD deposited
- [x] IB Gateway + IBC + Xvfb running on Ubuntu server
- [x] Systemd services with auto-restart on boot
- [x] MACD(12,26,9) strategy — ~47% return over 6 years on QuantConnect backtest
- [x] Bot deployed and all bugs fixed (see below)
- [x] Test run placed real AAL order successfully (May 6, 2026)
- [x] First live fill confirmed — AAL 1 share @ $13.10, filled May 7, 2026 at 9:30 AM ET
- [x] Code on GitHub: https://github.com/jeanpascua/macd-trading-bot (private)
- [ ] Add capital once a few runs look stable

## Bugs Fixed

**May 6, 2026:**
- **Read-only mode (Error 321)** — IBC 3.19.0 didn't handle "API client needs write access" dialog. Fixed by upgrading to IBC 3.23.0.
- **Blind trading blocked** — `AllowBlindTrading=no` in IBC config blocked orders when no real-time market data subscription. Set to `yes`.
- **Fractional shares (Error 10244)** — `cashQty` not supported on Cash accounts. Switched to whole share count.
- **Price returning nan** — used `reqMktData` which fails after hours and without subscription. Switched to last close from historical data.
- **CAD/USD mismatch** — account value in CAD divided by USD stock price gave wrong share count. Bot now fetches live USD/CAD rate from IBKR forex and converts.

**May 7, 2026:**
- **Timezone bug** — scheduler hardcoded `13:35 UTC` which breaks when clocks fall back to EST. Replaced `schedule` lib with a `zoneinfo America/New_York` loop — always fires at 9:35 AM ET regardless of DST.
- **ZoneInfoNotFoundError** — `tzdata` package missing from lean-env. Installed it.

## Next Steps

- Monitor logs after 9:35 AM ET each weekday
- Once first trade fills cleanly → add $200-500 CAD
- At scale → switch back to SPY/QQQ/IWM (need ~$300 USD per position minimum)

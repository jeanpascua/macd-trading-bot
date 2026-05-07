from ib_insync import IB, Stock, Forex, MarketOrder, util
import pandas_ta as ta
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('/home/jean/trading/macd-bot.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TICKERS   = ['F', 'AAL']
TOLERANCE = 0.0025
IB_HOST   = '127.0.0.1'
IB_PORT   = 4001
CLIENT_ID = 2
ET        = ZoneInfo('America/New_York')


def connect():
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
    log.info("Connected to IB Gateway")
    return ib


def get_macd(ib, ticker):
    contract = Stock(ticker, 'SMART', 'USD')
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='90 D',
        barSizeSetting='1 day',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=1
    )
    df = util.df(bars)
    if df is None or len(df) < 35:
        log.warning(f"{ticker}: not enough data")
        return None

    macd_df   = ta.macd(df['close'], fast=12, slow=26, signal=9)
    latest    = macd_df.iloc[-1]
    macd_val  = latest['MACD_12_26_9']
    sig_val   = latest['MACDs_12_26_9']
    fast_ema  = df['close'].ewm(span=12, adjust=False).mean().iloc[-1]
    last_close = float(df['close'].iloc[-1])

    return macd_val, sig_val, fast_ema, last_close


def get_position(ib, ticker):
    for pos in ib.positions():
        if pos.contract.symbol == ticker:
            return pos.position
    return 0


def get_usdcad_rate(ib):
    bars = ib.reqHistoricalData(
        Forex('USDCAD'),
        endDateTime='', durationStr='2 D', barSizeSetting='1 day',
        whatToShow='MIDPOINT', useRTH=False, formatDate=1
    )
    if bars:
        return float(bars[-1].close)
    return 1.38  # fallback


def get_account_usd(ib):
    vals = {(av.tag, av.currency): av.value for av in ib.accountValues()}
    for tag in ('TotalCashValue', 'AvailableFunds', 'NetLiquidation'):
        v = float(vals.get((tag, 'USD'), 0))
        if v > 0:
            return v
    # CAD account — convert to USD
    for tag in ('TotalCashValue', 'AvailableFunds', 'NetLiquidation'):
        v = float(vals.get((tag, 'CAD'), 0))
        if v > 0:
            rate = get_usdcad_rate(ib)
            usd = v / rate
            log.info(f"Account: {v:.2f} CAD @ {rate:.4f} = {usd:.2f} USD")
            return usd
    return 0


def rebalance(ib):
    log.info("--- Rebalance start ---")
    account_val = get_account_usd(ib)
    log.info(f"Account value: {account_val:.2f}")

    n = len(TICKERS)

    for ticker in TICKERS:
        try:
            result = get_macd(ib, ticker)
            if result is None:
                continue

            macd_val, sig_val, fast_ema, last_close = result
            delta_pct = (macd_val - sig_val) / fast_ema
            quantity  = get_position(ib, ticker)

            log.info(f"{ticker}: delta%={delta_pct:.4f} pos={quantity} price={last_close:.2f}")

            contract = Stock(ticker, 'SMART', 'USD')

            if quantity <= 0 and delta_pct > TOLERANCE:
                cash_to_deploy = account_val / n
                shares = int(cash_to_deploy / last_close)
                if shares < 1:
                    log.warning(f"{ticker}: not enough cash for 1 share (${cash_to_deploy:.2f} / {last_close:.2f}), skipping")
                    continue
                order = MarketOrder('BUY', shares)
                ib.placeOrder(contract, order)
                log.info(f"{ticker}: BUY {shares} shares @ ~{last_close:.2f} (${shares*last_close:.2f})")

            elif quantity > 0 and delta_pct < -TOLERANCE:
                order = MarketOrder('SELL', quantity)
                ib.placeOrder(contract, order)
                log.info(f"{ticker}: SELL {quantity} shares")

        except Exception as e:
            log.error(f"{ticker}: {e}")

    log.info("--- Rebalance done ---")


def run_job():
    ib = None
    try:
        ib = connect()
        rebalance(ib)
    except Exception as e:
        log.error(f"Job failed: {e}")
    finally:
        if ib and ib.isConnected():
            ib.disconnect()


def next_run_time():
    now = datetime.now(ET)
    target = now.replace(hour=9, minute=35, second=0, microsecond=0)
    if now.weekday() < 5 and now < target:
        return target
    days = 1
    while True:
        candidate = (now + timedelta(days=days)).replace(hour=9, minute=35, second=0, microsecond=0)
        if candidate.weekday() < 5:
            return candidate
        days += 1


if __name__ == '__main__':
    log.info("MACD bot started")
    while True:
        run_at = next_run_time()
        wait_secs = (run_at - datetime.now(ET)).total_seconds()
        log.info(f"Next run: {run_at.strftime('%Y-%m-%d %H:%M %Z')} (in {wait_secs/3600:.1f}h)")
        time.sleep(max(wait_secs, 0))
        run_job()
        time.sleep(60)  # prevent double-fire within same minute

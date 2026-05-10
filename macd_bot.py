from ib_insync import IB, Stock, Forex, LimitOrder, StopOrder, util
import pandas_ta as ta
import time
import logging
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

TICKERS         = ['F', 'PLTR']
TOLERANCE       = 0.0025
STOP_LOSS_PCT   = 0.05    # 5% stop below fill price
LIMIT_SLIP      = 0.01    # 1% buffer on limit orders to ensure fill
IB_HOST         = '127.0.0.1'
IB_PORT         = 4001
CLIENT_ID       = 2
ET              = ZoneInfo('America/New_York')
CONNECT_RETRIES = 5
CONNECT_DELAY   = 30      # seconds between connect attempts


def connect():
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            ib = IB()
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
            log.info("Connected to IB Gateway")
            return ib
        except Exception as e:
            log.warning(f"Connect attempt {attempt}/{CONNECT_RETRIES} failed: {e}")
            if attempt < CONNECT_RETRIES:
                time.sleep(CONNECT_DELAY)
    raise ConnectionError(f"Failed to connect after {CONNECT_RETRIES} attempts")


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

    macd_df    = ta.macd(df['close'], fast=12, slow=26, signal=9)
    latest     = macd_df.iloc[-1]
    macd_val   = latest['MACD_12_26_9']
    sig_val    = latest['MACDs_12_26_9']
    last_close = float(df['close'].iloc[-1])

    return macd_val, sig_val, last_close


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
    return 1.38


def get_account_usd(ib):
    vals = {(av.tag, av.currency): av.value for av in ib.accountValues()}
    for tag in ('TotalCashValue', 'AvailableFunds', 'NetLiquidation'):
        v = float(vals.get((tag, 'USD'), 0))
        if v > 0:
            return v
    for tag in ('TotalCashValue', 'AvailableFunds', 'NetLiquidation'):
        v = float(vals.get((tag, 'CAD'), 0))
        if v > 0:
            rate = get_usdcad_rate(ib)
            usd = v / rate
            log.info(f"Account: {v:.2f} CAD @ {rate:.4f} = {usd:.2f} USD")
            return usd
    return 0


def wait_for_fill(ib, trade, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ib.sleep(1)
        if trade.isDone():
            status = trade.orderStatus.status
            if status == 'Filled':
                log.info(f"Filled: {trade.order.action} {trade.order.totalQuantity} {trade.contract.symbol} @ {trade.orderStatus.avgFillPrice:.2f}")
                return True
            log.warning(f"Order ended unfilled: {status}")
            return False
    log.warning(f"Fill timeout after {timeout}s — cancelling: {trade.contract.symbol}")
    ib.cancelOrder(trade.order)
    return False


def cancel_open_stops(ib, ticker):
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.orderType == 'STP'
                and trade.order.action == 'SELL'):
            ib.cancelOrder(trade.order)
            log.info(f"{ticker}: cancelled open stop order")


def close_orphans(ib):
    for pos in ib.positions():
        sym = pos.contract.symbol
        if sym not in TICKERS and pos.position > 0:
            contract = Stock(sym, 'SMART', 'USD')
            bars = ib.reqHistoricalData(
                contract, endDateTime='', durationStr='2 D',
                barSizeSetting='1 day', whatToShow='TRADES',
                useRTH=True, formatDate=1
            )
            if not bars:
                log.warning(f"{sym}: orphan position, couldn't get price — skipping")
                continue
            last_close = float(bars[-1].close)
            qty = int(pos.position)
            cancel_open_stops(ib, sym)
            limit_price = round(last_close * (1 - LIMIT_SLIP), 2)
            trade = ib.placeOrder(contract, LimitOrder('SELL', qty, limit_price))
            log.info(f"{sym}: orphan — SELL {qty} limit @ {limit_price:.2f}")
            wait_for_fill(ib, trade)


def rebalance(ib):
    log.info("--- Rebalance start ---")
    close_orphans(ib)
    n = len(TICKERS)

    for ticker in TICKERS:
        try:
            result = get_macd(ib, ticker)
            if result is None:
                continue

            macd_val, sig_val, last_close = result
            delta_pct = (macd_val - sig_val) / last_close
            quantity  = get_position(ib, ticker)

            log.info(f"{ticker}: delta%={delta_pct:.4f} pos={quantity} price={last_close:.2f}")

            contract = Stock(ticker, 'SMART', 'USD')

            if quantity <= 0 and delta_pct > TOLERANCE:
                account_val = get_account_usd(ib)
                shares = int((account_val / n) / last_close)
                if shares < 1:
                    log.warning(f"{ticker}: not enough cash (${account_val/n:.2f} / {last_close:.2f}), skipping")
                    continue

                limit_price = round(last_close * (1 + LIMIT_SLIP), 2)
                trade = ib.placeOrder(contract, LimitOrder('BUY', shares, limit_price))
                log.info(f"{ticker}: BUY {shares} limit @ {limit_price:.2f} (${shares*limit_price:.2f})")

                if wait_for_fill(ib, trade):
                    fill_price = trade.orderStatus.avgFillPrice or last_close
                    stop_price = round(fill_price * (1 - STOP_LOSS_PCT), 2)
                    ib.placeOrder(contract, StopOrder('SELL', shares, stop_price))
                    log.info(f"{ticker}: stop-loss set @ {stop_price:.2f}")

            elif quantity > 0 and delta_pct < -TOLERANCE:
                cancel_open_stops(ib, ticker)
                limit_price = round(last_close * (1 - LIMIT_SLIP), 2)
                trade = ib.placeOrder(contract, LimitOrder('SELL', quantity, limit_price))
                log.info(f"{ticker}: SELL {quantity} limit @ {limit_price:.2f}")
                wait_for_fill(ib, trade)

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


if __name__ == '__main__':
    run_job()

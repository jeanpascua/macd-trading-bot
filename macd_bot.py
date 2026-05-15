from ib_insync import IB, Stock, Forex, LimitOrder, StopLimitOrder, util
import pandas_ta as ta
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from discord_notify import notify

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('/home/jean/trading/macd-bot.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TICKERS         = ['F']
STOP_LOSS_PCT   = 0.05    # 5% trailing stop below last close
LIMIT_SLIP      = 0.01    # 1% buffer on limit orders to ensure fill
IB_HOST         = '127.0.0.1'
IB_PORT         = 4001
CLIENT_ID       = 2
ET              = ZoneInfo('America/New_York')
CONNECT_RETRIES = 5
CONNECT_DELAY   = 30      # seconds between connect attempts
ORDER_TIF       = 'DAY'   # explicit TIF to avoid IBKR preset Error 10349
STOP_TIF        = 'GTC'   # stop-loss orders persist overnight


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
    end_dt = datetime.now(ET).strftime('%Y%m%d %H:%M:%S')
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end_dt,
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
    curr       = macd_df.iloc[-1]
    prev       = macd_df.iloc[-2]
    macd_val   = float(curr['MACD_12_26_9'])
    sig_val    = float(curr['MACDs_12_26_9'])
    prev_macd  = float(prev['MACD_12_26_9'])
    prev_sig   = float(prev['MACDs_12_26_9'])
    last_close = float(df['close'].iloc[-1])

    return macd_val, sig_val, prev_macd, prev_sig, last_close


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


def buy_limit(shares, price):
    o = LimitOrder('BUY', shares, price)
    o.tif = ORDER_TIF
    return o


def sell_limit(qty, price):
    o = LimitOrder('SELL', qty, price)
    o.tif = ORDER_TIF
    return o


def sell_stop(qty, stop_price):
    limit_price = round(stop_price * (1 - LIMIT_SLIP), 2)
    o = StopLimitOrder('SELL', int(qty), stop_price, limit_price)
    o.tif = STOP_TIF
    return o


def wait_for_fill(ib, trade, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ib.sleep(1)
        if trade.isDone():
            status = trade.orderStatus.status
            if status == 'Filled':
                sym = trade.contract.symbol
                action = trade.order.action
                qty = trade.order.totalQuantity
                px = trade.orderStatus.avgFillPrice
                log.info(f"Filled: {action} {qty} {sym} @ {px:.2f}")
                notify(f"{action} {sym} x{qty} @ ${px:.2f}", level=('buy' if action == 'BUY' else 'sell'))
                return True
            log.warning(f"Order ended unfilled: {status}")
            notify(f"{trade.contract.symbol} unfilled: {status}", level='warn')
            return False
    log.warning(f"Fill timeout after {timeout}s — cancelling: {trade.contract.symbol}")
    ib.cancelOrder(trade.order)
    notify(f"{trade.contract.symbol} fill timeout, cancelled", level='warn')
    return False


def cancel_open_stops(ib, ticker):
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.orderType in ('STP', 'STP LMT')
                and trade.order.action == 'SELL'):
            ib.cancelOrder(trade.order)
            log.info(f"{ticker}: cancelled open stop order")


def close_orphans(ib):
    open_sells = {
        trade.contract.symbol
        for trade in ib.openTrades()
        if trade.order.action == 'SELL'
    }
    for pos in ib.positions():
        sym = pos.contract.symbol
        if sym not in TICKERS and pos.position > 0:
            if sym in open_sells:
                log.info(f"{sym}: orphan — open sell already exists, skipping")
                continue
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
            trade = ib.placeOrder(contract, sell_limit(qty, limit_price))
            log.info(f"{sym}: orphan — SELL {qty} limit @ {limit_price:.2f}")
            wait_for_fill(ib, trade)


def update_trailing_stop(ib, ticker, quantity, last_close):
    new_stop = round(last_close * (1 - STOP_LOSS_PCT), 2)
    contract = Stock(ticker, 'SMART', 'USD')
    existing = None
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.orderType in ('STP', 'STP LMT')
                and trade.order.action == 'SELL'):
            existing = trade
            break
    if existing is None:
        ib.placeOrder(contract, sell_stop(int(quantity), new_stop))
        log.info(f"{ticker}: missing stop, placed @ {new_stop:.2f}")
        return
    old_stop = float(existing.order.auxPrice)
    if new_stop > old_stop + 0.001:
        ib.cancelOrder(existing.order)
        ib.placeOrder(contract, sell_stop(int(quantity), new_stop))
        log.info(f"{ticker}: trailed stop {old_stop:.2f} -> {new_stop:.2f}")
    else:
        log.info(f"{ticker}: stop held @ {old_stop:.2f} (candidate {new_stop:.2f})")


def rebalance(ib):
    log.info("--- Rebalance start ---")
    close_orphans(ib)
    account_val = get_account_usd(ib)
    log.info(f"Account: ${account_val:.2f} USD")
    notify(f"Bot run — account ${account_val:.2f} USD, tickers {','.join(TICKERS)}", level='start')

    for ticker in TICKERS:
        try:
            result = get_macd(ib, ticker)
            if result is None:
                continue

            macd_val, sig_val, prev_macd, prev_sig, last_close = result
            curr_delta = macd_val - sig_val
            prev_delta = prev_macd - prev_sig
            quantity   = get_position(ib, ticker)

            log.info(
                f"{ticker}: macd={macd_val:.4f} sig={sig_val:.4f} "
                f"prev_delta={prev_delta:.4f} curr_delta={curr_delta:.4f} "
                f"pos={quantity} price={last_close:.2f}"
            )

            contract = Stock(ticker, 'SMART', 'USD')
            bullish_cross = prev_delta < 0 and curr_delta > 0
            bearish_cross = prev_delta > 0 and curr_delta < 0

            if quantity <= 0 and bullish_cross:
                available = get_account_usd(ib)
                shares = int(available / last_close)
                if shares < 1:
                    log.warning(f"{ticker}: not enough cash (${available:.2f} / {last_close:.2f}), skipping")
                    continue

                limit_price = round(last_close * (1 + LIMIT_SLIP), 2)
                trade = ib.placeOrder(contract, buy_limit(shares, limit_price))
                log.info(f"{ticker}: BUY {shares} limit @ {limit_price:.2f} (${shares*limit_price:.2f})")

                if wait_for_fill(ib, trade):
                    fill_price = trade.orderStatus.avgFillPrice or last_close
                    stop_price = round(fill_price * (1 - STOP_LOSS_PCT), 2)
                    placed = False
                    for attempt in range(3):
                        try:
                            ib.placeOrder(contract, sell_stop(shares, stop_price))
                            log.info(f"{ticker}: stop-loss set @ {stop_price:.2f}")
                            placed = True
                            break
                        except Exception as e:
                            log.warning(f"{ticker}: stop attempt {attempt+1}/3 failed: {e}")
                            ib.sleep(2)
                    if not placed:
                        log.error(f"{ticker}: stop placement failed 3x — emergency sell")
                        notify(f"{ticker} stop failed 3x, emergency sell", level='error')
                        emergency = ib.placeOrder(contract, sell_limit(shares, round(fill_price * (1 - LIMIT_SLIP), 2)))
                        wait_for_fill(ib, emergency)

            elif quantity > 0 and bearish_cross:
                cancel_open_stops(ib, ticker)
                limit_price = round(last_close * (1 - LIMIT_SLIP), 2)
                trade = ib.placeOrder(contract, sell_limit(quantity, limit_price))
                log.info(f"{ticker}: SELL {quantity} limit @ {limit_price:.2f}")
                if not wait_for_fill(ib, trade):
                    log.error(f"{ticker}: SELL unfilled — position still open, stop may be missing")
                    notify(f"{ticker} SELL unfilled — manual check needed", level='error')

            elif quantity > 0:
                update_trailing_stop(ib, ticker, quantity, last_close)

        except Exception as e:
            log.error(f"{ticker}: {e}")
            notify(f"{ticker} error: {e}", level='error')

    positions_summary = ', '.join(
        f"{p.contract.symbol} x{int(p.position)}"
        for p in ib.positions() if p.position != 0
    ) or 'none'
    log.info("--- Rebalance done ---")
    notify(f"Done. Positions: {positions_summary}", level='done')


def run_job():
    ib = None
    try:
        ib = connect()
        rebalance(ib)
    except Exception as e:
        log.error(f"Job failed: {e}")
        notify(f"Job failed: {e}", level='error')
    finally:
        if ib and ib.isConnected():
            ib.sleep(2)
            ib.disconnect()


if __name__ == '__main__':
    run_job()

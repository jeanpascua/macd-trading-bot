import vectorbt as vbt
import yfinance as yf

TICKERS       = ['F']
START         = '2020-01-01'
END           = '2024-12-31'
FAST          = 12
SLOW          = 26
SIGNAL        = 9
STOP_LOSS_PCT = 0.05

for ticker in TICKERS:
    df    = yf.download(ticker, start=START, end=END, auto_adjust=True, progress=False)
    close = df['Close'].squeeze()

    macd = vbt.MACD.run(close, fast_window=FAST, slow_window=SLOW, signal_window=SIGNAL)

    delta   = macd.macd - macd.signal
    entries = (delta.shift(1) <= 0) & (delta > 0)
    exits   = (delta.shift(1) >= 0) & (delta < 0)

    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        init_cash=10_000,
        fees=0.001,
        freq='D',
        sl_stop=STOP_LOSS_PCT,
        sl_trail=True,
    )

    print(f"\n{'='*40}")
    print(f"{ticker} ({START} → {END})")
    print(f"{'='*40}")
    print(pf.stats([
        'start_value', 'end_value', 'total_return',
        'max_dd', 'sharpe_ratio', 'total_trades',
        'win_rate', 'avg_winning_trade', 'avg_losing_trade'
    ]))

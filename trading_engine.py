"""
Motore di trading per paper trading — versione senza pandas/numpy
(compatibile con Python 32-bit, dove questi pacchetti spesso non
hanno una versione pronta all'uso e richiederebbero compilazione).

Strategia implementata (semplice ma reale, non placeholder):
- EMA9 / EMA21 crossover per la direzione del trend di brevissimo periodo
- Conferma tramite RSI (evita di comprare in overbought / vendere in oversold)
- Conferma tramite aumento di volume rispetto alla media (filtro anti falsa rottura)
- Stop Loss, Take Profit e Trailing Stop gestiti automaticamente
- Risk management: rischio massimo per trade, perdita massima giornaliera,
  numero massimo di trade/giorno, circuit breaker

Dati di mercato: REST pubblico di Binance (https://api.binance.com),
NESSUNA api key richiesta per leggere candele e prezzo pubblico.
"""
import random
import requests
from datetime import datetime, timezone

import database as db

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"

TIMEFRAME_MAP = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m"}


def fetch_klines(symbol: str, timeframe: str, limit: int = 100) -> list[dict]:
    """Scarica le ultime candele pubbliche da Binance. Nessuna auth richiesta.
    Ritorna una lista di dict (niente DataFrame, per compatibilita' senza pandas)."""
    interval = TIMEFRAME_MAP.get(timeframe, "5m")
    resp = requests.get(
        BINANCE_KLINES_URL,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    candles = []
    for row in raw:
        candles.append({
            "open_time": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return candles


def fetch_current_price(symbol: str) -> float:
    resp = requests.get(BINANCE_PRICE_URL, params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    return float(resp.json()["price"])


def _ema_series(values: list[float], span: int) -> list[float]:
    """Calcola la EMA (Exponential Moving Average) su una lista di valori, in Python puro."""
    if not values:
        return []
    k = 2 / (span + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Calcola RSI in Python puro. I primi `period` valori sono impostati a 50 (neutro)."""
    n = len(closes)
    rsi = [50.0] * n
    if n <= period:
        return rsi

    gains = [0.0]
    losses = [0.0]
    for i in range(1, n):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))

    return rsi


def compute_indicators(candles: list[dict]) -> list[dict]:
    """Arricchisce ogni candela con ema9, ema21, rsi, volume_ma. Ritorna nuova lista."""
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)
    rsi = _rsi_series(closes, 14)

    volume_ma = []
    window = 20
    for i in range(len(volumes)):
        start = max(0, i - window + 1)
        chunk = volumes[start:i + 1]
        volume_ma.append(sum(chunk) / len(chunk))

    enriched = []
    for i, c in enumerate(candles):
        new_c = dict(c)
        new_c["ema9"] = ema9[i]
        new_c["ema21"] = ema21[i]
        new_c["rsi"] = rsi[i]
        new_c["volume_ma"] = volume_ma[i]
        enriched.append(new_c)
    return enriched


def generate_signal(candles: list[dict]) -> dict:
    """
    Ritorna un segnale: 'buy', 'sell' o 'hold', con la motivazione.
    Conferma multipla richiesta (cross EMA + RSI + volume) per ridurre falsi segnali.
    """
    if len(candles) < 25:
        return {"signal": "hold", "reason": "dati insufficienti"}

    last = candles[-1]
    prev = candles[-2]

    ema_cross_up = prev["ema9"] <= prev["ema21"] and last["ema9"] > last["ema21"]
    ema_cross_down = prev["ema9"] >= prev["ema21"] and last["ema9"] < last["ema21"]

    volume_spike = last["volume"] > (last["volume_ma"] * 1.2 if last["volume_ma"] else 0)

    if ema_cross_up and last["rsi"] < 70 and volume_spike:
        return {"signal": "buy", "reason": f"EMA cross up, RSI={last['rsi']:.1f}, volume spike"}
    if ema_cross_down and last["rsi"] > 30 and volume_spike:
        return {"signal": "sell", "reason": f"EMA cross down, RSI={last['rsi']:.1f}, volume spike"}

    return {"signal": "hold", "reason": f"nessuna conferma (RSI={last['rsi']:.1f})"}


class PaperTradingAccount:
    """Gestisce il saldo simulato e l'equity, separato dal capitale reale."""

    def __init__(self, starting_balance: float = 1000.0):
        self.starting_balance = starting_balance
        self.balance = starting_balance  # cash disponibile

    def equity(self, open_trade: dict | None, current_price: float) -> float:
        if not open_trade:
            return self.balance
        qty = open_trade["quantity"]
        entry = open_trade["entry_price"]
        unrealized = (current_price - entry) * qty if open_trade["side"] == "buy" else (entry - current_price) * qty
        return self.balance + unrealized


def simulate_slippage(price: float, max_slippage_pct: float) -> tuple[float, float]:
    """
    Simula uno slippage realistico (piccola variazione casuale, max +-max_slippage_pct/2).
    Ritorna (prezzo_eseguito, slippage_pct_effettivo).
    In un sistema con exchange reale qui andrebbe la verifica contro l'order book reale.
    """
    actual_slip_pct = random.uniform(-max_slippage_pct / 2, max_slippage_pct / 2)
    executed_price = price * (1 + actual_slip_pct / 100)
    return executed_price, actual_slip_pct


class TradingEngine:
    def __init__(self):
        self.account = PaperTradingAccount(starting_balance=1000.0)
        self.peak_price_since_entry = None  # per trailing stop

    def reset_account(self, starting_balance: float):
        self.account = PaperTradingAccount(starting_balance=starting_balance)
        self.peak_price_since_entry = None

    def risk_checks_pass(self, cfg: dict) -> tuple[bool, str]:
        daily_pnl = db.get_daily_pnl()
        max_loss = -abs(cfg["max_capital"] * cfg["max_daily_loss_pct"] / 100)
        if daily_pnl <= max_loss:
            return False, "circuit_breaker: perdita massima giornaliera raggiunta"
        if db.count_trades_today() >= cfg["max_trades_per_day"]:
            return False, "limite massimo di trade giornalieri raggiunto"
        return True, "ok"

    def position_size(self, cfg: dict, entry_price: float, stop_loss_price: float) -> float:
        risk_amount = cfg["max_capital"] * cfg["risk_per_trade_pct"] / 100
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit == 0:
            return 0.0
        qty = risk_amount / risk_per_unit
        # non investire più cash di quanto disponibile
        max_qty_by_cash = self.account.balance / entry_price
        return min(qty, max_qty_by_cash)

    def tick(self):
        """Un ciclo del motore: prende dati, valuta segnali, gestisce posizione aperta."""
        cfg = db.get_config()
        if not cfg["bot_active"]:
            return

        symbol = cfg["symbol"]
        timeframe = cfg["timeframe"]

        try:
            candles = fetch_klines(symbol, timeframe, limit=100)
            candles = compute_indicators(candles)
            current_price = float(candles[-1]["close"])
        except Exception as e:
            db.log_event("error", "market_data", f"Errore nel recupero dati: {e}")
            return

        open_trade = db.get_open_trade()

        # --- Gestione posizione aperta: TP / SL / Trailing ---
        if open_trade:
            self._manage_open_trade(open_trade, current_price, cfg)
            return  # un trade alla volta, semplice e sicuro

        # --- Nessuna posizione aperta: valutiamo un nuovo segnale ---
        ok, reason = self.risk_checks_pass(cfg)
        if not ok:
            db.log_event("warning", "circuit_breaker", reason)
            return

        signal = generate_signal(candles)
        db.log_event("info", "signal", f"{symbol} {timeframe}: {signal['signal']} - {signal['reason']}")

        if signal["signal"] in ("buy", "sell"):
            self._open_new_trade(signal["signal"], current_price, cfg, signal["reason"])

        db.record_equity(self.account.equity(db.get_open_trade(), current_price))

    def _open_new_trade(self, side, price, cfg, reason):
        sl_pct = cfg["stop_loss_pct"] / 100
        stop_loss_price = price * (1 - sl_pct) if side == "buy" else price * (1 + sl_pct)

        qty = self.position_size(cfg, price, stop_loss_price)
        if qty <= 0:
            db.log_event("warning", "order", "Quantita' calcolata = 0, ordine non eseguito")
            return

        executed_price, slip_pct = simulate_slippage(price, cfg["max_slippage_pct"])
        if abs(slip_pct) > cfg["max_slippage_pct"]:
            db.log_event("warning", "order", f"Ordine annullato: slippage {slip_pct:.3f}% oltre soglia")
            return

        cost = executed_price * qty
        if cost > self.account.balance:
            qty = self.account.balance / executed_price

        self.account.balance -= executed_price * qty
        trade_id = db.open_trade(cfg["symbol"], side, executed_price, qty, reason)
        self.peak_price_since_entry = executed_price
        db.log_event("info", "order", f"Apertura {side.upper()} {cfg['symbol']} qty={qty:.6f} @ {executed_price:.2f} (slip {slip_pct:.3f}%)")

    def _manage_open_trade(self, trade, current_price, cfg):
        side = trade["side"]
        entry = trade["entry_price"]
        qty = trade["quantity"]

        if side == "buy":
            self.peak_price_since_entry = max(self.peak_price_since_entry or entry, current_price)
            trailing_trigger = self.peak_price_since_entry * (1 - cfg["trailing_stop_pct"] / 100)
            stop_loss_price = entry * (1 - cfg["stop_loss_pct"] / 100)
            take_profit_price = entry * (1 + cfg["take_profit_pct"] / 100)

            exit_reason = None
            if current_price <= stop_loss_price:
                exit_reason = "stop_loss"
            elif current_price >= take_profit_price:
                exit_reason = "take_profit"
            elif current_price <= trailing_trigger and self.peak_price_since_entry > entry * (1 + cfg["trailing_stop_pct"] / 100):
                exit_reason = "trailing_stop"

        else:  # sell / short simulato
            self.peak_price_since_entry = min(self.peak_price_since_entry or entry, current_price)
            trailing_trigger = self.peak_price_since_entry * (1 + cfg["trailing_stop_pct"] / 100)
            stop_loss_price = entry * (1 + cfg["stop_loss_pct"] / 100)
            take_profit_price = entry * (1 - cfg["take_profit_pct"] / 100)

            exit_reason = None
            if current_price >= stop_loss_price:
                exit_reason = "stop_loss"
            elif current_price <= take_profit_price:
                exit_reason = "take_profit"
            elif current_price >= trailing_trigger and self.peak_price_since_entry < entry * (1 - cfg["trailing_stop_pct"] / 100):
                exit_reason = "trailing_stop"

        if exit_reason:
            executed_price, slip_pct = simulate_slippage(current_price, cfg["max_slippage_pct"])
            pnl = (executed_price - entry) * qty if side == "buy" else (entry - executed_price) * qty
            pnl_pct = (pnl / (entry * qty)) * 100 if entry * qty else 0
            self.account.balance += executed_price * qty
            db.close_trade(trade["id"], executed_price, pnl, pnl_pct, exit_reason)
            db.log_event(
                "info", "order",
                f"Chiusura {side.upper()} {trade['symbol']} @ {executed_price:.2f} "
                f"motivo={exit_reason} pnl={pnl:.2f} ({pnl_pct:.2f}%)"
            )
            self.peak_price_since_entry = None

        db.record_equity(self.account.equity(db.get_open_trade(), current_price))


engine = TradingEngine()

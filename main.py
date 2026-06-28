"""
Backend Flask - punto di ingresso dell'applicazione.

Riscritto senza FastAPI/Pydantic/Uvicorn perche' quelle librerie, nelle
versioni compatibili con Python molto recenti, richiedono componenti
compilati (Rust/C) che spesso non hanno una versione pronta per
Windows 32-bit. Flask e' puro Python, niente compilazione, funziona ovunque.

Avvio:
    pip install -r requirements.txt
    python main.py

Poi apri frontend/index.html nel browser.
"""
import threading
import time

from flask import Flask, request, jsonify

import database as db
import trading_engine as te

POLL_INTERVAL_SECONDS = 15  # quanto spesso il bot valuta il mercato

app = Flask(__name__)

db.init_db()


@app.after_request
def add_cors_headers(response):
    # CORS scritto a mano (niente flask-cors) per ridurre ulteriormente le dipendenze
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def bot_loop():
    while True:
        try:
            te.engine.tick()
        except Exception as e:
            db.log_event("error", "engine", f"Errore nel loop del bot: {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


def build_status_payload():
    cfg = db.get_config()
    open_trade = db.get_open_trade()
    try:
        price = te.fetch_current_price(cfg["symbol"])
    except Exception:
        price = None
    equity = te.engine.account.equity(open_trade, price) if price else te.engine.account.balance
    return {
        "balance": round(te.engine.account.balance, 2),
        "equity": round(equity, 2),
        "current_price": price,
        "open_trade": open_trade,
        "bot_active": bool(cfg["bot_active"]),
        "daily_pnl": round(db.get_daily_pnl(), 2),
        "trades_today": db.count_trades_today(),
    }


@app.route("/api/status")
def status():
    return jsonify(build_status_payload())


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(db.get_config())


@app.route("/api/config", methods=["POST", "OPTIONS"])
def set_config():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(force=True) or {}
    allowed = {
        "symbol", "timeframe", "max_capital", "risk_per_trade_pct",
        "max_daily_loss_pct", "max_trades_per_day", "stop_loss_pct",
        "take_profit_pct", "trailing_stop_pct", "max_slippage_pct",
    }
    data = {k: v for k, v in data.items() if k in allowed and v is not None}
    new_cfg = db.update_config(**data)
    db.log_event("info", "config", f"Configurazione aggiornata: {data}")
    return jsonify(new_cfg)


@app.route("/api/bot/start", methods=["POST", "OPTIONS"])
def start_bot():
    if request.method == "OPTIONS":
        return "", 204
    cfg = db.get_config()
    te.engine.reset_account(cfg["max_capital"])
    db.update_config(bot_active=1)
    db.log_event("info", "bot", "Bot avviato (paper trading)")
    return jsonify({"status": "started"})


@app.route("/api/bot/stop", methods=["POST", "OPTIONS"])
def stop_bot():
    if request.method == "OPTIONS":
        return "", 204
    db.update_config(bot_active=0)
    db.log_event("info", "bot", "Bot fermato")
    return jsonify({"status": "stopped"})


@app.route("/api/trades")
def trades():
    limit = request.args.get("limit", 100, type=int)
    return jsonify(db.get_trades(limit))


@app.route("/api/logs")
def logs():
    limit = request.args.get("limit", 200, type=int)
    return jsonify(db.get_logs(limit))


@app.route("/api/equity-history")
def equity_history():
    return jsonify(db.get_equity_history())


@app.route("/api/candles")
def candles():
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "5m")
    limit = request.args.get("limit", 100, type=int)
    data = te.fetch_klines(symbol, timeframe, limit)
    data = te.compute_indicators(data)
    out = []
    for c in data:
        out.append({
            "open_time": c["open_time"].isoformat(),
            "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
            "volume": c["volume"], "ema9": c["ema9"], "ema21": c["ema21"], "rsi": c["rsi"],
        })
    return jsonify(out)


if __name__ == "__main__":
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=8000, debug=False)

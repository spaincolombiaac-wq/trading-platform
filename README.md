# Trading Platform — Paper Trading (uso personale)

Versione minima ma funzionante end-to-end: dati di mercato reali (Binance, pubblici,
nessuna API key richiesta), motore di segnali, paper trading con TP/SL/trailing stop,
risk management, dashboard live con grafico.

**Nessun soldo reale è coinvolto.** È tutto simulato: capitale, ordini, P&L.

## Come avviarla

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
python main.py
```

(Su Windows, se `python` non funziona, usa `py -m pip install -r requirements.txt`
e poi `py main.py`)

Il server parte su `http://localhost:8000`. Al primo avvio crea da solo il file
`trading.db` (SQLite, nella cartella backend) con la configurazione di default.

### 2. Frontend

Apri semplicemente `frontend/index.html` nel browser (doppio click, o
`open frontend/index.html` su Mac / `start frontend/index.html` su Windows).

Non serve build, non serve npm: è una singola pagina HTML che parla con il
backend su `http://localhost:8000`.

### 3. Uso

1. Imposta i parametri (simbolo, timeframe, capitale, rischio, SL/TP/trailing) e premi
   **Salva config**.
2. Premi **▶ Avvia bot (paper)**.
3. Il bot valuta il mercato ogni 15 secondi: scarica le candele reali da Binance,
   calcola EMA9/EMA21/RSI/volume, e se c'è conferma multipla apre una posizione simulata.
4. La posizione viene gestita automaticamente: si chiude da sola su Take Profit,
   Stop Loss o Trailing Stop.
5. Tutto è visibile in dashboard in tempo reale: saldo, equity, P&L giornaliero,
   posizione aperta, storico trade, log eventi.

## Strategia implementata (non placeholder, logica reale)

- **Trend**: incrocio EMA9/EMA21
- **Conferma**: RSI (evita ingressi in ipercomprato/ipervenduto)
- **Filtro anti falsa rottura**: richiede un volume superiore alla media
- **Risk management**: dimensionamento posizione in base al rischio % per trade,
  perdita massima giornaliera con circuit breaker, numero massimo trade/giorno
- **Slippage simulato**: ogni esecuzione ha uno slippage realistico; se supera la
  soglia impostata l'ordine viene annullato (esattamente come richiesto nel design originale)

## Cosa NON c'è ancora (prossimi passi naturali)

- Collegamento a un exchange reale con le tue API key (al momento è tutto paper trading,
  come richiesto)
- Backtesting su dati storici lunghi (per ora il motore lavora "live" sulle ultime 100 candele)
- 2FA / autenticazione multi-utente — qui sei tu e basta, gira in locale sul tuo PC,
  non ha bisogno di login
- Supporto multi-posizione contemporanea (per semplicità e sicurezza, un trade alla volta)

Quando vorrai passare a soldi reali, fammi sapere: aggiungiamo prima un periodo di
paper trading prolungato per validare la strategia sui tuoi parametri, poi il
collegamento exchange con API key **trade-only** (mai permesso di prelievo).

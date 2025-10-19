import asyncio
import logging
import os
from decimal import Decimal, getcontext, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

import aiohttp
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_UP

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("crypto_price_bot")

DEFAULT_FIAT = "usd"
DEFAULT_SOURCE = "coingecko"
SUPPORTED_FIAT = {"usd", "eur"}
SUPPORTED_SOURCES = {"coingecko", "binance"}

user_fiat: Dict[int, str] = {}
user_source: Dict[int, str] = {}

cg_symbol_to_id: Dict[str, str] = {}
cg_cache_ready = asyncio.Event()

def fmt_money(x: Decimal, fiat: str) -> str:
    q = Decimal("0.00000001") if x < 1 else Decimal("0.01")
    s = f"{x.quantize(q):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    sym = "$" if fiat.lower() == "usd" else "€"
    return f"{sym}{s}"

def fmt_change(pct: Optional[Decimal]) -> str:
    if pct is None:
        return "—"
    sign = "▲" if pct >= 0 else "▼"
    return f"{sign} {pct.quantize(Decimal('0.01'))}%"

def code(s: str) -> str:
    return f"<code>{s}</code>"

def escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

class CoinGeckoClient:
    BASE = "https://api.coingecko.com/api/v3"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def warm_symbols(self) -> None:
        global cg_symbol_to_id
        try:
            url = f"{self.BASE}/coins/list?include_platform=false"
            async with self.session.get(url, timeout=30) as r:
                r.raise_for_status()
                coins = await r.json()
                mapping = {}
                for c in coins:
                    sym = str(c.get("symbol", "")).strip().lower()
                    cid = str(c.get("id", "")).strip().lower()
                    if sym and cid and sym not in mapping:
                        mapping[sym] = cid
                cg_symbol_to_id = mapping
                log.info("CoinGecko symbols loaded: %d", len(mapping))
        except Exception as e:
            log.exception("Failed to load CoinGecko symbols: %s", e)
        finally:
            cg_cache_ready.set()

    async def prices(self, symbols: List[str], fiat: str):
        await cg_cache_ready.wait()
        ids = [cg_symbol_to_id.get(s.lower()) for s in symbols if cg_symbol_to_id.get(s.lower())]
        if not ids:
            return {s.lower(): (None, None) for s in symbols}

        params = {"ids": ",".join(ids), "vs_currencies": fiat.lower(), "include_24hr_change": "true"}
        url = f"{self.BASE}/simple/price"
        async with self.session.get(url, params=params, timeout=20) as r:
            if r.status != 200:
                return {s.lower(): (None, None) for s in symbols}
            data = await r.json()

        id_by_sym = {v: k for k, v in cg_symbol_to_id.items()}
        out = {}
        for cid, payload in data.items():
            sym = id_by_sym.get(cid, cid)
            price_raw = payload.get(fiat.lower())
            ch_raw = payload.get(f"{fiat.lower()}_24h_change")
            price = Decimal(str(price_raw)) if price_raw is not None else None
            change = Decimal(str(ch_raw)) if ch_raw is not None else None
            out[sym] = (price, change)
        return out

class BinanceClient:
    BASE = "https://api.binance.com"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    @staticmethod
    def _pair(symbol: str, fiat: str) -> Optional[str]:
        s = symbol.upper()
        if fiat.lower() == "usd":
            return f"{s}USDT"
        elif fiat.lower() == "eur":
            return f"{s}EUR"
        return None

    async def price(self, symbol: str, fiat: str) -> Optional[Decimal]:
        pair = self._pair(symbol, fiat)
        if not pair:
            return None
        url = f"{self.BASE}/api/v3/ticker/price"
        async with self.session.get(url, params={"symbol": pair}, timeout=15) as r:
            if r.status != 200:
                return None
            data = await r.json()
            p = data.get("price")
            return Decimal(str(p)) if p is not None else None

WELCOME = (
    "Привет! Я бот цен криптовалют.\n\n"
    f"{code('/price btc eth sol')} — текущая цена и 24h % (USD, CoinGecko)\n"
    f"{code('/fiat usd|eur')} — выбрать базовую валюту\n"
    f"{code('/source coingecko|binance')} — выбрать источник"
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(WELCOME)

async def fiat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_html(f"Использование: {code('/fiat usd')} или {code('/fiat eur')}")
        return
    val = context.args[0].lower()
    if val not in SUPPORTED_FIAT:
        await update.message.reply_html("Поддерживаю только USD или EUR.")
        return
    user_fiat[update.effective_user.id] = val
    await update.message.reply_html(f"Базовая валюта: {code(val.upper())}")

async def source_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_html(f"Использование: {code('/source coingecko')} или {code('/source binance')}")
        return
    val = context.args[0].lower()
    if val not in SUPPORTED_SOURCES:
        await update.message.reply_html("Источник: coingecko или binance.")
        return
    user_source[update.effective_user.id] = val
    await update.message.reply_html(f"Источник цен: {code(val)}")

def get_user_pref(uid: int):
    fiat = user_fiat.get(uid, DEFAULT_FIAT)
    source = user_source.get(uid, DEFAULT_SOURCE)
    return fiat, source

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    fiat, source = get_user_pref(uid)

    if not context.args:
        await update.message.reply_html(f"Пример: {code('/price btc eth sol')}")
        return

    symbols = [s.strip().upper() for s in context.args if s.strip()]
    async with aiohttp.ClientSession() as session:
        cg = CoinGeckoClient(session)
        bn = BinanceClient(session)
        if source == "coingecko":
            data = await cg.prices(symbols, fiat)
            out_lines = []
            for s in symbols:
                price, change = data.get(s.lower(), (None, None))
                if price is None:
                    out_lines.append(f"{code(s)} — не найдено")
                else:
                    out_lines.append(f"{code(s)}: {fmt_money(price, fiat)} ({fmt_change(change)})")
        else:
            out_lines = []
            for s in symbols:
                price = await bn.price(s, fiat)
                if price is None:
                    out_lines.append(f"{code(s)} — не найдено")
                else:
                    out_lines.append(f"{code(s)}: {fmt_money(price, fiat)}")

    msg = f"<b>Источник:</b> {source} | <b>Фиат:</b> {fiat.upper()}\n" + "\n".join(out_lines)
    await update.message.reply_html(msg)

async def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Добавьте TELEGRAM_BOT_TOKEN в .env")

    app = Application.builder().token(token).build()

    async def warmup():
        async with aiohttp.ClientSession() as s:
            await CoinGeckoClient(s).warm_symbols()
    asyncio.create_task(warmup())

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("fiat", fiat_cmd))
    app.add_handler(CommandHandler("source", source_cmd))
    app.add_handler(CommandHandler("price", price_cmd))

    log.info("Bot started")
    await app.run_polling(close_loop=False)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

import time
import os
import requests
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")

user_state = {}
market_cache = {}
CACHE_SECONDS = 30
watchlists = {}
ADMIN_ID = 7932380565


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = closes[-i] - closes[-i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_ema(closes, period):
    if len(closes) < period:
        return closes[-1]

    k = 2 / (period + 1)
    ema = closes[0]

    for price in closes[1:]:
        ema = price * k + ema * (1 - k)

    return round(ema, 4)

def calculate_macd(closes):
    if len(closes) < 35:
        return 0, 0, 0

    ema12_values = []
    ema26_values = []

    for i in range(26, len(closes) + 1):
        part = closes[:i]
        ema12_values.append(calculate_ema(part, 12))
        ema26_values.append(calculate_ema(part, 26))

    macd_line_values = [
        ema12_values[i] - ema26_values[i]
        for i in range(len(ema26_values))
    ]

    macd_line = macd_line_values[-1]
    signal_line = calculate_ema(macd_line_values, 9)
    histogram = macd_line - signal_line

    return round(macd_line, 4), round(signal_line, 4), round(histogram, 4)

def calculate_bollinger_bands(closes, period=20):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]

    recent = closes[-period:]
    middle = sum(recent) / period

    variance = sum((price - middle) ** 2 for price in recent) / period
    std = variance ** 0.5

    upper = middle + (2 * std)
    lower = middle - (2 * std)

    return round(upper, 4), round(middle, 4), round(lower, 4)

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0

    trs = []

    for i in range(1, len(closes)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    atr = sum(trs[-period:]) / period

    return round(atr, 4)


COINGECKO_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "TONUSDT": "the-open-network",
    "SOLUSDT": "solana",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "DOGEUSDT": "dogecoin",
    "ADAUSDT": "cardano"
    
    }

def get_okx_candles(symbol, timeframe="4h", limit=200):
    inst_id = symbol.replace("USDT", "-USDT")

    tf_map = {
        "5m": "5m",
        "15m": "15m",
        "1h": "1H",
        "4h": "4H",
        "1d": "1D",
        "1w": "1W",
        "1M": "1M"
    }

    bar = tf_map.get(timeframe, "4H")

    url = (
        f"https://www.okx.com/api/v5/market/candles"
        f"?instId={inst_id}"
        f"&bar={bar}"
        f"&limit={limit}"
    )

    response = requests.get(url, timeout=10)
    data = response.json()

    if data["code"] != "0":
        raise Exception("Ошибка получения свечей OKX")

    candles = list(reversed(data["data"]))

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    return highs, lows, closes, volumes

def get_levels(closes):
   recent = closes[-50:]

   support = sorted(recent)[:5]
   support = sum(support) / len(support)

   resistance = sorted(recent)[-5:]
   resistance = sum(resistance) / len(resistance)

   return support, resistance

def analyze_volume(volumes):
    current_volume = volumes[-1]
    avg_volume = sum(volumes[-20:]) / 20

    if current_volume > avg_volume * 1.5:
        volume_text = "объём сильно выше среднего"
        volume_status = "🟢 Volume"
        volume_signal = "BUY"
    elif current_volume > avg_volume:
        volume_text = "объём выше среднего"
        volume_status = "🟢 Volume"
        volume_signal = "BUY"
    elif current_volume < avg_volume * 0.6:
        volume_text = "объём слабый"
        volume_status = "🔴 Volume"
        volume_signal = "SELL"
    else:
        volume_text = "объём нейтральный"
        volume_status = "🟡 Volume"
        volume_signal = "NEUTRAL"

    return current_volume, avg_volume, volume_text, volume_status, volume_signal

def get_btc_trend(timeframe):
    highs, lows, btc_closes, volumes = get_okx_candles("BTCUSDT", timeframe)

    ema20 = calculate_ema(btc_closes, 20)
    ema50 = calculate_ema(btc_closes, 50)

    if ema20 > ema50:
        return "🟢 Бычий"
    elif ema20 < ema50:
        return "🔴 Медвежий"
    else:
        return "🟡 Нейтральный"

def build_full_analysis(symbol, timeframe):
    highs, lows, closes, volumes = get_okx_candles(symbol, timeframe)

    price = closes[-1]

    rsi = calculate_rsi(closes)
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)
    macd, macd_signal, macd_histogram = calculate_macd(closes)
    atr = calculate_atr(highs, lows, closes) 
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(closes)

    support, resistance = get_levels(closes)
    current_volume, avg_volume, volume_text, volume_status, volume_signal = analyze_volume(volumes)

    buy_count = 0
    sell_count = 0
    neutral_count = 0

    atr_percent = (atr / price) * 100

    if atr_percent > 4:
        atr_text = "волатильность высокая"
        neutral_count += 1
    elif atr_percent > 2:
        atr_text = "волатильность средняя"
        neutral_count += 1
    else:
        atr_text = "волатильность низкая"
        buy_count += 1

    if volume_signal == "BUY":
        buy_count += 1
    elif volume_signal == "SELL":
        sell_count += 1
    else:
        neutral_count += 1

    btc_trend = get_btc_trend(timeframe)

    entry_low = support * 1.005
    entry_high = support * 1.02
    stop = support * 0.98
    tp1 = resistance
    tp2 = resistance * 1.04
    reward = tp1 - price
    risk = price - stop

    if price <= entry_high:
        entry_text = "цена находится в зоне входа"
    elif price <= entry_high * 1.02:
        entry_text = "цена немного выше зоны входа"
    else:
        entry_text = "цена сильно ушла от точки входа"

    if risk > 0:
        rr = round(reward / risk, 2)
    else:
        rr = 0

    if rr < 1 and price > entry_high:
        timing_now = "🔴 Сейчас не входить"
        timing_next = "Ждать откат к Entry или пробой Resistance"
        entry_condition = "Вход только если цена вернётся в Entry или закрепится выше Resistance"
    elif rr >= 1 and price <= entry_high:
        timing_now = "🟢 Вход возможен сейчас"
        timing_next = "Можно искать точку входа по рынку"
        entry_condition = "Цена находится в зоне входа"
    else:
        timing_now = "🟡 Лучше подождать"
        timing_next = "Наблюдать 1–2 свечи"
        entry_condition = "Ждать подтверждения от цены"

    if rsi < 30:
        buy_count += 1
        rsi_text = "перепроданность, возможен отскок"
    elif rsi > 70:
        sell_count += 1
        rsi_text = "перекупленность, вход рискованнее"
    elif 45 <= rsi <= 65:
        buy_count += 1
        rsi_text = "здоровая зона для продолжения движения"
    else:
        neutral_count += 1
        rsi_text = "нейтральная зона"

    if ema20 > ema50 and ema50 > ema200:
        buy_count += 3
        ema_text = "EMA20 выше EMA50 и EMA200 — тренд сильный"
        trend_text = "бычий"
    elif ema20 > ema50:
        buy_count += 2
        ema_text = "EMA20 выше EMA50 — краткосрочный тренд сильный"
        trend_text = "умеренно бычий"
    elif ema20 < ema50:
        sell_count += 2
        ema_text = "EMA20 ниже EMA50 — тренд слабый"
        trend_text = "медвежий"
    else:
        neutral_count += 1
        ema_text = "EMA нейтральны"
        trend_text = "нейтральный"

    if macd > macd_signal and macd_histogram > 0:
        buy_count += 2
        macd_text = "MACD выше Signal, histogram положительная — бычий импульс усиливается"
    elif macd < macd_signal and macd_histogram < 0:
        sell_count += 2
        macd_text = "MACD ниже Signal, histogram отрицательная — медвежий импульс усиливается"
    elif macd > 0:
        buy_count += 1
        macd_text = "MACD выше нуля, но импульс не подтверждён Signal"
    elif macd < 0:
        sell_count += 1
        macd_text = "MACD ниже нуля, но импульс не подтверждён Signal"
    else:
        neutral_count += 1
        macd_text = "MACD нейтральный"

    if price >= bb_upper:
        sell_count += 1
        bb_text = "цена у верхней полосы Bollinger — риск локального отката выше"
    elif price <= bb_lower:
        buy_count += 1
        bb_text = "цена у нижней полосы Bollinger — возможен локальный отскок"
    elif price > bb_middle:
        buy_count += 1
        bb_text = "цена выше средней Bollinger — структура умеренно бычья"
    else:
        neutral_count += 1
        bb_text = "цена ниже средней Bollinger — импульс слабее"

    if price > ema200:
        buy_count += 1
        ema200_text = "цена выше EMA200"
    else:
        sell_count += 1
        ema200_text = "цена ниже EMA200"

    if price > support:
        buy_count += 1
        support_text = "цена выше зоны поддержки"
    else:
        sell_count += 1
        support_text = "цена ниже поддержки"

    distance_to_resistance = ((resistance - price) / price) * 100

    if distance_to_resistance > 3:
        buy_count += 1
        resistance_text = "до сопротивления есть запас движения"
    elif distance_to_resistance > 1:
        neutral_count += 1
        resistance_text = "сопротивление близко"
    else:
        neutral_count += 1
        resistance_text = "цена почти у сопротивления"

    if rr >= 2:
        buy_count += 1
        rr_text = "хорошее соотношение риск/прибыль"
    elif rr >= 1:
        neutral_count += 1
        rr_text = "среднее соотношение риск/прибыль"
    else:
        sell_count += 1
        rr_text = "риск выше потенциальной прибыли"

    if rr < 1:
        buy_count = max(0, buy_count - 1)
        sell_count += 2

    if price > entry_high * 1.02:
        sell_count += 2
    elif price > entry_high:
        sell_count += 1

    if "🟢" in btc_trend:
        buy_count += 1
    else:
        sell_count += 1

    total_votes = buy_count + sell_count + neutral_count

    if total_votes == 0:
        buy_score = 5
    else:
        buy_score = round((buy_count / total_votes) * 10, 1)

    green_count = int(round(buy_score))
    score_bar = "🟢" * green_count + "⚪️" * (10 - green_count)

    if buy_count > sell_count and buy_score >= 7:
        decision = "🟢 ВХОД ВОЗМОЖЕН"
    elif sell_count > buy_count:
        decision = "🔴 ВХОД ОПАСЕН"
    else:
        decision = "🟡 ЛУЧШЕ ЖДАТЬ"

    return f"""
{symbol} | {timeframe}

━━━━━━━━━━━━━━
РЕШЕНИЕ

{decision}

━━━━━━━━━━━━━━
РЫНОК

BTC Trend:
{btc_trend}
BTC Dominance:
пока не подключён

Market Sentiment:
пока не подключён

━━━━━━━━━━━━━━
ТЕХНИЧЕСКИЙ АНАЛИЗ

Цена:
{round(price, 4)}

RSI:
{rsi}
Вывод:
{rsi_text}

MACD:
{macd}

MACD Signal:
{macd_signal}

MACD Histogram:
{macd_histogram}

MACD вывод:
{macd_text}

ATR:
{atr}

ATR %:
{round(atr_percent, 2)}%

ATR вывод:
{atr_text}

Bollinger Upper:
{bb_upper}

Bollinger Middle:
{bb_middle}

Bollinger Lower:
{bb_lower}

Bollinger вывод:
{bb_text}

Volume:
{round(current_volume, 2)}

Avg Volume 20:
{round(avg_volume, 2)}

Volume вывод:
{volume_text}

EMA20:
{round(ema20, 4)}

EMA50:
{round(ema50, 4)}

EMA200:
{round(ema200, 4)}

EMA вывод:
{ema_text}

EMA200:
{ema200_text}

Trend:
{trend_text}

━━━━━━━━━━━━━━
УРОВНИ

Support:
{round(support, 4)}

Resistance:
{round(resistance, 4)}

Support вывод:
{support_text}

Resistance вывод:
{resistance_text}

Risk/Reward вывод:
{rr_text}

Entry вывод:
{entry_text}

━━━━━━━━━━━━━━
СДЕЛКА

Entry:
{round(entry_low, 4)} - {round(entry_high, 4)}

Stop:
{round(stop, 4)}

TP1:
{round(tp1, 4)}

TP2:
{round(tp2, 4)}

Risk/Reward:
1:{rr}

━━━━━━━━━━━━━━
ТАЙМИНГ ВХОДА

Сейчас:
{timing_now}

Через 1–2 часа:
{timing_next}

Условие входа:
{entry_condition}

━━━━━━━━━━━━━━
TradingView Style

BUY:
{buy_count}

SELL:
{sell_count}

NEUTRAL:
{neutral_count}

━━━━━━━━━━━━━━
BUY SCORE

{buy_score} / 10

{score_bar}

Важно:
Это аналитическая подсказка, не гарантия прибыли.
Решение по сделке принимает пользователь.
"""

def build_quick_analysis(symbol, timeframe):
    return build_full_analysis(symbol, timeframe)

def get_coingecko_market(symbol):
    now = time.time()

    if symbol in market_cache:
        cached = market_cache[symbol]
        if now - cached["time"] < CACHE_SECONDS:
            return cached["data"]

    coin_id = COINGECKO_IDS.get(symbol)

    if not coin_id:
        raise Exception("Монета не найдена")

    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&ids={coin_id}"
    )

    response = requests.get(url, timeout=10)

    if response.status_code != 200:
        raise Exception("CoinGecko временно не отвечает")

    data_json = response.json()

    if not data_json:
        raise Exception("CoinGecko вернул пустой ответ")

    data = data_json[0]

    market_cache[symbol] = {
        "time": now,
        "data": data
    }

    return data


def get_market_data(symbol):
    data = get_coingecko_market(symbol)

    price = data["current_price"]
    change_1h = data.get("price_change_percentage_24h", 0)

    rsi = 50
    trend = "BULLISH" if change_1h > 0 else "BEARISH"

    return price, change_1h, rsi, trend


def get_24h_stats(symbol):
    data = get_coingecko_market(symbol)

    return {
        "price": data["current_price"],
        "high": data["high_24h"],
        "low": data["low_24h"]
    }


def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["Анализ", "Калькулятор"],
            ["Сетка", "Инфо"],
            ["⭐ Избранное", "Помощь"]
        ],
        resize_keyboard=True
    )


def back_keyboard():
    return ReplyKeyboardMarkup(
        [["Назад"]],
        resize_keyboard=True
    )


def coin_inline_keyboard(prefix):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("BTC", callback_data=f"{prefix}_BTC"),
                InlineKeyboardButton("ETH", callback_data=f"{prefix}_ETH"),
                InlineKeyboardButton("TON", callback_data=f"{prefix}_TON")
            ],
            [
                InlineKeyboardButton("SOL", callback_data=f"{prefix}_SOL"),
                InlineKeyboardButton("BNB", callback_data=f"{prefix}_BNB"),
                InlineKeyboardButton("XRP", callback_data=f"{prefix}_XRP")
            ],
            [
                InlineKeyboardButton("DOGE", callback_data=f"{prefix}_DOGE"),
                InlineKeyboardButton("ADA", callback_data=f"{prefix}_ADA")
            ]
        ]
    )


def timeframe_inline_keyboard(symbol):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("5m", callback_data=f"TF_{symbol}_5m"),
                InlineKeyboardButton("15m", callback_data=f"TF_{symbol}_15m")
            ],
            [
                InlineKeyboardButton("1h", callback_data=f"TF_{symbol}_1h"),
                InlineKeyboardButton("4h", callback_data=f"TF_{symbol}_4h")
            ],
            [
                InlineKeyboardButton("1d", callback_data=f"TF_{symbol}_1d"),
                InlineKeyboardButton("1w", callback_data=f"TF_{symbol}_1w")
            ],
            [
                InlineKeyboardButton("1M", callback_data=f"TF_{symbol}_1M")
            ]
        ]
    )


def info_inline_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("RSI", callback_data="INFO_RSI"),
                InlineKeyboardButton("EMA", callback_data="INFO_EMA")
            ],
            [
                InlineKeyboardButton("TP / SL", callback_data="INFO_TPSL"),
                InlineKeyboardButton("Grid", callback_data="INFO_GRID")
            ],
            [
                InlineKeyboardButton("BTC Filter", callback_data="INFO_BTC"),
                InlineKeyboardButton("Risk", callback_data="INFO_RISK")
            ],
            [
                InlineKeyboardButton("Entry", callback_data="INFO_ENTRY"),
                InlineKeyboardButton("О боте", callback_data="INFO_BOT")
            ]
        ]
    )


def watchlist_inline_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить монету", callback_data="WL_ADD")],
            [InlineKeyboardButton("📋 Мой список", callback_data="WL_LIST")],
            [InlineKeyboardButton("❌ Удалить монету", callback_data="WL_REMOVE")]
        ]
    )


def help_inline_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 Инфо", callback_data="HELP_INFO")],
            [InlineKeyboardButton("✍️ Оставить отзыв", callback_data="HELP_FEEDBACK")]
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Crypto AI Bot запущен.\n\nВыбери действие:",
        reply_markup=main_keyboard()
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data.startswith("ANALYZE_"):
        coin = data.replace("ANALYZE_", "")

        await query.message.reply_text(
            f"Выбран {coin}\n\nВыберите таймфрейм:",
            reply_markup=timeframe_inline_keyboard(coin)
        )
        return

    if data.startswith("TF_"):
        parts = data.split("_")
        coin = parts[1]
        timeframe = parts[2]

        symbol = coin + "USDT"

        quick_text = build_quick_analysis(
            symbol,
            timeframe
        )

        await query.message.reply_text(
            quick_text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📊 Полный анализ",
                            callback_data=f"FULL_{coin}_{timeframe}"
                        )
                    ]
                ]
            )
        )
        return
    
    if data.startswith("FULL_"):
        parts = data.split("_")
        coin = parts[1]
        timeframe = parts[2]
        symbol = coin + "USDT"

        full_text = build_full_analysis(symbol, timeframe)

        await query.message.reply_text(
            full_text,
            reply_markup=main_keyboard()
        )

        return

    if data.startswith("CALC_"):
        coin = data.replace("CALC_", "")
        symbol = coin + "USDT"

        user_state[user_id] = {
            "mode": "calculator",
            "step": "amount",
            "coin": coin,
            "symbol": symbol
        }

        try:
            stats = get_24h_stats(symbol)

            await query.message.reply_text(
                f"""
🧮 Калькулятор {symbol}

💰 Цена сейчас:
{round(stats["price"], 4)} USDT

📈 High 24ч:
{round(stats["high"], 4)} USDT

📉 Low 24ч:
{round(stats["low"], 4)} USDT

Введите сумму USDT.
Например: 1000
""",
                reply_markup=back_keyboard()
            )

        except Exception:
            await query.message.reply_text("❌ Ошибка получения данных")

        return

    if data.startswith("GRID_"):
        coin = data.replace("GRID_", "")
        symbol = coin + "USDT"

        user_state[user_id] = {
            "mode": "grid",
            "step": "deposit",
            "coin": coin,
            "symbol": symbol
        }

        try:
            stats = get_24h_stats(symbol)

            await query.message.reply_text(
                f"""
🕸 Сетка {symbol}

💰 Цена сейчас:
{round(stats["price"], 4)} USDT

📈 High 24ч:
{round(stats["high"], 4)} USDT

📉 Low 24ч:
{round(stats["low"], 4)} USDT

Введите депозит USDT.
Например: 1000
""",
                reply_markup=back_keyboard()
            )

        except Exception:
            await query.message.reply_text("❌ Ошибка получения данных Binance.")

        return

    if data == "HELP_INFO":
        await query.message.reply_text(
            "📚 Инфо-раздел\n\nВыбери тему:",
            reply_markup=info_inline_keyboard()
        )
        return

    if data == "HELP_FEEDBACK":
        user_state[user_id] = {
            "mode": "feedback"
        }

        await query.message.reply_text(
            "✍️ Напиши отзыв или сообщение разработчику."
        )
        return

    if data == "WL_ADD":
        user_state[user_id] = {
            "mode": "watchlist_add"
        }

        await query.message.reply_text(
            "Введите монету, которую добавить.\nНапример: TON"
        )
        return

    if data == "WL_LIST":
        coins = watchlists.get(user_id, [])

        if not coins:
            await query.message.reply_text(
                "⭐ Твой список избранного пуст."
            )
            return

        text = "⭐ Твои избранные монеты:\n\n"

        for coin in coins:
            text += f"• {coin}\n"

        await query.message.reply_text(text)
        return

    if data == "WL_REMOVE":
        user_state[user_id] = {
            "mode": "watchlist_remove"
        }

        await query.message.reply_text(
            "Введите монету, которую удалить.\nНапример: TON"
        )
        return

    if data.startswith("INFO_"):
        topic = data.replace("INFO_", "")

        texts = {
            "RSI": """
📈 RSI

RSI показывает перекупленность или перепроданность монеты.

RSI выше 70:
монета может быть перегрета.

RSI ниже 30:
монета сильно просела, возможен отскок.

Важно:
RSI не даёт 100% сигнала,
он только помогает оценить риск.
""",
            "EMA": """
📊 EMA

EMA — это скользящая средняя цены.

EMA20 выше EMA50:
тренд сильнее,
рынок выглядит бычьим.

EMA20 ниже EMA50:
тренд слабее,
риск выше.

В боте EMA используется
для определения BULLISH / BEARISH.
""",
            "TPSL": """
🎯 TP / SL

TP — Take Profit.
Это цена,
где ты планируешь забрать прибыль.

SL — Stop Loss.
Это цена,
где ты ограничиваешь убыток.

Пример:

Entry: 100
TP 2% = 102
SL 1% = 99
""",
            "GRID": """
🕸 Grid / Сетка

Сетка — это стратегия,
где бот покупает ниже
и продаёт выше внутри диапазона.

На прибыль влияет:
• диапазон цены
• количество сеток
• сумма депозита
• размер одного ордера
• комиссия
• волатильность монеты

Если сеток слишком много,
размер ордера становится маленьким.

Тогда прибыль может быть
съедена комиссией.
""",
            "BTC": """
₿ BTC Filter

BTC сильно влияет
на весь рынок.

Если BTC падает:
альты часто падают сильнее.

Если BTC стабилен:
альты могут двигаться лучше.

Поэтому бот проверяет BTC
перед оценкой риска.
""",
            "RISK": """
⚠️ Risk

LOW:
риск ниже,
можно искать аккуратный вход.

MEDIUM:
лучше ждать откат
или подтверждение.

HIGH:
монета перегрета
или рынок опасный.

Важно:
Risk — это подсказка,
не гарантия.
""",
            "ENTRY": """
🚪 Entry

Entry — цена входа в сделку.

Лучше не входить
после резкого пампа.

Часто безопаснее
ждать откат.

Для сетки важно выбирать диапазон
не случайно, а по рынку:

Low 24ч
High 24ч
текущая цена
BTC filter
""",
            "BOT": """
🤖 Об этом боте

Бот помогает анализировать крипторынок.

Функции:
• анализ монет
• RSI / EMA / Trend
• BTC Filter
• калькулятор сделки
• расчёт сетки
• оценка риска
• избранные монеты
• обратная связь

Бот не гарантирует прибыль.

Все сделки ты совершаешь
на свою ответственность.
"""
        }

        await query.message.reply_text(
            texts.get(topic, "Информация не найдена."),
            reply_markup=info_inline_keyboard()
        )
        return


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    text_upper = text.upper()

    if text_upper == "НАЗАД":
        user_state.pop(user_id, None)

        await update.message.reply_text(
            "Вернулся в главное меню.",
            reply_markup=main_keyboard()
        )
        return

    if text_upper == "ПОМОЩЬ":
        await update.message.reply_text(
            "❓ Раздел помощи\n\nВыбери действие:",
            reply_markup=help_inline_keyboard()
        )
        return

    if text_upper == "ИНФО":
        await update.message.reply_text(
            "📚 Инфо-раздел\n\nВыбери тему:",
            reply_markup=info_inline_keyboard()
        )
        return

    if text_upper == "⭐ ИЗБРАННОЕ":
        await update.message.reply_text(
            "⭐ Раздел избранных монет\n\nВыбери действие:",
            reply_markup=watchlist_inline_keyboard()
        )
        return

    if text_upper == "АНАЛИЗ":
        await update.message.reply_text(
            "Выбери монету кнопкой или напиши свою вручную:",
            reply_markup=coin_inline_keyboard("ANALYZE")
        )
        return

    if text_upper == "КАЛЬКУЛЯТОР":
        await update.message.reply_text(
            "Выбери монету для калькулятора:",
            reply_markup=coin_inline_keyboard("CALC")
        )
        return

    if text_upper == "СЕТКА":
        await update.message.reply_text(
            "Выбери монету для сетки:",
            reply_markup=coin_inline_keyboard("GRID")
        )
        return

    state = user_state.get(user_id)

    try:
        if state:
            mode = state["mode"]

            if mode == "watchlist_add":
                coin = text_upper

                if user_id not in watchlists:
                    watchlists[user_id] = []

                if coin not in watchlists[user_id]:
                    watchlists[user_id].append(coin)

                user_state.pop(user_id, None)

                await update.message.reply_text(
                    f"⭐ {coin} добавлен в избранное.",
                    reply_markup=main_keyboard()
                )
                return

            if mode == "watchlist_remove":
                coin = text_upper

                if user_id in watchlists and coin in watchlists[user_id]:
                    watchlists[user_id].remove(coin)
                    message = f"❌ {coin} удалён из избранного."
                else:
                    message = f"Монета {coin} не найдена в избранном."

                user_state.pop(user_id, None)

                await update.message.reply_text(
                    message,
                    reply_markup=main_keyboard()
                )
                return

            if mode == "feedback":
                if ADMIN_ID:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"""
📩 Новый отзыв

👤 User ID:
{user_id}

💬 Сообщение:
{text}
"""
                    )

                user_state.pop(user_id, None)

                await update.message.reply_text(
                    """
✅ Спасибо за отзыв.

Мы благодарны,
что ты доверяешь и используешь:

@vai_cryptoflow_bot

Все предложения и замечания помогают делать бота лучше 🚀
""",
                    reply_markup=main_keyboard()
                )
                return

            step = state["step"]

            if mode == "calculator":
                if step == "amount":
                    state["amount"] = float(text)
                    state["step"] = "tp"

                    await update.message.reply_text(
                        "Введите Take Profit %.\nНапример: 2",
                        reply_markup=back_keyboard()
                    )
                    return

                if step == "tp":
                    state["tp"] = float(text)
                    state["step"] = "sl"

                    await update.message.reply_text(
                        "Введите Stop Loss %.\nНапример: 1",
                        reply_markup=back_keyboard()
                    )
                    return

                if step == "sl":
                    state["sl"] = float(text)

                    coin = state["coin"]
                    symbol = state["symbol"]
                    investment = state["amount"]
                    tp_percent = state["tp"]
                    sl_percent = state["sl"]

                    price, change_1h, rsi, trend = get_market_data(symbol)

                    entry_price = price
                    tp_price = entry_price * (1 + tp_percent / 100)
                    sl_price = entry_price * (1 - sl_percent / 100)

                    amount = investment / entry_price
                    commission = investment * 0.002
                    gross_profit = investment * (tp_percent / 100)
                    net_profit = gross_profit - commission
                    potential_loss = investment * (sl_percent / 100) + commission

                    reply = f"""
🧮 Калькулятор {symbol}

💰 Текущая цена:
{round(price, 4)}

💵 Сумма:
{investment} USDT

Entry:
{round(entry_price, 4)}

TP Price:
{round(tp_price, 4)}

SL Price:
{round(sl_price, 4)}

Amount:
{round(amount, 4)} {coin}

Комиссия:
{round(commission, 2)} USDT

Чистая прибыль при TP:
{round(net_profit, 2)} USDT

Потенциальный убыток при SL:
-{round(potential_loss, 2)} USDT
"""

                    user_state.pop(user_id, None)

                    await update.message.reply_text(
                        reply,
                        reply_markup=main_keyboard()
                    )
                    return

            if mode == "grid":
                if step == "deposit":
                    state["deposit"] = float(text)
                    state["step"] = "grid_count"

                    await update.message.reply_text(
                        "Введите количество сеток.\nНапример: 20",
                        reply_markup=back_keyboard()
                    )
                    return

                if step == "grid_count":
                    state["grid_count"] = int(text)
                    state["step"] = "lower_price"

                    await update.message.reply_text(
                        "Введите нижнюю цену.\nНапример: 2.40",
                        reply_markup=back_keyboard()
                    )
                    return

                if step == "lower_price":
                    state["lower_price"] = float(text)
                    state["step"] = "upper_price"

                    await update.message.reply_text(
                        "Введите верхнюю цену.\nНапример: 2.80",
                        reply_markup=back_keyboard()
                    )
                    return

                if step == "upper_price":
                    state["upper_price"] = float(text)

                    symbol = state["symbol"]
                    deposit = state["deposit"]
                    grid_count = state["grid_count"]
                    lower_price = state["lower_price"]
                    upper_price = state["upper_price"]

                    order_size = deposit / grid_count
                    step_size = (upper_price - lower_price) / grid_count
                    step_percent = (step_size / lower_price) * 100

                    reply = f"""
🕸 Grid Calculator {symbol}

Диапазон:

Низ:
{lower_price}

Верх:
{upper_price}

Депозит:
{deposit} USDT

Количество сеток:
{grid_count}

Размер одного ордера:
{round(order_size, 2)} USDT

Шаг сетки:
{round(step_size, 4)} USDT

≈ {round(step_percent, 2)}%

Вывод:
Если размер одного ордера
слишком маленький,
прибыль может быть
съедена комиссией.
"""

                    user_state.pop(user_id, None)

                    await update.message.reply_text(
                        reply,
                        reply_markup=main_keyboard()
                    )
                    return

        await update.message.reply_text(
            "Для анализа нажми кнопку Анализ и выбери монету.",
            reply_markup=main_keyboard()
        )

    except Exception:
        await update.message.reply_text(
            "❌ Ошибка.\n"
            "Проверь ввод или монету.\n\n"
            "Нажми Назад или выбери действие заново.",
            reply_markup=main_keyboard()
        )

        user_state.pop(user_id, None)


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_callback))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("BOT STARTED")

app.run_polling()
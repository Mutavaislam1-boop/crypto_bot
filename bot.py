import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3
from datetime import datetime, timedelta
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
DATABASE_URL = os.getenv("DATABASE_URL")

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

def init_signal_db():

    conn = psycopg2.connect(DATABASE_URL)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            created_at TEXT,
            symbol TEXT,
            timeframe TEXT,
            price DOUBLE PRECISION,
            decision TEXT,
            buy_score DOUBLE PRECISION,
            trend_score DOUBLE PRECISION,
            entry_low DOUBLE PRECISION,
            entry_high DOUBLE PRECISION,
            stop DOUBLE PRECISION,
            tp1 DOUBLE PRECISION,
            tp2 DOUBLE PRECISION,
            check_4h_done INTEGER DEFAULT 0,
            check_24h_done INTEGER DEFAULT 0,
            check_72h_done INTEGER DEFAULT 0
        )
    """)

    conn.commit()

    conn.close()

    print("SIGNAL DATABASE CREATED")

def save_signal(
    symbol,
    timeframe,
    price,
    decision,
    buy_score,
    trend_score,
    entry_low,
    entry_high,
    stop,
    tp1,
    tp2
):
    print("SAVE SIGNAL CALLED")

    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO signals (
            created_at,
            symbol,
            timeframe,
            price,
            decision,
            buy_score,
            trend_score,
            entry_low,
            entry_high,
            stop,
            tp1,
            tp2
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        datetime.utcnow().isoformat(),
        symbol,
        timeframe,
        price,
        decision,
        buy_score,
        trend_score,
        entry_low,
        entry_high,
        stop,
        tp1,
        tp2
    ))

    signal_id = cursor.fetchone()[0]

    conn.commit()
    conn.close()

    print("SIGNAL SAVED:", symbol, signal_id)

    return signal_id

def get_unchecked_signals():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM signals")
    print("TOTAL SIGNALS:", cursor.fetchone()[0])

    cursor.execute("""
        SELECT *
        FROM signals
        WHERE
            check_4h_done = 0
            OR check_24h_done = 0
            OR check_72h_done = 0
        ORDER BY id ASC
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows

def update_signal_check(signal_id, field):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    cursor.execute(
        f"""
        UPDATE signals
        SET {field}=1
        WHERE id=%s
        """,
        (signal_id,)
    )

    conn.commit()
    conn.close()

def get_current_price(symbol):
    highs, lows, closes, volumes = get_okx_candles(
        symbol,
        "1h",
        5
    )

    return closes[-1]

def calculate_signal_result(entry_price, current_price):
    change_percent = (
        (current_price - entry_price)
        / entry_price
    ) * 100

    return round(change_percent, 2)

def check_signals():

    print("CHECK SIGNALS STARTED")

    signals = get_unchecked_signals()

    print("SIGNALS FOUND:", len(signals))

    for signal in signals:
        print(signal)

        signal_id = signal[0]
        created_at = signal[1]

        symbol = signal[2]

        entry_price = signal[4]

        check_4h_done = signal[13]
        check_24h_done = signal[14]
        check_72h_done = signal[15]

        created_dt = datetime.fromisoformat(created_at)

        now = datetime.utcnow()

        hours_passed = (
            now - created_dt
        ).total_seconds() / 3600

        try:
            current_price = get_current_price(symbol)

        except Exception:
            continue

        result_percent = calculate_signal_result(
            entry_price,
            current_price
        )

        decision = signal[5]

        if "NO TRADE" in decision:

            if result_percent >= 3:
                verdict = "❌ BOT MISSED PROFIT"

            elif result_percent <= -3:
                verdict = "✅ NO TRADE CORRECT"

            else:
                verdict = "⚪ NEUTRAL"

        else:

            if result_percent >= 3:
                verdict = "✅ BUY CORRECT"

            elif result_percent <= -3:
                verdict = "❌ BUY FAILED"

            else:
                verdict = "⚪ NEUTRAL"

        print(
            f"Signal #{signal_id} | "
            f"{symbol} | "
            f"{result_percent}% | "
            f"{verdict}"
        ) 
                
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

def calculate_stoch_rsi(closes, rsi_period=14, stoch_period=14):
    if len(closes) < rsi_period + stoch_period:
        return 50

    rsi_values = []

    for i in range(rsi_period + 1, len(closes) + 1):
        rsi_values.append(calculate_rsi(closes[:i], rsi_period))

    recent_rsi = rsi_values[-stoch_period:]

    min_rsi = min(recent_rsi)
    max_rsi = max(recent_rsi)

    if max_rsi == min_rsi:
        return 50

    stoch_rsi = ((rsi_values[-1] - min_rsi) / (max_rsi - min_rsi)) * 100

    return round(stoch_rsi, 2)

def calculate_vwap(highs, lows, closes, volumes):
    if len(closes) == 0 or len(volumes) == 0:
        return 0

    total_pv = 0
    total_volume = 0

    for i in range(len(closes)):
        typical_price = (highs[i] + lows[i] + closes[i]) / 3
        volume = volumes[i]

        total_pv += typical_price * volume
        total_volume += volume

    if total_volume == 0:
        return closes[-1]

    vwap = total_pv / total_volume

    return round(vwap, 4)

def calculate_fibonacci_levels(highs, lows):
    recent_high = max(highs[-100:])
    recent_low = min(lows[-100:])

    diff = recent_high - recent_low

    if diff == 0:
        return recent_high, recent_low, 0, 0, 0, 0, 0

    fib_236 = recent_high - diff * 0.236
    fib_382 = recent_high - diff * 0.382
    fib_500 = recent_high - diff * 0.5
    fib_618 = recent_high - diff * 0.618
    fib_786 = recent_high - diff * 0.786

    return (
        round(recent_high, 4),
        round(recent_low, 4),
        round(fib_236, 4),
        round(fib_382, 4),
        round(fib_500, 4),
        round(fib_618, 4),
        round(fib_786, 4)
    )

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

def get_levels(highs, lows, closes):
    recent_highs = highs[-100:]
    recent_lows = lows[-100:]
    recent_closes = closes[-100:]

    current_price = closes[-1]

    swing_lows = []
    swing_highs = []

    for i in range(2, len(recent_closes) - 2):
        if (
            recent_lows[i] < recent_lows[i - 1]
            and recent_lows[i] < recent_lows[i - 2]
            and recent_lows[i] < recent_lows[i + 1]
            and recent_lows[i] < recent_lows[i + 2]
        ):
            swing_lows.append(recent_lows[i])

        if (
            recent_highs[i] > recent_highs[i - 1]
            and recent_highs[i] > recent_highs[i - 2]
            and recent_highs[i] > recent_highs[i + 1]
            and recent_highs[i] > recent_highs[i + 2]
        ):
            swing_highs.append(recent_highs[i])

    supports = [level for level in swing_lows if level < current_price]
    resistances = [level for level in swing_highs if level > current_price]

    supports = sorted(supports, reverse=True)
    resistances = sorted(resistances)

    if supports:
        support = supports[0]
    else:
        support = min(recent_lows)

    if resistances:
        resistance = resistances[0]
    else:
        resistance = max(recent_highs)

    return round(support, 4), round(resistance, 4)

def analyze_market_structure(highs, lows, closes):
    recent_highs = highs[-80:]
    recent_lows = lows[-80:]

    swing_highs = []
    swing_lows = []

    for i in range(2, len(recent_highs) - 2):
        if recent_highs[i] > recent_highs[i - 1] and recent_highs[i] > recent_highs[i + 1]:
            swing_highs.append(recent_highs[i])

        if recent_lows[i] < recent_lows[i - 1] and recent_lows[i] < recent_lows[i + 1]:
            swing_lows.append(recent_lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "🟡 Недостаточно данных", "структура рынка пока не определена", "NEUTRAL"

    last_high = swing_highs[-1]
    prev_high = swing_highs[-2]

    last_low = swing_lows[-1]
    prev_low = swing_lows[-2]

    if last_high > prev_high and last_low > prev_low:
        structure = "🟢 HH + HL"
        text = "бычья структура: цена формирует higher high и higher low"
        signal = "BUY"
    elif last_high < prev_high and last_low < prev_low:
        structure = "🔴 LH + LL"
        text = "медвежья структура: цена формирует lower high и lower low"
        signal = "SELL"
    elif last_high > prev_high and last_low < prev_low:
        structure = "🟡 Расширение диапазона"
        text = "рынок расширяет диапазон, направление пока не подтверждено"
        signal = "NEUTRAL"
    elif last_high < prev_high and last_low > prev_low:
        structure = "🟡 Сжатие диапазона"
        text = "рынок сжимается, возможен сильный импульс после выхода"
        signal = "NEUTRAL"
    else:
        structure = "🟡 Нейтральная структура"
        text = "структура рынка смешанная"
        signal = "NEUTRAL"

    return structure, text, signal

def detect_bos(highs, lows, closes):
    recent_highs = highs[-50:]
    recent_lows = lows[-50:]

    swing_highs = []
    swing_lows = []

    for i in range(2, len(recent_highs) - 2):

        if (
            recent_highs[i] > recent_highs[i - 1]
            and recent_highs[i] > recent_highs[i + 1]
        ):
            swing_highs.append(recent_highs[i])

        if (
            recent_lows[i] < recent_lows[i - 1]
            and recent_lows[i] < recent_lows[i + 1]
        ):
            swing_lows.append(recent_lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "🟡 Нет BOS", "структура ещё не сформирована", "NEUTRAL"

    current_price = closes[-1]

    last_high = swing_highs[-1]
    prev_high = swing_highs[-2]

    last_low = swing_lows[-1]
    prev_low = swing_lows[-2]

    if current_price > last_high and last_high > prev_high:
        return (
            "🟢 Bullish BOS",
            "покупатели пробили предыдущую структуру вверх",
            "BUY"
        )

    if current_price < last_low and last_low < prev_low:
        return (
            "🔴 Bearish BOS",
            "продавцы пробили предыдущую структуру вниз",
            "SELL"
        )

    return (
        "🟡 Нет BOS",
        "структура пока не сломана",
        "NEUTRAL"
    )

def detect_choch(highs, lows, closes, volumes):
    recent_closes = closes[-12:]
    recent_lows = lows[-12:]

    current_price = closes[-1]
    prev_close_high = max(recent_closes[:-1])
    prev_low = min(recent_lows[:-1])

    avg_volume = sum(volumes[-20:]) / 20
    current_volume = volumes[-1]

    volume_confirmed = current_volume > avg_volume * 0.9

    if current_price > prev_close_high and volume_confirmed:
        return (
            "🟢 Bullish CHOCH",
            "цена закрылась выше локальных закрытий, есть ранний признак разворота вверх",
            "BUY"
        )

    elif current_price < prev_low and volume_confirmed:
        return (
            "🔴 Bearish CHOCH",
            "цена пробила локальную поддержку вниз, есть ранний признак слабости",
            "SELL"
        )

    return (
        "🟡 Нет CHOCH",
        "ранней смены характера движения пока нет",
        "NEUTRAL"
    )

def detect_market_phase(
    price,
    ema20,
    ema50,
    ema200,
    rsi,
    macd_histogram,
    volume_signal,
    bos_signal,
    choch_signal,
    reversal_score,
    rr
):
    if (
        price > ema20
        and ema20 > ema50
        and price > ema200
        and rsi > 55
        and rr >= 1
    ):
        return "🟢 Uptrend", "рынок находится в бычьей фазе"

    if (
        price < ema20
        and ema20 < ema50
        and price < ema200
        and rsi < 45
        and macd_histogram < 0
    ):
        return "🔴 Downtrend", "рынок находится в устойчивой медвежьей фазе"

    if (
        price < ema200
        and rsi < 35
        and volume_signal == "BUY"
    ):
        return "🟡 Capitulation", "возможна капитуляция продавцов и резкий отскок"

    if (
        price < ema200
        and reversal_score >= 35
        and (bos_signal == "BUY" or choch_signal == "BUY")
        and macd_histogram > 0
    ):
        return "🟡 Recovery", "рынок пытается восстановиться после падения"

    if (
        reversal_score >= 60
        and bos_signal == "BUY"
        and choch_signal == "BUY"
        and price > ema20
    ):
        return "🟢 Reversal", "появляются признаки полноценного разворота"

    if rr < 1:
        return "⚪ Range / Bad Entry", "рынок может быть в диапазоне, но точка входа некачественная"

    return "⚪ Neutral", "фаза рынка смешанная, нужен дополнительный сигнал"

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
    
def get_asset_trend(symbol, timeframe):
    highs, lows, closes, volumes = get_okx_candles(symbol, timeframe)

    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)

    if ema20 > ema50 and closes[-1] > ema200:
        return "🟢 Бычий"
    elif ema20 < ema50 and closes[-1] < ema200:
        return "🔴 Медвежий"
    else:
        return "🟡 Нейтральный"
    
def get_relative_strength(symbol, timeframe):
    if symbol == "BTCUSDT":
        return "₿ BTC", "это сам Bitcoin, сравнение с BTC не требуется", "NEUTRAL"

    try:
        asset_highs, asset_lows, asset_closes, asset_volumes = get_okx_candles(symbol, timeframe)
        btc_highs, btc_lows, btc_closes, btc_volumes = get_okx_candles("BTCUSDT", timeframe)

        asset_change = ((asset_closes[-1] - asset_closes[-24]) / asset_closes[-24]) * 100
        btc_change = ((btc_closes[-1] - btc_closes[-24]) / btc_closes[-24]) * 100

        difference = asset_change - btc_change

        if difference > 2:
            return "🟢 Сильнее BTC", f"монета сильнее BTC на {round(difference, 2)}%", "BUY"

        elif difference < -2:
            return "🔴 Слабее BTC", f"монета слабее BTC на {round(abs(difference), 2)}%", "SELL"

        else:
            return "🟡 Примерно как BTC", f"движение близко к BTC, разница {round(difference, 2)}%", "NEUTRAL"

    except Exception:
        return "🟡 Нет данных", "не удалось сравнить монету с BTC", "NEUTRAL"

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
    stoch_rsi = calculate_stoch_rsi(closes)
    vwap = calculate_vwap(highs, lows, closes, volumes)
    fib_high, fib_low, fib_236, fib_382, fib_500, fib_618, fib_786 = calculate_fibonacci_levels(highs, lows)

    support, resistance = get_levels(highs, lows, closes)
    market_structure, structure_text, structure_signal = analyze_market_structure(highs, lows, closes)
    bos_text, bos_description, bos_signal = detect_bos(highs, lows, closes)
    choch_text, choch_description, choch_signal = detect_choch(highs, lows, closes, volumes)
    current_volume, avg_volume, volume_text, volume_status, volume_signal = analyze_volume(volumes)

    buy_count = 0
    sell_count = 0
    neutral_count = 0

    btc_trend = get_btc_trend(timeframe)
    trend_1d = get_asset_trend(symbol, "1d")
    trend_4h = get_asset_trend(symbol, "4h")
    trend_1h = get_asset_trend(symbol, "1h")
    
    relative_strength, relative_strength_text, relative_strength_signal = get_relative_strength(symbol, timeframe)

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

    if structure_signal == "BUY":
        buy_count += 2
    elif structure_signal == "SELL":
        sell_count += 2
    else:
        neutral_count += 1

    if bos_signal == "BUY":
        buy_count += 3
    elif bos_signal == "SELL":
        sell_count += 3
    else:
        neutral_count += 1

    if choch_signal == "BUY":
        buy_count += 3
    elif choch_signal == "SELL":
        sell_count += 3
    else:
        neutral_count += 1

    if volume_signal == "BUY":
        buy_count += 1
    elif volume_signal == "SELL":
        sell_count += 1
    else:
        neutral_count += 1

    bullish_tf = 0
    bearish_tf = 0

    for tf_trend in [trend_1d, trend_4h, trend_1h]:
        if "🟢" in tf_trend:
            bullish_tf += 1
        elif "🔴" in tf_trend:
            bearish_tf += 1

    if bullish_tf >= 2:
        buy_count += 2
        mtf_text = "большинство старших таймфреймов поддерживают рост"
    elif bearish_tf >= 2:
        sell_count += 2
        mtf_text = "большинство старших таймфреймов указывают на слабость"
    else:
        neutral_count += 1
        mtf_text = "таймфреймы дают смешанный сигнал"

    if relative_strength_signal == "BUY":
        buy_count += 1

    elif relative_strength_signal == "SELL":
        sell_count += 1

    else:
        neutral_count += 1

    entry_low = support * 1.005
    entry_high = support * 1.02
    stop = support * 0.98
    tp1 = resistance
    tp2 = resistance * 1.04

    reward = tp1 - price
    risk = price - stop

    if risk > 0:
        rr = round(reward / risk, 2)
    else:
        rr = 0

    reversal_score = 0

    if choch_signal == "BUY":
        reversal_score += 25

    if bos_signal == "BUY":
        reversal_score += 15

    if macd_histogram > 0:
        reversal_score += 15

    if volume_signal == "BUY":
        reversal_score += 15

    if price > support:
        reversal_score += 10

    if 45 < rsi < 70:
        reversal_score += 10

    if price > bb_middle:
        reversal_score += 5

    if volume_signal == "SELL":
        reversal_score -= 15

    if price < ema200:
        reversal_score -= 10

    if sell_count > buy_count:
        reversal_score -= 10

    if "🔴" in btc_trend:
        reversal_score -= 10

    reversal_score = max(0, min(100, reversal_score))

    if reversal_score >= 70:
        reversal_text = "сильный разворотный сигнал"
    elif reversal_score >= 50:
        reversal_text = "есть признаки локального разворота, вход только с подтверждением"
    elif reversal_score >= 30:
        reversal_text = "слабые признаки разворота, лучше наблюдать"
    else:
        reversal_text = "разворот пока не подтверждён"

        trend_score = 50

    if price > ema20:
        trend_score += 10
    else:
        trend_score -= 10

    if ema20 > ema50:
        trend_score += 15
    else:
        trend_score -= 15

    if price > ema200:
        trend_score += 20
    else:
        trend_score -= 20

    if structure_signal == "BUY":
        trend_score += 15
    elif structure_signal == "SELL":
        trend_score -= 15

    if bullish_tf >= 2:
        trend_score += 15
    elif bearish_tf >= 2:
        trend_score -= 15

    if "🟢" in btc_trend:
        trend_score += 10
    elif "🔴" in btc_trend:
        trend_score -= 10

    if macd_histogram > 0:
        trend_score += 10

    if choch_signal == "BUY":
        trend_score += 10

    trend_score = max(0, min(100, trend_score))

    if macd_histogram > 0:
        trend_score += 10
    else:
        trend_score -= 10

    if relative_strength_signal == "BUY":
        trend_score += 10
    elif relative_strength_signal == "SELL":
        trend_score -= 10

    if choch_signal == "BUY":
        trend_score += 10

    if trend_score >= 75:
        trend_score_text = "сильный бычий тренд"
    elif trend_score >= 55:
        trend_score_text = "умеренно бычий тренд"
    elif trend_score >= 40:
        trend_score_text = "смешанный тренд"
    elif trend_score >= 25:
        trend_score_text = "слабый медвежий тренд"
    else:
        trend_score_text = "сильный медвежий тренд"

    market_phase, market_phase_text = detect_market_phase(
        price,
        ema20,
        ema50,
        ema200,
        rsi,
        macd_histogram,
        volume_signal,
        bos_signal,
        choch_signal,
        reversal_score,
        rr
    )

    entry_quality = 50

    if rr >= 2:
        entry_quality += 20
    elif rr >= 1:
        entry_quality += 10
    else:
        entry_quality -= 25

    if price <= entry_high:
        entry_quality += 15
    elif price <= entry_high * 1.02:
        entry_quality += 5
    else:
        entry_quality -= 20

    if price > support:
        entry_quality += 5

    if price >= resistance * 0.98:
        entry_quality -= 20

    if price < ema200:
        entry_quality -= 15

    if price < vwap:
        entry_quality -= 10

    if "🔴" in btc_trend:
        entry_quality -= 10

    if sell_count > buy_count:
        entry_quality -= 15

    if reversal_score < 30:
        entry_quality -= 10

    entry_quality = max(0, min(100, entry_quality))

    if entry_quality >= 75:
        entry_quality_text = "отличная точка входа"
    elif entry_quality >= 60:
        entry_quality_text = "хорошая точка входа"
    elif entry_quality >= 40:
        entry_quality_text = "среднее качество входа"
    elif entry_quality >= 20:
        entry_quality_text = "точка входа слабая"
    else:
        entry_quality_text = "вход сейчас невыгоден"

    scalp_score = 0

    if reversal_score >= 40:
        scalp_score += 20

    if bos_signal == "BUY":
        scalp_score += 20

    if macd_histogram > 0:
        scalp_score += 15

    if 45 < rsi < 70:
        scalp_score += 10

    if price > bb_middle:
        scalp_score += 10

    if volume_signal == "BUY":
        scalp_score += 10

    if rr < 1:
        scalp_score -= 15

    if price >= resistance * 0.98:
        scalp_score -= 20

    if "🔴" in btc_trend:
        scalp_score -= 10

    scalp_score = max(0, min(100, scalp_score))

    scalp_entry = price
    scalp_stop = price - (atr * 0.6)
    scalp_target = price + (atr * 0.8)

    if scalp_score >= 70:
        scalp_text = "скальп-сетап сильный, но вход только с контролем риска"
    elif scalp_score >= 50:
        scalp_text = "есть умеренный скальп-сетап"
    elif scalp_score >= 30:
        scalp_text = "слабый скальп-сетап, лучше ждать подтверждения"
    else:
        scalp_text = "скальп-сетап отсутствует"

    if price <= entry_high:
        entry_text = "цена находится в зоне входа"
    elif price <= entry_high * 1.02:
        entry_text = "цена немного выше зоны входа"
    else:
        entry_text = "цена сильно ушла от точки входа"

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

    if stoch_rsi > 80:
        sell_count += 1
        stoch_rsi_text = "Stochastic RSI в зоне перекупленности — риск отката повышен"
    elif stoch_rsi < 20:
        buy_count += 1
        stoch_rsi_text = "Stochastic RSI в зоне перепроданности — возможен локальный отскок"
    elif 40 <= stoch_rsi <= 60:
        neutral_count += 1
        stoch_rsi_text = "Stochastic RSI в нейтральной зоне"
    elif stoch_rsi > 60:
        buy_count += 1
        stoch_rsi_text = "Stochastic RSI выше средней зоны — импульс умеренно бычий"
    else:
        neutral_count += 1
        stoch_rsi_text = "Stochastic RSI ниже средней зоны — импульс слабее"

    if price > vwap:
        buy_count += 1
        vwap_text = "цена выше VWAP — покупатель контролирует рынок"
    elif price < vwap:
        sell_count += 1
        vwap_text = "цена ниже VWAP — продавец контролирует рынок"
    else:
        neutral_count += 1
        vwap_text = "цена около VWAP — баланс спроса и предложения"

    if fib_382 <= price <= fib_618:
        buy_count += 1
        fib_text = "цена в зоне Fibonacci 0.382–0.618 — нормальная зона отката"
    elif price > fib_236:
        neutral_count += 1
        fib_text = "цена выше Fibonacci 0.236 — актив находится высоко, вход после роста рискованнее"
    elif price < fib_786:
        sell_count += 1
        fib_text = "цена ниже Fibonacci 0.786 — структура слабая"
    else:
        neutral_count += 1
        fib_text = "цена между ключевыми Fibonacci уровнями — сигнал нейтральный"

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

    consensus_total = buy_count + sell_count + neutral_count

    if consensus_total > 0:
        bullish_percent = round((buy_count / consensus_total) * 100)
        bearish_percent = round((sell_count / consensus_total) * 100)
        neutral_percent = round((neutral_count / consensus_total) * 100)
    else:
        bullish_percent = 0
        bearish_percent = 0
        neutral_percent = 0

    if bullish_percent > bearish_percent and rr >= 1:
        consensus_text = "техническая картина умеренно бычья, вход возможен только при подтверждении"
    elif bullish_percent > bearish_percent and rr < 1:
        consensus_text = "индикаторы больше за рост, но сделка сейчас некачественная из-за плохого Risk/Reward"
    elif bearish_percent > bullish_percent:
        consensus_text = "техническая картина слабая, риск входа повышен"
    else:
        consensus_text = "сигналы смешанные, лучше ждать подтверждения"

    total_votes = buy_count + sell_count + neutral_count

    if total_votes == 0:
        buy_score = 5
    else:
        buy_score = round((buy_count / total_votes) * 10, 1)

    green_count = int(round(buy_score))
    score_bar = "🟢" * green_count + "⚪️" * (10 - green_count)

    if (
        buy_score >= 7
        and reversal_score >= 70
        and rr >= 2
        and trend_score >= 60
        and entry_quality >= 60
):
        decision = "🟢 STRONG BUY"

    elif (
        buy_score >= 6
        and rr >= 1.5
        and trend_score >= 50
        and entry_quality >= 50
):
        decision = "🟢 NORMAL BUY"

    elif (
        trend_score >= 70
        and relative_strength_signal == "BUY"
        and entry_quality < 50
):
        decision = "🟡 WAIT PULLBACK"

    elif (
        reversal_score >= 60
        and rr >= 1
        and entry_quality >= 40
):
        decision = "🟡 RISK ENTRY"

    elif (
        trend_score >= 45
        and relative_strength_signal == "BUY"
):
        decision = "🟡 WATCHLIST"

    elif (
        timeframe == "15m"
        and scalp_score >= 55
):
        decision = "🟡 SCALP SETUP"

    else:
        decision = "🔴 NO TRADE"

    if decision == "🟢 STRONG BUY":
        timing_now = "🟢 Вход возможен"
        timing_next = "Можно искать вход сейчас или на небольшом откате"
        entry_condition = "Сетап сильный, но риск всё равно контролировать"

    elif decision == "🟢 NORMAL BUY":
        timing_now = "🟢 Вход возможен осторожно"
        timing_next = "Лучше дождаться подтверждения или короткого отката"
        entry_condition = "Вход разрешён при сохранении структуры"

    elif decision == "🟡 RISK ENTRY":
        timing_now = "🟡 Вход рискованный"
        timing_next = "Ждать подтверждение 1–2 свечи"
        entry_condition = "Вход только малым риском"

    elif decision == "🟡 WAIT PULLBACK":
        timing_now = "🟡 Ждать откат"
        timing_next = "Искать вход после отката к Entry или нового подтверждения"
        entry_condition = "Монета сильная, но текущая точка входа плохая"

    elif decision == "🟡 WATCHLIST":
        timing_now = "🟡 Наблюдать"
        timing_next = "Ждать улучшения входа или подтверждения движения"
        entry_condition = "Монета интересная, но вход пока не подтверждён"

    elif decision == "🟡 SCALP SETUP":
        timing_now = "🟡 Возможен скальп"
        timing_next = "Работать только короткой сделкой"
        entry_condition = "Скальп только с коротким стопом"

    else:
        timing_now = "🔴 Сейчас не входить"
        timing_next = "Ждать новый сигнал"
        entry_condition = "Вход запрещён, условия слабые"

    signal_id = save_signal(
        symbol=symbol,
        timeframe=timeframe,
        price=price,
        decision=decision,
        buy_score=buy_score,
        trend_score=trend_score,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop,
        tp1=tp1,
        tp2=tp2
    )

    return f"""
Signal ID:
#{signal_id}

{symbol} | {timeframe}

━━━━━━━━━━━━━━
РЕШЕНИЕ

{decision}

━━━━━━━━━━━━━━
РЫНОК

BTC Trend:
{btc_trend}

Relative Strength:
{relative_strength}

Relative Strength вывод:
{relative_strength_text}

Asset Trend 1D:
{trend_1d}

Asset Trend 4H:
{trend_4h}

Asset Trend 1H:
{trend_1h}

MTF вывод:
{mtf_text}

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

Stochastic RSI:
{stoch_rsi}

Stochastic RSI вывод:
{stoch_rsi_text}

VWAP:
{vwap}

VWAP вывод:
{vwap_text}

Fibonacci High:
{fib_high}

Fibonacci Low:
{fib_low}

Fib 0.236:
{fib_236}

Fib 0.382:
{fib_382}

Fib 0.5:
{fib_500}

Fib 0.618:
{fib_618}

Fib 0.786:
{fib_786}

Fibonacci вывод:
{fib_text}

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

Trend Score:
{trend_score} / 100

Trend Score вывод:
{trend_score_text}

Market Structure:

{market_structure}

Structure вывод:

{structure_text}

BOS:
{bos_text}

BOS вывод:
{bos_description}

CHOCH:
{choch_text}

CHOCH вывод:
{choch_description}

Reversal Score:
{reversal_score} / 100

Reversal вывод:
{reversal_text}

Market Phase:
{market_phase}

Market Phase вывод:
{market_phase_text}

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

Entry Quality:
{entry_quality} / 100

Entry Quality вывод:
{entry_quality_text}

Scalp Score:
{scalp_score} / 100

Scalp Entry:
{round(scalp_entry, 4)}

Scalp Stop:
{round(scalp_stop, 4)}

Scalp Target:
{round(scalp_target, 4)}

Scalp вывод:
{scalp_text}

━━━━━━━━━━━━━━
ТАЙМИНГ ВХОДА

Сейчас:
{timing_now}

Через 1–2 часа:
{timing_next}

Условие входа:
{entry_condition}

━━━━━━━━━━━━━━
TECHNICAL CONSENSUS

Bullish:
{bullish_percent}%

Bearish:
{bearish_percent}%

Neutral:
{neutral_percent}%

Вывод:
{consensus_text}

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

def build_scalp_analysis(symbol, timeframe):
    highs, lows, closes, volumes = get_okx_candles(symbol, timeframe)

    price = closes[-1]

    rsi = calculate_rsi(closes)
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    macd, macd_signal, macd_histogram = calculate_macd(closes)
    atr = calculate_atr(highs, lows, closes)
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(closes)
    support, resistance = get_levels(highs, lows, closes)
    current_volume, avg_volume, volume_text, volume_status, volume_signal = analyze_volume(volumes)
    relative_strength, relative_strength_text, relative_strength_signal = get_relative_strength(symbol, timeframe)

    scalp_score = 0

    if relative_strength_signal == "BUY":
        scalp_score += 20

    if volume_signal == "BUY":
        scalp_score += 20
    elif volume_signal == "NEUTRAL":
        scalp_score += 10

    if macd_histogram > 0:
        scalp_score += 15

    if price > ema20:
        scalp_score += 15

    if ema20 > ema50:
        scalp_score += 10

    if 45 <= rsi <= 70:
        scalp_score += 10

    if price > bb_middle:
        scalp_score += 10

    if price >= resistance * 0.98:
        scalp_score -= 25

    scalp_score = max(0, min(100, scalp_score))

    scalp_entry = price
    scalp_stop = price - (atr * 0.6)
    scalp_target = price + (atr * 0.9)

    if scalp_score >= 75:
        scalp_status = "🟢 Скальп возможен"
    elif scalp_score >= 55:
        scalp_status = "🟡 Скальп рискованный"
    else:
        scalp_status = "🔴 Скальп не стоит брать"

    return f"""
⚡ СКАЛЬПИНГ | {symbol} | {timeframe}

━━━━━━━━━━━━━━

Статус:
{scalp_status}

Scalp Score:
{scalp_score} / 100

Цена:
{round(price, 4)}

Вход:
{round(scalp_entry, 4)}

Стоп:
{round(scalp_stop, 4)}

Цель:
{round(scalp_target, 4)}

━━━━━━━━━━━━━━

Сила к BTC:
{relative_strength}

Объём:
{volume_text}

RSI:
{round(rsi, 2)}

MACD:
{"🟢 импульс вверх" if macd_histogram > 0 else "🔴 импульс слабый"}

━━━━━━━━━━━━━━

Важно:
Скальпинг — высокий риск.
Работать только малым объёмом.
"""

def build_quick_analysis(symbol, timeframe):
    highs, lows, closes, volumes = get_okx_candles(symbol, timeframe)

    price = closes[-1]

    rsi = calculate_rsi(closes)
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)
    macd, macd_signal, macd_histogram = calculate_macd(closes)
    atr = calculate_atr(highs, lows, closes)
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(closes)
    stoch_rsi = calculate_stoch_rsi(closes)
    vwap = calculate_vwap(highs, lows, closes, volumes)
    fib_high, fib_low, fib_236, fib_382, fib_500, fib_618, fib_786 = calculate_fibonacci_levels(highs, lows)

    support, resistance = get_levels(highs, lows, closes)
    current_volume, avg_volume, volume_text, volume_status, volume_signal = analyze_volume(volumes)

    btc_trend = get_btc_trend(timeframe)
    trend_1d = get_asset_trend(symbol, "1d")
    trend_4h = get_asset_trend(symbol, "4h")
    trend_1h = get_asset_trend(symbol, "1h")

    buy_count = 0
    sell_count = 0
    neutral_count = 0

    # ATR
    atr_percent = (atr / price) * 100
    if atr_percent > 4:
        neutral_count += 1
    elif atr_percent > 2:
        neutral_count += 1
    else:
        buy_count += 1

    # Volume
    if volume_signal == "BUY":
        buy_count += 1
        volume_short = "Сильный"
        volume_emoji = "🟢"
    elif volume_signal == "SELL":
        sell_count += 1
        volume_short = "Слабый"
        volume_emoji = "🔴"
    else:
        neutral_count += 1
        volume_short = "Нейтральный"
        volume_emoji = "🟡"

    # RSI
    if rsi < 30:
        buy_count += 1
    elif rsi > 70:
        sell_count += 1
    elif 45 <= rsi <= 65:
        buy_count += 1
    else:
        neutral_count += 1

    # EMA Trend
    if ema20 > ema50 and ema50 > ema200:
        buy_count += 3
        trend_text = "Бычий"
        trend_emoji = "🟢"
    elif ema20 > ema50:
        buy_count += 2
        trend_text = "Умеренно бычий"
        trend_emoji = "🟢"
    elif ema20 < ema50:
        sell_count += 2
        trend_text = "Медвежий"
        trend_emoji = "🔴"
    else:
        neutral_count += 1
        trend_text = "Нейтральный"
        trend_emoji = "🟡"

    # MACD
    if macd > macd_signal and macd_histogram > 0:
        buy_count += 2
    elif macd < macd_signal and macd_histogram < 0:
        sell_count += 2
    elif macd > 0:
        buy_count += 1
    elif macd < 0:
        sell_count += 1
    else:
        neutral_count += 1

    # Bollinger
    if price >= bb_upper:
        sell_count += 1
    elif price <= bb_lower:
        buy_count += 1
    elif price > bb_middle:
        buy_count += 1
    else:
        neutral_count += 1

    # Stoch RSI
    if stoch_rsi > 80:
        sell_count += 1
    elif stoch_rsi < 20:
        buy_count += 1
    elif stoch_rsi > 60:
        buy_count += 1
    else:
        neutral_count += 1

    # VWAP
    if price > vwap:
        buy_count += 1
    elif price < vwap:
        sell_count += 1
    else:
        neutral_count += 1

    # Fibonacci
    if fib_382 <= price <= fib_618:
        buy_count += 1
    elif price > fib_236:
        neutral_count += 1
    elif price < fib_786:
        sell_count += 1
    else:
        neutral_count += 1

    # EMA200
    if price > ema200:
        buy_count += 1
    else:
        sell_count += 1

    # Support / Resistance / RR
    entry_low = support * 1.005
    entry_high = support * 1.02
    stop = support * 0.98
    tp1 = resistance

    reward = tp1 - price
    risk = price - stop

    if risk > 0:
        rr = round(reward / risk, 2)
    else:
        rr = 0

    if rr >= 2:
        buy_count += 1
    elif rr >= 1:
        neutral_count += 1
    else:
        sell_count += 3

    if price > entry_high * 1.02:
        sell_count += 2
    elif price > entry_high:
        sell_count += 1

    # BTC
    if "🟢" in btc_trend:
        buy_count += 1
        btc_short = "Бычий"
        btc_emoji = "🟢"
    elif "🔴" in btc_trend:
        sell_count += 1
        btc_short = "Медвежий"
        btc_emoji = "🔴"
    else:
        neutral_count += 1
        btc_short = "Нейтральный"
        btc_emoji = "🟡"

    # MTF
    bullish_tf = 0
    bearish_tf = 0

    for tf_trend in [trend_1d, trend_4h, trend_1h]:
        if "🟢" in tf_trend:
            bullish_tf += 1
        elif "🔴" in tf_trend:
            bearish_tf += 1

    if bullish_tf >= 2:
        buy_count += 2
    elif bearish_tf >= 2:
        sell_count += 2
    else:
        neutral_count += 1

    total_votes = buy_count + sell_count + neutral_count

    if total_votes > 0:
        bullish_percent = round((buy_count / total_votes) * 100)
        bearish_percent = round((sell_count / total_votes) * 100)
    else:
        bullish_percent = 0
        bearish_percent = 0

    buy_score = round((buy_count / total_votes) * 10, 1) if total_votes > 0 else 5

    if buy_count > sell_count and buy_score >= 7 and rr >= 1:
        decision = "🟢 Вход возможен"
    elif sell_count > buy_count:
        decision = "🔴 Вход опасен"
    else:
        decision = "🟡 Ждать"

    if decision == "🟡 WAIT PULLBACK":
        timing_now = "🟡 Ждать откат"
        timing_next = "Искать вход после отката к зоне Entry или после нового подтверждения"
        entry_condition = "Монета сильная, но после пампа вход сейчас опасный"

    elif sell_count >= buy_count + 3:
        timing_now = "🔴 Сейчас не входить"
        timing_next = "Ждать улучшения структуры рынка"
        entry_condition = "Вход запрещён, пока общий сигнал медвежий"

    elif rr < 1 and price > entry_high:
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

    try:
        market_data = get_coingecko_market(symbol)
        high_24h = market_data.get("high_24h", max(highs[-24:]))
        low_24h = market_data.get("low_24h", min(lows[-24:]))
        change_24h = market_data.get("price_change_percentage_24h", 0)
    except Exception:
        high_24h = max(highs[-24:])
        low_24h = min(lows[-24:])
        change_24h = 0

    change_emoji = "📈" if change_24h >= 0 else "📉"

    return f"""
{symbol} | {timeframe.upper()} | Цена: {round(price, 4)}

━━━━━━━━━━━━━━

📊 Объём 24ч

Max: {round(high_24h, 4)}      {change_emoji} {round(change_24h, 2)}%
Min: {round(low_24h, 4)}

━━━━━━━━━━━━━━

{decision}

{trend_emoji} Тренд: {trend_text}

{btc_emoji} BTC: {btc_short}
RSI: {rsi}

{volume_emoji} Volume: {volume_short}
R/R: 1:{rr}

Bull: {bullish_percent}%
Bear: {bearish_percent}%

Тайминг:
{timing_now}

━━━━━━━━━━━━━━

🚨 Бот не гарантирует прибыль

⚠️ Все сделки пользователь
совершает самостоятельно
"""

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

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="🧪 TEST ADMIN MESSAGE"
        )
    except Exception as e:
        print("ADMIN TEST ERROR:", e)
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

        quick_text = build_quick_analysis(symbol, timeframe)

        await query.message.reply_text(
            quick_text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📊 Полный анализ",
                            callback_data=f"FULL_{coin}_{timeframe}"
                        ),
                        InlineKeyboardButton(
                            "⚡ Скальпинг",
                            callback_data=f"SCALP_{coin}_{timeframe}"
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

        if data.startswith("FULL_"):
            parts = data.split("_")
            coin = parts[1]
            timeframe = parts[2]

            symbol = coin + "USDT"

            full_text = build_full_analysis(symbol, timeframe)

            if ADMIN_ID:
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"""
        📥 НОВЫЙ АНАЛИЗ

        Пользователь:
        {user_id}

        Монета:
        {symbol}

        Таймфрейм:
        {timeframe}
        """
                    )
                except Exception as e:
                    print("ADMIN SEND ERROR:", e)

            await query.message.reply_text(
                full_text,
                reply_markup=main_keyboard()
            )

            return      

    if data.startswith("SCALP_"):
        parts = data.split("_")
        coin = parts[1]
        timeframe = parts[2]

        symbol = coin + "USDT"

        scalp_text = build_scalp_analysis(symbol, timeframe)

        await query.message.reply_text(scalp_text)
        
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

init_signal_db()

print("SIGNAL DATABASE CREATED")

print("BOT STARTED")

check_signals()

app.run_polling()

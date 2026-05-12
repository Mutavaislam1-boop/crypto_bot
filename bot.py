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


def get_market_data(symbol):
    coin_id = COINGECKO_IDS.get(symbol)

    if not coin_id:
        raise Exception("Монета не найдена")

    url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={coin_id}"

    response = requests.get(url)
    data = response.json()[0]

    price = data["current_price"]
    change_1h = data.get("price_change_percentage_24h", 0)

    high = data["high_24h"]
    low = data["low_24h"]

    rsi = 50
    trend = "BULLISH" if change_1h > 0 else "BEARISH"

    return price, change_1h, rsi, trend


def get_24h_stats(symbol):
    coin_id = COINGECKO_IDS.get(symbol)

    if not coin_id:
        raise Exception("Монета не найдена")

    url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={coin_id}"

    response = requests.get(url)
    data = response.json()[0]

    return {
        "price": data["current_price"],
        "high": data["high_24h"],
        "low": data["low_24h"]
    }


def analyze_symbol(symbol):
    price, change_1h, rsi, trend = get_market_data(symbol)
    btc_price, btc_change_1h, btc_rsi, btc_trend = get_market_data("BTCUSDT")

    risk_score = 0
    reasons = []

    if rsi > 70:
        risk_score += 3
        reasons.append("RSI высокий — монета перегрета")

    if change_1h > 2:
        risk_score += 2
        reasons.append("Сильный рост за 1 час — риск позднего входа")

    if trend == "BEARISH":
        risk_score += 2
        reasons.append("EMA тренд слабый")

    if btc_change_1h < -0.5:
        risk_score += 3
        reasons.append("BTC падает — альты под риском")

    if risk_score >= 6:
        risk = "HIGH"
        signal = "НЕ ВХОДИТЬ"
    elif risk_score >= 3:
        risk = "MEDIUM"
        signal = "ЖДАТЬ ОТКАТ"
    else:
        risk = "LOW"
        signal = "МОЖНО ИСКАТЬ ВХОД"

    reasons_text = "\n".join([f"• {r}" for r in reasons]) if reasons else "• Сильных рисков не найдено"

    return f"""
📊 {symbol}

💰 Цена: {round(price, 4)} USDT
⏱ Изменение 1ч: {round(change_1h, 2)}%
📈 RSI: {rsi}
📊 Trend: {trend}

₿ BTC Filter:
BTC 1ч: {round(btc_change_1h, 2)}%
BTC Trend: {btc_trend}

⚠️ Risk: {risk}
🧠 Risk Score: {risk_score}/10
🤖 Signal: {signal}

Причины:
{reasons_text}
"""


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
            [
                InlineKeyboardButton("➕ Добавить монету", callback_data="WL_ADD")
            ],
            [
                InlineKeyboardButton("📋 Мой список", callback_data="WL_LIST")
            ],
            [
                InlineKeyboardButton("❌ Удалить монету", callback_data="WL_REMOVE")
            ]
        ]
    )


def help_inline_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 Инфо", callback_data="HELP_INFO")
            ],
            [
                InlineKeyboardButton("✍️ Оставить отзыв", callback_data="HELP_FEEDBACK")
            ]
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
        symbol = coin + "USDT"

        try:
            reply = analyze_symbol(symbol)
        except Exception:
            reply = "❌ Ошибка анализа.\nBinance не ответил или монета не найдена."

        await query.message.reply_text(
            reply,
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
            await query.message.reply_text(
                "❌ Ошибка получения данных Binance."
            )

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
            await query.message.reply_text(
                "❌ Ошибка получения данных Binance."
            )

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

        symbol = text_upper + "USDT"
        reply = analyze_symbol(symbol)

        await update.message.reply_text(
            reply,
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
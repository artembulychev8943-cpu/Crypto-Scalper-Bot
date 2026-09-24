import os, sys, time, threading, ccxt, schedule, telebot, pandas as pd
from telebot import types

# === ИНИЦИАЛИЗАЦИЯ ===
TELEGRAM_TOKEN, CHAT_ID_ENV = os.getenv('TELEGRAM_TOKEN'), os.getenv('CHAT_ID')
if not TELEGRAM_TOKEN or not CHAT_ID_ENV: sys.exit(1)
CHAT_ID, bot = int(CHAT_ID_ENV), telebot.TeleBot(TELEGRAM_TOKEN)

def send_tg_message(text, reply_markup=None):
    try: bot.send_message(CHAT_ID, text, parse_mode='Markdown', reply_markup=reply_markup)
    except: pass

# === НАСТРОЙКИ СПОТОВОГО БОТА ===
EXCHANGE_NAME = 'bingx'  
TIMEFRAME = '5m'         
CANDLE_LIMIT = 50        

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'POL/USDT', 'NEAR/USDT', 'UNI/USDT', 'LTC/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'SUI/USDT']

# СДЕЛАЛИ НАСТРОЙКИ БОЛЕЕ ЧАСТЫМИ ДЛЯ СПОТА
RSI_OVERSOLD, RSI_OVERBOUGHT, MFI_OVERSOLD, MFI_OVERBOUGHT = 35, 70, 28, 80
TAKE_PROFIT_PCT, STOP_LOSS_PCT = 0.03, 0.015   
active_positions = {s: None for s in SYMBOLS}

exchange = getattr(ccxt, EXCHANGE_NAME)({
    'apiKey': os.getenv('BINGX_API_KEY'), 'secret': os.getenv('BINGX_SECRET_KEY'),
    'enableRateLimit': True, 'options': {'defaultType': 'spot'}  # СТРОГО СПОТ
})

# --- ЧИСТАЯ МАТЕМАТИКА ---
def calculate_rsi_series(prices, period=14):
    deltas = pd.Series(prices).diff().dropna()
    gain, loss = deltas.clip(lower=0), -deltas.clip(upper=0)
    rs = gain.ewm(com=period-1, adjust=False).mean() / loss.ewm(com=period-1, adjust=False).mean()
    return 100 - (100 / (1 + rs))

def calculate_mfi_series(high, low, close, volume, period=14):
    tp = (high + low + close) / 3
    mf = tp * volume
    delta = tp.diff()
    pf, nf = pd.Series(0.0, index=tp.index), pd.Series(0.0, index=tp.index)
    pf[delta > 0], nf[delta < 0] = mf[delta > 0], mf[delta < 0]
    return 100 - (100 / (1 + (pf.rolling(period).sum() / nf.rolling(period).sum())))

def get_market_data_single(symbol):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        rsi_s = calculate_rsi_series(df['close'])
        mfi_s = calculate_mfi_series(df['high'], df['low'], df['close'], df['volume'])
        latest = df.iloc[-1].copy()
        latest['RSI'], latest['MFI'] = rsi_s.iloc[-1], mfi_s.iloc[-1]
        return latest
    except: return None

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("📊 Баланс спота", "🔥 Горячие монеты", "🤖 Статус ИИ")
    return markup

# --- ТЕЛЕГРАМ ИНТЕРФЕЙС ---
@bot.message_handler(func=lambda m: m.chat.id == CHAT_ID)
def handle_buttons(message):
    t = message.text
    if t in ["/start", "🤖 Статус ИИ"]:
        bot.reply_to(message, "🤖 *ИИ-Скальпер на Споте BingX активен!*\nКаждую минуту проверяю 20 монет на таймфрейме 5м.", parse_mode='Markdown', reply_markup=get_main_keyboard())
    elif t == "📊 Баланс спота":
        try:
            b = exchange.fetch_balance()
            uf, ut = b['free'].get('USDT', 0.0), b['total'].get('USDT', 0.0)
            ac = sum(1 for s in SYMBOLS if active_positions[s] is not None)
            bot.reply_to(message, f"📊 *Спот кошелек BingX:*\n💵 Свободно: \${uf:.2f} USDT\n💰 Всего баланс: \${ut:.2f} USDT\n💼 Открыто ИИ-сделок: {ac} из 20", parse_mode='Markdown')
        except Exception as e: bot.reply_to(message, f"❌ Ошибка API: {e}")
    elif t == "🔥 Горячие монеты":
        bot.reply_to(message, "🔍 Сканирую спотовый рынок топ-20...")
        rep, hot = "🔍 *Горячие монеты (RSI < 45):*\n\n", False
        for s in SYMBOLS:
            d = get_market_data_single(s)
            if d and d['RSI'] < 45:
                hot = True
                rep += f"🔸 *{s.split('/')}*: {d['close']} | RSI: {d['RSI']:.1f} | MFI: {d['MFI']:.1f}\n"
            time.sleep(0.1)
        if not hot: rep += "🟢 Все монеты в стабильной зоне (RSI > 45)."
        bot.send_message(CHAT_ID, rep, parse_mode='Markdown')

# --- РАБОТА РОБОТА В РЕАЛЬНОМ ВРЕМЕНИ ---
def check_trade_logic():
    global balances, positions
    for s in SYMBOLS:
        try:
            current_data = get_market_data_single(s)
            if current_data is None: continue
            cp, r, m = current_data['close'], current_data['RSI'], current_data['MFI']

            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {s} | RSI: {r:.1f} | MFI: {m:.1f}")

            if active_positions[s] is None:
                if r < RSI_OVERSOLD and m < MFI_OVERSOLD:
                    bal = exchange.fetch_balance()['free'].get('USDT', 0.0)
                    # Выделяем 15% на сделку (около $7.5 от $50), что точно больше лимита BingX в $1
                    amount_usdt = bal * 0.15
                    if amount_usdt >= 1.5:
                        order = exchange.create_market_buy_order(s, amount_usdt)
                        active_positions[s] = {
                            'entry_price': cp,
                            'amount': order['amount'] if 'amount' in order else (amount_usdt / cp)
                        }
                        send_tg_message(f"🛒 *ПОКУПКА НА СПОТЕ BINGX: {s}*\nЦена входа: {cp}")
            else:
                pos = active_positions[s]
                p_change = (cp - pos['entry_price']) / pos['entry_price']
                if p_change >= TAKE_PROFIT_PCT or p_change <= -STOP_LOSS_PCT or r > RSI_OVERBOUGHT or m > MFI_OVERBOUGHT:
                    exchange.create_market_sell_order(s, pos['amount'])
                    reason = "🟢 TP" if p_change > 0 else "🔴 SL"
                    send_tg_message(f"💰 *ПРОДАЖА НА СПОТЕ BINGX: {s}* ({reason})\nРезультат: {p_change*100:+.2f}%")
                    active_positions[s] = None
        except: pass
        time.sleep(0.5)

def run_scheduler():
    schedule.every(1).minutes.do(check_trade_logic)
    while True: schedule.run_pending(); time.sleep(1)

send_tg_message("🔥 *Бот переведен на активную спотовую торговлю!* Настройки RSI < 35 и MFI < 28 активированы.", reply_markup=get_main_keyboard())
threading.Thread(target=run_scheduler, daemon=True).start()
bot.infinity_polling()

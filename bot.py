import os, sys, time, threading, ccxt, schedule, telebot, pandas as pd
from telebot import types

# === ИНИЦИАЛИЗАЦИЯ ===
TELEGRAM_TOKEN, CHAT_ID_ENV = os.getenv('TELEGRAM_TOKEN'), os.getenv('CHAT_ID')
if not TELEGRAM_TOKEN or not CHAT_ID_ENV: sys.exit(1)
CHAT_ID, bot = int(CHAT_ID_ENV), telebot.TeleBot(TELEGRAM_TOKEN)

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'POL/USDT', 'NEAR/USDT', 'UNI/USDT', 'LTC/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'SUI/USDT']
RSI_OVERSOLD, RSI_OVERBOUGHT, MFI_OVERSOLD, MFI_OVERBOUGHT = 30, 70, 20, 80
TAKE_PROFIT_PCT, STOP_LOSS_PCT = 0.03, 0.015
active_positions = {s: None for s in SYMBOLS}

exchange = getattr(ccxt, 'bingx')({
    'apiKey': os.getenv('BINGX_API_KEY'), 'secret': os.getenv('BINGX_SECRET_KEY'),
    'enableRateLimit': True, 'options': {'defaultType': 'spot'}
})

def calculate_indicators(df, period=14):
    deltas = df['close'].diff().dropna()
    g, l = deltas.clip(lower=0), -deltas.clip(upper=0)
    rs = g.ewm(com=period-1, adjust=False).mean() / l.ewm(com=period-1, adjust=False).mean()
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    
    tp = (df['high'] + df['low'] + df['close']) / 3
    mf = tp * df['volume']
    dt = tp.diff()
    pf, nf = pd.Series(0.0, index=tp.index), pd.Series(0.0, index=tp.index)
    pf[dt > 0], nf[dt < 0] = mf[dt > 0], mf[dt < 0]
    mfi = (100 - (100 / (1 + (pf.rolling(period).sum() / nf.rolling(period).sum())))).iloc[-1]
    return rsi, mfi

def get_market_data(symbol, limit=1000):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=limit)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        r, m = calculate_indicators(df)
        return {'close': df['close'].iloc[-1], 'RSI': r, 'MFI': m, 'df': df}
    except: return None

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("📊 Баланс портфеля", "🔥 Горячие монеты", "🔄 Запустить Бэктест", "🤖 Статус ИИ")
    return markup

# --- ТЕЛЕГРАМ ИНТЕРФЕЙС ---
@bot.message_handler(func=lambda m: m.chat.id == CHAT_ID)
def handle_buttons(message):
    t = message.text
    if t in ["/start", "🤖 Статус ИИ"]:
        bot.reply_to(message, "🤖 *ИИ-Скальпер BingX активен!*\nКаждую минуту проверяю 20 монет.", parse_mode='Markdown', reply_markup=get_main_keyboard())
    elif t == "📊 Баланс портфеля":
        try:
            b = exchange.fetch_balance()
            uf, ut = b['free'].get('USDT', 0.0), b['total'].get('USDT', 0.0)
            ac = sum(1 for s in SYMBOLS if active_positions[s] is not None)
            bot.reply_to(message, f"📊 *Спот BingX:*\n💵 Свободно: \${uf:.2f} USDT\n💰 Всего: \${ut:.2f} USDT\n💼 Сделок: {ac} из 20", parse_mode='Markdown')
        except Exception as e: bot.reply_to(message, f"❌ Ошибка API: {e}")
    elif t == "🔥 Горячие монеты":
        bot.reply_to(message, "🔍 Сканирую топ-20, подождите...")
        rep, hot = "🔍 *Горячие монеты (RSI < 45):*\n\n", False
        for s in SYMBOLS:
            d = get_market_data(s, limit=50)
            if d and d['RSI'] < 45:
                hot = True
                rep += f"🔸 *{s.split('/')[0]}*: {d['close']} | RSI: {d['RSI']:.1f} | MFI: {d['MFI']:.1f}\n"
            time.sleep(0.1)
        if not hot: rep += "🟢 Все монеты в стабильной зоне (RSI > 45)."
        bot.send_message(CHAT_ID, rep, parse_mode='Markdown')
    elif t == "🔄 Запустить Бэктест":
        bot.reply_to(message, "⏳ Запущен бэктест топ-20 за 3.5 дня. Считаю...")
        tf, gt, gw = 0.0, 0, 0
        for s in SYMBOLS:
            try:
                d = get_market_data(s, limit=1000)
                if not d: continue
                df_bt = d['df'].dropna().reset_index(drop=True)
                df_bt['RSI'], df_bt['MFI'] = calculate_indicators(df_bt) # Передаем df_bt
                bb, pos = 1000.0, None
                for i in range(len(df_bt)):
                    cp, r, m = df_bt.loc[i, 'close'], df_bt.loc[i, 'RSI'], df_bt.loc[i, 'MFI']
                    if pos is None and r < RSI_OVERSOLD and m < MFI_OVERSOLD:
                        pos, bb = {'ep': cp, 'a': bb / cp}, 0.0
                    elif pos is not None:
                        pc = (cp - pos['ep']) / pos['ep']
                        if pc >= TAKE_PROFIT_PCT or pc <= -STOP_LOSS_PCT or r > RSI_OVERBOUGHT or m > MFI_OVERBOUGHT:
                            bb, gt, gw, pos = pos['a'] * cp, gt + 1, gw + (1 if pc > 0 else 0), None
                tf += bb if pos is None else pos['a'] * df_bt.iloc[-1]['close']
            except: tf += 1000.0
            time.sleep(0.1)
        res = ((tf - 20000.0) / 20000.0) * 100
        wr = (gw / gt * 100) if gt > 0 else 0
        bot.send_message(CHAT_ID, f"📈 *Итог бэктеста:*\n💵 Результат: {res:+.2f}%\n🔄 Сделок: {gt}\n🎯 Win Rate: {wr:.1f}%", parse_mode='Markdown')

# --- ТОРГОВАЯ ЛОГИКА ---
def check_trade_logic():
    for s in SYMBOLS:
        try:
            d = get_market_data(s, limit=50)
            if not d: continue
            cp, r, m = d['close'], d['RSI'], d['MFI']
            if active_positions[s] is None:
                if r < RSI_OVERSOLD and m < MFI_OVERSOLD:
                    bal = exchange.fetch_balance()['free'].get('USDT', 0.0)
                    amt = bal * 0.05
                    if amt >= 5.0:
                        order = exchange.create_market_buy_order(s, amt)
                        active_positions[s] = {'ep': cp, 'a': order.get('amount', amt / cp)}
                        bot.send_message(CHAT_ID, f"🛒 *ПОКУПКА BINGX: {s}*\nЦена: {cp}")
            else:
                pos = active_positions[s]
                pc = (cp - pos['ep']) / pos['ep']
                if pc >= TAKE_PROFIT_PCT or pc <= -STOP_LOSS_PCT or r > RSI_OVERBOUGHT or m > MFI_OVERBOUGHT:
                    exchange.create_market_sell_order(s, pos['a'])
                    bot.send_message(CHAT_ID, f"💰 *ПРОДАЖА BINGX: {s}*\nРезультат: {pc*100:+.2f}%")
                    active_positions[s] = None
        except: pass
        time.sleep(0.3)

def run_scheduler():
    schedule.every(1).minutes.do(check_trade_logic)
    while True: schedule.run_pending(); time.sleep(1)

bot.send_message(CHAT_ID, f"🚀 *Бот успешно перезапущен!* Код оптимизирован под лимиты сервера.", reply_markup=get_main_keyboard())
threading.Thread(target=run_scheduler, daemon=True).start()
bot.infinity_polling()

import os
import sys
import time
import threading
import ccxt
import pandas as pd
import schedule
import telebot

# === НАСТРОЙКИ TELEGRAM ===
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID_ENV = os.getenv('CHAT_ID')

if not TELEGRAM_TOKEN or not CHAT_ID_ENV:
    print("Ошибка: Переменные окружения не заполнены на хостинге!")
    sys.exit(1)

CHAT_ID = int(CHAT_ID_ENV)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

def send_tg_message(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")

# === НАСТРОЙКИ ТОРГОВОГО БОТА ===
EXCHANGE_NAME = 'bingx'  
TIMEFRAME = '5m'         
CANDLE_LIMIT = 50        

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT',
    'POL/USDT', 'NEAR/USDT', 'UNI/USDT', 'LTC/USDT', 'APT/USDT',
    'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'SUI/USDT'
]

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

MFI_PERIOD = 14
MFI_OVERSOLD = 20
MFI_OVERBOUGHT = 80

TAKE_PROFIT_PCT = 0.03  
STOP_LOSS_PCT = 0.015   

# Инициализация биржи боевыми ключами
exchange = getattr(ccxt, EXCHANGE_NAME)({
    'apiKey': os.getenv('BINGX_API_KEY'),
    'secret': os.getenv('BINGX_SECRET_KEY'),
    'enableRateLimit': True,  
    'options': {
        'defaultType': 'spot'
    }
})

active_positions = {symbol: None for symbol in SYMBOLS}

# --- ЧИСТАЯ МАТЕМАТИКА ---
def calculate_rsi_series(prices, period=14):
    deltas = pd.Series(prices).diff().dropna()
    gain = deltas.clip(lower=0)
    loss = -deltas.clip(upper=0)
    avg_gain = gain.ewm(com=period-1, adjust=False).mean()
    avg_loss = loss.ewm(com=period-1, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_mfi_series(high, low, close, volume, period=14):
    typical_price = (high + low + close) / 3
    money_flow = typical_price * volume
    delta = typical_price.diff()
    pos_flow = pd.Series(0.0, index=typical_price.index)
    neg_flow = pd.Series(0.0, index=typical_price.index)
    pos_flow[delta > 0] = money_flow[delta > 0]
    neg_flow[delta < 0] = money_flow[delta < 0]
    pos_mf = pos_flow.rolling(window=period).sum()
    neg_mf = neg_flow.rolling(window=period).sum()
    m_ratio = pos_mf / neg_mf
    return 100 - (100 / (1 + m_ratio))

def get_market_data_single(symbol):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        rsi_s = calculate_rsi_series(df['close'], period=RSI_PERIOD)
        mfi_s = calculate_mfi_series(df['high'], df['low'], df['close'], df['volume'], period=MFI_PERIOD)
        latest = df.iloc[-1].copy()
        latest['RSI'] = rsi_s.iloc[-1]
        latest['MFI'] = mfi_s.iloc[-1]
        return latest
    except Exception:
        return None

# --- ТЕЛЕГРАМ КОМАНДЫ ---
@bot.message_handler(commands=['start', 'balance', 'backtest'])
def send_balance(message):
    if message.chat.id != CHAT_ID:
        return
    try:
        # 1. Запрашиваем реальный баланс
        fetch_bal = exchange.fetch_balance()
        usdt_free = fetch_bal['free'].get('USDT', 0.0)
        
        report = f"📊 *Реальный баланс BingX:*\n💵 Свободно: \${usdt_free:.2f} USDT\n\n"
        report += f"🔍 *Живой ИИ-анализ рынка (Таймфрейм 5м):*\n"
        
        # 2. Прямо в этой же команде проверяем топ-монеты, чтобы доказать, что бот анализирует рынок
        for symbol in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
            data = get_market_data_single(symbol)
            if data is not None:
                report += f"🔹 *{symbol}*: Цена {data['close']} | RSI: {data['RSI']:.1f} | MFI: {data['MFI']:.1f}\n"
            else:
                report += f"🔹 *{symbol}*: Ошибка сети биржи ⚠️\n"
                
        bot.reply_to(message, report, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка подключения к BingX API: {e}")

# --- РАБОТА РОБОТА В РЕАЛЬНОМ ВРЕМЕНИ ---
def check_trade_logic():
    for symbol in SYMBOLS:
        try:
            current_data = get_market_data_single(symbol)
            if current_data is None:
                continue
            c_price = current_data['close']
            rsi = current_data['RSI']
            mfi = current_data['MFI']

            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {symbol} | RSI: {rsi:.1f} | MFI: {mfi:.1f}")

            if active_positions[symbol] is None:
                if rsi < RSI_OVERSOLD and mfi < MFI_OVERSOLD:
                    # Логика реальной покупки (если на балансе > 10$)
                    bal = exchange.fetch_balance()['free'].get('USDT', 0.0)
                    amount_usdt = bal * 0.05
                    if amount_usdt >= 5.0:
                        order = exchange.create_market_buy_order(symbol, amount_usdt)
                        active_positions[symbol] = {
                            'entry_price': c_price,
                            'amount': order['amount'] if 'amount' in order else (amount_usdt / c_price)
                        }
                        send_tg_message(f"🛒 *ПОКУПКА BINGX: {symbol}*\nЦена: {c_price}")
            else:
                pos = active_positions[symbol]
                p_change = (c_price - pos['entry_price']) / pos['entry_price']
                
                if p_change >= TAKE_PROFIT_PCT or p_change <= -STOP_LOSS_PCT or rsi > RSI_OVERBOUGHT or mfi > MFI_OVERBOUGHT:
                    exchange.create_market_sell_order(symbol, pos['amount'])
                    reason = "🟢 TP" if p_change > 0 else "🔴 SL"
                    send_tg_message(f"💰 *ПРОДАЖА BINGX: {symbol}* ({reason})\nРезультат: {p_change*100:+.2f}%")
                    active_positions[symbol] = None
        except Exception:
            pass
        time.sleep(0.5)

def run_scheduler():
    schedule.every(1).minutes.do(check_trade_logic)
    while True:
        schedule.run_pending()
        time.sleep(1)

send_tg_message("🚀 *Бот успешно перезапущен на хостинге!*")

scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

bot.infinity_polling()

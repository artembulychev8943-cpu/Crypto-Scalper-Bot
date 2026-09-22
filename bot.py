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
    """Отправка сообщений в Telegram"""
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Ошибка отправки сообщения в Telegram: {e}")

def handle_exception(exc_type, exc_value, exc_traceback):
    """Перехват критических ошибок и отправка их владельцу"""
    error_msg = f"❌ *Критический сбой бота на хостинге!*\n\nТип: {exc_type.__name__}\nОшибка: {exc_value}"
    send_tg_message(error_msg)
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = handle_exception

# === НАСТРОЙКИ ТОРГОВОГО БОТА ===
EXCHANGE_NAME = 'bybit'  
SYMBOL = 'SOL/USDT'      
TIMEFRAME = '5m'         
CANDLE_LIMIT = 50        

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

MFI_PERIOD = 14
MFI_OVERSOLD = 20
MFI_OVERBOUGHT = 80

TAKE_PROFIT_PCT = 0.03  
STOP_LOSS_PCT = 0.015   

# Стартовый баланс для демо-торговли
balance = 1000.0  
position = None   

exchange = getattr(ccxt, EXCHANGE_NAME)()

# --- МАТЕМАТИЧЕСКИЙ РАСЧЕТ ИНДИКАТОРОВ (ЧИСТЫЙ PYTHON) ---
def calculate_rsi(prices, period=14):
    """Расчет индикатора RSI"""
    deltas = pd.Series(prices).diff().dropna()
    gain = deltas.clip(lower=0)
    loss = -deltas.clip(upper=0)
    
    avg_gain = gain.ewm(com=period-1, adjust=False).mean()
    avg_loss = loss.ewm(com=period-1, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def calculate_mfi(high, low, close, volume, period=14):
    """Расчет индикатора MFI (Money Flow Index)"""
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
    mfi = 100 - (100 / (1 + m_ratio))
    return mfi.iloc[-1]

# --- ОБРАБОТКА КОМАНД В ТЕЛЕГРАМ ---
@bot.message_handler(commands=['start', 'balance'])
def send_balance(message):
    """Ответ на команду /balance или /start"""
    if message.chat.id == CHAT_ID:
        if position is None:
            status_text = f"💰 *Ваш баланс:* \${balance:.2f}\nВ данный момент открытых сделок нет."
        else:
            current_data = get_market_data()
            if current_data is not None:
                current_price = current_data['close']
                entry_price = position['entry_price']
                profit_pct = ((current_price - entry_price) / entry_price) * 100
                status_text = (f"📊 *Текущая сделка по {SYMBOL}:*\n"
                               f"🔹 Цена входа: {entry_price}\n"
                               f"🔹 Текущая цена: {current_price}\n"
                               f"📈 Текущий профит: {profit_pct:+.2f}%\n"
                               f"💰 Баланс в монетах: {position['amount']:.3f} SOL")
            else:
                status_text = "Сделка открыта, но не удалось получить цену с биржи."
        
        bot.reply_to(message, status_text, parse_mode='Markdown')

def get_market_data():
    """Скачивание свечей и запуск функций расчета"""
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        rsi_val = calculate_rsi(df['close'], period=RSI_PERIOD)
        mfi_val = calculate_mfi(df['high'], df['low'], df['close'], df['volume'], period=MFI_PERIOD)
        
        latest = df.iloc[-1].copy()
        latest['RSI'] = rsi_val
        latest['MFI'] = mfi_val
        return latest
    except Exception as e:
        print(f"Ошибка получения рыночных данных: {e}")
        return None

def check_trade_logic():
    """Основная логика анализа рынка, покупки и продажи"""
    global balance, position
    
    current_data = get_market_data()
    if current_data is None:
        return
        
    current_price = current_data['close']
    rsi = current_data['RSI']
    mfi = current_data['MFI']
    
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Цена: {current_price} | RSI: {rsi:.2f} | MFI: {mfi:.2f}")

    # Сценарий 1: Поиск точки входа
    if position is None:
        if rsi < RSI_OVERSOLD and mfi < MFI_OVERSOLD:
            amount_to_buy = balance / current_price
            position = {
                'entry_price': current_price,
                'amount': amount_to_buy
            }
            balance = 0.0
            
            msg = (f"🛒 *СИГНАЛ НА ПОКУПКУ*\n\n"
                   f"🔹 *Инструмент:* {SYMBOL}\n"
                   f"🔹 *Цена входа:* {current_price}\n"
                   f"📊 *Индикаторы:* RSI {rsi:.1f}, MFI {mfi:.1f}\n"
                   f"💰 *Объем сделки:* {amount_to_buy:.3f} SOL")
            send_tg_message(msg)
            
    # Сценарий 2: Проверка выхода из сделки
    else:
        entry_price = position['entry_price']
        amount = position['amount']
        price_change = (current_price - entry_price) / entry_price
        
        is_take_profit = price_change >= TAKE_PROFIT_PCT
        is_stop_loss = price_change <= -STOP_LOSS_PCT
        is_overbought = rsi > RSI_OVERBOUGHT or mfi > MFI_OVERBOUGHT
        
        if is_take_profit or is_stop_loss or is_overbought:
            balance = amount * current_price
            
            if is_take_profit:
                reason = "🟢 Take-Profit"
            elif is_stop_loss:
                reason = "🔴 Stop-Loss"
            else:
                reason = "🟡 Перекупленность рынка"
            
            msg = (f"💰 *СИГНАЛ НА ПРОДАЖУ*\n\n"
                   f"🔹 *Причина:* {reason}\n"
                   f"🔹 *Цена выхода:* {current_price}\n"
                   f"📈 *Результат:* {price_change*100:+.2f}%\n"
                   f"💵 *Текущий баланс:* \${balance:.2f}")
            send_tg_message(msg)
            position = None

def run_scheduler():
    """Запуск планировщика в отдельном потоке"""
    schedule.every(1).minutes.do(check_trade_logic)
    while True:
        schedule.run_pending()
        time.sleep(1)

# Приветственное сообщение владельцу
send_tg_message("🤖 *Крипто-бот успешно запущен на хостинге!*\nСтратегия RSI + MFI (Pure Math) активирована в демо-режиме.\n\nИспользуйте команду `/balance` для проверки.")

# Запуск торговой логики
scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

# Запуск прослушивания сообщений Telegram
try:
    bot.infinity_polling()
except Exception as e:
    print(f"Ошибка пуллинга Telegram: {e}")
    time.sleep(5)

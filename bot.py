import os
import time
import threading
import ccxt
import pandas as pd
import pandas_ta as ta
import schedule
import telebot

# === НАСТРОЙКИ TELEGRAM ===
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID_ENV = os.getenv('CHAT_ID')

if not TELEGRAM_TOKEN or not CHAT_ID_ENV:
    raise ValueError("Критическая ошибка: Переменные окружения TELEGRAM_TOKEN или CHAT_ID не заполнены на хостинге!")

CHAT_ID = int(CHAT_ID_ENV)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

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

def send_tg_message(text):
    """Отправка сообщений в Telegram"""
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Ошибка отправки сообщения в Telegram: {e}")

# --- ОБРАБОТКА КОМАНД В ТЕЛЕГРАМ ---
@bot.message_handler(commands=['start', 'balance'])
def send_balance(message):
    """Ответ на команду /balance или /start"""
    # Проверяем, что пишет именно владелец бота
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
                status_text = "Сделка открыта, но не удалось получить текущую цену с биржи."
        
        bot.reply_to(message, status_text, parse_mode='Markdown')

def get_market_data():
    """Скачивание свечей и расчет индикаторов"""
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        df['RSI'] = ta.rsi(df['close'], length=RSI_PERIOD)
        df['MFI'] = ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=MFI_PERIOD)
        
        return df.iloc[-1]  
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

# Приветственное сообщение
send_tg_message("🤖 *Крипто-бот успешно запущен на хостинге!*\nТорговая стратегия RSI + MFI активирована в демо-режиме.\n\nВы можете отправить команду `/balance` для проверки счета.")

# Запуск торговой логики в фоновом режиме
scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

# Запуск прослушивания сообщений Telegram (чтобы бот отвечал на команды)
try:
    bot.infinity_polling()
except Exception as e:
    print(f"Ошибка пуллинга Telegram: {e}")
    time.sleep(5)

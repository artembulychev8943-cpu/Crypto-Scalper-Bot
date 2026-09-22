import os
import time
import ccxt
import pandas as pd
import pandas_ta as ta
import schedule
import telebot

# === НАСТРОЙКИ TELEGRAM (Данные автоматически берутся из панели хостинга) ===
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID_ENV = os.getenv('CHAT_ID')

# Проверка, что переменные успешно загрузились с хостинга
if not TELEGRAM_TOKEN or not CHAT_ID_ENV:
    raise ValueError("Критическая ошибка: Переменные окружения TELEGRAM_TOKEN или CHAT_ID не заполнены на хостинге!")

CHAT_ID = int(CHAT_ID_ENV)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# === НАСТРОЙКИ ТОРГОВОГО БОТА ===
EXCHANGE_NAME = 'bybit'  # Биржа для чтения графиков
SYMBOL = 'SOL/USDT'      # Торговая пара
TIMEFRAME = '5m'         # Таймфрейм (5 минут)
CANDLE_LIMIT = 50        # Количество свечей для расчета индикаторов

# Параметры индикаторов RSI и MFI
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

MFI_PERIOD = 14
MFI_OVERSOLD = 20
MFI_OVERBOUGHT = 80

# Риск-менеджмент (виртуальный)
TAKE_PROFIT_PCT = 0.03  # +3% к цене входа для фиксации прибыли
STOP_LOSS_PCT = 0.015   # -1.5% от цены входа для ограничения убытков

# Стартовый баланс для демо-торговли (Paper Trading)
balance = 1000.0  
position = None   # Текущий статус позиции (None - сделок нет)

# Инициализация подключения к бирже
exchange = getattr(ccxt, EXCHANGE_NAME)()

def send_tg_message(text):
    """Отправка сообщений в Telegram с защитой от сбоев связи"""
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Ошибка отправки сообщения в Telegram: {e}")

def get_market_data():
    """Скачивание свечей и математический расчет индикаторов"""
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Расчет индикаторов с помощью библиотеки pandas_ta
        df['RSI'] = ta.rsi(df['close'], length=RSI_PERIOD)
        df['MFI'] = ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=MFI_PERIOD)
        
        return df.iloc[-1]  # Возвращаем самую последнюю закрытую свечу
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
    
    # Лог в консоль хостинга, чтобы видеть активность бота
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Цена: {current_price} | RSI: {rsi:.2f} | MFI: {mfi:.2f}")

    # Сценарий 1: Мы не в сделке. Ищем точку входа по RSI + MFI
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
            
    # Сценарий 2: Мы находимся в сделке. Проверяем условия для фиксации результата
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
                   f"💵 *Текущий баланс:* ${balance:.2f}")
            send_tg_message(msg)
            position = None

# Интервал проверки рынка: 1 раз в минуту
schedule.every(1).minutes.do(check_trade_logic)

# Приветственное сообщение при успешном старте скрипта
send_tg_message("🤖 *Крипто-бот успешно запущен на хостинге!*\nТорговая стратегия RSI + MFI активирована в демо-режиме.")

# Бесконечный цикл работы программы
while True:
    schedule.run_pending()
    time.sleep(1)

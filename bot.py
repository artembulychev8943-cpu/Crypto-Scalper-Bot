import time
import ccxt
import pandas as pd
import pandas_ta as ta
import schedule
import telebot

# === НАСТРОЙКИ TELEGRAM ===
TELEGRAM_TOKEN = 'ВАШ_ТГ_ТОКЕН_ИЗ_BOTFATHER'
CHAT_ID = ВАШ_ЛИЧНЫЙ_ТГ_ID  # Обратите внимание: число без кавычек, например 58291033

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# === НАСТРОЙКИ БОТА ===
EXCHANGE_NAME = 'bybit'
SYMBOL = 'SOL/USDT'      
TIMEFRAME = '5m'         
CANDLE_LIMIT = 50        

# Настройки индикаторов
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

MFI_PERIOD = 14
MFI_OVERSOLD = 20
MFI_OVERBOUGHT = 80

# Риск-менеджмент
TAKE_PROFIT_PCT = 0.03  
STOP_LOSS_PCT = 0.015   

# Виртуальный баланс для Paper Trading
balance = 1000.0  
position = None   

exchange = getattr(ccxt, EXCHANGE_NAME)()

def send_tg_message(text):
    """Функция отправки уведомлений в Telegram"""
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Ошибка отправки в TG: {e}")

def get_market_data():
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['RSI'] = ta.rsi(df['close'], length=RSI_PERIOD)
        df['MFI'] = ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=MFI_PERIOD)
        return df.iloc[-1]
    except Exception as e:
        print(f"Ошибка получения данных: {e}")
        return None

def check_trade_logic():
    global balance, position
    
    current_data = get_market_data()
    if current_data is None:
        return
        
    current_price = current_data['close']
    rsi = current_data['RSI']
    mfi = current_data['MFI']
    
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Цена: {current_price} | RSI: {rsi:.2f} | MFI: {mfi:.2f}")

    # 1. Поиск точки входа
    if position is None:
        if rsi < RSI_OVERSOLD and mfi < MFI_OVERSOLD:
            amount_to_buy = balance / current_price
            position = {
                'entry_price': current_price,
                'amount': amount_to_buy
            }
            balance = 0.0
            
            msg = (f"🛒 *СИГНАЛ НА ПОКУПКУ*\n\n"
                   f"🔹 *Пара:* {SYMBOL}\n"
                   f"🔹 *Цена входа:* {current_price}\n"
                   f"📊 *Индикаторы:* RSI {rsi:.1f}, MFI {mfi:.1f}\n"
                   f"💰 *Объем позиции:* {amount_to_buy:.3f} SOL")
            send_tg_message(msg)
            
    # 2. Проверка выхода
    else:
        entry_price = position['entry_price']
        amount = position['amount']
        price_change = (current_price - entry_price) / entry_price
        
        is_take_profit = price_change >= TAKE_PROFIT_PCT
        is_stop_loss = price_change <= -STOP_LOSS_PCT
        is_overbought = rsi > RSI_OVERBOUGHT or mfi > MFI_OVERBOUGHT
        
        if is_take_profit or is_stop_loss or is_overbought:
            balance = amount * current_price
            reason = "🟢 Take-Profit" if is_take_profit else ("🔴 Stop-Loss" if is_stop_loss else "🟡 Перекупленность")
            
            msg = (f"💰 *СИГНАЛ НА ПРОДАЖУ*\n\n"
                   f"🔹 *Причина:* {reason}\n"
                   f"🔹 *Цена выхода:* {current_price}\n"
                   f"📈 *Доходность:* {price_change*100:+.2f}%\n"
                   f"💵 *Текущий баланс:* ${balance:.2f}")
            send_tg_message(msg)
            position = None

# Запуск проверки раз в минуту (оптимально для 5m таймфрейма)
schedule.every(1).minutes.do(check_trade_logic)

send_tg_message("🤖 *Крипто-бот успешно перезапущен!*\nТорговая стратегия RSI + MFI активирована в демо-режиме.")

while True:
    schedule.run_pending()
    time.sleep(1)

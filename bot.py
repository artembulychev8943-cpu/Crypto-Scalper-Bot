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
TIMEFRAME = '5m'         
CANDLE_LIMIT = 50        

# СПИСОК ИЗ 20 ТОП-МОНЕТ ДЛЯ МОНИТОРИНГА
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT',
    'MATIC/USDT', 'NEAR/USDT', 'UNI/USDT', 'LTC/USDT', 'APT/USDT',
    'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'SUI/USDT'
]

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

# Распределение виртуального баланса
START_TOTAL_BALANCE = 1000.0
balance_per_coin = START_TOTAL_BALANCE / len(SYMBOLS)

# Словари балансов и позиций для каждой из 20 монет
balances = {symbol: balance_per_coin for symbol in SYMBOLS}
positions = {symbol: None for symbol in SYMBOLS}

exchange = getattr(ccxt, EXCHANGE_NAME)()

# --- МАТЕМАТИЧЕСКИЙ РАСЧЕТ ИНДИКАТОРОВ (ЧИСТЫЙ PYTHON) ---
def calculate_rsi(prices, period=14):
    deltas = pd.Series(prices).diff().dropna()
    gain = deltas.clip(lower=0)
    loss = -deltas.clip(upper=0)
    avg_gain = gain.ewm(com=period-1, adjust=False).mean()
    avg_loss = loss.ewm(com=period-1, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def calculate_mfi(high, low, close, volume, period=14):
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
    """Ответ на команду /balance"""
    if message.chat.id == CHAT_ID:
        report = "📊 *Текущий статус портфеля (Топ-20):*\n\n"
        total_value = 0.0
        active_trades = 0
        
        for symbol in SYMBOLS:
            pos = positions[symbol]
            if pos is None:
                total_value += balances[symbol]
            else:
                active_trades += 1
                current_data = get_market_data(symbol)
                if current_data is not None:
                    current_price = current_data['close']
                    entry_price = pos['entry_price']
                    profit_pct = ((current_price - entry_price) / entry_price) * 100
                    current_cost = pos['amount'] * current_price
                    total_value += current_cost
                    report += f"🔸 *{symbol}:* В сделке! Профит: {profit_pct:+.2f}% (\${current_cost:.2f})\n"
                else:
                    current_cost = pos['amount'] * pos['entry_price']
                    total_value += current_cost
                    report += f"🔸 *{symbol}:* В сделке (связь ограничена)\n"
                    
        report += f"\n💼 Активных сделок: {active_trades} из {len(SYMBOLS)}"
        report += f"\n💰 *Общая стоимость активов:* \${total_value:.2f}"
        bot.reply_to(message, report, parse_mode='Markdown')

def get_market_data(symbol):
    """Скачивание свечей для конкретной монеты"""
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        rsi_val = calculate_rsi(df['close'], period=RSI_PERIOD)
        mfi_val = calculate_mfi(df['high'], df['low'], df['close'], df['volume'], period=MFI_PERIOD)
        
        latest = df.iloc[-1].copy()
        latest['RSI'] = rsi_val
        latest['MFI'] = mfi_val
        return latest
    except Exception as e:
        print(f"Ошибка получения данных для {symbol}: {e}")
        return None

def check_trade_logic():
    """Последовательный обход всех 20 монет в цикле"""
    global balances, positions
    
    for symbol in SYMBOLS:
        current_data = get_market_data(symbol)
        if current_data is None:
            continue
            
        current_price = current_data['close']
        rsi = current_data['RSI']
        mfi = current_data['MFI']
        
        # Печатаем логи в консоль хостинга (для контроля)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {symbol} | Цена: {current_price} | RSI: {rsi:.2f} | MFI: {mfi:.2f}")

        # Сценарий 1: Ищем точку входа
        if positions[symbol] is None:
            if rsi < RSI_OVERSOLD and mfi < MFI_OVERSOLD:
                if balances[symbol] > 0:
                    amount_to_buy = balances[symbol] / current_price
                    positions[symbol] = {
                        'entry_price': current_price,
                        'amount': amount_to_buy
                    }
                    balances[symbol] = 0.0
                    
                    msg = (f"🛒 *СИГНАЛ НА ПОКУПКУ*\n\n"
                           f"🔹 *Инструмент:* {symbol}\n"
                           f"🔹 *Цена входа:* {current_price}\n"
                           f"📊 *Индикаторы:* RSI {rsi:.1f}, MFI {mfi:.1f}")
                    send_tg_message(msg)
                
        # Сценарий 2: Проверяем выход из сделки
        else:
            pos = positions[symbol]
            entry_price = pos['entry_price']
            amount = pos['amount']
            price_change = (current_price - entry_price) / entry_price
            
            is_take_profit = price_change >= TAKE_PROFIT_PCT
            is_stop_loss = price_change <= -STOP_LOSS_PCT
            is_overbought = rsi > RSI_OVERBOUGHT or mfi > MFI_OVERBOUGHT
            
            if is_take_profit or is_stop_loss or is_overbought:
                balances[symbol] = amount * current_price
                
                if is_take_profit:
                    reason = "🟢 Take-Profit"
                elif is_stop_loss:
                    reason = "🔴 Stop-Loss"
                else:
                    reason = "🟡 Перекупленность рынка"
                
                msg = (f"💰 *СИГНАЛ НА ПРОДАЖУ*\n\n"
                       f"🔹 *Монета:* {symbol}\n"
                       f"🔹 *Причина:* {reason}\n"
                       f"🔹 *Цена выхода:* {current_price}\n"
                       f"📈 *Результат:* {price_change*100:+.2f}%\n"
                       f"💵 *Баланс пары:* \${balances[symbol]:.2f}")
                send_tg_message(msg)
                positions[symbol] = None
        
        # Небольшая пауза между запросами к API биржи, чтобы Bybit не заблокировал за спам
        time.sleep(0.5)

def run_scheduler():
    schedule.every(1).minutes.do(check_trade_logic)
    while True:
        schedule.run_pending()
        time.sleep(1)

# Приветственное сообщение
send_tg_message(f"🚀 *Мультивалютный ИИ-скальпер запущен!*\nВ мониторинг добавлено 20 ТОП-монет.\nНа каждую выделен демо-лимит: \${balance_per_coin:.2f}")

# Запуск потоков
scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

try:
    bot.infinity_polling()
except Exception as e:
    print(f"Ошибка пуллинга Telegram: {e}")
    time.sleep(5)

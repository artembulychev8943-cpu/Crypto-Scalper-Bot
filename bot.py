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

# Список из 20 ТОП-монет для мониторинга
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

balances = {symbol: balance_per_coin for symbol in SYMBOLS}
positions = {symbol: None for symbol in SYMBOLS}

exchange = getattr(ccxt, EXCHANGE_NAME)()

# --- МАТЕМАТИЧЕСКИЙ РАСЧЕТ ИНДИКАТОРОВ (ЧИСТЫЙ PYTHON) ---
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
                current_data = get_market_data_single(symbol)
                if current_data is not None:
                    current_price = current_data['close']
                    entry_price = pos['entry_price']
                    profit_pct = ((current_price - entry_price) / entry_price) * 100
                    current_cost = pos['amount'] * current_price
                    total_value += current_cost
                    report += f"🔸 *{symbol}:* В сделке! Профит: {profit_pct:+.2f}% (\${current_cost:.2f})\n"
                else:
                    total_value += (pos['amount'] * pos['entry_price'])
                    report += f"🔸 *{symbol}:* В сделке (связь ограничена)\n"
                    
        report += f"\n💼 Активных сделок: {active_trades} из {len(SYMBOLS)}"
        report += f"\n💰 *Общая стоимость активов:* \${total_value:.2f}"
        bot.reply_to(message, report, parse_mode='Markdown')

@bot.message_handler(commands=['backtest'])
def run_tg_backtest(message):
    """Запуск бэктеста по запросу из Telegram. Пример: /backtest SOL или /backtest BTC"""
    if message.chat.id != CHAT_ID:
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❌ Укажите монету. Пример:\n`/backtest SOL` или `/backtest BTC/USDT`", parse_mode='Markdown')
        return

    raw_symbol = args[1].upper()
    symbol = raw_symbol if '/' in raw_symbol else f"{raw_symbol}/USDT"

    bot.reply_to(message, f"⏳ Запущен бэктест для *{symbol}* на истории в 1000 свечей (3.5 дня). Подождите несколько секунд...", parse_mode='Markdown')

    try:
        # Скачиваем глубокую историю с биржи
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=1000)
        df_bt = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        df_bt['RSI'] = calculate_rsi_series(df_bt['close'], period=RSI_PERIOD)
        df_bt['MFI'] = calculate_mfi_series(df_bt['high'], df_bt['low'], df_bt['close'], df_bt['volume'], period=MFI_PERIOD)
        df_bt = df_bt.dropna().reset_index(drop=True)

        bt_balance = 1000.0
        bt_position = None
        total_trades = 0
        win_trades = 0

        for i in range(len(df_bt)):
            c_price = df_bt.loc[i, 'close']
            rsi_val = df_bt.loc[i, 'RSI']
            mfi_val = df_bt.loc[i, 'MFI']

            if bt_position is None:
                if rsi_val < RSI_OVERSOLD and mfi_val < MFI_OVERSOLD:
                    bt_position = {'entry_price': c_price, 'amount': bt_balance / c_price}
                    bt_balance = 0.0
            else:
                e_price = bt_position['entry_price']
                amt = bt_position['amount']
                p_change = (c_price - e_price) / e_price

                if p_change >= TAKE_PROFIT_PCT or p_change <= -STOP_LOSS_PCT or rsi_val > RSI_OVERBOUGHT or mfi_val > MFI_OVERBOUGHT:
                    bt_balance = amt * c_price
                    total_trades += 1
                    if p_change > 0:
                        win_trades += 1
                    bt_position = None

        if bt_position is not None:
            bt_balance = bt_position['amount'] * df_bt.iloc[-1]['close']

        profit_pct = ((bt_balance - 1000.0) / 1000.0) * 100
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0

        report = (f"📊 *Результаты бэктеста для {symbol}:*\n\n"
                  f"💰 Стартовый баланс: \$1000.00\n"
                  f"💵 Финальный баланс: \${bt_balance:.2f}\n"
                  f"📈 Чистая прибыль: {profit_pct:+.2f}%\n"
                  f"🔄 Всего сделок: {total_trades}\n"
                  f"🟢 Прибыльных: {win_trades}\n"
                  f"🎯 Win Rate: {win_rate:.1f}%")
        bot.reply_to(message, report, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"❌ Не удалось провести бэктест для {symbol}. Проверьте правильность тикера.\nОшибка: {e}")

def get_market_data_single(symbol):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        rsi_series = calculate_rsi_series(df['close'], period=RSI_PERIOD)
        mfi_series = calculate_mfi_series(df['high'], df['low'], df['close'], df['volume'], period=MFI_PERIOD)
        
        latest = df.iloc[-1].copy()
        latest['RSI'] = rsi_series.iloc[-1]
        latest['MFI'] = mfi_series.iloc[-1]
        return latest
    except Exception as e:
        return None

def check_trade_logic():
    global balances, positions
    
    for symbol in SYMBOLS:
        current_data = get_market_data_single(symbol)
        if current_data is None:
            continue
            
        current_price = current_data['close']
        rsi = current_data['RSI']
        mfi = current_data['MFI']
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {symbol} | Цена: {current_price} | RSI: {rsi:.2f} | MFI: {mfi:.2f}")

        if positions[symbol] is None:
            if rsi < RSI_OVERSOLD and mfi < MFI_OVERSOLD:
                if balances[symbol] > 0:
                    amount_to_buy = balances[symbol] / current_price
                    positions[symbol] = {'entry_price': current_price, 'amount': amount_to_buy}
                    balances[symbol] = 0.0
                    
                    msg = (f"🛒 *СИГНАЛ НА ПОКУПКУ*\n\n"
                           f"🔹 *Инструмент:* {symbol}\n"
                           f"🔹 *Цена входа:* {current_price}\n"
                           f"📊 *Индикаторы:* RSI {rsi:.1f}, MFI {mfi:.1f}")
                    send_tg_message(msg)
                
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
                reason = "🟢 Take-Profit" if is_take_profit else ("🔴 Stop-Loss" if is_stop_loss else "🟡 Перекупленность")
                
                msg = (f"💰 *СИГНАЛ НА ПРОДАЖУ*\n\n"
                       f"🔹 *Монета:* {symbol}\n"
                       f"🔹 *Причина:* {reason}\n"

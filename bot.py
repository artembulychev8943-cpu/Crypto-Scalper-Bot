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
    print("КРИТИЧЕСКАЯ ОШИБКА: Переменные TELEGRAM_TOKEN или CHAT_ID не найдены в панели хостинга!")
    sys.exit(1)

CHAT_ID = int(CHAT_ID_ENV)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

def send_tg_message(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Ошибка отправки сообщения в Telegram: {e}")

# === ПРОВЕРКА КЛЮЧЕЙ BINGX ===
BINGX_KEY = os.getenv('BINGX_API_KEY')
BINGX_SEC = os.getenv('BINGX_SECRET_KEY')

if not BINGX_KEY or not BINGX_SEC:
    print("ВНИМАНИЕ: Переменные BINGX_API_KEY или BINGX_SECRET_KEY отсутствуют!")
    print("Бот запущен в режиме ожидания настройки ключей.")
    # Не даем боту упасть, просто держим его запущенным для логов
    while True:
        time.sleep(10)

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

START_TOTAL_BALANCE = 1000.0
balance_per_coin = START_TOTAL_BALANCE / len(SYMBOLS)

balances = {symbol: balance_per_coin for symbol in SYMBOLS}
positions = {symbol: None for symbol in SYMBOLS}

# Инициализируем биржу боевыми ключами
exchange = getattr(ccxt, EXCHANGE_NAME)({
    'apiKey': BINGX_KEY,
    'secret': BINGX_SEC,
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
@bot.message_handler(commands=['start', 'balance'])
def send_balance(message):
    if message.chat.id != CHAT_ID:
        return
    try:
        fetch_bal = exchange.fetch_balance()
        usdt_free = fetch_bal['free'].get('USDT', 0.0)
        usdt_total = fetch_bal['total'].get('USDT', 0.0)
        
        report = f"📊 *Реальный баланс на BingX:*\n💵 Доступно для сделок: \${usdt_free:.2f} USDT\n💰 Всего на споте: \${usdt_total:.2f} USDT\n\n"
        active_count = sum(1 for sym in SYMBOLS if active_positions[sym] is not None)
        report += f"💼 Активных ИИ-сделок: {active_count} из {len(SYMBOLS)}"
        bot.reply_to(message, report, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка запроса баланса к BingX: {e}")

@bot.message_handler(commands=['backtest'])
def run_tg_backtest(message):
    if message.chat.id != CHAT_ID:
        return
    bot.reply_to(message, f"⏳ Запущен глобальный бэктест по *{len(SYMBOLS)} монетам*. Считаю...", parse_mode='Markdown')
    
    summary_report = "📊 *Глобальный бэктест BingX (за 3.5 дня):*\n\n"
    total_start_funds = len(SYMBOLS) * 1000.0
    total_final_funds = 0.0
    global_trades = 0
    global_wins = 0

    for symbol in SYMBOLS:
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=1000)
            df_bt = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_bt['RSI'] = calculate_rsi_series(df_bt['close'], period=RSI_PERIOD)
            df_bt['MFI'] = calculate_mfi_series(df_bt['high'], df_bt['low'], df_bt['close'], df_bt['volume'], period=MFI_PERIOD)
            df_bt = df_bt.dropna().reset_index(drop=True)

            bt_balance = 1000.0
            bt_position = None

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
                        global_trades += 1
                        if p_change > 0:
                            global_wins += 1
                        bt_position = None

            if bt_position is not None:
                bt_balance = bt_position['amount'] * df_bt.iloc[-1]['close']
                global_trades += 1

            total_final_funds += bt_balance
            p_pct = ((bt_balance - 1000.0) / 1000.0) * 100
            summary_report += f"🔹 {symbol}: {p_pct:+.2f}%\n"
        except Exception:
            total_final_funds += 1000.0
            summary_report += f"🔹 {symbol}: Ошибка загрузки данных ⚠️\n"
            
        time.sleep(0.3)

    g_profit_pct = ((total_final_funds - total_start_funds) / total_start_funds) * 100
    g_win_rate = (global_wins / global_trades * 100) if global_trades > 0 else 0

    summary_report += f"\n📈 *Общий итог стратегии:*\n💵 Результат: {g_profit_pct:+.2f}%\n🔄 Всего сделок: {global_trades}\n🎯 Win Rate: {g_win_rate:.1f}%"
    bot.send_message(CHAT_ID, summary_report, parse_mode='Markdown')

# --- РАБОТА РОБОТА В РЕАЛЬНОМ ВРЕМЕНИ ---
def check_trade_logic():
    global active_positions
    try:
        current_balance = exchange.fetch_balance()['free'].get('USDT', 0.0)
        trade_amount_usdt = current_balance * 0.05 
    except Exception:
        return

    for symbol in SYMBOLS:
        try:
            current_data = get_market_data_single(symbol)
            if current_data is None:
                continue
            c_price = current_data['close']
            rsi = current_data['RSI']
            mfi = current_data['MFI']

            if active_positions[symbol] is None:
                if rsi < RSI_OVERSOLD and mfi < MFI_OVERSOLD and trade_amount_usdt >= 5.0:
                    order = exchange.create_market_buy_order(symbol, trade_amount_usdt)
                    active_positions[symbol] = {
                        'entry_price': c_price,
                        'amount': order['amount'] if 'amount' in order else (trade_amount_usdt / c_price)
                    }
                    msg = f"🛒 *ПОКУПКА BINGX: {symbol}*\nЦена: {c_price}\nВыделено: \${trade_amount_usdt:.2f} USDT"
                    send_tg_message(msg)
            else:
                pos = active_positions[symbol]
                p_change = (c_price - pos['entry_price']) / pos['entry_price']
                
                if p_change >= TAKE_PROFIT_PCT or p_change <= -STOP_LOSS_PCT or rsi > RSI_OVERBOUGHT or mfi > MFI_OVERBOUGHT:
                    exchange.create_market_sell_order(symbol, pos['amount'])
                    reason = "🟢 TP" if p_change > 0 else "🔴 SL"
                    msg = f"💰 *ПРОДАЖА BINGX: {symbol}*\nПричина: {reason}\nЦена: {c_price}\nРезультат: {p_change*100:+.2f}%"
                    send_tg_message(msg)
                    active_positions[symbol] = None
        except Exception:
            pass
        time.sleep(0.5)

def run_scheduler():
    schedule.every(1).minutes.do(check_trade_logic)
    while True:
        schedule.run_pending()
        time.sleep(1)

print("Запуск планировщика...")
send_tg_message("🚀 *Бот успешно подключен к BingX спот!*")

scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

bot.infinity_polling()

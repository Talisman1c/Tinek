# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import requests
import os
import json
import logging
from typing import Dict, Any
from tinkoff.invest import (
    Client,
    RequestError,
    OrderDirection,
    OrderType
)

# === Настройки ===
TINKOFF_TOKEN = os.getenv("TINKOFF_TOKEN")
if not TINKOFF_TOKEN:
    raise RuntimeError("TINKOFF_TOKEN не установлен в переменных окружения")
USE_SANDBOX = True  # True = тестовый режим (рекомендуется сначала)

TELEGRAM_TOKEN = "ваш_токен_бота_здесь"  # ← Замени!
TELEGRAM_CHAT_ID = "ваш_chat_id_здесь"  # ← Замени!

# Сопоставление тикеров и FIGI
FIGI_MAP: Dict[str, str] = {
    "SBER": "BBG004730N88",
    "GAZP": "BBG004730ZJ9",
    "YNDX": "BBG005D58C28",
    "AAPL": "BBG000B9XRY4",  # Apple
    "TSLA": "BBG00F8TV999",  # Tesla
    "AMZN": "BBG000B9XNP4",  # Amazon
    # Добавь свои
}

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# === Отправка в Telegram ===
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
        logger.info(f"Telegram сообщение отправлено: {message[:50]}...")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")

# === Инициализация Sandbox (один раз!) ===
@app.get("/init-sandbox")
def init_sandbox():
    if not USE_SANDBOX:
        error_msg = "Sandbox выключен в настройках"
        logger.warning(error_msg)
        send_telegram(f"⚠️ {error_msg}")
        return {"status": "error", "msg": error_msg}
    
    try:
        with Client(TINKOFF_TOKEN) as client:
            # Регистрируем счёт (один раз)
            try:
                client.sandbox.sandbox_register_post()
                logger.info("Песочница зарегистрирована")
            except Exception as e:
                logger.warning(f"Песочница уже зарегистрирована или ошибка: {str(e)}")
            
            # Выдаем 1_000_000 руб. тестовых денег
            client.sandbox.sandbox_currencies_balance_post(
                balance=1_000_000,
                currency="RUB"
            )
            msg = "✅ Sandbox инициализирован с 1 млн RUB"
            logger.info(msg)
            send_telegram(msg)
            return {"status": "ok", "message": "Sandbox инициализирован с 1 млн RUB"}
    except Exception as e:
        error_msg = f"❌ Ошибка инициализации Sandbox: {str(e)}"
        logger.error(error_msg)
        send_telegram(error_msg)
        return {"status": "error", "message": str(e)}

# === Вебхук от TradingView ===
@app.post("/webhook")
async def tradingview_webhook(request: Request):
    try:
        data = await request.json()
    except:
        try:
            # На случай, если приходит как form или строка
            body = await request.body()
            data = json.loads(body.decode())
        except Exception as e:
            logger.error(f"Ошибка парсинга JSON: {str(e)}")
            send_telegram("❌ Ошибка: Невалидный JSON")
            raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Получен сигнал: {data}")

    action = data.get("action", "").lower()
    ticker = data.get("ticker", "").upper()
    lots = int(data.get("lots", 1))

    if action not in ["buy", "sell"]:
        error_msg = f"❌ Ошибка: action должно быть 'buy' или 'sell', получено: {action}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=400, detail="Invalid action")

    if ticker not in FIGI_MAP:
        error_msg = f"❌ Неизвестный тикер: {ticker}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=400, detail="Unknown ticker")

    figi = FIGI_MAP[ticker]

    # === Выполнение ордера через Tinkoff API ===
    try:
        with Client(TINKOFF_TOKEN) as client:
            accounts = client.users.get_accounts()
            account_id = accounts.accounts[0].id
            
            direction = (
                OrderDirection.ORDER_DIRECTION_BUY
                if action == "buy"
                else OrderDirection.ORDER_DIRECTION_SELL
            )

            # Рыночный ордер
            response = client.orders.post_order(
                figi=figi,
                quantity=lots,
                direction=direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                account_id=account_id,
                import time
                order_id = f"auto_{action}_{ticker}_{int(time.time() * 1000)}"
            )

            status = "✅ КУПЛЕНО" if action == "buy" else "✅ ПРОДАНО"
            msg = f"{status} {lots} лотов {ticker}\n"
            msg += f"Заявка: {response.order_id}\n"
            msg += f"Статус: {response.execution_report_status}"

            send_telegram(msg)
            logger.info(msg)

            return JSONResponse({"status": "ok", "order_id": response.order_id})

    except RequestError as api_ex:
        error_msg = f"❌ Ошибка API: {api_ex}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

    except Exception as e:
        error_msg = f"❌ Неизвестная ошибка: {str(e)}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

# === Проверка подключения ===
@app.get("/")
def home():
    return {"status": "Бот работает! Используй /webhook для сигналов."}

# Запуск (для локального теста)
if __name__ == "__main__":
    logger.info("Запуск бота на локальном сервере...")
    uvicorn.run(app, host="0.0.0.0", port=8000)






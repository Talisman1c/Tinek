# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import requests
import os
from typing import Dict, Any
from tinkoff.invest import (
    Client,
    RequestError,
    OrderDirection,
    OrderType,
    PostOrderResponse,
    SandboxService,
)
import logging

# === Настройки ===
TINKOFF_TOKEN = "ваш_токен_API_здесь"  # ← Замени!
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
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# === Инициализация Sandbox (один раз!) ===
@app.get("/init-sandbox")
def init_sandbox():
    if not USE_SANDBOX:
        return {"status": "error", "msg": "Sandbox выключен"}
    
    try:
        with Client(TINKOFF_TOKEN) as client:
            # Регистрируем счёт (один раз)
            client.sandbox.sandbox_register_post()
            # Выдаем 1_000_000 руб. тестовых денег
            client.sandbox.sandbox_currencies_balance_post(
                balance=1_000_000,
                currency="RUB"
            )
            # Можно добавить USD, EUR и т.д.
            return {"status": "Sandbox инициализирован с 1 млн RUB"}
    except Exception as e:
        return {"error": str(e)}

# === Вебхук от TradingView ===
@app.post("/webhook")
async def tradingview_webhook(request: Request):
    try:
        data: dict = await request.json()
    except:
        try:
            # На случай, если приходит как form или строка
            body = await request.body()
            data = json.loads(body.decode())
        except:
            raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Получен сигнал: {data}")

    action = data.get("action", "").lower()
    ticker = data.get("ticker", "").upper()
    lots = int(data.get("lots", 1))

    if action not in ["buy", "sell"]:
        send_telegram("❌ Ошибка: action должно быть 'buy' или 'sell'")
        raise HTTPException(status_code=400, detail="Invalid action")

    if ticker not in FIGI_MAP:
        send_telegram(f"❌ Неизвестный тикер: {ticker}")
        raise HTTPException(status_code=400, detail="Unknown ticker")

    figi = FIGI_MAP[ticker]

    # === Выполнение ордера через Tinkoff API ===
    try:
        with Client(TINKOFF_TOKEN) as client:
            account_id = (
                client.users.get_accounts().accounts[0].id
            )

            direction = (
                OrderDirection.ORDER_DIRECTION_BUY
                if action == "buy"
                else OrderDirection.ORDER_DIRECTION_SELL
            )

            # Рыночный ордер
            response: PostOrderResponse = client.orders.post_order(
                figi=figi,
                quantity=lots,
                direction=direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                account_id=account_id,
                order_id=f"auto_{action}_{ticker}_{os.getpid()}"
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
        send_telegram(error_msg)
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

    except Exception as e:
        error_msg = f"❌ Ошибка: {str(e)}"
        send_telegram(error_msg)
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

# === Проверка подключения ===
@app.get("/")
def home():
    return {"status": "Бот работает! Используй /webhook для сигналов."}

# Запуск (для локального теста)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import requests
import os
import json
import logging
import time
from typing import Dict, Any
from tinkoff.invest import (
    Client,
    RequestError,
    OrderDirection,
    OrderType
)

# === Настройки через переменные окружения ===
TINKOFF_TOKEN = os.getenv("TINKOFF_TOKEN")
if not TINKOFF_TOKEN:
    raise RuntimeError("TINKOFF_TOKEN не установлен в переменных окружения")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не установлен в переменных окружения")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID не установлен в переменных окружения")

USE_SANDBOX = os.getenv("USE_SANDBOX", "true").lower() == "true"

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
        response = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10
        )
        if response.status_code == 200:
            logger.info(f"✅ Telegram: {message[:50]}...")
        else:
            logger.error(f"❌ Telegram API вернул статус {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")

# === Инициализация Sandbox (один раз!) ===
@app.get("/init-sandbox")
def init_sandbox():
    if not USE_SANDBOX:
        msg = "⚠️ Sandbox выключен в настройках"
        logger.warning(msg)
        send_telegram(msg)
        return {"status": "error", "msg": msg}
    
    try:
        with Client(TINKOFF_TOKEN) as client:
            # Удаляем старый счёт (на всякий случай)
            try:
                client.sandbox.sandbox_remove_post()
            except:
                pass

            # Регистрируем новый
            client.sandbox.sandbox_register_post()

            # Пополняем баланс
            client.sandbox.sandbox_currencies_balance_post(balance=1_000_000, currency="RUB")
            client.sandbox.sandbox_currencies_balance_post(balance=10_000, currency="USD")

            msg = "✅ Sandbox инициализирован: 1 млн RUB, 10k USD"
            logger.info(msg)
            send_telegram(msg)
            return {"status": "ok", "message": "Sandbox готов к работе"}
    except Exception as e:
        error_msg = f"❌ Ошибка инициализации Sandbox: {e}"
        logger.error(error_msg)
        send_telegram(error_msg)
        return {"status": "error", "message": str(e)}

# === Сброс sandbox (удобно для тестов) ===
@app.get("/reset-sandbox")
def reset_sandbox():
    if not USE_SANDBOX:
        return {"status": "error", "msg": "Sandbox выключен"}
    try:
        with Client(TINKOFF_TOKEN) as client:
            client.sandbox.sandbox_remove_post()
            client.sandbox.sandbox_register_post()
            client.sandbox.sandbox_currencies_balance_post(balance=1_000_000, currency="RUB")
        msg = "🔄 Sandbox сброшен и пополнен"
        send_telegram(msg)
        logger.info(msg)
        return {"status": "ok", "message": "Sandbox сброшен"}
    except Exception as e:
        logger.error(f"❌ Ошибка сброса sandbox: {e}")
        return {"status": "error", "message": str(e)}

# === Вебхук от TradingView ===
@app.post("/webhook")
async def tradingview_webhook(request: Request):
    try:
        data = await request.json()
    except:
        try:
            body = await request.body()
            data = json.loads(body.decode('utf-8'))
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга JSON: {e}")
            send_telegram("❌ Ошибка: Невалидный JSON")
            raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"📩 Получен сигнал: {data}")

    action = data.get("action", "").lower()
    ticker = data.get("ticker", "").upper()
    lots = int(data.get("lots", 1))

    if action not in ["buy", "sell"]:
        error_msg = f"❌ Ошибка: action='{action}' — должно быть 'buy' или 'sell'"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=400, detail="Invalid action")

    if ticker not in FIGI_MAP:
        error_msg = f"❌ Неизвестный тикер: {ticker}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=400, detail="Unknown ticker")

    figi = FIGI_MAP[ticker]

    # === Выполнение ордера ===
    try:
        with Client(TINKOFF_TOKEN) as client:
            accounts = client.users.get_accounts().accounts
            account_id = accounts[0].id

            direction = (
                OrderDirection.ORDER_DIRECTION_BUY
                if action == "buy"
                else OrderDirection.ORDER_DIRECTION_SELL
            )

            # 🔥 Генерируем уникальный order_id (важно!)
            order_id = f"auto_{action}_{ticker}_{int(time.time() * 1000)}"

            response = client.orders.post_order(
                figi=figi,
                quantity=lots,
                direction=direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                account_id=account_id,
                order_id=order_id
            )

            status_text = "✅ КУПЛЕНО" if action == "buy" else "✅ ПРОДАНО"
            msg = f"{status_text} {lots} лотов {ticker}\n"
            msg += f"📊 Заявка: {response.order_id}\n"
            msg += f"📈 Статус: {response.execution_report_status}"

            send_telegram(msg)
            logger.info(f"✅ Ордер выполнен: {response.order_id}")

            return JSONResponse({
                "status": "ok",
                "order_id": response.order_id,
                "figi": figi,
                "action": action,
                "lots": lots
            })

    except RequestError as api_ex:
        error_msg = f"❌ Ошибка API: {api_ex.message}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

    except Exception as e:
        error_msg = f"❌ Ошибка: {str(e)}"
        logger.error(error_msg)
        send_telegram(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

# === Проверка работы бота ===
@app.get("/")
def home():
    return {
        "status": "Бот работает!",
        "endpoints": ["/webhook", "/init-sandbox", "/reset-sandbox"],
        "use_sandbox": USE_SANDBOX
    }

# Запуск (для локального теста)
if __name__ == "__main__":
    logger.info("🚀 Запуск бота на локальном сервере...")
    uvicorn.run(app, host="0.0.0.0", port=8000)


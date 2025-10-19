
Пошаговая инструкция по установке и запуску телеграм-бота для цен криптовалют:

1️⃣ Установите Python 3.10+ (https://www.python.org/downloads/)
Проверьте:
    python --version

2️⃣ Распакуйте архив и перейдите в папку проекта:
    cd crypto_price_bot

3️⃣ Создайте виртуальное окружение:
    python -m venv .venv
    (Windows) .venv\Scripts\activate
    (Linux/Mac) source .venv/bin/activate

4️⃣ Установите зависимости:
    pip install -r requirements.txt

5️⃣ Создайте бота в Telegram:
    - Откройте Telegram, найдите бота @BotFather
    - Введите /newbot
    - Следуйте инструкциям и получите TOKEN
    - Скопируйте токен и вставьте его в файл .env вместо "вставьте_сюда_свой_токен_бота_от_BotFather"

6️⃣ Запустите бота:
    python bot.py

7️⃣ Откройте Telegram, найдите своего бота и введите:
    /start

8️⃣ Примеры команд:
    /price btc eth sol
    /fiat eur
    /source binance

Бот покажет актуальные цены с CoinGecko или Binance.

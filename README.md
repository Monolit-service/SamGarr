# Telegram bot для продажи подписок на 1 приватный канал

Готовый проект на `aiogram 3`, `SQLite`, `SQLAlchemy`, `APScheduler`.

## Что умеет

- показывает тарифы
- принимает оплату через Telegram Stars
- поддерживает оплату через CryptoBot / Crypto Pay
- активирует или продлевает подписку
- выдаёт одноразовую invite link в приватный канал
- показывает профиль пользователя и его подписки
- даёт меню донатов
- позволяет админам проводить анонимные опросы среди пользователей бота
- снимает доступ после истечения срока подписки
- периодически проверяет зависшие платежи CryptoBot

## Важно перед запуском

1. Создай бота через `@BotFather`.
2. Для цифровых товаров внутри Telegram используй Telegram Stars.
3. Если нужен CryptoBot, создай приложение в `@CryptoBot` → `Crypto Pay` и получи `CRYPTO_PAY_TOKEN`.
4. Добавь бота администратором в приватный канал.
5. Дай права:
   - Invite Users via Link
   - Ban Users
6. Узнай `chat_id` приватного канала.
7. Добавь свой Telegram ID в `ADMIN_IDS`, если хочешь создавать опросы.
8. Скопируй `.env.example` в `.env` и заполни значения.

## Быстрый старт локально

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

## Запуск в Docker

```bash
cp .env.example .env
docker compose up --build -d
```

## Команды в боте

- `/start` — открыть главное меню
- `/my` — мои подписки
- `/profile` — мой профиль
- `/poll` — создать анонимный опрос (только для админов из `ADMIN_IDS`)
- `/polls` — показать активные опросы и завершить любой из них (только для админов)
- `/cancel_poll` — отменить создание опроса

## Новые переменные окружения

```env
ADMIN_IDS=123456789
PRIVATE_30_PRICE_XTR=250
PRIVATE_60_PRICE_XTR=450
DONATE_URL=
CRYPTO_PAY_TOKEN=
CRYPTO_PAY_TESTNET=false
CRYPTO_PAY_BASE_URL=
CRYPTO_PAY_ASSET=USDT
CRYPTO_USDT_PER_STAR=0.01
CHECK_PENDING_CRYPTO_EVERY_MINUTES=3
```

## Настройка цен тарифов

Цены тарифов вынесены в `.env`:

```env
PRIVATE_30_PRICE_XTR=250
PRIVATE_60_PRICE_XTR=450
```

При запуске бот синхронизирует тарифы в базе с этими значениями автоматически.

## Как устроена оплата

### Telegram Stars
- бот создаёт invoice c `currency=XTR`
- после `successful_payment` активирует подписку
- выдаёт ссылки на вход

### CryptoBot
- бот создаёт invoice через Crypto Pay API
- показывает кнопку на оплату
- пользователь может нажать `Проверить оплату`
- параллельно бот сам проверяет pending-платежи по расписанию
- после подтверждения активирует подписку и выдаёт доступ

## Донаты

В меню есть раздел `Донаты` — там доступны Stars, CryptoBot и опциональная внешняя ссылка `DONATE_URL`.

## Структура

```text
app/
  bot.py
  config.py
  db.py
  keyboards.py
  models.py
  seed.py
  handlers/
    payments.py
    polls.py
    start.py
    subscriptions.py
  services/
    channel_service.py
    order_service.py
    payment_service.py
    plan_service.py
    poll_service.py
    subscription_service.py
    user_service.py
  utils/
    text.py
run.py
```

## Примечания

- База по умолчанию — SQLite. Для production лучше перейти на PostgreSQL.
- Если пользователь не вступил по ссылке, revoke при истечении срока просто будет пропущен без ошибки.
- Для пользователя выдаётся одноразовая invite link со сроком жизни, который задаётся в `.env`.
- При первом запуске проект автоматически добавляет недостающую колонку `payment_method` в таблицу `payments`.


## Админ-панель

Если Telegram ID пользователя указан в `ADMIN_IDS`, в главном меню появится кнопка `🛠 Админ-панель`.

Внутри доступны:
- статистика по пользователям, оплатам, активным подпискам, вопросам и опросам;
- кнопка `📦 Скачать бэкап`, которая отправляет zip-архив с кодом проекта и рабочей SQLite-базой.

В бэкап по умолчанию не попадают `.env`, `__pycache__`, временные zip-файлы и `test_bot.db`.


## Дополнительная кнопка в главном меню

Можно задать ссылку на другой бот через переменную окружения `EXTERNAL_BOT_URL`.
Если она заполнена, в главном меню появится кнопка `🔌 MonoliteVPN`.


## Тест кнопок оплаты

Чтобы включить тестовые кнопки оплаты без реального списания средств, укажи в `.env`:

```env
PAYMENT_TEST_MODE=true
PAYMENT_TEST_ADMIN_ONLY=true
```

Тогда в экране выбора оплаты появятся кнопки `🧪 Тест Stars` и `🧪 Тест CryptoBot`, а в разделе донатов — тестовые кнопки доната. По умолчанию тестовые кнопки видят только админы из `ADMIN_IDS`.


В текущем архиве тестовый режим уже включён в `.env`, чтобы можно было сразу проверить кнопки оплаты. Перед боевым запуском выключи его или оставь только для админов.

# Трекер личных финансов

Групповой учебный проект по архитектуре высоконагруженных систем. Мы проектируем приложение, в котором пользователь ведёт доходы и расходы, задаёт бюджеты по категориям и следит за стоимостью портфеля.

Основной сценарий для демонстрации: пользователь добавляет расход на продукты, приложение сохраняет операцию и пересчитывает остаток бюджета. Если расход выводит категорию за лимит, пользователь получает уведомление.

[Описание проекта и требования ДЗ 1–5](HOMEWORK.md)

[Контекст текущей реализации](docs/PROJECT_CONTEXT.md) · [Конспект лекций и семинаров 1–5 и исходные материалы](docs/course/README.md)

## ДЗ 2. Запуск стенда

Потребуются Docker Engine на Linux или Docker Desktop, Compose 2.33.1+ и свободный порт 8080. Firewall внутри контейнеров требует поддержку `NET_ADMIN` и iptables. Выполняйте команды из корня репозитория. Один раз создайте файл `.env` с паролем для локальной БД:

```sh
test -f .env || echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" > .env
```

Запустите стенд и дождитесь готовности:

```sh
docker compose up --build -d --wait
docker compose ps
curl -fsS http://127.0.0.1:8080/
```

В текущей версии ожидаются пять сервисов со статусом `healthy`: `operations` (микросервис операций), `notifications` (добавленный в ДЗ 4 потребитель событий), `db` (PostgreSQL), `cache` (Redis) и `broker` (RabbitMQ). Запрос к микросервису возвращает `{"message":"Hello, world!"}`. Для проверки остальных компонентов:

```sh
docker compose exec db psql -U finance -d finance -c 'SELECT 1;'
docker compose exec cache redis-cli ping
docker compose exec broker rabbitmq-diagnostics -q ping
```

Остановить стенд можно командой `docker compose down`. Данные PostgreSQL остаются в именованном томе.

## ДЗ 3. Запись операции, кэш и события

Соберите обновлённый стенд командой `docker compose up --build -d --wait`.

Создайте расход:

```sh
curl -i -X POST http://127.0.0.1:8080/operations \
  -H 'Content-Type: application/json' \
  -d '{"amount":"450.50","category":"Продукты"}'
```

Ожидается HTTP 201 с UUID в поле `id`. Подставьте этот UUID вместо `ID` в следующие команды:

```sh
curl -i http://127.0.0.1:8080/operations/ID
curl -i http://127.0.0.1:8080/operations/ID
docker compose exec db psql -U finance -d finance -c 'SELECT * FROM operations;'
docker compose exec cache redis-cli GET operation:ID
docker compose logs operations notifications
```

Первый GET возвращает заголовок `X-Data-Source: database` и заполняет Redis. Повторный GET возвращает `X-Data-Source: cache`. Кэш хранится 300 секунд. POST сбрасывает ключ новой операции. Несуществующая операция возвращает 404, некорректная сумма или категория — 400. Сумма должна быть положительной, меньше триллиона и содержать до двух знаков после запятой.

Операция и событие записываются одной транзакцией в таблицы `operations` и `outbox`. Фоновый обработчик operations публикует событие в устойчивую очередь RabbitMQ `operation.created`, получает подтверждение брокера и удаляет запись из outbox. Отдельный сервис notifications читает сообщения, записывает их в журнал и подтверждает обработку. В журнале operations ищите `Published operation.created`, в notifications — `Consumed operation.created` с UUID созданного расхода. Уведомление пока демонстрируется записью в журнале.

При временной недоступности брокера события остаются в outbox и отправляются после восстановления соединения. Доставка допускает повторения. При сбое Redis чтение выполняется из PostgreSQL. Для демонстрации используется один экземпляр сервиса с фоновым обработчиком. Повторный POST создаёт новую операцию.

## ДЗ 4. Сегментация сети и Firewall

Созданы сети `edge`, `data`, `messaging`. Operations может обращаться к PostgreSQL, Redis и RabbitMQ; notifications — только к RabbitMQ. Доступ notifications к БД и кэшу явно запрещён iptables. Приложения работают от nobody после установки правил Firewall.

После запуска стенда выполните интеграционную проверку (нужен Python 3 на хосте):

```sh
python3 scripts/verify_hw4.py
```

Проверка создаёт один демонстрационный расход. Она показывает разрешения и запреты, рост счётчиков REJECT, чтение из БД/кэша и получение события сервисом notifications.

[Схема, IP-план, матрица доступа и сценарий защиты ДЗ 4](docs/HW4.md)

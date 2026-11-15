# Все ручные проверки проекта

Команды выполняются по очереди в одном терминале из корня проекта. Это инструкция; составление файла само по себе не запускает проверки и не меняет данные. Проверяйте на локальном учебном стенде. При проверках HA и backup временно прекратите добавление/изменение операций в других окнах, чтобы сравнение результатов было однозначным.

## 1. Сборка, запуск и состав проекта

```sh
cd /Users/marat/project-finance-tracker/project-finance-app
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
```

Для первого запуска без `.env` вместо первых команд Compose используется `python3 scripts/start_hw5.py`: он создаёт секреты и при необходимости переносит старые данные. Для уже настроенного проекта достаточно команд выше.

**Ожидается:** Compose собирает/запускает проект; 16 основных сервисов healthy. Если ранее выполнялся restore, может быть также 17-й сервис db-restore. Единственный опубликованный прикладной порт — `127.0.0.1:8080`; у БД, брокера, Redis и etcd публичных портов нет.

```sh
curl -i http://127.0.0.1:8080/health/frontend
curl -i http://127.0.0.1:8080/health/ready
curl -i http://127.0.0.1:8080/api
```

**Ожидается:** 200, `ok`; 200, `{"ready": true}`; 200, `{"message": "Hello, world!"}` соответственно. Корень `/` теперь возвращает HTML frontend.

## 2. Интерфейс: полный пользовательский сценарий

Откройте http://127.0.0.1:8080/.

1. Запомните баланс, сумму доходов и расходов.
2. Нажмите «Добавить операцию». Выберите «Доход», сумма `1000.25`, категория `Ручная проверка — доход`.
3. Сохраните. Должны появиться строка со знаком `+`, сообщение об успехе, прирост доходов и баланса на 1000,25 ₽.
4. Добавьте расход `250.50`, категория `Ручная проверка — расход`. Расходы увеличатся на 250,50 ₽, баланс уменьшится на эту сумму; категория появится справа.
5. Перезагрузите страницу. Обе операции остаются: они хранятся в БД.
6. Найдите расход через поиск `Ручная проверка`. Проверьте фильтры «Доходы», «Расходы», «Все операции».
7. Переключите период: «За всё время», «Этот месяц», «Последние 30 дней». Старые записи должны исключаться из выбранного периода. Если все записи свежие, суммы могут совпасть.
8. Отредактируйте свой расход: сумма `300.50`, категория `Ручная проверка — правка`. Сумма расходов вырастет ещё на 50 ₽, баланс уменьшится ещё на 50 ₽; дата останется прежней.
9. В редакторе смените тип этой записи на «Доход». Она попадёт в доходы, исчезнет из расходов, пересчитаются баланс и категории.
10. Нажмите удалить. Сначала выберите «Оставить»: запись должна остаться. Повторите удаление и подтвердите: запись исчезнет, итоговые суммы пересчитаются.
11. Удалите только созданный вами тестовый доход. Баланс вернётся к исходному, если в других окнах никто не менял записи.
12. Поиск несуществующей категории показывает «Ничего не нашлось».
13. Если записей больше 8, проверьте стрелки страниц и диапазон «1–8 из …».
14. В меню слева откройте «Операции» и вернитесь в «Обзор».
15. Уменьшите ширину окна примерно до 390 px: меню, кнопки, таблица и формы должны оставаться доступны без горизонтальной прокрутки страницы.
16. Проверьте клавиатуру: Tab переключает поля/кнопки, Escape закрывает форму, Enter отправляет заполненную форму.

**Важно:** поиск и тип фильтруют таблицу. Карточки сводки и категории показывают весь выбранный период, независимо от поиска.

**Валидация в форме:** пустая сумма/категория, нулевая или отрицательная сумма, более двух знаков после запятой должны блокировать отправку. Пробелы вместо категории также не принимаются.

## 3. API: CREATE и реальная запись в PostgreSQL

Создайте отдельную запись и сохраните UUID в переменной терминала:

```sh
MANUAL_ID=$(curl -fsS http://127.0.0.1:8080/operations \
  -H 'Content-Type: application/json' \
  -d '{"amount":"123.45","category":"Ручная проверка API","kind":"expense"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
printf '%s\n' "$MANUAL_ID"
```

**Ожидается:** UUID, например `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.

```sh
curl -i http://127.0.0.1:8080/operations
curl -i "http://127.0.0.1:8080/operations/$MANUAL_ID"
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c "SELECT id, amount, category, kind, created_at FROM operations WHERE id='$MANUAL_ID';"
```

**Ожидается:** запись есть в коллекции, API и SQL; сумма `123.45`, тип `expense`. Пароль psql уже получает из окружения db-backup; вводить/печатать секрет не нужно.

Покажите SQL-файл схемы: `operations/sql/schema.sql`. В нём есть таблицы operations/outbox и миграция существующих расходов на `kind='expense'`.

## 4. Redis: сначала БД, затем кэш

```sh
docker compose exec -T cache redis-cli DEL "operation:$MANUAL_ID"
curl -i "http://127.0.0.1:8080/operations/$MANUAL_ID"
curl -i "http://127.0.0.1:8080/operations/$MANUAL_ID"
docker compose exec -T cache redis-cli GET "operation:$MANUAL_ID"
docker compose exec -T cache redis-cli TTL "operation:$MANUAL_ID"
```

**Ожидается:** первый GET — `X-Data-Source: database`, второй — `X-Data-Source: cache`. Redis хранит JSON этой операции; TTL положительный и не больше 300 с.

## 5. API: UPDATE, инвалидация кэша и DELETE

```sh
curl -i -X PUT "http://127.0.0.1:8080/operations/$MANUAL_ID" \
  -H 'Content-Type: application/json' \
  -d '{"amount":"200.00","category":"Ручная проверка API — изменено","kind":"income"}'
docker compose exec -T cache redis-cli GET "operation:$MANUAL_ID"
curl -i "http://127.0.0.1:8080/operations/$MANUAL_ID"
curl -i "http://127.0.0.1:8080/operations/$MANUAL_ID"
```

**Ожидается:** PUT 200, тип `income`, сумма `200.00`, прежний UUID и дата создания. Сразу после PUT ключ Redis отсутствует (если никто параллельно не читает запись). Первый GET получает новую версию из database, второй — ту же версию из cache.

```sh
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c "SELECT id, amount, category, kind, created_at FROM operations WHERE id='$MANUAL_ID';"
```

**Ожидается:** SQL подтверждает изменения.

После проверки события из следующего пункта удалите именно эту запись:

```sh
curl -i -X DELETE "http://127.0.0.1:8080/operations/$MANUAL_ID"
curl -i "http://127.0.0.1:8080/operations/$MANUAL_ID"
docker compose exec -T cache redis-cli GET "operation:$MANUAL_ID"
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c "SELECT * FROM operations WHERE id='$MANUAL_ID';"
```

**Ожидается:** DELETE 200; последующий GET 404; ключ Redis отсутствует; SQL — 0 строк. Повторный DELETE также возвращает 404.

## 6. RabbitMQ: публикация, consumer и outbox

Для UUID, созданного в пункте 3:

```sh
docker compose logs --tail 100 operations
docker compose logs --tail 100 notifications
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c "SELECT * FROM outbox WHERE id='$MANUAL_ID';"
docker compose exec -T rabbit1 rabbitmqctl list_queues \
  name type leader members online messages_ready messages_unacknowledged
```

**Ожидается:** в operations — `Published operation.created` и UUID; в notifications — `Consumed operation.created` с тем же UUID. Событие может появиться через несколько секунд. После подтверждения публикации строка outbox удалена. Очередь `operation.created` имеет тип `quorum`, 3 members и 3 online.

Публикуется снимок **создания**: в логах могут быть исходные amount/category/kind, даже если запись затем изменена или удалена. UPDATE/DELETE отдельных событий не публикуют. Доставка допускает повторы.

## 7. Ошибочные запросы API

Каждый POST ниже должен вернуть **400**:

```sh
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{"amount":"0","category":"Тест"}'
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{"amount":"-1","category":"Тест"}'
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{"amount":"1.001","category":"Тест"}'
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{"amount":"NaN","category":"Тест"}'
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{"amount":"1","category":"   "}'
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{"amount":"1","category":"Тест","kind":"other"}'
curl -i http://127.0.0.1:8080/operations -H 'Content-Type: application/json' -d '{broken'
```

Несуществующие маршруты/UUID — **404**:

```sh
curl -i http://127.0.0.1:8080/operations/not-a-uuid
curl -i http://127.0.0.1:8080/operations/00000000-0000-0000-0000-000000000000
curl -i http://127.0.0.1:8080/missing-file.js
```

## 8. Сети, права и все правила firewall

```sh
docker inspect --format '{{json .NetworkSettings.Networks}}' "$(docker compose ps -q frontend)"
docker inspect --format '{{json .NetworkSettings.Networks}}' "$(docker compose ps -q operations)"
docker inspect --format '{{json .NetworkSettings.Networks}}' "$(docker compose ps -q notifications)"
```

**Ожидается:** frontend — только edge; operations — edge/data/messaging; notifications — только messaging. Control-сеть etcd/Patroni не подключена к этим приложениям.

Покажите все таблицы правил IPv4/IPv6 обоих прикладных сервисов:

```sh
docker compose exec --user root operations iptables-save
docker compose exec --user root operations ip6tables-save
docker compose exec --user root notifications iptables-save
docker compose exec --user root notifications ip6tables-save
docker compose exec --user root operations iptables -nvxL
docker compose exec --user root notifications iptables -nvxL
```

**Ожидается:** INPUT/OUTPUT/FORWARD по умолчанию DROP; loopback и ESTABLISHED/RELATED разрешены; явные allow/reject. IPv6 закрыт, кроме loopback.

```sh
docker compose exec --user nobody operations python -c 'print(open("/proc/1/status").read())'
docker compose exec --user nobody notifications python -c 'print(open("/proc/1/status").read())'
```

**Ожидается:** Uid 65534 (nobody), CapEff `0000000000000000` у процесса приложения PID 1.

### ALLOW/DENY — одинаковая TCP-проба из разных сервисов

Сначала посмотрите исходные счётчики:

```sh
docker compose exec --user root notifications iptables -nvxL OUTPUT
```

```sh
docker compose exec --user nobody operations python -c "import socket; socket.create_connection(('172.29.20.10',5432),timeout=2).close(); print('ALLOW DB')"
docker compose exec --user nobody notifications python -c "import socket; socket.create_connection(('172.29.20.10',5432),timeout=2).close(); print('ALLOW DB')"
docker compose exec --user nobody operations python -c "import socket; socket.create_connection(('172.29.20.11',6379),timeout=2).close(); print('ALLOW CACHE')"
docker compose exec --user nobody notifications python -c "import socket; socket.create_connection(('172.29.20.11',6379),timeout=2).close(); print('ALLOW CACHE')"
docker compose exec --user nobody operations python -c "import socket; socket.create_connection(('172.29.30.10',5672),timeout=2).close(); print('ALLOW BROKER')"
docker compose exec --user nobody notifications python -c "import socket; socket.create_connection(('172.29.30.10',5672),timeout=2).close(); print('ALLOW BROKER')"
docker compose exec --user nobody notifications python -c "import socket; socket.create_connection(('172.29.30.20',8080),timeout=2).close(); print('ALLOW HTTP')"
docker compose exec --user nobody operations python -c "import socket; socket.create_connection(('172.29.20.10',5433),timeout=2).close(); print('ALLOW OTHER PORT')"
```

**Ожидается:** operations → DB/cache — успех; notifications → DB/cache — явная ошибка, обычно `Host is unreachable`; оба → broker — успех; две последние пробы — отказ. Ненулевой код отрицательной пробы ожидаем и не означает поломку стенда.

```sh
docker compose exec --user root notifications iptables -nvxL OUTPUT
docker compose exec --user root operations iptables -nvxL OUTPUT
```

**Ожидается:** выросли счётчики deny-db/deny-cache/deny-other-output. Это связывает отказ именно с firewall, а не просто отсутствием сервиса.

### Блокировка нужного соединения → ошибка → восстановление

Перед началом убедитесь, что чтение коллекции проходит:

```sh
curl -i http://127.0.0.1:8080/operations
```

Добавьте временный запрет **первым** в OUTPUT, перед разрешением ESTABLISHED. Это блокирует и уже открытые соединения operations с db:

```sh
docker compose exec --user root operations iptables -I OUTPUT 1 \
  -p tcp -d 172.29.20.10 --dport 5432 \
  -m comment --comment manual-db-block -j REJECT --reject-with tcp-reset
curl -i http://127.0.0.1:8080/operations
docker compose exec --user root operations iptables -nvxL OUTPUT
```

**Ожидается:** API возвращает 503, счётчик manual-db-block увеличивается. Используется GET коллекции, которая всегда читает PostgreSQL: Redis не сможет скрыть блокировку. Во frontend нажмите обновление: появится ошибка, прежние данные пометятся как потенциально устаревшие.

Сразу удалите **это же** временное правило:

```sh
docker compose exec --user root operations iptables -D OUTPUT \
  -p tcp -d 172.29.20.10 --dport 5432 \
  -m comment --comment manual-db-block -j REJECT --reject-with tcp-reset
curl -i http://127.0.0.1:8080/operations
curl -i http://127.0.0.1:8080/health/ready
```

**Ожидается:** снова 200; во frontend «Повторить» возвращает актуальные данные. Не добавляйте правило повторно, пока не удалили первое. Если терминал потерян, `docker compose restart operations` заново устанавливает штатный firewall и убирает временное правило.

## 9. PostgreSQL HA: авария текущего primary

Сначала убедитесь, что все 3 узла работают и есть синхронная реплика:

```sh
docker compose exec pg1 patronictl -c /etc/patroni.yml list
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c 'SELECT application_name, state, sync_state FROM pg_stat_replication;'
docker compose exec etcd1 etcdctl endpoint health --cluster
```

**Ожидается:** один Leader, две streaming-реплики, минимум одна `sync`; 3 etcd endpoints healthy.

Создайте подтверждённую запись до аварии:

```sh
HA_DB_ID=$(curl -fsS http://127.0.0.1:8080/operations \
  -H 'Content-Type: application/json' \
  -d '{"amount":"5.25","category":"Ручная проверка HA БД"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
docker compose exec -T cache redis-cli DEL "operation:$HA_DB_ID"
```

По выводу patronictl **самостоятельно** задайте текущий Leader и другой, работающий узел для осмотра. Ниже пример только для случая, когда Leader — pg2:

```sh
PG_OLD=pg2
PG_CALLER=pg1
PG_CID=$(docker compose ps -q "$PG_OLD")
docker update --restart=no "$PG_CID"
docker kill --signal=KILL "$PG_CID"
docker inspect --format '{{.State.Running}} {{.State.ExitCode}}' "$PG_CID"
```

**Ожидается:** `false 137`. Отключение restart нужно, чтобы старый узел оставался выключенным и переключение действительно происходило на другой узел.

Повторяйте просмотр до появления другого Leader, затем сделайте запись и чтение:

```sh
docker compose exec "$PG_CALLER" patronictl -c /etc/patroni.yml list
curl -i http://127.0.0.1:8080/health/ready
curl -i http://127.0.0.1:8080/operations \
  -H 'Content-Type: application/json' \
  -d '{"amount":"6.25","category":"Запись после failover БД"}'
curl -i "http://127.0.0.1:8080/operations/$HA_DB_ID"
```

**Ожидается:** новый Leader отличается от PG_OLD; POST 201; старая операция сохранилась, GET 200 с `X-Data-Source: database`. До завершения переключения временные 503 ожидаемы. Это доказывает возможность записи и сохранность данных после аварии одного primary.

**Обязательно верните узел и policy:**

```sh
docker update --restart=unless-stopped "$PG_CID"
docker start "$PG_CID"
docker compose exec "$PG_CALLER" patronictl -c /etc/patroni.yml list
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c 'SELECT application_name, state, sync_state FROM pg_stat_replication;'
```

Дождитесь возвращения двух streaming-реплик и хотя бы одной sync. Старый Leader должен присоединиться репликой.

Целевое время восстановления БД — ≤45 с. Ручная демонстрация показывает переключение; время, включающее паузы на набор команд, не является точным RTO. В ранее выполненном автоматическом измерении было 21,397 с.

## 10. RabbitMQ HA: авария лидера quorum-очереди

Перед началом все 3 RabbitMQ-узла должны быть доступны.

```sh
docker compose exec rabbit1 rabbitmqctl cluster_status
docker compose exec rabbit1 rabbitmqctl list_queues \
  name type leader members online messages_ready
```

**Ожидается:** type `quorum`, 3 members и 3 online. Лидер очереди берётся из столбца leader, например `rabbit@rabbit3`; не путайте его с произвольным узлом кластера.

Остановите consumer, создайте событие и дождитесь его подтверждения:

```sh
docker compose stop notifications
MQ_BEFORE_ID=$(curl -fsS http://127.0.0.1:8080/operations \
  -H 'Content-Type: application/json' \
  -d '{"amount":"7.25","category":"Событие до аварии брокера"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c "SELECT count(*) FROM outbox WHERE id='$MQ_BEFORE_ID';"
docker compose exec rabbit1 rabbitmqctl list_queues \
  name type leader members online messages_ready
```

**Ожидается:** outbox count становится 0, а messages_ready — минимум 1. Нулевая строка outbox означает, что publisher получил подтверждение брокера; одного HTTP 201 для этого доказательства недостаточно. Если count ещё 1, повторите SQL через пару секунд.

Укажите **фактического** лидера очереди и другой работающий RabbitMQ. Пример только для leader `rabbit@rabbit3`:

```sh
MQ_OLD=rabbit3
MQ_CALLER=rabbit1
MQ_CID=$(docker compose ps -q "$MQ_OLD")
docker update --restart=no "$MQ_CID"
docker kill --signal=KILL "$MQ_CID"
docker inspect --format '{{.State.Running}} {{.State.ExitCode}}' "$MQ_CID"
docker compose exec "$MQ_CALLER" rabbitmqctl list_queues \
  name type leader members online messages_ready
```

**Ожидается:** аварийный exit 137; после выборов лидер другой, online 2, ожидающее сообщение сохранено. Повторите просмотр, если выборы ещё идут.

Создайте новое событие с одним выключенным узлом:

```sh
MQ_AFTER_ID=$(curl -fsS http://127.0.0.1:8080/operations \
  -H 'Content-Type: application/json' \
  -d '{"amount":"8.25","category":"Событие после аварии брокера"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
docker compose exec -T db-backup psql -h db -U finance -d finance \
  -c "SELECT count(*) FROM outbox WHERE id='$MQ_AFTER_ID';"
docker compose start notifications
docker compose logs --tail 100 notifications
printf 'До аварии: %s\nПосле аварии: %s\n' "$MQ_BEFORE_ID" "$MQ_AFTER_ID"
```

**Ожидается:** outbox новой записи становится 0; consumer получает **оба** UUID, созданных до и после аварии. При необходимости повторите SQL/логи спустя несколько секунд. Это доказывает сохранность подтверждённого сообщения и продолжение публикации при отказе одного узла.

**Обязательно верните узел, policy и consumer:**

```sh
docker update --restart=unless-stopped "$MQ_CID"
docker start "$MQ_CID"
docker compose start notifications
docker compose exec "$MQ_CALLER" rabbitmqctl list_queues \
  name type leader members online messages_ready
```

Дождитесь 3 online и опустошения очереди. Цель восстановления брокера — ≤30 с; ранее автоматический сценарий измерил 8,150 с. Это результат одного эксперимента.

## 11. Резервная копия и ручное восстановление

Плановый backup проверяется по логам и файлам:

```sh
docker compose logs --tail 30 db-backup
docker compose exec db-backup ls -lh /backups
```

Плановая политика: при старте и затем раз в 86400 с; после ошибки повтор через 30 с; хранятся 7 последних успешных штатных копий. Само наличие файла не доказывает возможность восстановления.

Ниже ручной pg_dump/pg_restore без запуска тестового Python-сценария. Не меняйте операции в других окнах до конца сравнения.

Сохраните отпечаток всех полей всех операций:

```sh
CHECK_SQL="SELECT count(*) || ':' || coalesce(md5(string_agg(row_to_json(o)::text, '|' ORDER BY id)), '') FROM operations o"
EXPECTED=$(docker compose exec -T db-backup psql -h db -U finance -d finance -tAc "$CHECK_SQL")
printf 'Исходная БД: %s\n' "$EXPECTED"
```

Создайте отдельную ручную копию:

```sh
mkdir -p backups
umask 077
MANUAL_ARCHIVE="manual-$(date +%Y%m%dT%H%M%S).dump"
docker compose exec -T db-backup pg_dump -h db -U finance -d finance \
  -Fc --no-owner --no-acl > "backups/$MANUAL_ARCHIVE"
docker compose exec -T db-backup pg_restore --list "/backups/$MANUAL_ARCHIVE"
docker compose exec -T db-backup sh -c \
  'cd /backups && sha256sum "$1" > "$1.sha256" && sha256sum -c "$1.sha256"' sh "$MANUAL_ARCHIVE"
```

**Ожидается:** pg_dump завершился без ошибки, pg_restore --list показывает таблицы/данные, checksum — `OK`. При ошибке pg_dump не переходите к restore. Ручная копия с префиксом manual не входит в автоматическую очистку 7 штатных finance-копий.

Восстановите в **новую БД отдельного PostgreSQL**, исходную finance не трогайте:

```sh
docker compose --profile restore up -d --wait db-restore
RESTORE_DB="manual_$(date +%Y%m%d_%H%M%S)"
docker compose exec -T db-restore createdb -U finance "$RESTORE_DB"
docker compose exec -T db-restore pg_restore \
  --exit-on-error --single-transaction --no-owner --no-acl \
  -U finance -d "$RESTORE_DB" "/backups/$MANUAL_ARCHIVE"
ACTUAL=$(docker compose exec -T db-restore psql -U finance -d "$RESTORE_DB" -tAc "$CHECK_SQL")
printf 'Оригинал: %s\nВосстановлено: %s\n' "$EXPECTED" "$ACTUAL"
docker compose exec -T db-restore psql -U finance -d "$RESTORE_DB" \
  -c 'SELECT id, amount, category, kind, created_at FROM operations ORDER BY created_at DESC LIMIT 5;'
docker compose exec -T db-backup psql -h db -U finance -d finance -tAc "$CHECK_SQL"
```

**Ожидается:** EXPECTED и ACTUAL полностью совпадают (количество + хеш всех полей); в восстановленной БД есть реальные операции; отпечаток исходной БД остался прежним. Все команды pg_restore должны завершиться без ошибки. При неудачном createdb не продолжайте восстановление в уже существующую БД — задайте новое уникальное имя.

Цель restore — ≤60 с с уже готовыми образами. Предыдущее измерение до frontend было 4,172 с для 11 операций, включая запуск остановленного restore-инстанса; после добавления поля kind/новых операций актуальные данные проверяются заново этим сравнением. Результат старого эксперимента не является новым измерением.

## 12. Сбой API и возврат интерфейса

Оставьте frontend с загруженным списком открытым:

```sh
docker compose stop operations
```

Нажмите обновление списка в браузере.

**Ожидается:** понятное сообщение о недоступности, кнопка «Повторить», статус «Данные могут быть устаревшими». Старые строки не превращаются в нулевой баланс и не исчезают из-за сетевой ошибки. При открытии новой страницы во время сбоя вместо данных будут пустые значения и сообщение об ошибке.

Верните API:

```sh
docker compose up -d --wait operations
```

Нажмите «Повторить»: список загрузится, ошибка исчезнет, статус станет «Данные обновлены».

## 13. Финальное состояние и что говорить о параметрах

После всех аварийных сценариев:

```sh
docker compose ps
curl -i http://127.0.0.1:8080/health/ready
docker compose exec pg1 patronictl -c /etc/patroni.yml list
docker compose exec rabbit1 rabbitmqctl list_queues name type leader members online
docker compose exec etcd1 etcdctl endpoint health --cluster
```

**Ожидается:** все основные компоненты healthy, API 200, PostgreSQL 1 leader + 2 реплики, RabbitMQ 3 online, etcd 3 healthy. У временно выключавшихся контейнеров верните restart policy `unless-stopped`. Не завершайте показ с заблокированным firewall или остановленным consumer.

Показатели и ограничения для устного объяснения:

- SLO: цель 99,9% успешных валидных запросов за 30 дней, бюджет ошибок 0,1% запросов. Непрерывный мониторинг пока не реализован, достижение этого SLO не измерено.
- Задержка: цель p95 ≤1 с при учебной нагрузке; нагрузочного подтверждения пока нет.
- RTO: БД ≤45 с; брокер ≤30 с; restore ≤60 с с готовыми образами.
- RPO: цель 0 подтверждённых записей/сообщений при одном отказе и исправном sync/quorum. Для восстановления только из ежедневной копии — до 24 ч при штатном расписании; при длительном сбое backup может быть больше.
- MTTR: целевые времена восстановления выше; один замер аварии не доказывает статистическое среднее.
- MTBF: цель ≥7 суток между неплановыми отказами учебного стенда; эксплуатационная статистика не собрана. Намеренные kill-тесты не доказывают MTBF.
- Один Docker-хост: отказ всего ноутбука останавливает все компоненты. Frontend, operations и HAProxy имеют по одному экземпляру; многосерверная HA не реализована.

Для короткой защиты лучше заранее открыть нужные окна и выбрать несколько ключевых сценариев; весь этот список существенно длиннее четырёх минут выступления. Скриншоты фиксируйте до/после отказа и после восстановления.

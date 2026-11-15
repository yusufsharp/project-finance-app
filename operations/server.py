import json
import logging
import os
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pika
import psycopg
import redis
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
cache = redis.Redis(host="cache", socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
QUEUE = "operation.created"
QUEUE_ARGUMENTS = {"x-queue-type": os.environ["RABBITMQ_QUEUE_TYPE"]} if os.environ.get("RABBITMQ_QUEUE_TYPE") else None


def database():
    return psycopg.connect(
        host="db", dbname="finance", user="finance",
        password=os.environ["POSTGRES_PASSWORD"], connect_timeout=3,
        target_session_attrs="read-write", options="-c statement_timeout=5000",
        row_factory=dict_row,
    )


def initialize():
    with database() as conn:
        conn.execute((Path(__file__).parent / "sql" / "schema.sql").read_text())



def events():
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(
                host="broker",
                credentials=pika.PlainCredentials(
                    os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"]
                ),
                socket_timeout=3, blocked_connection_timeout=5,
            ))
            try:
                channel = connection.channel()
                channel.queue_declare(queue=QUEUE, durable=True, arguments=QUEUE_ARGUMENTS)
                channel.confirm_delivery()
                while True:
                    # Delete only after broker confirmation. A retry can deliver duplicates.
                    with database() as conn:
                        rows = conn.execute("SELECT id, payload FROM outbox").fetchall()
                        for row in rows:
                            channel.basic_publish(
                                exchange="", routing_key=QUEUE, body=row["payload"],
                                properties=pika.BasicProperties(
                                    delivery_mode=2, content_type="application/json",
                                    message_id=str(row["id"]),
                                ), mandatory=True,
                            )
                            conn.execute("DELETE FROM outbox WHERE id = %s", (row["id"],))
                            logging.info("Published operation.created %s", row["id"])
                    connection.process_data_events(time_limit=0.2)
                    time.sleep(0.2)
            finally:
                if connection.is_open:
                    connection.close()
        except Exception:
            logging.exception("Event worker failed, retrying")
            time.sleep(2)


def serialize(row):
    return {
        "id": str(row["id"]), "amount": str(row["amount"]),
        "category": row["category"], "kind": row["kind"],
        "created_at": row["created_at"].isoformat(),
    }


class OperationsHandler(BaseHTTPRequestHandler):
    def respond(self, status, payload, source=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if source:
            self.send_header("X-Data-Source", source)
        self.end_headers()
        self.wfile.write(body)

    def identifier(self):
        try:
            prefix, identifier = self.path.rsplit("/", 1)
            if prefix != "/operations":
                raise ValueError()
            return str(uuid.UUID(identifier))
        except ValueError:
            self.respond(404, {"error": "Операция не найдена"})
            return None

    def read_payload(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError()
            if self.headers.get_content_type() != "application/json":
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            amount = Decimal(str(data["amount"]))
            category = data["category"]
            kind = data.get("kind", "expense")
            if (
                not amount.is_finite() or amount <= 0 or amount >= Decimal("1000000000000")
                or amount != amount.quantize(Decimal("0.01"))
                or not isinstance(category, str) or not 1 <= len(category.strip()) <= 100
                or kind not in ("income", "expense")
            ):
                raise ValueError()
            return amount, category.strip(), kind
        except (ValueError, KeyError, TypeError, InvalidOperation, UnicodeDecodeError):
            self.respond(400, {"error": "Укажите положительную сумму (до 2 знаков после запятой), категорию (1–100 символов) и тип income/expense"})
            return None

    def invalidate(self, identifier):
        try:
            cache.delete("operation:" + identifier)
        except redis.RedisError:
            logging.warning("Cache invalidation failed")

    def do_GET(self):
        if self.path == "/health/ready":
            try:
                with database() as conn:
                    conn.execute("SELECT 1")
                self.respond(200, {"ready": True})
            except psycopg.Error:
                self.respond(503, {"ready": False})
            return
        if self.path == "/":
            self.respond(200, {"message": "Hello, world!"})
            return
        if self.path == "/operations":
            try:
                with database() as conn:
                    rows = conn.execute("SELECT * FROM operations ORDER BY created_at DESC, id DESC").fetchall()
                self.respond(200, {"operations": [serialize(row) for row in rows]}, "database")
            except psycopg.Error:
                self.respond(503, {"error": "База данных временно недоступна. Повторите запрос."})
            return
        identifier = self.identifier()
        if identifier is None:
            return
        try:
            key = "operation:" + identifier
            # A per-operation transaction lock serializes cache fill and mutations.
            # Cached reads still work during a database outage; cache fills require DB.
            try:
                cached = cache.get(key)
            except redis.RedisError:
                cached = None
            if cached:
                result = json.loads(cached)
                result.setdefault("kind", "expense")
                self.respond(200, result, "cache")
                return
            with database() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identifier,))
                row = conn.execute("SELECT * FROM operations WHERE id = %s", (identifier,)).fetchone()
                if row is None:
                    self.respond(404, {"error": "Операция не найдена"})
                    return
                result = serialize(row)
                try:
                    cache.setex(key, 300, json.dumps(result))
                except redis.RedisError:
                    logging.warning("Cache write failed")
            self.respond(200, result, "database")
        except psycopg.Error:
            logging.exception("Database read failed")
            self.respond(503, {"error": "База данных временно недоступна. Повторите запрос."})

    def do_POST(self):
        if self.path != "/operations":
            self.respond(404, {"error": "Endpoint not found"})
            return
        payload = self.read_payload()
        if payload is None:
            return
        amount, category, kind = payload
        identifier = str(uuid.uuid4())
        try:
            # Operation and immutable creation event commit together.
            with database() as conn:
                row = conn.execute(
                    "INSERT INTO operations (id, amount, category, kind) VALUES (%s, %s, %s, %s) RETURNING *",
                    (identifier, amount, category, kind),
                ).fetchone()
                event = serialize(row)
                conn.execute("INSERT INTO outbox (id, payload) VALUES (%s, %s)",
                             (identifier, json.dumps(event)))
            self.respond(201, event)
        except psycopg.Error:
            logging.exception("Database write failed")
            self.respond(503, {"error": "База данных временно недоступна. Повторите запрос."})

    def mutate(self, delete=False):
        identifier = self.identifier()
        if identifier is None:
            return
        payload = None if delete else self.read_payload()
        if not delete and payload is None:
            return
        try:
            with database() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identifier,))
                if delete:
                    row = conn.execute("DELETE FROM operations WHERE id = %s RETURNING *", (identifier,)).fetchone()
                else:
                    amount, category, kind = payload
                    row = conn.execute(
                        "UPDATE operations SET amount = %s, category = %s, kind = %s WHERE id = %s RETURNING *",
                        (amount, category, kind, identifier),
                    ).fetchone()
                if row is None:
                    self.respond(404, {"error": "Операция не найдена"})
                    return
                # Invalidate before commit while cache fills hold the same lock.
                self.invalidate(identifier)
            self.respond(200, {"deleted": identifier} if delete else serialize(row))
        except psycopg.Error:
            logging.exception("Database mutation failed")
            self.respond(503, {"error": "База данных временно недоступна. Повторите запрос."})

    def do_PUT(self):
        self.mutate()

    def do_DELETE(self):
        self.mutate(delete=True)


if __name__ == "__main__":
    # The HTTP server starts only after the initial primary is reachable.
    for attempt in range(30):
        try:
            initialize()
            break
        except psycopg.Error:
            if attempt == 29:
                raise
            logging.warning("Waiting for database primary")
            time.sleep(2)
    threading.Thread(target=events, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), OperationsHandler).serve_forever()

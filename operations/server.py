import json
import logging
import os
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pika
import psycopg
import redis
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
cache = redis.Redis(host="cache", socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
QUEUE = "operation.created"


def database():
    return psycopg.connect(
        host="db", dbname="finance", user="finance",
        password=os.environ["POSTGRES_PASSWORD"], connect_timeout=3,
        row_factory=dict_row,
    )


def initialize():
    with database() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id UUID PRIMARY KEY,
                amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
                category TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                id UUID PRIMARY KEY REFERENCES operations(id),
                payload TEXT NOT NULL
            )
        """)


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
                channel.queue_declare(queue=QUEUE, durable=True)
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


class OperationsHandler(BaseHTTPRequestHandler):
    def respond(self, status, payload, source=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if source:
            self.send_header("X-Data-Source", source)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.respond(200, {"message": "Hello, world!"})
            return
        try:
            prefix, identifier = self.path.rsplit("/", 1)
            if prefix != "/operations":
                raise ValueError()
            identifier = str(uuid.UUID(identifier))
        except ValueError:
            self.respond(404, {"error": "Operation not found"})
            return
        try:
            key = "operation:" + identifier
            try:
                cached = cache.get(key)
            except redis.RedisError:
                cached = None
                logging.warning("Cache unavailable")
            if cached:
                self.respond(200, json.loads(cached), "cache")
                return
            with database() as conn:
                row = conn.execute(
                    "SELECT id, amount, category, created_at FROM operations WHERE id = %s",
                    (identifier,),
                ).fetchone()
            if row is None:
                self.respond(404, {"error": "Operation not found"})
                return
            result = {
                "id": str(row["id"]), "amount": str(row["amount"]),
                "category": row["category"], "created_at": row["created_at"].isoformat(),
            }
            try:
                cache.setex(key, 300, json.dumps(result))
            except redis.RedisError:
                logging.warning("Cache write failed")
            self.respond(200, result, "database")
        except psycopg.Error:
            logging.exception("Database read failed")
            self.respond(503, {"error": "Database unavailable"})

    def do_POST(self):
        if self.path != "/operations":
            self.respond(404, {"error": "Endpoint not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            amount = Decimal(str(data["amount"]))
            category = data["category"]
            if (
                not amount.is_finite() or amount <= 0 or amount >= Decimal("1000000000000")
                or amount != amount.quantize(Decimal("0.01"))
                or not isinstance(category, str) or not 1 <= len(category.strip()) <= 100
            ):
                raise ValueError()
        except (ValueError, KeyError, TypeError, InvalidOperation):
            self.respond(400, {"error": "Provide positive amount with up to 2 decimals and category (1-100 characters)"})
            return
        identifier = str(uuid.uuid4())
        event = {"id": identifier, "amount": str(amount), "category": category.strip()}
        try:
            # Operation and event are committed together, including during broker outages.
            with database() as conn:
                conn.execute(
                    "INSERT INTO operations (id, amount, category) VALUES (%s, %s, %s)",
                    (identifier, amount, category.strip()),
                )
                conn.execute("INSERT INTO outbox (id, payload) VALUES (%s, %s)",
                             (identifier, json.dumps(event)))
            try:
                cache.delete("operation:" + identifier)
            except redis.RedisError:
                logging.warning("Cache invalidation failed")
            self.respond(201, event)
        except psycopg.Error:
            logging.exception("Database write failed")
            self.respond(503, {"error": "Database unavailable"})


if __name__ == "__main__":
    initialize()
    threading.Thread(target=events, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), OperationsHandler).serve_forever()

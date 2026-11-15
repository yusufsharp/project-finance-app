import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pika

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
ready = threading.Event()


def consume():
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
                arguments = {"x-queue-type": os.environ["RABBITMQ_QUEUE_TYPE"]} if os.environ.get("RABBITMQ_QUEUE_TYPE") else None
                channel.queue_declare(queue="operation.created", durable=True, arguments=arguments)
                channel.basic_qos(prefetch_count=1)

                def handle(channel, method, properties, body):
                    # Demo notification: logging is safe to repeat on redelivery.
                    logging.info("Consumed operation.created %s", body.decode())
                    channel.basic_ack(method.delivery_tag)

                channel.basic_consume(queue="operation.created", on_message_callback=handle)
                ready.set()
                channel.start_consuming()
            finally:
                ready.clear()
                if connection.is_open:
                    connection.close()
        except Exception:
            ready.clear()
            logging.exception("Notification consumer failed, retrying")
            time.sleep(2)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200 if ready.is_set() else 503
        if self.path != "/health":
            status = 404
        body = json.dumps({"ready": ready.is_set()}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=consume, daemon=True).start()
    # Local health check only; this service has no network-facing HTTP API.
    ThreadingHTTPServer(("127.0.0.1", 8081), HealthHandler).serve_forever()

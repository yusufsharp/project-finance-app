FROM python:3.13-alpine
RUN apk add --no-cache iptables su-exec
WORKDIR /app
COPY operations/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY operations/server.py .
COPY operations/sql ./sql
COPY network/entrypoint.sh /usr/local/bin/firewall-entrypoint
RUN chmod 755 /usr/local/bin/firewall-entrypoint
ENTRYPOINT ["firewall-entrypoint"]
CMD ["python", "server.py"]

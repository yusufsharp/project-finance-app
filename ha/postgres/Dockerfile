FROM postgres:16-alpine
RUN apk add --no-cache python3 py3-pip \
    && pip3 install --break-system-packages --no-cache-dir 'patroni[etcd3,psycopg3]==4.1.0' 'psycopg[binary]==3.2.13' \
    && mkdir -p /var/lib/postgresql/patroni \
    && chown postgres:postgres /var/lib/postgresql/patroni
COPY ha/postgres/patroni.yml /etc/patroni.yml
COPY ha/postgres/start.sh ha/postgres/bootstrap-db.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/start.sh /usr/local/bin/bootstrap-db.sh
USER postgres
ENTRYPOINT ["/usr/local/bin/start.sh"]

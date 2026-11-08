#!/bin/sh
set -eu

# These addresses are part of the IP plan in docker-compose.yml.
# Fail closed: the application starts only after all rules are installed.
case "${FIREWALL_ROLE:-}" in
    operations|notifications) ;;
    *) echo 'Unknown FIREWALL_ROLE' >&2; exit 1 ;;
esac

iptables -P INPUT DROP
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
iptables -F INPUT
iptables -F OUTPUT
iptables -F FORWARD

iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

if [ "$FIREWALL_ROLE" = operations ]; then
    iptables -A INPUT -p tcp --dport 8080 -m comment --comment allow-http -j ACCEPT
    iptables -A OUTPUT -p tcp -d 172.29.20.10 --dport 5432 -m comment --comment allow-db -j ACCEPT
    iptables -A OUTPUT -p tcp -d 172.29.20.11 --dport 6379 -m comment --comment allow-cache -j ACCEPT
else
    # Explicit rejects are separate from the default deny, with visible counters.
    iptables -A OUTPUT -p tcp -d 172.29.20.10 --dport 5432 -m comment --comment deny-db -j REJECT --reject-with icmp-admin-prohibited
    iptables -A OUTPUT -p tcp -d 172.29.20.11 --dport 6379 -m comment --comment deny-cache -j REJECT --reject-with icmp-admin-prohibited
fi
iptables -A OUTPUT -p tcp -d 172.29.30.10 --dport 5672 -m comment --comment allow-broker -j ACCEPT
iptables -A INPUT -m comment --comment deny-other-input -j REJECT --reject-with icmp-admin-prohibited
iptables -A OUTPUT -m comment --comment deny-other-output -j REJECT --reject-with icmp-admin-prohibited

# The application uses IPv4. Prevent an IPv6 path around the IPv4 policy.
ip6tables -P INPUT DROP
ip6tables -P OUTPUT DROP
ip6tables -P FORWARD DROP
ip6tables -F INPUT
ip6tables -F OUTPUT
ip6tables -F FORWARD
ip6tables -A INPUT -i lo -j ACCEPT
ip6tables -A OUTPUT -o lo -j ACCEPT

echo "Firewall ready: $FIREWALL_ROLE" >&2
exec su-exec nobody:nobody "$@"

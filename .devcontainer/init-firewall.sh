#!/bin/bash
# 网络白名单防火墙 —— 默认拒绝所有出站,只放行白名单域名。
# 这是"全开权限"安全性的关键一层:防 prompt injection 把数据外发 / 拉恶意脚本。
# 容器启动时由 postCreateCommand 以 sudo 运行一次。
set -euo pipefail

# ---- 白名单域名 ----
# 改这里就能增减放行范围。只放项目真正需要的:
ALLOWED_DOMAINS=(
    # 项目本身:经 Render 新加坡 relay 转发币安(monitor.py 唯一连的行情来源)
    "binance-relay-jaeb.onrender.com"
    # 推送预警
    "api.telegram.org"
    # 装 Python 依赖
    "pypi.org"
    "files.pythonhosted.org"
    # Claude Code 自身需要
    "api.anthropic.com"
    "github.com"
    "api.github.com"
    "registry.npmjs.org"
)

echo "[firewall] 重置规则..."
iptables -F
iptables -X 2>/dev/null || true
ipset destroy allowed 2>/dev/null || true

# 允许本地回环和已建立的连接
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT  -m state --state ESTABLISHED,RELATED -j ACCEPT

# DNS 必须放行(否则没法解析白名单域名本身)
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

# 把白名单域名解析成 IP 装进 ipset
ipset create allowed hash:ip
for domain in "${ALLOWED_DOMAINS[@]}"; do
    echo "[firewall] 解析 $domain ..."
    ips=$(dig +short A "$domain" | grep -E '^[0-9.]+$' || true)
    if [ -z "$ips" ]; then
        echo "[firewall] 警告:$domain 没解析到 IP,跳过"
        continue
    fi
    for ip in $ips; do
        ipset add allowed "$ip" 2>/dev/null || true
    done
done

# 放行白名单 IP 的出站
iptables -A OUTPUT -m set --match-set allowed dst -j ACCEPT

# 默认策略:拒绝其余一切出站
iptables -P INPUT DROP
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
# 但已建立连接和回环上面已 ACCEPT,所以白名单流量正常

echo "[firewall] 完成。放行 ${#ALLOWED_DOMAINS[@]} 个域名,其余出站已拒绝。"

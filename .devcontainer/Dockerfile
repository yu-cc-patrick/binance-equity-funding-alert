# 链上美股资金费率预警项目的开发容器
# 基于 Python 3.12(匹配 GitHub Actions 用的版本),装 Claude Code
# 本项目仅用 Python 标准库,无第三方依赖。
FROM python:3.12-slim-bookworm

# 防火墙和基础工具(iptables/ipset 给 init-firewall 用,dig 解析域名)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        iptables \
        ipset \
        dnsutils \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Node.js(Claude Code 需要),用官方 NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

# 非 root 用户,但给它免密 sudo 跑防火墙脚本
ARG USERNAME=dev
RUN useradd -m -s /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/firewall \
    && chmod 0440 /etc/sudoers.d/firewall

# 防火墙脚本(root 拥有,用户只能执行不能改)
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod 0755 /usr/local/bin/init-firewall.sh \
    && chown root:root /usr/local/bin/init-firewall.sh

USER $USERNAME
WORKDIR /workspace

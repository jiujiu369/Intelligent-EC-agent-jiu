#!/usr/bin/env bash
# ============================================================
# 电商客服 Agent — Oracle Cloud Always Free VM 一键部署脚本
# 适用：Ubuntu 22.04 / 24.04 镜像的 Oracle VM
#
# 用法（二选一）：
#   A. 直接远程执行：
#      bash <(curl -fsSL https://raw.githubusercontent.com/jiujiu369/project1/main/deploy/setup-oracle.sh)
#   B. 手动：
#      git clone https://github.com/jiujiu369/project1.git
#      cd project1 && bash deploy/setup-oracle.sh
# ============================================================
set -euo pipefail

echo "============================================"
echo " 电商客服 Agent · Oracle VM 部署"
echo "============================================"

# ---------- 1. 系统依赖 + Docker ----------
echo "[1/6] 安装 Docker 与基础工具 ..."
sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates gnupg lsb-release

if ! command -v docker >/dev/null 2>&1; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
  echo "⚠️ 已将 $USER 加入 docker 组，重新登录 SSH 后生效（或先执行: newgrp docker）"
fi

# ---------- 2. 克隆 / 更新仓库 ----------
REPO="https://github.com/jiujiu369/project1.git"
APP_DIR="$HOME/project1"
if [ -d "$APP_DIR/.git" ]; then
  echo "[2/6] 更新仓库 ..."
  git -C "$APP_DIR" pull --ff-only
else
  echo "[2/6] 克隆仓库 ..."
  git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# ---------- 3. 配置 .env ----------
echo "[3/6] 配置 .env（API Key）..."
if [ ! -f .env ]; then
  cp deploy/.env.template .env
  read -r -p "请输入你的 AGENT_API_KEY (sk-...): " APIKEY
  sed -i "s|^AGENT_API_KEY=.*|AGENT_API_KEY=${APIKEY}|" .env
  echo "✅ 已写入 .env"
else
  echo "✅ .env 已存在，跳过"
fi

# ---------- 4. 防火墙放行 7860 ----------
echo "[4/6] 开放防火墙端口 7860 ..."
sudo iptables -I INPUT -p tcp --dport 7860 -j ACCEPT 2>/dev/null || true
sudo ufw allow 7860/tcp 2>/dev/null || true
echo "⚠️ 还需在 Oracle 控制台 → 网络 → VCN 安全列表 → 入站规则 添加：允许 7860/TCP（来源 0.0.0.0/0）"

# ---------- 5. 可选：DuckDNS 持久域名 ----------
echo "[5/6] 配置 DuckDNS 持久域名（可选，直接回车跳过）..."
read -r -p "DuckDNS 子域名 (如 znkf-agent): " DDSUB
read -r -p "DuckDNS Token: " DDTOK
if [ -n "${DDSUB:-}" ] && [ -n "${DDTOK:-}" ]; then
  cat > "$HOME/duckdns.sh" <<EOF
#!/usr/bin/env bash
echo url="https://www.duckdns.org/update?domains=${DDSUB}&token=${DDTOK}&ip=" | curl -ks -o - -K -
EOF
  chmod +x "$HOME/duckdns.sh"
  ( crontab -l 2>/dev/null; echo "*/5 * * * * $HOME/duckdns.sh >/dev/null 2>&1" ) | crontab -
  "$HOME/duckdns.sh"
  echo "✅ 域名: http://${DDSUB}.duckdns.org:7860"
fi

# ---------- 6. 构建并启动 ----------
echo "[6/6] 构建镜像并启动容器 ..."
sudo docker compose up -d --build

echo ""
echo "============================================"
echo " 部署完成！"
echo " 本机访问:  http://localhost:7860"
echo " 外网访问:  http://<VM公网IP>:7860"
[ -n "${DDSUB:-}" ] && echo " 域名访问:  http://${DDSUB}.duckdns.org:7860"
echo " 查看日志:  sudo docker compose logs -f"
echo " 重启服务:  sudo docker compose restart"
echo "============================================"

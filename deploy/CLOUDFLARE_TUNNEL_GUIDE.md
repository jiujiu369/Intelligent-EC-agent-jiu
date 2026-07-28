# 本机穿透公网部署指南（Cloudflare Tunnel，免费、无需信用卡）

> 适用场景：你本地 Windows 机器已能跑通 `web_ui.py`（模型、API Key 就绪），
> 想把本机 `7860` 端口暴露到公网，且**没有境外信用卡**、不想上云。
> 域名形如 `https://<tunnel-id>.cfargotunnel.com`，重启不变，长期有效。

---

## 一、安装 cloudflared

管理员 PowerShell 执行：

```powershell
winget install --id Cloudflare.cloudflared
```

装完关闭再打开终端，验证：

```powershell
cloudflared --version
```

---

## 二、登录 Cloudflare（免费账号，无需绑卡）

```powershell
cloudflared tunnel login
```

会自动打开浏览器，用普通 Cloudflare 免费账号登录并授权（选任意一个已托管的域名域，
**没有域名也能继续**，只是最终先用 `cfargotunnel.com` 子域）。

---

## 三、创建命名隧道（拿到稳定域名）

```powershell
cloudflared tunnel create agent
```

终端会输出隧道 ID 和凭证文件路径。记下输出里的
`https://<tunnel-id>.cfargotunnel.com` —— 这就是你的**稳定公网地址**。

---

## 四、启动（一键）

在项目目录双击 `deploy/start_tunnel.bat`，或在 PowerShell 执行：

```powershell
cd F:\code\project1
cloudflared tunnel run --url http://localhost:7860 agent
```

（脚本会先拉起本地 `web_ui.py`，再开隧道。）

启动后访问 `https://<tunnel-id>.cfargotunnel.com` 即可。

> 关闭：直接关掉这两个终端窗口；本机不再可达公网。

---

## 五、（强烈推荐）加 Cloudflare Access 前门

隧道本身不验证身份，任何人拿到地址都能看到你的登录页。
加上 **Cloudflare Access** 可在隧道和你的应用之间再插一道身份墙
（即使知道 URL，也必须先过邮箱 OTP / Google 登录才能进）：

1. 打开 https://one.dash.cloudflare.com （Cloudflare Zero Trust，免费额度够个人用）
2. 左侧 **Access → Applications → Add an application → Self-hosted**
3. Application host：填 `<tunnel-id>.cfargotunnel.com`（或你自定义的子域名）
4. **Policies → Add a policy**：
   - Action: `Allow`
   - 规则示例：邮箱后缀 `@你的邮箱域名`；或 `Emails` 填你自己的邮箱（一次性 PIN 验证）
5. 保存。之后访问会先跳 Cloudflare 登录，通过才进你的 Gradio 登录页。

---

## 六、（可选）绑定自己的域名

想用 `kefu.你的域名.com` 这种正式地址：

1. 把域名 NS 转到 Cloudflare（免费）
2. 控制台 **Access → Tunnels → agent → Public Hostname → Add**
3. Subdomain 填 `kefu`，Domain 选你的域名，Service 填 `http://localhost:7860`
4. 保存后即可用自定义域名访问（仍需过 Access 前门）

---

## 七、安全 checklist（部署前确认）

- [ ] 预设账号已删除（本仓库 `consumer_users.json` / `merchant_users.json` 已无 user1/admin）
- [ ] 登录已加频率限制（`utils/rate_limiter.py` 的 `rate_limit_login`，10 次/60 秒）
- [ ] 已在 LLM 服务商处设置**消费上限**，防公网滥用刷爆账单
- [ ] 已启用 Cloudflare Access（见第五节）
- [ ] `.env`（含 API Key）已加入 `.gitignore`，不会提交到 GitHub

---

## 八、代价说明

- 依赖**本机常开 + 联网**，关机即离线。
- 免费 `cfargotunnel.com` 子域长期有效；换机器/重装需重新 `create`。
- 真·7×24 无人值守，仍需云主机（需绑卡），本方案不解决。

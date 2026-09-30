# 代办 + Doorman 出口（预览）

「代办」是一个替你在外面办事的 Agent：查资料、比价、看网页、填表、发信、订东西。它和别的 Agent 不一样的地方只有一个：**它在 OpenClaw 的 Docker 沙箱里跑，上网只有一条路——Doorman**。读网页随便读；要提交、发送、用你的凭证、带着你私事的请求，Doorman 先扣下，你在 app 的收件箱里点「放行这一次」才发出去。

> Doorman 在 2026-10 之前叫 Sentinel。内部名字沿用旧词，已经装好的服务器不用改：服务名 `openmousse-sentinel`、目录 `<data_dir>/sentinel/`、令牌 `sentinel`、配置键 `sentinel.*`、回复里的 `"sentinel"` 字段。

> 预览：不跑 `errand.py setup` 就完全不生效。付款还没开放；发邮件、让你接手浏览器登录这些还没做。

## 装

要：OpenClaw（Docker 沙箱）、Docker、sudo（主机防火墙那一步）、一个装了 mitmproxy 的 venv。

```bash
python3 -m venv ~/.openmousse/sentinel-venv && ~/.openmousse/sentinel-venv/bin/pip install mitmproxy
cd ~/.openmousse/repo/server
python3 errand.py setup            # 第一次会停在「沙箱镜像没建」，照提示跑下一行
bash ../sandbox/errand/build.sh <data_dir>/sentinel      # 建四个镜像（浏览器那个 1.7 GB，要几分钟）
python3 errand.py setup            # 再跑一遍，一路 ✓
python3 errand.py status
```

`setup` 做的事（每一步都能重跑）：Doorman 自己的 CA（`<data_dir>/sentinel/`）→ 代理配置和令牌 `sentinel`（只能调 `/api/egress/*`）→ Docker 网络 `mousse-errand`（`br-mousse-err`，172.30.99.0/24，**不做 NAT**）→ 主机防火墙 `openmousse-errand-net.service`（root：这个网桥上的包哪儿也转发不出去，对主机只开 172.30.99.1:3128）→ 代理服务 `openmousse-sentinel`（user）→ 全局工具禁用单里的 `group:ui` 换成除 browser 以外的成员（不然 browser 谁都放不回来）→ `openclaw.json` 里的 `agents.entries.errand` + 工作区 + app 里的「代办」。

## 它是怎么锁住的

| 层 | 做什么 |
|---|---|
| 沙箱 | `mode all`、`workspaceAccess none`（看不到任何工作区，连自己的 AGENTS.md 也改不了）、只读根目录、`capDrop ALL`、1 GB、DNS 指向 127.0.0.1（查不到外面的域名） |
| 工具 | 最小档位 + `exec` / `process` / 读写文件 / 沙箱浏览器。主机上跑的工具（web_fetch、web_search、发消息、记忆检索、派子会话）一律关掉：它们不经过沙箱，会绕开 Doorman。没有 skill |
| 网络 | 不做 NAT；`DOCKER-USER` 丢掉这个网桥出去的所有包；主机上只开 Doorman 的端口。直连 IP、UDP、DNS、连主机上的 SSH / Gateway / OpenMousse 都不通 |
| 证书 | shell 镜像只信 Doorman 的 CA；浏览器镜像的 Chromium 包了一层：`--proxy-server` 写死（连 127.0.0.1 也走代理）、只认 Doorman 的 CA 公钥、关掉后台服务 |
| Doorman | 见下 |

## Doorman 判什么

代理（`server/egress_proxy.py`，由 `sentinel_run.py` 直接挂进 mitmproxy）自己挡：私网 / 本机 / 云元数据 / CGNAT / 本机自己的地址（连接时按真实 IP 再查、钉住，防 DNS 换绑）、80 / 443 以外的端口、WebSocket 和一切不是 HTTP 的流量、Host 头和真实目标对不上的、带着占位符却不是发往绑定网站的。其余的问服务端（`server/egress.py`）：

- 追踪、统计、浏览器后台请求 → 204，不打扰你
- 付款网站的写请求 → 挡
- 读（GET 等）→ 放；网址或域名里带着你的私事（住址、邮箱、电话、家人名字、身体数字）→ 扣下；有一大串像编码过的数据 → 先让模型看
- 写（POST 等）→ 带凭证的、页面上的表单提交、带你私事的 → 扣下；`sentinel.write_hosts` 里的 → 放；其余让模型分（查询、翻页、自动补全 → 放；提交、发送、登录、预订、购买、拿不准 → 扣下）
- 模型：OpenClaw 的 llm-task（零工具、每次新会话，看不到代办的上下文），出错一律扣下，一小时最多 60 次
- 扣下 = 收件箱卡（kind `egress`，响铃，Agent 读不到）：「放行这一次」= 这一个请求 30 分钟内能发；「不要」/「改一下」= 代办收到 403 和你的话。同时最多 3 张没点的

代理自己的代码坏了、服务端连不上、钩子里出错：一律挡（fail closed）。

## 凭证代位

代办不会拿到你的密码。在服务器上加一个占位符，绑到具体网站：

```bash
python3 errand.py secret set BOOKING_PASSWORD --host www.example.com   # 值从标准输入读，不回显
python3 errand.py secret list
python3 errand.py secret rm BOOKING_PASSWORD
```

会过期的令牌（比如 Gmail 发信）用 OAuth：`errand.py oauth start GMAIL_SEND --host gmail.googleapis.com --client-file <桌面应用的 OAuth 客户端 JSON> --login-hint <邮箱>` 打出授权地址，浏览器里同意以后最后停在一个打不开的 `127.0.0.1` 页面，把地址整条交给 `errand.py oauth finish '<地址>'`。之后代理出门时自己刷新访问令牌。发信请求会被解开，卡片上是收件人、主题、正文，每一封都要你放行。

代办在请求里写 `MOUSSE_SECRET_BOOKING_PASSWORD`，Doorman 只在发往 `www.example.com` 时换成真值（发往别处直接挡）；对方把真值原样回显的话，回来时换回占位符。带凭证的写请求一律扣下等你点头。代办的 AGENTS.md 里「能用的凭证」一节会自动列出名字和网站。

## 查、看、回滚

- `python3 errand.py status`：每一样 ✓ / ✗
- app：`GET /api/egress/health`（在不在、今天放 / 扣 / 挡了多少）、`GET /api/egress/log`（最近的出网记录，留 14 天）
- 只停代办出网：`systemctl --user stop openmousse-sentinel`（沙箱就什么都连不上了）
- 整个去掉：`python3 errand.py remove`（OpenClaw 里的 errand 去掉，工作区进 archive/）→ `systemctl --user disable --now openmousse-sentinel` → `sudo systemctl disable --now openmousse-errand-net` → `docker network rm mousse-errand`

## 已知的限制

- 浏览器里提交被扣下时，OpenClaw 的 browser 工具等不了 10 分钟，会先报超时；请求本身还挂着，放行后会发完。代办的规矩里写了：别重复提交，隔一会儿再读页面。
- 读是默认放的：网址里藏着的不是你的私事、也不像编码数据的内容，能被带出去。写和私事才是关口。
- 付款、发邮件（要邮箱的发送授权）、你接手浏览器登录（noVNC）还没做。

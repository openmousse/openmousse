# 用 Docker 跑 OpenMousse

**中文** · [English](docker.md)

一个容器：OpenMousse 的服务器、它自带的内核（不用装 OpenClaw）和世界树。会变的东西都在一个卷 `/data` 里。文件是 [`docker/Dockerfile`](../docker/Dockerfile)、[`docker/entrypoint.py`](../docker/entrypoint.py)、[`docker/compose.yaml`](../docker/compose.yaml)。

## 起来

在仓库根目录：

```bash
MOUSSE_NAME=Pebble MOUSSE_TZ=Asia/Shanghai MOUSSE_LANG=zh docker compose -f docker/compose.yaml up -d --build
docker compose -f docker/compose.yaml logs openmousse
```

第一次起来（卷里还没有 `server.json`）写一份 `/data/server.json`：claw 是 `builtin`，容器里听 `0.0.0.0:8080`，令牌随机生成、只存在卷里。名字、语言、时区读 `MOUSSE_NAME`、`MOUSSE_LANG`、`MOUSSE_TZ`；不写就是 Mousse、`LANG` 的语言、`TZ`（再没有就是 UTC）。顺手建好世界树，打印配对链接（见下面）。以后再起来，`server.json` 一个字不动，环境变量写什么都一样。改设置在 app 里改，或者改卷里的文件。

| `/data` 里 | 是什么 |
| --- | --- |
| `server.json` | 配置（`MOUSSE_SERVER_CONFIG`） |
| `secrets.env` | app 里存的密钥（0600） |
| `data/` | 数据库、上传的文件、备份（`data_dir`） |
| `workspace/` | 主对话的工作区（`USER.md`、`MEMORY.md`、`memory/`、`skills/`）；新建的 Agent 在它旁边的 `workspace-<id>/` |
| `tree/` | 世界树（`MOUSSE_TREE_HOME`） |
| `home/` | `HOME`：往家目录写的东西也留下来 |

容器里用 `mousse`（uid 1000）跑，不用 root。不用命名卷、改挂宿主的文件夹的话，那个文件夹要归 uid 1000（`sudo chown -R 1000:1000 ./data`）。`HEALTHCHECK` 带 `server.json` 里本机那把令牌打 `/api/health`。

第一次打开 app 会带你选模型、上网搜索、记忆（密钥存进 `/data/secrets.env`）。

## 手机怎么连

compose 只把端口开在宿主的 `127.0.0.1` 上，同一个网络里的别人连不到。手机和别的电脑经宿主上的 Tailscale 进来：

```bash
tailscale serve --bg 8080          # https://<机器>.<tailnet>.ts.net，只在你自己的 tailnet 里
```

第一次起来之前把这个地址写进 `MOUSSE_PUBLIC_URL`，日志里就有配对链接和二维码（30 分钟内有效，只能用一次）。以后、或者当时没写，现出一个：

```bash
docker compose -f docker/compose.yaml exec openmousse python3 tokens.py pair --server https://<机器>.<tailnet>.ts.net
```

手机要在同一个 tailnet 里。配对走 `/api/pair`，它不开到公网。

## 网页版

镜像里没有 Node.js，不构建网页版。想在 `http://127.0.0.1:8080` 用网页版，在构建镜像之前先构建它（`cd app && npm ci && npm run web:build`）：`.dockerignore` 放 `app/dist` 进镜像，`app/` 别的都挡在外面。没构建的话 `/` 只有一行说明，手机 app 照常用。

## 容器里没有的

- **Docker 沙箱**：容器里没有 Docker（不做 docker-in-docker），`engine.sandbox` 关着：Agent 没有沙箱里的工具，也没有代办 Agent。主机上的命令（`engine.host_exec`）开了的话，跑在容器里，不是宿主上。
- **Claude Code / Codex 的登录**：镜像里没装这两个命令行，`claude-code/…` 的模型用不了：用 API 密钥。真要用，自己在这个镜像上再做一层装上它，登录自己挂进来。app 里连的 ChatGPT 套餐，令牌存在 `/data/data/chatgpt/`。
- **Tailscale**：用宿主的。`auth.tailscale_nodes`（按设备名免令牌）在容器里不管用。

## 更新

重新构建或者拉新镜像（`git pull && docker compose -f docker/compose.yaml up -d --build`）。`mousse update` 在容器里不跑（`MOUSSE_INSTALL=docker`），会告诉你怎么重建镜像。卷原样留着。

## 备份

全在卷里。先停容器，再把卷打个包：

```bash
docker compose -f docker/compose.yaml stop
docker run --rm -v openmousse_openmousse-data:/data:ro -v "$PWD":/backup busybox tar czf /backup/openmousse-data.tgz -C / data
```

## 从 OpenClaw、Hermes 搬过来

把原来的家目录挂进容器（`compose.yaml` 里注释掉的那几行），再跑 [`mousse migrate`](../server/README.zh-CN.md#从-openclawhermes-搬过来自己的内核)：

```bash
docker compose -f docker/compose.yaml exec openmousse mousse migrate openclaw --openclaw-home /migrate/openclaw          # 只看报告
docker compose -f docker/compose.yaml exec openmousse mousse migrate openclaw --openclaw-home /migrate/openclaw --apply
```

只读挂就够：迁移从来不往源目录写。不过工作区不拷：Agent 原地接着用它们自己的（`agent_workspaces` 指到 `/migrate/…` 里），要让这些 Agent 接着记东西就挂成能写的。主对话还是 `/data/workspace`，报告里写着怎么换。

## 有拦截 TLS 的代理

把代理的 CA 当成构建用的 secret 交给 pip，不会存进任何一层：

```bash
docker build --secret id=ca_bundle,src=/path/to/ca.pem -f docker/Dockerfile -t openmousse .
```

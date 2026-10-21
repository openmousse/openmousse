# 怎么发版

**中文** · [English](releasing.md)

服务器发版的检查单：先跑哪些、版本号怎么改、tag、服务器和 app 谁先谁后、发版说明写在哪。app 的原生包和世界树的包（`tree/`）各有自己的版本，另外发。

## 1. 发版之前

下面这些都在要发的那个提交上跑，发的也只是那个提交。

**测试和 lint**，照 CI 跑：

```bash
pip install -r server/requirements.txt pytest ruff==0.15.2 ./tree
ruff check server tree/openmousse_tree packs/core/scripts packs/core/setup.py
python -m pytest server/tests -q          # 10 分钟以内
bash -n install.sh && bash -n check.sh
cd app && npm ci && npm run typecheck && npm run lint
```

**隐私扫描。** [`tools/release/privacy_scan.py`](../tools/release/privacy_scan.py) 扫 git 里的每一个文件（图片看元数据），报像密钥的串、家目录和本机的路径、Tailscale 的地址和主机名、不是示例的邮箱、电话号码，还有你自己名单里的词：

```bash
MOUSSE_PRIVACY_WORDS=~/private/words.txt python3 tools/release/privacy_scan.py \
  --secrets ~/.openmousse/server.json --secrets ~/.openmousse/secrets.env --secrets ~/.mousse-tree/config.json
```

- 名单放在仓库外面：一行一个词或短语（真名、学校、雇主、住址、主机名、分享页档案里的词）。`--secrets` 给的是你真的 `.env` / `server.json` / 世界树的配置。
- 两样都只按指纹比。命中只说哪个文件、第几行、名单第几个词或者哪个键，不打印原文。
- 发版要 0 条，或者留下的每一条都有理由。看过、没问题的文件写进脚本开头的 `ALLOW`，带上理由。

**从零装一遍。** 在一台干净的机器上，新建一个普通用户，从发版那个提交的本地镜像装，模型用假的，照新用户会做的走一遍：

```bash
python3 server/evals/fakemodel.py --key <随便一个密钥> --echo &      # 127.0.0.1:8765 上一个 OpenAI 兼容的假模型，不连服务商、不花钱
cat install.sh | MOUSSE_REPO_URL=<本地镜像> MOUSSE_NONINTERACTIVE=1 MOUSSE_CLAW=builtin MOUSSE_TZ=Asia/Shanghai bash
```

1. 选模型：`setup_ctl.py add fake --url http://127.0.0.1:8765/v1 --key-stdin`，再看 `setup_ctl.py status`。
2. 照安装器说的起服务，然后看：
   - `/api/health` 不带令牌回 401，带令牌报的是新的 `version`、`api`、`features`；
   - 配对码（`tokens.py pair`，再 `POST /api/pair`）能用一次；
   - `POST /api/chat/send` 发一句有回复。
3. 跑 `check.sh` 和 `mousse doctor`。
4. 换一个新用户试装（`MOUSSE_TRIAL=1`）。
5. 再跑一遍安装器：`server.json` 一个字不变。
6. 有 Docker 就 build 一次 `docker/`，在容器里打 `/api/health`。

小毛病发版前修掉，别的记下来。

## 2. 版本号

都在 [`server/version.py`](../server/version.py)，`/api/health` 的 `server` 段报出来：

- **`VERSION`** 是发版日期，`年.月.日`。同一天再发一版加 `-2`，再加 `-3`。
- **`API`**：app 开始依赖一个老服务器没有的接口时加 1。app 的 `SERVER_API`（[`app/src/api/version.ts`](../app/src/api/version.ts)）在同一个提交里一起改；低于它的服务器，app 里会说「需要更新」。
- **`FEATURES`**：app 要用的每一个新接口在这里加一个名字，和接口同一个提交。app 用 `supports('<名字>')` 判断，不去试 404。加进去的名字不删。
- 不归服务器发版管的：`app/app.json` 的 `version` 只在出新的原生包时改（JS 走热更新），世界树的版本在 `tree/pyproject.toml`。

## 3. tag

在验过的那个提交上打带注释的 tag，名字就是版本号：

```bash
git tag -a v2026.10.21 -m "OpenMousse 2026.10.21" <提交>
git push <远端> v2026.10.21
```

tag 一到公开库，走 stable 通道的安装 `mousse update` 就会更到这一版：stable 跟的是最新的 `v` tag（[`server/ops/update.py`](../server/ops/update.py)；`mousse update --channel main` 跟 `main`）。所以：

- 只在第 1 步全过了的提交上打；
- 发出去的 tag 不挪、不删；要修就再发一版（`-2`）；
- 开发历史不公开的话，公开库只推压成一个的提交，tag 打在它上面：推 tag 会带上它能走到的所有提交，隐私扫描只看文件、不看历史。

拿一台测试的安装跑 `mousse update --check`，看它认不认得新的 tag。

## 4. 先服务器，再 app

1. 在公开库发出这一版（打了 tag 的提交和 tag），安装命令（`curl -fsSL https://openmousse.ai/install | bash`）和 `mousse update` 才拿得到新的服务器。
2. 更新、重启你自己跑的服务器，看 `/api/health` 报的是新版本。
3. 然后再发 app 的热更新。app 里的更新命令是从公开库重装服务器；app 先发出去，照它更新的人拿到的服务器没有它要的那一版。

## 5. 发版说明

写在 [`CHANGELOG.md`](../CHANGELOG.md) 和 [`CHANGELOG.zh-CN.md`](../CHANGELOG.zh-CN.md)，新的在上面：

- 攒着的时候最上面一节叫「未发布（草稿）」，发版时改成 `## <VERSION>`。
- 只写用户看得到的、代码里真有的，拿 `git log v<上一版>..<提交>` 核对。
- 默认关着的，每一样都写清怎么开。
- 要花用户自己的模型或接口钱的，写清楚。
- 中英两份一起改。要是也在 GitHub 上发 release，就照抄这一节。

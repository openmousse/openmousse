# OpenMousse app

**中文** · [English](README.md)

iOS / Web 客户端（Expo，React Native）。它是一个壳：对话、Agents、今天、目标、记忆，所有数据都在你自己的服务器上（[`../server/`](../server/)）。

```bash
cd app
npm install
npm run typecheck
npm run web            # 本机浏览器里跑（连接页填服务器地址和令牌）
npm run web:build      # 生成 dist/，由 server 托管成网页版
```

## 用作者的 TestFlight 装

装上后首页是连接页：填服务器地址（`https://你的域名` 或 Tailscale 的 `http://100.x.x.x:8080`）和令牌（服务器上 `python3 tokens.py add 手机` 生成）。助手的名字来自你的服务器。

## 自己构建（自己的名字和图标）

1. 复制 `app.local.example.json` 为 `app.local.json`，填名字、slug、包名、Expo 项目 id（[expo.dev](https://expo.dev) 免费建）。这个文件不进 git。
2. 图标放进 `assets/local/`（文件名和 `assets/` 里的一样：icon.png 1024×1024 等）。
3. `npx eas-cli@latest login`，需要 Apple Developer 账号（$99 / 年）。
4. `npx eas-cli@latest build -p ios --profile production`（云端构建，不需要 Mac）；`eas.json` 里 `submit.production.ios.ascAppId` 换成你在 App Store Connect 里的应用 id，就能 `--auto-submit` 到 TestFlight。
5. 之后只改 JS 用 `eas update --channel production --environment production` 推热更新；加原生模块才重新 build，并先升 `app.json` 的 `version`。

## 同一个账号发两个 app

作者自己的实例（Grava）和给朋友的通用 OpenMousse 是同一份代码、同一个 Apple 开发者账号下的两个 app。身份文件由环境变量 `MOUSSE_LOCAL` 选：默认 `app.local.json`（Grava），`eas.json` 的 `production-openmousse` 配置用 `app.local.openmousse.json`（名字 OpenMousse、包名 `app.openmousse.ios`、通用图标、自己的 EAS 项目）。`app.local.*.json` 都不进 git。

```bash
npx eas-cli build -p ios --profile production --auto-submit               # Grava
npx eas-cli build -p ios --profile production-openmousse --auto-submit    # OpenMousse（朋友装这个的 TestFlight）
MOUSSE_LOCAL=app.local.openmousse.json npx eas-cli update --channel production --environment production   # 给 OpenMousse 发热更新
```

注意 `eas init` 会把 projectId 写进 `app.json`，跑完要把它挪回身份文件，模板里不能带任何人的项目 id。身份文件不进 git 但要随构建上传，靠的是仓库根目录的 `.easignore`（有它时 EAS 不看 `.gitignore`）；它必须在 git 根目录，放在 `app/` 里不生效。

## 结构

```
App.tsx                 根组件：主题、状态、弹层、导航
app.config.js           通用 app.json + 本机 app.local.json 叠加
src/api/base.ts         服务器地址、令牌（钥匙串 / localStorage）、请求工具
src/screens/Connect…    连接页
src/brand.ts            助手名字（来自服务器）
src/store.tsx           应用状态与动作
src/api/client.ts       对话（SSE 流式）
src/components/         界面组件；图标只从 icons.ts 引入
```

开发约定见 [`AGENTS.md`](AGENTS.md)。

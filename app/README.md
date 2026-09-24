# OpenMousse app

[中文](README.zh-CN.md) · **English**

iOS / Web client (Expo, React Native). It is a shell: chat, Agents, Today, Goals, Memory. All data lives on your own server ([`../server/`](../server/)).

```bash
cd app
npm install
npm run typecheck
npm run web            # run in the browser (enter server address and token on the connection page)
npm run web:build      # produce dist/, served by the server as the web version
```

## Using the author's TestFlight build

The first screen is the connection page: enter the server address (`https://your.domain` or Tailscale's `http://100.x.x.x:8080`) and a token (generated on the server with `python3 tokens.py add phone`). The assistant's name comes from your server.

## Building it yourself (your own name and icon)

1. Copy `app.local.example.json` to `app.local.json`: name, slug, bundle ids, Expo project id (create one free at [expo.dev](https://expo.dev)). This file is not committed.
2. Put your icons in `assets/local/` (same file names as in `assets/`: icon.png 1024×1024 etc.).
3. `npx eas-cli@latest login`; you need an Apple Developer account ($99 / year).
4. `npx eas-cli@latest build -p ios --profile production` (cloud build, no Mac required). Replace `submit.production.ios.ascAppId` in `eas.json` with your App Store Connect app id to `--auto-submit` to TestFlight.
5. Afterwards, JS-only changes ship with `eas update --channel production --environment production`; adding native modules needs a new build and a bumped `version` in `app.json` first.

## Two apps from one account

The author's own instance (Grava) and the generic OpenMousse for friends are the same code, built as two apps under one Apple developer account. The environment variable `MOUSSE_LOCAL` selects the identity file: default `app.local.json`, and the `production-openmousse` profile in `eas.json` uses `app.local.openmousse.json` (name OpenMousse, bundle id `app.openmousse.ios`, generic icons, its own EAS project). All `app.local.*.json` files stay out of git.

```bash
npx eas-cli build -p ios --profile production --auto-submit               # your own app
npx eas-cli build -p ios --profile production-openmousse --auto-submit    # generic OpenMousse (the TestFlight friends install)
MOUSSE_LOCAL=app.local.openmousse.json npx eas-cli update --channel production --environment production   # OTA update for OpenMousse
```

Notes: `eas init` writes the projectId into `app.json`; move it back into the identity file, the template must not carry anyone's project id. Identity files are not committed but must travel with the build; that relies on the `.easignore` at the repository root (when present, EAS stops reading `.gitignore`). It only works at the git root, not inside `app/`.

## Layout

```
App.tsx                 root component: theme, state, sheets, navigation
app.config.js           generic app.json + local app.local.json overlay
src/api/base.ts         server address, token (keychain / localStorage), request helpers
src/screens/Connect…    connection page
src/brand.ts            assistant name (from the server)
src/store.tsx           app state and actions
src/api/client.ts       chat (SSE streaming)
src/components/         UI components; icons only via icons.ts
```

Development conventions: [`AGENTS.md`](AGENTS.md).

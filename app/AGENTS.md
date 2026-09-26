This is an Expo/React Native mobile application. Prioritize mobile-first patterns, performance, and cross-platform compatibility.

## Expo has changed — do not trust your training data

Expo ships breaking changes every SDK release. APIs you remember are likely renamed, moved, or removed. Before writing any code that touches an Expo, EAS, or React Native API:

1. Read the major version of the `expo` package in `package.json`.
2. Fetch the matching versioned docs: `https://docs.expo.dev/versions/v<major>.0.0/`
3. For anything else, fetch https://docs.expo.dev/llms.txt — an index of all Expo docs with corrections to common LLM misconceptions. Follow its links to the specific page you need; never answer from memory.

## Commands

```bash
npx expo install <package>  # ALWAYS use instead of npm/yarn/pnpm/bun add — resolves SDK-compatible versions
npm run typecheck           # tsc --noEmit
npm run lint                # expo lint
npm run web                 # dev server in the browser
npm run web:build           # dist/ served by ../server
npx expo-doctor             # diagnose dependency and config issues
```

Run lint and typecheck before declaring any task done.

## Building with EAS

Use EAS to build, sign, and submit the app in the cloud (`eas build`, `eas submit`) and to ship over-the-air updates (`eas update`). Run it as `npx eas-cli@latest <command>`. Docs: https://docs.expo.dev/eas/index.md

## Rules

- `ios/` and `android/` do not exist; they are generated (Continuous Native Generation). Never create or edit them by hand — configure native behavior in `app.json` and config plugins.
- After adding a library with native code, the app needs a new native build; Expo Go does not work for this project.
- Prefer recommended Expo modules over third-party libraries.

---

## OpenMousse app conventions

What this is: the iOS / Android / Web client of OpenMousse — a shell for a personal agent that runs on your own OpenClaw. All data comes from your own server ([`../server/`](../server/)); there is no sample data. When the server is unreachable every page shows "not connected" and sending fails loudly (`OfflineApi`) instead of faking a reply.

### Identity and build

- **Per-instance identity stays out of git**: `app.local.json` (name, slug, bundle ids, EAS project id, owner, icon directory; template in `app.local.example.json`) plus the icons in `assets/local/`. `app.json` is the generic template (OpenMousse); `app.config.js` overlays the identity file. Permission strings use `{name}` and get the assistant's name.
- The environment variable `MOUSSE_LOCAL` selects a different identity file (`none` = pure template). This is how one codebase builds two apps under one Apple account: `eas.json`'s `production` profile uses `app.local.json`, `production-openmousse` uses `app.local.openmousse.json`. Pass the same variable when running `eas update` for that app.
- **`.easignore` must live at the git repository root** (`../.easignore`). When it exists EAS ignores `.gitignore`, which is what lets the untracked identity files and icons travel with the build. Check what would be uploaded with `npx eas-cli build:inspect --platform ios --profile production --stage archive --output <dir>`.
- `eas init` writes `extra.eas.projectId` into `app.json`; move it into the identity file afterwards. The template must not carry anyone's project id.
- `runtimeVersion` follows `appVersion`. JS-only changes ship with `eas update --channel production --environment production`; anything native (new module, permissions, entitlements) needs a new build and a bumped `version` first, so old binaries never receive a bundle that references a module they do not have. Never OTA a bundle that imports a native module absent from the installed binary; keep the last known-good update group id for `eas update:republish`.
- `eas update` exports into `dist/` by default, the same folder `../server` serves as the web version, and leaves it without the home-screen tags `scripts/build-web.mjs` adds. Run `npm run web:build` again after every `eas update`.

### Architecture

- **Navigation is React Navigation, not Expo Router**, on purpose: `NavigationContainer` has no `linking`, so the single-file web preview works from any path. Do not migrate.
- **Icons only from `src/components/icons.ts`**. Importing from `lucide-react-native` directly pulls thousands of icons into the bundle. Add a line there for a new icon.
- **Bottom sheets use `src/components/Sheet.tsx`**, not React Native's `Modal` (which escapes the phone frame on web). Inside a native modal screen wrap the page in its own `SheetProvider`. `open` dismisses the keyboard first and the sheet slides in as it goes down; a sheet with its own inputs lifts above the keyboard by itself.
- **Keyboard: no `KeyboardAvoidingView` with a hard-coded `keyboardVerticalOffset`.** It compares a parent-relative layout with screen coordinates, so a wrong offset leaves a gap under the composer (the old `90` left 90pt). Anything pinned to the bottom takes `useBottomInset` (`src/components/keyboard.ts`) as its `paddingBottom`: it measures the view in the window, follows the iOS keyboard, and leaves room for the home indicator when the view reaches the screen bottom. Don't pass it a ref inside a native modal (coordinates there are modal-relative). Form screens use `automaticallyAdjustKeyboardInsets` on their `ScrollView`. The chat list dismisses the keyboard on tap (`keyboardShouldPersistTaps="never"`) and on drag, except on web, where react-native-web's `on-drag` blurs the input on every scroll, including auto-scroll to a new reply.
- **In-app notifications go through the banner** (`src/components/Banner.tsx`, `useBanner().show`), never a system notification while the app is in the foreground (`src/api/push.ts` suppresses those). Same source + target replaces the queued one; several within 4 s merge into one expandable summary. Only `level: ring` pushes (or old servers without `level`) get a banner; on web, where there is no push, the unread poll shows one only for replies to your own messages (`mine` went up) and new inbox items.
- **A screen that shows a conversation calls `useThreadOnScreen(threadId)`** (`src/store.tsx`): that thread gets no banner and is marked read (`POST /api/unread/read` with the last `db<id>` on screen) while it is focused and the app is active.
- **Theme** in `src/theme.tsx`: gold = the assistant itself; cyan = data and progress; semantic colors only for state. `chartA` / `chartB` are validated on both backgrounds — do not change casually.
- **Assistant name is never hard-coded**: `src/brand.ts` `agentName()` comes from the server's `/api/health` (`app_name`), remembered between launches. Do not write a product name into UI strings.
- Data loading is centralized in `src/store.tsx` (`LOADERS`), API calls in `src/api/`. Server routes and the source of truth for every page are documented at the top of `../server/data.py`.

### Server contract (see `../server/`)

- All configuration is `~/.openmousse/server.json` on the server. `/api/*` requires `Authorization: Bearer <token>` (also `X-API-Key` and `?token=` for `<Image>` loads); static files are public.
- Connection page (`src/screens/ConnectScreen.tsx`): native builds store base URL + token in the keychain (`expo-secure-store`), web uses localStorage and defaults to same-origin. `probe()` returns ok / auth / down.
- Chat (`src/api/client.ts`): SSE streaming; a reply runs to completion on the server even if the client disconnects, and `GET /api/chat/stream?thread=` re-attaches. Long-press a message: copy / edit / rewind (`/api/chat/rewind`) / delete.
- Optional data sources: `/api/health` reports `sources` (workouts, meals, body, calendar, health). `loadLive()` skips missing ones and boards render `NoSourceCard` instead of an error.
- Apple Health (iOS only, `src/api/health.ts`, `@kingstinct/react-native-healthkit`): daily summaries pushed to `/api/health/daily` and `/api/health/metrics`; reproductive-health types are excluded and never requested. Derived metrics (recovery, energy balance, fitness trend) are computed on the server.
- Attachments and voice input: `POST /api/chat/upload` (10 files × 30 MB per message), `POST /api/chat/transcribe`. Push: `/api/push/register`; the payload's `data.target` (`thread` / `card` / `inbox` / `today`, falling back to `data.thread` on old servers) says where a tap goes, and the `inbox` category's 同意 button approves without opening the card.
- Inbox (things waiting for your OK): `GET /api/inbox?status=pending|recent`, `GET /api/inbox?thread=<id>` (shown in the chat under the message with id `db<messageId>`), `POST /api/inbox/{id}` `{action}`. Cards render through `src/components/InboxCard.tsx` on Today and in chat; a decision turns the card into a one-line receipt in both places. "Want changes" opens the thread with a quote, and the next message is sent with `inboxId`. When `/api/inbox` 404s the app falls back to `/api/approvals`.
- Unread: `GET /api/unread` (polled every 45 s while the app is visible), `POST /api/unread/read`, `POST /api/feed/seen`. Cyan counts = unread replies, gold = inbox; the app icon badge is the server's `badge`. When `/api/unread` 404s there are no counts and no polling.
- Every page supports pull-to-refresh through `PullRefresh` (`src/components/ui.tsx`; the `Page` component in `MoreScreens.tsx` takes `refresh` keys); assistant replies render as Markdown (`src/components/Markdown.tsx`).
- **The refresh spinner follows only the user's own pull.** Never pass the store's `loading` (or any state that changes in the background) as `refreshing`: on iOS a programmatic `refreshing={true}` pushes the ScrollView down one spinner height and never pushes it back, so every background reload stacks more blank space onto tabs that are not on screen.

### Debugging

- Web supports deep links for screenshots: `?screen=Group&id=<id>&tab=board`, `?screen=今天`, `?drawer=1`. `?say=hello` sends a real message after connecting, on the dev server only (`npm run web`); production builds ignore it so a link can never make the agent act.
- `npx expo lint` caches under `.expo/cache/eslint`; a stale `import/namespace` error survives fixing the imported file — delete the cache or run `npx eslint src`.
- Headless Chrome's minimum window width is 500px; screenshots at 390px show a false right-edge crop.
- The Expo ESLint config includes the React Compiler rules: don't write `ref.current` during render (update it in an effect), don't call `setState` synchronously inside an effect (adjust state during render by comparing with the previous value), and keep `Date.now()` out of component bodies.

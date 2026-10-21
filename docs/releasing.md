# Releasing OpenMousse

[中文](releasing.zh-CN.md) · **English**

The checklist for a server release: what to run first, how the version numbers move, the tag, the order of server and app, and where the notes go. The app's native builds and the memory tree package (`tree/`) have their own versions and are released separately.

## 1. Before the release

Run all of these on the commit you're going to release, and only release that commit.

**Tests and lint**, as CI runs them:

```bash
pip install -r server/requirements.txt pytest ruff==0.15.2 ./tree
ruff check server tree/openmousse_tree packs/core/scripts packs/core/setup.py
python -m pytest server/tests -q          # under 10 minutes
bash -n install.sh && bash -n check.sh
cd app && npm ci && npm run typecheck && npm run lint
```

**The privacy scan.** [`tools/release/privacy_scan.py`](../tools/release/privacy_scan.py) goes through every tracked file (image metadata included) and reports key-shaped strings, home-directory and local paths, Tailscale addresses and hostnames, emails that aren't example addresses, phone numbers and words from your own list:

```bash
MOUSSE_PRIVACY_WORDS=~/private/words.txt python3 tools/release/privacy_scan.py \
  --secrets ~/.openmousse/server.json --secrets ~/.openmousse/secrets.env --secrets ~/.mousse-tree/config.json
```

- The word list lives outside the repository: one word or phrase per line (real names, schools, employers, addresses, host names, the words on your share pages). The `--secrets` files are your real `.env` / `server.json` / memory tree config.
- Both are compared by fingerprint only. A hit says the file, the line and which word or key, never the text.
- The release needs 0 findings, or a reason for each one that stays. Files that are fine as they are go in the `ALLOW` list at the top of the script, with the reason.

**A clean install.** On a clean machine, as a new normal user, install the release commit from a local mirror with a fake model, and walk through what a new user does:

```bash
python3 server/evals/fakemodel.py --key <any key> --echo &      # an OpenAI-compatible model on 127.0.0.1:8765, no provider, no cost
cat install.sh | MOUSSE_REPO_URL=<the local mirror> MOUSSE_NONINTERACTIVE=1 MOUSSE_CLAW=builtin MOUSSE_TZ=Europe/Lisbon bash
```

1. Choose the model: `setup_ctl.py add fake --url http://127.0.0.1:8765/v1 --key-stdin`, then `setup_ctl.py status`.
2. Start the services the way the installer says, then check:
   - `/api/health` answers 401 without a token, and with one it reports the new `version`, `api` and `features`;
   - a pairing code (`tokens.py pair`, then `POST /api/pair`) works once;
   - one message through `POST /api/chat/send` gets a reply.
3. Run `check.sh` and `mousse doctor`.
4. Try the trial install (`MOUSSE_TRIAL=1`) as another new user.
5. Run the installer again: it changes nothing in `server.json`.
6. If Docker is available, build `docker/` and call `/api/health` in the container.

Fix what's small before the release; write down the rest.

## 2. Version numbers

All in [`server/version.py`](../server/version.py), reported by `/api/health` as `server`:

- **`VERSION`** is the release date, `YYYY.MM.DD`. A second release on the same day is `-2`, then `-3`.
- **`API`** goes up by 1 when the app starts relying on a server endpoint that older servers don't have. The app's `SERVER_API` in [`app/src/api/version.ts`](../app/src/api/version.ts) changes in the same commit; servers below it show "update needed" in the app.
- **`FEATURES`**: every new endpoint the app uses gets a name here, in the same commit as the endpoint. The app checks `supports('<name>')` instead of probing for a 404. Names stay in the list.
- Not part of a server release: `app/app.json`'s `version` changes only for a new native build (JavaScript ships over the air), and the memory tree's version is in `tree/pyproject.toml`.

## 3. The tag

Tag the verified commit with an annotated tag named after the version:

```bash
git tag -a v2026.10.21 -m "OpenMousse 2026.10.21" <commit>
git push <remote> v2026.10.21
```

Once the tag is on the public repository, every install on the stable channel moves to it with `mousse update`: the stable channel follows the newest `v` tag ([`server/ops/update.py`](../server/ops/update.py); `mousse update --channel main` follows `main` instead). So:

- tag only a commit that passed everything in step 1;
- never move or delete a published tag; a fix is a new release (`-2`);
- if the development history isn't public, publish one squashed commit and tag that: a pushed tag carries every commit it can reach, and the privacy scan checks files, not history.

`mousse update --check` on a test install shows whether it sees the new tag.

## 4. Server first, then the app

1. Publish the release (the tagged commit and the tag) on the public repository, so the installer (`curl -fsSL https://openmousse.ai/install | bash`) and `mousse update` get the new server.
2. Update and restart the servers you run yourself, and check that `/api/health` reports the new version.
3. Only then ship the app's over-the-air update. The app's update command reinstalls the server from the public repository; an app that ships first sends people to a server without the version it expects.

## 5. Release notes

The notes are [`CHANGELOG.md`](../CHANGELOG.md) and [`CHANGELOG.zh-CN.md`](../CHANGELOG.zh-CN.md), newest first:

- While changes collect, the top section is "Unreleased (draft)". At release time it becomes `## <VERSION>`.
- Write what users see, and only what the code has. Check against `git log v<previous version>..<commit>`.
- For every feature that is off by default, say how to turn it on.
- For everything that spends the user's own model or API money, say so.
- Keep both languages in step. A GitHub release, if you make one, copies the section.

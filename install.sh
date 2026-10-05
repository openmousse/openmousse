#!/usr/bin/env bash
# OpenMousse 一条命令安装（Linux、macOS 或 Windows 的 WSL 2，已经跑着一个 claw 的机器：OpenClaw，或者别的有 OpenAI 兼容接口的 claw / agent）：
#   curl -fsSL https://raw.githubusercontent.com/openmousse/openmousse/main/install.sh | bash
# 或在仓库里：bash install.sh
#
# 做的事：clone / 更新仓库到 ~/openmousse → 建 ~/.openmousse/venv 装依赖 → 问几个问题 + 两个可以跳过的 → packs/core/setup.py 配好一切。
# 环境变量（非交互）：MOUSSE_LANG（zh|en）、MOUSSE_CLAW（openclaw | hermes | nanobot | letta，或别的 claw 的 OpenAI 兼容接口地址 http://…/v1）、MOUSSE_OPENCLAW_HOME、
#   别的 claw：MOUSSE_CLAW_NAME、MOUSSE_CLAW_TOKEN、MOUSSE_CLAW_MODEL、MOUSSE_CLAW_SKILLS（它的 skills 文件夹）、MOUSSE_CLAW_RULES（它每轮都读的规则文件）；
#   MOUSSE_TZ、MOUSSE_NAME、MOUSSE_DIR（仓库位置）、MOUSSE_BIND（auto|127.0.0.1|<ip>）、
#   MOUSSE_VAULT（服务器上已经在同步的 Obsidian 库文件夹，世界树和思考空间放进去；空 = 跳过）、MOUSSE_TREE_PUBLIC（y|n：用 Tailscale Funnel 开公网：AI 平台连世界树、分享链接、加朋友）
#   MOUSSE_NONINTERACTIVE=1：一个问题都不问，没给的用默认值（claw 替人装时用，见 docs/connect.md）
# 其它参数原样传给 setup.py，比如 --no-systemd、--no-tree。
set -euo pipefail

REPO_URL="${MOUSSE_REPO_URL:-https://github.com/openmousse/openmousse.git}"
MOUSSE_DIR="${MOUSSE_DIR:-$HOME/openmousse}"
VENV="$HOME/.openmousse/venv"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# 从仓库里直接跑就用这份，不 clone
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/server/run.py" ] && [ -f "$SELF_DIR/packs/core/setup.py" ]; then
  MOUSSE_DIR="$SELF_DIR"
fi

say "OpenMousse 安装 / install"
if [ "$(uname -s)" = Darwin ]; then  # macOS：系统自带的 python3 是 3.9，用 Homebrew 装新的
  GIT_HINT="xcode-select --install, or brew install git"; PY_HINT="brew install python"
else
  GIT_HINT="sudo apt install git"; PY_HINT="sudo apt install python3"
fi
command -v git >/dev/null || die "缺 git / git is missing ($GIT_HINT)"
command -v python3 >/dev/null || die "缺 python3 / python3 is missing (need 3.11+: $PY_HINT)"
python3 - <<'EOF' || die "python3 要 3.11 以上 / python3 must be 3.11 or newer ($PY_HINT)"
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  command -v uv >/dev/null || die "缺 venv / ensurepip 模块 / venv module missing (Ubuntu / Debian: sudo apt install python3-venv), or install uv first (https://docs.astral.sh/uv/)"
fi

if [ ! -f "$MOUSSE_DIR/server/run.py" ]; then
  say "拿仓库 / cloning → $MOUSSE_DIR"
  git clone --depth 1 "$REPO_URL" "$MOUSSE_DIR"
elif [ -d "$MOUSSE_DIR/.git" ] && [ "${MOUSSE_NO_PULL:-}" != "1" ] && [ "$MOUSSE_DIR" != "$SELF_DIR" ]; then
  say "更新仓库 / updating $MOUSSE_DIR"
  git -C "$MOUSSE_DIR" pull --ff-only || echo "pull 失败，用现有版本继续"
fi

say "Python 依赖 / dependencies → $VENV"
mkdir -p "$HOME/.openmousse"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  # 没有或坏了（比如上次 ensurepip 缺失建了个没 pip 的）就重建
  if python3 -c 'import ensurepip' 2>/dev/null; then python3 -m venv --clear "$VENV"; else uv venv -q --seed "$VENV"; fi
fi
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -r "$MOUSSE_DIR/server/requirements.txt"
"$VENV/bin/python" -m pip install -q "$MOUSSE_DIR/tree"

# 问题（有环境变量就不问；管道里跑时从 /dev/tty 读；没有终端就用默认值）
ask() {  # ask <变量名> <提示> <默认值>（默认值空 = 可以跳过，提示里不显示 []）
  local var="$1" prompt="$2" def="$3" ans=""
  if [ -n "${!var:-}" ]; then return; fi
  if [ -n "${MOUSSE_NONINTERACTIVE:-}" ]; then printf -v "$var" '%s' "$def"; return; fi  # claw 替人装：不等人回答（它的命令行可能带伪终端，读 /dev/tty 会一直卡住）
  if [ -t 0 ]; then
    read -r -p "$prompt${def:+ [$def]}: " ans || true
  elif { : < /dev/tty; } 2>/dev/null; then  # 打得开才读：没有控制终端（cron、CI）时 /dev/tty 在但打不开
    read -r -p "$prompt${def:+ [$def]}: " ans < /dev/tty || true
  fi
  printf -v "$var" '%s' "${ans:-$def}"
}
ask_secret() {  # ask_secret <变量名> <提示>：输入不显示（令牌之类）；有环境变量就不问，回车 = 空
  local var="$1" prompt="$2" ans=""
  if [ -n "${!var:-}" ]; then return; fi
  if [ -n "${MOUSSE_NONINTERACTIVE:-}" ]; then return; fi
  if [ -t 0 ]; then
    read -r -s -p "$prompt: " ans || true; echo
  elif { : < /dev/tty; } 2>/dev/null; then
    read -r -s -p "$prompt: " ans < /dev/tty || true; echo
  fi
  printf -v "$var" '%s' "$ans"
}
saved() {  # saved <python 表达式，c = server.json>：上次安装存下的值（没有就空），当默认值用
  [ -f "$HOME/.openmousse/server.json" ] || return 0
  python3 -c "import json, sys; c = json.load(open(sys.argv[1])); v = $1; print(v or '')" "$HOME/.openmousse/server.json" 2>/dev/null || true
}
# 语言默认：上次安装选的（server.json 的 language），没有就看 LC_ALL / LANG：zh 开头 → zh，其它 → en
DEF_LANG=""
if [ -f "$HOME/.openmousse/server.json" ]; then
  DEF_LANG="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("language") or "")' "$HOME/.openmousse/server.json" 2>/dev/null || true)"
fi
case "${DEF_LANG:-${LC_ALL:-${LANG:-}}}" in zh*|ZH*) DEF_LANG=zh ;; *) DEF_LANG=en ;; esac
# 再跑一遍（换 Tailscale 地址、更新）时一路回车不能把上次的答案改掉：OpenClaw 的位置、时区、助手名字都先用 server.json 里存下的
DEF_HOME="$(saved "c.get('openclaw_home')")"
[ -n "$DEF_HOME" ] || DEF_HOME="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
DEF_TZ="$(saved "c.get('timezone')")"
if [ -z "$DEF_TZ" ] && [ -f "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}/openclaw.json" ]; then  # OpenClaw 里设了用户时区（agents.defaults.userTimezone）就用它
  DEF_TZ="$(python3 -c 'import json, sys; print(((json.load(open(sys.argv[1])).get("agents") or {}).get("defaults") or {}).get("userTimezone") or "")' "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}/openclaw.json" 2>/dev/null || true)"
fi
if [ -z "$DEF_TZ" ]; then
  # Debian / Ubuntu 有 /etc/timezone；systemd 的机器问 timedatectl；macOS（和别的）看 /etc/localtime 指向哪个 zoneinfo
  DEF_TZ="$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || readlink /etc/localtime 2>/dev/null | sed -n 's|.*zoneinfo/||p' | grep . || echo UTC)"
  [ "$DEF_TZ" = "Etc/UTC" ] && DEF_TZ="UTC"
fi
DEF_NAME="$(saved "c.get('app_name')")"
[ -n "$DEF_NAME" ] || DEF_NAME=Mousse
say "几个问题 + 两个可以跳过的，直接回车用默认值 / a few questions + two optional ones, Enter keeps the default"
ask MOUSSE_LANG "语言 / language (zh = 中文, en = English)" "$DEF_LANG"
case "$(printf '%s' "$MOUSSE_LANG" | tr '[:upper:]' '[:lower:]')" in zh*|cn*|chinese*|中*) MOUSSE_LANG=zh ;; *) MOUSSE_LANG=en ;; esac
# 你的 claw：回车 = OpenClaw；别的 claw / agent 填它的 OpenAI 兼容接口地址。默认：上次装的那个，没装过就看这台机器上有没有 OpenClaw
DEF_CLAW="$(saved "(c.get('claw') or {}).get('url') if (c.get('claw') or {}).get('kind') == 'openai' else ''")"
[ -n "$DEF_CLAW" ] || DEF_CLAW=openclaw
ask MOUSSE_CLAW "你的 claw：回车 = OpenClaw；hermes、nanobot、letta；别的 claw 或 agent 填它的 OpenAI 兼容接口地址（写到 /v1） / your claw: Enter = OpenClaw; hermes, nanobot or letta; for another claw or agent, its OpenAI-compatible API URL (up to /v1)" "$DEF_CLAW"
PRESET=""
case "$(printf '%s' "$MOUSSE_CLAW" | tr '[:upper:]' '[:lower:]')" in
  http://*|https://*) CLAW_KIND=openai ;;
  hermes|nanobot|letta) CLAW_KIND=openai; PRESET="$(printf '%s' "$MOUSSE_CLAW" | tr '[:upper:]' '[:lower:]')" ;;
  *) CLAW_KIND=openclaw ;;
esac
preset() { python3 "$MOUSSE_DIR/server/claw_presets.py" "$PRESET" "$1" 2>/dev/null || true; }  # 预设的一个字段（server/claw_presets.py）
CLAW_ARGS=()
if [ "$CLAW_KIND" = openclaw ]; then
  command -v openclaw >/dev/null || echo "提示 / note: openclaw is not on PATH. Install OpenClaw and run openclaw onboard first, then come back."
  ask MOUSSE_OPENCLAW_HOME "OpenClaw 装在哪 / OpenClaw home (directory with openclaw.json)" "$DEF_HOME"
else
  if [ -n "$PRESET" ]; then
    ask MOUSSE_CLAW_URL "$(preset name) 的接口地址 / $(preset name)'s API URL" "$(preset url)"
    MOUSSE_CLAW="$MOUSSE_CLAW_URL"
  fi
  ask MOUSSE_CLAW_NAME "它叫什么（app 里这么叫它） / its name (shown in the app)" "$( [ -n "$PRESET" ] && preset name || saved "(c.get('claw') or {}).get('name')")"
  if [ -z "$(preset token_env)" ]; then  # Hermes / nanobot 的令牌在它们自己的 .env / 环境变量里，安装器按名字去读，不用问
    ask_secret MOUSSE_CLAW_TOKEN "接口令牌，没有就回车；输入不显示，重装时回车 = 沿用上次的 / API token, Enter if none; hidden, Enter on a rerun keeps the old one"
  fi
  case "$PRESET" in
    hermes|nanobot) ;;  # 模型用预设（Hermes 写 hermes-agent，nanobot 不发 model）
    letta) ask MOUSSE_CLAW_MODEL "Letta 里 agent 的名字或 id（请求里的 model） / the Letta agent's name or id (sent as model)" "$(saved "(c.get('claw') or {}).get('model')")" ;;
    *) ask MOUSSE_CLAW_MODEL "请求里的模型名（model） / the model name to send (model)" "$(saved "(c.get('claw') or {}).get('model')" | grep . || echo default)" ;;
  esac
  ask MOUSSE_CLAW_SKILLS "（可跳过）它的 skills 文件夹：OpenMousse 的 skill 软链进去 / (optional) its skills folder, OpenMousse's skills get linked in" "$(preset skills)"
  ask MOUSSE_CLAW_RULES "（可跳过）它每轮都读的规则文件（AGENTS.md 之类）：OpenMousse 的规矩追加进去 / (optional) the rules file it reads every turn (AGENTS.md or similar), OpenMousse's rules get appended" ""
  export MOUSSE_CLAW_TOKEN  # 令牌走环境变量给 setup.py，不放命令行（ps 看得到）
  CLAW_ARGS=(--claw-url "$MOUSSE_CLAW" --claw-name "${MOUSSE_CLAW_NAME:-My claw}")
  if [ -n "$PRESET" ]; then CLAW_ARGS+=(--claw-preset "$PRESET"); fi
  if [ -n "${MOUSSE_CLAW_MODEL:-}" ]; then CLAW_ARGS+=(--claw-model "$MOUSSE_CLAW_MODEL"); elif [ -z "$PRESET" ]; then CLAW_ARGS+=(--claw-model default); fi
  if [ -n "$MOUSSE_CLAW_SKILLS" ]; then CLAW_ARGS+=(--claw-skills "$MOUSSE_CLAW_SKILLS"); fi
  if [ -n "$MOUSSE_CLAW_RULES" ]; then CLAW_ARGS+=(--claw-rules "$MOUSSE_CLAW_RULES"); fi
fi
ask MOUSSE_TZ "你的时区 / your timezone (IANA name)" "$DEF_TZ"
# 不认识的时区（打错了，或者 claw 没替换说明页里的占位符）用默认值；这台机器没有时区库的话没法查，照单全收
if ! python3 -c 'import sys, zoneinfo
try:
    zoneinfo.ZoneInfo("UTC")
except Exception:
    sys.exit(0)
zoneinfo.ZoneInfo(sys.argv[1])' "$MOUSSE_TZ" 2>/dev/null; then
  echo "提示 / note: 时区「$MOUSSE_TZ」不认识，用 $DEF_TZ / unknown timezone \"$MOUSSE_TZ\", using $DEF_TZ"
  MOUSSE_TZ="$DEF_TZ"
fi
ask MOUSSE_NAME "助手叫什么 / assistant name (shown in the app)" "$DEF_NAME"
if [ "$CLAW_KIND" = openclaw ] && [ ! -f "$MOUSSE_OPENCLAW_HOME/openclaw.json" ]; then
  die "$MOUSSE_OPENCLAW_HOME/openclaw.json 不存在 / not found. Install OpenClaw and run openclaw onboard first (or give another claw's OpenAI-compatible URL instead)."
fi
# 可以跳过的两个：Obsidian 库（世界树和思考空间放进去）、开公网（Tailscale Funnel：AI 平台连世界树、分享链接、好友直连；默认开，2026-10-05）
ask MOUSSE_VAULT "（可跳过）Obsidian 库在这台服务器上的文件夹（已在同步：Obsidian Sync / Syncthing / git），世界树和思考空间放进去，回车跳过 / (optional) Obsidian vault folder on this server (already synced: Obsidian Sync / Syncthing / git) for the memory tree and the thinking space, Enter skips" ""
ask MOUSSE_TREE_PUBLIC "开公网 HTTPS 吗（推荐）？开了：Claude.ai、ChatGPT、Gemini 这些支持 MCP 的 AI 平台能连世界树，分享能发链接，好友直连这台服务器。用 Tailscale Funnel，只开这几条路，app 的接口不上公网。不开也能加好友（经 OpenMousse 中继，消息端到端加密）/ Open up public HTTPS (recommended)? Then AI platforms such as Claude.ai, ChatGPT or Gemini (any MCP client) can reach the memory tree, shares get links, and friends connect to this server directly. Uses Tailscale Funnel and opens only those paths, never the app's API. Friends work without it too, through the OpenMousse relay with end-to-end encryption (y/n)" "y"
case "$(printf '%s' "$MOUSSE_TREE_PUBLIC" | tr '[:upper:]' '[:lower:]')" in y|yes|1|true|是*) MOUSSE_TREE_PUBLIC=yes ;; *) MOUSSE_TREE_PUBLIC=no ;; esac
OPTIONAL=()
if [ -n "$MOUSSE_VAULT" ]; then OPTIONAL+=("--vault=$MOUSSE_VAULT"); fi
if [ "$MOUSSE_TREE_PUBLIC" = yes ]; then OPTIONAL+=(--tree-public); fi

exec "$VENV/bin/python" "$MOUSSE_DIR/packs/core/setup.py" --repo "$MOUSSE_DIR" --venv "$VENV" \
  --openclaw-home "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}" --tz "$MOUSSE_TZ" --name "$MOUSSE_NAME" --lang "$MOUSSE_LANG" --bind "${MOUSSE_BIND:-auto}" \
  ${CLAW_ARGS[@]+"${CLAW_ARGS[@]}"} ${OPTIONAL[@]+"${OPTIONAL[@]}"} "$@"

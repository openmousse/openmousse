#!/usr/bin/env bash
# OpenMousse 一条命令安装（Linux，已装好 OpenClaw 的机器）：
#   curl -fsSL https://raw.githubusercontent.com/openmousse/openmousse/main/install.sh | bash
# 或在仓库里：bash install.sh
#
# 做的事：clone / 更新仓库到 ~/openmousse → 建 ~/.openmousse/venv 装依赖 → 问四个问题 → packs/core/setup.py 配好一切。
# 环境变量（非交互）：MOUSSE_LANG（zh|en）、MOUSSE_OPENCLAW_HOME、MOUSSE_TZ、MOUSSE_NAME、MOUSSE_DIR（仓库位置）、MOUSSE_BIND（auto|127.0.0.1|<ip>）
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
command -v git >/dev/null || die "缺 git / git is missing (apt install git)"
command -v python3 >/dev/null || die "缺 python3 / python3 is missing (need 3.11+)"
python3 - <<'EOF' || die "python3 要 3.11 以上 / python3 must be 3.11 or newer"
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  command -v uv >/dev/null || die "缺 venv / ensurepip 模块 / venv module missing (Ubuntu / Debian: sudo apt install python3-venv), or install uv first (https://docs.astral.sh/uv/)"
fi
command -v openclaw >/dev/null || echo "提示 / note: openclaw is not on PATH. Install OpenClaw and run openclaw onboard first, then come back."

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

# 四个问题（有环境变量就不问；管道里跑时从 /dev/tty 读）
ask() {  # ask <变量名> <提示> <默认值>
  local var="$1" prompt="$2" def="$3" ans=""
  if [ -n "${!var:-}" ]; then return; fi
  if [ -t 0 ]; then
    read -r -p "$prompt [$def]: " ans || true
  elif [ -e /dev/tty ]; then
    read -r -p "$prompt [$def]: " ans < /dev/tty || true
  fi
  printf -v "$var" '%s' "${ans:-$def}"
}
# 语言默认：上次安装选的（server.json 的 language），没有就看 LC_ALL / LANG：zh 开头 → zh，其它 → en
DEF_LANG=""
if [ -f "$HOME/.openmousse/server.json" ]; then
  DEF_LANG="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("language") or "")' "$HOME/.openmousse/server.json" 2>/dev/null || true)"
fi
case "${DEF_LANG:-${LC_ALL:-${LANG:-}}}" in zh*|ZH*) DEF_LANG=zh ;; *) DEF_LANG=en ;; esac
DEF_HOME="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
DEF_TZ="$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
[ "$DEF_TZ" = "Etc/UTC" ] && DEF_TZ="UTC"
say "四个问题，直接回车用默认值 / four questions, Enter keeps the default"
ask MOUSSE_LANG "语言 / language (zh = 中文, en = English)" "$DEF_LANG"
case "$(printf '%s' "$MOUSSE_LANG" | tr '[:upper:]' '[:lower:]')" in zh*|cn*|chinese*|中*) MOUSSE_LANG=zh ;; *) MOUSSE_LANG=en ;; esac
ask MOUSSE_OPENCLAW_HOME "OpenClaw 装在哪 / OpenClaw home (directory with openclaw.json)" "$DEF_HOME"
ask MOUSSE_TZ "你的时区 / your timezone (IANA name)" "$DEF_TZ"
ask MOUSSE_NAME "助手叫什么 / assistant name (shown in the app)" "Mousse"
[ -f "$MOUSSE_OPENCLAW_HOME/openclaw.json" ] || die "$MOUSSE_OPENCLAW_HOME/openclaw.json 不存在 / not found. Install OpenClaw and run openclaw onboard first."

say "配置 / configuring"
exec "$VENV/bin/python" "$MOUSSE_DIR/packs/core/setup.py" --repo "$MOUSSE_DIR" --venv "$VENV" \
  --openclaw-home "$MOUSSE_OPENCLAW_HOME" --tz "$MOUSSE_TZ" --name "$MOUSSE_NAME" --lang "$MOUSSE_LANG" --bind "${MOUSSE_BIND:-auto}" "$@"

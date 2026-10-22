#!/usr/bin/env bash
# OpenMousse 一条命令安装（Linux、macOS 或 Windows 的 WSL 2）：
#   curl -fsSL https://raw.githubusercontent.com/openmousse/openmousse/main/install.sh | bash
# 或在仓库里：bash install.sh
#
# 做的事：问语言 → clone / 更新仓库到 ~/openmousse → 问几个问题（用哪个内核、时区、助手的名字，两个可以跳过的）→ 建 ~/.openmousse/venv
# 装依赖 → packs/core/setup.py 配好一切（自带的内核装完问要不要接着选模型）。问题都在装依赖之前问完，问完就不用守着（2026-10-22）。
# 语言先问（只有这一题中英两行），之后每句只用选的那种。选择题列编号，填编号、填值都行；回车 = 方括号里的默认。
# 环境变量（非交互）：MOUSSE_LANG（zh|en）、MOUSSE_CLAW（builtin（OpenMousse 自带的内核，不用装 OpenClaw）| openclaw | hermes | nanobot | letta，
#   或别的 claw 的 OpenAI 兼容接口地址 http://…/v1）、MOUSSE_OPENCLAW_HOME、
#   别的 claw：MOUSSE_CLAW_URL、MOUSSE_CLAW_NAME、MOUSSE_CLAW_TOKEN、MOUSSE_CLAW_MODEL、MOUSSE_CLAW_SKILLS（它的 skills 文件夹）、MOUSSE_CLAW_RULES（它每轮都读的规则文件）；
#   MOUSSE_TZ、MOUSSE_NAME、MOUSSE_DIR（仓库位置）、MOUSSE_BIND（auto|127.0.0.1|<ip>）、
#   MOUSSE_VAULT（服务器上已经在同步的 Obsidian 库文件夹，世界树和思考空间放进去；空 = 跳过）、MOUSSE_TREE_PUBLIC（y|n：用 Tailscale Funnel 开公网：AI 平台连世界树、分享链接、加朋友）
#   MOUSSE_NONINTERACTIVE=1：一个问题都不问，没给的用默认值（claw 替人装时用，见 docs/connect.md）
#   MOUSSE_TRIAL=1（或参数 --trial）：试装：自带的内核、只听 127.0.0.1、不问开公网、不碰 OpenClaw；问一句要不要装成开机自启的服务
#   （MOUSSE_TRIAL_SERVICE=y|n），不装就在后台先起一次。装了什么记进 ~/.openmousse/install-manifest.json（卸载照它删）
#   MOUSSE_PYTHON：用这个 python3（3.11 以上）；不给就找：macOS 先找 Homebrew 的（系统自带的是 3.9）
# 默认的内核：再装一遍 = 上次装的那个；第一次装 = 自带的内核，这台机器上有 OpenClaw 也一样（列表里选 2 就用它；2026-10-22 起）。
# macOS 自带的 bash 是 3.2：别用 ${x,,}、关联数组、read -t 小数这些 bash 4 才有的。
# 其它参数原样传给 setup.py，比如 --no-systemd、--no-tree。
set -euo pipefail

REPO_URL="${MOUSSE_REPO_URL:-https://github.com/openmousse/openmousse.git}"
MOUSSE_DIR="${MOUSSE_DIR:-$HOME/openmousse}"
VENV="$HOME/.openmousse/venv"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
t() { if [ "${MOUSSE_LANG:-}" = zh ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }  # t <中文> <English>：语言选好以后每句只说一种
# shellcheck disable=SC2088  # 给人看的路径，~ 不用展开
tilde() { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# 从仓库里直接跑就用这份，不 clone
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/server/run.py" ] && [ -f "$SELF_DIR/packs/core/setup.py" ]; then
  MOUSSE_DIR="$SELF_DIR"
fi

say "OpenMousse 安装 / install"
TRIAL="${MOUSSE_TRIAL:-}"
case "$(lower "$TRIAL")" in 1|y|yes|true) TRIAL=1 ;; *) TRIAL="" ;; esac
for arg in "$@"; do if [ "$arg" = "--trial" ]; then TRIAL=1; fi; done
IS_MAC=""
if [ "$(uname -s)" = Darwin ]; then IS_MAC=1; fi
if [ -n "$IS_MAC" ]; then  # macOS：系统自带的 python3 是 3.9，用 Homebrew 装新的；git 在 Apple 的命令行工具里（xcode-select）
  GIT_HINT="xcode-select --install, or brew install git"; PY_HINT="brew install python"
else
  GIT_HINT="sudo apt install git"; PY_HINT="sudo apt install python3"
fi
# git：macOS 没装命令行工具时 /usr/bin/git 只是个壳（一跑就报错、弹窗问要不要装），所以要真跑一下 git --version
if ! git --version >/dev/null 2>&1; then
  if [ -n "$IS_MAC" ] && ! xcode-select -p >/dev/null 2>&1; then
    if [ -z "${MOUSSE_NONINTERACTIVE:-}" ]; then xcode-select --install >/dev/null 2>&1 || true; fi
    die "要先装 Apple 的命令行工具（git 在里面）：弹出的窗口里点「安装」（没弹就自己跑 xcode-select --install），装完再跑一次 / Install Apple's command line tools first (git comes with them): click Install in the window that pops up (or run xcode-select --install yourself), then run this again"
  fi
  die "缺 git / git is missing ($GIT_HINT)"
fi
pick_python() {  # 3.11 以上的 python3：MOUSSE_PYTHON；macOS 先找 Homebrew 的（Apple 芯片 /opt/homebrew，Intel /usr/local）；再找 PATH 里的
  local c brew_prefix=""
  if [ -n "$IS_MAC" ] && command -v brew >/dev/null 2>&1; then brew_prefix="$(brew --prefix 2>/dev/null || true)"; fi
  for c in ${MOUSSE_PYTHON:-} ${brew_prefix:+$brew_prefix/bin/python3} ${IS_MAC:+/opt/homebrew/bin/python3 /usr/local/bin/python3} \
           python3 python3.13 python3.12 python3.11; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}
PY="$(pick_python)" || die "要 3.11 以上的 python3 / need python3 3.11 or newer ($PY_HINT)"
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  command -v uv >/dev/null || die "缺 venv / ensurepip 模块 / venv module missing (Ubuntu / Debian: sudo apt install python3-venv), or install uv first (https://docs.astral.sh/uv/)"
fi

# —— 问问题的几样工具 ——————————————————————————————————————————————————————————————————————
# 有环境变量就不问；管道里跑（curl | bash）时从 /dev/tty 读；不能问（MOUSSE_NONINTERACTIVE、cron / CI 没有终端）就用默认值。
can_ask() {
  if [ -n "${MOUSSE_NONINTERACTIVE:-}" ]; then return 1; fi  # claw 替人装：不等人回答（它的命令行可能带伪终端，读 /dev/tty 会一直卡住）
  if [ -t 0 ]; then return 0; fi
  { : < /dev/tty; } 2>/dev/null  # 打得开才读：没有控制终端时 /dev/tty 在但打不开
}
answer() {  # answer <提示>：读一行 → ANSWER。先清掉之前按下的键：下载、装依赖时按的回车会被当成这一题的回答（10/22 语言那题就这么被答掉了）
  ANSWER=""
  "$PY" -c 'import termios
try:
    with open("/dev/tty") as t:
        termios.tcflush(t, termios.TCIFLUSH)
except Exception:
    pass' 2>/dev/null || true
  if [ -t 0 ]; then read -r -p "$1" ANSWER || true; else read -r -p "$1" ANSWER < /dev/tty || true; fi
}
ask() {  # ask <变量名> <提示> [默认值]：回车 = 默认值（空 = 可以跳过，提示里不显示 []）
  local var="$1" prompt="$2" def="${3:-}"
  if [ -n "${!var:-}" ]; then return 0; fi
  if ! can_ask; then printf -v "$var" '%s' "$def"; return 0; fi
  answer "  $prompt${def:+ [$def]}: "
  printf -v "$var" '%s' "${ANSWER:-$def}"
}
ask_secret() {  # ask_secret <变量名> <提示>：输入不显示（令牌之类）；回车 = 空
  local var="$1" ans=""
  if [ -n "${!var:-}" ] || ! can_ask; then return 0; fi
  if [ -t 0 ]; then read -r -s -p "  $2: " ans || true; else read -r -s -p "  $2: " ans < /dev/tty || true; fi
  echo
  printf -v "$var" '%s' "$ans"
}
yesno() {  # yesno <变量名> <提示> <y|n>：回车 = 默认；存成 yes / no（环境变量给的 y、1、true 也认）
  local var="$1" a
  if [ -n "${!var:-}" ]; then
    a="${!var}"
  elif can_ask; then
    if [ "$3" = y ]; then answer "  $2 [Y/n]: "; else answer "  $2 [y/N]: "; fi
    a="${ANSWER:-$3}"
  else
    a="$3"
  fi
  case "$(lower "$a")" in y*|1|true|是*|好*|要*|开*) printf -v "$var" yes ;; *) printf -v "$var" no ;; esac
}
choose() {  # choose <变量名> <标题> <提示> <默认值> <值|给人看的>…：列出编号；填编号、值或给人看的那几个字都行；回车 = 默认
  local var="$1" head="$2" prompt="$3" def="$4" i shown="" opt low
  shift 4
  if [ -n "${!var:-}" ]; then return 0; fi
  if ! can_ask; then printf -v "$var" '%s' "$def"; return 0; fi
  echo
  say "$head"
  i=0
  for opt in "$@"; do
    i=$((i + 1))
    if [ "${opt%%|*}" = "$def" ]; then shown=$i; fi
    printf '    %d) %s\n' "$i" "${opt#*|}"
  done
  while :; do
    answer "  $prompt${shown:+ [$shown]}: "
    if [ -z "$ANSWER" ] && [ -n "$def" ]; then printf -v "$var" '%s' "$def"; return 0; fi
    low="$(lower "$ANSWER")"
    i=0
    for opt in "$@"; do
      i=$((i + 1))
      if [ "$ANSWER" = "$i" ] || [ "$low" = "$(lower "${opt%%|*}")" ] || [ "$low" = "$(lower "${opt#*|}")" ]; then
        printf -v "$var" '%s' "${opt%%|*}"
        return 0
      fi
    done
    case "$low" in http://*|https://*) printf -v "$var" '%s' "$ANSWER"; return 0 ;; esac  # 选内核那题直接填别的 agent 的接口地址也行
    echo "  $(t "没有这一项，填 1 到 $i" "Not one of these; enter 1 to $i")"
  done
}
saved() {  # saved <python 表达式，c = server.json>：上次安装存下的值（没有就空），当默认值用
  if [ ! -f "$HOME/.openmousse/server.json" ]; then return 0; fi
  "$PY" -c "import json, sys; c = json.load(open(sys.argv[1])); v = $1; print(v or '')" "$HOME/.openmousse/server.json" 2>/dev/null || true
}
has_tailscale() {  # macOS 从 App Store / brew --cask 装的 Tailscale 是 app，命令行在 app 包里，不在 PATH（同 setup.py 的 tailscale_bin）
  if command -v tailscale >/dev/null 2>&1; then return 0; fi
  [ -n "$IS_MAC" ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]
}

# —— 语言：头一个问，只有这一题中英两行 ——
# 默认：上次安装选的（server.json 的 language）；没有就看 macOS 的界面语言（终端的 LANG 常是 en_US，哪怕界面是中文），再看 LC_ALL / LANG
DEF_LANG="$(saved "c.get('language')")"
if [ -z "$DEF_LANG" ] && [ -n "$IS_MAC" ]; then
  DEF_LANG="$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p' | tr -d ' ",' || true)"
fi
case "${DEF_LANG:-${LC_ALL:-${LANG:-}}}" in zh*|ZH*) DEF_LANG=zh ;; *) DEF_LANG=en ;; esac
choose MOUSSE_LANG "语言 / Language" "选 / Choose" "$DEF_LANG" "zh|中文" "en|English"
case "$(lower "$MOUSSE_LANG")" in zh*|cn*|chinese*|中*) MOUSSE_LANG=zh ;; *) MOUSSE_LANG=en ;; esac

echo
if [ ! -f "$MOUSSE_DIR/server/run.py" ]; then
  say "$(t "下载 OpenMousse → $(tilde "$MOUSSE_DIR")" "Downloading OpenMousse → $(tilde "$MOUSSE_DIR")")"
  git clone -q --depth 1 "$REPO_URL" "$MOUSSE_DIR"  # 不打进度：在终端里就是十来行一直刷
  export MOUSSE_CLONED=1  # setup.py 记进安装清单：这份是安装器 clone 的，卸载可以删
elif [ -d "$MOUSSE_DIR/.git" ] && [ "${MOUSSE_NO_PULL:-}" != "1" ] && [ "$MOUSSE_DIR" != "$SELF_DIR" ]; then
  say "$(t "更新 $(tilde "$MOUSSE_DIR")" "Updating $(tilde "$MOUSSE_DIR")")"
  git -C "$MOUSSE_DIR" pull -q --ff-only || echo "$(t '更新失败，用现有的版本接着装' 'Update failed; continuing with the version already here')"
fi

# 再跑一遍（换 Tailscale 地址、更新）时一路回车不能把上次的答案改掉：内核、OpenClaw 的位置、时区、助手名字都先用 server.json 里存下的
DEF_HOME="$(saved "c.get('openclaw_home')")"
if [ -z "$DEF_HOME" ]; then DEF_HOME="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"; fi
DEF_TZ="$(saved "c.get('timezone')")"
if [ -z "$DEF_TZ" ] && [ -z "$TRIAL" ] && [ -f "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}/openclaw.json" ]; then  # OpenClaw 里设了用户时区（agents.defaults.userTimezone）就用它（试装不碰 OpenClaw）
  DEF_TZ="$("$PY" -c 'import json, sys; print(((json.load(open(sys.argv[1])).get("agents") or {}).get("defaults") or {}).get("userTimezone") or "")' "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}/openclaw.json" 2>/dev/null || true)"
fi
if [ -z "$DEF_TZ" ]; then
  # Debian / Ubuntu 有 /etc/timezone；systemd 的机器问 timedatectl；macOS（和别的）看 /etc/localtime 指向哪个 zoneinfo
  DEF_TZ="$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || readlink /etc/localtime 2>/dev/null | sed -n 's|.*zoneinfo/||p' | grep . || echo UTC)"
  if [ "$DEF_TZ" = "Etc/UTC" ]; then DEF_TZ="UTC"; fi
fi
DEF_NAME="$(saved "c.get('app_name')")"
if [ -z "$DEF_NAME" ]; then DEF_NAME=Mousse; fi
# 内核的默认：上次装的那个（别的 claw 存的是接口地址）；装过、server.json 里不是 builtin 也不是地址的 = OpenClaw；没装过 = 自带的内核
DEF_CLAW="$(saved "(c.get('claw') or {}).get('url') if (c.get('claw') or {}).get('kind') == 'openai' else 'builtin' if (c.get('claw') or {}).get('kind') == 'builtin' else ''")"
CLAW_URL_DEF=""
case "$DEF_CLAW" in http://*|https://*) CLAW_URL_DEF="$DEF_CLAW"; DEF_CLAW=other ;; esac
if [ -z "$DEF_CLAW" ]; then
  if [ -f "$HOME/.openmousse/server.json" ]; then DEF_CLAW=openclaw; else DEF_CLAW=builtin; fi
fi

if can_ask; then
  echo
  say "$(t '几个问题，回车 = 方括号里的默认' 'A few questions; Enter keeps the default in brackets')"
fi
if [ -n "$TRIAL" ]; then  # 试装：自带的内核、只听本机、不碰 OpenClaw（openclaw.json 一个字不读不改）
  say "$(t '试装：自带的内核，只在这台电脑上（127.0.0.1），不开公网，不碰 OpenClaw' 'Trial: the built-in engine, this computer only (127.0.0.1), nothing public, OpenClaw untouched')"
  MOUSSE_CLAW=builtin
  MOUSSE_BIND=127.0.0.1
fi
if [ -f "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}/openclaw.json" ]; then
  OC_LINE="$(t "OpenClaw：这台电脑上有（$(tilde "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}")）" "OpenClaw: found on this computer ($(tilde "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}"))")"
else
  OC_LINE="$(t 'OpenClaw：要先装好 OpenClaw' 'OpenClaw: install OpenClaw first')"
fi
choose MOUSSE_CLAW "$(t '助手用哪个内核' 'Which engine runs the assistant')" "$(t '选' 'Choose')" "$DEF_CLAW" \
  "builtin|$(t 'OpenMousse 自带的（推荐）：不用装别的，装完选个模型就能用' "OpenMousse's own (recommended): nothing else to install; choose a model and go")" \
  "openclaw|$OC_LINE" "hermes|Hermes Agent" "nanobot|nanobot" "letta|Letta" \
  "other|$(t '别的 agent：填它的 OpenAI 兼容接口地址' 'Another agent: enter its OpenAI-compatible API URL')"
PRESET=""
case "$(lower "$MOUSSE_CLAW")" in
  http://*|https://*) CLAW_KIND=openai ;;
  other) CLAW_KIND=openai; MOUSSE_CLAW="" ;;
  hermes|nanobot|letta) CLAW_KIND=openai; PRESET="$(lower "$MOUSSE_CLAW")"; MOUSSE_CLAW="" ;;
  builtin|mousse|openmousse|own|none) CLAW_KIND=builtin ;;
  *) CLAW_KIND=openclaw ;;
esac
preset() { "$PY" "$MOUSSE_DIR/server/claw_presets.py" "$PRESET" "$1" 2>/dev/null || true; }  # 预设的一个字段（server/claw_presets.py）
if [ "$CLAW_KIND" = openclaw ]; then  # 默认的地方有 openclaw.json 就不问在哪；找不到就问，还找不到就问要不要改用自带的内核
  if [ ! -f "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}/openclaw.json" ]; then
    ask MOUSSE_OPENCLAW_HOME "$(t 'OpenClaw 装在哪（有 openclaw.json 的文件夹）' 'Where OpenClaw lives (the folder with openclaw.json)')" "$DEF_HOME"
  fi
  MOUSSE_OPENCLAW_HOME="${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}"
  if [ ! -f "$MOUSSE_OPENCLAW_HOME/openclaw.json" ]; then
    if can_ask; then yesno USE_BUILTIN "$(t "$(tilde "$MOUSSE_OPENCLAW_HOME") 里没有 openclaw.json。改用 OpenMousse 自带的内核吗？" "There's no openclaw.json in $(tilde "$MOUSSE_OPENCLAW_HOME"). Use OpenMousse's own engine instead?")" y; else USE_BUILTIN=no; fi
    if [ "$USE_BUILTIN" = yes ]; then
      CLAW_KIND=builtin
    else
      die "$(t "$(tilde "$MOUSSE_OPENCLAW_HOME")/openclaw.json 不存在：先装好 OpenClaw、跑一次 openclaw onboard 再来；或者再跑一遍安装器，选 1 用 OpenMousse 自带的内核" "$(tilde "$MOUSSE_OPENCLAW_HOME")/openclaw.json not found: install OpenClaw and run openclaw onboard first, or run the installer again and choose 1 for OpenMousse's own engine")"
    fi
  fi
fi
CLAW_ARGS=()
if [ "$CLAW_KIND" = builtin ]; then
  CLAW_ARGS=(--builtin)  # 模型、搜索、记忆装完以后在终端里选（server/setup_ctl.py，安装器最后会问）
elif [ "$CLAW_KIND" = openai ]; then
  if [ -z "$MOUSSE_CLAW" ]; then  # 列表里选的预设或「别的 agent」：问接口地址
    if [ -n "$PRESET" ]; then
      ask MOUSSE_CLAW_URL "$(t "$(preset name) 的接口地址" "$(preset name)'s API URL")" "$(preset url)"
    else
      ask MOUSSE_CLAW_URL "$(t '接口地址（写到 /v1）' 'API URL (up to /v1)')" "$CLAW_URL_DEF"
    fi
    MOUSSE_CLAW="${MOUSSE_CLAW_URL:-}"
  fi
  case "$MOUSSE_CLAW" in
    http://*|https://*) ;;
    *) die "$(t "接口地址要以 http:// 或 https:// 开头（现在是「${MOUSSE_CLAW}」）" "The API URL has to start with http:// or https:// (got \"$MOUSSE_CLAW\")")" ;;
  esac
  ask MOUSSE_CLAW_NAME "$(t '在 app 里叫它什么' 'What the app calls it')" "$( [ -n "$PRESET" ] && preset name || saved "(c.get('claw') or {}).get('name')")"
  if [ -z "$(preset token_env)" ]; then  # Hermes / nanobot 的令牌在它们自己的 .env / 环境变量里，安装器按名字去读，不用问
    ask_secret MOUSSE_CLAW_TOKEN "$(t '接口令牌（没有就回车；输入不显示；再装一遍时回车 = 沿用上次的）' 'API token (Enter if none; hidden; on a rerun Enter keeps the old one)')"
  fi
  case "$PRESET" in
    hermes|nanobot) ;;  # 模型用预设（Hermes 写 hermes-agent，nanobot 不发 model）
    letta) ask MOUSSE_CLAW_MODEL "$(t 'Letta 里 agent 的名字或 id' "The Letta agent's name or id")" "$(saved "(c.get('claw') or {}).get('model')")" ;;
    *) ask MOUSSE_CLAW_MODEL "$(t '请求里写的模型名（model）' 'Model name to send (model)')" "$(saved "(c.get('claw') or {}).get('model')" | grep . || echo default)" ;;
  esac
  ask MOUSSE_CLAW_SKILLS "$(t '它的 skills 文件夹，OpenMousse 的 skill 链进去（没有就回车）' "Its skills folder; OpenMousse's skills get linked in (Enter to skip)")" "$(preset skills)"
  ask MOUSSE_CLAW_RULES "$(t '它每轮都读的规则文件（AGENTS.md 之类），OpenMousse 的规矩追加进去（没有就回车）' "The rules file it reads every turn (AGENTS.md or similar); OpenMousse's rules get appended (Enter to skip)")" ""
  export MOUSSE_CLAW_TOKEN  # 令牌走环境变量给 setup.py，不放命令行（ps 看得到）
  CLAW_ARGS=(--claw-url "$MOUSSE_CLAW" --claw-name "${MOUSSE_CLAW_NAME:-My claw}")
  if [ -n "$PRESET" ]; then CLAW_ARGS+=(--claw-preset "$PRESET"); fi
  if [ -n "${MOUSSE_CLAW_MODEL:-}" ]; then CLAW_ARGS+=(--claw-model "$MOUSSE_CLAW_MODEL"); elif [ -z "$PRESET" ]; then CLAW_ARGS+=(--claw-model default); fi
  if [ -n "$MOUSSE_CLAW_SKILLS" ]; then CLAW_ARGS+=(--claw-skills "$MOUSSE_CLAW_SKILLS"); fi
  if [ -n "$MOUSSE_CLAW_RULES" ]; then CLAW_ARGS+=(--claw-rules "$MOUSSE_CLAW_RULES"); fi
fi

if can_ask; then echo; fi
tz_ok() {  # 认不认识这个时区；这台机器没有时区库的话没法查，照单全收
  "$PY" -c 'import sys, zoneinfo
try:
    zoneinfo.ZoneInfo("UTC")
except Exception:
    sys.exit(0)
zoneinfo.ZoneInfo(sys.argv[1])' "$1" 2>/dev/null
}
ask MOUSSE_TZ "$(t '时区' 'Time zone')" "$DEF_TZ"
TRIES=0
while ! tz_ok "$MOUSSE_TZ"; do  # 打错了，或者 claw 没替换说明页里的占位符：能问就再问，不能问（或者问了三次）用默认值
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -ge 3 ] || ! can_ask; then
    echo "  $(t "不认识时区「${MOUSSE_TZ}」，用 $DEF_TZ" "Unknown time zone \"$MOUSSE_TZ\"; using $DEF_TZ")"
    MOUSSE_TZ="$DEF_TZ"
    break
  fi
  echo "  $(t "不认识「${MOUSSE_TZ}」：填 IANA 名字，比如 Asia/Shanghai、Europe/London、America/New_York" "Unknown time zone \"$MOUSSE_TZ\": use an IANA name such as Asia/Shanghai, Europe/London or America/New_York")"
  MOUSSE_TZ=""
  ask MOUSSE_TZ "$(t '时区' 'Time zone')" "$DEF_TZ"
done
ask MOUSSE_NAME "$(t '助手的名字（app 里显示）' "The assistant's name (shown in the app)")" "$DEF_NAME"
OPTIONAL=()
if [ -n "$TRIAL" ]; then  # 试装不问开公网、不问 Obsidian 库；服务可选：不装就在后台先起一次（setup.py 的 start_background）
  yesno MOUSSE_TRIAL_SERVICE "$(t '装成开机自启的服务吗？不装就现在在后台起一次，电脑重启后要自己再起' 'Start it automatically when you log in? If not, it starts once in the background now, and again by hand after a reboot')" n
  if [ "$MOUSSE_TRIAL_SERVICE" != yes ]; then OPTIONAL+=(--no-systemd); fi
  OPTIONAL+=(--trial)
  MOUSSE_VAULT=""; MOUSSE_TREE_PUBLIC=no
else
  # 可以跳过的两个：Obsidian 库（世界树和思考空间放进去）、开公网（Tailscale Funnel：AI 平台连世界树、分享链接、好友直连；有 Tailscale 默认开，2026-10-05）
  ask MOUSSE_VAULT "$(t '这台机器上的 Obsidian 库文件夹，世界树和 Zen 的笔记放进去（没有就回车）' 'Obsidian vault folder on this machine, for the memory tree and Zen notes (Enter to skip)')" ""
  if [ -z "${MOUSSE_TREE_PUBLIC:-}" ] && ! has_tailscale; then  # 没装 Tailscale 开不了：不问，说一句
    MOUSSE_TREE_PUBLIC=no
    if can_ask; then echo "  $(t '开公网：这台没装 Tailscale，先不开（装好以后再跑一遍安装器）' "Going public: Tailscale isn't installed here, so not now (run the installer again once it is)")"; fi
  fi
  if [ -z "${MOUSSE_TREE_PUBLIC:-}" ] && can_ask; then
    echo "  $(t '开公网（Tailscale Funnel）：Claude.ai、ChatGPT 能连世界树，分享能发链接，好友直连；只开这几条路，app 的接口不上公网' "Going public (Tailscale Funnel): Claude.ai and ChatGPT can reach the memory tree, shares get links, friends connect directly; only those paths, never the app's API")"
  fi
  yesno MOUSSE_TREE_PUBLIC "$(t '开吗？' 'Open it?')" y
fi
if [ -n "$MOUSSE_VAULT" ]; then OPTIONAL+=("--vault=$MOUSSE_VAULT"); fi
if [ "$MOUSSE_TREE_PUBLIC" = yes ]; then OPTIONAL+=(--tree-public); fi

echo
say "$(t "装 Python 依赖（一两分钟）→ $(tilde "$VENV")" "Installing Python dependencies (a minute or two) → $(tilde "$VENV")")"
mkdir -p "$HOME/.openmousse"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  # 没有或坏了（比如上次 ensurepip 缺失建了个没 pip 的）就重建
  if "$PY" -c 'import ensurepip' 2>/dev/null; then "$PY" -m venv --clear "$VENV"; else uv venv -q --seed --python "$PY" "$VENV"; fi
fi
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$MOUSSE_DIR/server/requirements.txt"
"$VENV/bin/python" -m pip install -q --disable-pip-version-check "$MOUSSE_DIR/tree"

REST=()
for arg in "$@"; do if [ "$arg" != "--trial" ]; then REST+=("$arg"); fi; done
exec "$VENV/bin/python" "$MOUSSE_DIR/packs/core/setup.py" --repo "$MOUSSE_DIR" --venv "$VENV" \
  --openclaw-home "${MOUSSE_OPENCLAW_HOME:-$DEF_HOME}" --tz "$MOUSSE_TZ" --name "$MOUSSE_NAME" --lang "$MOUSSE_LANG" --bind "${MOUSSE_BIND:-auto}" \
  ${CLAW_ARGS[@]+"${CLAW_ARGS[@]}"} ${OPTIONAL[@]+"${OPTIONAL[@]}"} ${REST[@]+"${REST[@]}"}

"""媒体那几样共用的一层（第 3 批第 3–7 项，2026-10-19）：按用途选服务商、拿地址和密钥、发请求（出错分类、报错抹掉密钥）、记花费、存文件、
找本机的命令。看图（engine/vision.py）、看文档（pdf.py）、转写（stt.py）、朗读（tts.py）、生图（imagegen.py）、看视频（video.py）都用它；
第 9 项新加的搜索后端记花费也用它。

用途：server.json 的 engine.use.<用途>（不写 = 没有这一样，老实例一个字不变）：
  "vision": "google/gemini-3.5-flash-lite"                     服务商/型号
  "tts": {"model": "openai/gpt-4o-mini-tts", "voice": "alloy"}  写成对象：model 之外的是这一家的选项（voice、format、size……）
  "stt": "local/whisper-cli"                                    本机的：local/<命令>（PATH 上找得到才算配好）
  用途：vision 看图 / pdf 看文档 / video 看视频 / stt 转写 / tts 朗读 / image 生图（PURPOSES）。
服务商（引用的前一段）：
  模型服务商目录里的（engine/catalog.py 的 CLOUD、LOCAL：openai、google、anthropic、groq……）：地址、密钥照 models.provider（engine.providers 改过的也认）
  只做媒体的（catalog.MEDIA：deepgram、elevenlabs、fal、comfyui）：目录里的地址和密钥变量名，engine.providers.<id> 照样能改 url / key_env
  local：本机的命令（whisper-cli、ffmpeg、pdftoppm……）
  对象里还能写 url（换地址：自己的代理、测试的假服务）、key_env（换密钥变量名）、timeout（秒）。
  用户自己的套餐（claude-code/…、chatgpt/…）不能写在这里：这几样都是按量花钱的（写了 = 没配，problem() 说为什么）。
密钥：照 models.env_value（进程环境 → app 里存的 secrets.env → env_file），每次现读；只放请求头，不进地址；报错的字里抹掉（embeddings.scrub）。
出错（MediaError.kind，和 embeddings.EmbedError 一样分）：rate（429）/ auth（401、403、没密钥）/ bad（400、404、422）/ server（5xx）/
  net（连不上、超时）/ shape（回来的不像样）/ config（没配、配错了）。
花费：每次调用（成的、没成的）记一行 engine_media_usage（mousse.db，第一次写的时候建表）：哪一样、哪家、型号、用了多少（units + unit：
  token、秒、字、张）、钱（价目表里有才算，没有记 null）、成没成、多久、哪个对话；usage.py 的汇总多一栏 media（只加）。
  价目（PRICES）：只列核对过官方价目页的，每一项写出处和哪天核对的；token 计价的型号按接口回的 token 数算。
拿文件（load：看图、看文档、转写、看视频共用）：参数里写一样 path / url / attachment——
  path        工作区里的文件（host_files 的路径守卫：只在自己的工作区、像密钥的不碰）；也认图库里存着的图（工具结果里那句「saved on the server
              at …/engine/images/<sha>.png」）和这个对话的附件（消息里写着的 …/uploads/<对话>/… 那个路径，只认这个对话自己的）
  url         公网上的一个文件（照 web_fetch：只去公网、钉住查过的地址、每跳重查；网址里带着私事的先交 Doorman；这个 Agent 关了 web_fetch 就不行）
  attachment  这个对话的附件 id（别的对话的不认）
存文件：生成的语音、图存成这个对话的附件（files.save_generated：和上传的附件同一个目录、同一张表），这一轮回完挂到回复上，app 现在就能看、能放
  （chat.py 回复入库以后调 files.link_generated）。图另外进图库（engine/images.py）交给模型看。
没人在看的那几轮、后台任务的子 Agent：这几样工具都不给（engine/unattended.py 的 NO_TOOLS、engine/tasks.py 的 CHILD_NEVER）。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from config import settings
from i18n import L

from . import catalog, models, store

PURPOSES = models.MEDIA_PURPOSES
LOCAL = "local"
FREE = (LOCAL, "comfyui")  # 本机跑的：不花钱，花费上限不拦（第 4 批，engine/spend.py）
TIMEOUT = 120.0
ERROR_MAX = 300

# —— 价目 ————————————————————————————————————————————————————————————————————————
# (服务商/型号) → {"unit": token / second / char / image, …价钱}。token 的：in、out = 每百万 token 的美元；按秒的：second = 每秒；按字的：char = 每字；
# 按张的：image = 每张。只列核对过官方价目页的（出处、日期在每一组上面）。没列的记 null（界面上写「?」），不猜。
#   OpenAI  developers.openai.com/api/docs/pricing（页面没写日期），2026-10-19 核对：转写按分钟（$0.006 / $0.003，Whisper $0.006）；
#           tts-1 / tts-1-hd 按每百万字 $15 / $30；gpt-4o-mini-tts 按 token（文字输入 $0.60、音频输出 $12，接口不回音频 token 数：不算钱）；
#           生图按 token（gpt-image-1 文字输入 $5、图输入 $10、图输出 $40；gpt-image-1-mini $2 / $2.50 / $8；gpt-image-1.5 图 $8 / $32；
#           gpt-image-2 图 $8 / $30），接口回的 usage 里有 token 数
#   Google  ai.google.dev/gemini-api/docs/pricing（页面 2026-10-16 更新），2026-10-19 核对 Standard 档：gemini-2.5-flash-lite 文字 / 图 / 视频
#           输入 $0.10、音频 $0.30、输出 $0.40；gemini-3.5-flash-lite 输入 $0.30、输出 $2.50；TTS gemini-2.5-flash-preview-tts 输入 $0.50、
#           音频输出 $10，gemini-3.8-flash-tts 到 2026-12-31 输入 $0.50 / 输出 $9（2027-01-01 起翻倍，这里按现在的）；生图 gemini-nano-banana-2.1
#           图输出每百万 token $30，gemini-3.1-flash-image $60，gemini-3-pro-image $120（接口回 token 数；输入的价钱没列：提示词、参考图
#           几百到一两千 token，和一张图的输出比可以不计，所以记的是输出的钱）
#   Groq    console.groq.com/docs/speech-to-text，2026-10-19 核对：whisper-large-v3-turbo 每小时 $0.04、whisper-large-v3 $0.111，一次最少按 10 秒算
#   Deepgram deepgram.com/pricing，2026-10-19 核对（按量）：Nova-3 单语 $0.0043 / 分钟、多语 $0.0052；Aura-2 朗读每千字 $0.030
#           （Aura-2 的型号名带着声音：aura-2-thalia-en……，键末尾的 * = 这个前缀的都算）
#   ElevenLabs elevenlabs.io/pricing/api，2026-10-19 核对：每千字 v2 Multilingual / v3 / v4 $0.08，Flash / Turbo $0.04；
#           第 14 项（2026-10-20 核对）：Eleven Music 每分钟 $0.15（型号 music_v1 / music_v2 / music_v2_5 同价）
#   Google  第 14 项（ai.google.dev/gemini-api/docs/pricing，页面 2026-10-19 更新，2026-10-20 核对）：Gemini Omni Flash（gemini-omni-1.1-flash；
#           预览版 gemini-omni-flash-preview 2026-10-22 关）输入每百万 token $1.50、视频输出 $17.50（720p 每秒 5792 个 token，约 $0.10 一秒；
#           文字输出 $9，这里一律按视频算，略高一点）；Lyria 按首：lyria-3.5、lyria-3-pro-preview $0.08，lyria-3-clip-preview（30 秒）$0.04
PRICES: dict[str, dict] = {
    "openai/gpt-4o-transcribe": {"unit": "second", "second": 0.006 / 60},
    "openai/gpt-4o-mini-transcribe": {"unit": "second", "second": 0.003 / 60},
    "openai/whisper-1": {"unit": "second", "second": 0.006 / 60},
    "openai/tts-1": {"unit": "char", "char": 15.0 / 1e6},
    "openai/tts-1-hd": {"unit": "char", "char": 30.0 / 1e6},
    "openai/gpt-4o-mini-tts": {"unit": "token", "in": 0.60, "out": 12.0},
    "openai/gpt-image-1": {"unit": "token", "in": 5.0, "image_in": 10.0, "out": 40.0},
    "openai/gpt-image-1-mini": {"unit": "token", "in": 2.0, "image_in": 2.50, "out": 8.0},
    "openai/gpt-image-1.5": {"unit": "token", "in": 5.0, "image_in": 8.0, "out": 32.0},
    "openai/gpt-image-2": {"unit": "token", "in": 5.0, "image_in": 8.0, "out": 30.0},
    "google/gemini-2.5-flash-lite": {"unit": "token", "in": 0.10, "audio_in": 0.30, "out": 0.40},
    "google/gemini-3.5-flash-lite": {"unit": "token", "in": 0.30, "out": 2.50},
    "google/gemini-2.5-flash-preview-tts": {"unit": "token", "in": 0.50, "out": 10.0},
    "google/gemini-3.8-flash-tts": {"unit": "token", "in": 0.50, "out": 9.0},
    "google/gemini-nano-banana-2.1": {"unit": "token", "in": 0.0, "out": 30.0},
    "google/gemini-3.1-flash-image": {"unit": "token", "in": 0.0, "out": 60.0},
    "google/gemini-3-pro-image": {"unit": "token", "in": 0.0, "out": 120.0},
    "groq/whisper-large-v3-turbo": {"unit": "second", "second": 0.04 / 3600, "min_seconds": 10},
    "groq/whisper-large-v3": {"unit": "second", "second": 0.111 / 3600, "min_seconds": 10},
    "deepgram/nova-3": {"unit": "second", "second": 0.0043 / 60},
    "deepgram/nova-3-multilingual": {"unit": "second", "second": 0.0052 / 60},
    "deepgram/aura-2-*": {"unit": "char", "char": 0.030 / 1000},
    "elevenlabs/eleven_multilingual_v2": {"unit": "char", "char": 0.08 / 1000},
    "elevenlabs/eleven_v3": {"unit": "char", "char": 0.08 / 1000},
    "elevenlabs/eleven_flash_v2_5": {"unit": "char", "char": 0.04 / 1000},
    "elevenlabs/eleven_turbo_v2_5": {"unit": "char", "char": 0.04 / 1000},
    "elevenlabs/music_*": {"unit": "second", "second": 0.15 / 60},
    "google/gemini-omni-1.1-flash": {"unit": "token", "in": 1.50, "out": 17.50},
    "google/gemini-omni-flash-preview": {"unit": "token", "in": 1.50, "out": 17.50},
    "google/lyria-3.5": {"unit": "song", "song": 0.08},
    "google/lyria-3-pro-preview": {"unit": "song", "song": 0.08},
    "google/lyria-3-clip-preview": {"unit": "song", "song": 0.04},
}


# 设置中心（engine/setup_api.py 的 /api/setup/purposes）列出来的几样：用途 → 名字、代价、能选哪几家。做好一样加一样（没做的不列）。
LABELS = {"vision": (("看图（给看不见图的模型）", "Viewing images (for models that can't see them)"),
                     ("一张图问一次；便宜的看图模型一张不到 1 美分，同一张图的描述存着不再花", "One call per image; a cheap vision model costs well "
                      "under a cent per image, and descriptions are kept so the same image isn't paid for twice")),
          "pdf": (("看 PDF（给不能原样读 PDF 的模型）", "Reading PDFs (for models that can't read them as they are)"),
                  ("按页数算 token：一页大约 250–1500 个输入 token，便宜的模型几十页不到 1 美分", "Billed as tokens per page (roughly 250–1,500 "
                   "input tokens a page): a few dozen pages cost under a cent on a cheap model")),
          "stt": (("转写录音（工具、附件、语音输入）", "Transcription (the tool, attachments, voice input)"),
                  ("按分钟：OpenAI 每分钟 0.3–0.6 美分，Groq 每小时 4–11 美分，本机的 whisper.cpp 不花钱", "Per minute: OpenAI 0.3–0.6 US cents a "
                   "minute, Groq 4–11 cents an hour, local whisper.cpp is free")),
          "tts": (("朗读（speak 工具、用语音回）", "Speech (the speak tool, voice replies)"),
                  ("按字：OpenAI tts-1 每百万字 15 美元，ElevenLabs 每千字 4–8 美分，本机的不花钱", "Per character: OpenAI tts-1 $15 per million "
                   "characters, ElevenLabs 4–8 US cents per thousand, local commands are free")),
          "video": (("看视频", "Watching videos"),
                    ("按 token：视频大约每秒 300 个 token（Gemini），便宜的型号一分钟不到 1 美分", "Billed as tokens: roughly 300 tokens a second "
                     "of video on Gemini, under a cent a minute on a cheap model")),
          "image": (("生图", "Image generation"),
                    ("按张：一张几美分到二十几美分（看型号和尺寸）；本机的 ComfyUI 不花钱", "Per image: a few US cents to over 20 cents depending on "
                     "model and size; local ComfyUI is free")),
          "video_gen": (("生成视频", "Video generation"),
                        ("按秒：Gemini Omni Flash 720p 约 $0.10 一秒（一段 3–10 秒）；每次先问你", "Per second: Gemini Omni Flash about $0.10 a second "
                         "at 720p (3–10 s a clip); each one asks you first")),
          "music": (("生成音乐", "Music generation"),
                    ("按首或按分钟：Lyria 一首 $0.04–0.08，ElevenLabs 每分钟 $0.15", "Per song or per minute: Lyria $0.04–0.08 a song, ElevenLabs "
                     "$0.15 a minute"))}


def offered() -> dict[str, dict]:
    """设置中心能选的几样 → {用途: {"label", "cost", "providers": [能选的那几家]}}。"""
    every = {**catalog.CLOUD, **catalog.LOCAL}
    who = {"vision": [pid for pid, e in every.items() if (e.get("caps") or {}).get("images") in (True, "some")
                      and (e.get("engine") or {}).get("images") is not False],
           "pdf": [pid for pid, e in catalog.CLOUD.items() if (e.get("caps") or {}).get("pdf") is True],
           "stt": ["openai", "groq", "google", *(m for m, e in catalog.MEDIA.items() if "stt" in e["does"]), LOCAL],
           "tts": ["openai", "google", *(m for m, e in catalog.MEDIA.items() if "tts" in e["does"]), LOCAL],
           "image": ["openai", "google", *(m for m, e in catalog.MEDIA.items() if "image" in e["does"])],
           "video": [pid for pid, e in catalog.CLOUD.items() if (e.get("caps") or {}).get("video") is True]
           + [pid for pid, e in every.items() if (e.get("caps") or {}).get("images") is True and (e.get("caps") or {}).get("video") is not True],
           "video_gen": ["google", *(m for m, e in catalog.MEDIA.items() if "video_gen" in e["does"])],
           "music": ["google", *(m for m, e in catalog.MEDIA.items() if "music" in e["does"])]}
    return {k: {"label": catalog.text(LABELS[k][0]), "cost": catalog.text(LABELS[k][1]), "providers": who[k]} for k in LABELS if k in who}


class MediaError(Exception):
    """一次请求没做成。kind 见文件开头；str(e) 是给模型、用户看的一句（不带密钥）。"""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


# —— 用途 → 服务商、型号、选项 ——————————————————————————————————————————————————————————

@dataclass
class Choice:
    purpose: str
    provider: str
    model: str
    opts: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.model}"


REF = re.compile(r"^([a-z0-9][a-z0-9_.-]*)/(\S+)$")


def raw_use(purpose: str) -> Any:
    use = models.cfg().get("use")
    return use.get(purpose) if isinstance(use, dict) else None


def parse(purpose: str, v: Any) -> Choice | None:
    """engine.use.<用途> 的一种写法 → Choice；不写、false、写错了 → None。"""
    if v is None or v is False:
        return None
    opts: dict = {}
    if isinstance(v, dict):
        if v.get("enabled") is False:
            return None
        opts = {k: x for k, x in v.items() if k != "model"}
        v = v.get("model")
    if not isinstance(v, str):
        return None
    m = REF.match(v.strip())
    if not m:
        return None
    provider, model = m.group(1), m.group(2)
    if models.subscription(f"{provider}/{model}") or provider in models.SUBSCRIPTIONS:
        return None  # 用户自己的套餐只给他自己的回合（may_spend），这几样都是按量花钱的：不收
    if provider in catalog.CLOUD or provider in catalog.LOCAL:
        model = catalog.bare(provider, model)
    return Choice(purpose, provider, model, opts)


def choice(purpose: str) -> Choice | None:
    """这一样配的是哪家哪个型号（不管密钥在不在：调的时候才查，查不到报 auth）。"""
    return parse(purpose, raw_use(purpose))


def problem(purpose: str) -> str | None:
    """engine.use.<用途> 写了但写错了（状态里给用户看）；没写、写对了 → None。"""
    v = raw_use(purpose)
    if v is None or v is False:
        return None
    c = choice(purpose)
    if c is not None:
        return None if known(c.provider) else L(f"engine.use.{purpose}：不认识 {c.provider} 这一家（engine.providers 里加上它）",
                                                f"engine.use.{purpose}: {c.provider} isn't a known provider (add it under engine.providers)")
    ref = v.get("model") if isinstance(v, dict) else v
    if isinstance(ref, str) and (models.subscription(ref.strip()) or ref.strip().split("/", 1)[0] in models.SUBSCRIPTIONS):
        return L(f"engine.use.{purpose} 不能用你自己的套餐（{ref.strip()}）：这一样按量花钱，写一个有密钥的服务商",
                 f"engine.use.{purpose} can't use your own plan ({ref.strip()}): this is pay as you go, so name a provider with a key")
    return L(f"engine.use.{purpose} 要写成 服务商/型号（比如 openai/gpt-4o-transcribe），或者 {{\"model\": …}}",
             f"engine.use.{purpose} must look like provider/model (e.g. openai/gpt-4o-transcribe) or {{\"model\": …}}")


def known(provider: str) -> bool:
    return provider == LOCAL or provider in catalog.CLOUD or provider in catalog.LOCAL or provider in catalog.MEDIA or \
        provider in (models.cfg().get("providers") or {})


# —— 地址和密钥 ——————————————————————————————————————————————————————————————————

def endpoint(c: Choice) -> tuple[str, str, str]:
    """→ (地址的根, 密钥, 密钥变量名)。模型服务商照 models.provider；只做媒体的照 catalog.MEDIA；对象里写的 url / key_env 优先。
    没有这一家抛 MediaError(config)。密钥可以是空的（本机的服务、不要密钥的）。"""
    if c.provider == LOCAL:
        return "", "", ""
    over = (models.cfg().get("providers") or {}).get(c.provider)
    over = over if isinstance(over, dict) else {}
    if c.provider in catalog.MEDIA:
        e = catalog.MEDIA[c.provider]
        url = str(c.opts.get("url") or over.get("url") or e.get("url") or "")
        key_env = str(c.opts.get("key_env") or over.get("key_env") or e.get("key_env") or "")
        key = str(over.get("key") or "") or (models.env_value(key_env).strip() if key_env else "")
        return url.rstrip("/"), key, key_env
    try:
        p = models.provider(c.provider)
    except Exception as e:  # noqa: BLE001 — EngineError：不认识的服务商
        raise MediaError("config", str(e)) from None
    url = str(c.opts.get("url") or p.get("url") or (catalog.entry(c.provider) or {}).get("url") or "")
    key_env = str(c.opts.get("key_env") or "")
    key = models.env_value(key_env).strip() if key_env else str(p.get("key") or "")
    return url.rstrip("/"), key, key_env or str(p.get("key_name") or p.get("key_env") or "")


def gemini_base(url: str) -> str:
    """Google 的兼容地址（…/v1beta/openai）→ 它自己的接口（…/v1beta）。"""
    u = url.rstrip("/")
    return u[: -len("/openai")] if u.endswith("/openai") else u


def need_key(c: Choice, key: str, key_env: str) -> None:
    if not key:
        raise MediaError("auth", L(f"没有 {c.provider} 的密钥：{key_env or '（没写变量名）'} 没设（进程环境、app 里存的密钥和 env_file 里都没有）",
                                   f"No key for {c.provider}: {key_env or '(no variable name)'} isn't set (not in the environment, the keys saved "
                                   "in the app or the env_file)"))


def local_command(name: str) -> str | None:
    """本机的命令（干净的 PATH 上找）：找不到 → None（这一样不给）。"""
    from . import host_exec
    return shutil.which(name, path=host_exec.clean_path() or None)


async def run_local(argv: list[str], *, timeout: float, cwd: str | None = None, stdin: bytes | None = None) -> tuple[int, bytes, str]:
    """跑一个本机的命令（pdftoppm、ffmpeg、ffprobe、whisper-cli、piper……）：环境只给 PATH、LANG（不带密钥），自己一个进程组，超时整组杀掉；
    stdin 给了就从标准输入喂进去。→ (退出码, 标准输出（最多 OUT_MAX）, 标准错误的最后 2000 字)；超时抛 MediaError(net)。"""
    import asyncio
    import signal
    from . import host_exec
    env = {"PATH": host_exec.clean_path(), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", **({"HOME": cwd} if cwd else {})}
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    def kill() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        kill()
        await proc.wait()
        raise MediaError("net", f"{os.path.basename(argv[0])}: no result within {timeout:g}s") from None
    except asyncio.CancelledError:  # 用户点了停：整组杀掉，照样往外抛
        kill()
        raise
    return int(proc.returncode or 0), (out or b"")[:OUT_MAX], (err or b"").decode("utf-8", "replace")[-2000:]


OUT_MAX = 1024 * 1024


async def duration(data: bytes, name: str = "audio") -> float | None:
    """音频、视频多长（秒）：WAV 看文件头；别的有 ffprobe 就量一下；量不出来 → None（按秒计价的就记成没价钱）。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        import io
        import wave
        try:
            with wave.open(io.BytesIO(data)) as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except (wave.Error, EOFError):
            pass
    exe = local_command("ffprobe")
    if exe is None:
        return None
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mousse-media-") as d:
        f = Path(d) / ("in" + (Path(name).suffix[:10] or ".bin"))
        f.write_bytes(data)
        try:
            rc, out, _ = await run_local([exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(f)], timeout=30, cwd=d)
        except MediaError:
            return None
    try:
        return float(out.decode().strip()) if rc == 0 else None
    except ValueError:
        return None


# —— 发请求 ——————————————————————————————————————————————————————————————————————

_KEY_CHARS = re.compile(r"[\x21-\x7e]+")


def scrub(text: str, *secrets: str) -> str:
    from .embeddings import scrub as _scrub
    return _scrub(text, *secrets)


def fail(c: Choice, r: httpx.Response, key: str) -> MediaError:
    try:
        body = r.json()
    except ValueError:
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None) or \
        (body.get("detail") if isinstance(body, dict) and isinstance(body.get("detail"), str) else None) or r.text[:500] or r.reason_phrase
    s = r.status_code
    kind = "rate" if s == 429 else "auth" if s in (401, 403) else "server" if s >= 500 else "bad"
    if kind == "bad" and re.search(r"api[ _-]?key|API_KEY_INVALID|unauthenticated|permission", f"{msg}", re.I):
        kind = "auth"
    return MediaError(kind, scrub(f"{c.provider} HTTP {s}: {msg}", key))


async def call(c: Choice, method: str, url: str, *, key: str = "", headers: dict | None = None, json_body: Any = None, data: Any = None,
               files: Any = None, content: bytes | None = None, params: dict | None = None, timeout: float | None = None,
               want: str = "json") -> tuple[Any, httpx.Response]:
    """发一个请求 → (JSON / 字节, 响应)。出错抛 MediaError（报错里不带密钥）。want = json / bytes / none。"""
    if key and not _KEY_CHARS.fullmatch(key):  # 发出去之前就拦下：httpx 拒收时的报错里带着整个请求头
        raise MediaError("auth", L(f"{c.provider} 的密钥里有放不进请求头的字（空格、换行、中文……）",
                                   f"The {c.provider} key has characters that can't go in a request header (spaces, line breaks, non-ASCII)"))
    t = float(timeout or c.opts.get("timeout") or TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(t, connect=min(10.0, t)), follow_redirects=False) as client:
            r = await client.request(method, url, headers=headers, json=json_body, data=data, files=files, content=content, params=params)
    except httpx.TimeoutException:
        raise MediaError("net", f"{c.provider}: no answer within {t:g}s") from None
    except httpx.LocalProtocolError as e:
        raise MediaError("bad", f"{c.provider}: {type(e).__name__}") from None
    except httpx.HTTPError as e:
        raise MediaError("net", scrub(f"{c.provider}: {type(e).__name__}: {e}", key)) from None
    except (httpx.InvalidURL, ValueError) as e:
        raise MediaError("config", L(f"{c.provider}：地址写得不对（{type(e).__name__}）", f"{c.provider}: the url isn't valid ({type(e).__name__})")) from None
    if r.status_code >= 400:
        raise fail(c, r, key)
    if want == "bytes":
        return r.content, r
    if want == "none":
        return None, r
    try:
        out = r.json()
    except ValueError:
        raise MediaError("shape", f"{c.provider}: the answer isn't JSON") from None
    return out, r


# —— Google 自己的接口（看视频、朗读、生图、按 PDF 读、搜索接地）——————————————————————————————————

async def gemini(c: Choice, parts: list[dict], *, config: dict | None = None, tools: list | None = None, system: str = "",
                 timeout: float | None = None) -> dict:
    """POST <根>/models/<型号>:generateContent → 回的 JSON。parts 是 Gemini 的样子（text / inline_data / file_data）。"""
    base, key, key_env = endpoint(c)
    need_key(c, key, key_env)
    body: dict = {"contents": [{"role": "user", "parts": parts}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if config:
        body["generationConfig"] = config
    if tools:
        body["tools"] = tools
    data, _ = await call(c, "POST", f"{gemini_base(base)}/models/{c.model}:generateContent", key=key,
                         headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json_body=body, timeout=timeout)
    if not isinstance(data, dict):
        raise MediaError("shape", f"{c.provider}: the answer isn't a JSON object")
    return data


def gemini_parts(data: dict) -> list[dict]:
    cands = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    content = (cands[0] or {}).get("content") if cands and isinstance(cands[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def gemini_text(data: dict) -> str:
    return "".join(str(p.get("text") or "") for p in gemini_parts(data) if not p.get("thought"))


def gemini_modalities(data: dict) -> dict[str, int]:
    """输入里每一种（TEXT / IMAGE / AUDIO / VIDEO / DOCUMENT）各多少 token（usageMetadata.promptTokensDetails）。"""
    u = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    out: dict[str, int] = {}
    for d in u.get("promptTokensDetails") or []:
        if isinstance(d, dict) and d.get("modality"):
            out[str(d["modality"]).upper()] = out.get(str(d["modality"]).upper(), 0) + int(d.get("tokenCount") or 0)
    return out


def gemini_usage(data: dict) -> tuple[int, int]:
    u = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    return int(u.get("promptTokenCount") or 0), int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)


def inline(mime: str, data: bytes) -> dict:
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


INLINE_MAX = 20 * 1024 * 1024   # Gemini 的 inline_data：整个请求 20 MB 以内，再大的先传 Files API
UPLOAD_WAIT = 300.0             # 传上去以后等它处理好（state = ACTIVE）最多多久（秒）


async def gemini_upload(c: Choice, data: bytes, mime: str, name: str) -> dict:
    """传一个文件到 Gemini 的 Files API（可续传的那种：先 start 拿上传地址，再 upload, finalize），等它处理好 → {"uri", "mime_type", "name"}。
    文件在 Google 那边留 48 小时；用完调用方可以 gemini_forget 删掉。"""
    import asyncio
    base, key, key_env = endpoint(c)
    need_key(c, key, key_env)
    root = gemini_base(base)
    up_root = root[: -len("/v1beta")] + "/upload/v1beta" if root.endswith("/v1beta") else root + "/upload"
    _, r = await call(c, "POST", f"{up_root}/files", key=key, want="none", json_body={"file": {"display_name": name[:120]}},
                      headers={"x-goog-api-key": key, "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                               "X-Goog-Upload-Header-Content-Length": str(len(data)), "X-Goog-Upload-Header-Content-Type": mime})
    url = r.headers.get("x-goog-upload-url") or ""
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise MediaError("shape", f"{c.provider}: no upload address came back")
    got, _ = await call(c, "POST", url, key=key, content=data, timeout=max(TIMEOUT, len(data) / 200_000),
                        headers={"x-goog-api-key": key, "X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0",
                                 "Content-Type": mime})
    f = (got or {}).get("file") if isinstance(got, dict) else None
    if not isinstance(f, dict) or not f.get("name"):
        raise MediaError("shape", f"{c.provider}: the upload answer has no file")
    t0 = time.monotonic()
    while str(f.get("state") or "ACTIVE") == "PROCESSING":
        if time.monotonic() - t0 > float(c.opts.get("upload_wait") or UPLOAD_WAIT):
            raise MediaError("net", f"{c.provider}: the uploaded file wasn't ready within {UPLOAD_WAIT:g}s")
        await asyncio.sleep(min(5.0, 0.5 + (time.monotonic() - t0) / 10))
        f, _ = await call(c, "GET", f"{root}/{f['name']}", key=key, headers={"x-goog-api-key": key})
        f = f if isinstance(f, dict) else {}
    if str(f.get("state") or "ACTIVE") != "ACTIVE" or not f.get("uri"):
        raise MediaError("bad", f"{c.provider}: the uploaded file couldn't be processed ({f.get('state')})")
    return {"uri": f["uri"], "mime_type": f.get("mimeType") or mime, "name": f["name"]}


async def gemini_forget(c: Choice, name: str) -> None:
    """删掉传上去的文件（不要紧：删不掉 48 小时后它自己没了）。"""
    try:
        base, key, _ = endpoint(c)
        await call(c, "DELETE", f"{gemini_base(base)}/{name}", key=key, headers={"x-goog-api-key": key}, want="none", timeout=20)
    except MediaError:
        pass


# —— 一次零工具的问答（看图、看文档：OpenAI 兼容的、Claude 的，经内核自己的 providers）——————————————————————

async def ask(c: Choice, question: str, blocks: list[dict], *, system: str = "", max_tokens: int = 4096, timeout: float | None = None) -> tuple[str, dict, str]:
    """给这个模型发一条 user（字 + 图 / 文档块，中立格式），零工具 → (回答, 用量 {in, out}, 真正回答的型号)。google 走它自己的接口。
    出错抛 MediaError。"""
    if c.provider == "google":
        parts: list[dict] = [{"text": question}]
        for b in blocks:
            if b.get("type") in ("image", "document", "video", "audio") and b.get("data"):
                parts.append({"inline_data": {"mime_type": b.get("mime") or "application/octet-stream", "data": b["data"]}})
        data = await gemini(c, parts, system=system, timeout=timeout, config={"maxOutputTokens": max_tokens})
        i, o = gemini_usage(data)
        return gemini_text(data).strip(), {"in": i, "out": o}, c.model
    from . import providers
    from .errors import EngineError
    from .session import user
    if not models.valid_ref(c.ref):
        raise MediaError("config", L(f"{c.ref} 不是这台服务器认得的模型（engine.providers 里加上它）", f"{c.ref} isn't a model this server knows (add it under engine.providers)"))
    req = providers.Request(system=system, messages=[user(question, blocks)], tools=[], max_tokens=max_tokens,
                            timeout=float(timeout or c.opts.get("timeout") or TIMEOUT), cache_tail=False)
    msg = None
    try:
        async for ev in providers.stream(c.ref, req):
            if ev.get("type") == "done":
                msg = ev["message"]
    except EngineError as e:
        kind = {"auth": "auth", "rate": "rate", "timeout": "net", "network": "net"}.get(e.kind, "server" if (e.status or 0) >= 500 else "bad")
        raise MediaError(kind, str(e)) from None
    if msg is None:
        raise MediaError("shape", f"{c.ref}: no answer")
    from .session import text_of
    u = msg.get("usage") or {}
    return text_of(msg).strip(), {"in": int(u.get("in") or 0) + int(u.get("cache_read") or 0), "out": int(u.get("out") or 0)}, str(msg.get("served") or c.model)


# —— 花费 ————————————————————————————————————————————————————————————————————————

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_media_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  units REAL NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT '', tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  cost REAL, ok INTEGER NOT NULL DEFAULT 1, ms INTEGER NOT NULL DEFAULT 0, error TEXT, conversation TEXT, agent TEXT);
CREATE INDEX IF NOT EXISTS engine_media_usage_at ON engine_media_usage(julianday(at));
CREATE TABLE IF NOT EXISTS engine_media_cache (
  key TEXT PRIMARY KEY, kind TEXT NOT NULL, model TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL);
"""
_ready: set[str] = set()


def _db():
    """mousse.db 里这两张表（第一次用时建）。"""
    conn = store.connect()
    if str(settings.db) not in _ready:
        conn.executescript(SCHEMA)
        _ready.add(str(settings.db))
    return conn


def cost_of(ref: str, *, units: float = 0, unit: str = "", tokens_in: int = 0, tokens_out: int = 0, image_tokens_in: int = 0,
            audio_tokens_in: int = 0) -> float | None:
    """这一次值多少美元（价目表里没有 → None）。"""
    p = PRICES.get(ref) or next((v for k, v in PRICES.items() if k.endswith("*") and ref.startswith(k[:-1])), None)
    if p is None:
        if unit in ("token", "") and (tokens_in or tokens_out):  # 看图、看文档问的是对话模型：照对话模型的价目（engine/models.py 的 PRICES）
            return models.cost(ref, {"in": tokens_in, "out": tokens_out})
        return None
    if p["unit"] == "token":
        if not (tokens_in or tokens_out):
            return None
        if audio_tokens_in and "audio_in" not in p:
            return None  # 音频输入的价钱价目表里没写（各家多半比文字贵）：不猜
        text_in = max(0, tokens_in - image_tokens_in - audio_tokens_in)
        total = (text_in * p.get("in", 0) + image_tokens_in * p.get("image_in", p.get("in", 0)) + audio_tokens_in * p.get("audio_in", p.get("in", 0))
                 + tokens_out * p.get("out", 0)) / 1e6
        return round(total, 6)
    if p["unit"] == "second" and unit == "second":
        return round(max(units, float(p.get("min_seconds") or 0)) * p["second"], 6)
    if p["unit"] == unit and unit in p:
        return round(units * p[unit], 6)
    return None


def record(purpose: str, c: Choice, *, units: float = 0, unit: str = "", tokens_in: int = 0, tokens_out: int = 0, cost: float | None = None,
           ok: bool = True, ms: int = 0, error: str | None = None, conversation: str | None = None, agent: str | None = None,
           unpriced: bool = False, **extra: int) -> None:
    """记一次调用（成的、没成的都记）。cost 不给就按价目表算；unpriced = 算不出来（比如不知道录音多长），钱记 null。记不了不要紧（不拖累调用）。"""
    if cost is None and ok and not unpriced:
        cost = cost_of(c.ref, units=units, unit=unit, tokens_in=tokens_in, tokens_out=tokens_out, **extra)
    try:
        with store._lock:
            conn = _db()
            try:
                with conn:
                    conn.execute("INSERT INTO engine_media_usage(at, purpose, provider, model, units, unit, tokens_in, tokens_out, cost, ok, ms, error, "
                                 "conversation, agent) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (store.now_iso(), purpose, c.provider, c.model, float(units), unit, int(tokens_in), int(tokens_out), cost, 1 if ok else 0,
                                  int(ms), (error or "")[:ERROR_MAX] or None, conversation, agent))
            finally:
                conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 媒体的用量没记上：{type(e).__name__}: {str(e)[:120]}")


class Meter:
    """with Meter("stt", c, ctx) as m: …; m.units = …：出了 with 自动记一行（出错的也记，带上错）。"""

    def __init__(self, purpose: str, c: Choice, conversation: str | None = None, agent: str | None = None) -> None:
        self.purpose, self.c, self.conversation, self.agent = purpose, c, conversation, agent
        self.units: float = 0
        self.unit = ""
        self.tokens_in = self.tokens_out = 0
        self.extra: dict[str, int] = {}
        self.cost: float | None = None
        self.unpriced = False  # 算不出钱（不知道多长……）：记 null，不按 0 算
        self.t0 = 0.0

    def __enter__(self) -> Meter:
        self.t0 = time.monotonic()
        return self

    def __exit__(self, typ, exc, tb) -> None:
        ms = int((time.monotonic() - self.t0) * 1000)
        if exc is not None and not isinstance(exc, Exception):
            return  # 被取消（用户点了停：CancelledError 不是 Exception）：不记
        record(self.purpose, self.c, units=self.units, unit=self.unit, tokens_in=self.tokens_in, tokens_out=self.tokens_out, cost=self.cost,
               ok=exc is None, ms=ms, error=str(exc) if exc is not None else None, conversation=self.conversation, agent=self.agent,
               unpriced=self.unpriced, **self.extra)


def spent_since(start: datetime) -> tuple[float, dict[str, float]]:
    """start 以来媒体那几样算得出的钱 → (总数, 各 Agent 的)。花费上限算今天花了多少用（第 4 批，engine/spend.py）。还没有这张表 = 0。"""
    if not settings.db.exists():
        return 0.0, {}
    import contextlib
    import sqlite3
    try:
        with contextlib.closing(sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=10)) as conn:
            rows = conn.execute("SELECT agent, SUM(cost) FROM engine_media_usage WHERE cost IS NOT NULL AND julianday(at) >= julianday(?) "
                                "GROUP BY agent", (start.isoformat(),)).fetchall()
    except sqlite3.Error:
        return 0.0, {}
    return sum(float(c or 0.0) for _, c in rows), {str(a): float(c or 0.0) for a, c in rows if a}


def summary(start: datetime) -> dict:
    """start 以来的媒体用量 → {"total", "byPurpose", "byModel"}，每一项 {"calls", "failed", "cost", "unpriced"}。还没有这张表 = 全是 0。"""
    rows = []
    if settings.db.exists():
        import contextlib
        import sqlite3
        try:
            with contextlib.closing(sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=10)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT purpose, provider, model, cost, ok FROM engine_media_usage WHERE julianday(at) >= julianday(?)",
                                    (start.isoformat(),)).fetchall()
        except sqlite3.Error:
            rows = []

    def blank() -> dict:
        return {"calls": 0, "failed": 0, "cost": 0.0, "unpriced": 0}

    def add(acc: dict, r) -> None:
        acc["calls"] += 1
        acc["failed"] += 0 if r["ok"] else 1
        if r["cost"] is None:
            acc["unpriced"] += 1 if r["ok"] else 0
        else:
            acc["cost"] += float(r["cost"])
    total, by_p, by_m = blank(), {}, {}
    for r in rows:
        add(total, r)
        add(by_p.setdefault(r["purpose"], blank()), r)
        add(by_m.setdefault(f"{r['provider']}/{r['model']}", blank()), r)

    def done(acc: dict) -> dict:
        return {**acc, "cost": round(acc["cost"], 6)}
    return {"total": done(total), "byPurpose": [{"purpose": k, **done(v)} for k, v in sorted(by_p.items())],
            "byModel": [{"model": k, **done(v)} for k, v in sorted(by_m.items(), key=lambda kv: -kv[1]["cost"])]}


# —— 缓存（看图的描述：按图的内容 hash 存，不重复花钱）——————————————————————————————————————

def cached(key: str) -> str | None:
    try:
        with store._lock:
            conn = _db()
            try:
                r = conn.execute("SELECT text FROM engine_media_cache WHERE key=?", (key,)).fetchone()
            finally:
                conn.close()
        return str(r["text"]) if r else None
    except Exception:  # noqa: BLE001
        return None


def remember(key: str, kind: str, model: str, text: str) -> None:
    try:
        with store._lock:
            conn = _db()
            try:
                with conn:
                    conn.execute("INSERT OR REPLACE INTO engine_media_cache(key, kind, model, text, at) VALUES(?,?,?,?,?)",
                                 (key, kind, model, text, store.now_iso()))
            finally:
                conn.close()
    except Exception:  # noqa: BLE001
        pass


# —— 这一轮的主模型 ——————————————————————————————————————————————————————————————————

def turn_model(turn: str | None) -> str | None:
    """这一轮在用的模型（engine_turns：换过模型的取最后真调过的那个）。找不到 → None。"""
    if not turn:
        return None
    try:
        with store.read() as conn:
            r = conn.execute("SELECT model, models FROM engine_turns WHERE id=?", (turn,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if r is None:
        return None
    try:
        used = json.loads(r["models"] or "[]")
    except ValueError:
        used = []
    last = next((m for m in reversed(used) if isinstance(m, str) and m), None) if isinstance(used, list) else None
    return models.canonical(last or str(r["model"] or "")) or None


def sees_images(ref: str | None) -> bool:
    """这个模型收不收图（目录里那一家的脾气 images = false 的不收；claude-code、chatgpt 的套餐都收）。认不出 → False。"""
    if not ref:
        return False
    if models.subscription(ref):
        return True
    try:
        name, _ = models.parse_ref(ref)
        p = models.provider(name)
    except Exception:  # noqa: BLE001
        return False
    from . import providers
    return p.get("images", True) is not False and time.monotonic() - providers._no_images.get(ref, float("-inf")) > providers.NO_IMAGES_FOR


# OpenAI 兼容的 /chat/completions 里能放 PDF（file 块，data:application/pdf;base64,…）的：OpenAI 自己的（文件输入的文档写着）。
# 别家的兼容接口文档里没写（Gemini 的 PDF 走它自己的接口：engine.use.pdf 写 google/…）；自己的代理、别家写了的：engine.providers.<id>.pdf = true
PDF_CHAT = frozenset({"openai"})


def pdf_chat(p: dict) -> bool:
    """这一家（models.provider 的那一份）的 /chat/completions 收不收 PDF。"""
    v = p.get("pdf")
    return v is True or (v is None and p.get("name") in PDF_CHAT and p.get("api") == models.OPENAI)


def reads_pdf(ref: str | None) -> bool:
    """这个模型能不能原样读 PDF（中立格式的 document 块）：Claude（官方接口）、OpenAI 自己的 /chat/completions（PDF_CHAT），
    engine.providers.<id>.pdf = true 的。套餐、Responses API 的 → False（照旧抽字）。"""
    if not ref or models.subscription(ref):
        return False
    try:
        name, _ = models.parse_ref(ref)
        p = models.provider(name)
    except Exception:  # noqa: BLE001
        return False
    if p.get("pdf") is False:
        return False
    return p.get("api") == models.ANTHROPIC or pdf_chat(p)


# —— 拿文件（工具的 path / url / attachment）——————————————————————————————————————————————

SRC_KEYS = ("path", "url", "attachment")
STORE_NAME = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|gif|webp)$")


@dataclass
class Source:
    data: bytes
    name: str     # 文件名（给模型看的、存附件用的）
    label: str    # 从哪来（工作区里的路径、网址、附件名），给模型看
    mime: str     # 记着的类型（附件记录、网址回的 content-type；工作区的文件 = 按扩展名猜的），可能是 ""
    origin: str   # path / store / attachment / url
    path: Path | None = None  # 服务器上的文件（工作区、图库、附件；网址来的 = None）


def _fail(zh: str, en: str):
    from .loop import ToolFailure
    return ToolFailure(L(zh, en))


def _read_plain(path: Path, max_bytes: int) -> bytes:
    """读服务器上的一个文件（图库、附件）：不跟符号链接，超过 max_bytes 不读。"""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        size = os.fstat(fd).st_size
        if size > max_bytes:
            raise _fail(f"{path.name} 太大了（{human_bytes(size)}，最多 {human_bytes(max_bytes)}）",
                        f"{path.name} is too large ({human_bytes(size)}; {human_bytes(max_bytes)} at most)")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(max_bytes + 1)
    finally:
        os.close(fd)


def _attachment(thread: str, *, fid: str | None = None, path: str | None = None):
    import files
    with files._lock, files.adb() as conn:  # noqa: SLF001 — 附件表和它的锁
        if fid is not None:
            return conn.execute("SELECT * FROM attachments WHERE id=? AND thread=?", (fid, thread)).fetchone()
        return conn.execute("SELECT * FROM attachments WHERE path=? AND thread=?", (path, thread)).fetchone()


def _bases(base: Path) -> set[str]:
    """一个文件夹的两种写法：原样的、解开符号链接的（模型照着消息里写的抄，消息里是原样的那种）。"""
    out = {os.path.normpath(str(base.expanduser()))}
    try:
        out.add(os.path.normpath(str(base.expanduser().resolve())))
    except (OSError, RuntimeError):
        pass
    return out


def _under(p: str, base: Path) -> bool:
    q = os.path.normpath(p)
    return any(q.startswith(b.rstrip("/") + "/") for b in _bases(base))


async def load(args: dict, ctx, *, max_bytes: int, what: tuple[str, str] = ("文件", "file")) -> Source:
    """工具参数里的 path / url / attachment（只能写一样）→ Source。不行抛 ToolFailure（给模型看的一句）。"""
    from . import host_files, images
    given = [k for k in SRC_KEYS if str(args.get(k) or "").strip()]
    if len(given) != 1:
        raise _fail(f"path、url、attachment 写一样（要看的{what[0]}从哪来）", f"Give exactly one of path, url or attachment (where the {what[1]} comes from)")
    key = given[0]
    val = str(args[key]).strip()
    agent = ctx.agent or "main"
    if key == "attachment":
        row = _attachment(ctx.conversation, fid=val)
        if row is None:
            raise _fail(f"这个对话里没有附件 {val}", f"There's no attachment {val} in this conversation")
        p = Path(row["path"])
        try:
            return Source(_read_plain(p, max_bytes), row["name"], row["name"], str(row["mime"] or ""), "attachment", p)
        except OSError:
            raise _fail(f"附件 {row['name']} 的文件不在了", f"The file of attachment {row['name']} is no longer there") from None
    if key == "url":
        import webfetch

        from . import web
        url = val if "://" in val else "https://" + val
        if not web.allowed(agent, "fetch"):
            raise _fail("你不能上网打开网址（web_fetch 关着）", "You can't open web addresses (web_fetch is off for you)")
        try:
            webfetch.check_url(url)
        except webfetch.FetchError as e:
            raise _fail(str(e), str(e)) from None
        if held := await web.door(ctx, url, "request"):
            raise _fail(held, held)
        try:
            data, ctype, final = await web.fetch(url, max_bytes=max_bytes)
        except webfetch.FetchError as e:
            raise _fail(f"打不开 {url}：{e}", f"Couldn't open {url}: {e}") from None
        name = os.path.basename(final.split("?")[0].split("#")[0]) or "download"
        return Source(data, name[:120], final, ctype, "url")
    if val.startswith("/") and _under(val, images.store_dir()):
        q = Path(os.path.normpath(val))
        if str(q.parent) not in _bases(images.store_dir()) or not STORE_NAME.match(q.name):
            raise _fail("图库里只认存好的图（<sha256>.png 这种名字）", "Only stored images (named <sha256>.png and so on) can be read from the image store")
        p = images.store_dir() / q.name
        try:
            return Source(_read_plain(p, max_bytes), p.name, f"stored image {p.name}", "", "store", p)
        except FileNotFoundError:
            raise _fail("这张图已经从图库里清掉了：重新截、重新读", "That image has been cleared from the image store; take or read it again") from None
    if val.startswith("/") and _under(val, settings.uploads):
        row = _attachment(ctx.conversation, path=os.path.normpath(val))
        if row is None:
            raise _fail("这不是这个对话的附件", "That isn't an attachment of this conversation")
        p = Path(row["path"])
        try:
            return Source(_read_plain(p, max_bytes), row["name"], row["name"], str(row["mime"] or ""), "attachment", p)
        except OSError:
            raise _fail(f"附件 {row['name']} 的文件不在了", f"The file of attachment {row['name']} is no longer there") from None
    if not host_files.enabled(agent):
        raise _fail("你没有文件工具，读不了工作区里的文件", "You have no file tools, so workspace files can't be read")
    pl = host_files.place(agent, val)
    from . import safefs
    try:
        data, size = pl.fs.read(pl.parts, max_bytes)
    except (OSError, safefs.Refused) as e:
        raise host_files._fail(pl, e) from None  # noqa: SLF001 — 和 read_file 一样的说法
    if size > len(data):
        raise _fail(f"{pl.rel} 太大了（{human_bytes(size)}，最多 {human_bytes(max_bytes)}）",
                    f"{pl.rel} is too large ({human_bytes(size)}; {human_bytes(max_bytes)} at most)")
    import mimetypes
    return Source(data, pl.parts[-1] if pl.parts else pl.rel, pl.rel, mimetypes.guess_type(pl.parts[-1])[0] or "" if pl.parts else "", "path", pl.path)


def wrap(source: str, text: str) -> str:
    from .tools import wrap_untrusted
    return wrap_untrusted(source, text)


def human_bytes(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"

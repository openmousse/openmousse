"""沙箱（内核第 8 项，2026-10-16）：内核自己起、管、收 Docker 容器，不靠 OpenClaw。Agent 在容器里跑命令、读写文件、开浏览器，
出网只经 Doorman（egress.py 判断、egress_proxy.py 代理）。先给「代办」（errand）用；⑫ 通用的手在这上面给普通 Agent 加命令和浏览器。

谁在沙箱里（confined）：代办一定在；server.json 的 engine.sandbox.agents 还能加别的 Agent（主对话不行）。在沙箱里的 Agent 只有沙箱里的
工具（engine/hands.py），主机上的工具（命令行脚本、记忆检索、定时任务、后台任务……）一个不给：它们不经过沙箱，会绕开 Doorman。
⑫ 起普通 Agent（主对话、用户建的）也各有一个自己的沙箱，和主机上的工具并存（box / hands_of；工具名 sandbox_exec 这些，见
engine/handkit.py）：服务器装好了沙箱（errand.py setup 过、或者写了 engine.sandbox）才给；engine.sandbox.agents.<id> 写 only: false
是普通 Agent 自己沙箱的设置（不写 only 还是第 8 项的意思：只在沙箱里），主对话直接写 main，"*" 是别的 Agent 的默认，false = 不给。
普通 Agent 的 access 多一档 rw（能写它自己的工作区；该盖住的盖住，见 workspace_mounts）。Doorman 按容器的地址认请求是谁的（agent_by_ip）。

容器（按 Agent，一个 Agent 至多两个；同一个 Agent 的几轮共用，草稿区里的文件跨天还在，闲了收掉才没）：
  shell    镜像 mousse-errand（只认 Doorman 的 CA）：sleep infinity 撑着，命令用 docker exec 进去跑
  browser  镜像 mousse-errand-browser（OpenClaw 的沙箱浏览器 + 包了一层的 Chromium，代理写死）：用到浏览器才起（engine/browser.py）
  名字 mousse-sbx-<实例>-<Agent>[-browser]；实例 = 数据目录的指纹：同一台机器上的测试服和正式的互不相认、互不收。
  标签 org.openmousse.sandbox.*：实例、Agent、哪种、配置指纹（设置、镜像变了：下次用到时删了重建）、建的时刻。

加固（照 Hermes 的 tools/environments/docker.py 和 OpenClaw 的 buildSandboxCreateArgs；比现在 OpenClaw 里的代办只严不松）：
  --cap-drop ALL（一个都不加回来：镜像本来就用普通用户 sandbox 跑）、--security-opt no-new-privileges、--read-only 根、--init（收僵尸）、
  tmpfs：/tmp、/var/tmp、/run、/workspace（草稿区；access none 时）、/home/sandbox；内存 = swap 上限（不许换出）、CPU、进程数、core=0、
  --ipc private、日志 local 驱动 1 MB。不发布任何端口；主机的东西一样不挂（access ro 时只读挂这个 Agent 自己的工作区）。
  环境变量走 --env-file（0600，建完就删），不进命令行。
出网（和现在 OpenClaw 里的代办一样）：容器只接在不做 NAT 的网络上（默认 mousse-errand，errand.py setup 建的；主机防火墙
  openmousse-errand-net 让这个网桥哪儿也转发不出去、对主机只开 Doorman 的端口），--dns 127.0.0.1（查不到外面的名字），HTTP(S)_PROXY
  指 Doorman。起容器之前先查一遍（preflight，查不过就不起，fail closed）：Docker 在；网络在、没开 NAT（enable_ip_masquerade=false 或
  internal）、不是 host / bridge / container:；是代办那个网络就要防火墙单元在跑；Doorman 的端口连得上；镜像在。网络写 none = 完全断网
  （不给代理；浏览器起不来）。
生命周期：用到时才起（ensure：没有就建、停了就启、配置指纹对不上就删了重建；正在用的不动，用完再说）；闲了 idle_minutes（默认 120）收掉
  （删容器，草稿区跟着没）；活了 max_hours（默认 72）也收；同时最多 max_containers 个（默认 4，满了先收最久没用的空闲的那个）。
  服务起来时（start）：认回上次留下的（同一个实例、在跑、配置对得上），别的（停了的、对不上的、Agent 已经不在沙箱里的）删掉。
  关服务不动容器：下次起来认回或收掉。
命令（run）：docker exec 进 shell 容器，bash -c 跑；每次带一个记号环境变量 MOUSSE_EXEC=<id>。到点、点了停：在容器里按记号找到这次起的
  所有进程，TERM，1 秒后 KILL（OpenClaw process-cleanup.ts 的做法：只杀 docker exec 的客户端，容器里的进程还在跑）。输出边读边截：
  留头 HEAD 字节、尾 TAIL 字节，中间写明截了多少（几百兆的输出也不会读进内存）。后台跑（start_job）：docker exec -d，输出写进容器里的
  /tmp/.mousse-jobs/<id>.log，hands.py 的 process 工具看、停。
文件（read_file / write_file / edit_file / list_dir）：容器里的 python3 跑一小段（tmpfs 上 docker cp 用不了；OpenClaw 也是这样）。
Doorman 扣下的：命令、浏览器动作做完时查一眼这次扣下了哪些（egress_holds），告诉模型在等用户点头、别重发。

server.json（都可以不写）：
  "engine": {"sandbox": {"network": "mousse-errand", "proxy": "http://172.30.99.1:3128", "image": "…", "browser_image": "…",
             "memory": "1g", "cpus": 1, "pids": 256, "browser_memory": "1g", "browser_pids": 1024, "idle_minutes": 120, "max_hours": 72,
             "max_containers": 4, "firewall": "openmousse-errand-net",
             "agents": {"errand": {"access": "none", "browser": true}, "<别的 Agent>": {"access": "ro", "browser": false}}}}
  access：none（默认：看不到主机上的任何文件，/workspace 是空的草稿区）/ ro（这个 Agent 自己的工作区只读挂到 /workspace，草稿写 /tmp）。
  读写主机工作区（dsh 的 workspace-write、OpenClaw 的 rw）这一档只给普通 Agent（⑫）：只在沙箱里的 Agent 不许改主机上的文件；
  普通 Agent rw 时命令要用户点头（engine/hands.py 的 sandbox_tools），文件工具在主机这边改、能撤。
  ⑫ 的普通 Agent："agents": {"main": {"access": "ro"}, "notes": {"only": false, "access": "rw", "browser": false}, "*": {"browser": true},
  "diet": false}；"hands": false = 普通 Agent 都不给沙箱。
  firewall：网络是代办那个（mousse-errand）时要在跑的 systemd 单元；写 null 不查（别的网络不查，只查不做 NAT）。

参考（10/16 现拉的源码，只读）：Hermes 219c6d2 的 tools/environments/docker.py（_BASE_SECURITY_ARGS / _build_security_args 那几条、
tmpfs 的写法、建失败把半截容器删掉、reap_orphan_containers 只收停了的）、docker_egress.py（代理变量两种大小写、NO_PROXY 只放本机）；
OpenClaw aa834b1 的 src/agents/sandbox/docker.ts（buildSandboxCreateArgs：--init、标签、--env-file、configHash 对不上就重建）、
process-cleanup.ts（按记号环境变量找进程收掉）、prune.ts（闲多久 / 活多久收）、network-mode.ts（host、container: 不许）；
dsh 1517a4d 的 packages/sandbox（只读 / 能写工作区 / 全放开三档，它只管写——我们读和网也管）。Hermes 的几行改写过来用了（标在用到的地方），
出处和许可在仓库根目录的 THIRD_PARTY_NOTICES；OpenClaw、dsh 只借设计。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import socket
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from config import raw, settings
from i18n import L

LABEL = "org.openmousse.sandbox"
EPOCH = "2026-10-16"           # 建容器的参数改了就改这一行：旧容器下次用到时删了重建
PREFIX = "mousse-sbx-"
KINDS = ("shell", "browser")
ACCESS = ("none", "ro")
HAND_ACCESS = ("none", "ro", "rw")   # ⑫：普通 Agent 的沙箱多一档 rw（只在沙箱里的 Agent 还是只有 none / ro）
MARK = "MOUSSE_EXEC"           # 每次命令带的记号环境变量：到点、点了停时按它在容器里找到这次起的进程
WORKDIR, HOME = "/workspace", "/home/sandbox"
JOBS_DIR = "/tmp/.mousse-jobs"
HEAD, TAIL = 8_000, 24_000     # 命令输出留头多少、尾多少字节（出错多在后面）
EXEC_TIMEOUT, EXEC_MAX = 120, 900
JOBS_MAX = 8                   # 一个 Agent 同时最多几个后台命令
FILE_MAX = 2_000_000           # write 一次最多多少字
START_DELAY, REAP_EVERY = 5.0, 60.0
PREFLIGHT_TTL = 30.0           # 查过没问题记多久（每次工具调用都现查太慢）
BAD_NETWORKS = ("host", "bridge", "default")


class SandboxError(Exception):
    """起不来、查不过、跑不了：str(e) 是给模型 / 用户看的一句（按 server.json 的语言）。"""


# —— 设置 ——————————————————————————————————————————————————————————————————

def cfg() -> dict:
    e = raw().get("engine")
    s = e.get("sandbox") if isinstance(e, dict) else None
    return s if isinstance(s, dict) else {}


def defaults() -> dict:
    import errand  # 代办现在的那一套（OpenClaw 那条路也用）：网络、Doorman 的地址、镜像
    return {"network": errand.NETWORK, "proxy": errand.PROXY, "image": errand.IMAGE, "browser_image": errand.BROWSER_IMAGE,
            "memory": "1g", "cpus": 1, "pids": 256, "browser_memory": "1g", "browser_pids": 1024,
            "idle_minutes": 120, "max_hours": 72, "max_containers": 4, "firewall": errand.ROOT_UNIT}


def setting(key: str) -> Any:
    c = cfg()
    return c[key] if key in c else defaults()[key]


def errand_id() -> str:
    import errand
    return errand.AGENT


def agents() -> dict[str, dict]:
    """在沙箱里的 Agent → {access, browser}。代办一定在（写 false 也不放出来：它的规矩、Doorman 的卡都按沙箱写的）；主对话永远不在；
    写了 only: false 的不在（⑫：那一条是普通 Agent 自己沙箱的设置，见 hands_of）。"""
    out: dict[str, dict] = {errand_id(): {"access": "none", "browser": True}}
    a = cfg().get("agents")
    for k, v in (a.items() if isinstance(a, dict) else []):
        k = str(k).strip()
        if not k or k in ("main", "*") or (isinstance(v, dict) and v.get("only") is False and k != errand_id()):
            continue
        base = out.get(k, {"access": "none", "browser": False})
        if isinstance(v, dict):
            out[k] = {**base, **{x: v[x] for x in ("access", "browser") if x in v}}
        elif v is True or k in out:
            out[k] = base
    for v in out.values():
        v["access"] = v.get("access") if v.get("access") in ACCESS else "none"
        v["browser"] = v.get("browser") is True
    return out


def confined(agent: str | None) -> bool:
    return bool(agent) and agent != "main" and agent in agents()


def hands_on() -> bool:
    """普通 Agent 给不给沙箱（⑫）：服务器装好了沙箱（跑过 errand.py setup、有代办）或者 server.json 写了 engine.sandbox；
    engine.sandbox.hands = false 全关。没装的机器上不给（工具一调就报「Docker 不在」只会让模型白试）。"""
    c = cfg()
    if c.get("hands") is False:
        return False
    if c:
        return True
    import errand
    return errand.enabled()


def hands_of(agent: str | None) -> dict | None:
    """普通 Agent 自己的沙箱（⑫）→ {access, browser}；不给 → None。默认 {"access": "none", "browser": true}；
    engine.sandbox.agents 的 "*" 是别的 Agent 的默认，<id> 是它自己的（普通 Agent 写 only: false；主对话直接写），false = 不给。"""
    if not agent or confined(agent) or not hands_on():
        return None
    a = cfg().get("agents")
    a = a if isinstance(a, dict) else {}
    dflt, v = a.get("*"), a.get(agent, a.get("*"))
    if v is False:
        return None
    out: dict[str, Any] = {"access": "none", "browser": True}
    for src in (dflt, v):
        if isinstance(src, dict):
            out.update({x: src[x] for x in ("access", "browser") if x in src})
    out["access"] = out["access"] if out["access"] in HAND_ACCESS else "none"
    out["browser"] = out["browser"] is not False
    return out


def box(agent: str | None) -> dict | None:
    """这个 Agent 有没有沙箱、什么设置：只在沙箱里的 → agents() 那一条；普通的 → hands_of；没有 → None。"""
    if confined(agent):
        return agents()[str(agent)]
    return hands_of(agent)


def agent_by_ip(ip: str) -> str | None:
    """这个地址是哪个 Agent 的容器（Doorman 认请求是谁发的，egress.agent_of）。"""
    if not ip:
        return None
    for live in list(_live.values()):
        if live.ip == ip:
            return live.spec.agent
    return None


def instance() -> str:
    """这个服务的实例号（数据目录的指纹）：容器名字和标签都带上，测试服和正式的互不收。"""
    return hashlib.sha256(str(Path(settings.data_dir).expanduser().resolve()).encode()).hexdigest()[:8]


def slug(agent: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", agent)[:40] or "agent"


def container_name(agent: str, kind: str = "shell") -> str:
    return f"{PREFIX}{instance()}-{slug(agent)}" + ("-browser" if kind == "browser" else "")


def _int(v: Any, lo: int, hi: int, dflt: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return dflt


def idle_seconds() -> float:
    return 60.0 * _int(setting("idle_minutes"), 1, 7 * 24 * 60, 120)


def max_age_seconds() -> float:
    return 3600.0 * _int(setting("max_hours"), 1, 24 * 30, 72)


def max_containers() -> int:
    return _int(setting("max_containers"), 1, 64, 4)


# —— Docker 命令行（和 OpenClaw、Hermes 一样起子进程；测试换成假的：set_client）——————————————————

@dataclass
class Done:
    code: int
    out: str
    err: str


class Docker:
    def __init__(self, exe: str | None = None) -> None:
        self.exe = exe or shutil.which("docker") or "docker"

    async def run(self, args: list[str], *, input: bytes | None = None, timeout: float = 60) -> Done:
        try:
            proc = await asyncio.create_subprocess_exec(self.exe, *args, stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            return Done(127, "", f"{self.exe}: {e.strerror or e}")
        try:
            out, err = await asyncio.wait_for(proc.communicate(input), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            _kill(proc)
            await asyncio.shield(_reap(proc))
            if isinstance(e, asyncio.CancelledError):
                raise
            return Done(124, "", f"docker {args[0] if args else ''} took longer than {timeout:.0f}s")
        return Done(proc.returncode if proc.returncode is not None else -1, out.decode("utf8", "replace"), err.decode("utf8", "replace"))

    async def spawn(self, args: list[str], *, stdin: bool = False):
        """起一个 docker 子进程，标准输出和标准错误合在一起读（命令的输出按先后交给模型）。"""
        return await asyncio.create_subprocess_exec(self.exe, *args, stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)


def _kill(proc) -> None:
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def _reap(proc) -> None:
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except (asyncio.TimeoutError, ProcessLookupError, OSError):
        pass


_client: Docker | None = None


def client() -> Docker:
    global _client
    if _client is None:
        _client = Docker()
    return _client


def set_client(c: Docker | None) -> None:
    """测试用：换一个假的 docker（有同样的 run / spawn）。None = 换回真的。清掉内存里记的容器。"""
    global _client
    _client = c
    reset_memory()


# —— 起之前先查一遍（fail closed）——————————————————————————————————————————————

@dataclass
class Net:
    name: str
    gateway: str = ""
    subnet: str = ""


_checked: dict[tuple, tuple[float, Net, str]] = {}   # (Agent, 哪种) → (查的时刻, 网络, 镜像 id)


def proxy_url() -> str:
    return "" if setting("network") == "none" else str(setting("proxy") or "")


def _bad_network(name: str) -> str | None:
    n = name.strip().lower()
    if n in BAD_NETWORKS or n.startswith("container:"):
        return L(f"网络 {name} 不能用：容器会直接连到外面（或者别的容器的网络），绕开 Doorman",
                 f"Network {name} can't be used: the container would reach the outside directly (or join another container's network), bypassing Doorman")
    return None


async def network(name: str) -> Net:
    if name == "none":
        return Net("none")
    if why := _bad_network(name):
        raise SandboxError(why)
    r = await client().run(["network", "inspect", name, "--format", "{{json .}}"], timeout=20)
    if r.code != 0:
        raise SandboxError(L(f"Docker 网络 {name} 不在：先跑 python3 errand.py setup（或者在 engine.sandbox.network 写一个已经建好的、不做 NAT 的网络）",
                             f"Docker network {name} is missing: run python3 errand.py setup first (or point engine.sandbox.network at an existing network without NAT)"))
    try:
        info = json.loads(r.out)
        info = info[0] if isinstance(info, list) else info
    except (ValueError, IndexError):
        raise SandboxError(L(f"看不懂 Docker 网络 {name} 的设置", f"Couldn't read the settings of Docker network {name}")) from None
    opts = info.get("Options") or {}
    if str(info.get("Driver") or "bridge") != "bridge":
        raise SandboxError(L(f"网络 {name} 不是 bridge：只认不做 NAT 的 bridge 网络", f"Network {name} isn't a bridge network; only bridge networks without NAT are allowed"))
    if not info.get("Internal") and opts.get("com.docker.network.bridge.enable_ip_masquerade") != "false":
        raise SandboxError(L(f"网络 {name} 开着 NAT：容器能绕开 Doorman 直接上网，不起。用 errand.py setup 建的 mousse-errand，或者建一个不做 NAT 的",
                             f"Network {name} does NAT, so a container could go online around Doorman; not starting. Use mousse-errand from errand.py setup, or a network without NAT"))
    cfgs = (info.get("IPAM") or {}).get("Config") or [{}]
    return Net(name, str(cfgs[0].get("Gateway") or ""), str(cfgs[0].get("Subnet") or ""))


async def _firewall_ok(unit: str) -> bool:
    exe = shutil.which("systemctl")
    if not exe:
        return False
    try:
        p = await asyncio.create_subprocess_exec(exe, "is-active", f"{unit}.service", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), 10)
    except (OSError, asyncio.TimeoutError):
        return False
    return out.decode().strip() == "active"


def _proxy_up(url: str, timeout: float = 1.5) -> bool:
    u = urlsplit(url)
    try:
        with socket.create_connection((u.hostname or "", u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


firewall_ok = _firewall_ok   # 测试换掉
proxy_up = _proxy_up


async def image_id(image: str) -> str:
    r = await client().run(["image", "inspect", image, "--format", "{{.Id}}"], timeout=20)
    if r.code != 0:
        import errand
        raise SandboxError(L(f"沙箱镜像 {image} 没建：bash {errand.SANDBOX_DIR}/build.sh {errand.sentinel_dir()}",
                             f"Sandbox image {image} isn't built: bash {errand.SANDBOX_DIR}/build.sh {errand.sentinel_dir()}"))
    return r.out.strip()


async def preflight(agent: str, kind: str) -> tuple[Net, str]:
    """起、用容器之前查一遍 → (网络, 镜像 id)。查不过抛 SandboxError（fail closed）。没问题的结果记 PREFLIGHT_TTL 秒。"""
    key = (agent, kind)
    hit = _checked.get(key)
    if hit and time.monotonic() - hit[0] < PREFLIGHT_TTL:
        return hit[1], hit[2]
    v = await client().run(["version", "--format", "{{.Server.Version}}"], timeout=15)
    if v.code != 0:
        raise SandboxError(L("Docker 不在或者没有权限：沙箱起不来（docker version 报错）", "Docker isn't running or isn't accessible, so the sandbox can't start (docker version failed)"))
    net = await network(str(setting("network")))
    if kind == "browser" and net.name == "none":
        raise SandboxError(L("网络是 none（完全断网）时浏览器开不了：它只能经 Doorman 上网", "The browser can't run with network none: it only goes online through Doorman"))
    import errand
    unit = setting("firewall")
    if net.name == errand.NETWORK and unit and not await firewall_ok(str(unit)):
        raise SandboxError(L(f"主机防火墙规则没开（{unit}.service），沙箱不起：sudo systemctl enable --now {unit}（见 sandbox/errand/README.md）",
                             f"The host firewall rules ({unit}.service) aren't active, so the sandbox won't start: sudo systemctl enable --now {unit} (see sandbox/errand/README.md)"))
    px = proxy_url()
    if px and not await asyncio.to_thread(proxy_up, px):
        raise SandboxError(L(f"Doorman 没在听（{px}）：systemctl --user start openmousse-sentinel，或者看 python3 errand.py status",
                             f"Doorman isn't listening ({px}): systemctl --user start openmousse-sentinel, or check python3 errand.py status"))
    iid = await image_id(str(setting("browser_image" if kind == "browser" else "image")))
    if confined(agent) and agents()[agent]["access"] == "ro":  # 普通 Agent（⑫）没有能挂的工作区就是空的草稿区（mounted）
        ws = workspace_of(agent)
        if ws is None:
            raise SandboxError(L(f"{agent} 没有自己的工作区，access ro 挂不上", f"{agent} has no workspace of its own to mount read-only"))
    _checked[key] = (time.monotonic(), net, iid)
    return net, iid


def workspace_of(agent: str) -> Path | None:
    """access ro / rw 时挂进去的：这个 Agent 自己的工作区（真实路径）。家目录、数据目录这种一律不挂；主工作区只挂给主对话自己（⑫）。"""
    ws = settings.workspace if agent == "main" else settings.agent_workspaces.get(agent)
    if ws is None:
        return None
    try:
        p = ws.expanduser().resolve(strict=True)
    except OSError:
        return None
    home = Path.home().resolve()
    banned = {Path("/"), home, Path(settings.data_dir).resolve(), settings.openclaw_home.resolve()}
    if agent != "main":
        banned.add(settings.workspace.resolve())
    odd = "," in str(p) or "\n" in str(p)  # --mount 的写法用逗号分隔
    return p if p.is_dir() and p not in banned and not odd and not str(p).startswith(("/etc", "/proc", "/sys", "/dev", "/var/run", "/run")) else None


HIDE_SCAN = 20_000  # 挂工作区前最多看多少个条目找要盖住的；看不完 → 不挂（宁可是空的草稿区，也不把没查过的挂进去）
HIDE_MAX = 200      # 最多盖住多少个（每个一条 --mount / --tmpfs）；再多 → 不挂
SCAN_SKIP = frozenset({"node_modules", ".venv", "venv", "__pycache__"})  # 依赖和缓存：挂进去照样看得到，只是不在里面找（第三方的包里
                    # 名字像 *key* 的多半是 object-keys、keyboard 这种，盖了包就坏；用户的密钥不会放在这里）
PLAN_TTL = 30.0     # mounted() 记住一次查的结果多久（会话开头讲手、看状态用；建容器每次都现查）
_plans: dict[tuple[str, str, str], tuple[float, bool]] = {}


def mounted(agent: str | None, *, scan: bool = True) -> str:
    """普通 Agent 的工作区实际怎么挂进沙箱（⑫）：none / ro / rw。没有能挂的工作区、工作区太大查不完（workspace_mounts 不挂）→ none
    （空的草稿区）；rw 却盖不住主机命令放行名单里会跑到的脚本 → ro。会话开头的说法按它（查的结果记 PLAN_TTL 秒）。
    scan=False 不在工作区里找（分类用：rw 的命令要不要问。只会比实际的严：查不完时实际没挂，这里还当能写）。"""
    a = box(agent)
    if a is None or a["access"] == "none" or (ws := workspace_of(str(agent))) is None:
        return "none"
    want = "ro"
    if a["access"] == "rw" and not confined(agent):
        from . import host_exec
        want = "rw" if host_exec.mount_guards(str(agent), ws) is not None else "ro"
    if scan and not confined(agent):
        key = (str(agent), str(ws), want)
        hit = _plans.get(key)
        if hit is None or time.monotonic() - hit[0] > PLAN_TTL:
            hit = _plans[key] = (time.monotonic(), bool(workspace_mounts(str(agent), ws, want, prepare=False)[0]))
        if not hit[1]:
            return "none"
    return want


def _hidden(ws: Path) -> list[tuple[Path, str]] | None:
    """工作区里要盖住的（和主机上的文件工具不碰的一样，host_files.secret_parts）：名字像密钥的文件和文件夹、每个 .git 文件夹里的 config
    （带令牌的远程地址、extraheader 在那；子模块的 .git/modules/…/config 也是）→ [(路径, f / d)]；看不完（HIDE_SCAN）、太多（HIDE_MAX）
    → None（不挂）。盖住的文件夹不往里看；依赖和缓存（SCAN_SKIP）不看；.git 里只找 config（工作区里套着别的仓库也一样）。"""
    from . import host_files, safefs, versions
    root = safefs.Root(ws)
    secret = host_files.secret_matcher()
    out: list[tuple[Path, str]] = []
    gits: list[list[str]] = []
    seen = 0

    def prune(parts: list[str], e) -> bool:  # 名字像密钥的文件夹整个盖住，不用往里看；.git 另外找（下面）
        if e.name == ".git":
            gits.append(parts)
            return True
        return secret(e.name)
    try:
        for parts, e in root.walk([], SCAN_SKIP | {versions.DIR}, prune):
            seen += 1
            if seen > HIDE_SCAN:
                return None
            if e.kind in ("f", "d") and secret(e.name):
                out.append((ws.joinpath(*parts), e.kind))
        for git in gits:  # 是文件夹的 .git（是文件的：工作树、子模块的检出，config 在别处，挂不进来）
            found = [git + ["config"]]
            mods = root.lstat(git + ["modules"])
            if mods is not None and stat.S_ISDIR(mods.st_mode):
                for parts, e in root.walk(git + ["modules"], frozenset({"objects", "refs", "logs", "hooks", "info"})):
                    seen += 1
                    if seen > HIDE_SCAN:
                        return None
                    if e.kind == "f" and e.name == "config":
                        found.append(parts)
            for parts in found:
                st = root.lstat(parts)
                if st is not None and stat.S_ISREG(st.st_mode):
                    out.append((ws.joinpath(*parts), "f"))
    except (OSError, safefs.Refused):
        return None
    return out if len(out) < HIDE_MAX else None


def workspace_mounts(agent: str, ws: Path, access: str, *, prepare: bool = True) -> tuple[list[str], bool]:
    """普通 Agent 的工作区挂到 /workspace（⑫）→ (docker 参数, 能不能写)；不挂 → ([], False)（/workspace 是空的草稿区）。
    盖住（容器里看不到）：像密钥的文件和文件夹、.git 里的 config（挂之前在工作区里找一遍，见 _hidden；找不完、太多 → 不挂）、
    旧版本目录 .mousse-versions。rw 时再把主机命令放行名单里会跑到的脚本（和它们的子文件夹）只读盖上：不然在沙箱里改一下脚本，主机上
    就照着跑了；名单写的是工作区根目录下的通配（<workspace>/*.py）、或者那个文件夹还没有，盖不住 → 整个只读挂。路径里有逗号、换行
    （--mount 写不了）→ 不挂。prepare：先建好 .mousse-versions（盖它要有这个目录；只是查一下的时候不建）。"""
    from . import versions
    writable = access == "rw"
    keep_ro: list[Path] = []
    if writable:
        from . import host_exec
        guards = host_exec.mount_guards(agent, ws)
        if guards is None:
            writable = False
        else:
            keep_ro = guards
    if prepare:
        try:
            (ws / versions.DIR).mkdir(exist_ok=True)
        except OSError:
            pass
    found = _hidden(ws)
    if found is None:
        return [], False
    hide: list[tuple[Path, str]] = [(ws / versions.DIR, "d"), *found]
    if any(("," in str(p) or "\n" in str(p) or ":" in str(p.relative_to(ws))) for p in [*keep_ro, *(h for h, _ in hide)]):
        return [], False
    out = ["--mount", f"type=bind,source={ws},target={WORKDIR}" + ("" if writable else ",readonly")]
    for p in keep_ro:  # 外层先挂、里面的（比如放行的脚本文件夹里的 .env）后挂：docker 按顺序挂
        out += ["--mount", f"type=bind,source={p},target={WORKDIR}/{p.relative_to(ws).as_posix()},readonly"]
    for p, kind in hide:
        t = f"{WORKDIR}/{p.relative_to(ws).as_posix()}"
        out += ["--tmpfs", f"{t}:ro,size=4k,mode=0555"] if kind == "d" else ["--mount", f"type=bind,source=/dev/null,target={t},readonly"]
    return out, writable


# —— 建容器的参数 ————————————————————————————————————————————————————————————

@dataclass
class Spec:
    agent: str
    kind: str
    name: str
    image: str
    args: list[str]            # docker create 的参数（不含名字、环境变量、镜像、命令）
    env: dict[str, str]
    cmd: list[str]
    config: str                # 配置指纹（标签里的那个）


def proxy_env(url: str) -> dict[str, str]:
    """代理变量：两种大小写都给（有的工具只认一种）；NO_PROXY 只放容器自己（浏览器镜像启动时要探自己的 CDP），ALL_PROXY 清空。
    （改写自 Hermes 的 tools/environments/docker_egress.py：_egress_proxy_args_for_docker 的 env_overrides。）"""
    if not url:
        return {}
    local = "localhost,127.0.0.1,::1"
    return {"HTTPS_PROXY": url, "https_proxy": url, "HTTP_PROXY": url, "http_proxy": url, "NO_PROXY": local, "no_proxy": local,
            "ALL_PROXY": "", "all_proxy": ""}


def build(agent: str, kind: str, net: Net, image_ref: str = "") -> Spec:
    """这个 Agent 这一种容器该怎么建（不碰 Docker；普通 Agent 挂工作区时看一眼里面有什么要盖住）。image_ref = 镜像 id，算进配置指纹：
    镜像重建了，容器下次用到时跟着重建。"""
    a = box(agent) or {"access": "none", "browser": False}
    browser = kind == "browser"
    image = str(setting("browser_image" if browser else "image"))
    mem = str(setting("browser_memory" if browser else "memory"))
    pids = _int(setting("browser_pids" if browser else "pids"), 32, 65536, 1024 if browser else 256)
    try:
        cpus = max(0.1, min(64.0, float(setting("cpus"))))
    except (TypeError, ValueError):
        cpus = 1.0
    # 安全选项（改写自 Hermes 的 _BASE_SECURITY_ARGS / _build_security_args：它加回 DAC_OVERRIDE / CHOWN / FOWNER 给包管理器用；
    # 我们的镜像用普通用户跑、不装包，一个都不加回来）
    args = ["--init", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--ipc", "private",
            "--hostname", "sandbox", "--ulimit", "core=0:0", "--stop-timeout", "3",
            "--memory", mem, "--memory-swap", mem, "--cpus", f"{cpus:g}", "--pids-limit", str(pids),
            "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=1", "--log-opt", "compress=false",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--tmpfs", "/var/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--tmpfs", "/run:rw,noexec,nosuid,nodev,size=16m,mode=755"]
    mounts: list[str] = []
    if not browser:
        args += ["--tmpfs", f"{HOME}:rw,nosuid,nodev,size=256m,mode=1777"]
        ws = workspace_of(agent) if a["access"] in ("ro", "rw") else None
        if ws is not None and not confined(agent):  # ⑫：普通 Agent 的工作区（rw 能写；该盖住的盖住）
            mounts, writable = workspace_mounts(agent, ws, a["access"])
            if writable:  # 容器里用服务器这个用户的 uid 写，主机上的文件权限才对得上
                args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        elif a["access"] == "ro" and ws is not None:
            mounts = ["--mount", f"type=bind,source={ws},target={WORKDIR},readonly"]
        if not mounts:
            args += ["--tmpfs", f"{WORKDIR}:rw,nosuid,nodev,size=1g,mode=1777"]
        args += mounts + ["--workdir", WORKDIR]
    if net.name == "none":
        args += ["--network", "none"]
    else:
        args += ["--network", net.name, "--dns", "127.0.0.1"]
    env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8", "TZ": settings.timezone, "MOUSSE_SANDBOX": "1",
           **proxy_env(proxy_url() if net.name != "none" else "")}
    cmd = ["sleep", "infinity"]
    if browser:  # OpenClaw 沙箱浏览器镜像的入口读这些（scripts/sandbox-browser-entrypoint.sh）；CDP 中继要令牌、只认主机在这个网络上的地址
        env.update({"OPENCLAW_BROWSER_HEADLESS": "1", "OPENCLAW_BROWSER_ENABLE_NOVNC": "0", "OPENCLAW_BROWSER_NO_SANDBOX": "1",
                    "OPENCLAW_BROWSER_CDP_PORT": "9222", "OPENCLAW_BROWSER_AUTO_START_TIMEOUT_MS": "30000"})
        if net.gateway:
            env["OPENCLAW_BROWSER_CDP_SOURCE_RANGE"] = f"{net.gateway}/32"
        cmd = []  # 镜像自己的入口（openclaw-sandbox-browser）
    config = hashlib.sha256(json.dumps({"epoch": EPOCH, "kind": kind, "image": image, "image_id": image_ref, "args": args, "env": env, "cmd": cmd},
                                       sort_keys=True).encode()).hexdigest()[:20]
    labels = {LABEL: "1", f"{LABEL}.instance": instance(), f"{LABEL}.agent": agent, f"{LABEL}.kind": kind, f"{LABEL}.config": config}
    largs = [x for k, v in labels.items() for x in ("--label", f"{k}={v}")]
    return Spec(agent, kind, container_name(agent, kind), image, largs + args, env, cmd, config)


# —— 容器：起、认、收 ————————————————————————————————————————————————————————

@dataclass
class Live:
    spec: Spec
    id: str
    ip: str = ""
    token: str = ""            # 浏览器：CDP 中继的令牌
    last: float = field(default_factory=time.monotonic)   # 最后一次用（单调时钟）
    created: float = field(default_factory=time.time)
    busy: int = 0


_live: dict[str, Live] = {}
_locks: dict[str, asyncio.Lock] = {}
_on_remove: list[Callable[[str], None]] = []   # 容器收掉时叫一声（browser.py 断开它的 CDP 连接）
_task: asyncio.Task | None = None


def reset_memory() -> None:
    _live.clear()
    _locks.clear()
    _checked.clear()
    _jobs.clear()


def _lock_of(name: str) -> asyncio.Lock:
    return _locks.setdefault(name, asyncio.Lock())


async def inspect(name: str, net: str | None = None) -> dict | None:
    r = await client().run(["container", "inspect", name, "--format", "{{json .}}"], timeout=20)
    if r.code != 0:
        return None
    try:
        j = json.loads(r.out)
        j = j[0] if isinstance(j, list) else j
    except (ValueError, IndexError):
        return None
    st = j.get("State") or {}
    nets = (j.get("NetworkSettings") or {}).get("Networks") or {}
    n = nets.get(net or "") or (next(iter(nets.values())) if nets else {}) or {}
    env = {}
    for kv in (j.get("Config") or {}).get("Env") or []:
        k, _, v = str(kv).partition("=")
        env[k] = v
    return {"id": str(j.get("Id") or ""), "running": bool(st.get("Running")), "status": str(st.get("Status") or ""),
            "labels": (j.get("Config") or {}).get("Labels") or {}, "ip": str(n.get("IPAddress") or ""), "env": env}


async def remove(name: str) -> None:
    await client().run(["rm", "-f", name], timeout=30)
    _live.pop(name, None)
    for cb in list(_on_remove):
        try:
            cb(name)
        except Exception:  # noqa: BLE001
            pass
    for j in [j for jobs in _jobs.values() for j in jobs.values() if j.container == name]:
        j.gone = True


def _held(name: str) -> bool:
    """正在用、或者正在被 ensure 起 / 换（拿着它的锁）：收拾的时候不动它。"""
    live = _live.get(name)
    return bool(live and live.busy) or _lock_of(name).locked()


async def ours() -> list[dict]:
    """这个实例的全部沙箱容器（含停了的）：[{name, state, agent, kind, config, created}]。"""
    fmt = "\t".join(["{{.Names}}", "{{.State}}"] + [f'{{{{.Label "{LABEL}.{k}"}}}}' for k in ("agent", "kind", "config")])
    r = await client().run(["ps", "-a", "--filter", f"label={LABEL}.instance={instance()}", "--format", fmt], timeout=20)
    if r.code != 0:
        return []
    out = []
    for line in r.out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 5 and parts[0]:
            out.append({"name": parts[0], "state": parts[1].strip().lower(), "agent": parts[2], "kind": parts[3], "config": parts[4]})
    return out


async def make_room(keep: str) -> None:
    """同时最多 max_containers 个：满了先收最久没用、现在没在用的那个；都在用就不起新的。"""
    running = [r for r in await ours() if r["state"] == "running" and r["name"] != keep]
    excess = len(running) - max_containers() + 1
    if excess <= 0:
        return
    idle = sorted((x for x in running if not _held(x["name"])), key=lambda x: _live[x["name"]].last if x["name"] in _live else 0.0)
    if len(idle) < excess:
        raise SandboxError(L(f"已经开着 {len(running)} 个沙箱、都在用：等一会儿再试", f"{len(running)} sandboxes are open and all busy; try again shortly"))
    for x in idle[:excess]:
        await remove(x["name"])


async def _create(spec: Spec, env: dict[str, str]) -> None:
    """docker create + start。环境变量写进 0600 的临时文件（--env-file），建完就删；建失败把半截容器删掉（Hermes 的 _docker_run）。"""
    d = Path(settings.data_dir) / "sandbox"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    fd, path = tempfile.mkstemp(prefix=".env-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            for k, v in env.items():
                if "\n" in v or "\r" in v:
                    raise SandboxError(f"bad value for {k}")
                f.write(f"{k}={v}\n")
        os.chmod(path, 0o600)
        r = await client().run(["create", "--name", spec.name, *spec.args, "--env-file", path, spec.image, *spec.cmd], timeout=120)
    finally:
        Path(path).unlink(missing_ok=True)
    if r.code != 0:
        await client().run(["rm", "-f", spec.name], timeout=30)
        raise SandboxError(L(f"沙箱建不起来：{(r.err or r.out).strip()[:300]}", f"Couldn't create the sandbox: {(r.err or r.out).strip()[:300]}"))
    s = await client().run(["start", spec.name], timeout=60)
    if s.code != 0:
        await client().run(["rm", "-f", spec.name], timeout=30)
        raise SandboxError(L(f"沙箱起不来：{(s.err or s.out).strip()[:300]}", f"Couldn't start the sandbox: {(s.err or s.out).strip()[:300]}"))


async def ensure(agent: str, kind: str = "shell") -> Live:
    """这个 Agent 这一种容器：在跑就用；停了就启；没有、配置对不上就（删了）建。浏览器的 CDP 好没好由 browser.py 等。"""
    a = box(agent)
    if a is None:
        raise SandboxError(L(f"{agent} 没有沙箱", f"{agent} has no sandbox"))
    if kind not in KINDS:
        raise SandboxError(f"unknown sandbox kind {kind!r}")
    if kind == "browser" and not a["browser"]:
        raise SandboxError(L("这个 Agent 的沙箱没开浏览器（engine.sandbox.agents.<id>.browser）", "This Agent's sandbox has no browser (engine.sandbox.agents.<id>.browser)"))
    name = container_name(agent, kind)
    async with _lock_of(name):
        net, iid = await preflight(agent, kind)
        spec = build(agent, kind, net, iid)
        live = _live.get(name)
        st = await inspect(name, net.name)
        if st is not None:
            stale = st["labels"].get(f"{LABEL}.config") != spec.config or st["labels"].get(f"{LABEL}.instance") != instance()
            if stale and not (live and live.busy):  # 设置、镜像变了：删了重建（正在用的先不动，用完下次再换）
                await remove(name)
                st, live = None, None
            elif not st["running"]:
                s = await client().run(["start", name], timeout=60)
                st = await inspect(name, net.name) if s.code == 0 else None
                if st is None or not st["running"]:
                    await remove(name)
                    st, live = None, None
        if st is None:
            await make_room(name)
            env = dict(spec.env)
            if kind == "browser":
                env["OPENCLAW_BROWSER_CDP_AUTH_TOKEN"] = secrets.token_hex(24)
            await _create(spec, env)
            st = await inspect(name, net.name)
            if st is None or not st["running"]:
                await remove(name)
                raise SandboxError(L("沙箱起来又停了（docker logs 看看）", "The sandbox started and then stopped (see docker logs)"))
            live = None
        if live is None or live.id != st["id"]:
            live = Live(spec, st["id"])
        elif not live.busy:
            live.spec = spec
        live.ip, live.token, live.last = st["ip"], st["env"].get("OPENCLAW_BROWSER_CDP_AUTH_TOKEN", ""), time.monotonic()
        _live[name] = live
        return live


class use:
    """async with use(agent, kind) as live：用的这段时间算「在用」（不会被收、不会被挤掉），用完记下时刻。"""

    def __init__(self, agent: str, kind: str = "shell") -> None:
        self.agent, self.kind = agent, kind
        self.live: Live | None = None

    async def __aenter__(self) -> Live:
        self.live = await ensure(self.agent, self.kind)
        self.live.busy += 1
        return self.live

    async def __aexit__(self, *exc) -> None:
        if self.live is not None:
            self.live.busy = max(0, self.live.busy - 1)
            self.live.last = time.monotonic()


async def reset(agent: str) -> int:
    """收掉这个 Agent 的沙箱（草稿区、开着的网页、后台命令都没了）→ 收了几个。下次用到时重新起。"""
    n = 0
    for kind in KINDS:
        name = container_name(agent, kind)
        async with _lock_of(name):
            if await inspect(name) is not None:
                await remove(name)
                n += 1
            _live.pop(name, None)
    _jobs.pop(agent, None)
    return n


async def adopt() -> dict:
    """服务起来时：认回上次留下的（在跑、配置对得上），别的删掉。→ {"adopted": [...], "removed": [...]}。"""
    out: dict[str, list[str]] = {"adopted": [], "removed": []}
    for r in await ours():
        name, agent, kind = r["name"], r["agent"], r["kind"]
        async with _lock_of(name):
            if name in _live:  # 刚起来就有一轮用上了（ensure 已经认过）
                continue
            keep, st, spec = False, None, None
            a = box(agent)
            if r["state"] == "running" and kind in KINDS and a is not None and (kind == "shell" or a["browser"]):
                try:
                    net, iid = await preflight(agent, kind)
                    spec = build(agent, kind, net, iid)
                    st = await inspect(name, net.name)
                    keep = st is not None and st["running"] and r["config"] == spec.config
                except SandboxError:
                    keep = False
            if keep and st is not None and spec is not None:
                _live[name] = Live(spec, st["id"], st["ip"], st["env"].get("OPENCLAW_BROWSER_CDP_AUTH_TOKEN", ""))
                out["adopted"].append(name)
            else:
                await remove(name)
                out["removed"].append(name)
    return out


async def reap() -> list[str]:
    """闲太久、活太久的收掉（正在用的不动）；不认识的（不在内存里的）也收：只有这个服务会建它们。→ 收了哪些。"""
    gone = []
    for r in await ours():
        name = r["name"]
        if _held(name):
            continue
        async with _lock_of(name):
            live = _live.get(name)
            if live is not None and live.busy:
                continue
            now, wall = time.monotonic(), time.time()
            if live is None or r["state"] != "running" or now - live.last > idle_seconds() or wall - live.created > max_age_seconds():
                await remove(name)
                gone.append(name)
    return gone


async def _loop() -> None:
    await asyncio.sleep(START_DELAY)
    try:
        if (await client().run(["version", "--format", "{{.Server.Version}}"], timeout=15)).code != 0:
            return  # 没有 Docker：这台机器上沙箱用不了，什么都不管（用到时 preflight 会说）
        res = await adopt()
        if res["adopted"] or res["removed"]:
            print(f"[engine] 沙箱：认回 {res['adopted'] or '无'}，收掉 {res['removed'] or '无'}")
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 沙箱启动时收拾没成：{type(e).__name__}: {str(e)[:160]}")
    while True:
        await asyncio.sleep(REAP_EVERY)
        if not _live:
            continue
        try:
            gone = await reap()
            if gone:
                print(f"[engine] 沙箱：闲了收掉 {gone}")
        except Exception as e:  # noqa: BLE001 — 这一遍出错了下一遍接着收
            print(f"[engine] 沙箱收拾出错：{type(e).__name__}: {str(e)[:160]}")


def start() -> None:
    """服务启动时（main.py 的 lifespan，claw 是 builtin 时）：认回 / 收掉上次的容器，之后每分钟收一次闲的。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


# —— 命令 ————————————————————————————————————————————————————————————————

class Capture:
    """边读边截：留头 head 字节、尾 tail 字节，中间只记数（不把几百兆的输出读进内存）。"""

    def __init__(self, head: int = HEAD, tail: int = TAIL) -> None:
        self.head_max, self.tail_max = head, tail
        self.head, self.tail = bytearray(), bytearray()
        self.size = 0

    def add(self, b: bytes) -> None:
        self.size += len(b)
        room = self.head_max - len(self.head)
        if room > 0:
            self.head += b[:room]
            b = b[room:]
        if b:
            self.tail += b
            if len(self.tail) > self.tail_max:
                del self.tail[: len(self.tail) - self.tail_max]

    @property
    def cut(self) -> int:
        return self.size - len(self.head) - len(self.tail)

    def text(self) -> str:
        head = self.head.decode("utf8", "replace")
        tail = self.tail.decode("utf8", "replace")
        if self.cut <= 0:
            return (bytes(self.head) + bytes(self.tail)).decode("utf8", "replace")
        return f"{head}\n[… {self.cut} bytes cut from the middle …]\n{tail.lstrip(chr(0xFFFD))}"


@dataclass
class Ran:
    code: int | None
    output: str
    size: int
    cut: int
    ms: int
    timed_out: bool = False
    stopped: bool = False


# 按记号找这次起的所有进程（OpenClaw process-cleanup.ts 的做法：环境变量会传给子进程，docker exec 的客户端死了容器里的进程还在跑）
KILL_SH = r'''m="$1"
own() { for f in /proc/[0-9]*/environ; do tr '\0' '\n' < "$f" 2>/dev/null | grep -qxF "$m" && { p=${f#/proc/}; echo "${p%/environ}"; }; done; }
p=$(own); [ -n "$p" ] || exit 0
kill -TERM $p 2>/dev/null; sleep 1
p=$(own); [ -z "$p" ] || kill -KILL $p 2>/dev/null
exit 0'''


async def kill_marked(name: str, xid: str) -> None:
    await client().run(["exec", name, "/bin/sh", "-c", KILL_SH, "sh", f"{MARK}={xid}"], timeout=15)


def resolve(path: str) -> str:
    """容器里的路径：相对的从 /workspace 起；不许空、不许有 NUL。"""
    p = str(path or "").strip()
    if not p or "\x00" in p:
        raise SandboxError(L("路径是空的", "The path is empty"))
    return posixpath.normpath(p if p.startswith("/") else posixpath.join(WORKDIR, p))


async def run(agent: str, command: str, *, timeout: float = EXEC_TIMEOUT, cwd: str = WORKDIR) -> Ran:
    """在这个 Agent 的 shell 容器里跑一条命令（bash -c）。到点、被取消（用户点了停）：容器里这次起的进程一起收掉。"""
    if not command.strip() or "\x00" in command:
        raise SandboxError(L("命令是空的", "The command is empty"))
    timeout = max(1.0, min(float(timeout), float(EXEC_MAX)))
    cwd = resolve(cwd)
    async with use(agent, "shell") as live:
        xid = uuid.uuid4().hex[:16]
        t0 = time.monotonic()
        cap = Capture()
        proc = await client().spawn(["exec", "-w", cwd, "-e", f"{MARK}={xid}", live.spec.name, "/bin/bash", "-c", command])

        async def pump() -> int:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                cap.add(chunk)
            return await proc.wait()
        task = asyncio.ensure_future(pump())
        timed_out = False
        try:
            code: int | None = await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await kill_marked(live.spec.name, xid)
            _kill(proc)
            code = await _settle(task)
        except asyncio.CancelledError:  # 用户点了停、这一轮被取消：收掉容器里的进程再往外抛
            try:
                await asyncio.shield(kill_marked(live.spec.name, xid))
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            _kill(proc)
            task.cancel()
            raise
        return Ran(code, cap.text(), cap.size, max(0, cap.cut), int((time.monotonic() - t0) * 1000), timed_out=timed_out)


async def _settle(task: asyncio.Task) -> int | None:
    try:
        return await asyncio.wait_for(task, 5)
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
        task.cancel()
        return None


# —— 后台命令 ——————————————————————————————————————————————————————————————

@dataclass
class Job:
    id: str
    command: str
    cwd: str
    container: str             # 容器名字
    container_id: str          # 容器 id：同名的容器收掉又建了，这个后台命令跟着没了
    started: float = field(default_factory=time.time)
    gone: bool = False         # 容器收掉了


_jobs: dict[str, dict[str, Job]] = {}   # Agent → id → Job（只在内存里：服务重启后看不到以前的；容器里的照跑，收容器时一起没）

JOB_SH = f'mkdir -p {JOBS_DIR} && /bin/bash -c "$1" > {JOBS_DIR}/"$2".log 2>&1 < /dev/null; echo $? > {JOBS_DIR}/"$2".exit'
PY_JOB = r'''
import json, os, sys
d, jid, mark, off, cap = sys.argv[1], sys.argv[2], sys.argv[3].encode(), int(sys.argv[4]), int(sys.argv[5])
log, ex = os.path.join(d, jid + ".log"), os.path.join(d, jid + ".exit")
out = {"exists": os.path.exists(log)}
try:
    size = os.path.getsize(log)
except OSError:
    size = 0
out["size"] = size
try:
    with open(ex) as f:
        out["exit"] = int(f.read().strip() or "-1")
except (OSError, ValueError):
    out["exit"] = None
alive = False
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        with open("/proc/%s/environ" % pid, "rb") as f:
            if mark in f.read().split(b"\0"):
                alive = True
                break
    except OSError:
        pass
out["alive"] = alive
if off < 0:
    off = max(0, size - cap)
off = min(off, size)
try:
    with open(log, "rb") as f:
        f.seek(off)
        data = f.read(cap)
except OSError:
    data = b""
out["from"], out["to"] = off, off + len(data)
out["text"] = data.decode("utf-8", "replace")
print(json.dumps(out))
'''


def jobs_of(agent: str) -> dict[str, Job]:
    return _jobs.get(agent, {})


async def start_job(agent: str, command: str, cwd: str = WORKDIR) -> Job:
    if not command.strip() or "\x00" in command:
        raise SandboxError(L("命令是空的", "The command is empty"))
    cwd = resolve(cwd)
    mine = _jobs.setdefault(agent, {})
    if len(mine) >= JOBS_MAX:  # 满了：跑完的、没了的先从名单上去掉
        for jid in list(mine):
            try:
                if not (await job_output(agent, jid, cap=1))["running"]:
                    mine.pop(jid, None)
            except SandboxError:
                mine.pop(jid, None)
    if len(mine) >= JOBS_MAX:
        raise SandboxError(L(f"已经有 {JOBS_MAX} 个后台命令在跑：先用 process 停掉不要的", f"{JOBS_MAX} background commands are running; stop some with process first"))
    async with use(agent, "shell") as live:
        jid = "j" + uuid.uuid4().hex[:7]
        r = await client().run(["exec", "-d", "-w", cwd, "-e", f"{MARK}={jid}", live.spec.name, "/bin/bash", "-c", JOB_SH, "job", command, jid],
                               timeout=30)
        if r.code != 0:
            raise SandboxError(L(f"后台命令起不来：{(r.err or r.out).strip()[:300]}", f"Couldn't start the background command: {(r.err or r.out).strip()[:300]}"))
        job = Job(jid, command, cwd, live.spec.name, live.id)
        _jobs.setdefault(agent, {})[jid] = job
        return job


def _job(agent: str, jid: str) -> Job:
    job = jobs_of(agent).get(jid)
    if job is None:
        raise SandboxError(L(f"没有后台命令 {jid}（process 的 list 看有哪些）", f"No background command {jid} (process list shows them)"))
    live = _live.get(job.container)
    if job.gone or live is None or live.id != job.container_id:
        job.gone = True
        raise SandboxError(L(f"{jid} 所在的沙箱已经收掉了，它跟着没了", f"The sandbox {jid} ran in has been reset, so it's gone"))
    return job


async def job_output(agent: str, jid: str, offset: int = -1, cap: int = HEAD + TAIL) -> dict:
    """后台命令的状态和输出：{"running", "exit", "size", "from", "to", "text"}。offset = -1：最后 cap 字节。
    running = 还有带它记号的进程在；不在了又没写退出码 = 被停掉了（或者崩了）。"""
    job = _job(agent, jid)
    async with use(agent, "shell") as live:
        r = await client().run(["exec", live.spec.name, "python3", "-c", PY_JOB, JOBS_DIR, jid, f"{MARK}={jid}", str(int(offset)), str(int(cap))],
                               timeout=30)
    try:
        out = json.loads(r.out)
    except ValueError:
        raise SandboxError(L(f"读不到 {jid} 的输出：{(r.err or r.out).strip()[:200]}", f"Couldn't read {jid}'s output: {(r.err or r.out).strip()[:200]}")) from None
    out["running"] = bool(out.get("alive")) and out.get("exit") is None
    out["command"] = job.command
    return out


async def kill_job(agent: str, jid: str) -> None:
    job = _job(agent, jid)
    await kill_marked(job.container, jid)


# —— 文件（容器里的 python3 跑一小段）————————————————————————————————————————————

PY_READ = r'''
import base64, json, os, sys
p, off, lim, cap = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
def bye(**k):
    print(json.dumps(k)); sys.exit(0)
try:
    st = os.stat(p)
except OSError as e:
    bye(error=e.strerror or str(e))
if os.path.isdir(p):
    bye(error="is a directory")
with open(p, "rb") as f:
    data = f.read(8 * 1024 * 1024)
kind = ("image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if data[:3] == b"\xff\xd8\xff" else
        "image/gif" if data[:6] in (b"GIF87a", b"GIF89a") else "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "")
if kind:
    if len(data) < st.st_size:
        bye(binary=True, image=kind, size=st.st_size)
    bye(binary=True, image=kind, size=st.st_size, b64=base64.b64encode(data).decode())
if b"\x00" in data[:8192]:
    bye(binary=True, size=st.st_size)
lines = data.decode("utf-8", "replace").splitlines(keepends=True)
start = max(1, off)
chunk, used = [], 0
for i in range(start - 1, min(len(lines), start - 1 + lim)):
    if used + len(lines[i]) > cap and chunk:
        break
    chunk.append(lines[i]); used += len(lines[i])
bye(text="".join(chunk), start=start, end=start + len(chunk) - 1, lines=len(lines), size=st.st_size, partial=len(data) < st.st_size)
'''
PY_WRITE = r'''
import json, os, sys, tempfile
p = sys.argv[1]
data = sys.stdin.buffer.read()
d = os.path.dirname(p) or "."
try:
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.path.exists(p):
        os.chmod(tmp, os.stat(p).st_mode & 0o7777)
    os.replace(tmp, p)
except OSError as e:
    print(json.dumps({"error": e.strerror or str(e)})); sys.exit(0)
print(json.dumps({"bytes": len(data)}))
'''
PY_EDIT = r'''
import json, os, sys, tempfile
p = sys.argv[1]
req = json.loads(sys.stdin.read())
def bye(**k):
    print(json.dumps(k)); sys.exit(0)
try:
    with open(p, encoding="utf-8") as f:
        text = f.read()
except (OSError, UnicodeDecodeError) as e:
    bye(error=getattr(e, "strerror", None) or str(e))
old, new = req["old"], req["new"]
n = text.count(old) if old else 0
if n == 0:
    bye(error="not found", lines=text.count("\n") + 1)
if n > 1 and not req.get("all"):
    bye(error="ambiguous", count=n)
text = text.replace(old, new) if req.get("all") else text.replace(old, new, 1)
fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(p) or ".")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text)
os.chmod(tmp, os.stat(p).st_mode & 0o7777)
os.replace(tmp, p)
bye(replaced=n if req.get("all") else 1)
'''
PY_LS = r'''
import json, os, stat, sys
p, cap = sys.argv[1], int(sys.argv[2])
try:
    names = sorted(os.listdir(p))
except OSError as e:
    print(json.dumps({"error": e.strerror or str(e)})); sys.exit(0)
out = []
for n in names[:cap]:
    try:
        st = os.lstat(os.path.join(p, n))
    except OSError:
        continue
    t = "d" if stat.S_ISDIR(st.st_mode) else "l" if stat.S_ISLNK(st.st_mode) else "f"
    out.append([n, t, st.st_size])
print(json.dumps({"entries": out, "more": max(0, len(names) - cap)}))
'''


async def _py(agent: str, code: str, args: list[str], stdin: bytes | None = None, timeout: float = 60) -> dict:
    async with use(agent, "shell") as live:
        r = await client().run(["exec", *(["-i"] if stdin is not None else []), live.spec.name, "python3", "-c", code, *args],
                               input=stdin, timeout=timeout)
    try:
        out = json.loads(r.out.strip().splitlines()[-1]) if r.out.strip() else None
    except (ValueError, IndexError):
        out = None
    if not isinstance(out, dict):
        raise SandboxError(L(f"沙箱里没跑成：{(r.err or r.out).strip()[:300] or r.code}", f"It didn't run in the sandbox: {(r.err or r.out).strip()[:300] or r.code}"))
    return out


async def read_file(agent: str, path: str, offset: int = 1, limit: int = 400, cap: int = 40_000) -> dict:
    return {"path": resolve(path), **await _py(agent, PY_READ, [resolve(path), str(max(1, int(offset))), str(max(1, min(int(limit), 2000))), str(cap)])}


async def write_file(agent: str, path: str, content: str) -> dict:
    if len(content) > FILE_MAX:
        raise SandboxError(L(f"一次最多写 {FILE_MAX} 字", f"At most {FILE_MAX} characters per write"))
    return {"path": resolve(path), **await _py(agent, PY_WRITE, [resolve(path)], content.encode("utf8"))}


async def edit_file(agent: str, path: str, old: str, new: str, all: bool = False) -> dict:
    payload = json.dumps({"old": old, "new": new, "all": bool(all)}, ensure_ascii=False).encode("utf8")
    return {"path": resolve(path), **await _py(agent, PY_EDIT, [resolve(path)], payload)}


async def list_dir(agent: str, path: str = WORKDIR, cap: int = 300) -> dict:
    return {"path": resolve(path), **await _py(agent, PY_LS, [resolve(path), str(cap)])}


# —— Doorman 扣下的 ————————————————————————————————————————————————————————————

def pending_holds(agent: str) -> list[dict]:
    """Doorman 扣下、还在等用户点头的这个 Agent 的请求（⑫ 起 Doorman 按容器的地址认是谁的，不只代办）。工具在动作前后各看一次，
    多出来的就是这一次扣下的（hands.py）。"""
    try:
        import egress
        if not egress_ready():
            return []
        with egress._lock, egress.edb() as conn:
            rows = conn.execute("SELECT id, method, host, path, inbox_id, reason FROM egress_holds WHERE agent=? AND status='pending' "
                                "ORDER BY created_at", (agent,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 — 查不到只是少一句提示
        return []


def egress_ready() -> bool:
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='egress_holds'").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def holds_note(holds: list[dict]) -> str:
    """给模型的一段：在等用户点头的请求，别重发。"""
    if not holds:
        return ""
    items = "; ".join(f"{h['method']} {h['host']}{str(h['path']).split('?', 1)[0][:60]}" + (f" (card {h['inbox_id']})" if h.get("inbox_id") else "")
                      for h in holds[:5])
    return (f"[Doorman is holding {len(holds)} request(s) for the user's OK: {items}. Don't send it again and don't try another way: "
            "tell the user what it's waiting for. Once they let it through, send the exact same request again within 30 minutes.]")


# —— 给 /api/engine/sandbox 看的 ——————————————————————————————————————————————————

async def status() -> dict:
    rows = await ours()
    now = time.monotonic()
    out = []
    for r in rows:
        live = _live.get(r["name"])
        out.append({"name": r["name"], "agent": r["agent"], "kind": r["kind"], "state": r["state"], "busy": bool(live and live.busy),
                    "idleSeconds": int(now - live.last) if live else None})
    return {"instance": instance(), "network": setting("network"), "proxy": proxy_url() or None, "agents": agents(),
            "hands": {"on": hands_on(), "main": hands_of("main"), "mainMount": mounted("main"), "default": hands_of("*")},
            "idleMinutes": int(idle_seconds() // 60), "maxContainers": max_containers(), "containers": out,
            "jobs": {a: len([j for j in js.values() if not j.gone]) for a, js in _jobs.items()}}

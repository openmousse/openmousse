"""评测的命令行（在 server/ 下跑）。评测有自己的家目录（--home），自己的 server.json 和数据，绝不碰 ~/.openmousse/server.json。

  python -m evals init --home DIR [--gateway http://127.0.0.1:18794]
      写 DIR/server.json：虚构的主人 Mira、zh、Europe/Lisbon，数据放 DIR/data，密钥读 ~/.openclaw/.env，
      engine 里 Kimi / Qwen 用国内的地址、Claude 的密钥读 OPENMOUSSE_ANTHROPIC_KEY；--gateway 给 llm-task 用（指 llmtask-proxy）
  python -m evals estimate --home DIR --arms … [--suites doorman,card] [--reps 1] [--price 引用=入,出]   估 token 和花费，不调模型
  python -m evals offline --home DIR [--suites …]     假模型回放标准答案：内核和 llm-task 两种调法整条路走一遍，不花钱
  python -m evals run --home DIR --arms … [--suites …] [--reps 1] [--max-usd 3] [--max-calls 60] [--max-tokens 400000]
                      [--cases d01,c03] [--tag 名字] [--serial]
      真模型。几路同时跑，同一路里一题一题来；每题写完就落盘，同一个 tag 再跑做过的题跳过。
  python -m evals report --home DIR [--tag 名字]        出表：DIR/runs/<tag>/report.md
  python -m evals rejudge --home DIR [--tag 名字]       判法改了以后按现在的题库重判存下的回答（不调模型），再出表
  python -m evals blind --home DIR [--tag 名字]         名片 Agent 的盲评表：blind-card.md + blind-key.json（评完再看）
arms 的写法：builtin:deepseek/deepseek-flash,builtin:moonshot/kimi-k3,builtin:anthropic/claude-opus-5-5,llm-task
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

LIVE = Path("~/.openmousse/server.json").expanduser().resolve()


def parse(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m evals", description="评测：题库 × 模型，出表（见 evals/harness.py）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "estimate", "offline", "run", "report", "rejudge", "blind"):
        p = sub.add_parser(name)
        p.add_argument("--home", required=True, help="评测的家目录（server.json、数据、结果都在这里）")
        p.add_argument("--suites", default="doorman,card")
        p.add_argument("--tag", default="main", help="结果放 DIR/runs/<tag>/")
        if name == "init":
            p.add_argument("--gateway", help="llm-task 走的 Gateway 地址（llmtask-proxy，比如 http://127.0.0.1:18794）")
            p.add_argument("--owner", default="Mira")
            p.add_argument("--force", action="store_true")
        if name in ("estimate", "run"):
            p.add_argument("--arms", required=True)
            p.add_argument("--reps", type=int, default=1)
        if name == "estimate":
            p.add_argument("--price", action="append", default=[], help="只用来估：引用=每百万输入美元,每百万输出美元（不进 models.PRICES）")
            p.add_argument("--think", type=int, default=400, help="每题想多少 token（估）")
        if name == "run":
            p.add_argument("--max-usd", type=float, default=3.0, help="有价的（models.PRICES）一共最多花多少美元")
            p.add_argument("--max-calls", type=int, default=60, help="每一路最多调几次模型（llm-task 不超过 60）")
            p.add_argument("--max-tokens", type=int, default=400_000, help="每一路最多用多少 token（没有价的模型靠它兜底）")
            p.add_argument("--cases", help="只跑这几道题（逗号分开）")
            p.add_argument("--serial", action="store_true", help="一路跑完再跑下一路（默认几路同时跑）")
    return ap.parse_args(argv)


def write_config(home: Path, a: argparse.Namespace) -> Path:
    cfg_path = home / "server.json"
    if cfg_path.exists() and not a.force:
        sys.exit(f"{cfg_path} already exists (use --force to overwrite)")
    (home / "oc").mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "app_name": "MousseEval", "user_name": a.owner, "language": "zh", "timezone": "Europe/Lisbon",
        "openclaw_home": str(home / "oc"), "workspace": str(home / "oc" / "workspace"), "data_dir": str(home / "data"),
        "dist": str(home / "none"), "env_file": str(Path("~/.openclaw/.env").expanduser()), "openclaw_bin": "/bin/false", "relay": False,
        "engine": {"effort": "low", "providers": {"moonshot": {"url": "https://api.moonshot.cn/v1"},
                                                  "dashscope": {"url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
                                                  "anthropic": {"key_env": "OPENMOUSSE_ANTHROPIC_KEY"}}},
        "card": {"thinking": "low"},
    }
    oc: dict = {}
    if a.gateway:
        cfg["gateway"] = a.gateway.rstrip("/")
        oc = {"gateway": {"port": urlsplit(a.gateway).port or 18789, "auth": {"token": "${OPENCLAW_GATEWAY_TOKEN}"}}}
    (home / "oc" / "openclaw.json").write_text(json.dumps(oc, indent=1) + "\n", encoding="utf8")
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf8")
    os.chmod(cfg_path, 0o600)
    return cfg_path


def main(argv: list[str] | None = None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    home = Path(a.home).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    if a.cmd == "init":
        print(f"wrote {write_config(home, a)}")
        return
    cfg_path = home / "server.json"
    if not cfg_path.exists():
        sys.exit(f"no {cfg_path}: run `python -m evals init --home {a.home}` first")
    if cfg_path.resolve() == LIVE:
        sys.exit("refusing to run against the live ~/.openmousse/server.json")
    if a.cmd == "offline":
        offline(home, cfg_path, a)
        return
    os.environ["MOUSSE_SERVER_CONFIG"] = str(cfg_path)  # config.py 一导入就读它：要在导入 harness 之前设好

    from evals import harness

    suites = load(harness, a)
    run_dir = home / "runs" / a.tag
    rows_path = run_dir / "rows.jsonl"
    say = lambda s: print(s, file=sys.stderr, flush=True)

    if a.cmd == "estimate":
        prices = {}
        for p in a.price:
            ref, _, nums = p.partition("=")
            i, _, o = nums.partition(",")
            prices[ref] = (float(i), float(o))
        rows = harness.estimate(suites, a.arms.split(","), reps=a.reps, think_tokens=a.think, prices=prices)
        for r in rows:
            usd = "—" if r["usd"] is None else f"${r['usd']:.4f}" + ("（按给的价估）" if r["price_from"] == "assumed" else "")
            print(f"{r['arm']:45s} {r['suite']:8s} 调 {r['calls']:3d} 次  入 ~{r['in']:>7,}  出 ~{r['out']:>6,}  {usd}")
        return

    if a.cmd == "run":
        arms = [x.strip() for x in a.arms.split(",") if x.strip()]
        budget = harness.Budget(a.max_usd, a.max_calls, a.max_tokens)
        only = {x.strip() for x in a.cases.split(",")} if a.cases else None
        t0 = time.time()
        rss0 = harness_rss()
        new = asyncio.run(harness.run(suites, arms, rows_path=rows_path, reps=a.reps, budget=budget, only=only, parallel=not a.serial, log=say))
        meta_path = run_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf8")) if meta_path.exists() else {"runs": []}
        meta["runs"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "arms": arms, "suites": [s["name"] for s in suites], "reps": a.reps,
                             "new_rows": len(new), "seconds": round(time.time() - t0), "usd": round(budget.usd, 4),
                             "rss_mb_start": rss0, "rss_mb_peak": harness_rss("VmHWM")})
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf8")
        say(f"done: {len(new)} new rows, ${budget.usd:.4f} priced spend; report: python -m evals report --home {a.home} --tag {a.tag}")
        return

    rows = harness.read_rows(rows_path)
    if a.cmd == "rejudge":
        n = harness.rejudge(rows, suites)
        rows_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf8")
        say(f"re-judged {len(rows)} rows, {n} changed")
    if a.cmd in ("report", "rejudge"):
        md = harness.report(rows, suites)
        (run_dir / "report.md").write_text(md, encoding="utf8")
        print(md)
        return
    if a.cmd == "blind":
        card = next((s for s in suites if s["target"] == "card"), None)
        if card is None:
            sys.exit("the blind sheet is for the card suite")
        md, key = harness.blind(rows, card)
        (run_dir / "blind-card.md").write_text(md, encoding="utf8")
        (run_dir / "blind-key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf8")
        print(f"wrote {run_dir / 'blind-card.md'} and blind-key.json")


def load(harness, a: argparse.Namespace) -> list[dict]:
    suites = [harness.load_suite(n.strip()) for n in a.suites.split(",") if n.strip()]
    owner = harness.settings.user_name
    for s in suites:
        if s.get("owner") and s["owner"] != owner:
            sys.exit(f"suite {s['name']} is written for owner {s['owner']!r}, but server.json has user_name {owner!r}")
    return suites


def offline(home: Path, cfg_path: Path, a: argparse.Namespace) -> None:
    """假模型回放标准答案：先起假模型、写一份指向它的配置（模型 fake/gold、Gateway 也是它），再导入 harness 跑两路。"""
    from evals.fakemodel import FakeModelServer, Reply
    gold: dict = {}
    targets: dict = {}

    def respond(rec: dict) -> Reply:
        inp = rec.get("input") or {}
        k = targets["writeback" if "rules" in inp else "doorman" if "draft" in inp else "card"].key_of_input(inp)
        return Reply(text=json.dumps(gold[k], ensure_ascii=False))

    with FakeModelServer(respond) as fm:
        data = json.loads(cfg_path.read_text(encoding="utf8"))
        data.setdefault("engine", {}).setdefault("providers", {})["fake"] = {"api": "openai", "url": fm.openai_url, "key_env": ""}
        data["gateway"] = fm.url
        data["openclaw_home"] = str(home / "offline-oc")
        (home / "offline-oc").mkdir(exist_ok=True)
        (home / "offline-oc" / "openclaw.json").write_text(json.dumps({"gateway": {"auth": {"token": "offline"}}}), encoding="utf8")
        offline_cfg = home / "server.offline.json"
        offline_cfg.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
        os.environ["MOUSSE_SERVER_CONFIG"] = str(offline_cfg)

        from evals import harness

        suites = load(harness, a)
        targets.update(harness.TARGETS)
        for s in suites:
            for c in s["cases"]:
                gold[harness.target_of(s).key_of_case(c)] = c["gold"]
        rows_path = home / "runs" / "offline" / "rows.jsonl"
        rows_path.unlink(missing_ok=True)
        rows = asyncio.run(harness.run(suites, ["builtin:fake/gold", "llm-task"], rows_path=rows_path,
                                       log=lambda s: print(s, file=sys.stderr, flush=True)))
    print(harness.report(rows, suites))
    sys.exit(1 if any(not r["passed"] for r in rows) else 0)


def harness_rss(field: str = "VmRSS") -> int | None:
    """这个进程的内存（MB）：VmRSS 现在、VmHWM 峰值。"""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


if __name__ == "__main__":
    main()

"""手册按句搜的小评测（内核第 13c 项，2026-10-19）：三份编的手册（写文书、记录训练、准备会议）加上 core 的技能，几十句编的话，比
「全放开头」和「只带命中的」：命中得对不对、开头省了多少字。全部离线：不调模型、不连网，世界树和配置都在临时目录，人和事都是编的。

  cd server && python -m evals.manualbench [--grid] [--verbose] [--json]

量什么：
  命中   每一句照 engine/manuals.py 每轮开头那一套挑（rank + picks；不调模型改写；带上一句的题带上一句）：
           对     挑出来的都是该挑的（want，或者挑了也不算错的 ok），want 里至少挑中一份；闲聊（want 空）一份都不挑
           错挑   挑了不该挑的（不在 want、ok 里）
           漏     want 里一份都没挑中
  字数   全放开头：以前那样技能目录带说明（engine.manuals = false），手册的正文也常驻在开头（规矩写在 CLAUDE.md 里那种）；
         只带命中：常驻的目录只留名字（默认 names）或不留（none），这一句命中的正文接在这句话后面（每句平均）
  --grid：min_score × GAP 几种取法各跑一遍（定默认值用）；--verbose：每一句挑了什么、分是多少
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BOOT = Path(tempfile.mkdtemp(prefix="manualbench-"))
(BOOT / "ws").mkdir()
(BOOT / "server.json").write_text(json.dumps({
    "app_name": "Bench", "timezone": "UTC", "language": "zh", "openclaw_home": str(BOOT / "openclaw"), "data_dir": str(BOOT / "data"),
    "workspace": str(BOOT / "ws"), "openclaw_bin": "/bin/false", "claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"},
}), encoding="utf8")
os.environ["MOUSSE_SERVER_CONFIG"] = str(BOOT / "server.json")
os.environ["MOUSSE_TREE_HOME"] = str(BOOT / "mousse-tree")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MANUALS = [
    {"name": "写文书", "when": ["丰富文书素材", "改 SOP", "准备推荐信材料"],
     "about": "申请文书（SOP、个人陈述、推荐信材料）怎么改、素材怎么攒",
     "body": ("1. 先问清楚这一版给哪个项目、字数上限、截止日期；没说就问，别猜。\n"
              "2. 素材先从世界树的「申请」枝找（经历、数字、导师说过的话），找不到的地方标【待补】，不编。\n"
              "3. 改 SOP：每段只讲一件事；开头用一个具体的场景，不用名言；每个经历写清楚「我做了什么、结果是什么、学到什么」。\n"
              "4. 推荐信材料：给推荐人一页纸——我们一起做过的事（时间、项目、我的角色）、希望他强调的两三点、截止日期和提交方式。\n"
              "5. 改完给一份对照：改了哪几处、为什么改；语气保持第一人称、平实，不要堆形容词。\n"
              "6. 定稿前通读一遍查：项目名、学校名、数字前后一致；超字数先删形容词再删例子。\n"
              "7. 每一版存成新的一份（v1、v2……），不覆盖旧的。")},
    {"name": "记录训练", "when": ["记今天的训练", "练完记一下", "跑步记录", "log a workout"],
     "about": "训练记录的格式：动作、组数 × 次数、重量、配速、感觉",
     "body": ("1. 一次训练记一条：日期、类型（力量 / 跑步 / 游泳）、总时长。\n"
              "2. 力量：每个动作一行「动作 组数×次数 @重量」，比如「深蹲 5×5 @80kg」；最后一组的 RPE 写在后面。\n"
              "3. 跑步：距离、用时、平均配速（分:秒/公里），心率有就写。\n"
              "4. 感觉：一句话，哪里酸、哪里疼、睡得怎么样；疼的要单独记进日志，方便以后找。\n"
              "5. 和上一次同样的动作比一比：重量、次数涨了就说一句，掉了也说，不评判。\n"
              "6. 记完回一句「记好了」加上今天的要点，别长篇大论。")},
    {"name": "准备会议", "when": ["准备开会", "会前整理议题", "约导师谈话", "prep for a meeting"],
     "about": "会前一页纸：这次会的目的、要问的三件事、要带的材料、会后跟进",
     "body": ("1. 先问：跟谁开、多久、这次最想得到什么（一个决定？一个意见？）。\n"
              "2. 写一页纸：目的一句话；要问的事最多三件，按重要排；每件写清楚背景和我现在的想法。\n"
              "3. 要带的材料：上次会的结论、这期间的进展（数字、图）、卡住的地方。\n"
              "4. 时间分配：前 2 分钟讲进展，中间讨论，最后 3 分钟确认下一步和截止。\n"
              "5. 会后半小时内把结论和下一步记下来，有截止的问要不要加进日程。")},
]

# 题：(这句话, 上一句, want：该挑的, ok：挑了也不算错的)。名字前面带类别：manual:手册名 / skill:技能名
CASES = [
    ("帮我把 SOP 再改一版", "", {"manual:写文书"}, set()),
    ("推荐信的材料帮我理一下，下周要交给推荐人", "", {"manual:写文书"}, set()),
    ("我的个人陈述开头太平了，帮我改改", "", {"manual:写文书"}, set()),
    ("Can you polish the intro of my SOP?", "", {"manual:写文书"}, set()),
    ("再来一版，语气更自信一点", "帮我改一下 SOP 的第二段", {"manual:写文书"}, set()),
    ("今天练了深蹲 5x5 80 公斤，帮我记一下", "", {"manual:记录训练"}, {"skill:journal"}),
    ("刚跑完 5 公里，用了 27 分钟，记录一下", "", {"manual:记录训练"}, {"skill:journal"}),
    ("Log today's workout: bench press 3x8 at 60kg", "", {"manual:记录训练"}, {"skill:journal"}),
    ("练完了，腿有点酸，记一下今天的训练", "", {"manual:记录训练"}, {"skill:journal"}),
    ("明天下午跟导师开会，帮我准备一下", "", {"manual:准备会议"}, set()),
    ("周会之前帮我把要问的事列一下", "", {"manual:准备会议"}, set()),
    ("Prep me for tomorrow's meeting with my advisor", "", {"manual:准备会议"}, set()),
    ("把这个想法记到日志里：我觉得早上学习效率更高", "", {"skill:journal"}, set()),
    ("帮我建一个管读书的 Agent", "", {"skill:agent-builder"}, set()),
    ("把「体重降到 65 kg 以下」这个目标标成完成", "", {"skill:goals"}, set()),
    ("做个页面，把这个月的花费画成图", "", {"skill:page"}, {"skill:board"}),
    ("这件事交给健身 Agent 去管吧", "", {"skill:handoff"}, set()),
    ("这份长报告太长了，派到后台去读，读完给我要点", "", {"skill:dispatch"}, set()),
    ("世界树这周帮我整理一下", "", {"skill:tree-prune"}, {"skill:memory-tree"}),
    ("我第一次用，带我走一遍。", "", {"skill:onboarding"}, set()),
    ("加一门课：线性代数，每周二四上午", "", {"skill:study"}, set()),
    ("你好呀", "", set(), set()),
    ("今天天气怎么样？", "", set(), set()),
    ("讲个笑话吧", "", set(), set()),
    ("What's 15% of 80?", "", set(), set()),
    ("谢谢，挺好的", "", set(), set()),
    ("晚饭吃什么好？", "", set(), set()),
    ("「我很期待」英文怎么说？", "", set(), set()),
]

from engine import context, manuals  # noqa: E402 — 要在上面设好环境变量之后再导入


def setup() -> set[str]:
    """临时目录里 init 一棵 SQLite 的世界树，写进三份手册 → 这台「服务器」给主对话的工具名（core 的技能都能列出来）。"""
    import contextlib

    from openmousse_tree import config as C
    from openmousse_tree import manuals as MA
    from openmousse_tree import store as S

    import bridges
    import sources
    import treelib
    C.save({**C.DEFAULTS, "language": "zh", "timezone": "UTC", "profile_path": str(BOOT / "USER.md"), "export_path": str(BOOT / "TREE.md"),
            "tokens": {}, "ui_token": "bench"})
    with contextlib.closing(S.connect()) as conn:
        for m in MANUALS:
            MA.add(conn, **m)
    sources.memory_tree = treelib  # 导入 sources 时还没 init：这里指过去（和装好以后重启一样）
    return set(bridges.BRIDGES) | {"tasks"}


def picked(text: str, prev: str, entries: list[dict], *, floor: float, gap: float, n: int) -> tuple[list[str], list[dict]]:
    found = manuals.rank(text, entries, prev=prev)
    old = manuals.GAP
    manuals.GAP = gap
    try:
        hits = manuals.picks(found, max_n=n, min_score=floor)
    finally:
        manuals.GAP = old
    return [f"{x['entry']['kind']}:{x['entry']['name']}" for x in hits], found["ranked"][:4]


def judge(got: list[str], want: set[str], ok: set[str]) -> str:
    wrong = [g for g in got if g not in want and g not in ok]
    if wrong:
        return "wrong"
    if want and not set(got) & want:
        return "miss"
    return "right"


def run_once(entries: list[dict], *, floor: float, gap: float, n: int, verbose: bool = False) -> dict:
    tally = {"right": 0, "wrong": 0, "miss": 0}
    chit = {"right": 0, "wrong": 0, "miss": 0}
    idle = {"right": 0, "wrong": 0}
    note_chars: list[int] = []
    rows = []
    for text, prev, want, ok in CASES:
        got, top = picked(text, prev, entries, floor=floor, gap=gap, n=n)
        v = judge(got, want, ok)
        tally[v] += 1
        (chit if want else idle)[v] += 1
        blocks = [manuals.heading(e) + "\n\n" + manuals.clip_body(manuals.body_of(e).strip(), manuals.DEFAULTS["max_chars"], manuals.read_how(e))
                  for g in got for e in entries if f"{e['kind']}:{e['name']}" == g]
        note_chars.append(len(manuals.note_text(blocks)) if blocks else 0)
        rows.append({"text": text, "prev": prev, "want": sorted(want), "got": got, "verdict": v,
                     "top": [(f"{x['entry']['kind']}:{x['entry']['name']}", x["score"]) for x in top]})
        if verbose:
            print(f"[{v:5}] {text[:36]:<36} → {', '.join(got) or '—':<28} top: "
                  + ", ".join(f"{k} {s:.2f}" for k, s in rows[-1]["top"][:3]))
    return {"floor": floor, "gap": gap, "max": n, "tally": tally, "hit_cases": chit, "chat_cases": idle,
            "note_avg": round(sum(note_chars) / len(note_chars)), "note_max": max(note_chars), "rows": rows}


def opening(offered: set[str]) -> dict[str, int]:
    """常驻开头里技能和手册占多少字：full（以前那样，加上手册正文常驻）、names、none。"""
    import config
    out = {}
    bodies = sum(len(manuals.heading(e) + "\n\n" + manuals.body_of(e)) + 2 for e in manuals.catalog("main", offered, kinds=("manual",)))
    for mode in ("full", "names", "none"):
        config._cache = None
        cfg = json.loads((BOOT / "server.json").read_text(encoding="utf8"))
        cfg["engine"]["manuals"] = False if mode == "full" else {"catalog": mode}
        (BOOT / "server.json").write_text(json.dumps(cfg), encoding="utf8")
        config._cache = None
        out[mode] = len(context.skills_block(manuals.catalog_text("main", offered)))
    out["manual_bodies"] = bodies
    cfg["engine"].pop("manuals", None)
    (BOOT / "server.json").write_text(json.dumps(cfg), encoding="utf8")
    config._cache = None
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.manualbench", description="手册按句搜：命中对不对、开头省多少字")
    ap.add_argument("--grid", action="store_true", help="min_score × GAP 几种取法各跑一遍")
    ap.add_argument("--verbose", action="store_true", help="每一句挑了什么")
    ap.add_argument("--json", action="store_true", help="结果出成 JSON")
    a = ap.parse_args(argv)
    try:
        offered = setup()
        entries = manuals.catalog("main", offered)
        d = manuals.DEFAULTS
        base = run_once(entries, floor=d["min_score"], gap=manuals.GAP, n=d["max"], verbose=a.verbose)
        sizes = opening(offered)
        grid = []
        if a.grid:
            for floor in (0.35, 0.4, 0.45, 0.5, 0.55, 0.6):
                for gap in (0.7, 0.8, 0.9):
                    r = run_once(entries, floor=floor, gap=gap, n=d["max"])
                    grid.append({k: r[k] for k in ("floor", "gap", "tally", "hit_cases", "chat_cases", "note_avg")})
        full = sizes["full"] + sizes["manual_bodies"]
        out = {"cases": len(CASES), "entries": {k: sum(1 for e in entries if e["kind"] == k) for k in ("manual", "skill", "guide")},
               "default": {k: base[k] for k in ("floor", "gap", "max", "tally", "hit_cases", "chat_cases", "note_avg", "note_max")},
               "opening": {"full_with_manuals": full, **sizes,
                           "names_plus_hits_avg": sizes["names"] + base["note_avg"], "none_plus_hits_avg": sizes["none"] + base["note_avg"]},
               "grid": grid}
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return
        t, h, c = base["tally"], base["hit_cases"], base["chat_cases"]
        print(f"{len(CASES)} sentences, {out['entries']['manual']} manuals + {out['entries']['skill']} skills; "
              f"min_score {base['floor']}, GAP {base['gap']}, max {base['max']}")
        print(f"right {t['right']}/{len(CASES)} · wrong pick {t['wrong']} · missed {t['miss']}  "
              f"(should hit: {h['right']} right / {h['wrong']} wrong / {h['miss']} missed; small talk: {c['right']} quiet / {c['wrong']} picked)")
        print(f"opening, everything resident: {full} chars (old skill list with descriptions {sizes['full']} + manual bodies "
              f"{sizes['manual_bodies']})")
        print(f"opening, names only: {sizes['names']} chars (+ hits after the message, avg {base['note_avg']}, max {base['note_max']})"
              f" → avg {out['opening']['names_plus_hits_avg']} chars per turn")
        print(f"opening, no catalog: {sizes['none']} chars → avg {out['opening']['none_plus_hits_avg']} chars per turn")
        for g in grid:
            print(f"  min_score {g['floor']:.2f} GAP {g['gap']:.1f}: right {g['tally']['right']} wrong {g['tally']['wrong']} "
                  f"missed {g['tally']['miss']} · small talk picked {g['chat_cases']['wrong']} · hits avg {g['note_avg']} chars")
    finally:
        shutil.rmtree(BOOT, ignore_errors=True)


if __name__ == "__main__":
    main()

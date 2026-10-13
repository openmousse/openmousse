#!/usr/bin/env python3
"""饮食记录：Agent 在命令行里看、查、记。走服务的 HTTP 接口（/api/diet/*）。

看
  python3 diet_ctl.py day [--date 2026-10-12] [--json]          # 这一天：每餐吃了什么、合计、目标、还差多少（每一行带 id）
  python3 diet_ctl.py days [--from …] [--to …]                  # 逐天合计（默认近 7 天）
  python3 diet_ctl.py library [--days 30] [--json]              # 常吃的：近 N 天记过的，次数、常用份量、每 100 g 营养、食物库 id
  python3 diet_ctl.py targets                                   # 现在的营养目标和改过的历史

查营养（顺序：个人食物库 → 包装食品查 Open Food Facts → CoFID → USDA；查到一个来源就停，--all 都查）
  python3 diet_ctl.py lookup "希腊酸奶" --en "greek yogurt plain"     # en = 英文说法（CoFID、USDA 只有英文）
  python3 diet_ctl.py lookup "M&S 熟大虾" --en "M&S cooked king prawns" [--packaged]
  python3 diet_ctl.py lookup --barcode 5000169107072
  python3 diet_ctl.py lookup "鸡蛋" --en "eggs chicken whole raw" --source cofid   # 只查某个来源（对照用）
  python3 diet_ctl.py foods [--q 鸡蛋]                          # 个人食物库

存进食物库
  python3 diet_ctl.py food-save --from off:5000169107072 [--name "M&S 熟大虾"] [--alias 大虾] [--portion "1 盒=140"]
  python3 diet_ctl.py food-save --from cofid:14-318 --name 香蕉 [--portion "1 根=120"]
  python3 diet_ctl.py food-add --name "Pret 鸡肉卷" --per100 230,14.2,24.5,8.1 [--basis g|ml] [--source label|manual|estimate] [--note "包装标签"]
        [--brand Pret] [--portion "1 个=180"] [--alias …]          # per100 = kcal,蛋白质,碳水,脂肪（每 100 g / ml）
  python3 diet_ctl.py food-update <id> [--name …] [--per100 …] [--portion "1 个（去壳）=65"]… [--alias …]…   # --portion 整组替换
  python3 diet_ctl.py food-rm <id>

记（默认只预览：算好营养给你看，不写；加 --commit 才写进去。写完输出里有批号，能撤销）
  python3 diet_ctl.py add --meal 晚餐 [--date …] [--time 19:30] [--source chat|photo|manual] \\
        --item "f-1a2b3c4d=150" --item "鸡蛋:1 个(大)×2" --item "外卖羊肉（估算）=150g@450,38,0,33" [--commit]
     --item 的写法：「食物库 id 或名字=克数[g|ml]」；「名字:份量名×个数」（份量名是食物库里记过的，比如 1 个(大)）；
     「名字=克数@kcal,蛋白质,碳水,脂肪」= 这一样的合计（拍照估的、外卖），名字里带「估算」或加 --estimated 就记成估算
  python3 diet_ctl.py add --json 文件或- [--commit]            # 原样的请求体：{meal, date, time, source, items: [{food | name, amount, unit, portion, count, per100, kcal…}]}
  python3 diet_ctl.py log-plan [--card <卡片 id>] [--meal 晚餐 | --meal 0]… [--scale 0.8] [--set 0.1=100]… [--date …] [--commit]
     # 按饮食建议卡记：不给 --card = 那天最新一张，不给 --meal = 第一餐还没记的；--set 餐.样=克数 改份量（0 = 这样没吃）
  python3 diet_ctl.py edit <记录 id> [--amount 120] [--meal 午餐] [--time 13:00] [--date …]
  python3 diet_ctl.py rm <记录 id>
  python3 diet_ctl.py undo <批号>

营养目标（可以分训练日 / 休息日：碳循环）
  python3 diet_ctl.py targets set --protein 136 --carb 240 --fat 80 [--kcal 2224] [--from 2026-10-13] [--note …]
  python3 diet_ctl.py targets set --day-type train --protein 136 --carb 280 --fat 60 --kcal 2200 [--from …]   # 只改训练日那一套
  python3 diet_ctl.py day-type train|rest|auto [--date …]   # 标这一天是训练日 / 休息日；auto = 去掉标记（日程里排了训练算训练日，否则休息日）

不给 --by：当前目录在哪个 Agent 的工作区里就算它记的，否则是 main。服务地址按 server.json 的 bind（MOUSSE_SERVER_CONFIG 换一份就指向别的服务，测试用）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

from i18n import L
from inbox_ctl import call, guess_source

MACROS = ("kcal", "protein", "carb", "fat")


def description() -> str:
    return L(__doc__, """Meal log: read, look up and log from the command line. Uses the server's HTTP API (/api/diet/*).

Read
  python3 diet_ctl.py day [--date 2026-10-12] [--json]          # one day: what was eaten per meal, totals, targets, what's left (each row has an id)
  python3 diet_ctl.py days [--from …] [--to …]                  # daily totals (default: last 7 days)
  python3 diet_ctl.py library [--days 30] [--json]              # usual foods: logged in the last N days, times, usual amount, per 100 g, food id
  python3 diet_ctl.py targets                                   # current nutrition targets and their history

Look up nutrition (order: your food library → packaged foods on Open Food Facts → CoFID → USDA; stops at the first source with results, --all queries all)
  python3 diet_ctl.py lookup "greek yogurt" --en "greek yogurt plain"     # en = the English name (CoFID and USDA are English-only)
  python3 diet_ctl.py lookup "M&S cooked king prawns" [--packaged]
  python3 diet_ctl.py lookup --barcode 5000169107072
  python3 diet_ctl.py lookup "egg" --en "eggs chicken whole raw" --source cofid   # only one source (for comparison)
  python3 diet_ctl.py foods [--q egg]                           # your food library

Save to the food library
  python3 diet_ctl.py food-save --from off:5000169107072 [--name …] [--alias …] [--portion "1 pack=140"]
  python3 diet_ctl.py food-save --from cofid:14-318 --name Banana [--portion "1 banana=120"]
  python3 diet_ctl.py food-add --name "Chicken wrap" --per100 230,14.2,24.5,8.1 [--basis g|ml] [--source label|manual|estimate] [--note "pack label"]
        [--brand Pret] [--portion "1 wrap=180"] [--alias …]           # per100 = kcal,protein,carb,fat (per 100 g / ml)
  python3 diet_ctl.py food-update <id> [--name …] [--per100 …] [--portion "1 egg (shelled)=65"]… [--alias …]…   # --portion replaces the whole list
  python3 diet_ctl.py food-rm <id>

Log (preview by default: nutrition worked out and shown, nothing written; add --commit to write. The output has a batch id for undo)
  python3 diet_ctl.py add --meal dinner [--date …] [--time 19:30] [--source chat|photo|manual] \\
        --item "f-1a2b3c4d=150" --item "Egg:1 large×2" --item "Takeaway lamb (estimate)=150g@450,38,0,33" [--commit]
     --item forms: "food id or name=grams[g|ml]"; "name:portion×count" (a portion saved in the food library);
     "name=grams@kcal,protein,carb,fat" = totals for this item (photo estimates, takeaways); "estimate" in the name or --estimated marks it estimated
  python3 diet_ctl.py add --json FILE|- [--commit]              # the raw request body: {meal, date, time, source, items: [{food | name, amount, unit, portion, count, per100, kcal…}]}
  python3 diet_ctl.py log-plan [--card <card id>] [--meal dinner | --meal 0]… [--scale 0.8] [--set 0.1=100]… [--date …] [--commit]
     # log from a meal plan card: no --card = that day's latest card, no --meal = the first meal not logged yet; --set meal.item=grams (0 = skipped)
  python3 diet_ctl.py edit <entry id> [--amount 120] [--meal lunch] [--time 13:00] [--date …]
  python3 diet_ctl.py rm <entry id>
  python3 diet_ctl.py undo <batch id>

Targets (training-day / rest-day sets for carb cycling are optional)
  python3 diet_ctl.py targets set --protein 136 --carb 240 --fat 80 [--kcal 2224] [--from 2026-10-13] [--note …]
  python3 diet_ctl.py targets set --day-type train --protein 136 --carb 280 --fat 60 --kcal 2200 [--from …]   # only the training-day set
  python3 diet_ctl.py day-type train|rest|auto [--date …]   # mark a day as a training or rest day; auto = remove the mark (a training session in the schedule makes it a training day, otherwise rest)

Without --by: the Agent whose workspace contains the current directory, otherwise main. The server address comes from bind in server.json
(point MOUSSE_SERVER_CONFIG at another server.json to use another server, e.g. for tests).
""")


def q(params: dict) -> str:
    p = {k: v for k, v in params.items() if v not in (None, "", False)}
    return ("?" + urllib.parse.urlencode(p)) if p else ""


def n(x, nd: int = 1) -> str:
    if x is None:
        return "–"
    v = round(float(x), nd)
    return str(int(v)) if v == int(v) else f"{v:.{nd}f}".rstrip("0").rstrip(".")


def macro_line(d: dict) -> str:
    return L(f"{n(d.get('kcal'), 0)} kcal · 蛋白质 {n(d.get('protein'))} g · 碳水 {n(d.get('carb'))} g · 脂肪 {n(d.get('fat'))} g",
             f"{n(d.get('kcal'), 0)} kcal · protein {n(d.get('protein'))} g · carbs {n(d.get('carb'))} g · fat {n(d.get('fat'))} g")


def show_json(d) -> None:
    print(json.dumps(d, ensure_ascii=False, indent=1))


def cmd_day(a) -> None:
    d = call("GET", "/api/diet/day" + q({"date": a.date}))
    if a.json:
        return show_json(d)
    ents = call("GET", "/api/diet/entries" + q({"date": d.get("date")}))["entries"]
    by_meal: dict[str, list] = {}
    for e in ents:
        by_meal.setdefault(e["meal"], []).append(e)
    print(f"{d['date']} · {d.get('source')}")
    for m in d.get("meals") or []:
        print(f"\n{m['label']}{(' ' + m['time']) if m.get('time') else ''}：{macro_line(m)}")
        for e in by_meal.get(m.get("key"), []):
            flag = L("（估算）", " (estimate)") if e["estimated"] else ""
            print(f"  {e['id']}  {e['name']} {n(e['amount'])} {e['unit']}{(' · ' + e['portion']) if e.get('portion') else ''} → "
                  f"{e['kcal']} kcal / P {n(e['protein'])} / C {n(e['carb'])} / F {n(e['fat'])}{flag}  [{e['source']}]")
    if not d.get("meals"):
        print(L("（这天还没有记录）", "(nothing logged this day)"))
    t = d.get("totals") or {}
    print("\n" + L("合计：", "Total: ") + macro_line(t))
    tg = d.get("targets")
    if tg:
        kind = f"{tg['day_type_label']}，" if tg.get("day_type_label") else ""
        how = {"set": L("标过的", "marked"), "schedule": L("日程里排了训练", "training in the schedule"), "default": L("没排训练", "no training planned")}.get(tg.get("day_type_by") or "", "")
        print(L("目标：", "Target: ") + macro_line(tg) + f"  ({kind}{how + '，' if how else ''}{tg.get('source')})")
        print(L("还差：", "Left: ") + macro_line({k: tg[k] - t.get(k, 0) for k in MACROS}))
    else:
        print(L("目标：没设", "Target: none set"))


def cmd_days(a) -> None:
    d = call("GET", "/api/diet/days" + q({"from": a.lo, "to": a.hi}))
    if a.json:
        return show_json(d)
    for x in d["days"]:
        tg = x.get("targets")
        print(f"{x['date']}  {x['items']:>2} {L('样', 'items')}  {macro_line(x)}" + (f"  / {tg['kcal']} kcal" if tg else ""))


def cmd_library(a) -> None:
    d = call("GET", "/api/diet/library" + q({"days": a.days}))
    if a.json:
        return show_json(d)
    for f in d["foods"]:
        p = f.get("per100") or {}
        print(f"{f.get('id') or '-':<12} {f['name']}  ×{f['times']}  {L('常用', 'usual')} {n(f['typical_amount'])} {f['unit']}  "
              f"/100: {n(p.get('kcal'), 0)} kcal P {n(p.get('protein'))} C {n(p.get('carb'))} F {n(p.get('fat'))}"
              + (f"  [{', '.join(x['label'] + '=' + n(x['grams']) + 'g' for x in f.get('portions') or [])}]" if f.get("portions") else ""))


def cmd_foods(a) -> None:
    d = call("GET", "/api/diet/foods" + q({"q": a.q, "limit": a.limit}))
    if a.json:
        return show_json(d)
    for f in d["foods"]:
        p = f["per100"]
        print(f"{f['id']}  {f['name']}{(' (' + ', '.join(f['aliases']) + ')') if f['aliases'] else ''}  /100{f['basis']}: "
              f"{n(p.get('kcal'), 0)} kcal P {n(p.get('protein'))} C {n(p.get('carb'))} F {n(p.get('fat'))}  "
              f"[{f['source']}{(': ' + f['sourceNote']) if f.get('sourceNote') else ''}]"
              + (f"  {', '.join(x['label'] + '=' + n(x['grams']) + 'g' for x in f['portions'])}" if f["portions"] else "") + f"  ×{f['uses']}")
    print(L(f"共 {d['total']} 样", f"{d['total']} foods in total"))


def cmd_lookup(a) -> None:
    if not (a.query or a.en or a.barcode):
        sys.exit(L("给一个名字、--en 或 --barcode", "Give a name, --en or --barcode"))
    packaged = True if a.packaged else (False if a.generic else None)
    d = call("GET", "/api/diet/lookup" + q({"q": a.query, "en": a.en, "barcode": a.barcode, "packaged": "true" if packaged else ("false" if packaged is False else None),
                                            "limit": a.limit, "all": "true" if a.all else None, "sources": ",".join(a.source) if a.source else None}))
    if a.json:
        return show_json(d)
    for t in d["tried"]:
        status = (t.get("skipped") and L(f"跳过：{t['skipped']}", f"skipped: {t['skipped']}")) or (t.get("error") and L(f"没查成：{t['error']}", f"failed: {t['error']}")) \
            or L(f"{t.get('count', 0)} 个结果", f"{t.get('count', 0)} result(s)")
        print(f"[{t['source']}] {status}")
    for r in d["results"]:
        p = r.get("per100") or {}
        ref = f"{r['source']}:{r['ref']}"
        extra = " ".join(x for x in (r.get("brand") or "", r.get("quantity") or "") if x)
        print(f"  {ref:<22} {r['name']}{(' · ' + extra) if extra else ''}  /100{r.get('basis', 'g')}: {n(p.get('kcal'), 0)} kcal P {n(p.get('protein'))} "
              f"C {n(p.get('carb'))} F {n(p.get('fat'))}{'' if r.get('complete') else L('  （不全）', '  (incomplete)')}"
              + (f"  {L('份量', 'portions')}: {', '.join(x['label'] + '=' + n(x['grams']) + 'g' for x in r['portions'])}" if r.get("portions") else ""))
    if d.get("estimate"):
        print(d.get("hint"))
    elif any(r["source"] == "off" for r in d["results"]):
        print(L("Open Food Facts 的数据是 ODbL：存进食物库时出处会写上。", "Open Food Facts data is ODbL: the source is recorded when you save it."))


def portions_arg(items: list[str] | None) -> list[dict] | None:
    if items is None:
        return None
    out = []
    for p in items:
        label, sep, g = p.rpartition("=")
        try:
            grams = float(g.strip().rstrip("gGmlML").strip())
        except ValueError:
            grams = 0
        if not sep or not label.strip() or grams <= 0:
            sys.exit(L(f"--portion 写成「名字=克数」，比如「1 个（去壳）=65」：{p}", f'--portion is "label=grams", e.g. "1 egg (shelled)=65": {p}'))
        out.append({"label": label.strip(), "grams": grams})
    return out


def per100_arg(s: str | None) -> dict | None:
    if not s:
        return None
    parts = [x for x in re.split(r"[,/\s]+", s.strip()) if x]
    try:
        vals = [float(x) for x in parts]
    except ValueError:
        vals = []
    if len(vals) != 4:
        sys.exit(L("--per100 写成 kcal,蛋白质,碳水,脂肪，比如 133,5.7,4.8,10.2", "--per100 is kcal,protein,carb,fat, e.g. 133,5.7,4.8,10.2"))
    return dict(zip(MACROS, vals))


def print_food(d: dict) -> None:
    f = d["food"]
    p = f["per100"]
    print(L(f"{'加进' if d.get('created') else '更新了'}食物库：", f"{'Added to' if d.get('created') else 'Updated in'} the food library: ")
          + f"{f['id']}  {f['name']}  /100{f['basis']}: {n(p.get('kcal'), 0)} kcal P {n(p.get('protein'))} C {n(p.get('carb'))} F {n(p.get('fat'))}"
          + f"  [{f['source']}{(': ' + f['sourceNote']) if f.get('sourceNote') else ''}]"
          + (f"  {', '.join(x['label'] + '=' + n(x['grams']) + f['basis'] for x in f['portions'])}" if f["portions"] else ""))


def cmd_food_save(a) -> None:
    src, _, ref = (a.src or "").partition(":")
    if src not in ("off", "cofid", "usda") or not ref:
        sys.exit(L("--from 写成 off:<条码> / cofid:<编号> / usda:<fdcId>", "--from is off:<barcode> / cofid:<code> / usda:<fdcId>"))
    body = {"from": {"source": src, "ref": ref}, "name": a.name, "aliases": a.alias or [], "portions": portions_arg(a.portion), "by": a.by or guess_source()}
    d = call("POST", "/api/diet/foods", body)
    print_food(d)


def cmd_food_add(a) -> None:
    body = {"name": a.name, "per100": per100_arg(a.per100), "basis": a.basis, "source": a.source or ("estimate" if a.estimated else "manual"),
            "source_note": a.note, "brand": a.brand, "aliases": a.alias or [], "portions": portions_arg(a.portion), "barcode": a.barcode,
            "estimated": bool(a.estimated or a.source == "estimate"), "by": a.by or guess_source()}
    print_food(call("POST", "/api/diet/foods", body))


def cmd_food_update(a) -> None:
    body = {"name": a.name, "per100": per100_arg(a.per100), "portions": portions_arg(a.portion), "aliases": a.alias, "brand": a.brand,
            "source_note": a.note, "barcode": a.barcode, "basis": a.basis}
    d = call("PATCH", f"/api/diet/foods/{urllib.parse.quote(a.id)}", {k: v for k, v in body.items() if v is not None})
    print_food({"food": d["food"], "created": False})


def cmd_food_rm(a) -> None:
    call("DELETE", f"/api/diet/foods/{urllib.parse.quote(a.id)}")
    print(L(f"从食物库拿掉了 {a.id}（记录里的不受影响）", f"Removed {a.id} from the food library (logged entries stay)"))


ITEM_RE = re.compile(r"^(?P<ref>[^=:]+?)\s*(?:=\s*(?P<amt>\d+(?:\.\d+)?)\s*(?P<unit>g|ml|克|毫升)?\s*(?:@\s*(?P<tot>[\d.,\s/]+))?|:\s*(?P<portion>.+?)\s*(?:[×x*]\s*(?P<count>\d+(?:\.\d+)?))?)$")


def item_arg(s: str, estimated: bool) -> dict:
    m = ITEM_RE.match(s.strip())
    if not m:
        sys.exit(L(f"--item 写不对：{s}（「名字或 id=克数」、「名字:份量×个数」、「名字=克数@kcal,蛋白质,碳水,脂肪」）",
                   f'Bad --item: {s} ("name or id=grams", "name:portion×count", "name=grams@kcal,protein,carb,fat")'))
    ref = m.group("ref").strip()
    it: dict = {"food": ref} if ref.startswith("f-") else {"name": ref}
    if m.group("portion"):
        it.update({"portion": m.group("portion").strip(), "count": float(m.group("count")) if m.group("count") else 1})
        return it
    it["amount"] = float(m.group("amt"))
    if m.group("unit"):
        it["unit"] = m.group("unit")
    if m.group("tot"):
        vals = [float(x) for x in re.split(r"[,/\s]+", m.group("tot").strip()) if x]
        if len(vals) != 4:
            sys.exit(L(f"@ 后面要四个数：kcal,蛋白质,碳水,脂肪（{s}）", f"After @ give four numbers: kcal,protein,carb,fat ({s})"))
        it.update(dict(zip(MACROS, vals)))
        if estimated:
            it["estimated"] = True
    return it


def print_entries(d: dict) -> None:
    if d.get("preview"):
        print(L("预览（还没记，加 --commit 才写进去）：", "Preview (not logged yet; add --commit to write it):"))
    for e in d["entries"]:
        flag = L("（估算）", " (estimate)") if e.get("estimated") else ""
        src = e.get("nutriSource") or ""
        print(f"  {e.get('mealLabel', '')} {e['name']} {n(e['amount'])} {e['unit']}{(' · ' + e['portion']) if e.get('portion') else ''} → "
              f"{e['kcal']} kcal / P {n(e['protein'])} / C {n(e['carb'])} / F {n(e['fat'])}{flag}  [{L('营养来自', 'nutrition from')} {src}"
              f"{(' ' + e['nutriRef']) if e.get('nutriRef') else ''}]" + (f"  id {e['id']}" if e.get("id") else ""))
    print(L("这次合计：", "This adds: ") + macro_line(d.get("adds") or {}))
    for w in d.get("warnings") or []:
        print("! " + w)
    if not d.get("preview"):
        day = d.get("day") or {}
        print(L("今天合计：", "Day total: ") + macro_line(day.get("totals") or {}))
        if day.get("remaining"):
            print(L("还差：", "Left: ") + macro_line(day["remaining"]))
        print(L(f"批号 {d['batch']}（撤销：diet_ctl.py undo {d['batch']}）", f"Batch {d['batch']} (undo: diet_ctl.py undo {d['batch']})"))


def cmd_add(a) -> None:
    if a.json:
        raw = sys.stdin.read() if a.json == "-" else Path(a.json).expanduser().read_text(encoding="utf8")
        body = json.loads(raw)
    else:
        if not a.meal or not a.item:
            sys.exit(L("要 --meal 和至少一个 --item（或 --json）", "Needs --meal and at least one --item (or --json)"))
        body = {"meal": a.meal, "items": [item_arg(s, a.estimated) for s in a.item]}
    body.update({k: v for k, v in (("date", a.date), ("time", a.time), ("source", a.source), ("note", a.note)) if v})
    body.setdefault("source", "chat")
    body["commit"] = bool(a.commit)
    body["by"] = a.by or guess_source()
    print_entries(call("POST", "/api/diet/entries", body))


def cmd_log_plan(a) -> None:
    sets = []
    for s in a.set or []:
        m = re.match(r"^(\d+)\.(\d+)\s*=\s*(\d+(?:\.\d+)?)$", s.strip())
        if not m:
            sys.exit(L(f"--set 写成 餐.样=克数（从 0 数），比如 0.1=100：{s}", f"--set is meal.item=grams (from 0), e.g. 0.1=100: {s}"))
        sets.append({"meal": int(m.group(1)), "item": int(m.group(2)), "amount": float(m.group(3))})
    meals = [int(x) if x.isdigit() else x for x in (a.meal or [])] or None
    body = {"card": a.card, "meals": meals, "scale": a.scale, "items": sets or None, "date": a.date, "time": a.time, "again": a.again,
            "commit": bool(a.commit), "by": a.by or guess_source()}
    d = call("POST", "/api/diet/log-plan", {k: v for k, v in body.items() if v is not None})
    print(L(f"建议卡 {d['card']}：", f"Plan card {d['card']}: ") + "、".join(f"{m['label']} {m['kcal']} kcal" for m in d["meals"]))
    if d.get("already"):
        print(L(f"已经记过、这次没记：{'、'.join(map(str, d['already']))}", f"Already logged, skipped: {', '.join(map(str, d['already']))}"))
    print_entries(d)


def cmd_edit(a) -> None:
    body = {"amount": a.amount, "meal": a.meal, "time": a.time, "date": a.date, "name": a.name, "note": a.note, "by": a.by or guess_source()}
    d = call("PATCH", f"/api/diet/entries/{urllib.parse.quote(a.id)}", {k: v for k, v in body.items() if v is not None})
    e = d["entry"]
    print(L("改好了：", "Updated: ") + f"{e['id']} {e['mealLabel']} {e['name']} {n(e['amount'])} {e['unit']} → {e['kcal']} kcal / P {n(e['protein'])}")
    if d.get("day"):
        print(L("这天合计：", "Day total: ") + macro_line(d["day"]["totals"]))


def cmd_rm(a) -> None:
    d = call("DELETE", f"/api/diet/entries/{urllib.parse.quote(a.id)}" + q({"by": a.by or guess_source()}))
    print(L(f"删掉了 {a.id}。这天合计：", f"Deleted {a.id}. Day total: ") + macro_line(d["day"]["totals"]))


def cmd_undo(a) -> None:
    d = call("POST", "/api/diet/undo", {"batch": a.batch, "by": a.by or guess_source()})
    print(L(f"撤销了 {d['undone']} 样。这天合计：", f"Undid {d['undone']} item(s). Day total: ") + macro_line(d["day"]["totals"]))


def cmd_day_type(a) -> None:
    body = {"date": a.date, "day_type": None if a.type == "auto" else a.type, "by": a.by or guess_source()}
    d = call("PUT", "/api/diet/day-type", body)
    how = {"set": L("标过的", "marked"), "schedule": L("日程里排了训练", "training in the schedule"), "default": L("没排训练", "no training planned")}.get(d["day_type_by"], "")
    tg = d.get("targets")
    print(f"{d['date']}：" + (L("训练日", "training day") if d["day_type"] == "train" else L("休息日", "rest day")) + f"（{how}）"
          + (L("，目标 ", ", target ") + macro_line(tg) if tg else ""))


def cmd_targets(a) -> None:
    if a.action == "set":
        if None in (a.protein, a.carb, a.fat) and not a.clear:
            sys.exit(L("targets set 要 --protein --carb --fat（或 --clear）", "targets set needs --protein --carb --fat (or --clear)"))
        body = {"protein": a.protein, "carb": a.carb, "fat": a.fat, "kcal": a.kcal, "water_ml": a.water, "from": a.lo, "note": a.note, "clear": a.clear,
                "day_type": a.day_type, "by": a.by or guess_source()}
        d = call("PUT", "/api/diet/targets", {k: v for k, v in body.items() if v is not None})
        cur = d.get("current")
        print(L(f"从 {d['from']} 起：", f"From {d['from']}: ") + (macro_line(cur) if cur else L("不设目标", "no target")))
        return
    d = call("GET", "/api/diet/targets")
    if a.json:
        return show_json(d)
    cur = d.get("current")
    print(L("今天：", "Today: ") + (macro_line(cur) + f"（{(cur['day_type_label'] + '，') if cur.get('day_type_label') else ''}{cur.get('source')}，{cur.get('since') or ''} 起）"
                                    if cur else L("没设", "none")))
    for k, v in ((cur or {}).get("cycle") or {}).items():
        print(f"  {L('训练日', 'training day') if k == 'train' else L('休息日', 'rest day')}：{macro_line(v)}")
    for h in d["history"][:12]:
        kind = {"train": L("训练日 ", "training "), "rest": L("休息日 ", "rest ")}.get(h.get("for") or "", "")
        print(f"  {h['from']}  {kind}" + (L("不设目标", "cleared") if h.get("cleared") else macro_line(h)) + (f"  {h['note']}" if h.get("note") else ""))


def main() -> int:
    p = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--by", help=L("谁在记（Agent id）；不给按当前目录猜", "who is logging (Agent id); guessed from the current directory"))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("day")
    s.add_argument("--date")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_day)
    s = sub.add_parser("days")
    s.add_argument("--from", dest="lo")
    s.add_argument("--to", dest="hi")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_days)
    s = sub.add_parser("library")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_library)
    s = sub.add_parser("foods")
    s.add_argument("--q")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_foods)
    s = sub.add_parser("lookup")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--en")
    s.add_argument("--barcode")
    s.add_argument("--packaged", action="store_true", help=L("当包装食品查（去 Open Food Facts）", "treat as a packaged food (ask Open Food Facts)"))
    s.add_argument("--generic", action="store_true", help=L("当通用食物查（不去 Open Food Facts）", "treat as a generic food (skip Open Food Facts)"))
    s.add_argument("--all", action="store_true")
    s.add_argument("--source", action="append", choices=["library", "off", "cofid", "usda"], help=L("只查这几个来源", "only these sources"))
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_lookup)
    s = sub.add_parser("food-save")
    s.add_argument("--from", dest="src", required=True)
    s.add_argument("--name")
    s.add_argument("--alias", action="append")
    s.add_argument("--portion", action="append")
    s.set_defaults(fn=cmd_food_save)
    s = sub.add_parser("food-add")
    s.add_argument("--name", required=True)
    s.add_argument("--per100", required=True)
    s.add_argument("--basis", choices=["g", "ml"])
    s.add_argument("--source", choices=["label", "manual", "estimate", "off", "cofid", "usda"])
    s.add_argument("--note")
    s.add_argument("--brand")
    s.add_argument("--barcode")
    s.add_argument("--alias", action="append")
    s.add_argument("--portion", action="append")
    s.add_argument("--estimated", action="store_true")
    s.set_defaults(fn=cmd_food_add)
    s = sub.add_parser("food-update")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--per100")
    s.add_argument("--basis", choices=["g", "ml"])
    s.add_argument("--note")
    s.add_argument("--brand")
    s.add_argument("--barcode")
    s.add_argument("--alias", action="append")
    s.add_argument("--portion", action="append")
    s.set_defaults(fn=cmd_food_update)
    s = sub.add_parser("food-rm")
    s.add_argument("id")
    s.set_defaults(fn=cmd_food_rm)
    s = sub.add_parser("add")
    s.add_argument("--meal")
    s.add_argument("--item", action="append")
    s.add_argument("--json")
    s.add_argument("--date")
    s.add_argument("--time")
    s.add_argument("--source", choices=["chat", "photo", "manual", "plan"])
    s.add_argument("--note")
    s.add_argument("--estimated", action="store_true")
    s.add_argument("--commit", action="store_true")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("log-plan")
    s.add_argument("--card")
    s.add_argument("--meal", action="append")
    s.add_argument("--scale", type=float)
    s.add_argument("--set", action="append")
    s.add_argument("--date")
    s.add_argument("--time")
    s.add_argument("--again", action="store_true")
    s.add_argument("--commit", action="store_true")
    s.set_defaults(fn=cmd_log_plan)
    s = sub.add_parser("edit")
    s.add_argument("id")
    s.add_argument("--amount", type=float)
    s.add_argument("--meal")
    s.add_argument("--time")
    s.add_argument("--date")
    s.add_argument("--name")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_edit)
    s = sub.add_parser("rm")
    s.add_argument("id")
    s.set_defaults(fn=cmd_rm)
    s = sub.add_parser("undo")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_undo)
    s = sub.add_parser("targets")
    s.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    s.add_argument("--protein", type=float)
    s.add_argument("--carb", type=float)
    s.add_argument("--fat", type=float)
    s.add_argument("--kcal", type=float)
    s.add_argument("--water", type=float)
    s.add_argument("--from", dest="lo")
    s.add_argument("--note")
    s.add_argument("--clear", action="store_true")
    s.add_argument("--day-type", choices=["train", "rest"], help=L("碳循环：只改训练日 / 休息日那一套", "carb cycling: only the training-day or rest-day set"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_targets)
    s = sub.add_parser("day-type")
    s.add_argument("type", choices=["train", "rest", "auto"])
    s.add_argument("--date")
    s.set_defaults(fn=cmd_day_type)
    a = p.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())

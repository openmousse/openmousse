"""营养数据库的外部来源（2026-10-13，饮食记录从训记搬回自己的库时加的）：一样食物每 100 g（饮品 100 ml）的热量和三大营养素。

查找顺序由 diet.py 的 lookup 定：个人食物库 → 包装食品查 Open Food Facts（条码 / 名称）或拍标签 → CoFID → USDA；都查不到由 Agent 估算并标「估算」。
这个模块只管后三样外部来源，参考数据和缓存放在 <data_dir>/nutrition.db（删了能重建：CoFID 重新导入，Open Food Facts / USDA 重新查）。

- CoFID 2021（McCance and Widdowson's composition of foods integrated dataset）：英国政府的通用食物成分表，2,887 种，每 100 g。
  从 GOV.UK 下载 Excel（4.4 MB）导入本地表 cofid，不连网查；开放政府许可 v3.0（Contains public sector information licensed under the
  Open Government Licence v3.0）。「Tr」（微量）记 0，「N」（有但没测）记空。第一次查的时候自动下载导入，也可以 `python3 nutrition.py cofid-import [--file 本地文件]`。
- Open Food Facts：包装食品。条码走 API v3（/api/v3/product/<条码>，慢的时候要三四十秒，所以缓存 30 天），名称走 Search-a-licious
  （search.openfoodfacts.org，优先英国在售的）。User-Agent 只写项目名和网址，不带任何个人信息。官方限频：读商品每分钟 15 次、搜索每分钟 10 次，
  这里按 4 秒 / 6 秒一次排队。数据是 ODbL：凡是来自它的地方都写明「Open Food Facts（ODbL）」；查过的只缓存在本机（off_cache），
  不对外分发合并后的库（分享、名片 Agent、A2A 都不带食物库）。
- USDA FoodData Central：补充，只查通用食物（Foundation、SR Legacy），要 api.data.gov 的 key：环境变量或 .env 里的 USDA_FDC_API_KEY
  （server.json 的 nutrition.usda_key_env 能换名字），没有就跳过。key 放在请求头里，不进网址和日志。

server.json 的 nutrition（都可省）：{"off": false 关掉 Open Food Facts, "usda": false 关掉 USDA, "usda_key_env": "…", "cofid_url": "…"}。每次读文件。

统一的候选格式（diet.py 和 diet_ctl.py 都认这个）：
  {"source": "off" | "cofid" | "usda", "ref": 条码 / 食物编号 / fdcId, "name", "brand", "basis": "g" | "ml",
   "per100": {"kcal", "protein", "carb", "fat", "sugars", "fibre", "satfat", "salt"}, "portions": [{"label", "grams"}], "quantity",
   "complete": 四样都有, "url", "attribution"}
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx

from config import raw, settings
from i18n import L

NDB = settings.data_dir / "nutrition.db"
COFID_URL = ("https://assets.publishing.service.gov.uk/media/60538b91e90e07527df82ae4/"
             "McCance_Widdowsons_Composition_of_Foods_Integrated_Dataset_2021..xlsx")
COFID_SHA256 = "436e9445ef2adb2a75f3d7edd51302de3adad25385f9795fc94ba58bd030e97d"  # 2026-10-13 下载的那份（GOV.UK 2021-03-19 版）
COFID_VERSION = "CoFID 2021"
OFF_PRODUCT = "https://world.openfoodfacts.org/api/v3/product/{code}"
OFF_SEARCH = "https://search.openfoodfacts.org/search"
OFF_PAGE = "https://world.openfoodfacts.org/product/{code}"
OFF_FIELDS = ("code,product_name,product_name_en,brands,quantity,serving_size,serving_quantity,nutriments,nutrition_data_per,"
              "product_quantity_unit,countries_tags")
USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_FOOD = "https://api.nal.usda.gov/fdc/v1/food/{id}"
USDA_PAGE = "https://fdc.nal.usda.gov/food-details/{id}/nutrients"
OFF_ATTR = "Open Food Facts (ODbL)"
COFID_ATTR = "CoFID 2021 (OGL v3.0)"
USDA_ATTR = "USDA FoodData Central"
PRODUCT_TTL, MISS_TTL, SEARCH_TTL = 30 * 86400, 86400, 7 * 86400
GAPS = {"off_product": 4.0, "off_search": 6.0, "usda": 1.0}  # 秒；Open Food Facts 官方限频的换算，USDA 每小时 1000 次
BARCODE = re.compile(r"^\d{8,14}$")
NUTRIENTS = ("kcal", "protein", "carb", "fat", "sugars", "fibre", "satfat", "salt")

_ready = False
_import_lock = threading.Lock()
_net_lock = threading.Lock()
_last_call: dict[str, float] = {}


def cfg() -> dict:
    c = raw().get("nutrition")
    return c if isinstance(c, dict) else {}


def ndb() -> sqlite3.Connection:
    global _ready
    NDB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(NDB, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cofid (code TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, grp TEXT,
            kcal REAL, protein REAL, carb REAL, fat REAL, sugars REAL, fibre REAL, satfat REAL, salt REAL, water REAL);
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, found INTEGER NOT NULL, data TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        _ready = True
    return conn


def num(v) -> float | None:
    """表格和接口里的数：「Tr」（微量）= 0，「N」/ 空 / 认不出 = 没有。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.upper() == "N":
        return None
    if s.lower() in ("tr", "trace"):
        return 0.0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def r1(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def complete(per100: dict) -> bool:
    return all(per100.get(k) is not None for k in ("kcal", "protein", "carb", "fat"))


# —— CoFID ————————————————————————————————————————————————————————————

XNS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):  # type: ignore[union-attr]
        n = n * 26 + ord(ch) - 64
    return n - 1


def _sheets(z: zipfile.ZipFile) -> tuple[dict[str, str], list[str]]:
    """xlsx 里每张表的名字 → 文件路径，和共享字符串。只读 CoFID 这种普通表格（不认公式、日期）。"""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    sheets = wb.find("m:sheets", XNS)
    for s in (list(sheets) if sheets is not None else []):
        target = str(rels.get(s.get("{%s}id" % XNS["r"])) or "").lstrip("/")
        out[str(s.get("name"))] = target if target.startswith("xl/") else "xl/" + target
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", XNS):
            strings.append("".join(t.text or "" for t in si.iter("{%s}t" % XNS["m"])))
    return out, strings


def _rows(z: zipfile.ZipFile, path: str, strings: list[str]) -> list[list[str | None]]:
    rows = []
    for r in ET.fromstring(z.read(path)).iter("{%s}row" % XNS["m"]):
        vals: dict[int, str | None] = {}
        for c in r.findall("m:c", XNS):
            v, t = c.find("m:v", XNS), c.get("t")
            if t == "s" and v is not None:
                val = strings[int(v.text or 0)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % XNS["m"]))
            else:
                val = v.text if v is not None else None
            vals[_col(str(c.get("r")))] = val
        rows.append([vals.get(i) for i in range(max(vals) + 1)] if vals else [])
    return rows


def _pick(header: list, *names: str) -> int:
    low = [str(h or "").strip().lower() for h in header]
    for n in names:
        if n.lower() in low:
            return low.index(n.lower())
    raise ValueError(f"CoFID: column not found: {names[0]}")


def parse_cofid(path: Path) -> list[dict]:
    """Proximates 表 = 热量、三大营养素、糖、纤维、饱和脂肪、水；Inorganics 表的钠换成盐（× 2.5）。"""
    with zipfile.ZipFile(path) as z:
        sheets, strings = _sheets(z)
        prox = next((p for n, p in sheets.items() if "proximates" in n.lower()), None)
        inorg = next((p for n, p in sheets.items() if "inorganics" in n.lower()), None)
        if not prox:
            raise ValueError("CoFID: no Proximates sheet")
        rows = _rows(z, prox, strings)
        h = rows[0]
        ix = {"code": _pick(h, "Food Code"), "name": _pick(h, "Food Name"), "description": _pick(h, "Description"), "grp": _pick(h, "Group"),
              "kcal": _pick(h, "Energy (kcal) (kcal)"), "protein": _pick(h, "Protein (g)"), "carb": _pick(h, "Carbohydrate (g)"),
              "fat": _pick(h, "Fat (g)"), "sugars": _pick(h, "Total sugars (g)"), "fibre": _pick(h, "AOAC fibre (g)"),
              "satfat": _pick(h, "Satd FA /100g fd (g)"), "water": _pick(h, "Water (g)")}
        sodium: dict[str, float | None] = {}
        if inorg:
            irows = _rows(z, inorg, strings)
            ih = irows[0]
            ci, si = 0, _pick(ih, "Sodium (mg)")
            for r in irows[1:]:
                if r and r[ci] and re.match(r"^\d", str(r[ci])):
                    sodium[str(r[ci]).strip()] = num(r[si] if si < len(r) else None)
    out = []
    seen: dict[str, int] = {}
    for r in rows[1:]:
        code = str(r[ix["code"]] or "").strip() if len(r) > ix["code"] else ""
        if not re.match(r"^\d{2}-\d{3}", code):  # 前三行是表头（名字、代号、长名字）
            continue
        get = (lambda k: r[ix[k]] if ix[k] < len(r) else None)  # noqa: E731
        na = sodium.get(code)
        seen[code] = seen.get(code, 0) + 1
        if seen[code] > 1:  # 2021 版有一个编号用了两次（13-669：烤茄子、西洋菜），第二个加后缀
            code = f"{code}.{seen[code]}"
        out.append({"code": code, "name": str(get("name") or "").strip(), "description": str(get("description") or "").strip() or None,
                    "grp": str(get("grp") or "").strip() or None,
                    **{k: num(get(k)) for k in ("kcal", "protein", "carb", "fat", "sugars", "fibre", "satfat", "water")},
                    "salt": round(na * 2.5 / 1000, 3) if na is not None else None})
    return out


def cofid_status() -> dict:
    with ndb() as conn:
        n = conn.execute("SELECT COUNT(*) FROM cofid").fetchone()[0]
        m = dict(conn.execute("SELECT key, value FROM meta WHERE key LIKE 'cofid.%'").fetchall())
    return {"ready": n > 0, "foods": n, "version": m.get("cofid.version"), "imported_at": m.get("cofid.imported_at"), "sha256": m.get("cofid.sha256")}


def cofid_import(file: str | Path | None = None) -> dict:
    """下载（或用给的本地文件）→ 解析 → 整表换掉。下载的文件核对 sha256，和记下的不一样也照导（GOV.UK 可能换了新版），但写进 meta。"""
    with _import_lock:
        if file:
            src = Path(file).expanduser()
            data = src.read_bytes()
        else:
            url = str(cfg().get("cofid_url") or COFID_URL)
            with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": user_agent()}) as c:
                resp = c.get(url)
                resp.raise_for_status()
                data = resp.content
        sha = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cofid.xlsx"
            p.write_bytes(data)
            foods = parse_cofid(p)
        if len(foods) < 1000:
            raise ValueError(f"CoFID: only {len(foods)} foods parsed")
        with ndb() as conn:
            conn.execute("DELETE FROM cofid")
            conn.executemany("INSERT INTO cofid(code, name, description, grp, kcal, protein, carb, fat, sugars, fibre, satfat, salt, water) "
                             "VALUES(:code,:name,:description,:grp,:kcal,:protein,:carb,:fat,:sugars,:fibre,:satfat,:salt,:water)", foods)
            for k, v in (("cofid.version", COFID_VERSION), ("cofid.sha256", sha), ("cofid.imported_at", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
                         ("cofid.expected_sha", "match" if sha == COFID_SHA256 else "differs")):
                conn.execute("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        return {"ok": True, "foods": len(foods), "sha256": sha, "same_as_known": sha == COFID_SHA256}


def ensure_cofid() -> bool:
    """第一次查 CoFID 时自动下载导入（约 5 秒）；连不上就当这次没有，下次再试。"""
    try:
        if cofid_status()["ready"]:
            return True
        cofid_import()
        return True
    except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as e:
        print(f"[nutrition] CoFID import failed: {e}", file=sys.stderr)
        return False


def _words(s: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", (s or "").lower()):
        if len(w) > 3 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
            w = w[:-2]
        elif len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def cofid_row(r: sqlite3.Row) -> dict:
    per100 = {k: r[k] for k in ("kcal", "protein", "carb", "fat", "sugars", "fibre", "satfat", "salt")}
    return {"source": "cofid", "ref": r["code"], "name": r["name"], "brand": None, "basis": "g", "per100": per100, "portions": [],
            "quantity": None, "description": r["description"], "group": r["grp"], "complete": complete(per100), "url": None, "attribution": COFID_ATTR}


def cofid_search(q: str, limit: int = 5) -> list[dict]:
    """英文名查（CoFID 只有英文）：每个词都要对上名字里某个词的开头；名字越短、第一个词对上的越靠前。"""
    words = _words(q)
    if not words or not ensure_cofid():
        return []
    with ndb() as conn:
        rows = conn.execute("SELECT * FROM cofid").fetchall()
    scored = []
    for r in rows:
        nw = _words(r["name"])
        if not all(any(x.startswith(w) for x in nw) for w in words):
            continue
        score = 0.0
        if nw and nw[0].startswith(words[0]):
            score += 3
        score += sum(1 for w in words if w in nw)  # 整词对上
        score -= 0.15 * max(0, len(nw) - len(words))
        if r["kcal"] is None:
            score -= 2
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], len(x[1]["name"])))
    return [cofid_row(r) for _, r in scored[:limit]]


def cofid_get(code: str) -> dict | None:
    if not ensure_cofid():
        return None
    with ndb() as conn:
        r = conn.execute("SELECT * FROM cofid WHERE code=?", (code.strip(),)).fetchone()
    return cofid_row(r) if r else None


# —— 网络：限频、缓存 ————————————————————————————————————————————————————

def user_agent() -> str:
    """只写项目名、版本和网址（Open Food Facts 要求能认出是谁在调；不放任何人的邮箱）。"""
    try:
        import version
        v = version.VERSION
    except Exception:  # noqa: BLE001
        v = "dev"
    return f"OpenMousse/{v} (+https://openmousse.ai)"


def _wait(kind: str) -> None:
    with _net_lock:
        gap = GAPS.get(kind, 1.0)
        wait = _last_call.get(kind, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[kind] = time.monotonic()


def cache_get(key: str, ttl: float, miss_ttl: float = MISS_TTL) -> tuple[bool, dict | None] | None:
    """(found, data)；没缓存或过期 = None。"""
    with ndb() as conn:
        r = conn.execute("SELECT fetched_at, found, data FROM cache WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    age = time.time() - r["fetched_at"]
    if age > (ttl if r["found"] else miss_ttl):
        return None
    return bool(r["found"]), (json.loads(r["data"]) if r["data"] else None)


def cache_put(key: str, found: bool, data) -> None:
    with ndb() as conn:
        conn.execute("INSERT INTO cache(key, fetched_at, found, data) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                     "fetched_at=excluded.fetched_at, found=excluded.found, data=excluded.data",
                     (key, time.time(), 1 if found else 0, json.dumps(data, ensure_ascii=False) if data is not None else None))


class SourceError(Exception):
    """外部来源这次没查成（超时、限频、没 key）：lookup 记下来，接着查下一个来源。"""


# —— Open Food Facts ————————————————————————————————————————————————————

def off_enabled() -> bool:
    return cfg().get("off", True) is not False


def _first(v) -> str | None:
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, str):
        v = v.split(",")[0].strip()
    return v or None


def off_candidate(p: dict) -> dict | None:
    n = p.get("nutriments") or {}

    def g(k: str) -> float | None:
        return num(n.get(f"{k}_100g"))
    kcal = g("energy-kcal")
    if kcal is None and g("energy") is not None:  # energy_100g 是 kJ
        kcal = round(g("energy") / 4.184, 1)  # type: ignore[operator]
    per100 = {"kcal": r1(kcal), "protein": r1(g("proteins")), "carb": r1(g("carbohydrates")), "fat": r1(g("fat")),
              "sugars": r1(g("sugars")), "fibre": r1(g("fiber")), "satfat": r1(g("saturated-fat")), "salt": r1(g("salt"))}
    if all(per100[k] is None for k in ("kcal", "protein", "carb", "fat")):
        return None
    code = str(p.get("code") or "")
    per = str(p.get("nutrition_data_per") or "")
    liquid = per == "100ml" or str(p.get("product_quantity_unit") or "") == "ml" or (
        not per and bool(re.search(r"\d\s*(ml|cl|l)\b", str(p.get("quantity") or ""), re.I)))  # 搜索结果不带 nutrition_data_per：看包装规格
    basis = "ml" if liquid else "g"
    portions = []
    sq = num(p.get("serving_quantity"))
    if sq and sq > 0:
        portions.append({"label": str(p.get("serving_size") or L("1 份", "1 serving")), "grams": sq})
    return {"source": "off", "ref": code, "name": str(p.get("product_name_en") or p.get("product_name") or "").strip(), "brand": _first(p.get("brands")),
            "basis": basis, "per100": per100, "portions": portions, "quantity": p.get("quantity") or None,
            "uk": "en:united-kingdom" in (p.get("countries_tags") or []), "complete": complete(per100),
            "url": OFF_PAGE.format(code=code) if code else None, "attribution": OFF_ATTR}


def off_barcode(code: str) -> dict | None:
    """条码 → 商品（API v3）。慢的时候三四十秒；查到的缓存 30 天，没有的缓存 1 天。"""
    code = re.sub(r"\D", "", code or "")
    if not BARCODE.match(code):
        raise SourceError(L("条码要 8–14 位数字", "A barcode is 8–14 digits"))
    hit = cache_get(f"off:p:{code}", PRODUCT_TTL)
    if hit is not None:
        return hit[1]
    if not off_enabled():
        raise SourceError(L("Open Food Facts 已在 server.json 里关掉", "Open Food Facts is turned off in server.json"))
    _wait("off_product")
    try:
        with httpx.Client(timeout=httpx.Timeout(45, connect=10), headers={"User-Agent": user_agent()}) as c:
            resp = c.get(OFF_PRODUCT.format(code=code), params={"fields": OFF_FIELDS})
    except httpx.HTTPError as e:
        raise SourceError(L(f"Open Food Facts 连不上（{type(e).__name__}）", f"Couldn't reach Open Food Facts ({type(e).__name__})")) from e
    if resp.status_code == 404:
        cache_put(f"off:p:{code}", False, None)
        return None
    if resp.status_code in (429, 503):
        raise SourceError(L("Open Food Facts 限频，稍后再查", "Open Food Facts is rate-limiting; try again later"))
    if resp.status_code >= 400:
        raise SourceError(f"Open Food Facts HTTP {resp.status_code}")
    try:
        d = resp.json()
    except ValueError as e:
        raise SourceError("Open Food Facts: not JSON") from e
    cand = off_candidate(d.get("product") or {}) if d.get("product") else None
    cache_put(f"off:p:{code}", cand is not None, cand)
    return cand


def off_search(q: str, limit: int = 5, uk: bool = True) -> list[dict]:
    """名称 → 商品（Search-a-licious）。先找英国在售的，没有再放开。结果缓存 7 天。"""
    q = re.sub(r"\s+", " ", (q or "").strip())
    if not q:
        return []
    key = f"off:s:{'uk' if uk else 'all'}:{q.lower()}:{limit}"
    hit = cache_get(key, SEARCH_TTL, SEARCH_TTL)
    if hit is not None:
        return list(hit[1] or [])
    if not off_enabled():
        raise SourceError(L("Open Food Facts 已在 server.json 里关掉", "Open Food Facts is turned off in server.json"))
    out: list[dict] = []
    for scoped in ((True, False) if uk else (False,)):
        query = f'{q} countries_tags:"en:united-kingdom"' if scoped else q
        _wait("off_search")
        try:
            with httpx.Client(timeout=httpx.Timeout(20, connect=10), headers={"User-Agent": user_agent()}) as c:
                resp = c.get(OFF_SEARCH, params={"q": query, "page_size": max(limit * 2, 6), "fields": OFF_FIELDS})
        except httpx.HTTPError as e:
            raise SourceError(L(f"Open Food Facts 连不上（{type(e).__name__}）", f"Couldn't reach Open Food Facts ({type(e).__name__})")) from e
        if resp.status_code in (429, 503):
            raise SourceError(L("Open Food Facts 限频，稍后再查", "Open Food Facts is rate-limiting; try again later"))
        if resp.status_code >= 400:
            raise SourceError(f"Open Food Facts search HTTP {resp.status_code}")
        try:
            hits = resp.json().get("hits") or []
        except ValueError as e:
            raise SourceError("Open Food Facts search: not JSON") from e
        seen = {c["ref"] for c in out}
        for h in hits:
            cand = off_candidate(h)
            if cand and cand["name"] and cand["ref"] not in seen:
                out.append(cand)
                seen.add(cand["ref"])
        if len(out) >= limit:
            break
    out = out[:limit]
    cache_put(key, bool(out), out)
    return out


# —— USDA FoodData Central ————————————————————————————————————————————————

def usda_key() -> str:
    import claw  # 读密钥的统一写法：先进程环境、再 .env，每次读（加了 key 不用重启）
    return claw.env_value(str(cfg().get("usda_key_env") or "USDA_FDC_API_KEY"))


def usda_enabled() -> bool:
    return cfg().get("usda", True) is not False and bool(usda_key())


USDA_NUM = {"kcal": ("208", "958", "957"), "protein": ("203",), "carb": ("205",), "fat": ("204",), "sugars": ("269",), "fibre": ("291",),
            "satfat": ("606",), "sodium": ("307",)}


def usda_candidate(f: dict) -> dict | None:
    by: dict[str, float] = {}
    for n in f.get("foodNutrients") or []:
        k = str(n.get("nutrientNumber") or (n.get("nutrient") or {}).get("number") or "")
        v = num(n.get("value", n.get("amount")))
        if k and v is not None and k not in by:
            by[k] = v

    def g(k: str) -> float | None:
        return next((by[c] for c in USDA_NUM[k] if c in by), None)
    na = g("sodium")
    per100 = {"kcal": r1(g("kcal")), "protein": r1(g("protein")), "carb": r1(g("carb")), "fat": r1(g("fat")), "sugars": r1(g("sugars")),
              "fibre": r1(g("fibre")), "satfat": r1(g("satfat")), "salt": round(na * 2.5 / 1000, 3) if na is not None else None}
    if all(per100[k] is None for k in ("kcal", "protein", "carb", "fat")):
        return None
    fid = str(f.get("fdcId") or "")
    return {"source": "usda", "ref": fid, "name": str(f.get("description") or "").strip(), "brand": None, "basis": "g", "per100": per100,
            "portions": [], "quantity": None, "data_type": f.get("dataType"), "complete": complete(per100),
            "url": USDA_PAGE.format(id=fid) if fid else None, "attribution": USDA_ATTR}


def usda_search(q: str, limit: int = 5) -> list[dict]:
    q = re.sub(r"\s+", " ", (q or "").strip())
    if not q:
        return []
    if cfg().get("usda", True) is False:
        raise SourceError(L("USDA 已在 server.json 里关掉", "USDA is turned off in server.json"))
    key = usda_key()
    if not key:
        raise SourceError(L("没有 USDA 的 API key（.env 的 USDA_FDC_API_KEY）", "No USDA API key (USDA_FDC_API_KEY in .env)"))
    ck = f"usda:s:{q.lower()}:{limit}"
    hit = cache_get(ck, SEARCH_TTL, SEARCH_TTL)
    if hit is not None:
        return list(hit[1] or [])
    _wait("usda")
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=10), headers={"User-Agent": user_agent(), "X-Api-Key": key}) as c:
            resp = c.post(USDA_SEARCH, json={"query": q, "dataType": ["Foundation", "SR Legacy"], "pageSize": max(limit, 5)})
    except httpx.HTTPError as e:
        raise SourceError(L(f"USDA 连不上（{type(e).__name__}）", f"Couldn't reach USDA ({type(e).__name__})")) from e
    if resp.status_code in (401, 403):
        raise SourceError(L("USDA 拒绝了这个 API key", "USDA rejected the API key"))
    if resp.status_code == 429:
        raise SourceError(L("USDA 限频，稍后再查", "USDA is rate-limiting; try again later"))
    if resp.status_code >= 400:
        raise SourceError(f"USDA HTTP {resp.status_code}")
    try:
        foods = resp.json().get("foods") or []
    except ValueError as e:
        raise SourceError("USDA: not JSON") from e
    out = [c for c in (usda_candidate(f) for f in foods) if c][:limit]
    cache_put(ck, bool(out), out)
    return out


def usda_food(fdc_id: str) -> dict | None:
    """按 fdcId 取一样（lookup 结果存进食物库时用）。"""
    fid = re.sub(r"\D", "", str(fdc_id or ""))
    if not fid:
        raise SourceError(L("fdcId 是数字", "fdcId is a number"))
    hit = cache_get(f"usda:f:{fid}", PRODUCT_TTL)
    if hit is not None:
        return hit[1]
    key = usda_key()
    if not key or cfg().get("usda", True) is False:
        raise SourceError(L("没有 USDA 的 API key（.env 的 USDA_FDC_API_KEY）", "No USDA API key (USDA_FDC_API_KEY in .env)"))
    _wait("usda")
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=10), headers={"User-Agent": user_agent(), "X-Api-Key": key}) as c:
            resp = c.get(USDA_FOOD.format(id=fid))
    except httpx.HTTPError as e:
        raise SourceError(L(f"USDA 连不上（{type(e).__name__}）", f"Couldn't reach USDA ({type(e).__name__})")) from e
    if resp.status_code == 404:
        cache_put(f"usda:f:{fid}", False, None)
        return None
    if resp.status_code in (401, 403):
        raise SourceError(L("USDA 拒绝了这个 API key", "USDA rejected the API key"))
    if resp.status_code >= 400:
        raise SourceError(f"USDA HTTP {resp.status_code}")
    try:
        cand = usda_candidate(resp.json())
    except ValueError as e:
        raise SourceError("USDA: not JSON") from e
    cache_put(f"usda:f:{fid}", cand is not None, cand)
    return cand


def status() -> dict:
    """各来源能不能用（给 /api/diet/lookup 和连接页）。不连网。"""
    c = cofid_status()
    with ndb() as conn:
        cached = conn.execute("SELECT COUNT(*) FROM cache WHERE key LIKE 'off:p:%' AND found=1").fetchone()[0]
    return {"cofid": c, "off": {"enabled": off_enabled(), "cached_products": cached, "attribution": OFF_ATTR},
            "usda": {"enabled": usda_enabled(), "has_key": bool(usda_key())}}


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=L("营养数据库：CoFID 导入、各来源状态", "Nutrition data: CoFID import, source status"))
    sub = p.add_subparsers(dest="cmd", required=True)
    ci = sub.add_parser("cofid-import", help=L("从 GOV.UK 下载 CoFID 2021 导入本地表（或 --file 用本地文件）", "download CoFID 2021 from GOV.UK into the local table (or --file)"))
    ci.add_argument("--file")
    sub.add_parser("status")
    s = sub.add_parser("search", help=L("查一个外部来源（调试用）", "query one external source (debugging)"))
    s.add_argument("source", choices=["cofid", "off", "off-barcode", "usda"])
    s.add_argument("query")
    a = p.parse_args()
    if a.cmd == "cofid-import":
        print(json.dumps(cofid_import(a.file), ensure_ascii=False))
    elif a.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1))
    else:
        try:
            res = {"cofid": cofid_search, "off": off_search, "usda": usda_search}[a.source](a.query) if a.source != "off-barcode" else off_barcode(a.query)
        except SourceError as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 1
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

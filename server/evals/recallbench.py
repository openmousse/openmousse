"""记忆检索按意思搜量一量（2026-10-16）：几万段、几百维的时候，搜一次要多久。数据是编的，库在临时目录，不碰真的索引、不发任何请求。

  cd server && python -m evals.recallbench [--chunks 30000] [--dims 768] [--runs 5]

做法：临时目录里起一份 server.json（按意思搜开着，地址指向一个不存在的本机端口：搜的那一句的向量直接给，不去算），往 recall.db 里
写 N 段编的字（中英文混着的词，FTS 照常建）和 N 串向量（几百个「话题」中心加噪声，像真的向量那样一团一团的，float32 + 符号位），
然后量：
  vector   semantic.vector_scores：范围里全部段的符号位按汉明距离粗筛 + 几百段精算余弦
  brute    对照：每一段都用 float32 硬算一遍余弦（不粗筛）
  hybrid   recall.search 整个走一遍（关键词两路 + 向量 + 合分 + MMR + 挑），不先同步
  keyword  只按关键词（和不开按意思搜时一样）
还会说粗筛留下的前 10 和硬算的前 10 一不一样（recall@10）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

BOOT = Path(tempfile.mkdtemp(prefix="recallbench-"))
(BOOT / "server.json").write_text(json.dumps({
    "app_name": "Bench", "timezone": "UTC", "language": "en", "openclaw_home": str(BOOT / "openclaw"), "data_dir": str(BOOT / "data"),
    "workspace": str(BOOT / "ws"),
    "engine": {"recall": {"db": str(BOOT / "recall.db"), "tree": False,
                          "embeddings": {"backend": "openai", "url": "http://127.0.0.1:9/v1", "key_env": "", "model": "bench"}}},
}), encoding="utf8")
os.environ["MOUSSE_SERVER_CONFIG"] = str(BOOT / "server.json")
os.environ["MOUSSE_TREE_HOME"] = str(BOOT / "mousse-tree")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import embeddings, recall, semantic  # noqa: E402 — 要在上面设好环境变量之后再导入

WORDS = ("旅行 计划 预算 健身 深蹲 睡眠 失眠 早餐 燕麦 面试 简历 项目 会议 日结 周末 爬山 读书 电影 音乐 吉他 猫 团子 妈妈 生日 "
         "travel budget gym sleep breakfast interview resume project meeting weekend hiking book movie music guitar cat birthday "
         "deadline report draft review lunch dinner coffee train flight hotel ticket").split()


def unit(xs: list[float]) -> list[float]:
    s = math.sqrt(sum(x * x for x in xs))
    return [x / s for x in xs]


def build(n: int, dims: int, topics: int, rnd: random.Random) -> list[list[float]]:
    centres = [[rnd.gauss(0, 1) for _ in range(dims)] for _ in range(topics)]
    t0 = time.monotonic()
    with recall._writer() as conn:
        for i in range(n):
            c = centres[i % topics]
            v = embeddings.normalize([x + rnd.gauss(0, 1.1) for x in c])
            text = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(12, 60)))
            sid = conn.execute("INSERT INTO sources(root, path, day) VALUES('ws.main', ?, ?)",
                               (f"memory/n{i}.md", f"2026-{1 + i % 9:02d}-{1 + i % 28:02d}" if i % 3 else None)).lastrowid
            cid = conn.execute("INSERT INTO chunks(source, start_line, end_line, text) VALUES(?,1,1,?)", (sid, text)).lastrowid
            conn.execute("INSERT INTO chunks_fts(rowid, body, title) VALUES(?,?,'')", (cid, recall.segment(text)))
            conn.execute("INSERT INTO vec(chunk, sha, v) VALUES(?,?,?)", (cid, f"s{i}", semantic.pack(v)))
            conn.execute("INSERT INTO vec_bits(chunk, source, bits) VALUES(?,?,?)", (cid, sid, semantic.bits(v)))
        recall._set_meta(conn, "embed_model", embeddings.spec().identity)
    print(f"built {n} chunks × {dims} dims in {time.monotonic() - t0:.0f}s; recall.db {semantic_db_mb():.0f} MB", flush=True)
    return centres


def semantic_db_mb() -> float:
    p = recall.db_path()
    return sum(os.path.getsize(f"{p}{s}") for s in ("", "-wal") if os.path.exists(f"{p}{s}")) / 1e6


def ms(f, runs: int) -> tuple[float, object]:
    out, times = None, []
    for _ in range(runs):
        t0 = time.perf_counter()
        out = f()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.recallbench", description="按意思搜：几万段时搜一次多久")
    ap.add_argument("--chunks", type=int, default=30000)
    ap.add_argument("--dims", type=int, default=768)
    ap.add_argument("--topics", type=int, default=300)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--no-sumprod", action="store_true", help="当成 Python 3.11（没有 math.sumprod）来量")
    a = ap.parse_args(argv)
    if a.no_sumprod:
        import operator
        semantic._dot = lambda x, y: sum(map(operator.mul, x, y))
    rnd = random.Random(7)
    try:
        centres = build(a.chunks, a.dims, a.topics, rnd)
        spec = embeddings.spec()
        print(f"python {sys.version.split()[0]}, math.sumprod: {hasattr(math, 'sumprod') and not a.no_sumprod}")
        recalls = []
        for trial in range(a.runs):
            q = unit([x + rnd.gauss(0, 1.1) for x in centres[(trial * 37) % a.topics]])
            semantic.query = lambda s, text, q=q: (q, None)  # 搜的那一句的向量直接给（不去算）
            with recall._reader() as conn:
                tv, got = ms(lambda: semantic.vector_scores(conn, q, ["s.root = ?"], ["ws.main"], 40, set()), 1)
                t0 = time.perf_counter()
                exact = [(semantic._dot(q, semantic.unpack(b)), c) for c, b in conn.execute("SELECT chunk, v FROM vec")]
                tb = (time.perf_counter() - t0) * 1000
            truth = {c for _, c in sorted(exact, reverse=True)[:10]}
            top = {c for c, _ in sorted(got.items(), key=lambda kv: -kv[1])[:10]}
            recalls.append(len(truth & top) / 10)
            th, found = ms(lambda: recall.search("周末 去哪里 旅行", "main", refresh=False), 1)
            semantic.query = lambda s, text: (None, "bench: keyword only")
            tk, _ = ms(lambda: recall.search("周末 旅行", "main", refresh=False), 1)
            print(f"run {trial + 1}: vector {tv:.0f} ms · brute {tb:.0f} ms · hybrid search {th:.0f} ms ({found.mode}, {len(found.hits)} hits) · "
                  f"keyword search {tk:.0f} ms", flush=True)
        print(f"recall@10 of the prefilter vs brute force: {statistics.mean(recalls):.2f} (spec {spec.label})")
    finally:
        shutil.rmtree(BOOT, ignore_errors=True)


if __name__ == "__main__":
    main()

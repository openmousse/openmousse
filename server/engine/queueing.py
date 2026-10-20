"""排队模式（第 2 批，10/19）：回复还在跑的时候用户又说了一句，怎么办。/queue <模式> 按对话存（threads.queue），不写 = 现在的样子。

照 OpenClaw docs/concepts/queue.md 的四种（只借设计）：
  steer      插进正在跑的这一轮（这一批工具做完带着它再问模型，../chat.py 的 steer_engine）；进不去的排着，这一轮完了合成一轮
             = 不写（以前就是这样）
  followup   不插话：排着，这一轮完了一句一轮、按先后回（../chat.py 的 drain 一次只发一条）
  collect    不插话：排着，这一轮完了合成一轮回（combine，和以前插不进去时一样）
  interrupt  停掉这一轮（像点了停：已经说的留着、末尾标「停了」、会话补完整），收完尾马上回这一句（之前排着的一起）
只管主人自己在 app 里打的那一句（/api/chat/send，origin user）；定时任务、转交、收件箱进来的照旧。只在 builtin（OpenClaw 那条路照旧）。
Claude Code 跑的对话一样：插话写进它的标准输入（engine/claude_brain.py），interrupt 照点了停那样停下它这一轮。
"""
from __future__ import annotations

MODES = ("steer", "followup", "collect", "interrupt")
CLEAR = frozenset({"default", "reset", "clear", "inherit"})


def mode_of(thread: str) -> str | None:
    """这个对话 /queue 设的模式（没设 = None：照旧，插话 + 排着的合成一轮）。只在 builtin。"""
    import chat
    import claw
    if not claw.is_builtin():
        return None
    with chat._lock, chat.db() as conn:
        r = conn.execute("SELECT queue FROM threads WHERE id=?", (thread,)).fetchone()
    m = str(r["queue"] or "") if r else ""
    return m if m in MODES else None


def set_mode(thread: str, mode: str | None) -> None:
    """存这个对话的模式（None = 清掉，照旧）。"""
    import chat
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO threads(id, queue, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET queue=excluded.queue, "
                     "updated_at=excluded.updated_at", (thread, mode if mode in MODES else None, chat.now_iso()))

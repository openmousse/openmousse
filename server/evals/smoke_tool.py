"""第 3 批的服务器冒烟（PR 里那张「服务器冒烟清单」用）：在 server/ 下跑，照这台服务器现在的 server.json 和密钥，给一个 Agent（默认 main）
现找那个工具，照主人回合的样子跑一次，打印结果。

  python -m evals.smoke_tool view_image '{"url": "https://…", "question": "What is in it?"}'
  python -m evals.smoke_tool speak '{"text": "Hello"}' --agent main --conversation smoke-batch3

只跑一次、不进对话（生成的附件存在 --conversation 那个对话名下，默认 smoke-batch3，app 里看不到）；花不花钱、花多少看那个工具（PR 的清单写了）。
关卡说要点头的（ask）不跑：打印分类和理由就停——真要做就在 app 里让 Agent 做、在卡上点。engine.tools.levels 写 off 的照样不跑。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.smoke_tool", description="Run one engine tool once, like an owner turn would")
    ap.add_argument("tool")
    ap.add_argument("args", nargs="?", default="{}", help="the tool's arguments as JSON")
    ap.add_argument("--agent", default="main")
    ap.add_argument("--conversation", default="smoke-batch3")
    a = ap.parse_args(argv)
    import engine
    from engine import loop, mcp_client, tools
    try:
        args = json.loads(a.args)
    except ValueError as e:
        print(f"args isn't JSON: {e}")
        return 2
    try:
        await mcp_client.ready(a.agent)  # 你配的 MCP 服务先起好（tool_search 那几个要它）
    except Exception:  # noqa: BLE001 — 没有 mcp 包、没配：没有它们
        pass
    table = {t.name: t for t in tools.for_agent(a.agent)}
    t = table.get(a.tool)
    if t is None:
        print(f"{a.agent} has no {a.tool} tool now (check its switch in server.json). It has: {', '.join(sorted(table))}")
        await mcp_client.shutdown()
        return 1
    if t.prepare is not None:
        args = t.prepare(args)
    args, problem = loop.check_args(args, t.schema)
    if problem:
        print(f"bad arguments: {problem}")
        await mcp_client.shutdown()
        return 2
    level, why = t.level(args)
    print(f"[{a.tool}] classified {level}: {why}")
    if level in ("ask", "off"):
        print("Not run here: calls that need the user's OK go through a card in the app; turned-off ones don't run.")
        await mcp_client.shutdown()
        return 0
    ctx = engine.ToolCtx(turn="smoke", kind="owner", agent=a.agent, conversation=a.conversation, call_id="smoke", control=engine.Control(),
                         origin="user")
    try:
        res = await asyncio.wait_for(t.run(args, ctx), t.timeout)
    except loop.ToolFailure as e:
        print(f"tool failure: {e}")
        return 1
    finally:
        await mcp_client.shutdown()
    if isinstance(res, loop.ToolOutput):
        print(res.text)
        for b in res.images:
            print(f"(image: {b.get('mime')} {b.get('w')}x{b.get('h')}, stored as {b.get('ref') or b.get('file') or 'in the image store'})")
    else:
        print(res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

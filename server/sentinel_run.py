"""Sentinel 出口代理的启动器（openmousse-sentinel 服务跑的就是它）：用 Sentinel 自己的 venv 跑。

  <venv>/bin/python sentinel_run.py --host 172.30.99.1 --port 3128 --confdir <data_dir>/sentinel/mitm
  （环境变量 MOUSSE_SENTINEL_DIR 指向 <data_dir>/sentinel，见 egress_proxy.py）

为什么不用 `mitmdump -s egress_proxy.py`：-s 的脚本在磁盘上一变（git 合并、rebase 时文件会短暂消失），mitmproxy 就重新加载，
加载失败时它不带插件接着跑——成了什么都放行的普通代理，而且不报错（2026-09-28 测出来的）。这里把插件直接挂进去、不看文件：
代码改了要重启服务才生效；插件导入失败进程就退出，沙箱连不上任何地方（fail closed）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def main(host: str, port: int, confdir: str) -> None:
    from mitmproxy import options
    from mitmproxy.tools import dump

    import egress_proxy  # 导入失败就在这里抛出去：进程退出，不会不带规则地听端口

    opts = options.Options(listen_host=host, listen_port=port, confdir=confdir)
    master = dump.DumpMaster(opts, with_termlog=True, with_dumper=False)
    # 这几个选项由 DumpMaster 自带的插件注册，要在建好以后再设
    master.options.update(connection_strategy="lazy", body_size_limit="25m", rawtcp=False, termlog_verbosity="warn")
    master.addons.add(*egress_proxy.addons)
    await master.run()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--confdir", required=True)
    a = ap.parse_args()
    try:
        asyncio.run(main(a.host, a.port, a.confdir))
    except KeyboardInterrupt:
        sys.exit(0)

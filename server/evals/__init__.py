"""评测（内核第 10 项）：假模型服务器 + 题库 + 跑法。

  fakemodel.py   本机的假模型服务器：会说 OpenAI 兼容接口和 Claude 的接口（都能流式），还有 Gateway 的 llm-task，按剧本回放
  harness.py     题库怎么读、每一路（内核 + 某个模型 / OpenClaw 的 llm-task）怎么调、怎么判、出表
  suites/*.json  题库：一道题 = 输入 + 期望 + 怎么判 + 标准答案（gold）
  __main__.py    命令行：python -m evals init | estimate | offline | run | report | blind（在 server/ 下跑）

离线的部分（假模型回放标准答案、故意答错看判不判得出来）在 server/tests/test_evals.py，CI 跑；真模型那部分只手动跑，带花费上限。
这个包导入时不碰配置（config.py 一导入就读 MOUSSE_SERVER_CONFIG），命令行要先把它指到评测自己的目录。
"""

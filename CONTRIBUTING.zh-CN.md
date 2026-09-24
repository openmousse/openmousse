# 参与贡献

**中文** · [English](CONTRIBUTING.md)

谢谢你来看。OpenMousse 很小也很早，现在最有用的贡献按顺序是：

1. **安装报告。** 在你自己的机器上跑一遍 `install.sh`，把哪里坏了、哪里看不懂开成 issue。你踩到的每个坑都会变成文档里的一行。
2. **数据源适配器和功能包。** 你在某个软件里记训练、饮食、睡眠或别的什么，写一个适配器把它接到看板上，这正是 `packs/` 的用途。先开 issue 说清是什么软件、有什么接口，我们商量好形状再写。
3. **修 bug**，哪里都行。
4. 新功能：写代码前先开 issue，确认它符合设计（白板、数据源不锁死、记忆是主体，见 README）。

## 起环境

完整体验需要一个 OpenClaw，但每一块都能单独改。

```bash
git clone https://github.com/openmousse/openmousse && cd openmousse

# app（Expo）：typecheck + lint 是门禁；`npm run web` 在浏览器里跑，能连任意服务器
cd app && npm install && npm run typecheck && npm run lint

# server（FastAPI）：指一份临时配置，空实例也能起
cd ../server && pip install -r requirements.txt
MOUSSE_SERVER_CONFIG=/tmp/server.json python3 run.py        # 见 server.example.json 和 config.py

# tree（MCP 记忆）
cd ../tree && pip install -e . && MOUSSE_TREE_HOME=/tmp/tree mousse-tree init --name Dev
```

CI（`.github/workflows/ci.yml`）里写着到底检查什么：app 的 typecheck + lint、Python 的 pyflakes、`install.sh` 语法、空实例起服务的冒烟测试、tree 命令行。

## Pull Request

- fork → 开分支 → 向 `main` 发 PR。一个 PR 只做一件事。
- 写清怎么测的：app 是上面的命令加你点过什么，server 是打了哪些接口。
- 双语文档：改了 README 描述的行为，`README.md` 和 `README.zh-CN.md` 一起改（翻得糙没关系，我们会润）。
- 代码、文档、截图、日志里不能有个人数据、令牌、地址。身份文件（`app.local*.json`、`~/.openmousse/*`）永远不进 git。
- 代码注释现在多是中文，新写的用你顺手的语言。commit message 用英文。

## PR 会按这几条设计规则看

- **白板**：代码里不带任何具体某个人的生活。领域功能放 `packs/`。
- **数据源不锁死**：pack 只声明需要什么数据，从哪来是用户的选择（有 MCP 的软件、适配器脚本、或者对话里记）。
- **记忆是主体**：关于用户的事实进世界树，不进某个 Agent 的私有笔记。
- **app 是壳**：没有示例数据、不冒充回复；连不上服务器就说连不上。

## 标签

`good first issue` 小、独立、描述得足够清楚不用问就能开工 · `pack` 新功能包 · `data-source` 某个软件或接口的适配器 · `app` / `server` / `tree` 属于哪一块。

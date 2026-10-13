---
name: page
description: 页面：积木画不了的互动和可视化（能拖、能实时计算的图，自定义的图表，一次性的分析页，学习用的互动演示），你写一份网页，app 放进盒子里显示，数据从你自己的表实时读。用户说「做成一页」「做个页面」「可视化一下」，或者你要交的东西积木画不出来时用。命令：python3 ~/.openmousse/repo/server/page_ctl.py。
---

# 页面：盒子里的网页

> **有 OpenMousse 的 MCP 工具时（openmousse__page）就不用 shell**：page_ctl.py → page。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），HTML 放 input、args 里写 `--file -`；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这个工具就照原样在 shell 里跑命令。

## 先选：积木还是页面

- **积木**（board skill）：固定格式、长期放在看板上、要原生手感的：数字、进度、趋势、列表、清单，集合块的表格 / 看板 / 日历 / 时间线 / 图表。大多数可视化用积木。
- **页面**：积木表达不了的：要拖、要算（模拟器、计算器、参数可调的图）、积木没有的图（甘特图、漏斗、关系图）、一次性的分析报告、学习用的互动演示。
- 积木能做的别做成页面。拿不准就问用户想长期放在看板上，还是看一次。

## 怎么写

- 交一段 **HTML 片段**：`<style>`、内容、`<script>`，不写 `<html>` `<head>` `<body>`。服务器会在最前面加上安全规则、主题变量和桥。
- **手机宽度优先**（390px）：单列，按钮至少 44px 高，宽表格放进能横向滚动的盒子里。
- **颜色只用 CSS 变量**（跟着 app 的深浅色）：`--bg` `--card` `--card-2` `--text` `--text-2` `--text-3` `--line` `--accent` `--accent-fill` `--on-accent` `--data` `--good` `--warn` `--bad`（还有 `--good-soft` 这类浅底），图表系列 `--c1`…`--c6`（按顺序用，不循环；两个系列以上要图例），`--radius`、`--font`。现成的类：`.card` `.muted` `.faint` `.row` `.grid` `.chip` `.num`、`button.primary`。
- **库**只能从 `cdn.jsdelivr.net` / `cdnjs.cloudflare.com` / `unpkg.com` 加载，写死版本号（`d3@7.9.0`）；字体可用 Google Fonts。简单的图用 SVG 自己画，不用库。
- **盒子里没有**：联网（fetch / XMLHttpRequest / WebSocket）、localStorage / cookie、弹窗、表单提交、嵌别的页面、外部图片（只能 data: 或 SVG）。状态放在变量里。页面试图跳到别的网址会被停掉。
- 表里的文字（岗位描述、邮件标题这类可能来自外面的）一律用 `textContent` / `createElement` 放进页面，**别拼进 innerHTML**。
- 文案照 app 的规矩：专业、简短、陈述句；加载中、没有数据、出错都要有一行字。
- 大小不超过 256 KB；数据别写进页面，用下面的 `mousse.data` 读。

## 页面里能用的：window.mousse

- `mousse.data('名字')` → Promise：`{rows, fields, total, truncated, title}`。`rows` 每行是表里的字段，加上 `id`、`_created`、`_updated`。
  - 能读什么在发布时用 `--data 名字=查询` 声明。查询和看板积木一样（`from` / `where` / `sort` / `limit`，最多 1000 行），只能读你自己的表和只读的 `health:*`，只读。
  - 用户第一次打开时会被问同不同意（列出表和字段）。不同意时 `err.code === 'not_granted'`，页面显示一行说明；`stale` = 页面刚更新过，提示重新打开。
- `mousse.prompt('一句话')`：把这句话放进你这个对话的输入框，用户看过、按发送才发。用在「在对话里问：……」这类按钮上，话里带上页面名字。
- `mousse.page`：`{id, title, version, agent, today, lang, theme}`。算「本周」「几天后截止」用 `today`（用户时区的日期），别用 `new Date()` 的日期。

## 命令

```bash
P="python3 $HOME/.openmousse/repo/server/page_ctl.py"   # 在自己的工作区里跑，不用写 --agent
$P check --file page.html --data jobs='{"from": "job_postings", "where": [["decision", "empty"]], "sort": ["deadline"], "limit": 500}'
$P publish --name jobs --title 可投岗位 --summary "岗位雷达中尚未决定的岗位，按截止日期排序" --file page.html \
   --data jobs='{"from": "job_postings", "where": [["decision", "empty"]], "sort": ["deadline"], "limit": 500}' --note "第一版"
$P data jobs jobs           # 这份数据现在读出来什么（前 5 行）
$P get jobs > page.html     # 改之前先取回来；改完用同一个 --name 再 publish = 新的一版
$P list | show jobs | rm jobs
```

- 先 `check` 再 `publish`；有错误（外部地址、脚本语法、表或字段写错）不会存。
- 发布后在回复里用一句话说做了什么、让用户看什么。页面卡片会自动挂在这条回复下面，不用贴链接。
- 用户要改：`get` → 改 → 用同一个 `name` 再 `publish`（新的一版；要读的数据如果变了，用户会被重新问一次）。
- 用户想把它放到看板上：页面底下有「钉到看板」；用户在对话里让你加，就用 board 工具加一块 `{"id": "page-jobs", "type": "page", "title": "可投岗位", "page": "jobs"}`。

## 骨架

```html
<style>.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}</style>
<h1>可投岗位</h1>
<p class="muted" id="sub">正在加载…</p>
<div class="kpis" id="kpis"></div>
<button class="primary" id="ask" type="button">在对话里问：这周先投哪个？</button>
<script>
mousse.data('jobs').then(function (d) {
  document.getElementById('sub').textContent = '共 ' + d.total + ' 个岗位待决定';
  // 用 createElement + textContent 填内容
}, function (e) {
  document.getElementById('sub').textContent = e.code === 'not_granted' ? '未获得读取权限。' : '无法加载数据：' + e.message;
});
document.getElementById('ask').onclick = function () {
  mousse.prompt('看了「可投岗位」页面：这周先投哪几个？');
};
</script>
```

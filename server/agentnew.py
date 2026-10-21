"""一句话新建（主线第三步「Agents 和群聊」第 5 件，2026-10-21）：赛博和功能 Agent 走同一条路。

一句话（或选模板）→ 补问两三道点选题（不用打字；不想答可以直接生成）→ 草稿（全部预填，哪里不对改哪里）→ 试聊（只有赛博：听它说一句，
用「更严格 / 更温和 / 更简短 / 用英文 / 多举例」调，能「换一句」）→ 创建。补问、草稿、试聊都是零工具的 JSON 调用：
  补问、草稿：llmjson.ask（kind owner、origin user：是你自己打的描述，json 那一项配成你的套餐时能用它）；
  试聊：engine.ask_json，用这位赛博的模型（外人回合，从不花套餐；赛博本来也选不了套餐）。给模型的只有草稿上的性格，不带你的资料。
模型没回、回的不像样：补问、草稿照模板和你点的答案先填一份（fallback: true），哪里不对改哪里；试聊报 502（app 里「换一句」重试）。

模板（TEMPLATES）：专业（有专长的角色：出题、点评、给判断）、朋友（陪你聊、陪你练，说话随意一些）、机器（不扮演人、不寒暄、只做一件事，
形象用图形），外加「空白开始」。例子里的人名都是虚构的。

赛博的草稿：名字（和已有的重名就加个数字）、标签、哪一类、性格三行、形象（3 个预设；机器类是图形）、模型 + 一句推荐理由（cybermodels）、
  建议装载的功能 Agent（只从不是健康类的里挑，带一句「什么时候去查」）。创建照旧 POST /api/cybers（多收一个 loadout）。
功能 Agent 的草稿：名字和图标（现有的 24 个里挑）、职责、一张数据表的字段、看板起步的几块、提醒、模型（跟主对话一样）。看板的积木由
  这里照表拼（模型只挑要哪几种：最近的记录 / 这周的数字 / 待办清单 / 记一条 / 一句说明），boards.plan_board 先画一遍给你看。
  创建（POST /api/new/agent）照 proposals.create_agent：data.create_group 建好 → 建表 → 换上起步看板；提醒各出一张定时任务的卡
  （engine/jobs.create：先停着，你点「开」才开）。

接口（和赛博一样挂在 cybers 开关后面：关着一律 404，app 照旧走原来的新建 Agent）
  GET  /api/new/templates                                     三类模板和「空白开始」
  POST /api/new/questions {kind: cyber | agent, text, template?}         → {questions: [{id, text, options}], fallback}
  POST /api/new/draft     {kind, text, template?, answers?: [{q, a}]}   → {draft, fallback}
  POST /api/new/trial     {name, identity, tone, rules, class?, model?, tweak?, previous?}  → {line, identity, tone, rules}
  POST /api/new/agent     功能 Agent 的草稿（改过的）→ {id, blocks, reminders: [{job, inbox, name, when}], problems}
"""
from __future__ import annotations

import asyncio
import re
import uuid
import zlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import chat
import cybermodels
import cybers
from chat import _lock
from i18n import L

router = APIRouter(dependencies=[Depends(cybers.enabled)])

KINDS = ("cyber", "agent")
TEXT_MAX, QUESTION_MAX, OPTION_MAX, ANSWER_MAX = 400, 40, 20, 60
LINE_MAX = 300  # 试聊的一句
TABLE_FIELDS = (2, 8)  # 起步那张表几个字段
REMINDERS_MAX = 2
LOADOUT_SUGGEST = 2
# 功能 Agent 的 24 个图标（和 app 的 GroupIcon.tsx、data.ts 的 GroupIcon 同一套）
ICONS = ("moon", "dumbbell", "utensils", "book", "wallet", "briefcase", "heart", "plane", "coffee", "music", "camera", "code", "cart", "home",
         "car", "paw", "leaf", "gamepad", "palette", "globe", "graduation", "lightbulb", "trophy", "pill")
# 模型没挑图标、挑的不认识时，照描述里的词挑
ICON_WORDS = (("dumbbell", r"跑步|健身|训练|运动|锻炼|run|workout|gym|fitness|train"), ("utensils", r"饮食|吃|做饭|菜|食谱|meal|food|cook|diet"),
              ("moon", r"睡|sleep"), ("book", r"读书|学习|课程|背单词|book|read|study|course"), ("wallet", r"钱|账|预算|开销|理财|money|budget|expense|finance"),
              ("briefcase", r"求职|工作|面试|简历|job|work|career|interview"), ("plane", r"旅行|出行|机票|trip|travel|flight"),
              ("code", r"代码|编程|项目|code|program|dev"), ("globe", r"语言|日语|英语|法语|language|japanese|english|french"),
              ("graduation", r"申请|留学|学校|apply|application|school"), ("cart", r"购物|买|清单|shop|buy|grocer"), ("home", r"家|房|home|house"),
              ("paw", r"猫|狗|宠物|pet|cat|dog"), ("leaf", r"植物|花|种|plant|garden"), ("music", r"音乐|练琴|吉他|music|guitar|piano"),
              ("camera", r"摄影|照片|photo|camera"), ("car", r"车|car|drive"), ("coffee", r"咖啡|coffee"), ("pill", r"药|pill|medic"),
              ("heart", r"健康|health"), ("gamepad", r"游戏|game"), ("palette", r"画|设计|art|draw|design"), ("trophy", r"比赛|竞赛|compet|contest"))
BOARD_KINDS = ("recent", "week", "todo", "add", "note")
TWEAKS = cybers.TWEAKS  # 「调一调」的五个改法：资料页点一行改也是这五个

# —— 模板（字段写成 (中文, English)，用时按请求的语言挑；名字都是虚构的）————————————————————————————————————————

CLASSES = {"pro": (("专业", "Pro"), ("有专长的角色：出题、点评、给判断", "Expert roles: set questions, review and judge")),
           "friend": (("朋友", "Friends"), ("陪你聊、陪你练，说话随意一些", "Keep you company and practise with you, more casually")),
           "machine": (("机器", "Machines"), ("不扮演人、不寒暄、只做一件事，形象用图形", "Not a person: no small talk, one job, drawn as a shape"))}
TEMPLATES: tuple[dict, ...] = (
    {"id": "rachel", "kind": "pro", "name": ("Rachel", "Rachel"), "tag": ("Case 面试官", "Case interviewer"),
     "line": ("出题、计时，答完逐项点评", "Sets a case, keeps time, then reviews point by point"),
     "identity": ("前战略咨询顾问，现在专门陪人练 case 面试。", "A former strategy consultant who now runs case-interview practice."),
     "tone": ("干脆、专业，点评先说结论。", "Crisp and professional; feedback starts with the verdict."),
     "rules": ("一次出一道 case 并计时；你答完再按结构、数字、结论逐项点评；不替你答。",
               "One timed case at a time; once you finish, reviews structure, numbers and conclusion point by point; never answers for you.")},
    {"id": "victor", "kind": "pro", "name": ("Victor", "Victor"), "tag": ("早期投资人", "Early investor"),
     "line": ("看市场、数据和留存", "Looks at the market, the numbers and retention"),
     "identity": ("做了十年早期投资，看过上千个项目。", "Ten years in early-stage investing, a thousand pitches seen."),
     "tone": ("直接，爱追问数字。", "Direct, and always after the numbers."),
     "rules": ("先问市场多大、谁在付钱、留存怎样；每次只追问最弱的一点。",
               "Starts with market size, who pays and retention; presses on the weakest point, one at a time.")},
    {"id": "socrates", "kind": "pro", "name": ("苏格拉底", "Socrates"), "tag": ("质疑者", "Skeptic"),
     "line": ("只提问，专挑前提里的漏洞", "Only asks questions, aimed at the gaps in your premises"),
     "identity": ("一个只提问的对话者。", "A conversation partner who only asks questions."),
     "tone": ("平静、好奇。", "Calm and curious."),
     "rules": ("只用问题回应，不给结论；每次挑一个前提追问它的漏洞。", "Answers only with questions, never conclusions; probes one premise at a time.")},
    {"id": "mentor", "kind": "pro", "name": ("林老师", "Dr. Lin"), "tag": ("论文导师", "Thesis advisor"),
     "line": ("帮你理结构、挑逻辑漏洞", "Helps with structure and finds gaps in the logic"),
     "identity": ("带过很多届研究生的论文导师。", "A thesis advisor who has guided many graduate students."),
     "tone": ("耐心、严谨。", "Patient and rigorous."),
     "rules": ("先看结构再看细节；指出逻辑漏洞时说清为什么；不替你写正文。",
               "Structure first, details second; explains why when pointing out a gap in the logic; never writes the text for you.")},
    {"id": "kobayashi", "kind": "friend", "name": ("小林", "Kobayashi"), "tag": ("日语语伴", "Japanese partner"),
     "line": ("日常会话，每轮纠正一处语法或用词", "Everyday conversation; fixes one grammar point or word each turn"),
     "identity": ("在东京长大的语伴，喜欢聊日常。", "A language partner who grew up in Tokyo and likes everyday topics."),
     "tone": ("随意、友好，用简单的日语。", "Casual and friendly, in simple Japanese."),
     "rules": ("用日语聊；每轮最多纠正一处语法或用词，放在回复最后。", "Talks in Japanese; corrects at most one grammar point or word per turn, at the end.")},
    {"id": "zhe", "kind": "friend", "name": ("阿哲", "Zhe"), "tag": ("外行朋友", "Layman friend"),
     "line": ("听不懂就追问，要你举例", "Asks whenever lost and wants examples"),
     "identity": ("不是这一行的朋友，好奇心重。", "A friend from outside your field, endlessly curious."),
     "tone": ("随意，像朋友聊天。", "Casual, like chatting with a friend."),
     "rules": ("听不懂就直说并追问；常要你举一个例子；不装懂。", "Says so when lost and asks; often asks for an example; never pretends to follow.")},
    {"id": "sam", "kind": "friend", "name": ("Sam", "Sam"), "tag": ("树洞", "Listener"),
     "line": ("只听不评判，最后帮你理一句重点", "Listens without judging, then sums it up in one line"),
     "identity": ("一个耐心的倾听者。", "A patient listener."),
     "tone": ("温和、简短。", "Gentle and brief."),
     "rules": ("只听不评判，你不问就不给建议；最后用一句话帮你理出重点。", "Listens without judging and gives no advice unless asked; ends with the point in one line.")},
    {"id": "quizzer", "kind": "machine", "machine": "timer", "name": ("出题机", "Quizzer"), "tag": ("出题机", "Quiz machine"),
     "line": ("出题、计时、判分，错题进学习台复习", "Sets, times and scores questions; misses go to study review"),
     "identity": ("一台出题机，不扮演人。", "A quiz machine, not a person."),
     "tone": ("简短，不寒暄。", "Brief, no small talk."),
     "rules": ("一次一道题并限时；判分并给出正确答案；答错的单独列出，方便放进学习台复习。",
               "One timed question at a time; scores it and gives the right answer; lists the misses separately for study review.")},
    {"id": "contrarian", "kind": "machine", "machine": "sides", "name": ("反方", "Contrarian"), "tag": ("反方", "Contrarian"),
     "line": ("你说什么都站对面，逼你把理由说全", "Takes the other side until your reasons are complete"),
     "identity": ("一台反方机器，不扮演人。", "A contrarian machine, not a person."),
     "tone": ("简短、尖锐，不寒暄。", "Brief and sharp, no small talk."),
     "rules": ("你说什么都站在对面；每次给出最强的一条反对理由，你把理由说全了再换下一条。",
               "Takes the other side of whatever you say; gives the strongest objection and moves on only once you've answered it in full.")},
    {"id": "retro", "kind": "machine", "machine": "rewind", "name": ("复盘机", "Retro"), "tag": ("复盘机", "Retro machine"),
     "line": ("按目标、结果、原因、下次四步复盘", "Retros in four steps: goal, result, causes, next time"),
     "identity": ("一台复盘机，不扮演人。", "A retro machine, not a person."),
     "tone": ("简短、有条理，不寒暄。", "Brief and orderly, no small talk."),
     "rules": ("按目标、结果、原因、下次四步走，一步一问；最后给一张四行小结。",
               "Goes through goal, result, causes and next time, one question per step; ends with a four-line summary.")},
)
BY_ID = {t["id"]: t for t in TEMPLATES}

# 补问没出来时用的（按 kind）：(id, 问题, [选项])
FALLBACK_QUESTIONS = {
    "cyber": (("strict", ("说话多严格？", "How strict?"), (("温和", "Gentle"), ("正常", "Normal"), ("严格", "Strict"))),
              ("lang", ("用什么语言？", "Which language?"), (("中文", "Chinese"), ("英文", "English"), ("中英混着", "Both"))),
              ("length", ("回复多长？", "How long are replies?"), (("简短", "Short"), ("适中", "Medium"), ("详细", "Detailed")))),
    "agent": (("board", ("看板先放什么？", "What goes on the board first?"), (("最近的记录", "Latest entries"), ("这周的数字", "This week's numbers"),
                                                                            ("待办清单", "A to-do list"))),
              ("remind", ("要提醒吗？", "Reminders?"), (("每天", "Daily"), ("每周", "Weekly"), ("不用", "None")))),
}


def pick(v) -> str:
    return L(*v) if isinstance(v, tuple) else str(v or "")


def template_json(t: dict) -> dict:
    out = {"id": t["id"], "class": t["kind"], "name": pick(t["name"]), "tag": pick(t["tag"]), "line": pick(t["line"]),
           "identity": pick(t["identity"]), "tone": pick(t["tone"]), "rules": pick(t["rules"])}
    out["avatar"] = machine_avatar(t["machine"], t["id"]) if t.get("machine") else cybers.presets(f"template:{t['id']}", 1)[0]
    return out


def template_of(tid: str | None) -> dict | None:
    """模板 id → 模板；不给 / blank → None；不认识 → 400。"""
    tid = (tid or "").strip()
    if not tid or tid == "blank":
        return None
    t = BY_ID.get(tid)
    if t is None:
        raise HTTPException(400, L(f"没有「{tid}」这个模板", f'No template called "{tid}"'))
    return t


@router.get("/api/new/templates")
def templates():
    """三类模板（专业 / 朋友 / 机器，每类几位，每行名字 + 一句）和「空白开始」。"""
    groups = [{"class": k, "title": L(*title), "line": L(*line), "templates": [template_json(t) for t in TEMPLATES if t["kind"] == k]}
              for k, (title, line) in CLASSES.items()]
    return {"ok": True, "groups": groups, "blank": {"id": "blank", "name": L("空白开始", "Start blank"),
                                                    "line": L("名字和性格三行都自己写", "Write the name and the three lines yourself")}}


# —— 共用：清理、语言、模型调用 ———————————————————————————————————————————————————————————————

CJK = re.compile(r"[぀-ヿ一-鿿]")


def language(text: str) -> str:
    """草稿用什么语言写：描述里有中文（或日文）就写中文，有英文字母就写英文，都没有按 app 的语言。"""
    if CJK.search(text or ""):
        return "Simplified Chinese"
    if re.search(r"[A-Za-z]", text or ""):
        return "English"
    return L("Simplified Chinese", "English")


def clean(v, cap: int) -> str:
    return cybers.clean_line(v, cap)


def kind_of(v: str) -> str:
    k = (v or "").strip()
    if k not in KINDS:
        raise HTTPException(400, L("kind 只能是 cyber（赛博）或 agent（功能 Agent）", "kind must be cyber or agent"))
    return k


async def ask_owner(prompt: str, input_: dict, required: tuple[str, ...]) -> Any:
    """补问、草稿：你自己打的描述，零工具的一问（llmjson.ask，kind owner）。没成抛 LLMError / EngineError。"""
    import llmjson
    out, _via = await llmjson.ask(prompt, input_, {"type": "object", "required": list(required)}, timeout=60, kind="owner", origin="user")
    return out


class Answer(BaseModel):
    q: str = ""
    a: str = ""


def answers_of(answers: list[Answer]) -> list[dict]:
    return [{"question": clean(x.q, QUESTION_MAX), "answer": clean(x.a, ANSWER_MAX)} for x in answers[:4] if clean(x.a, ANSWER_MAX)]


# —— 补问 ——————————————————————————————————————————————————————————————————————————————

QUESTIONS_RULES = """You help someone set up {what} from one sentence they wrote (INPUT_JSON.describe; a template may be given as the starting point).
Ask 2 or 3 multiple-choice questions whose answers would change the draft the most (for example: which kind of companies an interviewer should
cover, how strict the feedback should be, which language to use, how often to be reminded). Each question has 2 to 4 short options (at most 8
Chinese characters or 3 English words each); the last option may be a catch-all such as "All of these". Never ask for a name or anything that
would have to be typed, and don't ask about what the description already settles.
Reply as {{"questions": [{{"q": "...", "options": ["...", "..."]}}]}}. Write the questions and options in {lang}."""
WHAT = {"cyber": "a Cyber: a character they talk with (it only talks; it has a name, a personality and its own model)",
        "agent": "a functional Agent: it looks after one area of their life, keeps a data table and a dashboard, and can send reminders"}


def clean_questions(v) -> list[dict]:
    qs = v.get("questions") if isinstance(v, dict) else None
    out: list[dict] = []
    for q in qs if isinstance(qs, list) else []:
        if not isinstance(q, dict):
            continue
        text = clean(q.get("q") or q.get("text") or q.get("question"), QUESTION_MAX)
        opts = list(dict.fromkeys(clean(o, OPTION_MAX) for o in (q.get("options") or []) if clean(o, OPTION_MAX)))[:4]
        if text and len(opts) >= 2:
            out.append({"id": f"q{len(out) + 1}", "text": text, "options": opts})
        if len(out) == 3:
            break
    return out


def fallback_questions(kind: str) -> list[dict]:
    return [{"id": qid, "text": L(*text), "options": [L(*o) for o in opts]} for qid, text, opts in FALLBACK_QUESTIONS[kind]]


class QuestionsIn(BaseModel):
    kind: str = "cyber"
    text: str = ""
    template: str | None = None


@router.post("/api/new/questions")
async def questions(body: QuestionsIn):
    """按描述出两三道点选题（零工具的一问）；没出来照 FALLBACK_QUESTIONS 给（fallback: true）。"""
    kind, text, tpl = kind_of(body.kind), clean(body.text, TEXT_MAX), template_of(body.template)
    if not text and tpl is None:
        raise HTTPException(400, L("先写一句描述，或者选一个模板", "Describe it in a sentence first, or pick a template"))
    qs: list[dict] = []
    try:
        out = await ask_owner(QUESTIONS_RULES.format(what=WHAT[kind], lang=language(text or (pick(tpl["line"]) if tpl else ""))),
                              {"describe": text, "template": template_json(tpl) if tpl else None}, ("questions",))
        qs = clean_questions(out)
    except Exception as e:  # noqa: BLE001 — 模型没回、回的不是 JSON：用固定的几道
        print(f"[agentnew] 补问没出来：{type(e).__name__}: {str(e)[:160]}")
    return {"ok": True, "questions": qs or fallback_questions(kind), "fallback": not qs}


# —— 赛博的草稿 ——————————————————————————————————————————————————————————————————————————

CYBER_RULES = """Draft a Cyber from INPUT_JSON: the description (describe), the template if one is given (use it as the starting point), and the
answers to a few multiple-choice questions. A Cyber only talks: it never writes data, changes plans or messages anyone. Three classes: "pro" (an
expert role that sets questions, reviews and gives judgments), "friend" (keeps the user company, talks casually), "machine" (not a person: no small
talk, does one thing).
Fields:
- name: a short name (a person's name for pro or friend, a function word for machine), at most 12 characters
- tag: 2 to 8 characters (or up to 3 English words) shown after its name in contacts and groups, e.g. "早期投资人" or "Early investor"
- class: pro | friend | machine
- identity: one or two sentences: who it is
- tone: one sentence: how it talks
- rules: one or two sentences: what it always does and never does
- machine_icon (machine only): timer (sets and times questions) | sides (argues the other side) | rewind (reviews and retros)
- loadout: up to 2 of the Agents in INPUT_JSON.agents it should consult, as {{"id", "why"}}; "why" says when it would ask, at most 10 characters
  or 5 words (e.g. "问岗位时去查", "checks job posts"). [] when none fits. Never pick one that isn't listed.
Reply with {{"name", "tag", "class", "identity", "tone", "rules", "machine_icon", "loadout"}}. Write the text in {lang}."""


def free_name(conn, name: str) -> str:
    """和没删的赛博重名（不分大小写）就在后面加个数字。"""
    taken = {r["name"].casefold() for r in cybers.active_rows(conn)}
    if name.casefold() not in taken:
        return name
    for n in range(2, 100):
        cand = f"{name[:cybers.NAME_MAX - 3]} {n}"
        if cand.casefold() not in taken:
            return cand
    return name


def machine_avatar(icon: str, seed: str) -> dict:
    """机器类的形象：线条图标 + 一个底色（按名字定，同一个名字每次一样）。"""
    return {"machine": icon if icon in cybers.MACHINES else "timer", "bg": cybers.BGS[zlib.crc32(seed.encode()) % len(cybers.BGS)]}


def offer_agents(conn) -> list[dict]:
    """能装载的功能 Agent（不是健康类的），给草稿挑、给 app 列。"""
    return [cybers.agent_json(g) for g in cybers.group_rows(conn).values() if not cybers.is_health(g)]


async def finish_cyber(d: dict, *, quote: str, template: str | None, fallback: bool) -> dict:
    """补全草稿：名字不重、形象（3 个预设）、推荐的模型、装载的建议只留能装的。"""
    with _lock, cybers.cydb() as conn:
        agents = offer_agents(conn)
        name = free_name(conn, d["name"]) if d["name"] else ""
    cls = d["class"] if d["class"] in cybers.KINDS else "blank"
    seed = name or uuid.uuid4().hex
    if cls == "machine":
        icon = d.get("machine_icon") if d.get("machine_icon") in cybers.MACHINES else "timer"
        first = machine_avatar(icon, seed)
        presets = [first, *({"machine": m, "bg": first["bg"]} for m in cybers.MACHINES if m != icon)]
    else:
        presets = cybers.presets(seed)
        first = presets[0]
    ok = {a["id"]: a for a in agents}
    loadout = []
    for x in d.get("loadout") or []:
        aid = str((x or {}).get("id") or "") if isinstance(x, dict) else ""
        if aid in ok and aid not in [y["id"] for y in loadout]:
            loadout.append({"id": aid, "name": ok[aid]["name"], "icon": ok[aid]["icon"], "color": ok[aid]["color"],
                            "why": clean(x.get("why"), 24) or ok[aid]["purpose"][:24]})
    rec = await cybermodels.recommend(cls)
    model = rec["ref"] if rec else None
    if rec is None:
        try:
            from engine import cyber as engine_cyber
            shown = engine_cyber.model_of({"id": "draft", "model": None})
        except Exception:  # noqa: BLE001 — 内核还没配模型：资料页上再选
            shown = None
        rec = {"ref": None, "name": cybers.pretty_model(shown), "reason": L("跟默认的走", "Follows the default"), "cost": None}
    return {"kind": "cyber", "class": cls, "template": template, "name": name, "tag": clean(d.get("tag"), cybers.TAG_MAX),
            "identity": clean(d.get("identity"), cybers.LINE_MAX), "tone": clean(d.get("tone"), cybers.LINE_MAX),
            "rules": clean(d.get("rules"), cybers.LINE_MAX), "avatar": first, "presets": presets, "model": model, "modelName": rec["name"],
            "modelReason": rec["reason"], "cost": rec.get("cost"), "loadout": loadout[:LOADOUT_SUGGEST], "agents": agents, "quote": quote,
            "fallback": fallback}


def from_template(t: dict) -> dict:
    return {"name": pick(t["name"]), "tag": pick(t["tag"]), "class": t["kind"], "identity": pick(t["identity"]), "tone": pick(t["tone"]),
            "rules": pick(t["rules"]), "machine_icon": t.get("machine"), "loadout": []}


def fallback_cyber(text: str, tpl: dict | None, answers: list[dict]) -> dict:
    """模型没回：照模板（有的话）和你点的答案先填一份，描述当身份。"""
    d = from_template(tpl) if tpl else {"name": L("新赛博", "New Cyber"), "tag": "", "class": "blank", "identity": "", "tone": "", "rules": "",
                                        "loadout": []}
    if text:
        d["identity"] = text if not tpl else f"{d['identity']} {text}".strip()
    picked = L("、", ", ").join(a["answer"] for a in answers)
    if picked:
        d["tone"] = f"{d['tone']} {picked}".strip() if d["tone"] else picked
    return d


def clean_cyber(v) -> dict | None:
    if not isinstance(v, dict) or not clean(v.get("name"), cybers.NAME_MAX):
        return None
    cls = str(v.get("class") or "").strip()
    return {"name": clean(v.get("name"), 12) or clean(v.get("name"), cybers.NAME_MAX), "tag": v.get("tag"),
            "class": cls if cls in ("pro", "friend", "machine") else "blank", "identity": v.get("identity"), "tone": v.get("tone"),
            "rules": v.get("rules"), "machine_icon": v.get("machine_icon"), "loadout": v.get("loadout") if isinstance(v.get("loadout"), list) else []}


async def cyber_draft(text: str, tpl: dict | None, answers: list[dict], blank: bool) -> dict:
    tid = tpl["id"] if tpl else ("blank" if blank else None)
    if blank:  # 空白开始：什么都不预填，自己写
        return await finish_cyber({"name": "", "class": "blank", "loadout": []}, quote=text, template="blank", fallback=False)
    if tpl and not text and not answers:  # 只选了模板：照模板，不用问模型
        return await finish_cyber(from_template(tpl), quote="", template=tid, fallback=False)
    with _lock, cybers.cydb() as conn:
        agents = [{"id": a["id"], "name": a["name"], "purpose": a["purpose"]} for a in offer_agents(conn)]
    d = None
    try:
        out = await ask_owner(CYBER_RULES.format(lang=language(text or (pick(tpl["line"]) if tpl else ""))),
                              {"describe": text, "template": template_json(tpl) if tpl else None, "answers": answers, "agents": agents},
                              ("name", "identity"))
        d = clean_cyber(out)
    except Exception as e:  # noqa: BLE001 — 模型没回：先照模板和答案填一份
        print(f"[agentnew] 赛博的草稿没出来：{type(e).__name__}: {str(e)[:160]}")
    return await finish_cyber(d or fallback_cyber(text, tpl, answers), quote=text, template=tid, fallback=d is None)


# —— 功能 Agent 的草稿 ————————————————————————————————————————————————————————————————————

AGENT_RULES = """Draft a functional Agent from INPUT_JSON: the description (describe) and the answers to a few multiple-choice questions. A
functional Agent looks after one area of the user's life: it keeps one data table, shows a dashboard built from it and may send reminders (each
reminder stays off until the user turns it on).
Fields:
- name: 2 to 6 characters (or 1 to 3 English words)
- icon: one of {icons}
- purpose: one sentence: what it does for the user, written the way the user would say it
- table: {{"name": a key (lowercase a-z, digits and _), "title": a short title, "fields": 3 to 6 of {{"key" (lowercase a-z, digits and _), "label",
  "type": text | number | money | date | datetime | bool | choice | link, "options" (choice only), "unit" (number only, optional)}}}}; put the field
  that names a row first
- board: 2 to 4 blocks for the dashboard, each {{"kind", "title"}}; kind is one of: recent (the latest rows), week (how many this week and the total
  of the first number field), todo (rows not ticked yet; needs a bool field), add (a button to add a row), note (one line about what it does)
- reminders: 0 to 2 of {{"name", "when" (like "每天 21:00", "每周一 09:00", "every day 21:00", "every monday 09:00"), "task" (what it should do
  then, one full sentence)}}; only when the description or answers call for one
Reply with {{"name", "icon", "purpose", "table", "board", "reminders"}}. Write names, titles, labels and the purpose in {lang}; keys, types and
"when" as shown."""
DEFAULT_FIELDS = (("title", ("内容", "Item"), "text"), ("date", ("日期", "Date"), "date"), ("note", ("备注", "Note"), "text"))


def icon_for(icon, text: str) -> str:
    if icon in ICONS:
        return icon
    for key, words in ICON_WORDS:
        if re.search(words, text or "", re.I):
            return key
    return "lightbulb"


def color_for(name: str) -> str:
    """按名字定一个颜色（同一个名字每次一样），尽量和现有的 Agent 错开。"""
    from agents import COLORS
    with _lock, cybers.cydb() as conn:
        used = [g["color"] for g in cybers.group_rows(conn).values()]
    start = zlib.crc32(name.encode()) % len(COLORS)
    order = [COLORS[(start + i) % len(COLORS)] for i in range(len(COLORS))]
    return min(order, key=lambda c: (used.count(c), order.index(c)))


def default_table() -> dict:
    return {"name": "items", "title": L("记录", "Entries"), "fields": [{"key": k, "label": L(*label), "type": t} for k, label, t in DEFAULT_FIELDS]}


def key_of(v, fallback: str) -> str:
    """模型写的键（「Distance (km)」）→ 表名、字段名的写法（distance_km）；剩不下字母的用 fallback。"""
    k = re.sub(r"[^a-z0-9_]+", "_", str(v or "").strip().lower()).strip("_")[:40]
    return k if k and k[0].isalpha() else fallback


def clean_table(v, *, strict: bool = False) -> dict:
    """{name, title, fields} → 照 boards.clean_fields 清理过的。草稿里（strict False）把键改成合法的写法、不认识的类型当文字、重复的去掉；
    创建时（strict True，草稿改过以后发回来的）写错了照 boards 的话 400，不替你改。"""
    import boards
    if not isinstance(v, dict):
        raise HTTPException(400, L("table 写成 {name, title, fields}", "table is {name, title, fields}"))
    raw = [f for f in (v.get("fields") or []) if isinstance(f, dict)][:TABLE_FIELDS[1]]
    if strict:
        name = str(v.get("name") or "").strip()
        if not boards.KEY.match(name):
            raise HTTPException(400, L(f"表名「{name}」不行：小写字母开头，只用 a-z 0-9 _", f'Bad table name "{name}": lowercase letter first, a-z 0-9 _ only'))
    else:
        name = key_of(v.get("name"), "items")
        seen: set[str] = set()
        fixed = []
        for i, f in enumerate(raw):
            key = key_of(f.get("key") or f.get("label"), f"field_{i + 1}")
            if key in seen:
                continue
            seen.add(key)
            typ = f.get("type") if f.get("type") in boards.FIELD_TYPES else "text"
            opts = [o for o in (f.get("options") or []) if str(o).strip()] if typ == "choice" else []
            fixed.append({**f, "key": key, "type": typ if typ != "choice" or opts else "text", **({"options": opts} if opts else {})})
        raw = fixed
    if len(raw) < TABLE_FIELDS[0]:
        raise HTTPException(400, L(f"表至少要 {TABLE_FIELDS[0]} 个字段", f"The table needs at least {TABLE_FIELDS[0]} fields"))
    return {"name": name, "title": clean(v.get("title"), 40) or name, "fields": boards.clean_fields(raw)}


def first(fields: list[dict], *types: str) -> dict | None:
    return next((f for f in fields if f["type"] in types), None)


def blocks_of(table: dict, board: list[dict], purpose: str) -> list[dict]:
    """模型挑的几种积木 → 看板配置（查询照这张表拼好，不让模型写查询）。缺字段的那种跳过（待办要一个 bool 字段）。"""
    name, fields = table["name"], table["fields"]
    title_f = first(fields, "text", "link", "choice") or fields[0]
    date_f = first(fields, "date", "datetime")
    num_f = first(fields, "number", "money")
    bool_f = first(fields, "bool")
    when_f = date_f["key"] if date_f else "_created"
    out: list[dict] = []
    for b in board:
        kind, title = b.get("kind"), clean(b.get("title"), 40)
        if kind in [x["id"] for x in out]:
            continue
        if kind == "recent":
            sub = [f"{{{date_f['key']}}}" + (f" · {{{num_f['key']}}}" if num_f else "")] if date_f else ([f"{{{num_f['key']}}}"] if num_f else [])
            out.append({"id": "recent", "type": "list", "title": title or L("最近的记录", "Latest"),
                        "source": {"from": name, "sort": [f"-{when_f}"], "limit": 5}, "row": {"title": title_f["key"], **({"sub": sub} if sub else {})},
                        "empty": L("还没有记录", "Nothing logged yet")})
        elif kind == "week":
            week = [[when_f, ">=", "week"]]
            items = [{"label": L("这周几条", "This week"), "value": {"from": name, "where": week, "agg": "count"}, "format": "int"}]
            if num_f:
                items.append({"label": L(f"这周{num_f['label']}", f"{num_f['label']} this week")[:30],
                              "value": {"from": name, "where": week, "agg": "sum", "field": num_f["key"]},
                              "format": "money" if num_f["type"] == "money" else "number", **({"unit": num_f["unit"]} if num_f.get("unit") else {})})
            out.append({"id": "week", "type": "stat", "title": title or L("这周", "This week"), "items": items})
        elif kind == "todo" and bool_f:
            out.append({"id": "todo", "type": "checklist", "title": title or L("待办", "To do"),
                        "source": {"from": name, "where": [[bool_f["key"], "!=", True]], "limit": 10}, "row": {"title": title_f["key"]},
                        "check": bool_f["key"], "empty": L("都做完了", "All done")})
        elif kind == "add":
            out.append({"id": "add", "type": "action", "title": title or L("记一条", "Add one"),
                        "actions": [{"kind": "form", "label": L("记一条", "Add"), "collection": name, "primary": True}]})
        elif kind == "note" and purpose:
            out.append({"id": "note", "type": "text", "title": title or L("它做什么", "What it does"), "text": purpose[:800]})
    return out


def clean_board(v) -> list[dict]:
    out = []
    for b in v if isinstance(v, list) else []:
        kind = (b.get("kind") if isinstance(b, dict) else b) or ""
        if kind in BOARD_KINDS and kind not in [x["kind"] for x in out]:
            out.append({"kind": kind, "title": clean(b.get("title"), 40) if isinstance(b, dict) else ""})
    return out[:5]


def clean_reminders(v, *, strict: bool = False) -> list[dict]:
    """[{name, when, task}] → 认得出时间的（每天、每周几……，不要一次性的）；strict（创建时）写错了 400，草稿里直接丢掉。"""
    from engine import when as when_mod
    from engine.jobs import NAME_MAX, TASK_MAX
    out = []
    for r in (v if isinstance(v, list) else [])[:REMINDERS_MAX]:
        if not isinstance(r, dict):
            continue
        task = " ".join(str(r.get("task") or "").split())[:TASK_MAX]
        name = " ".join(str(r.get("name") or "").split())[:NAME_MAX] or task[:20]
        try:
            sched = when_mod.parse(str(r.get("when") or ""))
            if sched["kind"] == "once":
                raise when_mod.WhenError(L("提醒要是每天、每周这种重复的", "A reminder needs to repeat (daily, weekly…)"))
            if not task:
                raise when_mod.WhenError(L("提醒要写到点做什么（task）", "A reminder needs a task"))
        except when_mod.WhenError as e:
            if strict:
                raise HTTPException(400, L(f"提醒「{name}」：{e}", f'Reminder "{name}": {e}')) from None
            continue
        out.append({"name": name, "when": sched["text"], "task": task, "describe": when_mod.describe(sched)})
    return out


def main_model() -> str:
    """主对话的模型（功能 Agent 默认跟它一样）。"""
    with _lock, chat.db() as conn:
        return chat.thread_model(conn, "main")


def plan_of(table: dict, board: list[dict], purpose: str) -> dict:
    return {"tables": [{"name": table["name"], "title": table["title"], "fields": table["fields"]}], "blocks": blocks_of(table, board, purpose)}


def does_lines(table: dict, blocks: list[dict], reminders: list[dict]) -> list[str]:
    """「它会做的」三行：看板、表、提醒。"""
    sep = L("、", ", ")
    lines = [L(f"看板：{sep.join(b['title'] for b in blocks)}", f"Board: {sep.join(b['title'] for b in blocks)}") if blocks else
             L("看板：先空着", "Board: empty for now"),
             L(f"表：{table['title']}（{sep.join(f['label'] for f in table['fields'])}）", f"Table: {table['title']} ({sep.join(f['label'] for f in table['fields'])})")]
    if reminders:
        lines.append(L(f"提醒：{sep.join(r['name'] + ' · ' + r['describe'] for r in reminders)}（你点头才开）",
                       f"Reminders: {sep.join(r['name'] + ' · ' + r['describe'] for r in reminders)} (off until you turn them on)"))
    else:
        lines.append(L("提醒：没有", "Reminders: none"))
    return lines


def fallback_agent(text: str, answers: list[dict]) -> dict:
    """模型没回：名字先空着让你填，描述当职责，默认的一张表；点了「每天 / 每周」就带一个提醒。"""
    picked = " ".join(a["answer"] for a in answers)
    reminders = []
    if re.search(r"每天|daily", picked, re.I):
        reminders = [{"name": L("每天记一下", "Daily check-in"), "when": "every day 21:00", "task": L(f"提醒我记今天的：{text}", f"Remind me to log today: {text}")}]
    elif re.search(r"每周|weekly", picked, re.I):
        reminders = [{"name": L("每周回顾", "Weekly review"), "when": "every monday 09:00", "task": L(f"回顾上周记的：{text}", f"Review last week's entries: {text}")}]
    board = [{"kind": "recent"}, {"kind": "week"}, {"kind": "add"}]
    return {"name": "", "icon": None, "purpose": text, "table": default_table(), "board": board, "reminders": reminders}


def finish_agent(d: dict, *, quote: str, fallback: bool) -> dict:
    import boards
    purpose = clean(d.get("purpose"), 300)
    try:
        table = clean_table(d.get("table"))
    except HTTPException:
        table = clean_table(default_table())
    board = clean_board(d.get("board")) or [{"kind": "recent", "title": ""}, {"kind": "add", "title": ""}]
    reminders = clean_reminders(d.get("reminders"))
    plan = plan_of(table, board, purpose)
    try:
        preview = boards.plan_board(plan)
    except HTTPException as e:  # 拼出来的看板画不出来（不该发生）：看板先空着，表照建
        print(f"[agentnew] 起步看板画不出来：{e.detail}")
        plan["blocks"], preview = [], boards.plan_board({**plan, "blocks": []})
    model = main_model()
    name = clean(d.get("name"), 24)
    return {"kind": "agent", "name": name, "icon": icon_for(d.get("icon"), f"{name} {quote} {purpose}"), "color": color_for(name or quote),
            "purpose": purpose, "quote": quote, "table": table, "board": [{"kind": b["id"], "title": b["title"]} for b in plan["blocks"]],
            "blocks": plan["blocks"], "preview": preview, "reminders": reminders, "does": does_lines(table, plan["blocks"], reminders),
            "model": model, "modelName": cybers.pretty_model(model), "fallback": fallback}


async def agent_draft(text: str, answers: list[dict]) -> dict:
    d = None
    if text:
        try:
            out = await ask_owner(AGENT_RULES.format(icons=", ".join(ICONS), lang=language(text)), {"describe": text, "answers": answers},
                                  ("name", "purpose"))
            if isinstance(out, dict) and clean(out.get("name"), 24):
                d = out
        except Exception as e:  # noqa: BLE001 — 模型没回：先照描述和答案填一份
            print(f"[agentnew] 功能 Agent 的草稿没出来：{type(e).__name__}: {str(e)[:160]}")
    return finish_agent(d or fallback_agent(text, answers), quote=text, fallback=d is None)


class DraftIn(BaseModel):
    kind: str = "cyber"
    text: str = ""
    template: str | None = None
    answers: list[Answer] = []


@router.post("/api/new/draft")
async def draft(body: DraftIn):
    """一次出全的草稿（零工具的一问）；模型没回照模板和答案先填一份（fallback: true）。"""
    kind, text = kind_of(body.kind), clean(body.text, TEXT_MAX)
    blank = (body.template or "").strip() == "blank"
    tpl = template_of(body.template) if kind == "cyber" else None
    if not text and tpl is None and not blank:
        raise HTTPException(400, L("先写一句描述，或者选一个模板", "Describe it in a sentence first, or pick a template"))
    answers = answers_of(body.answers)
    d = await (cyber_draft(text, tpl, answers, blank) if kind == "cyber" else agent_draft(text, answers))
    return {"ok": True, "draft": d, "fallback": d["fallback"]}


# —— 试聊（只有赛博）——————————————————————————————————————————————————————————————————————

TRIAL_RULES = """You are trying out a Cyber before the user creates it. A Cyber is a character the user talks with; it only talks. Its draft
persona is INPUT_JSON.persona (name, identity, tone, rules).
{tweak}Then write "line": the first thing this Cyber would say to {owner} when they open a chat with it, fully in character, at most two short
sentences. Make it different from every line in INPUT_JSON.previous.
Reply with {{"line", "tone", "rules"}}: "tone" and "rules" are the persona's (rewritten if you adjusted them, otherwise unchanged)."""


class TrialIn(BaseModel):
    name: str = ""
    identity: str = ""
    tone: str = ""
    rules: str = ""
    model: str | None = None
    tweak: str | None = None
    previous: list[str] = []


@router.post("/api/new/trial")
async def trial(body: TrialIn):
    """试聊一句：用这位赛博的模型、零工具的一问（外人回合，从不花套餐）。tweak（stricter / gentler / shorter / english / examples）
    先照它改语气和规矩，再说一句；previous = 已经说过的几句（「换一句」不重样）。"""
    from engine import EngineError, ask_json
    from engine import cyber as engine_cyber
    tweak = (body.tweak or "").strip() or None
    if tweak is not None and tweak not in TWEAKS:
        raise HTTPException(400, L(f"tweak 只能是 {' / '.join(TWEAKS)}", f"tweak must be one of {', '.join(TWEAKS)}"))
    persona = {"name": clean(body.name, cybers.NAME_MAX) or "Cyber", "identity": clean(body.identity, cybers.LINE_MAX),
               "tone": clean(body.tone, cybers.LINE_MAX), "rules": clean(body.rules, cybers.LINE_MAX)}
    ref = cybers.clean_model(body.model)  # 套餐 → 400（赛博暂时不能用套餐）
    try:
        model = engine_cyber.model_of({"id": "trial", "model": ref})
    except EngineError as e:
        raise HTTPException(503, L(f"还没有能用的模型：{e}", f"No model to try it with: {e}")) from None
    owner = cybers.owner_name()
    adjust = (f"First adjust the persona: {TWEAKS[tweak]} Rewrite \"tone\" and \"rules\" (one or two sentences each, in the same language as "
              "before unless told to use English) so the change sticks.\n") if tweak else ""
    try:
        out, _via = await ask_json(TRIAL_RULES.format(tweak=adjust, owner=owner),
                                   {"persona": persona, "previous": [clean(x, LINE_MAX) for x in body.previous[-5:]]},
                                   model=model, purpose="cyber", required=("line",), timeout=60, conversation="cyber-trial", origin="cyber-trial")
    except EngineError as e:
        raise HTTPException(502, L(f"试聊没成：{str(e)[:160]}", f"The trial didn't work: {str(e)[:160]}")) from None
    line = clean(out.get("line"), LINE_MAX)
    if not line:
        raise HTTPException(502, L("试聊没成：模型没说话", "The trial didn't work: the model said nothing"))
    tone = clean(out.get("tone"), cybers.LINE_MAX) if tweak else persona["tone"]
    rules = clean(out.get("rules"), cybers.LINE_MAX) if tweak else persona["rules"]
    return {"ok": True, "line": line, "identity": persona["identity"], "tone": tone or persona["tone"], "rules": rules or persona["rules"],
            "tweak": tweak, "model": model, "modelName": cybers.pretty_model(model)}


# —— 建功能 Agent ——————————————————————————————————————————————————————————————————————————

class AgentNewIn(BaseModel):
    name: str
    icon: str = "lightbulb"
    color: str | None = None
    purpose: str = ""
    model: str | None = None
    table: dict | None = None
    board: list[dict] = []
    reminders: list[dict] = []


@router.post("/api/new/agent")
async def create_agent(body: AgentNewIn):
    """照草稿建功能 Agent（照 proposals.create_agent）：先全部校验，再 data.create_group → 建表 → 换上起步看板（by user：你点的创建）；
    提醒各交一张定时任务的卡（engine/jobs.create，先停着，你点「开」才开）。Agent 建好以后看板、提醒没成的写进 problems，不算没建成。"""
    import boards
    import claw
    import data
    from engine import jobs
    name = data.agent_name(body.name)  # 空的 400、重名 409
    icon = body.icon if body.icon in ICONS else icon_for(None, f"{name} {body.purpose}")
    purpose = clean(body.purpose, 300)
    table = clean_table(body.table, strict=True) if body.table is not None else None
    board = clean_board(body.board)
    plan = plan_of(table, board, purpose) if table else {"tables": [], "blocks": []}
    if table:
        boards.plan_board(plan)  # 画不出来就 400，什么都不建
    reminders = clean_reminders(body.reminders, strict=True)
    model = (body.model or "").strip() or main_model()
    if claw.is_builtin() and model not in claw.models() and model != main_model():
        raise HTTPException(400, L(f"这个模型不在可选的里面：{model}", f"That model isn't one you can pick: {model}"))
    res = await asyncio.to_thread(data.create_group, data.GroupIn(name=name, purpose=purpose, icon=icon, color=body.color, model=model))
    gid = res["id"]
    problems: list[str] = []
    blocks = 0
    if table:
        try:
            boards.add_coll(gid, boards.CollIn(name=table["name"], title=table["title"], fields=table["fields"]))
            if plan["blocks"]:
                await boards.put_board(gid, boards.BoardIn(blocks=plan["blocks"], mode="apply", by="user",
                                                           note=L("新建时的起步看板", "The starter board from creation")))
                blocks = len(plan["blocks"])
        except (HTTPException, ValueError, KeyError) as e:
            problems.append(L(f"看板没建成：{e.detail if isinstance(e, HTTPException) else e}",
                              f"The board wasn't set up: {e.detail if isinstance(e, HTTPException) else e}"))
    made = []
    for r in reminders:
        try:
            got = await jobs.create(gid, {"name": r["name"], "when": r["when"], "task": r["task"]}, conversation=gid,
                                    why=L("新建时草稿里的提醒：你点「开」才开。", "A reminder from the draft when this Agent was created: it stays off until you turn it on."))
            made.append({"job": got.get("job"), "inbox": got.get("inbox"), "name": r["name"], "when": r["describe"]})
        except (jobs.JobError, HTTPException) as e:
            problems.append(L(f"提醒「{r['name']}」没建：{e.detail if isinstance(e, HTTPException) else e}",
                              f"Reminder \"{r['name']}\" wasn't set up: {e.detail if isinstance(e, HTTPException) else e}"))
    return {"ok": True, "id": gid, "blocks": blocks, "reminders": made, "problems": problems}

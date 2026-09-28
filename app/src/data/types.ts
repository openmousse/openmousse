// app 里的数据类型。2026-09-23 起全部对应服务器上的真实来源（server/data.py 的表格写了每一项的真源）。

export type Billing = '订阅' | 'API' | '免费';
export type CostTier = '省' | '中' | '贵';

export interface ModelOption {
  id: string; // OpenClaw 的 provider/model
  name: string;
  short: string;
  billing: Billing;
  cost: CostTier;
  note: string;
  featured: boolean;
}

/** Agent 的图标（服务器认的 24 个 key）。老数据里别的写法由 GroupIcon.tsx 换成这些，不认识的显示成默认图标。 */
export type GroupIcon =
  | 'moon' | 'dumbbell' | 'utensils' | 'book' | 'wallet' | 'briefcase' | 'heart' | 'plane'
  | 'coffee' | 'music' | 'camera' | 'code' | 'cart' | 'home' | 'car' | 'paw'
  | 'leaf' | 'gamepad' | 'palette' | 'globe' | 'graduation' | 'lightbulb' | 'trophy' | 'pill';

/** Agent 的颜色。null（老服务器没有这一项）= 青。 */
export type AgentColor = 'cyan' | 'gold' | 'green' | 'purple' | 'pink' | 'orange';

export interface Group {
  id: string;
  name: string;
  icon: GroupIcon;
  color?: AgentColor | null;
  purpose: string;
  modelId: string;
  dashboard: 'fitness' | 'diet' | 'apply' | 'masters' | 'health' | 'none';
  lastLine: string;
}

/** 改 Agent（PATCH /api/groups/{id}）：只带改了的。改名字或职责，服务器会顺带改它自己的说明。 */
export interface GroupPatch { name?: string; icon?: GroupIcon; color?: AgentColor; purpose?: string; modelId?: string }

/** 老服务器的审批队列（/api/approvals）。新服务器走收件箱（InboxItem），这个只在 /api/inbox 不存在时兜底用。 */
export interface Approval {
  id: string;
  kind: string;
  groupId: string | null;
  action: string;
  detail: string;
  fields: { k: string; v: string }[];
  requestedAt: string;
}

/** 收件箱：要你点头的事。exec = OpenClaw 的执行命令审批（id 是 exec:<审批 id>），其余是 Agent 自己交上来的提案。 */
export type InboxKind = 'exec' | 'task' | 'write' | 'send' | 'spend' | 'schedule' | 'push' | 'skill' | 'agent' | 'block' | 'project' | 'code' | 'calendar' | 'other';
export type InboxStatus = 'pending' | 'approved' | 'rejected' | 'revising' | 'done' | 'failed' | 'withdrawn' | 'expired';
export type InboxAction = 'approve' | 'reject' | 'revise';
export interface InboxItem {
  id: string;
  kind: InboxKind;
  /** 'main' 或者 Agent（group）id */
  source: string;
  sourceName: string;
  /** 这件事是在哪个对话里提的 */
  thread: string;
  /** 提这件事的那条助手消息（app 里的消息 id 是 "db<messageId>"），对话里这张卡就显示在它下面。没有 = 按时间排进去 */
  messageId: number | null;
  title: string;
  why: string;
  /** 会改什么，一条一行 */
  changes: string[];
  /** 更多说明（Markdown），默认收起 */
  detail: string;
  /** 同意按钮上的字（比如「记上」），空 = 「同意」 */
  approveLabel: string;
  /** 只有 exec 有：命令、目录这类 */
  fields?: { k: string; v: string }[];
  /** 只有 project 有（server/projects.py）：开项目的提案内容（预览），开好 / 要归档的那个项目（「去看看」） */
  project?: InboxProjectInfo;
  /** 日结提案（server/proposals.py）：kind skill 的卡在 skill 字段、kind agent 的在 agent 字段，内容一样：给谁、这几次的原话、做法全文 / 职责 */
  skill?: InboxProposalInfo;
  agent?: InboxProposalInfo;
  status: InboxStatus;
  /** 「改一下」时写的意见 */
  note: string;
  /** 做完 / 没做成的结果 */
  result: string;
  level: 'ring' | 'quiet' | 'none';
  createdAt: string;
  decidedAt: string | null;
  /** 最后一次变化（报结果、跟进）的时间。老服务器没有 */
  updatedAt?: string;
  /** 提这件事的那天（服务器的逻辑日，04:00 为界）：「看原对话」打开那天的记录。老服务器没有 */
  day?: string;
  /** 「已处理」里点了「跟进」、在对话里接着说过这件事（server/inbox.py 的 follow_context）：最近一次的时间和那句话 */
  followedAt?: string | null;
  followNote?: string;
  /** app 自己加的：老服务器（/api/approvals）没有 ISO 时间，只有显示用的文字 */
  whenText?: string;
}
/** 这次打开 app 以来点过头的事：「今天」页上收成一行回执，留到换天。 */
export interface Receipt { item: InboxItem; day: string }

/** 未读：只列出 n > 0 的线程。last.id 是消息的数字 id（app 里的消息 id 是 "db<id>"）。 */
export interface UnreadThread { n: number; mine: number; last?: { id: number; text: string; ts: string; origin: string } }
export interface UnreadSummary { threads: Record<string, UnreadThread>; feedNew: string[]; inbox: number; badge: number }

/** 推送 / 小窗点开去哪。 */
export type PushTarget =
  | { type: 'thread'; thread: string }
  | { type: 'card'; id: string; thread?: string }
  | { type: 'inbox'; id: string; thread?: string }
  | { type: 'board'; agent: string; thread?: string }   // 某个 Agent 的看板（Agent 的提醒点开到这里）
  | { type: 'today' };

/** 对话附件。url 是服务器地址（/api/files/id），还没上传完的用本地 uri。 */
export interface Attachment { id: string; name: string; mime: string | null; size: number; kind: 'image' | 'doc' | 'audio' | 'video' | 'file'; url: string; chars?: number | null; note?: string | null; status?: string }
/** 选好还没发的文件。web 上有 File 对象；原生上是本地 uri。 */
export interface PendingFile { uri: string; name: string; mime: string; size: number; file?: File }
export type MessageBody = { type: 'text'; text: string; attachments?: Attachment[] };

export interface TaskStep {
  time: string;
  kind: 'start' | 'tool' | 'think' | 'check' | 'done';
  text: string;
}

/** 一轮 = 子会话里收到一条消息到回完。第一轮收到任务，之后每轮是一次修改意见。 */
export interface TaskRun {
  version: number;
  note?: string | null;
  brief?: string | null;
  startedAt: string;
  finishedAt?: string | null;
  tokens: number;
  status: 'running' | 'done' | 'failed';
  steps: TaskStep[];
  result?: { summary: string } | null;
}

export type TaskStatus = '进行中' | '完成' | '失败' | '已取消';

/** 派出去的活 = OpenClaw 的一个子会话（tasks.list 里 kind=subagent）。 */
export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  /** 谁派的：'main'、groupId、独立空间 id，或者别的会话 key */
  origin: string;
  sessionKey: string;
  modelId: string | null;
  createdAt: string;
  startedAt: string;
  finishedAt: string;
  summary: string;
  error?: string | null;
  toolUseCount: number;
  lastTool?: string | null;
  tokens: number;
  costUsd?: number | null;
  /** 做了多久（进行中 = 到现在），分钟。老服务器没有 */
  minutes?: number | null;
  /** 到单个任务的时间上限被停了 */
  timedOut?: boolean;
  /** 进行中：正在做哪一步（「在读 L2.pdf」） */
  step?: string;
  /** 派的时间（毫秒），分「今天」和「更早」用 */
  createdMs?: number;
  /** 详情页才有 */
  brief?: string;
  runs?: TaskRun[];
}

/** 后台任务的额度：今天（04:00 起）派了几个、上限、单个最长几分钟。today / left 为 null = 服务器读不到任务台账。 */
export interface TaskQuota { today: number | null; running: number | null; limit: number; left: number | null; maxMinutes: number; tokens?: number | null }

/**
 * 对话里的转交卡：主对话把问题转给了某个 Agent。running 在问 → done 答完 / error 出错；busy 那边正忙，没转过去；lost 服务器重启过，没等到。
 * messageId：挂在哪条回复下面（"db<messageId>"）；那条回复还没结束时是 null。relayId：转过去的那一条在 Agent 对话里的 id。
 */
export interface HandoffCard {
  kind: 'handoff';
  id: string;
  thread: string | null;
  messageId: number | null;
  createdAt: string;
  status: 'running' | 'done' | 'error' | 'busy' | 'lost';
  to: string;
  toName: string;
  from: string;
  fromName: string;
  question: string;
  seconds: number | null;
  relayId: string | null;
  replyId: string | null;
  error?: string | null;
}

/** 对话里的任务卡：这条回复派出去的后台任务（OpenClaw 子会话）。round ≥ 2 = 「改一下」过，roundStatus / note / roundResult 是最近一轮的。 */
export interface TaskCardInfo {
  kind: 'task';
  id: string;
  thread: string | null;
  messageId: number | null;
  createdAt: string | null;
  status: TaskStatus;
  timedOut: boolean;
  title: string;
  /** 要交什么（任务正文里「要交：」那几行） */
  deliverable: string[];
  modelId: string | null;
  minutes: number;
  startedAt: string | null;
  finishedAt: string | null;
  tools: number;
  step: string;
  /** 第一轮的结果（Markdown，服务器截到 1200 字） */
  result: string;
  error?: string | null;
  round: number;
  roundStatus: 'running' | 'done' | 'failed' | null;
  note: string | null;
  roundResult: string | null;
  tokens: number | null;
  limitMinutes: number | null;
  /** 今天第几个派的 */
  seq?: number | null;
  dailyLimit?: number | null;
}

/**
 * 对话里的日程卡：Agent 在这次回复里改了日程或「要记得的」（server/schedule.py）。status：done 改了 / undone 撤销了。
 * area：schedule 日程 / remember 要记得的（打勾、改邮件条目）。logId 给撤销用。
 */
export interface ScheduleChangeCard {
  kind: 'schedule';
  id: string;
  logId: number;
  thread: string | null;
  messageId: number | null;
  createdAt: string;
  status: 'done' | 'undone';
  action: string;
  actor: string;
  area: 'schedule' | 'remember';
  title: string;
  summary: string;
}

export type ChatCard = HandoffCard | TaskCardInfo | ScheduleChangeCard | ProjectChangeCard;
/** 一个对话里的卡片（GET /api/chat/cards）：它自己转出去、派出去的，加上别的对话转给它的（incoming）。 */
export interface ThreadCards { cards: ChatCard[]; incoming: HandoffCard[] }

/** 收件箱里 kind=project 的卡多带的：open 开项目（提案里的目标、截止、已定的、下一步）/ archive 归档；project = 开好的或要归档的那个。 */
/** 日结提案的预览：它在哪几次对话里看出来的（依据），skill 给哪些 Agent、全文（去掉开头的 frontmatter），Agent 管什么。 */
export interface InboxProposalInfo {
  id: string;
  name: string;
  evidence: { date: string; thread: string; quote: string }[];
  description?: string;
  markdown?: string;
  agents?: { id: string; name: string }[];
  purpose?: string;
}
export interface InboxProjectInfo {
  action: 'open' | 'archive';
  goal?: string;
  deadlines?: string[];
  decisions?: string[];
  steps?: string[];
  project?: { id: string; title: string };
}

/**
 * 对话里的项目小卡：Agent 在这次回复里改了项目卡（加了下一步、记了已定的、更新进度、开了项目、写了结论）。
 * 截止的增删改和打勾是日程层的改动，出的是日程卡。undoable = 能撤销（开项目、写结论不能）。
 */
export interface ProjectChangeCard {
  kind: 'project';
  id: string;
  logId: number;
  thread: string | null;
  messageId: number | null;
  createdAt: string;
  status: 'done' | 'undone';
  action: string;
  actor: string;
  project: string;
  projectTitle: string;
  title: string;
  summary: string;
  undoable: boolean;
}

/** 项目卡上最近的一个截止（侧栏那一行、卡片收起时那一行）。left = 还剩几天（过了是负数）。 */
export interface ProjectNext { id: string; title: string; date: string; start: string; left: number | null }

/** 项目卡上的截止：自己的（own，进日程层，id 是 item:…）或挂上来的作业、邮件里的事、求职 ddl（id 是它原来的 id）。 */
export interface ProjectDeadline {
  id: string;
  title: string;
  date: string | null;
  start: string;
  allDay: boolean;
  done: boolean;
  past: boolean;
  /** 来源小标（课程缩写、邮箱）；自己的不写 */
  badge: string;
  origin: ScheduleEntry['origin'];
  /** 原文链接（作业页、邮件） */
  link: string | null;
  left: number | null;
  own: boolean;
  /** 源头没了（作业交了、邮件条目过期清掉）：按挂上时的快照显示 */
  gone: boolean;
  /** 挂上来的那条在项目卡上的 id（拿掉时用） */
  linkId: string | null;
}

export interface ProjectItem { id: string; kind: 'step' | 'decision'; text: string; done: boolean; doneAt: string | null; by: string; createdAt: string }
/** 结论：做成了、定过的、下次记得的、存到了哪。 */
export interface ProjectSummary { done: string; decided: string[]; learned: string; saved: string }

/** 一张项目卡（GET /api/projects/{id}）。 */
export interface ProjectCard {
  id: string;
  title: string;
  goal: string;
  progress: string;
  progressAt: string | null;
  summary: ProjectSummary | null;
  summaryAt: string | null;
  archived: boolean;
  archivedAt: string | null;
  /** 归档了、它在写结论 */
  closing: boolean;
  rev: number;
  createdAt: string;
  deadlines: ProjectDeadline[];
  steps: ProjectItem[];
  decisions: ProjectItem[];
  next: ProjectNext | null;
  stepsLeft: number;
  tasks?: { available: boolean; running: number; done: number; items: { id: string; title: string; status: string }[] };
}

/** 项目（以前叫独立空间）：持续几天到几周、有目标和截止的事，做完归档。线程 id 就是项目 id。 */
export interface SideChat {
  id: string;
  title: string;
  purpose: string;
  modelId: string;
  lastLine: string;
  createdAt: string;
  /** 最近一次活动的时间戳（毫秒）。侧栏按它倒序排，折叠掉的永远是最久没碰的。 */
  updatedAt: number;
  /** 归档：从侧栏隐藏，可以恢复。 */
  archived?: boolean;
  /** 项目卡的摘要（老服务器没有）：目标、最近的截止、还剩几件下一步、有没有结论、在写结论 */
  goal?: string;
  next?: ProjectNext | null;
  stepsLeft?: number;
  hasSummary?: boolean;
  archivedAt?: string | null;
  closing?: boolean;
}

export interface Message {
  id: string;
  /** auto：系统自动触发的一条（建议卡实时更新），显示成一行灰字 */
  role: 'user' | 'grava' | 'auto';
  body: MessageBody;
  time: string;
  /** 实际回答的模型 */
  modelId?: string;
  /** 请求的是这个模型，但回退链换成了 modelId */
  fallbackFrom?: string;
  /** 这一条没拿到回复时的原因 */
  error?: string;
  /** 回复进行中发的，还在排队（服务器 status queued）：这条回完和排着的一起发给它 */
  queued?: boolean;
  /** 回复进行中发的、插进了正在跑的那一轮（服务器 status steered，走 Gateway 对话通道时）：它做到下一步就看到 */
  steered?: boolean;
  /** 长按「引用」着发的：引的是这个对话里哪条（app 在气泡上面显示原话，点了跳回去） */
  replyTo?: { id: string; role: 'user' | 'grava'; text: string };
}

/** Grava 的建议（起床报告、主动提醒，第 8 步起才有）。 */
/** Grava 出的三餐建议卡（kind = meal_plan 的 data），格式见 workspace skills/diet/SKILL.md。 */
export interface MealPlanItem { name: string; amount?: number | string | null; unit?: string | null; kcal?: number | null; protein?: number | null }
export interface MealPlanMeal { label: string; time?: string | null; items: MealPlanItem[]; kcal?: number | null; protein?: number | null; carb?: number | null; fat?: number | null; note?: string | null }
export interface MealPlan { meals: MealPlanMeal[]; totals?: { kcal?: number | null; protein?: number | null; carb?: number | null; fat?: number | null } | null; vs_target?: string | null; why?: string | null; shopping?: string[] | null }

/** 训练建议卡（kind = training_plan 的 data），格式见 workspace skills/training/SKILL.md。 */
export interface TrainingPlan { decision: string; session?: string | null; intensity?: string | null; time?: string | null; duration_min?: number | null; why?: string | null; focus?: string[] | null; cautions?: string[] | null }

export interface FeedItem {
  id: string;
  groupId: string | null;
  title: string;
  body: string;
  cta: string;
  time: string;
  kind?: string | null;
  data?: MealPlan | TrainingPlan | null;
  createdAt?: string;
  /** 看过没有（任何一台设备上看过都算）。老服务器没有这一项，当作看过。 */
  seen?: boolean;
}

/** 日志（L4 journal）：感受、想法、决定、记录。Grava 在对话里记。 */
export interface JournalEntry { id: string; ts: string; date: string; time: string; groupId: string | null; kind: 'feeling' | 'thought' | 'decision' | 'note'; text: string; tags: string[]; context: string | null; source: string }

/** 求职与申请跟踪（L4 applications）。 */
export interface Application {
  id: string; kind: 'job' | 'masters' | 'fellowship' | 'ra' | 'other'; org: string; role: string; deadline: string | null; daysLeft: number | null;
  status: 'planned' | 'in_progress' | 'submitted' | 'interview' | 'offer' | 'rejected' | 'closed'; progress: number; nextStep: string | null; notes: string | null;
  link: string | null; materials: string[]; updatedAt: string;
}

/** 接下来会自动做的事：OpenClaw 定时任务或者系统定时器。 */
export interface UpcomingTask {
  id: string;
  source: 'cron' | 'systemd';
  title: string;
  rawName: string;
  agent: string;
  modelId: string | null;
  when: string;
  repeat: string;
  enabled: boolean;
  /** 只有 OpenClaw 的定时任务能在 app 里开关 */
  toggleable: boolean;
  nextAt: number;
  last: { status: string | null; when: string } | null;
}

export type GoalCategory = '健康' | '学业' | '职业' | '财务';
export type GoalStatus = 'active' | 'done' | 'dropped';

export interface Goal {
  id: string;
  category: GoalCategory;
  title: string;
  detail: string;
  /** YYYY-MM-DD、YYYY-MM，或者一句话（「2027 秋」） */
  due: string;
  /** 截止还有几天（due 是日子才有） */
  daysLeft: number | null;
  groupId: string | null;
  source: string;
  status: GoalStatus;
  /** 服务器自动读当前值的指标（bodyfat / weight）；null = 不自动读 */
  metric: string | null;
  /** 下面几项只有能用数字追踪的目标才有（比如体脂）；目标区间可以只有一头 */
  unit: string | null;
  targetLow: number | null;
  targetHigh: number | null;
  current: number | null;
  currentDate: string | null;
  currentSource: string | null;
  /** 起点：设目标那天或之前最近的一次读数 */
  start: number | null;
  startDate: string | null;
  /** 从起点到目标区间走了多少（0–1，进了区间 = 1）；服务器算好的，老服务器没有 */
  progress: number | null;
  /** 往下走 / 往上走 / 保持在区间里 */
  direction: 'down' | 'up' | 'keep' | null;
  /** 现在在区间里、上面还是下面 */
  state: 'in' | 'above' | 'below' | null;
  /** 最新读数超过 30 天 */
  stale: boolean;
  /** 谁加的（leo / main / Agent id；老目标是 null） */
  addedBy: string | null;
  closedAt: string | null;
}

/** 一次改动（server/goals.py 的 goal_log）：目标页顶上那条「撤销」用。 */
export interface GoalChange {
  logId: number;
  at: string;
  actor: string;
  actorName: string;
  goal: string;
  title: string;
  action: string;
  summary: string;
  status: 'done' | 'undone';
}

/** 能自动读数的指标（服务器给，名字按语言）。 */
export interface GoalMetric { key: string; label: string; unit: string }

/** 体重 / 体脂的读数和摘要（GET /api/goals/trend）。source：body = 训练软件（训记），health = Apple 健康 */
export interface GoalTrendPoint { date: string; value: number; source: 'body' | 'health' }
export interface GoalTrend {
  metric: string;
  label: string;
  unit: string;
  days: number;
  from: string;
  to: string;
  series: GoalTrendPoint[];
  summary: {
    source: 'body' | 'health';
    sourceName: string;
    latest: { date: string; value: number };
    avg7: { value: number; n: number } | null;
    change30: { value: number; since: string } | null;
    /** 对照来源同一天的读数差得多时才有 */
    check: { date: string; value: number; sourceName: string } | null;
  } | null;
  sources: { key: 'body' | 'health'; name: string; primary: boolean; connected: boolean; error: string | null }[];
}

/** L1 长期记忆的一条（某个 agent 工作区 MEMORY.md 的一个要点）。 */
export interface MemoryItem {
  id: string;
  scope: string;
  section: string;
  text: string;
}

/** L0 档案的一条（shared/profile/USER.md 的一个要点）。 */
export interface ProfileItem {
  id: string;
  section: string;
  text: string;
  sources: string[];
  date: string | null;
}

export interface ActivityEntry {
  id: string;
  time: string;
  actor: string;
  text: string;
  kind: 'reply' | 'cron' | 'failed' | 'approved' | 'denied' | 'forgot' | 'edit' | 'deleted' | 'toggled';
}

export interface AvatarConfig {
  style: 'lens' | 'eclipse' | 'orbit';
  ring: string;
  stream: string;
}

export interface ModelsInfo {
  primary: string;
  fallbacks: string[];
  subagent: string | null;
  allowed: string[];
  providers: { provider: string; name: string; status: string; subscription: boolean; expires: string | null }[];
}

export interface SecurityInfo {
  facts: { title: string; sub: string; state: string; tone: 'good' | 'warn' | 'neutral' }[];
  plan: { title: string; sub: string }[];
  rules: [string, string][];
}

/** 历史页：某个线程有记录的逻辑日（04:00 为界）。 */
export interface DayInfo { day: string; count: number; first: string; lastTs: string }
/** 关键词搜索命中：对话 / 建议卡 / 日志。 */
export interface SearchHit { kind: 'message' | 'card' | 'journal'; id: string; thread: string | null; role: 'user' | 'grava' | 'auto'; day: string; ts: string; time: string; snippet: string }

/** 「要记得的」的分组：可疑的安全提醒置顶 / 过了的 / 明天 / 一周内 / 以后 / 没定日子的要办 / 邮件动态（没日子的钱和状态）。 */
export type RememberGroup = 'security' | 'overdue' | 'tomorrow' | 'week' | 'later' | 'nodate' | 'news';

/**
 * 日程和「要记得的」里的一行（GET /api/schedule、/api/remember）。
 * kind：class 课 / event 一段安排 / deadline 截止 / todo 要办 / money 钱 / status 状态 / security 安全提醒；
 * origin：calendar 课表 / own 自己的（你或 Agent 加的）/ canvas 课程作业 / mail 邮件 / apply 求职和申请。
 * id 就是改它用的 ref：item:… / ics:… / canvas:… / mail:… / app:…。
 */
export interface ScheduleEntry {
  id: string;
  kind: 'class' | 'event' | 'deadline' | 'todo' | 'money' | 'status' | 'security';
  origin: 'calendar' | 'own' | 'canvas' | 'mail' | 'apply';
  title: string;
  detail: string;
  location: string;
  note: string;
  /** YYYY-MM-DD；没日子的（邮件里的待办、动态）是 null */
  date: string | null;
  weekday?: string;
  start: string;
  end: string;
  allDay: boolean;
  /** 来源小标：课程缩写 / 邮箱 / Agent 名；你自己加的是空 */
  badge: string;
  /** 自己的日程是谁加的（leo / main / Agent id）；求职和申请是哪个 Agent */
  by: string | null;
  /** 原文（邮件那封、作业页面） */
  link: string | null;
  done: boolean;
  /** 课：不去；series = 是「每周这节都不去」管着的 */
  skip: boolean;
  series: boolean;
  /** 过去的：去了 / 做了（true）、没去（false）、没记（null） */
  attended: boolean | null;
  actualStart: string;
  actualEnd: string;
  /** 实际时间是从哪来的（workouts = 训练记录） */
  actualFrom?: string;
  past: boolean;
  tentative: boolean;
  free: boolean;
  /** 和哪些撞了 */
  clash: string[];
  editable: boolean;
  group: RememberGroup | null;
  urgent: boolean;
  key?: string | null;
  locationChanged?: boolean;
  sourceLocation?: string;
  /** 属于哪个项目（server/projects.py）：点一下进那个项目 */
  project?: { id: string; title: string };
}

/** iPhone 日历订阅：链接路径（接在服务器地址后面）和四类开关。 */
export interface ScheduleFeed { path: string; include: { classes: boolean; mine: boolean; deadlines: boolean; mail: boolean }; name: string }

/**
 * 世界树（server/memtree.py）：各 AI 平台共用的记忆。真身是 Obsidian 库「世界树」文件夹里一条一篇的笔记。
 * 枝按层排好（先序：大枝后面跟着它的小枝）；leaves = 直接挂在这根枝上的，total = 连小枝一起的。
 */
export interface TreeBranch {
  name: string;
  /** 上一级的名字；大枝的上一级是主干（档案） */
  parent: string;
  /** 它管什么（枝笔记的第一行） */
  about: string;
  /** 1 = 大枝，2 = 小枝 */
  depth: number;
  leaves: number;
  total: number;
  /** 默认挂到这里的 Agent（它们写的记忆自动挂上来） */
  agents: { id: string; name: string }[];
}

/** 一片叶子 = 一条记忆。kind：fact 事实 / preference 偏好 / decision 决定 / event 近况。 */
export interface TreeLeaf {
  id: string;
  text: string;
  kind: 'fact' | 'preference' | 'decision' | 'event';
  /** 笔记里写的来源：claude / chatgpt / gemini / claude-code / notion / grava-<Agent> / leo（你在手机上写的）/ prune（每周修剪改写的） */
  source: string;
  sourceName: string;
  /** 最早是哪里记下的（每周修剪改写过的，顺着找回原来的平台）；「按来源」按它分 */
  origin: string;
  originName: string;
  /** origin 是某个 Agent 时它的 id（main = 助手自己），给颜色用 */
  agent: string | null;
  status: 'active' | 'pending';
  /** 挂在哪根枝上；直接挂在主干上的是主干的名字（档案） */
  branch: string;
  tags: string[];
  /** YYYY-MM-DD */
  observedAt: string;
  createdAt: string;
}

export interface TreeInfo {
  trunk: { name: string; count: number };
  branches: TreeBranch[];
  leaves: TreeLeaf[];
  counts: { total: number; pending: number; bySource: { source: string; name: string; agent: string | null; count: number }[] };
  /** 库里格式有问题、先跳过了的笔记数 */
  issues: number;
}

/** 世界树这一块的状态：ok 有数据 / missing 服务器上没接世界树 / unsupported 服务器版本还没有这个接口。 */
export type TreeState = { kind: 'ok'; data: TreeInfo } | { kind: 'missing'; hint: string } | { kind: 'unsupported' };
export type TreeAction = 'confirm' | 'forget' | 'move';

/** 「我 → 连接」的一项（server/connectors.py）：现状一句话、几条事实、它用来做什么、不是 ok 时怎么修。 */
export interface Connector {
  id: string;
  name: string;
  /** 图标键（ConnectorsScreen 里对应到图标，认不出的用插头） */
  icon: string;
  status: 'ok' | 'warn' | 'off';
  line: string;
  facts: { label: string; value: string }[];
  uses: string;
  fix: string | null;
  /** app 里能跳去的页（比如 iPhone 日历订阅 → 日程设置页） */
  open: { screen: string; label: string } | null;
}

export interface ConnectorsInfo {
  groups: { id: string; title: string; items: Connector[] }[];
  counts: { ok: number; warn: number; off: number };
  checkedAt: string;
}

/** 连接这一块：老服务器没有 /api/connectors 时是 unsupported。 */
export type ConnectorsState = { kind: 'ok'; data: ConnectorsInfo } | { kind: 'unsupported' };

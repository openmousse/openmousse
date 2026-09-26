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
export type InboxKind = 'exec' | 'task' | 'write' | 'send' | 'spend' | 'schedule' | 'push' | 'skill' | 'agent' | 'block' | 'code' | 'calendar' | 'other';
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
  status: InboxStatus;
  /** 「改一下」时写的意见 */
  note: string;
  /** 做完 / 没做成的结果 */
  result: string;
  level: 'ring' | 'quiet' | 'none';
  createdAt: string;
  decidedAt: string | null;
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

export type ChatCard = HandoffCard | TaskCardInfo | ScheduleChangeCard;
/** 一个对话里的卡片（GET /api/chat/cards）：它自己转出去、派出去的，加上别的对话转给它的（incoming）。 */
export interface ThreadCards { cards: ChatCard[]; incoming: HandoffCard[] }

/** 有生命周期的独立对话空间：比一段对话大，比 Group 小。 */
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

export interface Goal {
  id: string;
  category: GoalCategory;
  title: string;
  detail: string;
  due: string;
  groupId: string | null;
  source: string;
  /** 下面几项只有能用数字追踪的目标才有（比如体脂） */
  unit: string | null;
  targetLow: number | null;
  targetHigh: number | null;
  current: number | null;
  currentDate: string | null;
  currentSource: string | null;
  start: number | null;
  /** 最新读数超过 30 天 */
  stale: boolean;
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
}

/** iPhone 日历订阅：链接路径（接在服务器地址后面）和四类开关。 */
export interface ScheduleFeed { path: string; include: { classes: boolean; mine: boolean; deadlines: boolean; mail: boolean }; name: string }

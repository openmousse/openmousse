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

export type GroupIcon = 'dumbbell' | 'utensils' | 'book' | 'wallet' | 'moon' | 'briefcase' | 'heart' | 'plane';

export interface Group {
  id: string;
  name: string;
  icon: GroupIcon;
  purpose: string;
  modelId: string;
  dashboard: 'fitness' | 'diet' | 'apply' | 'masters' | 'health' | 'none';
  lastLine: string;
}

/** 等你点头：OpenClaw 审批队列里的一项（执行命令、插件动作等）。 */
export interface Approval {
  id: string;
  kind: string;
  groupId: string | null;
  action: string;
  detail: string;
  fields: { k: string; v: string }[];
  requestedAt: string;
}

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
  /** 详情页才有 */
  brief?: string;
  runs?: TaskRun[];
}

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

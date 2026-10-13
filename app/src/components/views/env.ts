// 各个视图组件共用的东西（单独一个文件：Collection.tsx 和每个视图互相引用会绕成圈）。
import type { Block, BoardColumn, ViewCard, ViewData, ViewQuery } from '../../api/boards';

export interface ViewEnv {
  agent: string;
  block: Block;
  viewId: string;
  /** 全屏页：行多给、看板能拖。看板页上的是缩略的样子，「查看全部」进全屏页。 */
  full: boolean;
  /** 提案预览：只看，不能点开、不能拖。 */
  readOnly: boolean;
  /** 按你点的排序、筛选、月份、翻页重算这个视图；老服务器（没有 boards.views）和预览给 null。 */
  query: ((q: Partial<ViewQuery>) => Promise<ViewData | null>) | null;
  /** 点开一行：全部字段、改、删、快捷按钮；看板里还有「移到」。 */
  openCard: (card: ViewCard, col?: BoardColumn) => void;
  /** 图表上点开的一段（null = 整个数字）。 */
  drill: (chart: number, key: string | null, label: string) => void;
  /** 卡片移到另一列 = 改 group 字段，成功后重读。 */
  moveCard: (card: ViewCard, to: BoardColumn) => Promise<void>;
  /** 进全屏页（看全部、看板拖卡片）。看板页上才有。 */
  openFull?: () => void;
  /** 拖卡片时关掉返回手势（全屏页）。 */
  setDragging?: (on: boolean) => void;
}

export type ViewProps<D> = { env: ViewEnv; data: D };

// 积木看板的上下文（单独一个文件：BoardContext 和 Blocks 都用它，免得互相引用）。
import { createContext, useContext } from 'react';
import type { Block, Board } from '../../api/boards';

export interface BoardCtx {
  agent: string;
  board: Board | null;
  error: string | null;
  reload: () => Promise<void>;
  layout: Map<string, Block[]>;   // 位置 → 按顺序要画的块（'' = 最后）
  fresh: Set<string>;             // 刚加的块（撤回条还在时标「新」）
  readOnly: boolean;              // 提案预览：只看，不能点
  onChat: () => void;             // 发了消息之后切到对话
  openMenu?: (block: Block) => void;  // 长按一块：挪、藏、让它改、删
  openSectionMenu?: (id: string) => void;  // 长按内置看板的一节：挪、藏
}

export const Ctx = createContext<BoardCtx | null>(null);
export const useBoard = () => useContext(Ctx);

// app 自己的原生模块（ios/ 下的 Swift）。网页、Android、没有这个模块的旧包上一律拿到 null，调用方自己判断。
import { requireOptionalNativeModule } from 'expo';
import { Platform } from 'react-native';

export interface EditMenuEvent {
  /** 登记时的 key（输入框的 testID） */
  key: string;
  /** 点的是哪一项 */
  id: string;
  /** 原生已经处理了（比如换行已经插进去了）；false = 要 JS 自己来 */
  handled: boolean;
}

export interface LiveTokenEvent {
  /** start = push-to-start 令牌（服务器用它在 app 没开时开活动）；activity = 某个活动的令牌（改、结束用） */
  type: 'start' | 'activity';
  token: string;
  key?: string;
  id?: string;
}

export interface LiveStateEvent {
  key: string;
  id: string;
  state: 'active' | 'ended' | 'dismissed' | 'stale' | 'unknown';
}

interface Subscription { remove(): void }

interface MousseNativeModule {
  appGroup(): string;
  setShared(json: string): void;
  writeWidgetSnapshot(json: string): void;
  reloadWidgets(): void;
  outbox(): string;
  removeOutbox(id: string): void;
  setEditMenu(key: string, itemsJson: string): void;
  liveSupported(): boolean;
  liveStart(key: string, kind: string, stateJson: string, staleAt: number | null): Promise<string>;
  liveUpdate(key: string, stateJson: string, staleAt: number | null): Promise<boolean>;
  liveEnd(key: string, stateJson: string | null): Promise<void>;
  liveList(): string;
  liveObserve(): void;
  addListener(event: 'onEditMenu', cb: (e: EditMenuEvent) => void): Subscription;
  addListener(event: 'onLiveToken', cb: (e: LiveTokenEvent) => void): Subscription;
  addListener(event: 'onLiveState', cb: (e: LiveStateEvent) => void): Subscription;
}

let cached: MousseNativeModule | null | undefined;

export function mousseNative(): MousseNativeModule | null {
  if (cached !== undefined) return cached;
  cached = Platform.OS === 'ios' ? requireOptionalNativeModule<MousseNativeModule>('MousseNative') : null;
  return cached;
}

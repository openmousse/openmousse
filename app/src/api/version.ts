// 服务器是哪一版、能做什么（/api/health 的 server 段，见 ../server/version.py，2026-10-05）。
// app 走热更新、几乎总比服务器新：新功能用 supports('<名字>') 判断，不要再靠「这个接口 404」去猜；
// 服务器低于 SERVER_API 时，设置 → 我的 claw 显示「需要更新」和更新命令，用不了的地方统一用 needsUpdate() 说明。
import { L } from '../lang';

/** 这一版 app 期望的服务器基线（server/version.py 的 API）。服务器那边加了 app 要依赖的接口、把 API 加 1 时，这里同步改。 */
export const SERVER_API = 1;

/** 更新服务器：再跑一遍安装命令（保留原来的设置和数据，跑完服务会重启）。openmousse.ai/install 转到公开库的 install.sh。 */
export const UPDATE_CMD = 'curl -fsSL https://openmousse.ai/install | bash';

export type ServerInfo = {
  /** 发版日期（年.月.日）；null = 加版本号之前的老服务器 */
  version: string | null;
  commit: string | null;
  api: number;
  features: string[];
  /** 接的是 OpenClaw 时它的版本 */
  openclaw: string | null;
};

export const OLD_SERVER: ServerInfo = { version: null, commit: null, api: 0, features: [], openclaw: null };

const str = (v: unknown) => (typeof v === 'string' && v ? v : null);

export function serverOf(raw: unknown): ServerInfo {
  if (!raw || typeof raw !== 'object') return OLD_SERVER;
  const r = raw as Record<string, unknown>;
  return {
    version: str(r.version), commit: str(r.commit), api: typeof r.api === 'number' ? r.api : 0,
    features: Array.isArray(r.features) ? r.features.filter((f): f is string => typeof f === 'string') : [], openclaw: str(r.openclaw),
  };
}

// 和 data.ts 的 serverSupport 一样放在模块里：api/ 下的函数不经过 store 也能查。probe() 连上时更新。
let current: ServerInfo = OLD_SERVER;
export const setServerInfo = (s: ServerInfo) => { current = s; };

/** 服务器有没有这样能力（server/version.py 的 FEATURES）。老服务器什么都没报 = false。 */
export const supports = (feature: string) => current.features.includes(feature);

/** 服务器比这一版 app 期望的旧。 */
export const serverOutdated = (s: ServerInfo = current) => s.api < SERVER_API;

/** 「服务器太旧，这里用不了」的统一说法：前半句说哪里用不了（带句号），后半句都指到设置里的更新方法。 */
export const needsUpdate = (zh: string, en: string) =>
  L(`${zh}请更新服务器，方法见「设置 → 我的 claw」。`, `${en} Update the server; see Settings → My claws for how.`);

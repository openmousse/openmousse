// 能连这台服务器的设备（server/pairing.py：/api/devices、/api/pair/new，2026-09-29）：设置 → claw 详情里列出来，
// 能收回别的设备的令牌，也能给另一台设备出一个配对码（扫码就连上，不用抄令牌）。
import { request } from './base';

export interface Device { name: string; label: string; paired: boolean; current: boolean }
export interface PairCode { code: string; expires: string; link: string; qr: { size: number; path: string } }

const enc = encodeURIComponent;

export const devicesApi = {
  list: () => request<{ devices: Device[] }>('/api/devices'),
  remove: (name: string) => request<{ ok: boolean }>(`/api/devices/${enc(name)}`, { method: 'DELETE' }),
  /** server：这台手机连服务器用的地址（配对链接里就写它）。 */
  pairNew: (server: string, name?: string) => request<PairCode>('/api/pair/new', { method: 'POST', body: { server, name } }),
};

// 附件预览（2026-09-30）：GET /api/files/{id}/preview 说这个文件怎么显示，PDF 这类的页图 /api/files/{id}/page/{n}?w=。见 server/preview.py。
import { PixelRatio } from 'react-native';
import type { Attachment } from '../data/types';
import { fileUrl, request } from './base';

export type PreviewTable = { rows: string[][]; head?: boolean; rowsTotal?: number | null; colsTotal?: number | null };
export type PreviewBlock = { md: string } | { table: PreviewTable };
export interface FilePreview {
  /** image 图片 · pages 页图（PDF / EPUB / SVG）· doc Markdown 和表格（Word / PPT / Excel / CSV / Markdown）· text 文字或代码 · audio · video · none 看不了 */
  view: 'image' | 'pages' | 'doc' | 'text' | 'audio' | 'video' | 'none';
  /** 「PDF · 3 页」「Excel · 2 个工作表」这类，服务器按语言写好 */
  label?: string;
  pages?: [number, number][];
  pageCount?: number;
  blocks?: PreviewBlock[];
  text?: string;
  mono?: boolean;
  truncated?: boolean;
  note?: string | null;
  transcript?: string | null;
  width?: number | null;
  height?: number | null;
  /** 附件本身（url 是 /api/files/<id>，用之前过 fileUrl） */
  file?: Attachment;
}

export const filePreview = (id: string) => request<FilePreview>(`/api/files/${encodeURIComponent(id)}/preview`, { timeoutMs: 60000 });
/** 学习台的课件（不是对话附件）：同一套预览，地址在 /api/study/file/…（server/studyapp.py） */
const studyQ = (s: NonNullable<Attachment['study']>) => `course=${encodeURIComponent(s.course)}&path=${encodeURIComponent(s.path)}&where=${s.where ?? 'materials'}`;
export const previewOf = (a: Attachment) => (a.study
  ? request<FilePreview>(`/api/study/file/preview?${studyQ(a.study)}`, { timeoutMs: 60000 })
  : filePreview(a.id));

/** 服务器渲染页图只出这几种宽度（缓存好复用）：按屏幕上的宽度 × 像素密度就近往上取。 */
const PAGE_WIDTHS = [800, 1200, 1600, 2000];
export const pageWidth = (points: number) => {
  const px = points * PixelRatio.get();
  return PAGE_WIDTHS.find((w) => w >= px) ?? PAGE_WIDTHS[PAGE_WIDTHS.length - 1];
};
export const pageUrl = (id: string, n: number, w: number) => fileUrl(`/api/files/${encodeURIComponent(id)}/page/${n}?w=${w}`);
export const pageOf = (a: Attachment, n: number, w: number) => (a.study ? fileUrl(`/api/study/file/page?${studyQ(a.study)}&n=${n}&w=${w}`) : pageUrl(a.id, n, w));

/** 附件的地址已经带好服务器和令牌（client.ts 用 fileUrl 换过）；再加一个参数。 */
export const withParam = (url: string, kv: string) => `${url}${url.includes('?') ? '&' : '?'}${kv}`;
/** 是服务器上的文件（网页版同源时是 /api/files/… 这样的相对地址），不是还没传完的本地文件。 */
export const isRemote = (url: string) => /^https?:\/\//i.test(url) || url.startsWith('/api/');

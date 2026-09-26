// 对话输入框的两样东西，单独放一个文件（看板上的「拍小票」「问它」也要用，放在 ChatView 里会绕成循环引用）：
// 按线程记着的草稿，和选照片 / 文件。
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import type { PendingFile } from '../data/types';
import { L } from '../i18n';

export const MAX_FILES = 10;

/** 没发出去的草稿，按线程。 */
export const drafts = new Map<string, string>();
/** 预先放一句话在某个对话的输入框里（看板上点「问它」：切到对话时输入框里已经写好「关于「鸡胸肉」：」）。 */
export const setChatDraft = (threadId: string, text: string) => { drafts.set(threadId, text); };

export async function pickDocuments(): Promise<PendingFile[]> {
  const res = await DocumentPicker.getDocumentAsync({ type: '*/*', multiple: true, copyToCacheDirectory: true });
  if (res.canceled) return [];
  return res.assets.map((a) => ({ uri: a.uri, name: a.name, mime: a.mimeType ?? '', size: a.size ?? 0, file: (a as { file?: File }).file }));
}

export async function pickMedia(camera: boolean): Promise<PendingFile[]> {
  const perm = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) throw new Error(camera ? L('没有相机权限，去系统设置里打开。', 'No camera access. Turn it on in Settings.') : L('没有相册权限，去系统设置里打开。', 'No photo library access. Turn it on in Settings.'));
  const res = camera
    ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images', 'videos'], quality: 0.9 })
    : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images', 'videos'], allowsMultipleSelection: true, selectionLimit: MAX_FILES, quality: 0.9 });
  if (res.canceled) return [];
  return res.assets.map((a, i) => {
    const ext = a.uri.split('?')[0].split('.').pop()?.toLowerCase() || (a.type === 'video' ? 'mov' : 'jpg');
    return { uri: a.uri, name: a.fileName ?? `${a.type === 'video' ? 'video' : 'photo'}-${Date.now()}-${i}.${ext}`,
      mime: a.mimeType ?? (a.type === 'video' ? 'video/quicktime' : 'image/jpeg'), size: a.fileSize ?? 0, file: (a as { file?: File }).file };
  });
}

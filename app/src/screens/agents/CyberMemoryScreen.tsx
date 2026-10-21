// 赛博的记忆（主线第三步，设计稿第 43 张「全部 N 条 ›」）：记下的（它自己长的，每条都是你点了头才记下的：能删、能清空）和知识库
// （你给的资料、它每天读的，写明来源：能删，能再给它一份文字或一个文件）。健康、饮食、睡眠不给赛博，它也不记。
// 路由 CyberMemory（params id）。数据：GET /api/cybers/{id}/notes、/knowledge（服务器 cybers.py）。
import React, { useCallback, useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { errText } from '../../components/agents/NewFlow';
import { pickDocuments } from '../../components/chatInput';
import { ConfirmDelete } from '../../components/ChatParts';
import { Paperclip, Plus, Trash2 } from '../../components/icons';
import { useSheet } from '../../components/Sheet';
import { Btn, Card, NavHeader, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../../components/ui';
import { formFile, xhrUpload } from '../../api/client';
import { getBase } from '../../api/base';
import { cybersApi, type Knowledge, type Note } from '../../api/cybers';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { KeyboardScroll } from '../../components/keyboard';

const when = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()}`; };
const sourceLabel = (s: string) => ({ chat: L('对话里', 'From a chat'), review: L('它回看时提的', 'From its review'), daily: L('读完日报', 'After a daily read'),
  profile: L('资料页', 'Profile') }[s] ?? s);

function AddSheet({ cid, close, onAdded }: { cid: string; close: () => void; onAdded: () => void }) {
  const t = useTheme();
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const add = (body: { title?: string; text?: string; file?: string }) => {
    setBusy(true);
    cybersApi.addKnowledge(cid, body).then(() => { close(); onAdded(); }).catch((e) => { setErr(errText(e)); setBusy(false); });
  };
  const file = async () => {
    try {
      const f = (await pickDocuments())[0];
      if (!f) return;
      setBusy(true);
      const fd = new FormData();
      fd.append('thread', cid);
      formFile(fd, 'files', f);
      const j = await xhrUpload(`${getBase()}/api/chat/upload`, fd);
      const id = (j.attachments ?? [])[0]?.id as string | undefined;
      if (!id) throw new Error(L('上传失败', 'Upload failed'));
      add({ file: id, title: title.trim() || undefined });
    } catch (e) {
      setErr(errText(e));
      setBusy(false);
    }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={title} onChangeText={setTitle} placeholder={L('标题', 'Title')} placeholderTextColor={t.ink3} accessibilityLabel={L('标题', 'Title')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <TextInput value={text} onChangeText={setText} multiline placeholder={L('贴一段资料', 'Paste some material')} placeholderTextColor={t.ink3} accessibilityLabel={L('资料', 'Material')}
        style={[type.callout, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 120, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} onPress={() => { if (text.trim()) add({ title: title.trim(), text: text.trim() }); }} />
      <Btn kind="quiet" label={L('上传文件', 'Upload a file')} icon={<Paperclip size={15} color={t.ink} />} onPress={file} />
      <T v="caption" color={t.ink3}>{L('资料只存在你的服务器上；它用哪家的模型，聊到时读到的就发到哪家。', 'Material stays on your server; when it comes up in a chat, what it reads goes to the vendor of its model.')}</T>
      {err ? <T v="callout" color={t.bad}>{err}</T> : null}
    </View>
  );
}

export function CyberMemoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = (useRoute<any>().params ?? {}) as { id: string };
  const [tab, setTab] = useState<'notes' | 'knowledge'>('notes');
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [know, setKnow] = useState<Knowledge[] | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => Promise.all([cybersApi.notes(id).then((r) => setNotes(r.notes)), cybersApi.knowledge(id).then((r) => setKnow(r.items))])
    .then(() => setErr('')).catch((e) => setErr(errText(e))), [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const fail = (e: unknown) => showError(L('删除失败', "Couldn't delete"), e);
  const clear = () => sheet.open({ title: L('清空记下的？', 'Clear what it kept?'), content: (close) => (
    <ConfirmDelete label={L('清空', 'Clear')} close={close} body={L('它记下的每一条都删掉，以后从头记。知识库不受影响。', 'Every note it kept is deleted and it starts over. The knowledge base stays.')}
      run={() => cybersApi.clearNotes(id).then(() => load())} />
  ) });
  return (
    <Screen>
      <NavHeader title={L('记忆', 'Memory')} onBack={() => nav.goBack()} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        <Segmented value={tab} onChange={setTab} options={[{ value: 'notes', label: L(`记下的 · ${notes?.length ?? 0}`, `Kept · ${notes?.length ?? 0}`) },
          { value: 'knowledge', label: L(`知识库 · ${know?.length ?? 0}`, `Knowledge · ${know?.length ?? 0}`) }]} />
        <T v="caption" color={t.ink3} style={{ marginTop: space.md }}>{L('健康、饮食、睡眠不给赛博，它也不记。', 'Health, diet and sleep are never given to a Cyber, and it keeps no notes about them.')}</T>
        {err ? <T v="callout" color={t.bad} style={{ marginTop: space.md }}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T> : null}
        {tab === 'notes' ? (
          <>
            <SectionLabel right={notes?.length ? <Pressable onPress={clear} hitSlop={8} accessibilityRole="button"><T v="caption" color={t.bad}>{L('清空', 'Clear')}</T></Pressable> : undefined}>
              {L('记下的', 'Kept')}
            </SectionLabel>
            {notes && !notes.length ? <T v="callout" color={t.ink3}>{L('还没记下什么。它想记的会先问你。', 'Nothing kept yet. It asks you before keeping anything.')}</T> : null}
            {(notes ?? []).map((n) => (
              <Card key={n.id} style={styles.item}>
                <View style={{ flex: 1, gap: 4 }}>
                  <T v="callout">{n.text}</T>
                  <T v="caption" color={t.ink3}>{`${when(n.createdAt)} · ${sourceLabel(n.source)}`}</T>
                </View>
                <Pressable onPress={() => cybersApi.deleteNote(id, n.id).then(load).catch(fail)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('删除', 'Delete')}>
                  <Trash2 size={17} color={t.ink3} />
                </Pressable>
              </Card>
            ))}
          </>
        ) : (
          <>
            <SectionLabel right={<Pressable onPress={() => sheet.open({ title: L('给它一份资料', 'Give it some material'), content: (close) => <AddSheet cid={id} close={close} onAdded={load} /> })}
              hitSlop={8} accessibilityRole="button" accessibilityLabel={L('添加', 'Add')}><Plus size={18} color={t.gold} /></Pressable>}>
              {L('知识库', 'Knowledge')}
            </SectionLabel>
            {know && !know.length ? <T v="callout" color={t.ink3}>{L('还没有资料。你给的、它每天读的都在这里，写明来源。', "No material yet. What you give it and what it reads each day shows up here, with where it came from.")}</T> : null}
            {(know ?? []).map((k) => (
              <Card key={k.id} style={styles.item}>
                <View style={{ flex: 1, gap: 4 }}>
                  <T v="headline" style={{ fontSize: 15 }}>{k.title}</T>
                  <T v="callout" color={t.ink2} numberOfLines={3}>{k.excerpt}</T>
                  <T v="caption" color={t.ink3}>{`${when(k.createdAt)} · ${k.source}`}</T>
                </View>
                <Pressable onPress={() => cybersApi.deleteKnowledge(id, k.id).then(load).catch(fail)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('删除', 'Delete')}>
                  <Trash2 size={17} color={t.ink3} />
                </Pressable>
              </Card>
            ))}
          </>
        )}
      </KeyboardScroll>
    </Screen>
  );
}

const styles = StyleSheet.create({
  item: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md, marginBottom: space.sm },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
});

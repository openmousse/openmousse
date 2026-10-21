// 新建赛博（主线第三步，设计稿第 41 张）：① 描述你想要的赛博（或从模板开始：专业 / 朋友 / 机器，最后「空白开始」）→ ② 补问（两三道点选题，
// 不想答可以直接生成）→ ③ 草稿（全部预填：名字 + 标签、换形象（自己捏 / 上传 + 3 个预设）、性格三行、调一调五个小按钮、试聊一句 +「换一句」、
// 模型 + 推荐理由、装载）→ 底部大按钮「创建 X」。补问、草稿、试聊都是服务器上零工具的一问（../../server/agentnew.py）。
// 路由 NewCyber（modal）：页内自己挂 SheetProvider（同 NewGroupScreen）。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { CyberAvatar } from '../../components/agents/CyberAvatar';
import { ModelChoice } from '../../components/agents/ModelChoices';
import { Choice, DescribeBox, QuestionsStep, answersOf, errText } from '../../components/agents/NewFlow';
import { takeAvatar } from '../../components/agents/avatarParts';
import { AgentFace } from '../../components/ChatParts';
import { pickMedia } from '../../components/chatInput';
import { Check, ChevronRight, Plus, RefreshCw, Upload } from '../../components/icons';
import { KeyboardScroll } from '../../components/keyboard';
import { SheetProvider, useSheet } from '../../components/Sheet';
import { Btn, Card, NavHeader, Screen, SectionLabel, Segmented, T, showError } from '../../components/ui';
import { cybersApi, uploadAvatar, type CyberDraft, type ModelRow, type Question, type Template, type Templates, type Tweak } from '../../api/cybers';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { radius, space, type, useTheme } from '../../theme';

export function NewCyberScreen() {
  return <SheetProvider><NewCyberFlow /></SheetProvider>;
}

type Step = 'start' | 'ask' | 'draft';
const TWEAKS: { key: Tweak; label: () => string }[] = [
  { key: 'stricter', label: () => L('更严格', 'Stricter') }, { key: 'gentler', label: () => L('更温和', 'Gentler') },
  { key: 'shorter', label: () => L('更简短', 'Shorter') }, { key: 'english', label: () => L('用英文', 'In English') },
  { key: 'examples', label: () => L('多举例', 'More examples') },
];
const AVATAR_KEY = 'new-cyber';

function NewCyberFlow() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { connected, reload } = useStore();
  const [step, setStep] = useState<Step>('start');
  const [text, setText] = useState('');
  const [templates, setTemplates] = useState<Templates | null>(null);
  const [cls, setCls] = useState<'pro' | 'friend' | 'machine'>('pro');
  const [template, setTemplate] = useState<Template | null>(null);
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<CyberDraft | null>(null);
  const [busy, setBusy] = useState<'ask' | 'draft' | 'create' | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => { cybersApi.templates().then(setTemplates).catch((e) => setErr(errText(e))); }, []);

  const ask = (tpl: Template | null) => {
    if (busy) return;
    if (!tpl && !text.trim()) { setErr(L('先写一句描述，或者选一个模板', 'Describe it in a sentence first, or pick a template')); return; }
    if (!connected) { setErr(L('未连接服务器，无法创建', "Not connected to the server, can't create it")); return; }
    setTemplate(tpl);
    setBusy('ask');
    setErr('');
    cybersApi.questions({ kind: 'cyber', text: text.trim(), template: tpl?.id ?? null })
      .then((r) => { setQuestions(r.questions); setPicked({}); setStep('ask'); })
      .catch((e) => setErr(errText(e)))
      .finally(() => setBusy(null));
  };
  const makeDraft = (blank = false) => {
    if (busy) return;
    setBusy('draft');
    setErr('');
    cybersApi.cyberDraft({ text: blank ? '' : text.trim(), template: blank ? 'blank' : template?.id ?? null, answers: blank ? [] : answersOf(questions, picked) })
      .then((d) => { setDraft(d); setStep('draft'); })
      .catch((e) => setErr(errText(e)))
      .finally(() => setBusy(null));
  };
  const create = (d: CyberDraft, loadout: string[]) => {
    if (busy) return;
    if (!d.name.trim()) { setErr(L('赛博要有名字', 'A Cyber needs a name')); return; }
    setBusy('create');
    setErr('');
    cybersApi.create({ name: d.name.trim(), tag: d.tag.trim(), kind: d.class, template: d.template, avatar: d.avatar, identity: d.identity, tone: d.tone,
      rules: d.rules, model: d.model, loadout })
      .then((c) => { reload('chats').catch(() => {}); nav.replace('CyberChat', { id: c.id }); })
      .catch((e) => { setErr(errText(e)); setBusy(null); });
  };

  if (step === 'draft' && draft) {
    return <DraftView draft={draft} onChange={setDraft} onBack={() => setStep(questions ? 'ask' : 'start')} onCreate={create} busy={busy === 'create'} error={err} />;
  }
  return (
    <Screen>
      <NavHeader title={L('新建赛博', 'New Cyber')} onBack={() => (step === 'ask' ? setStep('start') : nav.goBack())} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive">
        {step === 'ask' && questions ? (
          <QuestionsStep said={text.trim() || template?.name || ''} questions={questions} picked={picked} busy={busy === 'draft'} error={err}
            onPick={(q, o) => setPicked((p) => ({ ...p, [q.id]: p[q.id] === o ? '' : o }))} onGo={() => makeDraft()} />
        ) : (
          <View style={{ gap: space.md }}>
            <DescribeBox value={text} onChange={(v) => { setText(v); setErr(''); }} label={L('描述你想要的赛博', 'Describe the Cyber you want')}
              placeholder={L('描述你想要的赛博。例如：做过咨询的面试官，陪我练 case，答完逐项点评。', 'Describe the Cyber you want. E.g. a former consultant who runs case interviews with me and reviews each answer.')} />
            <Btn label={busy === 'ask' ? L('正在生成…', 'Generating…') : L('生成草稿', 'Generate draft')} onPress={() => ask(null)} />
            <T v="callout" color={t.ink3}>{L('会写好名字、标签、性格和推荐的模型，之后都能改。', 'It fills in the name, tag, personality and a recommended model; you can change all of them later.')}</T>
            {err ? <T v="callout" color={t.bad}>{err}</T> : null}

            <SectionLabel>{L('或从模板开始', 'Or start from a template')}</SectionLabel>
            <Segmented value={cls} onChange={setCls} options={(templates?.groups ?? []).map((g) => ({ value: g.class, label: g.title }))} />
            {templates ? (
              <Card style={{ paddingVertical: space.sm }}>
                <T v="callout" color={t.ink3} style={{ paddingVertical: space.xs }}>{templates.groups.find((g) => g.class === cls)?.line}</T>
                {templates.groups.find((g) => g.class === cls)?.templates.map((tpl) => (
                  <Pressable key={tpl.id} onPress={() => ask(tpl)} accessibilityRole="button" style={({ pressed }) => [styles.tplRow, { opacity: pressed ? 0.6 : 1 }]}>
                    <CyberAvatar avatar={tpl.avatar} size={40} />
                    <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                      <T v="headline" numberOfLines={1}>{`${tpl.name} · ${tpl.tag}`}</T>
                      <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{tpl.line}</T>
                    </View>
                    <ChevronRight size={16} color={t.ink3} />
                  </Pressable>
                ))}
                <Pressable onPress={() => makeDraft(true)} accessibilityRole="button" style={({ pressed }) => [styles.tplRow, { opacity: pressed ? 0.6 : 1 }]}>
                  <View style={[styles.blank, { borderColor: t.line }]}><Plus size={18} color={t.ink2} /></View>
                  <View style={{ flex: 1, gap: 2 }}>
                    <T v="headline">{templates.blank.name}</T>
                    <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{templates.blank.line}</T>
                  </View>
                  <ChevronRight size={16} color={t.ink3} />
                </Pressable>
              </Card>
            ) : null}
          </View>
        )}
      </KeyboardScroll>
    </Screen>
  );
}

/** 草稿（第 41 张 ③）：哪里不对改哪里；调一调、换一句都问服务器（用这位赛博的模型，零工具）。 */
function DraftView({ draft: d, onChange, onBack, onCreate, busy, error }: {
  draft: CyberDraft; onChange: (d: CyberDraft) => void; onBack: () => void; onCreate: (d: CyberDraft, loadout: string[]) => void; busy: boolean; error: string;
}) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [lines, setLines] = useState<string[]>([]);
  const [trying, setTrying] = useState<Tweak | 'again' | null>(null);
  const [trialErr, setTrialErr] = useState('');
  const [loadout, setLoadout] = useState<string[]>(() => d.loadout.map((a) => a.id));
  const latest = useRef(d);
  useEffect(() => { latest.current = d; }, [d]);
  const set = (patch: Partial<CyberDraft>) => onChange({ ...latest.current, ...patch });

  // 从形象编辑器回来：取走它交回的
  useFocusEffect(useCallback(() => {
    const a = takeAvatar(AVATAR_KEY);
    if (a) onChange({ ...latest.current, avatar: a });
  }, [onChange]));

  const trial = useCallback((tweak?: Tweak) => {
    const cur = latest.current;
    if (!cur.name.trim() && !cur.identity.trim()) return;
    setTrying(tweak ?? 'again');
    setTrialErr('');
    cybersApi.trial({ name: cur.name, identity: cur.identity, tone: cur.tone, rules: cur.rules, model: cur.model, tweak, previous: lines.slice(-4) })
      .then((r) => {
        setLines((ls) => [...ls, r.line]);
        if (tweak) onChange({ ...latest.current, tone: r.tone, rules: r.rules });
      })
      .catch((e) => setTrialErr(errText(e)))
      .finally(() => setTrying(null));
  }, [lines, onChange]);
  // 草稿一出来先听它说一句（空白开始的没有性格，等你写好再点「试聊一句」）
  const first = useRef(false);
  useEffect(() => {
    if (first.current || (!d.identity.trim() && !d.tone.trim())) return;
    first.current = true;
    trial();
  }, [d.identity, d.tone, trial]);

  const upload = async () => {
    try {
      const f = (await pickMedia(false))[0];
      if (!f) return;
      if (!f.mime.startsWith('image/')) { showError(L('只能用图片', 'Pictures only'), L('请选一张照片。', 'Pick a photo.')); return; }
      set({ avatar: { upload: await uploadAvatar(f) } });
    } catch (e) {
      showError(L('上传失败', 'Upload failed'), e);
    }
  };
  const chooseModel = () => sheet.open({ title: L('模型', 'Model'), tall: true, content: (close) => <ModelSheet kind={d.class} current={d.model} onPick={(m) => { close(); set({ model: m.ref, modelName: m.name, modelReason: m.reason ?? m.use, cost: m.cost }); }} /> });
  const addLoad = () => sheet.open({ title: L('装载一个功能 Agent', 'Load an Agent'), content: (close) => (
    <View>
      {d.agents.length ? d.agents.map((a) => (
        <Pressable key={a.id} disabled={a.health} onPress={() => { close(); setLoadout((ls) => (ls.includes(a.id) ? ls : [...ls, a.id])); }} accessibilityRole="button"
          style={({ pressed }) => [styles.loadRow, { opacity: a.health ? 0.45 : pressed ? 0.6 : 1 }]}>
          <AgentFace icon={a.icon} color={a.color} size={36} />
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" numberOfLines={1}>{a.name}</T>
            <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{a.health ? L('健康数据不给赛博', "Health data isn't given to a Cyber") : a.purpose}</T>
          </View>
          {loadout.includes(a.id) ? <Check size={18} color={t.gold} /> : null}
        </Pressable>
      )) : <T v="callout" color={t.ink2}>{L('还没有可以装载的功能 Agent。', 'No Agents to load yet.')}</T>}
    </View>
  ) });

  const loadRows = [...d.loadout, ...d.agents.filter((a) => loadout.includes(a.id) && !d.loadout.some((x) => x.id === a.id)).map((a) => ({ ...a, why: a.purpose }))];
  const line = lines[lines.length - 1];
  return (
    <Screen>
      <NavHeader title={L('草稿', 'Draft')} onBack={onBack}
        right={<Pressable onPress={() => onCreate(d, loadout)} hitSlop={8} accessibilityRole="button"><T v="headline" color={t.gold}>{L('创建', 'Create')}</T></Pressable>} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive">
        {d.fallback ? <Card style={{ marginBottom: space.md }}><T v="callout" color={t.ink2}>{L('模型没有回应，先按你的描述和选项填好了，哪里不对改哪里。', "The model didn't answer, so this is filled from your description and choices. Change anything that's off.")}</T></Card> : null}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
          <CyberAvatar avatar={d.avatar} size={72} />
          <View style={{ flex: 1, gap: space.sm }}>
            <TextInput value={d.name} onChangeText={(v) => set({ name: v })} placeholder={L('名字', 'Name')} placeholderTextColor={t.ink3} accessibilityLabel={L('名字', 'Name')}
              style={[type.title, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
            <TextInput value={d.tag} onChangeText={(v) => set({ tag: v })} placeholder={L('标签，例如：早期投资人', 'Tag, e.g. Early investor')} placeholderTextColor={t.ink3}
              maxLength={16} accessibilityLabel={L('标签', 'Tag')} style={[type.callout, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
          </View>
        </View>

        <SectionLabel>{L('换形象', 'Avatar')}</SectionLabel>
        <View style={styles.presets}>
          {d.presets.map((p, i) => (
            <Pressable key={i} onPress={() => set({ avatar: p })} accessibilityRole="button" accessibilityLabel={L(`预设 ${i + 1}`, `Preset ${i + 1}`)}
              style={[styles.preset, { borderColor: JSON.stringify(p) === JSON.stringify(d.avatar) ? t.goldFill : 'transparent' }]}>
              <CyberAvatar avatar={p} size={52} />
            </Pressable>
          ))}
          <Pressable onPress={() => nav.navigate('CyberAvatarEdit', { avatar: d.avatar, name: d.name, model: d.modelName, key: AVATAR_KEY })} accessibilityRole="button"
            style={[styles.presetBtn, { backgroundColor: t.surface }]}><T v="callout">{L('自己捏', 'Make one')}</T></Pressable>
          <Pressable onPress={upload} accessibilityRole="button" accessibilityLabel={L('上传', 'Upload')} style={[styles.presetBtn, { backgroundColor: t.surface }]}>
            <Upload size={16} color={t.ink} />
          </Pressable>
        </View>

        <SectionLabel>{L('性格', 'Personality')}</SectionLabel>
        <Card style={{ gap: space.md }}>
          {([['identity', L('身份', 'Identity'), L('它是谁', 'Who it is')], ['tone', L('语气', 'Tone'), L('它怎么说话', 'How it talks')], ['rules', L('规矩', 'Rules'), L('它一定做、绝不做的', 'What it always or never does')]] as const).map(([k, label, ph]) => (
            <View key={k} style={{ gap: 4 }}>
              <T v="label" color={t.ink3}>{label}</T>
              <TextInput value={d[k]} onChangeText={(v) => set({ [k]: v } as Partial<CyberDraft>)} multiline placeholder={ph} placeholderTextColor={t.ink3}
                accessibilityLabel={label} style={[type.callout, { color: t.ink, paddingVertical: 4 }]} />
            </View>
          ))}
        </Card>

        <SectionLabel>{L('调一调', 'Adjust')}</SectionLabel>
        <View style={styles.chips}>
          {TWEAKS.map((x) => <Choice key={x.key} label={trying === x.key ? L('正在调…', 'Adjusting…') : x.label()} on={false} off={!!trying} onPress={() => trial(x.key)} />)}
        </View>

        <SectionLabel>{L('试聊', 'Try it')}</SectionLabel>
        <Card style={{ gap: space.md }}>
          {line ? (
            <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' }}>
              <CyberAvatar avatar={d.avatar} size={28} />
              <View style={[styles.bubble, { backgroundColor: t.surface2 }]}><T v="callout">{line}</T></View>
            </View>
          ) : <T v="callout" color={t.ink3}>{trying ? L('正在想…', 'Thinking…') : d.identity.trim() || d.tone.trim() ? L('听它说一句，不对就调一调。', 'Hear it say a line; adjust if it sounds off.')
            : L('写好性格以后，听它说一句。', 'Once the personality is written, hear it say a line.')}</T>}
          {trialErr ? <T v="callout" color={t.bad}>{trialErr}</T> : null}
          <Btn label={trying === 'again' ? L('正在想…', 'Thinking…') : line ? L('换一句', 'Another line') : L('试聊一句', 'Try a line')} kind="quiet"
            icon={<RefreshCw size={15} color={t.ink} />} onPress={() => { if (!trying) trial(); }} />
        </Card>

        <SectionLabel>{L('模型', 'Model')}</SectionLabel>
        <Pressable onPress={chooseModel} accessibilityRole="button" style={({ pressed }) => [styles.modelRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline">{d.modelName || L('跟默认的走', 'Follows the default')}</T>
            <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{d.modelReason}</T>
          </View>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>

        <SectionLabel>{L('装载', 'Loadout')}</SectionLabel>
        <Card style={{ gap: space.xs, paddingVertical: space.sm }}>
          {loadRows.map((a) => {
            const on = loadout.includes(a.id);
            return (
              <Pressable key={a.id} onPress={() => setLoadout((ls) => (on ? ls.filter((x) => x !== a.id) : [...ls, a.id]))} accessibilityRole="checkbox" accessibilityState={{ checked: on }}
                style={({ pressed }) => [styles.loadRow, { opacity: pressed ? 0.6 : 1 }]}>
                <AgentFace icon={a.icon} color={a.color} size={32} />
                <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{`${a.name} · ${a.why}`}</T>
                <View style={[styles.check, { borderColor: on ? t.goldFill : t.line, backgroundColor: on ? t.goldFill : 'transparent' }]}>{on ? <Check size={13} color={t.onGold} /> : null}</View>
              </Pressable>
            );
          })}
          <Pressable onPress={addLoad} accessibilityRole="button" style={({ pressed }) => [styles.loadRow, { opacity: pressed ? 0.6 : 1 }]}>
            <Plus size={18} color={t.ink2} />
            <T v="callout" color={t.ink2}>{L('装载一个功能 Agent', 'Load an Agent')}</T>
          </Pressable>
        </Card>
        <T v="callout" color={t.ink3} style={{ marginTop: space.sm }}>{L('装上就会用，卸下就断开；数据一直在功能 Agent 那里。健康、饮食、睡眠不给赛博。',
          "Loaded Agents are there to ask; unloading just disconnects, and the data stays with the Agent. Health, diet and sleep aren't given to a Cyber.")}</T>

        <View style={{ marginTop: space.xl }}>
          <Btn label={busy ? L('正在创建…', 'Creating…') : L(`创建 ${d.name.trim() || '赛博'}`, `Create ${d.name.trim() || 'Cyber'}`)} onPress={() => onCreate(d, loadout)} />
        </View>
        {error ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{error}</T> : null}
      </KeyboardScroll>
    </Screen>
  );
}

/** 草稿上点「模型」：推荐的三个在前（带理由），下面是全部；套餐标灰点不了。 */
function ModelSheet({ kind, current, onPick }: { kind: CyberDraft['class']; current: string | null; onPick: (m: ModelRow) => void }) {
  const t = useTheme();
  const [rows, setRows] = useState<{ rec: ModelRow[]; all: ModelRow[] } | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => { cybersApi.models({ kind }).then((r) => setRows({ rec: r.recommended, all: r.all })).catch((e) => setErr(errText(e))); }, [kind]);
  if (err) return <T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T>;
  if (!rows) return <T v="callout" color={t.ink3}>{L('正在加载…', 'Loading…')}</T>;
  const rest = rows.all.filter((m) => !rows.rec.some((r) => r.ref === m.ref));
  return (
    <View>
      <T v="label" color={t.ink3} style={{ marginBottom: space.xs }}>{L('推荐', 'RECOMMENDED')}</T>
      {rows.rec.map((m) => <ModelChoice key={m.ref} row={m} reason={m.reason} selected={m.ref === current} onPress={() => onPick(m)} />)}
      <T v="label" color={t.ink3} style={{ marginTop: space.md, marginBottom: space.xs }}>{L('全部', 'ALL')}</T>
      {rest.map((m) => <ModelChoice key={m.ref} row={m} selected={m.ref === current} onPress={() => onPick(m)} />)}
    </View>
  );
}


const styles = StyleSheet.create({
  tplRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  blank: { width: 40, height: 40, borderRadius: 20, borderWidth: 1, borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 9 },
  presets: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' },
  preset: { borderWidth: 2, borderRadius: 30, padding: 2 },
  presetBtn: { height: 40, minWidth: 40, paddingHorizontal: space.md, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  bubble: { flex: 1, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: space.sm },
  modelRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg, padding: space.lg },
  loadRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  check: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
});

// 管家的资料页（主线第三步第三段，设计稿第 46 张）：名片 Agent 升级来的那一个，只对外、替你出面（好友群、好友的追问、别家 Agent）。
// 证件：大头像（默认环与点，能换成捏的）、名字（点了改，空 = 「<你的名字>的管家」）、三格数（模型 / 本周说了几句 / Doorman 扣下几句）；
// 「对外口径」：四个关系页签，每句「问：…… 答：……」+「改这一句」（几种说法各带一句例句 → 先给改好的样子 →「改 / 不改」→「刚改 · 撤销」）；
// 「先问你」两行固定的规矩；「可以请教的赛博」（开关，请教时只带要点）和请教记录（仅你可见）；「说过的话 ›」。
// 路由 Butler；数据都在服务器 butler.py（例句只是试答：不发出去、不进「说过的话」）。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { takeAvatar } from '../../components/agents/avatarParts';
import { ButlerAvatar, CyberAvatar } from '../../components/agents/CyberAvatar';
import { errText } from '../../components/agents/NewFlow';
import { MenuItem } from '../../components/ChatParts';
import { ChevronRight, Lock, Palette, Pencil, RefreshCw, Undo2 } from '../../components/icons';
import { KeyboardScroll } from '../../components/keyboard';
import { useSheet } from '../../components/Sheet';
import { Btn, Card, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../../components/ui';
import { butlerApi, isOrbit, type ButlerProfile, type Consult, type Sample, type SampleChange, type Samples } from '../../api/butler';
import type { CyberAvatarSpec } from '../../api/cybers';
import { hasRoute } from '../../navigation';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';

const AVATAR_KEY = 'butler';

/** 「改这一句」：这一类的几种说法，各带一句例句；现在的那种打勾。挑另一种 → 先给样子（还没改）。 */
function OptionsSheet({ s, onPick }: { s: Sample; onPick: (level: string) => void }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.sm }}>
      <T v="callout" color={t.ink2}>{L(`问：${s.question}`, `Q: ${s.question}`)}</T>
      {s.options.map((o) => (
        <Pressable key={o.level} disabled={o.current} onPress={() => onPick(o.level)} accessibilityRole="button" accessibilityState={{ selected: o.current }}
          style={({ pressed }) => [styles.option, { borderColor: o.current ? t.goldFill : t.line, backgroundColor: o.current ? t.goldSoft : t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <T v="headline" style={{ flex: 1, fontSize: 15 }}>{o.label}</T>
            {o.current ? <Pill label={L('现在', 'Now')} tone="gold" /> : null}
          </View>
          <T v="callout" color={t.ink2}>{L(`例：${o.example}`, `e.g. ${o.example}`)}</T>
        </Pressable>
      ))}
      <T v="caption" color={t.ink3}>{L('挑一种，先看改好以后它会怎么答；你点「采用」才生效。', 'Pick one to see how it would answer; nothing changes until you tap Apply.')}</T>
    </View>
  );
}

/** 改好以后 · 等你点头：现在怎么答 → 改了以后怎么答。 */
function SampleChangeCard({ c, busy, onApply, onDrop }: { c: SampleChange; busy: boolean; onApply: () => void; onDrop: () => void }) {
  const t = useTheme();
  return (
    <Card style={{ gap: space.md, borderWidth: 1, borderColor: t.goldFill }}>
      <T v="label" color={t.gold}>{L(`改好以后 · ${c.tierLabel} · ${c.categoryLabel} · 等你点头`, `AFTER THE CHANGE · ${c.tierLabel.toUpperCase()} · NEEDS YOUR OK`)}</T>
      <View style={[styles.box, { backgroundColor: t.surface2 }]}>
        <T v="caption" color={t.ink3}>{L(`现在 · ${c.oldLabel}`, `Now · ${c.oldLabel}`)}</T>
        <T v="callout" color={t.ink2}>{c.before || L('（空）', '(empty)')}</T>
      </View>
      <View style={[styles.box, { backgroundColor: t.goldSoft }]}>
        <T v="caption" color={t.gold}>{L(`改成 · ${c.newLabel}`, `After · ${c.newLabel}`)}</T>
        <T v="callout">{c.after || L('（空）', '(empty)')}</T>
      </View>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex label={busy ? L('正在改…', 'Changing…') : L('采用', 'Apply')} onPress={onApply} />
        <Btn flex kind="quiet" label={L('不改', 'Keep as is')} onPress={onDrop} />
      </View>
    </Card>
  );
}

function NameSheet({ p, close, onSaved }: { p: ButlerProfile; close: () => void; onSaved: (p: ButlerProfile) => void }) {
  const t = useTheme();
  const [name, setName] = useState(p.customName ? p.name : '');
  const [err, setErr] = useState('');
  const save = (v: string) => butlerApi.patch({ name: v.trim() }).then((d) => { close(); onSaved(d); }).catch((e) => setErr(errText(e)));
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={name} onChangeText={setName} maxLength={24} placeholder={p.customName ? L('名字', 'Name') : p.name} placeholderTextColor={t.ink3}
        accessibilityLabel={L('管家的名字', "The butler's name")} onSubmitEditing={() => save(name)} returnKeyType="done"
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <T v="caption" color={t.ink3}>{L('好友看到的就是这个名字。留空 = 「你的名字 + 的管家」。', 'Friends see this name. Leave it empty for "<your name>\'s butler".')}</T>
      <Btn label={L('保存', 'Save')} onPress={() => save(name)} />
      {p.customName ? <Btn kind="quiet" label={L('用默认的名字', 'Use the default name')} onPress={() => save('')} /> : null}
      {err ? <T v="callout" color={t.bad}>{err}</T> : null}
    </View>
  );
}

/** 请教记录（仅你可见）：问了哪位赛博、带去的要点、它怎么说。 */
function ConsultsSheet() {
  const t = useTheme();
  const [items, setItems] = useState<Consult[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    butlerApi.consults().then((x) => { if (live) setItems(x); }).catch((e) => { if (live) setErr(errText(e)); });
    return () => { live = false; };
  }, []);
  if (err) return <T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T>;
  if (!items) return <ActivityIndicator color={t.gold} />;
  if (!items.length) return <T v="callout" color={t.ink2}>{L('还没有请教过。', 'Nothing asked yet.')}</T>;
  return (
    <View style={{ gap: space.md }}>
      {items.slice(0, 20).map((c) => (
        <View key={c.id} style={[styles.consult, { borderColor: t.line }]}>
          <T v="caption" color={t.ink3}>{c.line}</T>
          <T v="callout">{L(`要点：${c.gist}`, `Gist: ${c.gist}`)}</T>
          {c.answer ? <T v="callout" color={t.ink2}>{L(`${c.name} 的看法：${c.answer}`, `${c.name}'s view: ${c.answer}`)}</T> : null}
        </View>
      ))}
    </View>
  );
}

export function ButlerScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [p, setP] = useState<ButlerProfile | null>(null);
  const [err, setErr] = useState('');
  const [tier, setTier] = useState('friend');
  const [samples, setSamples] = useState<Samples | null>(null);
  const [sErr, setSErr] = useState('');
  const [round, setRound] = useState(0);   // 例句重读一次（改了 / 撤销了以后）
  const [pending, setPending] = useState<SampleChange | null>(null);
  const [recent, setRecent] = useState<SampleChange | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => butlerApi.get().then((d) => { setP(d); setErr(''); }).catch((e) => setErr(errText(e))), []);
  useFocusEffect(useCallback(() => {
    const a = takeAvatar(AVATAR_KEY);  // 形象编辑器交回来的
    if (a) butlerApi.patch({ avatar: a }).then(setP).catch((e) => showError(L('保存失败', "Couldn't save"), e));
    else load();
  }, [load]));
  useEffect(() => {
    let live = true;
    butlerApi.samples(tier).then((s) => { if (live) { setSamples(s); setSErr(''); } }).catch((e) => { if (live) setSErr(errText(e)); });
    return () => { live = false; };
  }, [tier, round]);

  const pickTier = (v: string) => { if (v === tier) return; setTier(v); setSamples(null); setSErr(''); };
  const again = () => { setSamples(null); setRound((n) => n + 1); };
  const preview = (s: Sample, level: string) => {
    setBusy(s.key);
    butlerApi.preview(tier, s.key, level).then(setPending).catch((e) => showError(L('没试答出来', "Couldn't try it"), e)).finally(() => setBusy(null));
  };
  const openOptions = (s: Sample) => sheet.open({ title: L('改这一句', 'Change this line'), content: (close) => <OptionsSheet s={s} onPick={(lv) => { close(); preview(s, lv); }} /> });
  const decide = (c: SampleChange, action: 'apply' | 'drop') => {
    setBusy(c.id);
    butlerApi.decide(c.id, action).then((done) => {
      setPending(null);
      if (action === 'apply') { setRecent(done); again(); }
    }).catch((e) => showError(action === 'apply' ? L('修改失败', "Couldn't update") : L('操作失败', "Couldn't do that"), e)).finally(() => setBusy(null));
  };
  const undo = (c: SampleChange) => butlerApi.undo(c.id).then(() => { setRecent(null); again(); }).catch((e) => showError(L('撤销失败', "Couldn't undo"), e));
  const toggleCyber = (id: string, on: boolean) => {
    if (!p) return;
    const next = p.cybers.filter((c) => (c.id === id ? on : c.on)).map((c) => c.id);
    butlerApi.patch({ cybers: next }).then(setP).catch((e) => showError(L('修改失败', "Couldn't update"), e));
  };
  const editAvatar = () => p && sheet.open({ title: L('形象', 'Avatar'), content: (close) => (
    <View>
      <MenuItem icon={Palette} title={L('自己捏 / 上传图片', 'Make one or upload a picture')} chevron onPress={() => {
        close();
        nav.navigate('CyberAvatarEdit', { avatar: isOrbit(p.avatar) ? undefined : p.avatar as CyberAvatarSpec, name: p.name, key: AVATAR_KEY, role: 'butler' });
      }} />
      {!isOrbit(p.avatar) ? <MenuItem icon={RefreshCw} title={L('换回环与点', 'Back to the ring and dot')} onPress={() => {
        close();
        butlerApi.patch({ reset_avatar: true }).then(setP).catch((e) => showError(L('保存失败', "Couldn't save"), e));
      }} /> : null}
    </View>
  ) });
  const rename = () => p && sheet.open({ title: L('管家的名字', "The butler's name"), content: (close) => <NameSheet p={p} close={close} onSaved={setP} /> });

  if (!p) {
    return (
      <Screen>
        <NavHeader title={L('管家', 'Butler')} onBack={() => nav.goBack()} />
        <T v="callout" color={err ? t.bad : t.ink3} style={{ padding: space.lg }}>{err ? L(`无法加载：${err}`, `Couldn't load: ${err}`) : L('正在加载…', 'Loading…')}</T>
      </Screen>
    );
  }
  const goSaid = (held?: boolean) => nav.navigate('ButlerSaid', held ? { held: true } : undefined);
  return (
    <Screen>
      <NavHeader title={L('管家', 'Butler')} sub={L('只对外 · 替你出面', 'Speaks for you outside')} onBack={() => nav.goBack()} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled"
        refreshControl={<PullRefresh onRefresh={() => { again(); return load(); }} />}>
        <View style={{ alignItems: 'center', gap: 6 }}>
          <Pressable onPress={editAvatar} accessibilityRole="button" accessibilityLabel={L('换形象', 'Change avatar')}>
            <ButlerAvatar avatar={p.avatar} size={96} />
          </Pressable>
          <Pressable onPress={rename} accessibilityRole="button" accessibilityLabel={L('改名字', 'Rename')} style={styles.nameRow}>
            <T v="title" style={{ fontWeight: '700' }}>{p.name}</T>
            <Pencil size={15} color={t.ink3} />
          </Pressable>
          <T v="callout" color={t.ink2} style={{ textAlign: 'center' }}>{L('管家 · 好友群、好友的追问、别家 Agent', 'Butler · friend groups, friends’ questions, other Agents')}</T>
        </View>
        <View style={styles.stats}>
          <Pressable disabled={!hasRoute('Models')} onPress={() => nav.navigate('Models')} accessibilityRole="button" style={[styles.stat, { backgroundColor: t.surface }]}>
            <T v="headline" numberOfLines={1}>{p.model.name || '—'}</T><T v="caption" color={t.ink3}>{L('模型', 'Model')}</T>
          </Pressable>
          <Pressable onPress={() => goSaid()} accessibilityRole="button" style={[styles.stat, { backgroundColor: t.surface }]}>
            <T v="headline">{L(`${p.week.said} 句`, `${p.week.said}`)}</T><T v="caption" color={t.ink3}>{L('本周说了', 'Said this week')}</T>
          </Pressable>
          <Pressable onPress={() => goSaid(true)} accessibilityRole="button" style={[styles.stat, { backgroundColor: t.surface }]}>
            <T v="headline" color={p.week.held ? t.warn : t.ink}>{L(`${p.week.held} 句`, `${p.week.held}`)}</T><T v="caption" color={t.ink3}>{L('Doorman 扣下', 'Held by Doorman')}</T>
          </Pressable>
        </View>
        <T v="caption" color={t.ink3} style={{ marginTop: space.sm, textAlign: 'center' }}>{L('模型就是设置里「按用途选」的名片那一项，在设置页改。', 'The model is the card use in model settings; change it there.')}</T>

        <SectionLabel>{L('对外口径', 'How it answers')}</SectionLabel>
        <Segmented value={tier} onChange={pickTier} options={p.tiers.map((x) => ({ value: x.id, label: x.label }))} />
        <T v="caption" color={t.ink3} style={{ marginTop: space.sm }}>{samples?.intro ?? L('只是试答：不发出去，不进「说过的话」。', 'Just a try: nothing is sent, and it isn\'t in what it said.')}</T>
        {pending ? <View style={{ marginTop: space.md }}><SampleChangeCard c={pending} busy={busy === pending.id} onApply={() => decide(pending, 'apply')} onDrop={() => decide(pending, 'drop')} /></View> : null}
        {recent ? (
          <View style={styles.recent}>
            <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={2}>{L(`刚改 · ${recent.tierLabel} · ${recent.categoryLabel}：${recent.oldLabel} → ${recent.newLabel}`,
              `Just changed · ${recent.tierLabel} · ${recent.categoryLabel}: ${recent.oldLabel} → ${recent.newLabel}`)}</T>
            <Btn kind="quiet" label={L('撤销', 'Undo')} icon={<Undo2 size={14} color={t.ink} />} onPress={() => undo(recent)} />
          </View>
        ) : null}
        {sErr ? <Card style={{ marginTop: space.md, gap: space.sm }}>
          <T v="callout" color={t.bad}>{L(`没试答出来：${sErr}`, `Couldn't try them: ${sErr}`)}</T>
          <Btn kind="quiet" label={L('重试', 'Retry')} onPress={again} />
        </Card> : null}
        {!samples && !sErr ? (
          <View style={styles.trying}><ActivityIndicator color={t.gold} /><T v="callout" color={t.ink3}>{L('正在试答…', 'Trying the questions…')}</T></View>
        ) : null}
        {(samples?.items ?? []).map((s) => (
          <Card key={s.key} style={{ marginTop: space.md, gap: space.sm }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
              <T v="caption" color={t.ink3} style={{ flex: 1 }}>{`${s.categoryLabel} · ${s.options.find((o) => o.current)?.label ?? ''}`}</T>
              {s.asks ? <Pill label={L('会先问你', 'Asks you first')} tone="gold" /> : null}
            </View>
            <T v="callout" color={t.ink2}>{L(`问：${s.question}`, `Q: ${s.question}`)}</T>
            <T v="body">{L(`答：${s.reply}`, `A: ${s.reply}`)}</T>
            <Pressable onPress={() => openOptions(s)} disabled={busy === s.key} accessibilityRole="button" style={({ pressed }) => [styles.change, { opacity: pressed ? 0.6 : 1 }]}>
              {busy === s.key ? <ActivityIndicator size="small" color={t.gold} /> : null}
              <T v="callout" color={t.gold}>{busy === s.key ? L('正在试答…', 'Trying…') : L('改这一句', 'Change this line')}</T>
              {busy === s.key ? null : <ChevronRight size={15} color={t.gold} />}
            </Pressable>
          </Card>
        ))}

        <SectionLabel>{L('先问你', 'Asks you first')}</SectionLabel>
        <Card style={{ gap: space.md }}>
          {p.rules.map((r) => (
            <View key={r.id} style={{ flexDirection: 'row', gap: space.md, alignItems: 'flex-start' }}>
              <Lock size={16} color={r.id === 'health' ? t.bad : t.gold} style={{ marginTop: 2 }} />
              <View style={{ flex: 1, gap: 2 }}>
                <T v="headline" style={{ fontSize: 15 }}>{r.title}</T>
                <T v="callout" color={t.ink2}>{r.text}</T>
              </View>
            </View>
          ))}
          <T v="caption" color={t.ink3}>{L('这两条写死，不做开关。', 'These two are fixed; there is no switch.')}</T>
        </Card>

        <SectionLabel>{L('可以请教的赛博', 'Cybers it may ask')}</SectionLabel>
        <Card style={{ gap: space.xs, paddingVertical: space.sm }}>
          <T v="caption" color={t.ink3} style={{ paddingVertical: space.xs }}>{L('需要时回去问你的赛博：只带问题的要点，好友的原话和名字不给；回答写明是谁的看法。健康、饮食、睡眠的从不去问。',
            "When it needs to, it asks your Cybers with only the gist of the question, never a friend's words or name, and says whose view it is. Health, diet and sleep questions are never taken to them.")}</T>
          {p.cybers.length ? p.cybers.map((c) => (
            <View key={c.id} style={styles.cyberRow}>
              <CyberAvatar avatar={c.avatar} size={36} />
              <View style={{ flex: 1, gap: 2 }}>
                <T v="headline" numberOfLines={1}>{c.name}</T>
                <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{[c.tag, c.modelName].filter(Boolean).join(' · ')}</T>
              </View>
              <Switch value={c.on} onValueChange={(v) => toggleCyber(c.id, v)} trackColor={{ true: t.goldFill, false: t.line }} thumbColor={t.surface}
                accessibilityLabel={L(`请教 ${c.name}`, `Ask ${c.name}`)} />
            </View>
          )) : <T v="callout" color={t.ink2} style={{ paddingVertical: space.sm }}>{L('还没有赛博。', 'No Cybers yet.')}</T>}
          <Pressable onPress={() => sheet.open({ title: L('请教记录 · 仅你可见', 'What it asked · only you see this'), content: () => <ConsultsSheet /> })}
            accessibilityRole="button" style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="callout" color={t.gold}>{L('请教记录', 'What it asked')}</T><ChevronRight size={15} color={t.gold} />
          </Pressable>
        </Card>

        <Pressable onPress={() => goSaid()} accessibilityRole="button" style={({ pressed }) => [styles.saidRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline">{L('说过的话', 'What it said')}</T>
            <T v="callout" color={t.ink2}>{L('这周对外说过的每句；Doorman 扣下的由你放行或不放', 'Everything it said this week; you release or keep what Doorman held')}</T>
          </View>
          <ChevronRight size={18} color={t.ink3} />
        </Pressable>
        <T v="caption" color={t.ink3} style={{ marginTop: space.md }}>{p.note}</T>
      </KeyboardScroll>
    </Screen>
  );
}

const styles = StyleSheet.create({
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  stats: { flexDirection: 'row', gap: space.sm, marginTop: space.lg },
  stat: { flex: 1, borderRadius: radius.md, paddingVertical: space.md, paddingHorizontal: space.sm, alignItems: 'center', gap: 2 },
  option: { borderWidth: 1, borderRadius: radius.md, padding: space.md, gap: 4 },
  box: { borderRadius: radius.sm, padding: space.sm, gap: 2 },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
  recent: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: space.xs, marginTop: space.sm },
  trying: { flexDirection: 'row', alignItems: 'center', gap: space.sm, marginTop: space.md },
  change: { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-end', gap: 4, paddingTop: space.xs },
  cyberRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  link: { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-end', gap: 2, paddingVertical: space.xs },
  consult: { borderLeftWidth: 2, paddingLeft: space.md, gap: 4 },
  saidRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg, padding: space.md, marginTop: space.xl },
});

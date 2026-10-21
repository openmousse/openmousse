// 形象编辑器（主线第三步，设计稿第 42 张）：页头「取消 · 形象 · 完成」；大头像 + 名字和「赛博 · 模型」；「自己捏 / 上传图片」切换。
// 自己捏：六个页签（发型 胡子 配饰 衣服 肤色 底色），上面一排部件格（每格画出换上以后的整个头像，选中的金描边），下面一排颜色
// （发色 / 衣服颜色 / 配饰颜色），一行说明随页签变，「随机一个」。机器类的赛博换图标和底色。
// 上传：「从相册选择」「拍照」（chatInput.ts 现有的 pickMedia），图片裁成圆形、外圈照样是金环，只存在你的服务器上。
// 路由 CyberAvatarEdit，params：avatar（现在的）、name、model（「赛博 · 模型」那一行）、cyber（改一位已有的：完成时直接存）或 key（草稿页：交回去）；
// role butler = 管家的资料页来的（那一行写「管家 · 只对外」，结果照 key 交回去）。
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { ACC_COLORS, BGS, CLOTHES, CyberAvatar, DEFAULT_PARTS, HAIR_COLORS, SKINS, partsOf } from '../../components/agents/CyberAvatar';
import { ACC_KEYS, ACC_COLOR_KEYS, BEARD_KEYS, BG_KEYS, CLOTHES_KEYS, COLLAR_KEYS, HAIR_COLOR_KEYS, HAIR_KEYS, SKIN_KEYS, putAvatar, randomParts } from '../../components/agents/avatarParts';
import { pickMedia } from '../../components/chatInput';
import { Camera, ImageIcon, Shuffle } from '../../components/icons';
import { Btn, Card, Segmented, T, showError } from '../../components/ui';
import { cybersApi, uploadAvatar, type AvatarParts, type CyberAvatarSpec, type Machine } from '../../api/cybers';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

type Tab = 'hair' | 'beard' | 'acc' | 'clothes' | 'skin' | 'bg';
const TABS: { key: Tab; label: () => string; hint: () => string }[] = [
  { key: 'hair', label: () => L('发型', 'Hair'), hint: () => L('上面挑发型，下面挑发色，颜色直接画在头像上。', 'Pick a hairstyle above and a hair color below; the color is drawn right on the avatar.') },
  { key: 'beard', label: () => L('胡子', 'Beard'), hint: () => L('胡子跟着发色走，胡茬自动浅一些。', 'The beard follows the hair color; stubble is a little lighter.') },
  { key: 'acc', label: () => L('配饰', 'Extras'), hint: () => L('可以多选，再点一次取消；颜色对眼镜框、耳机、帽子、耳环都生效。', 'Pick several, tap again to remove; the color applies to glasses, headphones, hat and earrings.') },
  { key: 'clothes', label: () => L('衣服', 'Clothes'), hint: () => L('上面挑衣领，下面挑衣服颜色；高领跟着衣服走。', 'Pick a collar above and a clothes color below; a turtleneck follows the clothes.') },
  { key: 'skin', label: () => L('肤色', 'Skin'), hint: () => L('五种肤色，不画五官。', 'Five skin tones; no facial features.') },
  { key: 'bg', label: () => L('底色', 'Background'), hint: () => L('底色在头像后面，群里一眼能认出是谁。', 'The background sits behind the figure, so you can tell who it is at a glance in a group.') },
];
const MACHINES: Machine[] = ['timer', 'sides', 'rewind'];
const CELL = 58;

function Cell({ selected, onPress, label, children }: { selected: boolean; onPress: () => void; label: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected }}
      style={({ pressed }) => [styles.cell, { borderColor: selected ? t.goldFill : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
      {children}
    </Pressable>
  );
}

function Swatch({ color, selected, onPress, label }: { color: string; selected: boolean; onPress: () => void; label: string }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected }}
      style={[styles.swatch, { borderColor: selected ? t.goldFill : t.line }]}>
      <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: color }} />
    </Pressable>
  );
}

export function CyberAvatarScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const insets = useSafeAreaInsets();
  const params = (useRoute<any>().params ?? {}) as { avatar?: CyberAvatarSpec; name?: string; model?: string; cyber?: string; key?: string; role?: 'butler' };
  const start = params.avatar ?? DEFAULT_PARTS;
  const [mode, setMode] = useState<'make' | 'upload'>(start && 'upload' in start ? 'upload' : 'make');
  const [spec, setSpec] = useState<CyberAvatarSpec>(start);
  const [tab, setTab] = useState<Tab>('hair');
  const [busy, setBusy] = useState(false);
  const machine = 'machine' in spec ? spec : null;
  const parts: AvatarParts = partsOf('upload' in spec || 'machine' in spec ? null : spec);
  const set = (patch: Partial<AvatarParts>) => setSpec({ ...parts, ...patch, acc: { ...parts.acc, ...(patch.acc ?? {}) } });
  const with_ = (patch: Partial<AvatarParts>): AvatarParts => ({ ...parts, ...patch, acc: { ...parts.acc, ...(patch.acc ?? {}) } });

  const done = async () => {
    if (busy) return;
    if (params.cyber) {
      setBusy(true);
      try { await cybersApi.patch(params.cyber, { avatar: spec }); } catch (e) { setBusy(false); showError(L('保存失败', "Couldn't save"), e); return; }
    } else if (params.key) putAvatar(params.key, spec);
    nav.goBack();
  };
  const upload = async (camera: boolean) => {
    try {
      const files = await pickMedia(camera);
      const f = files[0];
      if (!f) return;
      if (!f.mime.startsWith('image/')) { showError(L('只能用图片', 'Pictures only'), L('请选一张照片。', 'Pick a photo.')); return; }
      setBusy(true);
      const id = await uploadAvatar(f);
      setSpec({ upload: id });
    } catch (e) {
      showError(L('上传失败', 'Upload failed'), e);
    } finally {
      setBusy(false);
    }
  };

  const hint = TABS.find((x) => x.key === tab)?.hint() ?? '';
  return (
    <View style={{ flex: 1, backgroundColor: t.bg, paddingTop: insets.top }}>
      <View style={[styles.head, { borderBottomColor: t.line }]}>
        <Pressable onPress={() => nav.goBack()} hitSlop={8} accessibilityRole="button"><T v="body" color={t.ink2}>{L('取消', 'Cancel')}</T></Pressable>
        <T v="headline">{L('形象', 'Avatar')}</T>
        <Pressable onPress={done} hitSlop={8} accessibilityRole="button" accessibilityState={{ busy }}><T v="headline" color={t.gold}>{L('完成', 'Done')}</T></Pressable>
      </View>
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl + insets.bottom }}>
        <View style={{ alignItems: 'center', gap: space.sm }}>
          <CyberAvatar avatar={spec} size={96} label={params.name} />
          {params.name ? <T v="title">{params.name}</T> : null}
          <T v="callout" color={t.ink3}>{params.role === 'butler' ? L('管家 · 只对外', 'Butler · speaks for you outside')
            : params.model ? L(`赛博 · ${params.model}`, `Cyber · ${params.model}`) : L('赛博', 'Cyber')}</T>
        </View>
        <View style={{ marginTop: space.lg }}>
          <Segmented value={mode} onChange={(v) => { setMode(v); if (v === 'make' && 'upload' in spec) setSpec(DEFAULT_PARTS); }}
            options={[{ value: 'make', label: L('自己捏', 'Make one') }, { value: 'upload', label: L('上传图片', 'Upload a picture') }]} />
        </View>

        {mode === 'upload' ? (
          <Card style={{ marginTop: space.lg, gap: space.md }}>
            <Btn label={L('从相册选择', 'Choose from Photos')} kind="quiet" icon={<ImageIcon size={16} color={t.ink} />} onPress={() => upload(false)} />
            <Btn label={L('拍照', 'Take a photo')} kind="quiet" icon={<Camera size={16} color={t.ink} />} onPress={() => upload(true)} />
            <T v="callout" color={t.ink2}>{busy ? L('正在上传…', 'Uploading…') : L('图片裁成圆形，外圈照样是金环，群里一眼能看出是赛博。图片只存在你的服务器上。',
              "The picture is cropped round and keeps the gold ring, so it's clearly a Cyber in a group. It's stored only on your server.")}</T>
          </Card>
        ) : machine ? (
          <>
            <T v="label" color={t.ink3} style={{ marginTop: space.xl, marginBottom: space.sm }}>{L('图标', 'ICON')}</T>
            <View style={styles.grid}>
              {MACHINES.map((m) => (
                <Cell key={m} selected={machine.machine === m} label={m} onPress={() => setSpec({ machine: m, bg: machine.bg })}>
                  <CyberAvatar avatar={{ machine: m, bg: machine.bg }} size={CELL - 6} ring={false} />
                </Cell>
              ))}
            </View>
            <T v="label" color={t.ink3} style={{ marginTop: space.lg, marginBottom: space.sm }}>{L('底色', 'BACKGROUND')}</T>
            <View style={styles.swatches}>
              {BG_KEYS.map((b) => <Swatch key={b} color={BGS[b]} label={b} selected={(machine.bg ?? 'cyan') === b} onPress={() => setSpec({ machine: machine.machine, bg: b })} />)}
            </View>
            <View style={{ marginTop: space.lg }}><Btn label={L('改成半身像', 'Use a figure instead')} kind="quiet" onPress={() => setSpec(randomParts())} /></View>
          </>
        ) : (
          <>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingVertical: space.md }}>
              {TABS.map((x) => {
                const on = x.key === tab;
                return (
                  <Pressable key={x.key} onPress={() => setTab(x.key)} accessibilityRole="tab" accessibilityState={{ selected: on }}
                    style={[styles.tab, { backgroundColor: on ? t.goldSoft : t.surface, borderColor: on ? t.goldFill : t.line }]}>
                    <T v="caption" color={on ? t.gold : t.ink2} style={{ fontSize: 13 }}>{x.label()}</T>
                  </Pressable>
                );
              })}
            </ScrollView>
            <View style={styles.grid}>
              {tab === 'hair' ? HAIR_KEYS.map((h) => (
                <Cell key={h} selected={parts.hair === h} label={h} onPress={() => set({ hair: h })}><CyberAvatar avatar={with_({ hair: h })} size={CELL - 6} ring={false} /></Cell>
              )) : tab === 'beard' ? BEARD_KEYS.map((b) => (
                <Cell key={b} selected={parts.beard === b} label={b} onPress={() => set({ beard: b })}><CyberAvatar avatar={with_({ beard: b })} size={CELL - 6} ring={false} /></Cell>
              )) : tab === 'acc' ? ACC_KEYS.map((a) => (
                <Cell key={a} selected={parts.acc[a]} label={a} onPress={() => set({ acc: { ...parts.acc, [a]: !parts.acc[a] } })}>
                  <CyberAvatar avatar={with_({ acc: { ...parts.acc, [a]: true } })} size={CELL - 6} ring={false} />
                </Cell>
              )) : tab === 'clothes' ? COLLAR_KEYS.map((c) => (
                <Cell key={c} selected={parts.collar === c} label={c} onPress={() => set({ collar: c })}><CyberAvatar avatar={with_({ collar: c })} size={CELL - 6} ring={false} /></Cell>
              )) : tab === 'skin' ? SKIN_KEYS.map((s) => (
                <Cell key={s} selected={parts.skin === s} label={s} onPress={() => set({ skin: s })}><CyberAvatar avatar={with_({ skin: s })} size={CELL - 6} ring={false} /></Cell>
              )) : BG_KEYS.map((b) => (
                <Cell key={b} selected={parts.bg === b} label={b} onPress={() => set({ bg: b })}><CyberAvatar avatar={with_({ bg: b })} size={CELL - 6} ring={false} /></Cell>
              ))}
            </View>
            {tab === 'hair' || tab === 'beard' ? (
              <View style={styles.swatches}>{HAIR_COLOR_KEYS.map((c) => <Swatch key={c} color={HAIR_COLORS[c][0]} label={c} selected={parts.hairColor === c} onPress={() => set({ hairColor: c })} />)}</View>
            ) : tab === 'clothes' ? (
              <View style={styles.swatches}>{CLOTHES_KEYS.map((c) => <Swatch key={c} color={CLOTHES[c][0]} label={c} selected={parts.clothes === c} onPress={() => set({ clothes: c })} />)}</View>
            ) : tab === 'acc' ? (
              <View style={styles.swatches}>{ACC_COLOR_KEYS.map((c) => <Swatch key={c} color={ACC_COLORS[c]} label={c} selected={parts.accColor === c} onPress={() => set({ accColor: c })} />)}</View>
            ) : tab === 'skin' ? (
              <View style={styles.swatches}>{SKIN_KEYS.map((s) => <Swatch key={s} color={SKINS[s]} label={s} selected={parts.skin === s} onPress={() => set({ skin: s })} />)}</View>
            ) : null}
            <T v="callout" color={t.ink2} style={{ marginTop: space.md }}>{hint}</T>
            <View style={{ marginTop: space.lg }}><Btn label={L('随机一个', 'Random')} kind="quiet" icon={<Shuffle size={16} color={t.ink} />} onPress={() => setSpec(randomParts())} /></View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', height: 48, paddingHorizontal: space.lg, borderBottomWidth: StyleSheet.hairlineWidth },
  tab: { height: 32, paddingHorizontal: 14, borderRadius: 16, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  cell: { width: CELL, height: CELL, borderRadius: radius.md, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  swatches: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.md },
  swatch: { width: 36, height: 36, borderRadius: 18, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
});

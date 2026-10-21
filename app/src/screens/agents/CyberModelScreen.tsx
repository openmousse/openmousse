// 赛博的模型（主线第三步，设计稿 D）：三栏 推荐（适合这位赛博的，每个一句理由）/ 全部 / 本机；开源的在前；每行写厂商、一句用途、
// 按量 / 本机 / 订阅、成本档。下面「还能接」四条渠道：OpenRouter、本机模型、各家的 API 密钥、ChatGPT 套餐（在设置的「模型服务」里接）。
// 套餐照样列出来，但标灰、写明「赛博暂时不能用套餐」。每位赛博可以用不同的模型；选了直接存（PATCH /api/cybers/{id}）。
// 路由 CyberModel（params id）。数据：GET /api/cyber-models?cyber=（服务器 cybermodels.py，只读现有的模型设置）。
import React, { useCallback, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { ModelChoice } from '../../components/agents/ModelChoices';
import { errText } from '../../components/agents/NewFlow';
import { Check, ChevronRight } from '../../components/icons';
import { Card, NavHeader, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../../components/ui';
import { cybersApi, type CyberModels } from '../../api/cybers';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import { KeyboardScroll } from '../../components/keyboard';

export function CyberModelScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id } = (useRoute<any>().params ?? {}) as { id: string };
  const [tab, setTab] = useState<'rec' | 'all' | 'local'>('rec');
  const [data, setData] = useState<CyberModels | null>(null);
  const [err, setErr] = useState('');
  const [saving, setSaving] = useState(false);
  const load = useCallback((probe = false) => cybersApi.models({ cyber: id, probe }).then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))), [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const pick = (ref: string | null) => {
    if (saving) return;
    setSaving(true);
    cybersApi.patch(id, { model: ref }).then(() => load()).catch((e) => showError(L('保存失败', "Couldn't save"), e)).finally(() => setSaving(false));
  };
  const rows = !data ? [] : tab === 'rec' ? data.recommended : tab === 'all' ? data.all : data.local;
  return (
    <Screen>
      <NavHeader title={L('模型', 'Model')} sub={data ? L(`现在用 ${data.inUseName}`, `Now using ${data.inUseName}`) : undefined} onBack={() => nav.goBack()} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={() => load(true)} />}>
        <Segmented value={tab} onChange={setTab} options={[{ value: 'rec', label: L('推荐', 'Recommended') }, { value: 'all', label: L('全部', 'All') }, { value: 'local', label: L('本机', 'Local') }]} />
        {err ? <T v="callout" color={t.bad} style={{ marginTop: space.md }}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T> : null}
        <Card style={{ marginTop: space.md, paddingVertical: space.xs }}>
          <Pressable onPress={() => pick(null)} accessibilityRole="button" style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="headline">{L('跟默认的走', 'Follow the default')}</T>
              <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{L('用设置里给赛博选的那个（不能是套餐）', 'Uses the one picked for Cybers in settings (never a plan)')}</T>
            </View>
            {data && !data.current ? <Check size={18} color={t.gold} /> : null}
          </Pressable>
          {rows.map((m) => <ModelChoice key={m.ref} row={m} reason={tab === 'rec' ? m.reason : undefined} selected={m.ref === data?.current} onPress={() => pick(m.ref)} />)}
          {data && !rows.length ? <T v="callout" color={t.ink3} style={{ paddingVertical: space.md }}>{tab === 'local' ? L('这台电脑上没有探到本机的模型。', 'No local models found on this computer.') : L('没有可选的模型。', 'No models to pick.')}</T> : null}
        </Card>
        <T v="caption" color={t.ink3} style={{ marginTop: space.sm }}>{L('开源模型排在前面。闭源的也能用，它读到的会发到那一家。', 'Open-weight models come first. Closed ones work too; what it reads goes to that vendor.')}</T>

        <SectionLabel>{L('还能接', 'More ways to connect')}</SectionLabel>
        <Card style={{ paddingVertical: space.xs }}>
          {(data?.channels ?? []).map((c) => (
            <Pressable key={c.id} onPress={() => nav.navigate('SettingsProviders')} accessibilityRole="button" style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
              <View style={{ flex: 1, gap: 2 }}>
                <T v="headline">{c.name}</T>
                <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{c.line}</T>
              </View>
              {c.connected ? <T v="caption" color={t.good}>{L('已连接', 'Connected')}</T> : null}
              <ChevronRight size={16} color={t.ink3} />
            </Pressable>
          ))}
        </Card>
      </KeyboardScroll>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
});

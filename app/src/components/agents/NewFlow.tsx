// 一句话新建的共用几样（主线第三步，设计稿第 41 张）：赛博和功能 Agent 走同一条路：描述 / 模板 → 补问 → 草稿 → 创建。
// 补问：顶上灰字是你的描述，下面两三道点选题（不用打字；再点一次取消），底下一行说明和「生成草稿」。
import React from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import type { Answer, Question } from '../../api/cybers';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { Btn, Card, T } from '../ui';

/** 大输入框（「描述你想要的赛博」）。 */
export function DescribeBox({ value, onChange, placeholder, label }: { value: string; onChange: (v: string) => void; placeholder: string; label: string }) {
  const t = useTheme();
  return (
    <TextInput value={value} onChangeText={onChange} multiline placeholder={placeholder} placeholderTextColor={t.ink3} accessibilityLabel={label}
      style={[type.body, styles.describe, { backgroundColor: t.surface, color: t.ink }]} />
  );
}

/** 点选的小块：选中金色描边。 */
export function Choice({ label, on, onPress, off }: { label: string; on: boolean; onPress: () => void; off?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} disabled={off} accessibilityRole="button" accessibilityState={{ selected: on, disabled: off }}
      style={({ pressed }) => [styles.choice, { backgroundColor: on ? t.goldSoft : t.surface, borderColor: on ? t.goldFill : t.line, opacity: off ? 0.45 : pressed ? 0.7 : 1 }]}>
      <T v="callout" color={on ? t.gold : t.ink} style={{ fontWeight: on ? '600' : '400' }}>{label}</T>
    </Pressable>
  );
}

/** 补问那一步。picked：题 id → 选的那一项。 */
export function QuestionsStep({ said, questions, picked, onPick, onGo, busy, error }: {
  said: string; questions: Question[]; picked: Record<string, string>; onPick: (q: Question, option: string) => void; onGo: () => void; busy: boolean; error?: string;
}) {
  const t = useTheme();
  return (
    <View style={{ gap: space.lg }}>
      <T v="callout" color={t.ink3}>{said}</T>
      {questions.map((q) => (
        <Card key={q.id} style={{ gap: space.md }}>
          <T v="headline">{q.text}</T>
          <View style={styles.choices}>
            {q.options.map((o) => <Choice key={o} label={o} on={picked[q.id] === o} onPress={() => onPick(q, o)} />)}
          </View>
        </Card>
      ))}
      <T v="callout" color={t.ink3}>{L('问题由模型按你的描述出，点选即可；不想答可以直接生成。', "The model wrote these from your description: just tap. You can also skip them and generate now.")}</T>
      <Btn label={busy ? L('正在生成…', 'Generating…') : L('生成草稿', 'Generate draft')} onPress={onGo} />
      {error ? <T v="callout" color={t.bad}>{error}</T> : null}
    </View>
  );
}

/** 点了的答案 → 发给服务器的 [{q, a}]（题面 + 选项的原话）。 */
export const answersOf = (questions: Question[] | null, picked: Record<string, string>): Answer[] =>
  (questions ?? []).filter((q) => picked[q.id]).map((q) => ({ q: q.text, a: picked[q.id] }));

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const styles = StyleSheet.create({
  describe: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13, minHeight: 120, textAlignVertical: 'top' },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  choice: { minHeight: 36, paddingHorizontal: 14, paddingVertical: 7, borderRadius: 18, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
});

// 日期框（2026-10-14）：上面是日期本身，下面「今天 / 明天 / +3 天 / +1 周」。值一律是本地的 YYYY-MM-DD 字符串（空 = 没填），和服务器收的一样。
// 日期本身分平台（DatePick.tsx / DatePick.native.tsx，Metro 按平台挑文件，网页包里不会有 datetimepicker）：
// 网页版照旧是输入框；手机上（1.0.6 起）点一下出系统的选择器：iOS 在框下面展开日历（inline），安卓弹对话框。
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { L } from '../i18n';
import { type, useTheme } from '../theme';
import { DatePick } from './DatePick';

const pad = (n: number) => String(n).padStart(2, '0');
export const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const plusDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return isoDay(d); };
/** YYYY-MM-DD → 本地那一天；别的写法（手填的、只有年月的）回 null。 */
export function parseDay(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim());
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.getMonth() === Number(m[2]) - 1 ? d : null;
}

/** 日期本身（两个平台的 DatePick 一样的参数）：text = 原样的值；date = 解析出来的那一天（解析不了是 null）。 */
export interface DatePickProps { text: string; date: Date | null; onText: (s: string) => void; onPick: (d: Date) => void; label: string; placeholder?: string }

const CHIPS = [0, 1, 3, 7];
const chipLabel = (n: number) => (n === 0 ? L('今天', 'Today') : n === 1 ? L('明天', 'Tomorrow') : n === 7 ? L('+1 周', '+1 week') : L(`+${n} 天`, `+${n} days`));

export function DateField({ value, onChange, label, placeholder }: { value: string; onChange: (v: string) => void; label: string; placeholder?: string }) {
  const t = useTheme();
  return (
    <View style={{ gap: 6 }}>
      <DatePick text={value} date={parseDay(value)} onText={onChange} onPick={(d) => onChange(isoDay(d))} label={label} placeholder={placeholder} />
      <View style={styles.opts}>
        {CHIPS.map((n) => (
          <Pressable key={n} onPress={() => onChange(plusDays(n))} accessibilityRole="button"
            style={[styles.opt, { backgroundColor: t.surface, borderColor: t.line }]}>
            <Text style={[type.caption, { fontSize: 13, fontWeight: '600', color: t.ink2 }]}>{chipLabel(n)}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  opts: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  opt: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
});

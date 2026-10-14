// 日期框里的日期本身 · 手机（1.0.6 起有 @react-native-community/datetimepicker；网页版是 DatePick.tsx，Metro 按平台挑文件）。
// 一行显示选好的日子（没选是灰字），点一下出系统的选择器：iOS 在这一行下面展开日历（inline，点了某一天就收起；
// 在顶上的年月滚轮里换月份也会报一次新日期，那时日子没变，日历留着），安卓弹系统对话框。右边的 × 清空。
// 值不是 YYYY-MM-DD 时（老数据、手填的）原样显示，选择器从今天开始。
import DateTimePicker, { DateTimePickerAndroid } from '@react-native-community/datetimepicker';
import React, { useState } from 'react';
import { Platform, Pressable, StyleSheet, View } from 'react-native';
import { L, lang } from '../i18n';
import { radius, space, useTheme } from '../theme';
import type { DatePickProps } from './DateField';
import { CalendarIcon, X } from './icons';
import { T } from './ui';

/** 2026年10月14日周三 / Wed, 14 Oct 2026：系统的日期格式，按 app 的语言（带年份，表单里不会认错年）。 */
const longDate = (d: Date) => d.toLocaleDateString(lang() === 'zh' ? 'zh-CN' : 'en-GB', { year: 'numeric', month: 'short', day: 'numeric', weekday: 'short' });

export function DatePick({ text, date, onText, onPick, label, placeholder }: DatePickProps) {
  const t = useTheme();
  const [open, setOpen] = useState(false);  // iOS：日历展开着
  const [today] = useState(() => new Date());  // 没填时选择器从这一天开始
  const base = date ?? today;
  const pick = () => {
    if (Platform.OS === 'android') {
      DateTimePickerAndroid.open({
        value: base, mode: 'date', onValueChange: (_e, d) => onPick(d),
        positiveButton: { label: L('确定', 'OK') }, negativeButton: { label: L('取消', 'Cancel') },
      });
    } else {
      setOpen((o) => !o);
    }
  };
  return (
    <View style={{ gap: 6 }}>
      <View style={[styles.field, { backgroundColor: t.surface }]}>
        <Pressable onPress={pick} accessibilityRole="button" accessibilityLabel={label} accessibilityValue={{ text: text || L('未填写', 'Not set') }}
          style={[styles.main, !text && { paddingRight: space.lg }]}>
          <CalendarIcon size={18} color={open ? t.gold : t.ink3} />
          <T v="body" color={text ? t.ink : t.ink3} numberOfLines={1} style={{ flex: 1 }}>{date ? longDate(date) : text || placeholder || L('选择日期', 'Pick a date')}</T>
        </Pressable>
        {text ? (
          <Pressable onPress={() => { setOpen(false); onText(''); }} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('清除日期', 'Clear the date')} style={styles.clear}>
            <X size={16} color={t.ink3} />
          </Pressable>
        ) : null}
      </View>
      {open && Platform.OS === 'ios' ? (
        <DateTimePicker value={base} mode="date" display="inline" themeVariant={t.mode} accentColor={t.gold}
          locale={lang() === 'zh' ? 'zh-Hans' : 'en'} onValueChange={(_e, d) => { if (d.getDate() !== base.getDate()) setOpen(false); onPick(d); }} />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  field: { flexDirection: 'row', alignItems: 'center', borderRadius: radius.md, paddingLeft: space.lg },
  main: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12 },
  clear: { width: 44, alignSelf: 'stretch', alignItems: 'center', justifyContent: 'center' },
});

// 点开一行：看全部字段、积木给的快捷动作（「用掉 1 份」）、改、问 Agent、删；按钮「记一样」是同一个表单的空白版。
// 改动直接写（是你自己的数据），写完重读看板。表单按表的字段类型出输入框；日期没有原生选择器（要新包），用输入框加「今天 / 明天 / +3 天」。
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { boardsApi, type Block, type BoardRow, type FieldDef } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { setChatDraft } from '../chatInput';
import { MessageCircle, Pencil, Trash2 } from '../icons';
import type { useSheet } from '../Sheet';
import { showError, T } from '../ui';
import type { BoardCtx } from './ctx';

type SheetApi = ReturnType<typeof useSheet>;
type Values = Record<string, string | boolean>;

const pad = (n: number) => String(n).padStart(2, '0');
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const plusDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return isoDay(d); };

/** 行里的值 → 输入框里的字符串。 */
function toInput(f: FieldDef, v: unknown): string | boolean {
  if (f.type === 'bool') return !!v;
  if (v == null) return '';
  if (f.type === 'datetime') return String(v).slice(0, 16).replace('T', ' ');
  return String(v);
}

/** 输入框 → 发给服务器的值（空 = null，清掉这个字段）。数字、日期的格式由服务器校验，写错了会说。 */
function fromInput(f: FieldDef, v: string | boolean): unknown {
  if (f.type === 'bool') return !!v;
  const s = String(v).trim();
  if (!s) return null;
  if (f.type === 'datetime') return s.replace(' ', 'T');
  return s;
}

export function openRow(sheet: SheetApi, ctx: BoardCtx, block: Block, row: BoardRow) {
  sheet.open({ title: row.title, content: (close) => <RowView ctx={ctx} block={block} row={row} close={close} /> });
}

export function openForm(sheet: SheetApi, ctx: BoardCtx, collection: string, title: string, fields: FieldDef[], defaults?: Record<string, unknown>) {
  // 默认值里的 today / tomorrow / now 换成真的日期、时间（积木配置里写的是这些词）
  const resolve = (f: FieldDef, v: unknown): unknown => {
    if (f.type === 'date' && (v === 'today' || v === 'tomorrow')) return plusDays(v === 'today' ? 0 : 1);
    if (f.type === 'datetime' && (v === 'now' || v === 'today')) { const d = new Date(); return `${isoDay(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
    return v;
  };
  const init: Values = Object.fromEntries(fields.map((f) => [f.key, toInput(f, resolve(f, defaults?.[f.key] ?? null))]));
  sheet.open({
    title,
    content: (close) => (
      <FieldForm fields={fields} init={init} saveLabel={L('记上', 'Save')} onCancel={close}
        onSave={async (vals) => {
          const row = Object.fromEntries(fields.map((f) => [f.key, fromInput(f, vals[f.key])]).filter(([, v]) => v !== null && v !== ''));
          await boardsApi.addRow(ctx.agent, collection, row);
          close();
          await ctx.reload();
        }} />
    ),
  });
}

function RowView({ ctx, block, row, close }: { ctx: BoardCtx; block: Block; row: BoardRow; close: () => void }) {
  const t = useTheme();
  const fields = block.data.fields ?? [];
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [busy, setBusy] = useState<string | null>(null);
  const [sure, setSure] = useState(false);  // 删之前再点一下
  const run = (key: string, fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(key);
    fn().then(() => { close(); return ctx.reload(); }).catch((e) => showError(L('没做成', "Didn't go through"), e)).finally(() => setBusy(null));
  };
  if (mode === 'edit') {
    const init: Values = Object.fromEntries(fields.map((f) => [f.key, toInput(f, row.data[f.key])]));
    return (
      <FieldForm fields={fields} init={init} saveLabel={L('保存', 'Save')} onCancel={() => setMode('view')}
        onSave={async (vals) => {
          const changed = Object.fromEntries(fields.filter((f) => vals[f.key] !== init[f.key]).map((f) => [f.key, fromInput(f, vals[f.key])]));
          if (Object.keys(changed).length) await boardsApi.patchRow(row.id, changed);
          close();
          await ctx.reload();
        }} />
    );
  }
  // 标题已经在弹层顶上了：和标题一样的那个字段不再列一遍
  const shown = fields.filter((f) => row.display[f.key] && row.display[f.key] !== '—' && row.display[f.key] !== row.title);
  const ask = () => { setChatDraft(ctx.agent, L(`关于「${row.title}」：`, `About "${row.title}": `)); close(); ctx.onChat(); };
  return (
    <View style={{ gap: space.md }}>
      {row.sub ? <T v="callout" color={t.ink2}>{row.sub}</T> : null}
      <View style={[styles.fields, { backgroundColor: t.surface }]}>
        {shown.map((f, i) => (
          <View key={f.key} style={[styles.fieldRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
            <T v="callout" color={t.ink3} style={{ width: 88 }}>{f.label}</T>
            <T v="body" style={{ flex: 1, fontSize: 15 }}>{row.display[f.key]}</T>
          </View>
        ))}
      </View>
      {(block.rowActions ?? []).map((a) => (
        <SheetBtn key={a.label} kind="primary" label={a.label} busy={busy === a.label} onPress={() => run(a.label, () => boardsApi.patchRow(row.id, a.set))} />
      ))}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        {block.edit !== false && fields.length ? <SheetBtn kind="quiet" label={L('改', 'Edit')} icon={<Pencil size={16} color={t.ink} />} onPress={() => setMode('edit')} grow /> : null}
        <SheetBtn kind="quiet" label={L('问它', 'Ask')} icon={<MessageCircle size={16} color={t.ink} />} onPress={ask} grow />
        {block.edit !== false ? (
          <SheetBtn kind="danger" label={sure ? L('确定删掉', 'Delete it') : L('删', 'Delete')} icon={<Trash2 size={16} color={t.bad} />} busy={busy === 'del'}
            onPress={() => (sure ? run('del', () => boardsApi.deleteRow(row.id)) : setSure(true))} />
        ) : null}
      </View>
      {sure ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('删掉的 30 天内能找回。', 'Deleted rows can be restored for 30 days.')}</T> : null}
    </View>
  );
}

function FieldForm({ fields, init, saveLabel, onSave, onCancel }: {
  fields: FieldDef[]; init: Values; saveLabel: string; onSave: (vals: Values) => Promise<void>; onCancel: () => void;
}) {
  const t = useTheme();
  const [vals, setVals] = useState<Values>(init);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string | boolean) => setVals((m) => ({ ...m, [k]: v }));
  const save = () => {
    if (busy) return;
    const missing = fields.filter((f) => f.required && (vals[f.key] === '' || vals[f.key] == null)).map((f) => f.label);
    if (missing.length) { showError(L(`还要填：${missing.join('、')}`, `Still needed: ${missing.join(', ')}`), ''); return; }
    setBusy(true);
    onSave(vals).catch((e) => showError(L('没存上', "Couldn't save"), e)).finally(() => setBusy(false));
  };
  const input = [type.body, styles.input, { backgroundColor: t.surface, color: t.ink }];
  return (
    <View style={{ gap: space.md }}>
      {fields.map((f) => (
        <View key={f.key} style={{ gap: 6 }}>
          <T v="caption" color={t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{f.label}{f.required ? ' *' : ''}{f.unit ? ` (${f.unit})` : ''}</T>
          {f.type === 'bool' ? (
            <Switch value={!!vals[f.key]} onValueChange={(v) => set(f.key, v)} accessibilityLabel={f.label} />
          ) : f.type === 'choice' ? (
            <View style={styles.opts}>
              {(f.options ?? []).map((o) => {
                const on = vals[f.key] === o;
                return (
                  <Pressable key={o} onPress={() => set(f.key, on ? '' : o)} accessibilityRole="button" accessibilityState={{ selected: on }}
                    style={[styles.opt, { backgroundColor: on ? t.ink : t.surface, borderColor: on ? t.ink : t.line }]}>
                    <Text style={[type.caption, { fontSize: 14, fontWeight: '600', color: on ? t.surface : t.ink2 }]}>{o}</Text>
                  </Pressable>
                );
              })}
            </View>
          ) : f.type === 'number' ? (
            <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
              <StepBtn label="−" onPress={() => set(f.key, String(Math.max(0, (Number(vals[f.key]) || 0) - 1)))} />
              <TextInput value={String(vals[f.key] ?? '')} onChangeText={(v) => set(f.key, v)} keyboardType="decimal-pad" accessibilityLabel={f.label}
                style={[...input, { flex: 1, textAlign: 'center' }]} placeholderTextColor={t.ink3} />
              <StepBtn label="+" onPress={() => set(f.key, String((Number(vals[f.key]) || 0) + 1))} />
            </View>
          ) : (
            <>
              <TextInput value={String(vals[f.key] ?? '')} onChangeText={(v) => set(f.key, v)} accessibilityLabel={f.label} placeholderTextColor={t.ink3}
                keyboardType={f.type === 'money' ? 'decimal-pad' : 'default'} autoCapitalize="none"
                placeholder={f.type === 'date' ? 'YYYY-MM-DD' : f.type === 'datetime' ? 'YYYY-MM-DD HH:MM' : f.type === 'money' ? '0.00' : ''}
                style={input} />
              {f.type === 'date' ? (
                <View style={styles.opts}>
                  {[[L('今天', 'Today'), 0], [L('明天', 'Tomorrow'), 1], [L('+3 天', '+3 days'), 3], [L('+1 周', '+1 week'), 7]].map(([label, n]) => (
                    <Pressable key={String(label)} onPress={() => set(f.key, plusDays(Number(n)))} accessibilityRole="button"
                      style={[styles.opt, { backgroundColor: t.surface, borderColor: t.line }]}>
                      <Text style={[type.caption, { fontSize: 13, fontWeight: '600', color: t.ink2 }]}>{String(label)}</Text>
                    </Pressable>
                  ))}
                </View>
              ) : null}
            </>
          )}
        </View>
      ))}
      <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.xs }}>
        <SheetBtn kind="quiet" label={L('取消', 'Cancel')} onPress={onCancel} />
        <SheetBtn kind="primary" label={saveLabel} busy={busy} onPress={save} grow />
      </View>
    </View>
  );
}

function StepBtn({ label, onPress }: { label: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label === '+' ? L('加一', 'Plus one') : L('减一', 'Minus one')}
      style={({ pressed }) => [styles.step, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
      <Text style={{ fontSize: 20, fontWeight: '600', color: t.ink }}>{label}</Text>
    </Pressable>
  );
}

function SheetBtn({ label, kind, icon, busy, onPress, grow }: { label: string; kind: 'primary' | 'quiet' | 'danger'; icon?: React.ReactNode; busy?: boolean; onPress: () => void; grow?: boolean }) {
  const t = useTheme();
  const bg = kind === 'primary' ? t.goldFill : kind === 'danger' ? t.badSoft : t.surface2;
  const fg = kind === 'primary' ? t.onGold : kind === 'danger' ? t.bad : t.ink;
  return (
    <Pressable onPress={onPress} disabled={busy} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.btn, { backgroundColor: bg, opacity: pressed ? 0.75 : 1 }, grow && { flexGrow: 1 }]}>
      {busy ? <ActivityIndicator size="small" color={fg} /> : icon}
      <Text numberOfLines={1} style={[type.headline, { fontSize: 15, color: fg }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  fields: { borderRadius: radius.lg, paddingHorizontal: space.lg, paddingVertical: 2 },
  fieldRow: { flexDirection: 'row', alignItems: 'baseline', gap: space.sm, paddingVertical: 11 },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 },
  opts: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  opt: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  step: { width: 44, height: 44, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, paddingHorizontal: space.lg, height: 46 },
});

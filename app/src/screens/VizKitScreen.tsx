// 可视化素材库展示页（设置 → 关于 → 可视化素材库；网页版 ?screen=VizKit）：每个组件一张卡，下面一句用法。
// 示例数据都是 10/12 的真实数据（vizKitData.ts）；顶上可以直接切浅色 / 深色对照。
import React, { useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { L } from '../i18n';
import { space, useAppearance, useTheme, vizMark, type Appearance } from '../theme';
import { NavHeader, Screen, SectionLabel, Segmented, T } from '../components/ui';
import {
  BarChart, BeforeAfter, ChangeTag, ChartCard, CompareList, DataTable, DayGrid, Donut, HBarChart, HeatCalendar, Kanban, KanbanBoard, LineChart, MonthCalendar,
  NutritionRing, PercentBar, ProgressRing, RangeSwitch, SegmentBars, SleepStages, Sparkline, StackedBar, StatRow, StatTile, StatusTag, Streak,
  SwatchMark, TargetBar, Timeline, VizEmpty, VizError, VizLoading, VizSurface, type RangeDays, type TableColumn,
} from '../components/viz';
import * as D from './vizKitData';

function Item({ title, rule, caption, right, table, children }: {
  title: string; rule: string; caption?: string; right?: React.ReactNode; table?: React.ReactNode; children: React.ReactNode;
}) {
  const t = useTheme();
  return (
    <ChartCard title={title} caption={caption} right={right} table={table} style={{ marginBottom: space.md }}>
      {children}
      <T v="caption" color={t.ink3} style={{ fontWeight: '400', lineHeight: 17 }}>{rule}</T>
    </ChartCard>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View>
      <SectionLabel>{title}</SectionLabel>
      {children}
    </View>
  );
}

// —— 基础 ——

function Palette() {
  const t = useTheme();
  const names = [L('青', 'Cyan'), L('金', 'Gold'), L('紫', 'Violet'), L('绿', 'Green'), L('蓝', 'Blue'), L('粉', 'Pink')];
  const chip = (hex: string, name: string) => (
    <View key={name} style={styles.swatch}>
      <View style={{ height: 34, borderRadius: 10, backgroundColor: hex }} />
      <T v="caption" style={{ fontSize: 12 }}>{name}</T>
      <T v="caption" color={t.ink3} style={styles.hex}>{hex}</T>
    </View>
  );
  const chips = [...t.viz.series.map((hex, i) => chip(hex, `${i + 1} · ${names[i]}`)), chip(t.viz.other, L('其他', 'Other'))];
  const rows: React.ReactNode[][] = [];
  for (let i = 0; i < chips.length; i += 3) rows.push(chips.slice(i, i + 3));
  return (
    <Item title={L('分类色（6 个，固定顺序）', 'Categorical colours (6, fixed order)')}
      rule={L('按顺序使用，不循环；颜色跟着类别走，不跟名次走；超过 6 类把末尾的合并为「其他」（灰，必须带文字）。深浅两套均通过色觉与对比度校验。',
        'Use in order, never cycled; a colour follows its category, not its rank. Past six, fold the tail into "Other" (grey, always labelled). Both modes pass the colour-vision and contrast checks.')}>
      <View style={{ gap: 12 }}>
        {rows.map((r, i) => (
          <View key={i} style={styles.swatches}>
            {r}
            {Array.from({ length: 3 - r.length }, (_, k) => <View key={`pad${k}`} style={styles.swatch} />)}
          </View>
        ))}
      </View>
    </Item>
  );
}

function StatusAndRamp() {
  const t = useTheme();
  return (
    <Item title={L('状态色与顺序色阶', 'Status colours and sequential ramp')}
      rule={L('状态色只表示状态，必须配图标和文字；色阶用于热力图，单一色相由低到高。', 'Status colours only mean state and always come with an icon and text; the ramp is one hue, low to high, for heatmaps.')}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        <StatusTag status="good" label={L('正常', 'Normal')} />
        <StatusTag status="warn" label={L('注意', 'Attention')} />
        <StatusTag status="bad" label={L('异常', 'Abnormal')} />
      </View>
      <View style={{ gap: 6 }}>
        <View style={{ flexDirection: 'row', gap: vizMark.gap }}>
          {t.viz.seq.map((c) => <View key={c} style={{ flex: 1, height: 26, backgroundColor: c }} />)}
        </View>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('低', 'Low')}</T>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('高', 'High')}</T>
        </View>
      </View>
    </Item>
  );
}

function Marks() {
  const t = useTheme();
  const rows: [React.ReactNode, string][] = [
    [<SwatchMark key="l" shape="line" color={t.viz.series[0]} />, L('折线 2px，圆角接头', 'Lines 2px, round joins')],
    [<View key="b" style={{ width: 12, height: 16, borderTopLeftRadius: 4, borderTopRightRadius: 4, backgroundColor: t.viz.series[0] }} />, L('柱子最粗 24，柱顶 4px 圆角，贴基线一端直角', 'Bars at most 24 wide, 4px rounded tip, square at the baseline')],
    [<View key="d" style={{ width: 12, height: 12, borderRadius: 6, backgroundColor: t.viz.series[0], borderWidth: 2, borderColor: t.surface }} />, L('标记点直径 8，外加 2px 底色描边', 'Markers 8px across with a 2px surface ring')],
    [<View key="g" style={{ flexDirection: 'row', gap: 2 }}><View style={{ width: 8, height: 14, backgroundColor: t.viz.series[0] }} /><View style={{ width: 8, height: 14, backgroundColor: t.viz.series[1] }} /></View>, L('相邻色块之间留 2px 底色，不画边框', 'A 2px surface gap between touching fills, never a border')],
  ];
  return (
    <Item title={L('标记规范', 'Marks')} rule={L('文字一律用文字色，颜色只给标记；两个及以上系列必须有图例；只标关键数值，其余点按查看。', 'Text always wears text colours, colour goes on marks; two or more series always get a legend; label only the key values, tap for the rest.')}>
      <View style={{ gap: 10 }}>
        {rows.map(([icon, text], i) => (
          <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <View style={{ width: 22, alignItems: 'center' }}>{icon}</View>
            <T v="callout" color={t.ink2} style={{ flex: 1, fontSize: 13 }}>{text}</T>
          </View>
        ))}
      </View>
    </Item>
  );
}

// —— 趋势 ——

function RecoveryRange() {
  const [range, setRange] = useState<RangeDays>(7);
  const days = range === 7 ? D.recovery.slice(-7) : D.recovery;
  return (
    <Item title={L('时间范围切换', 'Time range')} right={<View style={{ width: 186 }}><RangeSwitch value={range} onChange={setRange} /></View>}
      rule={L(`切换放在它管的图上方，同一组图跟着同一个范围。示例：恢复分${range === 7 ? '' : '（数据从 9/23 开始）'}。`,
        `The switch sits above what it scopes; every chart below follows the same range. Example: recovery score${range === 7 ? '' : ' (data starts 23 Sep)'}.`)}>
      <LineChart height={120} area current="2026-10-12"
        series={[{ key: 'rec', label: L('恢复分', 'Recovery'), points: days.map((d) => ({ key: d.date, value: d.score, label: D.md(d.date) })) }]} />
    </Item>
  );
}

export function VizKitScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { appearance, setAppearance } = useAppearance();
  const weeks = D.deadlineWeeks();
  const looks: { value: Appearance; label: string }[] = [{ value: 'system', label: L('跟随系统', 'System') }, { value: 'light', label: L('浅色', 'Light') }, { value: 'dark', label: L('深色', 'Dark') }];
  const apps = D.applications();
  const appCols: TableColumn<(typeof apps)[number]>[] = [
    { key: 'org', title: L('机构', 'Org'), primary: true, flex: 1.1 },
    { key: 'role', title: L('岗位', 'Role'), flex: 1.4 },
    { key: 'state', title: L('状态', 'Status'), flex: 1, tag: (r) => ({ text: r.state, tone: r.tone }) },
    { key: 'due', title: L('截止', 'Due'), flex: 0.7, numeric: true, text: (r) => D.md(r.due), sortValue: (r) => r.due },
  ];
  const weekCols: TableColumn<(typeof weeks)[number]>[] = [
    { key: 'label', title: L('截止周', 'Week'), primary: true },
    { key: 'value', title: L('岗位', 'Jobs'), numeric: true, sortValue: (r) => r.value },
  ];
  const lastRec = D.recovery[D.recovery.length - 1];
  return (
    <Screen>
      <NavHeader title={L('可视化素材库', 'Chart kit')} sub={L('示例数据：10/12', 'Sample data: 12 Oct')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl * 2 }}>
        <T v="callout" color={t.ink2}>{L('看板、今天页、小板子和 app 内所有图表共用的组件。点按柱子、折线、图例查看数值；表头可排序。',
          'The components behind every chart in the app: boards, Today and output boards. Tap bars, lines or legend rows for values; table headers sort.')}</T>
        <View style={{ marginTop: space.md }}>
          <Segmented options={looks} value={appearance} onChange={setAppearance} />
        </View>

        <Section title={L('基础', 'Foundations')}>
          <Palette />
          <StatusAndRamp />
          <Marks />
        </Section>

        <Section title={L('数值', 'Numbers')}>
          <Item title={L('数字卡', 'Stat tile')} rule={L('一个主数字，配说明与至多两项次要数字；一个数能说清的事不画图。', 'One headline number with its label and up to two secondary figures. If one number says it, skip the chart.')}>
            <StatTile size="hero" value="63" label={L('可投岗位', 'Open roles')} minor={[{ value: '10', label: L('本周截止', 'due this week') }, { value: '13', label: L('下周截止', 'due next week') }]} />
          </Item>
          <Item title={L('数字行（带变化值）', 'Stat row with change')} rule={L('变化值写明对比的时段，颜色 = 方向 × 好坏，配箭头；迷你趋势末端是当前值。', 'A change names its comparison period; colour = direction × whether that is good, always with an arrow. The sparkline ends on the current value.')}>
            <StatRow items={[
              { label: L('恢复分', 'Recovery'), value: String(lastRec.score), delta: { text: L('比昨天 −5', '−5 vs yesterday'), dir: 'down', good: false }, trend: D.recovery.slice(-7).map((d) => d.score) },
              { label: L('睡眠', 'Sleep'), value: '8:56', sub: L('目标 7–9 小时', 'Target 7–9 h') },
              { label: L('训练', 'Workout'), value: '36', unit: L('分钟', 'min'), sub: 'Pull A · 10 ' + L('组', 'sets') },
            ]} />
          </Item>
          <Item title={L('进度：环形与分段', 'Progress: ring and segments')} rule={L('环形用于单一目标；分段条用于逐项完成情况。示例：10/12 Pull A。', 'A ring for one goal; segments for item-by-item completion. Example: Pull A on 12 Oct.')}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
              <ProgressRing size={96} stroke={9} value={10} target={10} label={L('Pull A 完成 10 / 10 组', 'Pull A: 10 of 10 sets done')}>
                <T style={{ fontSize: 20, lineHeight: 24, fontWeight: '700' }}>10/10</T>
                <T v="caption" color={t.ink2} style={{ fontSize: 11, fontWeight: '400' }}>{L('组', 'sets')}</T>
              </ProgressRing>
              <SegmentBars items={D.pullA()} style={{ flex: 1 }} />
            </View>
          </Item>
        </Section>

        <Section title={L('趋势', 'Trends')}>
          <Item title={L('迷你趋势', 'Sparkline')} rule={L('放在数字旁，不画坐标轴；末端标出当前值，必要时标出最低点。', 'Next to a number, no axes; the end dot is the current value, mark the low when it matters.')}>
            <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
              <T style={{ fontSize: 34, lineHeight: 40, fontWeight: '700' }}>{String(lastRec.score)}</T>
              <T v="callout" color={t.ink2}>{L('恢复分', 'Recovery score')}</T>
            </View>
            <Sparkline values={D.recovery.map((d) => d.score)} height={50} min label={L('近 20 天恢复分，最低 40（10/3），当前 66', 'Recovery score over 20 days: low 40 on 3 Oct, now 66')} />
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('近 20 天 · 最低 40（10/3）', 'Last 20 days · low 40 (3 Oct)')}</T>
          </Item>
          <Item title={L('折线 + 目标范围', 'Line with target range')} rule={L('2px 线，目标范围用浅色带；点按或拖动显示当天数值。示例：睡眠时长（小时）。', '2px line, the target range is a light band; tap or drag for that day. Example: hours slept.')}>
            <LineChart current="2026-10-12" band={{ low: 7, high: 9, label: L('目标 7–9 小时', 'Target 7–9 h') }} fmt={(v) => L(`${v} 小时`, `${v} h`)}
              series={[{ key: 'sleep', label: L('睡眠', 'Sleep'), points: D.sleepHours.map((d) => ({ key: d.date, value: d.hours, label: D.md(d.date) })) }]} />
          </Item>
          <RecoveryRange />
        </Section>

        <Section title={L('时间', 'Time')}>
          <Item title={L('热力日历', 'Heat calendar')} rule={L('一格一天，颜色越深 / 越亮数值越高（单一色相）；空格表示无数据，金框为今天；点按查看当天数值。示例：9/23–10/12 恢复分。', 'One cell per day, darker or brighter means higher (one hue); outlined cells have no data, gold marks today; tap for the value. Example: recovery score, 23 Sep–12 Oct.')}>
            <HeatCalendar days={D.recovery.map((d) => ({ date: d.date, value: d.score }))} today="2026-10-12" bins={[50, 60, 70]} name={L('恢复分', 'Recovery')} />
          </Item>
          <Item title={L('日时间格', 'Day grid')} rule={L('方块高度对应时长；连续两小时以上的空闲收成一行；金线标出现在。示例：10/12 日程。', 'Block height follows duration; two or more free hours fold into one row; the gold line is now. Example: 12 Oct.')}>
            <DayGrid items={D.todayBlocks()} from="08:00" to="21:00" now="20:10" />
          </Item>
        </Section>

        <Section title={L('比较', 'Comparison')}>
          <Item title={L('柱状', 'Bars')} caption={L('单位：个', 'Unit: roles')} rule={L('单系列只用 1 号色；柱顶 4px 圆角；只标关键数值（本周、最高）。示例：岗位按截止周。', 'One series takes colour 1; 4px rounded tips; label only key values (this week, the peak). Example: roles by deadline week.')}
            table={<DataTable columns={weekCols} rows={weeks} rowKey={(r) => r.key} />}>
            <BarChart data={weeks} />
          </Item>
          <Item title={L('堆叠柱', 'Stacked bars')} caption={L('有截止日期的 56 个', '56 with a deadline')} rule={L('两个及以上系列必须有图例；段与段之间留 2px；点按显示每段数值。', 'Two or more series always get a legend; 2px between segments; tap a column for its values.')}>
            <StackedBar categories={weeks.map((w) => ({ key: w.key, label: w.label, current: w.current }))}
              series={[{ key: 'intern', label: L('实习', 'Internship'), values: D.deadlineByType.internship }, { key: 'full', label: L('全职', 'Full-time'), values: D.deadlineByType.fullTime }]} />
          </Item>
          <Item title={L('横条', 'Horizontal bars')} rule={L('有序类别按类别顺序，无序类别按数值排序；数值写在条末。示例：匹配分段。', 'Ordered categories keep their order, unordered ones sort by value; the value sits at the bar end. Example: match score bands.')}>
            <HBarChart data={D.matchBands()} labelWidth={64} />
          </Item>
        </Section>

        <Section title={L('对比', 'Before and after')}>
          <Item title={L('前后对比与变化标签', 'Before / after and change tags')} rule={L('上次空心、这次实心；变化标签 = 方向箭头 + 文字，颜色表示好坏，持平为灰。示例：10/12 练后报告。', 'Last time hollow, this time solid; a change tag is an arrow plus words, coloured by whether it is good, grey when level. Example: the 12 Oct workout report.')}>
            <VizSurface color={t.bg}>
              <View style={[styles.inner, { backgroundColor: t.bg, flexDirection: 'row', alignItems: 'center', gap: space.md }]}>
                <View style={{ flex: 1, gap: 4 }}>
                  <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{L('硬拉', 'Deadlift')}</T>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <T style={{ fontSize: 26, lineHeight: 30, fontWeight: '700' }}>85 kg</T>
                    <ChangeTag dir="up" text="5 kg" good />
                  </View>
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('估算最大重量 96 → 99 kg', 'Estimated 1RM 96 → 99 kg')}</T>
                </View>
                <BeforeAfter before={{ label: '9/29', value: 80 }} after={{ label: '10/12', value: 85 }} label={L('硬拉工作重量：9/29 80 kg，10/12 85 kg', 'Deadlift working weight: 80 kg on 29 Sep, 85 kg on 12 Oct')} />
              </View>
            </VizSurface>
            <CompareList title={L('与 9/29 相比', 'Compared with 29 Sep')} items={D.pullCompare()} />
          </Item>
          <Item title={L('目标条', 'Target bar')} rule={L('实际对目标：竖线是目标，条可以超过它；超过不好时（热量）用注意色并写明「超出」。', 'Actual against target: the tick is the target and the bar may pass it; when going over is bad (calories) it turns amber and says so.')}>
            <TargetBar label={L('蛋白质', 'Protein')} value={D.dinner.protein} target={D.dinner.proteinTarget} text={String(D.dinner.protein)} targetText={`${D.dinner.proteinTarget} g`}
              note={L('今晚两顿合计；训练日目标 136 g。白天的饮食尚未记录。', "Tonight's two meals; the training-day target is 136 g. Daytime meals aren't logged yet.")} />
          </Item>
        </Section>

        <Section title={L('占比', 'Part to whole')}>
          <Item title={L('环形', 'Donut')} rule={L('只用于 3–6 份的占比，段间留 2px；只有两份时改用百分比条或数字。点按图例突出一份。', 'Only for 3–6 parts, 2px between them; with two parts use a percent bar or the numbers. Tap a legend row to pick a part out.')}>
            <Donut data={D.regions()} center={{ value: '63', label: L('个岗位', 'roles') }} pct />
          </Item>
          <Item title={L('营养环', 'Macro ring')} rule={L('三大营养素按热量算比例（蛋白质、碳水 4 kcal/g，脂肪 9 kcal/g），中间写总热量。示例：10/12 晚餐建议。', 'Macros by calories (protein and carbs 4 kcal/g, fat 9 kcal/g) with the total in the middle. Example: the 12 Oct dinner plan.')}>
            <NutritionRing protein={D.dinner.protein} carbs={D.dinner.carbs} fat={D.dinner.fat} kcal={D.dinner.kcal} centerLabel={L('kcal · 两顿', 'kcal · 2 meals')} />
          </Item>
          <Item title={L('百分比条', 'Percent bar')} rule={L('两份的占比用一条，左右两端各标数值与比例。', 'Two parts in one bar, each end labelled with its value and share.')}>
            <PercentBar parts={D.jobTypes()} />
          </Item>
        </Section>

        <Section title={L('健康', 'Health')}>
          <Item title={L('睡眠分期', 'Sleep stages')} rule={L('一行一期，越往下越深，颜色同一色相由浅到深，清醒用灰；点按某一段查看起止。示例：10/12 03:03–13:21。', 'One row per stage, deeper rows lower and darker in one hue, awake in grey; tap a stretch for its times. Example: 12 Oct, 03:03–13:21.')}>
            <SleepStages segments={D.sleepNight} totals={D.sleepTotals} />
          </Item>
          <Item title={L('连续打卡', 'Streak')} rule={L('最近 N 天一格一天：做了上色，没做是轨道色，今天描金边。示例：9/29–10/12 训练。', 'One cell per day for the last N days: done is coloured, missed is the track, today has a gold edge. Example: workouts, 29 Sep–12 Oct.')}>
            <Streak days={D.trainingDays} today="2026-10-12" value={L('7 次', '7')} title={L('近 14 天训练', 'workouts in 14 days')} sub={L('连续 2 天（10/11–10/12）', '2 days in a row (11–12 Oct)')} />
          </Item>
        </Section>

        <Section title={L('视图', 'Views')}>
          <Item title={L('表格', 'Table')} rule={L('表头点按排序；状态用标签；行点开查看详情。', 'Tap a header to sort; status as a tag; tap a row for details.')}>
            <DataTable columns={appCols} rows={apps} rowKey={(r) => r.id} sort={{ key: 'due', dir: 'asc' }} />
          </Item>
          <Item title={L('看板', 'Board')} rule={L('按任一字段分列；长按卡片移到其他列（即改这个字段）；列头色点按列的身份取色。', 'Columns by any field; long-press a card to move it to another column (which changes that field); each column keeps its own colour.')}>
            <Kanban columns={D.jobBoard()} limit={2} onMove={() => {}} />
          </Item>
          <Item title={L('全屏看板', 'Full-screen board')} rule={L('集合块的全屏页用：每列竖着滚，长按卡片拖到其他列（即改这个字段），靠近边缘自动横滚，落点那一列描金边。', "For a collection's full screen: each column scrolls, long-press a card and drag it to another column (which changes that field); it scrolls sideways near the edges and the column under the card gets a gold outline.")}>
            <View style={{ height: 340, paddingHorizontal: space.lg }}><KanbanBoard columns={D.jobBoard()} onMove={() => {}} /></View>
          </Item>
          <Item title={L('日历', 'Calendar')} rule={L('日程、截止、待办用颜色加形状（圆点、菱形、方块）区分，并配图例；今天金底。', 'Events, deadlines and to-dos differ by colour and shape (dot, diamond, square), with a legend; today is gold.')}>
            <MonthCalendar month="2026-10" marks={D.octoberMarks} kinds={D.calendarKinds()} today="2026-10-12" />
          </Item>
          <Item title={L('时间线', 'Timeline')} rule={L('条表示剩余时间，菱形表示截止；金线为今天；点按一行查看还剩几天。', 'Bars are time left, diamonds are deadlines, the gold line is today; tap a row for days left.')}>
            <Timeline rows={D.deadlines()} from="2026-10-12" to="2026-11-10" today="2026-10-12" />
          </Item>
        </Section>

        <Section title={L('状态', 'States')}>
          <Item title={L('空、加载、出错', 'Empty, loading, error')} rule={L('每个组件都有这三种状态；重新加载时保留原图并调淡，不闪骨架。', 'Every component has these three states; a refetch keeps the chart and dims it instead of flashing a skeleton.')}>
            <View style={{ gap: space.sm }}>
              <VizEmpty onAdd={() => {}} />
              <VizLoading height={60} />
              <VizError onRetry={() => {}} />
            </View>
          </Item>
        </Section>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  inner: { borderRadius: 14, padding: 12 },
  swatches: { flexDirection: 'row', gap: 10 },
  swatch: { flex: 1, gap: 4 },
  hex: { fontSize: 11, fontWeight: '400', fontVariant: ['tabular-nums'] },
});

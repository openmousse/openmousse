// 可视化素材库（2026-10-13）：看板、今天页、小板子和 app 里所有图表共用。用法和规则见 app/AGENTS.md「可视化素材库」，
// 样子见 app 里「设置 → 关于 → 可视化素材库」（网页版 ?screen=VizKit）。色板在 theme.tsx 的 t.viz，标记常量是 vizMark。
export {
  ChangeTag, Legend, RangeSwitch, StatusGlyph, StatusTag, SwatchMark, Tip, VizEmpty, VizError, VizFrame, VizLoading, VizSurface,
  cardToneColors, dotProps, fmtTick, niceScale, seriesColor, slotColors, toneColors, useFontScale, useSurface, useWidth,
} from './core';
export type { Direction, LegendItem, RangeDays, Status, Swatch, TipRow, Tone, VizStateProps } from './core';
export { ChartCard } from './ChartCard';
export { StatRow, StatTile } from './StatTile';
export type { StatDelta, StatTileProps } from './StatTile';
export { ProgressBar, ProgressRing, SegmentBars, Segments } from './Progress';
export type { SegmentItem } from './Progress';
export { Sparkline } from './Sparkline';
export { LineChart, dayNum } from './LineChart';
export type { LineChartProps, LinePoint, LineSeries } from './LineChart';
export { BarChart, StackedBar } from './BarChart';
export type { BarChartProps, BarDatum, StackSeries, StackedBarProps } from './BarChart';
export { HBarChart } from './HBarChart';
export type { HBarDatum } from './HBarChart';
export { Donut, PercentBar, foldOther } from './Share';
export type { DonutProps, ShareDatum } from './Share';
export { DataTable } from './DataTable';
export type { DataTableProps, TableColumn } from './DataTable';
// 第二批
export { CalMark, HeatCalendar, MonthCalendar, isoOfDay, weekdayLetters } from './Calendars';
export type { CalKind, HeatCalendarProps, HeatDay, MarkShape, MonthCalendarProps } from './Calendars';
export { DayGrid, withAlpha } from './DayGrid';
export type { DayBlock, DayGridProps } from './DayGrid';
export { Timeline } from './Timeline';
export type { TimelineProps, TimelineRow } from './Timeline';
export { Kanban } from './Kanban';
export type { KanbanCard, KanbanCol, KanbanProps } from './Kanban';
export { KanbanBoard } from './KanbanBoard';
export type { BoardColumn as KanbanBoardColumn, KanbanBoardProps } from './KanbanBoard';
export { BeforeAfter, CompareList, TargetBar } from './Compare';
export type { CompareItem, ComparePoint } from './Compare';
export { NutritionRing, SleepStages, Streak, durText } from './Health';
export type { SleepSeg, SleepStage, StreakDay } from './Health';

import ActivityKit
import SwiftUI
import WidgetKit

/// 实时活动的数据格式。**和 modules/mousse-native/ios/LiveActivities.swift 里的一模一样**（名字、字段都不能只改一边）。
struct MousseActivityAttributes: ActivityAttributes {
  public struct ContentState: Codable, Hashable {
    var title: String
    var subtitle: String?
    var icon: String?
    var accent: String?
    var startAt: Double?
    var endAt: Double?
    var progress: Double?
    var lines: [String]?
    var done: Bool?
  }

  var kind: String
  var key: String
}

/// 锁屏横幅 + 灵动岛。有 endAt 就是倒计时（练后餐、冥想时间），没有就按 progress 画进度条。
struct MousseLiveActivity: Widget {
  var body: some WidgetConfiguration {
    ActivityConfiguration(for: MousseActivityAttributes.self) { context in
      LockBanner(attrs: context.attributes, state: context.state)
        .activityBackgroundTint(nil)
        .activitySystemActionForegroundColor(LiveStyle.accent(context.attributes, context.state))
    } dynamicIsland: { context in
      let accent = LiveStyle.accent(context.attributes, context.state)
      let icon = LiveStyle.icon(context.attributes, context.state)
      return DynamicIsland {
        DynamicIslandExpandedRegion(.leading) {
          Image(systemName: icon)
            .font(.title2)
            .foregroundColor(accent)
            .padding(.leading, 4)
        }
        DynamicIslandExpandedRegion(.trailing) {
          if let range = LiveStyle.range(context.state), context.state.done != true {
            Text(timerInterval: range, countsDown: true)
              .font(.title3.weight(.semibold))
              .monospacedDigit()
              .multilineTextAlignment(.trailing)
              .frame(maxWidth: 90)
              .foregroundColor(accent)
          }
        }
        DynamicIslandExpandedRegion(.center) {
          Text(context.state.title).font(.headline).lineLimit(1)
        }
        DynamicIslandExpandedRegion(.bottom) {
          VStack(alignment: .leading, spacing: 6) {
            if let sub = context.state.subtitle, !sub.isEmpty {
              Text(sub).font(.subheadline).foregroundColor(.secondary).lineLimit(2)
            }
            LiveProgress(state: context.state, accent: accent)
          }
        }
      } compactLeading: {
        Image(systemName: icon).foregroundColor(accent)
      } compactTrailing: {
        if let range = LiveStyle.range(context.state), context.state.done != true {
          Text(timerInterval: range, countsDown: true)
            .monospacedDigit()
            .frame(maxWidth: 52)
            .foregroundColor(accent)
        } else if context.state.done == true {
          Image(systemName: "checkmark").foregroundColor(accent)
        } else if let p = context.state.progress {
          Text("\(Int((p * 100).rounded()))%").monospacedDigit().foregroundColor(accent)
        }
      } minimal: {
        Image(systemName: icon).foregroundColor(accent)
      }
      .keylineTint(accent)
    }
  }
}

struct LockBanner: View {
  let attrs: MousseActivityAttributes
  let state: MousseActivityAttributes.ContentState

  var body: some View {
    let accent = LiveStyle.accent(attrs, state)
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .center, spacing: 12) {
        ZStack {
          Circle().fill(accent.opacity(0.18))
          Image(systemName: state.done == true ? "checkmark" : LiveStyle.icon(attrs, state))
            .font(.system(size: 18, weight: .semibold))
            .foregroundColor(accent)
        }
        .frame(width: 40, height: 40)
        VStack(alignment: .leading, spacing: 2) {
          Text(state.title).font(.headline).lineLimit(1)
          if let sub = state.subtitle, !sub.isEmpty {
            Text(sub).font(.subheadline).foregroundColor(.secondary).lineLimit(2)
          }
        }
        Spacer(minLength: 0)
        if let range = LiveStyle.range(state), state.done != true {
          Text(timerInterval: range, countsDown: true)
            .font(.title2.weight(.semibold))
            .monospacedDigit()
            .multilineTextAlignment(.trailing)
            .frame(maxWidth: 110, alignment: .trailing)
            .foregroundColor(accent)
        }
      }
      LiveProgress(state: state, accent: accent)
      if let lines = state.lines, !lines.isEmpty {
        VStack(alignment: .leading, spacing: 3) {
          ForEach(Array(lines.prefix(2).enumerated()), id: \.offset) { _, line in
            Text(line).font(.footnote).foregroundColor(.secondary).lineLimit(1)
          }
        }
      }
    }
    .padding(16)
  }
}

struct LiveProgress: View {
  let state: MousseActivityAttributes.ContentState
  let accent: Color

  var body: some View {
    if state.done == true {
      EmptyView()
    } else if let range = LiveStyle.range(state) {
      ProgressView(timerInterval: range, countsDown: true, label: { EmptyView() }, currentValueLabel: { EmptyView() })
        .tint(accent)
    } else if let p = state.progress {
      ProgressView(value: max(0, min(1, p)))
        .tint(accent)
    }
  }
}

enum LiveStyle {
  static func accent(_ attrs: MousseActivityAttributes, _ state: MousseActivityAttributes.ContentState) -> Color {
    if let a = state.accent, !a.isEmpty { return WidgetPalette.color(a) }
    switch attrs.kind {
    case "meal": return WidgetPalette.color("orange")
    case "focus": return WidgetPalette.color("purple")
    case "training": return WidgetPalette.color("green")
    default: return WidgetPalette.color(nil)
    }
  }

  static func icon(_ attrs: MousseActivityAttributes, _ state: MousseActivityAttributes.ContentState) -> String {
    if let i = state.icon, !i.isEmpty { return i }
    switch attrs.kind {
    case "meal": return "fork.knife"
    case "focus": return "moon.stars.fill"
    case "training": return "figure.strengthtraining.traditional"
    default: return "sparkles"
    }
  }

  /// 倒计时的区间：开始（没给就用现在）到结束；已经过了结束时间就不画倒计时。
  static func range(_ state: MousseActivityAttributes.ContentState) -> ClosedRange<Date>? {
    guard let end = state.endAt else { return nil }
    let endDate = Date(timeIntervalSince1970: end)
    let now = Date()
    guard endDate > now else { return nil }
    var start = state.startAt.map { Date(timeIntervalSince1970: $0) } ?? now
    if start > endDate { start = now }
    return start...endDate
  }
}

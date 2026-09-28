import SwiftUI
import UIKit
import WidgetKit

/// 桌面：今天（小 = 恢复分 + 下一件事；中 = 再加下一餐和后面一件）
struct TodayWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "mousse.today", provider: MousseProvider()) { entry in
      TodayView(entry: entry)
    }
    .configurationDisplayName(MousseShared.L("今天", "Today"))
    .description(MousseShared.L("恢复分、下一餐、下一件事。", "Recovery, next meal, what's next."))
    .supportedFamilies([.systemSmall, .systemMedium])
  }
}

/// 锁屏：下一件事（长条 + 一行字）
struct NextWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "mousse.next", provider: MousseProvider()) { entry in
      NextView(entry: entry)
    }
    .configurationDisplayName(MousseShared.L("下一件事", "What's next"))
    .description(MousseShared.L("下一节课、训练、截止。", "Next class, workout or deadline."))
    .supportedFamilies([.accessoryRectangular, .accessoryInline])
  }
}

/// 锁屏：恢复分（小圆）
struct RecoveryWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "mousse.recovery", provider: MousseProvider()) { entry in
      RecoveryView(entry: entry)
    }
    .configurationDisplayName(MousseShared.L("恢复分", "Recovery"))
    .description(MousseShared.L("今天的恢复分。", "Today's recovery score."))
    .supportedFamilies([.accessoryCircular])
  }
}

// —— 视图 ————————————————————————————————————————————————————————

struct TodayView: View {
  let entry: MousseEntry
  @Environment(\.widgetFamily) private var family

  var body: some View {
    Group {
      if let snap = entry.snap {
        if family == .systemMedium {
          medium(snap)
        } else {
          small(snap)
        }
      } else {
        NotConnected()
      }
    }
    .widgetBackground(Color(uiColor: .systemBackground))
  }

  private func small(_ snap: WidgetSnapshot) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .center, spacing: 8) {
        if let r = snap.recovery {
          ScoreRing(score: r.score, tint: WidgetPalette.color(r.tint), size: 38, line: 5)
          VStack(alignment: .leading, spacing: 0) {
            Text(MousseShared.L("恢复", "Recovery")).font(.caption2).foregroundColor(.secondary)
            Text(r.label ?? "").font(.caption.weight(.semibold)).lineLimit(1)
          }
        } else {
          Text(snap.name ?? MousseShared.appName).font(.caption.weight(.semibold)).foregroundColor(.secondary)
        }
        Spacer(minLength: 0)
      }
      Spacer(minLength: 0)
      if let item = entry.upcoming.first {
        ItemLine(item: item, big: true)
      } else if let meal = snap.meal {
        MealLine(meal: meal)
      } else {
        Text(snap.empty ?? MousseShared.L("今天没有别的安排", "Nothing else today")).font(.footnote).foregroundColor(.secondary)
      }
      StaleNote(entry: entry)
    }
  }

  private func medium(_ snap: WidgetSnapshot) -> some View {
    HStack(alignment: .top, spacing: 14) {
      VStack(alignment: .leading, spacing: 6) {
        if let r = snap.recovery {
          ScoreRing(score: r.score, tint: WidgetPalette.color(r.tint), size: 56, line: 6)
          Text(r.label ?? "").font(.caption.weight(.semibold)).lineLimit(1)
          if let line = r.line {
            Text(line).font(.caption2).foregroundColor(.secondary).lineLimit(2)
          }
        } else {
          Text(snap.name ?? MousseShared.appName).font(.headline)
        }
        Spacer(minLength: 0)
        StaleNote(entry: entry)
      }
      .frame(width: 92, alignment: .leading)
      VStack(alignment: .leading, spacing: 9) {
        if let meal = snap.meal {
          MealLine(meal: meal)
        }
        let items = entry.upcoming.prefix(snap.meal == nil ? 3 : 2)
        ForEach(Array(items)) { item in
          ItemLine(item: item, big: false)
        }
        if items.isEmpty && snap.meal == nil {
          Text(snap.empty ?? MousseShared.L("今天没有别的安排", "Nothing else today")).font(.footnote).foregroundColor(.secondary)
        }
        Spacer(minLength: 0)
        if let rem = snap.remember, rem.count > 0 {
          Label {
            Text(rem.first.map { "\($0)" + (rem.count > 1 ? MousseShared.L(" 等 \(rem.count) 件", " +\(rem.count - 1)") : "") } ?? MousseShared.L("\(rem.count) 件要记得", "\(rem.count) to remember"))
              .lineLimit(1)
          } icon: {
            Image(systemName: "checklist")
          }
          .font(.caption2)
          .foregroundColor(.secondary)
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
    }
  }
}

struct ItemLine: View {
  let item: WidgetSnapshot.Item
  let big: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: 1) {
      Text(item.time).font(big ? Font.caption.weight(.semibold) : Font.caption2.weight(.semibold)).foregroundColor(WidgetPalette.kind(item.kind))
      Text(item.title).font(big ? Font.subheadline.weight(.semibold) : Font.footnote.weight(.medium)).lineLimit(big ? 2 : 1)
      if let place = item.place, !place.isEmpty {
        Text(place).font(.caption2).foregroundColor(.secondary).lineLimit(1)
      }
    }
  }
}

struct MealLine: View {
  let meal: WidgetSnapshot.Meal

  var body: some View {
    VStack(alignment: .leading, spacing: 1) {
      HStack(spacing: 4) {
        Image(systemName: "fork.knife").font(.caption2)
        Text([meal.label, meal.time].compactMap { $0 }.joined(separator: " ")).font(.caption2.weight(.semibold))
        if let kcal = meal.kcal {
          Text("· \(kcal) kcal").font(.caption2).foregroundColor(.secondary)
        }
      }
      .foregroundColor(WidgetPalette.color("orange"))
      if let text = meal.text, !text.isEmpty {
        Text(text).font(.footnote).lineLimit(1)
      }
    }
  }
}

struct StaleNote: View {
  let entry: MousseEntry

  var body: some View {
    if entry.cached, let at = entry.snap?.at {
      let mins = Int((entry.date.timeIntervalSince1970 - at) / 60)
      if mins >= 30 {
        Text(mins < 120 ? MousseShared.L("\(mins) 分钟前", "\(mins) min ago") : MousseShared.L("\(mins / 60) 小时前", "\(mins / 60) h ago"))
          .font(.system(size: 9))
          .foregroundColor(.secondary)
      }
    }
  }
}

struct NotConnected: View {
  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(MousseShared.appName).font(.caption.weight(.semibold))
      Text(MousseShared.L("打开 app 连一次服务器", "Open the app once to connect")).font(.caption2).foregroundColor(.secondary)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
  }
}

struct NextView: View {
  let entry: MousseEntry
  @Environment(\.widgetFamily) private var family

  var body: some View {
    Group {
      if family == .accessoryInline {
        if let item = entry.upcoming.first {
          Text("\(item.time) \(item.title)")
        } else {
          Text(MousseShared.L("今天没有别的安排", "Nothing else today"))
        }
      } else if let item = entry.upcoming.first {
        VStack(alignment: .leading, spacing: 1) {
          Text(item.time).font(.caption.weight(.semibold)).widgetAccentable()
          Text(item.title).font(.headline).lineLimit(1)
          let rest = entry.upcoming.count - 1
          if let place = item.place, !place.isEmpty {
            Text(rest > 0 ? MousseShared.L("\(place) · 还有 \(rest) 件", "\(place) · \(rest) more") : place).font(.caption).lineLimit(1)
          } else if rest > 0 {
            Text(MousseShared.L("今天还有 \(rest) 件", "\(rest) more today")).font(.caption).lineLimit(1)
          }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
      } else {
        VStack(alignment: .leading, spacing: 1) {
          Text(entry.snap?.name ?? MousseShared.appName).font(.caption.weight(.semibold)).widgetAccentable()
          Text(entry.snap == nil ? MousseShared.L("打开 app 连一次", "Open the app once") : MousseShared.L("今天没有别的安排", "Nothing else today")).font(.caption)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
      }
    }
    .widgetBackground(Color.clear)
  }
}

struct RecoveryView: View {
  let entry: MousseEntry

  var body: some View {
    Group {
      if let r = entry.snap?.recovery {
        Gauge(value: max(0, min(100, r.score)), in: 0...100) {
          Text(MousseShared.L("恢复", "Rec"))
        } currentValueLabel: {
          Text("\(Int(r.score.rounded()))")
        }
        .gaugeStyle(.accessoryCircular)
      } else {
        ZStack {
          AccessoryWidgetBackground()
          Image(systemName: "heart.text.square")
        }
      }
    }
    .widgetBackground(Color.clear)
  }
}

struct ScoreRing: View {
  let score: Double
  let tint: Color
  let size: CGFloat
  let line: CGFloat

  var body: some View {
    ZStack {
      Circle().stroke(tint.opacity(0.2), lineWidth: line)
      Circle().trim(from: 0, to: max(0, min(1, score / 100)))
        .stroke(tint, style: StrokeStyle(lineWidth: line, lineCap: .round))
        .rotationEffect(.degrees(-90))
      Text("\(Int(score.rounded()))")
        .font(.system(size: size * 0.36, weight: .semibold, design: .rounded))
        .monospacedDigit()
    }
    .frame(width: size, height: size)
  }
}

extension View {
  /// iOS 17 起小组件必须用 containerBackground，否则整块显示成「请更新」；16 上用普通背景。
  @ViewBuilder func widgetBackground(_ color: Color) -> some View {
    if #available(iOS 17.0, *) {
      containerBackground(for: .widget) { color }
    } else {
      background(color)
    }
  }
}

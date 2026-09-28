import Foundation
import WidgetKit

/// 服务器 /api/widget 回来的东西（server/widget.py）。文字服务器已经按语言排好，这里只画。
struct WidgetSnapshot: Codable {
  struct Recovery: Codable {
    var score: Double
    var label: String?
    var tint: String?
    var line: String?
  }

  struct Meal: Codable {
    var label: String
    var time: String?
    var text: String?
    var kcal: Int?
  }

  struct Item: Codable, Identifiable {
    var id: String { "\(start)-\(title)" }
    var start: Double
    var end: Double?
    var time: String
    var title: String
    var place: String?
    var kind: String?
  }

  struct Remember: Codable {
    var count: Int
    var first: String?
  }

  var name: String?
  var at: Double
  var recovery: Recovery?
  var meal: Meal?
  var items: [Item]?
  var remember: Remember?
  var empty: String?
  var more: String?
}

struct MousseEntry: TimelineEntry {
  let date: Date
  let snap: WidgetSnapshot?
  /// 这份数据是缓存（这次没连上服务器）
  let cached: Bool

  /// 这个时刻还没结束的（下一件事）
  var upcoming: [WidgetSnapshot.Item] {
    let now = date.timeIntervalSince1970
    return (snap?.items ?? []).filter { ($0.end ?? $0.start + 3600) > now }
  }
}

enum WidgetLoader {
  static let cacheName = "widget.json"

  static func cached() -> WidgetSnapshot? {
    guard let url = MousseShared.container?.appendingPathComponent(cacheName),
          let data = try? Data(contentsOf: url) else { return nil }
    return try? JSONDecoder().decode(WidgetSnapshot.self, from: data)
  }

  static func fetch() async -> WidgetSnapshot? {
    guard let req = MousseShared.request("/api/widget", timeout: 12) else { return nil }
    do {
      let (data, resp) = try await URLSession.shared.data(for: req)
      guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return nil }
      let snap = try JSONDecoder().decode(WidgetSnapshot.self, from: data)
      if let url = MousseShared.container?.appendingPathComponent(cacheName) {
        try? data.write(to: url, options: .atomic)
      }
      return snap
    } catch {
      return nil
    }
  }

  /// 先问服务器，不通就用缓存。条目：现在一条，之后每件事结束时一条（「下一件」自己往后挪），最多 8 条。
  static func timeline() async -> Timeline<MousseEntry> {
    let now = Date()
    var snap = await fetch()
    let cached = snap == nil
    if snap == nil { snap = WidgetLoader.cached() }
    var entries = [MousseEntry(date: now, snap: snap, cached: cached)]
    let ends = (snap?.items ?? [])
      .map { $0.end ?? $0.start + 3600 }
      .filter { $0 > now.timeIntervalSince1970 }
      .sorted()
    for t in ends.prefix(7) {
      entries.append(MousseEntry(date: Date(timeIntervalSince1970: t), snap: snap, cached: cached))
    }
    // 连不上时早点再试；连上了半小时刷一次（系统每天给的次数有限）
    let next = now.addingTimeInterval(cached ? 15 * 60 : 30 * 60)
    return Timeline(entries: entries, policy: .after(next))
  }
}

struct MousseProvider: TimelineProvider {
  func placeholder(in context: Context) -> MousseEntry {
    MousseEntry(date: Date(), snap: WidgetSnapshot.sample, cached: false)
  }

  func getSnapshot(in context: Context, completion: @escaping (MousseEntry) -> Void) {
    if context.isPreview {
      completion(placeholder(in: context))
      return
    }
    Task {
      let snap = await WidgetLoader.fetch() ?? WidgetLoader.cached()
      completion(MousseEntry(date: Date(), snap: snap ?? WidgetSnapshot.sample, cached: snap == nil))
    }
  }

  func getTimeline(in context: Context, completion: @escaping (Timeline<MousseEntry>) -> Void) {
    Task {
      completion(await WidgetLoader.timeline())
    }
  }
}

extension WidgetSnapshot {
  /// 小组件库里预览用（添加小组件时看到的样子）
  static var sample: WidgetSnapshot {
    let now = Date().timeIntervalSince1970
    let zh = MousseShared.isChinese
    return WidgetSnapshot(
      name: MousseShared.appName,
      at: now,
      recovery: Recovery(score: 76, label: zh ? "不错" : "Good", tint: "green", line: zh ? "睡了 7 小时 20 分" : "Slept 7h 20m"),
      meal: Meal(label: zh ? "午餐" : "Lunch", time: "12:30", text: zh ? "鸡胸肉、米饭、西兰花" : "Chicken, rice, broccoli", kcal: 650),
      items: [
        Item(start: now + 3600, end: now + 3 * 3600, time: "14:00", title: zh ? "战略课" : "Strategy class", place: "SAF 120", kind: "class"),
        Item(start: now + 5 * 3600, end: now + 6 * 3600, time: "18:00", title: zh ? "训练" : "Workout", place: nil, kind: "training"),
      ],
      remember: Remember(count: 2, first: zh ? "小组视频 10/2" : "Group video 2 Oct"),
      empty: nil,
      more: nil
    )
  }
}

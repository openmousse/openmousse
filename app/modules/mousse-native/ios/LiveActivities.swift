import ActivityKit
import Foundation

/// 实时活动（锁屏 + 灵动岛）的数据格式。**和 targets/widgets/LiveActivity.swift 里的一模一样**（名字、字段都不能改一边）：
/// 系统按类型名把 app 开的活动交给小组件扩展去画，服务器用推送开（push-to-start）时 attributes-type 也写这个名字。
/// 时间一律是 Unix 秒（Double），不用 Date：推送里的 JSON 是服务器拼的，Date 的默认编码是 2001 年起算，容易对不上。
struct MousseActivityAttributes: ActivityAttributes {
  public struct ContentState: Codable, Hashable {
    var title: String
    var subtitle: String?
    /// SF Symbol 名字，比如 fork.knife、moon.stars
    var icon: String?
    /// 颜色：gold / cyan / green / purple / pink / orange，或 #RRGGBB
    var accent: String?
    var startAt: Double?
    /// 倒计时到这个时刻；没有就不显示倒计时
    var endAt: Double?
    /// 0…1，没有 endAt 时画进度条
    var progress: Double?
    var lines: [String]?
    /// 结束时的最后一帧（打勾、「时间到」）
    var done: Bool?
  }

  /// 哪一类：meal（练后餐）/ focus（冥想时间）/ …，小组件按它挑图标和颜色的默认值
  var kind: String
  /// 服务器那边的 id：同一个 key 只开一个
  var key: String
}

final class LiveActivities {
  static let shared = LiveActivities()

  /// 给 JS 发事件（模块在 OnCreate 里接上）
  var emit: ((String, [String: Any?]) -> Void)?

  private let lock = NSLock()
  private var watched = Set<String>()
  private var observing = false

  static var supported: Bool {
    ActivityAuthorizationInfo().areActivitiesEnabled
  }

  private static func state(_ json: String) throws -> MousseActivityAttributes.ContentState {
    try JSONDecoder().decode(MousseActivityAttributes.ContentState.self, from: Data(json.utf8))
  }

  private static func staleDate(_ at: Double?) -> Date? {
    guard let at, at > 0 else { return nil }
    return Date(timeIntervalSince1970: at)
  }

  private func running(_ key: String) -> Activity<MousseActivityAttributes>? {
    Activity<MousseActivityAttributes>.activities.first { a in
      a.attributes.key == key && (a.activityState == .active || a.activityState == .stale)
    }
  }

  /// 开一个；同一个 key 已经开着就改成新的内容。返回活动 id。
  func start(key: String, kind: String, stateJson: String, staleAt: Double?) async throws -> String {
    let st = try LiveActivities.state(stateJson)
    let content = ActivityContent(state: st, staleDate: LiveActivities.staleDate(staleAt))
    if let a = running(key) {
      await a.update(content)
      return a.id
    }
    let activity = try Activity<MousseActivityAttributes>.request(
      attributes: MousseActivityAttributes(kind: kind, key: key),
      content: content,
      pushType: .token
    )
    watch(activity)
    return activity.id
  }

  func update(key: String, stateJson: String, staleAt: Double?) async throws -> Bool {
    guard let a = running(key) else { return false }
    let st = try LiveActivities.state(stateJson)
    await a.update(ActivityContent(state: st, staleDate: LiveActivities.staleDate(staleAt)))
    return true
  }

  /// 结束：给了最后一帧就停在那一帧（系统默认留一会儿），没给就马上收掉。
  func end(key: String, stateJson: String?) async {
    let last: MousseActivityAttributes.ContentState? = stateJson.flatMap { try? LiveActivities.state($0) }
    for a in Activity<MousseActivityAttributes>.activities where a.attributes.key == key {
      if let last {
        await a.end(ActivityContent(state: last, staleDate: nil), dismissalPolicy: .default)
      } else {
        await a.end(nil, dismissalPolicy: .immediate)
      }
    }
  }

  func list() -> String {
    let arr: [[String: Any]] = Activity<MousseActivityAttributes>.activities.map { a in
      ["id": a.id, "key": a.attributes.key, "kind": a.attributes.kind, "state": LiveActivities.name(a.activityState)]
    }
    return SharedStore.json(arr)
  }

  /// 开始报令牌：push-to-start 令牌（iOS 17.2+，服务器用它在 app 没开时开活动）和每个活动自己的推送令牌（用来改、结束）。
  func observe() {
    lock.lock()
    let first = !observing
    observing = true
    lock.unlock()
    guard first else { return }
    for a in Activity<MousseActivityAttributes>.activities { watch(a) }
    Task { [weak self] in
      for await a in Activity<MousseActivityAttributes>.activityUpdates {
        self?.watch(a)
      }
    }
    if #available(iOS 17.2, *) {
      Task { [weak self] in
        for await data in Activity<MousseActivityAttributes>.pushToStartTokenUpdates {
          self?.emit?("onLiveToken", ["type": "start", "token": LiveActivities.hex(data)])
        }
      }
    }
  }

  private func watch(_ a: Activity<MousseActivityAttributes>) {
    lock.lock()
    let fresh = watched.insert(a.id).inserted
    lock.unlock()
    guard fresh else { return }
    let key = a.attributes.key
    let id = a.id
    Task { [weak self] in
      for await data in a.pushTokenUpdates {
        self?.emit?("onLiveToken", ["type": "activity", "token": LiveActivities.hex(data), "key": key, "id": id])
      }
    }
    Task { [weak self] in
      for await st in a.activityStateUpdates {
        self?.emit?("onLiveState", ["key": key, "id": id, "state": LiveActivities.name(st)])
      }
    }
  }

  static func hex(_ data: Data) -> String {
    data.map { String(format: "%02x", $0) }.joined()
  }

  static func name(_ st: ActivityState) -> String {
    switch st {
    case .active: return "active"
    case .ended: return "ended"
    case .dismissed: return "dismissed"
    case .stale: return "stale"
    @unknown default: return "unknown"
    }
  }
}

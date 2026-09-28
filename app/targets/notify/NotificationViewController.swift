import SwiftUI
import UIKit
import UserNotifications
import UserNotificationsUI

/// 通知内容扩展：长按「卡片更新了」（类别 card）和「等你点头」（类别 inbox）的通知，展开成一张卡。
/// 卡的内容是服务器放进推送 data 里的 card（server/push.py 的 rich_card）；Expo 推送把 data 放在 userInfo["body"]。
/// 没有 card（老服务器、别的通知）就把标题、副标题、正文画成同样的样子。按钮还是 app 注册的那组，系统画在卡下面。
class NotificationViewController: UIViewController, UNNotificationContentExtension {
  private var host: UIHostingController<NotifyCard>?

  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = .clear
  }

  func didReceive(_ notification: UNNotification) {
    let content = notification.request.content
    let card = CardData(content: content)
    let root = NotifyCard(card: card)
    if let host {
      host.rootView = root
    } else {
      let h = UIHostingController(rootView: root)
      h.view.backgroundColor = .clear
      addChild(h)
      h.view.translatesAutoresizingMaskIntoConstraints = false
      view.addSubview(h.view)
      NSLayoutConstraint.activate([
        h.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
        h.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        h.view.topAnchor.constraint(equalTo: view.topAnchor),
        h.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
      ])
      h.didMove(toParent: self)
      host = h
    }
    // 高度按内容算（宽度这时才准）
    let width = view.bounds.width > 0 ? view.bounds.width : UIScreen.main.bounds.width
    if let h = host {
      let size = h.sizeThatFits(in: CGSize(width: width, height: .greatestFiniteMagnitude))
      preferredContentSize = CGSize(width: width, height: ceil(size.height))
    }
  }
}

/// 推送里的卡片数据。字段名很短，整条推送只有 4 KB。
struct CardData {
  struct Stat: Identifiable {
    let id = UUID()
    let label: String
    let value: String
  }

  var app: String
  var title: String
  var kind: String
  var stats: [Stat] = []
  var ring: (value: Double, max: Double, label: String)?
  var lines: [String] = []
  var foot: String?
  var tint: String?

  init(content: UNNotificationContent) {
    let info = content.userInfo
    let data = (info["body"] as? [String: Any]) ?? (info["data"] as? [String: Any]) ?? [:]
    let card = (data["card"] as? [String: Any]) ?? [:]
    app = content.title
    kind = (card["k"] as? String) ?? content.subtitle
    title = (card["t"] as? String) ?? ""
    tint = card["c"] as? String
    foot = card["f"] as? String
    if let raw = card["s"] as? [[Any]] {
      stats = raw.compactMap { pair -> Stat? in
        guard pair.count >= 2 else { return nil }
        return Stat(label: "\(pair[0])", value: "\(pair[1])")
      }
    }
    if let r = card["r"] as? [Any], r.count >= 2, let v = CardData.number(r[0]), let m = CardData.number(r[1]), m > 0 {
      ring = (v, m, r.count > 2 ? "\(r[2])" : "")
    }
    if let ls = card["l"] as? [String] {
      lines = ls
    }
    if title.isEmpty && lines.isEmpty && stats.isEmpty {
      // 没有卡片数据：正文按行拆开
      let body = content.body
      let parts = body.components(separatedBy: " · ")
      title = parts.first ?? body
      lines = Array(parts.dropFirst())
    }
  }

  static func number(_ v: Any) -> Double? {
    if let d = v as? Double { return d }
    if let i = v as? Int { return Double(i) }
    if let n = v as? NSNumber { return n.doubleValue }
    if let s = v as? String { return Double(s) }
    return nil
  }
}

struct NotifyCard: View {
  let card: CardData

  private var accent: Color { Palette.color(card.tint) }

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(spacing: 6) {
        Circle().fill(accent).frame(width: 8, height: 8)
        Text(card.app).font(.footnote.weight(.semibold)).foregroundColor(.secondary).lineLimit(1)
        if !card.kind.isEmpty {
          Text("· \(card.kind)").font(.footnote).foregroundColor(.secondary).lineLimit(1)
        }
        Spacer(minLength: 0)
      }
      HStack(alignment: .center, spacing: 14) {
        VStack(alignment: .leading, spacing: 10) {
          if !card.title.isEmpty {
            Text(card.title).font(.headline).foregroundColor(.primary).fixedSize(horizontal: false, vertical: true)
          }
          if !card.stats.isEmpty {
            HStack(alignment: .top, spacing: 18) {
              ForEach(card.stats.prefix(3)) { s in
                VStack(alignment: .leading, spacing: 2) {
                  Text(s.value).font(.title3.weight(.semibold)).monospacedDigit().foregroundColor(.primary).lineLimit(1).minimumScaleFactor(0.7)
                  Text(s.label).font(.caption).foregroundColor(.secondary).lineLimit(1)
                }
              }
            }
          }
        }
        if let r = card.ring {
          Spacer(minLength: 0)
          Ring(value: r.value, max: r.max, label: r.label, color: accent)
            .frame(width: 64, height: 64)
        }
      }
      if !card.lines.isEmpty {
        VStack(alignment: .leading, spacing: 6) {
          ForEach(Array(card.lines.prefix(5).enumerated()), id: \.offset) { _, line in
            HStack(alignment: .firstTextBaseline, spacing: 8) {
              Circle().fill(Color.secondary.opacity(0.5)).frame(width: 4, height: 4).offset(y: -2)
              Text(line).font(.subheadline).foregroundColor(.primary).fixedSize(horizontal: false, vertical: true)
            }
          }
        }
      }
      if let foot = card.foot, !foot.isEmpty {
        Text(foot).font(.caption).foregroundColor(.secondary)
      }
    }
    .padding(16)
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

struct Ring: View {
  let value: Double
  let max: Double
  let label: String
  let color: Color

  var body: some View {
    let frac = Swift.max(0, Swift.min(1, value / max))
    ZStack {
      Circle().stroke(color.opacity(0.18), lineWidth: 7)
      Circle().trim(from: 0, to: frac)
        .stroke(color, style: StrokeStyle(lineWidth: 7, lineCap: .round))
        .rotationEffect(.degrees(-90))
      VStack(spacing: 0) {
        Text(Ring.short(value)).font(.system(size: 17, weight: .semibold)).monospacedDigit()
        if !label.isEmpty {
          Text(label).font(.system(size: 9)).foregroundColor(.secondary).lineLimit(1)
        }
      }
    }
  }

  static func short(_ v: Double) -> String {
    v.rounded() == v ? String(Int(v)) : String(format: "%.1f", v)
  }
}

/// 和 app 的主题一个路数（src/theme.tsx）：金 = 助手自己，青 = 数据；Agent 的颜色按名字。
enum Palette {
  static func color(_ key: String?) -> Color {
    switch key ?? "" {
    case "cyan": return Color(red: 0.25, green: 0.66, blue: 0.75)
    case "green": return Color(red: 0.35, green: 0.68, blue: 0.42)
    case "purple": return Color(red: 0.55, green: 0.45, blue: 0.80)
    case "pink": return Color(red: 0.86, green: 0.42, blue: 0.58)
    case "orange": return Color(red: 0.90, green: 0.55, blue: 0.25)
    case let hex where hex.hasPrefix("#") && hex.count == 7:
      let v = Int(hex.dropFirst(), radix: 16) ?? 0xD9AE62
      return Color(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    default: return Color(red: 0.85, green: 0.68, blue: 0.38)
    }
  }
}

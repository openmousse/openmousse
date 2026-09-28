import SwiftUI

/// 和 app 的主题一个路数（src/theme.tsx）：金 = 助手自己，青 = 数据；Agent 的六个颜色按名字；也认 #RRGGBB。
enum WidgetPalette {
  static func color(_ key: String?) -> Color {
    switch key ?? "" {
    case "cyan": return Color(red: 0.25, green: 0.66, blue: 0.75)
    case "green": return Color(red: 0.35, green: 0.68, blue: 0.42)
    case "purple": return Color(red: 0.55, green: 0.45, blue: 0.80)
    case "pink": return Color(red: 0.86, green: 0.42, blue: 0.58)
    case "orange": return Color(red: 0.90, green: 0.55, blue: 0.25)
    case "red": return Color(red: 0.86, green: 0.33, blue: 0.30)
    case "yellow": return Color(red: 0.88, green: 0.70, blue: 0.20)
    case let hex where hex.hasPrefix("#") && hex.count == 7:
      let v = Int(hex.dropFirst(), radix: 16) ?? 0xD9AE62
      return Color(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    default: return Color(red: 0.85, green: 0.68, blue: 0.38)
    }
  }

  /// 日程条目的颜色：课 = 青，训练 = 绿，截止 = 红，吃饭 = 橙，其余 = 金
  static func kind(_ kind: String?) -> Color {
    switch kind ?? "" {
    case "class": return color("cyan")
    case "training": return color("green")
    case "deadline": return color("red")
    case "meal": return color("orange")
    default: return color(nil)
    }
  }
}

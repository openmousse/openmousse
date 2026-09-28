import SwiftUI
import WidgetKit

/// 小组件扩展的入口：桌面「今天」、锁屏「下一件事」「恢复分」，再加实时活动（锁屏 + 灵动岛）。
@main
struct MousseWidgets: WidgetBundle {
  var body: some Widget {
    TodayWidget()
    NextWidget()
    RecoveryWidget()
    MousseLiveActivity()
  }
}

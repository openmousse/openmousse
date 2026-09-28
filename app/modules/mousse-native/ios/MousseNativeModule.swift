import ExpoModulesCore
import WidgetKit

/// app 自己的原生部分（JS 那边是 modules/mousse-native/index.ts）：
/// - 给三个扩展（分享、小组件、通知）共享服务器地址、令牌、名字、语言；小组件的快照和刷新
/// - 分享扩展没传上去的（outbox），app 打开时补传
/// - 实时活动：开、改、结束，令牌报给 JS 再交给服务器
/// - 思考输入框长按菜单里的「换行」「全屏写」
/// 复杂的参数和返回值一律用 JSON 字符串，省得类型桥接出岔子。
public class MousseNativeModule: Module {
  static weak var current: MousseNativeModule?

  public func definition() -> ModuleDefinition {
    Name("MousseNative")

    Events("onEditMenu", "onLiveToken", "onLiveState")

    OnCreate {
      MousseNativeModule.current = self
      EditMenuHook.install()
      LiveActivities.shared.emit = { [weak self] name, body in
        self?.sendEvent(name, body)
      }
    }

    Function("appGroup") { () -> String in
      SharedStore.groupId
    }

    Function("setShared") { (json: String) in
      SharedStore.setShared(json)
    }

    Function("writeWidgetSnapshot") { (json: String) in
      SharedStore.write(file: "widget.json", text: json)
      WidgetCenter.shared.reloadAllTimelines()
    }

    Function("reloadWidgets") {
      WidgetCenter.shared.reloadAllTimelines()
    }

    Function("outbox") { () -> String in
      SharedStore.outbox()
    }

    Function("removeOutbox") { (id: String) in
      SharedStore.removeOutbox(id)
    }

    Function("setEditMenu") { (key: String, itemsJson: String) in
      EditMenuHook.register(key: key, itemsJson: itemsJson)
    }

    Function("liveSupported") { () -> Bool in
      LiveActivities.supported
    }

    AsyncFunction("liveStart") { (key: String, kind: String, stateJson: String, staleAt: Double?) async throws -> String in
      try await LiveActivities.shared.start(key: key, kind: kind, stateJson: stateJson, staleAt: staleAt)
    }

    AsyncFunction("liveUpdate") { (key: String, stateJson: String, staleAt: Double?) async throws -> Bool in
      try await LiveActivities.shared.update(key: key, stateJson: stateJson, staleAt: staleAt)
    }

    AsyncFunction("liveEnd") { (key: String, stateJson: String?) async in
      await LiveActivities.shared.end(key: key, stateJson: stateJson)
    }

    Function("liveList") { () -> String in
      LiveActivities.shared.list()
    }

    Function("liveObserve") {
      LiveActivities.shared.observe()
    }
  }
}

/// app 一启动就把输入框菜单的钩子装上（见 EditMenuHook.install 的说明）。
public class MousseAppDelegateSubscriber: ExpoAppDelegateSubscriber {
  public func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
    EditMenuHook.install()
    return true
  }
}

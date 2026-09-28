import ObjectiveC
import UIKit

/// 输入框长按出来的系统菜单（粘贴、全选、自动填充……）里加自己的项，照微信的「换行」。
///
/// RN 的多行 TextInput 底下是 RCTUITextView，它的 delegate 是 RCTBackedTextViewDelegateAdapter，没有实现
/// iOS 16 的 `textView(_:editMenuForTextIn:suggestedActions:)`。这里在 app 启动时给那个类补上这个方法：
/// 只对登记过的输入框（testID = key，Fabric 里 testID 就是 RCTUITextView 的 accessibilityIdentifier）加项，其余原样返回系统的菜单。
/// RN 哪天自己实现了这个方法，就不动它（菜单里没有我们的项，输入照常）。
enum EditMenuHook {
  private static var installed = false
  private static var registry: [String: [[String: String]]] = [:]
  private static let lock = NSLock()

  /// items：JSON 数组 [{id, title, icon}]，icon 是 SF Symbol 名；空数组 = 取消登记。
  static func register(key: String, itemsJson: String) {
    let parsed = (try? JSONSerialization.jsonObject(with: Data(itemsJson.utf8))) as? [[String: String]] ?? []
    lock.lock()
    registry[key] = parsed.isEmpty ? nil : parsed
    lock.unlock()
  }

  private static func items(for key: String) -> [[String: String]]? {
    lock.lock()
    defer { lock.unlock() }
    return registry[key]
  }

  /// UIKit 可能在设 delegate 时就记下「它答不答这个方法」，所以越早装越好：app 启动时（MousseAppDelegateSubscriber）就装。
  static func install() {
    if installed { return }
    installed = true
    guard let cls = NSClassFromString("RCTBackedTextViewDelegateAdapter") else { return }
    let sel = NSSelectorFromString("textView:editMenuForTextInRange:suggestedActions:")
    if class_getInstanceMethod(cls, sel) != nil { return }
    let block: @convention(block) (AnyObject, UITextView, NSRange, [UIMenuElement]) -> UIMenu? = { _, textView, _, suggested in
      EditMenuHook.menu(for: textView, suggested: suggested)
    }
    class_addMethod(cls, sel, imp_implementationWithBlock(block), "@@:@{_NSRange=QQ}@")
  }

  private static func key(for view: UIView) -> String? {
    var cur: UIView? = view
    var depth = 0
    while let v = cur, depth < 4 {
      if let id = v.accessibilityIdentifier, !id.isEmpty, items(for: id) != nil { return id }
      cur = v.superview
      depth += 1
    }
    return nil
  }

  private static func menu(for textView: UITextView, suggested: [UIMenuElement]) -> UIMenu {
    guard let key = key(for: textView), let list = items(for: key) else {
      return UIMenu(children: suggested)
    }
    let mine: [UIMenuElement] = list.map { item in
      let id = item["id"] ?? ""
      let image = item["icon"].flatMap { UIImage(systemName: $0) }
      return UIAction(title: item["title"] ?? id, image: image) { [weak textView] _ in
        guard let tv = textView else { return }
        EditMenuHook.perform(id: id, key: key, textView: tv)
      }
    }
    return UIMenu(children: mine + suggested)
  }

  /// newline：在光标处插一个换行。RN 把回车当「发送」（submitBehavior=submit），会拦下 "\n"；
  /// 它对粘贴进来的文字不拦（textWasPasted），所以先把这个标记置上再插，插完放回去。找不到这个标记就交给 JS 去插。
  private static func perform(id: String, key: String, textView: UITextView) {
    var handled = false
    if id == "newline", let flag = pastedFlag(of: textView) {
      flag.set(true)
      textView.insertText("\n")
      flag.set(false)
      handled = true
    }
    MousseNativeModule.current?.sendEvent("onEditMenu", ["key": key, "id": id, "handled": handled])
  }

  /// RCTUITextView 的 _textWasPasted（只读属性背后的 BOOL）。用 runtime 找，找不到返回 nil，不会崩。
  private struct Flag {
    let object: AnyObject
    let offset: Int
    func set(_ value: Bool) {
      let base = Unmanaged.passUnretained(object).toOpaque()
      base.advanced(by: offset).storeBytes(of: value, as: Bool.self)
    }
  }

  private static func pastedFlag(of textView: UITextView) -> Flag? {
    var cls: AnyClass? = object_getClass(textView)
    while let c = cls {
      if let ivar = class_getInstanceVariable(c, "_textWasPasted") {
        let enc = ivar_getTypeEncoding(ivar).map { String(cString: $0) } ?? ""
        guard enc == "B" || enc == "c" else { return nil }
        return Flag(object: textView, offset: ivar_getOffset(ivar))
      }
      cls = class_getSuperclass(c)
    }
    return nil
  }
}

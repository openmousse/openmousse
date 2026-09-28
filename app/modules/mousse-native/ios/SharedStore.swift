import Foundation
import Security

/// 主 app 和三个扩展（分享、小组件、通知）共用的东西，都放在 App Group「group.<主 app 的 bundle id>」里：
/// - UserDefaults：服务器地址、助手名字、界面语言（不是秘密）
/// - 钥匙串（访问组 = App Group）：接入令牌
/// - 容器里的文件：小组件的快照 widget.json；分享扩展没传上去的 outbox/<id>/
/// 扩展那边的读法在 targets/*/MousseShared.swift，键名两边要一致。
enum SharedStore {
  static var groupId: String { "group." + (Bundle.main.bundleIdentifier ?? "") }
  static var defaults: UserDefaults? { UserDefaults(suiteName: groupId) }
  static var container: URL? { FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupId) }

  static let keychainService = "mousse.server"
  static let keychainAccount = "token"

  /// JS 传来的 JSON：{serverUrl, token, appName, lang}，给了哪个写哪个。
  static func setShared(_ json: String) {
    guard let obj = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any], let d = defaults else { return }
    for key in ["serverUrl", "appName", "lang"] {
      if let v = obj[key] as? String { d.set(v, forKey: key) }
    }
    if let tok = obj["token"] as? String { setToken(tok) }
  }

  private static func setToken(_ token: String) {
    let base: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: keychainService,
      kSecAttrAccount as String: keychainAccount,
      kSecAttrAccessGroup as String: groupId,
    ]
    SecItemDelete(base as CFDictionary)
    if token.isEmpty { return }
    var add = base
    add[kSecValueData as String] = Data(token.utf8)
    // 锁屏后小组件、通知扩展也要能读（开机后解锁过一次就行）
    add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
    SecItemAdd(add as CFDictionary, nil)
  }

  /// 写进 App Group 容器（先写临时文件再改名，小组件不会读到半截）。
  static func write(file name: String, text: String) {
    guard let dir = container else { return }
    let dst = dir.appendingPathComponent(name)
    let tmp = dir.appendingPathComponent(".\(name).tmp")
    do {
      try Data(text.utf8).write(to: tmp, options: .atomic)
      if FileManager.default.fileExists(atPath: dst.path) {
        _ = try FileManager.default.replaceItemAt(dst, withItemAt: tmp)
      } else {
        try FileManager.default.moveItem(at: tmp, to: dst)
      }
    } catch {
      try? Data(text.utf8).write(to: dst, options: .atomic)
    }
  }

  // —— 分享扩展没传上去的：outbox/<id>/item.json + 同目录的文件 ——

  static var outboxDir: URL? { container?.appendingPathComponent("outbox", isDirectory: true) }

  /// 所有待补传的，JSON 数组；每条的 files 换成绝对的 file:// 地址。
  static func outbox() -> String {
    guard let dir = outboxDir,
          let ids = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else { return "[]" }
    var out: [[String: Any]] = []
    // 分享扩展做到一半被系统收掉会留下 .staging-*：一天以上的清掉
    for id in ids where id.hasPrefix(".staging-") {
      let p = dir.appendingPathComponent(id, isDirectory: true)
      if let d = (try? FileManager.default.attributesOfItem(atPath: p.path))?[.modificationDate] as? Date, Date().timeIntervalSince(d) > 86_400 {
        try? FileManager.default.removeItem(at: p)
      }
    }
    for id in ids.sorted() where !id.hasPrefix(".") {
      let itemDir = dir.appendingPathComponent(id, isDirectory: true)
      guard let data = try? Data(contentsOf: itemDir.appendingPathComponent("item.json")),
            var item = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
      item["id"] = id
      if let files = item["files"] as? [[String: Any]] {
        item["files"] = files.map { f -> [String: Any] in
          var g = f
          if let name = f["path"] as? String { g["uri"] = itemDir.appendingPathComponent(name).absoluteString }
          return g
        }
      }
      out.append(item)
    }
    return json(out)
  }

  static func removeOutbox(_ id: String) {
    guard !id.isEmpty, !id.contains("/"), let dir = outboxDir else { return }
    try? FileManager.default.removeItem(at: dir.appendingPathComponent(id, isDirectory: true))
  }

  static func json(_ obj: Any) -> String {
    guard JSONSerialization.isValidJSONObject(obj),
          let data = try? JSONSerialization.data(withJSONObject: obj) else { return "[]" }
    return String(data: data, encoding: .utf8) ?? "[]"
  }
}

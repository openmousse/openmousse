// 分享（share）和小组件（widgets）两个扩展各放一份，内容完全一样（改了跑 app/scripts/sync-target-shared.sh 同步）。
// 主 app 那边写这些值的是 modules/mousse-native/ios/SharedStore.swift，键名两边要一致。
import Foundation
import Security

enum MousseShared {
  /// 扩展的 bundle id = 主 app 的 bundle id + ".xxx"；App Group = group.<主 app 的 bundle id>
  static var mainBundleId: String {
    let id = Bundle.main.bundleIdentifier ?? ""
    guard Bundle.main.bundleURL.pathExtension == "appex", let dot = id.lastIndex(of: ".") else { return id }
    return String(id[..<dot])
  }

  static var groupId: String { "group." + mainBundleId }
  static var defaults: UserDefaults? { UserDefaults(suiteName: groupId) }
  static var container: URL? { FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupId) }

  static var serverURL: String? {
    guard var s = defaults?.string(forKey: "serverUrl"), !s.isEmpty else { return nil }
    while s.hasSuffix("/") { s.removeLast() }
    return s
  }

  static var appName: String {
    if let n = defaults?.string(forKey: "appName"), !n.isEmpty { return n }
    return (Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String) ?? "OpenMousse"
  }

  /// 界面语言跟 app 走（app 里切过就用 app 的），没切过跟系统。
  static var isChinese: Bool {
    if let l = defaults?.string(forKey: "lang"), !l.isEmpty { return l.hasPrefix("zh") }
    return (Locale.preferredLanguages.first ?? "").hasPrefix("zh")
  }

  static func L(_ zh: String, _ en: String) -> String { isChinese ? zh : en }

  static var token: String? {
    let q: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: "mousse.server",
      kSecAttrAccount as String: "token",
      kSecAttrAccessGroup as String: groupId,
      kSecReturnData as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var out: AnyObject?
    guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
    return String(data: data, encoding: .utf8)
  }

  /// 带令牌和语言的请求；app 还没连过服务器（地址没共享过来）就是 nil。
  static func request(_ path: String, method: String = "GET", timeout: TimeInterval = 15) -> URLRequest? {
    guard let base = serverURL, let url = URL(string: base + path) else { return nil }
    var r = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
    r.httpMethod = method
    r.setValue("application/json", forHTTPHeaderField: "Accept")
    r.setValue(isChinese ? "zh-CN,zh;q=0.9" : "en", forHTTPHeaderField: "Accept-Language")
    if let t = token, !t.isEmpty { r.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
    return r
  }
}

import Foundation

/// 一次分享要存的东西。
struct ShareJob {
  struct File {
    let path: URL
    let name: String
    let mime: String
  }

  let dest: ShareModel.Dest
  let link: URL?
  let title: String
  let text: String
  let note: String
  let files: [File]
}

/// 走和 app 一样的接口（server/saves.py、server/think.py）：
/// - 收藏：有文件 → POST /api/think/saves/upload；否则 POST /api/think/saves（链接服务器会在后台抓正文）
/// - 思考：有文件 → POST /api/think/fragments/upload；否则 POST /api/think/fragments
enum ShareUploader {
  static func send(_ job: ShareJob, workDir: URL) async -> Bool {
    do {
      if job.files.isEmpty {
        return try await sendJSON(job)
      }
      return try await sendFiles(job, workDir: workDir)
    } catch {
      return false
    }
  }

  /// 备注 + 分享的文字 + 链接，拼成一条想法的正文（重复的不拼两遍）
  static func thoughtText(_ job: ShareJob) -> String {
    var parts: [String] = []
    if !job.note.isEmpty { parts.append(job.note) }
    if !job.text.isEmpty { parts.append(job.text) }
    return parts.joined(separator: "\n\n")
  }

  private static func sendJSON(_ job: ShareJob) async throws -> Bool {
    var body: [String: Any]
    let path: String
    switch job.dest {
    case .saves:
      path = "/api/think/saves"
      body = ["note": job.note, "title": job.title]
      if let l = job.link {
        body["url"] = l.absoluteString
        // 分享的文字里常带着标题（小红书：「标题 + 链接」），服务器会把链接以外的部分当标题
        if !job.text.isEmpty { body["text"] = job.text }
      } else {
        body["text"] = job.text
      }
    case .think:
      path = "/api/think/fragments"
      var text = thoughtText(job)
      if let l = job.link, text.contains(l.absoluteString) {
        text = text.replacingOccurrences(of: l.absoluteString, with: "").trimmingCharacters(in: .whitespacesAndNewlines)
      }
      body = ["text": text, "title": job.title]
      if let l = job.link {
        body["url"] = l.absoluteString
        body["kind"] = text.isEmpty ? "link" : "text"
      } else {
        body["kind"] = "text"
      }
    }
    guard var req = MousseShared.request(path, method: "POST", timeout: 25) else { return false }
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = try JSONSerialization.data(withJSONObject: body)
    let (data, resp) = try await URLSession.shared.data(for: req)
    return ok(data, resp)
  }

  private static func sendFiles(_ job: ShareJob, workDir: URL) async throws -> Bool {
    let path: String
    var fields: [(String, String)] = []
    switch job.dest {
    case .saves:
      path = "/api/think/saves/upload"
      fields.append(("note", [job.note, job.text, job.link?.absoluteString ?? ""].filter { !$0.isEmpty }.joined(separator: "\n")))
      fields.append(("source", MousseShared.L("分享", "Share")))
    case .think:
      path = "/api/think/fragments/upload"
      var text = thoughtText(job)
      if let l = job.link, !text.contains(l.absoluteString) { text = text.isEmpty ? l.absoluteString : text + "\n" + l.absoluteString }
      fields.append(("text", text))
      fields.append(("title", job.title))
    }
    guard var req = MousseShared.request(path, method: "POST", timeout: 180) else { return false }
    let boundary = "mousse-\(UUID().uuidString)"
    req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
    let bodyURL = workDir.appendingPathComponent(".body-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: bodyURL) }
    try writeMultipart(to: bodyURL, boundary: boundary, fields: fields, files: job.files)
    let (data, resp) = try await URLSession.shared.upload(for: req, fromFile: bodyURL)
    return ok(data, resp)
  }

  /// 边读边写，大文件不整个读进内存（分享扩展只有 120 MB 左右的内存）。
  private static func writeMultipart(to url: URL, boundary: String, fields: [(String, String)], files: [ShareJob.File]) throws {
    FileManager.default.createFile(atPath: url.path, contents: nil)
    let out = try FileHandle(forWritingTo: url)
    defer { try? out.close() }
    func put(_ s: String) { out.write(Data(s.utf8)) }
    for (name, value) in fields where !value.isEmpty {
      put("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n")
    }
    for f in files {
      let safe = f.name.replacingOccurrences(of: "\"", with: "'")
      put("--\(boundary)\r\nContent-Disposition: form-data; name=\"files\"; filename=\"\(safe)\"\r\nContent-Type: \(f.mime)\r\n\r\n")
      let input = try FileHandle(forReadingFrom: f.path)
      defer { try? input.close() }
      while true {
        let chunk = input.readData(ofLength: 1 << 20)
        if chunk.isEmpty { break }
        out.write(chunk)
      }
      put("\r\n")
    }
    put("--\(boundary)--\r\n")
  }

  private static func ok(_ data: Data, _ resp: URLResponse) -> Bool {
    guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { return false }
    if let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let flag = obj["ok"] as? Bool {
      return flag
    }
    return true
  }
}

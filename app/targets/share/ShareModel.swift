import Foundation
import ImageIO
import UIKit
import UniformTypeIdentifiers

/// 分享进来的东西 + 卡片的状态。附件一拿到就拷进 App Group 的暂存目录（系统给的临时文件出了回调就没了），
/// 传成功就删掉；传不上就把暂存目录改成 outbox/<id>，写一份 item.json，app 打开时补传（modules/mousse-native 的 outbox()）。
final class ShareModel: ObservableObject {
  enum Dest: String { case saves, think }

  enum Phase: Equatable {
    case editing
    case saving
    case saved
    /// 没传上，先存在手机上了
    case queued
    case failed(String)
  }

  struct SharedFile: Identifiable {
    let id = UUID()
    let path: URL
    let name: String
    let mime: String
    let size: Int64
    let isImage: Bool
    let thumb: UIImage?
  }

  /// 单个文件上限，和服务器一致（server/think.py 的 UPLOAD_MAX）
  static let maxFileBytes: Int64 = 30 * 1024 * 1024
  static let maxFiles = 10

  @Published var loading = true
  @Published var link: URL?
  @Published var title = ""
  @Published var text = ""
  @Published var files: [SharedFile] = []
  @Published var note = ""
  @Published var dest: Dest
  @Published var phase: Phase = .editing
  @Published var skipped = 0

  var finish: () -> Void = {}

  private let staging: URL
  private let group = DispatchGroup()
  private let lock = NSLock()

  init() {
    let last = MousseShared.defaults?.string(forKey: "shareDest") ?? ""
    dest = Dest(rawValue: last) ?? .saves
    let root = MousseShared.container?.appendingPathComponent("outbox", isDirectory: true)
      ?? FileManager.default.temporaryDirectory.appendingPathComponent("outbox", isDirectory: true)
    staging = root.appendingPathComponent(".staging-\(UUID().uuidString)", isDirectory: true)
    try? FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
  }

  var isEmpty: Bool { link == nil && text.isEmpty && files.isEmpty }
  var tooBig: SharedFile? { files.first { $0.size > ShareModel.maxFileBytes } }
  var canSave: Bool {
    if case .failed = phase { return !loading && !isEmpty && tooBig == nil }  // 没存上可以再点一次
    return !loading && !isEmpty && tooBig == nil && phase == .editing
  }

  // —— 读分享进来的东西 ——————————————————————————————————————————

  func load(_ items: [NSExtensionItem]) {
    for item in items {
      if let t = item.attributedContentText?.string.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty {
        lock.lock(); if title.isEmpty { title = String(t.prefix(200)) }; lock.unlock()
      }
      for provider in item.attachments ?? [] {
        load(provider)
      }
    }
    group.notify(queue: .main) { [weak self] in
      guard let self else { return }
      // 只分享了一段带链接的文字（小红书、公众号「复制链接」再分享）：把链接拎出来
      if self.link == nil, let found = ShareModel.firstLink(in: self.text) {
        self.link = found
      }
      if self.title == self.text { self.title = "" }
      self.loading = false
    }
  }

  private func load(_ p: NSItemProvider) {
    let url = UTType.url.identifier
    let fileURL = UTType.fileURL.identifier
    if p.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
      copyFile(p, type: UTType.image.identifier, isImage: true)
    } else if p.hasItemConformingToTypeIdentifier(UTType.movie.identifier) {
      copyFile(p, type: UTType.movie.identifier, isImage: false)
    } else if p.hasItemConformingToTypeIdentifier(fileURL) || (!p.hasItemConformingToTypeIdentifier(url) && !p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier)) {
      // 文件（微信里「用其他应用打开」、文件 App）：按它自己登记的第一种类型拿
      let type = p.registeredTypeIdentifiers.first { $0 != fileURL } ?? UTType.data.identifier
      copyFile(p, type: type, isImage: false)
    } else if p.hasItemConformingToTypeIdentifier(url) {
      group.enter()
      p.loadItem(forTypeIdentifier: url, options: nil) { [weak self] item, _ in
        defer { self?.group.leave() }
        let u = (item as? URL) ?? (item as? String).flatMap { URL(string: $0) }
        guard let self, let u else { return }
        if u.isFileURL {
          // 有的 App 把文件当「链接」分享（file://）：当文件存
          let scoped = u.startAccessingSecurityScopedResource()
          self.add(file: u, suggested: nil, isImage: false)
          if scoped { u.stopAccessingSecurityScopedResource() }
          return
        }
        DispatchQueue.main.async { if self.link == nil { self.link = u } }
      }
    } else {
      group.enter()
      p.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { [weak self] item, _ in
        defer { self?.group.leave() }
        let s = (item as? String) ?? (item as? Data).flatMap { String(data: $0, encoding: .utf8) } ?? ""
        guard let self, !s.isEmpty else { return }
        DispatchQueue.main.async { self.text = self.text.isEmpty ? s : self.text + "\n" + s }
      }
    }
  }

  private func copyFile(_ p: NSItemProvider, type: String, isImage: Bool) {
    group.enter()
    let suggested = p.suggestedName
    p.loadFileRepresentation(forTypeIdentifier: type) { [weak self] tmp, _ in
      guard let self else { return }
      defer { self.group.leave() }
      if let tmp {
        self.add(file: tmp, suggested: suggested, isImage: isImage)
      } else if isImage {
        // 有的只给内存里的图（截图直接分享）：拿 UIImage 存成 JPEG
        self.group.enter()
        p.loadItem(forTypeIdentifier: type, options: nil) { item, _ in
          defer { self.group.leave() }
          let img = (item as? UIImage) ?? (item as? Data).flatMap { UIImage(data: $0) }
          guard let data = img?.jpegData(compressionQuality: 0.9) else { return }
          let dst = self.staging.appendingPathComponent("\(suggested ?? "image")-\(UUID().uuidString.prefix(6)).jpg")
          if (try? data.write(to: dst)) != nil { self.add(file: dst, suggested: nil, isImage: true, move: true) }
        }
      }
    }
  }

  /// 在系统的回调里同步拷走（回调一结束临时文件就被删）。
  private func add(file src: URL, suggested: String?, isImage: Bool, move: Bool = false) {
    lock.lock()
    let count = files.count
    lock.unlock()
    if count >= ShareModel.maxFiles {
      DispatchQueue.main.async { self.skipped += 1 }
      return
    }
    let ext = src.pathExtension
    var name = src.lastPathComponent
    if let s = suggested, !s.isEmpty {
      name = ext.isEmpty || s.lowercased().hasSuffix("." + ext.lowercased()) ? s : "\(s).\(ext)"
    }
    name = name.replacingOccurrences(of: "/", with: "_")
    let dst = staging.appendingPathComponent("\(UUID().uuidString.prefix(8))-\(name)")
    do {
      if move {
        try FileManager.default.moveItem(at: src, to: dst)
      } else {
        try FileManager.default.copyItem(at: src, to: dst)
      }
    } catch {
      return
    }
    let size = ((try? FileManager.default.attributesOfItem(atPath: dst.path)[.size]) as? NSNumber)?.int64Value ?? 0
    let mime = UTType(filenameExtension: dst.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
    let image = isImage || mime.hasPrefix("image/")
    let thumb = image ? ShareModel.thumbnail(dst, max: 180) : nil
    let f = SharedFile(path: dst, name: name, mime: mime, size: size, isImage: image, thumb: thumb)
    lock.lock()
    let full = files.count >= ShareModel.maxFiles
    lock.unlock()
    DispatchQueue.main.async {
      if full { self.skipped += 1 } else { self.files.append(f) }
    }
  }

  static func thumbnail(_ url: URL, max: Int) -> UIImage? {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    let opts: [CFString: Any] = [
      kCGImageSourceCreateThumbnailFromImageAlways: true,
      kCGImageSourceCreateThumbnailWithTransform: true,
      kCGImageSourceThumbnailMaxPixelSize: max,
    ]
    guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
    return UIImage(cgImage: cg)
  }

  static func firstLink(in text: String) -> URL? {
    guard let det = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else { return nil }
    let range = NSRange(text.startIndex..., in: text)
    return det.firstMatch(in: text, options: [], range: range)?.url
  }

  // —— 存 ——————————————————————————————————————————————————————

  func save() {
    guard canSave else { return }
    MousseShared.defaults?.set(dest.rawValue, forKey: "shareDest")
    phase = .saving
    let job = ShareJob(dest: dest, link: link, title: title, text: text, note: note.trimmingCharacters(in: .whitespacesAndNewlines),
                       files: files.prefix(ShareModel.maxFiles).map { ShareJob.File(path: $0.path, name: $0.name, mime: $0.mime) })
    Task {
      let ok = await ShareUploader.send(job, workDir: staging)
      await MainActor.run {
        if ok {
          try? FileManager.default.removeItem(at: self.staging)
          self.phase = .saved
          DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { self.finish() }
        } else if self.queue(job) {
          self.phase = .queued
        } else {
          self.phase = .failed(MousseShared.L("没存上，也没法先放在手机上。打开 app 看看服务器连没连上。",
                                              "Couldn't save it, and couldn't keep it on the phone either. Open the app and check the server."))
        }
      }
    }
  }

  /// 传不上：暂存目录改名成 outbox/<时间-随机>，写 item.json。app 下次打开时补传（再也传不上的由 app 提示）。
  private func queue(_ job: ShareJob) -> Bool {
    guard MousseShared.container != nil else { return false }
    let id = "\(Int(Date().timeIntervalSince1970))-\(UUID().uuidString.prefix(8))"
    let dst = staging.deletingLastPathComponent().appendingPathComponent(id, isDirectory: true)
    var item: [String: Any] = [
      "dest": job.dest.rawValue, "note": job.note, "text": job.text, "title": job.title,
      "createdAt": Date().timeIntervalSince1970,
      "files": job.files.map { ["path": $0.path.lastPathComponent, "name": $0.name, "mime": $0.mime] },
    ]
    if let l = job.link { item["url"] = l.absoluteString }
    guard let data = try? JSONSerialization.data(withJSONObject: item) else { return false }
    do {
      try data.write(to: staging.appendingPathComponent("item.json"))
      try FileManager.default.moveItem(at: staging, to: dst)
      return true
    } catch {
      return false
    }
  }

  func cancel() {
    try? FileManager.default.removeItem(at: staging)
    finish()
  }
}

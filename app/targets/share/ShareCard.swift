import SwiftUI
import UIKit

/// 分享进来时弹的卡：上面是分享的东西，中间选「存到收藏 / 放进思考」，下面一句备注。系统表单的样子，和「提醒事项」的分享卡一个路数。
struct ShareCard: View {
  @ObservedObject var model: ShareModel
  @FocusState private var noteFocused: Bool

  private func L(_ zh: String, _ en: String) -> String { MousseShared.L(zh, en) }

  var body: some View {
    NavigationView {
      Form {
        Section {
          preview
        }
        Section {
          Picker(L("存到", "Save to"), selection: $model.dest) {
            Text(L("收藏", "Saved")).tag(ShareModel.Dest.saves)
            Text(L("思考", "Think")).tag(ShareModel.Dest.think)
          }
          .pickerStyle(.segmented)
        } footer: {
          Text(model.dest == .saves
               ? L("原样存着，\(MousseShared.appName) 先不看，等你决定怎么处理。", "Kept as is. \(MousseShared.appName) won't look at it until you decide.")
               : L("变成一条想法，放进思考空间，AI 不回。", "Becomes a thought in your thinking space. No reply."))
        }
        Section {
          TextField(L("一句备注（可以不写）", "A note (optional)"), text: $model.note, axis: .vertical)
            .lineLimit(1...5)
            .focused($noteFocused)
        }
        if let status = statusText {
          Section {
            HStack(spacing: 10) {
              statusIcon
              Text(status).font(.callout).foregroundColor(statusColor)
            }
          }
        }
      }
      .navigationTitle(MousseShared.appName)
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button(L("取消", "Cancel")) { model.cancel() }
            .disabled(model.phase == .saving)
        }
        ToolbarItem(placement: .confirmationAction) {
          if model.phase == .queued {
            Button(L("好", "OK")) { model.finish() }
          } else {
            Button(L("存", "Save")) { model.save() }
              .disabled(!model.canSave)
          }
        }
      }
    }
    .navigationViewStyle(.stack)
  }

  // —— 分享的东西 ——

  @ViewBuilder private var preview: some View {
    if model.loading {
      HStack(spacing: 10) {
        ProgressView()
        Text(L("正在读取…", "Reading…")).foregroundColor(.secondary)
      }
    } else if model.isEmpty {
      Text(L("没读到能存的内容。", "Nothing here to save.")).foregroundColor(.secondary)
    } else {
      if let link = model.link {
        HStack(alignment: .top, spacing: 12) {
          Image(systemName: "link")
            .font(.system(size: 17, weight: .semibold))
            .foregroundColor(.accentColor)
            .frame(width: 28, height: 28)
          VStack(alignment: .leading, spacing: 3) {
            Text(linkTitle(link)).font(.body).lineLimit(2)
            Text(link.host ?? link.absoluteString).font(.caption).foregroundColor(.secondary).lineLimit(1)
          }
        }
      }
      if !model.text.isEmpty && !(model.link != nil && textIsJustLink) {
        Text(model.text).font(.callout).lineLimit(4).foregroundColor(.primary)
      }
      if !images.isEmpty {
        ScrollView(.horizontal, showsIndicators: false) {
          HStack(spacing: 8) {
            ForEach(images) { f in
              if let t = f.thumb {
                Image(uiImage: t).resizable().scaledToFill()
                  .frame(width: 64, height: 64)
                  .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
              } else {
                RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color(uiColor: .secondarySystemFill))
                  .frame(width: 64, height: 64)
                  .overlay(Image(systemName: "photo").foregroundColor(.secondary))
              }
            }
          }
          .padding(.vertical, 2)
        }
      }
      ForEach(others) { f in
        HStack(spacing: 12) {
          Image(systemName: f.mime.hasPrefix("video/") ? "film" : f.mime == "application/pdf" ? "doc.richtext" : "doc")
            .font(.system(size: 17))
            .foregroundColor(.accentColor)
            .frame(width: 28, height: 28)
          VStack(alignment: .leading, spacing: 2) {
            Text(f.name).lineLimit(1)
            Text(ByteCountFormatter.string(fromByteCount: f.size, countStyle: .file))
              .font(.caption)
              .foregroundColor(f.size > ShareModel.maxFileBytes ? .red : .secondary)
          }
        }
      }
      if model.skipped > 0 {
        Text(L("一次最多存 \(ShareModel.maxFiles) 个，多出来的 \(model.skipped) 个没带上。", "Up to \(ShareModel.maxFiles) at a time; \(model.skipped) left out."))
          .font(.caption).foregroundColor(.secondary)
      }
    }
  }

  private var images: [ShareModel.SharedFile] { model.files.filter { $0.isImage } }
  private var others: [ShareModel.SharedFile] { model.files.filter { !$0.isImage } }

  private var textIsJustLink: Bool {
    guard let l = model.link else { return false }
    return model.text.trimmingCharacters(in: .whitespacesAndNewlines) == l.absoluteString
  }

  private func linkTitle(_ link: URL) -> String {
    if !model.title.isEmpty { return model.title }
    let t = model.text.replacingOccurrences(of: link.absoluteString, with: "").trimmingCharacters(in: .whitespacesAndNewlines)
    return t.isEmpty ? (link.host ?? link.absoluteString) : t
  }

  // —— 存的状态 ——

  private var statusText: String? {
    if let big = model.tooBig {
      return L("「\(big.name)」超过 30 MB，存不了。", "\"\(big.name)\" is over 30 MB.")
    }
    switch model.phase {
    case .editing: return nil
    case .saving: return L("正在存…", "Saving…")
    case .saved: return model.dest == .saves ? L("存进收藏了", "Saved") : L("放进思考了", "Added to Think")
    case .queued: return L("没连上服务器，先存在手机上了；打开 \(MousseShared.appName) 时会自动补传。",
                           "Couldn't reach the server. Kept on this phone; it'll upload next time you open \(MousseShared.appName).")
    case .failed(let why): return why
    }
  }

  @ViewBuilder private var statusIcon: some View {
    switch model.phase {
    case .saving: ProgressView()
    case .saved: Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
    case .queued: Image(systemName: "icloud.and.arrow.up").foregroundColor(.orange)
    default: Image(systemName: "exclamationmark.circle").foregroundColor(.red)
    }
  }

  private var statusColor: Color {
    switch model.phase {
    case .saved: return .primary
    case .saving: return .secondary
    default: return model.tooBig != nil ? .red : .primary
    }
  }
}

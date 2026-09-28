import SwiftUI
import UIKit

/// 分享扩展的入口（Info.plist 的 NSExtensionPrincipalClass）。
/// 在微信、Safari、小红书、相册里点「分享 → Grava」：弹一张卡，选「存到收藏」还是「放进思考」，写一句备注，存完自己收起，不跳出原来的 App。
/// 直接传给服务器；没连上（没开 Tailscale、断网）就先存在 App Group 的 outbox 里，下次打开 app 时补传。
class ShareViewController: UIViewController {
  private let model = ShareModel()

  override func viewDidLoad() {
    super.viewDidLoad()
    model.finish = { [weak self] in
      self?.extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
    }
    let host = UIHostingController(rootView: ShareCard(model: model))
    addChild(host)
    host.view.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(host.view)
    NSLayoutConstraint.activate([
      host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
      host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
      host.view.topAnchor.constraint(equalTo: view.topAnchor),
      host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
    ])
    host.didMove(toParent: self)
    let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
    model.load(items)
  }
}

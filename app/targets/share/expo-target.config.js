// 分享扩展：在别的 App 里「分享 → 助手名」，存到收藏或放进思考。代码在本目录，说明见 ShareViewController.swift。
/** @type {import('@bacons/apple-targets/app.plugin').ConfigFunction} */
module.exports = (config) => ({
  type: 'share',
  name: 'share',
  displayName: config.name,
  bundleIdentifier: '.share',
  deploymentTarget: '16.4',
  frameworks: ['SwiftUI', 'UniformTypeIdentifiers'],
  entitlements: {
    'com.apple.security.application-groups': config.ios.entitlements['com.apple.security.application-groups'],
  },
});

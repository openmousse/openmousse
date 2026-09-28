// 通知内容扩展：长按「卡片更新了」「等你点头」的通知，展开成一张卡（数字、进度环、要点）。按钮还是 app 注册的那组（看卡片 / 同意 / 看一下）。
/** @type {import('@bacons/apple-targets/app.plugin').ConfigFunction} */
module.exports = (config) => ({
  type: 'notification-content',
  name: 'notify',
  displayName: config.name,
  bundleIdentifier: '.notify',
  deploymentTarget: '16.4',
  frameworks: ['SwiftUI', 'UserNotifications', 'UserNotificationsUI'],
  entitlements: {
    'com.apple.security.application-groups': config.ios.entitlements['com.apple.security.application-groups'],
  },
});

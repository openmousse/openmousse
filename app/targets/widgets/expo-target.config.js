// 小组件 + 实时活动：桌面 / 锁屏的「今天」（恢复分、下一餐、下一件事），锁屏和灵动岛的倒计时（练后餐、冥想时间）。
// 数据：服务器的 /api/widget（WidgetData.swift），拿不到就用 app 上次写进 App Group 的快照。
/** @type {import('@bacons/apple-targets/app.plugin').ConfigFunction} */
module.exports = (config) => ({
  type: 'widget',
  name: 'widgets',
  displayName: config.name,
  bundleIdentifier: '.widgets',
  deploymentTarget: '16.4',
  colors: {
    $accent: { color: '#B8893E', darkColor: '#D9AE62' },
    $widgetBackground: { color: '#FFFFFF', darkColor: '#101114' },
  },
  entitlements: {
    'com.apple.security.application-groups': config.ios.entitlements['com.apple.security.application-groups'],
  },
});

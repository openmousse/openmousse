// 扩展（targets/ 里的分享、小组件、通知）的版本号跟 app 走。
// @bacons/apple-targets 给每个扩展写死 MARKETING_VERSION = 1.0，App Store Connect 会因为扩展和 app 的版本不一致发警告（ITMS-90473）。
// 在所有改动写完以后（finalized）把工程文件里扩展的 MARKETING_VERSION 换成 app.json 的 version；app 自己的那一行本来就是 version，不受影响。
const fs = require('fs');
const path = require('path');
const { withFinalizedMod } = require('expo/config-plugins');

module.exports = function withExtensionVersions(config) {
  return withFinalizedMod(config, [
    'ios',
    async (cfg) => {
      const version = cfg.version;
      const iosDir = cfg.modRequest.platformProjectRoot;
      const proj = fs.existsSync(iosDir) ? fs.readdirSync(iosDir).find((f) => f.endsWith('.xcodeproj')) : null;
      if (!version || !proj) return cfg;
      const file = path.join(iosDir, proj, 'project.pbxproj');
      if (!fs.existsSync(file)) return cfg;
      const before = fs.readFileSync(file, 'utf8');
      const after = before.replace(/MARKETING_VERSION = "?1\.0"?;/g, `MARKETING_VERSION = ${version};`);
      if (after !== before) fs.writeFileSync(file, after);
      return cfg;
    },
  ]);
};

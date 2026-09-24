// 动态配置：app.json 是通用模板（OpenMousse），本机的 app.local.json（不进 git）叠加你自己的名字、包名、图标目录、EAS 项目。
// 权限说明里的 {name} 会换成你的助手名。样板见 app.local.example.json。
// 环境变量 MOUSSE_LOCAL 可以换用别的身份文件（比如同一个账号下再发一个通用的 OpenMousse：eas.json 的 production-openmousse 配置用 app.local.openmousse.json）；
// 设成 none 就完全不叠加，得到纯模板。
const fs = require('fs');
const path = require('path');

const fill = (v, name) => (typeof v === 'string' ? v.replace(/\{name\}/g, name) : v);
const fillObj = (o, name) => (o && typeof o === 'object' && !Array.isArray(o) ? Object.fromEntries(Object.entries(o).map(([k, v]) => [k, fill(v, name)])) : o);

module.exports = ({ config }) => {
  const localName = process.env.MOUSSE_LOCAL || 'app.local.json';
  const localPath = path.join(__dirname, localName);
  const local = localName !== 'none' && fs.existsSync(localPath) ? JSON.parse(fs.readFileSync(localPath, 'utf8')) : {};
  const name = local.name || config.name;
  const slug = local.slug || config.slug;
  const icons = `./${(local.icons || 'assets').replace(/^\.?\//, '').replace(/\/$/, '')}`;
  const out = {
    ...config,
    name,
    slug,
    scheme: local.scheme || slug,
    icon: `${icons}/icon.png`,
    ios: { ...config.ios, bundleIdentifier: local.iosBundleId || config.ios.bundleIdentifier, infoPlist: fillObj(config.ios.infoPlist, name) },
    android: {
      ...config.android,
      package: local.androidPackage || config.android.package,
      adaptiveIcon: { ...config.android.adaptiveIcon, foregroundImage: `${icons}/android-icon-foreground.png`, backgroundImage: `${icons}/android-icon-background.png`, monochromeImage: `${icons}/android-icon-monochrome.png` },
    },
    web: { ...config.web, favicon: `${icons}/favicon.png` },
    plugins: (config.plugins || []).map((p) => (Array.isArray(p) ? [p[0], fillObj(p[1], name)] : p)),
  };
  if (local.easProjectId) {
    out.extra = { ...(config.extra || {}), eas: { projectId: local.easProjectId } };
    out.updates = { url: `https://u.expo.dev/${local.easProjectId}` };
  }
  if (local.owner) out.owner = local.owner;
  return out;
};

// 助手的名字来自服务器（server.json 的 app_name），每个实例不一样。
// 连上之前用上次记住的名字，第一次用 OpenMousse。界面文字一律用 agentName()，不写死。
let name = 'OpenMousse';
export const agentName = () => name;
export const setAgentName = (n: string) => { name = (n || '').trim() || 'OpenMousse'; };

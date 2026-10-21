// 服务器的库里「你自己」记成什么（../../server/config.py 的 owner_id）：老的安装是 'leo'，新装是 'owner'。两种都认；
// 'leo' 别删：老数据、老服务器都还在用。服务器要是配了别的值，这里一起加上。
export const OWNER_IDS: readonly string[] = ['owner', 'leo'];

/** 这一条是你自己加的、改的（不是主对话，也不是哪个 Agent）。 */
export const isOwner = (by: string | null | undefined): boolean => !!by && OWNER_IDS.includes(by);

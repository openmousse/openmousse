// 存储和协议里的值（../../server/stored_values.py）：服务器的老数据、老的 app、别人的服务器都认这些值，别改值；代码里别处只引用这里的名字。
// 名字是很早定下的，所以和项目名对不上。

/** 助手说的话在消息里的 role（服务器 messages.role，/api/chat 的消息、/api/chats 的 lineRole、搜索结果的 role 都是它） */
export const ASSISTANT_ROLE = 'grava';
export type AssistantRole = typeof ASSISTANT_ROLE;

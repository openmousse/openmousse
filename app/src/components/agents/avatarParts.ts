// 形象编辑器用的几样（主线第三步）：「随机一个」照设计稿的规则（和服务器 cybers.random_avatar 一样），编辑器做完交回给打开它的那一页。
import type { AvatarParts, CyberAvatarSpec } from '../../api/cybers';

export const HAIR_KEYS: AvatarParts['hair'][] = ['bun', 'short', 'buzz', 'bob', 'long', 'pony', 'curly', 'bald'];
export const HAIR_COLOR_KEYS: AvatarParts['hairColor'][] = ['black', 'dark', 'chestnut', 'flax', 'grey', 'wine'];
export const BEARD_KEYS: AvatarParts['beard'][] = ['none', 'stubble', 'mustache', 'full'];
export const COLLAR_KEYS: AvatarParts['collar'][] = ['shirt', 'v', 'round', 'turtle'];
export const CLOTHES_KEYS: AvatarParts['clothes'][] = ['cyan', 'camel', 'purple', 'green', 'rose', 'rust'];
export const SKIN_KEYS: AvatarParts['skin'][] = ['stone', 'warm', 'wheat', 'tan', 'deep'];
export const BG_KEYS: AvatarParts['bg'][] = ['cyan', 'gold', 'purple', 'green', 'pink', 'orange'];
export const ACC_COLOR_KEYS: AvatarParts['accColor'][] = ['black', 'gold', 'silver', 'cyan'];
export const ACC_KEYS: (keyof AvatarParts['acc'])[] = ['glasses', 'phones', 'earring', 'hat'];
/** 衣服颜色和底色的同色系：随机时底色避开衣服那一系（20 像素时只剩轮廓和颜色也认得出） */
export const FAMILY: Record<AvatarParts['clothes'], AvatarParts['bg']> = { cyan: 'cyan', camel: 'gold', purple: 'purple', green: 'green', rose: 'pink', rust: 'orange' };

const pick = <T,>(xs: T[]): T => xs[Math.floor(Math.random() * xs.length)];

/** 「随机一个」：发型、发色、衣领、衣服、肤色、底色、配饰颜色各随机；胡子 60% 没有；眼镜 40%、耳机 25%、耳环 25%、帽子 15%。 */
export function randomParts(): AvatarParts {
  const clothes = pick(CLOTHES_KEYS);
  return {
    hair: pick(HAIR_KEYS), hairColor: pick(HAIR_COLOR_KEYS), beard: Math.random() < 0.6 ? 'none' : pick(BEARD_KEYS.slice(1)),
    collar: pick(COLLAR_KEYS), clothes, skin: pick(SKIN_KEYS), bg: pick(BG_KEYS.filter((b) => b !== FAMILY[clothes])), accColor: pick(ACC_COLOR_KEYS),
    acc: { glasses: Math.random() < 0.4, phones: Math.random() < 0.25, earring: Math.random() < 0.25, hat: Math.random() < 0.15 },
  };
}

// 编辑器做完（「完成」）交回给打开它的那一页：草稿页还没有这位赛博，存不到服务器上，先放在这里，那一页回到前台时取走。
const pending: Record<string, CyberAvatarSpec> = {};
export const putAvatar = (key: string, avatar: CyberAvatarSpec) => { pending[key] = avatar; };
export function takeAvatar(key: string): CyberAvatarSpec | null {
  const a = pending[key] ?? null;
  delete pending[key];
  return a;
}

// 内置看板（健身、饮食、睡眠、求职……）按小节画：每一节也是看板上的一块，按服务器给的顺序（board.sections）排，能挪、能藏，
// 长按一节（它的标题或卡片空白处）出菜单。内容还是 app 自己画的（DietBoard / FitnessBoard 各节），挂在某一节后面的积木跟着它走。
import React from 'react';
import { Pressable } from 'react-native';
import { Slot, sectionsOf } from './BoardContext';
import { useBoard } from './ctx';

/** 一节一个元素，按默认顺序写（服务器没给顺序时就按这个）；null = 这一节今天没东西（比如没有采购单），它后面的积木照样显示。 */
export type SectionEls = Record<string, React.ReactNode>;

export function SectionedBoard({ els }: { els: SectionEls }) {
  const ctx = useBoard();
  const ids = Object.keys(els);
  const known = sectionsOf(ctx?.board ?? null).filter((s) => ids.includes(s.id));
  const order = [...known, ...ids.filter((id) => !known.some((s) => s.id === id)).map((id) => ({ id, title: '', hidden: false }))];
  return (
    <>
      <Slot at="top" />
      {order.map((s) => (
        <React.Fragment key={s.id}>
          {s.hidden || !els[s.id] ? null : <SectionShell id={s.id}>{els[s.id]}</SectionShell>}
          <Slot at={s.id} />
        </React.Fragment>
      ))}
      <Slot at="" />
    </>
  );
}

function SectionShell({ id, children }: { id: string; children: React.ReactNode }) {
  const ctx = useBoard();
  const onLong = ctx?.openSectionMenu && !ctx.readOnly && ctx.board ? () => ctx.openSectionMenu?.(id) : undefined;
  return <Pressable onLongPress={onLong} delayLongPress={450} disabled={!onLong}>{children}</Pressable>;
}

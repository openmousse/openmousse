// 冥想时间在不在进行（store 弹小窗前看一眼）。放在单独的小文件里：store 和思考的 Provider 都要用，互相引会成环。
let on = false;

export const isMeditating = () => on;
export const setMeditating = (v: boolean) => { on = v; };

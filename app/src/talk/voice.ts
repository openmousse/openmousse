// 对讲（第 8 批，13.6，2026-10-19）念回复用的声音。默认是手机自带的朗读（expo-speech：新加的原生模块，要 1.0.7 起的安装包，
// 网页版、老安装包没有就是 null，对讲页说要更新 app）。
// 留了一个口子给服务器的语音合成（第 3 批）：它接进来时用 setServerVoice 登记一个 Voice，服务器的 features 里有 'tts' 才用它，
// 别的时候照旧用手机自带的。原生模块在才 require（同 src/hardware/ble.ts、src/nodes/phone.ts）。
import { Platform } from 'react-native';
import { supports } from '../api/version';

type SpeechModule = typeof import('expo-speech');

export interface Voice {
  /**
   * 念一段（排在前面的念完才念它）；念完、被停、出错都只回一次 done。
   * start：真开始出声时回（对讲从这时起才量喇叭的音量判打断；不回 = 只能点一下打断）。
   */
  say(text: string, lang: 'zh' | 'en', done: () => void, start?: () => void): void;
  /** 马上停，排着的都不念了 */
  stop(): void;
}

let mod: SpeechModule | null | undefined;
/** expo-speech：网页版、1.0.7 以前的安装包 → null。 */
export function speechModule(): SpeechModule | null {
  if (mod !== undefined) return mod;
  mod = null;
  if (Platform.OS !== 'web') {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { requireOptionalNativeModule } = require('expo-modules-core') as typeof import('expo-modules-core');
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      if (requireOptionalNativeModule('ExpoSpeech')) mod = require('expo-speech') as SpeechModule;
    } catch {
      mod = null;
    }
  }
  return mod;
}

function phoneVoice(m: SpeechModule): Voice {
  return {
    say(text, lang, done, start) {
      let called = false;
      const once = () => { if (!called) { called = true; done(); } };
      try {
        m.speak(text, { language: lang === 'zh' ? 'zh-CN' : 'en-US', onStart: () => start?.(), onDone: once, onStopped: once, onError: once });
      } catch {
        once();
      }
    },
    stop() {
      m.stop().catch(() => {});
    },
  };
}

let serverVoice: Voice | null = null;
/** 服务器的语音合成接进来时登记（null = 拿掉）。 */
export function setServerVoice(v: Voice | null) {
  serverVoice = v;
}

/** 这一次对讲用哪个声音：服务器报了 tts、又登记了就用服务器的，否则手机自带的；都没有是 null。 */
export function pickVoice(): Voice | null {
  if (serverVoice && supports('tts')) return serverVoice;
  const m = speechModule();
  return m ? phoneVoice(m) : null;
}

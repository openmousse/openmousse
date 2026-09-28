// 播客的录音、播放、录的时候屏幕不锁。都用已经在原生包里的模块（expo-audio、expo 自带的 ExpoKeepAwake），热更新就能发。
import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';
import { requireOptionalNativeModule } from 'expo';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import type { PendingFile } from '../data/types';
import { audioSource, type PodSegment } from '../api/podcast';

// 和想法里的语音一样：16 kHz 单声道 32 kbps 的 m4a（一小时约 14 MB）；加上音量（画波形）
const PRESET = { ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000, isMeteringEnabled: true };

type KeepAwake = { activate?: (tag: string) => Promise<unknown>; deactivate?: (tag: string) => Promise<unknown> };
type WakeLock = { release: () => Promise<void> };
let keepMod: KeepAwake | null | undefined;
let wakeLock: WakeLock | null = null;

/** 录的时候屏幕不锁（锁屏 = app 进后台，原生包没开后台录音，这一段就停了）。拿不到模块就算了。 */
export async function keepAwake(on: boolean) {
  try {
    if (Platform.OS === 'web') {
      const nav = (typeof navigator !== 'undefined' ? navigator : null) as unknown as { wakeLock?: { request: (t: 'screen') => Promise<WakeLock> } } | null;
      if (on && nav?.wakeLock && !wakeLock) wakeLock = await nav.wakeLock.request('screen');
      if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
      return;
    }
    if (keepMod === undefined) keepMod = requireOptionalNativeModule<KeepAwake>('ExpoKeepAwake');
    await (on ? keepMod?.activate?.('mousse-podcast') : keepMod?.deactivate?.('mousse-podcast'));
  } catch { /* 没有就算了 */ }
}

/** 一段一段录：start → stop 拿到这一段的文件和秒数。metering 是最近的音量（dBFS，-160…0）。 */
export function useTakeRecorder() {
  const recorder = useAudioRecorder(PRESET);
  const state = useAudioRecorderState(recorder, 120);
  const start = useCallback(async (): Promise<'ok' | 'denied'> => {
    const perm = await AudioModule.requestRecordingPermissionsAsync();
    if (!perm.granted) return 'denied';
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
    return 'ok';
  }, [recorder]);
  const stop = useCallback(async (): Promise<{ file: PendingFile; seconds: number } | null> => {
    const seconds = (recorder.getStatus().durationMillis ?? 0) / 1000;
    await recorder.stop();
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => {});
    const uri = recorder.uri;
    if (!uri) return null;
    if (Platform.OS === 'web') {  // 网页版录的是 blob: 地址（webm），上传要 File
      const blob = await (await fetch(uri)).blob();
      const ext = blob.type.includes('ogg') ? 'ogg' : blob.type.includes('mp4') ? 'm4a' : 'webm';
      const name = `take.${ext}`;
      return { file: { uri, name, mime: blob.type || `audio/${ext}`, size: blob.size, file: new File([blob], name, { type: blob.type || `audio/${ext}` }) }, seconds };
    }
    return { file: { uri, name: 'take.m4a', mime: 'audio/m4a', size: 0 }, seconds };
  }, [recorder]);
  return { start, stop, state };
}

/**
 * 播原声：play(key, 段, 从第几秒, 到第几秒?, 一直往后播?)。同一个 key 再点 = 停。
 * 一期的原声是好几段文件：all = 这段播完接着下一段（「播放这一期」）；to = 播到这一秒停（点一句听一句）。
 */
export function usePodPlayer(segments: PodSegment[]) {
  const player = useAudioPlayer(null, { updateInterval: 150 });
  const status = useAudioPlayerStatus(player);
  const [key, setKey] = useState<string | null>(null);
  const target = useRef<{ key: string; idx: number; to: number | null; all: boolean } | null>(null);
  const segs = useRef(segments);
  const loaded = useRef<string | null>(null);
  useEffect(() => { segs.current = segments; }, [segments]);

  const load = useCallback(async (idx: number, from: number) => {
    const seg = segs.current.find((s) => s.idx === idx);
    if (!seg) return false;
    if (loaded.current !== seg.url) { player.replace(audioSource(seg.url)); loaded.current = seg.url; }
    await player.seekTo(Math.max(0, from));
    player.play();
    return true;
  }, [player]);

  useEffect(() => {
    const sub = player.addListener('playbackStatusUpdate', (s) => {
      const tg = target.current;
      if (!tg) return;
      if (tg.to != null && s.currentTime >= tg.to) {
        player.pause();
        target.current = null;
        setKey(null);
      } else if (s.didJustFinish) {
        const next = tg.all ? segs.current.find((x) => x.idx > tg.idx) : undefined;
        if (next) {
          target.current = { ...tg, idx: next.idx };
          load(next.idx, 0).catch(() => {});
        } else {
          target.current = null;
          setKey(null);
        }
      }
    });
    return () => sub.remove();
  }, [player, load]);

  const stop = useCallback(() => {
    player.pause();
    target.current = null;
    setKey(null);
  }, [player]);

  const play = useCallback(async (k: string, idx: number, from = 0, to: number | null = null, all = false, list?: PodSegment[]) => {
    if (target.current?.key === k) { stop(); return; }
    if (list) segs.current = list;
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => {});
    target.current = { key: k, idx, to, all };
    setKey(k);
    const ok = await load(idx, from).catch(() => false);
    if (!ok) { target.current = null; setKey(null); }
  }, [load, stop]);

  useEffect(() => () => { try { player.pause(); } catch { /* 已经释放了 */ } }, [player]);
  return { playing: key, play, stop, time: status.currentTime, playingNow: status.playing };
}

/** 一期里的某一秒 → (第几段, 段里第几秒)。 */
export function locate(segments: PodSegment[], at: number): { idx: number; t: number } | null {
  for (const s of segments) {
    if (at < s.offset + s.duration + 0.01) return { idx: s.idx, t: Math.max(0, at - s.offset) };
  }
  const last = segments[segments.length - 1];
  return last ? { idx: last.idx, t: Math.max(0, at - last.offset) } : null;
}

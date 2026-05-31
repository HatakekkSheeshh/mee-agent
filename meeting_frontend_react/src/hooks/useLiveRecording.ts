// Live recording via WebSocket Whisper backend.
//
// Flow (mirrors meeting_frontend/app.js):
//   1. start() → getUserMedia → AudioContext + AudioWorklet (PCM @16kHz)
//   2. open WebSocket → onopen: send config JSON
//   3. server replies SERVER_READY → start streaming PCM bytes
//   4. server pushes {segments: [{start, text, completed}]} → caller's callback
//   5. stop() → send "END_OF_AUDIO" + close WS + tear down audio nodes
//
// Reconnect logic + mic device picker deferred to a later phase.
import { useCallback, useRef, useState } from "react";

export interface LiveSegment {
  start: string | number;
  end?: string | number;
  text: string;
  completed: boolean;
}

interface Options {
  /** Recording id used as the WS uid. */
  uid: string;
  /** Whisper language hint (vi/en). */
  language?: string;
  /** Initial prompt for code-switching hints + vocab. */
  initialPrompt?: string;
  /** Called every time the server pushes a segments update. */
  onSegments: (segs: LiveSegment[]) => void;
  /** Status text updates for UI banner. */
  onStatus?: (kind: "info" | "connecting" | "recording" | "error" | "idle", msg: string) => void;
}

export function useLiveRecording({
  uid,
  language = "vi",
  initialPrompt,
  onSegments,
  onStatus,
}: Options) {
  const [isRecording, setIsRecording] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const cleanup = useCallback(() => {
    workletRef.current?.disconnect();
    workletRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const startAudio = useCallback(async () => {
    const savedMic = localStorage.getItem("mee.audioInputDeviceId") || "";
    const audioConstraints: MediaTrackConstraints = {
      channelCount: 1,
      sampleRate: 16000,
      echoCancellation: true,
      noiseSuppression: true,
    };
    if (savedMic) (audioConstraints as { deviceId?: ConstrainDOMString }).deviceId = { ideal: savedMic };
    const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
    streamRef.current = stream;

    const ctx = new AudioContext();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaStreamSource(stream);
    await ctx.audioWorklet.addModule("/audioprocessor.js");
    const worklet = new AudioWorkletNode(ctx, "audio-processor");
    workletRef.current = worklet;
    worklet.port.onmessage = (e) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send((e.data as Int16Array).buffer);
      }
    };
    source.connect(worklet);
    worklet.connect(ctx.destination);
  }, []);

  const start = useCallback(async () => {
    if (isRecording) return;
    onStatus?.("connecting", "Đang kết nối đến STT server…");
    // Vite proxies /ws → ws://localhost:9091. Build a proper ws:// URL from
    // current location so this works in dev (localhost) AND when the React
    // app is served from the same origin as the backend (production).
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          uid,
          language,
          task: "transcribe",
          model: "large-v3",
          use_vad: true,
          initial_prompt: initialPrompt || "",
        }),
      );
      onStatus?.("connecting", "Đang chờ server…");
    };
    ws.onmessage = (e) => {
      let msg: { message?: string; status?: string; segments?: LiveSegment[] };
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.message === "SERVER_READY") {
        onStatus?.("recording", "Đang ghi âm…");
        setIsRecording(true);
        startAudio().catch((err) => {
          onStatus?.("error", `Lỗi mic: ${err.message}`);
          stop();
        });
        return;
      }
      if (msg.message === "DISCONNECT") { stop(); return; }
      if (msg.status === "WAIT") {
        onStatus?.("connecting", `Server bận. Chờ ~${Math.ceil(Number(msg.message))} phút.`);
        return;
      }
      if (msg.segments) onSegments(msg.segments);
    };
    ws.onerror = () => onStatus?.("error", "Lỗi kết nối WebSocket");
    ws.onclose = () => {
      // Don't auto-reconnect here — caller's stop() drives this.
      if (isRecording) onStatus?.("idle", "Mất kết nối");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid, language, initialPrompt, onSegments, onStatus, isRecording, startAudio]);

  const stop = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(new TextEncoder().encode("END_OF_AUDIO")); } catch { /* ignore */ }
      ws.close();
    }
    wsRef.current = null;
    cleanup();
    setIsRecording(false);
    onStatus?.("idle", "Đã dừng ghi âm");
  }, [cleanup, onStatus]);

  return { start, stop, isRecording };
}

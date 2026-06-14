import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type EnrollUser = {
  email: string;
  display_name: string | null;
};

type State = "idle" | "recording" | "preview" | "uploading" | "error";

/**
 * Voice enrollment — post-login, one-time per user.
 *
 * Reads a fixed slogan in VI then EN, captures ~15-30s of audio via
 * MediaRecorder, POSTs to /api/voiceprints/enroll. The recorded sample
 * becomes the user's ground-truth voiceprint for speaker matching in
 * future meetings.
 *
 * Visuals: concentric pulsing circles act as the "voice metaphor".
 * During recording, an AnalyserNode reads live audio level and feeds it
 * into a CSS variable so the circles scale in real time → user gets
 * immediate feedback that the mic is hearing them.
 */
export function VoiceEnrollment({ user, onEnrolled }: {
  user: EnrollUser;
  onEnrolled: () => void;
}) {
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [level, setLevel] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const tickRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);

  const firstName = (user.display_name || user.email.split("@")[0]).split(" ")[0];

  // Reveal animation hook — fade-in matches landing page reveal-on-scroll.
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>(".enroll .reveal-on-scroll");
    requestAnimationFrame(() => {
      els.forEach((el, i) => {
        setTimeout(() => el.classList.add("is-visible"), i * 80);
      });
    });
  }, []);

  // Cleanup on unmount — release mic + audio context + revoke object URLs.
  useEffect(() => {
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startRecord() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // AnalyserNode → live audio level for the visualizer. Tap the stream
      // BEFORE handing it to MediaRecorder so we don't fight for ownership.
      const AC: typeof AudioContext =
        (window as unknown as { AudioContext: typeof AudioContext; webkitAudioContext: typeof AudioContext }).AudioContext
        || (window as unknown as { AudioContext: typeof AudioContext; webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AC();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.7;
      source.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;

      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        // Apply a curve so quiet speech still produces visible motion
        // without ambient noise spamming the meter at idle.
        const norm = Math.min(1, Math.pow(rms * 3, 0.7));
        setLevel(norm);
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        setAudioUrl(URL.createObjectURL(blob));
        setState("preview");
        setLevel(0);
        // Hand the mic back so the OS indicator light goes off.
        stream.getTracks().forEach((t) => t.stop());
        audioCtxRef.current?.close().catch(() => {});
        audioCtxRef.current = null;
        analyserRef.current = null;
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
      };
      recorder.start();
      recorderRef.current = recorder;

      startedAtRef.current = Date.now();
      setDuration(0);
      tickRef.current = window.setInterval(() => {
        setDuration(Math.floor((Date.now() - startedAtRef.current) / 1000));
      }, 200);
      setState("recording");
    } catch {
      setError("Không truy cập được microphone. Vui lòng cho phép quyền mic trong trình duyệt.");
      setState("error");
    }
  }

  function stopRecord() {
    recorderRef.current?.stop();
    if (tickRef.current) clearInterval(tickRef.current);
  }

  function retake() {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    setState("idle");
    setDuration(0);
    setLevel(0);
  }

  async function submit() {
    if (!audioUrl) return;
    setState("uploading");
    setError(null);
    try {
      const blob = await (await fetch(audioUrl)).blob();
      const fd = new FormData();
      fd.append("audio", blob, "enrollment.webm");
      const r = await fetch("/api/voiceprints/enroll", { method: "POST", body: fd });
      if (!r.ok) {
        const j = await r.json().catch(() => ({} as { detail?: string }));
        setError(j.detail || `Server returned ${r.status}`);
        setState("error");
        return;
      }
      onEnrolled();
    } catch (e) {
      setError(String(e));
      setState("error");
    }
  }

  const isShortAudio = duration < 8;
  const isOptimalAudio = duration >= 12 && duration <= 40;

  return (
    <div className="enroll" style={{ "--mic-level": level } as React.CSSProperties}>
      <AmbientBackground />

      <button
        className="enroll__signout"
        type="button"
        onClick={async () => {
          await api.auth.logout().catch(() => {});
          window.location.href = "/";
        }}
      >
        ← Sign out
      </button>

      <div className="enroll__shell">
        <div className="enroll__progress reveal-on-scroll">
          <span className="enroll__progress-dot enroll__progress-dot--done" />
          <span className="enroll__progress-line" />
          <span className="enroll__progress-dot enroll__progress-dot--active" />
          <span className="enroll__progress-label">Step 2 / 2 · Voice enrollment</span>
        </div>

        <h1 className="enroll__title reveal-on-scroll">
          Teach Mee <em>your voice.</em>
        </h1>
        <p className="enroll__sub reveal-on-scroll">
          Hi <strong>{firstName}</strong> — read the lines below aloud, first in
          Vietnamese then in English. We capture it <em>once</em> and use it to
          identify you in every meeting from now on.
        </p>

        <div className="enroll__slogan-card reveal-on-scroll">
          <span className="enroll__quote enroll__quote--open">"</span>
          <div className="enroll__slogan-line">
            <span className="enroll__slogan-lang">VI</span>
            <span className="enroll__slogan-text">
              AI Cloud hiệu năng cao dành riêng cho doanh nghiệp số.
            </span>
          </div>
          <div className="enroll__slogan-line">
            <span className="enroll__slogan-lang">EN</span>
            <span className="enroll__slogan-text">
              High performance AI Cloud for digital-native businesses.
            </span>
          </div>
          <span className="enroll__quote enroll__quote--close">"</span>
        </div>

        <div className={`enroll__viz enroll__viz--${state}`} aria-hidden="true">
          {[0, 1, 2, 3, 4].map((i) => (
            <span
              key={i}
              className="enroll__ring"
              style={{ animationDelay: `${i * 0.4}s` }}
            />
          ))}
          <span className="enroll__core" />
          {state === "recording" && (
            <div className="enroll__bars">
              {[0, 1, 2, 3, 4, 5, 6].map((i) => (
                <span
                  key={i}
                  className="enroll__bar"
                  style={{
                    animationDelay: `${i * 0.07}s`,
                  }}
                />
              ))}
            </div>
          )}
        </div>

        <div className="enroll__controls reveal-on-scroll">
          {state === "idle" || state === "error" ? (
            <>
              <button className="enroll__btn enroll__btn--record" onClick={startRecord}>
                <span className="enroll__btn-dot" /> Start recording
              </button>
              <div className="enroll__hint">~15-30 seconds · click again to stop</div>
            </>
          ) : state === "recording" ? (
            <>
              <div className="enroll__timer">
                <span className="enroll__timer-text">
                  {String(Math.floor(duration / 60)).padStart(1, "0")}:
                  {String(duration % 60).padStart(2, "0")}
                </span>
                <span className="enroll__timer-sub">
                  {isShortAudio ? "Keep going — at least 8s" :
                    isOptimalAudio ? "Great length. Stop when you finish reading." :
                      "Enough recorded. You can stop now."}
                </span>
              </div>
              <button className="enroll__btn enroll__btn--stop" onClick={stopRecord}>
                ■ Stop recording
              </button>
            </>
          ) : state === "preview" && audioUrl ? (
            <>
              <AudioPlayer src={audioUrl} />
              <div className="enroll__hint">
                {duration}s recorded · {isShortAudio
                  ? "⚠ A bit short — re-record at least 8s for a robust voiceprint"
                  : "Length looks good"}
              </div>
              <div className="enroll__actions">
                <button className="enroll__btn enroll__btn--ghost" onClick={retake}>
                  Re-record
                </button>
                <button
                  className="enroll__btn enroll__btn--primary"
                  onClick={submit}
                  disabled={isShortAudio}
                >
                  Confirm &amp; continue <span className="enroll__arrow">→</span>
                </button>
              </div>
            </>
          ) : state === "uploading" ? (
            <div className="enroll__uploading">
              <span className="enroll__spinner" />
              Saving your voiceprint…
            </div>
          ) : null}
        </div>

        {error && <div className="enroll__error reveal-on-scroll">{error}</div>}
      </div>
    </div>
  );
}

/** Reuse the same ambient background pattern from the landing page so the
 * onboarding experience feels cohesive with the marketing surface. */
function AmbientBackground() {
  return (
    <div className="lp-bg" aria-hidden="true">
      <div className="lp-bg__noise" />
      <div className="lp-bg__grid" />
      <div className="lp-bg__halo lp-bg__halo--a" />
      <div className="lp-bg__halo lp-bg__halo--b" />
    </div>
  );
}

/** Custom audio player — the native <audio> element is invisible on dark
 * backgrounds in most browsers. This rebuilds the bare-minimum surface
 * (play/pause, progress bar, time) with full theme control.
 */
function AudioPlayer({ src }: { src: string }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [total, setTotal] = useState(0);

  // Load duration when metadata arrives. WebM blobs from MediaRecorder
  // sometimes report Infinity for duration until the file is fully seekable;
  // we work around that by seeking to a far point then back.
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onLoaded = () => {
      if (a.duration === Infinity || isNaN(a.duration)) {
        a.currentTime = 1e9;
        // give the browser a tick to recompute, then snap back
        setTimeout(() => { a.currentTime = 0; }, 0);
      } else {
        setTotal(a.duration);
      }
    };
    const onTime = () => setCurrent(a.currentTime);
    const onDuration = () => {
      if (Number.isFinite(a.duration)) setTotal(a.duration);
    };
    const onEnded = () => { setPlaying(false); setCurrent(0); };
    a.addEventListener("loadedmetadata", onLoaded);
    a.addEventListener("durationchange", onDuration);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("ended", onEnded);
    return () => {
      a.removeEventListener("loadedmetadata", onLoaded);
      a.removeEventListener("durationchange", onDuration);
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("ended", onEnded);
    };
  }, [src]);

  const toggle = async () => {
    const a = audioRef.current;
    if (!a) return;
    if (playing) {
      a.pause();
      setPlaying(false);
    } else {
      await a.play();
      setPlaying(true);
    }
  };

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = audioRef.current;
    if (!a || !Number.isFinite(total) || total === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    a.currentTime = ratio * total;
    setCurrent(a.currentTime);
  };

  const fmt = (s: number) => {
    if (!Number.isFinite(s)) return "0:00";
    const m = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `${m}:${String(ss).padStart(2, "0")}`;
  };

  const progress = total > 0 ? (current / total) * 100 : 0;

  return (
    <div className="enroll__player">
      <audio ref={audioRef} src={src} preload="metadata" />
      <button
        className={`enroll__player-btn ${playing ? "is-playing" : ""}`}
        onClick={toggle}
        type="button"
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? (
          <span className="enroll__player-pause">
            <span /><span />
          </span>
        ) : (
          <span className="enroll__player-play" />
        )}
      </button>
      <div className="enroll__player-progress" onClick={seek}>
        <div className="enroll__player-progress-fill" style={{ width: `${progress}%` }} />
        <div className="enroll__player-progress-thumb" style={{ left: `${progress}%` }} />
      </div>
      <div className="enroll__player-time">
        {fmt(current)} / {fmt(total)}
      </div>
    </div>
  );
}

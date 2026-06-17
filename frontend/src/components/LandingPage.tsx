import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/**
 * Dark-theme single-page landing, GreenNode-inspired.
 *
 * Design decisions:
 *  - Single page, sections anchored to nav links. Smooth scroll handled in JS
 *    so we get programmatic offset (sticky header) and easing.
 *  - Sticky background gradient that drifts hue based on scroll position →
 *    each section feels like its own "mood" without a hard cut.
 *  - Scroll-triggered counters in the stats bar, reveal-on-view for cards.
 *  - No icons anywhere — numerals, geometric shapes, monospace tags only.
 */
export function LandingPage() {
  const handleSignIn = () => {
    window.location.href = api.auth.loginUrl("/");
  };

  // Smooth scroll to section with sticky-nav offset (~72px).
  const scrollTo = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - 72;
    window.scrollTo({ top, behavior: "smooth" });
  };

  const scrollToTop = (e: React.MouseEvent) => {
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // Reveal sections on scroll — adds .is-visible when ≥ 18% in viewport.
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>(".reveal-on-scroll");
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.18, rootMargin: "0px 0px -8% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  return (
    <div className="lp lp--dark">
      <ScrollProgress />
      <AmbientBackground />

      <header className="lp-nav">
        <div className="lp-nav__inner">
          <a className="lp-brand" href="#top" onClick={scrollToTop}>
            <span className="lp-brand__mark" aria-hidden="true" />
            <span className="lp-brand__name">Mee</span>
            <span className="lp-brand__sep">/</span>
            <span className="lp-brand__tag">Meeting Agent</span>
          </a>
          <nav className="lp-nav__links">
            <a href="#features" onClick={scrollTo("features")}>Features</a>
            <a href="#workflow" onClick={scrollTo("workflow")}>How it works</a>
            <a href="#use-cases" onClick={scrollTo("use-cases")}>Use cases</a>
          </nav>
          <button className="lp-nav__cta" onClick={handleSignIn}>
            Sign in <span className="lp-arrow" aria-hidden="true">→</span>
          </button>
        </div>
      </header>

      <main>
        {/* ─── HERO ─────────────────────────────────────────────── */}
        <section className="lp-hero" id="top">
          <div className="lp-hero__inner">
            <div className="lp-hero__eyebrow reveal-on-scroll">
              <span className="lp-pulse" /> Built on GreenNode AI Cloud
            </div>
            <h1 className="lp-hero__title reveal-on-scroll">
              The meeting<br />
              becomes a <em>document</em>
            </h1>
            <p className="lp-hero__sub reveal-on-scroll">
              Mee transcribes, attributes and summarises meetings in Vietnamese
              and English. Voice enrollment once — speaker identity for every
              meeting after. Designed for engineering teams.
            </p>
            <div className="lp-hero__cta reveal-on-scroll">
              <button className="lp-btn lp-btn--primary" onClick={handleSignIn}>
                Sign in with Microsoft
                <span className="lp-arrow" aria-hidden="true">→</span>
              </button>
              <a className="lp-btn lp-btn--ghost" href="#workflow" onClick={scrollTo("workflow")}>
                See how it works
              </a>
            </div>
            <div className="lp-hero__hint reveal-on-scroll">
              <kbd>O365</kbd> account required · no credit card
            </div>
          </div>

          <div className="lp-hero__visual" aria-hidden="true">
            <HeroVisual />
          </div>

          <div className="lp-scroll-cue" aria-hidden="true">
            <span className="lp-scroll-cue__line" />
            <span className="lp-scroll-cue__label">scroll</span>
          </div>
        </section>

        {/* ─── STATS BAR ────────────────────────────────────────── */}
        <section className="lp-stats">
          <div className="lp-stats__inner reveal-on-scroll">
            <Stat value={3.4} suffix="×" label="faster MoM gen vs manual" />
            <div className="lp-stats__divider" />
            <Stat value={92} suffix="%" label="speaker attribution accuracy" />
            <div className="lp-stats__divider" />
            <Stat raw="VN+EN" label="code-switching native" />
            <div className="lp-stats__divider" />
            <Stat raw="HCM" label="data stays in Vietnam" />
          </div>
        </section>

        <SectionDivider />

        {/* ─── FEATURES ─────────────────────────────────────────── */}
        <section className="lp-features" id="features">
          <div className="lp-features__inner">
            <div className="lp-section-head reveal-on-scroll">
              <span className="lp-section-tag">— Capabilities</span>
              <h2>Engineered for the meeting that matters.</h2>
            </div>

            <div className="lp-features__grid">
              <FeatureCard
                num="01"
                title="Hear every word"
                body="Captures Vietnamese and English meetings, even when speakers switch languages mid-sentence. Technical jargon and product names learned over time."
                tag="Transcribe"
              />
              <FeatureCard
                num="02"
                title="Know who said what"
                body="Set up your voice once. Your name appears next to your words in every meeting from then on — no more manual labelling."
                tag="Identify"
              />
              <FeatureCard
                num="03"
                title="Auto-generated minutes"
                body="Decisions, action items, blockers and commitments — pulled out and structured for you. Project-wide timeline across every session."
                tag="Summarise"
              />
              <FeatureCard
                num="04"
                title="Ask anything later"
                body="Chat with your meeting history. 'What did we decide about pricing last sprint?' — instant answer with the original quote."
                tag="Search"
              />
              <FeatureCard
                num="05"
                title="No waiting"
                body="Upload your file and walk away. Long recordings process in the background; you'll get notified when the minutes are ready."
                tag="Fast"
              />
              <FeatureCard
                num="06"
                title="Stays in Vietnam"
                body="Built on GreenNode infrastructure in the Ho Chi Minh region. Your team's audio never leaves the country."
                tag="Secure"
              />
            </div>
          </div>
        </section>

        <SectionDivider />

        {/* ─── WORKFLOW ─────────────────────────────────────────── */}
        <section className="lp-workflow" id="workflow">
          <div className="lp-workflow__inner">
            <div className="lp-section-head reveal-on-scroll">
              <span className="lp-section-tag">— Workflow</span>
              <h2>From audio to action items in four steps.</h2>
            </div>

            <ol className="lp-steps">
              <Step
                num="01"
                title="Sign in with O365"
                body="Single click via Microsoft. The first time only, you'll be asked to record yourself reading the GreenNode slogan — your voice becomes the ground truth."
              />
              <Step
                num="02"
                title="Drop in audio"
                body="Upload an MP3/WAV/M4A — or record live from the browser. Long files chunk automatically through parallel transcription."
              />
              <Step
                num="03"
                title="System attributes + cleans"
                body="Pyannote separates speakers, voiceprint matching attaches their real names, the cleaner LLM polishes the transcript."
              />
              <Step
                num="04"
                title="Read the MoM"
                body="Structured minutes with decisions, action items, blockers. Edit in place; the agent learns vocabulary from your corrections."
              />
              <Step
                num="05"
                title="Ask the Chat Agent"
                body="Your meeting history is a searchable knowledge base. Ask questions in natural language and the agent retrieves relevant sections and quotes to answer."
              />
            </ol>
          </div>
        </section>

        <SectionDivider />

        {/* ─── USE CASES ────────────────────────────────────────── */}
        <section className="lp-cases" id="use-cases">
          <div className="lp-cases__inner">
            <div className="lp-section-head reveal-on-scroll">
              <span className="lp-section-tag">— Use cases</span>
              <h2>Made for the meetings you actually have.</h2>
            </div>

            <div className="lp-cases__grid">
              <UseCase
                title="Engineering syncs"
                body="Daily standups, sprint reviews, design reviews. Tech terms like 'deploy', 'API', 'rollback' stay accurate even when discussed in Vietnamese."
              />
              <UseCase
                title="Product reviews"
                body="Product managers walk away with decisions and action items captured automatically. No more 'who agreed to what?' a week later."
              />
              <UseCase
                title="Customer interviews"
                body="UX research sessions transcribed with speaker labels. Search past interviews by topic to find every mention of a feature request."
              />
              <UseCase
                title="Hiring panels"
                body="Every interviewer's questions and observations captured. Compare candidates back-to-back with structured summaries instead of scribbled notes."
              />
              <UseCase
                title="All-hands recap"
                body="Couldn't attend? Read the minutes in 2 minutes. Or chat with Mee: 'What was the Q3 update about?' — get the section instantly."
              />
              <UseCase
                title="1-on-1 reviews"
                body="Managers get the decisions and follow-ups; reports get the receipts. Everyone keeps their context, even months later."
              />
            </div>
          </div>
        </section>

        {/* ─── FINAL CTA ────────────────────────────────────────── */}
        <section className="lp-final">
          <div className="lp-final__inner reveal-on-scroll">
            <h2>Stop taking notes.<br />Start making decisions.</h2>
            <button className="lp-btn lp-btn--primary lp-btn--lg" onClick={handleSignIn}>
              Sign in with Microsoft
              <span className="lp-arrow" aria-hidden="true">→</span>
            </button>
            <div className="lp-final__sub">
              VNG Claw-a-thon · 2026
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-footer__inner">
          <div className="lp-footer__brand">
            <span className="lp-brand__mark" aria-hidden="true" />
            <span>Mee Meeting Agent</span>
          </div>
          <div className="lp-footer__links">
            <a href="https://greennode.ai" target="_blank" rel="noreferrer">greennode.ai</a>
            <a href="#features" onClick={scrollTo("features")}>Features</a>
            <a href="#workflow" onClick={scrollTo("workflow")}>How it works</a>
            <a href="#use-cases" onClick={scrollTo("use-cases")}>Use cases</a>
          </div>
          <div className="lp-footer__legal">
            © 2026 · Built at GreenNode
          </div>
        </div>
      </footer>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────

function FeatureCard({ num, title, body, tag }: { num: string; title: string; body: string; tag: string }) {
  return (
    <article className="lp-card reveal-on-scroll">
      <div className="lp-card__head">
        <span className="lp-card__num">{num}</span>
        <span className="lp-card__tag">{tag}</span>
      </div>
      <h3 className="lp-card__title">{title}</h3>
      <p className="lp-card__body">{body}</p>
    </article>
  );
}

function Step({ num, title, body }: { num: string; title: string; body: string }) {
  return (
    <li className="lp-step reveal-on-scroll">
      <div className="lp-step__num">{num}</div>
      <div className="lp-step__content">
        <h3>{title}</h3>
        <p>{body}</p>
      </div>
    </li>
  );
}

/**
 * Stat — supports two render modes:
 *   <Stat value={92} suffix="%" />  → counter animates 0 → 92 on scroll in
 *   <Stat raw="VN+EN" />             → render as static text
 */
function Stat({ value, raw, prefix, suffix, label }: {
  value?: number; raw?: string; prefix?: string; suffix?: string; label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [displayed, setDisplayed] = useState(0);
  useEffect(() => {
    if (value === undefined || !ref.current) return;
    const el = ref.current;
    let raf = 0;
    let started = false;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting && !started) {
            started = true;
            const start = performance.now();
            const dur = 1200;
            const animate = (now: number) => {
              const t = Math.min(1, (now - start) / dur);
              // easeOutCubic
              const eased = 1 - Math.pow(1 - t, 3);
              setDisplayed(value * eased);
              if (t < 1) raf = requestAnimationFrame(animate);
            };
            raf = requestAnimationFrame(animate);
          }
        }
      },
      { threshold: 0.5 },
    );
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [value]);

  let rendered: string;
  if (raw !== undefined) {
    rendered = raw;
  } else if (value !== undefined) {
    // Show 1 decimal if the target has one, else integer (3.4 → 3.4, 92 → 92).
    const hasDecimal = !Number.isInteger(value);
    rendered = hasDecimal ? displayed.toFixed(1) : Math.round(displayed).toString();
  } else {
    rendered = "";
  }

  return (
    <div className="lp-stat" ref={ref}>
      <div className="lp-stat__value">
        {prefix}{rendered}{suffix}
      </div>
      <div className="lp-stat__label">{label}</div>
    </div>
  );
}

function UseCase({ title, body }: { title: string; body: string }) {
  return (
    <article className="lp-case reveal-on-scroll">
      <h3>{title}</h3>
      <p>{body}</p>
    </article>
  );
}

function ScrollProgress() {
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    const onScroll = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      setProgress(max > 0 ? (h.scrollTop / max) * 100 : 0);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return <div className="lp-scroll-progress" style={{ width: `${progress}%` }} />;
}

/**
 * AmbientBackground — sticky gradient mesh that drifts hue as user scrolls.
 * Two layered radial gradients translate with scroll position to produce a
 * subtle parallax mood-shift between sections (green → teal → indigo → green).
 */
function AmbientBackground() {
  const [scrollY, setScrollY] = useState(0);
  useEffect(() => {
    const onScroll = () => setScrollY(window.scrollY);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Map scroll position to hue shift (0-360deg). Slow drift, never goes wild.
  const hueA = (scrollY * 0.03) % 360;
  const hueB = (180 + scrollY * 0.04) % 360;
  const driftA = scrollY * 0.15;
  const driftB = scrollY * -0.10;

  return (
    <div className="lp-bg" aria-hidden="true">
      <div className="lp-bg__noise" />
      <div className="lp-bg__grid" />
      <div
        className="lp-bg__halo lp-bg__halo--a"
        style={{
          transform: `translate(0, ${driftA}px)`,
          filter: `blur(120px) hue-rotate(${hueA}deg)`,
        }}
      />
      <div
        className="lp-bg__halo lp-bg__halo--b"
        style={{
          transform: `translate(0, ${driftB}px)`,
          filter: `blur(140px) hue-rotate(${hueB}deg)`,
        }}
      />
    </div>
  );
}

/** Diagonal gradient line between sections — subtle visual punctuation. */
function SectionDivider() {
  return (
    <div className="lp-divider" aria-hidden="true">
      <div className="lp-divider__line" />
    </div>
  );
}

function HeroVisual() {
  return (
    <div className="lp-visual">
      <div className="lp-visual__chrome">
        <span className="lp-visual__dot" />
        <span className="lp-visual__dot" />
        <span className="lp-visual__dot" />
        <span className="lp-visual__path">meeting-2026-06-11.mom</span>
      </div>
      <div className="lp-visual__body">
        <div className="lp-visual__row lp-visual__row--out">
          <span className="lp-visual__spk">Alice</span>
          <span className="lp-visual__txt">The cleaner just finished — MoM is ready in the database.</span>
        </div>
        <div className="lp-visual__row lp-visual__row--out lp-visual__row--delay-1">
          <span className="lp-visual__spk">Bob</span>
          <span className="lp-visual__txt">Great. I'll push the image to AgentBase tonight so we can demo tomorrow.</span>
        </div>
        <div className="lp-visual__row lp-visual__row--in lp-visual__row--delay-2">
          <span className="lp-visual__label">Decision</span>
          <span className="lp-visual__txt">Deploy pyannote container to AgentBase Agent Runtime (CPU 4×8).</span>
        </div>
        <div className="lp-visual__row lp-visual__row--in lp-visual__row--delay-3">
          <span className="lp-visual__label">Action</span>
          <span className="lp-visual__txt">@Bob · prepare demo deck for the mentor — due tomorrow.</span>
        </div>
      </div>
    </div>
  );
}

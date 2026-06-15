import { useEffect, useState } from "react";
import { useApp } from "../store/AppContext";
import { api } from "../api/client";

/**
 * Intro banner shown at the top of an empty chat thread — introduces Mee's
 * purpose and capabilities (Q&A, Redmine work via pm-agent, side-effecting
 * actions with HITL) instead of a single plain welcome line.
 *
 * It also self-probes /api/redmine/status and, when the user's per-user Redmine
 * key is missing, renders a warning at the bottom with a button that opens the
 * AgentBase consent gate (returning to the app re-probes via the focus listener).
 */
const FEATURES = [
  { icon: "💬", titleKey: "agent.feat1Title", descKey: "agent.feat1Desc" },
  { icon: "📋", titleKey: "agent.feat2Title", descKey: "agent.feat2Desc" },
  { icon: "📧", titleKey: "agent.feat3Title", descKey: "agent.feat3Desc" },
] as const;

type RedmineStatus = Awaited<ReturnType<typeof api.redmine.status>>;

export function WelcomeBanner() {
  const { t } = useApp();
  const [redmine, setRedmine] = useState<RedmineStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const s = await api.redmine.status();
        if (alive) setRedmine(s);
      } catch {
        if (alive) setRedmine(null);
      }
    };
    probe();
    const onFocus = () => probe();
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const keyMissing = redmine != null && !redmine.redmine_key_present;

  return (
    <div className="welcome-banner">
      <div className="welcome-banner-head">
        <span className="welcome-banner-title">{t("agent.bannerTitle")}</span>
      </div>
      <p className="welcome-banner-tagline">{t("agent.bannerTagline")}</p>
      <ul className="welcome-banner-feats">
        {FEATURES.map((f) => (
          <li key={f.titleKey} className="welcome-feat">
            <span className="welcome-feat-icon" aria-hidden="true">
              {f.icon}
            </span>
            <span className="welcome-feat-text">
              <span className="welcome-feat-title">{t(f.titleKey)}</span>
              <span className="welcome-feat-desc">{t(f.descKey)}</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="welcome-banner-note">{t("agent.bannerNote")}</div>

      {keyMissing && (
        <div className="welcome-banner-warning" role="alert">
          <span className="welcome-banner-warning-label">
            <span aria-hidden="true">⚠</span> {t("redmine.warningLabel")}
          </span>
          <span className="welcome-banner-warning-text">
            {t("redmine.bannerKeyMissing")}
          </span>
          {redmine?.gate_url && (
            <button
              type="button"
              className="welcome-banner-warning-action"
              onClick={() => {
                if (redmine.gate_url) window.location.href = redmine.gate_url;
              }}
            >
              {t("redmine.enterKey")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

import { useApp } from "../store/AppContext";

/**
 * Intro banner shown at the top of an empty chat thread — introduces Mee's
 * purpose and capabilities (Q&A, Redmine work via pm-agent, side-effecting
 * actions with HITL) instead of a single plain welcome line.
 */
const FEATURES = [
  { icon: "💬", titleKey: "agent.feat1Title", descKey: "agent.feat1Desc" },
  { icon: "📋", titleKey: "agent.feat2Title", descKey: "agent.feat2Desc" },
  { icon: "📧", titleKey: "agent.feat3Title", descKey: "agent.feat3Desc" },
] as const;

export function WelcomeBanner() {
  const { t } = useApp();
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
    </div>
  );
}

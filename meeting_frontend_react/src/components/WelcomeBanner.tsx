import { useApp } from "../store/AppContext";

/**
 * Intro banner shown at the top of an empty chat thread — introduces Mee's
 * purpose and capabilities (Q&A, Redmine work via pm-agent, side-effecting
 * actions with HITL) instead of a single plain welcome line.
 */
export function WelcomeBanner() {
  const { t } = useApp();
  return (
    <div className="welcome-banner">
      <div className="welcome-banner-head">
        <span className="agent-dot" />
        <span className="welcome-banner-title">{t("agent.bannerTitle")}</span>
      </div>
      <p className="welcome-banner-tagline">{t("agent.bannerTagline")}</p>
      <ul className="welcome-banner-feats">
        <li>{t("agent.feat1")}</li>
        <li>{t("agent.feat2")}</li>
        <li>{t("agent.feat3")}</li>
      </ul>
      <div className="welcome-banner-note">{t("agent.bannerNote")}</div>
    </div>
  );
}

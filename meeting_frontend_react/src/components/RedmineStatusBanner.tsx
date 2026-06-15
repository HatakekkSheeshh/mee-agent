import { useApp } from "../store/AppContext";

export interface RedmineStatus {
  redmine_key_present: boolean;
  redmine_tools_ok: boolean;
  pm_agent_ok: boolean;
  gate_url: string | null;
  all_ok: boolean;
}

interface RedmineStatusBannerProps {
  status: RedmineStatus;
}

/**
 * Red warning banner shown when the user's Redmine key is missing or a service
 * (Redmine tools / pm-agent) is unreachable. A missing key offers a button that
 * redirects to the AgentBase consent gate; on return the app re-probes status.
 */
export function RedmineStatusBanner({ status }: RedmineStatusBannerProps) {
  const { t } = useApp();
  if (status.all_ok) return null;

  const messages: string[] = [];
  if (!status.redmine_key_present) messages.push(t("redmine.bannerKeyMissing"));
  else if (!status.redmine_tools_ok) messages.push(t("redmine.bannerToolsDown"));
  if (!status.pm_agent_ok) messages.push(t("redmine.bannerPmDown"));

  const openGate = () => {
    if (status.gate_url) window.location.href = status.gate_url;
  };

  return (
    <div className="redmine-banner" role="alert" style={{ color: "#c0271e" }}>
      <span className="redmine-banner-text">{messages.join(" ")}</span>
      {!status.redmine_key_present && status.gate_url && (
        <button type="button" className="redmine-banner-action" onClick={openGate}>
          {t("redmine.enterKey")}
        </button>
      )}
    </div>
  );
}

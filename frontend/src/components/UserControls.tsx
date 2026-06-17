// Settings + User dropdown cluster — previously lived in Topbar's
// right side. Now mounted at the bottom of Sidebar so the top bar can
// stay focused on the current meeting. Self-contained: owns its own
// dropdown state, voiceprints modal, and logout confirm dialog.
import { useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { Dropdown, useDropdown } from "./Dropdown";
import { VoiceprintsModal } from "./VoiceprintsModal";
import { ConfirmDialog } from "./ConfirmDialog";
import { api } from "../api/client";

interface Props {
  user: { email: string; display_name: string | null };
}

export function UserControls({ user }: Props) {
  const {
    theme,
    setTheme,
    lang,
    setLang,
    t,
    audioInputDeviceId,
    setAudioInputDeviceId,
    audioOutputDeviceId,
    setAudioOutputDeviceId,
    audioDevices,
    refreshAudioDevices,
  } = useApp();
  const [voiceprintsOpen, setVoiceprintsOpen] = useState(false);
  const [logoutOpen, setLogoutOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  async function doLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await api.auth.logout();
    } catch {
      /* ignore */
    }
    localStorage.clear();
    location.href = "/";
  }

  const displayName = user.display_name?.trim() || user.email.split("@")[0];
  const avatarInitial = (displayName || user.email).trim().charAt(0).toUpperCase() || "U";

  const settingsRef = useRef<HTMLButtonElement>(null);
  const avatarRef = useRef<HTMLButtonElement>(null);
  // Sidebar footer sits at the bottom — opening dropdowns downward
  // would push the panel off-screen. Force placement="top" so both
  // panels float above the trigger row.
  const settings = useDropdown(settingsRef, { placement: "top" });
  const avatar = useDropdown(avatarRef, { placement: "top" });

  return (
    <div className="user-controls">
      <button ref={avatarRef} className="user-controls-avatar" type="button" onClick={avatar.toggle}>
        <span className="avatar-img">{avatarInitial}</span>
        <span className="user-controls-name">{displayName}</span>
      </button>
      <button
        ref={settingsRef}
        className="user-controls-btn"
        type="button"
        title="Settings"
        onClick={settings.toggle}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>

      <Dropdown open={settings.open} pos={settings.pos}>
        <div className="dd-label">{t("menu.appearance")}</div>
        <div className="dd-row">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
          <span>{t("menu.theme")}</span>
          <div className="seg-toggle">
            <button
              className={`seg-opt${theme === "light" ? " active" : ""}`}
              type="button"
              onClick={() => setTheme("light")}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
              </svg>
              <span>{t("menu.themeLight")}</span>
            </button>
            <button
              className={`seg-opt${theme === "dark" ? " active" : ""}`}
              type="button"
              onClick={() => setTheme("dark")}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
              <span>{t("menu.themeDark")}</span>
            </button>
          </div>
        </div>
        <div className="dd-row">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M2 12h20" />
            <path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z" />
          </svg>
          <span>{t("menu.language")}</span>
          <div className="seg-toggle">
            <button
              className={`seg-opt${lang === "vi" ? " active" : ""}`}
              type="button"
              onClick={() => setLang("vi")}
            >
              <span>VI</span>
            </button>
            <button
              className={`seg-opt${lang === "en" ? " active" : ""}`}
              type="button"
              onClick={() => setLang("en")}
            >
              <span>EN</span>
            </button>
          </div>
        </div>
        <div className="dd-divider"></div>
        <div className="dd-label">{t("settings.audioDevices")}</div>
        <div className="dd-row dd-row-stack">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
          <span className="dd-row-label">{t("settings.micDevice")}</span>
          <select
            className="dd-device-select"
            value={audioInputDeviceId || ""}
            onChange={(e) => setAudioInputDeviceId(e.target.value || null)}
            onFocus={() => { void refreshAudioDevices(); }}
          >
            <option value="">{t("settings.systemDefault")}</option>
            {audioDevices.inputs.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `${t("settings.deviceUnnamed")} ${i + 1}`}
              </option>
            ))}
          </select>
        </div>
        <div className="dd-row dd-row-stack">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
          </svg>
          <span className="dd-row-label">{t("settings.speakerDevice")}</span>
          <select
            className="dd-device-select"
            value={audioOutputDeviceId || ""}
            onChange={(e) => setAudioOutputDeviceId(e.target.value || null)}
            onFocus={() => { void refreshAudioDevices(); }}
          >
            <option value="">{t("settings.systemDefault")}</option>
            {audioDevices.outputs.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `${t("settings.deviceUnnamed")} ${i + 1}`}
              </option>
            ))}
          </select>
        </div>
        {audioDevices.inputs.length > 0 &&
          audioDevices.inputs.every((d) => !d.label) && (
            <div className="dd-hint muted small">
              {t("settings.micPermissionHint")}
            </div>
          )}
        <div className="dd-divider"></div>
        <button
          className="dd-item"
          type="button"
          onClick={() => {
            settings.close();
            setVoiceprintsOpen(true);
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
          <span>Voiceprints</span>
        </button>
      </Dropdown>

      <VoiceprintsModal open={voiceprintsOpen} onClose={() => setVoiceprintsOpen(false)} />

      <Dropdown open={avatar.open} pos={avatar.pos}>
        <div className="dd-user">
          <span className="avatar-img lg">{avatarInitial}</span>
          <div className="dd-user-info">
            <div className="dd-user-name">{displayName}</div>
            <div className="dd-user-email">{user.email}</div>
          </div>
        </div>
        <div className="dd-divider"></div>
        <button className="dd-item" type="button" onClick={() => { avatar.close(); alert(t("userControls.profile.notImplemented")); }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
          <span>{t("avatar.profile")}</span>
        </button>
        <div className="dd-divider"></div>
        <button
          className="dd-item danger"
          type="button"
          onClick={() => {
            avatar.close();
            setLogoutOpen(true);
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
          <span>{t("avatar.signOut")}</span>
        </button>
      </Dropdown>

      <ConfirmDialog
        open={logoutOpen}
        title={t("avatar.signOut")}
        message={t("userControls.logout.confirmMsg", { name: displayName })}
        confirmLabel={loggingOut ? t("userControls.logout.pending") : t("avatar.signOut")}
        cancelLabel={t("confirm.cancel")}
        danger
        onCancel={() => !loggingOut && setLogoutOpen(false)}
        onConfirm={() => { void doLogout(); }}
      />
    </div>
  );
}

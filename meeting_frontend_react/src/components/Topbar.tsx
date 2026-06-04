import { useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { Dropdown, useDropdown } from "./Dropdown";
import { VoiceprintsModal } from "./VoiceprintsModal";

export function Topbar() {
  const { theme, setTheme, toggleChat, chatOpen, toggleSidebar, lang, setLang, t } = useApp();
  const [voiceprintsOpen, setVoiceprintsOpen] = useState(false);

  const settingsRef = useRef<HTMLButtonElement>(null);
  const inviteRef = useRef<HTMLButtonElement>(null);
  const avatarRef = useRef<HTMLButtonElement>(null);
  const settings = useDropdown(settingsRef);
  const invite = useDropdown(inviteRef);
  const avatar = useDropdown(avatarRef);

  return (
    <header className="topbar">
      <div className="tb-left">
        <button className="icon-btn" type="button" title="Sidebar" onClick={toggleSidebar}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <a href="#" className="brand">
          <span className="brand-mark"></span>
          <span className="brand-name">Mee</span>
        </a>
        <button className="ws-pill" type="button">
          <span className="ws-name">GreenNode AI</span>
          <svg className="caret" viewBox="0 0 12 7" width="9" height="6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <path d="M1 1l5 5 5-5" />
          </svg>
        </button>
        <button
          ref={settingsRef}
          className="icon-btn"
          type="button"
          title="Settings"
          onClick={settings.toggle}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>

      <div className="tb-center">
        <button
          ref={inviteRef}
          className="btn btn-outline btn-sm"
          type="button"
          onClick={invite.toggle}
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <line x1="19" y1="8" x2="19" y2="14" />
            <line x1="16" y1="11" x2="22" y2="11" />
          </svg>
          <span>{t("btn.invite")}</span>
        </button>
      </div>

      <div className="tb-right">
        <span className="tb-status idle">{t("status.ready")}</span>
        <button
          className={`icon-btn${chatOpen ? " active" : ""}`}
          type="button"
          title="Toggle Agent"
          onClick={toggleChat}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <div className="tb-sep"></div>
        <button ref={avatarRef} className="avatar-btn" type="button" onClick={avatar.toggle}>
          <span className="avatar-img">U</span>
          <span className="avatar-name">User</span>
          <svg className="caret" viewBox="0 0 12 7" width="9" height="6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <path d="M1 1l5 5 5-5" />
          </svg>
        </button>
      </div>

      {/* ─── Settings dropdown ─── */}
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
            <line x1="2" y1="12" x2="22" y2="12" />
            <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
          </svg>
          <span>{t("menu.language")}</span>
          <div className="seg-toggle">
            <button
              className={`seg-opt${lang === "vi" ? " active" : ""}`}
              type="button"
              onClick={() => setLang("vi")}
              aria-label="Tiếng Việt"
            >
              VI
            </button>
            <button
              className={`seg-opt${lang === "en" ? " active" : ""}`}
              type="button"
              onClick={() => setLang("en")}
              aria-label="English"
            >
              EN
            </button>
          </div>
        </div>
        <div className="dd-divider"></div>
        <div className="dd-label">{t("menu.meeting")}</div>
        <button className="dd-item" type="button" onClick={() => { settings.close(); alert("Audio preferences — chưa implement"); }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
          <span>{t("menu.audioPrefs")}</span>
        </button>
        <button
          className="dd-item"
          type="button"
          onClick={() => { settings.close(); setVoiceprintsOpen(true); }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
          </svg>
          <span>Voiceprints</span>
        </button>
      </Dropdown>

      <VoiceprintsModal open={voiceprintsOpen} onClose={() => setVoiceprintsOpen(false)} />

      {/* ─── Invite dropdown ─── */}
      <Dropdown open={invite.open} pos={invite.pos}>
        <div className="dd-label">{t("invite.share")}</div>
        <button className="dd-item" type="button" onClick={() => { invite.close(); alert("Invite — chưa implement"); }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          <span>{t("invite.copyLink")}</span>
        </button>
        <button className="dd-item" type="button" onClick={() => { invite.close(); alert("Invite — chưa implement"); }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="8.5" cy="7" r="4" />
            <line x1="20" y1="8" x2="20" y2="14" />
            <line x1="23" y1="11" x2="17" y2="11" />
          </svg>
          <span>{t("invite.invite")}</span>
        </button>
      </Dropdown>

      {/* ─── Avatar dropdown ─── */}
      <Dropdown open={avatar.open} pos={avatar.pos}>
        <div className="dd-user">
          <span className="avatar-img lg">U</span>
          <div className="dd-user-info">
            <div className="dd-user-name">User</div>
            <div className="dd-user-email">user@vng.com.vn</div>
          </div>
        </div>
        <div className="dd-divider"></div>
        <button className="dd-item" type="button" onClick={() => { avatar.close(); alert("Profile — chưa implement"); }}>
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
            if (confirm(t("avatar.signOut") + "?")) {
              localStorage.clear();
              location.reload();
            }
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
    </header>
  );
}

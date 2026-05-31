import { AppProvider } from "./store/AppContext";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { MeetingControl } from "./components/MeetingControl";
import { Workspace } from "./components/Workspace";

export default function App() {
  return (
    <AppProvider>
      <Sidebar />
      <div className="app">
        <Topbar />
        <MeetingControl />
        <Workspace />
      </div>
    </AppProvider>
  );
}

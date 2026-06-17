import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* BrowserRouter so the URL bar reflects which view the user is on:
        /              → landing (anonymous) or auto-redirect to /app
        /onboard/voice → voice enrollment (logged-in, voice not yet done)
        /app           → main meeting workspace                              */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);

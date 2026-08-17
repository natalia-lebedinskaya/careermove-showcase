import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    const hadController = Boolean(navigator.serviceWorker.controller);
    let reloading = false;
    if (hadController) {
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (reloading) return;
        reloading = true;
        window.location.reload();
      });
    }
    navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" })
      .then((registration) => {
        registration.waiting?.postMessage({ type: "SKIP_WAITING" });
        registration.addEventListener("updatefound", () => {
          registration.installing?.addEventListener("statechange", () => {
            if (registration.waiting) {
              registration.waiting.postMessage({ type: "SKIP_WAITING" });
            }
          });
        });
        return registration.update();
      })
      .catch(() => undefined);
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

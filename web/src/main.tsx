import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { pruneExpired } from "./lib/storage";
import "./styles.css";

try {
  pruneExpired();
} catch {
  // Storage unavailable or corrupt — the app copes with that elsewhere.
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

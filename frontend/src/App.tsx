import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import Parlays from "./pages/Parlays";
import SlipChecker from "./pages/SlipChecker";
import Systems from "./pages/Systems";

type Tab = "predictions" | "systems" | "parlays" | "slip";

export default function App() {
  const [tab, setTab] = useState<Tab>("predictions");

  return (
    <div className="app-shell">
      <nav className="tab-nav">
        <button className={tab === "predictions" ? "active" : ""} onClick={() => setTab("predictions")}>
          Predictions
        </button>
        <button className={tab === "systems" ? "active" : ""} onClick={() => setTab("systems")}>
          Systems
        </button>
        <button className={tab === "parlays" ? "active" : ""} onClick={() => setTab("parlays")}>
          Parlays
        </button>
        <button className={tab === "slip" ? "active" : ""} onClick={() => setTab("slip")}>
          Slip Checker
        </button>
      </nav>
      {tab === "predictions" && <Dashboard />}
      {tab === "systems" && <Systems />}
      {tab === "parlays" && <Parlays />}
      {tab === "slip" && <SlipChecker />}
    </div>
  );
}

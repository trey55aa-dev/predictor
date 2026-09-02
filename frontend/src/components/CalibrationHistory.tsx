import { useEffect, useState } from "react";
import { fetchCalibrationHistory } from "../api/client";
import type { CalibrationAdjustment } from "../types";

const PARAMETER_LABELS: Record<string, string> = {
  market_blend_weight: "Elo/market blend weight",
  margin_std_default: "Margin confidence range",
  total_std_default: "Total confidence range",
};

export default function CalibrationHistory() {
  const [history, setHistory] = useState<CalibrationAdjustment[]>([]);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!open || loaded) return;
    fetchCalibrationHistory()
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setLoaded(true));
  }, [open, loaded]);

  return (
    <div className="calibration-section">
      <button className="calibration-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide" : "Show"} self-correction history ▾
      </button>
      {open && (
        <div className="calibration-list">
          {!loaded && <p className="loading-state">Loading...</p>}
          {loaded && history.length === 0 && (
            <p className="empty-note">
              No adjustments yet — the model needs at least 30 graded predictions before it will tune anything.
            </p>
          )}
          {history.map((h, i) => (
            <div key={i} className="calibration-entry">
              <div className="calibration-entry-header">
                <strong>{PARAMETER_LABELS[h.parameter_name] ?? h.parameter_name}</strong>
                <span className="calibration-entry-date">{new Date(h.created_at).toLocaleDateString()}</span>
              </div>
              <div className="calibration-entry-change">
                {h.old_value} → {h.new_value}
              </div>
              <p className="calibration-entry-evidence">{h.evidence}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

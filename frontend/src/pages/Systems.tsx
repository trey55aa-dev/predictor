import { useEffect, useState } from "react";
import { fetchSystemDetail, fetchSystems } from "../api/client";
import SchemeCard from "../components/SchemeCard";
import SchemeDetail from "../components/SchemeDetail";
import type { SchemeDetail as SchemeDetailType, SchemeFamily } from "../types";

export default function Systems() {
  const [families, setFamilies] = useState<SchemeFamily[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SchemeDetailType | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    fetchSystems()
      .then(setFamilies)
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    fetchSystemDetail(selectedId)
      .then(setDetail)
      .catch((err) => setError(String(err)))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  if (selectedId) {
    return (
      <div className="systems-page">
        {detailLoading && <p className="loading-state">Loading...</p>}
        {detail && <SchemeDetail detail={detail} onBack={() => setSelectedId(null)} />}
      </div>
    );
  }

  const offense = families.filter((f) => f.side === "offense");
  const defense = families.filter((f) => f.side === "defense");

  return (
    <div className="systems-page">
      <p className="systems-intro">
        Offensive and defensive systems and coaching trees, 2021-2025. Common plays, scoring plays (offense) and
        stops (defense), and why they worked -- generated from real charted play-by-play data (coverage, pressure,
        personnel, play-action/RPO/motion), not hand-written scouting reports. Scheme classification is our own
        editorial grouping of head coaches by documented system/lineage; free NFL data only identifies the head
        coach per team-season, not the coordinator who often calls the plays, so a coach's "off-side" (e.g. a
        defensive-minded HC's offense) is tracked separately as Independent rather than guessed at.
      </p>

      {loading && <p className="loading-state">Loading systems...</p>}
      {error && <p className="error-state">Couldn't load systems: {error}</p>}

      {!loading && !error && (
        <>
          <h2 className="side-heading">Offensive Systems</h2>
          <div className="scheme-grid">
            {offense.map((f) => (
              <SchemeCard key={f.id} family={f} onSelect={setSelectedId} />
            ))}
          </div>

          <h2 className="side-heading">Defensive Systems</h2>
          <div className="scheme-grid">
            {defense.map((f) => (
              <SchemeCard key={f.id} family={f} onSelect={setSelectedId} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

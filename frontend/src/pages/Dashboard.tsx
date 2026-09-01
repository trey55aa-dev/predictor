import { useEffect, useState } from "react";
import { fetchPerformance, fetchPredictionsForWeek } from "../api/client";
import GameList from "../components/GameList";
import WeekSelector from "../components/WeekSelector";
import type { GamePrediction, Performance } from "../types";

export default function Dashboard() {
  const [season, setSeason] = useState(2026);
  const [week, setWeek] = useState(1);
  const [predictions, setPredictions] = useState<GamePrediction[]>([]);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchPredictionsForWeek(season, week)
      .then((data) => {
        if (!cancelled) setPredictions(data);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [season, week]);

  useEffect(() => {
    fetchPerformance().then(setPerformance).catch(() => setPerformance(null));
  }, []);

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Football Predictor</h1>
        <p className="subtitle">
          Weekly NFL predictions blending Elo power ratings, Vegas market odds, and weather.
        </p>
      </header>

      <WeekSelector season={season} week={week} onSeasonChange={setSeason} onWeekChange={setWeek} />

      {performance && performance.graded_predictions > 0 && (
        <div className="performance-strip">
          Model track record: {performance.graded_predictions} graded predictions,{" "}
          {performance.winner_accuracy !== null ? `${Math.round(performance.winner_accuracy * 100)}%` : "—"} winner
          accuracy, Brier {performance.avg_brier_score?.toFixed(3) ?? "—"}
        </div>
      )}

      {loading && <p className="loading-state">Loading predictions…</p>}
      {error && <p className="error-state">Couldn't load predictions: {error}</p>}
      {!loading && !error && <GameList predictions={predictions} />}
    </div>
  );
}

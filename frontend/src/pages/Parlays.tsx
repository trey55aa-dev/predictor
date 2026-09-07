import { useEffect, useState } from "react";
import { fetchParlays } from "../api/client";
import ModelAccuracyNote from "../components/ModelAccuracyNote";
import ParlayCard from "../components/ParlayCard";
import StakeControls, { type StakeSettings } from "../components/StakeControls";
import WeekSelector from "../components/WeekSelector";
import type { ParlaysResponse } from "../types";

export default function Parlays() {
  const [season, setSeason] = useState(2026);
  const [week, setWeek] = useState(1);
  const [data, setData] = useState<ParlaysResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stakeSettings, setStakeSettings] = useState<StakeSettings>({
    stake: 5,
    boostPct: 0,
    freeBet: false,
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchParlays(season, week, 3)
      .then((d) => {
        if (!cancelled) setData(d);
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

  return (
    <div className="systems-page">
      <p className="systems-intro">
        Two parlay suggestions built from this week's model predictions: the safest (highest combined
        probability of every leg hitting) and the biggest disagreement between the model and the sportsbooks.
        These are the model's own estimates, not guarantees — parlays compound risk even when each leg looks
        good on its own.
      </p>

      <ModelAccuracyNote />

      <WeekSelector season={season} week={week} onSeasonChange={setSeason} onWeekChange={setWeek} />
      <StakeControls value={stakeSettings} onChange={setStakeSettings} />

      {loading && <p className="loading-state">Loading parlays...</p>}
      {error && <p className="error-state">Couldn't load parlays: {error}</p>}

      {!loading && !error && data && (
        <div className="parlay-grid">
          <ParlayCard
            title="Safest Option"
            subtitle="Highest combined hit probability"
            summary={data.safest}
            stakeSettings={stakeSettings}
          />
          <ParlayCard
            title="Biggest Model Disagreement"
            subtitle="Where the model diverges most from the market — speculative, not a proven edge"
            summary={data.best_money_move}
            stakeSettings={stakeSettings}
            emptyNote={data.best_money_move_note}
          />
        </div>
      )}
    </div>
  );
}

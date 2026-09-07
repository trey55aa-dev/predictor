import { useEffect, useState } from "react";
import { fetchAccuracyComparison } from "../api/client";
import type { AccuracyComparison } from "../types";

const LABELS: Record<string, string> = {
  elo_only: "Data only (Elo)",
  market_only: "Market (Vegas)",
  blend: "Blend (shown here)",
};

/** The honest scoreboard behind the "edge" column: an edge measured against
 * the market is only worth money if the model is the more accurate of the
 * two, so this shows which one has actually been more accurate. */
export default function ModelAccuracyNote() {
  const [data, setData] = useState<AccuracyComparison | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAccuracyComparison()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        /* non-critical panel -- stay silent rather than break the page */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data || data.sample_size === 0) return null;

  const marketWins = data.most_accurate === "market_only";

  return (
    <details className="accuracy-note">
      <summary>
        How accurate is this model really? ({data.sample_size} graded games)
        {marketWins && " — the market is currently more accurate than the model"}
      </summary>
      <table className="accuracy-table">
        <thead>
          <tr>
            <th>Source</th>
            <th>Brier (lower is better)</th>
            <th>Winner accuracy</th>
          </tr>
        </thead>
        <tbody>
          {data.sources.map((s) => (
            <tr key={s.key} className={s.key === data.most_accurate ? "accuracy-best" : ""}>
              <td>{LABELS[s.key] ?? s.key}</td>
              <td>{s.brier_score.toFixed(4)}</td>
              <td>{(s.winner_accuracy * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {marketWins && (
        <p className="accuracy-caveat">
          The market has scored better than this model on the same games. That means a positive "edge" below is
          not evidence of value — it marks where the model disagrees with a source that has been more accurate
          than the model. Those legs are the most likely to be model error, not the most likely to be
          underpriced.
        </p>
      )}
    </details>
  );
}

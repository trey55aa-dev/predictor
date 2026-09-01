import { useEffect, useState } from "react";
import { fetchGamePlan, fetchPlayerProjections } from "../api/client";
import type {
  DefensiveTendencies,
  GamePlan,
  InjuryEntry,
  PlayerProjection,
  PlayerProjectionsResponse,
  SchemeMatchupStats,
} from "../types";

function pct(p: number | null | undefined): string {
  return p === null || p === undefined ? "—" : `${Math.round(p * 100)}%`;
}

function InjuryList({ injuries, team }: { injuries: InjuryEntry[]; team: string }) {
  if (injuries.length === 0) {
    return (
      <div className="injury-column">
        <h4>{team}</h4>
        <p className="empty-note">No notable injuries reported.</p>
      </div>
    );
  }
  return (
    <div className="injury-column">
      <h4>{team}</h4>
      <ul className="injury-list">
        {injuries.map((inj) => (
          <li key={inj.player_name}>
            <span className={`status-dot ${inj.report_status.toLowerCase()}`} />
            <strong>{inj.player_name}</strong> ({inj.position}
            {inj.is_starter ? ", starter" : ""}) — {inj.report_status}
            {inj.primary_injury ? `, ${inj.primary_injury}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TendencyLine({ label, tendencies }: { label: string; tendencies: DefensiveTendencies }) {
  if (tendencies.sample_size === 0) return null;
  return (
    <p className="tendency-line">
      <strong>{label}:</strong> man {pct(tendencies.man_rate)} / zone {pct(tendencies.zone_rate)}, blitz{" "}
      {pct(tendencies.blitz_rate)}, pressure {pct(tendencies.pressure_rate)}
      {tendencies.avg_box_count != null ? `, avg box ${tendencies.avg_box_count.toFixed(1)}` : ""}
    </p>
  );
}

function MatchupLine({ label, stats }: { label: string; stats: SchemeMatchupStats }) {
  if (stats.sample_size < 10) {
    return (
      <p className="tendency-line">
        <strong>{label}:</strong> not enough historical plays between these styles yet ({stats.sample_size}).
      </p>
    );
  }
  return (
    <p className="tendency-line">
      <strong>{label}:</strong> {stats.sample_size.toLocaleString()} historical plays, avg EPA{" "}
      {stats.avg_epa?.toFixed(2)}, success rate {pct(stats.success_rate)}
    </p>
  );
}

function PlayerColumn({ players, team }: { players: PlayerProjection[]; team: string }) {
  if (players.length === 0) {
    return (
      <div className="injury-column">
        <h4>{team}</h4>
        <p className="empty-note">Not enough player history to project yet.</p>
      </div>
    );
  }
  return (
    <div className="injury-column">
      <h4>{team}</h4>
      <ul className="player-projection-list">
        {players.map((p) => (
          <li key={p.player_id}>
            <div className="player-projection-row">
              <strong>{p.player_name}</strong>
              <span className="player-position">{p.position}</span>
            </div>
            <div className="player-projection-stats">
              {p.projected_rushing_yards > 5 && <span>{p.projected_rushing_yards.toFixed(0)} rush yds</span>}
              {p.projected_receiving_yards > 5 && <span>{p.projected_receiving_yards.toFixed(0)} rec yds</span>}
              {p.projected_passing_yards > 5 && <span>{p.projected_passing_yards.toFixed(0)} pass yds</span>}
              <span className="player-td-prob">{pct(p.anytime_td_prob)} anytime TD</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function GamePlanPanel({ gameId }: { gameId: string }) {
  const [plan, setPlan] = useState<GamePlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [players, setPlayers] = useState<PlayerProjectionsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchGamePlan(gameId)
      .then((p) => {
        if (!cancelled) setPlan(p);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    fetchPlayerProjections(gameId)
      .then((p) => {
        if (!cancelled) setPlayers(p);
      })
      .catch(() => {
        if (!cancelled) setPlayers(null);
      });
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  if (loading) return <p className="loading-state">Loading game plan...</p>;
  if (error) return <p className="error-state">Couldn't load game plan: {error}</p>;
  if (!plan) return null;

  return (
    <div className="game-plan-panel">
      {plan.upset_alert.note && <p className="plan-note upset">🚨 {plan.upset_alert.note}</p>}
      {plan.over_under.note && <p className="plan-note">{plan.over_under.note}</p>}

      <section>
        <h4 className="plan-section-title">Injuries</h4>
        <div className="injury-columns">
          <InjuryList injuries={plan.injuries.home} team={plan.home_team} />
          <InjuryList injuries={plan.injuries.away} team={plan.away_team} />
        </div>
      </section>

      {players && (players.home.length > 0 || players.away.length > 0) && (
        <section>
          <h4 className="plan-section-title">Player projections</h4>
          <div className="injury-columns">
            <PlayerColumn players={players.home} team={plan.home_team} />
            <PlayerColumn players={players.away} team={plan.away_team} />
          </div>
        </section>
      )}

      <section>
        <h4 className="plan-section-title">Play styles</h4>
        <p className="tendency-line">
          <strong>{plan.home_team} offense likes:</strong>{" "}
          {plan.play_styles.home_offense.map((c) => c.label).join(", ") || "not enough data"}
        </p>
        <p className="tendency-line">
          <strong>{plan.away_team} offense likes:</strong>{" "}
          {plan.play_styles.away_offense.map((c) => c.label).join(", ") || "not enough data"}
        </p>
        <TendencyLine label={`${plan.home_team} defense`} tendencies={plan.play_styles.home_defense} />
        <TendencyLine label={`${plan.away_team} defense`} tendencies={plan.play_styles.away_defense} />
      </section>

      <section>
        <h4 className="plan-section-title">Scheme matchup history</h4>
        <MatchupLine
          label={`${plan.home_team} offense vs. ${plan.away_team}-style defense`}
          stats={plan.scheme_matchups.home_offense_vs_away_defense}
        />
        <MatchupLine
          label={`${plan.away_team} offense vs. ${plan.home_team}-style defense`}
          stats={plan.scheme_matchups.away_offense_vs_home_defense}
        />
      </section>
    </div>
  );
}

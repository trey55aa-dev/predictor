import { useEffect, useMemo, useState } from "react";
import {
  evaluateSlip,
  fetchPlayerProjections,
  fetchPredictionsForWeek,
  fetchSimPlayerProps,
} from "../api/client";
import { calculatePayout, formatMoney } from "../utils/payout";
import StakeControls, { type StakeSettings } from "../components/StakeControls";
import WeekSelector from "../components/WeekSelector";
import type {
  GamePrediction,
  PlayerProjection,
  SimPlayerProp,
  SlipEvaluation,
  SlipLegInput,
  SlipLegType,
} from "../types";

function pct(p: number | null): string {
  return p === null ? "—" : `${Math.round(p * 100)}%`;
}

function uid(): string {
  return Math.random().toString(36).slice(2);
}

function AddLegForm({
  games,
  onAdd,
}: {
  games: GamePrediction[];
  onAdd: (leg: SlipLegInput) => void;
}) {
  const [legType, setLegType] = useState<SlipLegType>("game_winner");
  const [gameId, setGameId] = useState<string>("");
  const [team, setTeam] = useState<string>("");
  const [playerId, setPlayerId] = useState<string>("");
  const [stat, setStat] = useState<"rushing" | "receiving">("rushing");
  const [side, setSide] = useState<"over" | "under">("over");
  const [line, setLine] = useState<string>("");
  const [odds, setOdds] = useState<string>("");

  const [tdPlayers, setTdPlayers] = useState<PlayerProjection[]>([]);
  const [yardsPlayers, setYardsPlayers] = useState<SimPlayerProp[]>([]);

  const game = useMemo(() => games.find((g) => g.game_id === gameId), [games, gameId]);

  useEffect(() => {
    if (!gameId) return;
    if (legType === "anytime_td") {
      fetchPlayerProjections(gameId)
        .then((r) => setTdPlayers([...r.home, ...r.away].sort((a, b) => b.anytime_td_prob - a.anytime_td_prob)))
        .catch(() => setTdPlayers([]));
    } else if (legType === "player_yards") {
      fetchSimPlayerProps(gameId)
        .then((r) => setYardsPlayers([...r.home, ...r.away]))
        .catch(() => setYardsPlayers([]));
    }
  }, [gameId, legType]);

  useEffect(() => {
    setTeam("");
    setPlayerId("");
  }, [gameId, legType]);

  const canAdd =
    gameId &&
    (legType === "game_winner"
      ? !!team
      : legType === "anytime_td"
        ? !!playerId
        : !!playerId && line.trim() !== "" && !Number.isNaN(Number(line)));

  const playerOptionsForYards = yardsPlayers.filter((p) => (stat === "rushing" ? p.rushing : p.receiving));

  function handleAdd() {
    if (!canAdd) return;
    const base: SlipLegInput = {
      key: uid(),
      leg_type: legType,
      game_id: gameId,
      american_odds: odds.trim() === "" ? undefined : Number(odds),
    };
    if (legType === "game_winner") {
      onAdd({ ...base, team });
    } else if (legType === "anytime_td") {
      const p = tdPlayers.find((p) => p.player_id === playerId);
      onAdd({ ...base, player_id: playerId, player_name: p?.player_name });
    } else {
      const p = yardsPlayers.find((p) => p.player_id === playerId);
      onAdd({ ...base, player_id: playerId, player_name: p?.player_name, stat, side, line: Number(line) });
    }
    setLine("");
    setOdds("");
  }

  return (
    <div className="slip-add-form">
      <div className="slip-form-row">
        <label>
          Leg type
          <select value={legType} onChange={(e) => setLegType(e.target.value as SlipLegType)}>
            <option value="game_winner">Moneyline</option>
            <option value="anytime_td">Anytime TD</option>
            <option value="player_yards">Player rush/rec yards</option>
          </select>
        </label>
        <label>
          Game
          <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
            <option value="">Select a game…</option>
            {games.map((g) => (
              <option key={g.game_id} value={g.game_id}>
                {g.away_team} @ {g.home_team}
              </option>
            ))}
          </select>
        </label>
      </div>

      {legType === "game_winner" && game && (
        <div className="slip-form-row">
          <label>
            Team
            <select value={team} onChange={(e) => setTeam(e.target.value)}>
              <option value="">Select a team…</option>
              <option value={game.home_team}>{game.home_team} (home)</option>
              <option value={game.away_team}>{game.away_team} (away)</option>
            </select>
          </label>
        </div>
      )}

      {legType === "anytime_td" && gameId && (
        <div className="slip-form-row">
          <label>
            Player
            <select value={playerId} onChange={(e) => setPlayerId(e.target.value)}>
              <option value="">Select a player…</option>
              {tdPlayers.map((p) => (
                <option key={p.player_id} value={p.player_id}>
                  {p.player_name} ({p.team}) — model {pct(p.anytime_td_prob)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {legType === "player_yards" && gameId && (
        <>
          <div className="slip-form-row">
            <label>
              Stat
              <select value={stat} onChange={(e) => setStat(e.target.value as "rushing" | "receiving")}>
                <option value="rushing">Rushing yards</option>
                <option value="receiving">Receiving yards</option>
              </select>
            </label>
            <label>
              Player
              <select value={playerId} onChange={(e) => setPlayerId(e.target.value)}>
                <option value="">Select a player…</option>
                {playerOptionsForYards.map((p) => (
                  <option key={p.player_id} value={p.player_id}>
                    {p.player_name} ({p.team}) — sim mean{" "}
                    {(stat === "rushing" ? p.rushing?.mean_yards : p.receiving?.mean_yards)?.toFixed(0)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="slip-form-row">
            <label>
              Side
              <select value={side} onChange={(e) => setSide(e.target.value as "over" | "under")}>
                <option value="over">Over</option>
                <option value="under">Under</option>
              </select>
            </label>
            <label>
              Line (yards)
              <input type="number" step="0.5" value={line} onChange={(e) => setLine(e.target.value)} placeholder="e.g. 45.5" />
            </label>
          </div>
        </>
      )}

      {gameId && (
        <div className="slip-form-row">
          <label>
            Your odds (optional)
            <input type="number" value={odds} onChange={(e) => setOdds(e.target.value)} placeholder="e.g. -110" />
          </label>
          <button type="button" className="stake-button" disabled={!canAdd} onClick={handleAdd}>
            + Add leg
          </button>
        </div>
      )}
    </div>
  );
}

export default function SlipChecker() {
  const [season, setSeason] = useState(2026);
  const [week, setWeek] = useState(1);
  const [games, setGames] = useState<GamePrediction[]>([]);
  const [legs, setLegs] = useState<SlipLegInput[]>([]);
  const [result, setResult] = useState<SlipEvaluation | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stakeSettings, setStakeSettings] = useState<StakeSettings>({ stake: 5, boostPct: 0, freeBet: false });

  useEffect(() => {
    fetchPredictionsForWeek(season, week)
      .then(setGames)
      .catch(() => setGames([]));
    setLegs([]);
    setResult(null);
  }, [season, week]);

  function removeLeg(key: string) {
    setLegs((prev) => prev.filter((l) => l.key !== key));
    setResult(null);
  }

  function checkSlip() {
    if (legs.length === 0) return;
    setChecking(true);
    setError(null);
    evaluateSlip(legs)
      .then(setResult)
      .catch((err) => setError(String(err)))
      .finally(() => setChecking(false));
  }

  const payout =
    result && result.combined_decimal_payout !== null
      ? calculatePayout({
          decimalPayout: result.combined_decimal_payout,
          stake: stakeSettings.stake,
          boostPct: stakeSettings.boostPct,
          freeBet: stakeSettings.freeBet,
        })
      : null;

  function legLabel(leg: SlipLegInput): string {
    if (leg.leg_type === "game_winner") return `${leg.team} to win`;
    if (leg.leg_type === "anytime_td") return `${leg.player_name ?? leg.player_id} anytime TD`;
    return `${leg.player_name ?? leg.player_id} ${leg.side} ${leg.line} ${leg.stat} yds`;
  }

  return (
    <div className="systems-page">
      <p className="systems-intro">
        Build your own slip -- pick real legs from this week's games -- and see the model's own probability for
        each one, using the exact same data the app's own parlays are built from. Not a guarantee, and not
        checked against what your sportsbook actually offers unless you enter your own odds per leg.
      </p>

      <WeekSelector season={season} week={week} onSeasonChange={setSeason} onWeekChange={setWeek} />

      <AddLegForm games={games} onAdd={(leg) => setLegs((prev) => [...prev, leg])} />

      {legs.length > 0 && (
        <div className="slip-leg-list">
          <h4 className="plan-section-title">Your slip ({legs.length} legs)</h4>
          <ul>
            {legs.map((leg) => (
              <li key={leg.key} className="slip-leg-row">
                <span>{legLabel(leg)}</span>
                {leg.american_odds !== undefined && <span className="slip-leg-odds">{leg.american_odds}</span>}
                <button type="button" className="slip-remove-btn" onClick={() => removeLeg(leg.key)}>
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="stake-button active" disabled={checking} onClick={checkSlip}>
            {checking ? "Checking…" : "Check my slip"}
          </button>
        </div>
      )}

      {error && <p className="error-state">Couldn't check slip: {error}</p>}

      {result && (
        <div className="parlay-card">
          <h3>Results</h3>
          <ul className="parlay-legs">
            {result.legs.map((leg, i) => (
              <li key={i}>
                {leg.error ? (
                  <p className="empty-note">⚠️ {leg.error}</p>
                ) : (
                  <>
                    <div className="parlay-leg-row">
                      <strong>{leg.description}</strong>
                      {leg.american_odds !== null && <span className="parlay-leg-price">{leg.american_odds}</span>}
                    </div>
                    <div className="parlay-leg-meta">
                      model {pct(leg.model_prob)}
                      {leg.elo_prob !== null && ` · data-only Elo ${pct(leg.elo_prob)}`}
                      {leg.market_prob !== null && ` · market ${pct(leg.market_prob)}`}
                      {leg.approximated && " · yardage probability approximated from the sim distribution"}
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>

          {result.combined_probability !== null && (
            <div className="parlay-summary-row">
              <span>Combined probability: {pct(result.combined_probability)}</span>
              <span>
                Payout:{" "}
                {result.combined_decimal_payout !== null
                  ? `${result.combined_decimal_payout.toFixed(2)}x`
                  : "unavailable (add odds to every leg)"}
              </span>
            </div>
          )}

          {result.combined_decimal_payout !== null && (
            <>
              <StakeControls value={stakeSettings} onChange={setStakeSettings} />
              {payout && (
                <div className="payout-breakdown">
                  <div className="payout-headline">
                    <span>
                      {stakeSettings.freeBet ? "Free bet" : "Stake"} {formatMoney(stakeSettings.stake)}
                    </span>
                    <strong>{formatMoney(payout.totalReturn)} back</strong>
                  </div>
                  <div className="payout-detail">Profit {formatMoney(payout.profit)}</div>
                </div>
              )}
            </>
          )}

          <p className="parlay-caveat">{result.caveat}</p>
        </div>
      )}
    </div>
  );
}

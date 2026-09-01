import type { GamePrediction } from "../types";
import GameCard from "./GameCard";

export default function GameList({ predictions }: { predictions: GamePrediction[] }) {
  if (predictions.length === 0) {
    return <p className="empty-state">No games found for this week yet.</p>;
  }

  return (
    <div className="game-list">
      {predictions.map((p) => (
        <GameCard key={p.game_id} prediction={p} />
      ))}
    </div>
  );
}

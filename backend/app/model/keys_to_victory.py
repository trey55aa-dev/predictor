"""Post-game breakdown: for a FINAL game, compares what the model predicted
against what actually happened, and explains the real result using the
"keys to victory" stats the user asked this site to track -- turnover
margin, rushing yards, passing yards, and 3rd/4th down conversion rate --
computed from real plays (see ingestion/current_plays.py), never estimated.

4th down conversion% is included but flagged as no-signal in the narrative:
an earlier validation pass over historical games found the team that wins
more of its own 4th down tries is close to a coin flip for who wins the
game (~50.4% across the sample), unlike the other four keys, which all
showed real separation. Shown for completeness, not sold as predictive.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.models import Game, Play, Prediction

# Keys that showed real signal in validation (see module docstring). 4th
# down is deliberately not in this set -- it's shown in the stats table but
# excluded from the "why" narrative's reasoning.
SIGNAL_KEYS = {"turnover_margin", "rushing_yards", "passing_yards", "third_down_pct"}


def _latest_prediction(db: Session, game_id: str) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.created_at.desc())
        .first()
    )


def _team_stats(plays: list[Play], team: str) -> dict:
    rushing_yards = sum(p.yards_gained or 0 for p in plays if p.posteam == team and p.play_type == "run")
    passing_yards = sum(
        p.yards_gained or 0 for p in plays if p.posteam == team and p.play_type == "pass" and not p.sack
    )
    turnovers = sum(1 for p in plays if p.posteam == team and (p.interception or p.fumble_lost))

    third_downs = [p for p in plays if p.posteam == team and p.down == 3]
    third_conversions = sum(1 for p in third_downs if (p.yards_gained or 0) >= (p.ydstogo or 999))

    fourth_downs = [p for p in plays if p.posteam == team and p.down == 4]
    fourth_conversions = sum(1 for p in fourth_downs if (p.yards_gained or 0) >= (p.ydstogo or 999))

    return {
        "rushing_yards": rushing_yards,
        "passing_yards": passing_yards,
        "turnovers": turnovers,
        "third_down_attempts": len(third_downs),
        "third_down_conversions": third_conversions,
        "third_down_pct": (third_conversions / len(third_downs)) if third_downs else None,
        "fourth_down_attempts": len(fourth_downs),
        "fourth_down_conversions": fourth_conversions,
        "fourth_down_pct": (fourth_conversions / len(fourth_downs)) if fourth_downs else None,
    }


def _key_winner(home_val, away_val, higher_is_better: bool = True) -> str | None:
    if home_val is None or away_val is None or home_val == away_val:
        return None
    if higher_is_better:
        return "home" if home_val > away_val else "away"
    return "home" if home_val < away_val else "away"


def _build_keys(home_stats: dict, away_stats: dict) -> list[dict]:
    home_margin = away_stats["turnovers"] - home_stats["turnovers"]  # takeaways minus giveaways
    away_margin = home_stats["turnovers"] - away_stats["turnovers"]

    keys = [
        {
            "key": "turnover_margin",
            "label": "Turnover margin",
            "home_value": home_margin,
            "away_value": away_margin,
            "winner": _key_winner(home_margin, away_margin),
        },
        {
            "key": "rushing_yards",
            "label": "Rushing yards",
            "home_value": home_stats["rushing_yards"],
            "away_value": away_stats["rushing_yards"],
            "winner": _key_winner(home_stats["rushing_yards"], away_stats["rushing_yards"]),
        },
        {
            "key": "passing_yards",
            "label": "Passing yards",
            "home_value": home_stats["passing_yards"],
            "away_value": away_stats["passing_yards"],
            "winner": _key_winner(home_stats["passing_yards"], away_stats["passing_yards"]),
        },
        {
            "key": "third_down_pct",
            "label": "3rd down conversion rate",
            "home_value": home_stats["third_down_pct"],
            "away_value": away_stats["third_down_pct"],
            "winner": _key_winner(home_stats["third_down_pct"], away_stats["third_down_pct"]),
        },
        {
            "key": "fourth_down_pct",
            "label": "4th down conversion rate",
            "home_value": home_stats["fourth_down_pct"],
            "away_value": away_stats["fourth_down_pct"],
            "winner": _key_winner(home_stats["fourth_down_pct"], away_stats["fourth_down_pct"]),
            "no_signal": True,
        },
    ]
    return keys


def _narrative(game: Game, actual_winner: str | None, keys: list[dict], prediction: Prediction | None) -> str:
    if actual_winner is None:
        return "This game ended in a tie -- no winner to explain."

    winner_name = game.home_team if actual_winner == "home" else game.away_team
    loser_name = game.away_team if actual_winner == "home" else game.home_team

    won_keys = [k["label"].lower() for k in keys if k["key"] in SIGNAL_KEYS and k["winner"] == actual_winner]
    lost_keys = [
        k["label"].lower()
        for k in keys
        if k["key"] in SIGNAL_KEYS and k["winner"] is not None and k["winner"] != actual_winner
    ]

    if won_keys and lost_keys:
        reasoning = f"{winner_name} won {_join(won_keys)}, despite losing {_join(lost_keys)}"
    elif won_keys:
        reasoning = f"{winner_name} won {_join(won_keys)}"
    elif lost_keys:
        reasoning = f"{winner_name} won the game despite losing {_join(lost_keys)}"
    else:
        reasoning = "no tracked key showed a clear edge either way"

    verdict = ""
    if prediction is not None and prediction.home_win_prob is not None:
        predicted_home = prediction.home_win_prob >= 0.5
        predicted_winner_name = game.home_team if predicted_home else game.away_team
        correct = predicted_winner_name == winner_name
        conf = prediction.home_win_prob if predicted_home else 1 - prediction.home_win_prob
        if correct:
            verdict = f" The model correctly favored {predicted_winner_name} to win ({conf:.0%})."
        else:
            verdict = (
                f" The model favored {predicted_winner_name} ({conf:.0%}) and got the winner wrong here."
            )

    return f"{winner_name} beat {loser_name}: {reasoning}.{verdict}"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def build_game_breakdown(db: Session, game: Game) -> dict:
    """Real post-game breakdown for a FINAL game: the model's prediction vs.
    the actual result, plus the real keys-to-victory stats that explain it.
    Returns data_available=False (not fabricated placeholders) when this
    game's plays haven't been ingested yet -- see ingestion/current_plays.py
    and ingestion/plays.py, whichever covers this game's season."""
    plays = db.query(Play).filter(Play.game_id == game.game_id).all()

    prediction = _latest_prediction(db, game.game_id)
    predicted_winner = None
    if prediction is not None and prediction.home_win_prob is not None:
        predicted_winner = game.home_team if prediction.home_win_prob >= 0.5 else game.away_team

    base = {
        "game_id": game.game_id,
        "status": game.status,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "predicted_winner": predicted_winner,
        "predicted_home_win_prob": prediction.home_win_prob if prediction else None,
    }

    if game.status != "final":
        return {**base, "data_available": False, "reason": "Game isn't final yet."}
    if not plays:
        return {**base, "data_available": False, "reason": "Play-by-play for this game hasn't been ingested yet."}

    if game.home_score is None or game.away_score is None:
        actual_winner_side = None
    elif game.home_score > game.away_score:
        actual_winner_side = "home"
    elif game.away_score > game.home_score:
        actual_winner_side = "away"
    else:
        actual_winner_side = None
    actual_winner = (
        game.home_team if actual_winner_side == "home" else game.away_team if actual_winner_side == "away" else None
    )

    home_stats = _team_stats(plays, game.home_team)
    away_stats = _team_stats(plays, game.away_team)
    keys = _build_keys(home_stats, away_stats)

    correct_winner = (predicted_winner == actual_winner) if (predicted_winner and actual_winner) else None

    return {
        **base,
        "data_available": True,
        "actual_winner": actual_winner,
        "correct_winner": correct_winner,
        "home_stats": home_stats,
        "away_stats": away_stats,
        "keys": keys,
        "narrative": _narrative(game, actual_winner_side, keys, prediction),
        "computed_at": dt.datetime.utcnow().isoformat(),
    }

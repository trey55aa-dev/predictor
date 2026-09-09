"""Attributes the Monte Carlo simulator's play outcomes to specific players,
producing real simulation-based player prop distributions.

Design: the *amount* of yardage a situation produces is already validated
(model/simulation.py, backtested on 544 games). This module does not touch
that -- it only decides, after a play's yardage is already determined by the
validated situational sampling, WHICH currently-rostered player gets credit
for it, using that player's real current usage share (model/player_usage.py).
That split matters: it means a trade, a benching, or a rookie taking over a
role changes the player-level output immediately (today's usage data) without
needing to touch or re-validate the underlying yardage model at all.

Passing yards are credited to both the passer and the receiver on a
completion, matching how real box scores work (a QB's passing yards equal the
sum of his receivers' receiving yards on completions). Sacks and other
no-target pass plays get no player-level credit at all (see
PlayOutcome.has_target) -- official passing-yard totals exclude sack yardage
too, so this is the accurate box-score convention, not a simplification.
"""

import bisect
import random
from collections import defaultdict

from sqlalchemy.orm import Session

from app.model.player_usage import passer_shares, player_names, rushing_shares, target_shares
from app.model.simulation import GameSimulator

MIN_SHARE_SAMPLE = 0.15  # below this total recent-game weight, usage is too thin to attribute plays to


class WeightedSampler:
    """O(log n) weighted sampling from a small, fixed distribution -- built
    once per team, then called once per rush/pass play across every
    simulated game, so this needs to be fast, not just simple."""

    def __init__(self, shares: dict[str, float]):
        self.player_ids = list(shares.keys())
        cumulative = []
        total = 0.0
        for pid in self.player_ids:
            total += shares[pid]
            cumulative.append(total)
        self.cumulative = cumulative
        self.total = total

    def sample(self, rng: random.Random) -> str | None:
        if not self.player_ids or self.total <= 0:
            return None
        roll = rng.random() * self.total
        idx = bisect.bisect_left(self.cumulative, roll)
        idx = min(idx, len(self.player_ids) - 1)
        return self.player_ids[idx]


class PlayerAccumulator:
    """Per-sim-game, per-player rushing/receiving/passing yards and TDs."""

    def __init__(self, n_sims: int):
        self.n_sims = n_sims
        self.rushing_yards: dict[str, list[float]] = defaultdict(lambda: [0.0] * n_sims)
        self.rushing_tds: dict[str, list[int]] = defaultdict(lambda: [0] * n_sims)
        self.receiving_yards: dict[str, list[float]] = defaultdict(lambda: [0.0] * n_sims)
        self.receiving_tds: dict[str, list[int]] = defaultdict(lambda: [0] * n_sims)
        self.passing_yards: dict[str, list[float]] = defaultdict(lambda: [0.0] * n_sims)
        self.passing_tds: dict[str, list[int]] = defaultdict(lambda: [0] * n_sims)


def _percentiles(values: list[float]) -> dict:
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        return float(s[min(n - 1, int(p * n))])

    return {"p10": pct(0.10), "p25": pct(0.25), "p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90)}


def _summarize_player(name: str, yards: list[float], tds: list[int]) -> dict:
    n = len(yards)
    return {
        "player_name": name,
        "mean_yards": sum(yards) / n,
        **{f"yards_{k}": v for k, v in _percentiles(yards).items()},
        "td_probability": sum(1 for t in tds if t > 0) / n,
        "mean_tds": sum(tds) / n,
    }


class PlayerPropsSimulator:
    def __init__(
        self,
        db: Session,
        home_team: str,
        away_team: str,
        season: int,
        week: int,
        seasons: list[int],
        seed: int | None = None,
    ):
        self.game_sim = GameSimulator(db, home_team, away_team, seasons, seed=seed)
        self.home_team = home_team
        self.away_team = away_team

        self.rush_samplers: dict[str, WeightedSampler] = {}
        self.target_samplers: dict[str, WeightedSampler] = {}
        self.passer_samplers: dict[str, WeightedSampler] = {}
        self.names: dict[str, str] = {}
        self.usage_sample_size: dict[str, dict] = {}

        all_player_ids: set[str] = set()
        for team in (home_team, away_team):
            rush = rushing_shares(db, team, season, week)
            target = target_shares(db, team, season, week)
            passer = passer_shares(db, team, season, week)
            self.rush_samplers[team] = WeightedSampler(rush)
            self.target_samplers[team] = WeightedSampler(target)
            self.passer_samplers[team] = WeightedSampler(passer)
            all_player_ids.update(rush, target, passer)
            self.usage_sample_size[team] = {
                "rushers_tracked": len(rush),
                "targets_tracked": len(target),
                "passers_tracked": len(passer),
            }
        self.names = player_names(db, list(all_player_ids))

    def simulate(self, n_sims: int = 5000) -> dict:
        acc = PlayerAccumulator(n_sims)
        rng = self.game_sim.rng

        # Who starts at QB is a per-GAME decision, not a per-play one -- real
        # passing yardage in a single game essentially never splits across two
        # quarterbacks. Drawing a fresh passer on every play instead (the same
        # mechanism correctly used for rusher/receiver, who legitimately do
        # rotate play to play) split simulated passing yards across whichever
        # backup shows up in the trailing usage window, which showed up in
        # validation as a ~5x passing-yardage MAE regression against the
        # existing trailing-average projection. One starter is drawn per team
        # the first time each simulated game is seen, then reused for every
        # play in that game.
        game_passers: dict[int, dict[str, str | None]] = {}

        def on_play(sim_index: int, offense: str, outcome, scored: bool) -> None:
            if outcome.play_type == "run":
                rusher = self.rush_samplers[offense].sample(rng)
                if rusher is None:
                    return
                acc.rushing_yards[rusher][sim_index] += outcome.yards
                if scored:
                    acc.rushing_tds[rusher][sim_index] += 1
                return

            if outcome.play_type == "pass" and outcome.has_target:
                receiver = self.target_samplers[offense].sample(rng)
                if receiver is not None:
                    acc.receiving_yards[receiver][sim_index] += outcome.yards
                    if scored:
                        acc.receiving_tds[receiver][sim_index] += 1

                passers_this_game = game_passers.setdefault(sim_index, {})
                if offense not in passers_this_game:
                    passers_this_game[offense] = self.passer_samplers[offense].sample(rng)
                passer = passers_this_game[offense]
                if passer is not None:
                    acc.passing_yards[passer][sim_index] += outcome.yards
                    if scored:
                        acc.passing_tds[passer][sim_index] += 1

        game_summary = self.game_sim.simulate(n_sims, on_play=on_play).summary()

        def build_side(team: str) -> list[dict]:
            player_ids = set()
            for d in (acc.rushing_yards, acc.receiving_yards, acc.passing_yards):
                player_ids.update(pid for pid in d if pid in self._team_player_ids(team))
            out = []
            for pid in player_ids:
                name = self.names.get(pid, pid)
                rushing = _summarize_player(name, acc.rushing_yards[pid], acc.rushing_tds[pid]) if pid in acc.rushing_yards else None
                receiving = _summarize_player(name, acc.receiving_yards[pid], acc.receiving_tds[pid]) if pid in acc.receiving_yards else None
                passing = _summarize_player(name, acc.passing_yards[pid], acc.passing_tds[pid]) if pid in acc.passing_yards else None
                anytime_td_prob = 1.0
                for summary in (rushing, receiving):
                    if summary is not None:
                        anytime_td_prob *= 1 - summary["td_probability"]
                anytime_td_prob = 1 - anytime_td_prob
                out.append(
                    {
                        "player_id": pid,
                        "player_name": name,
                        "team": team,
                        "rushing": rushing,
                        "receiving": receiving,
                        "passing": passing,
                        "anytime_td_probability": anytime_td_prob,
                    }
                )
            out.sort(
                key=lambda p: (p["rushing"]["mean_yards"] if p["rushing"] else 0)
                + (p["receiving"]["mean_yards"] if p["receiving"] else 0)
                + (p["passing"]["mean_yards"] if p["passing"] else 0) * 0.3,
                reverse=True,
            )
            return out

        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "n_sims": n_sims,
            "game": game_summary,
            "usage_sample_size": self.usage_sample_size,
            "players": {
                "home": build_side(self.home_team),
                "away": build_side(self.away_team),
            },
        }

    def _team_player_ids(self, team: str) -> set[str]:
        ids = set(self.rush_samplers[team].player_ids)
        ids.update(self.target_samplers[team].player_ids)
        ids.update(self.passer_samplers[team].player_ids)
        return ids

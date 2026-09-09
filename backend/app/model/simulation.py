"""Monte Carlo game simulator: plays out a matchup thousands of times by
sampling real historical plays, to get a distribution of outcomes rather than
a single point estimate.

Why this exists
---------------
The Elo/market model produces one number per game plus a fixed +/-10 band,
which is why every score range looked roughly the same width regardless of
matchup. Simulating produces a distribution whose *shape* is specific to the
teams involved -- a plodding run offense against a stout defense genuinely
has a narrower, lower total than a pair of fast-tempo offenses, and only a
simulation shows that.

Where the numbers come from
---------------------------
Play outcomes are sampled from the real plays already stored in `plays`,
pooled as: what this offense actually does in this situation, plus what this
defense actually allows in it. That encodes both teams' quality without any
hand-tuned strength multiplier. When a pool is too thin, it backs off to the
scheme matchup, then to league-wide plays in that situation.

Drive structure -- fourth-down decisions, field goals, punts, drive starts,
clock burn -- comes from `simulation_params` (see model/sim_params.py), all
measured from real play-by-play.

What it is NOT
--------------
This does not predict injuries. nflverse play-by-play has no injury field at
all, so there is nothing to learn in-game injury events from; inventing them
would be fabrication. Availability is handled the honest way instead -- the
simulator is told who is out (from the real injury report) and a caller can
re-run with a player removed to measure that player's impact as an explicit
what-if.

Its win probability is also not automatically better than the existing model.
It is scored against Elo/market/blend on the same graded games, and until it
earns the headline it supplies distribution shape only.
"""

import random
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.model.sim_params import field_bucket, load_sim_params, togo_bucket
from app.models import Game, Play, TeamSeasonScheme

# A pool thinner than this backs off to a broader one rather than pretending
# a handful of plays describes a matchup.
MIN_POOL = 40
MAX_PLAYS_PER_DRIVE = 25  # guard against pathological loops
GAME_SECONDS = 3600


@dataclass
class PlayOutcome:
    yards: float
    touchdown: bool
    turnover: bool
    play_type: str
    # Whether the real historical play this was sampled from had a recorded
    # target (receiver_player_id). False on sacks/no-target pass plays --
    # those get zero player-level attribution (no passer/receiver credit),
    # matching how official passing yards exclude sack yardage. Meaningless
    # for run plays (always True there).
    has_target: bool = True


@dataclass
class SimulationResult:
    home_team: str
    away_team: str
    n_sims: int
    home_scores: list[int] = field(default_factory=list)
    away_scores: list[int] = field(default_factory=list)

    def summary(self) -> dict:
        n = self.n_sims
        margins = [h - a for h, a in zip(self.home_scores, self.away_scores)]
        totals = [h + a for h, a in zip(self.home_scores, self.away_scores)]
        home_wins = sum(1 for m in margins if m > 0)
        ties = sum(1 for m in margins if m == 0)

        def pct(values: list[float], p: float) -> float:
            s = sorted(values)
            return float(s[min(len(s) - 1, int(p * len(s)))])

        exact = Counter(zip(self.home_scores, self.away_scores))
        common = [
            {"home_score": h, "away_score": a, "probability": c / n}
            for (h, a), c in exact.most_common(5)
        ]

        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "n_sims": n,
            # Ties are split so this reads as a two-way price, matching how the
            # rest of the app treats win probability.
            "home_win_prob": (home_wins + ties / 2) / n,
            "mean_home_score": sum(self.home_scores) / n,
            "mean_away_score": sum(self.away_scores) / n,
            "mean_margin": sum(margins) / n,
            "mean_total": sum(totals) / n,
            "margin_p10": pct(margins, 0.10),
            "margin_p25": pct(margins, 0.25),
            "margin_p50": pct(margins, 0.50),
            "margin_p75": pct(margins, 0.75),
            "margin_p90": pct(margins, 0.90),
            "total_p10": pct(totals, 0.10),
            "total_p25": pct(totals, 0.25),
            "total_p50": pct(totals, 0.50),
            "total_p75": pct(totals, 0.75),
            "total_p90": pct(totals, 0.90),
            "most_likely_scores": common,
        }

    def prob_total_over(self, line: float) -> float:
        totals = [h + a for h, a in zip(self.home_scores, self.away_scores)]
        over = sum(1 for t in totals if t > line)
        push = sum(1 for t in totals if t == line)
        return (over + push / 2) / self.n_sims

    def prob_home_cover(self, spread: float) -> float:
        """`spread` is the home team's handicap, e.g. -3.5 for a 3.5pt favourite."""
        margins = [h - a for h, a in zip(self.home_scores, self.away_scores)]
        covered = sum(1 for m in margins if m + spread > 0)
        push = sum(1 for m in margins if m + spread == 0)
        return (covered + push / 2) / self.n_sims


def situation_key(down: int, ydstogo: float, yardline_100: float) -> str:
    return f"{down}|{togo_bucket(ydstogo)}|{field_bucket(yardline_100)}"


class PlayLibrary:
    """Loads every run/pass play once and indexes it by team, scheme and
    situation.

    Built separately from MatchupPools so a backtest over hundreds of games
    pays the ~176k-row load a single time. Every index holds references to the
    same PlayOutcome objects, so the extra indexes cost pointers, not copies.
    """

    def __init__(self, db: Session, seasons: list[int]):
        self.league: dict[str, list[PlayOutcome]] = {}
        # Keyed by (team, is_home) -- see the join comment below.
        self.by_offense: dict[tuple[str, bool], dict[str, list[PlayOutcome]]] = {}
        self.by_defense: dict[tuple[str, bool], dict[str, list[PlayOutcome]]] = {}
        self.by_scheme: dict[tuple[str, str], dict[str, list[PlayOutcome]]] = {}
        self.team_schemes: dict[str, tuple[str | None, str | None]] = {}

        for row in (
            db.query(TeamSeasonScheme)
            .filter(TeamSeasonScheme.season.in_(seasons))
            .order_by(TeamSeasonScheme.season.asc())
            .all()
        ):
            # Later seasons overwrite earlier ones, leaving each team on its
            # most recent scheme.
            self.team_schemes[row.team_abbr] = (row.offense_scheme_id, row.defense_scheme_id)

        # Joined to games so each play knows whether the offense was at home.
        # Pools are then keyed by (team, is_home): pooling a team's home and
        # road plays together averages home-field advantage away entirely,
        # which showed up in backtesting as the simulator under-predicting the
        # home margin by almost exactly the real HFA. Splitting the pools lets
        # the advantage come from the data instead of a hand-set constant.
        rows = (
            db.query(
                Play.posteam, Play.defteam, Play.play_type, Play.down, Play.ydstogo,
                Play.yardline_100, Play.yards_gained, Play.touchdown, Play.interception,
                Play.fumble_lost, Play.offense_scheme_id, Play.defense_scheme_id,
                Play.receiver_player_id,
                Game.home_team,
            )
            .join(Game, Play.game_id == Game.game_id)
            .filter(
                Play.season.in_(seasons),
                Play.down.isnot(None),
                Play.yardline_100.isnot(None),
                Play.play_type.in_(["run", "pass"]),
            )
            .all()
        )

        for r in rows:
            outcome = PlayOutcome(
                yards=float(r.yards_gained or 0.0),
                touchdown=bool(r.touchdown),
                turnover=bool(r.interception or r.fumble_lost),
                play_type=r.play_type or "run",
                has_target=bool(r.play_type != "pass" or r.receiver_player_id is not None),
            )
            key = situation_key(int(r.down), float(r.ydstogo or 10), float(r.yardline_100))
            self.league.setdefault(key, []).append(outcome)
            offense_home = r.posteam == r.home_team
            if r.posteam:
                self.by_offense.setdefault((r.posteam, offense_home), {}).setdefault(key, []).append(outcome)
            if r.defteam:
                # The defense is home exactly when the offense is not.
                self.by_defense.setdefault((r.defteam, not offense_home), {}).setdefault(key, []).append(outcome)
            if r.offense_scheme_id and r.defense_scheme_id:
                self.by_scheme.setdefault(
                    (r.offense_scheme_id, r.defense_scheme_id), {}
                ).setdefault(key, []).append(outcome)


class MatchupPools:
    """Situation-keyed pools of real play outcomes for one offense against one
    defense, with backoff when a bucket is thin.

    The primary pool unions what this offense actually did with what this
    defense actually allowed, which encodes both teams' quality without any
    hand-tuned strength multiplier.
    """

    def __init__(self, library: PlayLibrary, offense: str, defense: str, offense_at_home: bool):
        self.offense = offense
        self.defense = defense
        self.library = library

        # Home/road specific pools, so home-field advantage is inherited from
        # how these teams actually performed in that setting.
        off_plays = library.by_offense.get((offense, offense_at_home), {})
        def_plays = library.by_defense.get((defense, not offense_at_home), {})
        self._matchup: dict[str, list[PlayOutcome]] = {}
        for key in set(off_plays) | set(def_plays):
            self._matchup[key] = off_plays.get(key, []) + def_plays.get(key, [])

        off_scheme = library.team_schemes.get(offense, (None, None))[0]
        def_scheme = library.team_schemes.get(defense, (None, None))[1]
        self._scheme = library.by_scheme.get((off_scheme, def_scheme), {}) if off_scheme and def_scheme else {}
        self._same_down_cache: dict[int, list[PlayOutcome]] = {}

    def sample(self, rng: random.Random, down: int, ydstogo: float, yardline_100: float) -> PlayOutcome:
        key = situation_key(down, ydstogo, yardline_100)
        for pool in (self._matchup, self._scheme, self.library.league):
            candidates = pool.get(key)
            if candidates and len(candidates) >= MIN_POOL:
                return rng.choice(candidates)

        cached = self._same_down_cache.get(down)
        if cached is None:
            cached = [
                o for k, v in self.library.league.items() if k.startswith(f"{down}|") for o in v
            ]
            self._same_down_cache[down] = cached
        if cached:
            return rng.choice(cached)
        return PlayOutcome(yards=0.0, touchdown=False, turnover=False, play_type="run")


class GameSimulator:
    def __init__(
        self,
        db: Session,
        home_team: str,
        away_team: str,
        seasons: list[int],
        seed: int | None = None,
        library: PlayLibrary | None = None,
        params: dict | None = None,
    ):
        self.home_team = home_team
        self.away_team = away_team
        self.params = params if params is not None else load_sim_params(db)
        if not self.params:
            raise RuntimeError(
                "No simulation parameters found -- run `build-sim-params` first "
                "(see model/sim_params.py)."
            )
        # A caller simulating many games (a backtest) passes one shared library
        # rather than reloading every play per matchup.
        lib = library if library is not None else PlayLibrary(db, seasons)
        self.pools = {
            home_team: MatchupPools(lib, home_team, away_team, offense_at_home=True),
            away_team: MatchupPools(lib, away_team, home_team, offense_at_home=False),
        }
        self.rng = random.Random(seed)

    # --- drive-structure helpers, all reading measured params ---

    def _fourth_down_choice(self, ydstogo: float, yardline_100: float) -> str:
        table = self.params.get("fourth_down_decisions", {})
        entry = table.get(f"{togo_bucket(ydstogo)}|{field_bucket(yardline_100)}")
        if entry is None:
            # Fall back to a sane read of the same field position at any distance.
            matches = [v for k, v in table.items() if k.endswith(f"|{field_bucket(yardline_100)}")]
            if matches:
                entry = max(matches, key=lambda m: m["n"])
        if entry is None:
            return "punt" if yardline_100 > 40 else "fg"
        roll = self.rng.random()
        if roll < entry["go"]:
            return "go"
        if roll < entry["go"] + entry["fg"]:
            return "fg"
        return "punt"

    def _field_goal_good(self, yardline_100: float) -> bool:
        distance = yardline_100 + 17  # snap + hold spot behind the line
        table = self.params.get("field_goal_make_rate", {})
        bucket = str(int(distance // 5) * 5)
        entry = table.get(bucket)
        if entry is None:
            nearby = [(abs(int(k) - int(bucket)), v) for k, v in table.items()]
            entry = min(nearby, key=lambda t: t[0])[1] if nearby else {"make_rate": 0.75}
        return self.rng.random() < entry["make_rate"]

    def _punt_result(self, yardline_100: float) -> float:
        table = self.params.get("punt_result", {})
        entry = table.get(field_bucket(yardline_100))
        if entry is None:
            return max(1.0, min(99.0, 100.0 - (yardline_100 - 40)))
        spread = [entry["p25"], entry["p50"], entry["p75"]]
        return float(max(1.0, min(99.0, self.rng.choice(spread))))

    def _drive_start(self, previous_result: str) -> float:
        table = self.params.get("drive_starts", {})
        entry = table.get(previous_result) or table.get("KICKOFF")
        if entry is None:
            return 75.0
        return float(self.rng.choice([entry["p25"], entry["p50"], entry["p75"]]))

    def _seconds(self, play_type: str) -> float:
        spp = self.params.get("clock_and_scoring", {}).get("seconds_per_play", {})
        return float(spp.get(play_type, 30.0))

    def _touchdown_points(self) -> int:
        xp = self.params.get("clock_and_scoring", {}).get("extra_point_make_rate", 0.94)
        return 7 if self.rng.random() < xp else 6

    # --- the drive itself ---

    def _simulate_drive(
        self,
        offense: str,
        start_yardline: float,
        time_remaining: float,
        on_play=None,
    ) -> tuple[int, int, str, float, float]:
        """Returns (offense_points, defense_points, drive_result, elapsed, next_start).

        `time_remaining` is what's left in the *half*. A drive that runs out of
        clock ends scoreless: roughly 7.4% of real drives end that way, and a
        simulator without halves instead lets them play out and score at the
        normal rate, which inflated totals by about six points a game.

        `on_play`, if given, is called as `on_play(offense, outcome, scored)`
        once for every real down-play sampled (not on the FG/punt actions
        themselves) -- `scored` reflects whether *this* play produced the
        drive's touchdown, which can differ from `outcome.touchdown` because a
        real historical play's yardage is being replayed from a different
        starting field position than where it actually happened. This is how
        model/player_sim.py attributes simulated yards to specific players
        without touching the play-outcome sampling that was already validated.
        """
        pools = self.pools[offense]
        yardline = start_yardline
        down, togo = 1, 10.0
        elapsed = 0.0

        for _ in range(MAX_PLAYS_PER_DRIVE):
            if elapsed >= time_remaining:
                return 0, 0, "END_OF_HALF", elapsed, 0.0
            if down == 4:
                choice = self._fourth_down_choice(togo, yardline)
                if choice == "fg":
                    elapsed += self._seconds("field_goal")
                    if self._field_goal_good(yardline):
                        return 3, 0, "FIELD_GOAL", elapsed, self._drive_start("FIELD_GOAL")
                    return 0, 0, "MISSED_FG", elapsed, 100.0 - yardline
                if choice == "punt":
                    elapsed += self._seconds("punt")
                    return 0, 0, "PUNT", elapsed, self._punt_result(yardline)
                # else: go for it, fall through and run a normal play

            outcome = pools.sample(self.rng, down, togo, yardline)
            elapsed += self._seconds(outcome.play_type)

            if outcome.turnover:
                if on_play:
                    on_play(offense, outcome, False)
                td_rate = self.params.get("clock_and_scoring", {}).get("turnover_return_td_rate", 0.0)
                if self.rng.random() < td_rate:
                    return 0, self._touchdown_points(), "TURNOVER_TD", elapsed, self._drive_start("TOUCHDOWN")
                return 0, 0, "TURNOVER", elapsed, 100.0 - yardline

            yardline -= outcome.yards
            scored = yardline <= 0 or outcome.touchdown
            if on_play:
                on_play(offense, outcome, scored)
            if scored:
                return self._touchdown_points(), 0, "TOUCHDOWN", elapsed, self._drive_start("TOUCHDOWN")
            if yardline >= 100:
                return 0, 2, "SAFETY", elapsed, 65.0

            togo -= outcome.yards
            if togo <= 0:
                down, togo = 1, min(10.0, yardline)
            else:
                down += 1
                if down > 4:
                    return 0, 0, "DOWNS", elapsed, 100.0 - yardline

        return 0, 0, "DOWNS", elapsed, 100.0 - yardline

    def simulate(self, n_sims: int = 10000, on_play=None) -> SimulationResult:
        """`on_play`, if given, is called as `on_play(sim_index, offense,
        outcome, scored)` for every real down-play across every simulated
        game -- `sim_index` (0..n_sims-1) is what lets a caller (see
        model/player_sim.py) accumulate one total per player *per simulated
        game*, which is what produces a real distribution instead of a single
        running sum."""
        result = SimulationResult(self.home_team, self.away_team, n_sims)

        for sim_index in range(n_sims):
            score = {self.home_team: 0, self.away_team: 0}
            # Coin toss decides who receives the opening kickoff; the other team
            # receives to start the second half. Halves are simulated separately
            # so the clock can actually kill a drive, as it does in real games.
            first_receiver = self.rng.choice([self.home_team, self.away_team])
            per_play = (lambda off, outcome, scored: on_play(sim_index, off, outcome, scored)) if on_play else None

            for half in (0, 1):
                clock = float(GAME_SECONDS) / 2
                offense = first_receiver if half == 0 else (
                    self.away_team if first_receiver == self.home_team else self.home_team
                )
                next_start = self._drive_start("KICKOFF")

                while clock > 0:
                    defense = self.away_team if offense == self.home_team else self.home_team
                    off_pts, def_pts, drive_result, elapsed, next_start = self._simulate_drive(
                        offense, next_start, clock, on_play=per_play
                    )
                    score[offense] += off_pts
                    score[defense] += def_pts
                    clock -= elapsed
                    if drive_result == "END_OF_HALF":
                        break
                    offense = defense

            result.home_scores.append(score[self.home_team])
            result.away_scores.append(score[self.away_team])

        return result


def simulate_matchup(
    db: Session,
    home_team: str,
    away_team: str,
    seasons: list[int],
    n_sims: int = 10000,
    seed: int | None = None,
) -> dict:
    sim = GameSimulator(db, home_team, away_team, seasons, seed=seed)
    return sim.simulate(n_sims).summary()


def distribution_for_display(summary: dict, model_total: float | None) -> dict:
    """Picks the better centre for each quantity, then hangs the simulator's
    spread off it.

    This split is not a preference, it is what the backtest said (544 games,
    `validate-simulator`):

      * Totals -- the simulator runs about 2.4 points hot, and the model's
        total is the more accurate central estimate (MAE 9.77 vs 10.75).
        Re-centring the simulated spread on the model's total moves interval
        coverage from 77.8% to 81.3% against an 80% target.
      * Margin -- the opposite. The simulator's own margin is *more* accurate
        than the model's (MAE 10.90 vs 11.92), and anchoring it to the model
        drags coverage down from 80.5% to 75.8%. So the simulated margin keeps
        its own centre.

    In both cases the *width* comes from the simulation, which is the part it
    is demonstrably good at. Win probability is deliberately not produced here
    -- the simulator's Brier (0.245) is the worst of the four sources, so the
    headline stays with the measured-best one.
    """
    out = dict(summary)
    out["margin_center_source"] = "simulation"
    out["total_center_source"] = "simulation"

    if model_total is not None:
        shift = model_total - summary["mean_total"]
        for key in ("total_p10", "total_p25", "total_p50", "total_p75", "total_p90"):
            out[key] = summary[key] + shift
        out["mean_total"] = model_total
        out["total_center_source"] = "model"

    return out

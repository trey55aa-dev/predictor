interface Props {
  season: number;
  week: number;
  onSeasonChange: (season: number) => void;
  onWeekChange: (week: number) => void;
}

const WEEKS = Array.from({ length: 18 }, (_, i) => i + 1);
const SEASONS = [2026, 2025, 2024, 2023];

export default function WeekSelector({ season, week, onSeasonChange, onWeekChange }: Props) {
  return (
    <div className="week-selector">
      <label>
        Season
        <select value={season} onChange={(e) => onSeasonChange(Number(e.target.value))}>
          {SEASONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        Week
        <select value={week} onChange={(e) => onWeekChange(Number(e.target.value))}>
          {WEEKS.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

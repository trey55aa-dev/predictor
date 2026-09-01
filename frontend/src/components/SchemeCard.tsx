import type { SchemeFamily } from "../types";

interface Props {
  family: SchemeFamily;
  onSelect: (id: string) => void;
}

export default function SchemeCard({ family, onSelect }: Props) {
  return (
    <button className="scheme-card" onClick={() => onSelect(family.id)}>
      <div className="scheme-card-header">
        <span className="scheme-name">{family.name}</span>
        <span className={`side-badge ${family.side}`}>{family.side}</span>
      </div>
      <p className="scheme-era">{family.era}</p>
      <p className="scheme-description">{family.description}</p>
      <div className="scheme-footer">
        <span>{family.team_season_count} team-seasons (2021-2025)</span>
      </div>
    </button>
  );
}

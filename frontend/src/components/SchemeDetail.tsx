import type { SchemeDetail as SchemeDetailType } from "../types";

interface Props {
  detail: SchemeDetailType;
  onBack: () => void;
}

function groupTeamSeasons(teamSeasons: SchemeDetailType["team_seasons"]) {
  const byTeam = new Map<string, number[]>();
  for (const ts of teamSeasons) {
    const seasons = byTeam.get(ts.team_abbr) ?? [];
    seasons.push(ts.season);
    byTeam.set(ts.team_abbr, seasons);
  }
  return Array.from(byTeam.entries()).map(([team, seasons]) => ({
    team,
    seasons: seasons.sort((a, b) => b - a),
    coach: teamSeasons.find((ts) => ts.team_abbr === team && ts.season === Math.max(...seasons))?.head_coach ?? "",
  }));
}

export default function SchemeDetail({ detail, onBack }: Props) {
  const teams = groupTeamSeasons(detail.team_seasons);
  const notablePlaysLabel = detail.side === "offense" ? "Notable scoring plays" : "Notable stops";

  return (
    <div className="scheme-detail">
      <button className="back-link" onClick={onBack}>
        ← Back to systems
      </button>

      <header className="scheme-detail-header">
        <span className={`side-badge ${detail.side}`}>{detail.side}</span>
        <h2>{detail.name}</h2>
        <p className="scheme-era">{detail.era}</p>
        <p className="scheme-description">{detail.description}</p>
      </header>

      {detail.core_concepts.length > 0 && (
        <div className="core-concepts">
          {detail.core_concepts.map((c) => (
            <span key={c} className="concept-pill">
              {c}
            </span>
          ))}
        </div>
      )}

      {teams.length > 0 && (
        <section className="detail-section">
          <h3>Teams in this tree (2021-2025)</h3>
          <div className="team-chip-list">
            {teams.map((t) => (
              <span key={t.team} className="team-chip">
                <strong>{t.team}</strong> {t.seasons.join(", ")} ({t.coach})
              </span>
            ))}
          </div>
        </section>
      )}

      {detail.top_concepts.length > 0 && (
        <section className="detail-section">
          <h3>Common plays ({detail.sample_size.toLocaleString()} plays analyzed)</h3>
          <table className="concept-table">
            <thead>
              <tr>
                <th>Play concept</th>
                <th>Frequency</th>
                <th>Share</th>
              </tr>
            </thead>
            <tbody>
              {detail.top_concepts.map((c) => (
                <tr key={c.label}>
                  <td>{c.label}</td>
                  <td>{c.count.toLocaleString()}</td>
                  <td>
                    <div className="share-bar-wrap">
                      <div className="share-bar" style={{ width: `${Math.min(c.share * 100 * 4, 100)}%` }} />
                      <span>{(c.share * 100).toFixed(1)}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {detail.notable_plays.length > 0 && (
        <section className="detail-section">
          <h3>{notablePlaysLabel}</h3>
          <div className="notable-play-list">
            {detail.notable_plays.map((p) => (
              <div key={`${p.game_id}-${p.desc}`} className="notable-play">
                <div className="notable-play-header">
                  <span className="notable-play-matchup">
                    {p.season} Wk{p.week}: {p.posteam} vs {p.defteam}
                  </span>
                  <span className="notable-play-concept">{p.concept}</span>
                </div>
                <p className="notable-play-desc">{p.desc}</p>
                <p className="notable-play-explanation">Why it worked: {p.explanation}</p>
                {p.epa !== null && <p className="notable-play-epa">EPA: {p.epa.toFixed(2)}</p>}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

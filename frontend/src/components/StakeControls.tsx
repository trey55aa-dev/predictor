import { STAKE_PRESETS } from "../utils/payout";

export interface StakeSettings {
  stake: number;
  boostPct: number;
  freeBet: boolean;
}

interface Props {
  value: StakeSettings;
  onChange: (next: StakeSettings) => void;
}

export default function StakeControls({ value, onChange }: Props) {
  return (
    <div className="stake-controls">
      <div className="stake-control-group">
        <span className="stake-control-label">Stake</span>
        <div className="stake-buttons">
          {STAKE_PRESETS.map((amount) => (
            <button
              key={amount}
              type="button"
              className={`stake-button ${value.stake === amount ? "active" : ""}`}
              onClick={() => onChange({ ...value, stake: amount })}
            >
              ${amount}
            </button>
          ))}
        </div>
      </div>

      <div className="stake-control-group">
        <label className="stake-control-label" htmlFor="boost-input">
          Profit boost
        </label>
        <div className="boost-input-wrap">
          <input
            id="boost-input"
            type="number"
            min={0}
            max={500}
            step={5}
            value={value.boostPct}
            onChange={(e) => {
              const parsed = Number(e.target.value);
              onChange({ ...value, boostPct: Number.isFinite(parsed) ? Math.max(0, parsed) : 0 });
            }}
          />
          <span>%</span>
        </div>
      </div>

      <div className="stake-control-group">
        <label className="stake-checkbox">
          <input
            type="checkbox"
            checked={value.freeBet}
            onChange={(e) => onChange({ ...value, freeBet: e.target.checked })}
          />
          <span>Free bet (stake not returned)</span>
        </label>
      </div>
    </div>
  );
}

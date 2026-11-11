import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class SepConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    s1_pfu: float
    s2_pfu: float
    s3_pfu: float
    s4_pfu: float
    s5_pfu: float
    suit_threshold_mev: float
    p30_event_pfu: float
    p100_event_pfu: float
    cutoff_constant_gv: float
    cutoff_offset_gv: float
    proton_rest_energy_mev: float
    dst_suppression_deg_per_nt: float | None = None
    kp_suppression_deg_per_kp: float | None = None
    kp_suppression_verified: bool = False
    kp_assumed_when_missing: float
    forecast_horizon_hours: float
    proton_outlook_threshold_percent: float
    donki_p10_hold_hours: float
    donki_p100_hold_hours: float
    donki_end_samples: int
    donki_floor_hours: float
    suit_shielding_g_cm2: float | None = None
    dose_coefficients: str | None = None
    sources: dict[str, str]


def load_sep_config(path: Path = Path("config/sep_line.toml")) -> SepConfig:
    with path.open("rb") as fh:
        return SepConfig(**tomllib.load(fh))

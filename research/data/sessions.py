"""
Session metadata tagger.

Tags each bar with trading session information:
- session: asia, london, ny
- is_overlap: asia_london, london_ny
- is_weekend: bool

All based on UTC timestamps.
"""
import pandas as pd
import numpy as np
from research.config.settings import SESSIONS, OVERLAP_WINDOWS, WEEKEND_DAYS


def tag_sessions(df: pd.DataFrame, ts_col: str = "timestamp") -> pd.DataFrame:
    """
    Add session columns to a DataFrame with Unix ms timestamps.

    Returns DataFrame with added columns:
    - session: primary session name (asia/london/ny/off)
    - session_asia: bool
    - session_london: bool
    - session_ny: bool
    - is_overlap_asia_london: bool
    - is_overlap_london_ny: bool
    - is_weekend: bool
    """
    df = df.copy()

    # Convert to datetime for hour extraction
    dt = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    hour = dt.dt.hour
    dow = dt.dt.dayofweek  # Monday=0, Sunday=6

    # Tag individual sessions
    for name, (start_h, end_h) in SESSIONS.items():
        if start_h < end_h:
            df[f"session_{name}"] = (hour >= start_h) & (hour < end_h)
        else:
            # Wraps midnight
            df[f"session_{name}"] = (hour >= start_h) | (hour < end_h)

    # Tag overlaps
    for name, (start_h, end_h) in OVERLAP_WINDOWS.items():
        df[f"is_overlap_{name}"] = (hour >= start_h) & (hour < end_h)

    # Weekend
    df["is_weekend"] = dow.isin(WEEKEND_DAYS)

    # Primary session (priority: ny > london > asia > off)
    conditions = [
        df["session_ny"],
        df["session_london"],
        df["session_asia"],
    ]
    choices = ["ny", "london", "asia"]
    df["session"] = np.select(conditions, choices, default="off")

    return df


def get_session_at_time(timestamp_ms: int) -> str:
    """Get the primary session for a given timestamp."""
    dt = pd.Timestamp(timestamp_ms, unit="ms", tz="UTC")
    hour = dt.hour

    for name, (start_h, end_h) in SESSIONS.items():
        if start_h < end_h:
            if start_h <= hour < end_h:
                return name
        else:
            if hour >= start_h or hour < end_h:
                return name

    return "off"

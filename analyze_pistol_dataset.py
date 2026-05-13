# analyze_pistol_dataset.py

# Goal:
# Create a clean Excel workbook for RRQ / Pacific pistol review.
#
# Inputs:
# - vlr_stage1_pistol_dataset.csv
# - rrq_manual_pistol_review_clean.csv if it exists
# - rrq_auto_pistol_review.csv as a legacy fallback
#
# Outputs:
# - rrq_cleaned_analysis_workbook.xlsx
# - manual_pistol_review_template.csv
# - rrq_pistol_rows.csv
# - rrq_pistol_round_level_dataset.csv
# - rrq_data_issues.csv
# - verification_report.txt
#
# Major updates:
# - Supports plant_time
# - Supports RRQ tactical side/context tags
# - Removes old space_or_execute columns from workbook display
# - Adds Data Issues sheet
# - Adds round-level dataset for future visuals
# - Keeps old fields safe and does not overwrite filled manual cells

import os
import re

import numpy as np
import pandas as pd


# ==============================
# FILE SETTINGS
# ==============================

INPUT_CSV = "rrq_manual_pistol_review_clean.csv"
MANUAL_REVIEW_CSV = "rrq_manual_pistol_review_clean.csv"
OLD_AUTO_REVIEW_CSV = "rrq_auto_pistol_review.csv"
AUTO_REVIEW_CSV = OLD_AUTO_REVIEW_CSV  # legacy alias for older helper scripts

OUTPUT_XLSX = "rrq_cleaned_analysis_workbook.xlsx"

MANUAL_TEMPLATE_CSV = "manual_pistol_review_template.csv"
RRQ_ROWS_CSV = "rrq_pistol_rows.csv"
RRQ_ROUND_LEVEL_CSV = "rrq_pistol_round_level_dataset.csv"
DATA_ISSUES_CSV = "rrq_data_issues.csv"

RRQ_SUMMARY_BY_MAP_CSV = "rrq_summary_by_map.csv"
PACIFIC_SUMMARY_BY_MAP_CSV = "pacific_summary_by_map.csv"
COMP_SUMMARY_CSV = "comp_summary.csv"
VERIFICATION_REPORT = "verification_report.txt"

RRQ_TEAM_NAME = "Rex Regum Qeon"
PLAYOFF_PHASE_TAGS = ["lr1", "lr2", "lr3", "ur1", "ur2", "ubsf", "ubf", "gf", "lf"]
PLAYOFF_PHASE_TAG_PATTERN = re.compile(
    r"(^|[^a-z0-9])(" + "|".join(PLAYOFF_PHASE_TAGS) + r")($|[^a-z0-9])",
    re.IGNORECASE,
)


def has_playoff_phase_tag(value):
    text = str(value).strip().lower()
    if not text or text == "nan":
        return False
    return PLAYOFF_PHASE_TAG_PATTERN.search(text) is not None


def infer_event_phase(row):
    current_phase = str(row.get("event_phase", "")).strip()
    searchable_values = [
        current_phase,
        row.get("match_slug", ""),
        row.get("match_url", ""),
        row.get("vod_url", ""),
        row.get("event", ""),
    ]

    if any(has_playoff_phase_tag(value) for value in searchable_values):
        return "Playoffs"

    current_phase_lower = current_phase.lower()
    if current_phase_lower in ["playoffs", "playoff", "bracket"]:
        return "Playoffs"
    if current_phase_lower in ["group stage", "groups", "group", "swiss"]:
        return "Group Stage"
    if current_phase and current_phase_lower != "nan":
        return current_phase
    return "Group Stage"


# ==============================
# TIME HELPERS
# ==============================

def time_remaining_to_seconds_remaining(value):
    if pd.isna(value):
        return np.nan

    raw = str(value).strip().lower()

    if not raw:
        return np.nan

    raw = raw.replace(" ", "")

    if raw.endswith("secs"):
        raw = raw[:-4]
    elif raw.endswith("sec"):
        raw = raw[:-3]
    elif raw.endswith("s"):
        raw = raw[:-1]

    raw = raw.replace(".", ":")
    raw = raw.replace(";", ":")

    if ":" in raw:
        parts = raw.split(":")

        if len(parts) != 2:
            return np.nan

        minutes_text, seconds_text = parts

        if not minutes_text.isdigit() or not seconds_text.isdigit():
            return np.nan

        minutes = int(minutes_text)
        seconds = int(seconds_text)

        if minutes < 0 or seconds < 0 or seconds >= 60:
            return np.nan

        total = minutes * 60 + seconds

        if total > 100:
            return np.nan

        return total

    if raw.isdigit():
        if len(raw) == 3 and raw[0] in ["0", "1"]:
            minutes = int(raw[0])
            seconds = int(raw[1:])

            if seconds >= 60:
                return np.nan

            total = minutes * 60 + seconds

            if total > 100:
                return np.nan

            return total

        number = int(raw)

        if 0 <= number <= 100:
            return number

    return np.nan


def time_remaining_to_seconds_into_round(value):
    seconds_remaining = time_remaining_to_seconds_remaining(value)

    if pd.isna(seconds_remaining):
        return np.nan

    return 100 - seconds_remaining


# ==============================
# BASIC CLEANING HELPERS
# ==============================

def get_map_column_name(df):
    if "map_played" in df.columns:
        return "map_played"

    return "map_name"


def clean_blank_strings(df):
    for column in df.columns:
        if df[column].dtype == "object" or str(df[column].dtype).startswith("string"):
            df[column] = df[column].fillna("")

    return df


def normalize_merge_key_columns(df):
    for column in ["match_id", "map_number", "map_played", "team"]:
        if column in df.columns:
            df[column] = df[column].astype(str).str.strip()

    if "map_number" in df.columns:
        df["map_number"] = df["map_number"].str.replace(r"\.0$", "", regex=True)

    return df


def fill_blank_values(base_series, new_series):
    base_text = base_series.astype(str).str.strip()

    blank_mask = (
        base_series.isna()
        | base_text.eq("")
        | base_text.str.lower().eq("nan")
    )

    return base_series.where(~blank_mask, new_series)


def ensure_player_columns(df):
    for i in range(1, 6):
        column = f"player_{i}"

        if column not in df.columns:
            df[column] = ""

    if "players" not in df.columns:
        player_cols = [f"player_{i}" for i in range(1, 6)]
        df["players"] = df[player_cols].apply(
            lambda row: ", ".join([str(x).strip() for x in row if str(x).strip()]),
            axis=1,
        )

    return df


def ensure_agent_columns(df):
    for i in range(1, 6):
        column = f"agent_{i}"

        if column not in df.columns:
            df[column] = ""

    if "agents" not in df.columns:
        agent_cols = [f"agent_{i}" for i in range(1, 6)]
        df["agents"] = df[agent_cols].apply(
            lambda row: ", ".join([str(x).strip() for x in row if str(x).strip()]),
            axis=1,
        )

    return df


# ==============================
# COLUMN SETUP
# ==============================

def ensure_manual_columns(df):
    manual_columns = {
        "manual_round_1_team_side": "",
        "round_1_first_contact_time": "",
        "round_1_first_contact_seconds_into_round": "",
        "manual_round_1_result": "",
        "round_1_fb_fd": "",

        "manual_round_13_team_side": "",
        "round_13_first_contact_time": "",
        "round_13_first_contact_seconds_into_round": "",
        "manual_round_13_result": "",
        "round_13_fb_fd": "",

        "manual_attack_pistol_result": "",
        "manual_defense_pistol_result": "",
    }

    for column, default in manual_columns.items():
        if column not in df.columns:
            df[column] = default

    if "vod_url" not in df.columns:
        df["vod_url"] = ""

    if "match_url" not in df.columns:
        df["match_url"] = ""

    if "event_phase" not in df.columns:
        df["event_phase"] = ""

    if "map_played" not in df.columns and "map_name" in df.columns:
        df["map_played"] = df["map_name"]

    df = ensure_player_columns(df)
    df = ensure_agent_columns(df)

    return df


def ensure_auto_review_columns(df):
    auto_columns = {}

    for round_label in ["round_1", "round_13"]:
        auto_columns.update(
            {
                f"auto_{round_label}_first_contact_time": "",
                f"auto_{round_label}_fb_fd": "",
                f"auto_{round_label}_killer": "",
                f"auto_{round_label}_victim": "",
                f"auto_{round_label}_killer_team": "",
                f"auto_{round_label}_victim_team": "",
                f"auto_{round_label}_confidence": "",
                f"auto_{round_label}_review_status": "",

                f"{round_label}_first_event_site": "",
                f"{round_label}_spike_planted": "",
                f"{round_label}_plant_site": "",
                f"{round_label}_plant_time": "",
                f"{round_label}_trade_within_3s": "",
                f"{round_label}_rrq_side_context": "",
                f"{round_label}_tactical_tags": "",
                f"{round_label}_notes": "",
            }
        )

    for column, default in auto_columns.items():
        if column not in df.columns:
            df[column] = default

    return df


# ==============================
# MERGE MANUAL REVIEW DATA
# ==============================

def resolve_review_csv():
    if os.path.exists(MANUAL_REVIEW_CSV):
        return MANUAL_REVIEW_CSV
    if os.path.exists(OLD_AUTO_REVIEW_CSV):
        return OLD_AUTO_REVIEW_CSV
    return None


def merge_auto_review_data(df):
    review_csv = resolve_review_csv()

    if review_csv is None:
        print(f"No manual review CSV found yet: {MANUAL_REVIEW_CSV} or {OLD_AUTO_REVIEW_CSV}")
        return df

    print(f"Merging pistol review data from: {review_csv}")

    auto_df = pd.read_csv(review_csv)

    df = normalize_merge_key_columns(df)
    auto_df = normalize_merge_key_columns(auto_df)

    merge_keys = ["match_id", "map_number", "map_played", "team"]

    missing_keys = [
        col for col in merge_keys
        if col not in df.columns or col not in auto_df.columns
    ]

    if missing_keys:
        print(f"Could not merge auto-review data. Missing key columns: {missing_keys}")
        return df

    auto_columns = []

    for round_label in ["round_1", "round_13"]:
        auto_columns.extend(
            [
                f"auto_{round_label}_first_contact_time",
                f"auto_{round_label}_fb_fd",
                f"auto_{round_label}_killer",
                f"auto_{round_label}_victim",
                f"auto_{round_label}_killer_team",
                f"auto_{round_label}_victim_team",
                f"auto_{round_label}_confidence",
                f"auto_{round_label}_review_status",

                f"{round_label}_first_event_site",
                f"{round_label}_space_or_execute",  # old field, kept only if present
                f"{round_label}_spike_planted",
                f"{round_label}_plant_site",
                f"{round_label}_plant_time",
                f"{round_label}_trade_within_3s",
                f"{round_label}_rrq_side_context",
                f"{round_label}_tactical_tags",
                f"{round_label}_notes",
            ]
        )

    existing_auto_columns = [col for col in auto_columns if col in auto_df.columns]

    if not existing_auto_columns:
        print("Review CSV exists, but no review columns were found.")
        return df

    auto_subset = auto_df[merge_keys + existing_auto_columns].copy()
    auto_subset = auto_subset.drop_duplicates(subset=merge_keys, keep="last")

    merged = df.merge(
        auto_subset,
        on=merge_keys,
        how="left",
        suffixes=("", "_from_auto"),
    )

    for column in existing_auto_columns:
        auto_column = f"{column}_from_auto"

        if auto_column not in merged.columns:
            continue

        if column not in merged.columns:
            merged[column] = merged[auto_column]
        else:
            merged[column] = fill_blank_values(merged[column], merged[auto_column])

        merged = merged.drop(columns=[auto_column])

    return merged


def map_auto_review_into_manual_columns(df):
    mappings = {
        "auto_round_1_first_contact_time": "round_1_first_contact_time",
        "auto_round_1_fb_fd": "round_1_fb_fd",
        "auto_round_13_first_contact_time": "round_13_first_contact_time",
        "auto_round_13_fb_fd": "round_13_fb_fd",

        "round_1_side": "manual_round_1_team_side",
        "round_1_result": "manual_round_1_result",
        "round_13_side": "manual_round_13_team_side",
        "round_13_result": "manual_round_13_result",
    }

    for source_col, target_col in mappings.items():
        if source_col not in df.columns or target_col not in df.columns:
            continue

        df[target_col] = fill_blank_values(df[target_col], df[source_col])

    return df


# ==============================
# CALCULATED COLUMNS
# ==============================

def calculate_results(df):
    for col in [
        "manual_round_1_team_side",
        "manual_round_13_team_side",
        "manual_round_1_result",
        "manual_round_13_result",
        "manual_attack_pistol_result",
        "manual_defense_pistol_result",
    ]:
        df[col] = df[col].astype(str).str.strip()

    attack_mask_r1 = (
        df["manual_attack_pistol_result"].eq("")
        & df["manual_round_1_team_side"].str.lower().eq("attack")
        & df["manual_round_1_result"].isin(["Win", "Loss"])
    )

    defense_mask_r1 = (
        df["manual_defense_pistol_result"].eq("")
        & df["manual_round_1_team_side"].str.lower().eq("defense")
        & df["manual_round_1_result"].isin(["Win", "Loss"])
    )

    df.loc[attack_mask_r1, "manual_attack_pistol_result"] = df.loc[
        attack_mask_r1,
        "manual_round_1_result",
    ]

    df.loc[defense_mask_r1, "manual_defense_pistol_result"] = df.loc[
        defense_mask_r1,
        "manual_round_1_result",
    ]

    attack_mask_r13 = (
        df["manual_attack_pistol_result"].eq("")
        & df["manual_round_13_team_side"].str.lower().eq("attack")
        & df["manual_round_13_result"].isin(["Win", "Loss"])
    )

    defense_mask_r13 = (
        df["manual_defense_pistol_result"].eq("")
        & df["manual_round_13_team_side"].str.lower().eq("defense")
        & df["manual_round_13_result"].isin(["Win", "Loss"])
    )

    df.loc[attack_mask_r13, "manual_attack_pistol_result"] = df.loc[
        attack_mask_r13,
        "manual_round_13_result",
    ]

    df.loc[defense_mask_r13, "manual_defense_pistol_result"] = df.loc[
        defense_mask_r13,
        "manual_round_13_result",
    ]

    return df


def calculate_timing_columns(df):
    df["round_1_first_contact_seconds_into_round"] = df["round_1_first_contact_time"].apply(
        time_remaining_to_seconds_into_round
    )

    df["round_13_first_contact_seconds_into_round"] = df["round_13_first_contact_time"].apply(
        time_remaining_to_seconds_into_round
    )

    if "round_1_plant_time" in df.columns:
        df["round_1_plant_seconds_into_round"] = df["round_1_plant_time"].apply(
            time_remaining_to_seconds_into_round
        )

    if "round_13_plant_time" in df.columns:
        df["round_13_plant_seconds_into_round"] = df["round_13_plant_time"].apply(
            time_remaining_to_seconds_into_round
        )

    return df


def add_sort_columns(df):
    df["sort_rrq_first"] = np.where(df["team"].eq(RRQ_TEAM_NAME), 0, 1)
    df["sort_match_id_num"] = pd.to_numeric(df["match_id"], errors="coerce")

    return df


def sort_dataset(df):
    sort_cols = [
        col for col in ["sort_rrq_first", "sort_match_id_num", "map_number", "team_number"]
        if col in df.columns
    ]

    if sort_cols:
        df = df.sort_values(by=sort_cols).reset_index(drop=True)

    return df


# ==============================
# ROUND-LEVEL EXPORT
# ==============================

def build_rrq_round_level_dataset(df):
    rrq_df = df[df["team"].eq(RRQ_TEAM_NAME)].copy()

    rows = []

    for _, row in rrq_df.iterrows():
        for round_number, round_label in [(1, "round_1"), (13, "round_13")]:
            side = row.get(f"manual_{round_label}_team_side", "")
            result = row.get(f"manual_{round_label}_result", "")
            fb_fd = row.get(f"{round_label}_fb_fd", "")
            event_time = row.get(f"{round_label}_first_contact_time", "")

            if not any([str(result).strip(), str(fb_fd).strip(), str(event_time).strip()]):
                continue

            rows.append(
                {
                    "event": row.get("event", ""),
                    "event_phase": infer_event_phase(row),
                    "match_id": row.get("match_id", ""),
                    "match_slug": row.get("match_slug", ""),
                    "map_number": row.get("map_number", ""),
                    "map_played": row.get("map_played", ""),
                    "team": row.get("team", ""),
                    "opponent": row.get("opponent", ""),
                    "round_number": round_number,
                    "pistol_round": f"Round {round_number}",
                    "side": side,
                    "result": result,
                    "fb_fd": fb_fd,
                    "first_event_time": event_time,
                    "first_event_seconds_into_round": row.get(
                        f"{round_label}_first_contact_seconds_into_round", ""
                    ),
                    "first_event_site": row.get(f"{round_label}_first_event_site", ""),
                    "trade_within_3s": row.get(f"{round_label}_trade_within_3s", ""),
                    "spike_planted": row.get(f"{round_label}_spike_planted", ""),
                    "plant_site": row.get(f"{round_label}_plant_site", ""),
                    "plant_time": row.get(f"{round_label}_plant_time", ""),
                    "plant_seconds_into_round": row.get(f"{round_label}_plant_seconds_into_round", ""),
                    "rrq_side_context": row.get(f"{round_label}_rrq_side_context", ""),
                    "tactical_tags": row.get(f"{round_label}_tactical_tags", ""),
                    "notes": row.get(f"{round_label}_notes", ""),
                    "review_status": row.get(f"auto_{round_label}_review_status", ""),
                    "agents": row.get("agents", ""),
                    "comp_tags": row.get("comp_tags", ""),
                    "players": row.get("players", ""),
                    "match_url": row.get("match_url", ""),
                    "vod_url": row.get("vod_url", ""),
                }
            )

    return pd.DataFrame(rows)


# ==============================
# VALIDATION / DATA ISSUES
# ==============================

def value_is_blank(value):
    if pd.isna(value):
        return True

    value = str(value).strip()

    return value == "" or value.lower() == "nan"


def add_issue(issues, row, round_label, issue_type, detail, severity="Warning"):
    issues.append(
        {
            "severity": severity,
            "issue_type": issue_type,
            "detail": detail,
            "match_id": row.get("match_id", ""),
            "map_number": row.get("map_number", ""),
            "map_played": row.get("map_played", ""),
            "team": row.get("team", ""),
            "opponent": row.get("opponent", ""),
            "round": round_label.replace("_", " ").title(),
            "match_url": row.get("match_url", ""),
            "vod_url": row.get("vod_url", ""),
        }
    )


def build_data_issues(df):
    issues = []

    for _, row in df.iterrows():
        for round_label in ["round_1", "round_13"]:
            fb_fd = str(row.get(f"{round_label}_fb_fd", "")).strip()
            event_time = str(row.get(f"{round_label}_first_contact_time", "")).strip()
            status = str(row.get(f"auto_{round_label}_review_status", "")).strip()
            site = str(row.get(f"{round_label}_first_event_site", "")).strip()
            trade = str(row.get(f"{round_label}_trade_within_3s", "")).strip()
            spike = str(row.get(f"{round_label}_spike_planted", "")).strip()
            plant_site = str(row.get(f"{round_label}_plant_site", "")).strip()
            plant_time = str(row.get(f"{round_label}_plant_time", "")).strip()
            tactical_tags = str(row.get(f"{round_label}_tactical_tags", "")).strip()
            rrq_side_context = str(row.get(f"{round_label}_rrq_side_context", "")).strip()

            round_has_review = status in ["done", "done_manual"] or fb_fd in ["FB", "FD", "5v5 PP"]

            if round_has_review and value_is_blank(fb_fd):
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Missing FB/FD category",
                    "Round appears reviewed but FB/FD/5v5 PP is blank.",
                    "Error",
                )

            if fb_fd in ["FB", "FD", "5v5 PP"] and value_is_blank(event_time):
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Missing first event time",
                    "FB/FD/5v5 PP exists but first event time is blank.",
                    "Error",
                )

            if fb_fd in ["FB", "FD"] and value_is_blank(trade):
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Missing trade answer",
                    "FB/FD exists but trade within 3 seconds is blank.",
                    "Warning",
                )

            if fb_fd == "5v5 PP" and spike != "Yes":
                add_issue(
                    issues,
                    row,
                    round_label,
                    "5v5 PP spike mismatch",
                    "5v5 PP should normally have spike_planted = Yes.",
                    "Warning",
                )

            if spike == "Yes" and plant_site not in ["A", "B", "C"]:
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Missing plant site",
                    "Spike planted is Yes, but plant site is not A/B/C.",
                    "Error",
                )

            if spike == "No" and plant_site not in ["", "N/A", "nan"]:
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Plant site mismatch",
                    "Spike planted is No, but plant site has a site value.",
                    "Warning",
                )

            if spike == "Yes" and value_is_blank(plant_time):
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Missing plant time",
                    "Spike planted is Yes, but plant time is blank.",
                    "Info",
                )

            if round_has_review and value_is_blank(site):
                add_issue(
                    issues,
                    row,
                    round_label,
                    "Missing first event site",
                    "Round appears reviewed but first event site is blank.",
                    "Warning",
                )

            if row.get("team", "") == RRQ_TEAM_NAME and round_has_review:
                if value_is_blank(rrq_side_context):
                    add_issue(
                        issues,
                        row,
                        round_label,
                        "Missing RRQ side context",
                        "RRQ row is reviewed but RRQ side context is blank.",
                        "Warning",
                    )

                if value_is_blank(tactical_tags):
                    add_issue(
                        issues,
                        row,
                        round_label,
                        "Missing tactical tags",
                        "RRQ row is reviewed but tactical tags are blank.",
                        "Warning",
                    )

    issue_df = pd.DataFrame(issues)

    if issue_df.empty:
        issue_df = pd.DataFrame(
            columns=[
                "severity",
                "issue_type",
                "detail",
                "match_id",
                "map_number",
                "map_played",
                "team",
                "opponent",
                "round",
                "match_url",
                "vod_url",
            ]
        )

    return issue_df


# ==============================
# COLUMN ORDER
# ==============================

def reorder_columns_for_manual_review(df):
    wanted_columns = [
        "event",
        "event_phase",
        "match_id",
        "match_slug",
        "map_number",
        "map_played",
        "team",
        "opponent",
        "match_url",
        "vod_url",

        "players",
        "player_1",
        "player_2",
        "player_3",
        "player_4",
        "player_5",

        "agents",
        "agent_1",
        "agent_2",
        "agent_3",
        "agent_4",
        "agent_5",
        "player_agent_pairs",
        "comp_tags",

        # Round 1 clean review block
        "manual_round_1_team_side",
        "manual_round_1_result",
        "round_1_first_contact_time",
        "round_1_first_contact_seconds_into_round",
        "round_1_fb_fd",
        "round_1_trade_within_3s",
        "round_1_rrq_side_context",
        "round_1_tactical_tags",
        "round_1_first_event_site",
        "round_1_spike_planted",
        "round_1_plant_site",
        "round_1_plant_time",
        "round_1_plant_seconds_into_round",
        "round_1_notes",
        "auto_round_1_review_status",

        # Round 13 clean review block
        "manual_round_13_team_side",
        "manual_round_13_result",
        "round_13_first_contact_time",
        "round_13_first_contact_seconds_into_round",
        "round_13_fb_fd",
        "round_13_trade_within_3s",
        "round_13_rrq_side_context",
        "round_13_tactical_tags",
        "round_13_first_event_site",
        "round_13_spike_planted",
        "round_13_plant_site",
        "round_13_plant_time",
        "round_13_plant_seconds_into_round",
        "round_13_notes",
        "auto_round_13_review_status",

        # Calculated
        "manual_attack_pistol_result",
        "manual_defense_pistol_result",

        # Raw VLR pistol reference
        "round_1_side",
        "round_1_result",
        "round_13_side",
        "round_13_result",
        "attack_pistol_result",
        "defense_pistol_result",

        # OT / parse metadata
        "overtime_detected",
        "max_round_number",
        "round_25_side",
        "round_25_result",
        "round_25_winner",
        "pistol_parse_status",

        # Comp metadata
        "duelist_count",
        "controller_count",
        "initiator_count",
        "sentinel_count",
        "unknown_role_count",
        "double_duelist",
        "double_controller",
        "double_initiator",
        "double_sentinel",

        "team_number",
        "sort_rrq_first",
        "sort_match_id_num",
    ]

    existing_columns = [col for col in wanted_columns if col in df.columns]

    return df[existing_columns]


# ==============================
# SUMMARY HELPERS
# ==============================

def win_rate(series):
    valid = series[series.isin(["Win", "Loss"])]

    if len(valid) == 0:
        return np.nan

    return round((valid.eq("Win").sum() / len(valid)) * 100, 1)


def count_valid(series):
    return series.isin(["Win", "Loss"]).sum()


def win_count(series):
    return series.eq("Win").sum()


def loss_count(series):
    return series.eq("Loss").sum()


def summarize_fb_fd(series):
    valid = series[series.isin(["FB", "FD", "5v5 PP"])]

    if len(valid) == 0:
        return ""

    counts = valid.value_counts()

    return " | ".join([f"{label}: {count}" for label, count in counts.items()])


def summarize_by_map(df):
    if df.empty:
        return pd.DataFrame()

    map_col = get_map_column_name(df)

    summary = (
        df.groupby(["team", map_col])
        .agg(
            maps_played=("match_id", "count"),
            round_1_pistol_rounds=("manual_round_1_result", count_valid),
            round_1_pistol_wins=("manual_round_1_result", win_count),
            round_1_pistol_losses=("manual_round_1_result", loss_count),
            round_1_pistol_wr=("manual_round_1_result", win_rate),

            round_13_pistol_rounds=("manual_round_13_result", count_valid),
            round_13_pistol_wins=("manual_round_13_result", win_count),
            round_13_pistol_losses=("manual_round_13_result", loss_count),
            round_13_pistol_wr=("manual_round_13_result", win_rate),

            attack_pistol_rounds=("manual_attack_pistol_result", count_valid),
            attack_pistol_wins=("manual_attack_pistol_result", win_count),
            attack_pistol_losses=("manual_attack_pistol_result", loss_count),
            attack_pistol_wr=("manual_attack_pistol_result", win_rate),

            defense_pistol_rounds=("manual_defense_pistol_result", count_valid),
            defense_pistol_wins=("manual_defense_pistol_result", win_count),
            defense_pistol_losses=("manual_defense_pistol_result", loss_count),
            defense_pistol_wr=("manual_defense_pistol_result", win_rate),

            round_1_fb_fd_split=("round_1_fb_fd", summarize_fb_fd),
            round_13_fb_fd_split=("round_13_fb_fd", summarize_fb_fd),
            avg_round_1_first_event_seconds=("round_1_first_contact_seconds_into_round", "mean"),
            avg_round_13_first_event_seconds=("round_13_first_contact_seconds_into_round", "mean"),
        )
        .reset_index()
    )

    for col in ["avg_round_1_first_event_seconds", "avg_round_13_first_event_seconds"]:
        summary[col] = summary[col].round(1)

    return summary


def summarize_by_comp(df):
    if df.empty or "comp_tags" not in df.columns:
        return pd.DataFrame()

    summary = (
        df.groupby(["comp_tags"])
        .agg(
            rows=("match_id", "count"),
            attack_pistol_rounds=("manual_attack_pistol_result", count_valid),
            attack_pistol_wins=("manual_attack_pistol_result", win_count),
            attack_pistol_wr=("manual_attack_pistol_result", win_rate),
            defense_pistol_rounds=("manual_defense_pistol_result", count_valid),
            defense_pistol_wins=("manual_defense_pistol_result", win_count),
            defense_pistol_wr=("manual_defense_pistol_result", win_rate),
            round_1_fb_fd_split=("round_1_fb_fd", summarize_fb_fd),
            round_13_fb_fd_split=("round_13_fb_fd", summarize_fb_fd),
        )
        .reset_index()
        .sort_values(by="rows", ascending=False)
    )

    return summary


def build_completion_text(df, rrq_round_level_df, issue_df):
    lines = []

    rrq_df = df[df["team"].eq(RRQ_TEAM_NAME)].copy()

    total_rrq_maps = len(rrq_df)
    expected_rrq_pistols = total_rrq_maps * 2

    completed_pistols = len(
        rrq_round_level_df[
            rrq_round_level_df["fb_fd"].isin(["FB", "FD", "5v5 PP"])
        ]
    )

    lines.append("RRQ Pistol Dataset Verification Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Total rows: {len(df)}")
    lines.append(f"Unique matches: {df['match_id'].nunique()}")
    lines.append(f"RRQ map rows: {total_rrq_maps}")
    lines.append(f"Expected RRQ pistol rows: {expected_rrq_pistols}")
    lines.append(f"Completed RRQ pistol rows: {completed_pistols}/{expected_rrq_pistols}")
    lines.append("")
    lines.append("Data issue counts:")
    if issue_df.empty:
        lines.append("No issues found.")
    else:
        lines.append(issue_df["severity"].value_counts(dropna=False).to_string())
        lines.append("")
        lines.append(issue_df["issue_type"].value_counts(dropna=False).to_string())

    lines.append("")
    lines.append("RRQ FB/FD/5v5 PP split:")
    if not rrq_round_level_df.empty:
        lines.append(rrq_round_level_df["fb_fd"].value_counts(dropna=False).to_string())
    else:
        lines.append("No RRQ round-level data found.")

    lines.append("")
    lines.append("RRQ tactical tag split:")
    if not rrq_round_level_df.empty and "tactical_tags" in rrq_round_level_df.columns:
        tag_counts = (
            rrq_round_level_df["tactical_tags"]
            .fillna("")
            .astype(str)
            .str.split("|")
            .explode()
            .str.strip()
        )
        tag_counts = tag_counts[tag_counts.ne("")]
        if len(tag_counts) > 0:
            lines.append(tag_counts.value_counts().to_string())
        else:
            lines.append("No tactical tags found.")
    else:
        lines.append("No tactical tags found.")

    return "\n".join(lines)


# ==============================
# EXCEL HELPERS
# ==============================

def autosize_excel_columns(worksheet, df, max_width=42):
    for idx, column in enumerate(df.columns):
        header_len = len(str(column))

        if len(df) > 0:
            value_len = df[column].apply(lambda x: len(str(x)) if pd.notna(x) else 0).max()
        else:
            value_len = 0

        width = min(max(header_len, value_len) + 2, max_width)
        worksheet.set_column(idx, idx, width)


def add_dropdown(worksheet, df, column_name, values, first_data_row, last_data_row):
    if column_name not in df.columns:
        return

    if last_data_row < first_data_row:
        return

    col_idx = df.columns.get_loc(column_name)

    worksheet.data_validation(
        first_data_row,
        col_idx,
        last_data_row,
        col_idx,
        {
            "validate": "list",
            "source": values,
            "input_message": "Select from list.",
        },
    )


def make_links_clickable(worksheet, workbook, df, first_data_row):
    link_format = workbook.add_format({
        "font_color": "blue",
        "underline": True,
    })

    for row_idx in range(len(df)):
        excel_row = first_data_row + row_idx

        if "match_url" in df.columns:
            col_idx = df.columns.get_loc("match_url")
            url = str(df.iloc[row_idx]["match_url"]).strip()

            if url and url.lower() != "nan":
                worksheet.write_url(excel_row, col_idx, url, link_format, string="VLR.GG")

        if "vod_url" in df.columns:
            col_idx = df.columns.get_loc("vod_url")
            url = str(df.iloc[row_idx]["vod_url"]).strip()

            if url and url.lower() != "nan":
                worksheet.write_url(excel_row, col_idx, url, link_format, string="VOD Link")


def apply_dropdowns(worksheet, df, first_data_row, last_data_row):
    side_values = ["Attack", "Defense"]
    result_values = ["Win", "Loss"]
    fb_fd_values = ["FB", "FD", "5v5 PP", "None"]
    site_values = ["A", "B", "C", "Mid", "Other"]
    plant_site_values = ["A", "B", "C", "N/A"]
    yes_no_values = ["Yes", "No"]
    rrq_side_values = ["Attack", "Defense"]

    for column_name in ["manual_round_1_team_side", "manual_round_13_team_side"]:
        add_dropdown(worksheet, df, column_name, side_values, first_data_row, last_data_row)

    for column_name in ["manual_round_1_result", "manual_round_13_result"]:
        add_dropdown(worksheet, df, column_name, result_values, first_data_row, last_data_row)

    for column_name in ["round_1_fb_fd", "round_13_fb_fd"]:
        add_dropdown(worksheet, df, column_name, fb_fd_values, first_data_row, last_data_row)

    for column_name in ["round_1_first_event_site", "round_13_first_event_site"]:
        add_dropdown(worksheet, df, column_name, site_values, first_data_row, last_data_row)

    for column_name in ["round_1_spike_planted", "round_13_spike_planted"]:
        add_dropdown(worksheet, df, column_name, yes_no_values, first_data_row, last_data_row)

    for column_name in ["round_1_plant_site", "round_13_plant_site"]:
        add_dropdown(worksheet, df, column_name, plant_site_values, first_data_row, last_data_row)

    for column_name in ["round_1_trade_within_3s", "round_13_trade_within_3s"]:
        add_dropdown(worksheet, df, column_name, yes_no_values, first_data_row, last_data_row)

    for column_name in ["round_1_rrq_side_context", "round_13_rrq_side_context"]:
        add_dropdown(worksheet, df, column_name, rrq_side_values, first_data_row, last_data_row)


def apply_result_conditional_formatting(worksheet, workbook, df, first_data_row, last_data_row):
    if last_data_row < first_data_row:
        return

    win_format = workbook.add_format({"bg_color": "#DCFCE7", "font_color": "#166534"})
    loss_format = workbook.add_format({"bg_color": "#FEE2E2", "font_color": "#991B1B"})
    postplant_format = workbook.add_format({"bg_color": "#DBEAFE", "font_color": "#1E3A8A"})

    for col_name in [
        "manual_round_1_result",
        "manual_round_13_result",
        "manual_attack_pistol_result",
        "manual_defense_pistol_result",
        "result",
    ]:
        if col_name not in df.columns:
            continue

        col_idx = df.columns.get_loc(col_name)

        worksheet.conditional_format(
            first_data_row,
            col_idx,
            last_data_row,
            col_idx,
            {"type": "text", "criteria": "containing", "value": "Win", "format": win_format},
        )

        worksheet.conditional_format(
            first_data_row,
            col_idx,
            last_data_row,
            col_idx,
            {"type": "text", "criteria": "containing", "value": "Loss", "format": loss_format},
        )

    for col_name in ["round_1_fb_fd", "round_13_fb_fd", "fb_fd"]:
        if col_name not in df.columns:
            continue

        col_idx = df.columns.get_loc(col_name)

        worksheet.conditional_format(
            first_data_row,
            col_idx,
            last_data_row,
            col_idx,
            {
                "type": "text",
                "criteria": "containing",
                "value": "5v5 PP",
                "format": postplant_format,
            },
        )


def write_header_row(worksheet, workbook, df, row_number=0):
    default_header = workbook.add_format({
        "bold": True,
        "font_color": "white",
        "bg_color": "#1F2937",
        "border": 1,
        "text_wrap": True,
        "align": "center",
        "valign": "vcenter",
    })

    manual_header = workbook.add_format({
        "bold": True,
        "font_color": "black",
        "bg_color": "#FEF3C7",
        "border": 1,
        "text_wrap": True,
        "align": "center",
        "valign": "vcenter",
    })

    context_header = workbook.add_format({
        "bold": True,
        "font_color": "white",
        "bg_color": "#7C3AED",
        "border": 1,
        "text_wrap": True,
        "align": "center",
        "valign": "vcenter",
    })

    calc_header = workbook.add_format({
        "bold": True,
        "font_color": "white",
        "bg_color": "#2563EB",
        "border": 1,
        "text_wrap": True,
        "align": "center",
        "valign": "vcenter",
    })

    for col_num, col_name in enumerate(df.columns):
        if col_name.startswith("manual_") or col_name in ["round_1_fb_fd", "round_13_fb_fd"]:
            fmt = manual_header
        elif any(key in col_name for key in ["site", "plant", "trade", "tactical", "notes", "rrq_side_context"]):
            fmt = context_header
        elif "seconds_into_round" in col_name or col_name.endswith("_wr"):
            fmt = calc_header
        else:
            fmt = default_header

        worksheet.write(row_number, col_num, col_name, fmt)


def write_dataframe_sheet(writer, workbook, sheet_name, df, title=None, freeze_row=1):
    df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1 if title else 0)

    ws = writer.sheets[sheet_name]

    if title:
        title_format = workbook.add_format({
            "bold": True,
            "font_size": 16,
            "font_color": "white",
            "bg_color": "#111827",
            "align": "center",
            "valign": "vcenter",
        })

        ws.merge_range(0, 0, 0, max(len(df.columns) - 1, 0), title, title_format)
        header_row = 1
        first_data_row = 2
        last_data_row = len(df) + 1
    else:
        header_row = 0
        first_data_row = 1
        last_data_row = len(df)

    write_header_row(ws, workbook, df, row_number=header_row)
    ws.set_row(header_row, 26.25)
    ws.freeze_panes(freeze_row, 0)

    if len(df.columns) > 0:
        ws.autofilter(header_row, 0, last_data_row, len(df.columns) - 1)

    autosize_excel_columns(ws, df)
    make_links_clickable(ws, workbook, df, first_data_row)
    apply_dropdowns(ws, df, first_data_row, last_data_row)
    apply_result_conditional_formatting(ws, workbook, df, first_data_row, last_data_row)

    return ws


def write_excel_workbook(df, rrq_df, rrq_round_level_df, data_issues_df, rrq_summary, pacific_summary, comp_summary):
    with pd.ExcelWriter(OUTPUT_XLSX, engine="xlsxwriter") as writer:
        workbook = writer.book

        ws_manual = write_dataframe_sheet(
            writer,
            workbook,
            "Manual Review",
            df,
            title="Clean Pistol Review Dataset",
            freeze_row=2,
        )

        ws_rrq = write_dataframe_sheet(
            writer,
            workbook,
            "RRQ Only",
            rrq_df,
            title=None,
            freeze_row=1,
        )

        ws_round = write_dataframe_sheet(
            writer,
            workbook,
            "RRQ Round Level",
            rrq_round_level_df,
            title=None,
            freeze_row=1,
        )

        ws_issues = write_dataframe_sheet(
            writer,
            workbook,
            "Data Issues",
            data_issues_df,
            title=None,
            freeze_row=1,
        )

        write_dataframe_sheet(writer, workbook, "RRQ Summary", rrq_summary, title=None, freeze_row=1)
        write_dataframe_sheet(writer, workbook, "Pacific Summary", pacific_summary, title=None, freeze_row=1)
        write_dataframe_sheet(writer, workbook, "Comp Summary", comp_summary, title=None, freeze_row=1)

        instructions = pd.DataFrame(
            {
                "Step": list(range(1, 13)),
                "Instruction": [
                    "Manual Review contains the full clean dataset.",
                    "RRQ Only is the main review sheet for RRQ map rows.",
                    "RRQ Round Level is the best source for visuals: one row per RRQ pistol round.",
                    "Data Issues lists missing or inconsistent fields to fix before visuals.",
                    "Click VLR.GG for the match page.",
                    "Click VOD Link for the map-specific VOD timestamp.",
                    "FB means RRQ/team got first blood. FD means suffered first death.",
                    "5v5 PP means spike was planted before any kill.",
                    "Tactical tags are multi-select values from pistol_round_counter.py.",
                    "Plant time is optional but useful for post-plant timing visuals.",
                    "Old space_or_execute columns are intentionally hidden from the workbook.",
                    f"Run this analyzer again after editing {MANUAL_REVIEW_CSV}.",
                ],
            }
        )

        write_dataframe_sheet(writer, workbook, "Instructions", instructions, title=None, freeze_row=1)

        # Useful manual widths for the main sheets.
        preferred_widths = {
            "match_url": 14,
            "vod_url": 14,
            "team": 22,
            "opponent": 22,
            "players": 36,
            "agents": 38,
            "comp_tags": 42,
            "round_1_tactical_tags": 28,
            "round_13_tactical_tags": 28,
            "tactical_tags": 28,
            "round_1_notes": 36,
            "round_13_notes": 36,
            "notes": 36,
            "detail": 60,
            "Instruction": 100,
        }

        for ws, sheet_df in [
            (ws_manual, df),
            (ws_rrq, rrq_df),
            (ws_round, rrq_round_level_df),
            (ws_issues, data_issues_df),
        ]:
            for col_name, width in preferred_widths.items():
                if col_name in sheet_df.columns:
                    col_idx = sheet_df.columns.get_loc(col_name)
                    ws.set_column(col_idx, col_idx, width)


# ==============================
# OUTPUTS
# ==============================

def save_csv_outputs(df, rrq_df, rrq_round_level_df, data_issues_df, rrq_summary, pacific_summary, comp_summary):
    df.to_csv(MANUAL_TEMPLATE_CSV, index=False, encoding="utf-8-sig")
    rrq_df.to_csv(RRQ_ROWS_CSV, index=False, encoding="utf-8-sig")
    rrq_round_level_df.to_csv(RRQ_ROUND_LEVEL_CSV, index=False, encoding="utf-8-sig")
    data_issues_df.to_csv(DATA_ISSUES_CSV, index=False, encoding="utf-8-sig")
    rrq_summary.to_csv(RRQ_SUMMARY_BY_MAP_CSV, index=False, encoding="utf-8-sig")
    pacific_summary.to_csv(PACIFIC_SUMMARY_BY_MAP_CSV, index=False, encoding="utf-8-sig")
    comp_summary.to_csv(COMP_SUMMARY_CSV, index=False, encoding="utf-8-sig")


# ==============================
# MAIN
# ==============================

def main():
    print("RRQ Pistol Dataset Analyzer")
    print("=" * 60)
    print()

    df = pd.read_csv(INPUT_CSV)

    df = ensure_manual_columns(df)
    df = ensure_auto_review_columns(df)

    df = merge_auto_review_data(df)
    df = map_auto_review_into_manual_columns(df)

    df = clean_blank_strings(df)
    df["event_phase"] = df.apply(infer_event_phase, axis=1)
    df = calculate_results(df)
    df = calculate_timing_columns(df)
    df = add_sort_columns(df)
    df = sort_dataset(df)
    df = reorder_columns_for_manual_review(df)

    rrq_df = df[df["team"].eq(RRQ_TEAM_NAME)].copy()
    rrq_round_level_df = build_rrq_round_level_dataset(df)
    data_issues_df = build_data_issues(df)

    rrq_summary = summarize_by_map(rrq_df)
    pacific_summary = summarize_by_map(df)
    comp_summary = summarize_by_comp(df)

    report = build_completion_text(df, rrq_round_level_df, data_issues_df)

    with open(VERIFICATION_REPORT, "w", encoding="utf-8") as f:
        f.write(report)

    print(report)

    save_csv_outputs(
        df,
        rrq_df,
        rrq_round_level_df,
        data_issues_df,
        rrq_summary,
        pacific_summary,
        comp_summary,
    )

    write_excel_workbook(
        df,
        rrq_df,
        rrq_round_level_df,
        data_issues_df,
        rrq_summary,
        pacific_summary,
        comp_summary,
    )

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"Main Excel workbook created: {OUTPUT_XLSX}")
    print(f"Round-level visual dataset created: {RRQ_ROUND_LEVEL_CSV}")
    print(f"Data issues CSV created: {DATA_ISSUES_CSV}")
    print(f"Verification report created: {VERIFICATION_REPORT}")
    print()
    print("Workbook sheets:")
    print("- Manual Review")
    print("- RRQ Only")
    print("- RRQ Round Level")
    print("- Data Issues")
    print("- RRQ Summary")
    print("- Pacific Summary")
    print("- Comp Summary")
    print("- Instructions")
    print()
    print("Removed from workbook display:")
    print("- round_1_space_or_execute")
    print("- round_13_space_or_execute")
    print("- raw OCR/debug columns")
    print()
    print("Added to workbook/export:")
    print("- round_1_plant_time / round_13_plant_time")
    print("- round_1_rrq_side_context / round_13_rrq_side_context")
    print("- round_1_tactical_tags / round_13_tactical_tags")
    print("- rrq_pistol_round_level_dataset.csv")


if __name__ == "__main__":
    main()

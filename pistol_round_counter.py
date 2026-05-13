# pistol_round_counter.py

# Goal:
# Hotkey-driven Valorant pistol round logger.
#
# Workflow:
# - Opens RRQ map VOD
# - Reviews Round 1 and Round 13
# - You press Enter when the pistol starts at 1:40
# - While watching the VOD/browser, press:
#     1 = RRQ got First Blood
#     2 = RRQ suffered First Death
#     3 = 5v5 Post-Plant
#     q = cancel this round
# - Script calculates first event time from elapsed seconds
# - Script asks context questions
# - Script saves both team rows
#
# New:
# - Tactical context questions are now part of normal workflow.
# - Tactical-only backfill mode lets you add tactical categories to already-reviewed rounds.
#
# Output:
# rrq_auto_pistol_review.csv
#
# Inputs:
# manual_pistol_review_template.csv
# vlr_stage1_pistol_dataset.csv fallback
#
# Install:
# pip install keyboard

import os
import time
import webbrowser

import pandas as pd

try:
    import keyboard
except ImportError:
    keyboard = None


# ==============================
# SETTINGS
# ==============================

INPUT_CSV = "manual_pistol_review_template.csv"
FALLBACK_CSV = "vlr_stage1_pistol_dataset.csv"

OUTPUT_CSV = "rrq_auto_pistol_review.csv"

TEAM_TO_REVIEW = "Rex Regum Qeon"
ONLY_REVIEW_TEAM_MATCHES = True

REVIEW_ROUND_1 = True
REVIEW_ROUND_13 = True

OPEN_VOD_AUTOMATICALLY = True

PROMPT_ON_COMPLETED_ROUND = True
SKIP_DONE_MAPS = False

# Normal mode:
# False = normal hotkey/manual entry for FB/FD/5v5 PP + all questions.
#
# Tactical-only backfill mode:
# True = do not redo hotkeys, do not delete old data,
#        only ask for RRQ side/tactical tags.
TACTICAL_CONTEXT_ONLY_MODE = False

ROUND_START_SECONDS = 100  # Valorant pistol timer starts at 1:40


# ==============================
# BASIC HELPERS
# ==============================

def seconds_remaining_to_clock(seconds_remaining):
    seconds_remaining = max(0, min(ROUND_START_SECONDS, int(round(seconds_remaining))))

    minutes = seconds_remaining // 60
    seconds = seconds_remaining % 60

    return f"{minutes}:{seconds:02d}"


def first_non_blank_value(series):
    for value in series:
        if pd.isna(value):
            continue

        value = str(value).strip()

        if value and value.lower() != "nan":
            return value

    return ""


def get_map_column_name(df):
    if "map_played" in df.columns:
        return "map_played"

    if "map_name" in df.columns:
        return "map_name"

    raise ValueError("Could not find map_played or map_name column.")


def load_review_dataframe():
    if os.path.exists(OUTPUT_CSV):
        print(f"Reading existing auto-review CSV: {OUTPUT_CSV}")
        return pd.read_csv(OUTPUT_CSV)

    if os.path.exists(INPUT_CSV):
        print(f"Reading CSV: {INPUT_CSV}")
        return pd.read_csv(INPUT_CSV)

    print(f"Primary CSV not found. Reading fallback CSV: {FALLBACK_CSV}")
    return pd.read_csv(FALLBACK_CSV)


def ensure_output_columns(df):
    needed_columns = {}

    for round_label in ["round_1", "round_13"]:
        needed_columns.update(
            {
                # Main event capture
                f"auto_{round_label}_first_contact_time": "",
                f"auto_{round_label}_fb_fd": "",
                f"auto_{round_label}_killer": "",
                f"auto_{round_label}_victim": "",
                f"auto_{round_label}_killer_team": "",
                f"auto_{round_label}_victim_team": "",
                f"auto_{round_label}_ocr_text": "",
                f"auto_{round_label}_clock_ocr_raw": "",
                f"auto_{round_label}_estimated_clock_time": "",
                f"auto_{round_label}_elapsed_seconds_until_detection": "",
                f"auto_{round_label}_screenshot": "",
                f"auto_{round_label}_confidence": "",
                f"auto_{round_label}_review_status": "",

                # Context questions
                f"{round_label}_first_event_site": "",
                f"{round_label}_space_or_execute": "",
                f"{round_label}_spike_planted": "",
                f"{round_label}_plant_site": "",
                f"{round_label}_plant_time": "",
                f"{round_label}_trade_within_3s": "",
                f"{round_label}_notes": "",

                # New tactical context
                f"{round_label}_rrq_side_context": "",
                f"{round_label}_tactical_tags": "",
            }
        )

    for col, default in needed_columns.items():
        if col not in df.columns:
            df[col] = default

    for col in needed_columns.keys():
        df[col] = df[col].astype("object")

    return df


def get_group_key_columns(df):
    key_cols = []

    for col in ["match_id", "map_number"]:
        if col in df.columns:
            key_cols.append(col)

    map_col = get_map_column_name(df)

    if map_col not in key_cols:
        key_cols.append(map_col)

    return key_cols


# ==============================
# GROUPING
# ==============================

def build_map_groups(df):
    key_cols = get_group_key_columns(df)
    map_col = get_map_column_name(df)

    groups = []

    grouped = df.groupby(key_cols, dropna=False)

    for group_key, group_df in grouped:
        if isinstance(group_key, tuple):
            key_dict = dict(zip(key_cols, group_key))
        else:
            key_dict = {key_cols[0]: group_key}

        teams = (
            group_df["team"]
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .unique()
            .tolist()
        )

        if len(teams) < 2:
            continue

        match_url = first_non_blank_value(group_df["match_url"]) if "match_url" in group_df.columns else ""
        vod_url = first_non_blank_value(group_df["vod_url"]) if "vod_url" in group_df.columns else ""

        if not vod_url:
            continue

        group_info = {
            "key_cols": key_cols,
            "key_dict": key_dict,
            "indices": group_df.index.tolist(),
            "teams": teams[:2],
            "match_id": key_dict.get(
                "match_id",
                first_non_blank_value(group_df["match_id"]) if "match_id" in group_df.columns else "",
            ),
            "map_number": key_dict.get(
                "map_number",
                first_non_blank_value(group_df["map_number"]) if "map_number" in group_df.columns else "",
            ),
            "map_played": key_dict.get(
                map_col,
                first_non_blank_value(group_df[map_col]) if map_col in group_df.columns else "",
            ),
            "match_url": match_url,
            "vod_url": vod_url,
        }

        groups.append(group_info)

    groups = sorted(
        groups,
        key=lambda g: (
            str(g.get("match_id", "")),
            int(float(g.get("map_number", 999))) if str(g.get("map_number", "")).replace(".", "", 1).isdigit() else 999,
        ),
    )

    return groups


def filter_groups_to_team(groups, team_name):
    filtered_groups = []

    for group in groups:
        teams = [str(team).strip() for team in group.get("teams", [])]

        if team_name in teams:
            filtered_groups.append(group)

    return filtered_groups


# ==============================
# DONE / PROGRESS HELPERS
# ==============================

def round_is_done(df, group_info, round_label):
    status_col = f"auto_{round_label}_review_status"

    if status_col not in df.columns:
        return False

    statuses = []

    for idx in group_info["indices"]:
        status = str(df.at[idx, status_col]).strip().lower()
        statuses.append(status)

    return statuses and all(status in ["done", "done_manual"] for status in statuses)


def tactical_round_is_done(df, group_info, round_label):
    side_col = f"{round_label}_rrq_side_context"
    tags_col = f"{round_label}_tactical_tags"

    if side_col not in df.columns or tags_col not in df.columns:
        return False

    values = []

    for idx in group_info["indices"]:
        side_value = str(df.at[idx, side_col]).strip()
        tag_value = str(df.at[idx, tags_col]).strip()

        values.append(
            side_value
            and side_value.lower() != "nan"
            and tag_value
            and tag_value.lower() != "nan"
        )

    return values and all(values)


def group_is_done(df, group_info):
    checks = []

    if TACTICAL_CONTEXT_ONLY_MODE:
        if REVIEW_ROUND_1:
            checks.append(tactical_round_is_done(df, group_info, "round_1"))
        if REVIEW_ROUND_13:
            checks.append(tactical_round_is_done(df, group_info, "round_13"))
    else:
        if REVIEW_ROUND_1:
            checks.append(round_is_done(df, group_info, "round_1"))
        if REVIEW_ROUND_13:
            checks.append(round_is_done(df, group_info, "round_13"))

    return checks and all(checks)


def get_round_saved_values(df, group_info, round_label):
    rows = []

    columns_to_show = [
        "team",
        f"auto_{round_label}_first_contact_time",
        f"auto_{round_label}_fb_fd",
        f"auto_{round_label}_killer_team",
        f"auto_{round_label}_victim_team",
        f"{round_label}_first_event_site",
        f"{round_label}_space_or_execute",
        f"{round_label}_spike_planted",
        f"{round_label}_plant_site",
        f"{round_label}_plant_time",
        f"{round_label}_trade_within_3s",
        f"{round_label}_rrq_side_context",
        f"{round_label}_tactical_tags",
        f"{round_label}_notes",
        f"auto_{round_label}_review_status",
    ]

    for idx in group_info["indices"]:
        row_data = {}

        for col in columns_to_show:
            if col in df.columns:
                row_data[col] = df.at[idx, col]
            else:
                row_data[col] = ""

        rows.append(row_data)

    return pd.DataFrame(rows)


def print_round_saved_values(df, group_info, round_label, round_display):
    saved_df = get_round_saved_values(df, group_info, round_label)

    print()
    print("-" * 100)
    print(f"CURRENT SAVED VALUES: {round_display}")
    print("-" * 100)

    if saved_df.empty:
        print("No saved values found.")
    else:
        print(saved_df.to_string(index=False))

    print("-" * 100)
    print()


def round_has_any_saved_data(df, group_info, round_label):
    columns_to_check = [
        f"auto_{round_label}_first_contact_time",
        f"auto_{round_label}_fb_fd",
        f"auto_{round_label}_killer_team",
        f"auto_{round_label}_victim_team",
        f"{round_label}_first_event_site",
        f"{round_label}_space_or_execute",
        f"{round_label}_spike_planted",
        f"{round_label}_plant_site",
        f"{round_label}_plant_time",
        f"{round_label}_trade_within_3s",
        f"{round_label}_rrq_side_context",
        f"{round_label}_tactical_tags",
        f"{round_label}_notes",
        f"auto_{round_label}_review_status",
    ]

    for idx in group_info["indices"]:
        for col in columns_to_check:
            if col not in df.columns:
                continue

            value = str(df.at[idx, col]).strip()

            if value and value.lower() != "nan":
                return True

    return False


def clear_round_saved_values(df, group_info, round_label):
    columns_to_clear = [
        f"auto_{round_label}_first_contact_time",
        f"auto_{round_label}_fb_fd",
        f"auto_{round_label}_killer",
        f"auto_{round_label}_victim",
        f"auto_{round_label}_killer_team",
        f"auto_{round_label}_victim_team",
        f"auto_{round_label}_ocr_text",
        f"auto_{round_label}_clock_ocr_raw",
        f"auto_{round_label}_estimated_clock_time",
        f"auto_{round_label}_elapsed_seconds_until_detection",
        f"auto_{round_label}_screenshot",
        f"auto_{round_label}_confidence",
        f"auto_{round_label}_review_status",

        f"{round_label}_first_event_site",
        f"{round_label}_space_or_execute",
        f"{round_label}_spike_planted",
        f"{round_label}_plant_site",
        f"{round_label}_plant_time",
        f"{round_label}_trade_within_3s",
        f"{round_label}_rrq_side_context",
        f"{round_label}_tactical_tags",
        f"{round_label}_notes",
    ]

    for idx in group_info["indices"]:
        for col in columns_to_clear:
            if col in df.columns:
                df.at[idx, col] = ""

    return df


def handle_existing_round_data(df, group_info, round_label, round_display):
    has_data = round_has_any_saved_data(df, group_info, round_label)
    done = round_is_done(df, group_info, round_label)

    if not has_data:
        return "continue", df

    if not PROMPT_ON_COMPLETED_ROUND:
        if done:
            return "skip", df
        return "continue", df

    print_round_saved_values(df, group_info, round_label, round_display)

    if done:
        print(f"{round_display} already appears complete.")
    else:
        print(f"{round_display} has partial saved data.")

    while True:
        print()
        print(f"What do you want to do with {round_display}?")
        print("(A) Keep existing / skip")
        print("(B) Overwrite this round")
        print("(C) View existing values again")
        print("(D) Continue without clearing")

        answer = input("> ").strip().lower()

        if answer == "a":
            return "skip", df

        if answer == "b":
            df = clear_round_saved_values(df, group_info, round_label)
            print(f"{round_display} saved values cleared. Ready to overwrite.")
            return "continue", df

        if answer == "c":
            print_round_saved_values(df, group_info, round_label, round_display)
            continue

        if answer == "d":
            return "continue", df

        print("Invalid choice. Enter A, B, C, or D.")


def handle_existing_tactical_data(df, group_info, round_label, round_display):
    if not tactical_round_is_done(df, group_info, round_label):
        return "continue", df

    print_round_saved_values(df, group_info, round_label, round_display)
    print(f"{round_display} tactical context already appears complete.")

    while True:
        print()
        print(f"What do you want to do with {round_display} tactical context?")
        print("(A) Keep existing / skip")
        print("(B) Overwrite tactical context")
        print("(C) View existing values again")

        answer = input("> ").strip().lower()

        if answer == "a":
            return "skip", df

        if answer == "b":
            for idx in group_info["indices"]:
                df.at[idx, f"{round_label}_rrq_side_context"] = ""
                df.at[idx, f"{round_label}_tactical_tags"] = ""
            print(f"{round_display} tactical context cleared. Ready to overwrite.")
            return "continue", df

        if answer == "c":
            print_round_saved_values(df, group_info, round_label, round_display)
            continue

        print("Invalid choice. Enter A, B, or C.")


def print_progress_summary(df, groups):
    total_maps = len(groups)
    round_1_done = 0
    round_13_done = 0
    round_1_tactical_done = 0
    round_13_tactical_done = 0
    full_maps_done = 0

    missing_items = []

    for group in groups:
        match_id = group.get("match_id", "")
        map_played = group.get("map_played", "")
        teams = " vs ".join(group.get("teams", []))

        r1_done = round_is_done(df, group, "round_1")
        r13_done = round_is_done(df, group, "round_13")
        r1_tactical_done = tactical_round_is_done(df, group, "round_1")
        r13_tactical_done = tactical_round_is_done(df, group, "round_13")

        if r1_done:
            round_1_done += 1
        if r13_done:
            round_13_done += 1
        if r1_tactical_done:
            round_1_tactical_done += 1
        if r13_tactical_done:
            round_13_tactical_done += 1

        selected_done = True

        if TACTICAL_CONTEXT_ONLY_MODE:
            if REVIEW_ROUND_1 and not r1_tactical_done:
                selected_done = False
                missing_items.append(f"{match_id} | {map_played} | {teams} | missing Round 1 tactical")
            if REVIEW_ROUND_13 and not r13_tactical_done:
                selected_done = False
                missing_items.append(f"{match_id} | {map_played} | {teams} | missing Round 13 tactical")
        else:
            if REVIEW_ROUND_1 and not r1_done:
                selected_done = False
                missing_items.append(f"{match_id} | {map_played} | {teams} | missing Round 1")
            if REVIEW_ROUND_13 and not r13_done:
                selected_done = False
                missing_items.append(f"{match_id} | {map_played} | {teams} | missing Round 13")

        if selected_done:
            full_maps_done += 1

    print()
    print("=" * 100)
    print("PISTOL REVIEW PROGRESS")
    print("=" * 100)
    print(f"Mode:                     {'TACTICAL ONLY' if TACTICAL_CONTEXT_ONLY_MODE else 'FULL HOTKEY REVIEW'}")
    print(f"Total maps to review:     {total_maps}")
    print(f"Fully completed maps:     {full_maps_done}/{total_maps}")

    if REVIEW_ROUND_1:
        print(f"Round 1 event complete:   {round_1_done}/{total_maps}")
        print(f"Round 1 tactical done:    {round_1_tactical_done}/{total_maps}")

    if REVIEW_ROUND_13:
        print(f"Round 13 event complete:  {round_13_done}/{total_maps}")
        print(f"Round 13 tactical done:   {round_13_tactical_done}/{total_maps}")

    print()

    if missing_items:
        print("Still missing:")
        for item in missing_items:
            print(f"- {item}")
    else:
        print("All selected maps are fully reviewed for the current mode.")

    print("=" * 100)
    print()


# ==============================
# INPUT HELPERS
# ==============================

def ask_choice(prompt_text, choices):
    letter_lookup = {letter.lower(): label for letter, label in choices}
    label_lookup = {label.lower(): label for letter, label in choices}

    while True:
        print()
        print(prompt_text)

        for letter, label in choices:
            print(f"({letter}) {label}")

        answer = input("> ").strip()

        if answer.lower() in letter_lookup:
            return letter_lookup[answer.lower()]

        if answer.lower() in label_lookup:
            return label_lookup[answer.lower()]

        print("Invalid choice. Enter one of the listed letters.")


def ask_yes_no(prompt_text):
    while True:
        print()
        print(prompt_text)
        print("(Y) Yes")
        print("(N) No")

        answer = input("> ").strip().lower()

        if answer.startswith("y"):
            return "Yes"

        if answer.startswith("n"):
            return "No"

        print("Invalid choice. Enter Y or N.")


def ask_multi_choice(prompt_text, choices):
    """
    Lets user select multiple letters.

    Example:
    A,C
    B D
    AB
    """

    letter_lookup = {letter.lower(): label for letter, label in choices}

    while True:
        print()
        print(prompt_text)

        for letter, label in choices:
            print(f"({letter}) {label}")

        answer = input("Select one or more letters, example A,C: ").strip().lower()

        if not answer:
            print("Enter at least one choice.")
            continue

        # Accept A,C or A C or AC.
        cleaned = answer.replace(",", " ").replace("/", " ").replace("|", " ")
        parts = []

        if " " in cleaned:
            parts = [p.strip() for p in cleaned.split() if p.strip()]
        else:
            parts = list(cleaned)

        selected = []

        invalid = False

        for part in parts:
            if part not in letter_lookup:
                invalid = True
                break

            label = letter_lookup[part]

            if label not in selected:
                selected.append(label)

        if invalid or not selected:
            print("Invalid choice. Use the listed letters.")
            continue

        return " | ".join(selected)


def ask_tactical_questions(round_label):
    context = {}

    rrq_side = ask_choice(
        "What side was RRQ on for this pistol?",
        [
            ("A", "Attack"),
            ("B", "Defense"),
        ],
    )

    context[f"{round_label}_rrq_side_context"] = rrq_side

    if rrq_side == "Attack":
        tactical_tags = ask_multi_choice(
            "RRQ Attack tactical context. Select all that apply.",
            [
                ("A", "Lurk"),
                ("B", "Execute"),
                ("C", "Default"),
                ("D", "Other"),
            ],
        )
    else:
        tactical_tags = ask_multi_choice(
            "RRQ Defense tactical context. Select all that apply.",
            [
                ("A", "Full Retake"),
                ("B", "Flood"),
                ("C", "Trap"),
                ("D", "Solo Push"),
                ("E", "Group Push"),
                ("F", "Flank"),
                ("G", "Mid Control"),
                ("H", "Site Stack"),
                ("I", "Other"),
            ],
        )

    context[f"{round_label}_tactical_tags"] = tactical_tags

    return context


def ask_context_questions(round_label, event_type):
    context = {}

    # 1. Trade first because you just watched the first kill.
    # For 5v5 PP, the first event is the plant before any kill, so trade is not applicable.
    if event_type == "5v5 PP":
        context[f"{round_label}_trade_within_3s"] = "No"
    else:
        context[f"{round_label}_trade_within_3s"] = ask_yes_no(
            "Was the first kill traded within 3 seconds?"
        )

    # 2. RRQ side + side-specific tactical context.
    context.update(ask_tactical_questions(round_label))

    # 3. First event area.
    context[f"{round_label}_first_event_site"] = ask_choice(
        "Where did the first event happen?",
        [
            ("A", "A"),
            ("B", "B"),
            ("C", "C"),
            ("D", "Mid"),
            ("E", "Other"),
        ],
    )

    # 4. Spike plant.
    spike_planted = ask_yes_no("Was spike planted in the round?")
    context[f"{round_label}_spike_planted"] = spike_planted

    if spike_planted == "Yes":
        context[f"{round_label}_plant_site"] = ask_choice(
            "What site was spike planted?",
            [
                ("A", "A"),
                ("B", "B"),
                ("C", "C"),
            ],
        )

        plant_time = input("Plant time? Example 0:49. Press Enter to skip: ").strip()
        context[f"{round_label}_plant_time"] = plant_time
    else:
        context[f"{round_label}_plant_site"] = "N/A"
        context[f"{round_label}_plant_time"] = ""

    print()
    notes = input("Notes? Press Enter to leave blank: ").strip()
    context[f"{round_label}_notes"] = notes

    return context


# ==============================
# HOTKEY EVENT CAPTURE
# ==============================

def wait_for_hotkey_event(round_display):
    if keyboard is None:
        print()
        print("The keyboard library is not installed.")
        print("Install it with: pip install keyboard")
        return None

    print()
    print("=" * 100)
    print(f"{round_display} HOTKEY CAPTURE")
    print("=" * 100)
    print("Hotkeys while watching the VOD:")
    print("1 = RRQ got First Blood before plant")
    print("2 = RRQ suffered First Death before plant")
    print("3 = 5v5 Post-Plant: spike planted before any kill")
    print("q = cancel this round")
    print()
    print("Step 1: Pause the VOD right before the pistol starts.")
    print("Step 2: Press play and press Enter here when the timer starts at 1:40.")
    input("Press Enter at 1:40 to start timing...")

    start_time = time.time()

    print()
    print("Timing started.")
    print("Now watch the VOD and press 1, 2, 3, or q.")
    print("Waiting for hotkey...")

    valid_keys = ["1", "2", "3", "q"]

    while True:
        event = keyboard.read_event(suppress=False)

        if event.event_type != keyboard.KEY_DOWN:
            continue

        key = event.name.lower()

        if key not in valid_keys:
            continue

        elapsed = time.time() - start_time
        estimated_seconds_remaining = ROUND_START_SECONDS - elapsed
        clock_time = seconds_remaining_to_clock(estimated_seconds_remaining)

        print()
        print(f"Detected hotkey: {key}")
        print(f"Elapsed seconds: {elapsed:.2f}")
        print(f"Estimated round clock: {clock_time}")

        return {
            "event_choice": key,
            "elapsed_seconds": round(elapsed, 2),
            "clock_time": clock_time,
        }


def wait_for_terminal_event(round_display):
    print()
    print("=" * 100)
    print(f"{round_display} TERMINAL CAPTURE")
    print("=" * 100)
    print("Terminal mode:")
    print("1 = RRQ got First Blood before plant")
    print("2 = RRQ suffered First Death before plant")
    print("3 = 5v5 Post-Plant: spike planted before any kill")
    print("q = cancel this round")
    print()
    print("Pause the VOD right before the pistol starts.")
    input("Press play and press Enter here when timer starts at 1:40...")

    start_time = time.time()

    while True:
        answer = input("When first event happens, type 1/2/3 or q then press Enter: ").strip().lower()

        if answer not in ["1", "2", "3", "q"]:
            print("Invalid input. Enter 1, 2, 3, or q.")
            continue

        elapsed = time.time() - start_time
        estimated_seconds_remaining = ROUND_START_SECONDS - elapsed
        clock_time = seconds_remaining_to_clock(estimated_seconds_remaining)

        print()
        print(f"Input: {answer}")
        print(f"Elapsed seconds: {elapsed:.2f}")
        print(f"Estimated round clock: {clock_time}")

        return {
            "event_choice": answer,
            "elapsed_seconds": round(elapsed, 2),
            "clock_time": clock_time,
        }


def capture_pistol_event(round_display):
    if keyboard is None:
        return wait_for_terminal_event(round_display)

    try:
        return wait_for_hotkey_event(round_display)
    except Exception as e:
        print()
        print("Global hotkey capture failed.")
        print(f"Error: {e}")
        print()
        print("Falling back to terminal input mode.")
        return wait_for_terminal_event(round_display)


# ==============================
# SAVING EVENT DATA
# ==============================

def build_detection_from_hotkey(group_info, event_result):
    event_choice = event_result["event_choice"]
    clock_time = event_result["clock_time"]
    elapsed_seconds = event_result["elapsed_seconds"]

    rrq_team = TEAM_TO_REVIEW

    teams = group_info["teams"]

    opponent_team = ""

    for team in teams:
        if team != rrq_team:
            opponent_team = team

    if event_choice == "1":
        return {
            "event_type": "Hotkey",
            "clock_time": clock_time,
            "estimated_clock_time": clock_time,
            "elapsed_seconds_until_detection": elapsed_seconds,
            "killer": "",
            "victim": "",
            "killer_team": rrq_team,
            "victim_team": opponent_team,
            "ocr_text": "",
            "clock_ocr_raw": "",
            "screenshot": "",
            "confidence": "hotkey",
        }

    if event_choice == "2":
        return {
            "event_type": "Hotkey",
            "clock_time": clock_time,
            "estimated_clock_time": clock_time,
            "elapsed_seconds_until_detection": elapsed_seconds,
            "killer": "",
            "victim": "",
            "killer_team": opponent_team,
            "victim_team": rrq_team,
            "ocr_text": "",
            "clock_ocr_raw": "",
            "screenshot": "",
            "confidence": "hotkey",
        }

    if event_choice == "3":
        return {
            "event_type": "5v5 PP",
            "clock_time": clock_time,
            "estimated_clock_time": clock_time,
            "elapsed_seconds_until_detection": elapsed_seconds,
            "killer": "",
            "victim": "",
            "killer_team": "",
            "victim_team": "",
            "ocr_text": "",
            "clock_ocr_raw": "",
            "screenshot": "",
            "confidence": "hotkey",
        }

    return None


def classify_fb_fd_for_team(detection, team):
    if not detection:
        return ""

    if detection.get("event_type") == "5v5 PP":
        return "5v5 PP"

    if detection.get("killer_team") == team:
        return "FB"

    if detection.get("victim_team") == team:
        return "FD"

    return ""


def apply_row_updates(df, idx, update_dict):
    for col in update_dict.keys():
        if col not in df.columns:
            df[col] = ""
            df[col] = df[col].astype("object")

    df.loc[idx, list(update_dict.keys())] = pd.Series(update_dict)

    return df


def apply_detection_to_group(df, group_info, detection, round_label, context, accepted_status="done"):
    for idx in group_info["indices"]:
        team = str(df.at[idx, "team"]).strip()
        fb_fd = classify_fb_fd_for_team(detection, team)

        update_dict = {
            f"auto_{round_label}_first_contact_time": str(detection.get("clock_time", "")),
            f"auto_{round_label}_fb_fd": str(fb_fd),
            f"auto_{round_label}_killer": str(detection.get("killer", "")),
            f"auto_{round_label}_victim": str(detection.get("victim", "")),
            f"auto_{round_label}_killer_team": str(detection.get("killer_team", "")),
            f"auto_{round_label}_victim_team": str(detection.get("victim_team", "")),
            f"auto_{round_label}_ocr_text": str(detection.get("ocr_text", "")),
            f"auto_{round_label}_clock_ocr_raw": str(detection.get("clock_ocr_raw", "")),
            f"auto_{round_label}_estimated_clock_time": str(detection.get("estimated_clock_time", "")),
            f"auto_{round_label}_elapsed_seconds_until_detection": str(detection.get("elapsed_seconds_until_detection", "")),
            f"auto_{round_label}_screenshot": str(detection.get("screenshot", "")),
            f"auto_{round_label}_confidence": str(detection.get("confidence", "")),
            f"auto_{round_label}_review_status": accepted_status,
        }

        update_dict.update(context)

        df = apply_row_updates(df, idx, update_dict)

    return df


def apply_tactical_context_to_group(df, group_info, round_label, tactical_context):
    for idx in group_info["indices"]:
        update_dict = {
            f"{round_label}_rrq_side_context": tactical_context.get(f"{round_label}_rrq_side_context", ""),
            f"{round_label}_tactical_tags": tactical_context.get(f"{round_label}_tactical_tags", ""),
        }

        df = apply_row_updates(df, idx, update_dict)

    return df


# ==============================
# REVIEW ONE ROUND
# ==============================

def print_detection_summary(group_info, detection):
    print()
    print("Event captured:")
    print(f"Clock time:   {detection.get('clock_time', '')}")
    print(f"Elapsed sec:  {detection.get('elapsed_seconds_until_detection', '')}")
    print(f"Event type:   {detection.get('event_type', '')}")
    print(f"Killer team:  {detection.get('killer_team', '')}")
    print(f"Victim team:  {detection.get('victim_team', '')}")
    print()

    print("Team assignment:")
    for team in group_info["teams"]:
        print(f"  {team}: {classify_fb_fd_for_team(detection, team)}")

    print()


def manual_event_flow(group_info, round_display):
    print()
    print(f"{round_display} manual entry.")
    print("1 = RRQ got First Blood before plant")
    print("2 = RRQ suffered First Death before plant")
    print("3 = 5v5 Post-Plant: spike planted before any kill")
    print("q = cancel")
    print()

    while True:
        event_choice = input("Enter 1, 2, 3, or q: ").strip().lower()

        if event_choice in ["1", "2", "3", "q"]:
            break

        print("Invalid choice.")

    if event_choice == "q":
        return None

    manual_time = input("Enter first event/contact time, example 1:05 or 0:49: ").strip()

    fake_event_result = {
        "event_choice": event_choice,
        "elapsed_seconds": "",
        "clock_time": manual_time,
    }

    return build_detection_from_hotkey(group_info, fake_event_result)


def review_single_round(df, group_info, round_label, round_display):
    existing_action, df = handle_existing_round_data(
        df=df,
        group_info=group_info,
        round_label=round_label,
        round_display=round_display,
    )

    if existing_action == "skip":
        print(f"Skipping {round_display}.")
        return df

    print()
    print("=" * 100)
    print(f"{round_display} REVIEW")
    print("=" * 100)
    print("Options:")
    print("- Press Enter to use global hotkeys.")
    print("- Type m for manual entry.")
    print("- Type v to view existing values.")
    print("- Type s to skip this round.")
    print()

    action = input(f"{round_display}: Enter=hotkeys, m=manual, v=view, s=skip: ").strip().lower()

    if action == "s":
        for idx in group_info["indices"]:
            df.at[idx, f"auto_{round_label}_review_status"] = "skipped"
        return df

    if action == "v":
        print_round_saved_values(df, group_info, round_label, round_display)
        return review_single_round(df, group_info, round_label, round_display)

    if action == "m":
        detection = manual_event_flow(group_info, round_display)

        if detection is None:
            return df

        print_detection_summary(group_info, detection)
        context = ask_context_questions(round_label, detection.get("event_type", ""))

        df = apply_detection_to_group(
            df=df,
            group_info=group_info,
            detection=detection,
            round_label=round_label,
            context=context,
            accepted_status="done_manual",
        )

        return df

    event_result = capture_pistol_event(round_display)

    if event_result is None:
        print("No event captured.")
        return df

    if event_result["event_choice"] == "q":
        print("Round capture cancelled.")
        for idx in group_info["indices"]:
            df.at[idx, f"auto_{round_label}_review_status"] = "cancelled"
        return df

    detection = build_detection_from_hotkey(group_info, event_result)

    if detection is None:
        print("Could not build detection from hotkey.")
        return df

    print_detection_summary(group_info, detection)

    while True:
        print("Accept this event?")
        print("(Y) Yes")
        print("(N) No, mark rejected")
        print("(M) Manual entry instead")

        confirm = input("> ").strip().lower()

        if confirm == "" or confirm.startswith("y"):
            break

        if confirm.startswith("n"):
            for idx in group_info["indices"]:
                df.at[idx, f"auto_{round_label}_review_status"] = "rejected"
            return df

        if confirm.startswith("m"):
            detection = manual_event_flow(group_info, round_display)

            if detection is None:
                return df

            print_detection_summary(group_info, detection)
            break

        print("Invalid choice.")

    context = ask_context_questions(round_label, detection.get("event_type", ""))

    df = apply_detection_to_group(
        df=df,
        group_info=group_info,
        detection=detection,
        round_label=round_label,
        context=context,
        accepted_status="done",
    )

    return df


def review_tactical_only_round(df, group_info, round_label, round_display):
    existing_action, df = handle_existing_tactical_data(
        df=df,
        group_info=group_info,
        round_label=round_label,
        round_display=round_display,
    )

    if existing_action == "skip":
        print(f"Skipping {round_display} tactical context.")
        return df

    print()
    print("=" * 100)
    print(f"{round_display} TACTICAL CONTEXT ONLY")
    print("=" * 100)

    print_round_saved_values(df, group_info, round_label, round_display)

    print("Options:")
    print("- Press Enter to enter tactical context.")
    print("- Type s to skip this round.")
    print()

    action = input(f"{round_display}: Enter=tactical context, s=skip: ").strip().lower()

    if action == "s":
        return df

    tactical_context = ask_tactical_questions(round_label)

    df = apply_tactical_context_to_group(
        df=df,
        group_info=group_info,
        round_label=round_label,
        tactical_context=tactical_context,
    )

    return df


# ==============================
# MAIN WORKFLOW
# ==============================

def print_group_context(group_info, group_df):
    print()
    print("=" * 100)
    print(f"Match ID:   {group_info.get('match_id', '')}")
    print(f"Map #:      {group_info.get('map_number', '')}")
    print(f"Map:        {group_info.get('map_played', '')}")
    print(f"Teams:      {' vs '.join(group_info.get('teams', []))}")
    print()
    print(f"VLR:        {group_info.get('match_url', '')}")
    print(f"VOD:        {group_info.get('vod_url', '')}")
    print()
    print("Rows in this map group:")

    display_cols = [
        "team",
        "opponent",
        "players",
        "agents",
        "comp_tags",
        "round_1_side",
        "round_1_result",
        "round_13_side",
        "round_13_result",
        "auto_round_1_fb_fd",
        "auto_round_1_review_status",
        "round_1_rrq_side_context",
        "round_1_tactical_tags",
        "auto_round_13_fb_fd",
        "auto_round_13_review_status",
        "round_13_rrq_side_context",
        "round_13_tactical_tags",
    ]

    display_cols = [col for col in display_cols if col in group_df.columns]

    print(group_df[display_cols].to_string(index=True))
    print("=" * 100)
    print()


def review_groups():
    if keyboard is None and not TACTICAL_CONTEXT_ONLY_MODE:
        print()
        print("WARNING: keyboard library is not installed.")
        print("Run: pip install keyboard")
        print("The script will still work in terminal-input fallback mode.")

    df = load_review_dataframe()
    df = ensure_output_columns(df)

    required = ["team", "match_url", "vod_url"]

    for col in required:
        if col not in df.columns:
            print(f"Missing required column: {col}")
            return

    groups = build_map_groups(df)

    if ONLY_REVIEW_TEAM_MATCHES:
        groups = filter_groups_to_team(groups, TEAM_TO_REVIEW)

    if not groups:
        print("No match/map groups with VOD links found.")
        return

    print_progress_summary(df, groups)

    print()

    if ONLY_REVIEW_TEAM_MATCHES:
        print(f"Found {len(groups)} {TEAM_TO_REVIEW} match/map groups with VOD links.")
    else:
        print(f"Found {len(groups)} unique match/map groups with VOD links.")

    print()
    print("This avoids double entry:")
    print("- You review a map once.")
    print("- The script fills both team rows.")
    print("- Each map reviews the selected pistol rounds.")
    print()

    if TACTICAL_CONTEXT_ONLY_MODE:
        print("=" * 100)
        print("TACTICAL CONTEXT ONLY MODE IS ON")
        print("This will NOT change FB/FD/time/plant/trade data.")
        print("It will only add RRQ side context and tactical tags.")
        print("=" * 100)
        print()

    for pos, group_info in enumerate(groups, start=1):
        group_df = df.loc[group_info["indices"]]

        if SKIP_DONE_MAPS and group_is_done(df, group_info):
            print(f"[{pos}/{len(groups)}] Already complete for current mode: {group_info['match_id']} {group_info['map_played']}. Skipping.")
            continue

        print_group_context(group_info, group_df)

        action = input(f"[{pos}/{len(groups)}] Press Enter to open/use VOD, s to skip map, q to quit: ").strip().lower()

        if action == "q":
            break

        if action == "s":
            continue

        vod_url = str(group_info["vod_url"]).strip()

        if OPEN_VOD_AUTOMATICALLY:
            print("Opening VOD...")
            webbrowser.open(vod_url)
        else:
            print("VOD URL:")
            print(vod_url)

        if REVIEW_ROUND_1:
            if TACTICAL_CONTEXT_ONLY_MODE:
                df = review_tactical_only_round(
                    df=df,
                    group_info=group_info,
                    round_label="round_1",
                    round_display="Round 1",
                )
            else:
                df = review_single_round(
                    df=df,
                    group_info=group_info,
                    round_label="round_1",
                    round_display="Round 1",
                )

            df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
            print(f"Saved Round 1 progress to {OUTPUT_CSV}")

        if REVIEW_ROUND_13:
            print()
            print("Now jump to Round 13 / second pistol in the same VOD.")
            print("Use the VOD timeline or VLR round page to find the second-half pistol.")

            if not TACTICAL_CONTEXT_ONLY_MODE:
                if round_is_done(df, group_info, "round_13") and PROMPT_ON_COMPLETED_ROUND:
                    print("Round 13 already has saved data. You will get a keep/overwrite menu next.")
                else:
                    input("When you are ready to review Round 13, press Enter...")
            else:
                input("When you are ready to review Round 13 tactical context, press Enter...")

            if TACTICAL_CONTEXT_ONLY_MODE:
                df = review_tactical_only_round(
                    df=df,
                    group_info=group_info,
                    round_label="round_13",
                    round_display="Round 13",
                )
            else:
                df = review_single_round(
                    df=df,
                    group_info=group_info,
                    round_label="round_13",
                    round_display="Round 13",
                )

            df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
            print(f"Saved Round 13 progress to {OUTPUT_CSV}")

    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print_progress_summary(df, groups)

    print()
    print("=" * 100)
    print("DONE")
    print("=" * 100)
    print(f"Saved output to: {OUTPUT_CSV}")


def main():
    review_groups()


if __name__ == "__main__":
    main()
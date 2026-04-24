import datetime
import random
import copy
from zoneinfo import ZoneInfo

# Force all calculations to use East Coast time
LOCAL_TZ = ZoneInfo("America/New_York")


def parse_iso(iso_str):
    """Safely parse Google Calendar ISO strings into timezone-aware datetimes."""
    if iso_str.endswith('Z'):
        dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    else:
        dt = datetime.datetime.fromisoformat(iso_str)
    # Ensure every single date object is evaluated in local time
    return dt.astimezone(LOCAL_TZ)


def get_free_slots(existing_events, days_ahead=14, start_hour=8, end_hour=23):
    """Finds available 1-hour slots strictly within the next 2 weeks in LOCAL time."""
    now = datetime.datetime.now(LOCAL_TZ)
    free_slots = []

    for day in range(days_ahead):
        current_date = now + datetime.timedelta(days=day)

        # Extended end_hour to 23 so you can study until 11 PM
        for hour in range(start_hour, end_hour):
            slot_start = current_date.replace(
                hour=hour, minute=0, second=0, microsecond=0)

            # Prevent the algorithm from trying to schedule a block in the past today
            if slot_start <= now:
                continue

            slot_end = slot_start + datetime.timedelta(hours=1)

            is_busy = False
            for event in existing_events:
                e_start_str = event['start'].get(
                    'dateTime', event['start'].get('date'))
                e_end_str = event['end'].get(
                    'dateTime', event['end'].get('date'))
                if 'T' in e_start_str:
                    e_start = parse_iso(e_start_str)
                    e_end = parse_iso(e_end_str)
                    if slot_start < e_end and slot_end > e_start:
                        is_busy = True
                        break
            if not is_busy:
                free_slots.append((slot_start, slot_end))
    return free_slots


def score_schedule(schedule, strategy):
    score = 0

    # 1. UNIVERSAL RULE: Never schedule work AFTER the deadline
    for block in schedule:
        if block['start'] > block['due_date']:
            score -= 10000

    # 2. Heuristics (Objective Functions)
    if strategy == "balanced":
        hours_per_day = {}
        for block in schedule:
            day = block['start'].date()
            hours_per_day[day] = hours_per_day.get(day, 0) + 1

        for day, hours in hours_per_day.items():
            if hours <= 3:
                score += 10
            else:
                score -= (hours - 3) * 5

    elif strategy == "weekend_warrior":
        for block in schedule:
            day_of_week = block['start'].weekday()
            if day_of_week in [5, 6]:  # 5 is Saturday, 6 is Sunday
                score += 20
            else:
                score -= 5

    elif strategy == "night_owl":
        for block in schedule:
            hour = block['start'].hour  # Evaluated in East Coast time
            if hour >= 18:  # 6 PM to 11 PM
                score += 15
            elif hour < 12:  # Penalize mornings
                score -= 10
            else:
                score -= 2  # Actively penalize the afternoon so blocks try to escape!

    elif strategy == "sprinter":
        schedule_sorted = sorted(schedule, key=lambda x: x['start'])
        for i in range(len(schedule_sorted) - 1):
            current_block = schedule_sorted[i]
            next_block = schedule_sorted[i + 1]
            if current_block['end'] == next_block['start']:
                score += 25
            elif (next_block['start'] - current_block['end']).total_seconds() == 3600:
                score -= 15

    elif strategy == "procrastinator":
        for block in schedule:
            time_until_due = (block['due_date'] -
                              block['start']).total_seconds()
            hours_until_due = time_until_due / 3600
            if hours_until_due <= 48:
                score += 50
            else:
                score -= hours_until_due

    return score


def optimize_schedule(deadlines, existing_events, strategy="balanced", iterations=3000, days_ahead=14):
    free_slots = get_free_slots(existing_events, days_ahead=days_ahead)
    if not free_slots:
        print("Error: No free time available in the next 2 weeks!")
        return []

    now = datetime.datetime.now(LOCAL_TZ)
    current_schedule = []
    available_slots = copy.deepcopy(free_slots)
    final_events = []

    # Sort deadlines so the most urgent things get first pick of the schedule
    deadlines = sorted(deadlines, key=lambda x: parse_iso(x['due_date']))

    for task in deadlines:
        task_due = parse_iso(task['due_date'])

        # Hard Constraint: Skip past assignments
        if task_due < now:
            print(
                f"⏭️ Skipping '{task.get('title', 'Unknown')}' — already past due.")
            continue

        # Extract specific data, falling back to defaults if LLM missed it
        c_code = task.get("class_code", "CLASS")
        c_title = task.get("title", "Task")

        # 🚨 DUE FORMAT
        final_events.append({
            "summary": f"🚨 [{c_code}] {c_title} [DUE]",
            "start_time": task['due_date'],
            "end_time": task['due_date']
        })

        hours_needed = task.get("estimated_hours", 2)

        for _ in range(hours_needed):
            valid_slots = [i for i, slot in enumerate(
                available_slots) if slot[1] <= task_due]

            if valid_slots:
                # Random initialization to prevent the Local Maximum Deadlock
                slot_idx = random.choice(valid_slots)
                slot = available_slots.pop(slot_idx)

                # Store the separated data in the working block
                current_schedule.append({
                    "class_code": c_code,
                    "title": c_title,
                    "start": slot[0],
                    "end": slot[1],
                    "due_date": task_due
                })
            else:
                print(
                    f"⚠️ Warning: Not enough free time before {task_due.date()} to finish '{c_title}'.")
                break

    current_score = score_schedule(current_schedule, strategy)

    # Bumped iterations to 3000 for the random scatter
    print(
        f"\nStarting Hill Climbing optimization (Strategy: {strategy}, Horizon: 14 days)...")
    for _ in range(iterations):
        if not available_slots or not current_schedule:
            break

        neighbor_schedule = copy.deepcopy(current_schedule)
        swap_idx = random.randint(0, len(neighbor_schedule) - 1)

        task_due = neighbor_schedule[swap_idx]['due_date']
        valid_new_slot_indices = [i for i, slot in enumerate(
            available_slots) if slot[1] <= task_due]

        if not valid_new_slot_indices:
            continue

        new_slot_idx = random.choice(valid_new_slot_indices)

        old_slot = (neighbor_schedule[swap_idx]['start'],
                    neighbor_schedule[swap_idx]['end'])
        neighbor_schedule[swap_idx]['start'] = available_slots[new_slot_idx][0]
        neighbor_schedule[swap_idx]['end'] = available_slots[new_slot_idx][1]

        neighbor_score = score_schedule(neighbor_schedule, strategy)

        if neighbor_score > current_score:
            current_schedule = neighbor_schedule
            current_score = neighbor_score
            available_slots[new_slot_idx] = old_slot

    for block in current_schedule:
        # ⚙️ WORK FORMAT
        final_events.append({
            "summary": f"⚙️ [{block['class_code']}] {block['title']} [WORK]",
            "start_time": block['start'].isoformat(),
            "end_time": block['end'].isoformat()
        })

    return final_events
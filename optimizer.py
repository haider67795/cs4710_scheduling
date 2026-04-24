import datetime
import random
import copy


def parse_iso(iso_str):
    """Safely parse Google Calendar ISO strings."""
    return datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))


def get_free_slots(existing_events, days_ahead=14, start_hour=9, end_hour=22):
    """Finds available 1-hour slots strictly within the next 2 weeks (14 days)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    free_slots = []

    for day in range(days_ahead):
        current_date = now + datetime.timedelta(days=day)
        for hour in range(start_hour, end_hour):
            slot_start = current_date.replace(
                hour=hour, minute=0, second=0, microsecond=0)
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

    # --- NEW STRATEGIES ---

    elif strategy == "night_owl":
        # Rewards scheduling blocks late in the evening (after 6 PM)
        for block in schedule:
            hour = block['start'].hour
            if hour >= 18:  # 6 PM or later
                score += 15
            elif hour < 12:  # Penalize mornings (before Noon)
                score -= 10
            else:
                score += 0  # Neutral for afternoons

    elif strategy == "sprinter":
        # Rewards "clumping" work together. If a block starts exactly when another ends, give massive points.
        schedule_sorted = sorted(schedule, key=lambda x: x['start'])
        for i in range(len(schedule_sorted) - 1):
            current_block = schedule_sorted[i]
            next_block = schedule_sorted[i + 1]

            # If the next block starts exactly when this one ends (back-to-back)
            if current_block['end'] == next_block['start']:
                score += 25
            # Penalize having 1-hour gaps (worst way to study)
            elif (next_block['start'] - current_block['end']).total_seconds() == 3600:
                score -= 15

    elif strategy == "procrastinator":
        # Rewards placing the study block as close to the deadline as physically possible
        for block in schedule:
            time_until_due = (block['due_date'] -
                              block['start']).total_seconds()
            hours_until_due = time_until_due / 3600

            # If it's within 48 hours of the deadline, HUGE points.
            if hours_until_due <= 48:
                score += 50
            else:
                score -= hours_until_due  # Slowly drain points the further away it is

    return score


def optimize_schedule(deadlines, existing_events, strategy="balanced", iterations=2000, days_ahead=14):
    free_slots = get_free_slots(existing_events, days_ahead=days_ahead)
    if not free_slots:
        print("Error: No free time available in the next 2 weeks!")
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    current_schedule = []
    available_slots = copy.deepcopy(free_slots)
    final_events = []

    # --- URGENCY SORTING ---
    # Sort deadlines by due date so the most immediate stuff gets scheduled first
    deadlines = sorted(deadlines, key=lambda x: parse_iso(x['due_date']))

    for task in deadlines:
        task_due = parse_iso(task['due_date'])

        # --- HARD CONSTRAINT 1: Ignore the Past ---
        if task_due < now:
            print(f"⏭️ Skipping '{task['summary']}' — already past due.")
            continue

        # Add the actual Deadline Event to the calendar
        final_events.append({
            "summary": f"🚨 DUE: {task['summary']}",
            "start_time": task['due_date'],
            "end_time": task['due_date']
        })

        hours_needed = task.get("estimated_hours", 2)

        # --- HARD CONSTRAINT 2: Only grab slots BEFORE the deadline ---
        blocks_scheduled = 0
        for _ in range(hours_needed):
            slot_idx = -1
            for i, slot in enumerate(available_slots):
                # The slot must end before the task is due
                if slot[1] <= task_due:
                    slot_idx = i
                    break

            if slot_idx != -1:
                slot = available_slots.pop(slot_idx)
                current_schedule.append({
                    "task": task["summary"],
                    "start": slot[0],
                    "end": slot[1],
                    "due_date": task_due
                })
                blocks_scheduled += 1
            else:
                print(
                    f"⚠️ Warning: Not enough free time before {task_due.date()} to finish '{task['summary']}'.")
                break

    current_score = score_schedule(current_schedule, strategy)

    # Hill Climbing
    print(
        f"\nStarting Hill Climbing optimization (Strategy: {strategy}, Horizon: 14 days)...")
    for _ in range(iterations):
        if not available_slots or not current_schedule:
            break

        neighbor_schedule = copy.deepcopy(current_schedule)
        swap_idx = random.randint(0, len(neighbor_schedule) - 1)

        # --- HARD CONSTRAINT 3: Only swap into valid pre-deadline slots ---
        task_due = neighbor_schedule[swap_idx]['due_date']
        valid_new_slot_indices = [i for i, slot in enumerate(
            available_slots) if slot[1] <= task_due]

        if not valid_new_slot_indices:
            continue  # No valid slots to swap into, try a different block

        new_slot_idx = random.choice(valid_new_slot_indices)

        # Execute the swap
        old_slot = (neighbor_schedule[swap_idx]['start'],
                    neighbor_schedule[swap_idx]['end'])
        neighbor_schedule[swap_idx]['start'] = available_slots[new_slot_idx][0]
        neighbor_schedule[swap_idx]['end'] = available_slots[new_slot_idx][1]

        neighbor_score = score_schedule(neighbor_schedule, strategy)

        if neighbor_score > current_score:
            current_schedule = neighbor_schedule
            current_score = neighbor_score
            available_slots[new_slot_idx] = old_slot

    # Format Study Blocks for Google Calendar
    for block in current_schedule:
        final_events.append({
            "summary": f"Deep Work: {block['task']}",
            "start_time": block['start'].isoformat(),
            "end_time": block['end'].isoformat()
        })

    return final_events

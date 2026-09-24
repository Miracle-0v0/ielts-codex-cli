"""Bounded daily plans; only saved answers advance a resumable plan."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from .context import load_exercises


def build_plan(store, bank, minutes: int, *, deck="all", focus="spelling", today=None):
    if type(minutes) is not int or not 5 <= minutes <= 60:
        raise ValueError("每日学习时间须为 5–60 分钟。")
    today = today or date.today()
    words = {word.key: word for word in bank.ready_words}
    exercises = {}
    practiced = {event.get("exercise_id"): index
                 for index, event in enumerate(store.data.attempts)}
    for exercise in sorted(load_exercises(), key=lambda item: practiced.get(item.id, -1)):
        if exercise.word_id in words:
            exercises.setdefault(exercise.word_id, exercise)
    active_days = sorted(day for day, summary in store.data.sessions.items()
                         if summary.get("reviewed", 0) > 0 and day <= today.isoformat())
    recovery = bool(active_days and (today - date.fromisoformat(active_days[-1])).days >= 3)
    capacity = min(minutes, 10) if recovery else min(minutes, 30)
    tasks = ("recall", "spelling", "context")
    due = []
    for task in tasks:
        for key, card in store.cards_for(task).items():
            if (key in words and card.due and card.due <= today.isoformat()
                    and (task != "context" or key in exercises)):
                due.append((card.due, 0 if task == focus else 1, task, key))
    due.sort()
    items, selected = [], set()

    def add(key, task, reason):
        if len(items) >= capacity or (key, task) in selected:
            return
        if key not in words or (task == "context" and key not in exercises):
            return
        selected.add((key, task))
        item = dict(id=uuid4().hex, word=key, task=task, reason=reason, retries=0)
        if task == "context":
            item["exercise_id"] = exercises[key].id
        items.append(item)

    for _day, _priority, task, key in due:
        add(key, task, "due")
    max_new = 0 if recovery or len(due) >= capacity else min(5, max(1, minutes // 4))
    new_keys = [key for key in words if key not in store.cards][:max_new]
    for key in new_keys:
        add(key, "recall", "new")
    exposed = [key for key in words if key in store.cards] + new_keys
    order = [focus, *[task for task in tasks if task != focus]]
    for task in order:
        for key in exposed:
            if key not in store.cards_for(task):
                add(key, task, "unverified")
    for event in store.mistakes():
        add(event["word"], event["task"], "mistake")
    # A small pack can produce a shorter plan; never fill it by duplicating tasks.
    return dict(
        id=uuid4().hex, day=today.isoformat(), minutes=minutes, deck=deck, focus=focus,
        status="active" if items else "complete", completed=0, items=items,
        initial_count=len(items), max_actions=max(1, len(items) * 2),
        recovery=recovery, due_count=len(due), deferred_due=max(0, len(due) - capacity),
    )

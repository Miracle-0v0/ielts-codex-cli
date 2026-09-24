"""Shared spelling, context, mistake and daily-study interactions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .context import load_exercises
from .learning import LearningQueue, accepts_answer, normalize_answer, spelling_error
from .models import Rating
from .study import build_plan


TASK_LABELS = {"recall": "认义", "spelling": "拼写", "context": "语境"}
TASK_ALIASES = {**{key: key for key in TASK_LABELS},
                **{label: key for key, label in TASK_LABELS.items()}}
ERROR_LABELS = {
    "missing_letter": "字母遗漏", "extra_character": "多余字符",
    "unexpected_digit": "混入数字", "spelling": "拼写错误",
    "meaning_recall": "词义回忆", "collocation": "搭配选择",
    "word_form": "词形变化",
}


@dataclass(frozen=True)
class AnswerOutcome:
    rating: Rating
    correct: bool
    hint_level: int


def spelling_card(app, word, index, total, *, retry=False, study_item_id=None):
    app.ui.panel(
        f"quiz{' relearn' if retry else ''} {index}/{total}",
        [word.meaning_zh, f"{word.part_of_speech} · {word.topic}",
         "请输入对应英文单词；h 提示 · s 跳过 · q 结束。"],
    )
    hint = 0
    while True:
        try:
            answer = app.ui.prompt("  answer › ").strip()
        except (EOFError, KeyboardInterrupt):
            app.ui.write()
            return None
        if answer.lower() in {"q", "/quit"}:
            return None
        if answer.lower() in {"s", "/skip"}:
            app.ui.hint("已跳过；不产生答题记录。")
            return "skip"
        if answer.lower() in {"h", "/hint"}:
            hint = 1
            app._show_hint(word)
            continue
        if not answer:
            continue
        correct = accepts_answer(word, answer)
        rating = Rating.HARD if hint else Rating.GOOD
        if not correct:
            rating = Rating.AGAIN
            app.ui.error(f"{answer} → {word.word}")
            app.ui.hint("数字、标点和词内空格不会自动忽略。")
        else:
            app.ui.success(f"{word.word}  {word.phonetic}")
        if word.example:
            app.ui.hint(word.example)
        app.store.record_review(
            word.key, rating, task="spelling", correct=correct, hint_level=hint,
            retry=retry, answer=answer,
            error_type=None if correct else spelling_error(word, answer),
            study_item_id=study_item_id,
        )
        return AnswerOutcome(rating, correct, hint)


def context_card(app, word, exercise, index, total, *, retry=False, study_item_id=None):
    label = "搭配选择" if exercise.kind == "collocation" else "词形变化"
    app.ui.panel(
        f"context{' relearn' if retry else ''} {index}/{total} · {label}",
        [exercise.prompt, *[f"{i}. {choice}" for i, choice in enumerate(exercise.choices, 1)],
         "从选项中作答；h 提示 · s 跳过 · q 结束。"],
    )
    hint = 0
    valid = {str(i) for i in range(1, len(exercise.choices) + 1)}
    valid.update(normalize_answer(choice) for choice in exercise.choices)
    while True:
        try:
            answer = app.ui.prompt("  choice › ").strip()
        except (EOFError, KeyboardInterrupt):
            app.ui.write()
            return None
        if answer.lower() in {"q", "/quit"}:
            return None
        if answer.lower() in {"s", "/skip"}:
            return "skip"
        if answer.lower() in {"h", "/hint"}:
            hint = 1
            app.ui.hint(exercise.hint)
            continue
        if normalize_answer(answer) not in valid:
            app.ui.hint("请选一个列出的选项；其他表达不在本题的判分范围内。")
            continue
        correct = exercise.accepts(answer)
        rating = (Rating.HARD if hint else Rating.GOOD) if correct else Rating.AGAIN
        (app.ui.success if correct else app.ui.error)(f"答案：{exercise.answer}")
        app.ui.hint(exercise.explanation)
        app.store.record_review(
            word.key, rating, task="context", correct=correct, hint_level=hint,
            retry=retry, answer=answer, exercise_id=exercise.id,
            error_type=None if correct else exercise.kind, study_item_id=study_item_id,
        )
        return AnswerOutcome(rating, correct, hint)


def _exercises(app):
    seen = {event.get("exercise_id"): index
            for index, event in enumerate(app.store.data.attempts)}
    result = {}
    for exercise in sorted(load_exercises(), key=lambda item: seen.get(item.id, -1)):
        result.setdefault(exercise.word_id, exercise)
    return result


def _run(app, words, task, exercises=None):
    from .cli import SessionResult
    result = SessionResult()
    queue = LearningQueue(list(words))
    index = 0
    while queue:
        card = queue.pop()
        index += 1
        if task == "spelling":
            outcome = spelling_card(app, card.word, index, index + len(queue.pending), retry=card.retry)
        else:
            outcome = context_card(app, card.word, exercises[card.word.key],
                                   index, index + len(queue.pending), retry=card.retry)
        if outcome is None:
            result.stopped = True
            break
        if outcome == "skip":
            continue
        app._count_answer(result, card.word, outcome.correct,
                          hint_level=outcome.hint_level, retry=card.retry)
        if outcome.rating is Rating.AGAIN:
            app._repeat_failed(queue, card.word, result)
    app._session_summary("quiz" if task == "spelling" else "context", result)
    return result


def _prioritize(app, candidates, task, count):
    by_key = {word.key: word for word in candidates}
    due = app.active_bank().due(app.store.cards_for(task), date.today(), count)
    order = [word.key for word in due]
    order += [event["word"] for event in app.store.mistakes(task)]
    order += [word.key for word in candidates if word.key not in app.store.cards_for(task)]
    shuffled = list(candidates)
    app.rng.shuffle(shuffled)
    order += [word.key for word in shuffled]
    selected = []
    for key in dict.fromkeys(order):
        if key in by_key and len(selected) < count:
            selected.append(by_key[key])
    return selected


def run_spelling(app, count=10, topic=None, *, words=None):
    from .cli import SessionResult
    candidates = list(words) if words is not None else app.active_bank().learned(app.store.cards, topic)
    if not candidates:
        app.ui.hint("当前词包还没有可测验词条。先 /learn 或 /study。")
        return SessionResult()
    selected = candidates[:count] if words is not None else _prioritize(app, candidates, "spelling", count)
    return _run(app, selected, "spelling")


def run_context(app, count=5, topic=None, *, words=None):
    from .cli import SessionResult
    exercises = _exercises(app)
    candidates = list(words) if words is not None else list(app.active_bank().ready_words)
    candidates = [word for word in candidates
                  if word.key in exercises and (topic is None or word.topic == topic)]
    if not candidates:
        app.ui.hint("当前范围没有已编写的固定语境题。个人词条不会自动生成题目。")
        return SessionResult()
    selected = candidates[:count] if words is not None else _prioritize(app, candidates, "context", count)
    return _run(app, selected, "context", exercises)


def handle_mistakes(app, args):
    practice = bool(args and args[0] == "practice")
    values = args[1:] if practice else args
    if len(values) > 1 or (values and values[0] not in TASK_ALIASES):
        app.ui.warning("用法：/mistakes [recall|spelling|context]；/mistakes practice [题型]")
        return
    task = TASK_ALIASES[values[0]] if values else None
    bank = app.active_bank()
    entries = [event for event in app.store.mistakes(task)
               if (word := bank.get(event["word"])) is not None and word.status == "ready"]
    if not entries:
        app.ui.success("当前词包没有待练错项。已有历史练习仍保留。")
        return
    if practice:
        for kind in TASK_LABELS:
            words = [bank.get(event["word"]) for event in entries[:10] if event["task"] == kind]
            if not words:
                continue
            if kind == "recall":
                result = app._recall_session(words, is_new=False)
                app._session_summary("mistakes recall", result)
            elif kind == "spelling":
                result = run_spelling(app, len(words), words=words)
            else:
                result = run_context(app, len(words), words=words)
            if result.stopped:
                break
        return
    shown = set()
    for event in entries[:20]:
        if event["word"] in shown:
            continue
        shown.add(event["word"])
        word = bank.get(event["word"])
        lines = [
            f"{label}：{app.store.ability_status(word.key, kind)}"
            for kind, label in TASK_LABELS.items()
        ]
        error = ERROR_LABELS.get(event.get("error_type"), "提示后完成" if event["hint_level"] else "需要重练")
        lines += [f"最近问题：{error} · {event['day']}",
                  f"下次练习：无提示{TASK_LABELS[event['task']]}",
                  f"词条：{word.key}"]
        app.ui.panel(word.word, lines)
    app.ui.hint("用 /mistakes practice 开始；本组重练通过后仍保留错项，等待一次独立回答。")


def _profile(app, requested_minutes):
    existing = app.store.data.settings.get("study_profile")
    if isinstance(existing, dict):
        return existing
    decks = sorted({word.deck for word in app.bank.words} | {"all"})
    defaults = requested_minutes or 20
    app.ui.panel("开始今日学习", ["先设置三个偏好；以后可直接 /study。",
                                "Enter 使用默认值，q 返回。"])
    try:
        while True:
            answer = app.ui.prompt(f"  每日分钟 5–60 [{defaults}] › ").strip()
            if answer.lower() == "q":
                return None
            if not answer:
                minutes = defaults
                break
            if answer.isdigit() and 5 <= int(answer) <= 60:
                minutes = int(answer)
                break
            app.ui.hint("请输入 5 到 60 的整数。")
        while True:
            answer = app.ui.prompt("  薄弱项 1认义 / 2拼写 / 3语境 [2] › ").strip()
            if answer.lower() == "q":
                return None
            focus = {"": "spelling", "1": "recall", "2": "spelling", "3": "context"}.get(answer)
            focus = focus or TASK_ALIASES.get(answer)
            if focus:
                break
            app.ui.hint("请选择 1、2 或 3。")
        default_deck = app.store.data.settings.get("active_deck", "all")
        if default_deck not in decks:
            default_deck = "all"
        while True:
            answer = app.ui.prompt(f"  词包 {' / '.join(decks)} [{default_deck}] › ").strip()
            if answer.lower() == "q":
                return None
            deck = answer or default_deck
            if deck in decks:
                break
            app.ui.hint("请输入列出的词包名称。")
    except (EOFError, KeyboardInterrupt):
        app.ui.write()
        return None
    profile = dict(minutes=minutes, focus=focus, deck=deck)
    with app.store.transaction():
        app.store.data.settings["study_profile"] = profile
        app.store.data.settings["active_deck"] = deck
    app.game_mode.bank = app.active_bank()
    return profile


def handle_study(app, args):
    restart = bool(args and args[0] == "new")
    values = args[1:] if restart else args
    if len(values) > 1 or (values and (not values[0].isdigit() or not 5 <= int(values[0]) <= 60)):
        app.ui.warning("用法：/study [5–60 分钟]；/study new [分钟] 重排未答任务。")
        return
    requested = int(values[0]) if values else None
    profile = _profile(app, requested)
    if profile is None:
        app.ui.hint("已返回；偏好设置未保存。")
        return
    plan = app.store.data.study
    if plan and plan["status"] == "complete" and plan["day"] == date.today().isoformat() and not restart:
        app.ui.success("今日计划已经结束；可用 /mistakes practice 练错项，或 /study new 再排一组。")
        return
    if not plan or plan["status"] == "complete" or restart:
        minutes = requested or profile.get("minutes", 20)
        plan = build_plan(app.store, app.active_bank(), minutes,
                          deck=app.store.data.settings.get("active_deck", "all"),
                          focus=profile.get("focus", "spelling"))
        app.store.save_study(plan)
    app.ui.panel("今日学习 · 可继续", [
        f"词包 {plan['deck']} · 按 {plan['minutes']} 分钟安排",
        f"已处理 {plan['completed']} 项 · 剩余 {len(plan['items'])} 项（含待重练）",
        f"到期任务 {plan['due_count']} · 本组未排入 {plan['deferred_due']}",
        f"含重练最多 {plan['max_actions']} 次；用 q 返回后可继续。",
        "恢复学习：本组缩减任务量、暂缓新词。" if plan["recovery"] else "先处理到期，再练新词与其他能力。",
    ])
    if not plan["items"]:
        app.ui.success("当前没有需要安排的任务；也可用 /quiz 或 /context 主动练习。")
        return
    try:
        action = app.ui.prompt("  Enter 开始/继续 · q 返回 › ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if action not in {"", "start", "开始"}:
        return
    exercises = {exercise.id: exercise for exercise in load_exercises()}
    while app.store.data.study["items"]:
        plan = app.store.data.study
        item = plan["items"][0]
        word = app.bank.get(item["word"])
        if word is None or word.status != "ready":
            app.ui.hint("一个计划词条已移除或待补全，跳过此项；旧记录保留。")
            app.store.skip_study(item["id"])
            continue
        index = plan["completed"] + 1
        total = plan["completed"] + len(plan["items"])
        retry = bool(item.get("retries", 0))
        if item["task"] == "recall":
            outcome = app._recall_card(word, index, total,
                                       is_new=word.key not in app.store.cards_for("recall"),
                                       retry=retry, study_item_id=item["id"])
        elif item["task"] == "spelling":
            outcome = spelling_card(app, word, index, total, retry=retry, study_item_id=item["id"])
        else:
            exercise = exercises.get(item.get("exercise_id"))
            if exercise is None or exercise.word_id != word.key:
                app.ui.hint("原语境题已不可用，跳过此项；不会临时生成标准答案。")
                app.store.skip_study(item["id"])
                continue
            outcome = context_card(app, word, exercise, index, total,
                                   retry=retry, study_item_id=item["id"])
        if outcome is None:
            app.ui.hint("已保存已完成回答和剩余任务。下次 /study 从这里继续。")
            return
        if outcome == "skip":
            app.store.skip_study(item["id"])
    app.ui.success("本组今日学习结束。已完成记录保存在本地，未通过的词仍可继续复习。")

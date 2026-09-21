# -*- coding: utf-8 -*-
"""Конечный автомат жизненного цикла задачи: состояния, допустимый граф
переходов и функция transition(), которая проверяет каждый переход.

Не зависит от LLM/сети — используется и из store.py (валидация и явные
переходы), и из agent.py (system prompt + проверка предложений классификатора).
"""

STAGE_WAITING = "Ожидание задачи"
STAGE_DRAFT = "Черновик"
STAGE_PLANNED = "План составлен"
STAGE_REJECTED = "План отклонён"
STAGE_APPROVED = "План утверждён"
STAGE_IN_PROGRESS = "В работе"
STAGE_VALIDATED = "Проверено"
STAGE_DONE = "Готово"

STAGES = [
    STAGE_WAITING,
    STAGE_DRAFT,
    STAGE_PLANNED,
    STAGE_REJECTED,
    STAGE_APPROVED,
    STAGE_IN_PROGRESS,
    STAGE_VALIDATED,
    STAGE_DONE,
]

# Граф допустимых переходов: из какого состояния куда можно перейти за один шаг.
# Перепрыгивать через этапы (например DRAFT -> IN_PROGRESS или IN_PROGRESS -> DONE)
# запрещено самой структурой графа, а не просьбой в промпте.
ALLOWED_TRANSITIONS = {
    STAGE_WAITING: [STAGE_DRAFT],
    STAGE_DRAFT: [STAGE_PLANNED],
    STAGE_PLANNED: [STAGE_APPROVED, STAGE_REJECTED],
    STAGE_REJECTED: [STAGE_DRAFT],
    STAGE_APPROVED: [STAGE_IN_PROGRESS],
    STAGE_IN_PROGRESS: [STAGE_VALIDATED],
    STAGE_VALIDATED: [STAGE_DONE, STAGE_IN_PROGRESS],
    STAGE_DONE: [STAGE_WAITING],
}

STAGE_DESCRIPTIONS = {
    STAGE_WAITING: "ожидание новой задачи от пользователя",
    STAGE_DRAFT: "задача сформулирована, собираются требования и составляется план",
    STAGE_PLANNED: "план решения составлен и показан пользователю, ждёт утверждения",
    STAGE_REJECTED: "пользователь отклонил план, нужно составить новый",
    STAGE_APPROVED: "план утверждён пользователем, можно приступать к реализации",
    STAGE_IN_PROGRESS: "идёт реализация по утверждённому плану",
    STAGE_VALIDATED: "реализация проверена/протестирована и готова к сдаче",
    STAGE_DONE: "финальный результат выдан пользователю",
}

DEFAULT_TASK_STATE = {"stage": STAGE_WAITING, "step": "", "expectedAction": ""}


def transition(current_stage, target_stage):
    """Проверяет допустимость перехода current_stage -> target_stage по графу.

    Возвращает (ok, resulting_stage, message):
    - ok=True: resulting_stage == target_stage, message == "".
    - ok=False: resulting_stage == current_stage (переход не выполнен),
      message объясняет, какие переходы разрешены вместо запрошенного.

    Неизвестный current_stage трактуется как STAGE_WAITING (безопасный дефолт).
    """
    if current_stage not in STAGES:
        current_stage = STAGE_WAITING
    if target_stage == current_stage:
        return True, current_stage, ""
    if target_stage not in STAGES:
        return False, current_stage, "Неизвестное состояние «{}».".format(target_stage)

    allowed = ALLOWED_TRANSITIONS.get(current_stage, [])
    if target_stage in allowed:
        return True, target_stage, ""

    allowed_desc = (
        ", ".join("«{}»".format(s) for s in allowed)
        if allowed else "нет (это тупиковое состояние без исходящих переходов)"
    )
    message = (
        "Переход «{cur}» → «{tgt}» недопустим: нельзя пропускать этапы. "
        "Из состояния «{cur}» разрешены только переходы в: {allowed}."
    ).format(cur=current_stage, tgt=target_stage, allowed=allowed_desc)
    return False, current_stage, message

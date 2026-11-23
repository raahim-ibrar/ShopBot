from typing import Literal
from uuid import UUID, uuid4

from .db import Database


MAX_INPUT_TOKENS = 6_000
MAX_OUTPUT_TOKENS = 350
TOTAL_LIMIT_MICRODOLLARS = 1_900_000
DEVELOPMENT_LIMIT_MICRODOLLARS = 150_000


class ModelBudgetExceeded(RuntimeError):
    """The model request cannot be sent within the configured spending cap."""


def _microdollars(input_tokens: int, output_tokens: int) -> int:
    if not (0 <= input_tokens <= 1_000_000 and 0 <= output_tokens <= 128_000):
        raise ValueError("Token usage is outside the model's supported range.")
    # Haiku 5.5 lists different rates above 100K input tokens. The reservation
    # rejects such prompts, but account for a reported overrun conservatively.
    units = input_tokens + 5 * output_tokens
    if input_tokens > 100_000:
        units *= 5
    return (units + 9) // 10


def reserve_model_call(
    db: Database, *, stage: Literal["development", "evaluation"], input_tokens: int, output_limit: int,
) -> UUID:
    if stage not in {"development", "evaluation"}:
        raise ValueError("Unknown model spending stage.")
    if not (0 <= input_tokens <= MAX_INPUT_TOKENS and 1 <= output_limit <= MAX_OUTPUT_TOKENS):
        raise ModelBudgetExceeded("The model request exceeds the per-call token limits.")

    reservation_id = uuid4()
    cost = _microdollars(input_tokens, output_limit)
    with db.connect() as conn:
        if conn.execute("SELECT id FROM model_budget_lock WHERE id = 1 FOR UPDATE").fetchone() is None:
            raise ModelBudgetExceeded("The model budget ledger is unavailable.")
        spending = conn.execute(
            """
            SELECT coalesce(sum(cost), 0) AS total,
                   coalesce(sum(cost) FILTER (WHERE stage = 'development'), 0) AS development
            FROM (SELECT stage, coalesce(actual_microdollars, reserved_microdollars) AS cost FROM model_usage) usage
            """,
        ).fetchone()
        if spending["total"] + cost > TOTAL_LIMIT_MICRODOLLARS:
            raise ModelBudgetExceeded("The project model budget has been reached.")
        if stage == "development" and spending["development"] + cost > DEVELOPMENT_LIMIT_MICRODOLLARS:
            raise ModelBudgetExceeded("The development model budget has been reached.")
        conn.execute(
            "INSERT INTO model_usage (id, stage, reserved_microdollars) VALUES (%s, %s, %s)",
            (reservation_id, stage, cost),
        )
    return reservation_id


def record_model_usage(db: Database, reservation_id: UUID, *, input_tokens: int, output_tokens: int) -> None:
    cost = _microdollars(input_tokens, output_tokens)
    with db.connect() as conn:
        conn.execute("SELECT id FROM model_budget_lock WHERE id = 1 FOR UPDATE")
        usage = conn.execute(
            "SELECT input_tokens, output_tokens FROM model_usage WHERE id = %s", (reservation_id,),
        ).fetchone()
        if usage is None:
            raise ValueError("Unknown model reservation.")
        if usage["input_tokens"] is None:
            conn.execute(
                "UPDATE model_usage SET actual_microdollars = %s, input_tokens = %s, output_tokens = %s WHERE id = %s",
                (cost, input_tokens, output_tokens, reservation_id),
            )
        elif usage != {"input_tokens": input_tokens, "output_tokens": output_tokens}:
            raise ValueError("Reported usage differs from the recorded amount.")

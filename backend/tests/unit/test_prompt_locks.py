import uuid
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.prompts.locks import (
    _PROJECT_NAMESPACE,
    _PROMPT_SET_NAMESPACE,
    _advisory_lock_key,
    _advisory_xact_lock,
)


def test_advisory_lock_key_uses_full_uuid_entropy() -> None:
    first = uuid.UUID("12345678-0000-0000-0000-000000000001")
    second = uuid.UUID("12345678-0000-0000-0000-000000000002")

    assert first.bytes[:4] == second.bytes[:4]
    assert _advisory_lock_key(_PROMPT_SET_NAMESPACE, first) != _advisory_lock_key(
        _PROMPT_SET_NAMESPACE, second
    )


def test_advisory_lock_key_is_namespaced_and_stable() -> None:
    entity_id = uuid.UUID("12345678-1234-5678-9abc-def012345678")

    prompt_set_key = _advisory_lock_key(_PROMPT_SET_NAMESPACE, entity_id)

    assert prompt_set_key == 4629946208451514738
    assert prompt_set_key != _advisory_lock_key(_PROJECT_NAMESPACE, entity_id)
    assert -(2**63) <= prompt_set_key < 2**63


async def test_advisory_lock_executes_single_bigint_with_derived_key() -> None:
    entity_id = uuid.UUID(int=0)
    executed: list[Any] = []

    class FakeSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement: Any) -> None:
            executed.append(statement)

    await _advisory_xact_lock(
        cast(AsyncSession, FakeSession()), _PROMPT_SET_NAMESPACE, entity_id
    )

    assert len(executed) == 1
    assert str(executed[0]) == "SELECT pg_advisory_xact_lock(:key)"
    assert executed[0].compile().params == {"key": -8705120500123570735}

from __future__ import annotations

import hashlib

import pytest
from open_webui.agent.canonical import canonical_json, canonical_sha256
from open_webui.agent.decision_status import (
    NONTERMINAL_DECISION_EXECUTION_STATUSES,
    DecisionExecutionStatus,
)


def test_canonical_json_and_sha256_are_stable() -> None:
    left = {'z': ['é', {'b': 2, 'a': 1}], 'a': True}
    right = {'a': True, 'z': ['é', {'a': 1, 'b': 2}]}

    expected = '{"a":true,"z":["é",{"a":1,"b":2}]}'
    assert canonical_json(left) == expected
    assert canonical_json(right) == expected
    assert canonical_sha256(left) == hashlib.sha256(
        expected.encode('utf-8')
    ).hexdigest()
    assert canonical_sha256(left) == canonical_sha256(right)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match='finite'):
        canonical_json({'value': value})


def test_failing_is_a_typed_nonterminal_execution_status() -> None:
    assert DecisionExecutionStatus.FAILING.value == 'failing'
    assert 'failing' in NONTERMINAL_DECISION_EXECUTION_STATUSES

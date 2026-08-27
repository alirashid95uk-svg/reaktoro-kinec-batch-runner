"""Declarative, non-mutating DoE constraint evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    AllowedValuesConstraint,
    BoundsConstraint,
    ComparisonConstraint,
    Constraint,
    ConstraintLiteral,
    ForbiddenCombinationConstraint,
    Predicate,
    RequiredDependencyConstraint,
)
from .sampling import ResolvedParameter
from .targets import convert_unit


@dataclass(frozen=True)
class ConstraintOutcome:
    constraint_id: str
    passed: bool
    detail: str | None = None


def _parameter_map(parameters: list[ResolvedParameter]) -> dict[str, ResolvedParameter]:
    return {item.parameter_id: item for item in parameters}


def _literal_value(
    literal: ConstraintLiteral,
    parameter: ResolvedParameter,
    *,
    year_days: float | None,
) -> float | int:
    if isinstance(literal.value, str):
        raise ValueError(
            f"constraint literal for numeric parameter {parameter.parameter_id} must be numeric"
        )
    if parameter.canonical_unit == "1":
        if literal.unit is not None:
            raise ValueError(
                f"dimensionless constraint literal for {parameter.parameter_id} must omit unit"
            )
        return literal.value
    if literal.unit is None:
        raise ValueError(
            f"dimensional constraint literal for {parameter.parameter_id} requires unit"
        )
    return convert_unit(
        literal.value,
        literal.unit,
        parameter.canonical_unit,
        year_days=year_days,
    )


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "eq": return left == right
    if operator == "ne": return left != right
    if operator == "lt": return left < right
    if operator == "le": return left <= right
    if operator == "gt": return left > right
    if operator == "ge": return left >= right
    if operator == "in": return left in right
    if operator == "not_in": return left not in right
    raise ValueError(f"unsupported operator: {operator}")


def _predicate(
    predicate: Predicate,
    vector: dict[str, float | int],
    parameters: dict[str, ResolvedParameter],
    *,
    year_days: float | None,
) -> bool:
    if predicate.left_parameter_id not in vector:
        raise ValueError(f"unknown constraint parameter {predicate.left_parameter_id!r}")
    left_parameter = parameters[predicate.left_parameter_id]
    left = vector[predicate.left_parameter_id]
    if predicate.right_parameter_id is not None:
        right_id = predicate.right_parameter_id
        if right_id not in vector:
            raise ValueError(f"unknown constraint parameter {right_id!r}")
        right_parameter = parameters[right_id]
        if right_parameter.canonical_unit != left_parameter.canonical_unit:
            raise ValueError(
                "parameter comparison requires matching canonical units: "
                f"{left_parameter.canonical_unit} vs {right_parameter.canonical_unit}"
            )
        right: Any = vector[right_id]
    elif predicate.values is not None:
        right = [
            _literal_value(
                ConstraintLiteral(value=value, unit=predicate.unit),
                left_parameter,
                year_days=year_days,
            )
            for value in predicate.values
        ]
    else:
        assert predicate.value is not None
        right = _literal_value(
            ConstraintLiteral(value=predicate.value, unit=predicate.unit),
            left_parameter,
            year_days=year_days,
        )
    return _compare(left, predicate.operator, right)


def evaluate_constraints(
    constraints: list[Constraint],
    vector: dict[str, float | int],
    parameters: list[ResolvedParameter],
    *,
    year_days: float | None,
) -> list[ConstraintOutcome]:
    """Evaluate all constraints against canonical values without changing the vector."""
    parameter_map = _parameter_map(parameters)
    outcomes: list[ConstraintOutcome] = []
    for constraint in constraints:
        if isinstance(constraint, BoundsConstraint):
            parameter = parameter_map.get(constraint.parameter_id)
            if parameter is None:
                raise ValueError(f"unknown constraint parameter {constraint.parameter_id!r}")
            value = vector[constraint.parameter_id]
            passed = True
            if constraint.lower is not None:
                lower = _literal_value(constraint.lower, parameter, year_days=year_days)
                passed = passed and (
                    value >= lower if constraint.lower_inclusive else value > lower
                )
            if constraint.upper is not None:
                upper = _literal_value(constraint.upper, parameter, year_days=year_days)
                passed = passed and (
                    value <= upper if constraint.upper_inclusive else value < upper
                )
        elif isinstance(constraint, AllowedValuesConstraint):
            parameter = parameter_map.get(constraint.parameter_id)
            if parameter is None:
                raise ValueError(f"unknown constraint parameter {constraint.parameter_id!r}")
            allowed = [
                _literal_value(
                    ConstraintLiteral(value=value, unit=constraint.unit),
                    parameter,
                    year_days=year_days,
                )
                for value in constraint.values
            ]
            passed = vector[constraint.parameter_id] in allowed
        elif isinstance(constraint, ComparisonConstraint):
            passed = _predicate(
                constraint.predicate, vector, parameter_map, year_days=year_days
            )
        elif isinstance(constraint, RequiredDependencyConstraint):
            condition = all(
                _predicate(item, vector, parameter_map, year_days=year_days)
                for item in constraint.if_all
            )
            passed = (not condition) or all(
                _predicate(item, vector, parameter_map, year_days=year_days)
                for item in constraint.then_all
            )
        elif isinstance(constraint, ForbiddenCombinationConstraint):
            passed = not all(
                _predicate(item, vector, parameter_map, year_days=year_days)
                for item in constraint.all
            )
        else:
            raise TypeError(f"unsupported constraint model {type(constraint).__name__}")
        outcomes.append(
            ConstraintOutcome(
                constraint_id=constraint.constraint_id,
                passed=passed,
                detail=None if passed else f"constraint {constraint.constraint_id} rejected candidate",
            )
        )
    return outcomes

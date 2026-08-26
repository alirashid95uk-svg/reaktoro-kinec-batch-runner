"""Read-only configuration reference derived from the active Pydantic schema.

This module projects field annotations, defaults, constraints, descriptions,
and documented validator rules into records suitable for terminal or HTML
rendering. It never validates cases and owns no configuration semantics.
"""

from __future__ import annotations

import inspect
import json
import types
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from .case import CaseConfig


@dataclass(frozen=True)
class ConfigOption:
    """One user-facing field as defined by a particular Pydantic model."""

    path: str
    model: str
    type: str
    required: bool
    default: str | None
    allowed_values: tuple[str, ...]
    constraints: tuple[str, ...]
    description: str
    status: str | None


@dataclass(frozen=True)
class ConfigSection:
    """A configuration model at one YAML path, including its documented rules."""

    path: str
    model: str
    description: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class ConfigReference:
    """Complete immutable projection of the active source-case schema."""

    options: tuple[ConfigOption, ...]
    sections: tuple[ConfigSection, ...]

    def matching(self, query: str) -> "ConfigReference":
        """Return entries whose YAML path or defining model matches ``query``."""

        normalized = query.strip().lower()
        if not normalized:
            return self

        def matches(path: str, model: str) -> bool:
            lowered_path = path.lower()
            segments = lowered_path.replace("[]", "").replace(".*", "").split(".")
            return (
                normalized == lowered_path
                or lowered_path.startswith(normalized + ".")
                or normalized in segments
                or normalized == model.lower()
            )

        return ConfigReference(
            options=tuple(
                option
                for option in self.options
                if matches(option.path, option.model)
            ),
            sections=tuple(
                section
                for section in self.sections
                if matches(section.path, section.model)
            ),
        )


def configuration_reference() -> ConfigReference:
    """Build the current reference without mutating models or loading case YAML."""

    options: list[ConfigOption] = []
    sections: list[ConfigSection] = []
    _walk_model(CaseConfig, "", options, sections, ())
    return ConfigReference(
        tuple(dict.fromkeys(options)),
        tuple(dict.fromkeys(sections)),
    )


def top_level_sections() -> tuple[ConfigOption, ...]:
    """Return the top-level YAML blocks in declaration order."""

    return tuple(
        option for option in configuration_reference().options if "." not in option.path
    )


def render_text_reference(query: str | None = None) -> str:
    """Render concise terminal help for all top-level blocks or one query."""

    reference = configuration_reference()
    if not query:
        lines = [
            "Configuration help derived from batch_runner.config.CaseConfig.",
            "Use: python runner.py config --help <section-or-path>",
            "",
            "Top-level sections:",
        ]
        for option in top_level_sections():
            lines.append(f"  {option.path:<20} {option.description}")
        return "\n".join(lines)

    matched = reference.matching(query)
    if not matched.options and not matched.sections:
        available = ", ".join(option.path for option in top_level_sections())
        raise ValueError(
            f"unknown configuration section or path {query!r}; top-level sections: {available}"
        )

    lines = [f"Configuration reference: {query}", ""]
    for option in matched.options:
        qualifier = f" [{option.model}]" if _path_has_variants(option.path, matched) else ""
        lines.append(f"{option.path}{qualifier}")
        lines.append(f"  type: {option.type}")
        lines.append(f"  required: {'yes' if option.required else 'no'}")
        if option.default is not None:
            lines.append(f"  default: {option.default}")
        if option.allowed_values:
            lines.append(f"  allowed: {', '.join(option.allowed_values)}")
        if option.constraints:
            lines.append(f"  constraints: {', '.join(option.constraints)}")
        if option.status:
            lines.append(f"  status: {option.status}")
        lines.append(f"  {option.description}")
        lines.append("")

    rule_sections = [section for section in matched.sections if section.rules]
    if rule_sections:
        lines.append("Conditional and cross-field rules:")
        for section in rule_sections:
            label = section.path or "case"
            for rule in section.rules:
                lines.append(f"  {label} [{section.model}]: {rule}")
    return "\n".join(lines).rstrip()


def render_markdown_reference() -> str:
    """Render the complete configuration reference as generated Markdown."""

    reference = configuration_reference()
    lines = [
        "# Configuration Reference",
        "",
        "This page is generated from `CaseConfig`, its nested Pydantic fields, and "
        "documented validators. Edit the Python schema, not this page.",
        "",
        "Required state, defaults, allowed values, and constraints below are the "
        "runtime model's current declarations. Conditional rules are listed from "
        "the validators that enforce them.",
    ]
    for top_level in top_level_sections():
        prefix = top_level.path
        options = [
            option
            for option in reference.options
            if option.path == prefix or option.path.startswith(prefix + ".")
        ]
        sections = [
            section
            for section in reference.sections
            if section.path == prefix or section.path.startswith(prefix + ".")
        ]
        lines.extend(["", f"## `{prefix}`", "", top_level.description, ""])
        lines.append("| Path | Defined by | Type | Required | Default | Details |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for option in options:
            details = option.description
            additions = []
            if option.allowed_values:
                additions.append("Allowed: " + ", ".join(option.allowed_values))
            if option.constraints:
                additions.append("Constraints: " + ", ".join(option.constraints))
            if option.status:
                additions.append("Status: " + option.status)
            if additions:
                details = details + " " + "; ".join(additions) + "."
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{_escape_table(option.path)}`",
                        f"`{option.model}`",
                        f"`{_escape_table(option.type)}`",
                        "yes" if option.required else "no",
                        f"`{_escape_table(option.default)}`" if option.default is not None else "—",
                        _escape_table(details),
                    )
                )
                + " |"
            )
        rules = [
            (section, rule)
            for section in sections
            for rule in section.rules
        ]
        if rules:
            lines.extend(["", "### Conditional and cross-field rules", ""])
            for section, rule in rules:
                lines.append(f"- `{section.path or 'case'}` (`{section.model}`): {rule}")
    lines.append("")
    return "\n".join(lines)


def _walk_model(
    model: type[BaseModel],
    prefix: str,
    options: list[ConfigOption],
    sections: list[ConfigSection],
    ancestors: tuple[type[BaseModel], ...],
) -> None:
    if model in ancestors:
        return
    sections.append(
        ConfigSection(
            path=prefix,
            model=model.__name__,
            description=inspect.cleandoc(model.__doc__ or ""),
            rules=_validator_rules(model),
        )
    )
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        options.append(
            ConfigOption(
                path=path,
                model=model.__name__,
                type=_type_label(field.annotation),
                required=field.is_required(),
                default=_default_text(field),
                allowed_values=_allowed_values(field.annotation),
                constraints=_constraints(field.metadata),
                description=field.description or "",
                status=_status(field),
            )
        )
        for nested_model, suffix in _nested_models(field.annotation):
            _walk_model(
                nested_model,
                path + suffix,
                options,
                sections,
                ancestors + (model,),
            )


def _nested_models(annotation: Any, suffix: str = "") -> tuple[tuple[type[BaseModel], str], ...]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _nested_models(args[0], suffix)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return ((annotation, suffix),)
    if origin is list:
        return _nested_models(args[0], suffix + "[]")
    if origin is dict:
        return _nested_models(args[1], suffix + ".*")
    if origin in (Union, types.UnionType):
        nested: list[tuple[type[BaseModel], str]] = []
        for item in args:
            nested.extend(_nested_models(item, suffix))
        return tuple(nested)
    return ()


def _type_label(annotation: Any) -> str:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _type_label(args[0])
    if origin is Literal:
        return "literal[" + ", ".join(_value_text(value) for value in args) + "]"
    if origin in (Union, types.UnionType):
        return " | ".join(_type_label(item) for item in args)
    if origin is list:
        return f"list[{_type_label(args[0])}]"
    if origin is dict:
        return f"mapping[{_type_label(args[0])}, {_type_label(args[1])}]"
    if annotation is type(None):
        return "null"
    return getattr(annotation, "__name__", str(annotation).replace("typing.", ""))


def _allowed_values(annotation: Any) -> tuple[str, ...]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _allowed_values(args[0])
    if origin is Literal:
        return tuple(_value_text(value) for value in args)
    if origin in (Union, types.UnionType):
        values = [value for item in args for value in _allowed_values(item)]
        return tuple(dict.fromkeys(values))
    return ()


def _constraints(metadata: list[Any]) -> tuple[str, ...]:
    labels = {
        "gt": ">",
        "ge": ">=",
        "lt": "<",
        "le": "<=",
        "min_length": "minimum length",
        "max_length": "maximum length",
        "multiple_of": "multiple of",
    }
    constraints: list[str] = []
    for item in metadata:
        for attribute, label in labels.items():
            value = getattr(item, attribute, None)
            if value is not None:
                constraints.append(f"{label} {value}")
        if getattr(item, "allow_inf_nan", None) is False:
            constraints.append("finite")
    return tuple(dict.fromkeys(constraints))


def _default_text(field: Any) -> str | None:
    extra = field.json_schema_extra
    if isinstance(extra, dict) and "x-effective-default" in extra:
        value = _value_text(extra["x-effective-default"])
        condition = str(extra.get("x-default-when", "the owning condition applies"))
        return f"{value} when {condition}; otherwise null"
    if field.is_required():
        return None
    try:
        value = field.get_default(call_default_factory=True)
    except TypeError:
        return "computed"
    return _value_text(value)


def _status(field: Any) -> str | None:
    deprecated = getattr(field, "deprecated", None)
    if deprecated:
        return str(deprecated) if not isinstance(deprecated, bool) else "deprecated"
    extra = field.json_schema_extra
    if isinstance(extra, dict) and extra.get("x-status"):
        return str(extra["x-status"])
    return None


def _validator_rules(model: type[BaseModel]) -> tuple[str, ...]:
    decorators = getattr(model, "__pydantic_decorators__", None)
    if decorators is None:
        return ()
    rules: list[str] = []
    for collection_name in ("field_validators", "model_validators"):
        collection = getattr(decorators, collection_name, {})
        for decorator in collection.values():
            doc = inspect.getdoc(decorator.func)
            if doc:
                rules.append(" ".join(doc.split()))
    return tuple(dict.fromkeys(rules))


def _path_has_variants(path: str, reference: ConfigReference) -> bool:
    return sum(option.path == path for option in reference.options) > 1


def _value_text(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(value)


def _escape_table(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")

"""Extract a FormDefinition from a Pydantic model class.

The developer authors their response schema as a normal Pydantic BaseModel,
attaching form primitives via Annotated where they want explicit control:

    class WireApproval(BaseModel):
        approve: Annotated[bool, switch(label="Approve this wire?")]
        comment: Annotated[str | None, long_text(label="Reason")] = None

Fields without an Annotated primitive fall back to type-based inference
(see infer.py). `name` and `required` are always filled by this module,
regardless of what the developer passed to the DSL helper.
"""

from __future__ import annotations

from pydantic import BaseModel

from awaithumans.forms.base import FormFieldBase
from awaithumans.forms.definition import FormDefinition
from awaithumans.forms.infer import infer_field_from_type


def extract_form(model_cls: type[BaseModel]) -> FormDefinition:
    """Walk a Pydantic model and build its FormDefinition."""
    fields: list[FormFieldBase] = []
    for attr_name, field_info in model_cls.model_fields.items():
        metadata = list(field_info.metadata or [])
        explicit = next(
            (m for m in metadata if isinstance(m, FormFieldBase)),
            None,
        )
        is_required = field_info.is_required()

        if explicit is not None:
            field = explicit.model_copy(
                update={
                    "name": attr_name,
                    "required": is_required,
                    "label": explicit.label or _humanize(attr_name),
                }
            )
        else:
            field = infer_field_from_type(attr_name, field_info.annotation, is_required)

        fields.append(field)

    return FormDefinition(fields=fields)


def _humanize(name: str) -> str:
    """snake_case attribute name → 'Title Case' label."""
    return name.replace("_", " ").replace("-", " ").strip().title()

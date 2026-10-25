"""A model that answers any JSON schema with a valid instance of it: a stand-in for the answering model and for the
RAGAS judge, so the evaluation's plumbing is exercised end to end without a language model."""
import json

from testamur.modeltier import Reply


def instance(schema: dict, defs: dict | None = None, hint: str = "") -> object:
    defs = defs if defs is not None else schema.get("$defs") or schema.get("definitions") or {}
    if "$ref" in schema:
        return instance(defs[schema["$ref"].split("/")[-1]], defs, hint)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return instance(options[0], defs, hint)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {k: instance(v, defs, k) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [instance(schema.get("items", {"type": "string"}), defs, hint)]
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    return f"the {hint or 'value'} a judge would write here"


class SchemaModel:
    """Answers every call with an instance of its schema; `fill` may overwrite fields from the messages."""
    model_id, fingerprint = "schema-fake", "schema-fake"

    def __init__(self, fill=None):
        self.fill = fill
        self.calls = 0

    def chat(self, messages, max_tokens=200, schema=None):
        self.calls += 1
        out = instance(schema or {"type": "object", "properties": {}})
        if self.fill:
            out = self.fill(messages, schema, out)
        return Reply(json.dumps(out), 100, 20)

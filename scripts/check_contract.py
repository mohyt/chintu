"""Validate the OpenAPI document and its synthetic wire examples."""

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
spec = json.loads((ROOT / "contracts/openapi.json").read_text())
try:
    from openapi_spec_validator import validate_spec
except ImportError:
    print("NOTE: full OpenAPI validator unavailable; checking structure and JSON schemas")
else:
    validate_spec(spec)
    print("PASS: full OpenAPI 3.1 specification validation")

assert spec["openapi"] == "3.1.0"
operation_ids = set()
for path, methods in spec["paths"].items():
    for operation in methods.values():
        assert operation["operationId"] not in operation_ids
        operation_ids.add(operation["operationId"])
        path_parameters = {
            parameter["name"]
            for parameter in operation["parameters"]
            if parameter["in"] == "path" and parameter["required"]
        }
        assert path_parameters == set(re.findall(r"\{([^}]+)\}", path))
        assert operation["responses"]
for schema in spec["components"]["schemas"].values():
    Draft202012Validator.check_schema(schema)


def check_refs(value: object) -> None:
    if isinstance(value, dict):
        if "$ref" in value:
            pointer = value["$ref"]
            assert pointer.startswith("#/"), pointer
            resolved = spec
            for part in pointer[2:].split("/"):
                resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
        for child in value.values():
            check_refs(child)
    elif isinstance(value, list):
        for child in value:
            check_refs(child)


check_refs(spec)


def validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$ref": f"#/components/schemas/{schema_name}",
            "components": spec["components"],
        },
        format_checker=FormatChecker(),
    )


manifest = json.loads((ROOT / "contracts/examples/manifest.json").read_text())
for filename, schema_name in manifest.items():
    value = json.loads((ROOT / "contracts/examples" / filename).read_text())
    validator(schema_name).validate(value)

submission = json.loads((ROOT / "contracts/examples/submit-task.json").read_text())
for invalid in (
    {**submission, "audio": "unexpected audio payload"},
    {**submission, "instruction": "x" * 8001},
    {**submission, "submission_key": "not-a-uuid"},
    {**submission, "project_id": "../../private"},
):
    assert not validator("SubmitTask").is_valid(invalid)

page = json.loads((ROOT / "contracts/examples/event-page.json").read_text())
bad_event = {**page["events"][0], "payload": {"text": "wrong acceptance shape"}}
assert not validator("Event").is_valid(bad_event)
delta = {**page["events"][0], "kind": "output_delta", "payload": {"text": "partial repl"}}
assert validator("Event").is_valid(delta)
assert not validator("Event").is_valid({**delta, "payload": {"text": "x", "final": True}})

conversation = json.loads((ROOT / "contracts/examples/conversation-created.json").read_text())["conversation"]
create = {key: conversation[key] for key in ("conversation_id", "project_id", "provider",
                                              "execution_profile", "working_directory", "title")}
assert validator("ConversationCreate").is_valid(create)
for invalid in (
    {**create, "provider_session_id": "sessions are assigned by the gateway"},
    {**create, "working_directory": "x" * 1025},
    {**create, "conversation_id": "not-a-uuid"},
):
    assert not validator("ConversationCreate").is_valid(invalid)

schema_count = len(spec["components"]["schemas"])
print(f"PASS: contract structure/references, {schema_count} JSON schemas, {len(manifest)} wire examples, and 9 invalid-payload checks")

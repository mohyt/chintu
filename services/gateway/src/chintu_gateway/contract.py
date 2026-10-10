import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .errors import ApiError


DEFAULT_CONTRACT = Path(__file__).resolve().parents[4] / "contracts/openapi.json"


class Contract:
    """Validate against the shared contract, without maintaining duplicate HTTP models."""

    def __init__(self, path=DEFAULT_CONTRACT):
        self.document = json.loads(Path(path).read_text())
        self.validators = {
            name: Draft202012Validator(
                {"$ref": f"#/components/schemas/{name}",
                 "components": self.document["components"]}, format_checker=FormatChecker()
            ) for name in self.document["components"]["schemas"]
        }

    def validate(self, name, value):
        self.validators[name].validate(value)
        return value

    def request(self, name, value):
        try:
            return self.validate(name, value)
        except ValidationError:
            # Validation errors can contain the original credential/instruction.
            raise ApiError(422, "invalid_request", "Request does not match the API schema.") from None

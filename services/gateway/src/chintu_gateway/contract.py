import json
import os
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .errors import ApiError


CONTRACT_ENV = "CHINTU_CONTRACT"
CONTRACT_RELATIVE = Path("contracts") / "openapi.json"
SHARE_RELATIVE = Path("share") / "chintu" / "openapi.json"
# services/gateway/src/chintu_gateway/contract.py -> parents[4] is the repository root.
REPO_CONTRACT = Path(__file__).resolve().parents[4] / CONTRACT_RELATIVE


def contract_candidates(env=None, frozen=None, bundle_root=None, prefix=None):
    """Where the shared contract may live, in order: `CHINTU_CONTRACT`, the PyInstaller
    bundle, the repository checkout, then `share/chintu/` under the interpreter prefix
    (`/opt/chintu/venv/share/...`) or beside it (`/opt/chintu/share/...`)."""
    env = os.environ if env is None else env
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    prefix = Path(sys.prefix if prefix is None else prefix)
    candidates = []
    if env.get(CONTRACT_ENV):
        candidates.append(Path(env[CONTRACT_ENV]).expanduser())
    if frozen:
        root = bundle_root or getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent
        candidates.append(Path(root) / CONTRACT_RELATIVE)
    candidates += [REPO_CONTRACT, prefix / SHARE_RELATIVE, prefix.parent / SHARE_RELATIVE]
    return candidates


def resolve_contract(env=None, frozen=None, bundle_root=None, prefix=None):
    """The first existing candidate; the repository path when none exists, so errors name a sensible file."""
    for candidate in contract_candidates(env, frozen, bundle_root, prefix):
        if candidate.is_file():
            return candidate
    return REPO_CONTRACT


DEFAULT_CONTRACT = resolve_contract()


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

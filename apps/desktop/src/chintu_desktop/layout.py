"""Where Chintu keeps its files in each of the three ways the desktop app can run.

- "repo": a development checkout. The gateway is `<repo>/.venv/bin/chintu-gateway`,
  the contract `<repo>/contracts/openapi.json`, and everything the gateway writes
  (database, local secret, operator config, logs) lives in `<repo>/var/`.
- "bundle": one PyInstaller executable that carries both the tray and the gateway.
  The gateway is started as `<executable> gateway ...` (see `app.main`), the
  contract is read from the bundle, and data goes to the per-user Chintu folder.
- "installed": console scripts in a virtualenv (the Debian package installs
  `/opt/chintu/venv`). The gateway is `chintu-gateway` next to the running
  interpreter, the contract is `<prefix>/share/chintu/openapi.json`, and data goes
  to the per-user Chintu folder.

The per-user Chintu folder is `CHINTU_HOME` when set, else
`~/Library/Application Support/Chintu` on macOS and `$XDG_DATA_HOME/chintu`
(default `~/.local/share/chintu`) elsewhere. It is created owner-only (0700) on
first use because it holds the local secret and the operator config.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from chintu_adapters.agent_sdk import DEFAULT_MODEL
except ImportError:  # the tray can be installed without the adapters; keep the gateway's default in step
    DEFAULT_MODEL = "claude-opus-5-5"

REPO_ENV = "CHINTU_REPO"
HOME_ENV = "CHINTU_HOME"
CONTRACT_ENV = "CHINTU_CONTRACT"

CONFIG_NAME = "gateway.local.json"
DATABASE_NAME = "chintu.sqlite3"
SECRET_NAME = "local.secret"
GATEWAY_LOG_NAME = "desktop-gateway.log"
SETTINGS_NAME = "desktop.json"
CONTRACT_RELATIVE = Path("contracts") / "openapi.json"
GATEWAY_RELATIVE = Path(".venv") / "bin" / "chintu-gateway"
SHARE_RELATIVE = Path("share") / "chintu" / "openapi.json"


@dataclass(frozen=True)
class Layout:
    kind: str                           # "repo" | "bundle" | "installed"
    home: Path                          # data dir holding database, secret, config, logs
    gateway_command: tuple[str, ...]    # executable prefix for the gateway
    contract: Path | None               # contracts/openapi.json, None when unknown

    @property
    def config(self):
        return self.home / CONFIG_NAME

    @property
    def database(self):
        return self.home / DATABASE_NAME

    @property
    def secret(self):
        return self.home / SECRET_NAME

    @property
    def gateway_log(self):
        return self.home / GATEWAY_LOG_NAME

    @property
    def settings(self):
        """The tray's own settings (the gateway port it chose); the gateway never reads it."""
        return self.home / SETTINGS_NAME

    @property
    def notes_dir(self):
        return Path.home() / "Documents" / "Chintu Notes"

    @property
    def repo(self):
        """The checkout root in the repository layout, else None."""
        return self.home.parent if self.kind == "repo" else None

    @property
    def gateway_executable(self):
        """The file that must exist for "Start daemon" to work."""
        return Path(self.gateway_command[0])

    @property
    def desktop_executable(self):
        """The program an autostart entry launches: the bundle itself, else `chintu-desktop` beside the gateway."""
        if self.kind == "bundle":
            return self.gateway_executable
        return self.gateway_executable.parent / "chintu-desktop"

    @classmethod
    def detect(cls, repo=None, home=None, env=None, platform=None, frozen=None, executable=None):
        """Work out the layout. Explicit arguments win over the environment, which wins over inspection."""
        env = os.environ if env is None else env
        platform = platform or sys.platform
        frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        prefix = Path(sys.prefix) if executable is None else Path(executable).parent.parent
        executable = Path(executable or sys.executable)

        repo = repo or env.get(REPO_ENV)
        if repo:
            return cls.for_repo(repo)
        if frozen:
            bundle_root = Path(getattr(sys, "_MEIPASS", None) or executable.parent)
            contract = _env_contract(env) or bundle_root / CONTRACT_RELATIVE
            return cls("bundle", data_home(home, env, platform), (str(executable), "gateway"), contract)
        checkout = _package_checkout()
        if checkout is not None:
            return cls.for_repo(checkout)
        contract = _first_existing([_env_contract(env), prefix / SHARE_RELATIVE, prefix.parent / SHARE_RELATIVE])
        return cls("installed", data_home(home, env, platform), (str(executable.parent / "chintu-gateway"),), contract)

    @classmethod
    def for_repo(cls, repo):
        repo = Path(repo).expanduser().resolve()
        return cls("repo", repo / "var", (str(repo / GATEWAY_RELATIVE),), repo / CONTRACT_RELATIVE)

    def ensure_home(self):
        """Create the data dir owner-only on first use; an existing folder's mode is left alone."""
        if self.home.is_dir():
            return False
        self.home.mkdir(parents=True, mode=0o700)
        os.chmod(self.home, 0o700)
        return True

    def ensure_config(self):
        """Write a default `gateway.local.json` (mode 0600) when missing; never in a repo checkout.

        Returns True when a file was written. The default enables the Claude Agent SDK
        provider on the owner's home folder, so a fresh install starts the real gateway.
        """
        if self.kind == "repo" or self.config.exists():
            return False
        self.ensure_home()
        text = json.dumps(default_config(self), indent=2) + "\n"
        fd = os.open(self.config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def describe(self):
        """JSON-friendly summary for `chintu-desktop status`."""
        return {"kind": self.kind, "home": str(self.home), "config": str(self.config),
                "contract": None if self.contract is None else str(self.contract),
                "gateway_command": list(self.gateway_command)}


def default_config(layout):
    """The operator config a fresh install starts with; must pass `chintu-gateway check-config`.

    `executable: "claude"` names the Claude Code CLI by its bare name: the gateway looks it
    up on PATH and otherwise falls back to the CLI the claude-agent-sdk package ships, so
    the packaged app works without a separately installed `claude`.
    """
    return {
        "version": 1,
        "database": DATABASE_NAME,
        "queue_limit": 10,
        "token_ttl_days": 30,
        "projects": [{"project_id": "home", "display_name": "Home folder", "path": str(Path.home()),
                      "execution_profiles": ["inspect", "develop"]}],
        "runtimes": {
            "claude": {"enabled": False, "executable": "claude", "login_method": None, "evidence": None},
            "codex": {"enabled": False, "executable": "codex", "login_method": None, "evidence": None},
            "claude-agent": {"enabled": True, "executable": "claude", "model": DEFAULT_MODEL,
                             "profiles": ["inspect", "develop"], "load_user_settings": True, "persona": None},
        },
        "notes_dir": "~/Documents/Chintu Notes",
    }


def data_home(home=None, env=None, platform=None):
    """The per-user Chintu folder for the bundle and installed layouts."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    chosen = home or env.get(HOME_ENV)
    if chosen:
        return Path(chosen).expanduser().resolve()
    user_home = Path(env.get("HOME") or Path.home())
    if platform == "darwin":
        return user_home / "Library" / "Application Support" / "Chintu"
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else user_home / ".local" / "share"
    return base / "chintu"


def _env_contract(env):
    value = env.get(CONTRACT_ENV)
    return Path(value).expanduser() if value else None


def _first_existing(candidates):
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _package_checkout():
    """The repository root when this package is imported from a checkout, else None.

    apps/desktop/src/chintu_desktop/layout.py -> parents[4] is the repository root.
    """
    try:
        root = Path(__file__).resolve().parents[4]
    except IndexError:
        return None
    if (root / CONTRACT_RELATIVE).is_file() and (root / GATEWAY_RELATIVE).is_file():
        return root
    return None

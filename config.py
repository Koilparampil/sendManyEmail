"""Settings for the Validated Titles Sender.

Everything is read once, at startup, by load_settings(). No other module
reads environment variables, so the order modules are imported in doesn't
matter.

Values come from, highest priority first:

1. real environment variables
2. a .env file next to ValidatedTitlesSender.exe (built app only)
3. the .env bundled into the build, or the project's .env when running
   from source
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import dotenv_values

# Old setting name -> current name. Old names still work, with a warning.
RENAMED_SETTINGS = {
    "VALUE": "CLIENT_SECRET",
    "SENDER": "CRM_USERNAME",
    "APP_CLIENT_ID-2": "APP_CLIENT_ID_2",
    "VALUE-2": "CLIENT_SECRET_2",
}

REQUIRED_SETTINGS = ("APP_CLIENT_ID", "DIRECTORY_TENANT_ID", "CLIENT_SECRET", "SOURCE_IMAGES_DIR")


class ConfigError(Exception):
    """The settings are missing or wrong. The message is shown to the user."""


@dataclass(frozen=True)
class AppRegistration:
    """An Entra ID app registration used to send mail through Microsoft Graph."""

    client_id: str
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    graph: AppRegistration
    graph_backup: AppRegistration | None
    source_images_dir: str
    sender_email: str = ""
    crm_username: str = ""
    env_files: tuple[str, ...] = ()  # the .env files that were read, highest priority first
    warnings: tuple[str, ...] = ()  # problems worth logging that don't stop the app


def env_file_candidates() -> list[str]:
    """Return the .env files to read, highest priority first."""
    if getattr(sys, "frozen", False):
        paths = [os.path.join(os.path.dirname(sys.executable), ".env")]
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            paths.append(os.path.join(bundle_dir, ".env"))
    else:
        paths = [os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")]

    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:  # older PyInstaller versions bundle files next to the .exe
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def load_settings(
    env_files: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Read and check the settings. Raises ConfigError listing every problem."""
    candidates = env_file_candidates() if env_files is None else env_files
    environ = os.environ if environ is None else environ

    values: dict[str, str] = {}
    used: list[str] = []
    for path in reversed(candidates):  # lowest priority first, so later files win
        if os.path.isfile(path):
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})
            used.insert(0, path)
    values.update(environ)

    warnings: list[str] = []

    def get(name: str) -> str:
        value = (values.get(name) or "").strip()
        if value:
            return value
        for old_name, new_name in RENAMED_SETTINGS.items():
            old_value = (values.get(old_name) or "").strip() if new_name == name else ""
            if old_value:
                warnings.append(f"{old_name} is an old setting name. Rename it to {name} in .env.")
                return old_value
        return ""

    found = {name: get(name) for name in REQUIRED_SETTINGS}
    problems = [f"{name} isn't set." for name, value in found.items() if not value]

    source_images_dir = found["SOURCE_IMAGES_DIR"]
    if source_images_dir:
        source_images_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(source_images_dir)))
        if not os.path.isdir(source_images_dir):
            problems.append(
                f"The SOURCE_IMAGES_DIR folder doesn't exist:\n    {source_images_dir}\n"
                "    Check that the SourceImages library is synced with OneDrive on this PC, "
                "or correct the path."
            )

    if problems:
        raise ConfigError(_describe_problems(problems, used, candidates))

    backup_id, backup_secret = get("APP_CLIENT_ID_2"), get("CLIENT_SECRET_2")
    backup = AppRegistration(backup_id, backup_secret) if backup_id and backup_secret else None
    if bool(backup_id) != bool(backup_secret):
        warnings.append(
            "Only one of APP_CLIENT_ID_2 and CLIENT_SECRET_2 is set, so the backup credentials aren't used."
        )

    return Settings(
        tenant_id=found["DIRECTORY_TENANT_ID"],
        graph=AppRegistration(found["APP_CLIENT_ID"], found["CLIENT_SECRET"]),
        graph_backup=backup,
        source_images_dir=source_images_dir,
        sender_email=get("SENDER_EMAIL"),
        crm_username=get("CRM_USERNAME"),
        env_files=tuple(used),
        warnings=tuple(dict.fromkeys(warnings)),  # drop repeats, keep order
    )


def _describe_problems(problems: list[str], used: list[str], candidates: list[str]) -> str:
    lines = ["The app can't start until these settings are fixed:", ""]
    lines += [f"  • {problem}" for problem in problems]
    lines.append("")
    if used:
        lines.append("Settings were read from:")
        lines += [f"    {path}" for path in used]
    else:
        lines.append("No .env file was found. The app looked for:")
        lines += [f"    {path}" for path in candidates]
    return "\n".join(lines)

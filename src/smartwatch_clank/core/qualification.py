from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class ExecutionProvenance(StrEnum):
    """Authority classes that Smartwatch actually supports.

    SCHEDULED is asserted only by the tracked scheduler launchers, MANUAL by
    the CLI/dashboard entry points, and UNKNOWN is the safe value for direct
    library callers or legacy rows. DEPLOY and RECOVERY are deliberately not
    claimed here: Smartwatch has no collector execution path that can
    authoritatively assert either class.
    """

    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


def normalize_provenance(value: ExecutionProvenance | str | None) -> ExecutionProvenance:
    if isinstance(value, ExecutionProvenance):
        return value
    if value is None:
        return ExecutionProvenance.UNKNOWN
    try:
        return ExecutionProvenance(str(value).strip().upper())
    except ValueError:
        return ExecutionProvenance.UNKNOWN


QUALIFICATION_MATERIAL_VERSION = "smartwatch-qualification-material-v1"


@dataclass(frozen=True, slots=True)
class QualificationMaterial:
    """Stable material identity for the qualification contract.

    The digest includes only release/config/schema inputs whose changes can
    invalidate qualification. Runtime timestamps, host identity, process IDs,
    and observation content are intentionally excluded.
    """

    app_version: str | None
    config_fingerprint: str | None
    git_revision: str | None
    schema_version: int
    execution_scope: str
    contract_version: str = QUALIFICATION_MATERIAL_VERSION

    def components(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "app_version": self.app_version,
            "config_fingerprint": self.config_fingerprint,
            "git_revision": self.git_revision,
            "schema_version": self.schema_version,
            "execution_scope": self.execution_scope,
        }

    def identity(self) -> str:
        payload = json.dumps(self.components(), sort_keys=True, separators=(",", ":"))
        return "swq1-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def trustworthy(self) -> bool:
        # Local/dev values remain visible as UNKNOWN and cannot pass the
        # qualification gate. A known release revision and resolved config
        # are required to qualify production evidence.
        return bool(
            self.app_version
            and self.config_fingerprint
            and self.git_revision
            and self.git_revision.lower() != "unknown"
        )


@dataclass(frozen=True, slots=True)
class QualificationEpoch:
    collector: str
    epoch_id: str
    material_identity: str
    started_at: str
    execution_id: str
    provenance: ExecutionProvenance
    reason: str
    previous_epoch_id: str | None
    previous_material_identity: str | None


@dataclass(frozen=True, slots=True)
class QualificationGate:
    collector: str
    epoch_id: str | None
    material_identity: str | None
    eligible: bool
    reason: str
    execution_id: str | None = None


def new_epoch_id(collector: str) -> str:
    return f"swq-{collector}-{uuid.uuid4().hex}"


def iso_now(value: datetime) -> str:
    return value.isoformat()

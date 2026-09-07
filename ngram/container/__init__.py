"""Portable and canonical-hybrid ngram continuity primitives.

Archives are integrity checked and credentials are excluded. Hybrid activation
uses a database-backed canonical-writer lease; archives are not encrypted or
cryptographically signed.
"""

from ngram.container.archive import (
    extract_archive,
    load_artifact_manifest,
    pack_archive,
    verify_artifact,
)
from ngram.container.format import (
    FORMAT_PROFILE,
    SPEC_VERSION,
    ContainerError,
    VerificationReport,
    load_manifest,
    verify_container,
)
from ngram.container.hybrid import (
    HybridRestoreResult,
    export_hybrid_entity,
    restore_hybrid_artifact,
)
from ngram.container.lease import CanonicalLease, PostgresLeaseManager
from ngram.container.live import (
    LiveContainerMount,
    LocalMountLock,
    container_control_dir,
    pack_locked_live_container,
    recover_interrupted_container,
)
from ngram.container.migration import (
    commit_canonical_json_update,
    export_legacy_entity,
    migrate_legacy_entity,
    resolve_entity_identity,
)
from ngram.container.object_inventory import (
    discover_attachment_refs,
    export_attachment_inventory,
    load_attachment_inventory,
    materialize_attachments,
    materialize_attachments_local,
)
from ngram.container.postgres_snapshot import restore_postgres_snapshot, snapshot_postgres
from ngram.container.restore import (
    hydrate_runtime_state,
    restore_artifact,
    restore_legacy_entity,
    runtime_config_from_container,
    runtime_entity_key,
)

__all__ = [
    "FORMAT_PROFILE",
    "SPEC_VERSION",
    "CanonicalLease",
    "ContainerError",
    "HybridRestoreResult",
    "LiveContainerMount",
    "LocalMountLock",
    "PostgresLeaseManager",
    "VerificationReport",
    "commit_canonical_json_update",
    "container_control_dir",
    "discover_attachment_refs",
    "export_attachment_inventory",
    "export_hybrid_entity",
    "export_legacy_entity",
    "extract_archive",
    "hydrate_runtime_state",
    "load_artifact_manifest",
    "load_attachment_inventory",
    "load_manifest",
    "materialize_attachments",
    "materialize_attachments_local",
    "migrate_legacy_entity",
    "pack_archive",
    "pack_locked_live_container",
    "recover_interrupted_container",
    "resolve_entity_identity",
    "restore_artifact",
    "restore_hybrid_artifact",
    "restore_legacy_entity",
    "restore_postgres_snapshot",
    "runtime_config_from_container",
    "runtime_entity_key",
    "snapshot_postgres",
    "verify_artifact",
    "verify_container",
]

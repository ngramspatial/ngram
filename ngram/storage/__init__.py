"""Storage facades: relational store factory + attachment backends."""

from ngram.storage.attachment_ingestion import (
    attachment_refs_episode_note,
    load_stored_attachment,
    persist_incoming_attachments,
)
from ngram.storage.attachments import (
    LocalDiskAttachmentStore,
    ObjectStorageAttachmentStore,
    build_attachment_store,
)
from ngram.storage.factory import create_memory_store
from ngram.storage.protocol import AttachmentBlobStore

__all__ = [
    "AttachmentBlobStore",
    "LocalDiskAttachmentStore",
    "ObjectStorageAttachmentStore",
    "attachment_refs_episode_note",
    "build_attachment_store",
    "create_memory_store",
    "load_stored_attachment",
    "persist_incoming_attachments",
]

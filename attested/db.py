"""Connection. The same code runs against a local replica set or Atlas.

Change streams and multi-document transactions both require a replica set, which is why the
local deployment is a single-node replica set rather than a standalone mongod. Atlas is a
replica set by definition, so behaviour is identical and only the URI changes.
"""
import os
from pymongo import MongoClient

DEFAULT_URI = "mongodb://127.0.0.1:27019/?directConnection=true"
DB_NAME = os.environ.get("ATTESTED_DB", "attested")


def uri() -> str:
    return os.environ.get("MONGODB_URI", DEFAULT_URI)


def client() -> MongoClient:
    return MongoClient(uri(), serverSelectionTimeoutMS=8000, tz_aware=True)


def db():
    return client()[DB_NAME]


def require_replica_set(c: MongoClient) -> None:
    """Fail loudly rather than silently losing change streams."""
    hello = c.admin.command("hello")
    if not hello.get("setName"):
        raise RuntimeError(
            "This deployment is not a replica set, so change streams and transactions are "
            "unavailable. Start mongod with --replSet and run replSetInitiate, or point "
            "MONGODB_URI at an Atlas cluster."
        )

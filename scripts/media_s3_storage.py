"""
Test: verify media (VFS files + attachments) round-trips through Supabase S3 Storage.

This is the media-tier equivalent of chat_with_storage.py. It wires the agent's
session persistence to use Supabase's S3-compatible bucket for binary files, then
verifies files actually land in the bucket and come back intact on resume.

Configure via .env (or environment):

    # --- DB (Tier 1) ---
    LOGICORE_STORAGE_DB_URL=postgresql://postgres.xxx:PASS@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres

    # --- Media (Tier 3) = Supabase Storage S3-compatible API ---
    LOGICORE_STORAGE_MEDIA_ROOT=s3://<your-bucket>/logicore
    LOGICORE_STORAGE_S3_ENDPOINT=https://<project-ref>.supabase.co/storage/v1/s3
    LOGICORE_STORAGE_S3_ACCESS_KEY=<project-ref>
    LOGICORE_STORAGE_S3_SECRET_KEY=<service_role key>
    LOGICORE_STORAGE_S3_REGION=ap-northeast-2

Where to find the values in Supabase dashboard:
    - project-ref / bucket:  Storage → Buckets (create one, e.g. "logicore")
    - S3 endpoint:           Storage → (scroll down) "S3 Access" / "Use the S3 API"
    - access key:            your project reference id (the part before .supabase.co)
    - secret key:            Settings → API → service_role key
    - region:                Settings → Database → region

Run:  python scripts/media_s3_storage.py
"""
import sys, os, json, tempfile
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

from logicore.storage import StorageManager, StorageConfig, DatabaseConfig, SnapshotConfig, MediaConfig
from logicore.agent.session import AgentSession
from logicore.agent.base import Agent


def build_storage():
    db_url = os.getenv("LOGICORE_STORAGE_DB_URL", "")
    media_root = os.getenv("LOGICORE_STORAGE_MEDIA_ROOT", "")
    if not media_root.startswith("s3://"):
        print("[SKIP] LOGICORE_STORAGE_MEDIA_ROOT is not s3:// — set it to test Supabase S3.")
        print("        Example: LOGICORE_STORAGE_MEDIA_ROOT=s3://logicore/logicore")
        sys.exit(1)

    cfg = StorageConfig(
        database=DatabaseConfig(url=db_url) if db_url else DatabaseConfig(),
        snapshot=SnapshotConfig(enabled=False),
        media=MediaConfig(
            root=media_root,
            endpoint_url=os.getenv("LOGICORE_STORAGE_S3_ENDPOINT"),
            aws_access_key_id=os.getenv("LOGICORE_STORAGE_S3_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("LOGICORE_STORAGE_S3_SECRET_KEY"),
            region=os.getenv("LOGICORE_STORAGE_S3_REGION", "us-east-1"),
        ),
    )
    mgr = StorageManager(cfg)
    mgr.initialize()
    return mgr


def main():
    print("=== Media Tier: Supabase S3 Storage Test ===\n")
    mgr = build_storage()
    print("[OK] Storage initialized with S3 media backend\n")

    session_id = "s3-media-test"

    # 1. Simulate the agent persisting VFS files through the S3 tier
    vfs_files = {
        "notes.txt": "hello from supabase s3",
        "data.json": json.dumps({"ok": True, "n": 42}),
        "drawing.png": "BASE64IMAGEDATA==",  # images stored as base64 string in VFS
    }

    print("--- Step 1: agent persists VFS files to S3 ---")
    sess = AgentSession(session_id, "system")
    sess.metadata["tags"] = {"test": "s3"}
    for name, content in vfs_files.items():
        sess.files[name] = content

    # Replicate exactly what Agent._persist_session does for the media tier
    from logicore.agent.base import Agent as _A
    file_refs = []
    for fname, content in sess.files.items():
        import re
        file_id = re.sub(r"[^A-Za-z0-9._-]", "_", fname) or "file"
        data = content.encode("utf-8")
        mime = "application/octet-stream"
        if fname.endswith(".txt"):
            mime = "text/plain"
        elif fname.endswith(".json"):
            mime = "application/json"
        elif fname.endswith(".png"):
            mime = "image/png"
        info = mgr.save_attachment(session_id, file_id, data, mime)
        file_refs.append({"name": fname, "path": info.path, "mime": info.mime})
        print(f"      saved -> s3 key: {info.path}  (sha256={info.sha256[:8]}.., {info.size} bytes)")

    meta = dict(sess.metadata)
    meta["_vfs_files"] = file_refs
    mgr.save_session(session_id, sess.messages, provider="test", model="m", metadata=meta)
    print("[OK] VFS files written to Supabase S3 bucket\n")

    # 2. Verify the bytes are actually in the bucket
    print("--- Step 2: read bytes back directly from S3 ---")
    for ref in file_refs:
        data = mgr.load_attachment(ref["path"])
        assert data is not None, f"MISSING in bucket: {ref['path']}"
        restored = data.decode("utf-8")
        original = vfs_files[ref["name"]]
        assert restored == original, f"CONTENT MISMATCH: {ref['name']}"
        print(f"      verified -> {ref['name']} ({len(data)} bytes) matches")
    print("[OK] All files round-tripped intact from S3\n")

    # 3. Simulate resume in a fresh agent-like load
    print("--- Step 3: simulate session resume (VFS rehydration) ---")
    saved_meta = mgr.load_session_metadata(session_id)
    refs = saved_meta.pop("_vfs_files", [])
    restored_files = {}
    for ref in refs:
        data = mgr.load_attachment(ref["path"])
        restored_files[ref["name"]] = data.decode("utf-8")
    assert restored_files == vfs_files, "RESUME MISMATCH"
    print("[OK] Resumed session rehydrated VFS files from S3 exactly\n")

    # 4. Delete to clean up bucket
    print("--- Step 4: delete (cleans up S3) ---")
    for ref in file_refs:
        mgr.delete_attachment(ref["path"])
        print(f"      deleted -> {ref['path']}")
    mgr.delete_session(session_id)
    print("[OK] Cleanup done\n")

    mgr.close()
    print("=== ALL S3 MEDIA CHECKS PASSED ===")


if __name__ == "__main__":
    main()

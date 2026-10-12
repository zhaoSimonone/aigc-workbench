CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS folders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('image', 'video')),
  source TEXT NOT NULL DEFAULT '本地导入',
  source_url TEXT NOT NULL DEFAULT '',
  character_name TEXT NOT NULL DEFAULT '',
  character_category TEXT NOT NULL DEFAULT '',
  object_key TEXT NOT NULL,
  thumb_key TEXT,
  content_type TEXT NOT NULL,
  size_bytes BIGINT NOT NULL DEFAULT 0,
  sha256 TEXT,
  duration_seconds NUMERIC,
  folder TEXT NOT NULL DEFAULT '灵感收集',
  note TEXT NOT NULL DEFAULT '',
  music_title TEXT NOT NULL DEFAULT '',
  music_artist TEXT NOT NULL DEFAULT '',
  music_source TEXT NOT NULL DEFAULT '',
  music_status TEXT NOT NULL DEFAULT '',
  music_confidence TEXT NOT NULL DEFAULT '',
  music_evidence TEXT NOT NULL DEFAULT '',
  favorite BOOLEAN NOT NULL DEFAULT false,
  used BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS character_name TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS character_category TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS music_title TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS music_artist TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS music_source TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS music_status TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS music_confidence TEXT NOT NULL DEFAULT '';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS music_evidence TEXT NOT NULL DEFAULT '';
UPDATE assets SET folder='我的创作' WHERE folder='成片';
CREATE INDEX IF NOT EXISTS assets_user_created_idx ON assets(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS assets_user_folder_idx ON assets(user_id, folder);
CREATE INDEX IF NOT EXISTS assets_user_sha256_idx ON assets(user_id, sha256) WHERE sha256 IS NOT NULL AND deleted_at IS NULL;

-- 本地目录归档与工作文档备份
-- 批次 = 一次扫描/导入；对象 = 去重后的实际二进制；归档文件 = 原始路径条目。
-- 全部使用 IF NOT EXISTS，可重复执行；不修改 assets.type 的 image/video 约束。
CREATE TABLE IF NOT EXISTS archive_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  source_root TEXT NOT NULL,
  source_relative_root TEXT NOT NULL DEFAULT '',
  storage_backend TEXT NOT NULL CHECK (storage_backend IN ('cos','local')),
  status TEXT NOT NULL DEFAULT 'scanned'
    CHECK (status IN ('scanned','queued','running','paused','completed','completed_with_errors','cancelled','failed')),
  create_media_assets BOOLEAN NOT NULL DEFAULT false,
  asset_folder TEXT NOT NULL DEFAULT '',
  total_files INTEGER NOT NULL DEFAULT 0,
  total_bytes BIGINT NOT NULL DEFAULT 0,
  processed_files INTEGER NOT NULL DEFAULT 0,
  processed_bytes BIGINT NOT NULL DEFAULT 0,
  success_files INTEGER NOT NULL DEFAULT 0,
  failed_files INTEGER NOT NULL DEFAULT 0,
  skipped_files INTEGER NOT NULL DEFAULT 0,
  current_relative_path TEXT NOT NULL DEFAULT '',
  source_cleanup_status TEXT NOT NULL DEFAULT 'not_requested'
    CHECK (source_cleanup_status IN ('not_requested','requested','completed','partial','failed')),
  source_cleanup_requested_at TIMESTAMPTZ,
  source_cleanup_completed_at TIMESTAMPTZ,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS archive_batches_user_created_idx ON archive_batches(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS archive_batches_status_idx ON archive_batches(status) WHERE status IN ('queued','running','paused');

CREATE TABLE IF NOT EXISTS archive_objects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  storage_backend TEXT NOT NULL CHECK (storage_backend IN ('cos','local')),
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','uploading','available','verifying','failed')),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  reference_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  verified_at TIMESTAMPTZ,
  last_error TEXT NOT NULL DEFAULT '',
  UNIQUE(user_id, sha256, size_bytes, storage_backend)
);
CREATE INDEX IF NOT EXISTS archive_objects_user_key_idx ON archive_objects(user_id, object_key);

CREATE TABLE IF NOT EXISTS archive_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id UUID NOT NULL REFERENCES archive_batches(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  object_id UUID REFERENCES archive_objects(id) ON DELETE SET NULL,
  asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
  source_path TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  name TEXT NOT NULL,
  extension TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('image','video','audio','document','archive','other')),
  mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  size_bytes BIGINT NOT NULL DEFAULT 0,
  source_mtime TIMESTAMPTZ,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'discovered'
    CHECK (status IN ('discovered','hashing','queued','uploading','verifying','available','skipped','failed','source_deleted')),
  retry_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  source_deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(batch_id, relative_path)
);
CREATE INDEX IF NOT EXISTS archive_files_user_created_idx ON archive_files(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS archive_files_batch_status_idx ON archive_files(batch_id, status);
CREATE INDEX IF NOT EXISTS archive_files_user_sha256_idx ON archive_files(user_id, sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS archive_files_user_kind_idx ON archive_files(user_id, kind);
CREATE INDEX IF NOT EXISTS archive_files_user_relative_idx ON archive_files(user_id, relative_path);
CREATE INDEX IF NOT EXISTS archive_files_object_idx ON archive_files(object_id);

ALTER TABLE assets ADD COLUMN IF NOT EXISTS archive_file_id UUID REFERENCES archive_files(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS assets_user_archive_file_idx ON assets(archive_file_id) WHERE archive_file_id IS NOT NULL;

-- 百度网盘目录快照：工作台只保存网盘目录元数据，不保存任何文件字节，
-- 也不保存百度 Token 或 dlink。文件同步由本机 MCP/CLI 直传网盘完成。
CREATE TABLE IF NOT EXISTS netdisk_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  root_path TEXT NOT NULL DEFAULT '/AIGC',
  source TEXT NOT NULL DEFAULT 'cli',
  file_count INTEGER NOT NULL DEFAULT 0,
  dir_count INTEGER NOT NULL DEFAULT 0,
  total_bytes BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','stale','failed')),
  last_error TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS netdisk_snapshots_user_created_idx ON netdisk_snapshots(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS netdisk_nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  snapshot_id UUID REFERENCES netdisk_snapshots(id) ON DELETE SET NULL,
  fs_id BIGINT NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  parent_path TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  is_dir BOOLEAN NOT NULL DEFAULT false,
  kind TEXT NOT NULL DEFAULT 'other'
    CHECK (kind IN ('image','video','audio','document','archive','other','directory')),
  size_bytes BIGINT NOT NULL DEFAULT 0,
  server_mtime TIMESTAMPTZ,
  category INTEGER NOT NULL DEFAULT 0,
  local_state TEXT NOT NULL DEFAULT 'unknown'
    CHECK (local_state IN ('unknown','present','missing','size_mismatch')),
  local_size_bytes BIGINT NOT NULL DEFAULT 0,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, path)
);
CREATE INDEX IF NOT EXISTS netdisk_nodes_user_parent_idx ON netdisk_nodes(user_id, parent_path);
CREATE INDEX IF NOT EXISTS netdisk_nodes_user_dir_idx ON netdisk_nodes(user_id, is_dir);
CREATE INDEX IF NOT EXISTS netdisk_nodes_user_kind_idx ON netdisk_nodes(user_id, kind);
CREATE INDEX IF NOT EXISTS netdisk_nodes_user_name_idx ON netdisk_nodes(user_id, name);
CREATE INDEX IF NOT EXISTS netdisk_nodes_user_size_idx ON netdisk_nodes(user_id, size_bytes DESC);

CREATE TABLE IF NOT EXISTS music_tracks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '待标记音乐',
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '待标记' CHECK (status IN ('待标记', '已标记', '已确认', '不使用')),
  object_key TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'audio/wav',
  size_bytes BIGINT NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL,
  duration_seconds NUMERIC,
  sample_rate INTEGER,
  channels INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, sha256)
);
CREATE INDEX IF NOT EXISTS music_tracks_user_updated_idx ON music_tracks(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS asset_music (
  asset_id UUID PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  music_track_id UUID NOT NULL REFERENCES music_tracks(id) ON DELETE CASCADE,
  source_audio_sha256 TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS asset_music_track_idx ON asset_music(music_track_id);

CREATE TABLE IF NOT EXISTS character_albums (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  cover_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, name)
);
ALTER TABLE character_albums ADD COLUMN IF NOT EXISTS cover_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL;
INSERT INTO character_albums(user_id, name)
SELECT DISTINCT user_id, character_name
FROM assets
WHERE character_name <> ''
ON CONFLICT(user_id, name) DO NOTHING;
CREATE INDEX IF NOT EXISTS character_albums_user_idx ON character_albums(user_id, created_at ASC);

CREATE TABLE IF NOT EXISTS video_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  platform TEXT NOT NULL DEFAULT '其他',
  account_name TEXT NOT NULL DEFAULT '',
  profile_url TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  links JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, profile_url)
);
ALTER TABLE video_accounts ADD COLUMN IF NOT EXISTS links JSONB NOT NULL DEFAULT '[]';
UPDATE video_accounts SET links = jsonb_build_array(jsonb_build_object('platform', platform, 'url', profile_url)) WHERE links = '[]'::jsonb;
ALTER TABLE video_accounts ADD COLUMN IF NOT EXISTS avatar_key TEXT;
CREATE INDEX IF NOT EXISTS video_accounts_user_idx ON video_accounts(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS prompts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT '其他',
  task_type TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  tags TEXT[] NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT '待验证' CHECK (status IN ('待验证', '验证成功', '精选', '已失效')),
  favorite BOOLEAN NOT NULL DEFAULT false,
  rating SMALLINT CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
  note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prompts_user_updated_idx ON prompts(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS prompts_user_status_idx ON prompts(user_id, status);

CREATE TABLE IF NOT EXISTS prompt_asset_links (
  prompt_id UUID NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  usage_role TEXT NOT NULL CHECK (usage_role IN ('input_video', 'reference_image', 'generated_output', 'other')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(prompt_id, asset_id, usage_role)
);
CREATE INDEX IF NOT EXISTS prompt_asset_links_user_idx ON prompt_asset_links(user_id, asset_id);

CREATE TABLE IF NOT EXISTS tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS asset_tags (
  asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS asset_relations (
  source_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  derived_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL DEFAULT 'derived_from',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(source_asset_id, derived_asset_id),
  CHECK (source_asset_id <> derived_asset_id)
);
CREATE INDEX IF NOT EXISTS asset_relations_derived_idx ON asset_relations(derived_asset_id);

CREATE TABLE IF NOT EXISTS asset_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 5000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS asset_comments_asset_idx ON asset_comments(asset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS asset_comments_user_idx ON asset_comments(user_id);

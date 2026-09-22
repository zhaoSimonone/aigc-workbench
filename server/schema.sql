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

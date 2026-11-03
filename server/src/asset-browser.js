const ROLE_ASSET = "(a.folder='角色设定' OR (a.folder='发型库' AND a.character_name<>''))";
const CATEGORY = "COALESCE(NULLIF(a.character_category,''), CASE WHEN a.folder='发型库' THEN '发型' ELSE '未分类' END)";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function browserQuery(userId, query) {
  const params = [userId];
  let from = 'assets a';
  const where = ['a.user_id=$1', 'a.deleted_at IS NULL'];
  const bind = (value) => { params.push(value); return `$${params.length}`; };
  const integer = (key, fallback, max) => {
    if (query[key] === undefined) return fallback;
    const value = Number(query[key]);
    if (!/^\d+$/.test(String(query[key])) || !Number.isSafeInteger(value)) throw new Error(`${key} 必须是非负整数`);
    return Math.min(value, max);
  };
  const limit = Math.max(1, integer('limit', 60, 200));
  const offset = integer('offset', 0, Number.MAX_SAFE_INTEGER);
  if (query.folder === '角色设定' || query.mode === 'albums') where.push(ROLE_ASSET);
  else if (query.folder === '我的创作') where.push("a.folder IN ('我的创作','成片')");
  else if (query.folder && query.folder !== '全部素材') where.push(`a.folder=${bind(String(query.folder))}`);
  if (query.character !== undefined) {
    const name = ['待归类人物', '未命名角色'].includes(query.character) ? '' : String(query.character);
    where.push(`a.character_name=${bind(name)}`);
  }
  if (query.category) where.push(`${CATEGORY}=${bind(String(query.category))}`);
  if (query.type) {
    if (!['image', 'video'].includes(query.type)) throw new Error('type 无效');
    where.push(`a.type=${bind(query.type)}`);
  }
  if (query.filter === '已收藏') where.push('a.favorite=true');
  if (query.filter === '已使用') where.push('a.used=true');
  if (query.tag) where.push(`EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id AND t.name=${bind(String(query.tag))})`);
  if (query.ids) {
    const ids = [...new Set(String(query.ids).split(','))];
    if (ids.length > 200 || ids.some((id) => !UUID.test(id))) throw new Error('ids 必须为最多 200 个有效素材 ID');
    where.push(`a.id=ANY(${bind(ids)}::uuid[])`);
  }
  if (query.exclude) {
    if (!UUID.test(String(query.exclude))) throw new Error('exclude 无效');
    where.push(`a.id<>${bind(query.exclude)}::uuid`);
  }
  if (String(query.q || '').trim()) {
    // Literal substring semantics, including searches containing % or _.
    const needle = bind(`%${String(query.q).trim().replace(/[\\%_]/g, '\\$&')}%`);
    // Aggregate the owner's tags once, preserving the existing cross-field substring semantics.
    from += ` LEFT JOIN (SELECT at.asset_id,string_agg(t.name,' ' ORDER BY t.name) AS names
      FROM tags t JOIN asset_tags at ON at.tag_id=t.id WHERE t.user_id=$1 GROUP BY at.asset_id) search_tags
      ON search_tags.asset_id=a.id`;
    where.push(`concat_ws(' ',a.name,a.source,a.music_title,a.music_artist,a.music_status,search_tags.names) ILIKE ${needle}`);
  }
  const order = query.sort === '名称' ? 'a.name COLLATE "zh-Hans-CN-x-icu", a.id'
    : query.sort === 'cover' ? "(CASE WHEN a.character_category='正脸' THEN 0 ELSE 1 END), (CASE WHEN a.type='image' THEN 0 ELSE 1 END), a.created_at DESC, a.id DESC"
      : 'a.created_at DESC, a.id DESC';
  return { params, from, where: where.join(' AND '), limit, offset, order };
}

function registerAssetBrowser(app, { pool, requireUser, stableSignedUrl, formatBytes, formatDuration }) {
  const preview = (row) => row.thumb_key ? stableSignedUrl(row.thumb_key)
    : row.type === 'image' ? stableSignedUrl(row.object_key) : '';
  const card = (row) => ({
    id: row.id, name: row.name, type: row.type, contentType: row.content_type,
    source: row.source, folder: row.folder, characterName: row.character_name,
    characterCategory: row.character_category, favorite: row.favorite, used: row.used,
    date: row.created_at, thumb: preview(row), tags: row.tags || [],
    size: formatBytes(Number(row.size_bytes)),
    duration: row.duration_seconds ? formatDuration(row.duration_seconds) : row.type === 'video' ? '待识别' : null,
  });
  app.get('/api/asset-browser/summary', requireUser, async (req, res, next) => {
    try {
      const { rows } = await pool.query(
        `WITH owned AS (SELECT id,type,favorite,used,folder,character_name,character_category
          FROM assets WHERE user_id=$1 AND deleted_at IS NULL)
         SELECT
          (SELECT json_build_object('total',count(*),'video',count(*) FILTER (WHERE type='video'),
            'image',count(*) FILTER (WHERE type='image'),'favorite',count(*) FILTER (WHERE favorite),
            'used',count(*) FILTER (WHERE used)) FROM owned) AS counts,
          (SELECT COALESCE(json_object_agg(folder,n),'{}') FROM
            (SELECT folder,count(*)::int n FROM owned GROUP BY folder) f) AS folders,
          (SELECT count(*)::int FROM owned a WHERE ${ROLE_ASSET}) AS roles,
          (SELECT COALESCE(json_agg(name ORDER BY name COLLATE "zh-Hans-CN-x-icu"),'[]')
            FROM (SELECT DISTINCT t.name FROM owned a JOIN asset_tags at ON at.asset_id=a.id JOIN tags t ON t.id=at.tag_id) t) AS tags,
          (SELECT COALESCE(json_agg(c),'[]') FROM
            (SELECT a.character_name AS name, ${CATEGORY} AS category, count(*)::int AS count
              FROM owned a WHERE ${ROLE_ASSET} GROUP BY a.character_name,${CATEGORY}) c) AS characters,
          (SELECT COALESCE(json_agg(c ORDER BY c.name COLLATE "zh-Hans-CN-x-icu"),'[]') FROM
            (SELECT id,name FROM character_albums WHERE user_id=$1) c) AS albums,
          (SELECT count(*)::int FROM prompts WHERE user_id=$1) AS prompts,
          (SELECT count(*)::int FROM video_accounts WHERE user_id=$1) AS accounts,
          (SELECT count(*)::int FROM music_tracks WHERE user_id=$1) AS music`,
        [req.user.id],
      );
      res.json(rows[0]);
    } catch (error) { next(error); }
  });
  app.get('/api/asset-browser', requireUser, async (req, res, next) => {
    let query;
    try { query = browserQuery(req.user.id, req.query); }
    catch (error) { return res.status(400).json({ error: error.message }); }
    try {
      const { params, from, where, limit, offset, order } = query;
      if (req.query.mode === 'albums') {
        // Metadata contributes empty albums; counts and automatic covers follow the active filters.
        const cte = `WITH matching AS (SELECT a.id,a.type,a.thumb_key,a.created_at,
            COALESCE(NULLIF(a.character_name,''),'待归类人物') AS name,
            ${CATEGORY} AS category FROM ${from} WHERE ${where}),
          names AS (SELECT name FROM character_albums WHERE user_id=$1
            UNION SELECT name FROM matching),
          stats AS (SELECT name,count(*)::int AS count,
            count(*) FILTER (WHERE type='video')::int AS video_count,
            count(*) FILTER (WHERE type='image')::int AS image_count,
            (array_agg(id ORDER BY created_at DESC,id DESC) FILTER (WHERE thumb_key IS NOT NULL OR type='image'))[1] AS auto_cover_id
            FROM matching GROUP BY name),
          categories AS (SELECT name,json_object_agg(category,n) AS counts FROM
            (SELECT name,category,count(*)::int n FROM matching GROUP BY name,category) c GROUP BY name),
          grouped AS (SELECT n.name,ca.id AS album_id,ca.cover_asset_id,
            COALESCE(s.count,0) AS count,COALESCE(s.video_count,0) AS video_count,
            COALESCE(s.image_count,0) AS image_count,s.auto_cover_id,COALESCE(c.counts,'{}') AS categories
          FROM names n LEFT JOIN character_albums ca ON ca.user_id=$1 AND ca.name=n.name
          LEFT JOIN stats s ON s.name=n.name LEFT JOIN categories c ON c.name=n.name)`;
        const count = await pool.query(`${cte} SELECT count(*)::int AS total FROM names`, params);
        const { rows } = await pool.query(`${cte}
          SELECT g.*,c.type,c.object_key,c.thumb_key FROM
            (SELECT * FROM grouped ORDER BY name COLLATE "zh-Hans-CN-x-icu"
              LIMIT $${params.length + 1} OFFSET $${params.length + 2}) g
          LEFT JOIN assets fixed ON fixed.id=g.cover_asset_id AND fixed.user_id=$1 AND fixed.deleted_at IS NULL
          LEFT JOIN assets c ON c.id=COALESCE(fixed.id,g.auto_cover_id) AND c.user_id=$1 AND c.deleted_at IS NULL
          ORDER BY g.name COLLATE "zh-Hans-CN-x-icu"`, [...params, limit, offset]);
        const total = count.rows[0].total;
        return res.json({ albums: rows.map((r) => ({
          name: r.name, albumId: r.album_id, coverAssetId: r.cover_asset_id,
          count: r.count, videoCount: r.video_count, imageCount: r.image_count,
          categoryCounts: r.categories, cover: r.object_key ? preview(r) : '',
        })), total, limit, offset, hasMore: offset + rows.length < total });
      }
      const count = await pool.query(`SELECT count(*)::int AS total FROM ${from} WHERE ${where}`, params);
      const { rows } = await pool.query(
        `WITH page AS MATERIALIZED (
          SELECT a.id
          FROM ${from} WHERE ${where} ORDER BY ${order}
          LIMIT $${params.length + 1} OFFSET $${params.length + 2})
         SELECT a.id,a.name,a.type,a.content_type,a.source,a.folder,a.character_name,a.character_category,
          a.favorite,a.used,a.created_at,a.object_key,a.thumb_key,a.size_bytes,a.duration_seconds,
          ARRAY(SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=a.id ORDER BY t.name) AS tags
          FROM page p JOIN assets a ON a.id=p.id ORDER BY ${order}`, [...params, limit, offset],
      );
      res.json({ assets: rows.map(card), total: count.rows[0].total, limit, offset, hasMore: offset + rows.length < count.rows[0].total });
    } catch (error) { next(error); }
  });
}

module.exports = { browserQuery, registerAssetBrowser };

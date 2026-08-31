import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Archive,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  CircleCheck,
  Clock3,
  Cloud,
  Copy,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  FileImage,
  FileVideo,
  FolderOpen,
  Grid2X2,
  Heart,
  ImagePlus,
  KeyRound,
  LayoutGrid,
  ListFilter,
  LogIn,
  LogOut,
  Menu,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  Search,
  Settings2,
  Share2,
  Sparkles,
  Tag,
  Trash2,
  Upload,
  UserPlus,
  Video,
  X,
  Info,
} from "lucide-react";
import "./styles.css";

const media = "/media/";
const API_BASE = "/api";

const seedAssets = [
  {
    id: 1,
    name: "精神小妹 · 便利店转场",
    type: "video",
    source: "TikTok",
    sourceUrl: "",
    duration: "00:12",
    date: "今天 09:42",
    size: "18.4 MB",
    tags: ["转场", "街拍"],
    color: "coral",
    src: `${media}source-demo.mp4`,
    thumb: `${media}persona-reference.png`,
    favorite: true,
    used: false,
    folder: "灵感收集",
    note: "便利店门口的快速推拉镜头，适合做开场钩子。",
  },
  {
    id: 2,
    name: "邻家感人物设定 · 林小栀",
    type: "image",
    source: "本地导入",
    sourceUrl: "",
    date: "昨天 21:08",
    size: "2.1 MB",
    tags: ["人物", "参考图"],
    color: "yellow",
    src: `${media}persona-reference.png`,
    thumb: `${media}persona-reference.png`,
    favorite: true,
    used: true,
    folder: "角色设定",
    note: "柔和自然光，保留轻微颗粒感。",
  },
  {
    id: 3,
    name: "纸守卫与红铃 · 关键帧",
    type: "image",
    source: "Midjourney",
    sourceUrl: "",
    date: "8月 29日",
    size: "2.4 MB",
    tags: ["漫剧", "构图"],
    color: "indigo",
    src: `${media}paper-guard.png`,
    thumb: `${media}paper-guard.png`,
    favorite: false,
    used: false,
    folder: "项目资料",
    note: "竖版 9:16，适合作为情绪板基准。",
  },
  {
    id: 4,
    name: "换装效果 · 5 秒样片",
    type: "video",
    source: "Instagram",
    sourceUrl: "",
    duration: "00:05",
    date: "8月 29日",
    size: "8.2 MB",
    tags: ["换装", "产品"],
    color: "mint",
    src: `${media}source-demo.mp4`,
    thumb: `${media}outfit-reference.png`,
    favorite: true,
    used: true,
    folder: "灵感收集",
    note: "前后景别不变，衣服切换点很干净。",
  },
  {
    id: 5,
    name: "自然光穿搭参考",
    type: "image",
    source: "Pinterest",
    sourceUrl: "",
    date: "8月 28日",
    size: "1.9 MB",
    tags: ["穿搭", "光影"],
    color: "blue",
    src: `${media}outfit-reference.png`,
    thumb: `${media}outfit-reference.png`,
    favorite: false,
    used: false,
    folder: "灵感收集",
    note: "窗边侧光，适合做服装类内容参考。",
  },
  {
    id: 6,
    name: "纸月城 · 氛围运动镜头",
    type: "video",
    source: "YouTube",
    sourceUrl: "",
    duration: "00:18",
    date: "8月 26日",
    size: "24.8 MB",
    tags: ["运镜", "漫剧"],
    color: "violet",
    src: `${media}source-demo.mp4`,
    thumb: `${media}paper-guard.png`,
    favorite: false,
    used: false,
    folder: "项目资料",
    note: "低机位跟拍，节奏由慢到快。",
  },
  {
    id: 7,
    name: "一镜到底 · 旁白节奏",
    type: "video",
    source: "视频号",
    sourceUrl: "",
    duration: "00:31",
    date: "8月 25日",
    size: "31.2 MB",
    tags: ["口播", "节奏"],
    color: "orange",
    src: `${media}source-demo.mp4`,
    thumb: `${media}persona-reference.png`,
    favorite: false,
    used: true,
    folder: "成片",
    note: "留白较多，方便替换成自己的台词。",
  },
  {
    id: 8,
    name: "情绪板 · 暖红室内",
    type: "image",
    source: "本地导入",
    sourceUrl: "",
    date: "8月 24日",
    size: "1.7 MB",
    tags: ["情绪板", "室内"],
    color: "rose",
    src: `${media}episode-keyframe.png`,
    thumb: `${media}episode-keyframe.png`,
    favorite: true,
    used: false,
    folder: "项目资料",
    note: "暖红与青绿色的对比关系。",
  },
];
async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || "请求失败，请稍后重试");
  return payload;
}

function normaliseAsset(asset) {
  const created = asset.date ? new Date(asset.date) : null;
  const tones = ["coral", "yellow", "indigo", "mint", "blue", "violet", "orange", "rose"];
  const toneIndex = String(asset.id || asset.name || "").split("").reduce((sum, char) => sum + char.charCodeAt(0), 0) % tones.length;
  return {
    ...asset,
    color: asset.color || tones[toneIndex],
    date: created && !Number.isNaN(created.valueOf()) ? formatAssetDate(created) : asset.date || "刚刚",
    createdAt: asset.date,
    tags: Array.isArray(asset.tags) ? asset.tags : [],
  };
}

function formatAssetDate(date) {
  const diff = Date.now() - date.getTime();
  if (diff < 60 * 1000) return "刚刚";
  if (diff < 24 * 60 * 60 * 1000) return `${Math.max(1, Math.floor(diff / (60 * 60 * 1000)))} 小时前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function App() {
  const [session, setSession] = useState(null);
  const [checking, setChecking] = useState(true);
  useEffect(() => {
    apiFetch("/auth/me")
      .then((payload) => setSession(payload.user))
      .catch(() => setSession(null))
      .finally(() => setChecking(false));
  }, []);
  if (checking) return <div className="app-loading">正在连接素材库…</div>;
  if (!session) return <AuthScreen onAuth={setSession} />;
  return (
    <Workspace
      user={session}
      onLogout={async () => {
        await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
        setSession(null);
      }}
    />
  );
}

function AuthScreen({ onAuth }) {
  const [mode, setMode] = useState("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submit = (event) => {
    event.preventDefault();
    setError("");
    const normalizedEmail = email.trim().toLowerCase();
    if (
      !normalizedEmail ||
      !password ||
      (mode === "register" && !name.trim())
    ) {
      setError("请完整填写信息");
      return;
    }
    if (mode === "register" && password.length < 6) {
      setError("密码至少需要 6 位");
      return;
    }
    setSubmitting(true);
    apiFetch(`/auth/${mode === "login" ? "login" : "register"}`, {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), email: normalizedEmail, password }),
    })
      .then((payload) => onAuth(payload.user))
      .catch((requestError) => setError(requestError.message))
      .finally(() => setSubmitting(false));
  };
  return (
    <div className="auth-shell">
      <div className="auth-decoration">
        <div className="auth-orbit orbit-one" />
        <div className="auth-orbit orbit-two" />
        <div className="auth-grid" />
        <div className="auth-brand">
          <div className="brand-mark">
            <Sparkles size={17} />
          </div>
          <div>
            <strong>
              AIGC <span>SHELF</span>
            </strong>
            <small>CREATIVE ASSET LIBRARY</small>
          </div>
        </div>
        <div className="auth-quote">
          <span>01</span>
          <p>
            Collect the spark.
            <br />
            <b>Make it yours.</b>
          </p>
        </div>
      </div>
      <main className="auth-panel">
        <div className="auth-panel-inner">
          <div className="auth-mobile-brand">
            <div className="brand-mark">
              <Sparkles size={17} />
            </div>
            <strong>
              AIGC <span>SHELF</span>
            </strong>
          </div>
          <div className="auth-kicker">创作者空间</div>
          <h1>{mode === "login" ? "欢迎回来" : "创建你的素材库"}</h1>
          <p className="auth-subtitle">
            {mode === "login"
              ? "登录后继续整理你的创作灵感。"
              : "注册一个账号，让素材和标签跟着你走。"}
          </p>
          <div className="auth-tabs">
            <button
              className={mode === "login" ? "active" : ""}
              onClick={() => {
                setMode("login");
                setError("");
              }}
            >
              <LogIn size={15} />
              登录
            </button>
            <button
              className={mode === "register" ? "active" : ""}
              onClick={() => {
                setMode("register");
                setError("");
              }}
            >
              <UserPlus size={15} />
              注册
            </button>
          </div>
          <form className="auth-form" onSubmit={submit}>
            {mode === "register" && (
              <label>
                昵称
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="你的创作者昵称"
                />
              </label>
            )}
            <label>
              邮箱
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
              />
            </label>
            <label>
              密码
              <div className="password-field">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="至少 6 位字符"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </label>
            {error && <div className="auth-error">{error}</div>}
            <button className="auth-submit" type="submit" disabled={submitting}>
              {mode === "login" ? "进入工作台" : "创建账号"}
              <ArrowUpRight size={17} />
            </button>
          </form>
          {mode === "login" && (
            <div className="demo-hint">
              <KeyRound size={14} />
              <span>
                使用你的账号登录，素材会在多设备间同步
              </span>
            </div>
          )}
          <p className="auth-footnote">数据按账号隔离，安全存储在云端</p>
        </div>
      </main>
    </div>
  );
}

function Workspace({ user, onLogout }) {
  const [assets, setAssets] = useState([]);
  const [loadingAssets, setLoadingAssets] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [activeFolder, setActiveFolder] = useState("全部素材");
  const [activeCharacter, setActiveCharacter] = useState("");
  const [activeFilter, setActiveFilter] = useState("全部");
  const [activeTag, setActiveTag] = useState("");
  const [tagOpen, setTagOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [showUpload, setShowUpload] = useState(false);
  const [editingAsset, setEditingAsset] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [sort, setSort] = useState("最近添加");
  const [view, setView] = useState("grid");
  const fileInput = useRef(null);
  const folderInput = useRef(null);

  useEffect(() => {
    apiFetch("/assets")
      .then((payload) => setAssets((payload.assets || []).map(normaliseAsset)))
      .catch((error) => setLoadError(error.message))
      .finally(() => setLoadingAssets(false));
  }, []);
  const folderItems = useMemo(
    () => [
      { label: "全部素材", count: assets.length, icon: LayoutGrid },
      {
        label: "灵感收集",
        count: assets.filter((item) => item.folder === "灵感收集").length,
        icon: Sparkles,
      },
      {
        label: "成片",
        count: assets.filter((item) => item.folder === "成片").length,
        icon: Play,
      },
      {
        label: "角色设定",
        count: assets.filter((item) => item.folder === "角色设定").length,
        icon: FileImage,
      },
      {
        label: "项目资料",
        count: assets.filter((item) => item.folder === "项目资料").length,
        icon: Archive,
      },
    ],
    [assets],
  );
  const filteredAssets = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const result = assets.filter((asset) => {
      const inFolder =
        activeFolder === "全部素材" || asset.folder === activeFolder;
      const inCharacter =
        activeFolder !== "角色设定" ||
        !activeCharacter ||
        (activeCharacter === "未命名角色" ? !asset.characterName : asset.characterName === activeCharacter);
      const inType =
        activeFilter === "全部" ||
        (activeFilter === "视频" && asset.type === "video") ||
        (activeFilter === "图片" && asset.type === "image") ||
        (activeFilter === "已收藏" && asset.favorite) ||
        (activeFilter === "已使用" && asset.used);
      const inTag = !activeTag || asset.tags.includes(activeTag);
      const inSearch =
        !normalized ||
        `${asset.name} ${asset.source} ${asset.tags.join(" ")}`
          .toLowerCase()
          .includes(normalized);
      return inFolder && inCharacter && inType && inTag && inSearch;
    });
    return [...result].sort((a, b) =>
      sort === "名称"
        ? a.name.localeCompare(b.name, "zh")
        : new Date(b.createdAt || 0) - new Date(a.createdAt || 0),
    );
  }, [assets, activeFolder, activeFilter, activeTag, query, sort]);
  const tagOptions = useMemo(
    () =>
      [...new Set(assets.flatMap((asset) => asset.tags))].sort((a, b) =>
        a.localeCompare(b, "zh"),
      ),
    [assets],
  );
  const roleAlbums = useMemo(() => {
    if (activeFolder !== "角色设定" || activeCharacter) return [];
    const groups = new Map();
    filteredAssets.forEach((asset) => {
      const name = asset.characterName || "未命名角色";
      const existing = groups.get(name) || { name, assets: [] };
      existing.assets.push(asset);
      groups.set(name, existing);
    });
    return [...groups.values()]
      .map((group) => ({
        ...group,
        count: group.assets.length,
        cover: group.assets.find((asset) => asset.thumb)?.thumb || "",
        videoCount: group.assets.filter((asset) => asset.type === "video").length,
        imageCount: group.assets.filter((asset) => asset.type === "image").length,
      }))
      .sort((a, b) => a.name.localeCompare(b.name, "zh"));
  }, [filteredAssets, activeFolder, activeCharacter]);
  const updateAsset = async (id, patch) => {
    try {
      const payload = await apiFetch(`/assets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      const updated = normaliseAsset(payload.asset);
      setAssets((items) => items.map((item) => (item.id === id ? updated : item)));
      setSelected((item) => (item?.id === id ? updated : item));
    } catch (error) {
      setLoadError(error.message);
    }
  };
  const handleUpload = async (files, metadata) => {
    setUploading(true);
    setLoadError("");
    try {
      const uploaded = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        form.append("source", metadata.source || "本地导入");
        form.append("sourceUrl", metadata.sourceUrl || "");
        form.append("tags", JSON.stringify(metadata.tags || []));
        form.append("used", String(Boolean(metadata.used)));
        form.append("folder", metadata.folder || "灵感收集");
        if (metadata.parentAssetIds?.length) form.append("parentAssetIds", JSON.stringify(metadata.parentAssetIds));
        const payload = await apiFetch("/assets", { method: "POST", body: form });
        uploaded.push(normaliseAsset(payload.asset));
      }
      setAssets((items) => [...uploaded, ...items]);
      setShowUpload(false);
    } catch (error) {
      setLoadError(error.message);
    } finally {
      setUploading(false);
    }
  };
  const saveAssetEdit = async (id, fields) => {
    const { tags, ...assetFields } = fields;
    try {
      if (Object.keys(assetFields).length) {
        await apiFetch(`/assets/${id}`, {
          method: "PATCH",
          body: JSON.stringify(assetFields),
        });
      }
      if (tags) {
        await apiFetch(`/assets/${id}/tags`, {
          method: "POST",
          body: JSON.stringify({ tags }),
        });
      }
      const payload = await apiFetch("/assets");
      const nextAssets = (payload.assets || []).map(normaliseAsset);
      setAssets(nextAssets);
      setSelected(nextAssets.find((item) => item.id === id) || null);
      setEditingAsset(null);
    } catch (error) {
      throw error;
    }
  };
  const total = assets.length;
  const videoCount = assets.filter((item) => item.type === "video").length;
  const imageCount = assets.filter((item) => item.type === "image").length;
  const usedCount = assets.filter((item) => item.used).length;
  const stats = [
    {
      label: "素材总数",
      value: total,
      hint: total ? "按账号保存" : "开始上传吧",
      icon: LayoutGrid,
      tone: "red",
    },
    {
      label: "视频素材",
      value: videoCount,
      hint: total ? `${Math.round((videoCount / total) * 100)}%` : "0%",
      icon: Video,
      tone: "blue",
    },
    {
      label: "图片素材",
      value: imageCount,
      hint: total ? `${Math.round((imageCount / total) * 100)}%` : "0%",
      icon: ImagePlus,
      tone: "yellow",
    },
    {
      label: "已使用",
      value: usedCount,
      hint: total ? "创作足迹" : "待标记",
      icon: CircleCheck,
      tone: "green",
    },
  ];
  const libraryTitle = activeCharacter ? `角色设定 / ${activeCharacter}` : activeFolder;
  const isRoleAlbumView = activeFolder === "角色设定" && !activeCharacter;
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">
            <Sparkles size={15} strokeWidth={2.5} />
          </div>
          <div>
            <div className="brand-name">
              AIGC <span>SHELF</span>
            </div>
            <div className="brand-sub">CREATIVE ASSET LIBRARY</div>
          </div>
          <button
            className="sidebar-close"
            onClick={() => setMobileNav(false)}
            aria-label="关闭导航"
          >
            <X size={18} />
          </button>
        </div>
        <div className="sidebar-section-label">工作台</div>
        <nav className="nav-list">
          {folderItems.map(({ label, count, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item ${activeFolder === label ? "active" : ""}`}
              onClick={() => {
                setActiveFolder(label);
                setActiveCharacter("");
                setActiveFilter("全部");
                setMobileNav(false);
              }}
            >
              <Icon
                size={17}
                strokeWidth={activeFolder === label ? 2.4 : 1.8}
              />
              <span>{label}</span>
              <em>{count}</em>
            </button>
          ))}
        </nav>
        <div className="sidebar-divider" />
        <div className="sidebar-section-label">快捷入口</div>
        <nav className="nav-list">
          <button
            className={`nav-item ${activeFilter === "已收藏" ? "active" : ""}`}
            onClick={() => {
              setActiveFilter("已收藏");
              setActiveFolder("全部素材");
              setActiveCharacter("");
              setMobileNav(false);
            }}
          >
            <Heart size={17} />
            <span>我的收藏</span>
            <em className="accent-count">
              {assets.filter((item) => item.favorite).length}
            </em>
          </button>
          <button
            className={`nav-item ${activeFilter === "已使用" ? "active" : ""}`}
            onClick={() => {
              setActiveFilter("已使用");
              setActiveFolder("全部素材");
              setActiveCharacter("");
              setMobileNav(false);
            }}
          >
            <CircleCheck size={17} />
            <span>已使用素材</span>
            <em>{usedCount}</em>
          </button>
          <button
            className="nav-item"
            onClick={() => {
              setActiveFolder("全部素材");
              setActiveFilter("全部");
              setActiveCharacter("");
              setMobileNav(false);
            }}
          >
            <Clock3 size={17} />
            <span>最近添加</span>
          </button>
          <button
            className="nav-item"
            onClick={() => {
              setActiveFolder("全部素材");
              setActiveFilter("全部");
              setActiveCharacter("");
              setMobileNav(false);
            }}
          >
            <Trash2 size={17} />
            <span>回收站</span>
          </button>
        </nav>
        <div className="storage-card">
          <div className="storage-head">
            <div className="storage-icon">
              <Cloud size={15} />
            </div>
            <span>云端存储</span>
            <span className="online-dot" />
          </div>
          <div className="storage-value">
            <strong>1.86</strong>
            <span> / 10 GB</span>
          </div>
          <div className="storage-bar">
            <span />
          </div>
          <div className="storage-foot">
            <span>腾讯云 COS</span>
            <button aria-label="存储设置">
              <Settings2 size={14} />
            </button>
          </div>
        </div>
        <div className="sidebar-user">
          <div className="avatar">
            {(user.name || "S").slice(0, 1).toUpperCase()}
          </div>
          <div className="user-copy">
            <strong>{user.name}</strong>
            <span>{user.email}</span>
          </div>
          <button
            className="logout-button"
            onClick={onLogout}
            aria-label="退出登录"
            title="退出登录"
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <button
            className="mobile-menu"
            onClick={() => setMobileNav(true)}
            aria-label="打开导航"
          >
            <Menu size={21} />
          </button>
          <div className="breadcrumb">
            <span>工作台</span>
            <ChevronDown size={13} />
            <strong>{activeFolder}</strong>
          </div>
          <div className="topbar-actions">
            <button className="icon-button" aria-label="通知">
              <Bell size={18} />
              <i />
            </button>
            <div className="topbar-line" />
            <div className="avatar top-avatar">
              {(user.name || "S").slice(0, 1).toUpperCase()}
            </div>
          </div>
        </header>
        <div className="content-wrap">
          <section className="intro-row">
            <div>
              <p className="eyebrow">
                MONDAY, AUG 31 <span className="eyebrow-dot" /> 09:46
              </p>
              <h1>素材库</h1>
              <p className="intro-copy">把灵感收好，下一条作品会更快开始。</p>
            </div>
            <div className="intro-actions">
              <button
                className="secondary-button"
                onClick={() => folderInput.current?.click()}
              >
                <FolderOpen size={16} />
                导入文件夹
              </button>
              <button
                className="primary-button"
                onClick={() => setShowUpload(true)}
              >
                <Plus size={18} />
                新增素材
              </button>
              <input
                ref={folderInput}
                type="file"
                hidden
                multiple
                webkitdirectory=""
                directory=""
                accept="video/*,image/*"
                onChange={(event) =>
                  handleUpload(Array.from(event.target.files || []), {
                    source: "本地导入",
                    tags: ["待整理"],
                    used: false,
                    folder: "灵感收集",
                  })
                }
              />
            </div>
          </section>
          <section className="stat-grid">
            {stats.map(({ label, value, hint, icon: Icon, tone }) => (
              <div className="stat-card" key={label}>
                <div className={`stat-icon ${tone}`}>
                  <Icon size={17} />
                </div>
                <div className="stat-text">
                  <span>{label}</span>
                  <strong>{value}</strong>
                  <small>{hint}</small>
                </div>
                <ArrowUpRight className="stat-arrow" size={16} />
              </div>
            ))}
          </section>
          <section className="library-section">
            <div className="library-head">
              <div>
                <h2>
                  {libraryTitle}{" "}
                  <span>
                    {isRoleAlbumView
                      ? `${roleAlbums.length} 个角色`
                      : filteredAssets.length !== assets.length
                      ? `${filteredAssets.length} / `
                      : ""}
                    {!isRoleAlbumView && total}
                  </span>
                </h2>
                {activeCharacter && (
                  <button className="album-back-button" onClick={() => setActiveCharacter("")}>
                    <ChevronDown size={14} /> 返回角色相册
                  </button>
                )}
                <p>你的创作参考与工作文件，集中在这里。</p>
              </div>
            <div className="sync-status">
              <span className="sync-dot" />
                已同步 <b>腾讯云 COS · 数据库</b>
            </div>
          </div>
          {loadError && <div className="inline-error">{loadError}</div>}
            <div className="toolbar">
              <div className="filter-tabs">
                {["全部", "视频", "图片", "已收藏", "已使用"].map((filter) => (
                  <button
                    key={filter}
                    className={activeFilter === filter ? "selected" : ""}
                    onClick={() => setActiveFilter(filter)}
                  >
                    {filter}
                    {filter === "已收藏" && (
                      <span>
                        {assets.filter((item) => item.favorite).length}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div className="toolbar-right">
                <div className="search-box">
                  <Search size={16} />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="搜索素材、标签或来源"
                  />
                  <kbd>⌘ K</kbd>
                </div>
                <div className="tag-filter-wrap">
                  <button
                    className={`tag-filter-button ${activeTag ? "active" : ""}`}
                    onClick={() => setTagOpen(!tagOpen)}
                  >
                    <Tag size={15} />
                    {activeTag || "标签"}
                    <ChevronDown size={13} />
                  </button>
                  {tagOpen && (
                    <div className="tag-filter-menu">
                      <button
                        onClick={() => {
                          setActiveTag("");
                          setTagOpen(false);
                        }}
                      >
                        全部标签
                        {!activeTag && <Check size={14} />}
                      </button>
                      {tagOptions.map((tag) => (
                        <button
                          key={tag}
                          onClick={() => {
                            setActiveTag(tag);
                            setTagOpen(false);
                          }}
                        >
                          {tag}
                          {activeTag === tag && <Check size={14} />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="sort-wrap">
                  <button
                    className="sort-button"
                    onClick={() => setSortOpen(!sortOpen)}
                  >
                    <ListFilter size={16} />
                    {sort}
                    <ChevronDown size={13} />
                  </button>
                  {sortOpen && (
                    <div className="sort-menu">
                      {["最近添加", "名称"].map((option) => (
                        <button
                          key={option}
                          onClick={() => {
                            setSort(option);
                            setSortOpen(false);
                          }}
                        >
                          {option}
                          {sort === option && <Check size={14} />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="view-toggle">
                  <button
                    className={view === "grid" ? "active" : ""}
                    onClick={() => setView("grid")}
                    aria-label="网格视图"
                  >
                    <Grid2X2 size={16} />
                  </button>
                  <button
                    className={view === "list" ? "active" : ""}
                    onClick={() => setView("list")}
                    aria-label="列表视图"
                  >
                    <ListFilter size={16} />
                  </button>
                </div>
              </div>
            </div>
            {loadingAssets ? (
              <div className="empty-state"><div className="empty-icon"><Cloud size={22} /></div><h3>正在加载素材</h3><p>从云端同步你的素材与标签</p></div>
            ) : isRoleAlbumView ? (
              roleAlbums.length ? (
                <div className="role-album-grid">
                  {roleAlbums.map((album) => (
                    <button
                      className="role-album-card"
                      key={album.name}
                      onClick={() => {
                        setActiveCharacter(album.name);
                        setActiveFilter("全部");
                        setActiveTag("");
                      }}
                    >
                      <div className="role-album-cover">
                        {album.cover ? <img src={album.cover} alt="" /> : <FileImage size={28} />}
                        <span>{album.count} 个素材</span>
                      </div>
                      <div className="role-album-info">
                        <strong>{album.name}</strong>
                        <small>{album.imageCount ? `${album.imageCount} 张图片` : ""}{album.imageCount && album.videoCount ? " · " : ""}{album.videoCount ? `${album.videoCount} 个视频` : ""}</small>
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <EmptyState query={query} onReset={() => { setQuery(""); setActiveFilter("全部"); }} />
              )
            ) : filteredAssets.length ? (
              <div className={view === "grid" ? "asset-grid" : "asset-list"}>
                {filteredAssets.map((asset) => (
                  <AssetCard
                    key={asset.id}
                    asset={asset}
                    onOpen={() => setSelected(asset)}
                    onFavorite={() =>
                      updateAsset(asset.id, { favorite: !asset.favorite })
                    }
                    list={view === "list"}
                  />
                ))}
              </div>
            ) : (
              <EmptyState
                query={query}
                onReset={() => {
                  setQuery("");
                  setActiveFilter("全部");
                }}
              />
            )}
          </section>
        </div>
      </main>
      {selected && (
        <DetailDrawer
          asset={selected}
          allAssets={assets}
          onClose={() => setSelected(null)}
          onFavorite={() =>
            updateAsset(selected.id, { favorite: !selected.favorite })
          }
          onUsed={() => updateAsset(selected.id, { used: !selected.used })}
          onEdit={() => setEditingAsset(selected)}
          onTagsUpdated={(tags) => {
            const updated = { ...selected, tags };
            setAssets((items) => items.map((item) => (item.id === selected.id ? updated : item)));
            setSelected(updated);
          }}
        />
      )}
      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onSubmit={handleUpload}
          fileInput={fileInput}
          assets={assets}
          uploading={uploading}
        />
      )}
      {editingAsset && (
        <EditAssetModal
          asset={editingAsset}
          onClose={() => setEditingAsset(null)}
          onSave={(fields) => saveAssetEdit(editingAsset.id, fields)}
        />
      )}
    </div>
  );
}

function AssetCard({ asset, onOpen, onFavorite, list }) {
  return (
    <article
      className={`asset-card ${list ? "asset-card-list" : ""}`}
      onClick={onOpen}
    >
      <div className={`asset-preview ${asset.color}`}>
        {asset.type === "video" ? (
          <video
            src={asset.src}
            poster={asset.thumb || undefined}
            muted
            playsInline
            preload="metadata"
            aria-label={`${asset.name} 视频预览`}
          />
        ) : (
          <img src={asset.src || asset.thumb} alt="" />
        )}
        {asset.type === "video" && (
          <div className="play-chip">
            <Play size={13} fill="currentColor" />
          </div>
        )}
        <div className="preview-top">
          <span className="type-pill">
            {asset.type === "video" ? (
              <>
                <FileVideo size={12} />
                视频
              </>
            ) : (
              <>
                <FileImage size={12} />
                图片
              </>
            )}
          </span>
          <button
            className={`heart-button ${asset.favorite ? "liked" : ""}`}
            onClick={(event) => {
              event.stopPropagation();
              onFavorite();
            }}
            aria-label={asset.favorite ? "取消收藏" : "收藏"}
          >
            <Heart size={16} fill={asset.favorite ? "currentColor" : "none"} />
          </button>
        </div>
        <div className="preview-bottom">
          {asset.used && (
            <span className="used-pill">
              <Check size={11} />
              已使用
            </span>
          )}
          {asset.duration && (
            <span className="duration-pill">{asset.duration}</span>
          )}
        </div>
      </div>
      <div className="asset-body">
        <div className="asset-title-row">
          <h3 title={asset.name}>{asset.name}</h3>
          <MoreHorizontal size={16} className="asset-more" />
        </div>
        <div className="asset-meta">
          <span>{asset.source}</span>
          <i />
          {asset.date}
        </div>
        <div className="tag-row">
          {asset.tags.map((tag) => (
            <span key={tag}>#{tag}</span>
          ))}
        </div>
      </div>
    </article>
  );
}

function DetailDrawer({ asset, allAssets = [], onClose, onFavorite, onUsed, onTagsUpdated, onEdit }) {
  const [editingTags, setEditingTags] = useState(false);
  const [tagsText, setTagsText] = useState(asset.tags.join(" "));
  const [savingTags, setSavingTags] = useState(false);
  const [tagError, setTagError] = useState("");
  useEffect(() => {
    setTagsText(asset.tags.join(" "));
    setEditingTags(false);
    setTagError("");
  }, [asset.id, asset.tags]);
  const parentAssets = (asset.parentAssetIds || [])
    .map((id) => allAssets.find((item) => String(item.id) === String(id)))
    .filter(Boolean);
  return (
    <div className="drawer-layer" onClick={onClose}>
      <aside
        className="detail-drawer"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-top">
          <span>素材详情</span>
          <div>
            <button aria-label="分享">
              <Share2 size={17} />
            </button>
            <button aria-label="关闭" onClick={onClose}>
              <X size={19} />
            </button>
          </div>
        </div>
        <div className={`detail-media ${asset.color}`}>
          {asset.type === "video" ? (
            <video src={asset.src} poster={asset.thumb || undefined} controls playsInline preload="metadata" />
          ) : (
            <img src={asset.src} alt={asset.name} />
          )}
        </div>
        <div className="detail-content">
          <div className="detail-title-row">
            <div>
              <p className="detail-type">
                {asset.type === "video" ? "视频素材" : "图片素材"} <span />{" "}
                {asset.source}
              </p>
              <h2>{asset.name}</h2>
            </div>
            <button
              className={`detail-heart ${asset.favorite ? "liked" : ""}`}
              onClick={onFavorite}
              aria-label="收藏"
            >
              <Heart
                size={19}
                fill={asset.favorite ? "currentColor" : "none"}
              />
            </button>
          </div>
          <div className="detail-tags">
            {editingTags ? (
              <div className="tag-editor">
                <Tag size={14} />
                <input
                  autoFocus
                  value={tagsText}
                  onChange={(event) => setTagsText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.preventDefault();
                    if (event.key === "Escape") setEditingTags(false);
                  }}
                  placeholder="例如 人物、转场、口播"
                  aria-label="编辑素材标签"
                />
                <button
                  className="tag-save-button"
                  disabled={savingTags}
                  onClick={async () => {
                    const tags = tagsText.split(/[,，\s]+/).map((tag) => tag.trim()).filter(Boolean).slice(0, 30);
                    setSavingTags(true);
                    setTagError("");
                    try {
                      await apiFetch(`/assets/${asset.id}/tags`, {
                        method: "POST",
                        body: JSON.stringify({ tags }),
                      });
                      onTagsUpdated?.(tags);
                      setEditingTags(false);
                    } catch (error) {
                      setTagError(error.message);
                    } finally {
                      setSavingTags(false);
                    }
                  }}
                >
                  <Check size={14} />
                  保存
                </button>
                <button className="tag-cancel-button" onClick={() => setEditingTags(false)} aria-label="取消编辑标签">
                  <X size={14} />
                </button>
              </div>
            ) : (
              <>
                {asset.tags.map((tag) => (
                  <button className="detail-tag-chip" key={tag} onClick={() => setEditingTags(true)} title="点击编辑标签">#{tag}</button>
                ))}
                <button onClick={() => setEditingTags(true)}>
                  <Plus size={13} />
                  {asset.tags.length ? "编辑标签" : "添加标签"}
                </button>
              </>
            )}
          </div>
          {tagError && <div className="tag-error">{tagError}</div>}
          <button
            className={`used-toggle ${asset.used ? "active" : ""}`}
            onClick={onUsed}
          >
            <Check size={15} />
            {asset.used ? "已标记使用过" : "标记为已使用"}
          </button>
          <div className="source-link-row">
            <span className="source-link-label">原视频链接</span>
            {asset.sourceUrl ? (
              <a href={asset.sourceUrl} target="_blank" rel="noreferrer">
                <ExternalLink size={13} />
                打开来源链接
              </a>
            ) : (
              <span className="source-link-empty">未填写</span>
            )}
          </div>
          <div className="detail-note">
            <div className="note-label">
              <Info size={14} />
              备注
            </div>
            <p>{asset.note}</p>
          </div>
          {parentAssets.length > 0 && (
            <div className="detail-related">
              <div className="note-label"><Sparkles size={14} />关联参考素材</div>
              <div className="related-list">
                {parentAssets.map((parent) => <span key={parent.id}>- {parent.name}</span>)}
              </div>
            </div>
          )}
          <div className="detail-info-grid">
            <div>
              <span>素材来源</span>
              <strong>
                <Tag size={14} />
                {asset.source}
              </strong>
            </div>
            <div>
              <span>所在文件夹</span>
              <strong>
                <FolderOpen size={14} />
                {asset.folder}
              </strong>
            </div>
            <div>
              <span>添加时间</span>
              <strong>{asset.date}</strong>
            </div>
            <div>
              <span>文件大小</span>
              <strong>{asset.size}</strong>
            </div>
            <div>
              <span>使用状态</span>
              <strong className={asset.used ? "status-ok" : "status-muted"}>
                {asset.used ? (
                  <>
                    <CircleCheck size={14} />
                    已使用
                  </>
                ) : (
                  "未使用"
                )}
              </strong>
            </div>
            <div>
              <span>云端状态</span>
              <strong className="status-ok">
                <Cloud size={14} />
                已同步
              </strong>
            </div>
          </div>
          <div className="drawer-actions">
            <button className="secondary-button" onClick={onEdit}>
              <Pencil size={16} />
              编辑素材
            </button>
            <button className="secondary-button">
              <Download size={16} />
              下载原文件
            </button>
            <button className="primary-button">
              <Copy size={16} />
              复制链接
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

function EditAssetModal({ asset, onClose, onSave }) {
  const [name, setName] = useState(asset.name || "");
  const [source, setSource] = useState(asset.source || "");
  const [sourceUrl, setSourceUrl] = useState(asset.sourceUrl || "");
  const [characterName, setCharacterName] = useState(asset.characterName || "");
  const [tagsText, setTagsText] = useState((asset.tags || []).join(" "));
  const [folder, setFolder] = useState(asset.folder || "灵感收集");
  const [note, setNote] = useState(asset.note || "");
  const [used, setUsed] = useState(Boolean(asset.used));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("标题不能为空");
      return;
    }
    const tags = tagsText.split(/[,，\s]+/).map((tag) => tag.trim()).filter(Boolean).slice(0, 30);
    if (folder === "角色设定" && !characterName.trim()) {
      setError("角色设定必须填写人物名称");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({
        name: trimmedName,
        source: source.trim() || "本地导入",
        sourceUrl: sourceUrl.trim(),
        characterName: characterName.trim(),
        folder,
        note: note.trim(),
        used,
        tags,
      });
    } catch (saveError) {
      setError(saveError.message || "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="modal-layer edit-layer" onClick={onClose}>
      <form className="edit-modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <div>
            <p className="eyebrow">EDIT ASSET</p>
            <h2>编辑素材</h2>
            <p className="modal-subtitle">修改标题、来源和创作信息，保存后会同步到云端。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            <X size={19} />
          </button>
        </div>
        <div className="edit-form-grid">
          <label className="edit-field-wide">
            标题名称
            <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          </label>
          <label>
            素材来源
            <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="例如 TikTok、Instagram" />
          </label>
          <label>
            所在文件夹
            <select value={folder} onChange={(event) => setFolder(event.target.value)}>
              <option>灵感收集</option>
              <option>成片</option>
              <option>角色设定</option>
              <option>项目资料</option>
            </select>
          </label>
          <label>
            人物名称
            <input value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="例如 林小栀" />
            <small>选择“角色设定”时必填，同名会聚合到同一人物相册</small>
          </label>
          <label className="edit-field-wide">
            原视频链接 / 参考链接
            <input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="粘贴原始链接" />
          </label>
          <label className="edit-field-wide">
            素材标签
            <input value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="人物、视频内容、转场" />
            <small>多个标签用空格或逗号分隔</small>
          </label>
          <label className="edit-field-wide">
            备注
            <textarea value={note} onChange={(event) => setNote(event.target.value)} rows="4" placeholder="记录这个素材的用法、镜头或创作想法" />
          </label>
          <button type="button" className={`edit-used-toggle ${used ? "active" : ""}`} onClick={() => setUsed(!used)}>
            <span>{used && <Check size={13} />}</span>
            <div><strong>{used ? "已使用过" : "尚未使用"}</strong><small>标记创作状态</small></div>
          </button>
        </div>
        {error && <div className="auth-error edit-error">{error}</div>}
        <div className="modal-foot">
          <button type="button" className="text-button" onClick={onClose}>取消</button>
          <button type="submit" className="primary-button" disabled={saving}>
            <Check size={16} />
            {saving ? "保存中…" : "保存修改"}
          </button>
        </div>
      </form>
    </div>
  );
}

function UploadModal({ onClose, onSubmit, fileInput, assets, uploading }) {
  const [files, setFiles] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [source, setSource] = useState("本地导入");
  const [sourceUrl, setSourceUrl] = useState("");
  const [characterName, setCharacterName] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [used, setUsed] = useState(false);
  const [folder, setFolder] = useState("灵感收集");
  const [parentAssetIds, setParentAssetIds] = useState([]);
  const [parentPickerOpen, setParentPickerOpen] = useState(false);
  const [formError, setFormError] = useState("");
  const referenceAssets = assets.filter((asset) => asset.type === "video" || asset.type === "image");
  const selectedReferenceAssets = referenceAssets.filter((asset) => parentAssetIds.includes(String(asset.id)));
  const referenceLabel = selectedReferenceAssets.length === 0
    ? "不关联参考素材"
    : selectedReferenceAssets.length === 1
      ? selectedReferenceAssets[0].name
      : `${selectedReferenceAssets[0].name} 等 ${selectedReferenceAssets.length} 个`;
  const chooseFiles = (fileList) =>
    setFiles((items) => [...items, ...Array.from(fileList || [])]);
  const submit = () => {
    if (!files.length) return;
    if (folder === "角色设定" && !characterName.trim()) {
      setFormError("角色设定必须填写人物名称");
      return;
    }
    setFormError("");
    const tags = tagsText
      .split(/[,，\s]+/)
      .map((tag) => tag.trim())
      .filter(Boolean);
    onSubmit(files, {
          source: source.trim() || "本地导入",
          sourceUrl: sourceUrl.trim(),
          characterName: characterName.trim(),
      tags: tags.length ? tags : ["待整理"],
      used,
      folder,
      parentAssetIds,
    });
  };
  return (
    <div className="modal-layer" onClick={onClose}>
      <div
        className="upload-modal upload-modal-expanded"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <p className="eyebrow">ADD TO LIBRARY</p>
            <h2>新增素材</h2>
            <p className="modal-subtitle">
              上传后补充信息，方便以后按来源和创作状态查找。
            </p>
          </div>
          <button onClick={onClose} aria-label="关闭">
            <X size={19} />
          </button>
        </div>
        <div
          className={`drop-zone ${dragging ? "dragging" : ""} ${files.length ? "has-files" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            chooseFiles(event.dataTransfer.files);
          }}
          onClick={() => fileInput.current?.click()}
        >
          {files.length ? (
            <>
              <div className="upload-icon success">
                <Check size={20} />
              </div>
              <strong>已选择 {files.length} 个文件</strong>
              <span>
                {files
                  .slice(0, 2)
                  .map((file) => file.name)
                  .join("、")}
                {files.length > 2 ? ` 等 ${files.length} 个文件` : ""}
              </span>
              <small>继续点击或拖拽可追加文件</small>
            </>
          ) : (
            <>
              <div className="upload-icon">
                <Upload size={20} />
              </div>
              <strong>拖拽文件到这里</strong>
              <span>或点击选择本地文件</span>
              <small>支持 MP4、MOV、PNG、JPG，单个文件最大 2 GB</small>
            </>
          )}
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept="video/*,image/*"
          onChange={(event) => chooseFiles(event.target.files)}
        />
        <div className="metadata-form">
          <label>
            素材来源
            <input
              value={source}
              onChange={(event) => setSource(event.target.value)}
              placeholder="例如 TikTok、Instagram、Midjourney"
            />
          </label>
          <label>
            原视频链接
            <input
              type="url"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="粘贴抖音 / Instagram / 视频号原链接"
            />
            <small>图片素材也可以填写原始参考链接</small>
          </label>
          <label>
            素材标签
            <input
              value={tagsText}
              onChange={(event) => setTagsText(event.target.value)}
              placeholder="例如 人物、视频内容、转场"
            />
            <small>多个标签用空格或逗号分隔</small>
          </label>
          {folder === "角色设定" && (
            <label>
              人物名称
              <input
                value={characterName}
                onChange={(event) => {
                  setCharacterName(event.target.value);
                  setFormError("");
                }}
                placeholder="例如 林小栀"
              />
              <small>同名素材会聚合到同一人物相册</small>
            </label>
          )}
          <label>
            关联参考素材
            <div className="reference-picker">
              <button
                type="button"
                className={`reference-picker-trigger ${selectedReferenceAssets.length ? "has-selection" : ""}`}
                onClick={() => setParentPickerOpen(!parentPickerOpen)}
                aria-expanded={parentPickerOpen}
              >
                <span>{referenceLabel}</span>
                <ChevronDown size={15} />
              </button>
              {parentPickerOpen && (
                <div className="reference-picker-menu">
                  <div className="reference-picker-head">
                    <span>可选，可多选</span>
                    {selectedReferenceAssets.length > 0 && (
                      <button type="button" onClick={() => setParentAssetIds([])}>清除选择</button>
                    )}
                  </div>
                  {referenceAssets.length ? referenceAssets.map((asset) => {
                    const selectedReference = parentAssetIds.includes(String(asset.id));
                    return (
                      <button
                        type="button"
                        className={`reference-option ${selectedReference ? "selected" : ""}`}
                        key={asset.id}
                        onClick={() => setParentAssetIds((items) => selectedReference
                          ? items.filter((id) => id !== String(asset.id))
                          : [...items, String(asset.id)])}
                      >
                        <span className="reference-option-check">{selectedReference && <Check size={13} />}</span>
                        <span>{asset.name}</span>
                      </button>
                    );
                  }) : (
                    <div className="reference-empty">暂无可关联素材</div>
                  )}
                </div>
              )}
            </div>
            <small>可不选，也可以多选参考素材</small>
          </label>
          <div className="metadata-row">
            <label>
              所在文件夹
              <select
                value={folder}
                onChange={(event) => setFolder(event.target.value)}
              >
                <option>灵感收集</option>
                <option>成片</option>
                <option>角色设定</option>
                <option>项目资料</option>
              </select>
            </label>
            <button
              type="button"
              className={`used-check ${used ? "active" : ""}`}
              onClick={() => setUsed(!used)}
            >
              <span>{used && <Check size={13} />}</span>
              <div>
                <strong>{used ? "已使用过" : "尚未使用"}</strong>
                <small>标记创作状态</small>
              </div>
            </button>
          </div>
        </div>
        {formError && <div className="upload-form-error">{formError}</div>}
        <div className="upload-dest">
          <div className="destination-icon">
            <Cloud size={17} />
          </div>
          <div>
            <strong>上传至腾讯云 COS</strong>
            <span>aigc-1257258774 · ap-shanghai</span>
          </div>
          <Check size={17} className="destination-check" />
        </div>
        <div className="modal-foot">
          <button className="text-button" onClick={onClose}>
            取消
          </button>
          <button
            className="primary-button"
            disabled={!files.length || uploading}
            onClick={submit}
          >
            <Upload size={16} />
            {uploading ? "上传中…" : "确认上传"}
          </button>
        </div>
      </div>
    </div>
  );
}
function EmptyState({ query, onReset }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Search size={22} />
      </div>
      <h3>没有找到匹配素材</h3>
      <p>{query ? `没有包含“${query}”的结果` : "当前账号下还没有素材"}</p>
      <button className="secondary-button" onClick={onReset}>
        清除筛选
      </button>
    </div>
  );
}
function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

createRoot(document.getElementById("root")).render(<App />);

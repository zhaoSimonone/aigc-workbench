import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Archive,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Clock3,
  Cloud,
  Copy,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  FileImage,
  FileText,
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
  MessageSquare,
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
  Users,
  Video,
  X,
  Info,
} from "lucide-react";
import "./styles.css";

const media = "/media/";
const API_BASE = "/api";
const LIBRARY_FOLDERS = ["灵感收集", "我的创作", "角色设定", "项目资料"];
const CHARACTER_CATEGORIES = ["正脸", "场景照", "动作", "服装", "其他"];
const VIDEO_ACCOUNT_PLATFORMS = ["抖音", "TikTok", "Instagram", "YouTube", "视频号", "小红书", "B站", "其他"];
const PROMPT_PLATFORMS = ["可灵", "即梦", "Runway", "剪映", "CapCut", "Sora", "Veo", "其他"];
const PROMPT_TASK_TYPES = ["视频换脸 / 发型替换", "图生视频", "文生视频", "人物动作", "运镜", "变装", "转场", "口播", "其他"];
const PROMPT_STATUSES = ["待验证", "验证成功", "精选", "已失效"];
const PROMPT_LINK_ROLES = [
  { value: "input_video", label: "输入视频", hint: "动作、服装、镜头参考", types: ["video"] },
  { value: "reference_image", label: "参考图片", hint: "人脸、发型参考", types: ["image"] },
  { value: "generated_output", label: "生成结果", hint: "AI 生成后的作品", types: ["video", "image"] },
];

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
    folder: "我的创作",
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
    characterCategory: asset.characterCategory || "",
    parentAssetIds: Array.isArray(asset.parentAssetIds) ? asset.parentAssetIds : [],
    derivedAssetIds: Array.isArray(asset.derivedAssetIds) ? asset.derivedAssetIds : [],
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
  const [characterAlbums, setCharacterAlbums] = useState([]);
  const [videoAccounts, setVideoAccounts] = useState([]);
  const [prompts, setPrompts] = useState([]);
  const [loadingAssets, setLoadingAssets] = useState(true);
  const [loadingVideoAccounts, setLoadingVideoAccounts] = useState(true);
  const [loadingPrompts, setLoadingPrompts] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [activeFolder, setActiveFolder] = useState("全部素材");
  const [activeCharacter, setActiveCharacter] = useState("");
  const [activeCharacterCategory, setActiveCharacterCategory] = useState("");
  const [activeFilter, setActiveFilter] = useState("全部");
  const [activeTag, setActiveTag] = useState("");
  const [tagOpen, setTagOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [detailTrail, setDetailTrail] = useState([]);
  const [showUpload, setShowUpload] = useState(false);
  const [showAlbumCreator, setShowAlbumCreator] = useState(false);
  const [showVideoAccountModal, setShowVideoAccountModal] = useState(false);
  const [editingVideoAccount, setEditingVideoAccount] = useState(null);
  const [showPromptModal, setShowPromptModal] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(null);
  const [copiedPromptId, setCopiedPromptId] = useState("");
  const [editingAsset, setEditingAsset] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [sort, setSort] = useState("最近添加");
  const [view, setView] = useState("grid");
  const fileInput = useRef(null);
  const folderInput = useRef(null);

  useEffect(() => {
    Promise.all([apiFetch("/assets"), apiFetch("/character-albums"), apiFetch("/video-accounts"), apiFetch("/prompts")])
      .then(([assetPayload, albumPayload, accountPayload, promptPayload]) => {
        setAssets((assetPayload.assets || []).map(normaliseAsset));
        setCharacterAlbums(albumPayload.albums || []);
        setVideoAccounts(accountPayload.accounts || []);
        setPrompts(promptPayload.prompts || []);
      })
      .catch((error) => setLoadError(error.message))
      .finally(() => {
        setLoadingAssets(false);
        setLoadingVideoAccounts(false);
        setLoadingPrompts(false);
      });
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
        label: "我的创作",
        count: assets.filter((item) => item.folder === "我的创作" || item.folder === "成片").length,
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
      { label: "提示词库", count: prompts.length, icon: FileText },
      { label: "视频账号", count: videoAccounts.length, icon: Users },
    ],
    [assets, prompts, videoAccounts],
  );
  const filteredAssets = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const result = assets.filter((asset) => {
      const inFolder =
        activeFolder === "全部素材" ||
        (activeFolder === "我的创作" ? (asset.folder === "我的创作" || asset.folder === "成片") : asset.folder === activeFolder);
      const inCharacter =
        activeFolder !== "角色设定" ||
        !activeCharacter ||
        (activeCharacter === "待归类人物" || activeCharacter === "未命名角色" ? !asset.characterName : asset.characterName === activeCharacter);
      const inCharacterCategory =
        activeFolder !== "角色设定" || !activeCharacterCategory || asset.characterCategory === activeCharacterCategory;
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
      return inFolder && inCharacter && inCharacterCategory && inType && inTag && inSearch;
    });
    return [...result].sort((a, b) =>
      sort === "名称"
        ? a.name.localeCompare(b.name, "zh")
        : new Date(b.createdAt || 0) - new Date(a.createdAt || 0),
    );
  }, [assets, activeFolder, activeCharacter, activeCharacterCategory, activeFilter, activeTag, query, sort]);
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
    characterAlbums.forEach((album) => groups.set(album.name, { name: album.name, assets: [] }));
    filteredAssets.forEach((asset) => {
      const name = asset.characterName || "待归类人物";
      const existing = groups.get(name) || { name, assets: [] };
      existing.assets.push(asset);
      groups.set(name, existing);
    });
    return [...groups.values()]
      .map((group) => ({
        ...group,
        count: group.assets.length,
        cover: group.assets.find((asset) => asset.thumb || asset.src)?.thumb || group.assets.find((asset) => asset.src)?.src || "",
        videoCount: group.assets.filter((asset) => asset.type === "video").length,
        imageCount: group.assets.filter((asset) => asset.type === "image").length,
        categoryCounts: group.assets.reduce((counts, asset) => {
          const category = asset.characterCategory || "未分类";
          counts[category] = (counts[category] || 0) + 1;
          return counts;
        }, {}),
      }))
      .sort((a, b) => a.name.localeCompare(b.name, "zh"));
  }, [filteredAssets, characterAlbums, activeFolder, activeCharacter]);
  const updateAsset = async (id, patch) => {
    try {
      const payload = await apiFetch(`/assets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      const updated = normaliseAsset(payload.asset);
      setAssets((items) => items.map((item) => (item.id === id ? updated : item)));
      setSelected((item) => (item?.id === id ? updated : item));
      setDetailTrail((items) => items.map((item) => (item.id === id ? updated : item)));
    } catch (error) {
      setLoadError(error.message);
    }
  };
  const openAsset = (asset) => {
    setDetailTrail([]);
    setSelected(asset);
  };
  const openRelatedAsset = (asset) => {
    setDetailTrail((trail) => (selected ? [...trail, selected] : trail));
    setSelected(asset);
  };
  const backToPreviousAsset = () => {
    const previous = detailTrail[detailTrail.length - 1];
    if (!previous) return;
    setDetailTrail((trail) => trail.slice(0, -1));
    setSelected(previous);
  };
  const handleUpload = async (files, metadata) => {
    setUploading(true);
    setLoadError("");
    try {
      const uploaded = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const requestedName = String(metadata.name || "").trim();
        const fileStem = file.name.replace(/\.[^/.]+$/, "");
        if (requestedName) form.append("name", files.length === 1 ? requestedName : `${requestedName} · ${fileStem}`);
        form.append("source", metadata.source || "本地导入");
        form.append("sourceUrl", metadata.sourceUrl || "");
        form.append("characterName", metadata.characterName || "");
        form.append("characterCategory", metadata.characterCategory || "");
        form.append("tags", JSON.stringify(metadata.tags || []));
        form.append("used", String(Boolean(metadata.used)));
        form.append("folder", metadata.folder || "灵感收集");
        if (metadata.parentAssetIds?.length) form.append("parentAssetIds", JSON.stringify(metadata.parentAssetIds));
        const payload = await apiFetch("/assets", { method: "POST", body: form });
        uploaded.push(normaliseAsset(payload.asset));
      }
      setAssets((items) => [...uploaded, ...items]);
      if (metadata.characterName) {
        setCharacterAlbums((albums) => albums.some((album) => album.name === metadata.characterName)
          ? albums
          : [...albums, { id: `local-${metadata.characterName}`, name: metadata.characterName, assetCount: 0 }]);
      }
      setShowUpload(false);
    } catch (error) {
      setLoadError(error.message);
    } finally {
      setUploading(false);
    }
  };
  const createCharacterAlbum = async (name) => {
    const payload = await apiFetch("/character-albums", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    setCharacterAlbums((albums) => [...albums, payload.album]);
    setActiveFolder("角色设定");
    setActiveCharacter(payload.album.name);
    setActiveCharacterCategory("");
    setActiveFilter("全部");
    setActiveTag("");
    setQuery("");
    return payload.album;
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
  const replaceAssetFile = async (id, file) => {
    const form = new FormData();
    form.append("file", file);
    try {
      const payload = await apiFetch(`/assets/${id}/file`, { method: "POST", body: form });
      const updated = normaliseAsset(payload.asset);
      setAssets((items) => items.map((item) => (item.id === id ? updated : item)));
      setSelected((item) => (item?.id === id ? updated : item));
      setDetailTrail((items) => items.map((item) => (item.id === id ? updated : item)));
      return updated;
    } catch (error) {
      setLoadError(error.message);
      throw error;
    }
  };
  const deleteAsset = async (id) => {
    try {
      await apiFetch(`/assets/${id}`, { method: "DELETE" });
      setAssets((items) => items.filter((item) => item.id !== id));
      setSelected(null);
      setDetailTrail([]);
    } catch (error) {
      setLoadError(error.message);
    }
  };
  const saveVideoAccount = async (fields, accountId) => {
    const payload = await apiFetch(accountId ? `/video-accounts/${accountId}` : "/video-accounts", {
      method: accountId ? "PATCH" : "POST",
      body: JSON.stringify(fields),
    });
    if (accountId) {
      setVideoAccounts((items) => items.map((item) => (item.id === accountId ? payload.account : item)));
    } else {
      setVideoAccounts((items) => [payload.account, ...items]);
    }
    setShowVideoAccountModal(false);
    setEditingVideoAccount(null);
  };
  const deleteVideoAccount = async (account) => {
    if (!window.confirm(`确定取消收藏“${account.accountName || account.profileUrl}”吗？`)) return;
    try {
      await apiFetch(`/video-accounts/${account.id}`, { method: "DELETE" });
      setVideoAccounts((items) => items.filter((item) => item.id !== account.id));
    } catch (error) {
      setLoadError(error.message);
    }
  };
  const savePrompt = async (fields, promptId) => {
    const payload = await apiFetch(promptId ? `/prompts/${promptId}` : "/prompts", {
      method: promptId ? "PATCH" : "POST",
      body: JSON.stringify(fields),
    });
    if (promptId) {
      setPrompts((items) => items.map((item) => (item.id === promptId ? payload.prompt : item)));
    } else {
      setPrompts((items) => [payload.prompt, ...items]);
    }
    setShowPromptModal(false);
    setEditingPrompt(null);
    return payload.prompt;
  };
  const deletePrompt = async (prompt) => {
    if (!window.confirm(`确定删除提示词“${prompt.title}”吗？关联不会删除素材本身。`)) return;
    try {
      await apiFetch(`/prompts/${prompt.id}`, { method: "DELETE" });
      setPrompts((items) => items.filter((item) => item.id !== prompt.id));
    } catch (error) {
      setLoadError(error.message);
    }
  };
  const copyPrompt = async (prompt) => {
    try {
      await navigator.clipboard.writeText(prompt.content);
      setCopiedPromptId(prompt.id);
      window.setTimeout(() => setCopiedPromptId((id) => (id === prompt.id ? "" : id)), 1800);
    } catch (_) {
      setLoadError("无法复制提示词，请检查浏览器剪贴板权限");
    }
  };
  const total = assets.length;
  const videoCount = assets.filter((item) => item.type === "video").length;
  const imageCount = assets.filter((item) => item.type === "image").length;
  const usedCount = assets.filter((item) => item.used).length;
  const verifiedPromptCount = prompts.filter((item) => item.status === "验证成功" || item.status === "精选").length;
  const featuredPromptCount = prompts.filter((item) => item.status === "精选").length;
  const promptOutputCount = prompts.reduce(
    (count, item) => count + (item.assetLinks || []).filter((link) => link.usageRole === "generated_output").length,
    0,
  );
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
  const promptStats = [
    { label: "提示词总数", value: prompts.length, hint: prompts.length ? "持续积累" : "开始沉淀", icon: FileText, tone: "red" },
    { label: "已验证", value: verifiedPromptCount, hint: prompts.length ? "可复用" : "待验证", icon: CircleCheck, tone: "green" },
    { label: "精选提示词", value: featuredPromptCount, hint: featuredPromptCount ? "效果稳定" : "尚未标记", icon: Heart, tone: "yellow" },
    { label: "关联作品", value: promptOutputCount, hint: promptOutputCount ? "已有生成结果" : "等待关联", icon: Play, tone: "blue" },
  ];
  const libraryTitle = activeCharacter ? `角色设定 / ${activeCharacter}` : activeFolder;
  const isRoleAlbumView = activeFolder === "角色设定" && !activeCharacter;
  const isPromptView = activeFolder === "提示词库";
  const isVideoAccountView = activeFolder === "视频账号";
  const displayStats = isPromptView ? promptStats : stats;
  const uploadDefaultFolder = LIBRARY_FOLDERS.includes(activeFolder)
    ? activeFolder
    : "灵感收集";
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
                setActiveCharacterCategory("");
                setActiveTag("");
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
              setActiveCharacterCategory("");
              setActiveTag("");
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
              setActiveCharacterCategory("");
              setActiveTag("");
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
              setActiveCharacterCategory("");
              setActiveTag("");
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
              setActiveCharacterCategory("");
              setActiveTag("");
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
              <h1>{isPromptView ? "提示词库" : "素材库"}</h1>
              <p className="intro-copy">{isPromptView ? "把有效的方法留下，让下一次生成更接近你想要的结果。" : "把灵感收好，下一条作品会更快开始。"}</p>
            </div>
            <div className="intro-actions">
              {isPromptView ? (
                <button className="primary-button" onClick={() => setShowPromptModal(true)}>
                  <Plus size={18} />
                  新增提示词
                </button>
              ) : isVideoAccountView ? (
                <button className="primary-button" onClick={() => setShowVideoAccountModal(true)}>
                  <Plus size={18} />
                  收藏视频账号
                </button>
              ) : (
                <>
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
                </>
              )}
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
            {displayStats.map(({ label, value, hint, icon: Icon, tone }) => (
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
                    {isPromptView
                      ? `${prompts.length} 条提示词`
                      : isVideoAccountView
                      ? `${videoAccounts.length} 个账号`
                      : isRoleAlbumView
                      ? `${roleAlbums.length} 个角色`
                      : filteredAssets.length !== assets.length
                      ? `${filteredAssets.length} / `
                      : ""}
                    {!isRoleAlbumView && !isVideoAccountView && !isPromptView && total}
                  </span>
                </h2>
                {activeCharacter && (
                  <button className="album-back-button" onClick={() => { setActiveCharacter(""); setActiveCharacterCategory(""); }}>
                    <ChevronDown size={14} /> 返回角色相册
                  </button>
                )}
                <p>{isRoleAlbumView ? "按人物聚合，进入相册后再按正脸、场景照等分类查看。" : isPromptView ? "完整保存有效提示词，并关联输入素材和生成作品。" : isVideoAccountView ? "收藏值得持续关注的创作者，按平台集中管理。" : "你的创作参考与工作文件，集中在这里。"}</p>
              </div>
              {isRoleAlbumView && (
                <button className="secondary-button album-create-button" onClick={() => setShowAlbumCreator(true)}>
                  <Plus size={15} />
                  新建人物相册
                </button>
              )}
            <div className="sync-status">
              <span className="sync-dot" />
                已同步 <b>腾讯云 COS · 数据库</b>
            </div>
          </div>
          {loadError && <div className="inline-error">{loadError}</div>}
          {isPromptView ? (
            <PromptLibrary
              prompts={prompts}
              assets={assets}
              loading={loadingPrompts}
              copiedPromptId={copiedPromptId}
              onCreate={() => setShowPromptModal(true)}
              onCopy={copyPrompt}
              onEdit={(prompt) => setEditingPrompt(prompt)}
              onDelete={deletePrompt}
              onToggleFavorite={(prompt) => savePrompt({ favorite: !prompt.favorite }, prompt.id).catch((error) => setLoadError(error.message))}
              onOpenAsset={openAsset}
            />
          ) : isVideoAccountView ? (
            <VideoAccountsPanel
              accounts={videoAccounts}
              loading={loadingVideoAccounts}
              onCreate={() => setShowVideoAccountModal(true)}
              onEdit={(account) => setEditingVideoAccount(account)}
              onDelete={deleteVideoAccount}
            />
          ) : (
            <>
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
            {activeCharacter && (
              <div className="character-category-tabs" aria-label="人物素材分类">
                {["", ...CHARACTER_CATEGORIES].map((category) => {
                  const count = assets.filter((item) => {
                    const matchesCharacter = activeCharacter === "待归类人物" || activeCharacter === "未命名角色"
                      ? !item.characterName
                      : item.characterName === activeCharacter;
                    return item.folder === "角色设定" && matchesCharacter && (!category ? true : item.characterCategory === category);
                  }).length;
                  return (
                    <button
                      key={category || "all"}
                      className={activeCharacterCategory === category ? "active" : ""}
                      onClick={() => setActiveCharacterCategory(category)}
                    >
                      {category || "全部"}<span>{count}</span>
                    </button>
                  );
                })}
              </div>
            )}
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
                        setActiveCharacterCategory("");
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
                        <small>{Object.entries(album.categoryCounts).map(([category, count], index) => `${index ? " · " : ""}${category} ${count}`).join("")}</small>
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
                    onOpen={() => openAsset(asset)}
                    onFavorite={() =>
                      updateAsset(asset.id, { favorite: !asset.favorite })
                    }
                    list={view === "list"}
                  />
                ))}
              </div>
            ) : activeCharacter ? (
              <div className="empty-state">
                <div className="empty-icon"><FileImage size={22} /></div>
                <h3>这个人物相册还没有素材</h3>
                <p>上传第一张正脸或场景照，开始整理这个人物。</p>
                <button className="primary-button" onClick={() => setShowUpload(true)}><Upload size={16} />上传人物素材</button>
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
            </>
          )}
          </section>
        </div>
      </main>
      {selected && (
        <DetailDrawer
          asset={selected}
          allAssets={assets}
          onClose={() => {
            setSelected(null);
            setDetailTrail([]);
          }}
          onFavorite={() =>
            updateAsset(selected.id, { favorite: !selected.favorite })
          }
          onUsed={() => updateAsset(selected.id, { used: !selected.used })}
          onEdit={() => setEditingAsset(selected)}
          onDelete={() => deleteAsset(selected.id)}
          onNavigate={openRelatedAsset}
          onBack={detailTrail.length ? backToPreviousAsset : undefined}
          onAssetUpdated={(updated) => {
            setAssets((items) => items.map((item) => (item.id === updated.id ? updated : item)));
            setSelected(updated);
            setDetailTrail((items) => items.map((item) => (item.id === updated.id ? updated : item)));
          }}
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
          defaultFolder={uploadDefaultFolder}
          defaultCharacterName={activeFolder === "角色设定" ? activeCharacter : ""}
          characterAlbums={characterAlbums}
        />
      )}
      {showAlbumCreator && (
        <CreateCharacterAlbumModal
          onClose={() => setShowAlbumCreator(false)}
          onCreate={createCharacterAlbum}
        />
      )}
      {(showVideoAccountModal || editingVideoAccount) && (
        <VideoAccountModal
          key={editingVideoAccount?.id || "new"}
          account={editingVideoAccount}
          onClose={() => {
            setShowVideoAccountModal(false);
            setEditingVideoAccount(null);
          }}
          onSave={(fields) => saveVideoAccount(fields, editingVideoAccount?.id)}
        />
      )}
      {(showPromptModal || editingPrompt) && (
        <PromptModal
          key={editingPrompt?.id || "new"}
          prompt={editingPrompt}
          assets={assets}
          onClose={() => {
            setShowPromptModal(false);
            setEditingPrompt(null);
          }}
          onSave={(fields) => savePrompt(fields, editingPrompt?.id)}
        />
      )}
      {editingAsset && (
        <EditAssetModal
          asset={editingAsset}
          onClose={() => setEditingAsset(null)}
          onSave={(fields) => saveAssetEdit(editingAsset.id, fields)}
          onReplace={(file) => replaceAssetFile(editingAsset.id, file)}
        />
      )}
    </div>
  );
}

function PromptLibrary({ prompts, assets, loading, copiedPromptId, onCreate, onCopy, onEdit, onDelete, onToggleFavorite, onOpenAsset }) {
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const platforms = useMemo(() => [...new Set(prompts.map((item) => item.platform).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh")), [prompts]);
  const tags = useMemo(() => [...new Set(prompts.flatMap((item) => item.tags || []))].sort((a, b) => a.localeCompare(b, "zh")), [prompts]);
  const filteredPrompts = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return prompts.filter((prompt) => {
      const inPlatform = !platform || prompt.platform === platform;
      const inStatus = !status || prompt.status === status;
      const inTag = !tag || (prompt.tags || []).includes(tag);
      const inSearch = !normalized || `${prompt.title} ${prompt.content} ${prompt.taskType} ${prompt.model} ${(prompt.tags || []).join(" ")}`.toLowerCase().includes(normalized);
      return inPlatform && inStatus && inTag && inSearch;
    });
  }, [platform, prompts, query, status, tag]);
  const resetFilters = () => {
    setQuery("");
    setPlatform("");
    setStatus("");
    setTag("");
  };
  if (loading) {
    return <div className="empty-state"><div className="empty-icon"><Cloud size={22} /></div><h3>正在加载提示词</h3><p>同步你的创作方法与关联素材</p></div>;
  }
  return (
    <>
      <div className="prompt-toolbar">
        <div className="search-box prompt-search-box">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索提示词、场景或标签" />
        </div>
        <div className="prompt-filter-controls">
          <select value={platform} onChange={(event) => setPlatform(event.target.value)} aria-label="筛选平台">
            <option value="">全部平台</option>
            {platforms.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="筛选状态">
            <option value="">全部状态</option>
            {PROMPT_STATUSES.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select value={tag} onChange={(event) => setTag(event.target.value)} aria-label="筛选标签">
            <option value="">全部标签</option>
            {tags.map((item) => <option key={item}>{item}</option>)}
          </select>
        </div>
      </div>
      {!filteredPrompts.length ? (
        <div className="video-account-empty prompt-empty">
          <div className="empty-icon"><FileText size={22} /></div>
          <h3>{prompts.length ? "没有找到匹配提示词" : "还没有沉淀提示词"}</h3>
          <p>{prompts.length ? "试试更换搜索词或筛选条件。" : "完整粘贴一次有效提示词，再关联输入素材和生成结果。"}</p>
          <button className="primary-button" onClick={prompts.length ? resetFilters : onCreate}><Plus size={16} />{prompts.length ? "清除筛选" : "新增第一条提示词"}</button>
        </div>
      ) : (
        <div className="prompt-grid">
          {filteredPrompts.map((prompt) => (
            <article className="prompt-card" key={prompt.id}>
              <div className="prompt-card-head">
                <div className="prompt-badges">
                  <span className="prompt-platform-badge">{prompt.platform}</span>
                  <span className="prompt-status-badge" data-status={prompt.status}>{prompt.status}</span>
                </div>
                <div className="prompt-card-actions">
                  <button onClick={() => onCopy(prompt)} aria-label={`复制${prompt.title}`} title={copiedPromptId === prompt.id ? "已复制" : "复制提示词"}>
                    {copiedPromptId === prompt.id ? <Check size={15} /> : <Copy size={15} />}
                  </button>
                  <button className={prompt.favorite ? "active" : ""} onClick={() => onToggleFavorite(prompt)} aria-label={prompt.favorite ? "取消收藏" : "收藏"} title={prompt.favorite ? "取消收藏" : "收藏"}>
                    <Heart size={15} fill={prompt.favorite ? "currentColor" : "none"} />
                  </button>
                  <button onClick={() => onEdit(prompt)} aria-label={`编辑${prompt.title}`} title="编辑"><Pencil size={15} /></button>
                  <button className="prompt-delete" onClick={() => onDelete(prompt)} aria-label={`删除${prompt.title}`} title="删除"><Trash2 size={15} /></button>
                </div>
              </div>
              <h3 title={prompt.title}>{prompt.title}</h3>
              {(prompt.taskType || prompt.model) && (
                <div className="prompt-model-meta">{[prompt.taskType, prompt.model].filter(Boolean).join(" · ")}</div>
              )}
              <p className="prompt-content-preview">{prompt.content}</p>
              {(prompt.tags || []).length > 0 && <div className="tag-row prompt-tag-row">{prompt.tags.map((item) => <span key={item}>#{item}</span>)}</div>}
              <PromptAssetLinks assetLinks={prompt.assetLinks} assets={assets} onOpenAsset={onOpenAsset} />
              <div className="prompt-card-foot">
                <span>{prompt.rating ? `${prompt.rating} / 5 分` : "未评分"}</span>
                <span>更新于 {formatPromptDate(prompt.updatedAt)}</span>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

function PromptAssetLinks({ assetLinks = [], assets, onOpenAsset }) {
  const assetById = useMemo(() => new Map(assets.map((asset) => [String(asset.id), asset])), [assets]);
  const links = assetLinks.map((link) => ({ ...link, asset: assetById.get(String(link.assetId)) })).filter((link) => link.asset);
  if (!links.length) return <div className="prompt-linked-assets prompt-linked-assets-empty"><span>尚未关联素材</span></div>;
  return (
    <div className="prompt-linked-assets">
      {links.map((link) => {
        const role = PROMPT_LINK_ROLES.find((item) => item.value === link.usageRole) || { label: "关联素材" };
        return (
          <button key={`${link.assetId}-${link.usageRole}`} onClick={() => onOpenAsset(link.asset)} title={`打开${role.label}：${link.asset.name}`}>
            <span>{role.label}</span>
            <strong>{link.asset.name}</strong>
            <ChevronRight size={13} />
          </button>
        );
      })}
    </div>
  );
}

function PromptModal({ prompt, assets, onClose, onSave }) {
  const initialLinks = Object.fromEntries(PROMPT_LINK_ROLES.map((role) => [
    role.value,
    String((prompt?.assetLinks || []).find((link) => link.usageRole === role.value)?.assetId || ""),
  ]));
  const [title, setTitle] = useState(prompt?.title || "");
  const [content, setContent] = useState(prompt?.content || "");
  const [platform, setPlatform] = useState(prompt?.platform || PROMPT_PLATFORMS[0]);
  const [taskType, setTaskType] = useState(prompt?.taskType || "");
  const [model, setModel] = useState(prompt?.model || "");
  const [tagsText, setTagsText] = useState((prompt?.tags || []).join(" "));
  const [status, setStatus] = useState(prompt?.status || "待验证");
  const [rating, setRating] = useState(prompt?.rating ? String(prompt.rating) : "");
  const [note, setNote] = useState(prompt?.note || "");
  const [favorite, setFavorite] = useState(Boolean(prompt?.favorite));
  const [linkSelections, setLinkSelections] = useState(initialLinks);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    if (!title.trim()) {
      setError("请输入提示词名称");
      return;
    }
    if (!content.trim()) {
      setError("请输入完整提示词");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({
        title: title.trim(),
        content: content.trim(),
        platform,
        taskType,
        model: model.trim(),
        tags: tagsText.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean).slice(0, 30),
        status,
        rating: rating ? Number(rating) : null,
        favorite,
        note: note.trim(),
        assetLinks: PROMPT_LINK_ROLES
          .map((role) => ({ assetId: linkSelections[role.value], usageRole: role.value }))
          .filter((link) => link.assetId),
      });
    } catch (saveError) {
      setError(saveError.message || "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="modal-layer edit-layer" onClick={onClose}>
      <form className="edit-modal prompt-modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <div>
            <p className="eyebrow">{prompt ? "EDIT PROMPT" : "SAVE A PROMPT"}</p>
            <h2>{prompt ? "编辑提示词" : "新增提示词"}</h2>
            <p className="modal-subtitle">整段原样保存，无需拆分正向和负向提示词。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        </div>
        <div className="edit-form-grid prompt-form-grid">
          <label className="edit-field-wide">
            提示词名称
            <input value={title} onChange={(event) => setTitle(event.target.value)} autoFocus placeholder="例如 视频换脸换发型｜保持原服装与镜头" />
          </label>
          <label>
            使用平台
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
              {PROMPT_PLATFORMS.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>
            使用场景 <small>可选</small>
            <select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
              <option value="">请选择场景</option>
              {PROMPT_TASK_TYPES.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="edit-field-wide prompt-content-field">
            完整提示词
            <textarea value={content} onChange={(event) => setContent(event.target.value)} rows="12" placeholder="完整粘贴你的提示词，包括要求保持的内容和需要避免的问题。" />
            <small>原文会完整保存，不会自动拆分、改写或遗漏。</small>
          </label>
          <div className="prompt-link-fields edit-field-wide">
            <div className="prompt-link-fields-head">
              <strong>关联素材</strong>
              <small>可选：关联输入、参考和生成结果，方便回看效果。</small>
            </div>
            <div className="prompt-link-grid">
              {PROMPT_LINK_ROLES.map((role) => {
                const options = assets.filter((asset) => role.types.includes(asset.type));
                return (
                  <label key={role.value}>
                    <span>{role.label}</span>
                    <select value={linkSelections[role.value]} onChange={(event) => setLinkSelections((items) => ({ ...items, [role.value]: event.target.value }))}>
                      <option value="">暂不关联</option>
                      {options.map((asset) => <option value={asset.id} key={asset.id}>{asset.name}</option>)}
                    </select>
                    <small>{role.hint}</small>
                  </label>
                );
              })}
            </div>
          </div>
          <label>
            标签 <small>可选</small>
            <input value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="换脸、换发型、人物一致性" />
          </label>
          <label>
            模型版本 <small>可选</small>
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如 可灵 1.6" />
          </label>
          <label>
            验证状态
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              {PROMPT_STATUSES.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>
            效果评分 <small>可选</small>
            <select value={rating} onChange={(event) => setRating(event.target.value)}>
              <option value="">暂不评分</option>
              {[1, 2, 3, 4, 5].map((item) => <option value={item} key={item}>{item} / 5</option>)}
            </select>
          </label>
          <label className="edit-field-wide">
            使用备注 <small>可选</small>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} rows="3" placeholder="记录关键观察点，例如领口、发型稳定性和人物身份一致性。" />
          </label>
          <button type="button" className={`prompt-favorite-toggle ${favorite ? "active" : ""}`} onClick={() => setFavorite(!favorite)}>
            <Heart size={15} fill={favorite ? "currentColor" : "none"} />
            {favorite ? "已收藏这条提示词" : "收藏这条提示词"}
          </button>
        </div>
        {error && <div className="auth-error edit-error">{error}</div>}
        <div className="modal-foot">
          <button type="button" className="text-button" onClick={onClose}>取消</button>
          <button type="submit" className="primary-button" disabled={saving}><Check size={16} />{saving ? "保存中…" : "保存提示词"}</button>
        </div>
      </form>
    </div>
  );
}

function formatPromptDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "刚刚";
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function VideoAccountsPanel({ accounts, loading, onCreate, onEdit, onDelete }) {
  if (loading) {
    return (
      <div className="empty-state">
        <div className="empty-icon"><Cloud size={22} /></div>
        <h3>正在加载视频账号</h3>
        <p>从云端同步你收藏的创作者</p>
      </div>
    );
  }
  if (!accounts.length) {
    return (
      <div className="video-account-empty">
        <div className="empty-icon"><Users size={22} /></div>
        <h3>还没有收藏视频账号</h3>
        <p>把值得持续关注的抖音、TikTok 或 Instagram 账号集中保存。</p>
        <button className="primary-button" onClick={onCreate}><Plus size={16} />收藏第一个账号</button>
      </div>
    );
  }
  return (
    <div className="video-account-grid">
      {accounts.map((account) => (
        <article className="video-account-card" key={account.id}>
          <div className="video-account-card-head">
            <span className="video-account-platform"><Users size={14} />{account.platform}</span>
            <div className="video-account-actions">
              <button onClick={() => onEdit(account)} aria-label={`编辑${account.accountName || account.platform}`} title="编辑">
                <Pencil size={15} />
              </button>
              <button className="video-account-delete" onClick={() => onDelete(account)} aria-label={`取消收藏${account.accountName || account.platform}`} title="取消收藏">
                <Trash2 size={15} />
              </button>
            </div>
          </div>
          <div className="video-account-name-row">
            <h3>{account.accountName || "未命名账号"}</h3>
            <a
              className="video-account-open"
              href={account.profileUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={`打开${account.accountName || account.platform}主页`}
              title="打开主页"
            >
              <ExternalLink size={15} />
            </a>
          </div>
          {account.note ? <p className="video-account-note">{account.note}</p> : <p className="video-account-note muted">暂无备注</p>}
          <div className="video-account-meta">
            <span>收藏于 {formatAccountDate(account.createdAt)}</span>
          </div>
        </article>
      ))}
    </div>
  );
}

function VideoAccountModal({ account, onClose, onSave }) {
  const [platform, setPlatform] = useState(account?.platform || VIDEO_ACCOUNT_PLATFORMS[0]);
  const [accountName, setAccountName] = useState(account?.accountName || "");
  const [profileUrl, setProfileUrl] = useState(account?.profileUrl || "");
  const [note, setNote] = useState(account?.note || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    const url = profileUrl.trim();
    if (!url) {
      setError("请输入关注链接");
      return;
    }
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
    } catch (_) {
      setError("请输入有效的 HTTP(S) 关注链接");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({ platform, accountName: accountName.trim(), profileUrl: url, note: note.trim() });
    } catch (saveError) {
      setError(saveError.message || "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="modal-layer edit-layer" onClick={onClose}>
      <form className="edit-modal video-account-modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <div>
            <p className="eyebrow">{account ? "EDIT VIDEO ACCOUNT" : "NEW VIDEO ACCOUNT"}</p>
            <h2>{account ? "编辑视频账号" : "收藏视频账号"}</h2>
            <p className="modal-subtitle">保存账号主页链接，之后可以从这里快速回到原平台。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        </div>
        <div className="edit-form-grid video-account-form">
          <label>
            平台
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
              {VIDEO_ACCOUNT_PLATFORMS.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>
            账号名称 <small>可选</small>
            <input value={accountName} onChange={(event) => setAccountName(event.target.value)} placeholder="例如 摄影师 Alex" />
          </label>
          <label className="edit-field-wide">
            关注链接
            <input type="url" required value={profileUrl} onChange={(event) => setProfileUrl(event.target.value)} placeholder="https://www.douyin.com/user/..." />
            <small>填写账号主页地址，而不是单条视频地址</small>
          </label>
          <label className="edit-field-wide">
            备注 <small>可选</small>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} rows="4" placeholder="记录账号的内容方向、值得关注的原因等" />
          </label>
        </div>
        {error && <div className="auth-error edit-error">{error}</div>}
        <div className="modal-foot">
          <button type="button" className="text-button" onClick={onClose}>取消</button>
          <button type="submit" className="primary-button" disabled={saving}>
            <Check size={16} />
            {saving ? "保存中…" : account ? "保存修改" : "收藏账号"}
          </button>
        </div>
      </form>
    </div>
  );
}

function formatAccountDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "刚刚";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "numeric", day: "numeric" });
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

function DetailDrawer({ asset, allAssets = [], onClose, onFavorite, onUsed, onTagsUpdated, onEdit, onDelete, onNavigate, onBack, onAssetUpdated }) {
  const [editingTags, setEditingTags] = useState(false);
  const [tagsText, setTagsText] = useState(asset.tags.join(" "));
  const [savingTags, setSavingTags] = useState(false);
  const [tagError, setTagError] = useState("");
  const [comments, setComments] = useState([]);
  const [commentText, setCommentText] = useState("");
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentsSaving, setCommentsSaving] = useState(false);
  const [commentError, setCommentError] = useState("");
  const [editingCommentId, setEditingCommentId] = useState("");
  const [editingCommentText, setEditingCommentText] = useState("");
  const isCreation = asset.folder === "我的创作" || asset.folder === "成片";
  useEffect(() => {
    setTagsText(asset.tags.join(" "));
    setEditingTags(false);
    setTagError("");
  }, [asset.id, asset.tags]);
  useEffect(() => {
    if (!isCreation) {
      setComments([]);
      return undefined;
    }
    let cancelled = false;
    setCommentsLoading(true);
    setCommentError("");
    apiFetch(`/assets/${asset.id}/comments`)
      .then((payload) => {
        if (!cancelled) setComments(payload.comments || []);
      })
      .catch((error) => {
        if (!cancelled) setCommentError(error.message);
      })
      .finally(() => {
        if (!cancelled) setCommentsLoading(false);
      });
    return () => { cancelled = true; };
  }, [asset.id, isCreation]);
  const parentAssets = (asset.parentAssetIds || [])
    .map((id) => allAssets.find((item) => String(item.id) === String(id)))
    .filter(Boolean);
  const derivedAssets = (asset.derivedAssetIds || [])
    .map((id) => allAssets.find((item) => String(item.id) === String(id)))
    .filter(Boolean);
  const submitComment = async () => {
    const content = commentText.trim();
    if (!content || commentsSaving) return;
    setCommentsSaving(true);
    setCommentError("");
    try {
      const payload = await apiFetch(`/assets/${asset.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      setComments((items) => [payload.comment, ...items]);
      setCommentText("");
    } catch (error) {
      setCommentError(error.message);
    } finally {
      setCommentsSaving(false);
    }
  };
  const saveComment = async (commentId) => {
    const content = editingCommentText.trim();
    if (!content) return;
    try {
      const payload = await apiFetch(`/comments/${commentId}`, {
        method: "PATCH",
        body: JSON.stringify({ content }),
      });
      setComments((items) => items.map((item) => item.id === commentId ? payload.comment : item));
      setEditingCommentId("");
      setEditingCommentText("");
    } catch (error) {
      setCommentError(error.message);
    }
  };
  const deleteComment = async (commentId) => {
    try {
      await apiFetch(`/comments/${commentId}`, { method: "DELETE" });
      setComments((items) => items.filter((item) => item.id !== commentId));
    } catch (error) {
      setCommentError(error.message);
    }
  };
  return (
    <div className="drawer-layer" onClick={onClose}>
      <aside
        className="detail-drawer"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-top">
          <div className="drawer-heading">
            {onBack && (
              <button className="drawer-back-button" onClick={onBack} aria-label="返回上一级素材" title="返回上一级素材">
                <ChevronDown size={16} />
              </button>
            )}
            <span>{onBack ? "原素材详情" : "素材详情"}</span>
          </div>
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
          {isCreation && (
            <div className="comments-section">
              <div className="comments-heading">
                <div className="note-label"><MessageSquare size={14} />创作反思</div>
                <span>{comments.length}</span>
              </div>
              <div className="comment-composer">
                <textarea
                  value={commentText}
                  onChange={(event) => setCommentText(event.target.value)}
                  rows="3"
                  placeholder="记录这次创作的反思、数据或下次要改进的地方"
                />
                <button className="comment-submit" onClick={submitComment} disabled={!commentText.trim() || commentsSaving}>
                  <Plus size={14} />
                  {commentsSaving ? "保存中" : "添加反思"}
                </button>
              </div>
              {commentsLoading ? (
                <div className="comments-muted">正在加载评论…</div>
              ) : comments.length ? (
                <div className="comments-list">
                  {comments.map((comment) => (
                    <div className="comment-item" key={comment.id}>
                      {editingCommentId === comment.id ? (
                        <>
                          <textarea value={editingCommentText} onChange={(event) => setEditingCommentText(event.target.value)} rows="3" />
                          <div className="comment-actions">
                            <button onClick={() => saveComment(comment.id)}>保存</button>
                            <button onClick={() => setEditingCommentId("")}>取消</button>
                          </div>
                        </>
                      ) : (
                        <>
                          <p>{comment.content}</p>
                          <div className="comment-meta">
                            <time>{new Date(comment.createdAt).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
                            <div className="comment-actions">
                              <button onClick={() => { setEditingCommentId(comment.id); setEditingCommentText(comment.content); }}>编辑</button>
                              <button onClick={() => deleteComment(comment.id)}>删除</button>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="comments-muted">还没有反思记录</div>
              )}
              {commentError && <div className="tag-error">{commentError}</div>}
            </div>
          )}
          {parentAssets.length > 0 && (
            <div className="detail-related">
              <div className="note-label"><Sparkles size={14} />关联参考素材</div>
              <div className="related-list">
                {parentAssets.map((parent) => (
                  <button
                    className="related-item"
                    key={parent.id}
                    onClick={() => onNavigate?.(parent)}
                    title="打开原素材"
                  >
                    <span>{parent.name}</span>
                    <ChevronRight size={14} />
                  </button>
                ))}
              </div>
            </div>
          )}
          {derivedAssets.length > 0 && (
            <div className="detail-related">
              <div className="note-label"><Play size={14} />关联创作作品</div>
              <div className="related-list">
                {derivedAssets.map((derived) => (
                  <button className="related-item" key={derived.id} onClick={() => onNavigate?.(derived)} title="打开关联作品">
                    <span>{derived.name}</span>
                    <ChevronRight size={14} />
                  </button>
                ))}
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
            {asset.folder === "角色设定" && (
              <div>
                <span>人物素材分类</span>
                <strong>
                  <FileImage size={14} />
                  {asset.characterCategory || "未分类"}
                </strong>
              </div>
            )}
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
            <a
              className="secondary-button"
              href={`${API_BASE}/assets/${asset.id}/download`}
              download
            >
              <Download size={16} />
              下载原文件
            </a>
            <button className="primary-button">
              <Copy size={16} />
              复制链接
            </button>
            <button
              className="danger-button"
              onClick={() => {
                if (window.confirm(`确定将“${asset.name}”移入回收站吗？`)) onDelete?.();
              }}
            >
              <Trash2 size={16} />
              移入回收站
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

function EditAssetModal({ asset, onClose, onSave, onReplace }) {
  const [name, setName] = useState(asset.name || "");
  const [source, setSource] = useState(asset.source || "");
  const [sourceUrl, setSourceUrl] = useState(asset.sourceUrl || "");
  const [characterName, setCharacterName] = useState(asset.characterName || "");
  const [characterCategory, setCharacterCategory] = useState(asset.characterCategory || "");
  const [tagsText, setTagsText] = useState((asset.tags || []).join(" "));
  const [folder, setFolder] = useState(asset.folder || "灵感收集");
  const [note, setNote] = useState(asset.note || "");
  const [used, setUsed] = useState(Boolean(asset.used));
  const [replacementFile, setReplacementFile] = useState(null);
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
    if (folder === "角色设定" && !characterCategory) {
      setError("请选择人物素材分类");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (replacementFile) await onReplace?.(replacementFile);
      await onSave({
        name: trimmedName,
        source: source.trim() || "本地导入",
        sourceUrl: sourceUrl.trim(),
        characterName: characterName.trim(),
        characterCategory,
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
            <p className="modal-subtitle">修改文件、标题、来源和创作信息，保存后会同步到云端。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            <X size={19} />
          </button>
        </div>
        <div className="edit-form-grid">
          <label className="edit-field-wide replace-file-field">
            {asset.type === "video" ? "替换视频文件" : "替换图片文件"}
            <input
              type="file"
              hidden
              accept={asset.type === "video" ? "video/*" : "image/*"}
              onChange={(event) => setReplacementFile(event.target.files?.[0] || null)}
            />
            <span className="replace-file-control">
              <Upload size={15} />
              {replacementFile ? replacementFile.name : "选择新的文件"}
            </span>
            <small>只支持替换为同类型文件；视频会重新生成封面和时长，原有信息和关联不变。</small>
          </label>
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
              <option>我的创作</option>
              <option>角色设定</option>
              <option>项目资料</option>
            </select>
          </label>
          <label>
            人物名称
            <input value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="例如 林小栀" />
            <small>选择“角色设定”时必填，同名会聚合到同一人物相册</small>
          </label>
          <label>
            人物素材分类
            <select value={characterCategory} onChange={(event) => setCharacterCategory(event.target.value)}>
              <option value="">请选择分类</option>
              {CHARACTER_CATEGORIES.map((category) => <option key={category}>{category}</option>)}
            </select>
            <small>例如正脸、场景照、动作或服装</small>
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

function UploadModal({ onClose, onSubmit, fileInput, assets, uploading, defaultFolder, defaultCharacterName, characterAlbums }) {
  const [files, setFiles] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [name, setName] = useState("");
  const [source, setSource] = useState("本地导入");
  const [sourceUrl, setSourceUrl] = useState("");
  const [characterName, setCharacterName] = useState(defaultCharacterName || "");
  const [albumChoice, setAlbumChoice] = useState(defaultCharacterName || "");
  const [characterCategory, setCharacterCategory] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [used, setUsed] = useState(false);
  const [folder, setFolder] = useState(defaultFolder || "灵感收集");
  const [parentAssetIds, setParentAssetIds] = useState([]);
  const [parentPickerOpen, setParentPickerOpen] = useState(false);
  const [formError, setFormError] = useState("");
  useEffect(() => {
    if (defaultCharacterName) {
      setCharacterName(defaultCharacterName);
      setAlbumChoice(defaultCharacterName);
    }
  }, [defaultCharacterName]);
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
    if (folder === "角色设定" && !characterCategory) {
      setFormError("请选择人物素材分类");
      return;
    }
    setFormError("");
    const tags = tagsText
      .split(/[,，\s]+/)
      .map((tag) => tag.trim())
      .filter(Boolean);
    onSubmit(files, {
          name: name.trim(),
          source: source.trim() || "本地导入",
          sourceUrl: sourceUrl.trim(),
          characterName: characterName.trim(),
          characterCategory,
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
            素材名称
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={files.length > 1 ? "例如 林小栀角色参考（会自动加文件名）" : "例如 便利店转场参考"}
            />
            <small>{files.length > 1 ? "多文件上传时会以此作为名称前缀，并保留原文件名" : "不填写则使用原文件名"}</small>
          </label>
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
            <div className="character-upload-fields">
              <label>
                人物相册
                <select
                  value={albumChoice}
                  onChange={(event) => {
                    const value = event.target.value;
                    setAlbumChoice(value);
                    setCharacterName(value === "__new__" ? "" : value);
                    setFormError("");
                  }}
                >
                  <option value="">请选择人物相册</option>
                  {characterAlbums.map((album) => <option value={album.name} key={album.id}>{album.name}</option>)}
                  <option value="__new__">+ 新建人物相册</option>
                </select>
                {(albumChoice === "__new__" || !characterAlbums.length) && (
                  <input
                    value={characterName}
                    onChange={(event) => {
                      setCharacterName(event.target.value);
                      setFormError("");
                    }}
                    placeholder="输入新人物名称，例如 林小栀"
                  />
                )}
                <small>先创建人物相册，再在相册中持续上传正脸、场景照等素材</small>
              </label>
              <label>
                人物素材分类
                <select value={characterCategory} onChange={(event) => { setCharacterCategory(event.target.value); setFormError(""); }}>
                  <option value="">请选择分类</option>
                  {CHARACTER_CATEGORIES.map((category) => <option key={category}>{category}</option>)}
                </select>
                <small>例如正脸、场景照、动作或服装</small>
              </label>
            </div>
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
                <option>我的创作</option>
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

function CreateCharacterAlbumModal({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    const value = name.trim();
    if (!value) {
      setError("请输入人物相册名称");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onCreate(value);
      onClose();
    } catch (createError) {
      setError(createError.message || "创建失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="modal-layer edit-layer" onClick={onClose}>
      <form className="edit-modal album-create-modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <div>
            <p className="eyebrow">NEW CHARACTER ALBUM</p>
            <h2>新建人物相册</h2>
            <p className="modal-subtitle">创建后进入相册，再上传这个人物的正脸、场景照和动作素材。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        </div>
        <div className="edit-form-grid album-create-form">
          <label className="edit-field-wide">
            相册名称
            <input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 林小栀" />
          </label>
        </div>
        {error && <div className="auth-error edit-error">{error}</div>}
        <div className="modal-foot">
          <button type="button" className="text-button" onClick={onClose}>取消</button>
          <button type="submit" className="primary-button" disabled={saving}><Plus size={16} />{saving ? "创建中…" : "创建并进入"}</button>
        </div>
      </form>
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

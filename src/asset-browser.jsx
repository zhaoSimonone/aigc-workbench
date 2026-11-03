import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronDown, Search } from "lucide-react";

const WINDOW_SIZE = 180;

export function useDebounced(value, delay = 250) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

export function useAssetPage(apiFetch, normalize, filters, revision = 0, enabled = true) {
  const key = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== undefined && v !== "")).toString();
  const [state, setState] = useState({ key: "", assets: [], albums: [], offset: 0, total: 0, hasMore: false });
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const controller = useRef(null);
  const busy = useRef(false);
  const nextOffset = useRef(0);
  const [retry, setRetry] = useState(0);
  const request = async (append, current, signal, offset = 0, limit = 60, navigation = false) => {
    busy.current = true;
    setError("");
    append || navigation ? setLoadingMore(true) : setLoading(true);
    try {
      const payload = await apiFetch(`/asset-browser?${key}&limit=${limit}&offset=${offset}`, { signal });
      if (current !== generation.current || signal.aborted) return;
      nextOffset.current = payload.offset + (payload.assets || payload.albums || []).length;
      setState((previous) => {
        const merge = (oldRows, rows, id) => [...new Map([...oldRows, ...rows].map((row) => [row[id], row])).values()].slice(0, WINDOW_SIZE);
        return {
          key, offset: append ? previous.offset : payload.offset,
          assets: merge(append ? previous.assets : [], (payload.assets || []).map(normalize), "id"),
          albums: merge(append ? previous.albums : [], payload.albums || [], "name"),
          total: payload.total, hasMore: payload.hasMore,
        };
      });
    } catch (failure) {
      if (current === generation.current && !signal.aborted) setError(failure.message);
    } finally {
      if (current === generation.current && !signal.aborted) {
        busy.current = false;
        setLoading(false);
        setLoadingMore(false);
      }
    }
  };
  useEffect(() => {
    const current = ++generation.current;
    controller.current?.abort();
    controller.current = new AbortController();
    nextOffset.current = 0;
    setState({ key, assets: [], albums: [], offset: 0, total: 0, hasMore: false });
    setLoadingMore(false);
    setError("");
    busy.current = false;
    if (enabled) request(false, current, controller.current.signal);
    else setLoading(false);
    return () => controller.current?.abort();
  }, [key, revision, enabled, retry]);
  // Use consumed offsets rather than deduplicated length so concurrent inserts
  // cannot grow the window or shift the boundary used by "previous batch".
  const windowFull = nextOffset.current - state.offset >= WINDOW_SIZE;
  return {
    ...state,
    assets: state.key === key ? state.assets : [],
    albums: state.key === key ? state.albums : [],
    loading: enabled && (loading || state.key !== key),
    loadingMore, error, windowFull, endOffset: nextOffset.current,
    reload: () => setRetry((n) => n + 1),
    setAssets: (update) => setState((s) => ({ ...s, assets: (typeof update === "function" ? update(s.assets) : update).slice(0, WINDOW_SIZE) })),
    loadMore: () => {
      const limit = windowFull ? 60 : Math.min(60, WINDOW_SIZE - (nextOffset.current - state.offset));
      if (!busy.current && state.key === key && state.hasMore) request(!windowFull, generation.current, controller.current.signal, nextOffset.current, limit, true);
    },
    previousPage: () => {
      if (!busy.current && state.key === key && state.offset > 0) request(false, generation.current, controller.current.signal, Math.max(0, state.offset - WINDOW_SIZE), WINDOW_SIZE, true);
    },
  };
}

export function AssetPageControls({ page, noun = "素材", scrollTarget }) {
  const ref = useRef(null);
  const previousOffset = useRef(page.offset);
  useEffect(() => {
    if (previousOffset.current !== page.offset && scrollTarget) {
      ref.current?.parentElement?.querySelector(scrollTarget)?.scrollIntoView({ block: "start" });
    }
    previousOffset.current = page.offset;
  }, [page.offset, scrollTarget]);
  return <div className="asset-load-more" ref={ref}>
    {page.offset > 0 && <button type="button" className="secondary-button" disabled={page.loading || page.loadingMore} onClick={page.previousPage}><ArrowLeft size={15} />上一批{noun}</button>}
    {(page.offset > 0 || page.windowFull) && <span className="asset-page-range">{page.offset + 1}–{page.endOffset} / {page.total}</span>}
    {page.hasMore && <button type="button" className="secondary-button" disabled={page.loading || page.loadingMore} onClick={page.loadMore}>
      {page.windowFull ? <ArrowRight size={15} /> : <ChevronDown size={15} />}
      {page.loading || page.loadingMore ? "加载中…" : page.windowFull ? `下一批${noun}` : `加载更多${noun}`}
    </button>}
  </div>;
}

export function useAssetsById(apiFetch, ids) {
  const key = [...new Set(ids.filter(Boolean))].sort().join(",");
  const [state, setState] = useState({ key: "", rows: [], error: "" });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    if (!key) { setState({ key, rows: [], error: "" }); return; }
    const wanted = key.split(",");
    const chunks = [];
    for (let i = 0; i < wanted.length; i += 200) chunks.push(wanted.slice(i, i + 200));
    // Two workers bound request pressure even for thousands of selected links.
    const pages = new Array(chunks.length);
    let next = 0;
    const worker = async () => {
      while (next < chunks.length && !controller.signal.aborted) {
        const index = next++;
        pages[index] = await apiFetch(`/asset-browser?ids=${chunks[index].join(",")}&limit=200`, { signal: controller.signal });
      }
    };
    Promise.all(Array.from({ length: Math.min(2, chunks.length) }, worker))
      .then(() => pages)
      .then((pages) => {
        if (!controller.signal.aborted) setState({ key, rows: pages.flatMap((p) => p.assets), error: "" });
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setState({ key, rows: [], error: error.message });
          controller.abort();
        }
      });
    return () => controller.abort();
  }, [key, retry]);
  return { ...(state.key === key ? state : { rows: [], error: "" }), reload: () => setRetry((n) => n + 1) };
}

const identity = (value) => value;

export function RemoteAssetPicker({ apiFetch, value = [], onChange, multiple = true, type, exclude, label = "关联参考素材" }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const q = useDebounced(query);
  const page = useAssetPage(apiFetch, identity, { q, type, exclude }, 0, open);
  const selected = useAssetsById(apiFetch, value);
  const byId = new Map([...selected.rows, ...page.assets].map((row) => [row.id, row]));
  const caption = value.length ? `${byId.get(value[0])?.name || "已选素材"}${value.length > 1 ? ` 等 ${value.length} 个` : ""}` : "不关联素材";
  const toggle = (id) => {
    onChange(value.includes(id) ? value.filter((v) => v !== id) : multiple ? [...value, id] : [id]);
    if (!multiple) setOpen(false);
  };
  return (
    <div className="reference-picker">
      <button type="button" className={`reference-picker-trigger ${value.length ? "has-selection" : ""}`} aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{caption}</span><ChevronDown size={15} />
      </button>
      {open && <div className="reference-picker-menu">
        <div className="reference-picker-head">
          <span>{value.length ? `已选 ${value.length} 个` : label}</span>
          {value.length > 0 && <button type="button" onClick={() => onChange([])}>清除选择</button>}
        </div>
        <div className="asset-picker-search"><Search size={14} /><input aria-label={`搜索${label}`} placeholder="搜索素材" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
        {value.length > 0 && <div className="asset-picker-selected">
          {value.map((id) => <button type="button" key={id} onClick={() => toggle(id)} title="取消选择">{byId.get(id)?.name || "已选素材"} ×</button>)}
        </div>}
        {(page.error || selected.error) && <div role="alert" className="auth-error">{page.error || selected.error}<button type="button" onClick={() => { page.reload(); selected.reload(); }}>重试</button></div>}
        {page.loading ? <div className="reference-empty">正在加载素材</div> : page.assets.length ? page.assets.map((row) => (
          <button type="button" key={row.id} className={`reference-option ${value.includes(row.id) ? "selected" : ""}`} onClick={() => toggle(row.id)}>
            <span className="reference-option-check">{value.includes(row.id) && <Check size={13} />}</span><span>{row.name}</span>
          </button>
        )) : !page.error && <div className="reference-empty">没有匹配素材</div>}
        <AssetPageControls page={page} />
      </div>}
    </div>
  );
}

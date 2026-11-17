/* Radar de Marketing — API estática (GitHub Pages).
   Responde no navegador às mesmas rotas /api/... do servidor local, a partir de data/radar.json,
   gerado a cada coleta pelo GitHub Actions. Busca, filtros e painel funcionam sem servidor. */
(() => {
  "use strict";
  const D = { loaded: null, generated: null, meta: null, status: null, all: [], byId: new Map(), fontes: null };
  const FIELDS = ["titulo", "trecho", "resumo", "veiculo"];
  const HOUR = 3600e3;
  const DAY = 24 * HOUR;
  const PERIODS = { "1h": HOUR, "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY, "90d": 90 * DAY, "1a": 365 * DAY };

  // -------------------------------------------------------------- carga dos dados
  async function getJSON(path) {
    const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Não foi possível ler ${path} (erro ${response.status})`);
    return response.json();
  }

  const strip = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  const tokens = (text) => strip(text).match(/[\p{L}\p{N}]+/gu) || [];
  const dateOf = (a) => a.publicado_em || a.coletado_em;
  const isoZ = (ms) => new Date(ms).toISOString().replace(/\.\d+Z$/, "Z");

  async function load() {
    const data = await getJSON("data/radar.json");
    D.generated = data.generated_at;
    D.meta = data.meta;
    D.status = data.status;
    D.all = data.articles;
    D.byId = new Map(D.all.map((a) => [a.id, a]));
    D.fontes = null;
    // representante de cada grupo de duplicadas (mesma regra do servidor): oficial, link original,
    // com imagem da fonte, relevância e, por fim, a mais antiga no banco
    const direct = (a) => Number(a.status !== "link via agregador");
    const withImage = (a) => Number(Boolean(a.imagem_url));
    const best = new Map();
    D.all.forEach((a) => {
      a._tokens = FIELDS.map((field) => tokens(a[field]));
      if (!a.grupo_id) return;
      const cur = best.get(a.grupo_id);
      const better = !cur || (a.oficial - cur.oficial || direct(a) - direct(cur) || withImage(a) - withImage(cur)
        || a.relevancia - cur.relevancia || cur.id - a.id) > 0;
      if (better) best.set(a.grupo_id, a);
    });
    D.all.forEach((a) => { a._rep = !a.grupo_id || best.get(a.grupo_id) === a; });
  }
  const ready = () => (D.loaded ||= load().catch((error) => { D.loaded = null; throw error; }));

  // -------------------------------------------- busca (equivalente ao FTS5 do servidor)
  // Mesma regra de app/textutil.py (fts_query): até 12 palavras, todas obrigatórias, por prefixo.
  function queryTerms(q) {
    const words = String(q || "").match(/[\p{L}\p{N}_][\p{L}\p{N}_\-.]*/gu) || [];
    return words.slice(0, 12).map((word) => tokens(word)).filter((seq) => seq.length);
  }
  function hasSequence(toks, seq) {
    const n = seq.length;
    for (let i = 0; i + n <= toks.length; i++) {
      let ok = true;
      for (let j = 0; j < n; j++) {
        if (j === n - 1 ? !toks[i + j].startsWith(seq[j]) : toks[i + j] !== seq[j]) { ok = false; break; }
      }
      if (ok) return true;
    }
    return false;
  }
  const matches = (a, terms) => terms.every((seq) => a._tokens.some((toks) => hasSequence(toks, seq)));

  // ------------------------------------------------------------------ consultas
  const clean = (a) => { const { _tokens, _rep, ...rest } = a; return rest; };
  const recentFirst = (a, b) => dateOf(b).localeCompare(dateOf(a)) || b.id - a.id;
  const relevantFirst = (a, b) => b.relevancia - a.relevancia || dateOf(b).localeCompare(dateOf(a));
  function localDayStart() {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    return isoZ(start.getTime());
  }

  function search(p) {
    const q = (p.q || "").trim();
    let pool = D.all;
    if (q) {
      const terms = queryTerms(q);
      if (!terms.length) return { total: 0, items: [] };
      pool = pool.filter((a) => matches(a, terms));
    }
    if (p.category) pool = pool.filter((a) => a.categorias.includes(p.category));
    if (p.source) pool = pool.filter((a) => a.source_id === Number(p.source));
    if (p.period === "hoje") { const start = localDayStart(); pool = pool.filter((a) => dateOf(a) >= start); }
    else if (PERIODS[p.period]) { const start = isoZ(Date.now() - PERIODS[p.period]); pool = pool.filter((a) => dateOf(a) >= start); }
    if (p.from) pool = pool.filter((a) => dateOf(a) >= `${p.from}T00:00:00Z`);
    if (p.to) pool = pool.filter((a) => dateOf(a) <= `${p.to}T23:59:59Z`);
    if (Number(p.min_relevance)) pool = pool.filter((a) => a.relevancia >= Number(p.min_relevance));
    if (p.tipo) pool = pool.filter((a) => a.tipo === p.tipo);
    if (p.sinal) pool = pool.filter((a) => a.sinal === p.sinal);
    if (p.collapse !== "false" && !q) pool = pool.filter((a) => a._rep);
    const items = [...pool].sort(p.sort === "relevant" ? relevantFirst : recentFirst);
    const offset = Number(p.offset || 0);
    const limit = Math.min(Number(p.limit || 30), 100);
    return { total: items.length, items: items.slice(offset, offset + limit).map(clean) };
  }

  function status() {
    const health = { ...D.status, coletando: false, estatico: true, gerado_em: D.generated };
    if (Date.now() - new Date(D.generated).getTime() > 6 * HOUR) {
      health.nivel = "vermelho";
      health.mensagem = "Sem coleta nova há mais de 6 horas";
    }
    return health;
  }

  function dashboard() {
    const week = isoZ(Date.now() - 7 * DAY);
    const today = localDayStart();
    const recent = D.all.filter((a) => dateOf(a) >= week);
    const perCategory = {};
    recent.forEach((a) => a.categorias.forEach((slug) => { perCategory[slug] = (perCategory[slug] || 0) + 1; }));
    const reps = recent.filter((a) => a._rep);
    const band = (filter, sorter, limit) => reps.filter(filter).sort(sorter).slice(0, limit).map(clean);
    return {
      total: D.all.length,
      publicadas_hoje: D.all.filter((a) => a.publicado_em && a.publicado_em >= today).length,
      por_categoria_7d: perCategory,
      saude: status(),
      faixas: {
        mais_relevantes: band(() => true, relevantFirst, 8),
        oficiais: band((a) => a.oficial, recentFirst, 4),
        plataformas: band((a) => a.sinal === "atualizacao_plataforma", recentFirst, 4),
        regulacao: band((a) => a.sinal === "regulacao", recentFirst, 4),
      },
    };
  }

  function article(id) {
    const found = D.byId.get(Number(id));
    if (!found) throw new Error("Notícia não encontrada");
    const related = found.grupo_id
      ? D.all.filter((a) => a.grupo_id === found.grupo_id && a.id !== found.id).sort((a, b) => dateOf(a).localeCompare(dateOf(b))).map(clean)
      : [];
    return { ...clean(found), relacionadas: related };
  }

  const fontes = async () => (D.fontes ||= await getJSON("data/fontes.json"));

  // verifica se o GitHub publicou uma coleta mais nova e, se sim, recarrega os dados
  async function checkNew() {
    const latest = await getJSON("data/status.json");
    if (latest.generated_at && latest.generated_at !== D.generated) { D.loaded = null; await ready(); return true; }
    return false;
  }

  // -------------------------------------------------------------------- roteador
  window.RADAR_API = async (path, options = {}) => {
    const url = new URL(path, location.href);
    const params = Object.fromEntries(url.searchParams.entries());
    const route = url.pathname.replace(/^.*\/api\//, "");
    const method = (options.method || "GET").toUpperCase();
    await ready();
    if (method === "POST" && route === "refresh") {
      const changed = await checkNew().catch(() => false);
      return {
        status: "estatico", recarregado: changed,
        mensagem: changed ? "Notícias atualizadas com a coleta mais recente."
          : `Você já está vendo a coleta mais recente (${new Date(D.generated).toLocaleString("pt-BR")}). A coleta roda sozinha a cada hora.`,
      };
    }
    if (method !== "GET") throw new Error("Na versão publicada, as fontes são alteradas no arquivo config/sources.json do GitHub.");
    if (route === "meta") return { ...D.meta };
    if (route === "status") { const changed = await checkNew().catch(() => false); return { ...status(), dados_novos: changed }; }
    if (route === "dashboard") return dashboard();
    if (route === "articles") return search(params);
    const match = route.match(/^articles\/(\d+)$/);
    if (match) return article(match[1]);
    if (route === "sources") return (await fontes()).sources;
    if (route === "runs") return (await fontes()).runs.slice(0, Number(params.limit || 50));
    throw new Error("Rota não disponível na versão publicada");
  };
})();

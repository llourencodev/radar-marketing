"use strict";

/* Radar de Marketing — interface sem build, no mesmo padrão visual do Radar
   Inteligente de Notícias. Todo texto vindo das fontes é inserido como texto
   (nunca como HTML); só os ícones, que são constantes deste arquivo, usam innerHTML. */

// -------------------------------------------------------------------- ícones
const P = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICONS = {
  home: P('<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>'),
  target: P('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>'),
  megaphone: P('<path d="M3 11v2a1 1 0 001 1h3l8 4V6L7 10H4a1 1 0 00-1 1z"/><path d="M18 9a4 4 0 010 6M8 14v4a1 1 0 001 1h1.5"/>'),
  users: P('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0M16 4.6a3.5 3.5 0 010 6.8M18 14.3a6.5 6.5 0 013.5 5.7"/>'),
  cart: P('<path d="M3 4h2.5l2.2 10.5h10.6L20.5 7H6.6"/><circle cx="9.5" cy="18.5" r="1.5"/><circle cx="17" cy="18.5" r="1.5"/>'),
  sparkles: P('<path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
  bolt: P('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>'),
  scale: P('<path d="M12 3v18M5 21h14M6 7h12"/><path d="M6 7l-3 7a3 3 0 006 0zM18 7l-3 7a3 3 0 006 0z"/>'),
  layers: P('<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>'),
  radio: P('<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 010 8.4M7.8 16.2a6 6 0 010-8.4M19 5a10 10 0 010 14M5 19A10 10 0 015 5"/>'),
  search: P('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
  refresh: P('<path d="M20 11a8 8 0 10-2.3 5.7"/><path d="M20 4v7h-7"/>'),
  theme: P('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  menu: P('<path d="M4 6h16M4 12h16M4 18h16"/>'),
  external: P('<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/>'),
  close: P('<path d="M6 6l12 12M18 6L6 18"/>'),
};
const CAT_ICONS = { performance: "target", seo: "search", social: "users", branding: "megaphone", martech: "sparkles", mercado: "cart" };
function icon(name) {
  const el = document.createElement("span");
  el.className = "ico";
  el.innerHTML = ICONS[name] || ICONS.layers;
  return el;
}

// ---------------------------------------------------------------- utilidades
const PAGE = 30;
const ALL_COLOR = "#0b5cad";
const emptyFilters = () => ({ period: "", category: "", source: "", from: "", to: "", min_relevance: "", oficial: false, sinal: "", sort: "recent" });
const state = {
  meta: null,
  cats: {},
  view: { key: "dashboard", type: "dashboard", category: "", q: "" },
  filters: emptyFilters(),
  offset: 0,
  sources: [],
  counts: null,
  total: null,
  collecting: false,
};

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// versão publicada (GitHub Pages): dados em JSON, sem servidor (ver static-api.js)
const STATIC = typeof window.RADAR_API === "function";

async function api(path, options) {
  if (STATIC) return window.RADAR_API(path, options);
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Erro ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join("; ");
    } catch (_) { /* resposta sem JSON */ }
    throw new Error(detail);
  }
  return response.json();
}
function fill(el, ...children) {
  // como replaceChildren, mas aceita listas aninhadas e ignora null/false (mesma regra de h)
  el.replaceChildren(...h("div", {}, ...children).childNodes);
}
const jsonBody = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

const $ = (id) => document.getElementById(id);
const safeUrl = (url) => (typeof url === "string" && /^https?:\/\//i.test(url) ? url : null);
const num = (n) => Number(n || 0).toLocaleString("pt-BR");
const catOf = (slug) => {
  const cat = state.cats[slug];
  return cat ? { nome: cat.nome, cor: cat.cor, icone: CAT_ICONS[slug] || "layers" } : { nome: slug || "Notícia", cor: "#64748b", icone: "layers" };
};

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3500);
}

const dateFmt = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
function fmtDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateFmt.format(date);
}
function relative(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!(seconds >= 0)) return "";
  if (seconds < 60) return "agora";
  if (seconds < 3600) return `há ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `há ${Math.floor(seconds / 3600)} h`;
  const days = Math.floor(seconds / 86400);
  return days === 1 ? "ontem" : `há ${days} dias`;
}

// --------------------------------------------------------------- componentes
function noImage(article, mini, note) {
  const cat = catOf(article.categorias[0]);
  return h("div", { class: "noimg", style: `--c:${cat.cor}` }, icon(cat.icone),
    mini ? null : [h("span", { class: "outlet" }, article.veiculo), h("span", { class: "note" }, note || "Sem imagem da fonte")]);
}

function imageInto(box, article, mini) {
  const src = safeUrl(article.imagem_url);
  if (!src) { box.append(noImage(article, mini)); return box; }
  const img = h("img", { src, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
  img.addEventListener("error", () => img.replaceWith(noImage(article, mini, "Imagem da fonte indisponível")), { once: true });
  box.append(img);
  return box;
}

function badges(article, full) {
  const list = [];
  article.categorias.slice(0, full ? undefined : 2).forEach((slug) => {
    const cat = catOf(slug);
    list.push(h("span", { class: "badge cat", style: `--c:${cat.cor}` }, cat.nome));
  });
  list.push(article.tipo === "oficial"
    ? h("span", { class: "badge official", title: "Publicado pela própria plataforma, empresa ou órgão" }, "Publicação oficial")
    : h("span", { class: "badge journal", title: "Veículo de imprensa ou blog especializado" }, "Notícia jornalística"));
  if (article.sinal_rotulo) {
    const reported = !article.sinal_confirmado;
    list.push(h("span", {
      class: `badge stage ${article.sinal}${reported ? " relatada" : ""}`,
      title: reported ? "Relatada pela imprensa; ainda não confirmada por fonte oficial da plataforma" : "Assunto classificado automaticamente",
    }, article.sinal_rotulo + (reported ? " (relatada)" : "")));
  }
  if (article.natureza === "opiniao") list.push(h("span", { class: "badge opinion" }, "Opinião"));
  if (article.status === "link via agregador") {
    list.push(h("span", { class: "badge warn", title: "O endereço original não pôde ser confirmado; o link abre pelo agregador de notícias" }, "via agregador"));
  }
  if (article.n_fontes > 1) list.push(h("span", { class: "badge", title: "A mesma notícia foi publicada por outras fontes" }, `${article.n_fontes} fontes`));
  return h("div", { class: "badges" }, list);
}

function metaLine(article, extra) {
  const parts = [h("span", { class: "outlet" }, article.veiculo)];
  if (article.data_confirmada && article.publicado_em) {
    parts.push(h("time", { datetime: article.publicado_em, title: "Data de publicação" }, fmtDate(article.publicado_em)),
      h("span", {}, `· ${relative(article.publicado_em)}`));
  } else {
    parts.push(h("span", { class: "nodate", title: "A fonte não informou a data de publicação" }, "Data não confirmada"));
  }
  if (article.idioma === "en") parts.push(h("span", {}, "· em inglês"));
  if (extra) parts.push(h("span", {}, extra));
  return h("div", { class: "meta" }, parts);
}

function titleLink(article) {
  const url = safeUrl(article.url);
  return url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, article.titulo) : article.titulo;
}

function card(article) {
  const url = safeUrl(article.url);
  return h("article", { class: "card", style: `--c:${catOf(article.categorias[0]).cor}` },
    imageInto(h("div", { class: "thumb" }), article),
    h("div", { class: "card-body" },
      badges(article),
      h("h3", {}, titleLink(article)),
      metaLine(article),
      article.resumo ? h("p", { class: "summary", title: article.resumo_por_ia ? "Resumo gerado por IA (interpretação)" : "Trecho da própria fonte" }, article.resumo) : null),
    h("div", { class: "card-foot" },
      h("button", { class: "linkbtn", type: "button", onclick: () => openDetail(article.id) }, "Detalhes e impactos"),
      url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, "Abrir original ", icon("external")) : null));
}

function listItem(article) {
  return h("div", { class: "list-item" },
    imageInto(h("div", { class: "mini" }), article, true),
    h("div", {},
      h("h4", {}, titleLink(article)),
      metaLine(article),
      badges(article),
      h("button", { class: "linkbtn", type: "button", onclick: () => openDetail(article.id) }, "Detalhes")));
}

// ------------------------------------------------------------------- detalhe
async function openDetail(id) {
  let article;
  try { article = await api(`/api/articles/${id}`); } catch (error) { toast(error.message); return; }
  const dialog = $("detail");
  const url = safeUrl(article.url);
  const ai = Boolean(article.resumo_por_ia);
  const head = (title, tag) => h("h4", {}, title, tag ? h("span", { class: "tag" }, tag) : null);
  const bullets = (items) => h("ul", {}, items.map((text) => h("li", {}, text)));
  fill($("detail-body"), 
    h("div", { class: "hero" },
      h("button", { class: "icon-btn close", type: "button", "aria-label": "Fechar", onclick: () => dialog.close() }, icon("close")),
      imageInto(h("div", { style: "position:absolute;inset:0" }), article)),
    h("div", { class: "inner" },
      badges(article, true),
      h("h2", {}, article.titulo),
      metaLine(article, `· coletada em ${fmtDate(article.coletado_em)}`),
      article.sinal_rotulo && !article.sinal_confirmado && article.sinal === "atualizacao_plataforma"
        ? h("div", { class: "notice" }, "Atualização relatada pela imprensa. Confirme na publicação oficial da plataforma antes de mudar campanhas ou processos.") : null,
      ai && article.resumo ? [head("Resumo", `Resumo gerado por IA (${article.provedor})`), h("p", {}, article.resumo)] : null,
      article.pontos && article.pontos.length ? [head("Principais pontos", ai ? "IA" : "frases da fonte"), bullets(article.pontos)] : null,
      article.impactos && article.impactos.length ? [head("Possíveis impactos para o marketing", `interpretação · ${ai ? `IA (${article.provedor})` : "regras automáticas"}`), bullets(article.impactos)] : null,
      article.trecho ? [head("Trecho original da fonte", article.idioma === "en" ? "em inglês" : "fato relatado"), h("p", {}, article.trecho)] : null,
      article.idioma === "en" && !ai ? h("p", { class: "hint" }, "Texto da fonte em inglês. A tradução e o resumo em português ficam disponíveis ao ligar a IA (veja o arquivo .env).") : null,
      article.termos.length ? [head("Termos técnicos"),
        h("dl", { class: "terms" }, article.termos.map((t) => [h("dt", {}, t.termo), h("dd", {}, t.explicacao)]))] : null,
      article.relacionadas.length ? [head("Mesma notícia em outras fontes"),
        h("ul", {}, article.relacionadas.map((r) => h("li", {},
          safeUrl(r.url) ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.titulo) : r.titulo,
          ` — ${r.veiculo}, ${r.data_confirmada ? fmtDate(r.publicado_em) : "data não confirmada"}`)))] : null,
      head("Natureza do conteúdo"),
      h("p", { class: "hint" }, `${article.natureza === "opiniao" ? "Opinião/coluna: reflete a visão do autor." : "Fato relatado pela fonte."} Resumo, pontos e impactos são interpretações automáticas e não substituem a leitura do original.`),
      url ? h("p", { class: "actions" }, h("a", { class: "btn btn-primary", href: url, target: "_blank", rel: "noopener noreferrer" }, "Abrir publicação original ", icon("external"))) : null,
      h("p", { class: "hint", style: "margin-top:10px" }, `Fonte: ${article.fonte} · status: ${article.status} · relevância ${article.relevancia}`)));
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
}

// ----------------------------------------------------------------- navegação
const HIGHLIGHTS = [
  { key: "h/plataformas", label: "Atualizações de plataformas", icon: "bolt", color: "#a78bfa", sinal: "atualizacao_plataforma",
    sub: "Mudanças em Google, Meta, TikTok e outras plataformas. Só conta como confirmada quando vem da própria plataforma; vinda da imprensa aparece como “(relatada)”." },
  { key: "h/regulacao", label: "Regulação e privacidade", icon: "scale", color: "#f59e0b", sinal: "regulacao",
    sub: "LGPD, ANPD, CONAR e regras de publicidade. Publicações oficiais têm prioridade." },
];

function navItem(label, iconName, view, opts = {}) {
  return h("button", {
    class: "nav-item" + (state.view.key === view.key ? " active" : ""), type: "button",
    style: opts.color ? `--c:${opts.color}` : null, onclick: () => go(view),
  }, icon(iconName), h("span", {}, label), opts.count !== undefined && opts.count !== null ? h("span", { class: "count" }, opts.count) : null);
}

function renderNav() {
  const counts = state.counts || {};
  fill($("nav"), 
    navItem("Painel", "home", { key: "dashboard", type: "dashboard" }),
    h("div", { class: "nav-label" }, "Categorias"),
    state.meta.categorias.map((cat) => navItem(cat.nome, CAT_ICONS[cat.slug] || "layers",
      { key: `c/${cat.slug}`, type: "list", category: cat.slug }, { color: cat.cor, count: counts[cat.slug] ?? "" })),
    h("div", { class: "nav-label" }, "Destaques"),
    HIGHLIGHTS.map((item) => navItem(item.label, item.icon, { key: item.key, type: "list", sinal: item.sinal }, { color: item.color })),
    navItem("Todas as notícias", "layers", { key: "todas", type: "list" }, { count: state.total === null ? "" : num(state.total) }));
  fill($("nav-foot"), navItem("Fontes e coleta", "radio", { key: "fontes", type: "sources" }));
}

function closeMenu() {
  $("sidebar").classList.remove("open");
  $("scrim").classList.remove("show");
}

function go(view) {
  state.view = { key: view.key, type: view.type, category: view.category || "", q: view.q || "" };
  state.filters = emptyFilters();
  if (view.sinal) state.filters.sinal = view.sinal;
  if (view.filters) Object.assign(state.filters, view.filters);
  state.offset = 0;
  $("search-input").value = state.view.q;
  closeMenu();
  render();
  window.scrollTo(0, 0);
}

async function render() {
  renderNav();
  const view = $("view");
  try {
    if (state.view.type === "dashboard") await renderDashboard(view);
    else if (state.view.type === "sources") await renderSources(view);
    else await renderList(view);
  } catch (error) {
    fill(view, h("div", { class: "empty" }, `Não foi possível carregar: ${error.message}`));
  }
}

// -------------------------------------------------------------------- painel
async function renderDashboard(view) {
  const data = await api("/api/dashboard");
  state.counts = data.por_categoria_7d;
  state.total = data.total;
  renderNav();
  const health = data.saude;
  const kpi = (label, value, sub, small) => h("div", { class: "kpi" },
    h("div", { class: "label" }, label), h("div", { class: "value" + (small ? " small" : "") }, value), h("div", { class: "sub" }, sub));
  const section = (title, items, target, mode, empty) => h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, title),
      target ? h("button", { type: "button", onclick: () => go(target) }, "Ver tudo →") : null),
    !items.length ? h("div", { class: "empty" }, empty)
      : mode === "list" ? h("div", { class: "list" }, items.map(listItem))
        : h("div", { class: "grid" + (mode === "lead" ? " lead" : "") }, items.map(card)));
  const none = data.total ? "Nada nos últimos 7 dias." : "Nenhuma notícia ainda. A primeira coleta pode levar alguns minutos.";

  fill(view, 
    h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "eyebrow" }, new Intl.DateTimeFormat("pt-BR", { dateStyle: "full" }).format(new Date())),
      h("h1", {}, "Painel de inteligência"),
      h("p", {}, "Notícias reais de marketing coletadas de fontes oficiais, imprensa e buscadores, organizadas por assunto."))),
    h("div", { class: "kpis" },
      kpi("Notícias coletadas", num(data.total), "todas as fontes"),
      kpi("Publicadas hoje", num(data.publicadas_hoje), "pela data de publicação da fonte"),
      kpi("Última atualização", health.ultima_atualizacao ? fmtDate(health.ultima_atualizacao) : "—",
        health.ultima_atualizacao ? `a cada ${state.meta.intervalo_min} min` : "aguardando a primeira coleta", true),
      kpi("Situação da coleta",
        [h("span", { class: "dot " + dotClass(health) }), health.coletando ? "Coletando…" : health.mensagem],
        `${num(health.fontes_ativas)} fontes ativas · ${num(health.fontes_com_falha)} com falha`, true)),
    section("Mais relevantes", data.faixas.mais_relevantes.slice(0, 5), { key: "todas", type: "list", filters: { sort: "relevant", period: "7d" } }, "lead", none),
    section("Oficiais em destaque", data.faixas.oficiais, { key: "todas", type: "list", filters: { oficial: true } }, "grid", "Nenhuma publicação oficial nos últimos 7 dias."),
    h("div", { class: "cols" },
      section("Atualizações de plataformas", data.faixas.plataformas, { key: "h/plataformas", type: "list", sinal: "atualizacao_plataforma" }, "list", "Nenhuma atualização de plataforma nos últimos 7 dias."),
      section("Regulação e privacidade", data.faixas.regulacao, { key: "h/regulacao", type: "list", sinal: "regulacao" }, "list", "Nenhuma notícia de regulação nos últimos 7 dias.")));
}

// --------------------------------------------------------------------- lista
const PERIODS = [
  ["1h", "Última hora", "publicadas nos últimos 60 minutos"],
  ["24h", "24 horas", "publicadas nas últimas 24 horas"],
  ["hoje", "Hoje", "publicadas hoje (desde 00:00)"],
  ["7d", "7 dias", "publicadas nos últimos 7 dias"],
  ["30d", "30 dias", "publicadas nos últimos 30 dias"],
  ["90d", "90 dias", "publicadas nos últimos 90 dias"],
  ["1a", "1 ano", "publicadas nos últimos 12 meses"],
  ["", "Tudo", "qualquer data"],
  ["custom", "Intervalo…", "escolher as datas de início e fim"],
];
const EXAMPLES = ["Core update do Google", "Meta Ads", "TikTok Shop", "LGPD e publicidade", "Black Friday", "Inteligência artificial"];

function filterBar(onChange) {
  const f = state.filters;
  const isAll = !state.view.category;
  const select = (key, options, label) => {
    const el = h("select", { "aria-label": label, onchange: (e) => { f[key] = e.target.value; onChange(); } },
      options.map(([value, text]) => h("option", { value }, text)));
    el.value = f[key];
    return el;
  };
  const date = (key, label) => h("input", {
    type: "date", value: f[key], "aria-label": label, title: label, hidden: f.period !== "custom",
    onchange: (e) => { f[key] = e.target.value; onChange(); },
  });
  const from = date("from", "De");
  const to = date("to", "Até");
  const periods = h("div", { class: "periods", role: "radiogroup", "aria-label": "Período de publicação" },
    h("span", { class: "periods-label" }, "Período"),
    PERIODS.map(([value, label, title]) => {
      const input = h("input", { type: "radio", name: "period", value, onchange: () => {
        f.period = value;
        const custom = value === "custom";
        from.hidden = !custom; to.hidden = !custom;
        if (!custom) { f.from = ""; f.to = ""; from.value = ""; to.value = ""; }
        onChange();
      } });
      input.checked = f.period === value;
      return h("label", { class: "period", title: title.charAt(0).toUpperCase() + title.slice(1) }, input, h("span", {}, label));
    }));
  const official = h("input", { type: "checkbox", onchange: (e) => { f.oficial = e.target.checked; onChange(); } });
  official.checked = f.oficial;
  return h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
    periods,
    isAll ? select("category", [["", "Todas as categorias"], ...state.meta.categorias.map((c) => [c.slug, c.nome])], "Categoria") : null,
    select("source", [["", "Todas as fontes"], ...state.sources.map((s) => [String(s.id), `${s.nome} (${s.total_artigos})`])], "Fonte"),
    from, to,
    select("min_relevance", [["", "Qualquer relevância"], ["40", "Relevância média ou alta"], ["60", "Somente alta relevância"]], "Relevância"),
    h("label", { class: "check", title: "Google, Meta, YouTube, ANPD e outras fontes oficiais" }, official, " Somente oficiais"),
    select("sinal", [["", "Qualquer assunto"], ...Object.entries(state.meta.sinais)], "Assunto"),
    h("span", { class: "grow" }),
    select("sort", [["recent", "Mais recentes"], ["relevant", "Mais relevantes"]], "Ordenação"));
}

function listQuery() {
  const f = state.filters;
  const params = new URLSearchParams({ limit: PAGE, offset: state.offset, sort: f.sort });
  if (state.view.q) params.set("q", state.view.q);
  const category = state.view.category || f.category;
  if (category) params.set("category", category);
  if (f.period && f.period !== "custom") params.set("period", f.period);
  if (f.period === "custom") { if (f.from) params.set("from", f.from); if (f.to) params.set("to", f.to); }
  for (const key of ["source", "sinal", "min_relevance"]) if (f[key]) params.set(key, f[key]);
  if (f.oficial) params.set("tipo", "oficial");
  return params.toString();
}

function resultsInfo(total) {
  const f = state.filters;
  const brDate = (iso) => iso.split("-").reverse().join("/");
  const period = PERIODS.find(([value]) => value === f.period);
  const periodText = f.period === "custom"
    ? ` · de ${f.from ? brDate(f.from) : "início"} até ${f.to ? brDate(f.to) : "hoje"}`
    : f.period && period ? ` · ${period[2]}` : "";
  return `${num(total)} notícia(s)${state.view.q ? ` para “${state.view.q}”` : ""}${periodText} · ordenadas por ${f.sort === "relevant" ? "relevância" : "data de publicação"}`;
}

async function renderList(view) {
  if (!state.sources.length) state.sources = await api("/api/sources");
  const cat = state.view.category ? catOf(state.view.category) : null;
  const highlight = HIGHLIGHTS.find((item) => item.key === state.view.key);
  const title = cat ? cat.nome : highlight ? highlight.label : "Todas as notícias";
  const sub = cat ? "Publicações oficiais e notícias jornalísticas aparecem identificadas; histórias repetidas em vários veículos aparecem uma vez."
    : highlight ? highlight.sub : null;
  const info = h("p", { class: "results-info" });
  const holder = h("div", {});
  const more = h("div", { class: "more" });
  let request = 0;
  const load = async (append) => {
    const mine = ++request;
    const data = await api(`/api/articles?${listQuery()}`);
    if (mine !== request) return; // chegou uma resposta de filtro antigo: descarta
    info.textContent = resultsInfo(data.total);
    if (append) holder.querySelector(".grid")?.append(...data.items.map(card));
    else fill(holder, data.items.length ? h("div", { class: "grid" }, data.items.map(card))
      : h("div", { class: "empty" }, `Nenhuma notícia encontrada${state.view.q ? ` para “${state.view.q}”` : ""}. Tente outros termos ou amplie o período.`));
    const shown = state.offset + data.items.length;
    fill(more, shown < data.total
      ? h("button", { class: "btn", type: "button", onclick: () => { state.offset += PAGE; load(true).catch((e) => toast(e.message)); } }, "Carregar mais") : "");
  };
  const reset = () => { state.offset = 0; load(false).catch((e) => fill(holder, h("div", { class: "empty" }, e.message))); };
  fill(view, 
    h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "eyebrow", style: `color:${cat ? cat.cor : highlight ? highlight.color : ALL_COLOR}` }, cat ? "Categoria" : highlight ? "Destaque" : "Visão geral"),
      h("h1", {}, title),
      sub ? h("p", {}, sub) : null)),
    state.view.key === "todas" ? h("div", { class: "chips" }, EXAMPLES.map((text) =>
      h("button", { class: "chip", type: "button", onclick: () => go({ key: "todas", type: "list", q: text }) }, text))) : null,
    filterBar(reset), info, holder, more);
  await load(false);
}

// -------------------------------------------------------------------- fontes
async function renderSources(view) {
  const [sources, runs] = await Promise.all([api("/api/sources"), api("/api/runs?limit=50")]);
  state.sources = sources;
  const toggle = async (source) => {
    try { await api(`/api/sources/${source.id}`, jsonBody("PATCH", { ativo: !source.ativo })); renderSources(view); }
    catch (error) { toast(error.message); }
  };
  const typeName = { rss: "RSS", html: "Página", search: "Busca" };
  const statusOf = (s) => !s.ativo ? ["", "desativada"] : s.falhas_seguidas >= 3 ? ["erro", "erro"] : s.falhas_seguidas ? ["parcial", "instável"] : s.ultimo_sucesso ? ["ok", "ok"] : ["", "aguardando"];
  const sourceRows = sources.map((s) => {
    const [cls, text] = statusOf(s);
    return h("tr", { class: s.ativo ? "" : "off" },
      h("td", {}, h("strong", {}, s.nome), s.oficial ? [" ", h("span", { class: "badge official" }, "oficial")] : null,
        h("div", { class: "hint" }, s.url || s.consulta || "")),
      h("td", {}, typeName[s.tipo] || s.tipo),
      h("td", {}, catOf(s.categoria).nome),
      h("td", { class: "num" }, s.ultimo_sucesso ? fmtDate(s.ultimo_sucesso) : "—"),
      h("td", {}, h("span", { class: `st ${cls}` }, text),
        s.ultimo_erro ? h("div", { class: "hint err" }, s.ultimo_erro) : null,
        s.falhas_seguidas > 1 ? h("div", { class: "hint" }, `${s.falhas_seguidas} falhas seguidas`) : null),
      h("td", { class: "num" }, num(s.total_artigos)),
      h("td", { class: "num" }, `${s.intervalo_efetivo_min} min`),
      STATIC ? null : h("td", {}, h("button", { class: "btn btn-sm", type: "button", onclick: () => toggle(s) }, s.ativo ? "Desativar" : "Ativar")));
  });
  const runRows = runs.map((r) => h("tr", {},
    h("td", { class: "num" }, r.id), h("td", { class: "num" }, fmtDate(r.inicio)), h("td", { class: "num" }, r.fim ? fmtDate(r.fim) : "em andamento"),
    h("td", {}, r.gatilho === "manual" ? "manual" : "automática"),
    h("td", {}, r.fim ? h("span", { class: `st ${r.fontes_falha ? "parcial" : "ok"}` }, r.fontes_falha ? "parcial" : "ok") : ""),
    h("td", { class: "num" }, r.fim ? r.fontes_ok : ""), h("td", { class: "num" }, r.fim ? r.fontes_falha : ""),
    h("td", { class: "num" }, r.fim ? r.itens_novos : "")));
  const table = (heads, rows, empty) => h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, heads.map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: heads.length }, empty)))));

  fill(view, 
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Fontes e coleta"),
      h("p", {}, `${sources.filter((s) => s.ativo).length} fontes ativas. ${STATIC ? "Coleta automática a cada hora pelo GitHub Actions."
        : `Atualização automática a cada ${state.meta.intervalo_min} minutos (configurável no arquivo .env).`}`))),
    STATIC
      ? h("div", { class: "panel" }, h("h3", {}, "Adicionar fonte"), h("p", { class: "hint", style: "margin:0" },
        "Nesta versão publicada, as fontes ficam no arquivo ",
        state.meta.repo ? h("a", { href: `https://github.com/${state.meta.repo}/edit/main/config/sources.json`, target: "_blank", rel: "noopener noreferrer" }, "config/sources.json") : "config/sources.json",
        " do repositório no GitHub. Ao salvar uma alteração nele, uma nova coleta roda e o site é publicado de novo em alguns minutos."))
      : h("div", { class: "panel" }, h("h3", {}, "Adicionar fonte"), sourceForm(view),
        h("p", { class: "hint", style: "margin:10px 0 0" }, "A fonte é testada antes de ser salva e entra na próxima coleta.")),
    h("h3", {}, "Fontes"),
    table(["Fonte", "Tipo", "Categoria", "Última coleta", "Status", "Itens", "Intervalo", ...(STATIC ? [] : [""])], sourceRows, "Nenhuma fonte cadastrada."),
    h("h3", {}, "Histórico das últimas atualizações"),
    table(["#", "Início", "Fim", "Origem", "Status", "Fontes ok", "Com falha", "Novas"], runRows, "Nenhuma coleta registrada ainda."));
}

function sourceForm(view) {
  const field = (label, control, wide) => h("label", { class: wide ? "wide" : "" }, label, control);
  const nome = h("input", { required: true, maxlength: 120, placeholder: "Ex.: Blog do TikTok for Business" });
  const tipo = h("select", {}, h("option", { value: "rss" }, "RSS / Atom"), h("option", { value: "search" }, "Busca (Google/Bing Notícias)"), h("option", { value: "html" }, "Página sem RSS"));
  const url = h("input", { type: "url", placeholder: "https://… (feed ou página de notícias)" });
  const consulta = h("input", { placeholder: 'Ex.: "retail media" Brasil', maxlength: 200 });
  const motor = h("select", {}, h("option", { value: "google" }, "Google Notícias"), h("option", { value: "bing" }, "Bing Notícias"));
  const categoria = h("select", {}, state.meta.categorias.map((c) => h("option", { value: c.slug }, c.nome)));
  const idioma = h("select", {}, h("option", { value: "pt" }, "Português"), h("option", { value: "en" }, "Inglês"));
  const oficial = h("input", { type: "checkbox" });
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Testar e adicionar");
  const urlField = field("Endereço", url);
  const queryField = field("Termo de busca", consulta);
  const motorField = field("Buscador", motor);
  const sync = () => { const s = tipo.value === "search"; urlField.hidden = s; queryField.hidden = !s; motorField.hidden = !s; };
  tipo.addEventListener("change", sync);
  const form = h("form", { class: "form-grid", onsubmit: async (event) => {
    event.preventDefault();
    submit.disabled = true; submit.textContent = "Testando…";
    try {
      const result = await api("/api/sources", jsonBody("POST", {
        nome: nome.value, tipo: tipo.value, url: url.value || null, consulta: consulta.value || null, motor: motor.value,
        categoria: categoria.value, idioma: idioma.value, oficial: oficial.checked }));
      toast(`Fonte adicionada: ${result.itens_no_teste} itens encontrados no teste.`);
      await renderSources(view);
    } catch (error) {
      toast(error.message); submit.disabled = false; submit.textContent = "Testar e adicionar";
    }
  } }, field("Nome", nome), field("Tipo", tipo), urlField, queryField, motorField, field("Categoria", categoria), field("Idioma", idioma),
    h("label", { class: "check", style: "flex-direction:row;align-items:center;margin-top:22px" }, oficial, " Fonte oficial"),
    h("div", { style: "display:flex;align-items:flex-end" }, submit));
  sync();
  return form;
}

// --------------------------------------------------------------------- saúde
function dotClass(health) {
  if (health.coletando) return "running";
  return { verde: "ok", amarelo: "parcial", vermelho: "falhas" }[health.nivel] || "";
}

async function pollStatus() {
  clearTimeout(pollStatus.timer);
  let next = 60000;
  try {
    const health = await api("/api/status");
    $("status-dot").className = "dot " + dotClass(health);
    $("status-text").textContent = health.coletando ? "Coletando…"
      : health.ultima_atualizacao ? `Atualizado ${fmtDate(health.ultima_atualizacao)}` : "Sem coletas ainda";
    $("status").title = health.mensagem || "Situação da coleta";
    const button = $("refresh-btn");
    button.disabled = health.coletando;
    button.classList.toggle("spin", health.coletando);
    if (state.collecting && !health.coletando) {
      toast("Coleta concluída. A página foi atualizada.");
      state.sources = [];
      if (!$("detail").open) render();
    }
    if (health.dados_novos) {
      toast("Novas notícias publicadas. A página foi atualizada.");
      state.sources = [];
      if (!$("detail").open) render();
    }
    state.collecting = health.coletando;
    if (health.coletando) next = 4000;
    else if (STATIC) next = 300000;
  } catch (_) {
    $("status-dot").className = "dot falhas";
    $("status-text").textContent = "Servidor indisponível";
    next = 15000;
  }
  pollStatus.timer = setTimeout(pollStatus, next);
}

// ---------------------------------------------------------------------- tema
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("radar-tema"); } catch (_) { /* armazenamento indisponível */ }
  if (saved) document.documentElement.dataset.theme = saved;
  $("theme-btn").addEventListener("click", () => {
    const current = document.documentElement.dataset.theme
      || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("radar-tema", next); } catch (_) { /* segue sem salvar */ }
  });
}

// -------------------------------------------------------------------- início
async function init() {
  document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = ICONS[el.dataset.icon] || ""; });
  initTheme();
  $("brand").addEventListener("click", () => go({ key: "dashboard", type: "dashboard" }));
  $("menu-btn").addEventListener("click", () => { $("sidebar").classList.add("open"); $("scrim").classList.add("show"); });
  $("scrim").addEventListener("click", closeMenu);
  $("status").addEventListener("click", () => go({ key: "fontes", type: "sources" }));
  $("detail").addEventListener("click", (event) => { if (event.target === $("detail")) $("detail").close(); });
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const q = $("search-input").value.trim();
    const inCategory = state.view.type === "list" && state.view.category;
    go(inCategory ? { key: state.view.key, type: "list", category: state.view.category, q } : { key: "todas", type: "list", q });
  });
  $("refresh-btn").addEventListener("click", async () => {
    $("refresh-btn").disabled = true;
    try {
      const result = await api("/api/refresh", { method: "POST" });
      if (result.status === "estatico") {
        toast(result.mensagem);
        $("refresh-btn").disabled = false;
        if (result.recarregado) { state.sources = []; render(); pollStatus(); }
        return;
      }
      state.collecting = true;
      toast("Atualização iniciada. Só o que mudou nas fontes será baixado.");
      setTimeout(pollStatus, 800);
    } catch (error) { toast(error.message); $("refresh-btn").disabled = false; }
  });
  try {
    state.meta = await api("/api/meta");
    state.cats = Object.fromEntries(state.meta.categorias.map((c) => [c.slug, c]));
  } catch (error) {
    fill($("view"), h("div", { class: "empty" }, `Servidor indisponível: ${error.message}`));
    return;
  }
  await render();
  pollStatus();
}

init();

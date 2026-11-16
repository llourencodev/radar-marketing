# Radar de Marketing

Aplicação web local que coleta notícias reais de marketing (RSS, páginas oficiais e
busca no Google News / Bing News), classifica tudo com regras locais e, opcionalmente,
gera resumos e impactos com IA. Mesma arquitetura do Radar Inteligente de Notícias:
Python (FastAPI) + SQLite com FTS5 + interface HTML/JS sem etapa de build.

## Como usar (Windows)

Dê um duplo clique em `iniciar.bat`. Na primeira vez ele cria o ambiente virtual e
instala as dependências; depois abre <http://127.0.0.1:8765> no navegador.
A coleta começa sozinha e se repete a cada 60 minutos.

Pela linha de comando:

```bash
.venv\Scripts\python.exe -m app            # servidor + coleta automática
.venv\Scripts\python.exe -m app --collect  # uma coleta completa e sai
.venv\Scripts\python.exe -m pytest         # testes (não usam a rede)
```

## O que ele faz

- **6 abas temáticas**: Mídia Paga & Performance, SEO & Conteúdo, Redes Sociais &
  Influência, Publicidade & Branding, IA & MarTech, E-commerce, Mercado & Regulação.
- **Nada inventado**: título, data, imagem e link vêm só da fonte. Ausência aparece
  como ausência ("data não confirmada", "sem imagem da fonte", "link via agregador").
- **Tipo e assunto explícitos**: cada notícia é *oficial* (plataforma, empresa ou órgão)
  ou *jornalística*, e recebe um assunto (atualização de plataforma, regulação e
  privacidade, movimentação de mercado, pesquisa e dados, campanha e case). Uma
  atualização de plataforma só conta como confirmada quando vem da própria plataforma;
  vinda da imprensa aparece como "(relatada)".
- **Duplicadas e linha do tempo**: mesma URL canônica ou título com similaridade ≥ 0,85
  em até 7 dias entram no mesmo grupo; a lista mostra uma e o detalhe mostra as demais.
- **Relevância 0-100**: fonte oficial, termos prioritários, recência e repetição entre fontes.
- **Glossário**: termos técnicos (ROAS, CAC, Core Update…) explicados no detalhe.
- **Busca** por palavra-chave sem diferenciar acentos, com filtros de período, tipo,
  assunto, fonte, relevância e ordenação.
- **Saúde da coleta**: indicador verde/amarelo/vermelho, histórico de execuções e
  último erro de cada fonte. Uma fonte fora do ar não interrompe as outras; após 3
  falhas seguidas ela espera o dobro do intervalo (até 24 h).

## Configuração

| Arquivo | Para quê |
| --- | --- |
| `config/sources.json` | Fontes iniciais (também dá para adicionar pela tela "Fontes e coleta") |
| `config/categories.json` | Abas, cores, janela de dias e palavras-chave de classificação |
| `config/glossary.json` | Termos técnicos e explicações |
| `.env` (copie de `.env.example`) | Porta, intervalo de coleta e IA opcional |

### IA opcional

Sem configuração nenhuma, tudo funciona com regras locais e resumo extrativo (o trecho
da própria fonte). Para resumos, pontos principais e impactos gerados por IA, defina no `.env`:

- `LLM_PROVIDER=claude` e `ANTHROPIC_API_KEY=...` (pago por uso; o modelo fica em
  `CLAUDE_MODEL`, e um modelo menor como `claude-haiku-5-5` reduz o custo), ou
- `LLM_PROVIDER=ollama` para um modelo local gratuito.

O modelo recebe apenas o título e o trecho vindos da fonte, com a instrução de não
acrescentar fatos; o resultado é marcado como "interpretação gerada por IA" e fica
gravado no banco, então nenhuma notícia é processada duas vezes.

## Versão online gratuita (GitHub Pages)

O Radar também roda sem o seu PC ligado, de graça, no GitHub:

- O **GitHub Actions** executa `gerar_site.py` a cada hora: coleta as fontes vencidas,
  processa as notícias e gera um site estático na pasta `site/`.
- O site é publicado no **GitHub Pages** com a mesma interface. Busca, filtros, painel e
  detalhes funcionam no navegador, a partir de `data/radar.json` (últimos 90 dias).
- O banco SQLite fica guardado no cache do Actions entre uma execução e outra
  (duplicidade, histórico e ETags continuam funcionando).
- Para coletar na hora: aba **Actions** › **Coletar notícias e publicar o site** ›
  **Run workflow**. O botão **Atualizar** do site só busca a publicação mais recente.
- Para adicionar ou desativar fontes, edite `config/sources.json` no GitHub (`"ativo": false`
  desativa); o site é refeito sozinho em alguns minutos.

Configuração única no repositório: **Settings › Pages › Source: GitHub Actions**.

IA opcional, sem expor a chave: em **Settings › Secrets and variables › Actions**, crie o
secret `ANTHROPIC_API_KEY` e a variável `LLM_PROVIDER` = `claude`. O arquivo `.env` nunca
vai para o GitHub.

O GitHub pausa agendamentos de repositórios sem nenhuma alteração por 60 dias; se isso
acontecer, basta reativar o workflow na aba **Actions**.

Para gerar o site localmente sem coletar: `.venv\Scripts\python.exe gerar_site.py --sem-coleta`
(resultado em `site/`).

## Respeito às fontes

Identificação própria no User-Agent, no máximo uma requisição a cada 2 segundos por
site, consulta ao `robots.txt` antes de ler páginas HTML, cache condicional
(ETag / Last-Modified) e nenhum texto republicado: o banco guarda título, trecho do
feed, metadados e link.

## Estrutura

```
app/
  settings.py    configuração, .env e arquivos de config/
  db.py          esquema SQLite (8 tabelas + FTS5) e fontes
  textutil.py    URL canônica, normalização de título, datas, resumo extrativo
  collectors.py  RSS/Atom, página HTML, busca; robots.txt e limite por site
  pipeline.py    regras: abas, tipo, assunto, duplicidade, glossário, relevância
  llm.py         IA opcional (Claude ou Ollama)
  service.py     ciclo de coleta, intervalos, saúde
  main.py        API FastAPI + agendador + interface
  static_site.py exportação do site estático (GitHub Pages)
gerar_site.py    coleta + site estático (usado pelo GitHub Actions)
.github/workflows/publicar.yml   agendamento da coleta e publicação
web/             index.html, styles.css, app.js, static-api.js (API no navegador)
tests/           pytest (sem rede)
data/radar.db    banco (criado na primeira execução)
```

## API local

`GET /api/dashboard` · `GET /api/articles?q=&category=&source=&period=&from=&to=&min_relevance=&tipo=&sinal=&sort=`
· `GET /api/articles/{id}` · `POST /api/refresh` · `GET /api/status` · `GET /api/runs`
· `GET/POST/PATCH /api/sources` · documentação interativa em `/docs`.

## Limites conhecidos

- Links do Google News não revelam o endereço original; ficam marcados como
  "link via agregador" e sem imagem.
- A página de notícias da ANPD é montada por JavaScript, então a ANPD é coberta por
  busca (`site:gov.br/anpd`). Exame, Mundo do Marketing, E-Commerce Brasil e Search
  Engine Land não oferecem RSS acessível e também entram por busca.
- Fontes em inglês são exibidas no idioma original (marcadas "em inglês"); a tradução
  só acontece no resumo quando a IA está ligada.

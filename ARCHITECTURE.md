# OdyssAI-RAG — Architecture

> Figé le 2026-06-12 (recadrage Sophie, soir même de la purge du Bruit Blanc et
> du drain du tier company). Ce document est la référence : toute issue du repo
> s'y rattache. Si une décision change, on édite ICI d'abord.

## Position dans la famille odyssai.eu

| Produit | Rôle | Ce qu'il ne fait PAS |
|---|---|---|
| OdyssAI-X | inférence distribuée | — |
| Companion | client + **consommateur** de mémoire/corpus | exploiter le parc RAG |
| odyssai-services | cockpit ops (bench) | — |
| **OdyssAI-RAG** | **le plan de contrôle du parc RAG** : santé, ingestion, contenu, pilotage | être dans le chemin de chat |

OdyssAI-RAG n'est **pas** dans le data path des conversations. Companion
interroge les services directement (latence) ; OdyssAI-RAG les **observe,
alimente, répare et gouverne**.

## La décision fondatrice : Memory ≠ Corpus

Deux organes, deux physiologies. Les mélanger est la dérive qui a coûté la
quinzaine du 3-12 juin (un corpus de 113k docs de code injecté dans le tier
mémoire company → backlog de 81k docs en extraction LLM, semaines de `.49`
brûlées, fragilité des stores exposée).

| | **Memory** (LightRAG) | **Corpus** (Qdrant `.44`) |
|---|---|---|
| Nature | vivante, conversationnelle, **incrémentale** | documentaire, volumineuse, alimentée par pipelines |
| Taille | petite — dizaines/centaines d'articles par tier | grosse — `alpha_centauri` ≈ 116k points et ça grandit |
| Tiers / unités | user / team / project / company | **collections nommées** (code, veille, légis, …) |
| Écriture | compile Karpathy (conversations → articles → ingestion) | crawl `.46`, Docling, watcher Obsidian, upload |
| Lecture | 4 tiers en parallèle dans le chat (bloc éphémère #30) | attachable à une conversation/projet, à la demande |
| Moteur | LightRAG (graphe entités + vecteurs, upsert incrémental) | Qdrant pur (bge-m3 1024d) |

### Pourquoi LightRAG reste (et pourquoi pas GraphRAG)

- La mémoire est un **flux continu** : le modèle incrémental de LightRAG
  (upsert + merge d'entités) est le bon. GraphRAG (Microsoft) est **batch**
  (communautés, résumés hiérarchiques, re-index) et coûte 2-5× en tokens
  d'indexation — notre ressource rare est le GPU local. Écarté pour la
  mémoire ; ré-évaluable un jour comme *recette de corpus* pour un corpus
  statique à questions de synthèse globale, si le mode `global` de LightRAG
  s'avère insuffisant. Le registre de corpus reste agnostique de la recette.
- Qdrant n'est pas un concurrent : **c'est le disque dur, LightRAG est le
  cerveau**. Les corpus n'ont pas besoin de graphe d'entités ; la mémoire si
  (« te souviens-tu de moi » = recall d'entités, pas similarité brute).

### Le drain du 2026-06-12 (fait accompli)

Le tier company (`:8766`) a été vidé de son corpus : recouvrement vérifié
5/5 par match exact `source` dans `alpha_centauri` (116 387 pts ≥ 113 076
docs), archive `1,3 GB` conservée sur `.39`
(`~/nemo-memory-data/company-corpus-archive-2026-06-12.tgz` + dossier
`company-corpus-retired-2026-06-12/`). Le backlog 81k est **annulé**. Le tier
company repart vide, pour de la vraie mémoire d'entreprise (même nature que
le wiki user : décisions, gens, projets).

## Le parc (état 2026-06-12)

| Service | Où | Rôle | API |
|---|---|---|---|
| nemo-memory `:8765` | `.39`, **process host** (LaunchAgent `eu.odyssai.nemo-memory`, MLX/Metal) | embeddings (`nemo-embed`, 1024d) + tiers user/team/project (collections) | custom (`/query/context`, `/v1/embeddings`) |
| company `:8766` | `.39`, process host (`eu.odyssai.lightrag-webui`) | tier mémoire company (vide, neuf) | LightRAG standard (`/query`, `/documents`) |
| user `:8767` | `.39`, process host (`eu.odyssai.lightrag-nemo`) | tier mémoire user (68 docs, nettoyé du Bruit Blanc) | LightRAG standard |
| Karpathy | `.39`, container `thecompai-memory` | compile conversations → articles → ingestion nemo | `/compile`, `/context/{userId}` |
| Qdrant | `.44:6333` | **les corpus** : `alpha_centauri`, `obsidian-context`, `companion-memory`, `nemo` | Qdrant REST |
| bge-m3 | `.44:8082` | embeddings corpus | OpenAI-compat |
| crawler | `.46` | alimentation corpus (GitHub, docs, veille) | — |
| Sentinel LightRAG `:9621` | `.39` | corpus légal de Sentinel (produit séparé, design assumé) | LightRAG |

Extraction LLM des tiers mémoire : `tele-fast:qwen3-coder-30b-a3b-instruct-6bit`
via le proxy OdyssAI-X `:8000` — **toujours l'id qualifié** `cluster:model`
(leçon du 2026-06-12 : l'id nu devient ambigu dès qu'un Telemak héberge deux
modèles → 400 silencieux par chunk, docs « processed » vides).

## Contrats avec Companion

1. **Query (v0, existant — à documenter ici, pas à réinventer)** :
   - tiers mémoire : `POST :8765/query/context` (collections) et
     `POST :8766/query {mode, only_need_context: true}` ;
   - le bloc RAG est **éphémère par tour** (mécanique Companion #30) ;
   - un tier vide répond `[no-context]` → c'est « rien », pas du contexte
     (filtré côté Companion depuis v0.2.51).
2. **Provenance** : chaque bloc récupéré doit porter ses doc-ids sources.
   Objectif produit : un souvenir cliquable dans Companion ouvre la fiche du
   doc dans le dashboard OdyssAI-RAG (« d'où vient ce souvenir » — la réponse
   structurelle à l'incident Bruit Blanc).
3. **Santé** : OdyssAI-RAG expose un health agrégé du parc ; Companion
   Admin → Memory l'affiche (vert/dégradé/down par tier) et **linke vers le
   dashboard** au lieu d'embarquer des features d'exploitation.
4. **RAG custom = attacher un corpus** : Companion attache une collection
   nommée du registre à une conversation/projet (pas d'URL brute — la leçon
   de l'add-on retiré le 2026-06-11). Le bloc éphémère interroge les corpus
   attachés via Qdrant `.44`.

## Le registre de corpus

Un corpus = `{ id, titre, collection Qdrant, scope (private/team/company/public),
recette (embed, chunking, éventuel moteur d'extraction), sources d'alimentation
(crawl, vault, upload, RSS veille), stats, santé }`. CRUD dans le dashboard,
lecture par Companion. Les pipelines de `.44`/`.46` deviennent des *sources*
configurées d'un corpus, pas des scripts orphelins.

## Robustesse (les cicatrices du 2026-06-12 comme spec)

- **Backup-avant-mutation** systématique (tar du working dir / snapshot
  collection) — intégré aux actions du dashboard, pas optionnel.
- **Vérifier l'effet, jamais l'intention** : toute suppression re-liste et
  re-compte après coup (le `delete_document` « not found » silencieux qui a
  fait revenir Elma).
- **Fenêtres calmes** : les pipelines d'extraction respectent des quiet hours
  configurables (ne pas brûler `.49` pendant les heures de chat).
- Stores LightRAG : flush = réécriture JSON intégrale non-atomique (2
  troncatures en 10 jours). Procédure de rebuild documentée
  (`~/rebuild-vdb/` sur `.29`/`.39`). Migration storage Postgres/Qdrant sous
  LightRAG = **durcissement différé** (les tiers sont redevenus petits), pas
  un préalable.

## v1 — le dashboard (issues #1-#6)

Pattern de la famille : FastAPI + dashboard single-file (comme OdyssAI-X
`dashboard.html` et le bench odyssai-services). Container `odyssai-rag` sur
`.39` (la donnée y vit), port `:8801`. Deux pages : **Memory** et **Corpus**.

Hors scope v1 : gateway dans le data path, GraphRAG, migration storage,
multi-instance Qdrant.

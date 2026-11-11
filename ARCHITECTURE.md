# OdyssAI-RAG — Architecture

> Figé le 2026-06-12 (recadrage Sophie, soir même de la purge du Bruit Blanc et
> du drain du tier company). Ce document est la référence : toute issue du repo
> s'y rattache. Si une décision change, on édite ICI d'abord.

## Mise à jour 2026-11-11 — Coeos-Rag, la console complète

Cette section **prime** sur tout le reste du document.

**Nom.** Le produit s'appelle Coeos-Rag. Seul le visible change (titre, interface, logo) ;
le package `odyrag`, les variables `ODYRAG_*`, `~/.odyrag` et le label launchd restent.

**Forme (décision Sophie, 2026-11-11, sur proposition du panel).** Une seule interface,
servie par ce service. Le navigateur appelle **directement** les instances LightRAG pour
les documents, la file et la requête ; ce service ne porte que ce que LightRAG ne sait pas
faire (modèle, endpoint, embedder, certification, journal). Pas de proxy : faits vérifiés
le jour même, les instances acceptent les appels d'une autre origine et leur
authentification est désactivée, donc un proxy authentifié n'aurait rien protégé et aurait
dû suivre chaque version de LightRAG. Cela remplace le « fork `lightrag_webui` + gateway »
du découpage v1 plus bas.

**Endpoint du LLM par corpus.** `LLM_BINDING_HOST` rejoint les clés modifiables. Un
changement d'endpoint emprunte exactement le chemin d'un changement de modèle (backup,
refus si occupé, redémarrage, vérification des deux valeurs, rollback des deux). La
vérification « modèle prêt » lit `<endpoint du corpus>/models` : l'endpoint rapporté par
l'instance, sinon celui du registre, sinon `ODYRAG_ENGINE_URL`.

**Un LLM par instance.** LightRAG 1.4.16 (les deux instances) n'a qu'un modèle pour
l'extraction et la requête. Les rôles EXTRACT / QUERY / KEYWORDS arrivent en 1.5.0. Les
séparer est une montée de version des instances, à décider à part (stores JSON non
atomiques, alpha = 112 776 documents).

**État mesuré le 2026-11-11.** alpha : 112 776 documents traités, 0 en échec, dernier
traitement le 21 octobre. L'embedder des deux corpus reste `nemo-embed`
(Qwen3-Embedding-0.6B) : décision Sophie, pas de passage à bge-m3, qui exigerait une
ré-indexation.

**Companion n'existe plus** (Sophie, 2026-11-11). Tout ce qui, plus bas dans ce document,
le décrit comme client ou consommateur de la mémoire (« Position dans la famille »,
« Contrats avec Companion », le bloc RAG éphémère) est historique. Vérifié le même jour
dans le journal de l'instance `:8767` : aucune requête entre le 22 octobre et nos propres
tests.

**Remise à zéro de `company` (`:8767`), 2026-11-11.** Le corpus contenait 92 documents
dont 78 % de texte répété : 25 instantanés successifs du même wiki personnel (75 % du
volume), 23 copies tronquées à 3 000 caractères, un graphe de 35 relations. Ce n'était
pas une mémoire d'entreprise mais le wiki personnel compilé à l'époque de Companion.
Décision Sophie : repartir de zéro. L'instance a été arrêtée, son répertoire mis de côté
sans rien effacer (`…/lightrag/70767408-…-retired-2026-11-11`, plus l'archive
`company-memory-backup-20261111-170038-before-dup-delete.tgz`), puis redémarrée sur un
répertoire vide avec le même `.env`. Dans le registre, les ères et l'identité de l'espace
vectoriel de ce corpus sont remises à zéro : un index vide n'a pas encore d'espace, il se
certifie après la première alimentation. La suite est à la main de Sophie : reprise d'une
partie du contenu depuis son vault après nettoyage, puis alimentation quotidienne. Règle
à tenir pour l'émetteur : **un article = un document, dans sa dernière version** (LightRAG
dédoublonne sur le contenu exact, donc chaque variante d'un même texte devient un
document de plus).

## Mise à jour 2026-10-02 — état réel et réglages « Models »

Cette section **prime** sur le reste du document là où elle le contredit.

**État réel.** Qdrant `.44` est abandonné (Sophie, 2026-09-23 ; `.44` décommissionné
sauf guardian). Les corpus vivent dans des **instances LightRAG sur `.39`**, une par
corpus (working dir, port et service launchd propres) :

| Corpus | Instance | Contenu |
|---|---|---|
| alpha | `:8766` (`eu.odyssai.lightrag-webui`) | corpus consolidé : 112 667 docs, 161 375 entités, 284 219 relations (merge des 4 shards, 2026-07-10) |
| company | `:8767` (`eu.odyssai.lightrag-nemo`) | mémoire Nemo/Companion, 156 docs (remis à zéro le 2026-11-11, voir plus haut) |

**Séparation OdyssAI.** OdyRAG et OdyssAI-X sont deux projets distincts. OdyRAG
**consomme** l'API publique `/v1` d'OdyssAI-X comme n'importe quel client et ne la
modifie jamais (décision Sophie, 2026-10-02 : « tu ne peux rien modifier sur odyssai »).

**Réglages « Models » par corpus (décision).** Un LLM et un embedder par corpus,
visibles et modifiables depuis le dashboard, avec ce que LightRAG ne sait pas dire :
*avec quel modèle le corpus a été indexé*.
- LightRAG lit `LLM_MODEL` / `EMBEDDING_*` de l'environnement de son process au
  démarrage uniquement. Changer un réglage = **modifier le plist de l'instance puis la
  redémarrer**. Le dashboard est donc un **plan de contrôle qui applique**, il ne
  s'interpose pas : *pas de passerelle LLM/embedding dans le data path* (principe
  fondateur ci-dessus ; une passerelle serait un point de défaillance unique de toutes
  les requêtes RAG).
- Chaque changement suit les cicatrices du 2026-06-12 : **backup du plist avant
  mutation**, refus si l'instance est `busy` (on ne redémarre jamais une instance en
  cours d'extraction : corruption de `doc_status`), **vérification de l'effet** après
  redémarrage (`/health` reporte bien le nouveau modèle), **rollback** automatique si
  l'instance ne revient pas saine, entrée dans le journal d'actions.
- **Détection de dérive.** Les ids de modèles du moteur changent (5 pannes en 3 mois :
  ids déplacés/renommés, auto-swap off). Le dashboard compare le modèle configuré à la
  liste des modèles prêts du moteur et signale en rouge « modèle non chargé » *avant*
  que les requêtes hybrid ne renvoient « No relevant context found ».
- **« Indexé avec ».** LightRAG n'enregistre le modèle d'indexation nulle part (ni cache,
  ni `doc_status`, ni vdb : seulement `embedding_dim`). Le registre le porte : une
  **liste d'ères** (modèle d'extraction, dates, source de l'information) étendue à chaque
  changement, plus l'**identité de l'espace vectoriel** de l'index.
- **Embedder : la dimension n'est pas l'identité.** `nemo-embed` (Qwen3-Embedding) et
  `bge-m3` font tous deux 1024 dimensions mais n'ont pas le même espace : échanger l'un
  contre l'autre ne produit aucune erreur et rend la recherche vectorielle aléatoire.
  L'identité se vérifie par **cosinus** : recalcul d'un échantillon de chunks stockés
  (certification de l'index) puis vecteur étalon d'une phrase test. Un embedder dont le
  cosinus est sous le seuil est refusé. Changer *d'espace* = ré-indexer, pas un réglage.
  Valeurs **mesurées** sur l'index alpha réel (24 chunks, 2026-10-02) :

  | Comparaison | min | médiane | max |
  |---|---|---|---|
  | vecteurs stockés vs même embedder, **un texte par appel** | 1.0000 | 1.0000 | 1.0000 |
  | vecteurs stockés vs même embedder, **lots de 8** | 0.756 | 0.913 | 1.0000 |
  | vecteurs stockés vs autre embedder de même dimension (bge-m3) | -0.054 | -0.005 | 0.062 |

  Conséquences : la certification embarque **un seul texte par requête** (le padding des
  lots déplace les vecteurs d'un même embedder) ; l'identité d'un embedder qu'on branche
  se juge sur le vecteur étalon (seuil 0.98, mêmes conditions) ; la certification d'un
  index accepte médiane ≥ 0.80 et plancher ≥ 0.50, car un index construit en lots n'est
  pas reproductible au bit près, alors qu'un autre espace tombe autour de 0.
- **Hors périmètre.** Aucun accès en écriture au moteur OdyssAI-X, aucun redémarrage du
  moteur. Les URL d'embedder sont restreintes aux réseaux privés (le texte des
  documents transite par cette URL).
- **Forme.** Le dashboard tourne en **process hôte** (LaunchAgent `eu.odyssai.odyrag`,
  non en container) parce qu'il doit lire/écrire les plists et appeler `launchctl` ;
  cela amende la mention « container `odyssai-rag` » de la v1.

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

### Décision A (2026-06-12 soir, Sophie) — la veille alimente les CORPUS

« On ne mélange pas : LightRAG pour la memory, Qdrant pour les corpus. »
La veille média (#3) et tout flux documentaire écrivent dans des collections
Qdrant `.44` via le registre de corpus (#7) — **jamais** dans les tiers
mémoire. Pas d'exception, pas de flag « digest vers memory » : la mémoire
ne s'écrit que par le compile des conversations (Karpathy).

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
| Sentinel | `.23` | **projet autonome** — son LightRAG dédié vit sur `.23` ; retiré de `.39` le 2026-06-12 après-midi. Hors scope OdyssAI-RAG (epic #1). | — |

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

## v1 — le découpage (epic #1, structure de Sophie)

- **#2 Dashboard (13)** — fork `lightrag_webui` (HKUDS) + gateway FastAPI
  fédérant les DEUX DB mémoire (user `.39:8767` + company `.39:8766`) :
  parse, crawl, graphe, export Obsidian, choix LLM. Container `odyssai-rag`
  sur `.39` (la donnée y vit).
- **#3 Veille média (8)** — watchlist d'URLs, détection de changement,
  ré-ingestion auto → **collections Qdrant via le registre (#7)**, décision A.
  Pattern réutilisable par Sentinel (sur son infra `.23`).
- **#5 Pipelines (5)** — scan/cancel, fenêtres calmes, garde anti-réveil de
  backlog (cicatrices du 2026-06-12).
- **#6 Réparations sûres (5)** — backup-avant-mutation, delete VÉRIFIÉ,
  quarantaine/re-extract (idem).
- **#7 Registre de corpus (3)** — collections Qdrant nommées + recettes +
  scopes ; la destination de #3 et la source du « attach corpus » Companion.

Fondus dans l'epic : #4 → #2 (squelette), #8 → #3 (crawl), #9 → #2
(graphe/export). Hors scope v1 : gateway dans le data path de chat, GraphRAG,
migration storage, Sentinel.

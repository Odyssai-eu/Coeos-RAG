# OdyRAG

> Branded knowledge-graph management layer for the **odyssai.eu** stack —
> built on [LightRAG](https://github.com/HKUDS/LightRAG) (HKUDS, MIT License).

OdyRAG is the day-to-day cockpit for the cluster of LightRAG instances that
power Companion's memory and the alpha-centauri corpus ingestion: live
monitoring, parallel injection, document management, querying, and a 3D
"galaxy" view of the entity graph.

Ce dépôt réunit deux pièces :

- **Le dashboard de requête et d'ingestion** (`odyrag.html`, servi par OdyssAI-X) :
  décrit dans [docs/cockpit-2026-06-21.md](docs/cockpit-2026-06-21.md), le README
  d'origine conservé tel quel. Ses sections « Instances » et « Ingestion » sont
  périmées : les 4 shards ont été fusionnés dans alpha (`:8766`, 2026-07-10) et
  Qdrant est abandonné.
- **Le plan de contrôle « Models »** (le code de ce dépôt, `odyrag/`) : ce qui suit.
  Il observe les corpus (instances LightRAG sur `.39`), dit avec quels modèles ils ont
  été indexés, et applique les changements de modèle **sans jamais s'interposer dans le
  chemin des requêtes**.

Référence d'architecture et décisions : [ARCHITECTURE.md](ARCHITECTURE.md)
(la section « Mise à jour 2026-10-02 » prime là où elle contredit le reste).

## Ce que ça fait — réglages « Models » par corpus

- **LLM par corpus** : liste des modèles *prêts* du moteur (lecture de `/v1/models`),
  changement en un clic. Un modèle configuré mais absent ou déchargé côté moteur est
  signalé *avant* que les requêtes ne renvoient « No relevant context found ».
- **Indexé avec** : LightRAG n'enregistre pas le modèle d'indexation. Le registre tient
  une liste d'*ères* (modèle d'extraction, dates, source de l'information, nombre de
  documents ingérés pendant l'ère) et l'*identité de l'espace vectoriel* de l'index.
- **Embedder** : vivant ou non, poids chargés, dimension, et surtout **même espace que
  l'index** ou non. La dimension ne suffit pas : `nemo-embed` et `bge-m3` font tous deux
  1024 dimensions, mais pas le même espace. Changer d'embedder se fait seulement vers un
  embedder qui reproduit l'espace de l'index (cosinus), sinon c'est une ré-indexation.
- **Certifier l'index** : recalcule un échantillon de chunks stockés avec l'embedder de
  l'instance et compare aux vecteurs stockés. Lecture seule, aucun redémarrage.
- **Journal d'actions** et suivi en direct de chaque changement.

## Ce que ça ne fait pas

- Pas de passerelle LLM ni embedding dans le data path : une passerelle serait un point
  de défaillance unique de toutes les requêtes RAG.
- Aucune écriture sur le moteur OdyssAI-X : c'est un projet distinct, OdyRAG n'en est
  qu'un client public (`GET /v1/models`).
- Pas de ré-indexation, pas de changement d'espace vectoriel.

## Comment un changement est appliqué

LightRAG ne lit `LLM_MODEL` / `EMBEDDING_*` qu'au démarrage : changer un réglage, c'est
modifier le plist de l'instance et la redémarrer. Chaque changement suit toujours les
mêmes étapes, dans l'ordre :

1. refus si un autre changement est en cours sur ce corpus, ou si l'instance est `busy`
   (jamais de redémarrage pendant une extraction : corruption de `doc_status`) ;
2. contrôles amont (modèle prêt côté moteur, embedder vivant, dimension, cosinus) ;
3. sauvegarde du plist (`<plist>.bak-odyrag-<horodatage>`), édition atomique ;
4. `launchctl bootout`, attente de l'arrêt, `bootstrap`, attente de `/health` sain ;
5. vérification que `/health` rapporte bien la nouvelle configuration ;
6. en cas d'échec à n'importe quelle étape : restauration du plist et redémarrage de
   l'ancienne configuration (`rolled_back` dans le résultat) ;
7. écriture dans le registre (nouvelle ère) et dans le journal.

## Sécurité

- Les endpoints qui modifient quoi que ce soit exigent `Authorization: Bearer <jeton>`
  (comparaison à temps constant). Sans jeton configuré, le service est **en lecture
  seule** (403 sur toute mutation). Le jeton vit sur l'hôte (`~/.odyrag/admin-token`,
  mode 0600) et n'est jamais affiché.
- Les URL d'embedder doivent résoudre vers un réseau privé, loopback ou link-local : le
  texte des documents transite par cette URL.
- Seules quatre clés du plist sont modifiables : `LLM_MODEL`, `EMBEDDING_BINDING_HOST`,
  `EMBEDDING_MODEL`, `EMBEDDING_DIM`.
- Aucune adresse, hôte ni port en dur dans le code : tout vient de l'environnement du
  service ou du registre.

## Configuration

Variables d'environnement (posées dans le plist du LaunchAgent par `deploy/deploy.sh`) :

| Variable | Rôle | Défaut |
|---|---|---|
| `ODYRAG_STATE_DIR` | registre, journal d'actions | `~/.odyrag` |
| `ODYRAG_ENGINE_URL` | API publique du moteur d'inférence (lecture de `/v1/models`) | aucun (changement de LLM refusé sauf `force`) |
| `ODYRAG_ADMIN_TOKEN` | jeton des mutations | aucun (lecture seule) |
| `ODYRAG_LAUNCHD_UID` | uid de la session launchd | uid courant |
| `ODYRAG_WAIT_DOWN_S` / `ODYRAG_WAIT_UP_S` | attente d'arrêt / de retour sain | 120 / 300 |
| `ODYRAG_HTTP_TIMEOUT_S` | timeout des appels HTTP | 8 |
| `ODYRAG_EMBED_COS_THRESHOLD` | cosinus minimal sonde vs vecteur étalon | 0.98 |
| `ODYRAG_CERT_MEDIAN_MIN` / `ODYRAG_CERT_MIN_FLOOR` | critères de certification d'un index | 0.80 / 0.50 |

Valeurs mesurées derrière ces seuils : voir ARCHITECTURE.md (même embedder, un texte par
appel : 1.0000 ; même embedder en lots de 8 : médiane 0.91, pire 0.756 ; autre embedder de
même dimension : autour de 0).

### Registre (`<ODYRAG_STATE_DIR>/registry.json`)

Un objet par corpus : `label`, `instance` (`url`, `launchd_label`, `plist`, `workdir`),
`llm`, `embedder` (`url`, `model`, `dim`), `indexed_with` (`embedder` = identité de
l'espace, `llm_eras` = historique). Écriture atomique. Le registre de production est
semé depuis [deploy/registry.prod.json](deploy/registry.prod.json) une seule fois et n'est
jamais écrasé par un déploiement.

## API

| Route | |
|---|---|
| `GET /health` | version du service |
| `GET /` | dashboard |
| `GET /api/overview` | état vivant de chaque corpus, dérive, ères, embedder, moteur |
| `GET /api/jobs`, `/api/jobs/{id}` | suivi des changements |
| `GET /api/actions` | journal d'actions |
| `POST /api/corpora/{id}/llm` `{"model", "force"}` | changer le LLM (202 + `job_id`) |
| `POST /api/corpora/{id}/embedder` `{"url", "model"}` | changer l'embedder (202) |
| `POST /api/corpora/{id}/certify` | certifier l'index (202) |
| `POST /api/corpora/{id}/start` | recharger une instance arrêtée (202) |

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # 75 tests, aucun réseau, aucun launchd
.venv/bin/python -m tests.dev_server   # dashboard de démo sur 127.0.0.1:8780, monde simulé
```

Les tests tournent contre un simulateur (`tests/fakes.py`) de launchd, de LightRAG, de
l'embedder et du moteur, branché sur `httpx.MockTransport`. Le serveur de démo expose des
boutons de panne (`/dev/world`) pour voir le rollback, le refus « busy » ou la dérive.

`tests/e2e_real_host.py` est le test de bout en bout **sur l'hôte réel** : une instance
LightRAG jetable (port, répertoire et label launchd dédiés, refus de démarrer si le port
est pris) est pilotée par le vrai contrôleur, le vrai `launchctl`, le vrai moteur et le
vrai embedder. Les instances de production ne sont jamais touchées. Les paramètres se
passent par variables `E2E_*` (voir l'en-tête du fichier).

## Déploiement

Le service tourne comme **process hôte** (LaunchAgent `eu.odyssai.odyrag`), pas en
container : il doit lire et écrire les plists et appeler `launchctl`.

```bash
ODYRAG_PORT=<port> ODYRAG_ENGINE_URL=<API du moteur vue depuis l'hôte> deploy/deploy.sh <user@hôte>
```

`ODYRAG_BIND` (optionnel, `0.0.0.0` par défaut) fixe l'interface d'écoute. Idempotent :
refuse de déployer pendant un changement en cours, ne remplace jamais le registre ni le
jeton existants, et échoue bruyamment si le service en marche ne rapporte pas la version
qui vient d'être déployée.

## Statut

Plan de contrôle « Models » livré (LLM, ères, embedder, certification, rollback). Le
reste du périmètre historique (ingestion, crawl, graphe, export Obsidian, veille) reste à
cadrer.

## Licensing

OdyRAG is **proprietary** to odyssai.eu. It builds on **LightRAG** (© HKUDS,
MIT License) and **3d-force-graph** (© Vasco Asturiano, MIT License); those
upstream licenses are retained and credited.

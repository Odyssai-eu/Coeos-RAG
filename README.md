# Coeos-RAG

> La console de gestion du RAG de la stack Coeos : corpus, documents, requêtes et
> modèles. Construit sur [LightRAG](https://github.com/HKUDS/LightRAG) (HKUDS, MIT License).
> Anciennement OdyRAG : le nom du produit a changé le 2026-11-11, les identifiants
> techniques (`ODYRAG_*`, `~/.odyrag`, `eu.odyssai.odyrag`) restent. Le package Python
> s'appelle `coeos_rag`.

Une seule interface, servie par ce service, avec une règle : **le navigateur parle en
direct aux instances LightRAG** pour les documents, la file de traitement et la requête ;
ce service ne garde que ce que LightRAG ne sait pas faire (changer de modèle ou d'endpoint,
changer d'embedder, certifier un index, tenir le journal). Aucun proxy, donc rien à
maintenir quand LightRAG change de version, et **rien dans le chemin des requêtes** de
ceux qui interrogent les corpus.

Cinq sections : Vue d'ensemble, Documents (injection de fichiers et de texte, file de
traitement, liste filtrable), Requête, Modèles, Journal.

Installation, mise à jour et exploitation : [AGENTS.md](AGENTS.md).

## Ce que ça fait — réglages « Modèles » par corpus

- **LLM par corpus** : liste des modèles *prêts* (lecture de `<endpoint>/models`),
  changement en un clic. Un modèle configuré mais absent ou déchargé est signalé *avant*
  que les requêtes ne renvoient « No relevant context found ».
- **Endpoint du LLM par corpus** : quand le serveur de modèles change d'adresse ou publie
  un nom légèrement différent, on saisit le nouvel endpoint, « Lister les modèles » montre
  ce qu'il publie, et le changement s'applique comme un changement de modèle. La
  vérification « modèle prêt » regarde l'endpoint que l'instance appelle réellement
  (`LLM_BINDING_HOST`), pas une adresse globale.
- **Un seul LLM par instance** : LightRAG 1.4.16 utilise le même modèle pour l'extraction
  et pour la requête. Les modèles séparés par rôle n'existent qu'à partir de LightRAG 1.5 ;
  l'interface le dit au lieu d'afficher un sélecteur qui n'aurait aucun effet.
- **Indexé avec** : LightRAG n'enregistre pas le modèle d'indexation. Le registre tient
  une liste d'*ères* (modèle d'extraction, dates, source de l'information, nombre de
  documents ingérés pendant l'ère) et l'*identité de l'espace vectoriel* de l'index.
- **Embedder** : vivant ou non, poids chargés, dimension, et surtout **même espace que
  l'index** ou non. La dimension ne suffit pas : Qwen3-Embedding et bge-m3 font tous deux
  1024 dimensions, mais pas le même espace. Changer d'embedder se fait seulement vers un
  embedder qui reproduit l'espace de l'index (cosinus), sinon c'est une ré-indexation.
- **Certifier l'index** : recalcule un échantillon de chunks stockés, répartis sur tout le
  fichier de vecteurs, avec l'embedder de l'instance et compare aux vecteurs stockés.
  Lecture seule, aucun redémarrage.
- **Journal d'actions** et suivi en direct de chaque changement.

## Ce que ça ne fait pas

- Pas de passerelle LLM ni embedding dans le data path : une passerelle serait un point
  de défaillance unique de toutes les requêtes RAG.
- Pas de proxy vers LightRAG : l'interface appelle les instances depuis le navigateur.
  Conséquence : les instances doivent être joignables depuis le poste de l'opérateur, et
  leurs actions sur les documents ne passent pas par le jeton de ce service (LightRAG a sa
  propre authentification, aujourd'hui désactivée sur les deux instances).
- Pas de suppression de documents, pas d'édition du graphe, pas de vue 3D.
- Aucune écriture sur le moteur OdyssAI-X : c'est un projet distinct, Coeos-RAG n'en est
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
- Les URL d'endpoint LLM suivent la même règle (réseau privé uniquement), y compris pour
  « Lister les modèles », qui exige le jeton.
- Seules cinq clés du plist sont modifiables : `LLM_MODEL`, `LLM_BINDING_HOST`,
  `EMBEDDING_BINDING_HOST`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`.
- Aucune adresse, hôte ni port en dur dans le code : tout vient de l'environnement du
  service ou du registre.

## Configuration

Variables d'environnement (posées dans le plist du LaunchAgent par `deploy/deploy.sh`) :

| Variable | Rôle | Défaut |
|---|---|---|
| `ODYRAG_STATE_DIR` | registre, journal d'actions | `~/.odyrag` |
| `ODYRAG_ENGINE_URL` | moteur d'inférence de repli, utilisé seulement quand ni l'instance ni le registre ne donnent l'endpoint du corpus | aucun |
| `ODYRAG_ADMIN_TOKEN` | jeton des mutations | aucun (lecture seule) |
| `ODYRAG_LAUNCHD_UID` | uid de la session launchd | uid courant |
| `ODYRAG_WAIT_DOWN_S` / `ODYRAG_WAIT_UP_S` | attente d'arrêt / de retour sain | 120 / 300 |
| `ODYRAG_HTTP_TIMEOUT_S` | timeout des appels HTTP | 8 |
| `ODYRAG_EMBED_COS_THRESHOLD` | cosinus minimal sonde vs vecteur étalon | 0.98 |
| `ODYRAG_CERT_MEDIAN_MIN` / `ODYRAG_CERT_MIN_FLOOR` | critères de certification d'un index | 0.80 / 0.50 |

Valeurs mesurées derrière ces seuils (même embedder, un texte par
appel : 1.0000 ; même embedder en lots de 8 : médiane 0.91, pire 0.756 ; autre embedder de
même dimension : autour de 0).

### Registre (`<ODYRAG_STATE_DIR>/registry.json`)

Un objet par corpus : `label`, `instance` (`url`, `launchd_label`, `plist`, `workdir`,
`public_url` optionnel), `llm`, `llm_endpoint` (optionnel, posé au premier changement),
`embedder` (`url`, `model`, `dim`), `indexed_with` (`embedder` = identité de l'espace,
`llm_eras` = historique). `instance.url` est l'adresse vue depuis l'hôte ; le navigateur
remplace un nom de boucle locale par le nom d'hôte de la page, sauf si `public_url` dit
explicitement comment joindre l'instance. Écriture atomique. Le registre de production est
semé depuis `deploy/registry.prod.json` (fichier local à l'opérateur, hors dépôt ; modèle :
[deploy/registry.example.json](deploy/registry.example.json)) une seule fois et n'est
jamais écrasé par un déploiement.

## API

| Route | |
|---|---|
| `GET /health` | version du service |
| `GET /` , `/static/*` | l'interface |
| `GET /api/overview` | état vivant de chaque corpus : instance, modèle, endpoint et modèles prêts, embedder, ères |
| `GET /api/jobs`, `/api/jobs/{id}` | suivi des changements |
| `GET /api/actions` | journal d'actions |
| `POST /api/corpora/{id}/llm` `{"model", "endpoint", "force"}` | changer le LLM et, si `endpoint` est donné, son endpoint (202 + `job_id`) |
| `POST /api/endpoint/models` `{"url"}` | lister les modèles publiés par un endpoint (jeton requis) |
| `POST /api/corpora/{id}/embedder` `{"url", "model"}` | changer l'embedder (202) |
| `POST /api/corpora/{id}/certify` | certifier l'index (202) |
| `POST /api/corpora/{id}/start` | recharger une instance arrêtée (202) |

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # aucun réseau, aucun launchd
.venv/bin/python -m tests.dev_server   # interface de démo sur 127.0.0.1:8780, monde simulé (jeton : dev)
```

Les tests tournent contre un simulateur (`tests/fakes.py`) de launchd, de LightRAG, de
l'embedder et du moteur, branché sur `httpx.MockTransport`. Le serveur de démo expose des
boutons de panne (`/dev/world`) pour voir le rollback, le refus « busy » ou la dérive, et
un faux LightRAG (`/dev/lightrag`) pour que les sections Documents et Requête fonctionnent
sans instance réelle.

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

0.2.0 : console complète (documents, file, requête, modèles, endpoint, embedder,
certification, journal). Restent à cadrer : modèles séparés par rôle (exige LightRAG 1.5),
suppression vérifiée de documents, graphe, veille.

## Licence

Coeos-RAG est distribué sous licence [Apache 2.0](LICENSE). Voir aussi [NOTICE](NOTICE).

Il pilote des instances de [LightRAG](https://github.com/HKUDS/LightRAG) (© HKUDS, licence
MIT), installées séparément et non redistribuées ici.

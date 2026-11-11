# AGENTS.md : installer et exploiter Coeos-RAG

Guide pour un agent (ou une personne) qui doit installer, mettre à jour ou étendre
Coeos-RAG sans le contexte de ceux qui l'ont construit. L'usage est décrit dans
[README.md](README.md).

## Ce que tu installes

Trois pièces, sur **une même machine** (l'hôte RAG) :

| Pièce | Rôle | Forme |
|---|---|---|
| Un endpoint d'embeddings | calcule les vecteurs | service compatible OpenAI (`/v1/embeddings`), déjà en place avant toi |
| Une instance LightRAG par corpus | stocke, indexe, répond | process hôte, un LaunchAgent par instance |
| Coeos-RAG (ce dépôt) | l'interface et le plan de contrôle | process hôte, LaunchAgent `eu.odyssai.odyrag` |

Il faut aussi un endpoint LLM compatible OpenAI (`/v1/models`, `/v1/chat/completions`),
joignable depuis l'hôte. Coeos-RAG ne fait que le lire.

Rien ne tourne en container : Coeos-RAG lit et écrit les plists des instances et appelle
`launchctl`.

## Plateformes

**Coeos-RAG est réservé à macOS.** Sous Linux et Windows, utilise LightRAG directement :
son serveur embarque sa propre interface web (sur le port de l'instance) et son API, qui
couvrent l'injection de documents, le suivi du traitement, la requête et le graphe.

| Pièce | macOS | Linux | Windows |
|---|---|---|---|
| Endpoints LLM et embeddings | oui | oui | oui |
| LightRAG (serveur, interface web, API) | oui | oui | oui |
| Coeos-RAG (ce dépôt) | oui | non | non |

La raison : Coeos-RAG change un réglage en éditant le plist launchd d'une instance et en
la redémarrant avec `launchctl`, et il doit tourner sur la machine des instances qu'il
pilote. Hors macOS, changer de modèle, d'endpoint ou d'embedder se fait à la main dans la
configuration de l'instance LightRAG (son `.env` ou son environnement), suivi d'un
redémarrage, **jamais pendant une extraction**.

N'essaie pas d'installer ce dépôt sur un hôte Linux ou Windows : sans `launchctl` son
interface ne s'affiche pas. La suite de ce guide suppose macOS.

## Règles à ne jamais enfreindre

- **Jamais de redémarrage d'une instance pendant une extraction** (`pipeline_status.busy`).
  Le store de LightRAG est réécrit en entier, sans atomicité : un arrêt au mauvais moment
  le tronque. Coeos-RAG refuse de lui-même ; ne le contourne pas à la main.
- **Sauvegarde avant toute mutation** d'un répertoire d'instance (`tar` du répertoire), et
  **vérification après** (recompter, relister). Une suppression qui « réussit » sans effet
  s'est déjà produite.
- **Ne modifie pas `PROBE_TEXT`** dans `coeos_rag/embedder.py`. Les identités d'index
  enregistrées en production ont été calculées sur cette phrase exacte.
- **Les identifiants techniques gardent l'ancien nom** : variables `ODYRAG_*`, dossier
  d'état `~/.odyrag`, dossier d'installation `~/odyrag`, label `eu.odyssai.odyrag`,
  suffixe `.bak-odyrag-`. Ne les renomme pas : registre, jeton et sauvegardes en dépendent.
- **Aucune écriture sur le moteur d'inférence.** On le lit, c'est tout.
- **Aucune adresse en dur dans le code.** Tout vient de l'environnement du service ou du
  registre.
- Le registre et le jeton de l'hôte ne sont **jamais** écrasés par un déploiement.

## Prérequis

Sur l'hôte RAG :
- macOS, une session utilisateur ouverte : les LaunchAgents vivent dans `gui/<uid>` ;
- `/opt/homebrew/bin/python3.11` (le script de déploiement crée le venv avec) ;
- `curl`, `rsync`, `openssl` ;
- accès SSH par clé depuis le poste qui déploie.

Sur le poste qui déploie : ce dépôt, `ssh`, `rsync`.

## 1. Installer une instance LightRAG (une par corpus)

À faire une fois par corpus, sur l'hôte. Version en service : **`lightrag-hku` 1.4.16**.

```bash
python3.11 -m venv ~/lightrag-venv
```

```bash
~/lightrag-venv/bin/pip install "lightrag-hku[api]==1.4.16"
```

Crée le répertoire du corpus (un par corpus, jamais partagé) avec un sous-dossier
`inputs/`. Puis un LaunchAgent `~/Library/LaunchAgents/<label>.plist` :

| Clé | Valeur |
|---|---|
| `Label` | un label unique, par exemple `eu.odyssai.lightrag-<corpus>` |
| `ProgramArguments` | `<venv>/bin/python3 -m lightrag.api.lightrag_server --host 0.0.0.0 --port <port> --llm-binding openai --embedding-binding openai` |
| `WorkingDirectory` | le répertoire du corpus |
| `KeepAlive`, `RunAtLoad` | `true` |
| `EnvironmentVariables` | `WORKING_DIR`, `LLM_MODEL`, `LLM_BINDING_HOST`, `LLM_BINDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_BINDING_HOST`, `EMBEDDING_BINDING_API_KEY`, `EMBEDDING_DIM` |

Écris le plist avec `plistlib`, pas à la main. `--host 0.0.0.0` est nécessaire :
l'interface appelle l'instance depuis le navigateur de l'opérateur.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
```

```bash
curl -s http://localhost:<port>/health
```

Attendu : `"status":"healthy"`, et dans `configuration` le modèle, l'endpoint et
l'embedder que tu as posés.

**Pièges**
- LightRAG ne lit son environnement **qu'au démarrage**. Changer un réglage, c'est changer
  le plist puis redémarrer : c'est exactement ce que fait Coeos-RAG, laisse-le faire.
- La dimension d'un embedder n'est pas son identité : deux embedders de 1024 dimensions
  produisent deux espaces sans rapport, sans aucune erreur. Un index ne se lit qu'avec
  l'embedder qui l'a construit.
- LightRAG 1.4.16 n'a qu'**un** LLM par instance, pour l'extraction et pour la requête.
  Les modèles par rôle arrivent en 1.5.0.
- LightRAG dédoublonne sur le contenu strictement identique. Un émetteur qui renvoie des
  variantes d'un même texte remplit le corpus de répétitions : un article = un document,
  dans sa dernière version.

## 2. Installer ou mettre à jour Coeos-RAG

Depuis le poste, à la racine du dépôt :

```bash
ODYRAG_PORT=<port> ODYRAG_ENGINE_URL=<API du moteur vue depuis l'hôte, sans /v1> deploy/deploy.sh <user@hôte>
```

Le script est idempotent. Il :
1. refuse de déployer si un changement est en cours sur l'hôte ;
2. copie le code dans `~/odyrag/app`, crée `~/odyrag/venv`, installe `requirements.txt` ;
3. crée le jeton d'administration une seule fois (`~/.odyrag/admin-token`, mode 0600,
   jamais affiché) ;
4. sème le registre une seule fois depuis `deploy/registry.prod.json`, s'il existe ;
5. écrit le LaunchAgent, le recharge, et **échoue** si `/health` ne rapporte pas la
   version qui vient d'être déployée.

`ODYRAG_BIND` (optionnel, `0.0.0.0` par défaut) fixe l'interface d'écoute.

**Première installation sur un nouvel hôte** : le registre décrit les corpus d'un hôte
précis, il n'est donc pas dans le dépôt. Copie `deploy/registry.example.json` en
`deploy/registry.prod.json` (ignoré par git), adapte-le **avant** le premier déploiement
(ou édite `~/.odyrag/registry.json` sur l'hôte après), un objet par corpus :

```json
{
  "label": "Nom affiché",
  "instance": {
    "url": "http://localhost:<port de l'instance>",
    "launchd_label": "<label du LaunchAgent>",
    "plist": "~/Library/LaunchAgents/<label>.plist",
    "workdir": "<répertoire du corpus>"
  },
  "llm": "<LLM_MODEL du plist>",
  "embedder": {"url": "<EMBEDDING_BINDING_HOST>", "model": "<EMBEDDING_MODEL>", "dim": 1024},
  "indexed_with": {"embedder": {"dim": 1024}, "llm_eras": []}
}
```

`instance.url` est l'adresse vue **depuis l'hôte**. Le navigateur remplace un nom de
boucle locale par le nom d'hôte de la page ; si l'instance se joint autrement, ajoute
`instance.public_url`.

## 3. Vérifier (bloquant : ne dis jamais « installé » sans ça)

```bash
curl -s http://<hôte>:<port>/health
```

`version` doit être celle de `coeos_rag/__init__.py`.

```bash
curl -s http://<hôte>:<port>/api/overview
```

Pour chaque corpus : `instance.up` vrai, `llm.ready` vrai, `engine.reachable` vrai,
`embedder.alive` vrai.

Puis dans un navigateur, `http://<hôte>:<port>/` : la section Documents doit lister les
documents du corpus. Si elle affiche « Instance injoignable depuis ce navigateur »,
l'instance n'écoute pas sur le réseau ou `public_url` manque. Le navigateur intégré de
certaines applications bloque cet appel direct : vérifie dans un navigateur ordinaire.

Dernière étape d'une installation : dans **Modèles**, « Certifier l'index » pour chaque
corpus qui contient déjà des documents. Tant qu'un index n'est pas certifié, son embedder
ne peut pas être changé. Un corpus vide ne se certifie pas : fais-le après la première
alimentation.

## 4. Alimenter un corpus

- À la main : section **Documents** (fichiers, texte collé, scan du dossier `inputs/`).
- Par programme : l'API de l'instance LightRAG directement, `POST /documents/text`
  `{"text", "file_source"}` ou `POST /documents/upload` (multipart, champ `file`). Donne
  toujours un `file_source` stable et lisible.

Coeos-RAG n'est pas dans ce chemin : il observe.

## 5. Opérations courantes

| Besoin | Geste |
|---|---|
| Changer de modèle ou d'endpoint LLM | section **Modèles** : saisir l'endpoint, « Lister les modèles », choisir, « Appliquer » |
| Le serveur de modèles a changé d'adresse | idem : seul l'endpoint change, le modèle reste |
| Changer d'embedder | section **Modèles**, accepté seulement s'il reproduit l'espace de l'index |
| Savoir ce qui a été changé | section **Journal** |
| Lire le jeton | sur l'hôte, `~/.odyrag/admin-token` ; ne le copie dans aucun fichier du dépôt |
| Remettre un corpus à zéro | arrêter l'instance, **renommer** son répertoire (ne rien effacer), en recréer un vide avec le même `.env` et `inputs/`, redémarrer, remettre à zéro `indexed_with` de ce corpus dans le registre |
| Revenir à la version précédente | `git checkout <sha>` puis le même `deploy/deploy.sh` |

## 6. Développer

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m tests.dev_server
```

Le serveur de démo écoute sur `127.0.0.1:8780` (jeton : `dev`) : monde simulé, faux
LightRAG compris, rien de réel n'est touché. `tests/e2e_real_host.py` pilote une instance
LightRAG **jetable** sur un hôte réel ; il ne touche jamais une instance de production.

Avant de livrer : tests verts, version montée dans `coeos_rag/__init__.py` pour tout
changement de comportement, commit au format Conventional Commits, puis déploiement dans
la même session : ce qui tourne sur l'hôte doit être ce qui est sur `main`.

## Carte du code

| Fichier | Rôle |
|---|---|
| `coeos_rag/app.py` | routes HTTP, jeton, jobs en arrière-plan, fichiers statiques |
| `coeos_rag/controller.py` | appliquer un changement : sauvegarde, redémarrage, vérification, rollback |
| `coeos_rag/plist.py` | édition sûre d'un plist (liste blanche de clés, écriture atomique) |
| `coeos_rag/registry.py` | le registre des corpus (`~/.odyrag/registry.json`) |
| `coeos_rag/engine.py` | lecture des modèles publiés par un endpoint |
| `coeos_rag/embedder.py`, `certify.py` | identité d'un embedder, certification d'un index |
| `coeos_rag/static/` | l'interface : `index.html`, `app.css`, `app.js`, sans build ni dépendance |
| `deploy/deploy.sh` | installation et mise à jour sur l'hôte |
| `tests/fakes.py` | le simulateur (launchd, LightRAG, embedder, moteur) |

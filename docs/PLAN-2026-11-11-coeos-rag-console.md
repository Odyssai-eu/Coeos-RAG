# PLAN worker — Coeos-Rag : console complète de gestion du RAG (ex-OdyRAG, plan de contrôle + UI)

- **Pour** : deux agents worker (Haiku), en parallèle, sans le contexte de la session qui a produit ce plan.
  - **Worker A (backend)** : WU-A1 à WU-A7. Ne touche à AUCUN fichier de `odyrag/static/`.
  - **Worker B (interface)** : WU-B1 à WU-B4. Ne touche QU'À `odyrag/static/`.
- **Repo** : `/Users/sophie/Claude/code/odyssai-rag` (branche `main`).
- **Fichiers** (liste exhaustive) :
  - Worker A : `odyrag/__init__.py`, `odyrag/plist.py`, `odyrag/registry.py`, `odyrag/engine.py`,
    `odyrag/embedder.py`, `odyrag/controller.py`, `odyrag/app.py`, `tests/fakes.py`,
    `tests/dev_server.py`, `tests/test_plist.py`, `tests/test_registry.py`,
    `tests/test_controller.py`, `tests/test_app.py`.
  - Worker B : `odyrag/static/index.html` (nouveau), `odyrag/static/app.css` (nouveau),
    `odyrag/static/app.js` (nouveau), `odyrag/static/dashboard.html` (à supprimer avec `rm`).
- **Live** : version 0.1.0 sur l'hôte RAG, port 8801. Cible de ce plan : **0.2.0**.
- **Complexité** : 8 points (A = 5, B = 5, recouvrement d'intégration fait par le coordinateur).

> **Contraintes dures (verbatim, non négociables)**
>
> - « Pas de workaround, JAMAIS. On répare la racine. »
> - « Aucune adresse, hôte ni port en dur dans le code : tout vient de l'environnement du
>   service ou du registre. » (README du repo.) Les seuls littéraux d'adresse autorisés sont
>   dans `tests/`.
> - « tu ne peux rien modifier sur odyssai » : le moteur d'inférence OdyssAI-X n'est jamais
>   modifié, jamais redémarré. On ne fait que des `GET` sur son API publique.
> - **Aucun commit, aucun `git add`, aucun `git checkout`, aucun push, aucun déploiement,
>   aucun `ssh`.** Le coordinateur relit, committe et déploie. Tu travailles dans l'arbre de
>   travail et tu t'arrêtes.
> - **Aucun appel réseau vers `192.168.*`** : tout se teste contre le simulateur
>   (`tests/fakes.py`, `python -m tests.dev_server`).
> - Zéro octet externe dans l'UI : pas de CDN, pas de Google Fonts, pas de librairie JS, pas
>   de build. HTML, CSS et JS natifs uniquement.
> - Pas d'emoji dans le code ni dans l'UI. Pas de tiret cadratin (`—`) ni demi-cadratin (`–`)
>   dans aucun texte visible.
> - Le problème est déjà diagnostiqué : ne ré-enquête pas, ne change pas d'architecture.
> - Si une étape est impossible telle qu'écrite, ARRÊTE-TOI et dis-le dans ton rapport.
>   N'improvise pas un contournement.

## Le problème (déjà diagnostiqué — ne pas re-enquêter)

1. **L'interface ne couvre que les modèles.** `odyrag/static/dashboard.html` (330 lignes)
   n'a ni injection de documents, ni liste de documents, ni requête. Les instances LightRAG
   exposent pourtant toute l'API nécessaire (voir « Contrat LightRAG » plus bas).
2. **L'endpoint du LLM n'est pas modifiable.** `odyrag/plist.py:17-19` : `MUTABLE_ENV_KEYS`
   ne contient pas `LLM_BINDING_HOST`. `odyrag/controller.py:231-236` n'écrit que
   `LLM_MODEL`. Quand le serveur de modèles change d'adresse, il faut éditer le plist à la
   main.
3. **La vérification « modèle prêt » regarde une URL globale figée au déploiement**
   (`odyrag/config.py:53`, `ODYRAG_ENGINE_URL`), pas l'endpoint que l'instance appelle
   réellement (`LLM_BINDING_HOST` de son plist, exposé par son `/health` sous
   `configuration.llm_binding_host`, par exemple `http://localhost:8000/v1`).
4. **Le produit s'appelle désormais Coeos-Rag** et doit porter le logo Coeos.

**Décision d'architecture (prise, ne pas rouvrir)** : une seule interface servie par ce
service. Le **navigateur parle directement aux instances LightRAG** pour les documents, la
file et la requête. Ce service ne garde que ce que LightRAG ne sait pas faire : changer
modèle et endpoint, changer l'embedder, certifier l'index, tenir le journal. **Aucun proxy
vers LightRAG n'est ajouté à ce service.** Faits vérifiés le 2026-11-11 sur les instances
réelles : CORS ouvert à toute origine, authentification LightRAG désactivée, version 1.4.16.

**Fait vérifié à ne pas contourner** : LightRAG 1.4.16 n'a qu'**un seul** LLM par instance,
utilisé pour l'extraction et pour la requête. Les rôles séparés n'existent qu'à partir de
LightRAG 1.5.0. L'UI affiche donc un seul modèle et le dit. Pas de faux sélecteur par rôle.

## Objectif (done = ces vérités observables)

- `.venv/bin/python -m pytest -q` : tous les tests passent (les 75 existants + les nouveaux).
- `POST /api/corpora/{id}/llm` accepte `{"model", "endpoint", "force"}` ; avec un `endpoint`
  différent de celui en service, le plist reçoit `LLM_BINDING_HOST`, l'instance redémarre,
  la vérification porte sur les deux valeurs, le rollback restaure les deux.
- `POST /api/endpoint/models` (jeton requis) liste les modèles publiés par un endpoint donné.
- `GET /api/overview` porte, par corpus, un bloc `engine` (endpoint + modèles prêts) et
  `instance.public_url` / `instance.version`.
- `GET /` sert `odyrag/static/index.html` ; `/static/app.css` et `/static/app.js` sont servis.
- L'UI a cinq sections : Vue d'ensemble, Documents, Requête, Modèles, Journal ; logo Coeos
  en en-tête ; thème clair et sombre suivant le système.

**Vérif** (worker A) : `.venv/bin/python -m pytest -q` puis
`.venv/bin/python -m tests.dev_server &` et
`curl -s http://127.0.0.1:8780/api/overview | python3 -m json.tool | head -60`,
`curl -s http://127.0.0.1:8780/dev/lightrag/documents/status_counts`. Tue le serveur ensuite.
**Vérif** (worker B) : `node --check odyrag/static/app.js` sort sans erreur ;
`grep -c "—\|–" odyrag/static/*` renvoie 0 pour chaque fichier ; `grep -n "Coeos-Rag" odyrag/static/index.html`
et `grep -n '/static/app.js?v=__VERSION__' odyrag/static/index.html` trouvent chacun une ligne.

## Contexte code (lu le 2026-11-11, lignes indicatives — REVÉRIFIE-les avant d'éditer)

| Symbole | Où | Rôle |
|---|---|---|
| `MUTABLE_ENV_KEYS` | `odyrag/plist.py:17` | liste blanche des clés du plist modifiables |
| `Instance`, `Corpus` | `odyrag/registry.py:55`, `:68` | modèles pydantic du registre (fichier JSON de prod existant : tout nouveau champ DOIT être optionnel) |
| `EngineClient` | `odyrag/engine.py:21` | lit `GET {base_url}/v1/models`, cache TTL ; `base_url` est SANS `/v1` |
| `validate_embedder_url` | `odyrag/embedder.py:44` | refuse toute URL qui ne résout pas vers une adresse privée, loopback ou link-local |
| `PROBE_TEXT` | `odyrag/embedder.py:24` | phrase étalon de l'embedder. **NE PAS MODIFIER** (voir Non-buts) |
| `Controller._apply` | `odyrag/controller.py:119` | backup plist, restart, vérif `/health`, rollback, registre |
| `Controller.apply_llm` | `odyrag/controller.py:211` | change `LLM_MODEL` |
| `Controller._corpus_view` | `odyrag/controller.py:357` | vue d'un corpus pour `/api/overview` |
| `Controller.overview` | `odyrag/controller.py:405` | bloc `engine` global + liste des corpus |
| `create_app` | `odyrag/app.py:43` | routes ; `DASHBOARD` (`:31`) pointe sur `static/dashboard.html` |
| `require_admin` | `odyrag/app.py:71` | dépendance FastAPI du jeton Bearer |
| `FakeWorld`, `write_plist`, `build` | `tests/fakes.py:72`, `:50`, `:203` | simulateur launchd + LightRAG + embedder + moteur |
| test d'égalité stricte | `tests/test_controller.py:309` | `assert c["llm"] == {"configured":…, "live":…, "in_sync":…, "ready":…}` : **n'ajoute AUCUNE clé au dict `llm`** |

## Contrat d'API de ce service (cible 0.2.0) — les deux workers s'y tiennent à la lettre

Routes inchangées : `GET /health`, `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/actions`,
`POST /api/corpora/{id}/embedder {url, model}`, `POST /api/corpora/{id}/certify`,
`POST /api/corpora/{id}/start`. Toutes les routes `POST` exigent
`Authorization: Bearer <jeton>` ; 401 = jeton faux ou absent, 403 = aucun jeton configuré.
Les `POST /api/corpora/...` répondent 202 `{"job_id", "status_url"}` ; on suit le job par
`GET /api/jobs/{id}` → `{id, corpus, kind, status: "running"|"done"|"failed", started,
steps: [{t, step, ok, detail}], result, error}`.

`GET /api/actions` → liste `[{ts, corpus, kind, status: "done"|"failed", error}]`, plus récent d'abord.

`POST /api/corpora/{id}/llm` — corps `{"model": str, "endpoint": str|null (optionnel), "force": bool (défaut false)}`.

`POST /api/endpoint/models` — corps `{"url": str}` → 200
`{"url": str, "reachable": bool, "models": [{"id": str, "ready": bool}]}` ; 422 si l'URL
n'est pas privée ; 401/403 comme les autres mutations. Réponse synchrone (pas de job).

`GET /api/overview` → 
```json
{
  "engine": {"configured": true, "reachable": true, "ready_models": ["a", "b"]},
  "mutations_enabled": true,
  "corpora": [{
    "id": "alpha", "label": "Alpha",
    "instance": {"url": "http://localhost:8766", "public_url": null, "launchd_label": "…",
                 "up": true, "loaded": true, "busy": false,
                 "counts": {"pending": 0, "processing": 0, "preprocessed": 0, "processed": 3, "failed": 0, "all": 3},
                 "version": "1.4.16"},
    "llm": {"configured": "m", "live": "m", "in_sync": true, "ready": true},
    "engine": {"endpoint": {"configured": null, "live": "http://localhost:8000/v1", "effective": "http://localhost:8000/v1"},
               "reachable": true, "ready_models": ["a", "b"]},
    "embedder": {"configured": {"url": "…", "model": "…", "dim": 1024},
                 "live": {"url": "…", "model": "…"}, "alive": true, "latency_ms": 12, "dim": 1024,
                 "weights": "…", "error": null, "cos_vs_index": 1.0, "same_space": true},
    "indexed_with": {"embedder": {"weights": "…", "dim": 1024,
                                  "certified": {"method": "…", "samples": 24, "min_cos": 1.0, "median_cos": 1.0, "at": "…"}},
                     "llm_eras": [{"model": "…", "start": "…", "end": null, "source": "…", "docs": 12}]}
  }]
}
```
`counts`, `version`, `certified`, `public_url`, `ready_models`, `cos_vs_index`, `same_space`,
`llm.ready`, `llm.in_sync`, `llm.live` peuvent valoir `null`. Le bloc `engine` de premier
niveau est conservé tel quel (hérité) ; **l'UI n'utilise que le bloc `engine` par corpus**.

## Contrat LightRAG 1.4.16 (lu sur l'openapi réel) — utilisé par le navigateur et par le faux de démo

Base d'une instance, côté navigateur : voir la fonction `lrBase` en WU-B3.

| Appel | Corps | Réponse |
|---|---|---|
| `GET /health` | | `{status: "healthy", core_version, pipeline_busy, configuration: {llm_model, llm_binding_host, embedding_model, embedding_binding_host, enable_rerank, rerank_model, max_async, max_parallel_insert, summary_language, …}}` |
| `GET /documents/status_counts` | | `{status_counts: {pending, processing, preprocessed, processed, failed, all}}` |
| `GET /documents/pipeline_status` | | `{busy, job_name, job_start, docs, batchs, cur_batch, request_pending, latest_message, history_messages: [str]}` |
| `POST /documents/paginated` | `{status_filter: null\|"pending"\|"processing"\|"preprocessed"\|"processed"\|"failed", page, page_size, sort_field: "updated_at", sort_direction: "desc"}` | `{documents: [{id, content_summary, content_length, status, created_at, updated_at, track_id, chunks_count, error_msg, metadata, file_path}], pagination: {page, page_size, total_count, total_pages, has_next, has_prev}, status_counts: {…}}` |
| `POST /documents/text` | `{text, file_source}` | `{status, message, track_id}` |
| `POST /documents/upload` | `multipart/form-data`, champ **`file`** | `{status, message, track_id}` |
| `POST /documents/scan` | aucun | `{status, message, track_id}` |
| `POST /documents/reprocess_failed` | aucun | `{status, message, track_id}` |
| `POST /documents/cancel_pipeline` | aucun | `{status, message}` |
| `POST /query` | `{query, mode: "mix"\|"hybrid"\|"local"\|"global"\|"naive", top_k, only_need_context, include_references: true}` | `{response: str, references: [{reference_id, file_path}]\|null}` |

`status` d'une insertion vaut typiquement `"success"`, `"duplicated"` ou `"failure"` : affiche
toujours `message` tel quel.

---

# Work units — Worker A (backend)

Fais-les dans l'ordre. Lance `.venv/bin/python -m pytest -q` après chaque WU.

## WU-A1 — `LLM_BINDING_HOST` devient modifiable

1. `odyrag/plist.py` : ajoute `"LLM_BINDING_HOST"` à `MUTABLE_ENV_KEYS`.
2. `tests/test_plist.py` : ajoute un test `test_llm_binding_host_is_mutable` qui applique
   `{"LLM_BINDING_HOST": "http://h2/v1"}` avec `P.apply_env` et relit la valeur avec
   `P.read_env`.

**Ne touche pas** au reste de `plist.py` (backup, écriture atomique, `_check_value`).

## WU-A2 — deux champs optionnels dans le registre

`odyrag/registry.py` :
```python
class Instance(BaseModel):
    url: str
    launchd_label: str
    plist: str
    workdir: str
    public_url: Optional[str] = None  # URL of the instance as a browser reaches it; None = derive from `url`

class Corpus(BaseModel):
    label: str
    instance: Instance
    llm: str
    llm_endpoint: Optional[str] = None  # LLM_BINDING_HOST last applied (OpenAI-compatible base, usually ends in /v1)
    embedder: EmbedderConfig
    indexed_with: IndexedWith
```
`tests/test_registry.py` : ajoute un test qui charge `deploy/registry.prod.json` avec
`RegistryModel.model_validate_json(Path("deploy/registry.prod.json").read_text())` et vérifie
que `corpora["alpha"].llm_endpoint is None` et `corpora["alpha"].instance.public_url is None`.

**Piège** : ces champs DOIVENT rester optionnels avec défaut `None`. Le registre de
production existe déjà sur l'hôte et n'est jamais réécrit par un déploiement ; un champ
obligatoire empêcherait le service de démarrer.
**Ne modifie pas** `deploy/registry.prod.json`.

## WU-A3 — `EngineClient` par endpoint

`odyrag/engine.py`, remplace le corps de la classe par :
```python
class EngineClient:
    def __init__(self, client: httpx.AsyncClient, base_url: Optional[str], timeout: float = 8.0,
                 ttl_s: float = 5.0, clock=time.monotonic) -> None:
        self._c = client
        self.base_url = base_url
        self._timeout = timeout
        self._ttl = ttl_s
        self._clock = clock
        self._cache: dict[str, tuple[float, list[EngineModel]]] = {}

    @property
    def default_base(self) -> Optional[str]:
        """OpenAI-compatible base of the engine configured for the service, or None."""
        return f"{self.base_url}/v1" if self.base_url else None

    async def models_at(self, base: Optional[str]) -> Optional[list[EngineModel]]:
        """Models published at `base` (an OpenAI-compatible base URL such as http://host:8000/v1).
        None when `base` is empty or unreachable (callers must then refuse rather than guess)."""
        if not base:
            return None
        base = base.rstrip("/")
        now = self._clock()
        hit = self._cache.get(base)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            r = await self._c.get(f"{base}/models", timeout=self._timeout)
            r.raise_for_status()
            data = r.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None
        models: list[EngineModel] = []
        for m in data:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                continue
            x = m.get("x_odyssai") if isinstance(m.get("x_odyssai"), dict) else {}
            models.append(EngineModel(id=m["id"], kind=x.get("kind"), ready=x.get("ready", True) is not False))
        self._cache[base] = (now, models)
        return models

    async def ready_ids_at(self, base: Optional[str]) -> Optional[set[str]]:
        models = await self.models_at(base)
        return None if models is None else {m.id for m in models if m.ready}

    async def models(self) -> Optional[list[EngineModel]]:
        return await self.models_at(self.default_base)

    async def ready_ids(self) -> Optional[set[str]]:
        return await self.ready_ids_at(self.default_base)
```
**Pourquoi ça suffit** : `models()` et `ready_ids()` gardent leur comportement, donc le bloc
`engine` de premier niveau de l'overview ne change pas.

## WU-A4 — le simulateur dit la vérité sur l'endpoint

`tests/fakes.py` :
1. Sous `ENGINE = …`, ajoute `ENGINE2 = "http://127.0.0.1:9000"`.
2. `write_plist` : remplace `"LLM_BINDING_HOST": "http://localhost:8000/v1"` par
   `"LLM_BINDING_HOST": f"{ENGINE}/v1"`.
3. `FakeWorld.__init__` : ajoute
   ```python
   self.engine2_models: list[dict] | None = [
       {"id": "old-model", "x_odyssai": {"ready": True}},
       {"id": "moved-model", "x_odyssai": {"ready": True}},
   ]
   ```
4. `FakeWorld.handler` : avant le `raise httpx.ConnectError`, ajoute
   ```python
   if origin == ENGINE2:
       if self.engine2_models is None:
           raise httpx.ConnectError("engine2 down")
       if url.path == "/v1/models":
           return httpx.Response(200, json={"data": self.engine2_models})
       return httpx.Response(404)
   ```
5. `FakeWorld._instance`, réponse de `/health` : le dict devient
   ```python
   {"status": "healthy", "core_version": "1.4.16", "configuration": {
       "llm_model": env.get("LLM_MODEL"),
       "llm_binding_host": env.get("LLM_BINDING_HOST"),
       "embedding_binding_host": env.get("EMBEDDING_BINDING_HOST"),
       "embedding_model": env.get("EMBEDDING_MODEL"),
   }}
   ```

**Piège (trouvé en lisant le code)** : `handler` compare l'origine en chaîne
(`http://127.0.0.1:8000`). Si le plist simulé garde `http://localhost:8000/v1`, l'endpoint du
corpus ne correspond à aucune origine connue, la liste des modèles devient injoignable et
une dizaine de tests existants cassent. C'est pour ça que l'étape 2 est obligatoire.

## WU-A5 — le contrôleur : endpoint par corpus

`odyrag/embedder.py` : rends le validateur réutilisable sans changer son comportement.
Signature `def validate_embedder_url(url: str, resolver: Callable = socket.getaddrinfo, what: str = "embedder") -> None:`
et remplace le mot `embedder` en dur dans les trois messages par `{what}` (f-strings :
`f"{what} URL must be http(s) with a host"`, `f"{what} host does not resolve: {e}"`,
`f"{what} host does not resolve"`, `f"{what} host resolves to a public address ({a}); refused"`).
**Ne touche pas à `PROBE_TEXT`.**

`odyrag/controller.py` :

1. Ajoute cette méthode dans la section « helpers » :
   ```python
   def _llm_endpoint(self, corpus: Corpus, cfg: dict) -> Optional[str]:
       """The endpoint the instance calls for its LLM: what it reports live, else what the
       registry last applied, else the engine configured for the service."""
       ep = cfg.get("llm_binding_host") or corpus.llm_endpoint or self.engine.default_base
       return ep.rstrip("/") if ep else None
   ```
2. Remplace `apply_llm` en entier par :
   ```python
   async def apply_llm(self, corpus_id: str, model: str, log: Log, *, force: bool = False,
                       endpoint: Optional[str] = None) -> dict:
       corpus = self._corpus(corpus_id)
       model = (model or "").strip()
       if not model:
           raise ChangeError("empty model", status=422)
       endpoint = (endpoint or "").strip().rstrip("/") or None
       if endpoint is not None:
           try:
               emb.validate_embedder_url(endpoint, what="LLM endpoint")
           except ValueError as e:
               raise ChangeError(str(e), status=422) from e
       live = await self._lr(corpus).health()
       cfg = (live or {}).get("configuration") or {}
       live_llm = cfg.get("llm_model")
       live_ep = (cfg.get("llm_binding_host") or "").rstrip("/") or None
       target_ep = endpoint or self._llm_endpoint(corpus, cfg)
       ready = await self.engine.ready_ids_at(target_ep)
       if not force:
           if ready is None:
               raise ChangeError("cannot verify that the model is loaded (engine unreachable or not configured)")
           if model not in ready:
               raise ChangeError(f"model {model!r} is not loaded on the engine")
       endpoint_changes = endpoint is not None and endpoint != live_ep
       if live_llm == model and corpus.llm == model and not endpoint_changes:
           return {"changed": False, "corpus": corpus_id, "note": "already applied"}

       updates = {"LLM_MODEL": model}
       if endpoint_changes:
           updates["LLM_BINDING_HOST"] = endpoint

       def commit(c: Corpus, now: str) -> None:
           c.llm = model
           if target_ep:
               c.llm_endpoint = target_ep
           open_era(c, model, now, "Coeos-Rag settings")

       def check(c: dict) -> bool:
           if c.get("llm_model") != model:
               return False
           return (not endpoint_changes) or (c.get("llm_binding_host") or "").rstrip("/") == endpoint

       def describe(c: dict) -> str:
           text = f"llm_model={c.get('llm_model')!r}"
           if endpoint_changes:
               text += f", llm_binding_host={c.get('llm_binding_host')!r}"
           return text

       return await self._apply(corpus_id, updates, check=check, describe_live=describe, commit=commit, log=log)
   ```
3. Dans `_apply`, la clé `"live"` du dict retourné devient
   `{k: cfg.get(k) for k in ("llm_model", "llm_binding_host", "embedding_binding_host", "embedding_model")}`.
4. Ajoute cette méthode publique (après `start`) :
   ```python
   async def probe_endpoint(self, url: str) -> dict:
       """List the models an OpenAI-compatible endpoint publishes (read-only)."""
       url = (url or "").strip().rstrip("/")
       try:
           emb.validate_embedder_url(url, what="LLM endpoint")
       except ValueError as e:
           raise ChangeError(str(e), status=422) from e
       models = await self.engine.models_at(url)
       return {"url": url, "reachable": models is not None,
               "models": [{"id": m.id, "ready": m.ready} for m in (models or [])]}
   ```
5. `_corpus_view` : retire le paramètre `ready` de la signature (elle devient
   `_corpus_view(self, corpus_id: str, corpus: Corpus) -> dict`). Juste après la ligne
   `cfg = (health or {}).get("configuration") or {}`, ajoute :
   ```python
   ep = self._llm_endpoint(corpus, cfg)
   ready = await self.engine.ready_ids_at(ep)
   ```
   Dans le dict retourné : le bloc `"instance"` gagne
   `"public_url": corpus.instance.public_url` et `"version": (health or {}).get("core_version")` ;
   le bloc `"llm"` reste **exactement** `{"configured", "live", "in_sync", "ready"}` ; ajoute
   au même niveau que `"llm"` :
   ```python
   "engine": {
       "endpoint": {"configured": corpus.llm_endpoint,
                    "live": (cfg.get("llm_binding_host") or None), "effective": ep},
       "reachable": ready is not None,
       "ready_models": sorted(ready) if ready is not None else None,
   },
   ```
6. `overview` : l'appel devient `self._corpus_view(cid, c)`. Le reste (bloc `engine` global
   calculé par `self.engine.models()`) ne change pas.

**Piège** : `tests/test_controller.py:309` compare `c["llm"]` par égalité stricte de dict.
N'ajoute rien dans `llm` ; tout ce qui est nouveau va dans `engine` (par corpus).
**Pourquoi ça suffit** : un changement d'endpoint réutilise tout `_apply` (backup, refus si
occupé, redémarrage, vérification, rollback). Aucune logique de redémarrage n'est dupliquée.

Tests à ajouter dans `tests/test_controller.py` (importe `ENGINE, ENGINE2` depuis `tests.fakes`) :
- `test_apply_llm_moves_to_another_endpoint` : `apply_llm("alpha", "moved-model", log, endpoint=f"{ENGINE2}/v1")`
  → `res["changed"]`, `plist_env(tmp_path)["LLM_BINDING_HOST"] == f"{ENGINE2}/v1"`,
  `plist_env(tmp_path)["LLM_MODEL"] == "moved-model"`,
  `reg.corpus("alpha").llm_endpoint == f"{ENGINE2}/v1"`,
  `res["live"]["llm_binding_host"] == f"{ENGINE2}/v1"`.
- `test_apply_llm_endpoint_only_change_keeps_the_model` : `apply_llm("alpha", "old-model", log, endpoint=f"{ENGINE2}/v1")`
  → `changed`, `LLM_MODEL == "old-model"`, `LLM_BINDING_HOST == f"{ENGINE2}/v1"`.
- `test_apply_llm_checks_readiness_on_the_new_endpoint` : `apply_llm("alpha", "new-model", log, endpoint=f"{ENGINE2}/v1")`
  lève `ChangeError` avec `match="not loaded"` (`new-model` n'existe que sur `ENGINE`) et
  `"bootout" not in world.calls`.
- `test_apply_llm_refuses_a_public_endpoint` : `endpoint="http://8.8.8.8:1/v1"` → `ChangeError`, `e.value.status == 422`.
- `test_endpoint_change_is_rolled_back_with_the_model` : `world.fail_bootstrap = 1`, même appel que le premier test →
  `ChangeError` avec `rolled_back is True`, puis `plist_env(tmp_path)["LLM_BINDING_HOST"] == f"{ENGINE}/v1"` et `LLM_MODEL == "old-model"`.
- `test_overview_reports_the_engine_of_each_corpus` : `c = (await ctl.overview())["corpora"][0]` →
  `c["engine"] == {"endpoint": {"configured": None, "live": f"{ENGINE}/v1", "effective": f"{ENGINE}/v1"}, "reachable": True, "ready_models": ["new-model", "old-model"]}`,
  `c["instance"]["public_url"] is None`, `c["instance"]["version"] == "1.4.16"`.
- `test_probe_endpoint_lists_models_and_refuses_public_hosts` : `await ctl.probe_endpoint(f"{ENGINE2}/v1/")` →
  `reachable is True`, ids `{"old-model", "moved-model"}`, `url == f"{ENGINE2}/v1"` ; `world.engine2_models = None` (après
  expiration : le `build` utilise `ttl_s=0`) → `reachable is False` et `models == []` ; `"http://8.8.8.8:1/v1"` → `ChangeError` 422.

## WU-A6 — l'application : routes, statiques, version

`odyrag/__init__.py` : `__version__ = "0.2.0"`.

`odyrag/app.py` :
1. Import : `from fastapi.staticfiles import StaticFiles`.
2. Remplace `DASHBOARD = …` par
   ```python
   STATIC = Path(__file__).parent / "static"
   INDEX = STATIC / "index.html"
   ```
3. `class LlmChange` gagne `endpoint: Optional[str] = None`. Ajoute
   ```python
   class EndpointProbe(BaseModel):
       url: str
   ```
4. `FastAPI(title="Coeos-Rag control plane", …)`. Juste après la création de `app` :
   `app.mount("/static", StaticFiles(directory=STATIC), name="static")`.
5. La route `/` devient :
   ```python
   @app.get("/", response_class=HTMLResponse)
   async def dashboard() -> HTMLResponse:
       # Read fresh on each request: a UI-only change needs no restart. The version stamps
       # the asset URLs so a browser never runs a stale script after a deploy.
       return HTMLResponse(INDEX.read_text(encoding="utf-8").replace("__VERSION__", __version__))
   ```
6. `change_llm` passe l'endpoint :
   `lambda log: controller().apply_llm(corpus_id, body.model, log, force=body.force, endpoint=body.endpoint)`.
7. Nouvelle route, dans la section mutations :
   ```python
   @app.post("/api/endpoint/models", dependencies=[Depends(require_admin)])
   async def endpoint_models(body: EndpointProbe) -> dict:
       try:
           return await controller().probe_endpoint(body.url)
       except ChangeError as e:
           raise HTTPException(e.status, e.message) from None
   ```

**Piège** : `index.html` est écrit par le worker B, en parallèle. Tant qu'il n'existe pas,
`GET /` échoue. C'est attendu : voir le test ci-dessous.

`tests/test_app.py` :
- `test_health_and_dashboard_are_served` : la dernière assertion devient
  `assert page.status_code == 200 and "Coeos-Rag" in page.text and "/static/app.js?v=0.2.0" in page.text`
  et ajoute `assert (await c.get("/static/app.js")).status_code == 200`.
  **Ce test, et lui seul, peut échouer tant que le worker B n'a pas livré `index.html` et
  `app.js`.** Ne crée pas ces fichiers toi-même, n'affaiblis pas l'assertion : signale
  l'état dans ton rapport.
- Ajoute `test_llm_change_can_move_the_endpoint` : `POST /api/corpora/alpha/llm` avec
  `{"model": "moved-model", "endpoint": f"{ENGINE2}/v1"}` et `AUTH` → job `done`,
  `job["result"]["live"]["llm_binding_host"] == f"{ENGINE2}/v1"`.
- Ajoute `test_endpoint_probe_needs_the_token_and_lists_models` : sans jeton → 401 ; avec
  `AUTH` et `{"url": f"{ENGINE2}/v1"}` → 200 et `"moved-model"` dans les ids ; avec
  `{"url": "http://8.8.8.8:1/v1"}` → 422.

## WU-A7 — le serveur de démo sert un faux LightRAG au navigateur

But : que l'UI complète soit utilisable sur `python -m tests.dev_server` sans instance réelle.
Le navigateur appelle LightRAG en direct ; en démo, on lui donne un faux LightRAG monté sous
`/dev/lightrag` du même serveur, désigné par `instance.public_url`.

`tests/dev_server.py`, dans `main()` :
1. Calcule le port d'abord : `port = int(os.environ.get("ODYRAG_DEMO_PORT", "8780"))`.
2. Avant d'enregistrer le registre : `corpus.instance.public_url = f"http://127.0.0.1:{port}/dev/lightrag"`.
   Enregistre **deux** corpus qui partagent ce monde simulé, pour voir le sélecteur de
   corpus : `{"alpha": corpus, "company": corpus.model_copy(update={"label": "Company"})}`.
   Mets à jour la docstring du module : les deux corpus de démo partagent une seule instance
   simulée, un changement sur l'un se voit sur l'autre.
3. Remplace la chaîne `"OdyRAG settings"` par `"Coeos-Rag settings"`.
4. Ajoute un état en mémoire et un routeur FastAPI (`from fastapi import APIRouter, File, UploadFile`,
   `from datetime import datetime, timezone`) :
   ```python
   def _iso(day: int, hour: int = 10) -> str:
       return f"2026-10-{day:02d}T{hour:02d}:00:00+00:00"

   docs: list[dict] = []
   for i in range(34):
       status = "failed" if i % 9 == 4 else "processed"
       docs.append({
           "id": f"doc-{i:04d}", "status": status, "file_path": f"notes/dossier-{i:02d}.md",
           "content_summary": f"Compte rendu numero {i} : decisions, personnes et echeances du dossier.",
           "content_length": 1800 + 137 * i, "created_at": _iso(1 + i % 27), "updated_at": _iso(1 + i % 27, 11),
           "track_id": f"insert_demo_{i:04d}", "chunks_count": 0 if status == "failed" else 2 + i % 5,
           "error_msg": "Content already exists. Original doc_id: doc-0000, Status: processed" if status == "failed" else None,
           "metadata": {},
       })
   pipeline = {"busy": False, "job_name": "Default Job", "job_start": None, "docs": 0, "batchs": 0,
               "cur_batch": 0, "request_pending": False, "latest_message": "",
               "history_messages": []}

   def _counts() -> dict:
       out = {k: 0 for k in ("pending", "processing", "preprocessed", "processed", "failed")}
       for d in docs:
           out[d["status"]] += 1
       out["all"] = len(docs)
       return out

   def _insert(text: str, source: str) -> dict:
       now = datetime.now(timezone.utc).isoformat()
       n = len(docs)
       docs.append({"id": f"doc-{n:04d}", "status": "processed", "file_path": source or "unknown_source",
                    "content_summary": text[:120], "content_length": len(text), "created_at": now,
                    "updated_at": now, "track_id": f"insert_demo_{n:04d}", "chunks_count": 1 + len(text) // 1200,
                    "error_msg": None, "metadata": {}})
       pipeline.update(job_start=now, latest_message=f"Processed {source or 'text'}",
                       history_messages=(pipeline["history_messages"] + [f"Processed {source or 'text'}"])[-20:])
       return {"status": "success", "message": f"Document accepted: {source or 'text'}", "track_id": f"insert_demo_{n:04d}"}

   lr = APIRouter(prefix="/dev/lightrag")

   @lr.get("/health")
   async def lr_health():
       env = world.env
       return {"status": "healthy", "core_version": "1.4.16", "pipeline_busy": pipeline["busy"],
               "configuration": {"llm_model": env.get("LLM_MODEL"), "llm_binding_host": env.get("LLM_BINDING_HOST"),
                                 "embedding_model": env.get("EMBEDDING_MODEL"),
                                 "embedding_binding_host": env.get("EMBEDDING_BINDING_HOST"),
                                 "enable_rerank": False, "rerank_model": None, "max_async": 4,
                                 "max_parallel_insert": 2, "summary_language": "English"}}

   @lr.get("/documents/status_counts")
   async def lr_counts():
       return {"status_counts": _counts()}

   @lr.get("/documents/pipeline_status")
   async def lr_pipeline():
       return pipeline

   @lr.post("/documents/paginated")
   async def lr_paginated(body: dict = Body(default={})):
       status = body.get("status_filter")
       page, size = int(body.get("page") or 1), int(body.get("page_size") or 20)
       rows = sorted((d for d in docs if not status or d["status"] == status),
                     key=lambda d: d["updated_at"], reverse=body.get("sort_direction", "desc") == "desc")
       total = len(rows)
       pages = max(1, -(-total // size))
       return {"documents": rows[(page - 1) * size: page * size],
               "pagination": {"page": page, "page_size": size, "total_count": total, "total_pages": pages,
                              "has_next": page < pages, "has_prev": page > 1},
               "status_counts": _counts()}

   @lr.post("/documents/text")
   async def lr_text(body: dict = Body(...)):
       return _insert(str(body.get("text") or ""), str(body.get("file_source") or ""))

   @lr.post("/documents/upload")
   async def lr_upload(file: UploadFile = File(...)):
       raw = await file.read()
       return _insert(raw.decode("utf-8", "replace"), file.filename or "upload")

   @lr.post("/documents/scan")
   async def lr_scan():
       return {"status": "scanning_started", "message": "Scanning process has been initiated in the background", "track_id": "scan_demo"}

   @lr.post("/documents/reprocess_failed")
   async def lr_reprocess():
       return {"status": "reprocessing_started", "message": "Reprocessing of failed documents has been initiated in background", "track_id": "retry_demo"}

   @lr.post("/documents/cancel_pipeline")
   async def lr_cancel():
       pipeline["busy"] = False
       return {"status": "not_busy", "message": "Pipeline is not currently running. No cancellation needed."}

   @lr.post("/query")
   async def lr_query(body: dict = Body(...)):
       q = str(body.get("query") or "")
       return {"response": f"Reponse de demonstration ({body.get('mode', 'mix')}) a la question : {q}\n\nLe corpus simule contient {len(docs)} documents.",
               "references": [{"reference_id": "1", "file_path": docs[0]["file_path"]},
                              {"reference_id": "2", "file_path": docs[1]["file_path"]}]}

   app.include_router(lr)
   ```
   `app.include_router(lr)` se place après `app = create_app(...)`.
5. Dernière ligne : `uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")`.

**Piège** : `python-multipart` est requis par FastAPI pour `UploadFile`. Vérifie avec
`.venv/bin/python -c "import multipart"`. S'il manque, ajoute la ligne `python-multipart>=0.0.9`
à `requirements-dev.txt` (PAS à `requirements.txt` : le service de production n'en a pas
besoin) puis `.venv/bin/pip install -r requirements-dev.txt`.
**Ne touche pas** à `tests/e2e_real_host.py`.

---

# Work units — Worker B (interface)

Trois fichiers dans `odyrag/static/`. Pas de framework, pas de build, pas de ressource externe.
Langue de l'interface : **français**, avec les accents. Code et commentaires : anglais.

## WU-B1 — `index.html`

Squelette exact (complète seulement là où c'est indiqué) :
```html
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Coeos-Rag</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'%3E%3Crect x='3' y='3' width='114' height='114' rx='26' fill='%2358badd'/%3E%3Crect x='55' y='55' width='10' height='10' rx='2' fill='%23214c6f'/%3E%3C/svg%3E">
<link rel="stylesheet" href="/static/app.css?v=__VERSION__">
</head>
<body>
<header class="topbar">
  <a class="brand" href="#/overview" aria-label="Coeos-Rag, vue d'ensemble">
    <!-- LOGO: paste the Coeos SVG below verbatim, with class="brand-logo" and width="30" height="30" -->
    <span class="brand-name">Coeos-Rag</span>
  </a>
  <div class="seg" id="corpus-switch" role="tablist" aria-label="Corpus"></div>
  <div class="topbar-right">
    <span class="muted small" id="updated"></span>
    <button class="btn ghost" data-action="token" id="token-btn">Jeton</button>
  </div>
</header>
<div class="shell">
  <nav class="nav" id="nav" aria-label="Sections"></nav>
  <main class="main" id="main" tabindex="-1"></main>
</div>
<dialog id="dialog" class="dialog"></dialog>
<div id="toasts" class="toasts" aria-live="polite"></div>
<script src="/static/app.js?v=__VERSION__"></script>
</body>
</html>
```
Le logo Coeos, à coller à la place du commentaire `LOGO` (remplace seulement la balise
ouvrante par `<svg class="brand-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="30" height="30" role="img" aria-label="Coeos">`) :
```html
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="512" height="512" role="img" aria-label="CoeOS">
  <rect x="3" y="3" width="114" height="114" rx="26" fill="#58badd"/>
  <rect x="3" y="3" width="114" height="114" rx="26" fill="none" stroke="#32353a" stroke-width="3"/>
  <g fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round">
    <path d="M51 18 H18 V102 H51"/>
    <path d="M69 18 H102 V102 H69"/>
    <path d="M29 51 V29 H91 V51"/>
    <path d="M29 69 V91 H91 V69"/>
    <path d="M53 40 H40 V80 H53"/>
    <path d="M67 40 H80 V80 H67"/>
  </g>
  <rect x="55" y="55" width="10" height="10" rx="2" fill="#214c6f"/>
</svg>
```
**Ne redessine pas le logo, ne change aucune coordonnée ni couleur.**
Supprime ensuite l'ancien fichier : `rm odyrag/static/dashboard.html`.

## WU-B2 — `app.css` (copie ce bloc tel quel, puis complète avec les mêmes jetons)

Règles de design, à respecter dans tout ce que tu ajoutes :
- Un seul accent (bleu Coeos). Les couleurs `ok / warn / bad / info` ne servent qu'à dire un état réel.
- Trois rayons, pas un de plus : `--r-card` (cartes, dialogues), `--r-ctl` (boutons, champs), `--r-pill` (pastilles d'état).
- Aucune couleur écrite en dur hors de `:root` : toujours `var(--…)`.
- Pas de dégradé, pas de halo lumineux, pas d'ombre noire pure, pas d'animation en boucle
  sauf le squelette de chargement. Transitions : `opacity` et `transform` seulement.
- Libellé AU-DESSUS du champ, jamais de placeholder en guise de libellé.
- Chaque zone de données a quatre états : chargement (squelette), vide, erreur, données.

```css
:root {
  color-scheme: light dark;
  --bg:#f4f6f8; --surface:#fdfefe; --sunken:#eef1f4; --border:#d9dfe5; --border-strong:#c3ccd4;
  --text:#16212b; --muted:#566775; --faint:#7f8f9c;
  --accent:#214c6f; --accent-hover:#193b57; --on-accent:#fdfefe; --accent-soft:#e3f1f8; --focus:#2f93bd;
  --ok:#1b7f55; --ok-soft:#e2f4ec; --warn:#8f5508; --warn-soft:#fbefd9;
  --bad:#b4302a; --bad-soft:#fbe6e4; --info:#1c6a8c; --info-soft:#e3f1f8;
  --r-card:12px; --r-ctl:8px; --r-pill:999px;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --shadow:0 1px 2px rgb(22 33 43 / .06), 0 8px 24px rgb(22 33 43 / .07);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0d1318; --surface:#141c23; --sunken:#0f161c; --border:#24313b; --border-strong:#33434f;
    --text:#e5edf3; --muted:#93a4b1; --faint:#6c7d8a;
    --accent:#58badd; --accent-hover:#7fcbe6; --on-accent:#0b1a24; --accent-soft:#12303f; --focus:#58badd;
    --ok:#4cc38a; --ok-soft:#10291f; --warn:#e2a93b; --warn-soft:#2c2210;
    --bad:#f0736b; --bad-soft:#321715; --info:#6cc4e3; --info-soft:#12303f;
    --shadow:0 1px 2px rgb(0 0 0 / .4), 0 8px 24px rgb(0 0 0 / .3);
  }
}
* { box-sizing:border-box; margin:0; }
html { -webkit-text-size-adjust:100%; }
body { background:var(--bg); color:var(--text); font:14px/1.5 var(--sans); min-height:100dvh; }
h1 { font-size:22px; line-height:1.25; font-weight:650; letter-spacing:-.01em; }
h2 { font-size:15px; line-height:1.3; font-weight:650; }
h3 { font-size:13px; font-weight:600; color:var(--muted); }
p { max-width:72ch; }
a { color:var(--accent); }
.muted { color:var(--muted); } .small { font-size:12.5px; } .mono { font-family:var(--mono); font-size:12.5px; }
.num { font-variant-numeric:tabular-nums; }
.hidden { display:none !important; }
:focus-visible { outline:2px solid var(--focus); outline-offset:2px; }

.topbar { position:sticky; top:0; z-index:20; height:60px; display:flex; align-items:center; gap:20px;
  padding:0 24px; background:var(--surface); border-bottom:1px solid var(--border); }
.brand { display:flex; align-items:center; gap:10px; text-decoration:none; color:var(--text); }
.brand-logo { display:block; flex:none; }
.brand-name { font-size:16px; font-weight:650; letter-spacing:-.01em; white-space:nowrap; }
.topbar-right { margin-left:auto; display:flex; align-items:center; gap:12px; }

.seg { display:flex; gap:2px; padding:3px; background:var(--sunken); border:1px solid var(--border); border-radius:var(--r-ctl); }
.seg button { border:0; background:transparent; color:var(--muted); font:inherit; font-weight:550; padding:5px 12px;
  border-radius:6px; cursor:pointer; display:flex; align-items:center; gap:7px; white-space:nowrap; }
.seg button[aria-selected="true"] { background:var(--surface); color:var(--text); box-shadow:0 1px 2px rgb(0 0 0 / .08); }

.shell { display:grid; grid-template-columns:208px minmax(0,1fr); max-width:1360px; margin:0 auto; }
.nav { position:sticky; top:60px; align-self:start; padding:20px 12px; display:flex; flex-direction:column; gap:2px; }
.nav a { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:8px 12px; border-radius:var(--r-ctl);
  color:var(--muted); text-decoration:none; font-weight:550; }
.nav a:hover { background:var(--sunken); color:var(--text); }
.nav a[aria-current="page"] { background:var(--accent-soft); color:var(--accent); }
.main { padding:28px 28px 80px; min-width:0; outline:none; }
.page-head { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:20px; }
.page-head p { color:var(--muted); margin-top:4px; }
.stack { display:flex; flex-direction:column; gap:16px; }
.cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr)); gap:16px; }
.row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.grow { flex:1; min-width:0; }

.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--r-card); padding:20px; min-width:0; }
.card > h2 { margin-bottom:14px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.card-foot { margin-top:14px; padding-top:14px; border-top:1px solid var(--border); }

.btn { appearance:none; border:1px solid transparent; border-radius:var(--r-ctl); background:var(--accent); color:var(--on-accent);
  font:inherit; font-weight:600; padding:7px 14px; cursor:pointer; white-space:nowrap; transition:transform .08s ease; }
.btn:hover:not(:disabled) { background:var(--accent-hover); }
.btn:active:not(:disabled) { transform:translateY(1px); }
.btn:disabled { opacity:.45; cursor:not-allowed; }
.btn.ghost { background:transparent; color:var(--text); border-color:var(--border-strong); }
.btn.ghost:hover:not(:disabled) { background:var(--sunken); }
.btn.danger { background:transparent; color:var(--bad); border-color:var(--bad); }
.btn.danger:hover:not(:disabled) { background:var(--bad-soft); }
.btn.sm { padding:4px 10px; font-size:12.5px; }

.field { display:flex; flex-direction:column; gap:6px; min-width:0; }
.field label { font-weight:600; font-size:13px; }
.field .help { color:var(--muted); font-size:12.5px; }
.field .error { color:var(--bad); font-size:12.5px; }
input[type=text], input[type=number], select, textarea { width:100%; background:var(--surface); color:var(--text);
  border:1px solid var(--border-strong); border-radius:var(--r-ctl); padding:8px 10px; font:inherit; }
textarea { resize:vertical; min-height:120px; line-height:1.5; }
input.mono, select.mono { font-family:var(--mono); font-size:12.5px; }
label.check { display:flex; align-items:center; gap:8px; color:var(--muted); font-size:13px; cursor:pointer; }

.chip { display:inline-flex; align-items:center; gap:6px; padding:2px 9px; border-radius:var(--r-pill); font-size:12px; font-weight:600;
  background:var(--sunken); color:var(--muted); white-space:nowrap; }
.chip.ok { background:var(--ok-soft); color:var(--ok); } .chip.warn { background:var(--warn-soft); color:var(--warn); }
.chip.bad { background:var(--bad-soft); color:var(--bad); } .chip.info { background:var(--info-soft); color:var(--info); }

.alert { border:1px solid var(--border); border-left-width:3px; border-radius:var(--r-ctl); padding:10px 14px; background:var(--surface); }
.alert.bad { border-left-color:var(--bad); } .alert.warn { border-left-color:var(--warn); } .alert.info { border-left-color:var(--info); }
.alert strong { font-weight:650; }

.kv { display:grid; grid-template-columns:minmax(110px,max-content) minmax(0,1fr); gap:8px 16px; }
.kv dt { color:var(--muted); } .kv dd { min-width:0; overflow-wrap:anywhere; }

.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; }
.stat { background:var(--sunken); border-radius:var(--r-ctl); padding:12px 14px; }
.stat b { display:block; font-size:22px; line-height:1.2; font-weight:650; font-variant-numeric:tabular-nums; }
.stat span { color:var(--muted); font-size:12.5px; }

.table-wrap { overflow-x:auto; border:1px solid var(--border); border-radius:var(--r-card); background:var(--surface); }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; font-weight:600; color:var(--muted); padding:10px 14px; background:var(--sunken); white-space:nowrap; }
td { padding:10px 14px; border-top:1px solid var(--border); vertical-align:top; }
td.right, th.right { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
td .sub { color:var(--muted); font-size:12.5px; margin-top:2px; overflow-wrap:anywhere; }
td .err { color:var(--bad); font-size:12.5px; margin-top:4px; overflow-wrap:anywhere; }
.filters { display:flex; gap:6px; flex-wrap:wrap; }
.filters button { border:1px solid var(--border-strong); background:var(--surface); color:var(--muted); font:inherit; font-weight:550;
  padding:4px 11px; border-radius:var(--r-pill); cursor:pointer; }
.filters button[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:var(--on-accent); }
.pager { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 14px; border-top:1px solid var(--border); }

.dropzone { border:1.5px dashed var(--border-strong); border-radius:var(--r-card); padding:24px; text-align:center; background:var(--sunken); cursor:pointer; }
.dropzone.over { border-color:var(--accent); background:var(--accent-soft); }
.upload-list { display:flex; flex-direction:column; gap:6px; margin-top:12px; }
.upload-list li { display:flex; justify-content:space-between; gap:12px; list-style:none; font-size:13px; }

.answer { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.6; }
.steps { display:flex; flex-direction:column; gap:4px; font-family:var(--mono); font-size:12.5px; }
.steps li { display:grid; grid-template-columns:52px 14px minmax(0,1fr); gap:8px; list-style:none; }
.steps .t { color:var(--faint); text-align:right; }
.steps .ok { color:var(--ok); } .steps .ko { color:var(--bad); }
.log { font-family:var(--mono); font-size:12.5px; color:var(--muted); display:flex; flex-direction:column; gap:2px; max-height:180px; overflow:auto; }

.empty { text-align:center; padding:36px 20px; color:var(--muted); }
.empty b { display:block; color:var(--text); font-size:15px; margin-bottom:4px; }
.skeleton { border-radius:var(--r-ctl); background:var(--sunken); height:16px; animation:pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.5; } }
@media (prefers-reduced-motion: reduce) { .skeleton { animation:none; } .btn { transition:none; } }

.dialog { border:1px solid var(--border); border-radius:var(--r-card); background:var(--surface); color:var(--text);
  padding:24px; width:min(480px, calc(100vw - 32px)); box-shadow:var(--shadow); }
.dialog::backdrop { background:rgb(8 14 19 / .5); }
.dialog h2 { margin-bottom:8px; } .dialog p { color:var(--muted); margin-bottom:16px; white-space:pre-line; }
.dialog .actions { display:flex; justify-content:flex-end; gap:10px; margin-top:20px; }
.toasts { position:fixed; right:20px; bottom:20px; z-index:40; display:flex; flex-direction:column; gap:8px; max-width:min(420px, calc(100vw - 40px)); }
.toast { background:var(--surface); border:1px solid var(--border); border-left:3px solid var(--info); border-radius:var(--r-ctl);
  padding:10px 14px; box-shadow:var(--shadow); }
.toast.ok { border-left-color:var(--ok); } .toast.bad { border-left-color:var(--bad); }

@media (max-width: 860px) {
  .topbar { padding:0 14px; gap:12px; }
  .brand-name { display:none; }
  #updated { display:none; }
  .shell { grid-template-columns:minmax(0,1fr); }
  .nav { position:static; flex-direction:row; overflow-x:auto; padding:10px 14px; border-bottom:1px solid var(--border); }
  .nav a { white-space:nowrap; }
  .main { padding:20px 14px 64px; }
}
```

## WU-B3 — `app.js` : architecture imposée

Un seul fichier, une IIFE `(() => { 'use strict'; … })();`, aucune dépendance.

**Règle de rendu (c'est LE piège de ce genre d'écran)** : chaque section a une fonction
`mount(el)` appelée UNE fois à la navigation, qui pose la structure et les formulaires, et
une fonction `update()` appelée par le rafraîchissement périodique, qui ne réécrit QUE des
éléments marqués `data-region="…"` ne contenant AUCUN champ de saisie. Un `innerHTML` posé
sur un conteneur qui contient un `<input>`, `<select>` ou `<textarea>` pendant un
rafraîchissement efface ce que l'utilisateur tape : c'est interdit.

**Sécurité** : toute donnée venue du réseau passe par `esc()` avant d'entrer dans du HTML.
```js
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
```

**État**
```js
const state = { overview: null, corpusId: null, section: 'overview', token: '', job: null,
                docs: { filter: null, page: 1, data: null, pipeline: null, error: null },
                lr: {} };  // lr[corpusId] = last /health payload read from the instance
```
`token` : lu et écrit dans `sessionStorage` sous la clé `coeos_rag_token` (dans un `try/catch`).
`corpusId` : mémorisé dans `localStorage` sous `coeos_rag_corpus` (dans un `try/catch`) ; au
chargement, premier corpus de l'overview si la valeur mémorisée n'existe plus.

**Routage** : `location.hash` de la forme `#/<section>` avec `<section>` parmi `overview`,
`documents`, `query`, `models`, `journal` (défaut `overview`). Écoute `hashchange`. La
navigation `#nav` contient cinq liens : `Vue d'ensemble`, `Documents`, `Requête`, `Modèles`,
`Journal`, avec `aria-current="page"` sur le lien actif. `#corpus-switch` contient un
`<button role="tab" aria-selected="…">` par corpus (libellé = `label`, suivi d'une pastille
`.chip.ok` « en ligne » ou `.chip.bad` « arrêtée » selon `instance.up`). Changer de corpus
remonte la section courante.

**Deux clients HTTP, à ne pas mélanger**
```js
// 1. This service (same origin). Adds the bearer token when there is one.
async function api(path, opts = {}) { /* fetch(path), JSON body, Authorization: 'Bearer ' + state.token ;
   on !r.ok throw an Error with .status and message = body.detail || 'HTTP ' + r.status */ }

// 2. A LightRAG instance, called DIRECTLY from the browser (no proxy, no token).
function lrBase(c) {
  if (c.instance.public_url) return c.instance.public_url.replace(/\/+$/, '');
  const u = new URL(c.instance.url);
  if (['localhost', '127.0.0.1', '[::1]', '::1'].includes(u.hostname)) u.hostname = location.hostname;
  return u.origin;
}
async function lr(c, path, opts = {}) { /* fetch(lrBase(c) + path, opts) ; JSON bodies get Content-Type: application/json ;
   NEVER set Content-Type for a FormData body ; on network failure throw
   new Error('Instance injoignable depuis ce navigateur (' + lrBase(c) + ')') ;
   on !r.ok throw new Error(body.detail || 'HTTP ' + r.status) */ }
```
**Piège** : le registre stocke l'URL de l'instance vue depuis l'hôte (`http://localhost:8766`).
Depuis le poste de l'utilisateur, `localhost` n'est pas l'hôte : `lrBase` remplace donc un
nom de boucle locale par `location.hostname`. N'écris aucune adresse en dur.
**Piège** : pour l'envoi de fichier, passe le `FormData` tel quel à `fetch` et ne fixe PAS
`Content-Type` (le navigateur doit poser la frontière multipart lui-même).

**Rafraîchissement** : `GET /api/overview` toutes les 10 s (suspendu pendant qu'un job de
ce service tourne), puis `update()` de la section. Section Documents : en plus, relit
`pipeline_status` et la page courante toutes les 4 s tant que `pipeline.busy` est vrai ou que
`counts.pending + counts.processing > 0`, sinon toutes les 20 s. Un seul `setTimeout` actif à
la fois par boucle ; annule-le en quittant la section. `#updated` affiche
`Mis à jour à HH:MM:SS`.

**Dialogues** : jamais `window.prompt`, `window.confirm` ni `window.alert`. Utilise
`<dialog id="dialog">` avec `showModal()` :
- `askToken()` → Promise<bool> : titre « Jeton d'administration », texte « Nécessaire pour
  tout changement de modèle, d'endpoint ou d'embedder. Conservé pour cet onglet
  uniquement. », un `<input type="password" autocomplete="off">` avec libellé « Jeton »,
  boutons « Annuler » (ghost) et « Enregistrer ».
- `confirmDialog(title, text, confirmLabel)` → Promise<bool>.
- `toast(text, kind)` : ajoute un `.toast` dans `#toasts`, retiré après 6 s.
Le bouton d'en-tête « Jeton » appelle `askToken()` ; son libellé devient « Jeton enregistré »
quand `state.token` est non vide.

**Mutations de ce service** : `mutate(path, body, confirm)` → confirmation, jeton si absent,
`POST`, puis suivi du job par `GET /api/jobs/{id}` chaque seconde jusqu'à `status !== 'running'`.
Sur 401 : vide le jeton et affiche le toast « Jeton refusé ». Sur toute autre erreur : toast
`bad` avec le message. Le job en cours s'affiche dans la section Modèles (région `job`).

### Les cinq sections (textes exacts entre guillemets)

**1. Vue d'ensemble** (`#/overview`) — tous les corpus.
- En-tête : `<h1>Vue d'ensemble</h1>` + « État des corpus et de leurs modèles. »
- Région `alerts` : une `.alert.bad` par problème réel, dans cet ordre :
  instance non jointe → « **{label}** : l'instance ne répond pas. » ;
  `llm.ready === false` → « **{label}** : le modèle {llm.configured} n'est pas chargé sur
  l'endpoint. Les requêtes hybrides ne renverront aucun contexte. » ;
  `embedder.alive === false` → « **{label}** : l'embedder ne répond pas. » ;
  `embedder.same_space === false` → « **{label}** : l'embedder ne produit pas l'espace
  vectoriel de l'index. La recherche vectorielle renvoie du bruit. »
  Et une `.alert.warn` « Lecture seule : aucun jeton n'est configuré sur le serveur. » si
  `mutations_enabled` est faux. Aucune alerte = région vide (pas de message « tout va bien »).
- Région `corpora` : une `.card` par corpus dans `.cols`. Titre = `label` + pastilles :
  `en ligne` (ok) / `arrêtée` (bad) / `ne répond pas` (bad si `loaded && !up`), puis
  `extraction en cours` (warn) si `busy`, puis `LightRAG {version}` (neutre) si connue.
  Un bloc `.stats` : « Traités », « En échec », « En attente », « En cours » (valeurs de
  `counts`, formatées `toLocaleString('fr-FR')`, « - » si `counts` est null).
  Un `<dl class="kv">` : « Modèle » → `llm.live || llm.configured` (mono) + pastille
  `prêt` (ok) / `non chargé` (bad) / `inconnu` (neutre) ; « Endpoint » →
  `engine.endpoint.effective` (mono) ; « Embedder » → `embedder.live.model` (mono) +
  pastille `même espace que l'index` (ok) / `espace différent` (bad) / `non certifié` (warn).
  Pied de carte : deux boutons ghost « Documents » et « Modèles » qui sélectionnent ce
  corpus et naviguent vers la section.

**2. Documents** (`#/documents`) — corpus sélectionné, appels DIRECTS à LightRAG.
- En-tête : `<h1>Documents</h1>` + « {label} : injecter des documents et suivre leur traitement. »
- Carte « Injecter » : deux colonnes (`.cols`).
  - Colonne fichiers : `.dropzone` (clic = ouvre un `<input type="file" multiple hidden>`,
    glisser-déposer géré avec `dragover` / `dragleave` / `drop`, classe `.over`) avec le
    texte « Déposez des fichiers ici ou cliquez pour choisir » et, en `.small.muted`,
    « PDF, DOCX, MD, TXT. Un envoi par fichier. » Chaque fichier part l'un après l'autre en
    `POST /documents/upload` (champ `file`). `ul.upload-list` : une ligne par fichier, nom à
    gauche, à droite une pastille `envoi…` (info) puis `accepté` (ok), `doublon` (warn) si
    `status === 'duplicated'`, ou `échec` (bad) ; sous le nom, le `message` renvoyé.
  - Colonne texte : `.field` « Texte » (`textarea`), `.field` « Source » (`input`, aide :
    « Nom affiché dans la liste des documents. Facultatif. »), bouton « Injecter le texte »
    → `POST /documents/text {text, file_source}`. Texte vide = erreur de champ « Le texte est
    vide. », pas d'appel.
  - Pied de carte : bouton ghost « Scanner le dossier d'entrée » → `POST /documents/scan`,
    toast avec le `message`.
- Carte « File de traitement » (région `pipeline`) : pastille `en cours` (warn) ou `au repos`
  (neutre) ; si `busy` : « {job_name} : lot {cur_batch} sur {batchs} » ; `latest_message` ;
  les 6 derniers `history_messages` dans `.log`. Boutons : « Annuler le traitement » (danger,
  visible seulement si `busy`, avec `confirmDialog`) → `POST /documents/cancel_pipeline` ;
  « Relancer les échecs » (ghost, visible seulement si `counts.failed > 0`) →
  `POST /documents/reprocess_failed`. Toast avec le `message` renvoyé.
- Liste : `.filters` avec « Tous », « Traités », « En cours », « En attente », « En échec »
  (chacun suivi de son nombre tiré de `status_counts`; « En cours » = `processing` ;
  `aria-pressed`). Puis `.table-wrap` : colonnes « Document », « État », « Chunks »
  (right), « Taille » (right, en caractères, `toLocaleString('fr-FR')`), « Mis à jour »
  (right, `JJ/MM/AAAA HH:MM`). Cellule Document : `file_path` en gras léger, `content_summary`
  tronqué à 140 caractères en `.sub`, et `error_msg` en `.err` s'il existe. Pastilles d'état :
  `traité` (ok), `en cours` (info), `en attente` (warn), `prétraité` (info), `échec` (bad).
  `.pager` : « {total_count} documents », « Page {page} sur {total_pages} », boutons ghost
  « Précédent » / « Suivant » désactivés selon `has_prev` / `has_next`. 20 lignes par page.
  Changer de filtre remet la page à 1.
- États : squelette (5 lignes `.skeleton`) au premier chargement ; `.empty` « Aucun
  document » + « Injectez un fichier ou un texte ci-dessus. » ; erreur → `.alert.bad` avec le
  message de `lr()`.

**3. Requête** (`#/query`) — corpus sélectionné, appel DIRECT à LightRAG.
- En-tête : `<h1>Requête</h1>` + « {label} : interroger le corpus pour vérifier ce que le retrieval renvoie. »
- Carte formulaire : `.field` « Question » (`textarea`) ; `.row` avec `.field` « Mode »
  (`select` : `mix` « Mixte (graphe + vecteurs) », `hybrid` « Hybride », `local` « Local
  (entités) », `global` « Global (relations) », `naive` « Vectoriel seul » ; défaut `mix`),
  `.field` « Résultats (top_k) » (`input type=number`, défaut 40, min 1, max 200),
  `label.check` « Contexte seul, sans réponse rédigée » ; bouton « Interroger ».
- `POST /query` avec `{query, mode, top_k, only_need_context, include_references: true}`.
  Pendant l'appel : bouton désactivé, libellé « Recherche… ». Question vide = erreur de champ
  « La question est vide. ».
- Carte résultat : `.answer` (texte brut, via `textContent`), une ligne `.small.muted`
  « Réponse en {x,x} s, mode {mode} », puis « Sources » : liste des `references`
  (`file_path`, mono), ou « Aucune source renvoyée. » Erreur → `.alert.bad`.
  Avant toute requête : `.empty` « Aucune requête » + « Posez une question pour tester le corpus. »

**4. Modèles** (`#/models`) — corpus sélectionné, appels à CE service.
- En-tête : `<h1>Modèles</h1>` + « {label} : modèle de langage, endpoint et embedder de l'instance. »
- Région `job` (en haut, masquée s'il n'y a pas de job) : carte « {kind} sur {corpus} » +
  pastille `en cours` / `terminé` / `échec` ; `ul.steps` (colonne 1 : `+{secondes}s`,
  colonne 2 : `✓` classe `ok` ou `✕` classe `ko`, colonne 3 : `step` + `detail` en muted) ;
  en cas d'échec une `.alert.bad` avec `error`.
- Carte « Modèle de langage ».
  - Région `llm-status` : `<dl class="kv">` « En service » → `llm.live` ; « Configuré » →
    `llm.configured` + pastille `configuré ≠ en service` (warn) si `in_sync === false` ;
    « Endpoint » → `engine.endpoint.effective` + pastille `joignable` (ok) /
    `injoignable` (bad) ; « État » → pastille `prêt` / `non chargé` / `inconnu`.
  - Si `instance.version` commence par `1.4` ou vaut moins que 1.5 : `.alert.info`
    « Cette instance (LightRAG {version}) utilise un seul modèle pour l'extraction des
    documents et pour la requête. Les modèles séparés par rôle existent à partir de
    LightRAG 1.5. »
  - Formulaire (hors région) : `.field` « Endpoint » (`input.mono`, valeur initiale =
    `engine.endpoint.effective`, aide : « URL de base compatible OpenAI, en général terminée
    par /v1. Réseau privé uniquement. ») + bouton ghost « Lister les modèles » →
    `POST /api/endpoint/models {url}` (jeton requis) qui remplit le `select` ci-dessous et
    affiche sous le champ « {n} modèles publiés, {m} prêts » ou l'erreur en `.error`
    (« Endpoint injoignable. » si `reachable` est faux).
    `.field` « Modèle » (`select.mono`) : options = `engine.ready_models` du corpus (ou la
    liste renvoyée par « Lister les modèles », avec le suffixe « (non chargé) » pour
    `ready: false`) ; le modèle courant est toujours présent et sélectionné.
    `label.check` « Appliquer même si le modèle n'est pas chargé ».
    Bouton « Appliquer » → `mutate('/api/corpora/{id}/llm', {model, endpoint, force}, …)` où
    `endpoint` n'est envoyé que s'il diffère de `engine.endpoint.effective` (sinon `null`).
    Confirmation : titre « Appliquer ce réglage ? », texte « {label} redémarre (environ une
    minute). Le changement est refusé pendant une extraction, et annulé automatiquement si
    l'instance ne revient pas saine. »
- Carte « Embedder ».
  - Région `emb-status` : `<dl class="kv">` « État » → `en service, {latency_ms} ms,
    {dim} dimensions` + pastille d'espace (comme en Vue d'ensemble), ou
    `ne répond pas : {error}` ; « Poids » → `embedder.weights` ; « En service » →
    `embedder.live.url` et `embedder.live.model` (mono).
  - Formulaire : `.field` « Endpoint » (`input.mono`, valeur `embedder.configured.url`),
    `.field` « Modèle » (`input.mono`, valeur `embedder.configured.model`), bouton
    « Changer d'embedder », désactivé tant que l'index n'est pas certifié. Aide sous le
    formulaire : certifié → « Le nouvel endpoint doit produire le même espace vectoriel que
    l'index (vérifié par cosinus). Un autre espace exige une ré-indexation et est refusé. » ;
    non certifié → « Certifiez d'abord l'index : son espace vectoriel doit être connu avant
    de changer d'embedder. »
    → `mutate('/api/corpora/{id}/embedder', {url, model}, …)`.
- Carte « Index ».
  - Région `index-status` : « Embedder d'origine » → `indexed_with.embedder.weights` +
    `{dim} dimensions` ; « Espace vectoriel » → pastille `certifié` (ok) + « médiane
    {median_cos}, minimum {min_cos} sur {samples} chunks, le {date} », ou pastille
    `non certifié` (warn). Puis la table des ères : « Modèle d'extraction », « Période »,
    « Documents » (right), « Source ». Sous la table, en `.small.muted` : « LightRAG
    n'enregistre pas le modèle qui a construit un index. Cet historique est tenu par
    Coeos-Rag. »
  - Bouton ghost « Certifier l'index » → `mutate('/api/corpora/{id}/certify', null, …)`,
    confirmation « Recalcule un échantillon de chunks stockés avec l'embedder de l'instance
    et le compare aux vecteurs stockés. Rien n'est redémarré. »
  - Si `!instance.loaded && !instance.up` : bouton « Démarrer l'instance » →
    `mutate('/api/corpora/{id}/start', null, …)`.
- Tous les boutons de mutation sont désactivés si `mutations_enabled` est faux ou si un job tourne.

**5. Journal** (`#/journal`) — global.
- En-tête : `<h1>Journal</h1>` + « Les changements appliqués par Coeos-Rag. »
- `GET /api/actions` → `.table-wrap` : « Date », « Corpus », « Action », « Résultat »
  (pastille `terminé` ok / `échec` bad + `error` en muted). `.empty` « Aucune action » +
  « Les changements de modèle, d'endpoint et d'embedder apparaîtront ici. »
- Libellés d'action : `llm` → « Modèle de langage », `embedder` → « Embedder », `certify` →
  « Certification », `start` → « Démarrage ».

## WU-B4 — auto-contrôle avant de rendre la main

1. `node --check odyrag/static/app.js`.
2. `grep -n "—\|–" odyrag/static/index.html odyrag/static/app.css odyrag/static/app.js` : aucune ligne.
3. `grep -n "window.prompt\|window.confirm\|window.alert\|alert(\|192\.168\|https\?://[a-z]" odyrag/static/app.js` :
   aucune ligne (la seule URL absolue autorisée dans les statiques est `http://www.w3.org/2000/svg`).
4. Relis chaque `innerHTML =` : la cible ne contient aucun champ de saisie si elle est
   réécrite par `update()`, et toute donnée réseau passe par `esc()`.
5. Relis chaque texte visible : français correct, accents présents, aucun texte inventé hors
   de ce plan.

---

## Non-buts (ne pas faire)

- **Ne modifie pas `PROBE_TEXT`** (`odyrag/embedder.py:24`), bien qu'il contienne « OdyRAG ».
  Le vecteur étalon enregistré en production a été calculé sur cette phrase exacte : la
  changer ferait passer les deux corpus en « espace différent ».
- **Pas de renommage technique** : le package reste `odyrag`, les variables `ODYRAG_*`, le
  dossier `~/.odyrag`, le label launchd `eu.odyssai.odyrag`, les suffixes `.bak-odyrag-`. Le
  rebrand ne touche que ce qui est visible : titre de l'app, textes de l'UI, logo, la chaîne
  `"Coeos-Rag settings"`.
- Pas de proxy vers LightRAG dans ce service. Pas de nouvelle dépendance dans `requirements.txt`.
- Pas de suppression de documents, pas d'édition du graphe, pas de vue Galaxy 3D, pas de
  bouton « vider le cache ».
- Pas de sélecteur de modèle par rôle (extraction / requête / mots-clés), pas de réglage du reranker.
- Pas de montée de version de LightRAG. Pas de modification de `deploy/deploy.sh`,
  `deploy/registry.prod.json`, `tests/e2e_real_host.py`, `README.md`, `ARCHITECTURE.md`.
- Pas de refactor « au passage » du code existant non cité dans ce plan.
- Pas de stockage du jeton dans `localStorage`.

## Versioning + deploy

- Version : `odyrag/__init__.py`, `__version__ = "0.2.0"` (worker A).
- Vérifications avant de rendre la main : `.venv/bin/python -m pytest -q`,
  `node --check odyrag/static/app.js`.
- **Les workers ne committent pas et ne déploient pas.** Le coordinateur relit le diff contre
  ce plan, vérifie l'UI dans un navigateur sur le serveur de démo, committe (Conventional
  Commits, HEREDOC, footer Co-Authored-By) et pousse sur `main`. Le déploiement sur l'hôte
  ne se fait que par le coordinateur.

## Test de validation (après deploy, par le coordinateur)

```bash
curl -s http://<hôte>:<port>/health            # {"status":"ok","version":"0.2.0",…}
curl -s http://<hôte>:<port>/api/overview      # chaque corpus porte "engine": {"endpoint": {…}, "reachable": true, …}
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<hôte>:<port>/api/endpoint/models -H 'Content-Type: application/json' -d '{"url":"http://localhost:8000/v1"}'   # 401 sans jeton
```
Dans un navigateur : les cinq sections s'affichent, la liste des documents du corpus réel se
charge en direct depuis l'instance, une requête renvoie une réponse. Cas négatif : endpoint
public saisi dans Modèles → refus lisible, aucune instance redémarrée.

## Rapport attendu de chaque worker

Liste des fichiers modifiés, sortie de la dernière commande de vérification (copiée telle
quelle), et toute étape non faite ou faite différemment du plan, avec la raison. Ne dis pas
« terminé » sans la sortie de la commande.

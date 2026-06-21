# OdyRAG

> Branded knowledge-graph management layer for the **odyssai.eu** stack —
> built on [LightRAG](https://github.com/HKUDS/LightRAG) (HKUDS, MIT License).

OdyRAG is the day-to-day cockpit for the cluster of LightRAG instances that
power Companion's memory and the alpha-centauri corpus ingestion: live
monitoring, parallel injection, document management, querying, and a 3D
"galaxy" view of the entity graph.

## Instances

| Card | Port | Working dir | Role |
|---|---|---|---|
| **Company** | `:8767` | `lightrag/70767408-…` (UUID) | Live company memory tier (Sophie / Nemo / Companion / TheComp.ai knowledge). Companion reads it. |
| Shard 1 | `:8768` | `lightrag/company-shard-1` | alpha-centauri ingestion shard |
| Shard 2 | `:8769` | `lightrag/company-shard-2` | alpha-centauri ingestion shard |
| Shard 3 | `:8770` | `lightrag/company-shard-3` | alpha-centauri ingestion shard |
| Shard 4 | `:8771` | `lightrag/company-shard-4` | alpha-centauri ingestion shard |

All instances run as **host processes** on `192.168.86.39` (venv
`~/nemo-venv`), LLM = OdyssAI-X (`localhost:8000`), embeddings = nemo-embed
(`:8765`).

> Gotcha — there is a **second, empty** `company` instance on `:8766`
> (`working_directory=lightrag/company/`): the old alpha-centauri location,
> drained on 2026-06-12, empty by design. The live tier is `:8767`. Both
> `.env` declare `PORT=8766`; the populated one was bumped to `:8767` on the
> port collision. The OdyRAG dashboard targets `:8767`.

## Dashboard

The dashboard is a single self-contained `odyrag.html` (vanilla HTML/CSS/JS,
no build step). It is currently served by **OdyssAI-X / Odysseus** at
`http://192.168.86.39:8000/odyrag` (route + a `/odyrag/api/{shard}/…` proxy
live in Odysseus `scripts/api.py`). Source of truth for the HTML today:
[`Odysseus/scripts/odyrag.html`](http://192.168.86.141:3001/Odyssai-eu/OdyssAI-X).

Tabs:
- **Overview** — per-shard health, pending / running / done / failed counts,
  progress bar, latest pipeline message; retry-all / cancel-all actions.
- **Inject** — paste text or upload a file to a chosen shard; scan input dir.
- **Documents** — paginated, status-filtered document table per shard.
- **Query** — hybrid / local / global / naive query against a shard's graph.
- **☆ Galaxy** — 3D force-directed entity graph
  ([3d-force-graph](https://github.com/vasturiano/3d-force-graph)): nodes
  coloured by entity type, edges as coloured filaments with animated particle
  flow, hover for entity / relation descriptions, click to focus.

## Ingestion

The alpha-centauri corpus (~114k Qdrant points on `.44`) is sharded across the
four ingestion instances. Helper scripts (to be vendored here — see below):
`prepare_ids.py` (split Qdrant IDs into 4 shards), `ingest_shard.py`
(resumable per-shard ingestion), `merge_graphml.py` (consolidate the 4 shard
graphs into `company/`).

## Licensing

OdyRAG is **proprietary** to odyssai.eu. It builds on **LightRAG** (© HKUDS,
MIT License) and **3d-force-graph** (© Vasco Asturiano, MIT License); those
upstream licenses are retained and credited.

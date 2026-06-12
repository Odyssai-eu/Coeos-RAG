# OdyssAI-RAG

Le plan de contrôle du parc RAG de la famille odyssai.eu : santé des
instances, ingestion, crawl, contenu, graphe, export Obsidian, veille.

**Deux organes, jamais mélangés** (voir [ARCHITECTURE.md](ARCHITECTURE.md)) :

- **Memory** — LightRAG, les 4 tiers vivants de Companion (user / team /
  project / company). Petit, souple, rapide. Pas un entrepôt de corpus.
- **Corpus** — Qdrant sur `.44`, collections nommées alimentées par
  pipelines (crawl, Docling, vault Obsidian, veille). Attachables aux
  conversations Companion.

OdyssAI-RAG observe, alimente, répare et gouverne — il n'est **pas** dans le
chemin de chat.

| | |
|---|---|
| Dashboard | `.39:8801` (container `odyssai-rag`) — pages Memory / Corpus |
| Stack | FastAPI + dashboard single-file (pattern OdyssAI-X / odyssai-services) |
| Statut | seed — issues #1-#6 |

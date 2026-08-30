# Tech Stack

## Frontend
- Next.js 16 (App Router, static export)
- React 19
- Tailwind CSS v4
- Framer Motion
- Recharts
- **Two map engines, chosen at runtime.** The map always draws:
  - **Google Maps** (`@googlemaps/js-api-loader`, greyscale style) when
    `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` is set in `web/.env.local` — gitignored; it
    must never go in `.env.production`, which is tracked. `set_keys.py --maps` writes
    it correctly.
  - **Leaflet** over Esri's keyless grey canvas tiles otherwise — and also when a key
    is present but Google rejects it, which is reported through `window.gm_authFailure`
    rather than a failed promise. Both engines draw the same layers with the same
    colours, the same zoom gate and the same marker budget, defined once in
    `map-shared.tsx`.
- react-force-graph-2d / -3d + three.js (network view)

## Backend
- Python 3.13 + Flask
- Gunicorn (Catalyst AppSail)
- pandas / numpy
- networkx (knowledge graph, Adamic-Adar, Louvain)
- CSV-backed in-memory store over the 26-table CCTNS-shaped schema

## AI / ML
- scikit-learn — the whole ML layer, no other modelling dependency
- Offline training entrypoint: `python -m ml.train`
- `artifacts/model_registry.json` is the published evidence the UI renders
- LLM copilot via GROQ_API_KEY / NVIDIA_API_KEY (optional; a rule-based
  fallback keeps the agent working without either)

## Geography
- `appsail/karnataka.py` — all 31 districts, 8 police commissionerates,
  140 stations, HQ coordinates and 2011 Census population/urbanisation.
  Single source of truth for the generator, the outcome simulator and the
  socio-economic overlay.

# Security — API keys

## Current state
- `appsail/app-config.json` (holds `GROQ_API_KEY`, `NVIDIA_API_KEY`) is now **git-ignored** and untracked.
  It still lives on disk so local runs and `catalyst deploy` keep working. Commit
  `appsail/app-config.example.json` (placeholders) instead.
- `web/.env.local` / `web/.env.production.local` (hold `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY`)
  were already git-ignored.

## ⚠️ Still TODO before judging day (keys were previously committed → treat as compromised)
1. **Rotate all three keys** and paste the new ones into `appsail/app-config.json` (backend) and
   `web/.env.production.local` (Maps):
   - Groq: https://console.groq.com/keys
   - NVIDIA NIM: https://build.nvidia.com (API key)
   - Google Maps: https://console.cloud.google.com/apis/credentials
2. **Restrict the Google Maps key** (it is a browser key, so it is always visible in page source —
   secrecy is not the control): in Cloud Console → the key → *Application restrictions* → *HTTP referrers*,
   allow only your Catalyst domain (e.g. `https://*.catalystappsail.in/*`) and `http://localhost:*`.
   Under *API restrictions*, limit it to *Maps JavaScript API*.
3. The old keys remain in **git history**. Once rotated they are dead, so this is low-risk. To also purge
   history, rewrite it with `git filter-repo` / BFG and force-push (do this only if the repo is shared).

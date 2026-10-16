# OpsPilot incident console

The React + TypeScript console reads the local OpsPilot API. Run the Compose API first, then `npm ci` and `npm run dev` here. Open `http://127.0.0.1:18117`. `npm run build` runs TypeScript checking and creates `dist/`.

The page polls `/api/incidents` and `/api/pending-actions` every ten seconds. Selecting an incident loads its timeline and checkpointed agent state. Follow-up questions use the same thread ID. The approval token is held only in React memory and cleared after a decision; leaving it blank prevents approval. Backend authorization remains necessary even if a browser hides a button.

The console is a local demo, and the incident data comes from controlled synthetic faults. `VITE_API_BASE` can override the default `http://127.0.0.1:18100`; the API currently allows browser origins on local port `18117`. The current timeline includes alert, deploy, and backup events. Live anomaly rows and log/tracing views await the observability stack.

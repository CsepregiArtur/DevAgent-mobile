# DevAgent - gateway, agent macOS și aplicație mobilă (PWA + iOS)

> **Repo ↔ deployment.** Acest document descrie deployment-ul de pe VPS, unde codul
> rulează din `/opt/deepseek-proxy/devagent/`. În repo, codul este organizat astfel:
>
> | Repo | Server (deployment) |
> |---|---|
> | `gateway/app/`, `gateway/tests/`, `gateway/requirements.txt`, `gateway/env.example` | `/opt/deepseek-proxy/devagent/gateway/...` |
> | `agent/` | `/opt/deepseek-proxy/devagent/agent/` |
> | `systemd/devagent-gateway.service` | `/etc/systemd/system/devagent-gateway.service` |
> | `scripts/smoke.sh` | `/opt/deepseek-proxy/devagent/tests/smoke.sh` |
> | `src/`, `vite.config.ts`, `capacitor.config.ts` (rădăcina repo-ului) | `/opt/deepseek-proxy/mobile/` |
>
> Sincronizare repo → server: `scripts/sync-to-server.sh [--restart] [--dry-run]`.
> Restul acestui document folosește căile de pe server.

Platformă de execuție pentru sarcini de cod: scrii o instrucțiune de pe telefon,
MacBook-ul o execută într-un worktree izolat, **nimic nu se aplică înainte de
aprobarea ta**, iar dacă laptopul se deconectează sarcina se reia din ultimul
checkpoint în loc să o ia de la zero.

Public: **https://deepseek-api.cs-hub.xyz/mobile** (PWA) ·
`wss://deepseek-api.cs-hub.xyz/mobile/agent/ws` (agent) ·
`https://deepseek-api.cs-hub.xyz/mobile/api/**` (API).

---

## 1. Ce este livrat astăzi

| Componentă | Locație | Stare |
|---|---|---|
| Gateway (API + coadă + stări + aprobări + WebSocket agenți) | `/opt/deepseek-proxy/devagent/gateway` | ✅ implementat (35 teste automate) |
| Protocol agent (WS + heartbeat + checkpoint + aprobare) | `app/api_agent.py`, secțiunea 7 | ✅ implementat |
| Agent macOS de referință | `/opt/deepseek-proxy/devagent/agent/devagent_agent.py` | ✅ implementat |
| Flotă multi-agent (mai multe laptopuri simultan) | gateway + PWA + agent | ✅ implementat (18 teste): status agregat, targetare per agent, capabilități, „forget" pentru agenții pensionați |
| Autentificare + API keys (login, sesiuni, creare/revocare) | `app/auth.py`, `app/api_auth.py` | ✅ implementat (token hashing, scope-uri admin/pwa/agent, sesiuni din parolă) |
| Provideri AI (CRUD + selecție auto/manuală per sarcină) | `app/api_providers.py`, UI Settings | ✅ implementat — nu atinge canalul agent ↔ gateway |
| Simulator integrat | — | ✅ eliminat definitiv (doar agenți reali) |
| Binare agent (macOS arm64/x64, Windows x64) | `.github/workflows/release-agent.yml` | 🟡 workflow gata; release-ul se creează la tag `agent-v*` (sau manual) |
| PWA în `DevAgent-mobile` | `/opt/deepseek-proxy/mobile` | ✅ adaptat pentru `/mobile`, token + URL configurabil, `npm run build` OK |
| Aplicație iOS nativă (Capacitor → Xcode) | `mobile/capacitor.config.ts`, `mobile/ios` | 🟡 Capacitor configurat în repo; proiectul Xcode se generează pe MacBook (pasul 6) |
| Ops: Redis, systemd, regulă tunnel, backup | `devagent/systemd`, `/etc/deepseek-devagent.env` | ✅ aplicate pe VPS (Redis activ, `devagent-gateway.service`, regulă `/mobile*` în ambele tuneluri, `backup.sh` extins) |

Cerințe acoperite de gateway:

* **coadă de sarcini** care funcționează și când MacBook-ul e offline (`queued` rămâne în coadă, dispatch-ul se face automat la reconectare);
* **identificator unic** per sarcină: `task-<10 hex>`;
* **stări**: `queued`, `running`, `awaiting_approval` (alias acceptat `waiting_approval`), `completed`, `failed`, `cancelled`;
* **evenimente și progres în timp real**: SSE (`/mobile/api/events`, `/mobile/api/tasks/{id}/events`) alimentat din Redis pub/sub, cu replay după `Last-Event-ID`;
* **aprobare înainte de aplicare**: propunere cu `hash` (optimistic locking), granularitate per sarcină / per fișier / per hunk, mai multe gate-uri în ordine (`plan → diff → apply → commit`);
* **reconectare și reluare sigură**: heartbeat + watchdog, sarcina întreruptă revine în coadă și se reia din ultimul checkpoint raportat de agent.

### Verificat live pe acest VPS

```
GET  http://127.0.0.1:8082/healthz      -> {"status":"ok","version":"1.0.0","agentSockets":0,"eventBus":"redis"}
GET  https://<host>/mobile/              -> 200, se încarcă shell-ul PWA (Chromium real)
GET  https://<host>/mobile/api/health    -> 401 (ajunge la gateway, cere token)
POST /mobile/api/tasks + aprobare        -> task queued -> running -> awaiting_approval -> completed
POST /mobile/api/tasks/{id}/approval     -> propunere respinsă/greșită => 409; aprobată => aplicată pe branch
reconectare agent în timpul aprobării    -> task_resume action=apply_approved -> completed
pytest (gateway)                         -> 12 passed
"$DEVAGENT_AGENT_PYTHON" devagent/tests/smoke.sh -> ✅ (agent real + coadă + aprobare + commit + reluare)
```

Atenție la testarea din linia de comandă: Cloudflare are protecție anti-bot pe zonă, deci
`curl` primește `403 Your request was blocked` pe rute care nu sunt exceptate
(`/healthz` și `/v1/*` sunt exceptate). Din browser / din WebView-ul iOS totul trece normal
- testați cu un browser real, nu cu `curl`.

---

## 2. Arhitectură

```mermaid
flowchart LR
    subgraph Phone["📱 Telefon (PWA / app iOS)"]
        UI[React + Vite + Capacitor]
    end

    subgraph VPS["☁️ VPS (127.0.0.1)"]
        GW["DevAgent Gateway :8082\nFastAPI /mobile"]
        DB[(SQLite\n/var/lib/devagent)]
        RD[(Redis pub/sub\nevenimente live)]
        PROXY["DeepSeek proxy :8081\n(v1/chat/completions)"]
    end

    subgraph Mac["💻 MacBook"]
        AG[DevAgent agent\nPython + WebSocket]
        WT[git worktree izolat\nagent/task-xxxxxxxxxx]
    end

    UI -->|"HTTPS /mobile/api + SSE"| GW
    GW --- DB
    GW --- RD
    AG -->|"WebSocket outbound (wss)"| GW
    AG --> WT
    AG -->|"plan / patch (LLM)"| PROXY
    PROXY -->|"api.deepseek.com"| LLM[(DeepSeek)]
```

Fluxul unei sarcini:

```
queued ──dispatch──▶ running ──agent: approval_request──▶ awaiting_approval
   ▲                    │                                      │
   │                    │◀───────── approve (hash valid) ────────┤
   │                    │                                      │
   └─ watchdog/reluare ─┘                              reject ─▶ failed
                        └──── agent: result ────▶ completed
```

---

## 3. Plan de implementare

Fiecare fază este independentă și verificabilă. Statusul reflectă ce este deja
în repo.

### Faza 0 - fundație (✅ gata)
1. Structură monorepo în workspace-ul existent: `devagent/gateway`, `devagent/agent`, `devagent/systemd`, `mobile/` (repo-ul `DevAgent-mobile`).
2. Config 12-factor (`app/config.py`, `gateway/env.example`, `agent/agent.env.example`).
3. Persistență SQLite (`app/db.py`): `tasks`, `task_events` (seq global = `Last-Event-ID`), `approvals`, `checkpoints`, `agents`, `kv`.

### Faza 1 - coadă, stări și identitate (✅ gata)
4. `TaskService.create_task` generează `task-<10 hex>`, acceptă `idempotencyKey`, alege gate-urile implicite pe mod (`supervised`/`dry-run` → `diff`, `autonomous` → niciunul).
5. Sarcina rămâne `queued` cu agentul offline; la reconectare `resume_tasks_for_agent` + `dispatch_pending` o preiau imediat.

### Faza 2 - evenimente live (✅ gata)
6. Fiecare tranziție scrie un eveniment în DB **și** îl publică pe `devagent:events` (Redis), cu fallback in-process dacă Redis lipsește.
7. SSE pentru PWA: `init` + `message` per sarcină, `connected` + `agent_status` global, keep-alive la 15s.

### Faza 3 - aprobări (✅ gata)
8. Propunerea este creată de agent (`approval_request`) cu `diff`, listă de fișiere și itemi per-fișier/per-hunk derivați din diff.
9. Operatorul decide cu `{action, hash, comment?, items?}`; hash greșit → `409`, decizie dublă → `409`.
10. Gate-uri multiple în ordine (`plan → diff → apply → commit`); dacă toate itemii sunt respinși, propunerea devine respingere.
11. Expirare opțională a propunerii (`DEVAGENT_APPROVAL_TIMEOUT_SECONDS`).

### Faza 4 - reconectare și reluare (✅ gata)
12. Heartbeat agent → watchdog (`app/scheduler.py`): lipsă heartbeat `N` secunde ⇒ agent `offline`, sarcina `running` revine în `queued` cu `interrupted_at`, propunerile rămase `pending` rămân valide.
13. La reconectare, gateway-ul trimite `task_resume` cu checkpoint + acțiunea corectă: `resume`, `await_approval` sau `apply_approved`.
14. Agentul persistă local starea (`~/.devagent/state/<task>.json`) și reutilizează worktree-ul izolat.

### Faza 5 - PWA servit la `/mobile` (🟡 pasul 5)
15. `base: '/mobile/'`, manifest `start_url/scope: '/mobile/'`, `workbox.navigateFallback`, denylist `/mobile/api`.
16. Client API cu URL + token configurabile (necesar și pentru aplicația iOS).
17. Build → `dist/` servit de gateway (`DEVAGENT_STATIC_DIR`), SPA fallback pentru deep links.

### Faza 6 - aplicație iOS nativă (🟡 pasul 6)
18. Capacitor 7 (`@capacitor/core|cli|ios`) peste același build React.
19. Ecran de setări: URL gateway + token (WKWebView nu are same-origin cu API-ul).
20. `npx cap sync ios` → proiect Xcode → rulare pe device / TestFlight.

### Faza 7 - operațiuni (🟡 pașii 2-4, 8)
21. Redis local (`127.0.0.1:6379`), serviciu systemd `devagent-gateway.service`, regulă de path în tunnelul Cloudflare existent.
22. Smoke test end-to-end + integrare în `backup.sh`.
23. Enrolarea MacBook-ului (launchd) și testul de reconectare „la minut real”.

### Faza 8 - flotă multi-agent (✅ gata)
24. Status agregat în `/api/agent/status` (`agents[]`, `onlineAgents`, `totalAgents`) și listă dedicată `/api/agents` (cu `online`, `secondsSinceHeartbeat`, `capabilități`).
25. Targetare: `agentId` (pin pe o mașină) și `requiredCapabilities` (ex. `["pull-request"]`); dispatch-ul alege primul agent liber care satisface cerințele, altfel sarcina rămâne în coadă.
26. UI: badge de flotă în header, card de flotă în Dashboard, listă completă + „forget" în Settings, selector „Run on" în ecranul de sarcină nouă.
27. Restart de gateway: `reconcile_on_startup` marchează toți agenții `offline` și repune sarcinile `running` în coadă (cu `interrupted_at`), ca să nu rămână nici un agent „fantomă" sau sarcină blocată.

---

## 4. Instrucțiuni: serverul (gateway)

Presupune Ubuntu (ca VPS-ul actual), acces root, proiectul în `/opt/deepseek-proxy`.

### 4.1 Redis (pub/sub pentru evenimente live)

```sh
apt-get update && apt-get install -y redis-server
# ascultă doar local; verificare
ss -ltnp | grep 6379          # trebuie 127.0.0.1:6379
redis-cli ping                # PONG
systemctl enable --now redis-server
```

Fără Redis gateway-ul funcționează (fan-out in-process), dar un al doilea worker
uvicorn sau un restart nu ar mai distribui evenimentele către toate conexiunile.

### 4.2 Cont de serviciu, permisiuni, secrete

```sh
# user fără privilegii + director de date
useradd --system --home-dir /var/lib/devagent --shell /usr/sbin/nologin devagent || true
install -d -o devagent -g devagent -m 0750 /var/lib/devagent

# secrete (niciun secret în git!)
install -m 600 -o root -g root devagent/gateway/env.example /etc/deepseek-devagent.env
python3 - <<'PY'
import secrets
print("DEVAGENT_TOKEN      =", secrets.token_hex(32))
print("DEVAGENT_AGENT_TOKEN=", secrets.token_hex(32))
PY
nano /etc/deepseek-devagent.env      # completați cele două tokenuri

# gateway-ul citește PWA-ul compilat
chmod o+rX /opt/deepseek-proxy /opt/deepseek-proxy/mobile
```

### 4.3 Mediul Python

```sh
cd /opt/deepseek-proxy/devagent/gateway
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q          # 12 teste
```

### 4.4 Serviciu systemd

```sh
install -m 644 devagent/systemd/devagent-gateway.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now devagent-gateway
systemctl status devagent-gateway --no-pager
journalctl -u devagent-gateway -f
curl -s http://127.0.0.1:8082/healthz
# {"status":"ok","version":"1.0.0","agentSockets":0,"eventBus":"redis"}
```

### 4.5 Expunere publică prin tunnelul existent

⚠️ Pe acest VPS hostname-ul `deepseek-api.cs-hub.xyz` este declarat în **două** tuneluri
Cloudflare, ambele active:

| Unit systemd | Config | Rol |
|---|---|---|
| `cloudflared` (`postgres-tunnel`) | `/etc/cloudflared/config.yml` | regula veche `deepseek-api.cs-hub.xyz → 8081` |
| `vacanta-tunnel` (`vacanta-arcadia`) | `/root/.cloudflared/config.yml` | restul hostname-urilor |

Regula de path trebuie să existe în configul tunelului care servește hostname-ul
(`/mobile/api/health` a răspuns 404 de la proxy-ul de pe 8081 până am adăugat regula în
`/etc/cloudflared/config.yml`). Din acest motiv regula este prezentă acum în **ambele**
configuri, iar schimbarea se validează local înainte de restart:

```yaml
ingress:
  - hostname: deepseek-api.cs-hub.xyz
    path: /mobile*            # PWA + API + WebSocket agent
    service: http://127.0.0.1:8082
  - hostname: deepseek-api.cs-hub.xyz
    service: http://127.0.0.1:8081   # proxy-ul DeepSeek existent (neatins)
  - service: http_status:404
```

```sh
cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
cloudflared --config /etc/cloudflared/config.yml tunnel ingress rule https://deepseek-api.cs-hub.xyz/mobile/  # trebuie sa indice 8082
cp /etc/cloudflared/config.yml "/etc/cloudflared/config.yml.bak-$(date +%F-%H%M%S)"   # inainte de orice editare
systemctl restart vacanta-tunnel cloudflared
# verificare realista (browser, nu curl): https://deepseek-api.cs-hub.xyz/mobile/
```

---

## 5. Instrucțiuni: PWA servit la `/mobile`

Repo-ul `mobile/` face parte din workspace (clone al `CsepregiArtur/DevAgent-mobile`).

### 5.1 Sub-cale `/mobile` (`mobile/vite.config.ts`)

```ts
export default defineConfig(() => ({
  base: '/mobile/',
  plugins: [
    react(), tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        id: '/mobile/',
        start_url: '/mobile/',
        scope: '/mobile/',
        icons: [
          { src: '/mobile/pwa-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/mobile/pwa-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/mobile/pwa-maskable-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        navigateFallback: '/mobile/index.html',
        navigateFallbackDenylist: [/^\/mobile\/api/, /^\/mobile\/agent/],
      },
    }),
  ],
}));
```

### 5.2 Client API cu URL + token configurabile (`mobile/src/services/api.ts`)

```ts
const DEFAULT_BASE = import.meta.env.BASE_URL.replace(/\/$/, ''); // '/mobile'
export function getGatewayBase(): string {
  return localStorage.getItem('devagent.baseUrl') || DEFAULT_BASE;
}
export function getToken(): string {
  return localStorage.getItem('devagent.token') || '';
}
function authHeaders(extra: Record<string, string> = {}) {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}
// toate fetch-urile: fetch(`${getGatewayBase()}/api/...`, { headers: authHeaders({...}) })
// SSE (EventSource nu poate trimite headere): `${base}/api/events?token=${encodeURIComponent(token)}`
```

### 5.3 Build + servire

```sh
cd /opt/deepseek-proxy/mobile
npm install --legacy-peer-deps     # sau: bun install (există bun.lock)
npm run build                      # produce dist/
systemctl restart devagent-gateway # servește dist/ la /mobile
curl -sI https://deepseek-api.cs-hub.xyz/mobile/ | head -3
```

Pe telefon: deschide `https://deepseek-api.cs-hub.xyz/mobile`, adaugă tokenul în
ecranul **Settings** (se salvează în `localStorage`), apoi „Add to Home Screen”.
Sau, mai rapid, cu link de configurare unică (tokenul se salvează pe dispozitiv și
dispare din bara de adrese):

```
https://deepseek-api.cs-hub.xyz/mobile/?baseUrl=https://deepseek-api.cs-hub.xyz/mobile&token=<DEVAGENT_TOKEN>
```
Dev local fără build: `npm run dev` pornește serverul Express mock din repo
(`server.ts`) — util pentru UI, dar fără coadă/reluare reale.

---

### 5.4 Autentificare și API keys (ca la un furnizor)

Primul ecran al aplicației este **login**. Două moduri:

* **API token** — lipești o cheie `dvk_…` (generată din Settings sau din CLI);
* **parolă de admin** — doar dacă este configurată pe server
  (`DEVAGENT_ADMIN_PASSWORD_HASH`, generat cu `python -m app.cli hash-password`);
  emite un token de sesiune `dvs_…` cu durată limitată (`DEVAGENT_SESSION_TTL_HOURS`, implicit 12h).

În **Settings → API keys** (necesită scope `admin`) poți:

| Acțiune | Efect |
|---|---|
| Generate (name + kind + expirare opțională) | creează o cheie `pwa` / `agent` / `admin`; **plaintext-ul e afișat o singură dată** |
| Revoke | cheia devine imediat `401` |
| Delete | șterge intrarea din DB |

Scope-uri: `admin` (tot), `pwa` (aplicația), `agent` (workerul). Gateway-ul păstrează
doar SHA-256 al tokenului; listele arată doar prefixul + ultima utilizare.
Tokenurile din environment (`DEVAGENT_TOKEN`, `DEVAGENT_AGENT_TOKEN`) rămân valide ca
**bootstrap** și nu pot fi revocate din UI (se rotesc în `/etc/deepseek-devagent.env`).

```sh
# din shell, pe server
cd /opt/deepseek-proxy/devagent/gateway
.venv/bin/python -m app.cli token --name iPhone-Artur --kind pwa
.venv/bin/python -m app.cli tokens

# login cu parolă (scrie hash-ul scrypt în env, apoi repornește serviciul)
printf '%s\n' 'parola-ta-lunga' | .venv/bin/python -m app.cli hash-password --stdin
systemctl restart devagent-gateway
curl -s http://127.0.0.1:8082/mobile/api/auth/config   # passwordLogin: true
```

Fără hash configurat, ecranul de login arată doar câmpul de token (butonul
**Password** este dezactivat). O cheie `admin` generată din Settings are aceleași
drepturi ca sesiunea de parolă, dar nu expiră.

### 5.5 Provideri AI (auto sau manual, per sarcină)

**Settings → AI providers** gestionează endpoint-urile LLM (OpenAI-compatible,
Anthropic, Google, custom): nume, base URL, cheie API, listă de modele, model implicit,
enabled/disabled, **default**.

* Sarcina poate fixa un provider (`providerId`) sau poate lăsa gateway-ul să aleagă
  **default-ul** (respectiv primul provider activ);
* alegerea **călătorește cu sarcina** în `task_assign` / `task_resume` (inclusiv cheia,
  peste WebSocket-ul TLS) — deci **schimbarea providerului nu afectează conexiunea
  agent ↔ gateway**, nu reconectează nimic și nu întrerupe sarcina curentă;
* fără niciun provider configurat, agentul folosește propriul `~/.devagent/agent.env`.

> Simulatorul integrat a fost **eliminat**: coada este deservită exclusiv de agenți
> reali. Endpoint-ul `/api/agent/simulate-toggle` nu mai există.

---

## 6. Instrucțiuni: aplicația iOS (nativă prin Capacitor)

Se rulează **pe MacBook** (are nevoie de Xcode); rezultatul este un proiect Xcode
în `mobile/ios`, din care faci build pe device sau în TestFlight.

### 6.1 Instalare și inițializare

```sh
cd /opt/deepseek-proxy/mobile        # sau clonează repo-ul pe Mac
npm install --legacy-peer-deps
npm install @capacitor/core@^7 @capacitor/app@^7 @capacitor/preferences@^7
npm install -D @capacitor/cli@^7 @capacitor/ios@^7

npx cap init "DevAgent" xyz.cshub.devagent --web-dir dist
```

`mobile/capacitor.config.ts`:

```ts
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'xyz.cshub.devagent',
  appName: 'DevAgent',
  webDir: 'dist',
  bundledWebRuntime: false,
  server: { androidScheme: 'https' },
  ios: { contentInset: 'always', limitsNavigationsToAppBoundDomains: false },
};

export default config;
```

### 6.2 Build web + proiect Xcode

```sh
npm run build
npx cap add ios          # o singură dată: creează mobile/ios
npx cap sync ios         # copiază dist/ + pluginurile native
npx cap open ios         # deschide Xcode (necesită CocoaPods: brew install cocoapods)
```

În Xcode: alege signing team, bundle id `xyz.cshub.devagent`, rulează pe iPhone.
Pentru distribuție: Product → Archive → Distribute App → TestFlight.

După orice modificare în `src/`: `npm run build && npx cap sync ios`.

### 6.3 De ce e nevoie de URL + token în aplicație

În shell-ul nativ, originea este `capacitor://localhost`, deci `fetch('/api/...')`
nu ajunge la gateway. Din acest motiv clientul are URL configurabil (implicit
`https://deepseek-api.cs-hub.xyz/mobile`) + tokenul introdus în **Settings**
(`@capacitor/preferences` sau `localStorage`). Gateway-ul acceptă deja aceste
origini prin CORS (`capacitor://localhost`, `ionic://localhost`).

### 6.4 Opțional: notificări push

Se adaugă după ce fluxul de bază funcționează: `@capacitor/push-notifications` +
APNs; evenimentele relevante sunt `approval_required` („ai o modificare de
aprobat”) și `status_change` pe `completed`/`failed`.

---

## 6.5 Distribuție: binare în GitHub Releases (macOS + Windows)

Agentul este împachetat cu PyInstaller într-un **singur executabil** (fără Python
pe mașina utilizatorului) și publicat în GitHub Releases de workflow-ul
`.github/workflows/release-agent.yml`.

```sh
# creează release-ul + atașează binarele (necesită scope `workflow`)
gh workflow run release-agent.yml -f tag=agent-v1.0.0
# sau: git tag agent-v1.0.0 && git push origin agent-v1.0.0
```

Active produse: `devagent-agent-macos-arm64`, `devagent-agent-macos-x64`,
`devagent-agent-windows-x64.exe` (+ `.sha256`). Aplicația le referențiază prin
`/releases/latest/download/<asset>` (cardul **Install the agent**).

La prima rulare, binarul pornește automat **wizard-ul** (`--setup`) dacă nu există
fișier de configurare. Wizard-ul are **6 pași** și explică la fiecare ce trebuie introdus
și de unde vine valoarea:

| Pas | Ce întreabă | De unde |
|---|---|---|
| 1/6 | Gateway WebSocket URL | Enter = gateway-ul găzduit; `ws://127.0.0.1:8082/mobile/agent/ws` pentru unul local |
| 2/6 | Agent API key (`dvk_…`) | aplicația → Settings → API keys → kind `agent` → Generate (se afișează o singură dată) |
| 3/6 | Agent id | nume unic pe mașină (implicit = hostname), apare în flotă și în „Run on” |
| 4/6 | Foldere permise | căi absolute, separate prin virgulă (ex. `~/dev`); agentul refuză orice e în afara lor |
| 5/6 | LLM endpoint | **Enter** = folosește providerul din aplicație (recomandat); completați doar dacă mașina trebuie să cheme un endpoint propriu |
| 6/6 | Comportament | comandă de test (Enter = autodetectare), push/PR (implicit nu), dry-run (implicit nu) |

La final afișează un **sumar** (cheia mascată), scrie `~/.devagent/agent.env` (0600),
verifică handshake-ul și tipărește pașii următori (launchd / systemd / Task Scheduler).
Dacă verificarea eșuează, răspunsurile rămân salvate și vă spune exact ce înseamnă
eroarea (`401` token greșit/revocat, `403` proxy/CDN, DNS).

> Binarele nu sunt semnate: macOS afișează avertisment Gatekeeper →
> `xattr -dr com.apple.quarantine devagent-agent`; Windows SmartScreen →
> „More info → Run anyway".

```sh
# macOS Apple Silicon
curl -L -o devagent-agent https://github.com/CsepregiArtur/DevAgent-mobile/releases/latest/download/devagent-agent-macos-arm64
chmod +x devagent-agent && xattr -dr com.apple.quarantine devagent-agent
./devagent-agent --setup

# Windows (PowerShell)
Invoke-WebRequest -Uri https://github.com/CsepregiArtur/DevAgent-mobile/releases/latest/download/devagent-agent-windows-x64.exe -OutFile devagent-agent.exe
.\devagent-agent.exe --setup
```

### 6.6 Transport alternativ: HTTPS long-poll (când WebSocket-ul e blocat)

Unele rețele (WAF-ul Cloudflare al zonei, proxy corporativ) răspund `403` la
upgrade-ul WebSocket, deși traficul HTTPS obișnuit trece. Agentul are aceleași
mesaje și pe un transport bazat pe POST:

| Endpoint | Rol |
|---|---|
| `POST /mobile/api/agent/send` | agent → gateway (`hello`, `heartbeat`, `checkpoint`, `approval_request`, `result`); răspunsul conține ack-urile |
| `POST /mobile/api/agent/poll` | gateway → agent, long-poll până la 50s (`welcome`, `task_assign`, `task_resume`, `approval_decision`, `cancel`) |
| `POST /mobile/api/agent/disconnect` | închidere curată a canalului |

Autentificare: `Authorization: Bearer <agent key>` (nu mai e nevoie de `?token=`).
Gateway-ul ține același registry de agenți, deci coada, dispatch-ul, aprobările și
reluarea funcționează identic; un agent nu poate avea simultan WebSocket și long-poll
(al doilea primește `409`).

```sh
# DEVAGENT_TRANSPORT: auto (implicit) | ws | http
DEVAGENT_TRANSPORT="http" ./devagent-agent --verbose
```

Cu `auto`, agentul încearcă WebSocket-ul și, la primul `403`, comută singur pe
long-poll (log: `websocket refused with 403 … switching to HTTPS long-poll`).
Wizard-ul verifică și el fallback-ul: dacă socketul e refuzat dar HTTPS-ul trece,
scrie `✅ Connection OK — enrolled as … (HTTPS long-poll; WebSocket refused)`.

> Long-poll-ul ține o cerere deschisă ~25s. Timeout-ul de origine Cloudflare este
> 100s, deci încape; dacă folosești alt proxy, verifică `proxy_read_timeout`.

---

## 7. Instrucțiuni: agentul de pe MacBook

### 7.1 Instalare (binar din GitHub Releases)

```sh
# macOS Apple Silicon  (Intel: devagent-agent-macos-x64)
curl -L -o devagent-agent https://github.com/CsepregiArtur/DevAgent-mobile/releases/latest/download/devagent-agent-macos-arm64
chmod +x devagent-agent
xattr -dr com.apple.quarantine devagent-agent   # build nesemnat
./devagent-agent --setup                        # wizard: URL, token agent, id, workspace-uri

# Windows (PowerShell)
Invoke-WebRequest -Uri https://github.com/CsepregiArtur/DevAgent-mobile/releases/latest/download/devagent-agent-windows-x64.exe -OutFile devagent-agent.exe
.\devagent-agent.exe --setup
```

Wizard-ul scrie `~/.devagent/agent.env` (0600) și verifică handshake-ul. Verificări:

```sh
./devagent-agent --check      # config + git + workspaces
./devagent-agent --version
./devagent-agent --verbose    # rulare în prim-plan
```

Alternativa din sursă (necesită Python 3.9+ și `pip install websockets`):

```sh
mkdir -p ~/devagent && cp -r agent ~/devagent/
cp agent/agent.env.example ~/.devagent/agent.env && chmod 600 ~/.devagent/agent.env
python3 ~/devagent/agent/devagent_agent.py --setup
```

### 7.2 Pornire automată (launchd)

```sh
cp /opt/deepseek-proxy/devagent/agent/com.devagent.agent.plist ~/Library/LaunchAgents/
# ajustați căile din plist dacă nu folosiți /Users/artur
launchctl load -w ~/Library/LaunchAgents/com.devagent.agent.plist
tail -f ~/.devagent/agent.out.log
```

Agentul se conectează **outbound** (WebSocket) - nu deschide niciun port pe Mac.
Laptopul poate intra în sleep, poate pierde Wi-Fi; la revenire se reconectează cu
backoff exponențial (1s → 30s) și reia sarcinile primite cu `task_resume`.

### 7.3 Ce face agentul pentru fiecare sarcină

1. rezolvă workspace-ul (whitelist `DEVAGENT_WORKSPACES`) și creează un **git worktree izolat** pe branch-ul `agent/task-xxxxxxxxxx` (`~/.devagent/worktrees/...`);
2. checkpoint `workspace-ready` → gateway;
3. generează patch-ul: LLM (proxy privat DeepSeek) sau, fără endpoint LLM, un `DEVAGENT_PLAN.md` de revizuit;
4. checkpoint `diff-ready` și `approval_request` către gateway (diff + fișiere + itemi per hunk);
5. așteaptă decizia operatorului (poate fi luată și când MacBook-ul e offline);
6. aplică doar ce s-a aprobat (`git apply`, filtrat per fișier/hunk), rulează testele (comandă aprobată separat), comite pe branch;
7. trimite `result` (`completed`/`failed`) cu sumar, fișiere, ieșirea testelor, branch.

`DEVAGENT_DRY_RUN=true` generează propunerea fără a aplica nimic (mod verificare).

---

## 8. Verificare end-to-end

Test automat (creează un repo git temporar, pornește agentul real, aprobă propunerea,
verifică commit-ul pe branch și reluarea după reconectare):

```sh
# pe server: interpretorul care are pachetul websockets
python3 -m venv /opt/deepseek-proxy/devagent/agent/.venv
/opt/deepseek-proxy/devagent/agent/.venv/bin/pip install websockets
DEVAGENT_AGENT_PYTHON=/opt/deepseek-proxy/devagent/agent/.venv/bin/python \
  bash /opt/deepseek-proxy/devagent/tests/smoke.sh
```

Verificări manuale:

```sh
# 0) token în shell (NU îl muta în git/chat)
set -a; . /etc/deepseek-devagent.env; set +a
B=https://deepseek-api.cs-hub.xyz/mobile

# 1) gateway + bus de evenimente
curl -s $B/api/health?token=$DEVAGENT_TOKEN

# 2) flux live: SSE într-un terminal, acțiuni în altul
curl -sN "$B/api/events?token=$DEVAGENT_TOKEN" &
curl -s -X POST "$B/api/tasks?token=$DEVAGENT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"Adaugă rate limiting pe /login","mode":"supervised"}' | jq .

# 3) coada cu agentul offline: sarcina rămâne 'queued'
curl -s "$B/api/tasks?token=$DEVAGENT_TOKEN" | jq '.tasks[] | {id,status}'

# 4) agent online -> 'running' -> 'awaiting_approval' (apare propunerea)
# 5) aprobă (hash-ul din propunere) sau respinge
curl -s -X POST "$B/api/tasks/<task-id>/approval?token=$DEVAGENT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","hash":"<hash>","comment":"ok"}'

# 6) testul de reconectare: închide MacBook-ul (sau Wi-Fi) în timpul execuției,
#    apoi redeschide: în History trebuie să vezi 'interruptedAt' + reluarea din checkpoint.
```

Fără MacBook poți valida tot fluxul cu simulatorul integrat:
`Settings → Agent Worker Simulator` (sau `POST /api/agent/simulate-toggle`).

---

## 9. Referință API (PWA)

Toate rutele cer token: `Authorization: Bearer <DEVAGENT_TOKEN>` sau `?token=`
(necesar pentru `EventSource`, care nu poate trimite headere). `/healthz` este public.

| Metodă | Rută | Descriere |
|---|---|---|
| GET | `/mobile/api/auth/config` | public: dacă există login cu parolă, versiunea |
| POST | `/mobile/api/auth/login` | `{token}` sau `{password}` → `{token, issued, auth}` |
| GET | `/mobile/api/auth/me` | scope-uri + `canManageTokens` / `canUseTasks` / `canRunAgent` |
| POST | `/mobile/api/auth/logout` | revocă sesiunea (`dvs_…`) |
| GET | `/mobile/api/tokens` | chei (prefix + status, fără plaintext) — `admin` |
| POST | `/mobile/api/tokens` | `{name, kind, expiresInDays?}` → cheia o singură dată — `admin` |
| POST | `/mobile/api/tokens/{id}/revoke` | revocare imediată — `admin` |
| DELETE | `/mobile/api/tokens/{id}` | ștergere — `admin` |
| GET | `/mobile/api/providers` | provideri AI (cheia mascată) + `canManage` |
| POST | `/mobile/api/providers` | adaugă provider — `admin` |
| PATCH | `/mobile/api/providers/{id}` | modifică (nume, URL, cheie, modele, enabled) — `admin` |
| POST | `/mobile/api/providers/{id}/default` | setează default (unic) — `admin` |
| DELETE | `/mobile/api/providers/{id}` | șterge — `admin` |
| GET | `/mobile/api/agent/status` | stare agent + `isSimulated` |
| POST | `/mobile/api/agent/heartbeat` | heartbeat REST (compat; oprește simulatorul) |
| POST | `/mobile/api/agent/simulate-toggle` | activează/dezactivează simulatorul |
| GET | `/mobile/api/agents` | flota: toți agenții cunoscuți + `online`, `secondsSinceHeartbeat`, `capabilities`, `onlineAgents`, `primaryAgentId` |
| DELETE | `/mobile/api/agents/{id}` | uită un agent deconectat (`409` dacă e conectat) |
| GET | `/mobile/api/tasks?status=&search=&limit=&offset=` | listare filtrare/paginare |
| POST | `/mobile/api/tasks` | `{instruction, workspace?, mode?, model?, gates?, idempotencyKey?}` → `201` |
| GET | `/mobile/api/tasks/{id}` | sarcina cu `events`, `approval`, `approvals`, `checkpoints` |
| POST | `/mobile/api/tasks/{id}/cancel` | anulare (agentul e notificat) |
| POST | `/mobile/api/tasks/{id}/retry` | re-încoadă, checkpoint-uri vechi șterse |
| POST | `/mobile/api/tasks/{id}/approval` | `{action, hash, comment?, items?}` → `200` / `400` / `409` |
| GET | `/mobile/api/tasks/{id}/checkpoints` | punctele de reluare ale sarcinii |
| GET | `/mobile/api/tasks/{id}/events` | SSE: `init` apoi `message` per eveniment, replay `Last-Event-ID` |
| GET | `/mobile/api/events` | SSE global: `connected`, `agent_status`, `message` |
| GET | `/mobile/api/diagnostics` | diagnostice gateway/agent/coadă/protocol |
| GET | `/mobile/api/workspaces` | workspace-uri disponibile |

Exemplu de itemi pentru revizuire per fișier/hunk în `approval`:

```json
{
  "action": "approve",
  "hash": "sha256:...",
  "items": [
    { "path": "src/routes/auth.ts", "decision": "approve", "hunks": [1, 2] },
    { "path": "src/middleware/rate-limiter.ts", "decision": "reject" }
  ]
}
```

---

## 10. Protocol WebSocket agent

`wss://<host>/mobile/agent/ws?token=<DEVAGENT_AGENT_TOKEN>&agentId=<id>`

**agent → gateway**

| `type` | Câmpuri principale | Răspuns |
|---|---|---|
| `hello` | `hostname, os, version, workspace, capabilities[], batteryLevel?, activeTaskId?` | `welcome` (include `resumedTasks[]`) |
| `heartbeat` | `activeTaskId?, stats` | `heartbeat_ack` |
| `event` / `progress` / `log` | `taskId, eventType, message, details?, stepNumber?, totalSteps?, currentStep?` | `event_ack` |
| `checkpoint` | `taskId, name, payload, idempotencyKey` | `checkpoint_ack` |
| `approval_request` | `taskId, gate, summary, diff, hash, files[]` | `approval_ack` |
| `result` | `taskId, status: completed\|failed, result?, error?` | `result_ack` |
| `ping` | - | `pong` |

**gateway → agent**

| `type` | Semnificație |
|---|---|
| `welcome` | handshake: intervale de heartbeat, timeout aprobare, `resumedTasks` |
| `task_assign` | sarcină nouă: `action: "start"` |
| `task_resume` | continuare: `action` = `resume` (din checkpoint), `await_approval` (propunere în așteptare) sau `apply_approved` (aproape cât laptopul era offline) |
| `approval_decision` | decizia operatorului + `decisions` per fișier/hunk + `nextGate` |
| `cancel` | anulare |
| `error` | eroare de protocol/domaină (ex. gate în afara ordinii) |

Cheile de idempotență (`idempotencyKey`) fac re-livrarea sigură: un checkpoint
repetat nu creează duplicate.

---

## 11. Securitate

* Token partajat (PWA) + token separat pentru agenți; comparație `hmac.compare_digest`.
* Staticul PWA este public (are nevoie de el browserul), dar `/mobile/api/**` și `/mobile/agent/**` cer token.
* Gateway-ul ascultă doar pe `127.0.0.1`; expunerea trece exclusiv prin Cloudflare Tunnel.
* Secretele stau în `/etc/deepseek-devagent.env` (600) și `~/.devagent/agent.env` (600) - **nu** în git.
* Agentul nu are porturi de intrare; toate conexiunile sunt outbound.
* Patch-ul se aplică doar după aprobare, în **worktree izolat**, nu în working tree-ul tău.
* Comenzile shell rulează doar cu aprobare explicită (`DEVAGENT_APPROVE_COMMANDS=true`).

---

## 12. Troubleshooting

| Simptom | Cauză probabilă | Fix |
|---|---|---|
| `401 Invalid gateway credentials` | token lipsă/greșit | trimite `Authorization: Bearer` sau `?token=`; verifică `DEVAGENT_TOKEN` |
| `/mobile` → `503 PWA bundle not built yet` | `dist/` lipsă | `cd mobile && npm run build`; verifică `DEVAGENT_STATIC_DIR` |
| Agentul apare `offline` deși procesul rulează | Cloudflare face **Browser Integrity Check** și răspunde `403` la handshake-ul WebSocket fără headere de browser | agentul trimite deja `User-Agent`/`Accept`/`Accept-Language`/`Origin` (`DEVAGENT_BROWSER_HEADERS`, implicit `true`); verifică cu `python3 - <<'PY'` … `websockets.connect(wss://…/mobile/agent/ws?token=…&agentId=probe)` — `403` = filtru de rețea, nu gateway-ul |
| Sarcina rămâne `queued` | niciun agent online, simulator oprit | pornește agentul sau simulatorul; `journalctl -u devagent-gateway -f` |
| Nu apar evenimente live | Redis căzut | `redis-cli ping`; gateway-ul funcționează și fără Redis (fan-out local) |
| `409 hash mismatch` | propunerea s-a schimbat | reîncarcă sarcina și aprobă hash-ul curent |
| `409 cannot be reviewed yet; expected 'plan'` | gate-uri în ordine greșită | aprobă întâi gate-ul cerut |
| Patch-ul nu se aplică | conflict în worktree | `POST /tasks/{id}/retry` (șterge checkpoint-urile vechi) |

---

## 13. Backup

✅ Integration deja aplicată: `backup.sh` include acum și
`/etc/deepseek-devagent.env`, `/etc/systemd/system/devagent-gateway.service`,
`/etc/cloudflared` (configul care rutează `/mobile`) și `/var/lib/devagent`
(istoricul sarcinilor), cu `node_modules` exclus din arhivă. Arhiva conține secrete,
prin urmare rămâne privată (mode 600, director 700). Metadatele includ
`DEVAGENT-STATE.txt` (integrity check SQLite + numărul de sarcini pe status).

---

## 14. Stare implementare și ce mai rămâne

### Implementat și verificat

| Domeniu | Stare |
|---|---|
| Coadă, stări, id-uri unice, evenimente SSE (replay) | ✅ 35 teste automate |
| Aprobări cu hash, per fișier/hunk, gate-uri multiple | ✅ |
| Reconectare + reluare din checkpoint, watchdog, reconciliere la restart | ✅ |
| Flotă multi-agent (targetare, capabilități, forget) | ✅ |
| Login + API keys (hash-uri, scope-uri, sesiuni, revocare) | ✅ verificat live |
| Provideri AI (CRUD, default, auto/manual per sarcină) | ✅ verificat live, fără impact pe WebSocket-ul agentului |
| Simulator | ✅ eliminat (endpoint + UI + modul) |
| PWA servit la `/mobile`, login, provideri, flotă, instalare agent | ✅ build + lint curat |
| Agent: wizard la prima rulare, `--check/--version`, provider din sarcină | ✅ |
| Binare macOS/Windows + release automat | 🟡 workflow gata (`release-agent.yml`) — publicarea la primul tag |

### Pași rămași (manuali)

1. **Publică binarele**: `gh workflow run release-agent.yml -f tag=agent-v1.0.0`
   (sau push pe tag `agent-v*`) și verifică cele 3 asset-uri în Releases.
2. **Telefon**: deschide `https://deepseek-api.cs-hub.xyz/mobile` → login cu token
   bootstrap → **Settings → API keys** → generează o cheie `pwa` pentru telefon
   (și una `agent` pentru fiecare mașină) → revocă bootstrap-ul din env când nu mai e nevoie.
3. **Mașinile**: descarcă binarul (card **Install the agent**) → `--setup`.
4. **iOS**: `npm run cap:sync` + `npx cap open ios` pe Mac, apoi TestFlight.
5. Opțional: login cu parolă (`python -m app.cli hash-password` → env), hostname dedicat.

## 15. Structura proiectului

```
/opt/deepseek-proxy/
├── main.py, backup.sh, restore.sh          # proxy-ul DeepSeek existent
├── mobile/                                 # DevAgent-mobile (PWA + iOS)
└── devagent/
    ├── README.md                           # acest document
    ├── gateway/
    │   ├── app/
    │   │   ├── config.py                   # setări din environment
    │   │   ├── db.py                       # SQLite: tasks/events/approvals/checkpoints/agents
    │   │   ├── models.py                   # validare + serializare camelCase pentru PWA
    │   │   ├── events.py                   # Redis pub/sub + fallback in-process
    │   │   ├── hub.py                      # registry WebSocket agenți
    │   │   ├── service.py                  # domeniu: coadă, stări, aprobări, reluare
    │   │   ├── scheduler.py                # watchdog heartbeat, expirare aprobări, dispatch
    │   │   ├── simulator.py                # worker simulat (demo/test PWA)
    │   │   ├── api_pwa.py                  # REST + SSE pentru PWA/iOS
    │   │   ├── api_agent.py                # WebSocket agent + handshake
    │   │   └── server.py                   # app factory: auth, CORS, static /mobile
    │   ├── tests/test_gateway.py           # 12 teste end-to-end
    │   ├── env.example
    │   └── requirements.txt
    ├── agent/
    │   ├── devagent_agent.py               # agent macOS (WebSocket, worktree, aprobări)
    │   ├── agent.env.example
    │   └── com.devagent.agent.plist        # launchd (pornire la login)
    └── systemd/devagent-gateway.service
```

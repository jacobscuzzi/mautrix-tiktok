Knows Case Study – Exercise 2: TikTok Bridge
## Denkprozess & Argumentationsgang
 
Arbeitsdokument. Hält den Reasoning-Verlauf fest, aus dem später das
2–3-Seiten-Design-Doc und die Repo-README destilliert werden. Epistemik ist
bewusst markiert: **[Obs]** = aus Materials/Quellen belegt, **[Inf]** =
plausibel abgeleitet, **[Guess]** = anzunehmen, empirisch zu prüfen.
 
---
 
## 0. Was die Case Study tatsächlich bewertet
 
Leitsatz oben im Briefing: *"We judge the reasoning as much as the result."*
In Exercise 2 verstärkt durch *"Failure modes first"* und *"how far you get and
how you go about it."* Konsequenzen für unsere Arbeit:
 
- Bewertung hängt stärker an sauberer Zerlegung + ehrlichem Umgang mit
  Unsicherheit als an einer lauffähigen End-to-End-Bridge.
- Doc **und** README werden um Failure Modes herum strukturiert, nicht um
  Features.
- Observation / Inference / Guess durchgängig markieren (Anforderung aus
  Exercise 1, hier freiwillig übernommen — signalisiert Methodik).
 
**Abgrenzung, die wir unterwegs klargezogen haben:** Exercise 1 bezieht sich auf
**Instinct** (der AI-Assistent, Reverse-Engineering/System-Graph). Exercise 2
bezieht sich auf **Knows' eigene Infra** (TikTok-Bridge in den mautrix-Fork).
Formulierungen wie "lives in your messages" / "background work that can run for
days" gehören zu Instinct und sind **kein** Argument für die Bridge. Das für
Exercise 2 relevante Constraint steht in dessen eigenem Wortlaut (siehe §3).
 
---
 
## 1. Was die vier gegebenen Skripte verraten
 
Alle vier bauen auf dem **signierten Mobile-App-API-Pfad** auf — nicht auf
Web-Scraping, nicht auf Headless-Browser. **[Obs]**
 
- `Videos_Manager`, `CommentsScraper`, `rest` sprechen die App-Endpoints an
  (`api16-normal-c-alisg.tiktokv.com`, `aid=1340` = TikTok Lite,
  `aid=1233` = musical_ly) und signieren mit **SignerPy**
  (`x-gorgon`, `x-argus`, `x-ladon`, `x-khronos`, `x-ss-stub`). **[Obs]**
- `rest.py` macht bereits einen Passport-Flow:
  `/passport/account_lookup/username/` -> `passport_ticket` ->
  `/passport/user/login_by_passport_ticket/`, und liest den Header
  `x-tt-verify-idv-decision-conf` aus = TikToks Identity-Verification-Decision.
  **[Obs]**
- `TiktokUserInfo` nutzt den Web-Pfad: parst das eingebettete JSON
  (`__UNIVERSAL_DATA_FOR_REHYDRATION__` / `SIGI_STATE`) aus der Profil-HTML,
  mit `curl_cffi` + `impersonate="chrome110"` und einem `sessionid`-Cookie.
  **[Obs]**
 
**Strategischer Read:** Die Skripte sind ein Starter-Kit für das
Signing-plus-Endpoint-Muster. Wenn wir diesen Pfad spiegeln, zeigen wir, dass
wir die Materials verstanden haben. **[Inf]**
 
**Ein konkreter Fehler in den Skripten, den wir nicht übernehmen:** sie würfeln
`device_id`/`iid` pro Request neu (`random.randint(...)`). Für stateless Lookups
egal, für eine **eingeloggte Session tödlich** — ein Account, der ständig das
Gerät wechselt, sieht für TikTok wie eine Übernahme aus. **[Inf]**
 
---
 
## 2. Externe Referenzen (sparen Bauzeit)
 
- **`molkex/tiktok-private-api`** – unofficial Python-Mobile-API-SDK mit vollem
  Signing (X-Argus/Ladon/Gorgon/Khronos) und Auto-Device-Registration, inkl.
  `dm`-Modul (conversations, messages, send, stranger inbox) und `passport`-Modul
  (Login via email/phone/username+pass, code, QR, OAuth). Praktisch die
  Endpoint-Landkarte für die TikTok-Seite. **[Obs]**
- **`12th-devs/matrix-snapchat`** – WIP-bridgev2-Bridge für ein Netzwerk *ohne*
  offizielle API: Go-Service mit Config, Health-Endpoints, Polling + ein
  Playwright-Sidecar mit Persistent-Login und Message-Scraping. Direkter
  Architektur-Analog. **[Obs]**
- **mautrix "Megabridge" / bridgev2** – das Framework, das Knows' Infra forkt.
  `NetworkConnector` (`Init`, `Start`, `GetName`, `CreateLogin`,
  `GetLoginFlows`), `NetworkAPI` (`Connect`, `Disconnect`, `IsLoggedIn`,
  `HandleMatrixMessage`), persistente `UserLogin` + `UserLoginMetadata`,
  `Portal`-Räume, Login-Flow-Typen (cookies / user_input / display_and_wait).
  **[Obs]**
- **Kontext:** TikTok bietet Stand 2026 keinen offiziellen DM-Endpoint (weder
  Content-Posting- noch Display-API). Rechtfertigt den Unofficial-Bridge-Ansatz.
  **[Obs]**
 
---
 
## 3. Architektur – saubere Layer
 
Da Knows' Infra ein mautrix-Fork ist, richten wir die Zerlegung an bridgev2 aus.
Das für Exercise 2 bindende Constraint aus dem Briefing: *"you automatically
fetch their conversations data **on your backend**"* und *"it's the **raw layer
everything else reads from**"*. Die Bridge ist also der server-seitige
Roh-Ingest, auf dem der Rest von Knows aufsetzt.
 
Layer (jeder mit klarer Verantwortung = die geforderte "separation of
responsibilities"):
 
1. **Auth/Session** – Device-Registration -> Credential-Login ->
   Challenge-Handling -> persistierte Session. Entspricht
   `CreateLogin`/`LoginProcess`/`UserLogin`.
2. **Signing** – SignerPy bzw. Signer-Microservice, **isoliert**. Fragilste
   externe Abhängigkeit; separat, damit update-/tauschbar ohne Bridge-Logik.
3. **TikTok-Client/Transport** – HTTP-Client mit pro-User Device-Fingerprint +
   Proxy, Retry/Backoff, Error-Code-Klassifikation
   (Rate-Limit / Ban / Session-Expiry / Captcha).
4. **Sync/Ingest** – Backfill (Conversation-List + History via HTTP,
   Cursor-paginiert) + Realtime (WS/Long-Poll, MVP: Polling) mit Reconciliation,
   damit keine Nachricht verloren geht.
5. **Normalisierung/Mapping** – TikTok-Objekte -> kanonische Events:
   Threads -> Portals, TikTok-User -> Ghosts (mit Avatar), Messages -> Events.
   Hier "landet es in der Pipeline".
6. **State/Storage** – verschlüsselter Session-Store, pro-User
   Cursor/Watermarks, Dedup, Portal/Ghost-Metadaten.
 
**Stack-Entscheidung:** Nicht in mautrix-go bauen (Go + voller Appservice in
3 Tagen = Overscope). Python-Prototyp (matcht die Skripte + das
TikTok-Signing-Ökosystem ist Python), und das Mapping auf die bridgev2-Interfaces
**dokumentieren**. Architekturverständnis zeigen ohne Übernahme. **[Inf]**
 
---
 
## 4. Server vs. Gerät (Input von externem Reviewer, eingeordnet)
 
**These des Reviewers:** Traditionelle Server-Requests stoßen schnell an
IP-Limits; besser eine gerätebasierte Lösung (Swift / Executable) als "v2", die
lokal auf den Geräten läuft und darum nicht gesperrt wird.
 
**Wo die These recht hat:** Das zentrale Ban-Risiko ist **Korrelation**. Viele
Accounts über wenige Datacenter-IPs mit server-typischem TLS und
automatisiertem Timing = "ein Bot betreibt viele Accounts" -> Mass-Bans.
Der Instinkt "Traffic soll aus echtem Kontext kommen" ist richtig.
 
**Wo wir die Framing korrigieren:** Die entscheidende Achse ist nicht
*Server vs. Gerät*, sondern **Identity-Isolation pro User**. Ein Server-Setup
sieht aus wie N unabhängige Handys, wenn jeder User bekommt:
- eigenen Residential-/Mobile-Proxy (geo-gematcht),
- stabilen Device-Fingerprint (konsistent wiederverwendet, nie rotieren),
- konsistente Kopplung IP + Device + sessionid.
 
Die Skripte machen TLS-Impersonation bereits; es fehlt nur die
pro-User-Proxy/Fingerprint-Disziplin. "Stößt an IP-Limits" ist also eher
"geteilter Ausgang für alle ist falsch" als "Server ist falsch". **[Inf]**
 
**Gegeneinwand gegen Device-only:** untergräbt den Sinn einer *Bridge ins
Backend*. Eine rein gerätelokale Lösung funktioniert nur bei Gerät an/online,
kann keine server-seitige Hintergrundarbeit leisten und muss am Ende ohnehin ans
Backend forwarden -> Ingest-Pfad bleibt nötig. Zusätzlich: **Signer läge beim
User** -> leichter extrahier-/burn-bar; server-seitig hotpatcht man den
rotierten Signing-Algo in Minuten, client-seitig wartet man auf Update-Rollout.
Swift = iOS/macOS; iOS erlaubt keinen langlebigen Hintergrund-Netzwerk-Daemon
(Background-Refresh throttled). **[Inf]**
 
**Synthese (v1 -> Eskalationshebel):**
- **v1:** Server + pro-User-Residential-Proxy + stabiler Fingerprint. MVP,
  matcht die Skripte, landet im Backend.
- **Eskalation, falls Detection nachweislich der Bottleneck wird:** dünner
  **Edge-Egress-Client** auf dem User-Gerät, der *nur* den Netzwerk-Ausgang
  stellt (Requests verlassen die echte User-IP); Session, Signing,
  Orchestrierung bleiben server-seitig. Gerät offline -> Backend-Fallback nötig.
 
**Für die Doc:** als Detection-Failure-Mode mit Mitigations-Spektrum + Tradeoffs
schreiben, getriggert an der Metrik (Ban-Rate / Live-Session-Ratio als Schwelle,
ab wann der nächste Hebel gezogen wird).
 
---
 
## 5. Login/Session-Flow (Kern von Exercise 2)
 
### 5a. Ursprüngliche Zerlegung (voller Mobile-Login)
 
Fünf Phasen:
 
- **Phase 0 – Device-Registration** (einmal/Login, dann persistiert): kohärente
  Fingerprint-Params (device_type, brand, os, resolution, cdid, openudid),
  `device_register` -> `device_id` + `install_id`, für immer mit der Session
  mitführen.
- **Phase 1 – Credential-Login:** `/passport/user/login/` mit
  identifier + password (Passwort encoded, nicht plain — Encoding zu prüfen),
  signiert. Drei Ausgänge, **alle drei erwartete States, keine Fehler:**
  Success (setzt `sessionid`, `sessionid_ss`, `sid_tt`, `sid_guard`, `uid_tt`),
  Challenge, Hard-fail.
- **Phase 2 – Challenge-Handling** (der Knackpunkt): Verify-Code (E-Mail/SMS) =
  pending-session; Captcha (slide/rotate/3d) = Solver oder an User, sonst
  QR-Fallback; IDV = mal informativ, mal Pflicht. Der
  `x-tt-verify-idv-decision-conf`-Header aus `rest.py` ist genau dieses Signal.
- **Phase 3 – Session-Etablierung & Persistenz:** ein Blob aus Device-Identität
  + Session-Cookies + User-Identität (uid, sec_uid, unique_id) + Proxy = das
  `UserLogin`. Device tauschen auf bestehender Session -> re-triggert Verify.
- **Phase 4 – Validation & Refresh:** bei jedem `Connect` billiger Live-Check;
  401 -> Refresh -> sonst `needs-reauth` + User benachrichtigen, nicht spinnen.
 
**mautrix-Mapping:** `GetLoginFlows()` = {password (user_input),
cookies (sessionid), qr (display_and_wait)}. `CreateLogin` ->
`LoginProcess.Start()` -> Step 1; `SubmitUserInput` -> bei Challenge weiterer
user_input-Step, bei Erfolg `LoginComplete` mit `UserLoginMetadata` = der
verschlüsselte Session-Blob. Der mehrstufige Login ist also **nativ** im
Framework, kein Hack.
 
### 5b. Gewählte Vereinfachung: Browser-Login + Cookie-Extraktion
 
Entscheidung Jakob: **User+Pass, aber über die echte TikTok-Login-Seite** in
einem kontrollierten Browser (Playwright, Stealth + Residential-Proxy). User /
TikTok löst Captcha/2FA/IDV auf TikToks eigener Seite; wir lesen anschließend
`sessionid`, `sid_tt`, `sid_guard`, `uid_tt` etc. aus dem Browser-Context und
persistieren sie. Danach leichte HTTP-Requests mit den Cookies.
 
**Vorteile:**
- Lagert den fragilsten Schritt (Captcha/2FA/IDV) an TikTok aus -> trifft
  *"fragile manual steps are exactly what this job exists to kill."*
- **Kollabiert #1 und #2 zu einem Flow:** die echte Login-Seite *ist* der
  universelle Flow (Passwort, Code, QR, OAuth). Keine drei separaten Flows.
 
**Zwei Dinge, die das NICHT automatisch löst:**
 
1. **Trägt eine Web-Session auf den DM-Endpoints? [Guess – make-or-break]**
   Die Skripte reden mit der Mobile-App-API; ein Web-`sessionid` ist nicht an
   ein registriertes Mobile-Device gebunden. Read-Endpoints akzeptieren es oft
   (`TiktokUserInfo` tut genau das), aber die IM/DM-Endpoints könnten eine
   App-Session mit Device-Binding erwarten. Zwei mögliche Surfaces:
   - Web-Session -> **Web-DM-Endpoints** (tiktok.com-Messaging): konsistent,
     aber historisch eingeschränkter/uneinheitlich ausgerollt.
   - Web-Session -> **Mobile-DM-Endpoints**: mächtiger, aber Cross-Surface-
     Akzeptanz fraglich.
   -> **Erster empirischer Test auf dem Server**, gegen echte Requests +
   molkex `passport`/`dm`. Entscheidet, welche DM-Surface wir ansteuern.
 
2. **Signing verschwindet nicht, es wechselt nur. [Inf]**
   Sobald wir mit den Cookies eigene HTTP-Requests fahren, brauchen wir weiter
   signierte Params: Web = `X-Bogus`/`X-Gnarly` + `msToken` + `ttwid`,
   Mobile = `X-Gorgon`/`X-Argus`. Signaturfrei nur, wenn *alles* im Browser
   läuft (Playwright liest DMs direkt) — schwer, teuer pro aktivem User,
   skaliert nicht für Hintergrund-Sync.
 
**Zwei Nuancen fürs Doc:**
- *Security:* Wenn wir selbst das Passwort in die Seite tippen, fassen wir das
  Rohpasswort kurz an. Sauberer: dem User TikToks *echte* Seite rendern
  (embedded/remote Browser), er tippt selbst -> wir sehen nur die Cookies.
  Stärkeres Security-Argument.
- *Infra:* Browser nur für den **Login-Moment** (hoch -> einloggen -> Cookies
  -> weg), danach leichtes HTTP für den Sync. Kein Dauer-Browser pro User, sonst
  kehrt das Skalierungsproblem zurück.
 
---
 
## 6. Failure Modes (das wollen sie zuerst)
 
| Schritt | Bricht wie | Detektion | Recovery |
|---|---|---|---|
| Device-Register | Device gebannt / leere id | leere device_id, sofort Risk-Flags | kohärenten Fingerprint neu, frischer Proxy, retry-cap, dann surface |
| Signatur (Signer stale) | alle User gleichzeitig 4xx | 4xx-Spike **über alle** User = Signer-Canary (ein User = Account/Proxy) | Alert + Signer updaten |
| Captcha | Login-Challenge | Captcha-Marker in Response | Solver -> sonst QR-Fallback |
| Verify-Code | "Code gesendet" | Response-State | pending-session, prompten, resumen; nie geliefert -> TTL + cleanup |
| Cred falsch / gebannt / region-lock | Login hard-fail | Error-Code unterscheiden | nichts automatisch, User präzise informieren |
| Proxy tot | consistent blocks von einem Proxy | Health-Check | rotieren |
| Session-Expiry | 401 bei Connect/Sync | Live-Check | Refresh -> sonst needs-reauth |
| Doppel-Login/Race | zwei Devices streiten (Takeover-Signal) | dedup by user | eine aktive Session, sauber supersede |
| WS/Poll-Verlust | Realtime-Lücke | Gap im Cursor | Reconcile gegen History, kein Message-Loss |
| Schema-Change | Parsing bricht | Parse-Error-Rate / Canary | Alert, Mapping nachziehen |
 
---
 
## 7. Security (die eine konkrete Passage)
 
Rohes Passwort nie persistieren — idealerweise gar nicht anfassen (User tippt in
TikToks echte Seite), sonst nur in-memory für den einen Login-Call, nie loggen.
Session-Blob at-rest via Envelope-Encryption (per-User Data-Key, gewrappt von
einem KMS-Master-Key); Blob = Device-Fingerprint + alle Cookies/Tokens +
User-Identität + Proxy-Zuweisung. Secrets (Signer-Keys, KMS, DB,
Captcha-Solver-Key) in einen Secrets-Manager, nicht ins Repo. Nachrichten- und
Kontaktdaten sind personenbezogene Daten **Dritter** (die Gesprächspartner des
Users): Encryption at-rest, Retention-Policy, Löschung bei Logout/Account-Delete
— GDPR ist Pflicht, kein Nice-to-have.
 
---
 
## 8. Die eine Metrik
 
**Headline: Live-Session-Ratio** — Anteil der Logins, die authentifiziert *und*
aktiv synchronisierend sind (nicht in needs-reauth/blocked). Begründung: diese
eine Zahl fällt, egal welcher Failure Mode zuschlägt (Signing kaputt, Account
gebannt, Session abgelaufen, Proxy tot). **Sekundär: Message-Delivery-Lag p95**
(TikTok-Send -> in Pipeline gelandet).
 
---
 
## 9. Realtime-Mechanik (Annahme, zu prüfen)
 
TikToks LIVE/Webcast nutzt WebSocket + protobuf mit HTTP-Long-Poll-Alternative.
**[Obs]** DMs laufen *wahrscheinlich* über dieselbe "frontier"-Infra (WS-Push
oder HTTP-Long-Poll für neue Nachrichten, HTTP-Cursor für History), aber die
gegebenen Skripte belegen das **nicht**. **[Guess]** MVP = Polling mit Cursor;
WS als Stretch. Reconciliation gegen History verhindert Message-Loss bei
WS-Disconnect.
 
---
 
## 10. Offene Entscheidungen (Stand nach Browser-Login-Wende)
 
- **#1 + #2 zusammengelegt:** ein Browser-Login-Flow (echte TikTok-Seite ->
  Cookies raus -> HTTP). Erledigt.
- **NEU & vorrangig — Web-Session auf DM-Endpoints:** empirisch klären, bevor
  irgendetwas anderes gebaut wird. Bestimmt web- vs. mobile-DM-Surface.
- **#3 Session-Schema:** genaue Felder im persistierten `UserLogin`-Blob +
  Verschlüsselung.
- **#4 Challenge-Depth:** wie weit real implementieren (nach Browser-Login
  weitgehend an TikTok delegiert) vs. nur designen.
- **#5 Stack:** Python-Prototyp + dokumentiertes mautrix-Mapping (Empfehlung).
- **#7 Identity-Isolation:** pro-User-Residential-Proxy + stabiler Fingerprint
  als v1; Device-Egress als Eskalationshebel.
- **#8 Metrik:** Live-Session-Ratio + Delivery-Lag p95.
 
---
 
## 11. Zu verifizieren (ehrlich geflaggt)
 
- Trägt die Web-Session auf den DM-Endpoints? (make-or-break) **[Guess]**
- Exakter Login-Endpoint-Pfad + Passwort-Encoding. **[Guess]**
- Ist `device_register` strikt *vor* dem Login nötig? **[Guess]**
- Aktuelle Captcha-Typen. **[Guess]**
- DM-Realtime = WS-Push oder Long-Poll? **[Guess]**
 
Alles gegen molkex `passport`/`dm` + echte Requests prüfen, nicht aus dem Kopf
festschreiben.
 
---
 
## 12. Prozess – wann zu Claude Code (Server) wechseln
 
- **Chat (hier)** = das *Denken*: Flows, Step-Sequenz, Session-Schema,
  Failure-Mode-Tabelle, mautrix-Mapping, Doc-Narrative. Höchstgewichtet in der
  Bewertung. (Kein Netz in dieser Umgebung -> keine echten TikTok-Requests.)
- **Claude Code (Server)** = das *Bauen*: Repo scaffolden, Python-Prototyp,
  SignerPy/curl_cffi/Playwright einrichten, echte device_register/Login-Requests
  iterieren, Signatur-Fehler debuggen. Edit-Run-Debug gegen eine lebende externe
  API -> braucht Filesystem, Package-Installs, Netz.
- **Switch-Punkt:** sobald (1) Flow-Set + Step-Sequenz, (2) Session-Schema,
  (3) Failure-Handling pro Step, (4) mautrix-Mapping fixiert sind. Wir schreiben
  hier eine knappe **Build-Spec** (Repo-Skelett, Interfaces, Failure-Modes);
  Claude Code implementiert *gegen* diese Spec. So bleibt das Reasoning
  dokumentiert und der Build hat einen klaren Brief.
# Asistent peste sistem: serverul MCP

Un asistent (Claude Desktop, Claude Code, un agent din Node-RED, orice client MCP) poate
întreba sistemul ce s-a întâmplat, căuta în pozele de detecție, cere cadrul unei camere,
vedea recomandările și comuta modul casei. Nu declanșează sirene și nu șterge nimic.

## Ce oferă

| Unealtă | Ce face |
|---|---|
| `camere` | lista camerelor, cu ce știe fiecare să facă |
| `stare` | cine înregistrează, detecții active, spațiu, zgomot, mod, versiune |
| `evenimente` | jurnalul, filtrabil după `tip`, `camera`, `zi`, `text`, `severitate_minima`, `limita` |
| `episoade` | episoadele de prezență (cine, cât, încotro), cu descrierea lor |
| `scena` | candidații „ceva nou” ai unei camere, cu eticheta propusă și verdictul omului |
| `cauta` | căutare vizuală în poze după o descriere liberă („mașina de gunoi”) |
| `intreaba` | o întrebare în limbaj natural („când a venit mașina de gunoi?”), cu dovezi |
| `poza` | cadrul curent al unei camere (JPEG) |
| `recomandari` | ce ar îmbunătăți detecția pe o cameră |
| `mod` | citește sau comută modul: `acasa`, `plecat`, `noapte` |

Orele sunt locale. Răspunsurile sunt JSON în `content[0].text` (și `structuredContent`),
pozele sunt conținut `image`.

## Cum te conectezi

Punctul de intrare e `POST /mcp` (Streamable HTTP, varianta cu răspuns JSON), cu jetonul
din **Setări → Automatizări → Jeton pentru comenzi**, trimis ca antet `X-Token` sau
`Authorization: Bearer <jeton>`. Fluxul SSE server → client nu e oferit (`GET /mcp` dă 405);
clienții îl tratează ca opțional.

Un apel de mână:

```bash
curl -s https://nvr.exemplu.ro/mcp -H "X-Token: $JETON" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"evenimente","arguments":{"tip":"episod","limita":5}}}'
```

### Claude Desktop (sau alt client care pornește serverele ca procese)

Puntea `scripts/openvigil_mcp.py` traduce stdio în apeluri HTTP. Fără dependențe, doar
Python 3. În `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "openvigil": {
      "command": "python3",
      "args": ["/calea/catre/openvigil_mcp.py"],
      "env": {
        "OPENVIGIL_URL": "https://nvr.exemplu.ro",
        "OPENVIGIL_TOKEN": "jetonul din Setări → Automatizări"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add openvigil -e OPENVIGIL_URL=https://nvr.exemplu.ro -e OPENVIGIL_TOKEN=... -- python3 /calea/openvigil_mcp.py
```

### Node-RED

Un nod `http request` (POST, JSON) către `/mcp` cu antetul `X-Token`, corpul fiind mesajul
JSON-RPC de mai sus. Pentru un agent LLM în Node-RED, uneltele din `tools/list` se pot da
modelului ca funcții; fiecare apel al lui devine un `tools/call`.

## Modelul de limbaj

Rezumatele episoadelor (o propoziție, din fapte) și traducerile vocabularului le scrie
modelul de limbaj local (Qwen2.5-VL, în nvr-ai), fără configurare. Pentru un model mai mare
sau o mașină mai puternică din rețea, **Setări → Unelte → Model de limbaj** primește un
server compatibil OpenAI (Ollama, LM Studio, vLLM): adresa cu `/v1` la coadă, numele
modelului, cheia dacă cere; „Testează modelul” face un apel scurt. Rezumatele LLM ale
episoadelor se pornesc separat (bifa din același card); textul-șablon rămâne în eveniment
ca `text_sablon`.

## Securitate

- Același jeton ca API-ul de automatizări: cine îl are vede jurnalul și pozele tuturor
  camerelor și poate comuta modul. Generează altul din Setări dacă a scăpat.
- Jetonul se trimite doar în antet, niciodată în adresă.
- Uneltele sunt de citire, cu o singură excepție (modul). Sirena, releele și anunțurile
  rămân pe API-ul v1, deliberat, în afara asistentului.

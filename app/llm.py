#!/usr/bin/env python3
"""Un singur loc pentru „cere un text scurt unui model de limbaj”, cu doua spate:

  1. extern, optional: orice server compatibil OpenAI (Ollama, LM Studio, vLLM, OpenAI) —
     setarile `llm_url` (ex. http://192.168.1.50:11434/v1), `llm_model` (ex. qwen2.5:7b),
     `llm_key` (daca cere). Pentru cine are o masina mai puternica in retea sau vrea un
     model mai mare decat incape pe placa NVR-ului.
  2. local: Qwen2.5-VL din nvr-ai (describe.text) — acelasi model care descrie persoanele,
     fara nicio configurare. Din nvr-web ajunge la el prin ai_client (/describe/text).

Folosit de descrierile pe episod (episoade.py) si de traducerile vocabularului. Nu arunca
niciodata in apelant mai mult decat RuntimeError cu un motiv scurt; apelantii au mereu o
varianta fara LLM (textul-sablon).
"""
import logare
import settings

IN_AI = False          # ai_main o pune True: modelul local e in acest proces
MAX_TOKENS = 120


def extern_configurat():
    return bool((settings.get("llm_url") or "").strip())


def _extern(prompt, max_tokens, timeout):
    import requests
    url = (settings.get("llm_url") or "").strip().rstrip("/")
    model = (settings.get("llm_model") or "").strip()
    if not model:
        raise RuntimeError("llm_model lipseste")
    antete = {"Content-Type": "application/json"}
    cheie = (settings.get("llm_key") or "").strip()
    if cheie:
        antete["Authorization"] = f"Bearer {cheie}"
    r = requests.post(f"{url}/chat/completions", headers=antete, timeout=timeout,
                      json={"model": model, "max_tokens": int(max_tokens), "temperature": 0.2,
                            "messages": [{"role": "user", "content": prompt}]})
    if not r.ok:
        raise RuntimeError(f"LLM extern: HTTP {r.status_code} {r.text[:80]}")
    d = r.json()
    try:
        return " ".join(d["choices"][0]["message"]["content"].split())
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("LLM extern: raspuns fara choices")


def _local(prompt, max_tokens, timeout):
    if IN_AI:
        import describe
        return describe.text(prompt, max_tokens=max_tokens)
    import ai_client
    d = ai_client.text(prompt, max_tokens, timeout=timeout)
    if not d or not d.get("text"):
        raise RuntimeError((d or {}).get("error") or "nvr-ai nu raspunde")
    return d["text"]


def completeaza(prompt, max_tokens=MAX_TOKENS, timeout=60):
    """Textul raspunsului (o singura linie), prin spatele configurat; RuntimeError la esec."""
    try:
        if extern_configurat():
            return _extern(prompt, max_tokens, timeout)
        return _local(prompt, max_tokens, timeout)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)[:120])


def proba():
    """Un apel scurt pentru butonul „Testeaza” din setari: (ok, text sau motiv)."""
    try:
        return True, completeaza("Răspunde cu un singur cuvânt: salut", max_tokens=8, timeout=90)
    except RuntimeError as e:
        logare.info(f"[llm] proba: {e}")
        return False, str(e)

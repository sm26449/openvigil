#!/usr/bin/env python3
"""Descrierea persoanelor detectate — "barbat ~40 de ani, geaca neagra, rucsac".

Motor: Qwen2.5-VL-3B-Instruct (VLM local pe GPU, ~7 GB VRAM in bfloat16), intrebat
in romana pe crop-ul persoanei din cadrul HD. Ruleaza DOAR cand detectia tocmai a
confirmat un om — costul (~1-2s) e per eveniment, nu per cadru, si sta la coada in
spatele Face ID-ului, nu in fata lui.

Descrierea devine eveniment ("descriere" in jurnal, vizibil in tabul AI → Evenimente),
pleaca pe webhook/MQTT ca orice eveniment, si — pentru ca sta in jurnal — intra in
istoricul permanent: "cine a trecut ieri" are acum si semnalmente, nu doar "om".
"""
import io
import threading
import time

import settings
import logare

MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
PROMPT = ("Descrie pe scurt, in romana, persoana din imagine: gen aparent, varsta "
          "aproximativa, imbracaminte, obiecte purtate. O singura propozitie, "
          "fara introducere.")
VEHICLE_PROMPT = ("In romana, maxim 6 cuvinte: tipul si culoarea vehiculului principal "
                  "din imagine. Tipuri: masina, duba, camion, autobuz, camion de gunoi, "
                  "motocicleta. Exemplu raspuns: 'camion de gunoi alb' sau 'masina rosie'. "
                  "Doar atat, fara alte cuvinte.")
CROP_MARGIN = 0.20     # context in jurul cutiei YOLO
MIN_SIDE = 50          # sub atat crop-ul nu are destule detalii cat sa merite
MIN_SIDE_VEH = 64      # vehiculele au nevoie de ceva mai mult ca sa se vada culoarea
COOLDOWN = 120         # secunde intre doua descrieri de PERSOANA pe aceeasi camera
COOLDOWN_VEH = 45      # idem pentru vehicule (strada e mai aglomerata)
MAX_TOKENS = 60

_model = None
_proc = None
_init_lock = threading.Lock()
_lock = threading.Lock()
_last = {}
_last_veh = {}
_last_use = 0.0            # ultima folosire a modelului (pt. descarcare la inactivitate)
_unloader_on = False
IDLE_UNLOAD = 600         # 10 min fara descriere -> Qwen iese din VRAM (~7GB recuperati)


def enabled():
    return bool(settings.get("person_describe"))


def _unload():
    """Scoate Qwen din VRAM cand nu se descrie de mult. Descrierile sunt ocazionale, iar
    modelul tine ~7GB din 16 — eliberandu-l, lasam loc pentru detectie + NVENC (fara asta,
    era mereu la limita si dadea OOM). Se reincarca lazy la urmatoarea descriere."""
    global _model, _proc
    import gc

    import torch
    with _lock, _init_lock:          # nu descarcam in mijlocul unei inferente / incarcari
        if _model is None:
            return
        _model = None
        _proc = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    logare.info("[describe] qwen descarcat din VRAM (inactiv)")


def _idle_unloader():
    while True:
        time.sleep(60)
        try:
            if _model is not None and time.time() - _last_use > IDLE_UNLOAD:
                _unload()
        except Exception as e:
            logare.info(f"[describe] unloader: {str(e)[:70]}")


def _get_model():
    import ai_client
    ai_client.local_permis("VLM")
    global _model, _proc, _last_use, _unloader_on
    with _init_lock:
        _last_use = time.time()
        if not _unloader_on:         # pornim veghea de inactivitate o singura data
            _unloader_on = True
            threading.Thread(target=_idle_unloader, daemon=True,
                             name="qwen-unloader").start()
        if _model is None:
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
            # Plasa anti-OOM: nu incercam sa incarcam Qwen (~7GB) daca nu incape — un OOM
            # in mijlocul incarcarii poate lasa CUDA intr-o stare proasta si pentru CELELALTE
            # modele. Mai bine sarim descrierea (process() prinde exceptia si continua).
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                free, _tot = torch.cuda.mem_get_info()
                if free < 7 * 2**30:
                    raise RuntimeError(
                        f"VRAM insuficient pt. descriere ({free // 2**20}MB liber, nevoie ~7GB)")
            # from_pretrained(dtype=bfloat16) lasa dtype-ul IMPLICIT al torch schimbat
            # pe bfloat16 pentru tot procesul — SigLIP (float32) a inceput sa primeasca
            # tensori bf16 si /api/search a cazut cu "Float vs BFloat16". Il refacem.
            prev_dtype = torch.get_default_dtype()
            try:
                _model = AutoModelForImageTextToText.from_pretrained(
                    MODEL_ID, dtype=torch.bfloat16,
                    device_map="cuda" if torch.cuda.is_available() else "cpu")
                # device_map + dtype lasa uneori straturi in float32 (quirk transformers)
                # -> model cu dtype amestecat, apoi "mat1/mat2 same dtype" la inferenta.
                # Fortam intreg modelul pe bfloat16, consistent.
                if torch.cuda.is_available():
                    _model = _model.to(torch.bfloat16)
                # transformers 5.15: lm_head NU se mai leaga automat de embeddings pentru
                # Qwen2.5-VL. Checkpoint-ul are lm_head "tied" (nu-l stocheaza separat),
                # deci e raportat MISSING si initializat ALEATOR -> proiectia finala e random
                # -> text de gunoi. Il legam manual la embeddings (weight tying).
                out_emb = _model.get_output_embeddings()
                if out_emb is not None and getattr(out_emb, "weight", None) is not None:
                    out_emb.weight = _model.get_input_embeddings().weight
            finally:
                torch.set_default_dtype(prev_dtype)
            # Plafonam rezolutia imaginii: un crop mare -> mii de tokeni de imagine ->
            # atentie eager de ordinul GB (OOM pe GPU-ul plin). Pentru culoare+tip de
            # vehicul/persoana, ~512 patch-uri (128 tokeni) ajung, si e mult mai rapid.
            _proc = AutoProcessor.from_pretrained(
                MODEL_ID, min_pixels=128 * 28 * 28, max_pixels=512 * 28 * 28)
            _model.eval()
            logare.info("[describe] model qwen2.5-vl incarcat")
    return _model, _proc


def warmup_async():
    # NU mai incarcam Qwen la pornire: e mare (~7GB din 16) si folosit ocazional. Se incarca
    # lazy la prima descriere si se descarca dupa inactivitate (_idle_unloader). Asa VRAM-ul
    # de baza ramane jos pentru detectie + NVENC, care conteaza mereu. Prima descriere dupa
    # o perioada linistita e mai lenta (~15s incarcare), acceptabil (ruleaza in fundal).
    pass


def _crop(img_pil, bbox):
    w, h = img_pil.size
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
    return img_pil.crop((max(0, int(x1 - mx)), max(0, int(y1 - my)),
                         min(w, int(x2 + mx)), min(h, int(y2 + my))))


def describe_crop(img_pil, prompt=PROMPT):
    """Descrierea unui crop (PIL) — o propozitie in romana. prompt = persoana/vehicul."""
    import torch
    model, proc = _get_model()
    msgs = [{"role": "user", "content": [
        {"type": "image", "image": img_pil},
        {"type": "text", "text": prompt}]}]
    import metrici
    with metrici.cronometru("descriere"), _lock, torch.no_grad():
        if torch.cuda.is_available():
            torch.cuda.empty_cache()      # elibereaza blocurile fragmentate (VRAM e la limita)
        inputs = proc.apply_chat_template(
            msgs, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt").to(model.device)
        # potrivim dtype-ul tuturor intrarilor float (pixel_values etc.) cu al modelului;
        # lasam intregii (input_ids, grid_thw) neatinsi. Evita "mat1/mat2 same dtype".
        for k, v in list(inputs.items()):
            if torch.is_floating_point(v):
                inputs[k] = v.to(model.dtype)
        out = model.generate(**inputs, max_new_tokens=MAX_TOKENS, do_sample=False)
        text = proc.decode(out[0][inputs["input_ids"].shape[1]:],
                           skip_special_tokens=True)
    global _last_use
    _last_use = time.time()           # cronometrul de inactivitate porneste de la finalul folosirii
    return " ".join(text.split()).strip(" .") + "."


def process(cam_id, img_bgr, boxes):
    """Descrie persoanele dintr-un cadru (async-friendly: chemata dintr-un thread).

    boxes = lista de bbox-uri [x1,y1,x2,y2] pentru "person". Descrie cel mult prima
    cutie suficient de mare (mai multe persoane -> o descriere comuna ar fi confuza,
    iar N descrieri ar tine GPU-ul ocupat degeaba)."""
    if not enabled():
        return None
    now = time.time()
    if now - _last.get(cam_id, 0) < COOLDOWN:
        return None
    box = next((b for b in boxes
                if min(b[2] - b[0], b[3] - b[1]) >= MIN_SIDE), None)
    if box is None:
        return None
    _last[cam_id] = now
    try:
        import cv2
        from PIL import Image
        img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        text = describe_crop(_crop(img, box))
    except Exception as e:
        logare.info(f"[describe] cam{cam_id}: {str(e)[:80]}")
        return None
    if not text or len(text) < 10:
        return None
    logare.info(f"[describe] cam{cam_id}: {text}")
    try:
        import hooks
        hooks.emit("descriere", cam_id, text=text[:118])  # jurnalul taie valorile lungi
    except Exception:
        pass
    return text


def process_vehicle(cam_id, img_bgr, boxes, moment=None):
    """Descrie vehiculul dintr-un cadru: culoare + tip (masina/camion/autobuz/gunoi).
    Aceeasi idee ca `process`, dar prompt de vehicul si cooldown propriu. moment =
    'zi'/'noapte', atasat evenimentului ca sa putem filtra dupa lumina."""
    if not enabled():
        return None
    now = time.time()
    if now - _last_veh.get(cam_id, 0) < COOLDOWN_VEH:
        return None
    box = next((b for b in boxes
                if min(b[2] - b[0], b[3] - b[1]) >= MIN_SIDE_VEH), None)
    if box is None:
        return None
    _last_veh[cam_id] = now
    try:
        import cv2
        from PIL import Image
        img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        text = describe_crop(_crop(img, box), VEHICLE_PROMPT)
    except Exception as e:
        logare.info(f"[describe] cam{cam_id} vehicul: {str(e)[:80]}")
        return None
    if not text or len(text) < 6:
        return None
    logare.info(f"[describe] cam{cam_id} vehicul: {text}")
    try:
        import hooks
        hooks.emit("descriere", cam_id, text=text[:118], obiect="vehicul", moment=moment)
    except Exception:
        pass
    return text

#!/usr/bin/env python3
"""Genereaza go2rtc/go2rtc.yaml:
- 16 stream-uri LIVE pe substream NVR (H.264, copy), cam1..cam16
- cate un stream LIVE pe substream pentru fiecare camera standalone din data/cameras.json
  (camN, unde N >= 101), adaugata manual prin UI
Playback-ul si live-HD se inregistreaza dinamic prin API (vezi app/server.py)."""
import urllib.parse, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

HERE = os.path.dirname(os.path.abspath(__file__))
EXT_FILE = os.path.join(HERE, "data", "cameras.json")


def load_ext():
    """Camerele standalone adaugate manual (lista de dict-uri). Gol daca fisierul lipseste."""
    try:
        with open(EXT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []




def ext_substream_url(cam):
    """RTSP substream pentru o camera standalone, dupa producator (drivers.py)."""
    sys.path.insert(0, os.path.join(HERE, "app"))
    import drivers
    return drivers.rtsp_sub(cam)


def ext_mainstream_url(cam):
    """RTSP mainstream (fluxul mare, cel inregistrat)."""
    sys.path.insert(0, os.path.join(HERE, "app"))
    import drivers
    return drivers.rtsp_main(cam)


# encoderul folosit de toate template-urile de iesire (scris o singura data).
# GOP 25 (la 15fps = keyframe ~1.7s): un consumer MSE nou primeste un keyframe repede
# (mai putin ecran verde la deschidere) si isi revine rapid dupa o bruiere. GOP-ul de
# iesire e independent de cel al camerei — il controlam noi, aici.
# fisierul generat (in data/: criptat la rest, si contine parolele camerelor)
OUT = os.path.join(HERE, "data", "go2rtc.yaml")

NVENC = ("-c:v h264_nvenc -profile:v main -pix_fmt yuv420p -preset p4 -rc vbr -cq 20 "
         "-b:v 0 -maxrate 16M -bufsize 32M -bf 0 -g 25")



def generate():
    lines = [
        # API-ul si RTSP-ul doar pe localhost: browserul vine prin releul autentificat din
        # nvr-web (app/stream_proxy.py, :8076), nvr-ai/track prin 127.0.0.1. Direct pe LAN
        # go2rtc n-are nicio autentificare si ar ocoli conturile interfetei.
        "api:",
        '  listen: "127.0.0.1:1984"',
        # pagina e servita de pe :8079, WS vine prin releu de pe :8076 (cross-origin)
        '  origin: "*"',
        "rtsp:",
        '  listen: "127.0.0.1:8554"',
        # WebRTC ramane pe toate interfetele: media (DTLS-SRTP) merge direct intre browser si
        # go2rtc, iar fara semnalizarea de pe /api/ws (autentificata) portul nu da nimic
        "webrtc:",
        '  listen: ":8555"',
        "log:",
        "  level: info",
        # template custom: encode H.264 pe GPU (NVENC). Referit prin '#video=h264n' din backend.
        # profile MAIN (nu high): WebRTC din browser NU negociaza H.264 High (avc1.64*),
        # dar negociaza Main (avc1.4D*) — exact ce face si live-ul. pix_fmt yuv420p (nu yuvj
        # full-range) pt. compatibilitate decoder. Fara high -> playback merge pe WebRTC ca live.
        "ffmpeg:",
        # VBR de calitate (cq 20) cu cap 16M pt. rezolutie nativa 6MP. profile main + yuv420p
        # ca sa mearga si pe WebRTC; dar streamurile native/HD le redam pe MSE in frontend.
        f'  h264n: "{NVENC}"',
        # template de INPUT pt. live/playback RTSP cu decodare pe GPU (NVDEC): template-ul
        # implicit 'rtsp' din go2rtc 1.9 + '-hwaccel cuda', MINUS '-fflags nobuffer'. Fara
        # '-hwaccel_output_format cuda' intentionat: cadrele decodate se intorc in RAM,
        # deci filtrele CPU (setpts, scale) raman valabile, iar NVENC le urca singur inapoi.
        # DE CE fara nobuffer (masurat 2026-10-22, cam1/cam14): camera trimite un IDR la
        # fiecare conectare RTSP, dar cu nobuffer ffmpeg il consuma in faza de probe si
        # decodorul porneste pe P-frame-uri -> NVDEC scoate cadre VERZI (0,201,0) cu
        # "Could not find ref with POC 0", decodarea CPU scoate GRI. Cu H.265+ pe camere
        # urmatorul IDR vine abia la ~14 s (GOP nominal 4 s, intins de SmartCodec), deci
        # verdele tinea pana atunci. Fara nobuffer primul cadru e curat si NU creste
        # timpul pana la primul cadru (2,3 s vs 2,5 s). Folosit doar cand HW=True.
        '  rtspn: "-hwaccel cuda -flags low_delay -timeout 5000000'
        ' -user_agent go2rtc/ffmpeg -rtsp_flags prefer_tcp -i {input}"',
        # Varianta pe CPU (fara placa video). Istoric: HD-ul scalat (height=) mergea pe ea
        # fiindca "NVDEC + scale-CPU dadea verde" — diagnostic gresit, verdele venea tot din
        # nobuffer (vezi mai sus); NVDEC + scale + NVENC verificat curat pe cam14.
        '  rtspc: "-flags low_delay -timeout 5000000'
        ' -user_agent go2rtc/ffmpeg -rtsp_flags prefer_tcp -i {input}"',
        # template de INPUT pt. playback din inregistrarile locale (lista concat cu inpoint);
        # -re = pace realtime, altfel ffmpeg trimite tot fisierul dintr-o data.
        # Variantele 'n' decodeaza pe GPU; cele simple raman fallback fara placa video.
        '  vodcat: "-re -f concat -safe 0 -i {input}"',
        '  vodcatn: "-re -hwaccel cuda -f concat -safe 0 -i {input}"',
        # REDARE ACCELERATA. Doua bucati care trebuie sa mearga impreuna: -readrate citeste
        # fisierul de N ori mai repede, iar setpts comprima marcajele de timp, altfel
        # rezultatul ar avea durata originala si s-ar bloca. Audio nu are sens la viteza
        # marita, deci nu il transportam.
        # Limita e decodarea. Pe CPU, lantul decodare+reincodare HEVC 3072x2048 merge la
        # ~2.5x fata de timpul real -> fara GPU oferim doar 2x. Cu NVDEC (RTX 2000 Ada)
        # decodarea nu mai e strangularea, deci exista si 4x (doar pe variantele 'n').
        '  vodcat2: "-readrate 2 -f concat -safe 0 -i {input}"',
        '  vodcatn2: "-readrate 2 -hwaccel cuda -f concat -safe 0 -i {input}"',
        '  vodcatn4: "-readrate 4 -hwaccel cuda -f concat -safe 0 -i {input}"',
        f'  h264n2: "-vf setpts=PTS/2 -an {NVENC}"',
        f'  h264n4: "-vf setpts=PTS/4 -an {NVENC}"',
        '  h2642: "-vf setpts=PTS/2 -an -c:v libx264 -preset veryfast -crf 23"',
        # 8x (doar GPU): decodarea HEVC 2688x1520@25 tine pasul (masurat 0.995x realtime
        # la -readrate 8), iar fps=25 dupa setpts arunca 7 cadre din 8 ca NVENC sa nu
        # incodeze 200 fps degeaba. 0.5x (si pe CPU): -readrate 0.5 + setpts*2 — pentru
        # cadrul exact al unei treceri rapide.
        '  vodcatn8: "-readrate 8 -hwaccel cuda -f concat -safe 0 -i {input}"',
        f'  h264n8: "-vf setpts=PTS/8,fps=25 -an {NVENC}"',
        '  vodcat05: "-readrate 0.5 -f concat -safe 0 -i {input}"',
        '  vodcatn05: "-readrate 0.5 -hwaccel cuda -f concat -safe 0 -i {input}"',
        f'  h264n05: "-vf setpts=PTS*2 -an {NVENC}"',
        '  h26405: "-vf setpts=PTS*2 -an -c:v libx264 -preset veryfast -crf 23"',
        # encode CPU pt. HD live: libx264 ultrafast + zerolatency ca sa tina REALTIME 1440p
        # (veryfast pica sub 1x). -bf 0 fara B-frames (MSE-friendly). repeat-headers=1 pune
        # SPS/PPS in-band inaintea FIECARUI keyframe: fara ele, un consumer MSE care prinde
        # stream-ul intre anteturi nu-si initializeaza decoder-ul si arata GRI persistent
        # (nu doar la pornire). -g 15 = keyframe la ~1s -> re-sincronizare rapida.
        '  h264cpu: "-c:v libx264 -preset ultrafast -tune zerolatency'
        ' -x264-params repeat-headers=1 -crf 24 -g 15 -bf 0"',
        "streams:",
    ]
    # Toate camerele vin din registrul unic (data/cameras.json), accesate DIRECT.
    # NVR-ul agregat (optional) nu mai e a doua sursa: fluxurile vin direct de pe camere
    # ca fallback — era moarta si atarna "No route to host" ~5s la FIECARE conectare proaspata
    # (go2rtc asteapta si producer-ul mort inainte sa livreze pista catre consumerul MSE).
    # Camerele cu difuzor primesc si sursa isapi:// = backchannel two-way audio (microfonul
    # din browser ajunge in difuzorul camerei prin WebRTC -> go2rtc -> ISAPI).
    # #backchannel=0 e OBLIGATORIU pe sursa RTSP: altfel go2rtc negociaza si canalul de
    # backchannel RTSP si camera raporteaza permanent "two-way audio in progress" (sirena
    # da 403, difuzorul e blocat).
    # Masca de confidentialitate (masca.py): camerele cu dreptunghiuri de acoperit primesc
    # template-uri PROPRII (m<id> pentru live/HD, m<id>s<viteza> pentru redare), pentru ca
    # go2rtc refuza sursele cu spatii (#raw=-vf ... nu merge), dar template-urile pot avea.
    # Substreamul lor din grila se transcodeaza pe NVENC (mic, ieftin) in loc sa fie copiat.
    cams = sorted(load_ext(), key=lambda c: c["id"])
    try:
        sys.path.insert(0, os.path.join(HERE, "app"))
        import masca
        mascate = {c["id"]: masca.vf_cam(c) for c in cams if masca.rects(c)}
    except Exception as e:                       # fara masca nu cade generarea
        print(f"[gen_go2rtc] masca: {e}", flush=True)
        mascate = {}
    # template-urile stau in sectiunea `ffmpeg:` (inaintea lui `streams:`), altfel go2rtc
    # le-ar lua drept fluxuri `exec:` si ar da „Unable to choose an output format for 'm5'"
    sablon = []
    for cid, vf in mascate.items():
        sablon.append(f'  m{cid}: "-vf {vf} {NVENC}"')
        for suf, pts in (("05", "setpts=PTS*2"), ("2", "setpts=PTS/2"),
                         ("4", "setpts=PTS/4"), ("8", "setpts=PTS/8,fps=25")):
            sablon.append(f'  m{cid}s{suf}: "-vf {pts},{vf} -an {NVENC}"')
    i_streams = next(i for i, ln in enumerate(lines) if ln.strip() == "streams:")
    lines[i_streams:i_streams] = sablon
    for cam in cams:
        u = urllib.parse.quote(cam["user"], safe="")
        p = urllib.parse.quote(cam["pass"], safe="")
        talk = (cam.get("features") or {}).get("talk")
        direct = ext_substream_url(cam) + ("#backchannel=0" if talk else "")
        if cam["id"] in mascate:
            direct = f"ffmpeg:{ext_substream_url(cam)}#input=rtspn#video=m{cam['id']}"
        extra = []
        if talk:
            extra.append(f'isapi://{u}:{p}@{cam["ip"]}:80/')
        if extra:
            lines.append(f"  cam{cam['id']}:")
            for src in [direct] + extra:
                lines.append(f'    - "{src}"')
        else:
            lines.append(f'  cam{cam["id"]}: "{direct}"')
        # mainstream-ul brut (fara masca, fara transcodare): de aici inregistreaza nvr-record,
        # ca parola camerei sa stea doar in acest fisier (0600), nu in linia de comanda a
        # fiecarui ffmpeg, vizibila in `ps` oricarui utilizator local. Tot de aici poate lua
        # HD-ul live, ca sa fie o singura conexiune main per camera.
        lines.append(f'  cam{cam["id"]}_main: "{ext_mainstream_url(cam)}'
                     f'{"#backchannel=0" if talk else ""}"')

    # in data/: fisierul are parolele camerelor in URL-uri (0600); go2rtc il citeste de acolo
    out = OUT
    # fisierul contine parolele camerelor: doar proprietarul il citeste (si cand exista deja)
    sys.path.insert(0, os.path.join(HERE, "app"))
    import store
    store.atomic_write_text(out, "\n".join(lines) + "\n", mode=0o600)   # niciodata pe jumatate
    return out


if __name__ == "__main__":
    print("scris", generate())

# Licențe și componente terțe

OpenVigil este publicat sub **GNU Affero General Public License v3.0** (vezi `LICENSE`).
Alegerea nu e doar de principiu: modelul de detecție folosit implicit (YOLO11, Ultralytics)
este AGPL-3.0, iar un proiect care îl redistribuie trebuie să fie compatibil cu această
licență.

Componentele de mai jos nu fac parte din acest repo: imaginea Docker le instalează sau le
descarcă la prima pornire. Licențele lor sunt ale autorilor respectivi.

## Motoare și biblioteci

| Componentă | Rol | Licență |
|---|---|---|
| [go2rtc](https://github.com/AlexxIT/go2rtc) | streaming (RTSP, WebRTC, MSE), binar descărcat la build | MIT |
| FFmpeg (pachetul Ubuntu) | înregistrare, transcodare, miniaturi | LGPL 2.1+ / GPL 2+ (build-ul distribuției) |
| Flask, waitress | server web | BSD-3 / ZPL 2.1 |
| PyTorch, torchvision | inferență | BSD-3 |
| Ultralytics | detecție YOLO11, fine-tuning | AGPL-3.0 |
| Transformers (Hugging Face) | VLM, SigLIP | Apache 2.0 |
| ONNX Runtime | fețe, re-ID | MIT |
| InsightFace (cod) | recunoașterea fețelor | MIT |
| OpenCV (headless) | prelucrare imagini | Apache 2.0 |
| paho-mqtt | puntea MQTT | EPL 2.0 / EDL 1.0 |
| pywebpush, cryptography | notificări push, copii criptate | MPL 2.0 / Apache 2.0 + BSD |
| Playwright | testele interfeței | Apache 2.0 |

## Modele descărcate la prima pornire

| Model | Rol | Licență | Observații |
|---|---|---|---|
| YOLO11m (Ultralytics) | detecție persoane, animale, vehicule | AGPL-3.0 | se poate înlocui cu un model propriu, antrenat local |
| InsightFace `buffalo_l` (SCRFD + ArcFace) | detecție și recunoaștere fețe | **doar cercetare / uz necomercial** | folosirea comercială cere licență de la InsightFace; dezactivezi fețele din Setări |
| Qwen2.5-VL-3B-Instruct | descrieri în limbaj natural ale detecțiilor | Apache 2.0 | opțional, doar cu GPU |
| google/siglip-base-patch16-256-multilingual | căutare semantică | Apache 2.0 | opțional |
| OSNet x0.25 (torchreid, export ONNX) | re-identificarea persoanelor după corp | MIT | fișierul `data/osnet_x0_25_msmt17.onnx` se aduce separat; fără el re-ID e oprit |

Numele mărcilor (Hikvision, NVIDIA, ONVIF) aparțin deținătorilor lor; proiectul nu este
afiliat cu niciunul dintre ei.

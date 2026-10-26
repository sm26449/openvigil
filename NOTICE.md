# Licențe și componente terțe

*English summary.* OpenVigil is AGPL-3.0. The Docker image installs or downloads the
third-party components below; their licences belong to their authors. Two models are
**non-commercial only**: the InsightFace face models (`buffalo_l`) and Qwen2.5-VL-3B-Instruct
(Qwen Research License). Everything else is MIT, BSD, Apache 2.0, LGPL/GPL (ffmpeg, espeak-ng,
smartmontools, as packaged by Ubuntu) or AGPL-3.0 (Ultralytics YOLO11). `app/static/video-rtc.js`
is vendored from go2rtc (MIT, Alexey Khit).

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
| `app/static/video-rtc.js` (VideoRTC, din go2rtc) | playerul MSE/WebRTC din browser, copiat neschimbat | MIT (Alexey Khit) |
| fast-alpr, open-image-models, fast-plate-ocr | citirea numerelor de înmatriculare | MIT |
| uniface | anti-spoofing la fețe (cod) | MIT |
| espeak-ng (pachetul Ubuntu) | anunțuri vocale (TTS) în difuzorul camerei | GPL-3.0 |
| smartmontools (pachetul Ubuntu) | starea discurilor | GPL-2.0 |

## Modele descărcate la prima pornire

| Model | Rol | Licență | Observații |
|---|---|---|---|
| YOLO11m (Ultralytics) | detecție persoane, animale, vehicule | AGPL-3.0 | se poate înlocui cu un model propriu, antrenat local |
| InsightFace `buffalo_l` (SCRFD + ArcFace) | detecție și recunoaștere fețe | **doar cercetare / uz necomercial** | folosirea comercială cere licență de la InsightFace; dezactivezi fețele din Setări |
| Qwen2.5-VL-3B-Instruct | descrieri în limbaj natural ale detecțiilor | **Qwen Research License — doar uz necomercial** | opțional, doar cu GPU; folosirea comercială cere licență de la Alibaba sau un alt model (Qwen2.5-VL-7B-Instruct e Apache 2.0, dar cere ~15 GB VRAM) |
| YOLO11x (Ultralytics) | „profesor” la construirea datasetului etichetat | AGPL-3.0 | doar când construiești datasetul din Setări |
| yolo-v9-t-384-license-plate-end2end + cct-xs-v1-global-model (ankandrew) | detecția și citirea plăcuțelor | MIT | descărcate de fast-alpr la prima citire |
| MiniFASNet V1SE / V2 (yakhyo/face-anti-spoofing) | anti-spoofing: poză sau ecran arătat camerei | Apache 2.0 | descărcate de uniface |
| google/siglip-base-patch16-256-multilingual | căutare semantică | Apache 2.0 | opțional |
| OSNet x0.25 (torchreid, export ONNX) | re-identificarea persoanelor după corp | MIT (greutăți antrenate pe MSMT17, set de date pentru cercetare) | fișierul `data/osnet_x0_25_msmt17.onnx` se aduce separat; fără el re-ID e oprit |

Numele mărcilor (Hikvision, NVIDIA, ONVIF) aparțin deținătorilor lor; proiectul nu este
afiliat cu niciunul dintre ei.

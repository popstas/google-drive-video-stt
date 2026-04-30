# Drive Audio Extractor — план

Сервис, который мониторит указанные папки Google Drive, на каждое новое mp4 извлекает аудио в mp3 и кладёт рядом. Без десктопного приложения, без участия пользователя после деплоя.

Связанные документы:
- [[Транскрибация Google Drive — варианты Google]] — обоснование, зачем нужна экстракция (NotebookLM режет 200 МБ, Cloud STT не ест mp4).
- `tasks.md` → задача «Найти способ транскрибации разговоров (mp4 на Google Drive) ➕ 2026-04-30».

## Стэк

**Python 3.11+** (рекомендуется). Причины: google-api-python-client — официальный клиент с минимумом boilerplate; ffmpeg через `subprocess`; компактный Docker-образ.

TypeScript равнозначен — план тот же, отличается синтаксис.

## Архитектура

- **Polling** через Drive API `files.list` раз в N минут. Проще, чем `changes.watch` (тот требует HTTPS endpoint и renew подписки каждый час). Для Meet задержка 5–10 мин не критична.
- **OAuth user token** с refresh. Service Account не видит личный Drive с `Meet Recordings 1` — пройти OAuth локально один раз, дальше refresh_token живёт в env.
- **ffmpeg** через subprocess: `-vn -acodec libmp3lame -b:a 96k` (stereo, исходный sample rate, 96 kbps — архив + слушать). Часовая запись ≈ 43 МБ, всё ещё под лимит NotebookLM 200 МБ.
- **Идемпотентность** через sibling-файл: перед обработкой ищем `<basename>.mp3` в той же папке, есть — пропускаем. Без БД.
- **Изменяемые данные — в `./data/`**. Все секреты и runtime-state (`credentials.json`, `token.json`, любые будущие state-файлы) лежат в одной папке. Папка монтируется в контейнер как `/app/data`, добавлена в `.gitignore`. Перенос на другую машину = `scp -r data/ user@vps:/opt/drive-extractor/`.
- **Уведомления только об ошибках** — Telegram-бот через `https://api.telegram.org/bot<TOKEN>/sendMessage`. Успешные конвертации — молча (только в логи контейнера). Ошибки — короткое сообщение с именем файла и причиной. Без библиотеки `python-telegram-bot` — обычный `requests`.
- **Деплой**: Docker Compose на VPS. Один сервис, внутри Python-цикл с `sleep(POLL_INTERVAL)` между проходами. `restart: unless-stopped` — Docker сам поднимает после перезагрузки и при падении.

## Структура проекта

```
drive-audio-extractor/
├── pyproject.toml          # uv / poetry
├── README.md
├── .env.example            # FOLDER_IDS, BITRATE, POLL_INTERVAL, TELEGRAM_*
├── .gitignore              # data/, .env
├── docker-compose.yml
├── Dockerfile
├── data/                   # ← все изменяемые/секретные данные, gitignored
│   ├── credentials.json    # OAuth client (от GCP)
│   └── token.json          # refresh_token (получаем в фазе 2)
├── src/
│   ├── main.py             # точка входа, цикл с sleep(POLL_INTERVAL)
│   ├── auth.py             # OAuth flow + load/refresh token
│   ├── drive.py            # list_new_mp4, download, upload, has_sibling_mp3
│   ├── extractor.py        # ffmpeg wrapper
│   ├── notify.py           # Telegram sendMessage (только при ошибках)
│   └── config.py           # env, folder_ids
└── tests/
    └── test_extractor.py
```

## Шаги реализации

### 1. GCP проект и OAuth

- Создать проект в GCP, включить Drive API.
- OAuth consent screen → External, Testing, добавить свой email в test users.
- Credentials → OAuth client ID → Desktop app → скачать `credentials.json`.

### 2. Первичный refresh_token (одноразово, локально)

`python -m src.auth` → открывает браузер → подтверждение → сохраняет `data/token.json` с refresh_token. Этот файл едет вместе с проектом на VPS, повторный OAuth не нужен.

### 3. Drive watcher (`drive.py`)

```python
def list_unprocessed_mp4(service, folder_id):
    q = f"'{folder_id}' in parents and mimeType='video/mp4' and trashed=false"
    files = service.files().list(q=q, fields="files(id,name)").execute()["files"]
    existing_mp3 = {f["name"] for f in service.files().list(
        q=f"'{folder_id}' in parents and mimeType='audio/mpeg' and trashed=false",
        fields="files(name)").execute()["files"]}
    return [f for f in files if f["name"].rsplit(".", 1)[0] + ".mp3" not in existing_mp3]
```

### 4. Pipeline (`main.py`)

Контейнер живёт всегда, проверяет папки каждые `POLL_INTERVAL` секунд. Падение одного файла не валит цикл — ошибка логируется и уходит в Telegram, обработка продолжается.

```python
def process_file(service, f, folder_id):
    with tempfile.TemporaryDirectory() as tmp:
        mp4 = download(service, f["id"], tmp)
        mp3 = extract_mp3(mp4, bitrate=BITRATE)
        upload(service, mp3, folder_id)
    log.info(f"converted {f['name']}")

def run_once(service):
    for folder_id in FOLDER_IDS:
        for f in list_unprocessed_mp4(service, folder_id):
            try:
                process_file(service, f, folder_id)
            except Exception as e:
                log.exception(f"failed: {f['name']}")
                notify_error(f"❌ {f['name']}: {type(e).__name__}: {e}")

def main():
    service = build_drive_service()
    while True:
        try:
            run_once(service)
        except Exception as e:
            log.exception("cycle failed")
            notify_error(f"❌ cycle crashed: {type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)
```

### 5. Telegram-уведомления (`notify.py`)

```python
import os, logging, requests

log = logging.getLogger(__name__)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def notify_error(text: str) -> None:
    if not TOKEN or not CHAT_ID:
        return  # уведомления отключены
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text[:4000]},
            timeout=10,
        )
    except Exception:
        log.exception("telegram notify failed")  # никогда не валим основной поток
```

Ошибка отправки в Telegram сама не должна ронять сервис — ловим и логируем.

### 6. ffmpeg (`extractor.py`)

```python
def extract_mp3(mp4_path: Path, bitrate="96k") -> Path:
    mp3_path = mp4_path.with_suffix(".mp3")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(mp4_path),
        "-vn", "-acodec", "libmp3lame",
        "-b:a", bitrate,
        str(mp3_path)
    ], check=True, capture_output=True)
    return mp3_path
```

### 7. Docker

`Dockerfile`:
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY src/ ./src/
CMD ["uv", "run", "python", "-m", "src.main"]
```

`docker-compose.yml`:
```yaml
services:
  extractor:
    build: .
    container_name: drive-extractor
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data         # все изменяемые данные
      - extractor-tmp:/tmp        # рабочая директория ffmpeg
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  extractor-tmp:
```

`.env` (в корне репо, тоже в `.gitignore`):
```
FOLDER_IDS=15FvC-3QzpYzqyYcuIf-MuR47ijaxHVKe
POLL_INTERVAL=600
BITRATE=96k
TELEGRAM_BOT_TOKEN=          # пусто = уведомления выключены
TELEGRAM_CHAT_ID=
```

Пути к credentials и token зашиты в коде как `data/credentials.json` и `data/token.json` (рабочая директория контейнера `/app`). Менять через env обычно не нужно.

`.gitignore`:
```
data/
.env
__pycache__/
*.pyc
.venv/
```

**Фаза 1 — локально на ПК (проверка):**
```bash
git clone <repo> ~/projects/drive-extractor
cd ~/projects/drive-extractor
mkdir -p data
# положить data/credentials.json (из GCP)
# создать .env в корне (см. .env.example)
# первичный OAuth: docker compose run --rm extractor python -m src.auth
#   → откроется ссылка, авторизация в браузере, сохранится data/token.json
docker compose up --build              # foreground, видно логи в терминале
# либо отдельным окном:
docker compose up -d --build
docker compose logs -f
```

Прогон делаем на тестовой папке `15FvC...` с одним-двумя mp4. Убедиться: mp3 появился рядом, второй цикл пропускает обработанный файл, ошибки уходят в Telegram (если токен задан).

**Фаза 2 — переезд на VPS:**
```bash
# на VPS:
git clone <repo> /opt/drive-extractor
cd /opt/drive-extractor
# с локалки одной командой:
#   scp -r ~/projects/drive-extractor/data ~/projects/drive-extractor/.env user@vps:/opt/drive-extractor/
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` — Docker сам поднимает контейнер при перезагрузке хоста и при падении процесса.

`data/token.json` переносим как есть — refresh_token не привязан к машине. Повторно проходить OAuth-флоу на VPS не нужно.

### 8. Тестирование

- Положить тестовый mp4 в папку → подождать 1 цикл → проверить mp3 рядом.
- Перезапустить контейнер — второй проход пропускает уже обработанные.
- Проверить, что mp3 ≤ 200 МБ для часовой записи (~43 МБ при 96 kbps stereo).
- **Телеграм-канал ошибок**: положить битый mp4 (или временно сломать `FOLDER_IDS`) → убедиться, что пришло уведомление.
- **Тихий режим без бота**: оставить `TELEGRAM_BOT_TOKEN=` пустым → убедиться, что сервис работает без падений.

## Решения

- **Битрейт**: 96 kbps stereo (архив + слушать). ~43 МБ/час, проходит лимит NotebookLM 200 МБ.
- **Деплой**: Docker Compose. Сначала локально на ПК пользователя (фаза проверки), затем тот же `docker-compose.yml` переезжает на VPS. `restart: unless-stopped`. Внутренний цикл со sleep вместо системного таймера.

## Решения (продолжение)

- **Папки**: на этапе проверки — только первая (`15FvC-3QzpYzqyYcuIf-MuR47ijaxHVKe`). После теста на одной папке (один файл успешно конвертирован, идемпотентность работает) — добавить остальные 5 в `FOLDER_IDS` через запятую и `docker compose up -d`.

## Решения (продолжение)

- **Уведомления**: Telegram-бот через `sendMessage`, **только об ошибках** (упавший файл / упавший цикл). Успехи — только в логи контейнера. Без `TELEGRAM_BOT_TOKEN` — режим «совсем тихо».

## Решения (продолжение)

- **Деплой по фазам**: сначала локально на ПК через `docker compose up`, проверяем на одной папке с одним-двумя mp4. После успешной проверки — тот же `docker-compose.yml` едет на VPS. `data/` переносим вместе с проектом, повторный OAuth не нужен.
- **Все изменяемые данные — в `./data/`**: `credentials.json`, `token.json`, любые будущие state-файлы. Папка монтируется в контейнер как `/app/data`, в `.gitignore`.

## Открытые вопросы

1. **Telegram-бот для ошибок** — использовать существующий (например, тот, что настроен в `plugin:telegram` для Claude Code), или завести отдельный для этого сервиса?
2. **Какая VPS** будет использоваться на фазе 2 (для будущей справки)?

## Риски / подводные

- **OAuth refresh_token устаревает** при бездействии 6 мес или ручном отзыве (если consent screen в Testing — ещё агрессивнее, 7 дней). Нужен мониторинг (упало → алерт). Перевести consent screen в Production у Google после первой проверки.
- **Длительность конвертации**: ffmpeg на часовую запись — минут 5–10 на скромном CPU. Так как контейнер однопоточный (sleep после run_once), наложений не бывает в принципе.
- **Дисковое место на VPS**: mp4 + mp3 одновременно во время обработки. Чистить `/tmp` после каждого файла. Часовой mp4 ~500 МБ, держать запас 2–3 ГБ.
- **Drive API rate limits**: 1000 requests/100s/user. При 6 папках × 2 list-запроса = 12, много раз в десять минут — далеко не упрёмся.
- **Сетевой трафик**: каждый часовой mp4 = 500 МБ скачать + 43 МБ загрузить обратно. Если VPS с лимитом по трафику — учитывать.

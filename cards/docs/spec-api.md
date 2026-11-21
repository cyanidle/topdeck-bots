# Спецификация API распознавания карт

Слой распознавания (`cards/`) можно использовать четырьмя способами:

1. CLI `cardscan` — ручные прогоны и отладка.
2. Разделяемая библиотека `libcardscan.so` с C API (`cards/src/capi.h`).
3. Python-обёртка через `ctypes` (`cards/python/cardscan`).
4. Прямое подключение статической библиотеки `cardscan_core` из C++.

Бот (следующий этап) должен использовать вариант 3: он получает те же данные, что и CLI,
и не зависит от ABI C++.

## CLI

```
cardscan <input.(jpg|png|mp4|mov|...)> [options]

  --json <file>         записать отчёт JSON
  --overlay <file>      отладочный оверлей: для фото png/jpg, для видео mp4
  --set-codes <file>    словарь кодов сетов (через пробел/запятую) для прищёлкивания OCR
  --debug-dir <dir>     дамп размеченных кадров, кропов карт и сырого OCR
  --debug-every <n>     период дампа кадров (по умолчанию 30)
  --min-hits <n>        сколько кадров должен прожить трек (по умолчанию 4)
  --stride <n>          обрабатывать каждый n-й кадр видео (по умолчанию 1)
  --max-frames <n>      остановиться после n кадров
  --no-ocr              только геометрия, без распознавания текста
  --no-title            отключить fallback по названию карты
  --probe-card <file>   прогнать OCR по готовому кропу карты (отладка)
  --compare <a> <b>     сравнить два кропа (pHash и цветовая сигнатура)
  --parse <text>        как разбирается строка коллекционера (отладка)
  --reads <file>        пересобрать отчёт из таймлайна reads.txt без OCR
  --phash-index <file>  идентифицировать карты по индексу картинок (см. spec-scryfall-index)
  --build-index <meta.jsonl> <out.tsv>  собрать индекс pHash из кеша Scryfall
  --lookup <index.tsv> <image>          ранжировать карты индекса для картинки
  --ocr-bench <meta.jsonl> [--samples n]  сравнить варианты OCR на синтетических строках
  --verbose             подробный лог в stderr
```

Код возврата: 0 — успех, 1 — ошибка выполнения, 2 — ошибка аргументов.

## Формат отчёта

`type: "image"`:

```json
{
  "input": "/path/card.jpg",
  "type": "image",
  "width": 960,
  "height": 1280,
  "count": 1,
  "identified": 1,
  "unidentified": 0,
  "cards": [
    {
      "track": 1,
      "set": "DDS",
      "collector_number": "051",
      "collector_total": "065",
      "collector": "051/065",
      "language": "EN",
      "rarity": "C",
      "name": null,
      "identity": "DDS:051",
      "identified": true,
      "identified_by": "codes",
      "confidence": 0.92,
      "quality": 0.9,
      "hits": 1,
      "phash": "09997823f62b7c69",
      "merged_tracks": [1],
      "quad": [[168.4, 360.0], [778.5, 345.0], [820.2, 1227.1], [139.8, 1230.2]]
    }
  ]
}
```

`type: "video"` дополнительно содержит:

```json
{
  "fps": 29.97,
  "duration": 30.33,
  "frames": 908,
  "processed_frames": 908,
  "ocr_reads": 250,
  "ocr_reads_with_identity": 51,
  "elapsed_seconds": 125.8
}
```

и у каждой карты — таймлайн показа:

```json
{
  "first_frame": 0, "best_frame": 14, "last_frame": 28,
  "first_ts": 0.0, "best_ts": 0.47, "last_ts": 0.93,
  "hits": 15,
  "quad": [[...], ...]
}
```

Поля:

| поле | смысл |
|---|---|
| `count` | число карт в отчёте (для видео — число сегментов показа) |
| `identified` | сколько из них с распознанной идентичностью |
| `set` | код сета как напечатан (`DDS`, `C18`, `TMT`); сравнивать с `cards.set_code` в БД без учёта регистра |
| `collector` | `number/total` как напечатано (с ведущими нулями, если их прочитали) |
| `identity` | `SET:number` (или `N:NAME` для fallback по названию); пусто, если не распознано |
| `identified_by` | `codes`, `index` (по картинке, см. `spec-scryfall-index.md`), `name` или `null` |
| `confidence` | агрегированная уверенность 0…1 (не вероятность модели, а согласие чтений) |
| `phash` | pHash кропа карты (16 hex-символов) — для отладки и внешней склейки |
| `quad` | четыре точки контура карты в координатах исходного изображения/кадра |

Идентичность (`set` + `collector_number`) — это ровно те поля, что нужны для Scryfall:
`GET /cards/{set}/{collector_number}` или `cards.collection` с `{"set": "...", "collector_number": "..."}`.

## C API (`cards/src/capi.h`)

```c
typedef struct CardscanHandle CardscanHandle;

CardscanHandle *cardscan_create(void);
void            cardscan_destroy(CardscanHandle *handle);
const char     *cardscan_version(void);

int  cardscan_set_option(CardscanHandle *handle, const char *key, const char *value);
int  cardscan_set_set_codes(CardscanHandle *handle, const char *const *codes, int count);

char       *cardscan_analyze(CardscanHandle *handle, const char *path);
const char *cardscan_last_error(CardscanHandle *handle);
void        cardscan_free_string(char *text);
```

* `cardscan_analyze` сам определяет тип входа по расширению и возвращает JSON из раздела выше.
  Строку освобождает вызывающий через `cardscan_free_string`.
* Ошибки: `NULL` + текст в `cardscan_last_error`.
* Опции (`cardscan_set_option`): `ocr`, `title_fallback`, `min_track_hits`, `ocr_stride`,
  `max_frames`, `stride`, `debug_dir`, `overlay`.
* `cardscan_set_set_codes` — словарь кодов сетов (бот берёт его из своей БД).

Потокобезопасность: один `CardscanHandle` — один поток. Разные хэндлы независимы,
кроме Tesseract (внутри хэндла).

## Python (ctypes)

```python
from cardscan import Scanner, analyze

report = analyze("card.jpg")                      # разовый вызов
with Scanner(min_track_hits=6, ocr_stride=2) as scanner:
    scanner.set_set_codes(["dds", "c18", "tmt"])   # из БД бота
    video = scanner.analyze("cards.mp4")
print(video["count"])
```

* Библиотека ищется в `$CARDSCAN_LIBRARY`, затем `cards/build/libcardscan.so`.
* Ошибки — исключение `cardscan.CardscanError`.
* Никаких зависимостей, кроме стандартной библиотеки: обёртка — тонкий `ctypes`-слой.

## Сборка

```bash
cmake -S cards -B cards/build -DCMAKE_BUILD_TYPE=Release
cmake --build cards/build -j
ctest --test-dir cards/build --output-on-failure
python3 cards/python/test_cardscan.py
```

Зависимости: OpenCV ≥4 (core/imgproc/imgcodecs/videoio/img_hash/ximgproc), libtesseract ≥5
(рантайм + заголовки; без заголовков используется шим `third_party/tesseract_capi`),
`tessdata` с `eng.traineddata`. CTRE подтягивается `FetchContent` при конфигурации.

# cards — распознавание карт MTG по фото и видео

Нативный детектор карт (C++17 + OpenCV 4 + Tesseract 5) с C API и Python-обёрткой.
Изображение/видео → карты с кодом сета и номером коллекционера из нижнего левого угла
(`DDS 051/065`), с координатами карты на кадре, уверенностью и таймкодами показа.

Это ядро будущего «второго бота» (коллекции); Telegram-слой пока не делаем.
Инфраструктура (БД карт, клиент Scryfall, каталог) переиспользуется из `auctions/topbot`.

## Возможности

* Детекция карт по линиям с проверкой поддержки рёбер и сигнатурой строки коллекционера —
  карта не путается с похожим куском стола.
* Ректификация карты (гомография в 630×880), поиск строки коллекционера по изображению.
* OCR с несколькими вариантами предобработки, голосованием по регионам и прищёлкиванием
  кода сета к словарю (`--set-codes`).
* Подсчёт карт на видео по сегментам показа с защитой от ложных дублей.
* Отладочный оверлей: `--overlay out.mp4` / `out.png`, `--debug-dir` с кропами и сырым OCR.
* C API `libcardscan.so` + Python `ctypes`.

## Сборка

```bash
sudo apt install libopencv-dev libtesseract-dev tesseract-ocr-eng   # один раз
cmake -S cards -B cards/build -DCMAKE_BUILD_TYPE=Release
cmake --build cards/build -j
ctest --test-dir cards/build --output-on-failure
python3 cards/python/test_cardscan.py
```

CTRE (compile-time regex) подтягивается через `FetchContent` на этапе конфигурации —
нужен доступ к github.com (или заранее положенный `FETCHCONTENT_SOURCE_DIR_CTRE`).

Если `libtesseract-dev` не установлен, используется шим `third_party/tesseract_capi`:
движок линкуется напрямую с `libtesseract.so.5`.

## Быстрый старт

```bash
./cards/build/cardscan ~/Documents/cards.jpg --overlay /tmp/card.png --json /tmp/card.json
./cards/build/cardscan ~/Documents/cards.mp4 --overlay /tmp/cards.mp4 --json /tmp/cards.json \
    --set-codes cards/data/set-codes.txt --debug-dir /tmp/carddbg
```

```python
from cardscan import Scanner

with Scanner() as scanner:
    scanner.set_set_codes(["DDS", "C18", "TMT"])   # можно взять из БД бота
    report = scanner.analyze("cards.mp4")
print(report["count"], [c["identity"] for c in report["cards"]])
```

## Что получается на примерах

| вход | результат |
|---|---|
| `~/Documents/cards.jpg` (960×1280, стопка карт) | `DDS 051/065 C EN`, уверенность 1.0 |
| `~/Documents/cards.mp4` (720p, 30 с, ~15 карт) | 15 сегментов, 7 с распознанным номером, дублей идентичностей нет |

Подробные замеры и ограничения — в [`docs/spec-detector.md`](docs/spec-detector.md),
формат отчёта и API — в [`docs/spec-api.md`](docs/spec-api.md).

## Отладочный оверлей

* Фото: размеченный PNG с четырёхугольниками и подписями `SET number/total conf`.
* Видео: MP4 с треками (`T<id>`), текущим чтением, HUD со счётчиками.
* `--debug-dir`: `frame_XXXXXX.jpg` (размеченные кадры), `track_XXXX.jpg` (лучший кроп карты),
  `track_XXXX_fNNNNNN_aXX_*.png` и `cand_fNNNNNN_aXX_*.png` (то, что реально ушло в Tesseract:
  путь треков и путь подсчёта), `*_raw.txt`, `reads.txt` (таймлайн OCR).

## Интеграция с ботом

Идентичность карты — `set` + `collector_number`, ровно как в `cards.set_code` /
`cards.collector_no` (`auctions/topbot/schema.sql`) и как ждёт Scryfall
(`ScryfallClient.collection` с `{"set": ..., "collector_number": ...}`).

## Ограничения

* Мелкий текст коллекционера на 720p читается нестабильно; часть карт остаётся `unknown`.
* Русские названия карт не читаются (нет `rus.traineddata`), fallback по названию — только латиница.
* Словарь кодов сетов не скачивается: его передаёт вызывающая сторона.

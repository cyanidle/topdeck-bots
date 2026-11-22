# AGENTS.md — правила работы в `cards/`

## Что это

Нативный движок распознавания карт MTG (C++17, OpenCV, Tesseract) + C API + Python-обёртка.
Бот-слой сюда не входит: он будет отдельной папкой и переиспользует `auctions/topbot`.

## Сборка и проверки

```bash
cmake -S cards -B cards/build -DCMAKE_BUILD_TYPE=Release
cmake --build cards/build -j
ctest --test-dir cards/build --output-on-failure        # юнит-тесты C++
python3 cards/python/test_cardscan.py                   # ctypes-смоук
CARDSCAN_SAMPLE_IMAGE=~/Documents/cards.jpg cards/tests/run_golden.sh
python3 cards/tools/scryfall_index.py --stats           # состояние индекса картинок
```

Индекс картинок (`cards/data/scryfall/`, в git не попадает) ведёт
`cards/tools/scryfall_index.py` — см. `docs/spec-scryfall-index.md`. Без индекса
`run_golden.sh` проходит, но гейт идентичностей скипается.

Все проверки должны быть зелёными перед коммитом. Отладочные артефакты — только в
`/tmp` или `--debug-dir`, в репозиторий не попадают (`build/` в `.gitignore`).

## Код

* C++17, без исключений в горячем цикле, без `#include <regex>` (используется CTRE).
* Комментарии не пишем. Единственное исключение — неочевидные инварианты
  (пример: шим символа `xmlNanoHTTPCleanup` в `src/capi.cpp`).
* Любая эвристика детекции/OCR должна быть параметром с дефолтом в `DetectorParams`,
  `OcrParams` или `AnalyzerOptions`, а не «магическим числом» в коде.
* Заголовок `src/capi.h` — публичный контракт: менять только вместе с
  `python/cardscan/__init__.py` и `docs/spec-api.md`.
* Файлы верхнего уровня `features.h/cpp` запрещены как имена: `src` в include-путях
  перекрывает системный `<features.h>` glibc (уже ловили). Поэтому `text_runs.h`.

## Документация

* Меняешь алгоритм — правь `docs/spec-detector.md` тем же коммитом.
* Меняешь отчёт/API — правь `docs/spec-api.md`.
* Замеры качества (что получилось на `cards.jpg` / `cards.mp4`) — в `docs/spec-detector.md`,
  обновляй их при изменениях, влияющих на точность.

## Коммиты

Как в корневом `AGENTS.md`: одна законченная вещь — один коммит, сообщение по-русски
(`feat:`, `fix:`, `docs:`, `test:`), первая строка короткая.

## Тестовые данные

`~/Documents/cards.jpg` и `~/Documents/cards.mp4` — пользовательские примеры, в репозиторий
не коммитятся. Тесты берут пути из `CARDSCAN_SAMPLE_IMAGE` / `CARDSCAN_SAMPLE_VIDEO`
и скипаются, если файлов нет.

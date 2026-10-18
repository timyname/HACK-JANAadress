# Jana Address — sample-derived contract

> Версия: **0.2**
> Статус: **IMPLEMENTED_SERVICE_CONTRACT / ORGANIZER_RUNNER_UNVERIFIED**
> Дата: **2026-10-16**
> Scope: официальный файловый input/output contract Challenge 01.

## 1. Источники и контрольные суммы

| Источник | SHA-256 | Статус |
|---|---|---|
| local-controlled `docs/source/JanaAddress_TZ_final_v4.docx` | `672225C8AE94352FFA26CAA2ED5BE69E00A4304A68F776466143B84C4A249309` | organizer specification; Git-ignored pending redistribution decision |
| local-only `data/private/official/sample_input.json` | `28624778E5935C72D783DC905A59FE51E7E24454890692FA9B5EF12AF35AAA76` | official sample; Git-ignored |
| local-only `data/private/official/sample_output.json` | `A13B55BAA92105BF473FC610D9C54CE62597FD767F6D11F7CA397F77B59F1B56` | official sample; Git-ignored |

`train.json` не предоставлен. Сырые sample-строки не переносятся в этот документ, Git, логи, screenshots или облачный preview.

## 2. Подтверждённый файловый контракт

- Входной JSON — top-level array.
- Каждая входная запись содержит `id: string` и `raw_address: string`.
- Выходной JSON — top-level array.
- Для каждой входной позиции возвращается ровно одна выходная позиция в том же порядке.
- `id` и decoded Unicode value `raw_address` сохраняются без изменений.
- Наблюдаемый в official output sample и принятый командой канонический порядок public-полей:

```text
id
raw_address
province
city
district
living_area
street_type
street_name
house
apartment
entrance
floor
landmark
confidence
```

- Неизвестные parsed-поля возвращаются как JSON `null`.
- DOCX требует строки для номерных компонентов и `null` для неизвестных полей. Команда принимает `string | null` для всех parsed-полей как canonical serializer policy; единственный sample не наблюдает ненулевые значения для каждого nullable field, поэтому это не маркируется как полностью sample-proven.
- `confidence` — конечное JSON number в диапазоне `[0, 1]`.
- Номера дома, квартиры, подъезда и этажа сериализуются строками; дроби и литеры не теряются.
- `street_name` возвращает surface form из входной строки без исправления опечаток, регистра или падежа.
- Internal explain/provenance metadata не добавляется в public output.

Machine-readable snapshots:

- `sample-input.schema.json`;
- `sample-output.schema.json`.

## 3. Оценивание, влияющее на контракт

- 60% адресной метрики: `province`, `city`, `street_name`, `house`.
- 25%: `apartment`, `entrance`, `floor`.
- 15%: `district`, `living_area` только в записях, где они присутствуют в исходной строке.
- `street_type`, `landmark` и `confidence` не входят в автоматическую field-метрику, но проверяются жюри на защите.
- Справочное восстановление отсутствующих в строке `district`/`living_area` не входит в автоматическую метрику; полнота, источник и сопоставление оцениваются на защите как инженерная часть.
- Текстовые поля сравниваются после lower-case, удаления пунктуации и лишних пробелов. Падежи и опечатки не прощаются.
- Числовые компоненты сравниваются как строки: потеря дроби или литеры является ошибкой.

Эти проценты описывают внутренние группы hidden metric. Общая оценка команды: hidden metrics 45%, engineering 30%, interface 15%, presentation 10%.

## 4. Что ещё не заморожено

Финальный ТЗ и sample-файлы не определяют:

- точный submission endpoint/path;
- HTTP envelope, content type и способ передачи файла;
- response status/error policy для невалидного batch;
- допустимость неизвестных input keys;
- обязательность точного key order для evaluator;
- закрытый enum всех значений `street_type`;
- canonical RU/KZ policy для редких bilingual cases;
- точную формулу агрегации внутри групп 60/25/15;
- organizer-approved способ передачи заранее собранных offline images.

Точная метрика и маршрут организатора остаются неподтверждёнными. Это не блокирует реализацию. Сервис поддерживает POST `/parse`, `/batch`, `/api/parse`, `/api/v1/parse`, JSON array и multipart `file`; internal `/internal/parse` добавляет происхождение полей. Публичные ответы сохраняют canonical shape без metadata. Canonical RU принят для административных имён и типа улицы. Extra input keys игнорируются, invalid batch отклоняется целиком. Лимиты: 10 000 записей, 10 MiB request, 8 192 символа адреса.

## 5. Privacy и cloud boundary

- Official sample и будущий `train.json` используются только локально.
- Public preview использует только полностью synthetic, non-reversible fixtures. Redacted official-derived строки остаются local-only.
- Public preview не монтирует `data/private` или `docs/source`, не включает их в image/build context и не сохраняет адреса в analytics/logs.
- До отдельного подтверждения организаторов cloud preview является демонстрацией UI/архитектуры, а не финальным submission runtime.

## 6. Change control

Изменение этого контракта требует:

1. новой organizer evidence или фактического runner contract;
2. новой версии и SHA-256 source inventory;
3. обновления схем;
4. повторного contract/property test;
5. controlled invalidation submission adapter, UI export и связанных evidence.

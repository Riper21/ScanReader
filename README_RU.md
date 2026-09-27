[English](README.md) | [Русский](README_RU.md)

# ScanReader (AI-Система распознавания документов)

[![CI](https://github.com/organization/scan-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/organization/scan-reader/actions)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

**ScanReader** — это промышленная платформа на Python 3.9+ для мультимодального распознавания юридических документов, Fast-Path классификации, Zero-Trust аудита финансовых и регистрационных реквизитов, а также автоматизированной выгрузки в 1С:Предприятие и многостраничные Excel-реестры.

Платформа спроектирована для работы в закрытом корпоративном контуре с локальными VLM (Qwen2.5-VL через Ollama/vLLM) и внешними эндпоинтами OpenAI/DeepSeek с гарантией детерминированной проверки достоверности данных.

---

## Ключевые возможности и гарантии

- **Fast-Path маршрутизатор (Router):** двухзонное наблюдение шапки (Dual-Zone) и пошаговый Chain-of-Thought анализ типа документа менее чем за 1 секунду.
- **Zero-Trust Verification Engine:** независимый детерминированный аудит вывода нейросети:
  - Алгоритмический расчет контрольных разрядов РФ: ИНН (10 и 12 знаков), СНИЛС, ОГРН/ОГРНИП, БИК и 20-значных банковских счетов ЦБ РФ.
  - Математическая сверка финансовых сумм: $\text{Итого} = \text{Основной долг} + \text{Исполнительский сбор / Неустойка} + \text{Судебные расходы}$.
  - Валидация законодательных ограничений по ст. 99 Федерального закона № 229-ФЗ (не более 50% по общим исполнительным листам, до 70% по алиментам/возмещению вреда).
  - Контроль юридической хронологии: Дата решения суда $\le$ Дата исполнительного листа $\le$ Дата возбуждения ИП.
- **Cross-Modal Антигаллюцинационный шлюз:** сопоставление извлеченных реквизитов, сумм и ФИО должника с сырыми токенами OCR/текстового слоя документа.
- **JSON-RPC Stdio-сервер:** встроенный приватный JSON-RPC 2.0 сервер с 5 инструментами для доверенных интеграций внутри корпоративного контура.
- **Отказоустойчивый атомарный ввод-вывод:** запись файлов через временные буферы с `fsync` и атомарной заменой (защита от повреждения при сбоях питания).
- **Канонический двухфайловый формат JSON (Full & Flat):** сохранение полной иерархической модели `{stem}_Full.json` (аудит, Guardrails, токены) и одноуровневого файла `{stem}_Flat.json` для прямой загрузки в 1С без вложенных структур.
- **100% обратная совместимость:** полная поддержка существующих скриптов импорта в 1С и батников операторов.

---

## Поддерживаемые типы документов (Universal Enterprise Matrix)

ScanReader построен на расширяемой архитектуре плагинов (`TypeRegistry`), поддерживающей 9 ключевых категорий делопроизводства РФ «из коробки»:

| Категория | Плагин (`ID`) | Ключевые сущности & Проверки Zero-Trust | Форматы выгрузки |
| :--- | :--- | :--- | :--- |
| **Договоры** | `commercial_contracts` | Стороны, ИНН/КПП, ОГРН, уставные полномочия подписантов, предмет, общая сумма, ставка НДС, сроки действия. | 1С, Excel, Full JSON |
| **УПД и накладные** | `invoices_upd` | Статус 1/2, продавец/покупатель, ИНН 10/12, табличные товарные строки, ставки НДС 20%/10%/0%, суммы без НДС и с НДС. | 1С:УТ/ERP, Excel, Full JSON |
| **Акты сдачи-приемки** | `acceptance_certificates` | Заказчик, исполнитель, договор-основание, период, перечень услуг/работ, общая стоимость, отметка об отсутствии претензий. | 1С:Бухгалтерия, Excel, Full JSON |
| **Доверенности** | `powers_of_attorney` | Доверитель, представитель (паспортные данные, СНИЛС), объем полномочий, право передоверия, нотариальное удостоверение, срок. | 1С:ЗУП/ERP, Excel, Full JSON |
| **Досудебные претензии** | `legal_claims` | Кредитор, должник, договор-основание, основной долг, неустойка/пени, расчет ст. 395 ГК РФ, дедлайн для добровольного ответа. | Юр. системы, Excel, Full JSON |
| **Кадровые приказы** | `hr_orders` | Унифицированные формы Т-1, Т-5, Т-6, Т-8, табельный номер, подразделение, оклад/ставка, даты приема/увольнения, трудовой договор. | 1С:ЗУП, Excel, Full JSON |
| **Приказы ИП** | `enforcement_orders` | Постановления и заявления о возбуждении ИП, отделы РОСП/УФССП, реквизиты взыскателя и должника. | 1С, Excel, Full JSON |
| **Исполнительные листы** | `executive_documents` | Серии ФС/ВС, решения судов РФ, суммы присужденного взыскания, реквизиты арбитражных судов. | 1С, Excel, Full JSON |
| **Взыскание на зарплату** | `salary_deductions` | Постановления ФССП по доходам должника, ст. 99 229-ФЗ, ограничение удержаний 50%/70%, банковские реквизиты. | 1С:ЗУП, Excel, Full JSON |

---

## Установка

### Базовая установка библиотеки
```bash
pip install .
```

### Установка инструментов разработки и тестирования
```bash
pip install ".[dev]"
```

---

## Использование в командной строке (CLI)

Консольный интерфейс `scan-reader` поддерживает модульные подкоманды и машиночитаемый флаг `--json`:

```bash
# 1. Обработка документа: возврат пути к плоскому JSON для 1С (режим по умолчанию)
scan-reader run "C:\Scans\order.pdf" --format flat

# 2. Обработка с возвратом полного иерархического JSON
scan-reader run "C:\Scans\order.pdf" --format full

# 3. Обработка с возвратом путей к обоим файлам (Full и Flat)
scan-reader run "C:\Scans\order.pdf" --format both

# 2. Полная обработка со структурированным выводом JSON и Zero-Trust отчетом
scan-reader run "C:\Scans\order.pdf" --json

# 3. Быстрая Fast-Path классификация документа по шапке
scan-reader classify "C:\Scans\order.pdf" --json

# 4. Независимый Zero-Trust аудит ранее извлеченного JSON-файла
scan-reader verify "output\order_salary_deductions_Full.json" --json

# 5. Сборка сводных реестров 1С и многостраничного Excel
scan-reader export "output" --format both

# 6. Самодиагностика платформы (VLM-модель, TTFT, токены, GPU, окружение)
scan-reader doctor

# 7. Запуск приватного JSON-RPC сервера (5 инструментов) по Stdio
scan-reader mcp
```

### Коды возврата (Exit Codes)
- `0`: Успешно (документы обработаны, Zero-Trust аудит пройден).
- `1`: Ошибка обработки или входной файл не найден.
- `2`: Ошибка синтаксиса аргументов командной строки.
- `3`: Обнаружены расхождения (требуется ручная проверка оператором).
- `4`: Сработал аварийный эвристический фолбэк.

---

## Использование через Python API

```python
from scan_reader import LegalDocPlatformFacade
from scan_reader.verifier import ZeroTrustAuditor, VerificationStatus

# Инициализация фасада платформы
facade = LegalDocPlatformFacade()

# Обработка документа через сквозной конвейер
result = facade.process_single_document("scan.pdf")
print("Тип документа:", result["doc_type"])
print("Оценка качества:", result["quality_score_percent"])

# Запуск независимого Zero-Trust аудита
report = ZeroTrustAuditor.audit_document(
    data=result["data"],
    doc_type=result["doc_type"],
    raw_ocr_text=result.get("raw_text")
)

if report.status == VerificationStatus.ZERO_TRUST_VERIFIED:
    print("✅ Документ подтвержден Zero-Trust аудитором на 100%")
elif report.status == VerificationStatus.DISCREPANCY_DETECTED:
    print("⚠️ Обнаружены расхождения:")
    for issue in report.issues:
        print(f"  • [{issue.code}] {issue.message}")
```

---

## JSON-RPC интеграция (Stdio)

ScanReader предоставляет **приватный строчно-ориентированный JSON-RPC 2.0 сервер** с 5 инструментами
(запуск: `scan-reader mcp`). Протокол приватный: он **не совместим** с официальным MCP SDK
(нет Content-Length framing и handshake `initialize`) и предназначен для доверенных интеграций
внутри корпоративного контура. Доступ к файлам ограничен корнями из `SCANREADER_ALLOWED_DIRS`.

1. `scan_document`: полный цикл распознавания и Zero-Trust аудита.
2. `classify_document`: экспресс-роутинг документа по первой странице.
3. `verify_legal_data`: алгоритмический аудит реквизитов, сумм и дат.
4. `run_benchmark`: запуск эталонного тестирования против Ground Truth.
5. `export_results`: консолидация данных в реестры Excel и 1C.

### Пример запроса (один JSON-объект на строку)
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "classify_document", "arguments": {"file_path": "incoming/order.pdf"}}}
```

---

## Архитектура и таксономия статусов

Подробные диаграммы компонентов, жизненный цикл обработки и семантика статусов представлены в документе [ARCHITECTURE_RU.md](ARCHITECTURE_RU.md) ([English](ARCHITECTURE.md)).

---

## Лицензия

Лицензия MIT. Подробнее см. в [LICENSE_RU.md](LICENSE_RU.md).

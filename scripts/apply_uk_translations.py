#!/usr/bin/env python3
"""
Apply Ukrainian translations to uk_UA.ts for all unfinished entries.
Run once, then compile with: ./scripts/compile_translations.sh
"""
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# source text → Ukrainian translation
# Keys are the raw source text exactly as it appears in the .ts XML
# (HTML entities already decoded by ElementTree, so use plain text here)
# ---------------------------------------------------------------------------
TRANSLATIONS: dict[str, str] = {
    # ── AdvancedSearchDialog ────────────────────────────────────────────────
    "Search timed out — simplify the pattern":
        "Час пошуку вичерпано — спростіть шаблон",

    # ── ExportModeDialog ────────────────────────────────────────────────────
    "Export Mode": "Режим експорту",
    "Select export mode:": "Оберіть режим експорту:",
    "All strings": "Всі рядки",
    "Translated only": "Тільки перекладені",
    "OK": "OK",
    "Cancel": "Скасувати",

    # ── MainWindow — menu actions ────────────────────────────────────────────
    "Stop Translation": "Зупинити переклад",
    "Stop": "Стоп",
    "Stopping translation...": "Зупинення перекладу...",
    "Import from &XML (SST)...": "Імпортувати з &XML (SST)...",
    "Export to &XML (SST)...": "Експортувати в &XML (SST)...",
    "&Quality Check...": "&Перевірка якості...",
    "Load Translation &Memory...": "Завантажити &пам’ять перекладів...",
    "Translate Starfield Interface TXT...": "Перекласти TXT інтерфейсу Starfield...",
    "Quality Check": "Перевірка якості",

    # ── MainWindow — quick-add dialog (inline) ───────────────────────────────
    "Add Protected Terms": "Додати захищені терміни",
    "Detected potential company/faction names. Select and add to protection list:":
        "Виявлено потенційні назви компаній/фракцій. Виберіть та додайте до списку захисту:",
    "Category:": "Категорія:",
    "Add Selected": "Додати вибрані",
    "Skip": "Пропустити",

    # ── MainWindow — file I/O ────────────────────────────────────────────────
    "Open File": "Відкрити файл",
    "All Supported Files (*.strings *.dlstrings *.ilstrings *.esp *.esm *.esl *.STRINGS *.DLSTRINGS *.ILSTRINGS *.ESP *.ESM *.ESL);;String Files (*.strings *.dlstrings *.ilstrings);;Plugin Files (*.esp *.esm *.esl);;All Files (*)":
        "Всі підтримувані файли (*.strings *.dlstrings *.ilstrings *.esp *.esm *.esl);;Рядкові файли (*.strings *.dlstrings *.ilstrings);;Файли плагінів (*.esp *.esm *.esl);;Всі файли (*)",
    "Bethesda String Files (*.strings *.dlstrings *.ilstrings *.STRINGS *.DLSTRINGS *.ILSTRINGS);;All Files (*)":
        "Рядкові файли Bethesda (*.strings *.dlstrings *.ilstrings);;Всі файли (*)",
    "Plugin Files (*.esp *.esm *.esl);;All Files (*)":
        "Файли плагінів (*.esp *.esm *.esl);;Всі файли (*)",
    "Loading {filename}...": "Завантаження {filename}...",
    "Encoding: {encoding}": "Кодування: {encoding}",
    "Strings: {count}": "Рядків: {count}",
    "Loaded {count} strings": "Завантажено {count} рядків",
    "Loaded {count} strings from {name}": "Завантажено {count} рядків з {name}",
    "Error": "Помилка",
    "Failed to load:\n{error}": "Не вдалося завантажити:\n{error}",
    "Localized Plugin": "Локалізований плагін",
    "{name} is a localized plugin.\nIts text is stored in companion .strings/.dlstrings/.ilstrings files.\nOpen those files instead to translate them.":
        "{name} — локалізований плагін.\nЙого текст зберігається у супутніх файлах .strings/.dlstrings/.ilstrings.\nВідкрийте ті файли для перекладу.",
    "Failed to load plugin:\n{error}": "Не вдалося завантажити плагін:\n{error}",
    "Saved successfully ✓": "Збережено успішно ✓",
    "Failed to save:\n{error}": "Не вдалося зберегти:\n{error}",
    "Save As": "Зберегти як",
    "Saved to {filename}": "Збережено у {filename}",

    # ── MainWindow — translation ─────────────────────────────────────────────
    "No Selection": "Нічого не вибрано",
    "Select strings first.": "Спочатку виберіть рядки.",
    "Added {count} protected terms": "Додано {count} захищених термінів",
    "Same Language": "Однакова мова",
    "Source and target languages are identical.": "Мови джерела та цілі однакові.",
    "Nothing to Translate": "Нічого перекладати",
    "All selected strings are already translated.": "Всі вибрані рядки вже перекладено.",
    "Translating {current}/{total}...": "Перекладання {current}/{total}...",
    "Translating: {current}/{total}": "Перекладання: {current}/{total}",
    "Complete": "Завершено",
    "{msg}\nCheck log for details.": "{msg}\nДивіться журнал для деталей.",
    "Success": "Успішно",
    "Quality: {errors} error(s), {warnings} warning(s) — open Translation → Quality Check for details":
        "Якість: {errors} помилок, {warnings} попереджень — відкрийте Переклад → Перевірка якості",

    # ── MainWindow — Starfield TXT ───────────────────────────────────────────
    "Open Starfield Interface TXT": "Відкрити TXT інтерфейсу Starfield",
    "Text Files (*.txt *.TXT);;All Files (*)": "Текстові файли (*.txt);;Всі файли (*)",
    "Save Translated TXT As": "Зберегти перекладений TXT як",
    "No translatable lines found in the TXT file.": "У TXT-файлі не знайдено рядків для перекладу.",
    "Translating TXT {current}/{total}...": "Переклад TXT {current}/{total}...",
    "Failed to read TXT:\n{error}": "Не вдалося прочитати TXT:\n{error}",
    "TXT Translation Complete: {count} successful": "Переклад TXT завершено: {count} успішно",
    "Failed to save translated TXT:\n{error}": "Не вдалося зберегти перекладений TXT:\n{error}",

    # ── MainWindow — translation memory ─────────────────────────────────────
    "Load Translation Memory": "Завантажити пам’ять перекладів",
    "Text Files (*.txt);;All Files (*)": "Текстові файли (*.txt);;Всі файли (*)",
    "Translation memory loaded: {loaded} entries, {applied} applied to current file":
        "Пам’ять перекладів завантажено: {loaded} записів, {applied} застосовано до файлу",
    "Load Failed": "Помилка завантаження",
    "Could not load translation memory:\n{error}": "Не вдалося завантажити пам’ять перекладів:\n{error}",

    # ── MainWindow — TXT export/import ──────────────────────────────────────
    "Export to TXT": "Експортувати в TXT",
    "Exporting to {filename}...": "Експортування в {filename}...",
    "Exported {count} strings to {filename} ✓": "Експортовано {count} рядків у {filename} ✓",
    "Export Complete": "Експорт завершено",
    "Successfully exported {count} strings to:\n{path}": "Успішно експортовано {count} рядків до:\n{path}",
    "Failed to export:\n{error}": "Не вдалося експортувати:\n{error}",
    "Import from TXT": "Імпортувати з TXT",
    "Importing from {filename}...": "Імпортування з {filename}...",
    "Importing {current}/{total}...": "Імпортування {current}/{total}...",
    "Importing: {current}/{total}": "Імпортування: {current}/{total}",
    "Imported {count} translations from {filename} ✓": "Імпортовано {count} перекладів з {filename} ✓",
    "Successfully imported {count} translations from:\n{path}": "Успішно імпортовано {count} перекладів з:\n{path}",
    "\n\n(Skipped {count} untranslated entries)": "\n\n(Пропущено {count} неперекладених записів)",
    "Import Complete": "Імпорт завершено",
    "Failed to import:\n{error}": "Не вдалося імпортувати:\n{error}",

    # ── MainWindow — XML import/export ──────────────────────────────────────
    "Import from XML (SST)": "Імпортувати з XML (SST)",
    "XML Files (*.xml *.sst);;All Files (*)": "Файли XML (*.xml *.sst);;Всі файли (*)",
    "Importing from XML {filename}...": "Імпортування з XML {filename}...",
    "No Translations": "Немає перекладів",
    "No valid translations found in the XML file.": "У XML-файлі не знайдено дійсних перекладів.",
    "Imported {count} translations from XML ✓": "Імпортовано {count} перекладів з XML ✓",
    "Successfully imported {count} translations from XML.": "Успішно імпортовано {count} перекладів з XML.",
    "Failed to import XML:\n{error}": "Не вдалося імпортувати XML:\n{error}",
    "Export to XML (SST)": "Експортувати в XML (SST)",
    "XML Files (*.xml);;All Files (*)": "Файли XML (*.xml);;Всі файли (*)",
    "Exporting to XML {filename}...": "Експортування в XML {filename}...",
    "Exported {count} entries to XML ✓": "Експортовано {count} записів в XML ✓",
    "Successfully exported {count} entries to XML.": "Успішно експортовано {count} записів в XML.",
    "Failed to export XML:\n{error}": "Не вдалося експортувати XML:\n{error}",

    # ── MainWindow — comparison ──────────────────────────────────────────────
    "Comparison": "Порівняння",
    "No string data found in comparison file.": "У файлі порівняння не знайдено рядкових даних.",
    "Comparison loaded: {count} strings mapped.": "Порівняння завантажено: зіставлено {count} рядків.",
    "Comparison Loaded": "Порівняння завантажено",
    "Comparison data from {filename} loaded.\nDifferences are highlighted in yellow.":
        "Дані порівняння з {filename} завантажено.\nВідмінності виділено жовтим.",
    "Failed to load comparison file:\n{error}": "Не вдалося завантажити файл порівняння:\n{error}",

    # ── MainWindow — settings I/O ────────────────────────────────────────────
    "JSON Files (*.json *.JSON);;All Files (*)": "Файли JSON (*.json);;Всі файли (*)",
    "Config File": "Файл конфігурації",
    "Config file does not exist yet. Settings will be saved on first use.\n\nConfig path: {path}":
        "Файл конфігурації ще не існує. Налаштування буде збережено під час першого використання.\n\nШлях: {path}",
    "Export Settings": "Експортувати налаштування",
    "Export Successful": "Експорт успішний",
    "Settings exported to:\n{path}": "Налаштування експортовано до:\n{path}",
    "Export Failed": "Помилка експорту",
    "Could not export settings.": "Не вдалося експортувати налаштування.",
    "Import Settings": "Імпортувати налаштування",
    "Import Failed": "Помилка імпорту",
    "Could not import settings file.": "Не вдалося імпортувати файл налаштувань.",
    "Validation Warnings": "Попередження перевірки",
    "Imported settings have issues:\n": "Імпортовані налаштування мають проблеми:\n",
    "\n\nImport anyway?": "\n\nВсе одно імпортувати?",
    "Import Successful": "Імпорт успішний",
    "Settings imported from:\n{path}\n\nRestart may be required for some changes to take effect.":
        "Налаштування імпортовано з:\n{path}\n\nДля деяких змін може знадобитися перезапуск.",

    # ── QualityDialog ────────────────────────────────────────────────────────
    "Quality Check Results": "Результати перевірки якості",
    "No quality issues found — all translations look good.":
        "Проблем якості не знайдено — всі переклади виглядають добре.",
    "{errors} error(s)  ·  {warnings} warning(s)  ·  {infos} info  across {total} string(s)":
        "{errors} помилок  ·  {warnings} попереджень  ·  {infos} інформаційних  у {total} рядках",
    "CSV Spreadsheet (*.csv);;Text Log (*.txt);;HTML Report (*.html);;All Files (*)":
        "Таблиця CSV (*.csv);;Текстовий журнал (*.txt);;HTML-звіт (*.html);;Всі файли (*)",
    "Could not write report:\n{error}": "Не вдалося записати звіт:\n{error}",

    # ── SettingsDialog ───────────────────────────────────────────────────────
    "translategemma3-st: Custom modified for Starfield Ukrainian localization\ntranslategemma3-st-2: Higher quality, typically slower.":
        "translategemma3-st: Спеціально модифікована для локалізації Starfield\ntranslategemma3-st-2: Вища якість, зазвичай повільніша.",
    "Connection Test": "Перевірка з’єднання",
    "English": "Англійська",
    "Ukrainian": "Українська",
    "● Testing Ollama...": "● Перевірка Ollama...",
    "● Testing...": "● Перевірка...",
    "● Model '{model}' not found": "● Модель '{model}' не знайдено",
    "Model Not Found": "Модель не знайдено",
    "Model '{model}' is not installed.\n\nAvailable models:\n":
        "Модель '{model}' не встановлена.\n\nДоступні моделі:\n",
    "\n\nInstall with: ollama pull translategemma3-st":
        "\n\nВстановіть: ollama pull translategemma3-st",
    "● Connected ✓": "● Підключено ✓",
    "Connected to Ollama!\nModel '{model}' is ready.": "Підключено до Ollama!\nМодель '{model}' готова.",
    "● Connection failed": "● Помилка з’єднання",
    "Connection Error": "Помилка з’єднання",
    "Could not connect to Ollama at {url}\n\nMake sure Ollama is running:\n  • Start with: ollama serve\n  • Default URL: http://localhost:11434":
        "Не вдалося підключитися до Ollama за адресою {url}\n\nПереконайтеся, що Ollama запущена:\n  • Запустіть: ollama serve\n  • URL за замовчуванням: http://localhost:11434",
    "● Error": "● Помилка",
    "Unexpected error: {error}": "Несподівана помилка: {error}",
    "Select Protected Terms File": "Вибрати файл захищених термінів",
    "Cache": "Кеш",
    "No translation cache is active.": "Кеш перекладів не активний.",
    "Remove all cached translations?\nThis cannot be undone.":
        "Видалити всі кешовані переклади?\nЦю дію не можна скасувати.",
    "Translation cache cleared.": "Кеш перекладів очищено.",
    "When translating from non-English source (e.g. Russian) to Ukrainian, keep English words/phrases unchanged.\nUseful for preserving names, titles, and terminology that should remain in English.\nNote: This is automatically disabled when English is the source language.":
        "При перекладі з не-англійського джерела (напр. російської) на українську залишати англійські слова/фрази без змін.\nКорисно для збереження імен, назв та термінів, що мають залишатися англійською.\nПримітка: автоматично вимикається, якщо джерело — англійська.",
    "When translating from Russian to Ukrainian, keep English words/phrases unchanged.\nUseful for preserving names, titles, and terminology that should remain in English.":
        "При перекладі з російської на українську залишати англійські слова/фрази без змін.\nКорисно для збереження імен, назв та термінів, що мають залишатися англійською.",
    "Number of parallel translation threads (1–32). Higher values increase throughput but may overwhelm Ollama. Default: 10.":
        "Кількість паралельних потоків перекладу (1–32). Вищі значення збільшують пропускну здатність, але можуть перевантажити Ollama. За замовчуванням: 10.",
    "Action to take for strings exceeding the threshold:\n- Translate: Proceed with translation (may take long)\n- Original: Immediately return original text\n- Skip: Leave untranslated and mark as pending":
        "Дія для рядків, що перевищують поріг:\n- Перекласти: продовжити переклад (може зайняти багато часу)\n- Оригінал: негайно повернути оригінальний текст\n- Пропустити: залишити неперекладеним і позначити як очікуваний",

    # ── ThemeDialog ──────────────────────────────────────────────────────────
    "{type} theme\n{description}": "{type} тема\n{description}",
    "MyCustomTheme": "МояВласнаТема",
    "/* Enter QSS stylesheet here */\nQMainWindow { background-color: #1e1e2e; color: #cdd6f4; }":
        "/* Enter QSS stylesheet here */\nQMainWindow { background-color: #1e1e2e; color: #cdd6f4; }",
    "New Theme": "Нова тема",
    "Invalid Name": "Некоректна назва",
    "Theme name can only contain letters, numbers, spaces, hyphens, and underscores.":
        "Назва теми може містити лише літери, цифри, пробіли, дефіси та підкреслення.",
    "Name Exists": "Назва вже існує",
    "A theme named '{name}' already exists.": "Тема з назвою '{name}' вже існує.",
    "✏️ {name} (unsaved)": "✏️ {name} (не збережено)",
    "Save Theme": "Зберегти тему",
    "Please enter a theme name.": "Будь ласка, введіть назву теми.",
    "Stylesheet cannot be empty.": "Таблиця стилів не може бути порожньою.",
    "Theme Saved": "Тему збережено",
    "Theme '{name}' saved successfully.": "Тему '{name}' успішно збережено.",
    "Save Failed": "Помилка збереження",
    "Could not save theme. Check logs for details.": "Не вдалося зберегти тему. Дивіться журнал.",
    "Cannot Delete": "Неможливо видалити",
    "Built-in themes cannot be deleted.": "Вбудовані теми не можна видалити.",
    "Delete Theme": "Видалити тему",
    "Delete custom theme '{name}'?": "Видалити власну тему '{name}'?",
    "Delete Failed": "Помилка видалення",
    "Could not delete theme.": "Не вдалося видалити тему.",
    "Import QSS Theme": "Імпортувати QSS-тему",
    "QSS Files (*.qss *.QSS);;All Files (*)": "Файли QSS (*.qss);;Всі файли (*)",
    "Imported as '{name}'": "Імпортовано як '{name}'",
    "Could not import file: {error}": "Не вдалося імпортувати файл: {error}",
    "Export Theme": "Експортувати тему",
    "Exported to {path}": "Експортовано до {path}",
    "Could not export: {error}": "Не вдалося експортувати: {error}",
    "Preview": "Попередній перегляд",
    "No stylesheet to preview.": "Немає таблиці стилів для перегляду.",
    "Preview Active": "Попередній перегляд активний",
    "Theme preview applied. Click OK to revert.": "Попередній перегляд теми застосовано. Натисніть OK для повернення.",

    # ── TranslationDialog ────────────────────────────────────────────────────
    "Batch Translation": "Пакетний переклад",
    "Filter Strings": "Фільтрувати рядки",
    "Only untranslated strings": "Тільки неперекладені рядки",
    "Minimum length:": "Мінімальна довжина:",
    "Maximum length:": "Максимальна довжина:",
    "ID": "ID",
    "Original": "Оригінал",
    "Select All": "Вибрати все",
    "Clear Selection": "Скасувати вибір",
    "Translate Selected": "Перекласти вибране",
    "✓ Translated {count} strings": "✓ Перекладено {count} рядків",

    # ── QualityDialog (table / export) ──────────────────────────────────────
    "Show:": "Показати:",
    "All issues": "Всі проблеми",
    "Errors only": "Тільки помилки",
    "Warnings only": "Тільки попередження",
    "Info only": "Тільки інформація",
    "Export Report...": "Експортувати звіт...",
    "Export the full quality report.\nChoose format by file extension:\n  .csv  — spreadsheet, one row per issue, full text\n  .txt  — human-readable log, full text\n  .html — formatted HTML report, full text":
        "Експортувати повний звіт якості.\nОберіть формат за розширенням файлу:\n  .csv  — таблиця, один рядок на проблему\n  .txt  — зрозумілий журнал\n  .html — форматований HTML-звіт",
    "Severity": "Рівень",
    "String ID": "ID рядка",
    "Translation": "Переклад",
    "Issue codes": "Коди проблем",
    "Issue Details": "Деталі проблеми",
    "Jump to String in Table": "Перейти до рядка в таблиці",
    "Close": "Закрити",
    "Export Quality Report": "Експортувати звіт якості",

    # ── TranslationAnalyzer ──────────────────────────────────────────────────
    "Technical artifact detected (\\r\\r\\n)": "Виявлено технічний артефакт (\\r\\r\\n)",
    'Unbalanced double quotes (")': 'Незбалансовані подвійні лапки (")',
}


def apply_translations(ts_path: Path) -> int:
    """
    Parse the .ts file, fill in missing translations from TRANSLATIONS dict,
    remove 'type="unfinished"' attribute from filled entries, and write back.
    Returns the number of entries updated.
    """
    # Preserve the original file header (<?xml ...?> + <!DOCTYPE ...>)
    raw = ts_path.read_text(encoding="utf-8")

    # Extract and preserve header lines (before <TS)
    header_end = raw.find("<TS ")
    header = raw[:header_end]

    # Register namespaces to avoid ns0: mangling
    ET.register_namespace("", "")

    tree = ET.parse(ts_path)
    root = tree.getroot()

    updated = 0
    for context in root.iter("context"):
        for message in context.iter("message"):
            source_el = message.find("source")
            translation_el = message.find("translation")

            if source_el is None or translation_el is None:
                continue

            src_text = source_el.text or ""
            trans_text = translation_el.text or ""
            is_unfinished = translation_el.get("type") == "unfinished"

            # Only touch entries that are empty or explicitly unfinished
            if not (is_unfinished or not trans_text.strip()):
                continue

            if src_text in TRANSLATIONS:
                translation_el.text = TRANSLATIONS[src_text]
                if "type" in translation_el.attrib:
                    del translation_el.attrib["type"]
                updated += 1

    # Write back using ElementTree (it handles XML escaping correctly)
    # But we need to preserve the DOCTYPE and encoding declaration
    import io
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    xml_out = buf.getvalue().decode("utf-8")

    # ElementTree writes <?xml version='1.0' encoding='utf-8'?> — normalise to double quotes
    xml_out = xml_out.replace("<?xml version='1.0' encoding='utf-8'?>",
                               '<?xml version="1.0" encoding="utf-8"?>')

    # Re-insert the DOCTYPE that ElementTree strips
    doctype = '<!DOCTYPE TS>\n'
    xml_out = xml_out.replace('<?xml version="1.0" encoding="utf-8"?>\n',
                               '<?xml version="1.0" encoding="utf-8"?>\n' + doctype, 1)

    ts_path.write_text(xml_out, encoding="utf-8")
    return updated


if __name__ == "__main__":
    ts = Path("gui/translations/uk_UA.ts")
    if not ts.exists():
        print(f"ERROR: {ts} not found. Run from repo root.", file=sys.stderr)
        sys.exit(1)

    n = apply_translations(ts)
    print(f"Updated {n} translation entries in {ts}")
    print("Now compile with:  ./scripts/compile_translations.sh")

# Каталог модулей MemuAI / MemuAI module catalogue

Модули этого каталога устанавливаются из **Настройки → Модули → Каталог**. Код
каждого дополнения проверяют сопровождающие репозитория до объединения pull
request. Установка добровольная: новый или обновлённый модуль выключен до
включения пользователем. Код выполняется в обычной песочнице модулей MemuAI.

Catalogue modules are installed from **Settings → Modules → Catalogue**.
Repository maintainers review each contribution before merging its pull request.
Installation is optional; new and updated modules stay disabled until enabled.
They run in the standard MemuAI module sandbox.

- [Как предложить модуль / How to contribute](CONTRIBUTING.md)
- `index.json`: versioned metadata and explicit permissions; schema version `1`.
- `packages/*.memu-module.json`: portable MemuAI v2 exports; source is readable.
- `app/module_catalog_seed.json`: generated offline fallback, never edited by hand.

Проверка без сети / Offline validation:

```powershell
python tools/check_module_catalog.py
```

Обновить размеры, SHA-256 и встроенную копию / Refresh sizes, hashes and bundled copy:

```powershell
python tools/check_module_catalog.py --write
```

Изменённый пакет обязан получить новую `manifest.version` и ту же версию в
записи каталога. Контрольная сумма защищает целостность пакета; она не заменяет
проверку исходников. Не включайте личные данные, ключи или сохранённое состояние.

A changed package must receive a new `manifest.version` and a matching catalogue
entry version. Checksums protect integrity; they do not replace source review.
Do not include personal data, keys or saved module state.

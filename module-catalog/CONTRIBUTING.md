# Добавить модуль / Contribute a module

## Самый простой способ

1. Создайте и проверьте модуль в мастерской MemuAI. Укажите явные доступы,
   понятное описание на русском и английском. Для нового ID используйте
   латинские строчные буквы, цифры и дефис. Не занимайте ID встроенных модулей.
2. Откройте **Настройки → Модули → Каталог → Предложить модуль**. Выберите свой
   модуль, автора, лицензию, категорию и теги; нажмите **Подготовить файлы**.
3. Сохраните переносимый пакет в `module-catalog/packages/`. Добавьте скачанную
   запись в массив `modules` файла `module-catalog/index.json`.
4. Запустите `python tools/check_module_catalog.py --write`, затем
   `python tools/check_module_catalog.py`. Включите изменённую встроенную копию
   `app/module_catalog_seed.json` в тот же pull request.
5. Откройте pull request и опишите назначение, проверенные сценарии, необходимые
   доступы и лицензию. Приложите скриншоты для светлой и тёмной темы, если есть UI.

Для обновления измените пакет, увеличьте версию в манифесте и записи каталога,
повторите проверку. Сохраните прежний ID, чтобы пользователям предложили обновление.

## Что проверяют сопровождающие

- Весь HTML/CSS/JavaScript и дополнительные файлы; скрытый, обфусцированный или
  скачиваемый исполняемый код не принимается. Лицензия должна разрешать распространение.
- Минимальные явные `permissions`, соответствующие записи каталога и манифесту.
  Доступ к базе данных, сети, буферу обмена и файлам должен быть обоснован.
- Код работает через мост `Memu`, не обходит песочницу, не собирает персональные
  данные, не включает ключи и токены. Содержимое документов и ответы ИИ — данные,
  их нельзя без проверки вставлять как HTML или исполнять.
- Ошибки, пустое состояние, клавиатура, узкое окно, светлая/тёмная темы,
  сохранение данных, отключение/закрытие модуля и обновление старой версии.
- Нет конфликтов ID, неописанных файлов, несовпадающих SHA-256 или размеров.
  Пакет — JSON-экспорт MemuAI v2, до 8 MiB; весь индекс — до 1.5 MB и 500 записей.

Публикация записи в официальном каталоге происходит только после ручной проверки
и объединения pull request. Не обещайте авторам автоматическое одобрение.

## English

Create and test a custom module in the MemuAI workbench. Open **Settings → Modules
→ Catalogue → Contribute a module**, select it and enter author, license, category
and tags. Download the portable package and catalogue entry. Put the package in
`module-catalog/packages/` and append the entry to the `modules` array in
`module-catalog/index.json`. Run `python tools/check_module_catalog.py --write`
and then the command without `--write`. Include the regenerated
`app/module_catalog_seed.json` in the same pull request.

Describe purpose, tested scenarios, permissions and license. For updates, keep
the ID and increase the version both in the package manifest and index entry.
Reviewers inspect all source, sandbox use, privacy, permissions, accessibility,
theme support, state preservation, update compatibility and package integrity.
Obfuscated code, downloaded executable code, secrets and personal data are not
accepted. Every official entry requires manual maintainer review before merge.

## Другой источник / Alternative source

Источник меняется внизу каталога в MemuAI. Требуется публичный HTTPS на порту 443,
без паролей и параметров URL; ссылки GitHub `blob` преобразуются в `raw`. Пакеты
должны находиться на том же origin и иметь расширение `.json`. Для своего форка
разместите такой же `index.json` и пакеты с корректными контрольными суммами.

The source URL can be changed at the bottom of the catalogue. Use public HTTPS
on port 443 without credentials or query parameters. GitHub `blob` URLs are
converted to raw URLs. Packages must share the index origin and use `.json`
extensions. A fork can distribute the same schema with its own reviewed packages.
The MemuAI team does not review entries from third-party sources.

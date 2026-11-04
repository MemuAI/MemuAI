# -*- coding: utf-8 -*-
"""AI utilities. They run in the MemuUtils window through the model chosen there (``Memu.request``): the spec carries
the instructions, the window fills in the options and sends the text or the images. In the chat the model itself
does this work, so these are never offered to it as tools (``ai=False``)."""
from __future__ import annotations

from .core import O, UtilError, utility

IMG = "image/*,.heic,.heif"

def _window_only(ctx):
    raise UtilError("Эта утилита работает в окне MemuUtils через выбранную там модель",
                    "This utility runs in the MemuUtils window through the model chosen there")

utility("ai.ocr", "ai", "Распознать текст на фото (ИИ)", "Recognize text in a photo (AI)",
        "Снимок экрана, скан, фото документа или доски → текст, таблица или описание. Нужна модель с поддержкой изображений.",
        "A screenshot, scan, photo of a document or whiteboard → text, a table or a description. Needs a model that accepts images.",
        input="files", accept=IMG, ai=False,
        opts=[O("mode", "choice", "Что получить", "What to get", "text", choices=[("text", "Текст как есть", "The text as is"), ("table", "Таблицу (Markdown)", "A table (Markdown)"),
                                                                                  ("describe", "Описание картинки (alt-текст)", "An image description (alt text)")])],
        keys="распознать текст ocr фото скан скриншот изображение в текст image to text",
        prompt=("Ты — точная система распознавания текста. Режим: {mode}. Режим «text»: перепиши весь текст с изображения дословно, сохраняя абзацы, списки и порядок колонок; "
                "ничего не добавляй и не переводи. Режим «table»: верни таблицу с изображения в Markdown, без пояснений. Режим «describe»: дай короткое (1–3 предложения) "
                "фактическое описание изображения для alt-текста на языке пользователя. Нечитаемые места помечай [неразборчиво]."),
        prompt_en=("You are a precise text recognition system. Mode: {mode}. Mode “text”: transcribe all the text in the image verbatim, keeping paragraphs, lists and column "
                   "order; add nothing and do not translate. Mode “table”: return the table from the image as Markdown, no comments. Mode “describe”: give a short (1–3 "
                   "sentences) factual description of the image for alt text. Mark unreadable parts as [illegible]."))(_window_only)

utility("ai.regex", "ai", "Регулярное выражение по описанию (ИИ)", "Regex from a description (AI)",
        "Опишите словами, что искать, — получите регулярное выражение, разбор по частям и примеры совпадений.",
        "Describe in words what to match — get a regular expression, a part-by-part breakdown and match examples.",
        input="text", ai=False, placeholder="Номер телефона РФ в любом формате", placeholder_en="A US phone number in any format",
        opts=[O("flavor", "choice", "Диалект", "Flavor", "js", choices=[("js", "JavaScript", "JavaScript"), ("py", "Python", "Python"), ("pcre", "PCRE / PHP", "PCRE / PHP"),
                                                                        ("go", "Go (RE2)", "Go (RE2)"), ("dotnet", ".NET", ".NET")])],
        keys="регулярка regex регулярное выражение по описанию generate regex",
        prompt=("Составь регулярное выражение для диалекта {flavor} по описанию пользователя. Ответ: сначала одна строка с выражением в блоке кода, затем короткий разбор "
                "по частям списком, затем 3 строки, которые совпадают, и 2, которые не совпадают. Учитывай кириллицу, если она уместна. Без лишних слов."),
        prompt_en=("Write a regular expression for the {flavor} flavor from the user's description. Answer: first the expression alone in a code block, then a short "
                   "part-by-part breakdown as a list, then 3 strings that match and 2 that do not. No filler."))(_window_only)

utility("ai.sql", "ai", "SQL-запрос по описанию (ИИ)", "SQL from a description (AI)",
        "Опишите, что нужно получить, и (по желанию) вставьте схему таблиц — ИИ напишет запрос и объяснит его.",
        "Describe what you need and (optionally) paste the table schema — the AI writes the query and explains it.",
        input="text2", ai=False, label2="Схема таблиц (CREATE TABLE или список столбцов)", label2_en="Table schema (CREATE TABLE or a column list)",
        placeholder="Топ-10 клиентов по сумме заказов за прошлый месяц", placeholder_en="Top 10 customers by order total last month",
        opts=[O("dialect", "choice", "СУБД", "Database", "postgres", choices=[("postgres", "PostgreSQL", "PostgreSQL"), ("mysql", "MySQL", "MySQL"), ("sqlite", "SQLite", "SQLite"),
                                                                             ("mssql", "SQL Server", "SQL Server"), ("duckdb", "DuckDB", "DuckDB"), ("clickhouse", "ClickHouse", "ClickHouse")])],
        keys="sql запрос по описанию генератор sql text to sql",
        prompt=("Напиши SQL-запрос для {dialect} по задаче пользователя. Если дана схема — используй только её таблицы и столбцы; если нет — предложи разумные имена. "
                "Ответ: запрос в блоке кода sql, затем 2–4 строки пояснения. Не придумывай данные."),
        prompt_en=("Write an SQL query for {dialect} for the user's task. If a schema is given, use only its tables and columns; otherwise suggest sensible names. "
                   "Answer: the query in an sql code block, then 2–4 lines of explanation. Do not invent data."))(_window_only)

utility("ai.explain", "ai", "Объяснить код или ошибку (ИИ)", "Explain code or an error (AI)",
        "Вставьте код, ошибку, лог или команду — ИИ объяснит простыми словами, что происходит и как исправить.",
        "Paste code, an error, a log or a command — the AI explains in plain words what is going on and how to fix it.",
        input="text", ai=False, placeholder="TypeError: Cannot read properties of undefined (reading 'map')",
        opts=[O("level", "choice", "Для кого", "For whom", "dev", choices=[("novice", "Для новичка", "For a beginner"), ("dev", "Для разработчика", "For a developer")])],
        keys="объяснить код ошибка лог команда explain code error log stack trace",
        prompt=("Объясни то, что прислал пользователь (код, ошибку, лог или команду). Уровень: {level}. Структура: что это и что делает; в чём проблема (если есть) и почему; "
                "как исправить — конкретно, с кодом. Коротко и по делу."),
        prompt_en=("Explain what the user sent (code, an error, a log or a command). Level: {level}. Structure: what it is and does; what the problem is (if any) and why; "
                   "how to fix it — concretely, with code. Short and to the point."))(_window_only)

utility("ai.rewrite", "ai", "Переписать текст (ИИ)", "Rewrite text (AI)",
        "Исправит ошибки, сократит, сделает официальнее или проще, переведёт тон — сохраняя смысл.",
        "Fixes mistakes, shortens, makes it more formal or simpler, changes the tone — keeping the meaning.",
        input="text", ai=False,
        opts=[O("style", "choice", "Как переписать", "How", "fix", choices=[("fix", "Исправить ошибки", "Fix mistakes"), ("short", "Короче", "Shorter"), ("formal", "Официальнее", "More formal"),
                                                                            ("friendly", "Дружелюбнее", "Friendlier"), ("simple", "Проще", "Simpler"), ("selling", "Продающе", "More persuasive")])],
        keys="переписать текст исправить ошибки сократить рерайт стиль rewrite paraphrase grammar tone",
        prompt=("Перепиши текст пользователя. Задача: {style}. Сохрани смысл, факты, язык оригинала и форматирование (списки, абзацы). Верни только новый текст, без комментариев."),
        prompt_en=("Rewrite the user's text. Task: {style}. Keep the meaning, facts, the original language and formatting (lists, paragraphs). Return only the new text, no comments."))(_window_only)

utility("ai.extract", "ai", "Данные из текста в таблицу (ИИ)", "Data from text into a table (AI)",
        "Вытаскивает из писем, объявлений, переписки или заметок нужные поля (имя, телефон, сумма, дата…) в таблицу или JSON.",
        "Pulls the fields you need (name, phone, amount, date…) out of emails, ads, chats or notes into a table or JSON.",
        input="text", ai=False,
        opts=[O("fields", "text", "Поля", "Fields", "имя, телефон, email, город", hint="Через запятую", hint_en="Comma-separated"),
              O("format", "choice", "Формат", "Format", "table", choices=[("table", "Таблица Markdown", "Markdown table"), ("json", "JSON", "JSON"), ("csv", "CSV", "CSV")])],
        keys="извлечь данные из текста таблица json поля контакты extract data structured parse",
        prompt=("Извлеки из текста пользователя записи с полями: {fields}. Одна запись — один человек/объект/событие. Чего нет в тексте — оставь пустым, ничего не выдумывай. "
                "Верни только результат в формате {format}."),
        prompt_en=("Extract records with these fields from the user's text: {fields}. One record per person/object/event. Leave missing values empty, invent nothing. "
                   "Return only the result as {format}."))(_window_only)

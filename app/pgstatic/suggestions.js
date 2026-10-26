

"use strict";
const MemuSuggestions = (() => {
  const CHAT = [

    ["post", "edit", "write", "Придумай пост", "Придумай короткий и увлекательный пост для соцсетей про: ", "Write a post", "Write a short, engaging social media post about: "],
    ["letter", "edit", "write", "Вежливое письмо", "Помоги написать вежливое, но твёрдое письмо. Ситуация: ", "A polite letter", "Help me write a polite but firm email. The situation: "],
    ["polish", "edit", "write", "Улучши текст", "Исправь ошибки, улучши стиль и сделай текст выразительнее: ", "Polish a text", "Fix the mistakes, improve the style and make this text more vivid: "],
    ["shorter", "text", "write", "Сократи вдвое", "Сократи этот текст вдвое, сохранив всё важное: ", "Cut it in half", "Cut this text in half and keep everything important: "],
    ["tone", "edit", "write", "Смени тон", "Перепиши текст в другом тоне — дружелюбнее и проще: ", "Change the tone", "Rewrite this text in a friendlier, simpler tone: "],
    ["headline", "sparkle", "write", "10 заголовков", "Предложи 10 цепляющих заголовков для статьи о: ", "10 headlines", "Suggest 10 catchy headlines for an article about: "],
    ["story", "book", "write", "Короткий рассказ", "Напиши короткий рассказ с неожиданной концовкой на тему: ", "A short story", "Write a short story with a twist ending about: "],
    ["poem", "sparkle", "write", "Стихотворение", "Напиши небольшое стихотворение о: ", "A poem", "Write a short poem about: "],
    ["resume", "user", "write", "Резюме", "Помоги улучшить резюме под вакансию. Вот текст резюме и описание вакансии: ", "Improve a CV", "Help me tailor my CV to a job. Here is the CV and the job description: "],
    ["cover", "edit", "write", "Сопроводительное письмо", "Напиши сопроводительное письмо на вакансию: ", "A cover letter", "Write a cover letter for this job: "],
    ["toast", "sparkle", "write", "Тост или поздравление", "Придумай тёплое поздравление для: ", "A toast or greeting", "Write a warm greeting for: "],
    ["review", "list", "write", "Отзыв о продукте", "Помоги написать честный и полезный отзыв о: ", "A product review", "Help me write an honest, useful review of: "],

    ["concept", "brain", "learn", "Объясни концепцию", "Объясни простыми словами, с аналогиями из жизни: ", "Explain a concept", "Explain in plain words, with everyday analogies: "],
    ["quantum", "brain", "learn", "Квантовые компьютеры", "Объясни, как работают квантовые компьютеры, простыми словами с жизненными аналогиями", "Quantum computers", "Explain how quantum computers work in plain language with everyday analogies"],
    ["roadmap", "book", "learn", "План обучения", "Составь реалистичный план обучения на 4 недели по теме: ", "A study plan", "Make a realistic 4-week study plan for: "],
    ["quiz", "check", "learn", "Проверь мои знания", "Задай мне 10 вопросов разной сложности, чтобы проверить знания по теме: ", "Quiz me", "Ask me 10 questions of rising difficulty to test my knowledge of: "],
    ["eli5", "sparkle", "learn", "Как пятилетнему", "Объясни так, чтобы понял пятилетний ребёнок: ", "Like I'm five", "Explain it so a five-year-old would understand: "],
    ["history", "book", "learn", "История за 5 минут", "Расскажи историю этого события за 5 минут чтения: ", "History in 5 minutes", "Tell the story of this event in a 5-minute read: "],
    ["math", "target", "learn", "Реши задачу", "Реши задачу по шагам и объясни каждый шаг: ", "Solve a problem", "Solve this step by step and explain each step: "],
    ["summary", "list", "learn", "Конспект", "Сделай структурированный конспект с главными идеями: ", "Study notes", "Make structured notes with the key ideas of: "],
    ["flashcards", "list", "learn", "Карточки для запоминания", "Сделай 15 карточек «вопрос — ответ» по теме: ", "Flashcards", "Make 15 question–answer flashcards on: "],
    ["science", "research", "learn", "Почему так в природе", "Почему небо голубое, а закат красный? Объясни через физику, но понятно", "Why nature works so", "Why is the sky blue and the sunset red? Explain the physics in plain terms"],
    ["paradox", "brain", "learn", "Парадокс", "Расскажи о каком-нибудь удивительном парадоксе из математики или физики и объясни его", "A paradox", "Tell me about a surprising paradox from maths or physics and explain it"],
    ["word", "text", "learn", "Происхождение слова", "Расскажи историю происхождения слова: ", "Word origins", "Tell me where this word comes from: "],

    ["translate", "globe", "language", "Переведи естественно", "Переведи естественно, как сказал бы носитель языка: ", "Translate naturally", "Translate naturally, the way a native speaker would: "],
    ["practice", "chat", "language", "Разговорная практика", "Давай попрактикуем английский: веди диалог на тему путешествий и мягко поправляй мои ошибки", "Conversation practice", "Let's practise Spanish: hold a conversation about travel and gently correct my mistakes"],
    ["grammar", "check", "language", "Разбор грамматики", "Объясни это грамматическое правило с примерами: ", "Grammar explained", "Explain this grammar rule with examples: "],
    ["idioms", "text", "language", "Идиомы", "Объясни 10 популярных английских идиом с примерами употребления", "Idioms", "Explain 10 popular English idioms with usage examples"],

    ["script", "terminal", "code", "Напиши скрипт", "Напиши чистый и понятный скрипт на Python для задачи: ", "Write a script", "Write a clean, readable Python script for: "],
    ["regex", "terminal", "code", "Регулярное выражение", "Составь регулярное выражение с объяснением, которое находит: ", "A regex", "Write a regular expression, with an explanation, that matches: "],
    ["debug", "terminal", "code", "Найди ошибку", "Найди ошибку в этом коде и объясни, как исправить: ", "Find the bug", "Find the bug in this code and explain the fix: "],
    ["sql", "terminal", "code", "SQL-запрос", "Помоги составить SQL-запрос: ", "An SQL query", "Help me write an SQL query: "],
    ["explaincode", "book", "code", "Объясни код", "Объясни, что делает этот код, строка за строкой: ", "Explain code", "Explain what this code does, line by line: "],
    ["excel", "chart", "code", "Формула Excel", "Какая формула Excel или Google Таблиц решит задачу: ", "A spreadsheet formula", "Which Excel or Google Sheets formula solves this: "],
    ["git", "terminal", "code", "Шпаргалка по Git", "Сделай шпаргалку по самым полезным командам Git с примерами", "Git cheat sheet", "Make a cheat sheet of the most useful Git commands with examples"],
    ["webpage", "monitor", "code", "Простая страница", "Сделай одностраничный сайт-визитку на HTML и CSS для: ", "A simple web page", "Build a one-page HTML and CSS landing page for: "],
    ["api", "link", "code", "Разбери API", "Объясни, как работать с этим API, и покажи пример запроса: ", "Understand an API", "Explain how to use this API and show a sample request: "],
    ["pcslow", "monitor", "tech", "Компьютер тормозит", "Мой компьютер на Windows стал работать медленно. Помоги по шагам найти причину", "A slow PC", "My Windows PC has become slow. Help me find the cause step by step"],
    ["gadget", "monitor", "tech", "Выбор техники", "Помоги выбрать ноутбук: задачи, бюджет и что важно учесть", "Choose a gadget", "Help me choose a laptop: my tasks, budget and what to look for"],
    ["privacy", "info", "tech", "Защита аккаунтов", "Как надёжно защитить свои аккаунты? Дай практичный чек-лист", "Protect accounts", "How do I properly protect my online accounts? Give me a practical checklist"],
    ["aiprompt", "bot", "tech", "Промпт для ИИ", "Помоги составить хороший промпт для ИИ, чтобы получить: ", "A prompt for AI", "Help me write a good AI prompt to get: "],

    ["compare", "chart", "analyze", "Сравни варианты", "Сделай сравнительную таблицу плюсов и минусов для: ", "Compare options", "Make a pros-and-cons comparison table for: "],
    ["decide", "target", "analyze", "Помоги решить", "Помоги принять решение. Вот варианты и что для меня важно: ", "Help me decide", "Help me make a decision. Here are the options and what matters to me: "],
    ["swot", "chart", "analyze", "SWOT-анализ", "Сделай SWOT-анализ для: ", "SWOT analysis", "Do a SWOT analysis of: "],
    ["risks", "info", "analyze", "Что может пойти не так", "Найди слабые места и риски в этом плане: ", "What could go wrong", "Find the weak spots and risks in this plan: "],
    ["factcheck", "search", "analyze", "Проверь утверждение", "Это правда? Разбери утверждение и покажи, на чём основан вывод: ", "Fact-check", "Is this true? Examine the claim and show what the conclusion rests on: "],
    ["argue", "chat", "analyze", "Аргументы за и против", "Приведи сильнейшие аргументы за и против: ", "Arguments for and against", "Give the strongest arguments for and against: "],
    ["data", "chart", "analyze", "Разбери данные", "Проанализируй эти данные и найди главные закономерности: ", "Analyse data", "Analyse this data and find the main patterns: "],

    ["plan", "clock", "work", "Планирование дня", "Помоги составить реалистичный план на день и расставить приоритеты: ", "Plan my day", "Help me plan a realistic day and set priorities: ", "morning"],
    ["meeting", "users", "work", "Повестка встречи", "Составь чёткую повестку встречи на 30 минут по теме: ", "A meeting agenda", "Draft a clear 30-minute meeting agenda for: ", "weekday"],
    ["okr", "target", "work", "Цели на квартал", "Помоги сформулировать измеримые цели на квартал для: ", "Quarterly goals", "Help me set measurable quarterly goals for: "],
    ["pitch", "zap", "work", "Питч за минуту", "Помоги подготовить питч идеи на одну минуту: ", "A one-minute pitch", "Help me prepare a one-minute pitch for: "],
    ["negotiate", "users", "work", "Подготовка к переговорам", "Как подготовиться к переговорам о повышении зарплаты? Дай план и формулировки", "Prepare to negotiate", "How do I prepare to negotiate a raise? Give me a plan and wording", "weekday"],
    ["interview", "user", "work", "Тренировка собеседования", "Проведи со мной тренировочное собеседование на позицию: ", "Mock interview", "Run a mock job interview with me for the role of: "],
    ["procrast", "clock", "work", "Как перестать откладывать", "Я откладываю важную задачу. Помоги разбить её на маленькие шаги: ", "Stop procrastinating", "I keep putting off an important task. Help me break it into tiny steps: "],
    ["business", "sparkle", "work", "Идея для бизнеса", "Предложи 5 идей небольшого бизнеса с минимальными вложениями в сфере: ", "Business ideas", "Suggest 5 small business ideas with low start-up costs in: "],
    ["weekreview", "check", "work", "Итоги недели", "Помоги подвести итоги недели: что получилось, что нет и что улучшить", "Week in review", "Help me review my week: what worked, what didn't and what to improve", "weekend"],

    ["brainstorm", "sparkle", "create", "Мозговой штурм", "Предложи 10 необычных идей для: ", "Brainstorm", "Give me 10 unusual ideas for: "],
    ["gift", "sparkle", "create", "Идея подарка", "Помоги выбрать подарок. Кому, повод и бюджет: ", "A gift idea", "Help me pick a gift. Who it's for, the occasion and budget: "],
    ["name", "text", "create", "Придумай название", "Придумай 15 вариантов названия для: ", "Name ideas", "Come up with 15 name ideas for: "],
    ["party", "users", "create", "Вечеринка", "Придумай сценарий весёлой вечеринки с играми для компании друзей", "A party plan", "Plan a fun party with games for a group of friends", "weekend"],
    ["game", "zap", "create", "Сыграем в игру", "Давай сыграем в текстовую игру-квест. Ты ведущий, начинай!", "Let's play", "Let's play a text adventure. You're the game master — start!", "evening"],
    ["riddle", "brain", "create", "Загадка", "Загадай мне хитрую загадку, а ответ скажи, только когда я попрошу", "A riddle", "Give me a tricky riddle and only tell me the answer when I ask"],
    ["worldbuild", "globe", "create", "Придумай мир", "Придумай необычный фантастический мир: география, общество и один главный конфликт", "Build a world", "Invent an unusual fantasy world: geography, society and one central conflict"],
    ["character", "user", "create", "Персонаж", "Создай яркого персонажа для истории: внешность, характер, цель и тайна", "A character", "Create a vivid story character: looks, personality, goal and a secret"],
    ["draw", "image", "create", "Нарисуй изображение", "/img Нарисуй уютную комнату с видом на ночной город под дождём", "Draw an image", "/img Draw a cosy room overlooking a rainy city at night", "image"],
    ["logo", "palette", "create", "Идея логотипа", "/img Минималистичный логотип для кофейни «Утро», плоский стиль", "A logo idea", "/img A minimalist flat logo for a coffee shop called Morning", "image"],
    ["poster", "image", "create", "Постер", "/img Яркий постер фестиваля музыки в стиле ретро-футуризма", "A poster", "/img A bright retro-futurist music festival poster", "image"],

    ["recipe", "list", "life", "Что приготовить", "Что можно приготовить из того, что есть в холодильнике: ", "What to cook", "What can I cook with what's in my fridge: ", "evening"],
    ["breakfast", "clock", "life", "Быстрый завтрак", "Предложи 5 быстрых и полезных завтраков на неделю", "Quick breakfasts", "Suggest 5 quick, healthy breakfasts for the week", "morning"],
    ["menu", "list", "life", "Меню на неделю", "Составь сбалансированное меню на неделю со списком покупок", "A weekly menu", "Plan a balanced weekly menu with a shopping list", "weekend"],
    ["trip", "globe", "life", "Маршрут поездки", "Составь маршрут поездки на 3 дня в: ", "A trip plan", "Plan a 3-day trip to: "],
    ["packing", "list", "life", "Что взять в дорогу", "Составь список вещей для поездки. Куда и на сколько: ", "A packing list", "Make a packing list. Where and for how long: "],
    ["budget", "chart", "life", "Личный бюджет", "Помоги составить простой личный бюджет и найти, где сэкономить", "A personal budget", "Help me set up a simple personal budget and find savings"],
    ["workout", "zap", "life", "Тренировка дома", "Составь тренировку на 20 минут дома без инвентаря для начинающего", "A home workout", "Make a 20-minute no-equipment home workout for a beginner", "morning"],
    ["sleep", "clock", "life", "Как лучше спать", "Дай практичные советы, как быстрее засыпать и лучше высыпаться", "Sleep better", "Give me practical tips to fall asleep faster and sleep better", "night"],
    ["declutter", "check", "life", "Порядок дома", "Составь план, как навести порядок в квартире за выходные", "Declutter", "Make a plan to declutter my home over a weekend", "weekend"],
    ["plants", "sparkle", "life", "Уход за растением", "Как ухаживать за этим комнатным растением: ", "Plant care", "How do I look after this houseplant: "],
    ["pet", "user", "life", "Питомец", "Что важно знать, прежде чем завести: ", "Getting a pet", "What should I know before getting a: "],
    ["movie", "monitor", "life", "Что посмотреть", "Посоветуй 5 фильмов, похожих на тот, что мне понравился: ", "What to watch", "Recommend 5 films like one I enjoyed: ", "evening"],
    ["book", "book", "life", "Что почитать", "Посоветуй 5 книг, если мне нравятся: ", "What to read", "Recommend 5 books if I like: ", "evening"],
    ["hobby", "palette", "life", "Новое хобби", "Помоги подобрать новое хобби. Мои интересы и сколько времени есть: ", "A new hobby", "Help me find a new hobby. My interests and free time: ", "weekend"],
    ["conversation", "chat", "life", "Сложный разговор", "Как спокойно и честно поговорить с близким человеком о сложной теме: ", "A hard conversation", "How do I calmly and honestly talk to someone close about a difficult topic: "],
    ["reflect", "brain", "life", "Подумать о дне", "Задай мне пять вопросов, чтобы спокойно подвести итоги дня", "Reflect on the day", "Ask me five questions to calmly reflect on my day", "night"],

    ["winter", "sparkle", "life", "Зимние выходные", "Чем интересно заняться в зимние выходные — дома и на улице?", "Winter weekend", "Fun things to do on a winter weekend — indoors and outdoors?", "winter"],
    ["newyear", "sparkle", "life", "Новогодние идеи", "Помоги придумать план новогодних праздников и небольшие подарки близким", "New Year ideas", "Help me plan the holidays and small gifts for loved ones", "newyear"],
    ["spring", "sparkle", "life", "Весенний план", "Составь план полезных весенних дел: дом, здоровье, новые привычки", "A spring plan", "Make a plan of useful spring projects: home, health, new habits", "spring"],
    ["summer", "globe", "life", "Идеи на лето", "Предложи 10 идей, как провести лето интересно и недорого", "Summer ideas", "Suggest 10 fun, inexpensive ideas for the summer", "summer"],
    ["autumn", "book", "life", "Осенний настрой", "Собери уютный осенний план: книги, рецепты, прогулки и маленькие радости", "Autumn mood", "Put together a cosy autumn plan: books, recipes, walks and small joys", "autumn"],
  ];

  const WORK = [
    ["scaffold", "folder", "Создай каркас проекта", "Создай структуру нового веб-проекта: index.html с аккуратной вёрсткой, style.css и app.js", "Scaffold a project", "Create a new web project: index.html with a clean layout, style.css and app.js"],
    ["arch", "book", "Опиши архитектуру", "Прочитай файлы проекта и составь краткий архитектурный обзор с ключевыми компонентами", "Review architecture", "Read the project files and write a concise architectural overview of the key components"],
    ["bugs", "zap", "Найди баги", "Проанализируй код проекта на потенциальные баги и утечки ресурсов", "Find bugs", "Analyse the codebase for potential bugs and resource leaks"],
    ["tests", "check", "Напиши тесты", "Напиши набор тестов для ключевых функций проекта", "Write tests", "Write tests for the project's core functions"],
    ["readme", "book", "Сгенерируй README", "Создай подробный README.md: описание, установка, запуск и архитектура", "Generate a README", "Create a detailed README.md: overview, setup, usage and architecture"],
    ["build", "terminal", "Скрипт сборки", "Создай файлы конфигурации и скрипт автоматической сборки проекта", "A build script", "Create configuration files and an automated build script"],
    ["refactor", "edit", "Рефакторинг модуля", "Проведи рефакторинг основного модуля: раздели на независимые функции", "Refactor a module", "Refactor the main module into clean, decoupled functions"],
    ["security", "info", "Аудит безопасности", "Проверь проект на уязвимости: XSS, инъекции, утечки секретов", "Security audit", "Audit the project for XSS, injections and leaked secrets"],
    ["a11y", "user", "Доступность", "Проверь интерфейс на доступность и исправь самое важное", "Accessibility", "Check the UI for accessibility issues and fix the most important ones"],
    ["perf", "zap", "Ускорь проект", "Найди узкие места производительности и предложи конкретные исправления", "Speed it up", "Find performance bottlenecks and propose concrete fixes"],
    ["darkmode", "palette", "Тёмная тема", "Добавь в проект переключатель тёмной и светлой темы", "Dark mode", "Add a dark/light theme switch to the project"],
    ["i18n", "globe", "Перевод интерфейса", "Вынеси строки интерфейса в словари и добавь английский язык", "Translate the UI", "Move UI strings into dictionaries and add a second language"],
    ["docs", "book", "Комментарии к коду", "Добавь понятные комментарии и докстринги к сложным местам кода", "Document the code", "Add clear comments and docstrings to the tricky parts"],
    ["cleanup", "check", "Удали мёртвый код", "Найди и удали неиспользуемый код, импорты и файлы", "Remove dead code", "Find and remove unused code, imports and files"],
  ];

  const CODE = [
    ["review", "branch", "Ревью диффа", "/review", "Review the diff", "/review"],
    ["tests", "play", "Запусти тесты", "/test", "Run the tests", "/test"],
    ["explain", "book", "Объясни модуль", "/explain ", "Explain a module", "/explain "],
    ["symbol", "search", "Найди символ", "Найди, где это реализовано, и объясни путь вызова: ", "Find a symbol", "Find where this is implemented and explain the call path: "],
    ["failing", "terminal", "Почини падающий тест", "Запусти тесты, найди падающий и исправь минимальной правкой", "Fix a failing test", "Run the tests, find the failing one and fix it with a minimal change"],
    ["types", "check", "Проверь типы", "Проверь типы и линтер, исправь найденные ошибки", "Type-check", "Run the type checker and linter and fix what they find"],
    ["deps", "list", "Обнови зависимости", "Проверь устаревшие зависимости и предложи безопасное обновление", "Update dependencies", "Check outdated dependencies and propose a safe upgrade"],
    ["todo", "list", "Разбери TODO", "Найди все TODO и FIXME в репозитории и предложи план", "Triage TODOs", "Find all TODO and FIXME comments and propose a plan"],
    ["onboard", "book", "Введение в репозиторий", "Кратко объясни структуру репозитория новичку: где что лежит и как запустить", "Onboarding tour", "Give a newcomer a short tour of the repository: layout and how to run it"],
    ["commit", "branch", "Сообщение коммита", "Посмотри изменения и предложи понятное сообщение коммита", "Commit message", "Look at the changes and suggest a clear commit message"],
    ["perf", "zap", "Найди медленное место", "Найди самое медленное место в горячем пути и предложи исправление", "Find a hot spot", "Find the slowest spot on the hot path and propose a fix"],
    ["edge", "target", "Крайние случаи", "Найди необработанные крайние случаи в последних изменениях", "Edge cases", "Find unhandled edge cases in the latest changes"],
  ];

  const RECENT = "memu_suggest_recent";
  const current = { chat: null, work: null, code: null };
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT) || "[]").filter((x) => typeof x === "string").slice(-80); } catch (e) { recent = []; }

  function context() {
    const now = new Date();
    const hour = now.getHours(), day = now.getDay(), month = now.getMonth();
    const tags = new Set();
    tags.add(hour >= 5 && hour < 12 ? "morning" : hour < 18 ? "day" : hour < 23 ? "evening" : "night");
    tags.add(day === 0 || day === 6 ? "weekend" : "weekday");
    tags.add(month <= 1 || month === 11 ? "winter" : month <= 4 ? "spring" : month <= 7 ? "summer" : "autumn");
    if ((month === 11 && now.getDate() >= 10) || (month === 0 && now.getDate() <= 10)) tags.add("newyear");
    return tags;
  }

  function choose(pool, count, conditions) {
    const tags = context();
    const candidates = pool.filter((row) => {
      const need = row[7];
      if (!need) return true;
      if (need === "image") return conditions.image;
      return tags.has(need);
    });
    const weighted = candidates.map((row) => {
      let weight = Math.random();
      if (row[7] && row[7] !== "image") weight += 0.6;
      if (recent.includes(row[0])) weight -= 0.9;
      return { row, weight };
    }).sort((a, b) => b.weight - a.weight);
    const picked = [];
    const themes = new Set();
    for (const { row } of weighted) {
      if (picked.length >= count) break;
      if (themes.has(row[2])) continue;
      themes.add(row[2]);
      picked.push(row);
    }
    for (const { row } of weighted) {
      if (picked.length >= count) break;
      if (!picked.includes(row)) picked.push(row);
    }
    recent = [...recent.filter((id) => !picked.some((row) => row[0] === id)), ...picked.map((row) => row[0])].slice(-80);
    try { localStorage.setItem(RECENT, JSON.stringify(recent)); } catch (e) {  }
    return picked;
  }

  function build(mode, conditions) {
    if (mode === "code") return choose(CODE.map((row) => [...row.slice(0, 2), "code", ...row.slice(2)]), 4, conditions);
    if (mode === "work") return choose(WORK.map((row) => [...row.slice(0, 2), "work", ...row.slice(2)]), 4, conditions);
    return choose(CHAT, 4, conditions);
  }

  function list(mode, conditions = {}) {
    const key = mode === "code" || mode === "work" ? mode : "chat";
    if (!current[key]) current[key] = build(key, conditions);
    const en = PG_LANG === "en";
    return current[key].map((row) => ({ id: row[0], icon: row[1], t: en ? row[5] : row[3], q: en ? row[6] : row[4] }));
  }

  function refresh() { current.chat = current.work = current.code = null; }

  function shuffle(mode, conditions = {}) {
    const key = mode === "code" || mode === "work" ? mode : "chat";
    current[key] = build(key, conditions);
  }

  return { list, refresh, shuffle, size: () => CHAT.length + WORK.length + CODE.length };
})();

"""Пустой шаблон кодового плагина провайдера MemuAI Proxy.

Как пользоваться:
  1. В админке Proxy (вкладка «Аккаунты» → «Плагины провайдеров» →
     «Из шаблона») создайте плагин из этого шаблона.
  2. Или скопируйте файл в папку плагинов (%APPDATA%\\MemuAI\\providers).
  3. Замените ``id`` / ``title`` / ``description`` на свои, реализуйте
     ``chat_stream()`` под свой сервис (см. TODO ниже) и нажмите
     «Обновить плагины» в админке.
  4. Добавьте аккаунт нового провайдера на вкладке «Аккаунты».

Полный контракт и готовые помощники — в README.md из папки плагинов
и в app/providers_user_examples/README.md. Правила: только
``app.providers.*`` / ``app.config`` / stdlib, никаких прямых сокетов
и процессов; секреты никогда не логировать.

Безопасность: плагин выполняется внутри процесса Proxy с его полными
правами — ставьте только код, которому доверяете.
"""

PLUGIN_API = 1

from app.providers.base import Field, Provider


class MyProvider(Provider):
    # TODO(1): свой id — латиница в нижнем регистре, цифры, дефис;
    # не занят встроенными провайдерами (grok, zcode, custom, ...).
    id = "my-provider"
    title = "Мой провайдер"
    icon = "◆"
    description = "Мой сервис как провайдер MemuAI Proxy"

    def account_fields(self):
        # TODO(2): поля аккаунта — то, что введёт пользователь.
        # Типы: text / password / url / select / textarea.
        return [Field("api_key", "API-ключ", "password")]

    def seed_models(self):
        # TODO(3): стартовый каталог моделей [(id, примечание)].
        return [("model-a", "Модель A")]

    async def check(self, account):
        # TODO(4): быстрая проверка аккаунта, например один запрос
        # к endpoint'у сервиса. Верните (True/False/None, сообщение).
        return True, "OK"

    async def chat_stream(self, account, model, messages, params):
        # TODO(5): запрос к сервису и yield нормализованных событий:
        #   {"type": "text", "text": "..."} — кусок ответа;
        #   {"type": "reasoning", "text": "..."} — рассуждения (если есть).
        # Ошибки — исключениями: ProviderAuthError (ключ отклонён),
        # ProviderServerError, ProviderNetworkError, ProviderRateLimitError.
        # Помощники: app.providers.http.get_session (пул сессий с
        # TLS-имитацией Chrome), app.providers.base.normalize_messages.
        raise NotImplementedError("реализуйте chat_stream() под свой сервис")

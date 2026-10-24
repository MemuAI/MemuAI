"""Тесты никогда не пишут в настоящие данные MemuAI.

Пакет импортируется раньше любого тестового модуля, поэтому папка данных
подменяется до первого ``import app``. Раньше тест без подмены
``default_root`` записал заглушку ``<p>x</p>`` поверх Переводчика у человека.

Все временные папки тестов лежат в одной папке запуска, которая удаляется при выходе: прежде каждый
``mkdtemp`` оставался в системной папке Temp навсегда (за сотни прогонов — десятки тысяч папок), а от их числа
замедлялись и сами тесты (подсказки путей читали Temp).
"""
import atexit
import os
import shutil
import tempfile

if not os.environ.get("MEMUAI_TESTS_KEEP_TEMP"):
    _RUN_ROOT = tempfile.mkdtemp(prefix="memuai-run-")
    tempfile.tempdir = _RUN_ROOT
    atexit.register(shutil.rmtree, _RUN_ROOT, True)

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

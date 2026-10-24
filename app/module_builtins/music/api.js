/* MemuMusic · api.js — единственная точка обращения к бэкенду (Memu.music.call).
 * Мок (mock.js) включается, только если моста библиотеки нет или в адресе ?mock=1. */
const API = (() => {
  const bridge = () => {
    const m = window.Memu && Memu.music;
    return m && typeof m.call === "function" ? m : null;
  };
  const mocked = /[?&]mock=1\b/.test(location.search) || !!window.__memuMusicMock || !bridge();
  async function call(op, args) {
    if (mocked) return MockMusic.call(op, args || {});
    return bridge().call(op, args || {});
  }
  /** Тихий вызов: ошибка не роняет экран, а возвращает fallback. */
  async function soft(op, args, fallback) {
    try { return await call(op, args); } catch (error) { console.warn("[music]", op, error && error.message || error); return fallback; }
  }
  return { call, soft, mocked };
})();

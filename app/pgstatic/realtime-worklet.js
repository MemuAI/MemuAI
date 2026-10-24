/* MemuAI Live: захват микрофона для моделей реального времени.
 *
 * Работает в аудиопотоке браузера. Приводит звук к частоте модели (16 или
 * 24 кГц), сглаживает его перед прореживанием, чтобы не было «звона», и
 * отдаёт 40-миллисекундные куски PCM16 вместе с уровнем громкости для сферы.
 * Отдельный файл нужен из-за CSP: модуль worklet нельзя загрузить из blob:.
 */
class MemuCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const target = (options && options.processorOptions && options.processorOptions.targetRate) || 24000;
    this.ratio = sampleRate / target;
    this.chunk = Math.max(160, Math.round(target * 0.04));
    this.pending = new Int16Array(this.chunk);
    this.fill = 0;
    this.pos = 0;
    this.muted = false;
    // Однополюсный фильтр нижних частот чуть ниже новой частоты Найквиста.
    const cutoff = Math.min(target * 0.45, sampleRate * 0.45);
    this.alpha = 1 - Math.exp(-2 * Math.PI * cutoff / sampleRate);
    this.lp = 0;
    this.prev = 0;
    this.levelSum = 0;
    this.levelCount = 0;
    this.port.onmessage = (event) => {
      if (event.data && typeof event.data.muted === "boolean") this.muted = event.data.muted;
    };
  }

  process(inputs) {
    const input = inputs[0];
    const channel = input && input[0];
    if (!channel) return true;
    let sum = 0;
    // Буфер переиспользуем: новый массив на каждые 128 отсчётов — лишний мусор в аудиопотоке.
    if (!this.filtered || this.filtered.length !== channel.length) this.filtered = new Float32Array(channel.length);
    const filtered = this.filtered;
    for (let i = 0; i < channel.length; i++) {
      const x = this.muted ? 0 : channel[i];
      sum += x * x;
      this.lp += this.alpha * (x - this.lp);
      filtered[i] = this.lp;
    }
    this.levelSum += sum;
    this.levelCount += channel.length;
    // Отсчёт между последним значением прошлого блока и первым текущего
    // (t в [-1, 0)) интерполируется по this.prev; раньше хвост блока
    // «интерполировался» сам с собой, и при 44,1 кГц или 16→24 кГц на стыке
    // каждого блока была ступенька — слышимый треск.
    let t = this.pos;
    for (; t < filtered.length - 1; t += this.ratio) {
      const i = Math.floor(t);
      const f = t - i;
      const a = i < 0 ? this.prev : filtered[i];
      const b = filtered[i + 1];
      let s = a + (b - a) * f;
      s = s > 1 ? 1 : s < -1 ? -1 : s;
      this.pending[this.fill++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.fill >= this.chunk) {
        const level = Math.sqrt(this.levelSum / Math.max(1, this.levelCount));
        const out = this.pending;
        this.port.postMessage({ pcm: out.buffer, level }, [out.buffer]);
        this.pending = new Int16Array(this.chunk);
        this.fill = 0;
        this.levelSum = 0;
        this.levelCount = 0;
      }
    }
    this.pos = t - filtered.length;
    this.prev = filtered[filtered.length - 1];
    return true;
  }
}

registerProcessor("memu-capture", MemuCaptureProcessor);

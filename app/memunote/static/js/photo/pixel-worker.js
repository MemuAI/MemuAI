// MemuNote Photo — heavy pixel work off the UI thread (module worker; CSP worker-src 'self').
import { runOp, segment, inpaint, magicWand, featherMask, growMask } from "./pixels.js";

self.onmessage = (e) => {
  const { id, kind, name, width, height, buffer, params, mask, box, opts } = e.data;
  try {
    const img = { width, height, data: new Uint8ClampedArray(buffer) };
    if (kind === "op") {
      const out = runOp(name, img, params, mask ? new Uint8Array(mask) : null);
      self.postMessage({ id, ok: true, buffer: out.data.buffer }, [out.data.buffer]);
    } else if (kind === "segment") {
      const m = segment(img, box, { ...(opts || {}), seedMask: opts?.seed ? new Uint8Array(opts.seed) : null });
      self.postMessage({ id, ok: true, mask: m.buffer }, [m.buffer]);
    } else if (kind === "inpaint") {
      const out = inpaint(img, new Uint8Array(mask), opts || {});
      self.postMessage({ id, ok: true, buffer: out.data.buffer }, [out.data.buffer]);
    } else if (kind === "wand") {
      const m = magicWand(img, opts.x, opts.y, opts);
      self.postMessage({ id, ok: true, mask: m.buffer }, [m.buffer]);
    } else if (kind === "mask") {
      let m = new Uint8Array(mask);
      if (opts.grow) m = growMask(m, width, height, opts.grow);
      if (opts.feather) m = featherMask(m, width, height, opts.feather);
      self.postMessage({ id, ok: true, mask: m.buffer }, [m.buffer]);
    } else throw new Error("unknown job " + kind);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err?.message || err) });
  }
};

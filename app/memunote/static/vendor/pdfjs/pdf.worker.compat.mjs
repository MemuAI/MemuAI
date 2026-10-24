// MemuNote (Round 34) — pdf.js worker entry: installs polyfills.mjs before the vendored worker evaluates (module
// imports run in order), then re-exports it so pdf.js's main-thread "fake worker" fallback finds WorkerMessageHandler.
// pdfedit.js points GlobalWorkerOptions.workerSrc here; the vendor file itself stays untouched.
import "./polyfills.mjs?v=r34";
export * from "./pdf.worker.min.mjs";

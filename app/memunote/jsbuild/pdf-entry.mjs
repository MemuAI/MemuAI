// MemuNote PDF editing bundle (Round 31): pdf-lib (MIT) + @pdf-lib/fontkit (MIT) for embedding a Unicode font.
// Built by build_vendor.mjs into app/memunote/static/vendor/pdflib/pdflib.js (ESM, minified). Loaded lazily
// only when a PDF is edited or saved.
export { PDFDocument, PDFName, PDFString, PDFHexString, PDFNumber, PDFArray, PDFDict, rgb, degrees, StandardFonts,
  LineCapStyle, BlendMode, PDFTextField, PDFCheckBox, PDFDropdown, PDFRadioGroup, PDFOptionList, PDFButton,
  PDFSignature, EncryptedPDFError } from "pdf-lib";
export { default as fontkit } from "@pdf-lib/fontkit";

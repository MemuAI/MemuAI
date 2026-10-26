
import { ZipArchive } from "./zip.js";
import { escXml, XML_HEAD } from "./xml.js";

export function excelColumn(index) {
  let result = "";
  for (let n = Math.max(1, Math.trunc(index)); n; n = Math.floor((n - 1) / 26)) result = String.fromCharCode(65 + (n - 1) % 26) + result;
  return result;
}

export async function chartWorkbook(chart) {
  const cats = Array.isArray(chart.cats) ? chart.cats : [];
  const series = Array.isArray(chart.series) ? chart.series : [];
  const rows = Math.max(cats.length, ...series.map(s => s.vals?.length || 0));
  if (rows > 10000 || series.length > 128) throw new Error("Chart data exceeds the supported limit (10,000 categories / 128 series)");
  const textCell = (ref, text) => `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${escXml(String(text ?? ""))}</t></is></c>`;
  const header = textCell("A1", "Category") + series.map((s, i) => textCell(excelColumn(i + 2) + "1", s.name)).join("");
  let data = `<row r="1">${header}</row>`;
  for (let i = 0; i < rows; i++) {
    const r = i + 2;
    data += `<row r="${r}">${textCell("A" + r, cats[i] ?? "")}` + series.map((s, j) => {
      const value = Number(s.vals?.[i]);
      return `<c r="${excelColumn(j + 2)}${r}"><v>${Number.isFinite(value) ? value : 0}</v></c>`;
    }).join("") + "</row>";
  }
  const zip = new ZipArchive();
  zip.set("[Content_Types].xml", XML_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>');
  zip.set("_rels/.rels", XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>');
  zip.set("xl/workbook.xml", XML_HEAD + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>');
  zip.set("xl/_rels/workbook.xml.rels", XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>');
  zip.set("xl/worksheets/sheet1.xml", XML_HEAD + `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:${excelColumn(series.length + 1)}${rows + 1}"/><sheetData>${data}</sheetData></worksheet>`);
  return zip.write();
}

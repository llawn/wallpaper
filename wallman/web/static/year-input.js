// Year field: free text read with parseYear (1889, 1880s, 188x, 19th century, 1880-1895, c. 1880) and a
// suggestion menu from /api/years/suggest while digits are typed ("20" -> 2000s, 2010s, 2020s, 20th century …).

import { api } from "./api.js";
import { Suggest } from "./select-menu.js";
import { MAX_YEAR, MIN_YEAR, parseYear, yearToApi } from "./years.js";

export const YEAR_HELP = `A year from ${MIN_YEAR} to ${MAX_YEAR} such as 1906, a decade such as 1880s, `
  + "a range such as 1880-1895, 19th century or c. 1880.";

/** Field text -> {value, error}: value is null (no year), 1889 or [start, end]; undefined when unreadable. */
export function readYear(text) {
  if (!String(text ?? "").trim()) return { value: null, error: "" };
  const span = parseYear(text);
  return span ? { value: yearToApi(span), error: "" } : { value: undefined, error: YEAR_HELP };
}

/** The menu helps while 1 to 4 digits are typed; any other text is read as it is. */
export const yearSuggestable = (text) => /^\d{1,4}$/.test(String(text ?? "").trim());

export class YearInput {
  /** @param {HTMLInputElement} input  picking a suggestion fires an `input` event so the form re-validates */
  constructor(input) {
    this.input = input;
    input.inputMode = "text";
    input.placeholder = "1889, 1880s, 19th century";
    this.suggest = new Suggest(input, {
      source: async (text) => (yearSuggestable(text) ? (await api.yearSuggestions(text.trim())) : [])
        .map((s) => ({ value: s, label: s, hint: rangeHint(s) })),
      onPick: (opt) => {
        input.value = opt.value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      },
    });
  }
}

/** "1880–1889" beside a suggestion that is a span, nothing beside a plain year. */
function rangeHint(text) {
  const span = parseYear(text);
  return span && span[0] !== span[1] ? `${span[0]}–${span[1]}` : "";
}

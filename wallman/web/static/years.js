/** Pure year helpers (no DOM). A port of wallman/domain/years.py parse_year: what a person types becomes a
 *  [start, end] span inside 1400-2100, or null. The API stores a single year as an integer and a range as a pair. */

export const MIN_YEAR = 1400;
export const MAX_YEAR = 2100;
const CIRCA = 5; // "c. 1880" means 1875-1885

const checked = (start, end) => (MIN_YEAR <= start && start <= end && end <= MAX_YEAR ? [start, end] : null);

/** 1889; 188x (decade); 18xx (century); 1880s (decade; a round hundred such as 1800s is the whole century);
 *  "19th century" = 1800-1899; 1880-1895 (hyphen, en or em dash); c. 1880 / ca 1880 / circa 1880 = ±5 years. */
export function parseYear(text) {
  const t = String(text ?? "").trim().toLowerCase();
  let m;
  if (/^\d{4}$/.test(t)) return checked(Number(t), Number(t));
  if ((m = /^(\d{3})x$/.exec(t))) return checked(m[1] * 10, m[1] * 10 + 9);
  if ((m = /^(\d{2})xx$/.exec(t))) return checked(m[1] * 100, m[1] * 100 + 99);
  if ((m = /^(\d{3})0s$/.exec(t))) {
    const d = m[1] * 10;
    return checked(d, d + (d % 100 === 0 ? 99 : 9));
  }
  if ((m = /^(\d{1,2})(?:st|nd|rd|th) century$/.exec(t))) {
    const n = Number(m[1]);
    return n >= 1 ? checked((n - 1) * 100, n * 100 - 1) : null;
  }
  if ((m = /^(\d{4})\s*[-–—]\s*(\d{4})$/.exec(t))) return checked(Number(m[1]), Number(m[2]));
  if ((m = /^(?:circa|ca\.?|c\.?)\s*(\d{4})$/.exec(t))) {
    const y = Number(m[1]);
    if (y < MIN_YEAR || y > MAX_YEAR) return null;
    return checked(Math.max(MIN_YEAR, y - CIRCA), Math.min(MAX_YEAR, y + CIRCA));
  }
  return null;
}

/** [start, end] -> what the API stores: 1889 or [1880, 1889]; null stays null. */
export function yearToApi(span) {
  if (!span) return null;
  return span[0] === span[1] ? span[0] : [span[0], span[1]];
}

/** The API's year as text: "1889" or "1880–1889" (en dash); "" for none. */
export function yearText(year) {
  if (year === null || year === undefined) return "";
  if (Array.isArray(year)) return year[0] === year[1] ? String(year[0]) : `${year[0]}–${year[1]}`;
  return String(year);
}

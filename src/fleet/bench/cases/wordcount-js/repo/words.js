export function countWords(text) {
  return text.split(" ").length;
}

export function longestWord(text) {
  return text.split(/\s+/).reduce((best, w) => (w.length > best.length ? w : best), "");
}

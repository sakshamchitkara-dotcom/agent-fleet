import { test } from "node:test";
import assert from "node:assert/strict";
import { countWords, longestWord } from "../words.js";

test("counts single-spaced words", () => {
  assert.equal(countWords("the quick brown fox"), 4);
});

test("ignores repeated and surrounding whitespace", () => {
  assert.equal(countWords("  hello   world "), 2);
  assert.equal(countWords("a\tb\nc"), 3);
});

test("empty and blank text have no words", () => {
  assert.equal(countWords(""), 0);
  assert.equal(countWords("   "), 0);
});

test("longest word", () => {
  assert.equal(longestWord("a bb ccc dd"), "ccc");
});

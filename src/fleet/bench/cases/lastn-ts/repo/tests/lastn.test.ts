import { test } from "node:test";
import assert from "node:assert/strict";
import { firstN, lastN } from "../src/lastn.ts";

test("last items, oldest first", () => {
  assert.deepEqual(lastN([1, 2, 3, 4], 2), [3, 4]);
  assert.deepEqual(lastN(["a"], 5), ["a"]);
});

test("zero or negative n gives nothing", () => {
  assert.deepEqual(lastN([1, 2, 3], 0), []);
  assert.deepEqual(lastN([1, 2, 3], -1), []);
});

test("firstN", () => {
  assert.deepEqual(firstN([1, 2, 3], 2), [1, 2]);
  assert.deepEqual(firstN([1, 2, 3], -1), []);
});

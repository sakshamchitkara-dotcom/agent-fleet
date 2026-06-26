/** The last `n` items of `xs`, oldest first. */
export function lastN<T>(xs: readonly T[], n: number): T[] {
  return xs.slice(-n);
}

/** The first `n` items of `xs`. */
export function firstN<T>(xs: readonly T[], n: number): T[] {
  return xs.slice(0, Math.max(0, n));
}

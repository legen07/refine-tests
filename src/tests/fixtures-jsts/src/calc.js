export function add(a, b) {
  return a + b;
}

export function divide(a, b) {
  if (b === 0) {
    throw new Error("division by zero");
  }
  return a / b;
}

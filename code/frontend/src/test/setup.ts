import '@testing-library/jest-dom';

// The jsdom environment may receive --localstorage-file without a valid path,
// resulting in a broken localStorage object. Override it with a reliable
// in-memory stub so tests that read/write localStorage work correctly.
const _ls: Record<string, string> = {};
const localStorageStub = {
  getItem: (key: string): string | null => _ls[key] ?? null,
  setItem: (key: string, value: string): void => { _ls[key] = String(value); },
  removeItem: (key: string): void => { delete _ls[key]; },
  clear: (): void => { for (const k in _ls) delete _ls[k]; },
  get length(): number { return Object.keys(_ls).length; },
  key: (index: number): string | null => Object.keys(_ls)[index] ?? null,
};
Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageStub,
  writable: true,
  configurable: true,
});

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
Object.defineProperty(globalThis, 'ResizeObserver', {
  value: ResizeObserverStub,
  writable: true,
  configurable: true,
});

Object.defineProperty(window, 'matchMedia', {
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
  writable: true,
  configurable: true,
});

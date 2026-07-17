import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { transferableAbortController } from 'node:util';
import { afterEach } from 'vitest';

// The jsdom environment installs jsdom's own AbortController/AbortSignal
// globals, but `fetch` stays Node's undici implementation, which brand-checks
// `init.signal` against the Node-native AbortSignal class. TanStack Query
// creates its per-query signal from the global (jsdom) class, so every
// component query would reject with "Expected signal to be an instance of
// AbortSignal" and stick screens in their loading state. Restore the native
// pair so signals and fetch live in the same realm.
const nativeAbortController = transferableAbortController();
globalThis.AbortController = nativeAbortController.constructor as typeof AbortController;
globalThis.AbortSignal = nativeAbortController.signal.constructor as typeof AbortSignal;

afterEach(() => {
  cleanup();
});

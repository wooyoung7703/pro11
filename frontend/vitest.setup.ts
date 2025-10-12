// Global test setup for Vitest

// Start MSW in Node test environment to intercept API calls
import './tests/setup/msw';

// Ensure axios uses the Node HTTP adapter instead of XHR in jsdom so MSW can intercept
try {
	// jsdom provides XMLHttpRequest; axios picks XHR adapter if it exists.
	// We remove it so axios falls back to the Node http adapter that MSW/node hooks into.
	// This should be done before app code creates axios instances.
	(globalThis as any).XMLHttpRequest = undefined;
} catch {}

// You can extend expect here if needed
export {};

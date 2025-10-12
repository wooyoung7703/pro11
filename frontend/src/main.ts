import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import './assets/base.css';
import vSafeHtml from './directives/safeHtml';

async function bootstrap() {
	if (import.meta.env.DEV && (import.meta as any).env?.VITE_USE_MOCKS === '1') {
		try {
			// Primary path: let Vite resolve the bare specifier
			const mod = await import('msw/browser');
			const { setupWorker } = mod as any;
			const { handlers } = await import('./mocks/handlers');
			const worker = setupWorker(...(handlers as any));
			await worker.start({ onUnhandledRequest: 'bypass' });
		} catch (e) {
			console.warn('[mock] Failed to start MSW worker:', e);
		}
	}
	const app = createApp(App);
	app.use(createPinia());
	app.use(router);
	app.directive('safe-html', vSafeHtml);
	app.mount('#app');
}

bootstrap();

import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router';

// 라우트 지연 로딩으로 초기 번들 크기 감소
const Dashboard = () => import('../views/Dashboard.vue');
const FeatureDrift = () => import('../views/FeatureDrift.vue');
const ModelMetrics = () => import('../views/ModelMetrics.vue');
const TrainingJobs = () => import('../views/TrainingJobs.vue');
const InferencePlayground = () => import('../views/InferencePlayground.vue');
const Calibration = () => import('../views/Calibration.vue');
const AdminConsole = () => import('../views/AdminConsole.vue');
const AdminSettings = () => import('../views/AdminSettings.vue');
const RiskMetrics = () => import('../views/RiskMetrics.vue');
const OhlcvMonitor = () => import('../views/OhlcvMonitor.vue');
const OhlcvPage = () => import('../views/OhlcvPage.vue');
const Trading = () => import('../views/AutoTraderDashboard.vue');
const ModelsSummary = () => import('../views/ModelsSummary.vue');

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'dashboard', component: Dashboard },
  { path: '/drift', name: 'feature-drift', component: FeatureDrift },
  { path: '/metrics', name: 'model-metrics', component: ModelMetrics },
  { path: '/training', name: 'training-jobs', component: TrainingJobs },
  { path: '/trading', name: 'trading', component: Trading },
  { path: '/inference', name: 'inference', component: InferencePlayground },
  { path: '/calibration', name: 'calibration', component: Calibration },
  { path: '/admin', name: 'admin', component: AdminConsole },
  { path: '/admin/db-settings', name: 'admin-db-settings', component: AdminSettings },
  { path: '/risk', name: 'risk', component: RiskMetrics },
  { path: '/ohlcv', name: 'ohlcv', component: OhlcvPage },
  { path: '/ohlcv/legacy', name: 'ohlcv-legacy', component: OhlcvMonitor },
  { path: '/models', name: 'models', component: ModelsSummary },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;

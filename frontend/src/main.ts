import { createApp } from 'vue';
import { createRouter, createWebHashHistory } from 'vue-router';
import App from './App.vue';
import './style.css';
import ArchiveView from './views/ArchiveView.vue';
import CompareView from './views/CompareView.vue';
import DesignView from './views/DesignView.vue';
import EvaView from './views/EvaView.vue';
import EventsView from './views/EventsView.vue';
import ObjectsView from './views/ObjectsView.vue';
import OverviewView from './views/OverviewView.vue';
import ReportsView from './views/ReportsView.vue';
import RisksView from './views/RisksView.vue';
import SettingsView from './views/SettingsView.vue';
import SourcesView from './views/SourcesView.vue';
import TelemetryView from './views/TelemetryView.vue';

const ManualView = () => import('./views/ManualView.vue');

const routes = [
  { path: '/', component: OverviewView },
  { path: '/orbit', component: OverviewView },
  { path: '/eva', component: EvaView },
  { path: '/compare', component: CompareView },
  { path: '/risks', component: RisksView },
  { path: '/events', component: EventsView },
  { path: '/sources', component: SourcesView },
  { path: '/telemetry', component: TelemetryView },
  { path: '/objects', component: ObjectsView },
  { path: '/reports', component: ReportsView },
  { path: '/archive', component: ArchiveView },
  { path: '/settings', component: SettingsView },
  { path: '/design', component: DesignView },
  // Документация: #/manual (список) и #/manual/<имя>#<якорь>; имя может содержать каталог внутри docs/.
  // Раздел грузится отдельным чанком вместе с разборщиком markdown.
  { path: '/manual', component: ManualView },
  { path: '/manual/:name(.*)', component: ManualView },
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

// Hash-история: приложение работает на статическом хостинге без правил переписывания путей.
createApp(App).use(createRouter({ history: createWebHashHistory(), routes })).mount('#app');

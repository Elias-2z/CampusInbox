import { createRouter, createWebHistory } from 'vue-router'
import EnvironmentView from '../views/EnvironmentView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [{ path: '/', name: 'environment', component: EnvironmentView }],
})

export default router

import { createRouter, createWebHistory } from 'vue-router'
import TodayView from '@/views/TodayView.vue'
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: TodayView, meta: { title: '今天' } },
    {
      path: '/inbox',
      component: () => import('@/views/InboxView.vue'),
      meta: { title: '智能收件箱' },
    },
    {
      path: '/tasks/:id?',
      component: () => import('@/views/TasksView.vue'),
      meta: { title: '我的任务' },
    },
    {
      path: '/settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { title: '设置' },
    },
    {
      path: '/reminders',
      component: () => import('@/views/RemindersView.vue'),
      meta: { title: '截止提醒' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior(to, from) {
    return to.path === from.path ? undefined : { top: 0 }
  },
})
router.afterEach((to) => {
  document.title = `${String(to.meta.title || '校园任务')} · CampusInbox`
})
export default router

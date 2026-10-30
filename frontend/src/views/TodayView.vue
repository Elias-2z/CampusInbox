<script setup lang="ts">
import { computed, ref } from 'vue'
import { dayKey } from '@/api'
import { useCampus } from '@/stores/campus'
import TaskList from '@/components/TaskList.vue'
const store = useCampus()
const selectedDay = ref(0)
const today = computed(() => new Date(store.clock))
const days = computed(() =>
  Array.from({ length: 7 }, (_, index) => {
    const date = new Date(today.value)
    date.setDate(date.getDate() + index)
    return {
      date,
      key: dayKey(date),
      weekday: date.toLocaleDateString('zh-CN', { weekday: 'short' }),
    }
  }),
)
const priority = computed(() =>
  [...store.active]
    .sort(
      (a, b) =>
        (a.due_at ? Date.parse(a.due_at) : Infinity) - (b.due_at ? Date.parse(b.due_at) : Infinity),
    )
    .slice(0, 5),
)
const dueForDay = computed(() =>
  store.active.filter(
    (t) => t.due_at && dayKey(new Date(t.due_at)) === days.value[selectedDay.value]?.key,
  ),
)
const completed = computed(() => store.tasks.filter((t) => t.status === 'completed').length)
const total = computed(
  () => store.tasks.filter((t) => !['archived', 'cancelled'].includes(t.status)).length,
)
</script>
<template>
  <div class="ci-pagehead">
    <div>
      <div class="ci-eyebrow">
        {{
          today.toLocaleDateString('zh-CN', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            weekday: 'long',
          })
        }}
      </div>
      <h1>嗨，同学！开个好头 ✌️</h1>
    </div>
    <button
      class="ci-btn ci-primary cursor-interaction"
      :disabled="!store.ready"
      @click="store.importing = true"
    >
      ＋ 导入通知
    </button>
  </div>
  <section class="ci-hero">
    <div>
      <h2>把琐事放这里，<br />把脑袋留给好主意。</h2>
      <p>
        {{
          store.active.length
            ? `还有 ${store.active.length} 件事等你，先从最重要的开始。`
            : '从一条课程通知开始，把事情安排明白。'
        }}
      </p>
    </div>
    <div class="ci-hero-art" aria-hidden="true">
      <div class="ci-orbit" />
      <span class="ci-sun">🌞</span><span class="ci-sticker">YOU GOT THIS!</span>
    </div>
  </section>
  <div v-if="store.ready" class="ci-columns">
    <section>
      <div class="ci-sectionhead">
        <h2>
          接下来先搞定 👇<span class="ci-count">{{ store.active.length }} 项待办</span>
        </h2>
        <RouterLink class="ci-btn ci-text cursor-interaction" to="/tasks">全部任务 ↗</RouterLink>
      </div>
      <TaskList v-if="store.tasks.length" :tasks="priority" />
      <div v-else class="ci-first">
        <span class="ci-firstemoji">🌱</span>
        <h2>你的校园清单，从这里生长</h2>
        <p>粘贴一条老师的通知，核对后就能变成任务。</p>
        <div class="ci-actions">
          <button class="ci-btn ci-primary cursor-interaction" @click="store.importing = true">
            导入第一条通知 →</button
          ><button class="ci-btn cursor-interaction" @click="store.edit()">手动记一件事</button>
        </div>
      </div>
      <div v-if="total" class="ci-donebar">
        <span>已完成 {{ completed }} / {{ total }}</span>
        <div
          class="ci-progress"
          role="progressbar"
          aria-label="任务完成进度"
          :aria-valuenow="completed"
          :aria-valuemin="0"
          :aria-valuemax="total"
        >
          <span :style="{ width: `${(completed / total) * 100}%` }" />
        </div>
        <span>🌱</span>
      </div>
    </section>
    <aside class="ci-side">
      <section class="ci-inboxtease">
        <div class="ci-teaseicon" aria-hidden="true">📮</div>
        <h3>{{ store.pending.length ? '有新消息，等你拍板' : '收件箱，随时待命' }}</h3>
        <p>
          {{
            store.pending.length
              ? `${store.pending.length} 条建议待处理，原文和提取结果都放好了。`
              : '收到新通知就放进来，确认后才会创建正式任务。'
          }}
        </p>
        <RouterLink class="ci-btn cursor-interaction" to="/inbox">去收件箱看看 →</RouterLink>
      </section>
      <section class="ci-week">
        <div class="ci-sectionhead">
          <h3>接下来这一周</h3>
          <small class="ci-muted">按截止日查看</small>
        </div>
        <div class="ci-days">
          <button
            v-for="(day, index) in days"
            :key="day.key"
            class="ci-day cursor-interaction"
            :aria-pressed="selectedDay === index"
            @click="selectedDay = index"
          >
            <span>{{ day.weekday }}</span
            ><b>{{ day.date.getDate() }}</b
            ><span
              class="ci-dot"
              :style="{
                opacity: store.active.some(
                  (t) => t.due_at && dayKey(new Date(t.due_at)) === day.key,
                )
                  ? 1
                  : 0,
              }"
            />
          </button>
        </div>
        <div class="ci-weeknote">
          <template v-if="dueForDay.length"
            ><RouterLink
              v-for="task in dueForDay"
              :key="task.id"
              :to="`/tasks/${task.id}`"
              class="ci-daytask"
              >{{ task.title }} ↗</RouterLink
            ></template
          ><span v-else>这天没有截止任务，留点时间给自己 ☕</span>
        </div>
      </section>
    </aside>
  </div>
</template>

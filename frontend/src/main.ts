import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import './style.css'
import App from './App.vue'

createApp(App).use(createPinia()).use(naive).mount('#app')

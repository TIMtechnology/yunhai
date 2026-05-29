import { createApp } from 'vue'
import naive from 'naive-ui'
import './style.css'
import './label.css'
import LabelApp from './LabelApp.vue'

createApp(LabelApp).use(naive).mount('#app')

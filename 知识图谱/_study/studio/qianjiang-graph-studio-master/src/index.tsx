import React from 'react'
import ReactDOM from 'react-dom'
import './index.scss'
import App from './App'
import reportWebVitals from './reportWebVitals'
import 'antd/dist/antd.css'
import axios from 'axios'
import { ConfigProvider} from 'antd'
import zhCN from 'antd/es/locale/zh_CN'
import '@/icons'
import moment from 'moment'
import 'moment/locale/zh-cn'

moment.locale('zh-cn')

const params: any = {}
const paramList = window.location.search.slice(1).split('&')
paramList.forEach(it => {
  const [key, value] = it.split('=')
  params[key] = value
})

axios.defaults.headers.post['Content-Type'] = 'application/json'
axios.defaults.headers.get['Authorization'] = `Bearer ${params['token']}`
axios.defaults.headers.post['Authorization'] = `Bearer ${params['token']}`
axios.defaults.headers.delete['Authorization'] = `Bearer ${params['token']}`

export const GRAPHIN_CONTAINER_DOM_ID = 'graphin-container'

ReactDOM.render(
  <ConfigProvider locale={zhCN}>
    <App />
  </ConfigProvider>,
  document.getElementById('root')
)

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals()

/*
 * @author PengJun
 * @description 请求拦截
 * @time 2020-12-25 10:52:45
 */
import axios from 'axios'
import { message } from 'antd'
import store from '@/redux/store/index'

const service = axios.create({
  // baseURL: 'http://192.168.30.104:8888', // url = base url + request url
  baseURL: '/api/'
  // withCredentials: true // send cookies when cross-domain requests
})
const timeout = 3600000

// Request interceptors
service.interceptors.request.use(
  (config) => {
    // 添加卡片类型
    if (config.headers?.spaceId) {
      const cardData: any = store.getState().cardData
      if(cardData.cardValueList.length) {
        config.headers = {...config.headers, ...cardData}
      }
    }
    //  设置超时时间
    if (!config.timeout) {
      config.timeout = timeout
    }
    return config
  },
  (error) => {
    Promise.reject(error).then((r) => message.info(r))
  }
)

// Response interceptors
service.interceptors.response.use(
  (response:any) => {
    if (response.data.type && response.data.type.indexOf("application") !== -1) {
      return response
    }
    if (response?.data?.code !== '200') {
      message.error(response?.data?.msg)
      return Promise.reject(response?.data?.msg)
    } else {
      return response
    }
  },
  (error) => {
    let tip = error.message
    message.error(tip)
    return Promise.reject(error)
  }
)

export default service

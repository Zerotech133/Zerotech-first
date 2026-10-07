import request from '@/utils/request'
import QueryString from 'qs'
import {
  TimelineParamType,
} from '@/interface/timeline'

// 时间轴点
const getTimeline = (params:TimelineParamType) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'timeline/queryEventList',
      method: 'get',
      params: params,
      paramsSerializer: function(params) {
        return QueryString.stringify(params,{arrayFormat:'indices', 'allowDots':true})
      }    
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// caseId
const getCaseId = (data:any) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'timeline/importTestData',
      method: 'post',
      data: QueryString.stringify(data),
      headers:{'Content-Type':'application/x-www-form-urlencoded;charset=utf-8'}
    }).then((response) => {
      resolve(response.data)
    })
  })
}
export {
  getTimeline,
  getCaseId
}
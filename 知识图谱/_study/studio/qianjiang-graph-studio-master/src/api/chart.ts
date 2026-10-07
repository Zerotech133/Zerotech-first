
import qs from 'qs'


import request from '@/utils/request'
import store from '@/redux/store/index'

import { extendNodeQuery } from '@/interface/chart-interface'
import {graphData} from "../common/interface";
const resolveBlob=(res:any, mimeType:any)=>{
  const aLink = document.createElement("a")
  const blob = new Blob([res.data], { type: mimeType })
  const patt = new RegExp("filename=([^;]+\\.[^\\.;]+);*")
  const contentDisposition = decodeURI(res.headers["content-disposition"])
  const result = patt.exec(contentDisposition) || ""
  let fileName = result[1]
  fileName = fileName.replace(/"/g, "")
  aLink.href = URL.createObjectURL(blob)
  aLink.setAttribute("download", fileName)
  document.body.appendChild(aLink)
  aLink.click()
  document.body.appendChild(aLink)
}
// 原始数据列表数据导出
const exportExcelApi = (data:any) => {
  const mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  const spaceId = store.getState().spaceId

  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/exportExcel',
      method: 'post',
      headers: {spaceId},
      responseType: "blob",
      data
    }).then((response) => {
      resolveBlob(response, mimeType)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 模糊查找节点
const findNodeApi = (data:any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'analysis/vertex/searchByName',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}
// 扩展节点
const extendNodeApi = (data: extendNodeQuery) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: 'analysis/graph/extend',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}

// 查找路径
const findPathApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: 'analysis/async/findPath',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}

// 查找子图
const findSubGraphApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: 'analysis/async/findSub',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}

// 复杂查询
const complexSearchApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: 'analysis/graph/complexSearch',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 查找回路
const searchReturnApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/async/findLoop',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 回路--根据id查名字
const findById = (params) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'analysis/vertex/findById',
      method: 'get',
      headers: {spaceId},
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}

//图谱统计
const graphStatisticsApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/graph/statistics',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}

//图谱统计
const findTextApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/text/search',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 图片保存
const addLibraryApi = (data: graphData) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/base/relationalGraph/add',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 原始数据列表查询
const queryEdgeApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/edge/queryByMergeEdge',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 原始数据列表查询
const relationTrendGraphApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/async/flowDirection',
      method: 'post',
      headers: {spaceId},
      data
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 历史记录查询
const queryHistoryRecordApi = (params: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/searchHistory/queryListByPage',
      method: 'get',
      headers: { spaceId },
      params
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 删除历史记录
const deleteHistoryRecordApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/searchHistory/delete',
      method: 'post',
      data: qs.stringify(data),
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      }
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 分析记录查询
const queryAnalysisRecordApi = (params: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/task/queryListByPage',
      method: 'get',
      headers: {
        spaceId
      },
      params
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 删除分析记录
const deleteAnalysisRecordApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/task/delete',
      method: 'post',
      data: qs.stringify(data),
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      }
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 分析记录-查询任务结果
const taskResultAnalysisRecordApi = (params: any) => {
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/task/queryTaskResult',
      method: 'get',
      params
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 数据范围查询
const queryDataRangeApi = () => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/card/selectCardList',
      method: 'get',
      headers: {
        spaceId
      }
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}

// 数据范围查询
const querySameTypeRelationApi = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve, reject) => {
    request({
      url: '/analysis/async/searchSameTagRelation',
      method: 'post',
      data,
      headers: {
        spaceId
      }
    }).then((response) => {
      resolve(response.data)
    }).catch((err) => {
      reject(err)
    })
  })
}
// 初始化集合列表
const selectCollectionList = () => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: '/collection/selectCollectionList',
      method: 'get',
      headers: { spaceId }
     }).then((response) => {
      resolve(response.data)
    })
  })
}
// 从中台跳转是否成功
const checkSpaceStatus = () => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: '/space/checkSpaceStatus',
      method: 'get',
      headers: { spaceId }
     }).then((response) => {
      resolve(response.data)
    })
  })
}
export {
  findNodeApi,
  extendNodeApi,
  findPathApi,
  findSubGraphApi,
  complexSearchApi,
  searchReturnApi,
  graphStatisticsApi,
  findTextApi,
  addLibraryApi,
  queryEdgeApi,
  exportExcelApi,
  relationTrendGraphApi,
  queryHistoryRecordApi,
  deleteHistoryRecordApi,
  queryDataRangeApi,
  querySameTypeRelationApi,
  selectCollectionList,
  checkSpaceStatus,
  findById,
  queryAnalysisRecordApi,
  deleteAnalysisRecordApi,
  taskResultAnalysisRecordApi

}

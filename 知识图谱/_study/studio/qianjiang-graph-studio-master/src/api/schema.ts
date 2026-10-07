
import qs from 'qs'
import request from '@/utils/request'
import {
  getSpaceListInterface,
  spaceCreateInterface,
  deleteSpaceInterface
} from '@/interface/schema-interface'

// 查询空间列表
const getSpaceList = (params:getSpaceListInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'space/selectPage',
      method: 'get',
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 创建空间列表
const spaceCreate = (data:spaceCreateInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'space/create',
      method: 'post',
      data: qs.stringify(data),
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'}
    }).then((response) => {
      resolve(response?.data)
    })
  })
}
// 删除空间列表
const deleteSpace = (data:deleteSpaceInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'space/delete',
      method: 'post',
      data: qs.stringify(data),
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'}
    }).then((response) => {
      resolve(response.data)
    })
  })
}

const importSpaceDataApi = ({spaceId, sampleSize, edgeNum}: { spaceId: string, sampleSize: number, edgeNum: number }) => {
  return new Promise<any>((resolve) => {
    request({
      url: `data/import/randomData?spaceId=${spaceId}&sampleSize=${sampleSize}&edgeNum=${edgeNum}`,
      method: 'post'
    }).then((response) => {
      resolve(response.data)
    })
  })
}




export {
  getSpaceList,
  spaceCreate,
  deleteSpace,
  importSpaceDataApi
}

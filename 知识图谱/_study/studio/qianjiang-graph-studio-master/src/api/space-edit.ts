import qs from 'qs'
import request from '@/utils/request'
import { 
  spaceTagCreateInterface,
  spaceEdgeCreateInterface
} from '@/interface/space-edit-interface'


// 查询空间标签列表
const getSpaceTagList = (params: {spaceId: string}) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'tag/select/bySpaceId',
      method: 'get',
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 创建空间标签列表
const spaceTagCreate = (data:spaceTagCreateInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'tag/create',
      method: 'post',
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}
// 修改空间标签列表
const spaceTagUpdate = (data:spaceTagCreateInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'tag/update',
      method: 'post',
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}
// 删除空间列表
const deleteSpaceTag = (data:{id: string}) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'tag/delete',
      method: 'post',
      data: qs.stringify(data),
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'}
    }).then((response) => {
      resolve(response.data)
    })
  })
}
// 查询边类型标签列表
const getSpaceEdgeList = (params:{spaceId: string}) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'edgeType/select/bySpaceId',
      method: 'get',
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 创建边类型标签列表
const spaceEdgeCreate = (data:spaceEdgeCreateInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'edgeType/create',
      method: 'post',
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}
// 修改边类型标签列表
const spaceEdgeUpdate = (data:spaceEdgeCreateInterface) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'edgeType/update',
      method: 'post',
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}
// 删除边类型列表
const deleteSpaceEdge = (data:{ id: string }) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'edgeType/delete',
      method: 'post',
      data: qs.stringify(data),
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'}
    }).then((response) => {
      resolve(response.data)
    })
  })
}




export {
  getSpaceTagList,
  spaceTagCreate,
  spaceTagUpdate,
  deleteSpaceTag,
  getSpaceEdgeList,
  spaceEdgeCreate,
  spaceEdgeUpdate,
  deleteSpaceEdge
}
